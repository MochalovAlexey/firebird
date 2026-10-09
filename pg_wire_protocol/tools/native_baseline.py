#!/usr/bin/env python3
"""Exercise native TCP on an isolated copy of a completed Firebird build.

Example: python3 native_baseline.py --firebird-dir gen/Debug/firebird \
    --work-dir /tmp/pgwire-native-baseline

The work directory must not exist. It is retained for inspection; passwords
exist only in memory, subprocess input/environment, and security database hashes.
"""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firebird-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    source = args.firebird_dir.resolve()
    work = args.work_dir.absolute()
    if args.timeout <= 0 or args.timeout > 300:
        parser.error("--timeout must be greater than zero and at most 300 seconds")
    if work.exists() or work.is_symlink():
        parser.error("--work-dir must not exist")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", str(work)):
        parser.error("--work-dir must use ASCII letters, digits, slash, dot, dash or underscore")
    if source in work.resolve().parents:
        parser.error("--work-dir must be outside --firebird-dir")
    for name in ("bin/firebird", "bin/isql", "security6.fdb"):
        if not (source / name).is_file():
            parser.error(f"completed build required: missing {source / name}")

    work.mkdir(mode=0o700)
    work = work.resolve()
    runtime, locks, temp = (work / name for name in ("runtime", "locks", "tmp"))
    password, bad_password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    server = None
    reader = None
    summary = {"passed": False, "source": str(source), "work_dir": str(work), "checks": {}}

    def redact(value):
        return str(value).replace(password, "[REDACTED]").replace(bad_password, "[REDACTED]")

    def save_log(name, value):
        (work / name).write_text(redact(value), encoding="utf-8")

    def run_isql(label, sql, database=None, user="SYSDBA", secret=None, successful=True):
        child_env = env.copy()
        child_env["ISC_USER"] = user
        if secret is not None:
            child_env["ISC_PASSWORD"] = secret
        command = [str(runtime / "bin/isql"), "-quiet", "-bail"]
        if database:
            command.append(database)
        result = subprocess.run(command, input=sql, text=True, encoding="utf-8",
                                errors="replace", capture_output=True, env=child_env,
                                cwd=temp, timeout=args.timeout)
        output = result.stdout + result.stderr
        save_log(label + ".log", output)
        if successful and (result.returncode or "Statement failed" in output):
            raise RuntimeError(f"{label} failed; see {label}.log")
        return result.returncode, output

    def field(output, name):
        match = re.search(r"^" + re.escape(name) + r"\s+(.+?)\s*$", output, re.MULTILINE)
        if not match:
            raise RuntimeError(f"missing result field {name}")
        return match.group(1)

    def require(condition, message):
        if not condition:
            raise RuntimeError(message)

    try:
        # Dereference symlinks into independent copies, including the security DB.
        # No writes through links into the build or an installed server are possible.
        shutil.copytree(source, runtime, symlinks=False)
        locks.mkdir()
        temp.mkdir()
        data = runtime / "data"
        data.mkdir(exist_ok=True)
        database = data / "baseline.fdb"
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        config = (f"ServerMode = Super\nRemoteBindAddress = 127.0.0.1\n"
                  f"RemoteServicePort = {port}\nRemoteServiceUnixSocket =\n"
                  f"SecurityDatabase = {runtime / 'security6.fdb'}\n"
                  "AuthServer = Srp256\nAuthClient = Srp256\nUserManager = Srp\n"
                  "Providers = Remote, Engine14, Loopback\nWireCrypt = Required\n"
                  f"DatabaseAccess = Restrict {data}\n"
                  "TraceDSQL = 0\nAuditTraceConfigFile =\n")
        (runtime / "firebird.conf").write_text(config)
        (runtime / "databases.conf").write_text(
            f"pgwire_baseline = {database}\nsecurity.db = {runtime / 'security6.fdb'}\n")
        (runtime / "replication.conf").write_text("")
        env = os.environ.copy()
        for name in ("ISC_USER", "ISC_PASSWORD", "ISC_ROLE", "FIREBIRD_MSG"):
            env.pop(name, None)
        env.update(FIREBIRD=str(runtime), FIREBIRD_LOCK=str(locks), FIREBIRD_TMP=str(temp),
                   TMP=str(temp), TEMP=str(temp), TMPDIR=str(temp),
                   LD_LIBRARY_PATH=str(runtime / "lib"))
        summary.update(config=config, tcp_endpoint=f"inet://127.0.0.1:{port}/pgwire_baseline",
                       auth_server="Srp256", user="PGWIRE_TEST")
        version = subprocess.run([str(runtime / "bin/firebird"), "-z"], env=env,
                                 capture_output=True, text=True, timeout=args.timeout)
        summary["version"] = redact(version.stdout + version.stderr).strip()
        if version.returncode:
            raise RuntimeError("firebird -z failed")
        client_version = subprocess.run([str(runtime / "bin/isql"), "-z", "-quiet"],
                                        input="QUIT;\n", env=env, capture_output=True,
                                        text=True, timeout=args.timeout)
        summary["isql_version"] = redact(client_version.stdout + client_version.stderr).strip()
        require(client_version.returncode == 0, "isql -z failed")
        run_isql("bootstrap", f"""CREATE DATABASE '{database}' DEFAULT CHARACTER SET UTF8;
CREATE USER PGWIRE_TEST PASSWORD '{password}';
CREATE TABLE PGWIRE_BASELINE (ID INTEGER NOT NULL PRIMARY KEY);
GRANT SELECT, INSERT, UPDATE, DELETE ON PGWIRE_BASELINE TO USER PGWIRE_TEST;
COMMIT;
QUIT;
""")
        summary["checks"]["isolated_embedded_bootstrap"] = True

        server = subprocess.Popen([str(runtime / "bin/firebird"), "-d", "-p", str(port),
                                   "-el", str(locks)], env=env, cwd=temp,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                  errors="replace", start_new_session=True)

        def collect_server_output():
            with (work / "server.log").open("w", encoding="utf-8") as log:
                for line in server.stdout:
                    log.write(redact(line))
                    log.flush()

        reader = threading.Thread(target=collect_server_output, daemon=True)
        reader.start()
        summary["server_pid"] = server.pid
        deadline = time.monotonic() + args.timeout
        while True:
            if server.poll() is not None:
                raise RuntimeError("isolated server exited during startup; see server.log")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("native TCP listener startup timed out")
                time.sleep(0.05)

        uri = summary["tcp_endpoint"]
        _, output = run_isql("native_transactions", """SET LIST ON;
SELECT CAST(40 + 2 AS INTEGER) AS BASELINE_ANSWER, CURRENT_USER AS BASELINE_USER,
  RDB$GET_CONTEXT('SYSTEM', 'NETWORK_PROTOCOL') AS BASELINE_NETWORK FROM RDB$DATABASE;
SELECT MON$SERVER_PID AS BASELINE_SERVER_PID FROM MON$ATTACHMENTS
  WHERE MON$ATTACHMENT_ID = CURRENT_CONNECTION;
INSERT INTO PGWIRE_BASELINE VALUES (1);
COMMIT;
INSERT INTO PGWIRE_BASELINE VALUES (2);
ROLLBACK;
QUIT;
""", uri, "PGWIRE_TEST", password)
        require(field(output, "BASELINE_ANSWER") == "42", "computed SELECT mismatch")
        require(field(output, "BASELINE_USER") == "PGWIRE_TEST", "authenticated identity mismatch")
        protocol = field(output, "BASELINE_NETWORK")
        require(protocol in ("TCPv4", "TCPv6", "TCP"), f"unexpected network protocol: {protocol}")
        require(int(field(output, "BASELINE_SERVER_PID")) == server.pid, "wrong server process")
        summary["network_protocol"] = protocol
        summary["checks"].update(native_tcp=True, computed_select=True, authenticated_identity=True)

        bad_code, bad_output = run_isql("wrong_password", "QUIT;\n", uri, "PGWIRE_TEST",
                                       bad_password, successful=False)
        if not bad_code or not re.search(r"SQLSTATE\s*=\s*28000|user name and password|authentication",
                                         bad_output, re.IGNORECASE):
            raise RuntimeError("wrong-password connection did not produce an authentication failure")
        summary["checks"]["wrong_password_rejected"] = True
        _, output = run_isql("persisted_rows", """SET LIST ON;
SELECT COUNT(*) AS BASELINE_COUNT, SUM(ID) AS BASELINE_SUM FROM PGWIRE_BASELINE;
ROLLBACK;
QUIT;
""", uri, "PGWIRE_TEST", password)
        require(field(output, "BASELINE_COUNT") == "1", "commit/rollback row count mismatch")
        require(field(output, "BASELINE_SUM") == "1", "rolled-back row was persisted")
        summary["checks"].update(commit_persisted=True, rollback_not_persisted=True,
                                 reconnect_after_auth_failure=True)
        summary["passed"] = True
    except (Exception, KeyboardInterrupt) as error:
        summary["error"] = redact(f"{type(error).__name__}: {error}")
    finally:
        if server is not None:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=min(args.timeout, 20))
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
                    summary["passed"] = False
                    summary["error"] = "isolated server required SIGKILL during shutdown"
            elif summary["passed"]:
                summary["passed"] = False
                summary["error"] = "isolated server exited before the requested shutdown"
            summary["server_exit_code"] = server.returncode
            summary["checks"]["server_stopped"] = server.poll() is not None
            if reader:
                reader.join(timeout=5)
        summary_path = work / "summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"passed": summary["passed"], "summary": str(summary_path)}, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
