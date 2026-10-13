#!/usr/bin/env python3
"""P6 server acceptance on a private runtime: administration, resources and drivers."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import time

from pgwire_query_smoke import QueryHarness
from pgwire_startup_smoke import Client, error_fields, packet
from pgwire_transaction_smoke import TransactionClient
from wire_host_smoke import field, require, require_closed, reserve_ports


class ServerHarness(QueryHarness):
    def __init__(self, source, work, timeout, args):
        super().__init__(source, work, timeout, args)
        self.summary['stage'] = 'P6 Linux/Super Simple Query server acceptance'
        self.summary['known_limitations'] = [
            'Shutdown during ON CONNECT remains a native Debug engine failure, also without PgWire. '
            'It is excluded from this acceptance profile and reproduced separately by '
            'pgwire_attach_shutdown_smoke.py; it is not a passing check.',
            'Native READ COMMITTED READ CONSISTENCY AUTO COMMIT is outside the accepted profile.']
        self.server_mode = 'Super'

    def prepare(self):
        super().prepare()
        self.isql('server_fixtures', f"""CONNECT '{self.database}';
CREATE OR ALTER USER SYSDBA PASSWORD '{self.password}';
GRANT CREATE TABLE TO USER WIRE_HOST_TEST;
GRANT DELETE ON MON$STATEMENTS TO ROLE PGWIRE_OBSERVER;
GRANT DELETE ON MON$ATTACHMENTS TO ROLE PGWIRE_OBSERVER;
COMMIT;
QUIT;
""", embedded=True)

    def configure(self, configured_native_port, endpoints):
        super().configure(configured_native_port, endpoints)
        path = self.runtime / 'firebird.conf'
        path.write_text(path.read_text().replace('ServerMode = Super\n',
                                                f'ServerMode = {self.server_mode}\n'))

    def start_phase(self, name):
        native, configured, port = reserve_ports(3)
        self.start(name, native, configured, [{'alias': 'PgWire', 'port': port}])
        if self.server_mode == 'Super':
            ready = TransactionClient(self).login()
            ready.close()
            self.no_attachments()

    def sql(self, client, sql, state='I', command=None, error=None):
        result = client.query(sql.encode())
        require(result['state'] == state, f'{sql}: wrong transaction state {result}')
        if error:
            require(result['error'] is not None and result['error']['C'] == error and
                    result['command'] is None, f'{sql}: expected {error}, got {result}')
        else:
            require(result['error'] is None, f'{sql}: unexpected error {result}')
            if command:
                require(result['command'] == command, f'{sql}: wrong command {result}')
        return result

    def attachment_id(self, client):
        result = self.sql(client, 'SELECT CURRENT_CONNECTION FROM RDB$DATABASE')
        return int(result['rows'][0][0])

    def fatal(self, client, state):
        for _ in range(4):
            kind, payload = client.message()
            if kind == b'T':
                continue  # A running cursor may already have sent metadata.
            require(kind == b'E', f'expected terminal error, received {kind!r}')
            error = error_fields(payload)
            require(error['S'] == 'FATAL' and error['V'] == 'FATAL' and error['C'] == state,
                    f'wrong terminal diagnostic {error}')
            require_closed(client.connection)
            client.close(terminate=False)
            return
        raise RuntimeError('terminal error was not delivered')

    def wait_active(self, attachment, *, guard=None, lock_wait=False):
        # Synchronize on engine activity, not an assumed execution delay.
        deadline = time.monotonic() + self.timeout
        while True:
            stats = ('JOIN MON$RECORD_STATS R ON R.MON$STAT_ID=S.MON$STAT_ID '
                     'AND R.MON$STAT_GROUP=3 ' if lock_wait else '')
            active = self.scalar_native(f'SELECT COUNT(*) AS OBSERVED FROM MON$STATEMENTS S {stats}'
                f'WHERE S.MON$ATTACHMENT_ID={attachment} AND S.MON$STATE=1' +
                (' AND R.MON$RECORD_WAITS>0' if lock_wait else ''), 'active_statement')
            entered = guard is None or self.scalar_native(
                'SELECT GEN_ID(PGWIRE_LONG_GUARD,0) AS OBSERVED FROM RDB$DATABASE') > guard
            if active and entered:
                return
            require(time.monotonic() < deadline, 'query never reached the expected engine operation')

    def cancel_statements(self):
        for explicit, marker in ((False, 601), (True, 602)):
            client = TransactionClient(self).login()
            attachment = self.attachment_id(client)
            if explicit:
                self.sql(client, 'SET TRANSACTION', 'T', 'BEGIN')
                self.sql(client, 'INSERT INTO PGWIRE_EFFECTS VALUES(603)', 'T', 'INSERT 0 1')
            guard = self.scalar_native(
                'SELECT GEN_ID(PGWIRE_LONG_GUARD,0) AS OBSERVED FROM RDB$DATABASE')
            client.send(packet(b'Q', f'SELECT V FROM PGWIRE_LONG({marker})\0'.encode()))
            self.wait_active(attachment, guard=guard)
            self.isql('cancel_statement', f'DELETE FROM MON$STATEMENTS '
                      f'WHERE MON$ATTACHMENT_ID={attachment} AND MON$STATE=1;\nCOMMIT;\n')
            result = client.query_response()
            require(result['error'] is not None and result['error']['C'] == '57014' and
                    result['command'] is None and result['state'] == ('E' if explicit else 'I'),
                    f'administrative cancellation gave wrong result {result}')
            if explicit:
                self.sql(client, 'SELECT 1 FROM RDB$DATABASE', 'E', error='25P02')
                self.sql(client, 'ROLLBACK', command='ROLLBACK')
            self.sql(client, 'SELECT 42 FROM RDB$DATABASE', command='SELECT 1')
            client.close()
            self.no_attachments()
            require(self.scalar_native('SELECT COUNT(*) AS OBSERVED FROM PGWIRE_EFFECTS '
                                       f'WHERE ID IN ({marker},603)') == 0,
                    'cancelled work became visible to the native observer')

    def kill_attachments(self):
        for explicit, marker in ((False, 611), (True, 612)):
            client = TransactionClient(self).login()
            attachment = self.attachment_id(client)
            if explicit:
                self.sql(client, 'SET TRANSACTION', 'T', 'BEGIN')
                self.sql(client, f'INSERT INTO PGWIRE_EFFECTS VALUES({marker})', 'T', 'INSERT 0 1')
            self.isql('kill_attachment', f'DELETE FROM MON$ATTACHMENTS '
                      f'WHERE MON$ATTACHMENT_ID={attachment};\nCOMMIT;\n')
            client.send(packet(b'Q', b'SELECT 42 FROM RDB$DATABASE\0'))
            self.fatal(client, '57P01')
            self.no_attachments()
            require(self.scalar_native('SELECT COUNT(*) AS OBSERVED FROM PGWIRE_EFFECTS '
                                       f'WHERE ID={marker}') == 0, 'killed attachment committed')

    def gfix(self, label, *options):
        env = self.env.copy()
        # Credentials belong only to the cloned security database in this runtime.
        env.update(ISC_USER='SYSDBA', ISC_PASSWORD=self.password)
        result = subprocess.run([str(self.runtime / 'bin/gfix'),
            *options, f'inet://127.0.0.1:{self.native_port}/wire_host'], env=env,
            capture_output=True, text=True, timeout=self.timeout)
        (self.work / f'{label}.log').write_text(self.redact(result.stdout + result.stderr))
        require(result.returncode == 0, f'gfix {label} failed; see retained log')

    def database_shutdown(self):
        client = TransactionClient(self).login()
        self.sql(client, 'SET TRANSACTION', 'T', 'BEGIN')
        self.sql(client, 'INSERT INTO PGWIRE_EFFECTS VALUES(621)', 'T', 'INSERT 0 1')
        try:
            self.gfix('database_shutdown', '-shut', 'full', '-force', '0')
            client.send(packet(b'Q', b'SELECT 42 FROM RDB$DATABASE\0'))
            self.fatal(client, '57P01')
        finally:
            self.gfix('database_online', '-online', 'normal')
            client.close(terminate=False)
        self.no_attachments()
        require(self.scalar_native('SELECT COUNT(*) AS OBSERVED FROM PGWIRE_EFFECTS WHERE ID=621') == 0,
                'database shutdown committed the open transaction')
        healthy = TransactionClient(self).login()
        self.sql(healthy, 'SELECT 42 FROM RDB$DATABASE', command='SELECT 1')
        healthy.close()
        self.no_attachments()

    def resource_batch(self):
        for _ in range(8):
            client = TransactionClient(self).login()
            self.sql(client, 'SELECT ID FROM PGWIRE_ROWS', command='SELECT 3')
            self.sql(client, 'SELECT * FROM P6_MISSING_TABLE', error='42P01')
            self.sql(client, 'SET TRANSACTION', 'T', 'BEGIN')
            self.sql(client, 'INSERT INTO PGWIRE_EFFECTS VALUES(631)', 'T', 'INSERT 0 1')
            self.sql(client, 'ROLLBACK', command='ROLLBACK')
            require(self.scalar_native('SELECT COUNT(*) AS OBSERVED FROM MON$STATEMENTS S '
                'JOIN MON$ATTACHMENTS A ON A.MON$ATTACHMENT_ID=S.MON$ATTACHMENT_ID '
                "WHERE A.MON$REMOTE_VERSION='PgWire'") == 0, 'statement survived Query completion')
            self.no_transactions()
            client.close()
            self.no_attachments()
        proc = Path(f'/proc/{self.server.pid}')
        return {'fds': len(list((proc / 'fd').iterdir())),
                'threads': len(list((proc / 'task').iterdir()))}

    def resources(self):
        warmup = [self.resource_batch() for _ in range(2)]
        baseline = {key: max(item[key] for item in warmup) for key in ('fds', 'threads')}
        measured = [self.resource_batch() for _ in range(3)]
        self.summary['resources'] = {'warmup_sessions': 16, 'measured_sessions': 24,
            'baseline': baseline, 'batches': measured,
            'thread_scope': 'Diagnostic only: native server workers remain cached after detach. '
                            'Wire workers have fixed endpoint lifetime and are joined at shutdown.'}
        for counts in measured:
            require(counts['fds'] <= baseline['fds'],
                    f'FD growth after warmup: {baseline} -> {counts}')

    def driver_acceptance(self):
        env = self.env.copy()
        env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONDONTWRITEBYTECODE='1',
            PGWIRE_TEST_DSN=f'host=127.0.0.1 port={self.pg_port} dbname=wire_host user=WIRE_HOST_TEST',
            PGPASSWORD=self.password,
            PGWIRE_LIMITED_DSN=f'host=127.0.0.1 port={self.pg_port} dbname=wire_host user=WIRE_PG_OTHER',
            PGWIRE_LIMITED_PASSWORD=self.other_password,
            FB_NATIVE_DSN=f'inet://127.0.0.1:{self.native_port}/wire_host',
            FB_NATIVE_USER='WIRE_HOST_TEST', FB_NATIVE_PASSWORD=self.password,
            FB_ISQL=str(self.runtime / 'bin/isql'))
        package = Path(__file__).resolve().parents[1]
        result = subprocess.run([str(self.args.driver_python), '-B', '-m', 'pytest', '-q',
            '-p', 'no:cacheprovider', str(package / 'tests/test_pgwire_acceptance.py')],
            cwd=package, env=env, capture_output=True, text=True, timeout=180)
        output = self.redact(result.stdout + result.stderr)
        (self.work / 'pytest.log').write_text(output)
        require(result.returncode == 0 and not re.search(r'\d+ (skipped|xfailed)', output),
                'driver acceptance failed; see pytest.log')
        self.summary['pytest'] = output.strip().splitlines()[-1]
        self.no_attachments()

    def shutdown_lock_wait(self):
        locker = TransactionClient(self).login()
        self.sql(locker, 'SET TRANSACTION', 'T', 'BEGIN')
        self.sql(locker, 'UPDATE PGWIRE_ROWS SET PAYLOAD=999 WHERE ID=1', 'T', 'UPDATE 1')
        blocked = TransactionClient(self).login()
        attachment = self.attachment_id(blocked)
        self.sql(blocked, 'SET TRANSACTION READ COMMITTED WAIT', 'T', 'BEGIN')
        blocked.send(packet(b'Q', b'UPDATE PGWIRE_ROWS SET PAYLOAD=777 WHERE ID=1\0'))
        self.wait_active(attachment, lock_wait=True)
        password = Client(self)
        password.challenge()
        partial = Client(self)
        partial.send(b'\0\0')
        self.stop()
        for client in (locker, blocked, password, partial):
            require_closed(client.connection)
            client.close(terminate=False)
        output = self.isql('shutdown_rollback', f"CONNECT '{self.database}';\nSET LIST ON;\n"
            'SELECT PAYLOAD AS OBSERVED FROM PGWIRE_ROWS WHERE ID=1;\nROLLBACK;\nQUIT;\n', embedded=True)
        require(field(output, 'OBSERVED') == '31', 'shutdown committed a lock holder/waiter')

    def unsupported_server_mode(self):
        self.server_mode = 'SuperClassic'
        self.start_phase('superclassic')
        self.native_probe('superclassic_native')
        log = (self.runtime / 'firebird.log').read_text()
        require('Additional wire protocols currently require ServerMode=Super' in log,
                'unsupported server mode lacks a diagnostic')
        require('libPgWire.so' not in Path(f'/proc/{self.server.pid}/maps').read_text(),
                'unsupported mode loaded PgWire')
        try:
            unexpected = self.connect(self.pg_port)
        except ConnectionRefusedError:
            pass
        else:
            unexpected.close()
            raise RuntimeError('unsupported mode opened the PgWire listener')
        self.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firebird-dir', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--driver-python', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=30)
    args = parser.parse_args()
    source, work = args.firebird_dir.resolve(), args.work_dir.absolute()
    if not 0 < args.timeout <= 300 or work.exists() or work.is_symlink() or source in work.resolve().parents:
        parser.error('use timeout 0..300 and a new work directory outside the build')
    if not re.fullmatch(r'[A-Za-z0-9_./-]+', str(work)):
        parser.error('--work-dir must use safe ASCII path characters')
    for name in ('bin/firebird', 'bin/isql', 'bin/gfix', 'security6.fdb', 'plugins/libPgWire.so'):
        if not (source / name).is_file():
            parser.error(f'completed build required: missing {source / name}')
    args.driver_python = args.driver_python.absolute()
    if not args.driver_python.is_file():
        parser.error('--driver-python must include requirements-tests.txt dependencies')
    work.mkdir(mode=0o700)
    harness = ServerHarness(source, work.resolve(), args.timeout, args)
    try:
        harness.prepare()
        harness.start_phase('server')
        for name, action in (
            ('migrated_psycopg2_driver_acceptance', harness.driver_acceptance),
            ('warmup_attachment_statement_transaction_fd_thread_accounting', harness.resources),
            ('monitor_statement_cancellation_I_E_recovery', harness.cancel_statements),
            ('monitor_attachment_shutdown_fatal_rollback', harness.kill_attachments),
            ('database_shutdown_fatal_rollback_online', harness.database_shutdown),
            ('shutdown_lock_wait_password_partial_startup', harness.shutdown_lock_wait),
            ('superclassic_rejection_preserves_native', harness.unsupported_server_mode),
        ):
            harness.check(name, action)
        harness.summary['passed'] = True
    except (Exception, KeyboardInterrupt) as error:
        harness.summary['error'] = harness.redact(f'{type(error).__name__}: {error}')
    finally:
        try:
            harness.close()
        except Exception as error:
            harness.summary['passed'] = False
            harness.summary['shutdown_error'] = harness.redact(str(error))
        harness.summary['checks'] = harness.cases
        path = work / 'summary.json'
        path.write_text(json.dumps(harness.summary, indent=2) + '\n')
        print(json.dumps({'passed': harness.summary['passed'], 'summary': str(path)}))
    return 0 if harness.summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
