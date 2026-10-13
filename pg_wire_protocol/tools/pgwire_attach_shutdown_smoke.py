#!/usr/bin/env python3
"""Reproduce shutdown during a real ON CONNECT trigger in an isolated runtime.

Uses PostgreSQL TCP by default; --native-only disables PgWire and connects with
native isql. A sequence confirms entry into the trigger before SIGTERM. Success
requires clean server shutdown and rollback; assertions remain test failures.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import time

from pgwire_query_smoke import QueryHarness
from pgwire_startup_smoke import Client, packet
from wire_host_smoke import field, require, require_closed, reserve_ports


def prepare_trigger(harness):
    sql = f"""CONNECT '{harness.database}';
CREATE SEQUENCE P6_ATTACH_GUARD;
GRANT USAGE ON SEQUENCE P6_ATTACH_GUARD TO USER WIRE_HOST_TEST;
SET TERM ^ ;
CREATE TRIGGER P6_ATTACH_RUNNING ACTIVE ON CONNECT POSITION 100 AS
  DECLARE VARIABLE V BIGINT;
BEGIN
  IF (CURRENT_USER = 'WIRE_PG_OTHER') THEN
  BEGIN
    INSERT INTO PGWIRE_EFFECTS VALUES (691);
    V = GEN_ID(P6_ATTACH_GUARD, 1);
    SELECT COUNT(*) FROM PGWIRE_MANY A CROSS JOIN PGWIRE_MANY B
      CROSS JOIN PGWIRE_MANY C CROSS JOIN PGWIRE_MANY D
      CROSS JOIN PGWIRE_MANY E INTO :V;
  END
END^
SET TERM ; ^
GRANT INSERT ON PGWIRE_EFFECTS TO TRIGGER P6_ATTACH_RUNNING;
GRANT SELECT ON PGWIRE_MANY TO TRIGGER P6_ATTACH_RUNNING;
GRANT USAGE ON SEQUENCE P6_ATTACH_GUARD TO TRIGGER P6_ATTACH_RUNNING;
COMMIT;
QUIT;
"""
    (harness.work / 'attach_fixture.sql').write_text(sql)
    harness.isql('attach_fixture', sql, embedded=True)


def collect_native(harness, process):
    output, _ = process.communicate(timeout=5)
    (harness.work / 'pending_native_attach.log').write_text(harness.redact(output))
    harness.summary['native_attach_exit_code'] = process.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firebird-dir', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--native-only', action='store_true',
                        help='disable PgWire and reproduce through native Firebird TCP')
    args = parser.parse_args()
    source, work = args.firebird_dir.resolve(), args.work_dir.absolute()
    if work.exists() or work.is_symlink() or source in work.resolve().parents:
        parser.error('--work-dir must be new and outside the build')
    if not re.fullmatch(r'[A-Za-z0-9_./-]+', str(work)):
        parser.error('--work-dir must use safe ASCII path characters')
    # QueryHarness supplies the same database/security fixtures in both modes.
    for name in ('bin/firebird', 'bin/isql', 'security6.fdb', 'plugins/libPgWire.so'):
        if not (source / name).is_file():
            parser.error(f'completed build required: missing {source / name}')
    work.mkdir(mode=0o700)
    harness = QueryHarness(source, work.resolve(), 30, args)
    harness.summary.update(stage='P6 shutdown during real ON CONNECT attachment',
                           transport='native Firebird TCP with PgWire disabled' if args.native_only
                           else 'PostgreSQL protocol 3.0 TCP', native_only=args.native_only)
    native_client = None
    try:
        harness.prepare()
        prepare_trigger(harness)
        native, configured, port = reserve_ports(3)
        endpoints = [] if args.native_only else [{'alias': 'PgWire', 'port': port}]
        harness.start('attach_shutdown', native, configured, endpoints)
        if args.native_only:
            harness.native_probe('native_before_attach')
            env = harness.env.copy()
            env.update(ISC_USER='WIRE_PG_OTHER', ISC_PASSWORD=harness.other_password)
            native_client = subprocess.Popen([str(harness.runtime / 'bin/isql'), '-quiet', '-bail',
                f'inet://127.0.0.1:{native}/wire_host'], env=env, cwd=harness.temp,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors='replace')
        else:
            ready = Client(harness).login()
            ready.close()
            harness.no_attachments()
            pending = Client(harness)
            pending.challenge(user=b'WIRE_PG_OTHER')
            pending.send(packet(b'p', harness.other_password.encode() + b'\0'))

        # Observe native execution, not an assumed delay after sending a password.
        deadline = time.monotonic() + harness.timeout
        while True:
            entered = harness.scalar_native(
                'SELECT GEN_ID(P6_ATTACH_GUARD,0) AS OBSERVED FROM RDB$DATABASE',
                'attach_trigger_entered')
            if entered == 1:
                break
            require(time.monotonic() < deadline, 'ON CONNECT trigger did not start')
        harness.summary['trigger_entered'] = entered
        harness.stop()  # Requires exit 0 without SIGKILL; assertions fail here.
        if args.native_only:
            collect_native(harness, native_client)
            require(native_client.returncode != 0, 'native attach unexpectedly completed')
        else:
            # No response after the password challenge also excludes AuthenticationOk.
            require_closed(pending.connection)
            pending.close(terminate=False)
            harness.summary['no_authentication_ok'] = True

        output = harness.isql('attach_trigger_rollback', f"""CONNECT '{harness.database}';
SET LIST ON;
SELECT COUNT(*) AS OBSERVED FROM PGWIRE_EFFECTS WHERE ID=691;
ROLLBACK;
QUIT;
""", embedded=True)
        require(field(output, 'OBSERVED') == '0', 'unfinished ON CONNECT work was committed')
        harness.summary['trigger_write_rolled_back'] = True
        harness.summary['passed'] = True
    except (Exception, KeyboardInterrupt) as error:
        harness.summary['error'] = harness.redact(f'{type(error).__name__}: {error}')
    finally:
        try:
            harness.close()
        except Exception as error:
            harness.summary['passed'] = False
            harness.summary['shutdown_error'] = harness.redact(str(error))
        if native_client is not None:
            try:
                collect_native(harness, native_client)
            except subprocess.TimeoutExpired:
                native_client.kill()
                collect_native(harness, native_client)
                harness.summary['passed'] = False
                harness.summary['native_client_error'] = 'native isql required SIGKILL after server shutdown'
        path = work / 'summary.json'
        path.write_text(json.dumps(harness.summary, indent=2) + '\n')
        print(json.dumps({'passed': harness.summary['passed'], 'summary': str(path)}, indent=2))
    return 0 if harness.summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
