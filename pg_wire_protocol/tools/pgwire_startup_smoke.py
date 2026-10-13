#!/usr/bin/env python3
"""P2 startup/authentication over real TCP, with native SQL as an observer.

Uses Python's standard library and an installed libpq runtime through ctypes.
The new work directory retains an isolated runtime, databases and sanitized
logs. Includes SELECT and error recovery after the P3 execution slice; the
separate pgwire_query_smoke.py covers the complete M0 execution matrix.
"""

import argparse
import ctypes
import ctypes.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time

from wire_host_smoke import Harness, field, receive_exact, require, reserve_ports


def packet(kind, payload=b""):
    return kind + struct.pack("!I", len(payload) + 4) + payload


def startup(user=b"WIRE_HOST_TEST", database=b"wire_host", extra=(), application_name=b"pgwire_p2_raw"):
    fields = [] if user is None else [(b"user", user)]
    if database is not None:
        fields.append((b"database", database))
    fields += [(b"client_encoding", b"UTF8"), (b"application_name", application_name)]
    fields.extend(extra)
    payload = struct.pack("!I", 196608)
    payload += b"".join(key + b"\0" + value + b"\0" for key, value in fields) + b"\0"
    return struct.pack("!I", len(payload) + 4) + payload


def error_fields(payload):
    fields = {}
    position = 0
    while position < len(payload) and payload[position]:
        key = chr(payload[position])
        end = payload.find(b"\0", position + 1)
        require(end >= 0, "unterminated ErrorResponse field")
        fields[key] = payload[position + 1:end].decode("utf-8")
        position = end + 1
    require(position == len(payload) - 1, "invalid ErrorResponse terminator")
    require(all(key in fields for key in ("S", "V", "C", "M")), "incomplete ErrorResponse")
    return fields


def classify_connects(trace):
    """Reject backend socket hops while accounting for exact OS NSS endpoints."""
    nss_paths = {"/var/run/nscd/socket", "/run/systemd/userdb/io.systemd.DynamicUser"}
    nss_counts = {}
    attempts = 0
    for line in trace.splitlines():
        if not re.search(r"\bconnect\(", line):
            continue
        attempts += 1
        # NSS performs local user/group lookups through these OS services.
        # Require the complete literal sockaddr: prefixes, abstract sockets,
        # escaped/truncated paths and every INET address remain failures.
        unix = re.search(r'\{sa_family=AF_UNIX, sun_path="([^"\\]*)"\}, \d+\)', line)
        require(unix and unix[1] in nss_paths,
                "server attempted a backend or unrecognized socket connect; see server-connect.trace")
        path = unix[1]
        nss_counts[path] = nss_counts.get(path, 0) + 1
    return {"backend_connect_attempts": 0, "total_connect_attempts": attempts,
            "os_nss_unix_connects": dict(sorted(nss_counts.items()))}


class Client:
    def __init__(self, harness):
        self.harness = harness
        self.connection = harness.connect(harness.pg_port, retry=True)
        self.local_port = self.connection.getsockname()[1]
        self.messages = []

    def send(self, data, fragmented=False):
        if fragmented:
            for position in range(0, len(data), 3):
                self.connection.sendall(data[position:position + 3])
        else:
            self.connection.sendall(data)

    def message(self, timeout=5):
        header = receive_exact(self.connection, 5, timeout)
        size = struct.unpack("!I", header[1:])[0]
        require(4 <= size <= 1024 * 1024, "invalid backend message length")
        payload = receive_exact(self.connection, size - 4, timeout) if size > 4 else b""
        self.messages.append((header[:1], payload))
        return header[:1], payload

    def challenge(self, user=b"WIRE_HOST_TEST", database=b"wire_host", extra=(), fragmented=False):
        self.send(startup(user, database, extra), fragmented)
        kind, payload = self.message()
        require(kind == b"R" and payload == struct.pack("!I", 3),
                "startup did not request a cleartext password on loopback")

    def login(self, user=b"WIRE_HOST_TEST", password=None, database=b"wire_host", fragmented=False):
        self.challenge(user, database, fragmented=fragmented)
        self.send(packet(b"p", (password or self.harness.password).encode() + b"\0"), fragmented)
        authenticated = False
        parameters = {}
        for _ in range(32):
            kind, payload = self.message()
            if kind == b"R":
                require(not authenticated and payload == struct.pack("!I", 0), "invalid AuthenticationOk")
                authenticated = True
            elif kind == b"S":
                require(authenticated, "ParameterStatus preceded AuthenticationOk")
                parts = payload.split(b"\0")
                require(len(parts) == 3 and parts[-1] == b"", "malformed ParameterStatus")
                parameters[parts[0].decode()] = parts[1].decode()
            elif kind == b"K":
                require(authenticated and len(payload) == 8, "invalid BackendKeyData")
            elif kind == b"Z":
                require(authenticated and payload == b"I", "invalid initial ReadyForQuery")
                require(parameters.get("client_encoding", "").upper() == "UTF8", "missing UTF8 client encoding")
                return self
            else:
                code = error_fields(payload)["C"] if kind == b"E" else repr(kind)
                raise RuntimeError(f"unexpected startup response: {code}")
        raise RuntimeError("startup did not reach ReadyForQuery")

    def fatal(self, expected):
        kind, payload = self.message()
        require(kind == b"E", "startup/frame failure did not return ErrorResponse")
        code = error_fields(payload)["C"]
        allowed = {expected} if isinstance(expected, str) else set(expected)
        require(code in allowed, f"unexpected startup/frame SQLSTATE {code}, expected {sorted(allowed)}")
        require(not any(kind == b"R" and data == struct.pack("!I", 0)
                        for kind, data in self.messages), "AuthenticationOk preceded failed attach")
        require(not any(kind == b"Z" for kind, _ in self.messages), "ReadyForQuery preceded startup failure")
        self.connection.settimeout(5)
        try:
            require(self.connection.recv(1) == b"", "fatal error was not followed by connection close")
        except ConnectionResetError:
            pass

    def query(self, text, fragmented=False):
        self.send(packet(b"Q", text + b"\0"), fragmented)
        return self.query_response()

    def query_response(self):
        result = {"columns": None, "rows": [], "command": None, "error": None}
        for _ in range(100000):
            kind, payload = self.message()
            if kind == b"T":
                require(result["columns"] is None and len(payload) >= 2, "duplicate/invalid RowDescription")
                columns, offset = [], 2
                for _ in range(struct.unpack("!H", payload[:2])[0]):
                    end = payload.find(b"\0", offset)
                    require(end >= offset and end + 19 <= len(payload), "truncated column metadata")
                    name = payload[offset:end].decode("utf-8")
                    table, attribute, oid, size, modifier, format_code = struct.unpack("!IhIhih", payload[end + 1:end + 19])
                    columns.append({"name": name, "table": table, "attribute": attribute, "oid": oid,
                                    "size": size, "modifier": modifier, "format": format_code})
                    offset = end + 19
                require(offset == len(payload), "trailing RowDescription bytes")
                result["columns"] = columns
            elif kind == b"D":
                require(result["columns"] is not None and result["command"] is None and result["error"] is None,
                        "DataRow outside result set")
                require(len(payload) >= 2 and struct.unpack("!H", payload[:2])[0] == len(result["columns"]),
                        "DataRow column count mismatch")
                row, offset = [], 2
                for _ in result["columns"]:
                    require(offset + 4 <= len(payload), "truncated DataRow length")
                    length = struct.unpack("!i", payload[offset:offset + 4])[0]
                    offset += 4
                    require(length >= -1 and offset + max(length, 0) <= len(payload), "invalid DataRow value length")
                    row.append(None if length == -1 else payload[offset:offset + length].decode("utf-8"))
                    offset += max(length, 0)
                require(offset == len(payload), "trailing DataRow bytes")
                result["rows"].append(row)
            elif kind == b"C":
                require(result["command"] is None and result["error"] is None and payload.endswith(b"\0"),
                        "invalid/duplicate CommandComplete")
                result["command"] = payload[:-1].decode("utf-8")
            elif kind == b"E":
                require(result["error"] is None and result["command"] is None, "ErrorResponse after completion")
                result["error"] = error_fields(payload)
            elif kind == b"Z":
                require(payload == b"I", "implicit query did not restore ReadyForQuery I")
                require(result["error"] is not None or result["command"] is not None, "query has no result or error")
                return result
            else:
                raise RuntimeError(f"unexpected Query response {kind!r}")
        raise RuntimeError("Query response exceeded message bound")

    def query_integer(self, text=b'SELECT CAST(6 * 7 AS INTEGER) AS "answer" FROM RDB$DATABASE', expected=42):
        result = self.query(text)
        require(result["error"] is None and result["rows"] == [[str(expected)]] and result["command"] == "SELECT 1",
                "computed integer SELECT returned an unexpected result")
        require(result["columns"] == [{"name": "answer", "table": 0, "attribute": 0, "oid": 23,
                                       "size": 4, "modifier": -1, "format": 0}], "integer RowDescription mismatch")

    def query_error(self, text=b"SELECT CAST(1 AS DOUBLE PRECISION) FROM RDB$DATABASE", expected="0A000"):
        result = self.query(text)
        require(result["error"] is not None and result["error"]["C"] == expected,
                f"Query did not return expected SQLSTATE {expected}")
        return result

    def close(self, terminate=True):
        if terminate:
            self.send(packet(b"X"))
        self.connection.close()


def libpq_child():
    """Separate process bounds blocking libpq calls; credentials arrive on stdin."""
    params = json.load(sys.stdin)
    library = ctypes.CDLL(ctypes.util.find_library("pq") or "libpq.so.5")
    char_array = ctypes.POINTER(ctypes.c_char_p)
    functions = {
        "PQlibVersion": (ctypes.c_int, []),
        "PQconnectdbParams": (ctypes.c_void_p, [char_array, char_array, ctypes.c_int]),
        "PQstatus": (ctypes.c_int, [ctypes.c_void_p]),
        "PQprotocolVersion": (ctypes.c_int, [ctypes.c_void_p]),
        "PQtransactionStatus": (ctypes.c_int, [ctypes.c_void_p]),
        "PQexec": (ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_char_p]),
        "PQresultStatus": (ctypes.c_int, [ctypes.c_void_p]),
        "PQresultErrorField": (ctypes.c_char_p, [ctypes.c_void_p, ctypes.c_int]),
        "PQntuples": (ctypes.c_int, [ctypes.c_void_p]),
        "PQnfields": (ctypes.c_int, [ctypes.c_void_p]),
        "PQfname": (ctypes.c_char_p, [ctypes.c_void_p, ctypes.c_int]),
        "PQftype": (ctypes.c_uint, [ctypes.c_void_p, ctypes.c_int]),
        "PQgetvalue": (ctypes.c_char_p, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]),
        "PQclear": (None, [ctypes.c_void_p]),
        "PQfinish": (None, [ctypes.c_void_p]),
    }
    for name, (result, arguments) in functions.items():
        function = getattr(library, name)
        function.restype, function.argtypes = result, arguments
    options = {"host": "127.0.0.1", "port": str(params["port"]), "dbname": "wire_host",
               "user": "WIRE_HOST_TEST", "password": params["password"], "connect_timeout": "3",
               "sslmode": "disable", "gssencmode": "disable", "application_name": "pgwire_p2_libpq",
               "client_encoding": "UTF8"}
    keys = (ctypes.c_char_p * (len(options) + 1))(*(key.encode() for key in options), None)
    values = (ctypes.c_char_p * (len(options) + 1))(*(value.encode() for value in options.values()), None)
    connection = library.PQconnectdbParams(keys, values, 0)
    require(connection, "libpq did not allocate a connection")
    try:
        require(library.PQstatus(connection) == 0, "libpq startup failed")
        require(library.PQprotocolVersion(connection) == 3, "libpq protocol version mismatch")
        for sql in (b'SELECT CAST(6 * 7 AS INTEGER) AS "answer" FROM RDB$DATABASE', b"BEGIN", b"not SQL"):
            result = library.PQexec(connection, sql)
            require(result, "libpq did not allocate a result")
            try:
                if sql.startswith(b"SELECT"):
                    require(library.PQresultStatus(result) == 2, "libpq SELECT did not return PGRES_TUPLES_OK")
                    require(library.PQntuples(result) == 1 and library.PQnfields(result) == 1, "libpq SELECT dimensions mismatch")
                    require(library.PQfname(result, 0) == b"answer" and library.PQftype(result, 0) == 23,
                            "libpq integer metadata mismatch")
                    require(library.PQgetvalue(result, 0, 0) == b"42", "libpq computed value mismatch")
                else:
                    require(library.PQresultStatus(result) == 7, "libpq query did not return PGRES_FATAL_ERROR")
                    require(library.PQresultErrorField(result, ord("C")) == b"42601", "libpq parser SQLSTATE mismatch")
                require(library.PQtransactionStatus(connection) == 0, "libpq transaction status is not IDLE")
            finally:
                library.PQclear(result)
        print(json.dumps({"libpq_version": library.PQlibVersion(), "protocol": 3, "select_value": 42,
                          "select_oid": 23, "parser_sqlstate": "42601"}))
    finally:
        library.PQfinish(connection)


class StartupHarness(Harness):
    def __init__(self, source, work, timeout):
        super().__init__(source, work, timeout)
        self.other_password = secrets.token_urlsafe(32)
        self.denied_password = secrets.token_urlsafe(32)
        self.blocked_password = secrets.token_urlsafe(32)
        self.trap = None
        self.summary["transport"] = "real PostgreSQL protocol 3.0 TCP startup/authentication"
        self.summary["stage"] = "P2 startup/authentication regression with P3 SELECT execution"
        self.cases = []

    def redact(self, value):
        value = super().redact(value)
        for password in (self.other_password, self.denied_password, self.blocked_password):
            value = value.replace(password, "[REDACTED]")
        return value

    def prepare(self):
        shutil.copytree(self.source, self.runtime, symlinks=False)
        self.locks.mkdir()
        self.temp.mkdir()
        self.database.parent.mkdir(exist_ok=True)
        self.module = self.runtime / "plugins/libPgWire.so"
        require(self.module.is_file(), "build the PgWire plugin before running this harness")
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
CREATE USER WIRE_PG_OTHER PASSWORD '{self.other_password}';
CREATE USER WIRE_PG_DENIED PASSWORD '{self.denied_password}';
CREATE USER WIRE_PG_BLOCKED PASSWORD '{self.blocked_password}' INACTIVE;
CREATE TABLE WIRE_HOST_ROWS (ID INTEGER NOT NULL PRIMARY KEY);
GRANT SELECT, INSERT, UPDATE, DELETE ON WIRE_HOST_ROWS TO USER WIRE_HOST_TEST;
CREATE ROLE PGWIRE_OBSERVER SET SYSTEM PRIVILEGES TO MONITOR_ANY_ATTACHMENT;
GRANT PGWIRE_OBSERVER TO USER WIRE_HOST_TEST;
CREATE EXCEPTION PG_CONNECT_DENIED 'test connect rejection';
SET TERM ^ ;
CREATE TRIGGER PG_DENY_CONNECT ACTIVE ON CONNECT POSITION 0 AS
BEGIN
  IF (CURRENT_USER = 'WIRE_PG_DENIED') THEN EXCEPTION PG_CONNECT_DENIED;
END^
SET TERM ; ^
COMMIT;
QUIT;
""", embedded=True)
        # Separate physical files avoid per-database cache sharing between policy fixtures.
        self.policy_databases = {}
        for alias in ("pg_remote_off", "pg_auth_off", "pg_security_off", "pg_local_only", "pg_disallowed"):
            path = self.database.parent / (alias + ".fdb")
            shutil.copyfile(self.database, path)
            self.policy_databases[alias] = path
        self.trap = socket.socket()
        self.trap.bind(("127.0.0.1", 0))
        self.trap.listen(1)
        trap_port = self.trap.getsockname()[1]
        configs = {
            "pg_remote_off": "RemoteAccess = false",
            "pg_auth_off": "AuthServer = Legacy_Auth",
            "pg_security_off": f"SecurityDatabase = inet://127.0.0.1:{trap_port}/security.db",
            "pg_local_only": "Providers = Remote, Loopback",
            "pg_disallowed": "",
        }
        database_config = (f"wire_host = {self.database}\nWIRE_HOST_TEST = {self.database}\n"
                           f"security.db = {self.runtime / 'security6.fdb'}\n")
        for alias, path in self.policy_databases.items():
            database_config += f"{alias} = {path}\n{{\n{configs[alias]}\n}}\n"
        (self.runtime / "databases.conf").write_text(database_config)

    def configure(self, configured_native_port, endpoints):
        super().configure(configured_native_port, [])
        if not endpoints:
            return
        self.pg_port = endpoints[0]["port"]
        config_path = self.runtime / "firebird.conf"
        config = config_path.read_text().replace("WireProtocolPlugins = \n", "WireProtocolPlugins = PgWire\n")
        config = config.replace("WireProtocolPorts = \n", f"WireProtocolPorts = {self.pg_port}\n")
        config_path.write_text(config)
        module = endpoints[0].get("module", self.module)
        aliases = "wire_host,WIRE_HOST_TEST,pg_remote_off,pg_auth_off,pg_security_off,pg_local_only,configured_but_missing"
        (self.runtime / "plugins.conf").write_text(
            f"Plugin = PgWire\n{{\nModule = {module}\nRegisterName = PgWire\nConfig = Startup\n}}\n"
            f"Config = Startup\n{{\nBindAddress = 127.0.0.1\nWorkers = 4\nQueueSize = 16\n"
            f"DatabaseAliases = {aliases}\n}}\n")

    def start(self, phase, native_port, configured_native_port, endpoints, *, trace=False):
        if not trace:
            return super().start(phase, native_port, configured_native_port, endpoints)
        # -D preserves the tracee as our direct child, so the existing bounded
        # stop routine and native MON$SERVER_PID assertion still target Firebird.
        require(self.server is None, "previous server is still owned by the harness")
        self.phase, self.native_port = phase, native_port
        self.configure(configured_native_port, endpoints)
        self.events_path = self.work / f"{phase}.events"
        self.trace_path = self.work / "server-connect.trace"
        command = [shutil.which("strace"), "-D", "-f", "-e", "trace=connect", "-o", str(self.trace_path),
                   str(self.runtime / "bin/firebird"), "-d", "-p", str(native_port), "-el", str(self.locks)]
        self.server = subprocess.Popen(command, env=self.env, cwd=self.temp, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, errors="replace", start_new_session=True)
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
                            "endpoints": [{"alias": item["alias"], "port": item["port"]} for item in endpoints],
                            "trace": str(self.trace_path)}
        self.summary["runs"].append(self.run_summary)
        deadline = time.monotonic() + self.timeout
        while True:
            require(server.poll() is None, "traced server exited during startup; see trace-server.log")
            try:
                with socket.create_connection(("127.0.0.1", native_port), timeout=0.2):
                    return
            except OSError:
                require(time.monotonic() < deadline, "traced native listener startup timed out")
                time.sleep(0.05)

    def isql(self, label, sql, *, embedded=False, bad_password=False):
        if not embedded and not bad_password:
            sql = "SET ROLE PGWIRE_OBSERVER;\n" + sql
        return super().isql(label, sql, embedded=embedded, bad_password=bad_password)

    def attachment_count(self):
        output = self.isql("attachment_count", """SET LIST ON;
SELECT COUNT(*) AS PG_COUNT FROM MON$ATTACHMENTS
WHERE MON$REMOTE_VERSION = 'PgWire' AND MON$SYSTEM_FLAG = 0;
ROLLBACK;
QUIT;
""")
        return int(field(output, "PG_COUNT"))

    def no_attachments(self):
        deadline = time.monotonic() + 5
        while self.attachment_count():
            require(time.monotonic() < deadline, "PostgreSQL attachment leaked after disconnect")
            time.sleep(0.05)

    def check(self, name, action):
        action()
        self.cases.append(name)

    def identities(self):
        first = Client(self).login(fragmented=True)
        second = Client(self).login(user=b"WIRE_PG_OTHER", password=self.other_password)
        output = self.isql("identities", """SET LIST ON;
SELECT MON$USER AS PG_USER, MON$SERVER_PID AS PG_PID,
 MON$REMOTE_ADDRESS AS PG_PEER, MON$AUTH_METHOD AS PG_AUTH,
 MON$REMOTE_PROTOCOL AS PG_NETWORK, MON$REMOTE_PROCESS AS PG_APPLICATION,
 CS.RDB$CHARACTER_SET_NAME AS PG_CHARSET, MON$WIRE_ENCRYPTED AS PG_ENCRYPTED
FROM MON$ATTACHMENTS A JOIN RDB$CHARACTER_SETS CS
 ON CS.RDB$CHARACTER_SET_ID = A.MON$CHARACTER_SET_ID
WHERE MON$REMOTE_VERSION = 'PgWire' AND MON$SYSTEM_FLAG = 0;
ROLLBACK;
QUIT;
""")
        values = lambda name: re.findall(r"^" + name + r"\s+(.+?)\s*$", output, re.MULTILINE)
        require(sorted(values("PG_USER")) == ["WIRE_HOST_TEST", "WIRE_PG_OTHER"], "real attachment identities mismatch")
        require(values("PG_PID") == [str(self.server.pid)] * 2, "PG attachments belong to another process")
        peers = values("PG_PEER")
        require(len(peers) == 2 and all(peer.startswith("127.0.0.1") for peer in peers), "PG peer metadata absent")
        require(all(any(str(port) in peer for peer in peers) for port in (first.local_port, second.local_port)),
                "PG source ports are missing from attachment metadata")
        require(values("PG_CHARSET") == ["UTF8"] * 2, "PG attachment charset mismatch")
        require(values("PG_NETWORK") == ["TCPv4"] * 2, "PG attachment network metadata mismatch")
        require(values("PG_APPLICATION") == ["pgwire_p2_raw"] * 2, "PG application metadata mismatch")
        require(all("Srp256" in value for value in values("PG_AUTH")) and len(values("PG_AUTH")) == 2,
                "PG attachment did not use Srp256 authentication")
        require(values("PG_ENCRYPTED") == ["<false>"] * 2 or values("PG_ENCRYPTED") == ["FALSE"] * 2,
                "plaintext PG connection reported encryption")
        self.native_probe("native_with_pg_identities")
        first.query_integer()
        for text in (b"BEGIN", b"not SQL", b"SELECT $1 FROM RDB$DATABASE"):
            first.query_error(text, "42601")
            first.query_integer()
        second.send(packet(b"Q", b"SELECT CAST(1 AS INTEGER) FROM RDB$DATABASE\0") +
                    packet(b"Q", b"SELECT CAST(2 AS INTEGER) FROM RDB$DATABASE\0"))
        for expected in ("1", "2"):
            result = second.query_response()
            require(result["error"] is None and result["rows"] == [[expected]], "coalesced SELECT results mismatch")
        first.close()
        second.close()
        self.no_attachments()

    def denied(self, user, password, database, expected):
        client = Client(self)
        client.send(startup(user, database))
        kind, payload = client.message()
        if kind == b"R" and payload == struct.pack("!I", 3):
            client.send(packet(b"p", password.encode() + b"\0"))
        else:
            # Put back an already read ErrorResponse for the common validator.
            require(kind == b"E", "denied startup produced an unexpected message")
            require(error_fields(payload)["C"] == expected, "early startup denial SQLSTATE mismatch")
            require(error_fields(payload)["M"] == "Database connection rejected", "host denial exposed an internal error")
            require(not any(k in (b"R", b"Z") for k, _ in client.messages), "early denial authenticated the client")
            client.connection.settimeout(5)
            try:
                require(client.connection.recv(1) == b"", "early startup denial did not close")
            except ConnectionResetError:
                pass
            return
        client.fatal(expected)
        require(error_fields(client.messages[-1][1])["M"] == "Database connection rejected",
                "host denial exposed an internal error")

    def negotiation(self):
        client = Client(self)
        for code in (80877104, 80877103):
            client.send(struct.pack("!II", 8, code), fragmented=True)
            require(receive_exact(client.connection, 1) == b"N", "SSL/GSS request was not declined")
        client.login()
        client.query_error()
        client.close()

    def repeated_negotiation(self):
        client = Client(self)
        request = struct.pack("!II", 8, 80877103)
        client.send(request)
        require(receive_exact(client.connection, 1) == b"N", "SSL request was not declined")
        client.send(request)
        client.fatal("08P01")

    def malformed_startup(self, data, expected="08P01", truncated=False):
        client = Client(self)
        client.send(data)
        if truncated:
            client.connection.shutdown(socket.SHUT_WR)
        client.fatal(expected)

    def malformed_password(self, payload, expected="08P01"):
        client = Client(self)
        client.challenge()
        client.send(packet(b"p", payload))
        client.fatal(expected)

    def malformed_query(self, data, expected="08P01", truncated=False):
        client = Client(self).login()
        client.send(data)
        if truncated:
            client.connection.shutdown(socket.SHUT_WR)
        kind, payload = client.message()
        require(kind == b"E" and error_fields(payload)["C"] == expected, "malformed query SQLSTATE mismatch")
        client.connection.settimeout(5)
        try:
            require(client.connection.recv(1) == b"", "malformed query frame was not fatal")
        except ConnectionResetError:
            pass

    def libpq(self):
        result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--libpq-child"],
                                input=json.dumps({"port": self.pg_port, "password": self.password}),
                                env=self.env, text=True, capture_output=True, timeout=15)
        (self.work / "libpq.log").write_text(self.redact(result.stdout + result.stderr))
        require(result.returncode == 0, "real libpq startup/query smoke failed; see libpq.log")
        self.summary["libpq"] = json.loads(result.stdout)

    def native_transactions(self):
        self.isql("native_commit_and_rollback", """INSERT INTO WIRE_HOST_ROWS VALUES (1);
COMMIT;
INSERT INTO WIRE_HOST_ROWS VALUES (2);
ROLLBACK;
QUIT;
""")
        output = self.isql("native_persisted_rows", """SET LIST ON;
SELECT COUNT(*) AS NATIVE_ROWS, MIN(ID) AS NATIVE_ID FROM WIRE_HOST_ROWS;
ROLLBACK;
QUIT;
""")
        require(field(output, "NATIVE_ROWS") == "1" and field(output, "NATIVE_ID") == "1",
                "native commit/rollback behavior changed while PgWire was enabled")
        self.isql("native_bad_password", "QUIT;\n", bad_password=True)

    def run_main(self):
        native, configured_native, port = reserve_ports(3)
        self.start("startup", native, configured_native, [{"alias": "PgWire", "port": port}])
        self.check("native_transactions_and_bad_password", self.native_transactions)
        self.check("fragmented_startup_two_identities_and_query_recovery", self.identities)
        self.check("ssl_and_gss_declined", self.negotiation)
        self.check("repeated_ssl_request", self.repeated_negotiation)
        denies = [
            ("wrong_password", b"WIRE_HOST_TEST", self.bad_password, b"wire_host", "28P01"),
            ("unknown_user", b"WIRE_PG_UNKNOWN", self.bad_password, b"wire_host", "28P01"),
            ("inactive_user", b"WIRE_PG_BLOCKED", self.blocked_password, b"wire_host", "28P01"),
            ("attach_trigger_denies_after_valid_auth", b"WIRE_PG_DENIED", self.denied_password, b"wire_host", "XX000"),
            ("unknown_alias", b"WIRE_HOST_TEST", self.password, b"unknown", "3D000"),
            ("not_allowlisted_alias", b"WIRE_HOST_TEST", self.password, b"pg_disallowed", "3D000"),
            ("allowlisted_but_unknown_alias", b"WIRE_HOST_TEST", self.password, b"configured_but_missing", "3D000"),
            ("physical_path_denied", b"WIRE_HOST_TEST", self.password, str(self.database).encode(), "3D000"),
            ("uri_denied", b"WIRE_HOST_TEST", self.password, b"inet://127.0.0.1/wire_host", "3D000"),
            ("remote_access_false", b"WIRE_HOST_TEST", self.password, b"pg_remote_off", "42501"),
            ("per_database_authserver", b"WIRE_HOST_TEST", self.password, b"pg_auth_off", "28P01"),
            ("remote_security_database_denied", b"WIRE_HOST_TEST", self.password, b"pg_security_off", "3D000"),
        ]
        for name, user, password, database, expected in denies:
            self.check(name, lambda u=user, p=password, d=database, e=expected: self.denied(u, p, d, e))
        self.trap.settimeout(0.2)
        try:
            connection, _ = self.trap.accept()
        except socket.timeout:
            pass
        else:
            connection.close()
            raise RuntimeError("host attempted a remote security database connection")
        local = Client(self).login(database=b"pg_local_only")
        local.query_error()
        local.close()
        self.cases.append("local_engine_overrides_remote_provider_config")
        for length in (0, 3, 7, 16385, 0xFFFFFFFF):
            self.check(f"startup_length_{length}", lambda n=length: self.malformed_startup(struct.pack("!II", n, 196608)))
        self.check("unsupported_startup_options", lambda: self.malformed_startup(
            startup(extra=[(b"options", b"-c search_path=public")]), "0A000"))
        self.check("unknown_startup_parameter", lambda: self.malformed_startup(
            startup(extra=[(b"unexpected", b"value")]), "0A000"))
        self.check("invalid_utf8_startup", lambda: self.malformed_startup(
            startup(application_name=b"\xff"), "22021"))
        self.check("duplicate_startup_user", lambda: self.malformed_startup(
            startup(extra=[(b"user", b"WIRE_PG_OTHER")]), "08P01"))
        self.check("empty_startup_user", lambda: self.malformed_startup(startup(user=b""), "22023"))
        self.check("missing_startup_user", lambda: self.malformed_startup(startup(user=None), "22023"))
        self.check("empty_startup_database", lambda: self.malformed_startup(startup(database=b""), "22023"))
        self.check("unsupported_protocol_version", lambda: self.malformed_startup(
            struct.pack("!II", 8, 196609), "0A000"))
        default_database = Client(self).login(database=None)
        default_database.close()
        self.cases.append("missing_database_defaults_to_user_alias")
        self.check("missing_password_terminator", lambda: self.malformed_password(b"password"))
        self.check("embedded_password_nul", lambda: self.malformed_password(b"one\0two\0"))
        self.check("password_limit", lambda: self.malformed_password(b"x" * 4097 + b"\0"))
        self.check("invalid_utf8_password", lambda: self.malformed_password(b"\xff\0", "22021"))
        self.check("truncated_startup_header", lambda: self.malformed_startup(b"\0\0", truncated=True))
        self.check("truncated_startup_body", lambda: self.malformed_startup(startup()[:-1], truncated=True))
        for length in (0, 3, 1024 * 1024 + 1, 0xFFFFFFFF):
            self.check(f"query_length_{length}", lambda n=length: self.malformed_query(b"Q" + struct.pack("!I", n)))
        self.check("invalid_utf8_query", lambda: self.malformed_query(packet(b"Q", b"\xff\0"), "22021"))
        self.check("unterminated_query", lambda: self.malformed_query(packet(b"Q", b"SELECT 42")))
        self.check("embedded_query_nul", lambda: self.malformed_query(packet(b"Q", b"one\0two\0")))
        self.check("invalid_terminate_body", lambda: self.malformed_query(packet(b"X", b"\0")))
        self.check("truncated_query_body", lambda: self.malformed_query(packet(b"Q", b"SELECT 42\0")[:-1], truncated=True))
        self.check("libpq_ctypes_startup_and_query", self.libpq)
        self.no_attachments()
        self.native_probe("native_after_malformed_clients")
        authenticated = Client(self).login()
        waiting_password = Client(self)
        waiting_password.challenge()
        incomplete = Client(self)
        incomplete.send(b"\0\0")
        require(self.attachment_count() == 1, "incomplete authentication created a user attachment")
        self.stop()
        for client in (authenticated, waiting_password, incomplete):
            client.connection.settimeout(2)
            try:
                while client.connection.recv(4096):
                    pass
            except ConnectionResetError:
                pass
        self.cases.append("bounded_shutdown_authenticated_auth_wait_and_partial_startup")

    def run_trace(self):
        if not shutil.which("strace"):
            self.summary["connect_trace"] = {"skipped": "strace is not installed"}
            return
        native, configured_native, port = reserve_ports(3)
        self.start("trace", native, configured_native, [{"alias": "PgWire", "port": port}], trace=True)
        server_pid = self.server.pid
        first = Client(self).login()
        second = Client(self).login(database=b"pg_local_only")
        require(self.attachment_count() == 1, "traced PgWire attachment not visible to native SQL")
        self.native_probe("native_under_strace")
        first.query_integer()
        second.query_integer()
        self.denied(b"WIRE_HOST_TEST", self.bad_password, b"wire_host", "28P01")
        self.denied(b"WIRE_HOST_TEST", self.password, b"pg_security_off", "3D000")
        first.close()
        second.close()
        self.no_attachments()
        self.stop()
        trace = self.trace_path.read_text()
        require(re.search(rf"\b{server_pid}\b.*exited with 0", trace), "strace did not observe the server exit")
        connect_audit = classify_connects(trace)
        self.summary["connect_trace"] = {"path": str(self.trace_path), "server_pid": server_pid,
                                         **connect_audit,
                                         "scope": "server and all threads from startup through authentication and shutdown"}
        self.cases.append("no_server_backend_network_hop_syscall_trace")

    def close(self):
        try:
            super().close()
        finally:
            if self.trap:
                self.trap.close()


def main():
    if sys.argv[1:] == ["--libpq-child"]:
        libpq_child()
        return 0
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
    for name in ("bin/firebird", "bin/isql", "security6.fdb", "plugins/libPgWire.so"):
        if not (source / name).is_file():
            parser.error(f"completed build required: missing {source / name}")
    require(ctypes.util.find_library("pq"), "a libpq runtime is required")
    work.mkdir(mode=0o700)
    harness = StartupHarness(source, work.resolve(), args.timeout)
    try:
        harness.prepare()
        harness.run_main()
        harness.run_trace()
        harness.summary["passed"] = True
    except (Exception, KeyboardInterrupt) as error:
        harness.summary["error"] = harness.redact(f"{type(error).__name__}: {error}")
    finally:
        try:
            harness.close()
        except Exception as error:
            harness.summary["passed"] = False
            harness.summary["shutdown_error"] = harness.redact(f"{type(error).__name__}: {error}")
        harness.summary["checks"] = harness.cases
        path = work / "summary.json"
        path.write_text(json.dumps(harness.summary, indent=2) + "\n")
        print(json.dumps({"passed": harness.summary["passed"], "summary": str(path)}, indent=2))
    return 0 if harness.summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
