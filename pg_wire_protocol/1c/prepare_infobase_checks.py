#!/usr/bin/env python3
"""Check offline nullable UNIQUE mapping against isolated PG and native Firebird.

The native connection must name a scratch database. Its test DDL/data are all
rolled back. PostgreSQL is started in a new private --work directory; the real
source database is never contacted. Firebird credentials use ISC_USER/PASSWORD.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

from prepare_infobase import Database, index_sql, reconcile_index_nulls, schema_fingerprint
from wire_host_smoke import reserve_ports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-database', required=True)
    parser.add_argument('--isql', required=True, type=Path)
    parser.add_argument('--pgroot', type=Path, default=Path('/opt/pgpro/1c-18'))
    parser.add_argument('--work', required=True, type=Path)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=False)
    pending, mappings = [], []
    index = dict(name='PGWIRE_NULL_CHECK_IDX', unique=True, primary=False,
                 method='btree', valid=True, predicate=None, nulls_not_distinct=False,
                 default_opclasses=True, column_collations=True,
                 nullable_columns=['A', 'B'], segments=[['A', '0'], ['B', '0']],
                 definition='synthetic nullable unique index')
    definitions = index_sql('PUBLIC', 'PGWIRE_NULL_CHECK', index, pending, mappings, {index['name']})
    helper = mappings[0]['helper_index']
    collision_mappings = []
    index_sql('PUBLIC', 'PGWIRE_NULL_CHECK', index, [], collision_mappings, {index['name'], helper})
    assert collision_mappings[0]['helper_index'] != helper
    assert len(collision_mappings[0]['helper_index'].encode()) <= 63
    index['nulls_not_distinct'] = True
    try:
        index_sql('PUBLIC', 'PGWIRE_NULL_CHECK', index, [], [], set())
    except RuntimeError:
        pass
    else:
        raise AssertionError('NULLS NOT DISTINCT was silently accepted')
    index['nulls_not_distinct'] = False
    for option in ('default_opclasses', 'column_collations'):
        index[option] = False
        try:
            index_sql('PUBLIC', 'PGWIRE_NULL_CHECK', index, [], [], set())
        except RuntimeError:
            pass
        else:
            raise AssertionError('An unsupported unique-index comparison was silently accepted')
        index[option] = True

    original_schema = [{'name': 't', 'columns': [], 'indexes': [index]}]
    previous = {'committed': True, 'source': {'database': 'synthetic'}, 'schema': 'public',
                'schema_format': 2, 'schema_sha256': schema_fingerprint(original_schema)}
    guard_report = args.work/'guard-copy-report.json'
    guard_report.write_text(json.dumps(previous))
    changed_schema = [{'name': 't', 'columns': [{'name': 'changed'}], 'indexes': [index]}]
    class UnusedTarget:
        def query(self, *args):
            raise AssertionError('Changed source schema reached destination mutation')
    try:
        reconcile_index_nulls(None, UnusedTarget(),
            SimpleNamespace(copy_report=guard_report, schema='public'),
            {'source': {'database': 'synthetic'}}, changed_schema)
    except RuntimeError as error:
        assert str(error).startswith('Source schema differs')
    else:
        raise AssertionError('Changed source schema was accepted for reconciliation')

    values = [(None, None), (None, None), (1, None), (1, None), (None, 1), (None, 1), (1, 2)]
    inserts = ['INSERT INTO "PUBLIC"."PGWIRE_NULL_CHECK" VALUES (' +
               ','.join('NULL' if v is None else str(v) for v in row) + ')' for row in values]
    sql = '\n'.join([
        'SET BAIL ON;', 'SET AUTODDL OFF;', 'SET LIST ON;',
        'CREATE TABLE "PUBLIC"."PGWIRE_NULL_CHECK" ("A" INTEGER,"B" INTEGER);',
        'CREATE EXCEPTION "PUBLIC"."PGWIRE_NULL_CHECK_FAILED" \'Expected uniqueness failure\';',
        *[statement + ';' for statement in definitions],
        *[statement + ';' for statement in inserts],
        'SET TERM ^;',
        'EXECUTE BLOCK RETURNS (ROW_COUNT_CHECK INTEGER) AS DECLARE blocked BOOLEAN = FALSE; BEGIN',
        'BEGIN INSERT INTO "PUBLIC"."PGWIRE_NULL_CHECK" VALUES (1,2);',
        'WHEN SQLCODE -803 DO blocked = TRUE; END',
        'IF (NOT blocked) THEN EXCEPTION "PUBLIC"."PGWIRE_NULL_CHECK_FAILED";',
        'SELECT COUNT(*) FROM "PUBLIC"."PGWIRE_NULL_CHECK" INTO ROW_COUNT_CHECK; SUSPEND; END^',
        'SET TERM ;^', 'ROLLBACK;', 'QUIT;',
    ])
    env = os.environ.copy()
    env['FIREBIRD'] = str(args.isql.resolve().parent.parent)
    result = subprocess.run([str(args.isql), '-q', '-bail', args.native_database],
                            input=sql, text=True, capture_output=True, env=env, timeout=60)
    (args.work/'native.log').write_text(result.stdout + result.stderr)
    if result.returncode or not any(line.split() == ['ROW_COUNT_CHECK', '7']
                                    for line in result.stdout.splitlines()):
        raise RuntimeError('Native nullable uniqueness check failed; see native.log')

    def command(*arguments):
        result = subprocess.run([str(args.pgroot/'bin'/arguments[0]), *map(str, arguments[1:])],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError('Private PostgreSQL command failed: ' + arguments[0])

    command('initdb', '-D', args.work/'pgdata', '-U', 'PGWIRE_NULL_TEST', '-A', 'trust',
            '--encoding=UTF8', '--locale=C.UTF-8')
    port = reserve_ports(1)[0]
    started, client = False, None
    try:
        command('pg_ctl', '-D', args.work/'pgdata', '-l', args.work/'postgres.log', '-o',
                f'-h 127.0.0.1 -p {port} -k {args.work}', '-w', 'start')
        started = True
        client = Database(f'host=127.0.0.1 port={port} dbname=postgres user=PGWIRE_NULL_TEST sslmode=disable')
        client.query('BEGIN')
        client.query('CREATE TABLE pgwire_null_check(a INTEGER,b INTEGER,UNIQUE(a,b))')
        for statement in inserts:
            client.query(statement.replace('"PUBLIC"."PGWIRE_NULL_CHECK"', 'pgwire_null_check'))
        client.query('SAVEPOINT duplicate_check')
        try:
            client.query('INSERT INTO pgwire_null_check VALUES(1,2)')
        except RuntimeError as error:
            if not str(error).endswith('23505'):
                raise
        else:
            raise AssertionError('PostgreSQL allowed the fully non-NULL duplicate')
        client.query('ROLLBACK TO SAVEPOINT duplicate_check')
        assert client.query('SELECT count(*) FROM pgwire_null_check') == [['7']]
        client.query('ROLLBACK')
        report = {'passed': True, 'rows_each': 7, 'duplicate_partial_null_allowed': True,
                  'duplicate_all_null_allowed': True, 'duplicate_nonnull_rejected': True,
                  'helper_name_collision_avoided': True, 'nulls_not_distinct_rejected': True,
                  'source_schema_change_rejected_before_target_access': True,
                  'nondefault_unique_opclass_or_collation_rejected': True,
                  'native_test_transaction': 'rolled back', 'source_database_contacted': False,
                  'mapping': mappings[0]}
        (args.work/'summary.json').write_text(json.dumps(report, indent=2) + '\n')
        print('Nullable UNIQUE mapping matches PostgreSQL through native isql and real libpq')
    finally:
        if client:
            client.close()
        if started:
            command('pg_ctl', '-D', args.work/'pgdata', '-m', 'fast', '-w', 'stop')


if __name__ == '__main__':
    main()
