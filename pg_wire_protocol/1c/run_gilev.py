#!/usr/bin/env python3
"""Start, inspect or gracefully stop an already prepared Linux Gilev stand."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time


def process(pid):
    try:
        base = Path('/proc') / str(pid)
        # comm may contain spaces and parentheses; field 22 is starttime.
        fields = (base / 'stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        return {'pid': pid, 'starttime': int(fields[19]),
                'exe': str((base / 'exe').resolve(strict=True)),
                'argv': [os.fsdecode(arg) for arg in (base / 'cmdline').read_bytes().split(b'\0')[:-1]],
                'uid': base.stat().st_uid, 'group': int(fields[2]), 'session': int(fields[3])}
    except (FileNotFoundError, ProcessLookupError):
        return None


def session_pids(pid):
    members = []
    for entry in Path('/proc').iterdir():
        if entry.name.isdecimal():
            try:
                fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
                if fields[0] != 'Z' and int(fields[3]) == pid:
                    members.append(int(entry.name))
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                pass
    return members


def owned(stand, name, command, saved):
    pidfile = stand / (name + '.pid')
    if not pidfile.exists():
        if saved:
            raise RuntimeError(name + ': saved identity exists but PID file is missing')
        return None
    pid = int(pidfile.read_text().strip())
    if pid <= 1:
        raise RuntimeError(name + ': invalid PID')
    actual = process(pid)
    if not actual:
        if session_pids(pid):
            raise RuntimeError(name + ': its child processes are still running')
        return None
    if (actual['exe'] != str(Path(command[0]).resolve()) or actual['argv'][1:] != command[1:] or
            actual['uid'] != os.getuid() or actual['session'] != pid or actual['group'] != pid or
            (saved and (saved['pid'] != pid or saved['starttime'] != actual['starttime']))):
        raise RuntimeError(name + ': process ownership mismatch; refusing to use or signal it')
    return {'pid': pid, 'starttime': actual['starttime']}


def save_state(stand, state):
    path = stand / 'launcher-state.json'
    temporary = path.with_suffix('.tmp')
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as stream:
        json.dump(state, stream, indent=2)
        stream.write('\n')
    temporary.replace(path)


def listening(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.2):
            return True
    except OSError:
        return False


def stop_process(stand, name, command, record):
    # Pin the verified targets individually; never signal a reused numeric PID.
    descriptors = [os.pidfd_open(record['pid'])]
    try:
        if owned(stand, name, command, record) != record:
            raise RuntimeError(name + ': process changed before shutdown')
        if name == 'ragent':
            # ragent may exit without stopping rmngr/rphost/licensing processes.
            for pid in session_pids(record['pid']):
                if pid == record['pid']:
                    continue
                actual = process(pid)
                if not actual:
                    continue
                if (actual['uid'] != os.getuid() or actual['group'] != record['pid'] or
                        actual['session'] != record['pid'] or
                        Path(actual['exe']).parent != Path(command[0]).resolve().parent):
                    raise RuntimeError('ragent: unexpected process in its private session')
                try:
                    descriptors.append(os.pidfd_open(pid))
                except ProcessLookupError:
                    continue
                current = process(pid)
                if current and current != actual:
                    raise RuntimeError('ragent: child identity changed before shutdown')
        if owned(stand, name, command, record) != record:
            raise RuntimeError(name + ': process changed before shutdown')
        for descriptor in descriptors:
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGTERM)
            except ProcessLookupError:
                pass
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not session_pids(record['pid']):
            return
        time.sleep(0.1)
    raise RuntimeError(name + ': did not stop gracefully; subsequent services were left running')


def run(args):
    stand = args.stand_dir.resolve(strict=True)
    config = json.loads((stand / 'connection.json').read_text())
    installation = args.one_c_dir or config.get('one_c_dir')
    if not installation:
        raise RuntimeError('Set one_c_dir in connection.json or pass --one-c-dir')
    one_c = Path(installation).resolve(strict=True)
    runtime = Path(config['runtime']).resolve(strict=True)
    if runtime != stand / 'runtime':
        raise RuntimeError('runtime must be this stand directory\'s runtime subdirectory')
    commands = [
        ('server', [str(runtime / 'bin/firebird'), '-d', '-p', str(config['native_port']),
                    '-el', str(stand / 'locks')], [config['native_port'], config['pg_port']]),
        ('ragent', [str(one_c / 'ragent'), '-port', str(config['agent_port']),
                    '-regport', str(config['cluster_port']), '-range',
                    config.get('process_port_range', '12560:12589'), '-d', str(stand / 'cluster')],
                   [config['agent_port'], config['cluster_port']]),
        ('ras', [str(one_c / 'ras'), 'cluster', '--port=' + str(config['ras_port']),
                 '--monitor-port=' + str(config.get('monitor_port', 12555)),
                 '127.0.0.1:' + str(config['agent_port'])], [config['ras_port']]),
        ('client', [str(one_c / '1cv8'), 'ENTERPRISE', '/S',
                    '127.0.0.1:' + str(config['cluster_port']) + '/' + config['infobase'],
                    '/RunModeOrdinaryApplication', '/AppAutoCheckVersion-', '/L', 'ru',
                    '/Out', str(stand / 'logs/client-out.log')], [])]
    state_path = stand / 'launcher-state.json'
    # status creates no files and does not open database/network connections.
    lock = None
    try:
        if args.action != 'status':
            lock = os.open(stand / 'launcher.lock', os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        records = {name: owned(stand, name, command, state.get(name)) for name, command, _ in commands}
        if args.action == 'status':
            for name, _, _ in commands:
                print(name + ': ' + ('running PID ' + str(records[name]['pid']) if records[name] else 'stopped'))
            return 0
        if args.action == 'stop':
            for name, command, _ in reversed(commands):
                if records[name]:
                    stop_process(stand, name, command, records[name])
                    print(name + ': stopped', flush=True)
                (stand / (name + '.pid')).unlink(missing_ok=True)
                state.pop(name, None)
                save_state(stand, state)
            return 0

        if not (stand / 'cluster/1cv8wsrv.lst').is_file() or not Path(config['database']).is_file():
            raise RuntimeError('Existing cluster registry and database are required; this tool does not provision them')
        if not records['client'] and not os.environ.get('DISPLAY'):
            raise RuntimeError('DISPLAY is required to open the thick client GUI')
        for name, command, ports in commands:
            if not os.access(command[0], os.X_OK):
                raise RuntimeError(name + ': executable is unavailable')
            reserved = ports
            if name == 'ras':
                reserved = ports + [int(config.get('monitor_port', 12555))]
            elif name == 'ragent':
                first, last = map(int, config.get('process_port_range', '12560:12589').split(':'))
                if not 1 <= first <= last <= 65535:
                    raise RuntimeError('Invalid working-process port range')
                reserved = ports + list(range(first, last + 1))
            if not records[name] and any(listening(port) for port in reserved):
                raise RuntimeError(name + ': a required port is occupied without a matching owned process')
        for name, command, ports in commands:
            if records[name]:
                state[name] = records[name]  # Adopt an exact-match legacy PID file.
                save_state(stand, state)
                print(name + ': reused PID ' + str(records[name]['pid']), flush=True)
                continue
            env = os.environ.copy()
            if name == 'server':
                env.update(FIREBIRD=str(runtime), FIREBIRD_LOCK=str(stand / 'locks'),
                           FIREBIRD_TMP=str(stand / 'tmp'), LD_LIBRARY_PATH=str(runtime / 'lib'))
            log = stand / ('server.log' if name == 'server' else 'logs/' + name + '.log')
            with log.open('ab') as output:
                child = subprocess.Popen(command, cwd=stand / 'tmp', env=env,
                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            (stand / (name + '.pid')).write_text(str(child.pid) + '\n')
            actual = process(child.pid)
            if not actual:
                raise RuntimeError(name + ': exited at startup; see ' + str(log))
            state[name] = {'pid': child.pid, 'starttime': actual['starttime']}
            save_state(stand, state)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError(name + ': exited at startup; see ' + str(log))
                if ports and all(listening(port) for port in ports):
                    break
                if not ports:
                    time.sleep(1)
                    if child.poll() is None:
                        break
                time.sleep(0.1)
            else:
                raise RuntimeError(name + ': listeners did not become ready; use status/stop to inspect started services')
            print(name + ': started PID ' + str(child.pid), flush=True)
        return 0
    finally:
        if lock is not None:
            os.close(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'status', 'stop'))
    parser.add_argument('--stand-dir', type=Path, required=True)
    parser.add_argument('--one-c-dir', type=Path, help='Override connection.json one_c_dir')
    return run(parser.parse_args())


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print('Gilev stand: ' + str(error), file=sys.stderr)
        sys.exit(1)
