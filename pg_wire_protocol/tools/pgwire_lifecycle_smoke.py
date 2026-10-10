#!/usr/bin/env python3
"""Run unchanged P3 lifecycle checks against the P4 engine and plugin."""
import argparse
import json
from pathlib import Path
import re
from pgwire_query_smoke import QueryHarness
from pgwire_startup_smoke import Client
from wire_host_smoke import reserve_ports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firebird-dir', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=30)
    args = parser.parse_args()
    if not 0 < args.timeout <= 300:
        parser.error('--timeout must be greater than zero and at most 300 seconds')
    source, work = args.firebird_dir.resolve(), args.work_dir.absolute()
    if work.exists() or work.is_symlink() or source in work.resolve().parents:
        parser.error('--work-dir must be new and outside the build')
    if not re.fullmatch(r'[A-Za-z0-9_./-]+', str(work)):
        parser.error('--work-dir must use safe ASCII path characters')
    for name in ('bin/firebird', 'bin/isql', 'security6.fdb', 'plugins/libPgWire.so'):
        if not (source / name).is_file():
            parser.error(f'completed build required: missing {source / name}')
    work.mkdir(mode=0o700)
    harness = QueryHarness(source, work.resolve(), args.timeout, args)
    harness.summary['stage'] = 'P4 regression: unchanged P3 lifecycle cases'
    try:
        harness.prepare()
        harness.failed_endpoints()
        native, configured, port = reserve_ports(3)
        harness.start('lifecycle', native, configured, [{'alias': 'PgWire', 'port': port}])
        client = Client(harness).login()
        client.close()
        harness.no_attachments()
        for name, method in (
            ('raw_integer_null_metadata_rows_and_native_visibility', harness.raw_results),
            ('second_identity_engine_permissions_and_recovery', harness.second_identity_permissions),
            ('implicit_select_commit_and_rollback', harness.implicit_transactions),
            ('disconnect_during_result_releases_engine_resources', harness.disconnects),
        ):
            harness.check(name, method)
        harness.native_probe('native_after_p4_lifecycle')
        harness.check('shutdown_cancels_active_engine_query_and_rolls_back', harness.shutdown_active_query)
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
