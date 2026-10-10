#!/usr/bin/env python3
"""P4 Simple Query acceptance against a private Firebird runtime and database.

Exercises genuine PostgreSQL TCP through raw frames, C libpq and psycopg2.
The original build and installed/live databases are never modified.
"""

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

from pgwire_query_smoke import QueryHarness
from pgwire_startup_smoke import Client, StartupHarness, error_fields, packet
from wire_host_smoke import field, require, reserve_ports


class SimpleClient(Client):
    """Reuse the raw result decoder, adding the P4 EmptyQueryResponse."""
    def message(self, timeout=5):
        pending = getattr(self, "pending", None)
        if pending is not None:
            self.pending = None
            return pending
        return super().message(timeout)

    def query_response(self):
        message = self.message()
        if message[0] == b"I":
            require(message[1] == b"", "EmptyQueryResponse has a body")
            require(self.message() == (b"Z", b"I"), "EMPTY did not preserve idle state")
            return {"columns": None, "rows": [], "command": None, "error": None, "empty": True}
        self.pending = message
        result = super().query_response()
        result["empty"] = False
        return result


def psycopg2_child():
    import psycopg2
    from psycopg2 import extensions

    settings = json.load(sys.stdin)
    connection = psycopg2.connect(host="127.0.0.1", port=settings["port"], dbname="wire_host",
        user="WIRE_HOST_TEST", password=settings["password"], connect_timeout=3,
        sslmode="disable", gssencmode="disable", client_encoding="UTF8", application_name="pgwire_p4_psycopg2")
    try:
        connection.autocommit = True
        cursor = connection.cursor()
        try:
            cursor.execute("CREATE TABLE P4_PY_ROWS (ID INTEGER PRIMARY KEY, B BOOLEAN, N NUMERIC(18,2), T VARCHAR(20))")
            require(cursor.statusmessage == "CREATE TABLE", "psycopg2 CREATE tag differs")
            cursor.execute("INSERT INTO P4_PY_ROWS VALUES (1, TRUE, -12.30, 'Я🙂 ') RETURNING ID, B, N, T")
            require(cursor.statusmessage == "INSERT 0 1", "psycopg2 INSERT RETURNING tag differs")
            require(cursor.fetchone() == (1, True, Decimal("-12.30"), "Я🙂 "), "psycopg2 converted types differ")
            require([column.type_code for column in cursor.description] == [23, 16, 1700, 1043], "psycopg2 OIDs differ")
            cursor.execute("SELECT CAST('Я' AS CHAR(4)), CAST('A ' AS VARCHAR(4)), CAST('' AS VARCHAR(4)), "
                           "CAST(NULL AS BOOLEAN), CAST(12 AS NUMERIC(9,0)) FROM RDB$DATABASE")
            row = cursor.fetchone()
            require(row == ("Я   ", "A ", "", None, Decimal("12")), "psycopg2 CHAR/empty/NULL/numeric values differ")
            require([column.type_code for column in cursor.description] == [1042, 1043, 1043, 16, 1700],
                    "psycopg2 logical CHAR/numeric types were lost")
            cursor.execute("UPDATE P4_PY_ROWS SET N = 10.25 RETURNING N")
            require(cursor.statusmessage == "UPDATE 1" and cursor.fetchone() == (Decimal("10.25"),),
                    "psycopg2 UPDATE result/tag differs")
            try:
                cursor.execute("INSERT INTO P4_PY_ROWS (ID) VALUES (1)")
            except psycopg2.Error as error:
                require(error.pgcode == "23505", "psycopg2 duplicate SQLSTATE differs")
            else:
                raise RuntimeError("duplicate INSERT unexpectedly succeeded")
            require(connection.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE,
                    "implicit DML error retained a transaction")
            cursor.execute("DELETE FROM P4_PY_ROWS RETURNING ID")
            require(cursor.statusmessage == "DELETE 1" and cursor.fetchall() == [(1,)], "psycopg2 DELETE differs")
            cursor.execute("DROP TABLE P4_PY_ROWS")
            require(cursor.statusmessage == "DROP TABLE", "psycopg2 DROP tag differs")
        finally:
            cursor.close()
        print(json.dumps({"version": psycopg2.__version__, "libpq_version": extensions.libpq_version(),
                          "autocommit": True, "connection_context_used": False, "types": [16, 1042, 1043, 1700]}))
    finally:
        connection.close()


class SimpleHarness(QueryHarness):
    def __init__(self, source, work, timeout, args):
        super().__init__(source, work, timeout, args)
        self.summary["stage"] = "P4 Simple Query DML/DDL, basic scalar codecs and EMPTY"

    def prepare(self):
        StartupHarness.prepare(self)
        self.isql("simple_fixtures", f"""CONNECT '{self.database}';
CREATE TABLE P4_ROWS (ID INTEGER NOT NULL PRIMARY KEY, V INTEGER CHECK (V >= 0),
  TXT VARCHAR(40), B BOOLEAN, N NUMERIC(18,2));
CREATE TABLE P4_AUDIT (ID INTEGER);
CREATE TABLE P4_EFFECTS (ID INTEGER NOT NULL PRIMARY KEY);
CREATE SEQUENCE P4_GUARD;
CREATE EXCEPTION P4_COMMIT_DENIED 'P4 deliberately rejected commit';
GRANT SELECT, INSERT, UPDATE, DELETE ON P4_ROWS TO USER WIRE_HOST_TEST;
GRANT SELECT, INSERT, UPDATE, DELETE ON P4_EFFECTS TO USER WIRE_HOST_TEST;
GRANT SELECT ON P4_AUDIT TO USER WIRE_HOST_TEST;
GRANT USAGE ON SEQUENCE P4_GUARD TO USER WIRE_HOST_TEST;
GRANT CREATE TABLE TO USER WIRE_HOST_TEST;
GRANT CREATE DOMAIN TO USER WIRE_HOST_TEST;
GRANT CREATE SEQUENCE TO USER WIRE_HOST_TEST;
GRANT CREATE VIEW TO USER WIRE_HOST_TEST;
GRANT CREATE PROCEDURE TO USER WIRE_HOST_TEST;
GRANT CREATE EXCEPTION TO USER WIRE_HOST_TEST;
GRANT CREATE PACKAGE TO USER WIRE_HOST_TEST;
SET TERM ^ ;
CREATE TRIGGER P4_ROWS_AI FOR P4_ROWS ACTIVE AFTER INSERT POSITION 0 AS
BEGIN
  INSERT INTO P4_AUDIT VALUES (NEW.ID);
END^
CREATE PROCEDURE P4_UNSUPPORTED RETURNS (V DOUBLE PRECISION) AS
BEGIN
  V = GEN_ID(P4_GUARD, 1);
  INSERT INTO P4_EFFECTS VALUES (703);
  SUSPEND;
END^
CREATE PROCEDURE P4_FETCH_ERROR RETURNS (V INTEGER) AS
BEGIN
  INSERT INTO P4_EFFECTS VALUES (704);
  V = 1;
  SUSPEND;
  V = 0;
  SUSPEND;
END^
CREATE PROCEDURE P4_COMMIT_ERROR RETURNS (V INTEGER) AS
BEGIN
  INSERT INTO P4_EFFECTS VALUES (705);
  V = RDB$SET_CONTEXT('USER_TRANSACTION', 'P4_FAIL_COMMIT', '1');
  V = 705;
  SUSPEND;
END^
CREATE TRIGGER P4_REJECT_COMMIT ACTIVE ON TRANSACTION COMMIT POSITION 0 AS
BEGIN
  IF (CURRENT_USER = 'WIRE_HOST_TEST' AND
      RDB$GET_CONTEXT('USER_TRANSACTION', 'P4_FAIL_COMMIT') = '1') THEN
    EXCEPTION P4_COMMIT_DENIED;
END^
SET TERM ; ^
GRANT INSERT ON P4_AUDIT TO TRIGGER P4_ROWS_AI;
GRANT INSERT ON P4_EFFECTS TO PROCEDURE P4_UNSUPPORTED;
GRANT INSERT ON P4_EFFECTS TO PROCEDURE P4_FETCH_ERROR;
GRANT INSERT ON P4_EFFECTS TO PROCEDURE P4_COMMIT_ERROR;
GRANT USAGE ON EXCEPTION P4_COMMIT_DENIED TO TRIGGER P4_REJECT_COMMIT;
GRANT USAGE ON SEQUENCE P4_GUARD TO PROCEDURE P4_UNSUPPORTED;
GRANT EXECUTE ON PROCEDURE P4_UNSUPPORTED TO USER WIRE_HOST_TEST;
GRANT EXECUTE ON PROCEDURE P4_FETCH_ERROR TO USER WIRE_HOST_TEST;
GRANT EXECUTE ON PROCEDURE P4_COMMIT_ERROR TO USER WIRE_HOST_TEST;
COMMIT;
QUIT;
""", embedded=True)

    def sql(self, client, text, tag, rows=None, oids=None, unordered=False):
        result = client.query(text.encode("utf-8") if isinstance(text, str) else text)
        require(result["error"] is None and not result["empty"], f"SQL failed: {result['error']}")
        require(result["command"] == tag, f"command tag {result['command']!r}, expected {tag!r}")
        expected = [] if rows is None else [[None if value is None else str(value) for value in row] for row in rows]
        actual = result["rows"]
        require(sorted(actual) == sorted(expected) if unordered else actual == expected,
                f"rows {actual!r}, expected {expected!r}")
        if rows is None:
            require(result["columns"] is None, "command without output sent RowDescription")
        else:
            require(result["columns"] is not None, "result is missing RowDescription")
        if oids is not None:
            require([column["oid"] for column in result["columns"]] == oids, "logical result OIDs differ")
        self.no_transactions()
        return result

    def scalar_types(self):
        client = SimpleClient(self).login()
        result = self.sql(client, """SELECT TRUE AS "yes", FALSE AS "no", CAST(NULL AS BOOLEAN) AS "unknown",
CAST('A' AS CHAR(4)) AS "ascii", CAST('Я' AS CHAR(4)) AS "utf8",
CAST('' AS CHAR(4)) AS "blank", CAST('A ' AS CHAR(4)) AS "trailing",
CAST('A ' AS VARCHAR(4)) AS "varying", CAST('Я🙂 ' AS VARCHAR(10)) AS "unicode",
CAST('' AS VARCHAR(4)) AS "empty", CAST(NULL AS VARCHAR(4)) AS "null"
FROM RDB$DATABASE""", "SELECT 1",
            [("t", "f", None, "A   ", "Я   ", "    ", "A   ", "A ", "Я🙂 ", "", None)],
            [16, 16, 16, 1042, 1042, 1042, 1042, 1043, 1043, 1043, 1043])
        require([column["size"] for column in result["columns"]] == [1, 1, 1] + [-1] * 8,
                "boolean/string wire type lengths differ")
        require(all(column["modifier"] == -1 and column["format"] == 0 and column["table"] == 0
                    and column["attribute"] == 0 for column in result["columns"]), "scalar metadata differs")
        self.sql(client, """SELECT CAST(1.20 AS NUMERIC(4,2)), CAST(-12345.60 AS DECIMAL(9,2)),
CAST('-92233720368547758.08' AS NUMERIC(18,2)), CAST('92233720368547758.07' AS NUMERIC(18,2)),
CAST(-0.0123 AS DECIMAL(18,4)), CAST(12 AS NUMERIC(9,0)), CAST(NULL AS NUMERIC(18,2))
FROM RDB$DATABASE""", "SELECT 1", [("1.20", "-12345.60", "-92233720368547758.08",
            "92233720368547758.07", "-0.0123", "12", None)], [1700] * 7)
        self.sql(client, "SELECT CAST('BEGIN;$1' AS VARCHAR(20)) AS \"BEGIN;$1\" FROM RDB$DATABASE",
                 "SELECT 1", [("BEGIN;$1",)], [1043])
        client.query_error(b"SELECT CAST(ASCII_CHAR(0) AS VARCHAR(1) CHARACTER SET UTF8) FROM RDB$DATABASE", "22021")
        client.query_integer()
        client.close()
        self.no_attachments()

    def dml(self):
        client = SimpleClient(self).login()
        self.sql(client, "INSERT INTO P4_ROWS(ID,V,TXT,B,N) VALUES (1,10,'one',TRUE,1.25)", "INSERT 0 1")
        self.sql(client, "INSERT INTO P4_ROWS(ID,V) SELECT 2,20 FROM RDB$DATABASE UNION ALL SELECT 3,30 FROM RDB$DATABASE",
                 "INSERT 0 2")
        self.sql(client, "INSERT INTO P4_ROWS(ID,V) VALUES (4,40) RETURNING ID,V", "INSERT 0 1", [(4, 40)], [23, 23])
        self.sql(client, "INSERT INTO P4_ROWS(ID,V) SELECT 5,50 FROM RDB$DATABASE UNION ALL SELECT 6,60 FROM RDB$DATABASE "
                 "RETURNING ID", "INSERT 0 2", [(5,), (6,)], [23], unordered=True)
        self.sql(client, "UPDATE P4_ROWS SET V=V+1 WHERE ID<=3 RETURNING ID,V", "UPDATE 3",
                 [(1, 11), (2, 21), (3, 31)], [23, 23], unordered=True)
        self.sql(client, "UPDATE P4_ROWS SET V=1 WHERE ID<0", "UPDATE 0")
        self.sql(client, "UPDATE P4_ROWS SET V=1 WHERE ID<0 RETURNING ID", "UPDATE 0", [], [23])
        self.sql(client, "DELETE FROM P4_ROWS WHERE ID IN (5,6) RETURNING ID", "DELETE 2", [(5,), (6,)], [23], unordered=True)
        self.sql(client, "DELETE FROM P4_ROWS WHERE ID<0", "DELETE 0")
        self.sql(client, "MERGE INTO P4_ROWS T USING (SELECT 1 AS ID,111 AS V FROM RDB$DATABASE "
                 "UNION ALL SELECT 7,70 FROM RDB$DATABASE) S ON T.ID=S.ID "
                 "WHEN MATCHED THEN UPDATE SET T.V=S.V WHEN NOT MATCHED THEN INSERT(ID,V) VALUES(S.ID,S.V) RETURNING T.ID,T.V",
                 "MERGE 2", [(1, 111), (7, 70)], [23, 23], unordered=True)
        self.sql(client, "MERGE INTO P4_ROWS T USING (SELECT 2 AS ID FROM RDB$DATABASE) S ON T.ID=S.ID "
                 "WHEN MATCHED THEN DELETE", "MERGE 1")
        self.sql(client, "MERGE INTO P4_ROWS T USING (SELECT 999 AS ID FROM RDB$DATABASE) S ON T.ID=S.ID "
                 "WHEN MATCHED THEN UPDATE SET V=999", "MERGE 0")
        self.sql(client, "UPDATE OR INSERT INTO P4_ROWS(ID,V) VALUES(7,77) MATCHING(ID) RETURNING ID,V",
                 "UPDATE OR INSERT 1", [(7, 77)], [23, 23])
        self.sql(client, "UPDATE OR INSERT INTO P4_ROWS(ID,V) VALUES(8,80) MATCHING(ID)", "UPDATE OR INSERT 1")
        self.sql(client, "UPDATE OR INSERT INTO P4_ROWS(ID,V) VALUES(9,90) MATCHING(ID) RETURNING ID",
                 "UPDATE OR INSERT 1", [(9,)], [23])
        self.sql(client, "DELETE FROM P4_ROWS WHERE ID=4", "DELETE 1")
        self.sql(client, "INSERT INTO P4_ROWS(ID,V) SELECT 10,100 FROM RDB$DATABASE WHERE 1=0 RETURNING ID",
                 "INSERT 0 0", [], [23])
        require(self.scalar_native("SELECT COUNT(*) AS OBSERVED FROM P4_ROWS") == 5, "native DML row count differs")
        require(self.scalar_native("SELECT SUM(V) AS OBSERVED FROM P4_ROWS") == 389, "native DML values differ")
        require(self.scalar_native("SELECT COUNT(*) AS OBSERVED FROM P4_AUDIT") == 9, "INSERT trigger did not execute")
        self.isql("native_to_pg_change", "UPDATE P4_ROWS SET V=912 WHERE ID=9;\nCOMMIT;\nQUIT;\n")
        self.sql(client, "SELECT V FROM P4_ROWS WHERE ID=9", "SELECT 1", [(912,)], [23])
        client.close()
        self.no_attachments()

    def ddl(self):
        client = SimpleClient(self).login()
        operations = [
            ("CREATE DOMAIN P4_DOMAIN AS INTEGER DEFAULT 5 NOT NULL", "CREATE DOMAIN"),
            ("ALTER DOMAIN P4_DOMAIN SET DEFAULT 6", "ALTER DOMAIN"),
            ("CREATE TABLE P4_DDL(ID P4_DOMAIN, V INTEGER)", "CREATE TABLE"),
            ("ALTER TABLE P4_DDL ADD EXTRA INTEGER", "ALTER TABLE"),
            ("CREATE INDEX P4_INDEX ON P4_DDL(V)", "CREATE INDEX"),
            ("ALTER INDEX P4_INDEX INACTIVE", "ALTER INDEX"),
            ("SET STATISTICS INDEX P4_INDEX", "SET STATISTICS"),
            ("CREATE SEQUENCE P4_SEQUENCE START WITH 10", "CREATE SEQUENCE"),
            ("ALTER SEQUENCE P4_SEQUENCE RESTART WITH 40", "ALTER SEQUENCE"),
            ("CREATE VIEW P4_VIEW AS SELECT ID,V FROM P4_DDL", "CREATE VIEW"),
            ("ALTER VIEW P4_VIEW AS SELECT ID,V FROM P4_DDL WHERE V>=0", "ALTER VIEW"),
            ("COMMENT ON VIEW P4_VIEW IS 'UTF8 комментарий; BEGIN'", "COMMENT"),
            ("GRANT SELECT ON P4_VIEW TO USER WIRE_PG_OTHER", "GRANT"),
            ("REVOKE SELECT ON P4_VIEW FROM USER WIRE_PG_OTHER", "REVOKE"),
            ("CREATE EXCEPTION P4_EXCEPTION 'first'", "CREATE EXCEPTION"),
            ("ALTER EXCEPTION P4_EXCEPTION 'second'", "ALTER EXCEPTION"),
            ("CREATE PROCEDURE P4_PROC RETURNS(V INTEGER) AS BEGIN V=6; SUSPEND; END", "CREATE PROCEDURE"),
            ("CREATE OR ALTER PROCEDURE P4_PROC RETURNS(V INTEGER) AS BEGIN V=7; SUSPEND; END", "CREATE OR ALTER PROCEDURE"),
            ("ALTER PROCEDURE P4_PROC RETURNS(V INTEGER) AS BEGIN V=8; SUSPEND; END", "ALTER PROCEDURE"),
        ]
        for sql, tag in operations:
            self.sql(client, sql, tag)
        require(self.scalar_native("SELECT COUNT(*) AS OBSERVED FROM RDB$RELATIONS WHERE RDB$RELATION_NAME='P4_VIEW'") == 1,
                "native observer cannot see committed CREATE VIEW")
        require(self.scalar_native("SELECT COUNT(*) AS OBSERVED FROM RDB$FIELDS WHERE RDB$FIELD_NAME='P4_DOMAIN'") == 1,
                "native observer cannot see committed CREATE DOMAIN")
        self.sql(client, "INSERT INTO P4_DDL(V) VALUES(22) RETURNING ID,V", "INSERT 0 1", [(6, 22)], [23, 23])
        self.sql(client, "EXECUTE PROCEDURE P4_PROC", "EXECUTE PROCEDURE", [(8,)], [23])
        self.sql(client, "RECREATE PROCEDURE P4_PROC RETURNS(V INTEGER) AS BEGIN V=9; SUSPEND; END", "RECREATE PROCEDURE")
        self.sql(client, "SELECT V FROM P4_PROC", "SELECT 1", [(9,)], [23])
        self.sql(client, "RECREATE TABLE P4_RECREATED(ID INTEGER)", "RECREATE TABLE")
        for sql, tag in [("DROP TABLE P4_RECREATED", "DROP TABLE"), ("DROP PROCEDURE P4_PROC", "DROP PROCEDURE"),
                         ("DROP EXCEPTION P4_EXCEPTION", "DROP EXCEPTION"), ("DROP VIEW P4_VIEW", "DROP VIEW"),
                         ("DROP INDEX P4_INDEX", "DROP INDEX"), ("DROP TABLE P4_DDL", "DROP TABLE"),
                         ("DROP DOMAIN P4_DOMAIN", "DROP DOMAIN"), ("DROP SEQUENCE P4_SEQUENCE", "DROP SEQUENCE")]:
            self.sql(client, sql, tag)
        client.close()
        self.no_attachments()

    def blocks(self):
        client = SimpleClient(self).login()
        self.sql(client, "EXECUTE BLOCK AS BEGIN INSERT INTO P4_EFFECTS VALUES(701); END", "EXECUTE BLOCK")
        self.sql(client, "EXECUTE BLOCK RETURNS(V INTEGER) AS BEGIN INSERT INTO P4_EFFECTS VALUES(702); "
                 "V=11; SUSPEND; V=12; SUSPEND; END", "EXECUTE BLOCK", [(11,), (12,)], [23])
        require(self.scalar_native("SELECT COUNT(*) AS OBSERVED FROM P4_EFFECTS WHERE ID IN (701,702)") == 2,
                "EXECUTE BLOCK changes were not committed")
        client.close()
        self.no_attachments()

    def errors_and_preflight(self):
        client = SimpleClient(self).login()
        for sql, state in [("INSERT INTO P4_ROWS(ID,V) VALUES(1,1)", "23505"),
                           ("INSERT INTO P4_ROWS(ID,V) VALUES(NULL,1)", "23000"),
                           ("UPDATE P4_ROWS SET V=-1 WHERE ID=1", "23514"),
                           ("BEGIN", "42601"), ("SELECT $1 FROM RDB$DATABASE", "42601"),
                           ("SET TRANSACTION READ COMMITTED", "0A000"), ("COMMIT", "0A000"),
                           ("ROLLBACK", "0A000"), ("SAVEPOINT P4_NOT_SUPPORTED", "0A000")]:
            result = client.query_error(sql.encode(), state)
            require(result["command"] is None, "failed SQL produced CommandComplete")
            self.no_transactions()
            client.query_integer()
        require(self.scalar_native("SELECT V AS OBSERVED FROM P4_ROWS WHERE ID=1") == 111, "failed UPDATE was committed")
        guarded = [
            "INSERT INTO P4_ROWS(ID,V) VALUES(90,GEN_ID(P4_GUARD,1)) RETURNING CAST(V AS DOUBLE PRECISION)",
            "INSERT INTO P4_ROWS(ID,V) SELECT 91,GEN_ID(P4_GUARD,1) FROM RDB$DATABASE RETURNING CAST('x' AS BLOB SUB_TYPE TEXT)",
            "UPDATE P4_ROWS SET V=GEN_ID(P4_GUARD,1) WHERE ID=1 RETURNING CAST(V AS NUMERIC(38,0))",
            "SELECT V FROM P4_UNSUPPORTED", "EXECUTE PROCEDURE P4_UNSUPPORTED",
            "INSERT INTO P4_ROWS(ID,V) VALUES(92,?)",
        ]
        for sql in guarded:
            result = client.query_error(sql.encode(), "0A000")
            require(result["columns"] is None and not result["rows"], "unsupported result started execution")
            require(self.scalar_native("SELECT GEN_ID(P4_GUARD,0) AS OBSERVED FROM RDB$DATABASE") == 0,
                    "unsupported metadata allowed nontransactional effects")
            require(self.scalar_native("SELECT COUNT(*) AS OBSERVED FROM P4_EFFECTS WHERE ID=703") == 0,
                    "unsupported procedure changed data")
            self.no_transactions()
            client.query_integer()
        failed = client.query_error(b"SELECT CAST(1/V AS INTEGER) FROM P4_FETCH_ERROR", "22012")
        require(failed["rows"] == [["1"]] and failed["command"] is None, "fetch error did not interrupt streamed rows")
        require(self.scalar_native("SELECT COUNT(*) AS OBSERVED FROM P4_EFFECTS WHERE ID=704") == 0,
                "fetch error failed to roll back prior procedure changes")
        for sql in ("INSERT INTO P4_ROWS(ID,V) VALUES(93,GEN_ID(P4_GUARD,1)); SELECT 42 FROM RDB$DATABASE",
                    "CREATE TABLE P4_BATCH_BAD(ID INTEGER); SELECT 42 FROM RDB$DATABASE"):
            client.query_error(sql.encode(), "42601")
        require(self.scalar_native("SELECT GEN_ID(P4_GUARD,0) AS OBSERVED FROM RDB$DATABASE") == 0,
                "SQL batch was partially executed")
        require(self.scalar_native("SELECT COUNT(*) AS OBSERVED FROM RDB$RELATIONS WHERE RDB$RELATION_NAME='P4_BATCH_BAD'") == 0,
                "DDL batch was partially committed")
        client.query_integer()
        client.close()
        self.no_attachments()

    def commit_failure(self):
        client = SimpleClient(self).login()
        try:
            client.send(packet(b"Q", b"SELECT V FROM P4_COMMIT_ERROR\0"))
            kind, description = client.message()
            require(kind == b"T" and description == struct.pack("!H", 1) + b"V\0" +
                    struct.pack("!IhIhih", 0, 0, 23, 4, -1, 0),
                    "commit failure fixture did not produce integer RowDescription")
            require(client.message() == (b"D", struct.pack("!Hi", 1, 3) + b"705"),
                    "commit failure happened before the expected result row")
            kind, payload = client.message()
            require(kind == b"E", "commit failure produced completion or ReadyForQuery")
            diagnostic = error_fields(payload)
            require(diagnostic["S"] == "FATAL" and diagnostic["V"] == "FATAL" and
                    diagnostic["C"] == "XX000", "commit failure did not terminate with its engine diagnostic")
            client.connection.settimeout(5)
            try:
                require(client.connection.recv(1) == b"", "commit failure sent data after FATAL ErrorResponse")
            except ConnectionResetError:
                pass
        finally:
            client.close(terminate=False)
        self.no_attachments()
        require(self.scalar_native("SELECT COUNT(*) AS OBSERVED FROM P4_EFFECTS WHERE ID=705") == 0,
                "failed commit did not roll back the procedure change")
        healthy = SimpleClient(self).login()
        healthy.query_integer()
        self.no_transactions()
        healthy.close()
        self.no_attachments()

    def empty_queries(self):
        client = SimpleClient(self).login()
        for sql in (b"", b" \t\r\n", b"-- only comment", b"/* closed comment */", b" /* first */ /* second */ -- tail\n"):
            result = client.query(sql)
            require(result["empty"], f"token-free SQL {sql!r} did not receive EmptyQueryResponse: {result!r}")
            self.no_transactions()
            client.query_integer()
        for sql in (b";", b"\v", b"SELECT", b"'unterminated", b"/* unterminated", b'"unterminated', b"/* closed */ SELECT",
                    b" /* outer /* nested */ end */ -- tail\n"):
            result = client.query_error(sql, "42601")
            require(not result["empty"], "malformed SQL was mistaken for EMPTY")
            client.query_integer()
        client.send(packet(b"Q", b"/*empty*/\0") + packet(b"Q", b'SELECT CAST(42 AS INTEGER) FROM RDB$DATABASE\0'))
        require(client.query_response()["empty"], "coalesced EMPTY frame was lost")
        require(client.query_response()["rows"] == [["42"]], "query after coalesced EMPTY was lost")
        client.close()
        self.no_attachments()

    def lexical_boundary(self):
        client = SimpleClient(self).login()
        self.sql(client, '(SELECT CAST(42 AS INTEGER) FROM RDB$DATABASE)', 'SELECT 1', [(42,)], [23])
        self.sql(client, '-- UPDATE OR INSERT;\n /* ALTER SESSION RESET */ sElEcT '
                 'CAST(42 AS INTEGER) AS "UPDATE" FROM RDB$DATABASE', 'SELECT 1', [(42,)], [23])
        self.sql(client, 'wItH "UPDATE"("RETURNING") AS (SELECT CAST(43 AS INTEGER) FROM RDB$DATABASE) '
                 'SELECT "RETURNING", CAST(\'COMMIT; BEGIN $1 RETURNING\' AS VARCHAR(40)) AS "CREATE TABLE" '
                 'FROM "UPDATE"', 'SELECT 1', [(43, 'COMMIT; BEGIN $1 RETURNING')], [23, 1043])
        self.sql(client, '/* CREATE VIEW */ cReAtE GLOBAL TEMPORARY TABLE P4_LEXICAL '
                 '("RETURNING" INTEGER) ON COMMIT PRESERVE ROWS', 'CREATE TABLE')
        self.sql(client, 'CREATE UNIQUE DESCENDING INDEX P4_LEXICAL_INDEX ON P4_LEXICAL("RETURNING")', 'CREATE INDEX')
        self.sql(client, '/* SELECT */ InSeRt INTO P4_LEXICAL("RETURNING") VALUES(44) '
                 'rEtUrNiNg "RETURNING"', 'INSERT 0 1', [(44,)], [23])
        self.sql(client, 'UpDaTe P4_LEXICAL SET "RETURNING"=45 RETURNING "RETURNING"',
                 'UPDATE 1', [(45,)], [23])
        self.sql(client, 'DeLeTe FROM P4_LEXICAL RETURNING "RETURNING"', 'DELETE 1', [(45,)], [23])
        self.sql(client, 'DROP INDEX P4_LEXICAL_INDEX', 'DROP INDEX')
        self.sql(client, 'DROP TABLE P4_LEXICAL', 'DROP TABLE')
        self.sql(client, 'CREATE PACKAGE BODY AS BEGIN END', 'CREATE PACKAGE')
        self.sql(client, 'DROP PACKAGE BODY', 'DROP PACKAGE')
        self.sql(client, 'CREATE PACKAGE P4_LEXICAL_PACKAGE AS BEGIN END', 'CREATE PACKAGE')
        self.sql(client, 'CREATE PACKAGE BODY P4_LEXICAL_PACKAGE AS BEGIN END', 'CREATE PACKAGE BODY')
        self.sql(client, 'DROP PACKAGE BODY P4_LEXICAL_PACKAGE', 'DROP PACKAGE BODY')
        self.sql(client, 'DROP PACKAGE P4_LEXICAL_PACKAGE', 'DROP PACKAGE')
        self.sql(client, "SELECT RDB$SET_CONTEXT('USER_SESSION','P4_LEXICAL_GUARD','kept') FROM RDB$DATABASE",
                 'SELECT 1', [(0,)], [23])
        for sql in ("/* SELECT */ ALTER SESSION RESET", "SET ROLE PGWIRE_OBSERVER", "SET TIME ZONE 'UTC'",
                    "USING IN AUTONOMOUS TRANSACTION DO INSERT INTO P4_EFFECTS VALUES (GEN_ID(P4_GUARD,1))"):
            result = client.query(sql.encode())
            require(result['error'] is not None and result['error']['C'] == '0A000',
                    f"unsupported SQL {sql!r} did not return 0A000: {result!r}")
            self.no_transactions()
            self.sql(client, "SELECT CAST(RDB$GET_CONTEXT('USER_SESSION','P4_LEXICAL_GUARD') AS VARCHAR(4)) "
                     "FROM RDB$DATABASE", 'SELECT 1', [('kept',)], [1043])
        for sql, state in (("ALTER SESSION RESET EXTRA_TOKEN", '42601'), ("COMMIT EXTRA_TOKEN", '42601'),
                           ("USING DO", '42601'),
                           # Native USING requires parameters, local declarations or an autonomous clause.
                           ("USING DO INSERT INTO P4_EFFECTS VALUES(GEN_ID(P4_GUARD,1))", '42000'),
                           ("USING DO INSERT INTO P4_EFFECTS VALUES(GEN_ID(P4_GUARD,1)); SELECT 42 FROM RDB$DATABASE", '42601'),
                           ("INSERT INTO P4_EFFECTS VALUES(GEN_ID(P4_GUARD,1)); /* COMMIT */ SELECT 42 FROM RDB$DATABASE", '42601')):
            result = client.query(sql.encode())
            require(result['error'] is not None and result['error']['C'] == state,
                    f"invalid SQL {sql!r} did not return {state}: {result!r}")
            self.no_transactions()
            client.query_integer()
        require(self.scalar_native('SELECT GEN_ID(P4_GUARD,0) AS OBSERVED FROM RDB$DATABASE') == 0,
                'classification executed an unsupported wrapper or part of an SQL batch')
        client.close()
        self.no_attachments()

    def native_autoterm(self):
        env = self.env.copy()
        env.update(ISC_USER="WIRE_HOST_TEST", ISC_PASSWORD=self.password)
        sql = """SET LIST ON;
-- native comment-only input
/* native block comment */
EXECUTE BLOCK RETURNS (AUTOTERM_ANSWER INTEGER) AS
BEGIN
  AUTOTERM_ANSWER = 6 * 7;
  SUSPEND;
END;
SELECT CAST(49 AS INTEGER) AS NATIVE_FOLLOWUP FROM RDB$DATABASE;
ROLLBACK;
QUIT;
"""
        result = subprocess.run([str(self.runtime / "bin/isql"), "-quiet", "-bail", "-autoterm",
                                 f"inet://127.0.0.1:{self.native_port}/wire_host"], input=sql,
                                env=env, text=True, capture_output=True, timeout=self.timeout)
        output = result.stdout + result.stderr
        (self.work / "native-autoterm.log").write_text(self.redact(output))
        require(result.returncode == 0 and "Statement failed" not in output, "native isql AUTOTERM failed")
        require(field(output, "AUTOTERM_ANSWER") == "42" and field(output, "NATIVE_FOLLOWUP") == "49",
                "native AUTOTERM continuation changed")

    def build_c_driver(self):
        source = Path(__file__).with_name("pgwire_libpq_simple_query.c")
        self.c_driver = self.work / "pgwire_libpq_simple_query"
        result = subprocess.run([self.args.cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
            "-I", str(self.args.libpq_include_dir), str(source), str(self.args.libpq_library), "-o", str(self.c_driver)],
            capture_output=True, text=True, timeout=self.timeout)
        (self.work / "libpq-compile.log").write_text(result.stdout + result.stderr)
        require(result.returncode == 0, "P4 C libpq compilation failed")
        self.summary["c_build"] = {"headers": str(self.args.libpq_include_dir), "library": str(self.args.libpq_library),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "libpq_fe_h_sha256": hashlib.sha256((self.args.libpq_include_dir / "libpq-fe.h").read_bytes()).hexdigest()}

    def psycopg2(self):
        result = subprocess.run([str(self.args.driver_python), "-B", str(Path(__file__).resolve()), "--psycopg2-child"],
            input=json.dumps({"port": self.pg_port, "password": self.password}), env=self.driver_environment(),
            cwd=self.temp, capture_output=True, text=True, timeout=self.timeout)
        (self.work / "psycopg2.log").write_text(self.redact(result.stdout + result.stderr))
        require(result.returncode == 0, "P4 psycopg2 suite failed; see psycopg2.log")
        self.summary["psycopg2"] = json.loads(result.stdout)

    def run_simple_queries(self):
        native, configured, port = reserve_ports(3)
        self.start("simple", native, configured, [{"alias": "PgWire", "port": port}])
        ready = SimpleClient(self).login()
        ready.close()
        self.no_attachments()
        for name, action in [("scalar_types_exact_numeric_and_utf8_padding", self.scalar_types),
                             ("dml_counts_returning_merge_upsert_native_visibility", self.dml),
                             ("ddl_operation_object_tags_and_native_visibility", self.ddl),
                             ("execute_block_with_and_without_output", self.blocks),
                             ("errors_preflight_guards_rollback_and_batch_boundary", self.errors_and_preflight),
                             ("real_commit_failure_fatal_close_and_native_rollback", self.commit_failure),
                             ("engine_empty_vs_malformed_and_recovery", self.empty_queries),
                             ("limited_sql_classification_preserves_engine_validation", self.lexical_boundary),
                             ("native_isql_autoterm_continuation", self.native_autoterm),
                             ("direct_c_libpq_simple_query", self.c_libpq),
                             ("psycopg2_simple_query_autocommit", self.psycopg2)]:
            self.check(name, action)
        self.no_attachments()
        self.native_probe("native_after_simple_queries")
        self.stop()


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
    if not 0 < args.timeout <= 300 or work.exists() or work.is_symlink() or source in work.resolve().parents:
        parser.error("use timeout 0..300 and a new work directory outside the build")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", str(work)):
        parser.error("work directory must use safe ASCII path characters")
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
    harness = SimpleHarness(source, work.resolve(), args.timeout, args)
    try:
        harness.prepare()
        harness.build_c_driver()
        harness.run_simple_queries()
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
