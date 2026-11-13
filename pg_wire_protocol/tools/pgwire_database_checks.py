#!/usr/bin/env python3
"""Database identity/locale catalog acceptance in a private Firebird runtime."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from pgwire_1c_smoke import Libpq
from pgwire_namespace_checks import config_for, mapping, native, numbers
from pgwire_startup_smoke import Client, StartupHarness
from wire_host_smoke import require, reserve_ports

PROFILE = Path(__file__).resolve().parents[1] / '1c'
sys.path.insert(0, str(PROFILE))
from install_compat import SCRIPTS


def run(h, capture):
    h.prepare()
    h.isql('admin', f"CONNECT '{h.database}';\n"
           f"CREATE OR ALTER USER SYSDBA PASSWORD '{h.password}';\nCOMMIT;\nQUIT;\n", embedded=True)
    config = config_for(h)
    native(config, 'prerequisites', '\n'.join((PROFILE / name).read_text() for name in SCRIPTS[:8]), admin=True)
    before = mapping(config, 'namespace_ids_before')
    script = (PROFILE / 'sql/install_database_catalog.sql').read_text()
    native(config, 'install_rollback', script.replace('\nCOMMIT;',
           '\nSELECT 1/0 FROM SYSTEM.RDB$DATABASE;\nCOMMIT;'), admin=True, error=True)
    count = "SELECT COUNT(*) AS N FROM SYSTEM.RDB$FUNCTIONS WHERE RDB$SCHEMA_NAME='pg_catalog' " \
            "AND RDB$FUNCTION_NAME='current_database';\n"
    require(numbers(native(config, 'rollback_objects', count, admin=True)['output']) == [0],
            'Failed catalog installation retained objects')
    collision = h.database.with_name('catalog_collision.fdb')
    shutil.copy2(h.database, collision)
    cfg = dict(config, database=str(collision))
    native(cfg, 'collision_object', 'SET TERM ^;\nCREATE FUNCTION "pg_catalog"."current_database" '
           "RETURNS INTEGER AS BEGIN RETURN 73; END^\nSET TERM ;^\nCOMMIT;", admin=True)
    native(cfg, 'collision_install', script, admin=True, error=True)
    require(numbers(native(cfg, 'collision_preserved',
            'SELECT "pg_catalog"."current_database"() AS N FROM SYSTEM.RDB$DATABASE;', admin=True)['output']) == [73],
            'Installer replaced an existing function')

    def install(database, *options):
        command = [sys.executable, '-B', str(PROFILE / 'install_compat.py'), '--isql',
                   str(h.runtime / 'bin/isql'), '-d', str(database), *options]
        result = subprocess.run(command, env=h.env, cwd=h.temp, capture_output=True, text=True, timeout=h.timeout)
        (h.work / ('install_' + database.stem + '.log')).write_text(h.redact(result.stdout + result.stderr))
        require(result.returncode == 0, 'Compatibility CLI installation failed')

    install(h.database, '--upgrade-from-app16')
    native(config, 'repeat_refused', script, admin=True, error=True)
    require(mapping(config, 'namespace_ids_after') == before, 'Catalog migration changed namespace IDs')
    fresh = h.database.with_name('catalog_fresh_ru.fdb')
    h.isql('fresh_database', f"CREATE DATABASE '{fresh}' DEFAULT CHARACTER SET UTF8;\nQUIT;\n", embedded=True)
    install(fresh, '--locale', 'ru_RU.UTF-8')
    out = native(dict(config, database=str(fresh)), 'fresh_ru',
                 'SELECT "datcollate" AS LOCALE FROM "pg_catalog"."pg_database";', admin=True)['output']
    require('ru_RU.UTF-8' in out, 'Fresh CLI did not apply the requested native locale')

    native_port, pg_port = reserve_ports(2)
    h.start('database_catalog', native_port, native_port, [{'alias': 'PgWire', 'port': pg_port}])
    ready = Client(h)
    try:
        ready.login()
        ready.query_integer()
    finally:
        ready.close()
    config = dict(config_for(h), port=pg_port)
    first, second = Libpq(config), Libpq(config)
    checks = []
    sql = 'SELECT datcollate FROM pg_catalog.pg_database WHERE datname = current_database()'
    if capture:
        sql = (capture / 'profile/selected/05-database-collation.sql').read_text()
    try:
        def query(label, statement, rows=None, error=False, client=first):
            results = client.query(statement, True)
            require(len(results) == 1 and bool(results[0]['sqlstate']) == error and
                    (rows is None or results[0]['rows'] == rows), f'{label}: {results}')
            checks.append({'label': label, 'result': results[0]})
            return results[0]

        for client in (first, second):
            query('canonical_database', 'SELECT current_database()', [[str(h.database)]], client=client)
            query('current_database_only', 'SELECT datname FROM pg_catalog.pg_database', [[str(h.database)]], client=client)
            result = query('captured_locale_c', sql, [['C.UTF-8']], client=client)
            require(result['columns'][0]['oid'] == 1043, 'Unexpected native VARCHAR mapping')
        for binary in (0, 1):
            result = first.params(sql, [], result_format=binary)
            require(result['rows'] == [[b'C.UTF-8'.hex() if binary else 'C.UTF-8']],
                    'Extended/binary locale result differs')
        query('real_client_tablespaces', "SELECT spcname FROM pg_catalog.pg_tablespace "
              "WHERE spcname = 'v81c_index' OR spcname = 'v81c_data'", [])
        query('no_pg_tablespaces', 'SELECT spcname FROM pg_catalog.pg_tablespace', [])
        query('tablespace_readonly', "INSERT INTO pg_catalog.pg_tablespace VALUES('v81c_data')", error=True)
        query('real_client_mchar', "SELECT 1 FROM pg_catalog.pg_type as t WHERE typname='mchar' "
              "AND pg_catalog.pg_type_is_visible(t.oid) AND typnamespace IN ('public'::regnamespace)", [['1']])
        query('real_client_mchar_version', "SELECT installed_version FROM pg_catalog.pg_available_extensions "
              "WHERE NAME='mchar'", [['2.3']])
        query('real_client_fasttrun_version', "SELECT installed_version FROM pg_catalog.pg_available_extensions "
              "WHERE NAME='fasttrun'", [['2.0']])
        query('no_other_extensions', "SELECT name FROM pg_catalog.pg_available_extensions "
              "WHERE name NOT IN ('mchar','fasttrun','fulleq')", [])
        query('real_client_fulleq_version', "SELECT installed_version FROM pg_catalog.pg_available_extensions "
              "WHERE NAME='fulleq'", [['2.0']])
        query('real_client_typmod_function', "SELECT 1 FROM pg_catalog.pg_proc WHERE proname IN('mchartypmod_in') "
              "AND pronamespace IN('public'::regnamespace)", [])
        query('real_native_functions', "SELECT proname FROM pg_catalog.pg_proc WHERE proname IN('binrowver','bool_or') "
              "AND pronamespace='public'::regnamespace ORDER BY proname", [['binrowver'], ['bool_or']])
        query('function_native_identity', 'SELECT p.oid = f."RDB$FUNCTION_ID" + 1 FROM pg_catalog.pg_proc p '
              'JOIN "SYSTEM"."RDB$FUNCTIONS" f ON p.proname = TRIM(TRAILING FROM f."RDB$FUNCTION_NAME") '
              "WHERE p.proname='binrowver' AND f.\"RDB$SCHEMA_NAME\"='public'", [['t']])
        for name, cast in (('mchar', "'a'::mchar"), ('mvarchar', "'a'::mvarchar"),
                           ('bytea', "'\\x01'::bytea"), ('int8', '1::int8'),
                           ('regnamespace', "'public'::regnamespace")):
            oid = query('type_catalog_' + name, "SELECT oid FROM pg_catalog.pg_type WHERE typname='" + name + "'")['rows'][0][0]
            wire = query('type_codec_' + name, 'SELECT ' + cast)
            require(wire['columns'][0]['oid'] == int(oid), 'Catalog OID differs from actual wire type')
        query('unknown_type_visibility', 'SELECT pg_catalog.pg_type_is_visible(0), pg_catalog.pg_type_is_visible(NULL)', [[None, None]])
        query('alias_path_independent', 'SET SEARCH_PATH TO "SYSTEM"')
        query('visible_alias_matches_parser', 'SELECT pg_catalog.pg_type_is_visible(16449537)', [['t']])
        query('alias_compiles_without_public_path', "SELECT 'a'::mchar", [['a']])
        query('restore_path', 'SET SEARCH_PATH TO public, pg_catalog, "SYSTEM"')
        for control in ('BEGIN', 'SAVEPOINT a', 'ROLLBACK TO a', 'COMMIT', 'ALTER SESSION RESET'):
            query('transaction_control', control)
            query('stable_identity', 'SELECT current_database()', [[str(h.database)]])
        query('catalog_readonly', "UPDATE pg_catalog.pg_database SET datcollate='invalid'", error=True)
        query('recovery', 'SELECT 42', [['42']])
        native(config, 'configure_ru', (PROFILE / 'sql/configure_ru_collation.sql').read_text(), admin=True)
        query('live_native_ru', sql, [['ru_RU.UTF-8']])
        native(config, 'unsupported_collation',
               'ALTER CHARACTER SET SYSTEM.UTF8 SET DEFAULT COLLATION SYSTEM.UNICODE;\nCOMMIT;', admin=True)
        query('unsupported_native_change', sql, error=True)
        native(config, 'restore_ru',
               'ALTER CHARACTER SET SYSTEM.UTF8 SET DEFAULT COLLATION PGWIRE_COMPAT.RU_RU_UTF8;\nCOMMIT;', admin=True)
        query('restored_native_ru', sql, [['ru_RU.UTF-8']])
        native(config, 'native_after', 'SELECT COUNT(*) AS N FROM "pg_catalog"."pg_database";')
        h.summary.update(catalog_cases=checks, captured_sql_sha256=hashlib.sha256(sql.encode()).hexdigest(),
                         catalog_install_atomic=True, collision_preserved=True, namespace_ids_preserved=True,
                         cli_fresh_ru=True, cli_app16_upgrade=True)
    finally:
        first.close()
        second.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firebird-dir', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--capture-dir', type=Path)
    args = parser.parse_args()
    args.work_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    h = StartupHarness(args.firebird_dir.resolve(), args.work_dir.resolve(), 120)
    h.summary['stage'] = 'Current database identity and native locale catalog'
    try:
        run(h, args.capture_dir)
        h.close()
        h.summary['passed'] = True
    finally:
        h.close()
        if h.trap:
            h.trap.close()
        (h.work / 'summary.json').write_text(json.dumps(h.summary, ensure_ascii=False, indent=2) + '\n')
    print(h.work / 'summary.json')


if __name__ == '__main__':
    main()
