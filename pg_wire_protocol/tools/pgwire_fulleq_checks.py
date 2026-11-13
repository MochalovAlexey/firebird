#!/usr/bin/env python3
"""Partial fulleq profile against saved read-only PG oracle, in a private runtime."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from pgwire_1c_smoke import Libpq, OneCHarness
from pgwire_namespace_checks import config_for, native, numbers
from pgwire_startup_smoke import Client
from wire_host_smoke import require, reserve_ports

PROFILE = Path(__file__).resolve().parents[1] / '1c'
sys.path.insert(0, str(PROFILE))
from install_compat import SCRIPTS

CASES = [
    'NULL == NULL', 'NULL == 1', '1 == NULL',
    'TRUE == TRUE', 'TRUE == FALSE', 'FALSE == NULL',
    'CAST(-32768 AS SMALLINT) == CAST(-32768 AS SMALLINT)',
    'CAST(-2147483648 AS INTEGER) == CAST(2147483647 AS INTEGER)',
    'CAST(-9223372036854775808 AS BIGINT) == CAST(-9223372036854775808 AS BIGINT)',
    'CAST(9007199254740992 AS BIGINT) == CAST(9007199254740993 AS BIGINT)',
    "DATE '2026-11-13' == DATE '2026-11-13'",
    "TIME '12:34:56.1234' == TIME '12:34:56.1234'",
    "TIMESTAMP '2026-11-13 12:34:56.1234' == TIMESTAMP '2026-11-13 12:34:56.1234'",
    "CAST('2026-11-13 12:00:00+03:00' AS TIMESTAMP WITH TIME ZONE) == CAST('2026-11-13 09:00:00+00:00' AS TIMESTAMP WITH TIME ZONE)",
    "'a'::mchar == 'A '::mchar", "'a'::mvarchar == 'A '::mvarchar",
    "'a'::mchar(3) == 'A '::mchar(3)", "'a'::mvarchar(3) == 'A '::mvarchar(3)",
    "'a'::mchar == 'á '::mchar", "'a'::mvarchar == 'á '::mvarchar",
    "'a'::mchar == 'A '", "'A ' == 'a'::mvarchar",
    "CAST(NULL AS mchar) == CAST(NULL AS mchar)", "CAST(NULL AS mvarchar) == 'a'::mvarchar",
    r"'\x01'::bytea == '\x0100'::bytea", r"'\x'::bytea == '\x'::bytea",
    r"'\x00ff'::bytea == '\x00ff'", r"'\x00ff' == '\x00ff'::bytea",
    'false == true IS NULL', 'true IS NOT DISTINCT FROM NULL == NULL',
    'false == true == false', 'false == true = false', 'false = true == false',
    '(NULL == NULL) AND NOT (TRUE == FALSE)',
]
WRAPPERS = {
    'bool': ('TRUE', 'FALSE'), 'int2': ('1::smallint', '2::smallint'),
    'int4': ('1', '2'), 'int8': ('1::bigint', '2::bigint'),
    'date': ("DATE '2026-11-13'", "DATE '2026-11-14'"),
    'time': ("TIME '01:00:00'", "TIME '02:00:00'"),
    'timestamp': ("TIMESTAMP '2026-11-13 01:00:00'", "TIMESTAMP '2026-11-13 02:00:00'"),
    'timestamptz': ("CAST('2026-11-13 01:00:00+00:00' AS TIMESTAMP WITH TIME ZONE)", "CAST('2026-11-13 02:00:00+00:00' AS TIMESTAMP WITH TIME ZONE)"),
    'bytea': (r"'\x01'::bytea", r"'\x0100'::bytea"),
    'mchar': ("'a'::mchar", "'b'::mchar"), 'mvarchar': ("'a'::mvarchar", "'b'::mvarchar"),
}
for name, (a, b) in WRAPPERS.items():
    CASES.extend([f'public.isfulleq_{name}({a},{a})', f'public.isfulleq_{name}({a},{b})',
                  f'public.isfulleq_{name}(NULL,NULL)', f'public.isfulleq_{name}({a},NULL)'])


def run(h, oracle):
    h.prepare()
    h.isql('admin', f"CONNECT '{h.database}';\nCREATE OR ALTER USER SYSDBA PASSWORD '{h.password}';\nCOMMIT;\nQUIT;\n", embedded=True)
    cfg = config_for(h)
    fulleq_script = 'sql/install_fulleq.sql'
    native(cfg, 'prerequisites', '\n'.join((PROFILE/name).read_text()
           for name in SCRIPTS[:SCRIPTS.index(fulleq_script)]), admin=True)
    script = (PROFILE/fulleq_script).read_text()
    native(cfg, 'failed_install', script.replace('\nCOMMIT;', '\nSELECT 1/0 FROM SYSTEM.RDB$DATABASE;\nCOMMIT;'), admin=True, error=True)
    absent = native(cfg, 'failed_install_empty', "SELECT COUNT(*) AS N FROM SYSTEM.RDB$FUNCTIONS WHERE RDB$SCHEMA_NAME='public' AND RDB$FUNCTION_NAME STARTING WITH 'isfulleq_';", admin=True)
    require(numbers(absent['output']) == [0], 'Failed installer retained functions')
    result = subprocess.run([sys.executable, '-B', str(PROFILE/'install_compat.py'), '--isql',
        str(h.runtime/'bin/isql'), '-d', str(h.database), '--upgrade-from-app17'], env=h.env,
        capture_output=True, text=True, timeout=h.timeout)
    (h.work/'upgrade.log').write_text(h.redact(result.stdout+result.stderr))
    require(result.returncode == 0, 'APP-17 installer upgrade failed')
    native(cfg, 'repeat_install', script, admin=True, error=True)
    native(cfg, 'view_grant', 'GRANT CREATE VIEW ON SCHEMA "public" TO USER WIRE_HOST_TEST;\nCOMMIT;', admin=True)
    native_port, pg = reserve_ports(2)
    h.start('fulleq', native_port, native_port, [{'alias':'PgWire','port':pg}])
    ready=Client(h).login(); ready.close()
    config={'port':pg,'password':h.password}
    c=Libpq(config)
    results=[]
    def q(sql, rows=None, error=''):
        result=c.query(sql,True)[-1]
        require(result['sqlstate'] == error and (rows is None or result['rows']==rows), sql+': '+repr(result))
        results.append({'sql':sql,'result':result})
        return result
    try:
        q('SET standard_conforming_strings=on')
        for expression in CASES:
            expected=oracle[expression]
            result=q('SELECT '+expression, expected)
            require(result['columns'][0]['oid']==16, 'Comparison must return boolean')
        q("SELECT name,installed_version FROM pg_catalog.pg_available_extensions ORDER BY name", [['fasttrun','2.0'],['fulleq','2.0'],['mchar','2.3']])
        for expression in ("'a' == 'a '", "'a'::varchar(2) == 'a '::varchar(2)",
            "'a'::mchar == 'a'::mvarchar", "'a'::mchar == 'a'::varchar(2)",
            '1.25 == 1.25', 'CAST(1 AS NUMERIC(18,0)) == CAST(1 AS NUMERIC(18,0))',
            'CAST(1 AS REAL) == CAST(1 AS REAL)', 'CAST(1 AS DOUBLE PRECISION) == CAST(1 AS DOUBLE PRECISION)',
            "CAST('12:00:00+03:00' AS TIME WITH TIME ZONE) == CAST('09:00:00+00:00' AS TIME WITH TIME ZONE)"):
            q('SELECT '+expression,error='0A000')
        for binary in (0,1):
            result=c.params('SELECT CAST($1 AS INTEGER) == $2',[b'1',b'1'],[23,23],result_format=binary)
            require(not result['sqlstate'] and result['rows']==[['01' if binary else 't']], 'Extended comparison differs: '+repr(result))
            result=c.params('SELECT CAST($1 AS INTEGER) == $2',[None,None],[23,23],result_format=binary)
            require(not result['sqlstate'] and result['rows']==[['01' if binary else 't']], 'Extended NULL differs: '+repr(result))
            for name, oid in (('mchar',0x00fb0001),('mvarchar',0x00fb0002)):
                result=c.params(f'SELECT public.isfulleq_{name}($1,$2)',[b'a',b'A '],[oid,oid],result_format=binary)
                require(not result['sqlstate'] and result['rows']==[['01' if binary else 't']], 'Extended string equality differs: '+repr(result))
        q('CREATE VIEW fulleq_source AS SELECT false == true IS NULL AS a, true IS NOT DISTINCT FROM NULL == NULL AS b, false == true == false AS c FROM "SYSTEM"."RDB$DATABASE"')
        q('SELECT * FROM fulleq_source', [['f','t','t']])
        q("CREATE VIEW fulleq_string_source AS SELECT CAST('a' AS mchar) == CAST('A ' AS mchar) AS a FROM \"SYSTEM\".\"RDB$DATABASE\"")
        q('SELECT * FROM fulleq_string_source', [['t']])
        for view in ('fulleq_source','fulleq_string_source'):
            source=q('SELECT CAST("RDB$VIEW_SOURCE" AS VARCHAR(8191)) FROM "SYSTEM"."RDB$RELATIONS" '
                     'WHERE "RDB$SCHEMA_NAME"=\'public\' AND "RDB$RELATION_NAME"=\''+view+'\'')['rows'][0][0]
            native(dict(config_for(h)), 'reprepare_'+view,
                   'ALTER VIEW "public"."'+view+'" AS '+source+';\nCOMMIT;', admin=True)
        q('SELECT * FROM fulleq_source', [['f','t','t']])
        q('SELECT * FROM fulleq_string_source', [['t']])
        q('CREATE TEMP TABLE eq_keys(i integer,b bytea,m mchar(3))')
        q('CREATE INDEX eq_keys_i ON eq_keys(i)')
        for row in ("(1,'\\x01'::bytea,'a')","(2,'\\x0100'::bytea,'b')","(NULL,NULL,NULL)"):
            q('INSERT INTO eq_keys VALUES'+row)
        q('SELECT COUNT(*) FROM eq_keys WHERE i == 1', [['1']])
        q('SELECT COUNT(*) FROM eq_keys WHERE i == NULL', [['1']])
        q('SELECT COUNT(*) FROM eq_keys a JOIN eq_keys b ON a.i == b.i', [['3']])
        q('SELECT COUNT(*) FROM eq_keys a JOIN eq_keys b ON a.b == b.b', [['3']])
    finally:
        c.close()
    h.no_attachments();h.stop()
    native(cfg, 'native_views', 'SELECT * FROM "public"."fulleq_source";\nSELECT * FROM "public"."fulleq_string_source";', admin=True)
    native_port,pg=reserve_ports(2);h.start('fulleq_restart',native_port,native_port,[{'alias':'PgWire','port':pg}])
    ready=Client(h).login();ready.close()
    c=Libpq({'port':pg,'password':h.password})
    try:
        q('SELECT * FROM fulleq_source', [['f','t','t']])
        q('SELECT * FROM fulleq_string_source', [['t']])
    finally:c.close()
    h.summary.update(fulleq_cases=results,oracle_expressions=len(CASES),atomic_install=True,upgrade=True,stored_source_restart=True)
    h.no_attachments();h.native_probe('native_after')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firebird-dir',type=Path,required=True)
    parser.add_argument('--work-dir',type=Path,required=True)
    parser.add_argument('--oracle',type=Path,required=True)
    parser.add_argument('--timeout',type=float,default=120)
    args=parser.parse_args();work=args.work_dir.resolve();work.mkdir(parents=True,exist_ok=False)
    h=OneCHarness(args.firebird_dir.resolve(),work,args.timeout)
    try:
        run(h,json.loads(args.oracle.read_text()))
        h.summary['passed']=True
    except Exception as error:h.summary['error']=h.redact(str(error))
    finally:
        try:h.close()
        except Exception as error:h.summary.update(passed=False,shutdown_error=str(error))
        (work/'summary.json').write_text(json.dumps(h.summary,indent=2)+'\n')
        print(json.dumps({'passed':h.summary['passed'],'summary':str(work/'summary.json')}))
    return 0 if h.summary['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
