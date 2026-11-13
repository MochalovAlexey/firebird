#!/usr/bin/env python3
"""Exercise the live pg_tables projection in an isolated native/PgWire runtime."""
import argparse
import json
from pathlib import Path
import sys

from pgwire_1c_smoke import Libpq, OneCHarness
from pgwire_namespace_checks import config_for, native, numbers
from pgwire_startup_smoke import Client
from wire_host_smoke import require, reserve_ports

PROFILE = Path(__file__).resolve().parents[1] / '1c'
sys.path.insert(0, str(PROFILE))
from install_compat import SCRIPTS

INSTALLER = 'sql/install_tables_catalog.sql'
ATTRIBUTES = 'sql/install_attribute_catalog.sql'
OPENING_QUERY = ("SELECT COUNT(*) FROM pg_catalog.pg_tables WHERE tablename IN "
                 "('config','configsave','params','files') AND schemaname IN ('public')")


def run(h):
    h.prepare()
    h.isql('admin', f"CONNECT '{h.database}';\nCREATE OR ALTER USER SYSDBA PASSWORD '{h.password}';\nCOMMIT;\nQUIT;\n", embedded=True)
    cfg = config_for(h)
    native(cfg, 'prerequisites', '\n'.join((PROFILE / name).read_text()
           for name in SCRIPTS[:SCRIPTS.index(INSTALLER)]), admin=True)
    script = (PROFILE / INSTALLER).read_text()
    native(cfg, 'atomic_failure', script.replace('\nCOMMIT;',
           '\nSELECT 1/0 FROM SYSTEM.RDB$DATABASE;\nCOMMIT;'), admin=True, error=True)
    absent = native(cfg, 'atomic_absent', "SELECT COUNT(*) AS N FROM SYSTEM.RDB$RELATIONS "
                    "WHERE RDB$SCHEMA_NAME='pg_catalog' AND RDB$RELATION_NAME='pg_tables';", admin=True)
    require(numbers(absent['output']) == [0], 'Failed installation retained the view')
    native(cfg, 'install', script, admin=True)
    native(cfg, 'repeat_refused', script, admin=True, error=True)
    attributes = (PROFILE / ATTRIBUTES).read_text()
    native(cfg, 'attribute_atomic_failure', attributes.replace('\nCOMMIT;',
           '\nSELECT 1/0 FROM SYSTEM.RDB$DATABASE;\nCOMMIT;'), admin=True, error=True)
    absent = native(cfg, 'attribute_atomic_absent', "SELECT COUNT(*) AS N FROM SYSTEM.RDB$RELATIONS "
                    "WHERE RDB$SCHEMA_NAME='pg_catalog' AND RDB$RELATION_NAME='pg_attribute';", admin=True)
    require(numbers(absent['output']) == [0], 'Failed installation retained pg_attribute')
    native(cfg, 'attribute_install', attributes, admin=True)
    native(cfg, 'attribute_repeat_refused', attributes, admin=True, error=True)
    native(cfg, 'fixtures', '''CREATE SCHEMA "Tables hidden";
CREATE TABLE "Tables hidden"."No access"(ID INTEGER);
CREATE TABLE "public"."Mixed Name"(ID INTEGER);
CREATE TABLE "public"."A""quote"(ID INTEGER);
CREATE GLOBAL TEMPORARY TABLE "public"."native_gtt"(ID INTEGER) ON COMMIT PRESERVE ROWS;
GRANT CREATE TABLE ON SCHEMA "public" TO USER WIRE_HOST_TEST;
GRANT CREATE VIEW ON SCHEMA "public" TO USER WIRE_HOST_TEST;
COMMIT;
''', admin=True)
    native(cfg, 'owner_readonly', 'DELETE FROM "pg_catalog"."pg_tables";', admin=True, error=True)
    native(cfg, 'attribute_owner_readonly', 'DELETE FROM "pg_catalog"."pg_attribute";', admin=True, error=True)
    n, p = reserve_ports(2)
    h.start('tables_catalog', n, n, [{'alias': 'PgWire', 'port': p}])
    ready = Client(h).login(); ready.close()
    config = {'port': p, 'password': h.password}
    first, second = Libpq(config), Libpq(config)
    records = []
    h.summary['tables_cases'] = records
    def query(sql, rows=None, *, connection=first, error=False):
        result = connection.query(sql, True)[-1]
        require(bool(result['sqlstate']) == error and (rows is None or result['rows'] == rows),
                sql + ' expected=' + repr(rows) + ': ' + repr(result))
        records.append({'sql': sql, 'result': result})
        return result
    try:
        query(OPENING_QUERY, [['0']])
        for name in ('config', 'configsave', 'params', 'files'):
            query(f'CREATE TABLE {name}(id integer)')
        query('CREATE VIEW config_view AS SELECT id FROM config')
        attribute_query = ("SELECT a.attname,a.attnum,a.attisdropped FROM pg_catalog.pg_attribute a "
                           "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid WHERE c.relname='config' "
                           "AND c.relnamespace='public'::regnamespace ORDER BY a.attnum")
        partno_query = ("SELECT 1::INT8 FROM pg_catalog.pg_attribute a WHERE attnum>0 AND NOT attisdropped "
                        "AND attname='partno' AND attrelid=(SELECT c.oid FROM pg_catalog.pg_class c "
                        "WHERE c.relname='config' AND pg_catalog.pg_table_is_visible(c.oid) "
                        "AND c.relnamespace IN ('public'::regnamespace)) LIMIT 1")
        query(partno_query, [])
        result = query(attribute_query, [['id', '1', 'f']])
        require([column['oid'] for column in result['columns']] == [1043, 23, 16],
                'Attribute native metadata differs')
        query("SELECT a.attname FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
              "WHERE c.relname='config_view' AND c.relnamespace='public'::regnamespace", [['id']])
        for client in (first, second):
            query(OPENING_QUERY, [['4']], connection=client)
            result = query("SELECT schemaname,tablename FROM pg_catalog.pg_tables "
                           "WHERE tablename IN ('Mixed Name','A\"quote','No access') ORDER BY tablename",
                           [['public', 'A"quote'], ['public', 'Mixed Name'], ['Tables hidden', 'No access']],
                           connection=client)
            require([column['oid'] for column in result['columns']] == [1043, 1043],
                    'Catalog names must retain native VARCHAR metadata')
            query("SELECT tablename FROM pg_catalog.pg_tables WHERE tablename IN ('config_view','native_gtt')",
                  [], connection=client)
        query('SELECT * FROM "Tables hidden"."No access"', error=True)
        query('BEGIN')
        query('CREATE TABLE transient_table(id integer)')
        query("SELECT tablename FROM pg_catalog.pg_tables WHERE tablename='transient_table'", [['transient_table']])
        query("SELECT tablename FROM pg_catalog.pg_tables WHERE tablename='transient_table'", [], connection=second)
        query('ROLLBACK')
        query("SELECT tablename FROM pg_catalog.pg_tables WHERE tablename='transient_table'", [])
        query('DROP VIEW config_view')
        query('DROP TABLE config')
        query(OPENING_QUERY, [['3']], connection=second)
        query('CREATE TABLE config(id integer)')
        query(OPENING_QUERY, [['4']], connection=second)
        query('ALTER TABLE config ADD partno integer')
        query('ALTER TABLE config ADD "Keep Exact" integer')
        query(partno_query, [['1']])
        query(attribute_query, [['id', '1', 'f'], ['partno', '2', 'f'], ['Keep Exact', '3', 'f']])
        query('ALTER TABLE config ALTER id POSITION 3')
        query(attribute_query, [['id', '1', 'f'], ['partno', '2', 'f'], ['Keep Exact', '3', 'f']], connection=second)
        query('SELECT "RDB$FIELD_POSITION" FROM "SYSTEM"."RDB$RELATION_FIELDS" '
              'WHERE "RDB$SCHEMA_NAME"=\'public\' AND "RDB$RELATION_NAME"=\'config\' '
              'AND "RDB$FIELD_NAME"=\'id\'', [['2']])
        query('BEGIN')
        query('ALTER TABLE config ADD rolled_back integer')
        query("SELECT attname FROM pg_catalog.pg_attribute WHERE attname='rolled_back'", [['rolled_back']])
        query('ROLLBACK')
        query("SELECT attname FROM pg_catalog.pg_attribute WHERE attname='rolled_back'", [])
        query('ALTER TABLE config DROP partno')
        query(partno_query, [])
        query(attribute_query, [['id', '1', 'f'], ['Keep Exact', '3', 'f']])
        query('ALTER TABLE config ADD partno integer')
        query(partno_query, [['1']])
        query(attribute_query, [['id', '1', 'f'], ['Keep Exact', '3', 'f'], ['partno', '4', 'f']])
        for binary in (0, 1):
            result = first.params(OPENING_QUERY, result_format=binary)
            require(not result['sqlstate'] and result['rows'] == [['0000000000000004' if binary else '4']],
                    'Extended/binary opening count differs: ' + repr(result))
            records.append({'extended_binary': binary, 'result': result})
        query('CREATE TEMP TABLE "Current Temp"(id integer)')
        query('INSERT INTO "Current Temp" VALUES(1)')
        namespace = query('SELECT pg_my_temp_schema()')['rows'][0][0]
        temporary = "SELECT schemaname,tablename FROM pg_catalog.pg_tables WHERE tablename='Current Temp'"
        query(temporary, [['pg_temp_' + namespace, 'Current Temp']])
        query('SELECT a.attname FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid '
              'WHERE c.relnamespace=pg_my_temp_schema()', [])
        query(temporary, [], connection=second)
        query('CREATE TEMP TABLE "Current Temp"(id integer)', connection=second)
        query('INSERT INTO "Current Temp" VALUES(2)', connection=second)
        other_namespace = query('SELECT pg_my_temp_schema()', connection=second)['rows'][0][0]
        require(namespace != other_namespace, 'Temporary namespaces must be attachment-local')
        query(temporary, [['pg_temp_' + other_namespace, 'Current Temp']], connection=second)
        query(temporary, [['pg_temp_' + namespace, 'Current Temp']])
        query('BEGIN')
        query('SAVEPOINT before_drop')
        query('DROP TABLE "Current Temp"')
        query(temporary, [])
        query('ROLLBACK TO before_drop')
        query(temporary, [['pg_temp_' + namespace, 'Current Temp']])
        query('COMMIT')
        query('ALTER SESSION RESET')
        # Native RESET clears contents and retains the accepted APP-15 identity.
        query(temporary, [['pg_temp_' + namespace, 'Current Temp']])
        query('SELECT COUNT(*) FROM "Current Temp"', [['0']])
        query('SELECT id FROM "Current Temp"', [['2']], connection=second)
        query(temporary, [['pg_temp_' + other_namespace, 'Current Temp']], connection=second)
        query('SET TRANSACTION READ ONLY')
        query(OPENING_QUERY, [['4']])
        query('ROLLBACK')
        query('UPDATE pg_catalog.pg_tables SET tablename=tablename', error=True)
        query(OPENING_QUERY, [['4']])
        observed = native(config_for(h), 'native_opening',
               'SELECT COUNT(*) AS N FROM "pg_catalog"."pg_tables" WHERE "tablename" IN '
               "('config','configsave','params','files') AND \"schemaname\"='public';")
        require(numbers(observed['output']) == [4], 'Native opening count differs')
        h.summary.update(tables_cases=records, atomic_install=True, repeat_refused=True,
                         native_owner_readonly=True, current_attachment_temp_only=True)
    finally:
        first.close(); second.close()
    h.no_attachments()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firebird-dir', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=False)
    h = OneCHarness(args.firebird_dir.resolve(), args.work_dir.resolve(), 120)
    try:
        run(h)
        h.summary['passed'] = True
    except Exception as error:
        h.summary['error'] = h.redact(str(error))
    finally:
        try: h.close()
        except Exception as error: h.summary.update(passed=False, shutdown_error=str(error))
        (h.work / 'summary.json').write_text(json.dumps(h.summary, indent=2) + '\n')
        print(json.dumps({'passed': h.summary['passed'], 'summary': str(h.work / 'summary.json')}))
    return 0 if h.summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
