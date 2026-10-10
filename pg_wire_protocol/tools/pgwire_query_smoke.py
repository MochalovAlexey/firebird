#!/usr/bin/env python3
"""Real P3/M0 SELECT execution through raw TCP, C libpq and psycopg2.

Creates an isolated runtime and native fixtures. Genuine PostgreSQL development
headers and an interpreter with psycopg2 must be supplied explicitly. Passwords
are generated in memory and passed only through environment/stdin, never argv.
"""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import time

from pgwire_startup_smoke import Client, StartupHarness, packet
from wire_host_smoke import field, require, reserve_ports


ANSWER = b'SELECT CAST(6 * 7 AS INTEGER) AS "answer" FROM RDB$DATABASE'


def psycopg2_child():
    import psycopg2
    from psycopg2 import extensions

    settings = json.load(sys.stdin)
    connection = psycopg2.connect(host="127.0.0.1", port=settings["port"], dbname="wire_host",
                                  user="WIRE_HOST_TEST", password=settings["password"], connect_timeout=3,
                                  sslmode="disable", gssencmode="disable", client_encoding="UTF8",
                                  application_name="pgwire_m0_psycopg2")
    try:
        connection.autocommit = True
        cursor = connection.cursor()
        try:
            cursor.execute(ANSWER.decode())
            require(cursor.fetchone() == (42,), "psycopg2 computed answer differs")
            require(cursor.description[0].name == "answer" and cursor.description[0].type_code == 23,
                    "psycopg2 alias/OID differs")
            cursor.execute("SELECT CAST(7 * 7 AS INTEGER) FROM RDB$DATABASE")
            require(cursor.fetchone() == (49,), "psycopg2 second expression differs")
            cursor.execute("SELECT CAST(-32768 AS SMALLINT), CAST('9223372036854775807' AS BIGINT), "
                           "CAST(NULL AS INTEGER) FROM RDB$DATABASE")
            require(cursor.fetchone() == (-32768, 9223372036854775807, None), "psycopg2 integer/NULL conversion differs")
            require([column.type_code for column in cursor.description] == [21, 20, 23], "psycopg2 type OIDs differ")
            cursor.execute("SELECT ID, PAYLOAD FROM PGWIRE_ROWS ORDER BY ID")
            require(cursor.fetchall() == [(1, 31), (2, 47), (3, -9)], "psycopg2 table rows differ")
            cursor.execute("SELECT ID FROM PGWIRE_ROWS WHERE ID < 0")
            require(cursor.fetchall() == [] and cursor.rowcount == 0, "psycopg2 empty result differs")
            for sql, expected in (("not SQL", "42601"), ("BEGIN", "42601"),
                                  ("SELECT $1 FROM RDB$DATABASE", "42601"),
                                  ("SELECT CAST('x' AS VARCHAR(1)) FROM RDB$DATABASE", "0A000")):
                try:
                    cursor.execute(sql)
                except psycopg2.Error as error:
                    require(error.pgcode == expected, "psycopg2 error SQLSTATE differs")
                else:
                    raise RuntimeError("psycopg2 accepted unsupported SQL")
                require(connection.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE,
                        "psycopg2 error left an active transaction")
                cursor.execute(ANSWER.decode())
                require(cursor.fetchone() == (42,), "psycopg2 did not recover after SQL error")
        finally:
            cursor.close()
        print(json.dumps({"psycopg2_version": psycopg2.__version__,
                          "libpq_compile_version": psycopg2.__libpq_version__,
                          "libpq_runtime_version": extensions.libpq_version(),
                          "autocommit": connection.autocommit, "connection_context_used": False,
                          "answer": 42, "answer_oid": 23}))
    finally:
        connection.close()


class QueryHarness(StartupHarness):
    def __init__(self, source, work, timeout, args):
        super().__init__(source, work, timeout)
        self.args = args
        self.summary["stage"] = "P3/M0 integer SELECT execution"
        self.summary["transport"] = "real PostgreSQL protocol 3.0 TCP query execution"
        self.summary["platform"] = platform.platform()

    def prepare(self):
        super().prepare()
        self.isql("query_fixtures", f"""CONNECT '{self.database}';
CREATE TABLE PGWIRE_ROWS (ID INTEGER NOT NULL PRIMARY KEY, PAYLOAD INTEGER);
INSERT INTO PGWIRE_ROWS VALUES (1, 31);
INSERT INTO PGWIRE_ROWS VALUES (2, 47);
INSERT INTO PGWIRE_ROWS VALUES (3, -9);
CREATE TABLE PGWIRE_EFFECTS (ID INTEGER NOT NULL PRIMARY KEY);
CREATE TABLE PGWIRE_MANY (ID INTEGER NOT NULL PRIMARY KEY);
CREATE SEQUENCE PGWIRE_GUARD;
CREATE SEQUENCE PGWIRE_STREAM_GUARD;
CREATE SEQUENCE PGWIRE_LONG_GUARD;
GRANT SELECT, INSERT, UPDATE, DELETE ON PGWIRE_ROWS TO USER WIRE_HOST_TEST;
GRANT SELECT, INSERT, UPDATE, DELETE ON PGWIRE_EFFECTS TO USER WIRE_HOST_TEST;
GRANT SELECT ON PGWIRE_MANY TO USER WIRE_HOST_TEST;
GRANT USAGE ON SEQUENCE PGWIRE_GUARD TO USER WIRE_HOST_TEST;
GRANT USAGE ON SEQUENCE PGWIRE_STREAM_GUARD TO USER WIRE_HOST_TEST;
GRANT USAGE ON SEQUENCE PGWIRE_LONG_GUARD TO USER WIRE_HOST_TEST;
SET TERM ^ ;
CREATE PROCEDURE PGWIRE_EFFECT (P_ID INTEGER) RETURNS (V INTEGER) AS
BEGIN
  INSERT INTO PGWIRE_EFFECTS VALUES (:P_ID);
  V = P_ID;
  SUSPEND;
END^
CREATE PROCEDURE PGWIRE_FETCH_ERROR (P_ID INTEGER) RETURNS (V INTEGER) AS
BEGIN
  INSERT INTO PGWIRE_EFFECTS VALUES (:P_ID);
  V = 1;
  SUSPEND;
  V = 0;
  SUSPEND;
END^
CREATE PROCEDURE PGWIRE_STREAM (P_ID INTEGER) RETURNS (V INTEGER) AS
BEGIN
  INSERT INTO PGWIRE_EFFECTS VALUES (:P_ID);
  V = GEN_ID(PGWIRE_STREAM_GUARD, 1);
  SUSPEND;
  FOR SELECT A.ID FROM PGWIRE_MANY A CROSS JOIN PGWIRE_MANY B CROSS JOIN PGWIRE_MANY C INTO :V DO
    SUSPEND;
END^
CREATE PROCEDURE PGWIRE_LONG (P_ID INTEGER) RETURNS (V BIGINT) AS
BEGIN
  INSERT INTO PGWIRE_EFFECTS VALUES (:P_ID);
  V = GEN_ID(PGWIRE_LONG_GUARD, 1);
  SELECT COUNT(*) FROM PGWIRE_MANY A CROSS JOIN PGWIRE_MANY B CROSS JOIN PGWIRE_MANY C
    CROSS JOIN PGWIRE_MANY D CROSS JOIN PGWIRE_MANY E INTO :V;
  SUSPEND;
END^
EXECUTE BLOCK AS DECLARE VARIABLE I INTEGER = 0;
BEGIN
  WHILE (I < 257) DO
  BEGIN
    INSERT INTO PGWIRE_MANY VALUES (:I);
    I = I + 1;
  END
END^
SET TERM ; ^
GRANT EXECUTE ON PROCEDURE PGWIRE_EFFECT TO USER WIRE_HOST_TEST;
GRANT EXECUTE ON PROCEDURE PGWIRE_FETCH_ERROR TO USER WIRE_HOST_TEST;
GRANT EXECUTE ON PROCEDURE PGWIRE_STREAM TO USER WIRE_HOST_TEST;
GRANT EXECUTE ON PROCEDURE PGWIRE_LONG TO USER WIRE_HOST_TEST;
GRANT INSERT ON PGWIRE_EFFECTS TO PROCEDURE PGWIRE_EFFECT;
GRANT INSERT ON PGWIRE_EFFECTS TO PROCEDURE PGWIRE_FETCH_ERROR;
GRANT INSERT ON PGWIRE_EFFECTS TO PROCEDURE PGWIRE_STREAM;
GRANT INSERT ON PGWIRE_EFFECTS TO PROCEDURE PGWIRE_LONG;
GRANT SELECT ON PGWIRE_MANY TO PROCEDURE PGWIRE_STREAM;
GRANT SELECT ON PGWIRE_MANY TO PROCEDURE PGWIRE_LONG;
GRANT USAGE ON SEQUENCE PGWIRE_STREAM_GUARD TO PROCEDURE PGWIRE_STREAM;
GRANT USAGE ON SEQUENCE PGWIRE_LONG_GUARD TO PROCEDURE PGWIRE_LONG;
COMMIT;
QUIT;
""", embedded=True)

    def build_c_driver(self):
        source = Path(__file__).with_name("pgwire_libpq_query.c")
        self.c_driver = self.work / "pgwire_libpq_query"
        command = [self.args.cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                   "-I", str(self.args.libpq_include_dir), str(source), str(self.args.libpq_library),
                   "-o", str(self.c_driver)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=self.timeout)
        (self.work / "libpq-compile.log").write_text(result.stdout + result.stderr)
        require(result.returncode == 0, "direct C libpq harness compilation failed")
        self.summary["c_build"] = {"compiler": self.args.cc, "headers": str(self.args.libpq_include_dir),
                                   "library": str(self.args.libpq_library),
                                   "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                                   "libpq_fe_h_sha256": hashlib.sha256(
                                       (self.args.libpq_include_dir / "libpq-fe.h").read_bytes()).hexdigest()}
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source.parents[2],
                                  capture_output=True, text=True, timeout=5)
        if revision.returncode == 0:
            self.summary["source_revision"] = revision.stdout.strip()

    def driver_environment(self):
        env = {key: value for key, value in self.env.items() if not key.startswith("PG")}
        env.update(PGHOST="127.0.0.1", PGPORT=str(self.pg_port), PGDATABASE="wire_host",
                   PGUSER="WIRE_HOST_TEST", PGPASSWORD=self.password, PGSSLMODE="disable",
                   PGGSSENCMODE="disable", PGCONNECT_TIMEOUT="3", PGCLIENTENCODING="UTF8",
                   PGAPPNAME="pgwire_m0_c_libpq")
        return env

    def c_libpq(self):
        result = subprocess.run([str(self.c_driver)], env=self.driver_environment(), cwd=self.temp,
                                capture_output=True, text=True, timeout=self.timeout)
        (self.work / "libpq-direct-c.log").write_text(self.redact(result.stdout + result.stderr))
        require(result.returncode == 0, "direct C libpq query suite failed; see libpq-direct-c.log")
        self.summary["c_libpq"] = json.loads(result.stdout)

    def psycopg2(self):
        result = subprocess.run([str(self.args.driver_python), "-B", str(Path(__file__).resolve()), "--psycopg2-child"],
                                input=json.dumps({"port": self.pg_port, "password": self.password}),
                                env=self.driver_environment(), cwd=self.temp, capture_output=True,
                                text=True, timeout=self.timeout)
        (self.work / "psycopg2.log").write_text(self.redact(result.stdout + result.stderr))
        require(result.returncode == 0, "psycopg2 query suite failed; see psycopg2.log")
        self.summary["psycopg2"] = json.loads(result.stdout)

    def no_transactions(self):
        output = self.isql("idle_query_transactions", """SET LIST ON;
SELECT COUNT(*) AS PG_TRANSACTIONS FROM MON$TRANSACTIONS T
JOIN MON$ATTACHMENTS A ON A.MON$ATTACHMENT_ID = T.MON$ATTACHMENT_ID
WHERE A.MON$REMOTE_VERSION = 'PgWire' AND A.MON$SYSTEM_FLAG = 0;
ROLLBACK;
QUIT;
""")
        require(field(output, "PG_TRANSACTIONS") == "0", "session retained an implicit transaction")

    def scalar_native(self, sql, label="native_observer"):
        output = self.isql(label, "SET LIST ON;\n" + sql + ";\nCOMMIT;\nQUIT;\n")
        return int(field(output, "OBSERVED"))

    def select(self, client, sql, rows, oids=None):
        result = client.query(sql.encode() if isinstance(sql, str) else sql)
        expected = [[None if value is None else str(value) for value in row] for row in rows]
        require(result["error"] is None, f"SELECT returned error: {result['error']}")
        require(result["rows"] == expected, "SELECT rows differ from engine fixture")
        require(result["command"] == f"SELECT {len(rows)}", "SELECT row count/tag differs")
        if oids is not None:
            require([column["oid"] for column in result["columns"]] == oids, "result OIDs differ")
        self.no_transactions()
        return result

    def raw_results(self):
        client = Client(self).login()
        client.query_integer()
        identity = (b'SELECT CAST(CASE WHEN CURRENT_USER = \'WIRE_HOST_TEST\' THEN 1 ELSE 0 END AS INTEGER) '
                    b'AS "answer" FROM RDB$DATABASE')
        client.query_integer(identity, 1)
        result = self.select(client,
            "SELECT CAST(-32768 AS SMALLINT) AS \"small\", CAST('-2147483648' AS INTEGER) AS \"integer\", "
            "CAST('-9223372036854775808' AS BIGINT) AS \"big\", CAST(NULL AS INTEGER) AS \"nil\" FROM RDB$DATABASE",
            [(-32768, -2147483648, -9223372036854775808, None)], [21, 23, 20, 23])
        require([column["name"] for column in result["columns"]] == ["small", "integer", "big", "nil"],
                "quoted aliases changed")
        require([column["size"] for column in result["columns"]] == [2, 4, 8, 4], "integer sizes differ")
        require(all(column["format"] == 0 and column["table"] == 0 and column["attribute"] == 0
                    and column["modifier"] == -1 for column in result["columns"]), "integer metadata differs")
        result = self.select(client, 'SELECT CAST(49 AS INTEGER) AS "число" FROM RDB$DATABASE', [(49,)], [23])
        require(result["columns"][0]["name"] == "число", "UTF8 alias changed")
        self.select(client, "/* BEGIN $1 ; */ SELECT CAST(CASE WHEN 'BEGIN;$1' = 'BEGIN;$1' "
                    "THEN 42 ELSE 0 END AS INTEGER) FROM RDB$DATABASE -- ; BEGIN\n", [(42,)], [23])
        self.select(client, "SELECT ID, PAYLOAD FROM PGWIRE_ROWS ORDER BY ID", [(1, 31), (2, 47), (3, -9)], [23, 23])
        self.select(client, "SELECT ID FROM PGWIRE_ROWS WHERE ID < 0", [], [23])
        self.select(client, "SELECT ID FROM PGWIRE_MANY ORDER BY ID", [(number,) for number in range(257)], [23])
        self.isql("native_change_visible_to_pg", "UPDATE PGWIRE_ROWS SET PAYLOAD = 99 WHERE ID = 1;\nCOMMIT;\nQUIT;\n")
        self.select(client, "SELECT PAYLOAD FROM PGWIRE_ROWS WHERE ID = 1", [(99,)], [23])
        self.isql("native_restore_fixture", "UPDATE PGWIRE_ROWS SET PAYLOAD = 31 WHERE ID = 1;\nCOMMIT;\nQUIT;\n")
        # Fragmentation and two complete Query messages in one write exercise
        # message boundaries without changing the SQL delivered to the parser.
        result = client.query(ANSWER, fragmented=True)
        require(result["rows"] == [["42"]], "fragmented SQL result differs")
        client.send(packet(b"Q", ANSWER + b"\0") + packet(b"Q", ANSWER + b"\0"))
        require(client.query_response()["rows"] == [["42"]] and client.query_response()["rows"] == [["42"]],
                "coalesced Query results differ")
        client.close()
        self.no_attachments()

    def second_identity_permissions(self):
        client = Client(self).login(user=b"WIRE_PG_OTHER", password=self.other_password)
        client.query_error(b"SELECT ID FROM PGWIRE_ROWS", "42501")
        self.no_transactions()
        identity = (b'SELECT CAST(CASE WHEN CURRENT_USER = \'WIRE_PG_OTHER\' THEN 1 ELSE 0 END AS INTEGER) '
                    b'AS "answer" FROM RDB$DATABASE')
        client.query_integer(identity, 1)
        self.no_transactions()
        client.close()
        self.no_attachments()

    def errors_and_guards(self):
        client = Client(self).login()
        for sql, state in (("not SQL", "42601"), ("BEGIN", "42601"),
                           ("SELECT $1 FROM RDB$DATABASE", "42601"),
                           ("SELECT GEN_ID(PGWIRE_GUARD, 1) FROM RDB$DATABASE; "
                            "SELECT CAST(42 AS INTEGER) FROM RDB$DATABASE", "42601"),
                           ("SELECT ID FROM PGWIRE_MISSING", "42P01"),
                           ("SELECT CAST(? AS INTEGER) FROM RDB$DATABASE", "0A000"),
                           ("SELECT CAST(1.25 AS NUMERIC(9,2)) FROM RDB$DATABASE", "0A000"),
                           ("SELECT CAST(1 AS NUMERIC(9,0)) FROM RDB$DATABASE", "0A000"),
                           ("SELECT CAST('x' AS VARCHAR(1)) FROM RDB$DATABASE", "0A000")):
            client.query_error(sql.encode(), state)
            self.no_transactions()
            client.query_integer()
        guarded = [
            "INSERT INTO PGWIRE_ROWS VALUES (901, 17) RETURNING GEN_ID(PGWIRE_GUARD, 1)",
            "INSERT INTO PGWIRE_ROWS SELECT 902, 18 FROM RDB$DATABASE RETURNING GEN_ID(PGWIRE_GUARD, 1)",
            "UPDATE PGWIRE_ROWS SET PAYLOAD = 888 WHERE ID = 1 RETURNING GEN_ID(PGWIRE_GUARD, 1)",
            "EXECUTE BLOCK RETURNS (ANSWER INTEGER) AS BEGIN "
            "ANSWER = GEN_ID(PGWIRE_GUARD, 1); INSERT INTO PGWIRE_EFFECTS VALUES (701); SUSPEND; END",
            "INSERT INTO PGWIRE_EFFECTS VALUES (702)",
            "SELECT CAST(V AS VARCHAR(20)) FROM PGWIRE_STREAM(703)",
        ]
        for sql in guarded:
            result = client.query_error(sql.encode(), "0A000")
            require(result["columns"] is None and not result["rows"], "capability refusal started producing results")
            require(self.scalar_native("SELECT GEN_ID(PGWIRE_GUARD, 0) AS OBSERVED FROM RDB$DATABASE") == 0,
                    "rejected statement advanced a nontransactional sequence")
            require(self.scalar_native("SELECT GEN_ID(PGWIRE_STREAM_GUARD, 0) AS OBSERVED FROM RDB$DATABASE") == 0,
                    "unsupported result type executed the selectable procedure")
            require(self.scalar_native("SELECT COUNT(*) AS OBSERVED FROM PGWIRE_ROWS WHERE ID IN (901, 902)") == 0,
                    "rejected DML changed a row")
            require(self.scalar_native("SELECT PAYLOAD AS OBSERVED FROM PGWIRE_ROWS WHERE ID = 1") == 31,
                    "rejected UPDATE changed a row")
            require(self.scalar_native("SELECT COUNT(*) AS OBSERVED FROM PGWIRE_EFFECTS") == 0,
                    "rejected statement executed side effects")
            self.no_transactions()
            client.query_integer()
        client.close()
        self.no_attachments()

    def implicit_transactions(self):
        client = Client(self).login()
        self.select(client, "SELECT V FROM PGWIRE_EFFECT(801)", [(801,)], [23])
        require(self.scalar_native("SELECT COUNT(*) AS OBSERVED FROM PGWIRE_EFFECTS WHERE ID = 801") == 1,
                "successful implicit SELECT did not commit procedure changes")
        failed = client.query_error(b"SELECT CAST(1 / V AS INTEGER) FROM PGWIRE_FETCH_ERROR(802)", "22012")
        require(failed["rows"] == [["1"]], "fetch error did not occur after the first successful DataRow")
        require(failed["command"] is None, "fetch failure emitted a successful CommandComplete")
        require(self.scalar_native("SELECT COUNT(*) AS OBSERVED FROM PGWIRE_EFFECTS WHERE ID = 802") == 0,
                "failed implicit SELECT did not roll back procedure changes")
        self.no_transactions()
        client.query_integer()
        client.close()
        self.no_attachments()

    def disconnects(self):
        client = Client(self).login()
        client.send(packet(b"Q", b"SELECT V FROM PGWIRE_STREAM(803)\0"))
        require(client.message()[0] == b"T", "streaming SELECT did not start with metadata")
        require(client.message()[0] == b"D", "streaming procedure did not produce its first row")
        client.close(terminate=False)
        self.no_attachments()
        self.no_transactions()
        require(self.scalar_native("SELECT GEN_ID(PGWIRE_STREAM_GUARD, 0) AS OBSERVED FROM RDB$DATABASE") == 1,
                "streaming procedure was not executed before disconnect")
        require(self.scalar_native("SELECT COUNT(*) AS OBSERVED FROM PGWIRE_EFFECTS WHERE ID = 803") == 0,
                "disconnect did not roll back the streaming procedure's changes")
        healthy = Client(self).login()
        healthy.query_integer()
        healthy.close()
        self.no_attachments()

    def failed_endpoints(self):
        native, configured_native, port = reserve_ports(3)
        self.start("missing_pgwire", native, configured_native,
                   [{"alias": "PgWire", "port": port, "module": self.work / "missing-PgWire-module"}])
        self.native_probe("native_despite_missing_pgwire")
        try:
            connection = self.connect(port)
        except ConnectionRefusedError:
            pass
        else:
            connection.close()
            raise RuntimeError("missing PgWire module opened a listener")
        self.endpoint_diagnostic(port)
        self.stop()
        self.cases.append("missing_actual_pgwire_module_preserves_native")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            reservation.listen(1)
            port = reservation.getsockname()[1]
            native, configured_native = reserve_ports(2)
            self.start("occupied_pgwire", native, configured_native, [{"alias": "PgWire", "port": port}])
            self.native_probe("native_despite_occupied_pgwire_port")
            self.endpoint_diagnostic(port)
            require("libPgWire.so" not in Path(f"/proc/{self.server.pid}/maps").read_text(),
                    "occupied endpoint loaded PgWire before bind succeeded")
            self.stop()
        self.cases.append("occupied_pgwire_port_preserves_native")

    def endpoint_diagnostic(self, port):
        log = (self.runtime / "firebird.log").read_text()
        require(f"Wire protocol PgWire port {port}:" in log, "failed endpoint has no alias/port diagnostic")

    def shutdown_active_query(self):
        client = Client(self).login()
        client.send(packet(b"Q", b"SELECT V FROM PGWIRE_LONG(804)\0"))
        deadline = time.monotonic() + 5
        while True:
            active = self.scalar_native("""SELECT COUNT(*) AS OBSERVED FROM MON$STATEMENTS S
JOIN MON$ATTACHMENTS A ON A.MON$ATTACHMENT_ID = S.MON$ATTACHMENT_ID
WHERE A.MON$REMOTE_VERSION = 'PgWire' AND S.MON$STATE = 1""", "active_engine_query")
            entered = self.scalar_native("SELECT GEN_ID(PGWIRE_LONG_GUARD, 0) AS OBSERVED FROM RDB$DATABASE",
                                         "long_procedure_entered")
            if active and entered == 1:
                break
            require(time.monotonic() < deadline, "long SELECT never became active in the engine")
            time.sleep(0.05)
        self.stop()
        client.connection.settimeout(2)
        try:
            while client.connection.recv(4096):
                pass
        except ConnectionResetError:
            pass
        output = self.isql("shutdown_rolls_back_query", f"""CONNECT '{self.database}';
SET LIST ON;
SELECT COUNT(*) AS OBSERVED FROM PGWIRE_EFFECTS WHERE ID = 804;
ROLLBACK;
QUIT;
""", embedded=True)
        require(field(output, "OBSERVED") == "0", "server shutdown committed an unfinished SELECT")

    def run_queries(self):
        native, configured_native, port = reserve_ports(3)
        self.start("queries", native, configured_native, [{"alias": "PgWire", "port": port}])
        ready = Client(self).login()
        ready.close()
        self.no_attachments()
        self.cases.append("pgwire_authenticated_ready_before_driver_tests")
        self.check("direct_c_libpq_pqexec", self.c_libpq)
        self.check("psycopg2_explicit_autocommit", self.psycopg2)
        self.check("raw_integer_null_metadata_rows_and_native_visibility", self.raw_results)
        self.check("second_identity_engine_permissions_and_recovery", self.second_identity_permissions)
        self.check("engine_errors_recovery_and_preexecution_guards", self.errors_and_guards)
        self.check("implicit_select_commit_and_rollback", self.implicit_transactions)
        self.check("disconnect_during_result_releases_engine_resources", self.disconnects)
        self.native_probe("native_after_query_suite")
        self.check("shutdown_cancels_active_engine_query_and_rolls_back", self.shutdown_active_query)


def main():
    if sys.argv[1:] == ["--psycopg2-child"]:
        psycopg2_child()
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firebird-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--libpq-include-dir", type=Path, required=True)
    parser.add_argument("--libpq-library", type=Path, required=True)
    parser.add_argument("--driver-python", type=Path, required=True)
    parser.add_argument("--cc", default="cc")
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
    for name in ("libpq_library", "driver_python"):
        path = getattr(args, name).absolute()
        if not path.is_file():
            parser.error(f"missing dependency: {path}")
        setattr(args, name, path)
    args.libpq_include_dir = args.libpq_include_dir.resolve()
    for name in ("libpq-fe.h", "postgres_ext.h", "pg_config.h"):
        if not (args.libpq_include_dir / name).is_file():
            parser.error(f"genuine libpq development headers required: missing {name}")
    args.cc = shutil.which(args.cc)
    if not args.cc:
        parser.error("C compiler is unavailable")
    work.mkdir(mode=0o700)
    harness = QueryHarness(source, work.resolve(), args.timeout, args)
    try:
        harness.prepare()
        harness.build_c_driver()
        harness.failed_endpoints()
        harness.run_queries()
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
