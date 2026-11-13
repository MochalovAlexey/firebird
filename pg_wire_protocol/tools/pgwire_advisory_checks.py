#!/usr/bin/env python3
"""Real libpq transaction advisory locks, using an isolated Firebird runtime.

The statement timeout bounds deliberate lock conflicts; it is not an idle or
transport timeout. No existing database, server configuration or credentials are
used. The child-process watchdog bounds regressions in lock acquisition itself.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from pgwire_1c_smoke import Libpq, OneCHarness
from pgwire_startup_smoke import Client
from wire_host_smoke import require, reserve_ports


def checks(config):
    clients = [Libpq(config) for _ in range(3)]
    a, b, c = clients
    report = []

    def sql(client, text, error=None):
        result = client.query(text, True)[-1]
        require(result['sqlstate'] == (error or ''), text + ': ' + repr(result))
        return result

    def lock(client, key, shared=False):
        result = sql(client, 'SELECT pg_catalog.pg_advisory_xact_lock' +
                     ('_shared' if shared else '') + f'({key})')
        require(result['rows'] == [['0']] and result['columns'][0]['oid'] == 23,
                'Profile lock return differs: ' + repr(result))

    def blocked(key, shared=False):
        sql(c, 'BEGIN')
        sql(c, 'SELECT pg_catalog.pg_advisory_xact_lock' +
            ('_shared' if shared else '') + f'({key})', '57014')
        require(c.pq.PQtransactionStatus(c.connection) == 3, 'Timeout must enter E')
        sql(c, 'ROLLBACK')

    try:
        for text in ('SET STATEMENT_TIMEOUT = 0', 'SET STATEMENT_TIMEOUT TO DEFAULT',
                     'SET STATEMENT_TIMEOUT = 100'):
            result = sql(c, text)
            require(result['tag'] == 'SET', 'Timeout command tag differs: ' + repr(result))
        for text in ('SET STATEMENT_TIMEOUT TO -1', "SET STATEMENT_TIMEOUT TO '1s'",
                     'SET LOCAL STATEMENT_TIMEOUT TO 1', 'SET STATEMENT_TIMEOUT TO ON'):
            sql(c, text, '42601')
            require(c.pq.PQtransactionStatus(c.connection) == 0, 'Rejected SET must remain idle')
        sql(c, 'SET STATEMENT_TIMEOUT TO 100')
        report.append('statement_timeout zero/DEFAULT/equal forms; unsupported values reject without entering T')
        sql(a, 'BEGIN'); sql(b, 'BEGIN')
        lock(a, -9223372036854775808, True); lock(b, -9223372036854775808, True)
        lock(c, 9223372036854775807)
        blocked(-9223372036854775808)
        sql(a, 'COMMIT'); blocked(-9223372036854775808)
        sql(b, 'ROLLBACK'); lock(c, -9223372036854775808)
        report.append('shared/shared; exclusive conflict; signed bigint keys; commit and rollback release')

        sql(a, 'BEGIN'); lock(a, 11); lock(a, 11, True)
        blocked(11, True)
        sql(a, 'SAVEPOINT s'); lock(a, 12)
        blocked(12)
        sql(a, 'ROLLBACK TO s'); lock(c, 12); blocked(11)
        sql(a, 'ROLLBACK')
        report.append('reacquire does not downgrade; savepoint rollback releases only new keys')

        sql(a, 'BEGIN'); lock(a, 13, True)
        sql(a, 'SAVEPOINT s'); lock(a, 13)
        blocked(13, True)
        sql(a, 'ROLLBACK TO s')
        sql(b, 'BEGIN'); lock(b, 13, True); blocked(13)
        sql(b, 'ROLLBACK'); sql(a, 'ROLLBACK'); lock(c, 13)
        report.append('savepoint rollback restores shared mode after exclusive upgrade')

        sql(a, 'BEGIN'); sql(a, 'SAVEPOINT outer_scope'); sql(a, 'SAVEPOINT inner_scope')
        lock(a, 14); sql(a, 'RELEASE SAVEPOINT inner_scope'); blocked(14)
        sql(a, 'ROLLBACK TO outer_scope'); lock(c, 14); sql(a, 'ROLLBACK')
        report.append('savepoint release merges lock undo into parent')

        sql(a, 'BEGIN'); lock(a, 21)
        sql(a, 'SAVEPOINT outer_scope'); lock(a, 22)
        sql(a, 'SAVEPOINT inner_scope'); lock(a, 23)
        sql(a, 'SELECT 1 / 0', '22012')
        lock(c, 23); blocked(22); blocked(21)
        sql(a, 'ROLLBACK TO outer_scope'); lock(c, 22); blocked(21)
        sql(a, 'ROLLBACK'); lock(c, 21)
        report.append('E immediately releases current subtransaction locks; outer recovery preserved')

        sql(a, 'BEGIN'); lock(a, 24)
        first = a.result(a.pq.PQprepare(a.connection, b'advisory_duplicate', b'SELECT 1', 0, None))
        require(not first['sqlstate'], 'Initial prepare failed')
        duplicate = a.result(a.pq.PQprepare(a.connection, b'advisory_duplicate', b'SELECT 1', 0, None))
        require(duplicate['sqlstate'] == '42P05', 'Expected protocol-only duplicate name')
        lock(c, 24); sql(a, 'ROLLBACK')
        report.append('protocol-only Extended error releases failed transaction locks before rollback')

        lock(a, 31); lock(b, 31)
        sql(a, 'SELECT pg_catalog.pg_advisory_xact_lock(32); SELECT 1 / 0', '22012')
        lock(b, 32)
        sql(a, 'BEGIN'); lock(a, 33); a.close(); lock(b, 33)
        report.append('implicit success/error and disconnect release locks')

        result = b.params('SELECT pg_catalog.pg_advisory_xact_lock($1)',
                          [(-42).to_bytes(8, 'big', signed=True)], [20], [1], result_format=1)
        require(not result['sqlstate'] and result['rows'] == [['00000000']],
                'Binary bigint Bind or integer output differs: '+repr(result))
        result = sql(b, 'SELECT pg_catalog.pg_advisory_xact_lock(NULL)')
        require(result['rows'] == [[None]], 'NULL must not acquire a key')
        report.append('Extended binary bigint argument/result and strict NULL')
        sql(c, 'SET STATEMENT_TIMEOUT TO DEFAULT')
        return report
    finally:
        for client in clients:
            client.close()


def main():
    if sys.argv[1:] == ['--child']:
        try:
            print(json.dumps({'checks': checks(json.load(sys.stdin)), 'passed': True}))
            return 0
        except Exception as error:
            print(json.dumps({'passed': False, 'error': str(error)}))
            return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firebird-dir', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args()
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=False)
    harness = OneCHarness(args.firebird_dir.resolve(), work, args.timeout)
    try:
        harness.prepare()
        api = work / 'advisory_api'
        compiled = subprocess.run(['c++', '-std=c++17', '-I', str(harness.runtime/'include'),
            str(Path(__file__).with_name('pgwire_advisory_api.cpp')), '-L', str(harness.runtime/'lib'),
            '-lfbclient', '-o', str(api)], capture_output=True, text=True, timeout=args.timeout)
        (work/'native-api-build.log').write_text(compiled.stdout + compiled.stderr)
        require(compiled.returncode == 0, 'Native API probe did not compile')
        native_api = subprocess.run([str(api), str(harness.database.parent/'native_api.fdb')], env=harness.env,
            capture_output=True, text=True, timeout=args.timeout)
        (work/'native-api.log').write_text(native_api.stdout + native_api.stderr)
        require(native_api.returncode == 0, 'Native API lock probe failed')
        harness.summary['native_api'] = json.loads(native_api.stdout)
        script = Path(__file__).parents[1] / '1c/sql/install_advisory_locks.sql'
        harness.isql('advisory_fixture', f"CONNECT '{harness.database}';\n"
            'CREATE SCHEMA "public";\nCREATE SCHEMA "pg_catalog";\n'
            'GRANT USAGE ON SCHEMA "public" TO PUBLIC;\n'
            'GRANT USAGE ON SCHEMA "pg_catalog" TO PUBLIC;\nCOMMIT;\n' +
            script.read_text() + '\nQUIT;\n', embedded=True)
        native, pg = reserve_ports(2)
        harness.start('advisory', native, native, [{'alias': 'PgWire', 'port': pg}])
        ready = Client(harness).login(); ready.close()
        harness.native_probe('native_before')
        config = {'port': pg, 'password': harness.password}
        result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--child'],
            input=json.dumps(config), capture_output=True, text=True, timeout=args.timeout)
        (work / 'libpq.log').write_text(harness.redact(result.stdout + result.stderr))
        require(result.stdout, 'No child report')
        harness.summary['advisory'] = json.loads(result.stdout)
        require(result.returncode == 0, 'Advisory checks failed: ' + result.stdout)
        harness.no_attachments()
        harness.native_probe('native_after')
        harness.summary['passed'] = True
    except Exception as error:
        harness.summary['error'] = harness.redact(str(error))
    finally:
        try:
            harness.close()
        except Exception as error:
            harness.summary.update(passed=False, shutdown_error=str(error))
        (work / 'summary.json').write_text(json.dumps(harness.summary, indent=2) + '\n')
        print(json.dumps({'passed': harness.summary['passed'], 'summary': str(work/'summary.json')}))
    return 0 if harness.summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
