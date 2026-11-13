#!/usr/bin/env python3
"""Copy an existing 1C PostgreSQL database into an empty Firebird PgWire schema.

This is an offline preparation tool, not a runtime SQL translator. Install the
compatibility SQL in a separate new Firebird database first. Source queries run
in one read-only, repeatable-read transaction. The destination copy is atomic.
Credentials are accepted as libpq conninfo strings or environment variables;
neither credentials nor row contents are written to the verification report.
"""
import argparse
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from pgwire_client import Connection


def ident(value):
    return '"' + value.replace('"', '""') + '"'


def literal(value):
    return "'" + value.replace("'", "''") + "'"


class Database(Connection):
    def __init__(self, dsn, libpq=None):
        args = SimpleNamespace(host='', port=0, database='', user='',
                               connect_timeout=5, libpq=libpq)
        super().__init__(args, '', conninfo=dsn)
        for name, result, args in (
            ('PQprepare', C.c_void_p, [C.c_void_p, C.c_char_p, C.c_char_p, C.c_int, C.c_void_p]),
            ('PQexecPrepared', C.c_void_p, [C.c_void_p, C.c_char_p, C.c_int,
                                           C.c_void_p, C.c_void_p, C.c_void_p, C.c_int]),
            ('PQsendQueryParams', C.c_int, [C.c_void_p, C.c_char_p, C.c_int,
                                          C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p, C.c_int]),
            ('PQsetSingleRowMode', C.c_int, [C.c_void_p]),
            ('PQgetResult', C.c_void_p, [C.c_void_p]),
        ):
            function = getattr(self.pq, name)
            function.restype, function.argtypes = result, args

    def result(self, handle, binary=False):
        if not handle:
            raise RuntimeError('Connection returned no result')
        try:
            status = self.pq.PQresultStatus(handle)
            if status not in (1, 2, 9):  # COMMAND_OK, TUPLES_OK, SINGLE_TUPLE
                state = self.pq.PQresultErrorField(handle, ord('C')) or b'unavailable'
                # Server error text can contain parameter values or credentials.
                raise RuntimeError('Database operation failed; SQLSTATE ' + state.decode())
            rows = []
            for row in range(self.pq.PQntuples(handle)):
                values = []
                for col in range(self.pq.PQnfields(handle)):
                    value = None if self.pq.PQgetisnull(handle, row, col) else C.string_at(
                        self.pq.PQgetvalue(handle, row, col), self.pq.PQgetlength(handle, row, col))
                    values.append(value if binary or value is None else value.decode('utf8'))
                rows.append(values)
            return rows
        finally:
            self.pq.PQclear(handle)

    def query(self, sql, binary=False):
        return self.result(self.pq.PQexec(self.connection, sql.encode()), binary)

    def prepare_insert(self, sql):
        self.result(self.pq.PQprepare(self.connection, b'', sql.encode(), 0, None))

    def insert(self, values):
        pointers = (C.c_char_p * len(values))(*values)
        lengths = (C.c_int * len(values))(*(len(v) if v is not None else 0 for v in values))
        formats = (C.c_int * len(values))(*([1] * len(values)))
        self.result(self.pq.PQexecPrepared(self.connection, b'', len(values), pointers,
                                         lengths, formats, 0))

    def stream(self, sql):
        if not self.pq.PQsendQueryParams(self.connection, sql.encode(), 0, None, None, None, None, 1):
            raise RuntimeError('Could not send verification query')
        if not self.pq.PQsetSingleRowMode(self.connection):
            raise RuntimeError('Could not enable bounded verification results')
        while True:
            handle = self.pq.PQgetResult(self.connection)
            if not handle:
                return
            yield from self.result(handle, binary=True)


class Fingerprint:
    """Order-independent row multiset digest, including NULL and byte lengths."""
    MODULUS = 1 << 256

    def __init__(self, binary_columns):
        self.count = self.total = self.squares = 0
        self.binary_columns = binary_columns
        self.binary_bytes = self.binary_cells = self.binary_max = 0

    def add(self, row):
        digest = hashlib.sha256()
        for index, value in enumerate(row):
            digest.update(struct.pack('!q', -1 if value is None else len(value)))
            if value is not None:
                digest.update(value)
                if index in self.binary_columns:
                    self.binary_cells += 1
                    self.binary_bytes += len(value)
                    self.binary_max = max(self.binary_max, len(value))
        number = int.from_bytes(digest.digest(), 'big')
        self.total = (self.total + number) % self.MODULUS
        self.squares = (self.squares + number * number) % self.MODULUS
        self.count += 1

    def report(self):
        return {'rows': self.count, 'sha256_sum': f'{self.total:064x}',
                'sha256_square_sum': f'{self.squares:064x}',
                'bytea_cells': self.binary_cells, 'bytea_bytes': self.binary_bytes,
                'bytea_max': self.binary_max}


def load_schema(source, schema):
    tables = []
    for oid, name, persistence in source.query(
        'SELECT c.oid,c.relname,c.relpersistence FROM pg_catalog.pg_class c '
        'JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace '
        f'WHERE n.nspname={literal(schema)} AND c.relkind=\'r\' ORDER BY c.relname'):
        if persistence != 'p':
            raise RuntimeError('Only persistent source tables are supported: ' + name)
        columns = [dict(zip(('name', 'type', 'not_null', 'default', 'identity', 'generated',
                             'type_collation'), row))
            for row in source.query(
                'SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),'
                'a.attnotnull,pg_catalog.pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated,'
                'a.attcollation=t.typcollation FROM pg_catalog.pg_attribute a '
                'JOIN pg_catalog.pg_type t ON t.oid=a.atttypid LEFT JOIN pg_catalog.pg_attrdef d '
                'ON d.adrelid=a.attrelid AND d.adnum=a.attnum '
                f'WHERE a.attrelid={oid} AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum')]
        indexes = []
        for index_oid, index_name, unique, primary, nulls_equal, method, predicate, valid, definition in source.query(
            'SELECT i.indexrelid,c.relname,i.indisunique,i.indisprimary,i.indnullsnotdistinct,am.amname,'
            'pg_catalog.pg_get_expr(i.indpred,i.indrelid),i.indisvalid,pg_catalog.pg_get_indexdef(i.indexrelid) '
            'FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid '
            f'JOIN pg_catalog.pg_am am ON am.oid=c.relam WHERE i.indrelid={oid} ORDER BY c.relname'):
            segments = source.query(
                'SELECT a.attname,k.option FROM pg_catalog.pg_index i '
                'CROSS JOIN LATERAL unnest(i.indkey,i.indoption) WITH ORDINALITY k(attnum,option,n) '
                'LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum '
                f'WHERE i.indexrelid={index_oid} ORDER BY k.n')
            opclasses, collations = source.query(
                'SELECT bool_and(o.opcdefault),bool_and(k.collationid=a.attcollation) '
                'FROM pg_catalog.pg_index i CROSS JOIN LATERAL '
                'unnest(i.indkey,i.indclass,i.indcollation) WITH ORDINALITY k(attnum,classid,collationid,n) '
                'LEFT JOIN pg_catalog.pg_opclass o ON o.oid=k.classid '
                'LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum '
                f'WHERE i.indexrelid={index_oid} AND k.n<=i.indnkeyatts')[0]
            indexes.append(dict(name=index_name, unique=unique == 't', primary=primary == 't',
                                method=method, predicate=predicate, valid=valid == 't',
                                definition=definition, segments=segments,
                                nulls_not_distinct=nulls_equal == 't',
                                default_opclasses=opclasses == 't', column_collations=collations == 't',
                                nullable_columns=[name for name, _ in segments if any(
                                    c['name'] == name and c['not_null'] != 't' for c in columns)]))
        constraints = source.query(
            f'SELECT conname,contype FROM pg_catalog.pg_constraint WHERE conrelid={oid} ORDER BY conname')
        if any(kind not in ('p', 'n') for _, kind in constraints):
            raise RuntimeError('Unsupported source constraint on ' + name)
        if source.query(f'SELECT 1 FROM pg_catalog.pg_trigger WHERE tgrelid={oid} AND NOT tgisinternal'):
            raise RuntimeError('Source has user triggers: ' + name)
        tables.append(dict(name=name, columns=columns, indexes=indexes))
    if not tables:
        raise RuntimeError('Source schema has no persistent tables')
    return tables


def column_sql(column, pending, table):
    sql_type = column['type']
    allowed = r'(?:bytea|boolean|smallint|integer|bigint|date|time without time zone|timestamp without time zone|real|double precision|numeric\(\d+,\d+\)|(?:public\.)?m(?:var)?char(?:\(\d+\))?)'
    if (not re.fullmatch(allowed, sql_type) or column['identity'] or column['generated'] or
            column['type_collation'] != 't'):
        raise RuntimeError('Unsupported source column type/generation: ' + table + '.' + column['name'])
    definition = ident(column['name']) + ' ' + sql_type
    default = column['default']
    if default is not None:
        if re.fullmatch(r'-?\d+(?:\.\d+)?|true|false', default):
            definition += ' DEFAULT ' + default
        elif re.fullmatch(r"nextval\('[A-Za-z_][A-Za-z_0-9.]*'::regclass\)", default):
            pending.append({'table': table, 'column': column['name'], 'kind': 'sequence_default',
                            'definition': default})
        else:
            raise RuntimeError('Unsupported source default: ' + table + '.' + column['name'])
    if column['not_null'] == 't':
        definition += ' NOT NULL'
    return definition


def index_sql(schema, table, index, pending, mappings, occupied_names):
    if index['nulls_not_distinct']:
        raise RuntimeError('NULLS NOT DISTINCT indexes are not supported: ' + index['name'])
    segments = index['segments']
    if (index['method'] != 'btree' or not index['valid'] or index['predicate'] or
            not index['default_opclasses'] or not index['column_collations'] or
            not segments or any(name is None or option != '0' for name, option in segments)):
        pending.append({'table': table, 'index': index['name'], 'kind': 'index',
                        'unique': index['unique'], 'definition': index['definition'],
                        'reason': 'expression/direction/predicate/access method/opclass/collation'})
        if index['unique']:
            raise RuntimeError('Cannot omit a source uniqueness constraint: ' + index['name'])
        return None
    target = ident(schema) + '.' + ident(table)
    fields = ','.join(ident(name) for name, _ in segments)
    if index['primary']:
        return [f'ALTER TABLE {target} ADD CONSTRAINT {ident(index["name"])} PRIMARY KEY ({fields})']
    definition = ('CREATE ' + ('UNIQUE ' if index['unique'] else '') +
                  f'INDEX {ident(schema)}.{ident(index["name"])} ON {target} ({fields})')
    if not index['unique'] or not index['nullable_columns']:
        return [definition]
    # Firebird compares partially NULL keys for uniqueness. PostgreSQL's
    # default permits duplicates whenever any key segment is NULL. Enforce
    # uniqueness only on fully non-NULL keys; keep a full index for lookup.
    predicate = ' AND '.join(ident(name) + ' IS NOT NULL' for name in index['nullable_columns'])
    prefix = index['name'].encode('utf8')[:32].decode('utf8', errors='ignore')
    counter = 0
    while True:
        suffix = hashlib.sha256(f'{schema}\0{index["name"]}\0{counter}'.encode()).hexdigest()[:20]
        helper = prefix + '__pgw_all_' + suffix
        if helper not in occupied_names:
            occupied_names.add(helper)
            break
        counter += 1
    definitions = [definition + ' WHERE ' + predicate,
                   f'CREATE INDEX {ident(schema)}.{ident(helper)} ON {target} ({fields})']
    mappings.append({'table': table, 'source_index': index['name'], 'helper_index': helper,
                     'nullable_columns': index['nullable_columns'], 'definitions': definitions})
    return definitions


def table_fingerprint(target, schema, table):
    fingerprint = Fingerprint({i for i, c in enumerate(table['columns']) if c['type'] == 'bytea'})
    sql = 'SELECT ' + ','.join(ident(c['name']) for c in table['columns']) + ' FROM ' + \
          ident(schema) + '.' + ident(table['name'])
    for row in target.stream(sql):
        fingerprint.add(row)
    return fingerprint.report()


def schema_fingerprint(tables, version=2):
    if version == 1:
        # Legacy reports predate the nullable-unique correction. Their index
        # definitions already include explicit NULLS/opclass/collation clauses.
        tables = json.loads(json.dumps(tables))
        for table in tables:
            for column in table['columns']:
                column.pop('type_collation', None)
            for index in table['indexes']:
                for key in ('nulls_not_distinct', 'nullable_columns',
                            'default_opclasses', 'column_collations'):
                    index.pop(key, None)
    elif version != 2:
        raise RuntimeError('Unsupported preparation schema fingerprint version')
    return hashlib.sha256(json.dumps(tables, sort_keys=True).encode()).hexdigest()


def reconcile_index_nulls(source, target, args, report, tables):
    previous = json.loads(args.copy_report.read_text())
    if not previous.get('committed') or previous.get('nullable_indexes_reconciled'):
        raise RuntimeError('Reconciliation requires a committed, unreconciled preparation report')
    if (previous.get('source', {}).get('database') != report['source']['database'] or
            previous.get('schema', 'public') != args.schema or
            previous.get('schema_sha256') != schema_fingerprint(tables, previous.get('schema_format', 1))):
        raise RuntimeError('Source schema differs from the committed preparation report')
    expected = {table['table']: table['target'] for table in previous['tables']}
    affected = {mapping['table'] for mapping in report['index_mappings']}
    report['tables'] = []
    target.query('BEGIN')
    for table in tables:
        if table['name'] in affected:
            before = table_fingerprint(target, args.schema, table)
            if before != expected.get(table['name']):
                raise RuntimeError('Target differs from the preparation report: ' + table['name'])
            report['tables'].append({'table': table['name'], 'before': before})
    for mapping in report['index_mappings']:
        target.query('DROP INDEX ' + ident(args.schema) + '.' + ident(mapping['source_index']))
        for sql in mapping['definitions']:
            target.query(sql)
        mapping['native_metadata'] = []
        for name, expected_flags in ((mapping['source_index'], ['1', '1']),
                                     (mapping['helper_index'], ['0', '0'])):
            metadata = target.query('SELECT COALESCE("RDB$UNIQUE_FLAG",0),'
                'CASE WHEN "RDB$CONDITION_BLR" IS NULL THEN 0 ELSE 1 END '
                'FROM "SYSTEM"."RDB$INDICES" WHERE "RDB$SCHEMA_NAME"=' + literal(args.schema) +
                ' AND "RDB$INDEX_NAME"=' + literal(name))
            if metadata != [expected_flags]:
                raise RuntimeError('Reconciled index metadata differs: ' + name)
            mapping['native_metadata'].append({'index': name, 'unique': expected_flags[0] == '1',
                                               'partial': expected_flags[1] == '1'})
    checked = {table['table']: table for table in report['tables']}
    for table in tables:
        if table['name'] in affected:
            after = table_fingerprint(target, args.schema, table)
            checked[table['name']]['after'] = after
            if after != checked[table['name']]['before']:
                raise RuntimeError('Data changed during index reconciliation: ' + table['name'])
    target.query('COMMIT')
    report['committed'] = report['nullable_indexes_reconciled'] = True
    previous['nullable_indexes_reconciled'] = True
    previous['index_mappings'] = report['index_mappings']
    previous['index_reconciliation_report'] = str(args.report)
    args.copy_report.write_text(json.dumps(previous, indent=2) + '\n')
    source.query('ROLLBACK')


def copy(source, target, args, report):
    source.query('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
    source.query("SET LOCAL bytea_output='hex'")
    report['source'] = dict(zip(('database', 'server_version', 'locale'), source.query(
        "SELECT current_database(),current_setting('server_version'),current_setting('lc_collate')")[0]))
    tables = load_schema(source, args.schema)
    report['source_sequences'] = []
    for row in source.query(
        'SELECT c.relname,s.seqstart,s.seqincrement,s.seqmin,s.seqmax,s.seqcache,s.seqcycle '
        'FROM pg_catalog.pg_sequence s JOIN pg_catalog.pg_class c ON c.oid=s.seqrelid '
        'JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace '
        f'WHERE n.nspname={literal(args.schema)} ORDER BY c.relname'):
        sequence = dict(zip(('name', 'start', 'increment', 'min', 'max', 'cache', 'cycle'), row))
        sequence['last_value'], sequence['is_called'] = source.query(
            'SELECT last_value,is_called FROM ' + ident(args.schema) + '.' + ident(row[0]))[0]
        report['source_sequences'].append(sequence)
    pending, definitions, omitted, mappings = [], [], set(), []
    occupied_names = {i['name'] for t in tables for i in t['indexes']}
    for table in tables:
        name = ident(args.schema) + '.' + ident(table['name'])
        columns = [column_sql(c, pending, table['name']) for c in table['columns']]
        indexes = []
        for index in table['indexes']:
            if index['name'] in args.omit_index:
                if index['unique']:
                    raise RuntimeError('Cannot omit a source uniqueness constraint: ' + index['name'])
                omitted.add(index['name'])
                pending.append({'table': table['name'], 'index': index['name'], 'kind': 'index',
                                'unique': False, 'definition': index['definition'],
                                'reason': 'explicit --omit-index after DDL preflight'})
                indexes.append(None)
            else:
                indexes.append(index_sql(args.schema, table['name'], index, pending, mappings, occupied_names))
        definitions.append(('CREATE TABLE ' + name + ' (' + ','.join(columns) + ')', indexes))
    if omitted != set(args.omit_index):
        raise RuntimeError('An explicitly omitted index does not exist in the source schema')
    report['pending_schema'] = pending
    report['schema'], report['schema_format'] = args.schema, 2
    report['schema_sha256'] = schema_fingerprint(tables)
    report['source_tables'] = len(tables)
    report['source_indexes'] = sum(len(t['indexes']) for t in tables)
    report['index_mappings'] = mappings
    if pending and not args.allow_incomplete_schema:
        raise RuntimeError('Source has unsupported schema objects; see report pending_schema. '
                           '--allow-incomplete-schema explicitly permits a data-only preparation stage.')
    if args.reconcile_index_nulls:
        reconcile_index_nulls(source, target, args, report, tables)
        return
    # These native metadata names also prove that the target is Firebird.
    existing = target.query('SELECT "RDB$RELATION_NAME" FROM "SYSTEM"."RDB$RELATIONS" '
        f'WHERE "RDB$SCHEMA_NAME"={literal(args.schema)} AND "RDB$VIEW_BLR" IS NULL')
    if existing:
        raise RuntimeError('Target schema is not empty; refusing to overwrite or resume')
    if args.preflight_only:
        report['preflight'] = {'tables_checked': 0, 'indexes_checked': 0, 'failures': []}
        for table, (ddl, indexes) in zip(tables, definitions):
            # Each attempt has an empty table and its own transaction. A failed
            # index must not hide subsequent diagnostics behind failed state E.
            for index, sql in [(None, None), *zip(table['indexes'], indexes)]:
                if index is not None and sql is None:
                    continue
                target.query('BEGIN')
                try:
                    target.query(ddl)
                    for statement in sql or []:
                        target.query(statement)
                    report['preflight']['indexes_checked' if sql else 'tables_checked'] += 1
                except RuntimeError as error:
                    failure = {'table': table['name'], 'error': str(error)}
                    if index:
                        failure.update(index=index['name'], unique=index['unique'],
                                       definition=index['definition'])
                    report['preflight']['failures'].append(failure)
                finally:
                    target.query('ROLLBACK')
            print(table['name'] + ': DDL preflight checked', flush=True)
        source.query('ROLLBACK')
        if report['preflight']['failures']:
            raise RuntimeError('DDL preflight rejected ' + str(len(report['preflight']['failures'])) + ' objects')
        return
    target.query('BEGIN')
    report['tables'] = []
    for table, (ddl, indexes) in zip(tables, definitions):
        name = ident(args.schema) + '.' + ident(table['name'])
        fields = ','.join(ident(c['name']) for c in table['columns'])
        select = 'SELECT ' + fields + ' FROM ' + name
        report['current_operation'] = 'create table ' + table['name']
        target.query(ddl)
        report['current_operation'] = 'prepare insert ' + table['name']
        target.prepare_insert('INSERT INTO ' + name + ' (' + fields + ') VALUES (' +
                              ','.join('$' + str(i + 1) for i in range(len(table['columns']))) + ')')
        binary_columns = {i for i, c in enumerate(table['columns']) if c['type'] == 'bytea'}
        original, copied = Fingerprint(binary_columns), Fingerprint(binary_columns)
        report['current_operation'] = 'copy rows ' + table['name']
        source.query('DECLARE migration_rows BINARY NO SCROLL CURSOR FOR ' + select)
        while True:
            rows = source.query(f'FETCH FORWARD {args.batch_rows} FROM migration_rows', binary=True)
            if not rows:
                break
            for row in rows:
                target.insert(row)
                original.add(row)
                if original.count % 1000 == 0:
                    print(f'{table["name"]}: {original.count} rows copied', flush=True)
        source.query('CLOSE migration_rows')
        report['current_operation'] = 'verify rows ' + table['name']
        for row in target.stream(select):
            copied.add(row)
        result = {'table': table['name'], 'source': original.report(), 'target': copied.report(),
                  'verified': original.report() == copied.report(), 'indexes_created': 0}
        report['tables'].append(result)
        if not result['verified']:
            raise RuntimeError('Binary data verification mismatch: ' + table['name'])
        for statements in indexes:
            for sql in statements or []:
                report['current_operation'] = 'create index on ' + table['name']
                target.query(sql)
                result['indexes_created'] += 1
        print(f'{table["name"]}: {original.count} rows verified', flush=True)
    target.query('COMMIT')
    report['committed'] = True
    report['nullable_indexes_reconciled'] = True
    source.query('ROLLBACK')
    report['schema_complete'] = not pending
    report.pop('current_operation', None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dsn', default=os.environ.get('PGWIRE_SOURCE_DSN'))
    parser.add_argument('--target-dsn', default=os.environ.get('PGWIRE_TARGET_DSN'))
    parser.add_argument('--schema', default='public')
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--libpq')
    parser.add_argument('--batch-rows', type=int, default=128)
    parser.add_argument('--preflight-only', action='store_true',
                        help='validate all table/index DDL in rolled-back transactions, without copying rows')
    parser.add_argument('--omit-index', action='append', default=[], metavar='NAME',
                        help='explicitly omit a nonunique index after reviewing its native DDL failure')
    parser.add_argument('--reconcile-index-nulls', action='store_true',
                        help='atomically correct nullable unique indexes from an earlier preparation')
    parser.add_argument('--copy-report', type=Path, help='committed preparation report for reconciliation')
    parser.add_argument('--allow-incomplete-schema', action='store_true',
                        help='copy rows while listing unsupported nonunique indexes and sequence defaults')
    args = parser.parse_args()
    if not args.source_dsn or not args.target_dsn or not 1 <= args.batch_rows <= 10000:
        parser.error('source/target DSNs and batch-rows between 1 and 10000 are required')
    if args.reconcile_index_nulls and (args.preflight_only or not args.copy_report):
        parser.error('--reconcile-index-nulls needs --copy-report and cannot use --preflight-only')
    if args.copy_report and args.copy_report.resolve() == args.report.resolve():
        parser.error('--copy-report and --report must be different files')
    report = {'mode': 'reconcile_index_nulls' if args.reconcile_index_nulls else
                     'ddl_preflight' if args.preflight_only else 'copy',
              'committed': False, 'source_read_only': True,
              'verification': 'binary row multiset SHA256 sum/square sum, counts and bytea lengths',
              'routine_policy': 'source routines/extensions are not copied; install compatibility SQL first'}
    source = target = None
    try:
        source = Database(args.source_dsn, args.libpq)
        target = Database(args.target_dsn, args.libpq)
        copy(source, target, args, report)
        return 0
    except Exception as error:
        # Never include libpq connect errors (which may echo conninfo) or row data.
        report['error'] = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print('Preparation failed: ' + report['error'], file=sys.stderr)
        return 1
    finally:
        for database in (target, source):
            if database:
                database.close()  # uncommitted destination work rolls back
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    sys.exit(main())
