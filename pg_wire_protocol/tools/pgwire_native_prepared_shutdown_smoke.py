#!/usr/bin/env python3
"""Native TCP shutdown with two prepared handles, using a fresh private runtime.

PgWire endpoints are disabled. Both prepare-only and open-cursor cases must
shut down cleanly; unfinished cursor changes must roll back. --timeout is an
external test watchdog, not a server setting.
"""
import argparse
import json
from pathlib import Path
import re
import select
import shutil
import subprocess

from pgwire_startup_smoke import StartupHarness
from wire_host_smoke import field, require, reserve_ports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firebird-dir', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--cxx', default='c++')
    parser.add_argument('--timeout', type=float, default=30)
    args = parser.parse_args()
    source, work = args.firebird_dir.resolve(), args.work_dir.absolute()
    if work.exists() or work.is_symlink() or source in work.resolve().parents:
        parser.error('--work-dir must be new and outside the build')
    if not re.fullmatch(r'[A-Za-z0-9_./-]+', str(work)) or not 0 < args.timeout <= 300:
        parser.error('use safe ASCII work path and timeout 0..300')
    for name in ('bin/firebird', 'bin/isql', 'security6.fdb', 'plugins/libPgWire.so',
                 'include/firebird/Interface.h', 'lib/libfbclient.so'):
        if not (source / name).is_file():
            parser.error(f'completed build required: missing {source / name}')
    compiler = shutil.which(args.cxx)
    if not compiler:
        parser.error('C++ compiler unavailable')
    work.mkdir(mode=0o700)
    h = StartupHarness(source, work.resolve(), args.timeout)
    h.summary.update(stage='native prepared-handle shutdown regression',
                     transport='native Firebird TCP only', native_only=True)
    client = None
    client_log = None
    try:
        h.prepare()
        sql = f"""CONNECT '{h.database}';
DROP TRIGGER PG_DENY_CONNECT;
CREATE TABLE P7_NATIVE_EFFECTS (ID INTEGER NOT NULL PRIMARY KEY);
SET TERM ^ ;
CREATE PROCEDURE P7_NATIVE_STREAM RETURNS(V INTEGER) AS
BEGIN
  INSERT INTO P7_NATIVE_EFFECTS VALUES(1);
  V = 1;
  WHILE (V <= 3) DO BEGIN SUSPEND; V = V + 1; END
END^
SET TERM ; ^
GRANT EXECUTE ON PROCEDURE P7_NATIVE_STREAM TO USER WIRE_HOST_TEST;
GRANT INSERT ON P7_NATIVE_EFFECTS TO PROCEDURE P7_NATIVE_STREAM;
COMMIT;
QUIT;
"""
        (work / 'fixture.sql').write_text(sql)
        h.isql('native_fixture', sql, embedded=True)
        driver = work / 'native_prepared_shutdown'
        compiled = subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', '-O2',
            '-I', str(h.runtime / 'include'), str(Path(__file__).with_name('pgwire_native_prepared_shutdown.cpp')),
            str(h.runtime / 'lib/libfbclient.so'), '-Wl,-rpath-link,' + str(h.runtime / 'lib'), '-o', str(driver)],
            capture_output=True, text=True, timeout=args.timeout)
        (work / 'compile.log').write_text(compiled.stdout + compiled.stderr)
        require(compiled.returncode == 0, 'native driver compilation failed; see compile.log')
        for mode in ('prepare', 'cursor'):
            native, configured = reserve_ports(2)
            h.start('native_' + mode, native, configured, [])
            h.native_probe('native_before_' + mode)
            env = h.env.copy()
            env.update(ISC_USER='WIRE_HOST_TEST', ISC_PASSWORD=h.password)
            client_log = work / (mode + '-client.log')
            client = subprocess.Popen([str(driver), f'inet://127.0.0.1:{native}/wire_host', mode],
                env=env, cwd=h.temp, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, errors='replace')
            require(select.select([client.stdout], [], [], args.timeout)[0], 'native client readiness watchdog expired')
            line = client.stdout.readline()
            client_log.write_text(h.redact(line))
            require(line == ('PREPARED 2\n' if mode == 'prepare' else 'FETCHED 1\n'),
                    'native client did not reach required state; see client log')
            h.stop()
            output, _ = client.communicate(input='q', timeout=args.timeout)
            with client_log.open('a') as log:
                log.write(h.redact(output))
            require(client.returncode == 0, 'native client cleanup failed')
            h.cases.append(mode + '_two_handles_clean_server_shutdown')
            client = None
        output = h.isql('native_rollback', f"CONNECT '{h.database}';\nSET LIST ON;\n"
            'SELECT COUNT(*) AS OBSERVED FROM P7_NATIVE_EFFECTS;\nCOMMIT;\nQUIT;\n', embedded=True)
        require(field(output, 'OBSERVED') == '0', 'unfinished native cursor work was committed')
        h.cases.append('unfinished_cursor_write_rolled_back')
        h.summary['passed'] = True
    except (Exception, KeyboardInterrupt) as error:
        h.summary['error'] = h.redact(f'{type(error).__name__}: {error}')
    finally:
        try:
            h.close()
        except Exception as error:
            h.summary['passed'] = False
            h.summary['shutdown_error'] = h.redact(str(error))
        if client is not None:
            try:
                output, _ = client.communicate(input='q', timeout=5)
            except subprocess.TimeoutExpired:
                client.kill()
                output, _ = client.communicate(timeout=5)
                h.summary['client_forced_kill'] = True
            with client_log.open('a') as log:
                log.write(h.redact(output))
        h.summary['checks'] = h.cases
        path = work / 'summary.json'
        path.write_text(json.dumps(h.summary, indent=2) + '\n')
        print(json.dumps({'passed': h.summary['passed'], 'summary': str(path)}, indent=2))
    return 0 if h.summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
