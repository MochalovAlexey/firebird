#!/usr/bin/env python3
"""Exercise native TCP and test wire listeners in one isolated Super server.

Requires a completed Linux build and the wire_tests target. This uses the test
module's labelled byte echo protocol; it does not test PostgreSQL compatibility.
The new --work-dir retains private config, database, logs and summary.json.
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


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def reserve_ports(count):
    reservations = []
    try:
        for _ in range(count):
            reservation = socket.socket()
            reservation.bind(("127.0.0.1", 0))
            reservations.append(reservation)
        return [reservation.getsockname()[1] for reservation in reservations]
    finally:
        for reservation in reservations:
            reservation.close()


def receive_exact(connection, size, timeout=5):
    deadline = time.monotonic() + timeout
    data = bytearray()
    while len(data) < size:
        connection.settimeout(max(0.01, deadline - time.monotonic()))
        part = connection.recv(size - len(data))
        require(part, "unexpected EOF from wire listener")
        data.extend(part)
    return bytes(data)


def require_closed(connection, timeout=5):
    connection.settimeout(timeout)
    try:
        require(connection.recv(1) == b"", "expected transport closure, received data")
    except (ConnectionResetError, BrokenPipeError):
        pass


def field(output, name):
    match = re.search(r"^" + re.escape(name) + r"\s+(.+?)\s*$", output, re.MULTILINE)
    require(match, f"missing native result field {name}")
    return match.group(1)


class Harness:
    def __init__(self, source, work, timeout):
        self.source, self.work, self.timeout = source, work, timeout
        self.runtime = work / "runtime"
        self.locks, self.temp = work / "locks", work / "tmp"
        self.database = self.runtime / "data" / "wire_host.fdb"
        self.password = secrets.token_urlsafe(32)
        self.bad_password = secrets.token_urlsafe(32)
        self.server = None
        self.reader = None
        self.clients = []
        self.phase = "bootstrap"
        self.events_path = work / "bootstrap.events"
        self.summary = {"passed": False, "source": str(source), "work_dir": str(work),
                        "transport": "real TCP, labelled test echo (not PostgreSQL)",
                        "checks": {}, "runs": []}

    def redact(self, value):
        return str(value).replace(self.password, "[REDACTED]").replace(self.bad_password, "[REDACTED]")

    def prepare(self):
        # Dereference every symlink: tests never mutate the build or installed data.
        shutil.copytree(self.source, self.runtime, symlinks=False)
        self.locks.mkdir()
        self.temp.mkdir()
        self.database.parent.mkdir(exist_ok=True)
        modules = list((self.runtime / "tests").glob("libWireTestPlugin.so"))
        require(len(modules) == 1, "build wire_tests before running the host smoke")
        self.module = modules[0]
        self.env = os.environ.copy()
        for name in ("ISC_USER", "ISC_PASSWORD", "ISC_ROLE", "FIREBIRD_MSG", "FB_WIRE_TEST_EVENTS"):
            self.env.pop(name, None)
        self.env.update(FIREBIRD=str(self.runtime), FIREBIRD_LOCK=str(self.locks),
                        FIREBIRD_TMP=str(self.temp), TMP=str(self.temp), TEMP=str(self.temp),
                        TMPDIR=str(self.temp), LD_LIBRARY_PATH=str(self.runtime / "lib"))
        (self.runtime / "databases.conf").write_text(
            f"wire_host = {self.database}\nsecurity.db = {self.runtime / 'security6.fdb'}\n")
        (self.runtime / "replication.conf").write_text("")
        self.configure(3050, [])
        self.isql("bootstrap", f"""CREATE DATABASE '{self.database}' DEFAULT CHARACTER SET UTF8;
CREATE USER WIRE_HOST_TEST PASSWORD '{self.password}';
CREATE TABLE WIRE_HOST_ROWS (ID INTEGER NOT NULL PRIMARY KEY);
GRANT SELECT, INSERT, UPDATE, DELETE ON WIRE_HOST_ROWS TO USER WIRE_HOST_TEST;
COMMIT;
QUIT;
""", embedded=True)

    def configure(self, configured_native_port, endpoints):
        names = ",".join(endpoint["alias"] for endpoint in endpoints)
        ports = ",".join(str(endpoint["port"]) for endpoint in endpoints)
        config = ("ServerMode = Super\nRemoteBindAddress = 127.0.0.1\n"
                  f"RemoteServicePort = {configured_native_port}\nRemoteServiceUnixSocket =\n"
                  f"SecurityDatabase = {self.runtime / 'security6.fdb'}\n"
                  "AuthServer = Srp256\nAuthClient = Srp256\nUserManager = Srp\n"
                  "Providers = Remote, Engine14, Loopback\nWireCrypt = Required\n"
                  f"DatabaseAccess = Restrict {self.database.parent}\n"
                  "TraceDSQL = 0\nAuditTraceConfigFile =\n"
                  f"WireProtocolPlugins = {names}\nWireProtocolPorts = {ports}\n")
        (self.runtime / "firebird.conf").write_text(config)
        sections = []
        for endpoint in endpoints:
            alias = endpoint["alias"]
            module = endpoint.get("module", self.module)
            section = f"Plugin = {alias}\n{{\nModule = {module}\nRegisterName = WireTest\n"
            if not endpoint.get("file_config"):
                section += f"Config = {alias}Config\n"
            section += "}\n"
            values = {"BindAddress": "127.0.0.1", "Workers": "1", "QueueSize": "1",
                      "Label": endpoint.get("label", alias), "Mode": "tcp_echo"}
            values.update(endpoint.get("config", {}))
            settings = "".join(f"{key} = {value}\n" for key, value in values.items())
            settings += endpoint.get("raw_config", "")
            if endpoint.get("file_config"):
                # The normal getter and plugin factory both fall back to the
                # module's .conf file when no named Config is selected.
                Path(module).with_suffix(".conf").write_text(settings)
            else:
                section += f"Config = {alias}Config\n{{\n{settings}}}\n"
            sections.append(section)
        (self.runtime / "plugins.conf").write_text("\n".join(sections))

    def isql(self, label, sql, *, embedded=False, bad_password=False):
        env = self.env.copy()
        env["ISC_USER"] = "SYSDBA" if embedded else "WIRE_HOST_TEST"
        if not embedded:
            env["ISC_PASSWORD"] = self.bad_password if bad_password else self.password
        command = [str(self.runtime / "bin/isql"), "-quiet", "-bail"]
        if not embedded:
            command.append(f"inet://127.0.0.1:{self.native_port}/wire_host")
        result = subprocess.run(command, input=sql, capture_output=True, text=True,
                                errors="replace", env=env, cwd=self.temp, timeout=self.timeout)
        output = result.stdout + result.stderr
        (self.work / f"{self.phase}-{label}.log").write_text(self.redact(output))
        if bad_password:
            require(result.returncode and re.search(
                r"SQLSTATE\s*=\s*28000|user name and password|authentication", output, re.IGNORECASE),
                "native listener accepted wrong password or returned unrelated failure")
        else:
            require(result.returncode == 0 and "Statement failed" not in output,
                    f"native {label} failed; see retained log")
        return output

    def start(self, phase, native_port, configured_native_port, endpoints):
        require(self.server is None, "previous server is still owned by the harness")
        self.phase, self.native_port = phase, native_port
        self.configure(configured_native_port, endpoints)
        self.events_path = self.work / f"{phase}.events"
        env = self.env.copy()
        env["FB_WIRE_TEST_EVENTS"] = str(self.events_path)
        self.server = subprocess.Popen(
            [str(self.runtime / "bin/firebird"), "-d", "-p", str(native_port), "-el", str(self.locks)],
            env=env, cwd=self.temp, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, errors="replace", start_new_session=True)
        server = self.server

        def collect():
            with (self.work / f"{phase}-server.log").open("w") as log:
                for line in server.stdout:
                    log.write(self.redact(line))
                    log.flush()

        self.reader = threading.Thread(target=collect, daemon=True)
        self.reader.start()
        self.run_summary = {"phase": phase, "pid": server.pid, "native_port": native_port,
                            "configured_native_port": configured_native_port,
                            "endpoints": [{"alias": item["alias"], "port": item["port"]} for item in endpoints]}
        self.summary["runs"].append(self.run_summary)
        deadline = time.monotonic() + self.timeout
        while True:
            require(server.poll() is None, f"{phase} server exited during startup")
            try:
                with socket.create_connection(("127.0.0.1", native_port), timeout=0.2):
                    return
            except OSError:
                require(time.monotonic() < deadline, "native listener startup timed out")
                time.sleep(0.05)

    def native_probe(self, label):
        output = self.isql(label, """SET LIST ON;
SELECT CAST(40 + 2 AS INTEGER) AS HOST_ANSWER, CURRENT_USER AS HOST_USER,
  RDB$GET_CONTEXT('SYSTEM', 'NETWORK_PROTOCOL') AS HOST_NETWORK FROM RDB$DATABASE;
SELECT MON$SERVER_PID AS HOST_PID FROM MON$ATTACHMENTS
  WHERE MON$ATTACHMENT_ID = CURRENT_CONNECTION;
ROLLBACK;
QUIT;
""")
        require(field(output, "HOST_ANSWER") == "42", "native computed SELECT mismatch")
        require(field(output, "HOST_USER") == "WIRE_HOST_TEST", "native authentication identity mismatch")
        require(field(output, "HOST_NETWORK") in ("TCPv4", "TCPv6", "TCP"), "native query was not over TCP")
        require(int(field(output, "HOST_PID")) == self.server.pid, "native query reached another process")

    def connect(self, port, label=None, retry=False):
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                connection = socket.create_connection(("127.0.0.1", port), timeout=0.3)
                self.clients.append(connection)
                if label is not None:
                    expected = (label + "\n").encode()
                    require(receive_exact(connection, len(expected)) == expected, "configured alias greeting mismatch")
                return connection
            except ConnectionRefusedError:
                if not retry or time.monotonic() >= deadline:
                    raise
                require(self.server.poll() is None, "server exited while awaiting wire listener")
                time.sleep(0.05)

    def events(self):
        return self.events_path.read_text().splitlines() if self.events_path.exists() else []

    def stop(self):
        if self.server is None:
            return
        server, self.server = self.server, None
        started = time.monotonic()
        forced = False
        already_exited = server.poll() is not None
        if not already_exited:
            with self.events_path.open("a") as events:
                events.write("harness.sigterm\n")
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                forced = True
                server.kill()
                server.wait(timeout=5)
        if self.reader:
            self.reader.join(timeout=5)
        self.run_summary.update(exit_code=server.returncode, shutdown_seconds=time.monotonic() - started,
                                forced_kill=forced)
        require(not forced and not already_exited and server.returncode == 0,
                "server failed clean bounded SIGTERM shutdown")

    def check_lifecycle(self, labels):
        events = self.events()
        for event in ("module.load", "module.cleanup", "module.unload"):
            require(events.count(event) == 1, f"unexpected shared module lifetime: {event}")
        cleanup = events.index("module.cleanup")
        require(events.index("harness.sigterm") < cleanup, "module was cleaned before requested shutdown")
        require(cleanup < events.index("module.unload"), "module unloaded before cleanup")
        require(sorted(event for event in events if event.endswith(":plugin.create")) ==
                sorted(f"{label}:plugin.create" for label in labels), "invalid endpoint created an extra plugin")
        for label in labels:
            created, destroyed = f"{label}:session.create", f"{label}:session.destroy"
            require(events.count(created) > 0 and events.count(created) == events.count(destroyed),
                    f"session leak for {label}")
            plugin_destroy = f"{label}:plugin.destroy"
            require(events.count(f"{label}:plugin.create") == 1 and events.count(plugin_destroy) == 1,
                    f"plugin leak for {label}")
            require(max(index for index, event in enumerate(events) if event == destroyed)
                    < events.index(plugin_destroy) < cleanup, f"invalid release order for {label}")

    def coexistence(self):
        invalid_configs = [
            ("PortOverride", {"Port": "12345"}), ("UnsafeBind", {"BindAddress": "0.0.0.0"}),
            ("WorkersZero", {"Workers": "0"}), ("WorkersLarge", {"Workers": "17"}),
            ("QueueZero", {"QueueSize": "0"}), ("QueueLarge", {"QueueSize": "65"}),
            ("UnsupportedTLS", {"TLS": "Required"}),
        ]
        ports = iter(reserve_ports(6 + len(invalid_configs)))
        native, configured_native = next(ports), next(ports)
        first = {"alias": "First", "port": next(ports), "label": "first",
                 "file_config": True}
        missing = {"alias": "Missing", "port": next(ports), "module": self.work / "does_not_exist"}
        second = {"alias": "Second", "port": next(ports), "label": "second", "config": {"Workers": "2"}}
        invalid = [{"alias": alias, "port": next(ports), "config": config}
                   for alias, config in invalid_configs]
        invalid.append({"alias": "DuplicateWorkers", "port": next(ports), "raw_config": "Workers = 2\n"})
        self.start("coexist", native, configured_native, [first, missing, second] + invalid)
        active = self.connect(first["port"], "first", retry=True)
        other = self.connect(second["port"], "second", retry=True)
        payload = b"fragmented\x00wire\xffpayload" * 40
        for offset in range(0, len(payload), 7):
            active.sendall(payload[offset:offset + 7])
        require(receive_exact(active, len(payload)) == payload, "first endpoint corrupted fragmented binary bytes")
        other.sendall(b"second remains independent")
        require(receive_exact(other, 26) == b"second remains independent", "second endpoint echo mismatch")
        # The first alias has no named Config. Its greeting above proves that
        # the factory read the module .conf; these non-default worker/queue
        # limits prove that the host read that same fallback configuration.
        # Active worker and one queued socket leave no room for a third socket.
        queued = self.connect(first["port"])
        queued.sendall(b"queued bytes")
        queued.settimeout(0.2)
        try:
            queued.recv(1)
            raise RuntimeError("queued connection reached a session before a worker was available")
        except socket.timeout:
            pass
        excess = self.connect(first["port"])
        require_closed(excess, 1)
        active.close()
        require(receive_exact(queued, len(b"first\nqueued bytes")) == b"first\nqueued bytes",
                "queued socket did not start after the worker became free")
        queued.close()
        self.native_probe("native_after_queue_pressure")
        self.isql("wrong_password", "QUIT;\n", bad_password=True)
        self.isql("transactions", """INSERT INTO WIRE_HOST_ROWS VALUES (1);
COMMIT;
INSERT INTO WIRE_HOST_ROWS VALUES (2);
ROLLBACK;
QUIT;
""")
        output = self.isql("persisted_rows", """SET LIST ON;
SELECT COUNT(*) AS HOST_COUNT, SUM(ID) AS HOST_SUM FROM WIRE_HOST_ROWS;
ROLLBACK;
QUIT;
""")
        require(field(output, "HOST_COUNT") == "1" and field(output, "HOST_SUM") == "1",
                "native commit/rollback persistence mismatch")
        for endpoint in [missing] + invalid:
            try:
                unexpected = self.connect(endpoint["port"])
            except ConnectionRefusedError:
                continue
            unexpected.close()
            raise RuntimeError(f"invalid endpoint {endpoint['alias']} opened a listener")
        events = self.events()
        for endpoint in [missing] + invalid:
            require(f"{endpoint['alias']}:plugin.create" not in events,
                    f"invalid endpoint instantiated plugin: {endpoint['alias']}")
        # Two sessions blocked on I/O must be woken by shutdown, including
        # one peer that has only sent part of a message/stream.
        partial = self.connect(second["port"], "second")
        partial.sendall(b"x")
        require(receive_exact(partial, 1) == b"x", "partial session failed")
        self.stop()
        require_closed(other, 1)
        require_closed(partial, 1)
        self.check_lifecycle(["first", "second"])
        self.summary["checks"].update(native_and_two_wire_ports_same_process=True,
                                       module_file_fallback_shared_by_host_and_factory=True,
                                       native_auth_and_transactions=True, missing_middle_preserves_ports=True,
                                       invalid_endpoints_isolated=True, worker_and_queue_bounds=True,
                                       shutdown_wakes_idle_and_partial_sessions=True,
                                       session_plugin_module_release_order=True)

    def native_conflict(self):
        native, configured_native = reserve_ports(2)
        self.start("native_conflict", native, configured_native,
                   [{"alias": "NativeConflict", "port": native, "label": "conflict"}])
        self.native_probe("native_despite_wire_conflict")
        self.stop()
        require(self.events() == ["harness.sigterm"], "native -p conflict loaded test module before being rejected")
        self.summary["checks"]["actual_native_port_conflict_rejected_before_module_load"] = True

    def close(self):
        try:
            self.stop()
        finally:
            for connection in self.clients:
                connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firebird-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    source, work = args.firebird_dir.resolve(), args.work_dir.absolute()
    if not 0 < args.timeout <= 300:
        parser.error("--timeout must be greater than zero and at most 300 seconds")
    if work.exists() or work.is_symlink() or source in work.resolve().parents:
        parser.error("--work-dir must be new and outside --firebird-dir")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", str(work)):
        parser.error("--work-dir must use ASCII letters, digits, slash, dot, dash or underscore")
    for name in ("bin/firebird", "bin/isql", "security6.fdb", "tests/libWireTestPlugin.so"):
        if not (source / name).is_file():
            parser.error(f"completed build required: missing {source / name}")
    work.mkdir(mode=0o700)
    harness = Harness(source, work.resolve(), args.timeout)
    try:
        harness.prepare()
        harness.coexistence()
        harness.native_conflict()
        harness.summary["passed"] = True
    except (Exception, KeyboardInterrupt) as error:
        harness.summary["error"] = harness.redact(f"{type(error).__name__}: {error}")
    finally:
        try:
            harness.close()
        except Exception as error:
            harness.summary["passed"] = False
            harness.summary["shutdown_error"] = harness.redact(f"{type(error).__name__}: {error}")
        path = work / "summary.json"
        path.write_text(json.dumps(harness.summary, indent=2) + "\n")
        print(json.dumps({"passed": harness.summary["passed"], "summary": str(path)}, indent=2))
    return 0 if harness.summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
