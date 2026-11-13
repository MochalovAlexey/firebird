#!/usr/bin/env python3
"""Check 1C datediff2 with libpq/native Firebird and an isolated PG oracle.

Only temporary runtimes/databases are used. PostgreSQL's reference body is the
function installed by 1C in the source database, not a Firebird translation.
"""
import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import subprocess
import sys

from pgwire_1c_smoke import Libpq, OneCHarness
from pgwire_namespace_checks import config_for, native, numbers
from pgwire_startup_smoke import Client
from wire_host_smoke import require, reserve_ports

PROFILE = Path(__file__).resolve().parents[1] / '1c'
sys.path.insert(0, str(PROFILE))
from install_compat import SCRIPTS

MODES = ('SECOND', 'MINUTE', 'HOUR', 'DAY', 'WEEK', 'MONTH', 'QUARTER', 'YEAR')
# Expressions from the source 1C function, preserving float8 epoch subtraction.
REFERENCE = dict(zip(MODES, (
    "date_part('epoch',d1)-date_part('epoch',d2)",
    "trunc((date_part('epoch',d1)-date_part('epoch',d2))/60)",
    "trunc((date_part('epoch',d1)-date_part('epoch',d2))/3600)",
    "cast(d1 as date)-cast(d2 as date)",
    "trunc((cast(d1 as date)-cast(d2 as date))/7.0)",
    "12*(date_part('year',d1)-date_part('year',d2))+date_part('month',d1)-date_part('month',d2)",
    "4*(date_part('year',d1)-date_part('year',d2))+date_part('quarter',d1)-date_part('quarter',d2)",
    "date_part('year',d1)-date_part('year',d2)")))


def cases():
    result = []
    def add(mode, d2, d1, error=False):
        # Native timestamp text accepts up to four fractional digits. Every
        # generated value is exactly representable at its 100-us precision.
        d2 = re.sub(r'(\.\d{4})00$', r'\1', d2) if d2 else None
        d1 = re.sub(r'(\.\d{4})00$', r'\1', d1) if d1 else None
        result.append(dict(mode=mode, d2=d2, d1=d1, error=error))
    base = datetime(2000, 1, 1)
    for micros in (0, 100, 499900, 500000, 500100, 1499900, 1500000,
                   1500100, 2499900, 2500000, 2500100, 59999900, 60000000,
                   60000100, 3599999900, 3600000000, 3600000100):
        for sign in (1, -1):
            d1 = (base + timedelta(microseconds=micros * sign)).isoformat(' ')
            for mode in MODES[:3]:
                add(mode, base.isoformat(' '), d1)
    for d2, d1 in (
        ('2024-02-28 23:59:59.9999', '2024-03-01 00:00:00.0001'),
        ('2025-12-31 23:59:59.9999', '2026-01-01 00:00:00.0001'),
        ('2026-03-31 23:59:59.9999', '2026-04-01 00:00:00.0001'),
        ('2026-01-01', '2026-01-07 23:59:59.9999'),
        ('2026-01-01', '2026-01-08'),
        ('2038-01-19 03:14:07.9999', '2038-01-19 03:14:10.4999'),
        ('1901-12-13 20:45:49.5001', '1901-12-13 20:45:52.0001'),
        ('9999-12-31 23:59:57.4999', '9999-12-31 23:59:59.9999')):
        for mode in MODES:
            add(mode, d2, d1)
            add(mode, d1, d2)
    for mode in MODES:
        for d2, d1 in ((None, '2000-01-01'), ('2000-01-01', None), (None, None)):
            add(mode, d2, d1)
    for seconds in (2147483647, 2147483647.5, 2147483648,
                    -2147483648, -2147483648.5, -2147483648.5001):
        add('SECOND', base.isoformat(' '),
            (base + timedelta(seconds=seconds)).isoformat(' '),
            error=seconds >= 2147483647.5 or seconds < -2147483648.5)
    for mode in ('second', 'SECOND ', ' SECOND', '', None, 'MILLISECOND', 'DAY\t'):
        add(mode, '2000-01-01', '2000-01-02', error=True)
        add(mode, None, None, error=True)
    return result


def literal(value):
    return 'NULL' if value is None else "'" + value.replace("'", "''") + "'"


def expression(case):
    return '"public"."datediff2"(' + ','.join((literal(case['mode']),
        'CAST(' + literal(case['d2']) + ' AS TIMESTAMP)',
        'CAST(' + literal(case['d1']) + ' AS TIMESTAMP)')) + ')'


def oracle(pgroot, work, test_cases, timeout):
    def command(name, *args):
        run = subprocess.run([str(pgroot/'bin'/name), *map(str, args)],
                             capture_output=True, text=True, timeout=timeout)
        (work/(name+'.log')).write_text(run.stdout+run.stderr)
        require(run.returncode == 0, 'Private PostgreSQL command failed: '+name)
    command('initdb', '-D', work/'pgdata', '-U', 'WIRE_HOST_TEST', '-A', 'trust',
            '--encoding=UTF8', '--locale=C.UTF-8')
    port = reserve_ports(1)[0]
    started, client = False, None
    try:
        command('pg_ctl', '-D', work/'pgdata', '-l', work/'postgres.log', '-o',
                f'-h 127.0.0.1 -p {port} -k {work}', '-w', 'start')
        started = True
        command('createdb', '-h', '127.0.0.1', '-p', port, '-U', 'WIRE_HOST_TEST', 'wire_host')
        client = Libpq({'port':port, 'password':'unused-private-trust'})
        sql = 'CREATE FUNCTION public.datediff2(mode varchar,d2 timestamp,d1 timestamp) RETURNS integer LANGUAGE plpgsql AS $$ BEGIN '
        sql += ' '.join(('IF' if i == 0 else 'ELSIF') + " mode='" + mode + "' THEN RETURN " + REFERENCE[mode] + ';'
                        for i, mode in enumerate(MODES))
        sql += ' END IF; END $$'
        require(not client.query(sql)[-1]['sqlstate'], 'Reference function installation failed')
        result = [client.query('SELECT '+expression(case), True)[-1] for case in test_cases]
        require(not client.query('CREATE FUNCTION public.vassn(bexpr boolean) RETURNS integer LANGUAGE plpgsql AS $$ BEGIN IF bexpr THEN RETURN 0; ELSE RETURN 2000000000; END IF; END $$')[-1]['sqlstate'], 'Reference vassn installation failed')
        vassn = [client.query('SELECT public.vassn('+value+')', True)[-1] for value in ('TRUE','FALSE','NULL')]
        (work/'oracle.json').write_text(json.dumps(result, indent=2)+'\n')
        (work/'vassn-oracle.json').write_text(json.dumps(vassn, indent=2)+'\n')
        return result, vassn
    finally:
        if client: client.close()
        if started: command('pg_ctl', '-D', work/'pgdata', '-m', 'fast', '-w', 'stop')


def run(h, pgroot):
    test_cases = cases()
    expected, vassn_expected = oracle(pgroot, h.work, test_cases, h.timeout)
    h.prepare()
    h.isql('admin', f"CONNECT '{h.database}';\nCREATE OR ALTER USER SYSDBA PASSWORD '{h.password}';\nCOMMIT;\nQUIT;\n", embedded=True)
    cfg = config_for(h)
    script_name = 'sql/install_1c_functions.sql'
    native(cfg, 'prerequisites', '\n'.join((PROFILE/name).read_text()
           for name in SCRIPTS[:SCRIPTS.index(script_name)]), admin=True)
    script = (PROFILE/script_name).read_text()
    native(cfg, 'atomic_failure', script.replace('\nCOMMIT;', '\nSELECT 1/0 FROM SYSTEM.RDB$DATABASE;\nCOMMIT;'), admin=True, error=True)
    absent = native(cfg, 'atomic_absent', "SELECT COUNT(*) AS N FROM SYSTEM.RDB$FUNCTIONS WHERE RDB$SCHEMA_NAME='public' AND RDB$FUNCTION_NAME IN ('datediff2','vassn');", admin=True)
    require(numbers(absent['output']) == [0], 'Failed installer retained application functions')
    native(cfg, 'install', script, admin=True)
    native(cfg, 'repeat_refused', script, admin=True, error=True)
    # Independent native execution of the same successful cases proves that the
    # function lives in the engine and is not a wire-level SQL interception.
    success = [(case, result) for case, result in zip(test_cases, expected) if not result['sqlstate']]
    output = native(cfg, 'native_values', '\n'.join('SELECT '+expression(case)+' AS N FROM SYSTEM.RDB$DATABASE;' for case, _ in success), admin=True)['output']
    values = re.findall(r'^N[ \t]+(<null>|-?\d+)[ \t]*$', output, re.M)
    require(values == ['<null>' if result['rows'][0][0] is None else result['rows'][0][0] for _, result in success], 'Native values differ from PG oracle')
    vassn = native(cfg, 'native_vassn', '\n'.join('SELECT "public"."vassn"('+value+') AS N FROM SYSTEM.RDB$DATABASE;' for value in ('TRUE','FALSE','NULL')), admin=True)
    require(numbers(vassn['output']) == [int(item['rows'][0][0]) for item in vassn_expected], 'Native vassn differs from PG oracle')
    native_port, pg = reserve_ports(2)
    h.start('datediff2', native_port, native_port, [{'alias':'PgWire','port':pg}])
    ready = Client(h).login(); ready.close()
    client = Libpq({'port':pg, 'password':h.password})
    results = []
    try:
        for case, reference in zip(test_cases, expected):
            result = client.query('SELECT '+expression(case), True)[-1]
            require(bool(reference['sqlstate']) == case['error'], 'Reference expectation differs: '+repr(case))
            if reference['sqlstate'] == '2F005':
                require(result['sqlstate'] == 'XX000', 'Unknown mode did not raise the documented native exception: '+repr(result))
            else:
                require(result['sqlstate'] == reference['sqlstate'] and result['rows'] == reference['rows'], 'Oracle mismatch: '+repr(case)+' '+repr(result)+' '+repr(reference))
            if not result['sqlstate']:
                require(result['columns'][0]['oid'] == 23, 'datediff2 must return int4')
            results.append(dict(case=case, result=result, reference_sqlstate=reference['sqlstate']))
        for binary in (0, 1):
            result = client.params('SELECT public.datediff2($1,$2,$3)',
                ['SECOND', '2000-01-01 00:00:00', '2000-01-01 00:00:02.5'], [1043,1114,1114], result_format=binary)
            require(not result['sqlstate'] and result['rows'] == [['00000002' if binary else '2']], 'Extended datediff2 differs: '+repr(result))
        lookup = client.query("SELECT proname FROM pg_catalog.pg_proc WHERE proname='datediff2'", True)[-1]
        require(not lookup['sqlstate'] and lookup['rows'] == [['datediff2']], 'Actual function is absent from pg_proc')
        for value, reference in zip(('TRUE','FALSE','NULL'), vassn_expected):
            result = client.query('SELECT public.vassn('+value+')', True)[-1]
            require(not result['sqlstate'] and result['rows'] == reference['rows'] and result['columns'][0]['oid'] == 23, 'vassn differs from PG oracle')
        client.recover()
    finally:
        client.close()
    h.no_attachments(); h.stop()
    # Minimum application privileges include invoking the exception on errors.
    cfg = config_for(h); cfg['native_port'] = None
    bad = native(cfg, 'native_invalid_mode', 'SELECT '+expression(dict(mode='second',d2=None,d1=None))+' FROM SYSTEM.RDB$DATABASE;', error=True)
    require(bad['sqlstates'] == ['HY000'], 'Native mode exception differs')
    h.summary.update(datediff2_cases=results, case_count=len(test_cases),
                     native_success_count=len(success), atomic_install=True,
                     repeated_install_refused=True, extended_text_and_binary=True,
                     real_pg_proc=True, vassn_cases=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firebird-dir', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--pg-root', type=Path, default=Path('/opt/pgpro/1c-18'))
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args()
    work = args.work_dir.resolve(); work.mkdir(parents=True, exist_ok=False)
    h = OneCHarness(args.firebird_dir.resolve(), work, args.timeout)
    try:
        run(h, args.pg_root.resolve())
        h.summary['passed'] = True
    except Exception as error:
        h.summary['error'] = h.redact(str(error))
    finally:
        try: h.close()
        except Exception as error: h.summary.update(passed=False, shutdown_error=str(error))
        (work/'summary.json').write_text(json.dumps(h.summary, indent=2)+'\n')
        print(json.dumps({'passed':h.summary['passed'], 'summary':str(work/'summary.json')}))
    return 0 if h.summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
