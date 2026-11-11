#!/usr/bin/env python3
"""Sequential Simple Query scripts in a new private runtime; no server timeouts.

Reuse the established native fixtures, raw decoder and real libpq. Commands are
sent unchanged, and failures stop the current script without undoing past COMMITs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from pgwire_1c_smoke import Libpq
from pgwire_extended_query_smoke import ExtendedClient, columns, parse, row
from pgwire_simple_query_smoke import SimpleHarness
from pgwire_startup_smoke import error_fields, packet
from wire_host_smoke import require, require_closed, reserve_ports


ANSWER = 'SELECT CAST(42 AS INTEGER) FROM "SYSTEM"."RDB$DATABASE"'


class ScriptClient(ExtendedClient):
    def script(self, sql, tags=(), *, state='I', error=None, rows=None, oids=None):
        self.send(packet(b'Q', sql.encode('utf-8') + b'\0'))
        events = self.events()
        diagnostic = repr([(k.decode(), error_fields(b) if k == b'E' else b[:120]) for k, b in events])
        require(events[-1] == (b'Z', state.encode()), f'{sql!r}: Ready differs: {diagnostic}')
        require([b[:-1].decode() for k, b in events if k == b'C'] == list(tags),
                f'{sql!r}: command tags differ: {diagnostic}')
        errors = [error_fields(b) for k, b in events if k == b'E']
        require(len(errors) == bool(error) and
                (not error or (errors[0]['C'] == error and errors[0]['S'] == 'ERROR')),
                f'{sql!r}: expected error {error}: {diagnostic}')
        if error:
            require(events[-2][0] == b'E', 'script continued after its first error')
        if rows is not None:
            actual = [[None if value is None else value.decode() for value in row(b)]
                      for k, b in events if k == b'D']
            require(actual == rows, f'{sql!r}: rows {actual!r}, expected {rows!r}')
        if oids is not None:
            require([[c['oid'] for c in columns(b)] for k, b in events if k == b'T'] == oids,
                    f'{sql!r}: OIDs differ: {diagnostic}')
        require(all(k in (b'T', b'D', b'C', b'E', b'I', b'Z') for k, _ in events),
                'unexpected script response')
        return events


def libpq_child():
    config = json.load(sys.stdin)
    client = None
    try:
        client = Libpq(config)
        results = client.query(ANSWER + '; SELECT CAST(NULL AS INTEGER) FROM "SYSTEM"."RDB$DATABASE"', True)
        require([r['tag'] for r in results] == ['SELECT 1', 'SELECT 1'] and
                [r['rows'] for r in results] == [[['42']], [[None]]] and
                all(r['columns'][0]['oid'] == 23 and not r['sqlstate'] for r in results),
                'libpq did not expose both script results')
        results = client.query('INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES(1091); COMMIT; INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES(1092); SELECT (', True)
        require([r['tag'] for r in results] == ['INSERT 0 1', 'COMMIT', 'INSERT 0 1', ''] and
                results[-1]['sqlstate'] == '42601' and client.pq.PQtransactionStatus(client.connection) == 0,
                'libpq sequential syntax failure/result order differs')
        client.recover()
        results = client.query('SET TRANSACTION; INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES(1093); SELECT 1/0 FROM "SYSTEM"."RDB$DATABASE"; ROLLBACK')
        require([r['tag'] for r in results] == ['BEGIN', 'INSERT 0 1', ''] and
                results[-1]['sqlstate'] == '22012' and client.pq.PQtransactionStatus(client.connection) == 3,
                'libpq did not observe explicit failed state and skipped ROLLBACK')
        results = client.query('ROLLBACK; ' + ANSWER, True)
        require([r['tag'] for r in results] == ['ROLLBACK', 'SELECT 1'] and
                results[-1]['rows'] == [['42']] and client.pq.PQtransactionStatus(client.connection) == 0,
                'libpq failed-block script recovery differs')
        print(json.dumps({'passed': True, 'libpq_version': client.pq.PQlibVersion(),
                          'multiple_results': 2, 'states': ['I', 'E', 'I']}))
        return 0
    except Exception as error:
        print(json.dumps({'passed': False, 'error': str(error).replace(config['password'], '[REDACTED]')}))
        return 1
    finally:
        if client:
            client.close()


class ScriptHarness(SimpleHarness):
    def seen(self, identifier, expected):
        actual = self.scalar_native(f'SELECT COUNT(*) AS OBSERVED FROM "PUBLIC"."P4_EFFECTS" WHERE "ID"={identifier}')
        require(actual == expected, f'native visibility for {identifier}: {actual}, expected {expected}')

    def finish(self, client):
        client.close()
        self.no_attachments()
        self.no_transactions()

    def results_and_ddl(self):
        c = ScriptClient(self).login()
        events = c.script(ANSWER + '; SELECT CAST(NULL AS INTEGER) FROM "SYSTEM"."RDB$DATABASE"',
                          ['SELECT 1', 'SELECT 1'], rows=[['42'], [None]], oids=[[23], [23]])
        require(b''.join(k for k, _ in events) == b'TDCTDCZ', 'multiple-result message order differs')
        c.script('CREATE TABLE "PUBLIC"."SCRIPT_ROWS"("ID" INTEGER); INSERT INTO "PUBLIC"."SCRIPT_ROWS" VALUES(7); SELECT "ID" FROM "PUBLIC"."SCRIPT_ROWS"', ['CREATE TABLE', 'INSERT 0 1', 'SELECT 1'], rows=[['7']])
        require(self.scalar_native('SELECT COUNT(*) AS OBSERVED FROM "PUBLIC"."SCRIPT_ROWS" WHERE "ID"=7') == 1,
                'script DDL/DML was not committed')
        c.script('DROP TABLE "PUBLIC"."SCRIPT_ROWS"', ['DROP TABLE'])
        # Two Query frames prove exactly one Ready belongs to each whole script.
        c.send(packet(b'Q', (ANSWER + '; ' + ANSWER).encode() + b'\0') +
               packet(b'Q', ANSWER.encode() + b'\0'))
        require([b''.join(k for k, _ in c.events()) for _ in range(2)] == [b'TDCTDCZ', b'TDCZ'],
                'a script emitted extra ReadyForQuery or consumed the next Query')
        self.finish(c)

    def boundaries(self):
        c = ScriptClient(self).login()
        c.script("SELECT CAST('a;''b' AS VARCHAR(20)) FROM \"SYSTEM\".\"RDB$DATABASE\"; /* ; */ "
                 "SELECT CAST(q'{Я; 'quoted'}' AS VARCHAR(30)) FROM \"SYSTEM\".\"RDB$DATABASE\"",
                 ['SELECT 1', 'SELECT 1'], rows=[["a;'b"], ["Я; 'quoted'"]])
        c.script('EXECUTE BLOCK RETURNS("V" INTEGER) AS BEGIN "V"=1; SUSPEND; "V"=2; SUSPEND; END; SELECT 3 AS "semi;colon" FROM "SYSTEM"."RDB$DATABASE"', ['EXECUTE BLOCK', 'SELECT 1'],
                 rows=[['1'], ['2'], ['3']])
        c.script('-- head\n' + ANSWER + '; /* closed tail */ -- EOF', ['SELECT 1'], rows=[['42']])
        for empty in ('', ' \t\r\n', '/* closed */ -- EOF'):
            events = c.script(empty)
            require(events == [(b'I', b''), (b'Z', b'I')], 'empty script response differs')
        c.script(';', error='42601')  # Keep the existing standalone-semicolon contract.
        c.script(ANSWER, ['SELECT 1'], rows=[['42']])  # Final command needs no semicolon.
        self.finish(c)

    def implicit_error(self):
        c = ScriptClient(self).login()
        guard = self.scalar_native('SELECT GEN_ID("PUBLIC"."P4_GUARD",0) AS OBSERVED FROM "SYSTEM"."RDB$DATABASE"')
        c.script('INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES(1001); SELECT 1/0 FROM "SYSTEM"."RDB$DATABASE"; SELECT GEN_ID("PUBLIC"."P4_GUARD",1) FROM "SYSTEM"."RDB$DATABASE"', ['INSERT 0 1'], error='22012')
        self.seen(1001, 0)
        require(self.scalar_native('SELECT GEN_ID("PUBLIC"."P4_GUARD",0) AS OBSERVED FROM "SYSTEM"."RDB$DATABASE"') == guard,
                'the script continued after failure')
        c.script('INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES(1002); SELECT (', ['INSERT 0 1'], error='42601')
        self.seen(1002, 0)
        c.script('INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES(1003); SELECT CAST(1 AS DECFLOAT(16)) FROM "SYSTEM"."RDB$DATABASE"',
                 ['INSERT 0 1'], error='0A000')
        self.seen(1003, 0)
        c.script('INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES(1004); COMMIT; USING IN AUTONOMOUS TRANSACTION DO INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES(GEN_ID("PUBLIC"."P4_GUARD",1)); INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES(1005)', ['INSERT 0 1', 'COMMIT'], error='0A000')
        self.seen(1004, 1)
        self.seen(1005, 0)
        require(self.scalar_native('SELECT GEN_ID("PUBLIC"."P4_GUARD",0) AS OBSERVED FROM "SYSTEM"."RDB$DATABASE"') == guard,
                'unsupported wrapper executed before capability rejection')
        c.script(ANSWER, ['SELECT 1'], rows=[['42']])
        self.finish(c)

    def committed_prefix(self):
        c = ScriptClient(self).login()
        for index, (tail, state) in enumerate([('SELECT (', '42601'),
                ('SELECT 1/0 FROM "SYSTEM"."RDB$DATABASE"', '22012'), ("SELECT 'unterminated", '42601'),
                ('/* unterminated', '42601')]):
            saved, rolled_back = 1010 + index * 2, 1011 + index * 2
            c.script(f'INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES({saved}); COMMIT; '
                     f'INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES({rolled_back}); {tail}',
                     ['INSERT 0 1', 'COMMIT', 'INSERT 0 1'], error=state)
            self.seen(saved, 1)
            self.seen(rolled_back, 0)
        # A broken trailing comment must not hide the already-delimited COMMIT.
        c.script('INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES(1018); COMMIT; /* unterminated',
                 ['INSERT 0 1', 'COMMIT'], error='42601')
        self.seen(1018, 1)
        c.script('INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES(1019); ROLLBACK; INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES(1020)',
                 ['INSERT 0 1', 'ROLLBACK', 'INSERT 0 1'])
        self.seen(1019, 0)
        self.seen(1020, 1)
        self.finish(c)

    def explicit_states(self):
        c = ScriptClient(self).login()
        c.script('SET TRANSACTION; INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES(1030); SELECT 1/0 FROM "SYSTEM"."RDB$DATABASE"; ROLLBACK', ['BEGIN', 'INSERT 0 1'], state='E', error='22012')
        self.seen(1030, 0)
        c.script(ANSWER + '; ROLLBACK', state='E', error='25P02')
        c.script('SELECT (; ROLLBACK', state='E', error='42601')
        c.script('ROLLBACK; ' + ANSWER, ['ROLLBACK', 'SELECT 1'], rows=[['42']])
        c.script('SET TRANSACTION; INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES(1031)', ['BEGIN', 'INSERT 0 1'], state='T')
        self.seen(1031, 0)
        c.script('COMMIT; ' + ANSWER, ['COMMIT', 'SELECT 1'], rows=[['42']])
        self.seen(1031, 1)
        c.script('SET TRANSACTION; SELECT 1/0 FROM "SYSTEM"."RDB$DATABASE"', ['BEGIN'], state='E', error='22012')
        c.script('COMMIT; ' + ANSWER, ['ROLLBACK', 'SELECT 1'], rows=[['42']])
        # Native SET TRANSACTION does not promote an existing implicit segment.
        c.script('INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES(1032); SET TRANSACTION; COMMIT',
                 ['INSERT 0 1'], error='25001')
        self.seen(1032, 0)
        self.finish(c)

    def native_session_and_retain(self):
        c = ScriptClient(self).login()
        value = 'SELECT CAST(12.5 AS DOUBLE PRECISION) FROM "SYSTEM"."RDB$DATABASE"'
        c.script('SET BIND OF DOUBLE PRECISION TO VARCHAR(40); SELECT 1/0 FROM "SYSTEM"."RDB$DATABASE"',
                 ['SET'], error='22012')
        c.script(value, ['SELECT 1'], oids=[[1043]])  # Attachment setting survives rollback.
        c.script('ALTER SESSION RESET; ' + value, ['RESET', 'SELECT 1'], oids=[[701]])
        c.script('SET TRANSACTION; INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES(1040); SAVEPOINT "S"; INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES(1041); ROLLBACK TO SAVEPOINT "S"; RELEASE SAVEPOINT "S"; COMMIT RETAIN; INSERT INTO "PUBLIC"."P4_EFFECTS" VALUES(1042); ROLLBACK RETAIN; SELECT COUNT(*) FROM "PUBLIC"."P4_EFFECTS" WHERE "ID"=1040',
                 ['BEGIN', 'INSERT 0 1', 'SAVEPOINT', 'INSERT 0 1', 'ROLLBACK', 'RELEASE',
                  'COMMIT', 'INSERT 0 1', 'ROLLBACK', 'SELECT 1'], state='T', rows=[['1']])
        self.seen(1040, 1)
        self.seen(1041, 0)
        self.seen(1042, 0)
        c.script('ROLLBACK; ' + ANSWER, ['ROLLBACK', 'SELECT 1'], rows=[['42']])
        self.finish(c)

    def commit_error(self):
        c = ScriptClient(self).login()
        c.send(packet(b'Q', (ANSWER + '; SELECT "V" FROM "PUBLIC"."P4_COMMIT_ERROR"').encode() + b'\0'))
        events = c.events(end=b'E')
        require(b''.join(k for k, _ in events) == b'TDCTDE', 'final commit failure emitted success/Ready')
        diagnostic = error_fields(events[-1][1])
        require(diagnostic['C'] == 'XX000' and diagnostic['S'] == 'FATAL', 'commit error was not fatal')
        require_closed(c.connection)
        c.close(terminate=False)
        self.no_attachments()
        self.no_transactions()
        self.seen(705, 0)

    def libpq(self):
        result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--child'],
            input=json.dumps({'port': self.pg_port, 'password': self.password, 'libpq': self.args.libpq}),
            capture_output=True, text=True, cwd=self.temp, env=self.driver_environment(), timeout=self.timeout)
        (self.work / 'libpq.log').write_text(self.redact(result.stdout + result.stderr))
        require(result.returncode == 0, 'libpq scripts failed; see libpq.log')
        self.summary['libpq'] = json.loads(result.stdout)
        self.no_attachments()
        for identifier, count in ((1091, 1), (1092, 0), (1093, 0)):
            self.seen(identifier, count)

    def run_scripts(self):
        native, configured, port = reserve_ports(3)
        self.start('scripts', native, configured, [{'alias': 'PgWire', 'port': port}])
        ready = ScriptClient(self).login()
        self.finish(ready)
        self.native_probe('native_before_scripts')
        for name in ('results_and_ddl', 'boundaries', 'implicit_error', 'committed_prefix',
                     'explicit_states', 'native_session_and_retain', 'commit_error', 'libpq'):
            self.check(name, getattr(self, name))
        self.native_probe('native_after_scripts')
        self.stop()


def main():
    if sys.argv[1:] == ['--child']:
        return libpq_child()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firebird-dir', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--libpq')
    parser.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args()
    source, work = args.firebird_dir.resolve(), args.work_dir.absolute()
    if work.exists() or work.is_symlink() or source in work.resolve().parents:
        parser.error('--work-dir must be new and outside --firebird-dir')
    if not re.fullmatch(r'[A-Za-z0-9_./-]+', str(work)) or not 0 < args.timeout <= 300:
        parser.error('use an ASCII work path and external timeout in (0,300] seconds')
    for name in ('bin/firebird', 'bin/isql', 'security6.fdb', 'plugins/libPgWire.so'):
        if not (source / name).is_file():
            parser.error('completed build required: missing ' + str(source / name))
    work.mkdir(mode=0o700)
    harness = ScriptHarness(source, work.resolve(), args.timeout, args)
    harness.summary['stage'] = 'Sequential Simple Query scripts, native transaction/session semantics'
    harness.summary['module_sha256'] = hashlib.sha256((source / 'plugins/libPgWire.so').read_bytes()).hexdigest()
    try:
        harness.prepare()
        harness.run_scripts()
        harness.summary['passed'] = True
    except (Exception, KeyboardInterrupt) as error:
        harness.summary['error'] = harness.redact(f'{type(error).__name__}: {error}')
    finally:
        try:
            harness.close()
        except Exception as error:
            harness.summary.update(passed=False, shutdown_error=harness.redact(error))
        harness.summary['checks'] = harness.cases
        path = work / 'summary.json'
        path.write_text(json.dumps(harness.summary, indent=2) + '\n')
        print(json.dumps({'passed': harness.summary['passed'], 'summary': str(path)}))
    return 0 if harness.summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
