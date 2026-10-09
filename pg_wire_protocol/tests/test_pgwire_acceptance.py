"""Original acceptance scaffold; migration to the plugin plan is pending.

Reference client: psycopg2-binary 2.9.11, libpq 17.6, ordinary synchronous
cursor, Simple Query protocol 3.0, UTF8, Firebird SQL dialect 3.  Expectations
predate the no-SQL-parsing wire plugin design: automatic BEGIN, transaction
contexts and RETURNING rejection need revision as specified in TEST_PLAN.md.
The original 56 collected cases are not the current implementation gate.

Only disposable PGW_T_<uuid> tables are created.  Use a dedicated test database
and accounts.  Missing configuration is an error, not a skipped test.  Runtime
invocations need an external wall-clock timeout as described in TEST_PLAN.md.
The native round trip checks database effects; process/transport architecture
and the absence of outbound database connections need separate verification.
"""

import os
import subprocess
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg2
from psycopg2 import extensions
import pytest


pytestmark = pytest.mark.integration


def _required_env(name):
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"Required environment variable {name} is missing or empty", pytrace=False)
    return value


def _connection_params(dsn_env="PGWIRE_TEST_DSN", password_env="PGPASSWORD", **overrides):
    try:
        params = extensions.parse_dsn(_required_env(dsn_env))
    except psycopg2.Error:
        pytest.fail(f"{dsn_env} is not a valid libpq connection string", pytrace=False)
    if params.get("host") not in ("127.0.0.1", "::1"):
        pytest.fail(f"{dsn_env} must explicitly use host=127.0.0.1 or host=::1", pytrace=False)
    if not params.get("dbname") or not params.get("user"):
        pytest.fail(f"{dsn_env} must explicitly name dbname and user", pytrace=False)
    if "password" in params:
        pytest.fail(f"Put the password in {password_env}, not in {dsn_env}", pytrace=False)

    # An inherited PGHOSTADDR must not change the explicitly selected loopback.
    params.update(
        hostaddr=params["host"],
        password=_required_env(password_env),
        options="",
        sslmode="disable",
        gssencmode="disable",
        target_session_attrs="any",
        client_encoding="UTF8",
        connect_timeout=5,
    )
    if extensions.libpq_version() >= 180000:
        params["max_protocol_version"] = "3.0"
    params.update(overrides)
    return params


@contextmanager
def _pg_connection(*, autocommit=False, dsn_env="PGWIRE_TEST_DSN",
                   password_env="PGPASSWORD", **overrides):
    conn = psycopg2.connect(**_connection_params(dsn_env, password_env, **overrides))
    try:
        conn.autocommit = autocommit
        yield conn
    finally:
        conn.close()


@pytest.fixture
def pg_connect():
    return _pg_connection


@pytest.fixture
def table(pg_connect):
    name = "PGW_T_" + uuid4().hex[:16].upper()
    # CREATE completes in its own committed transaction, before any DML test.
    with pg_connect(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE {name} ("
                "ID INTEGER NOT NULL PRIMARY KEY, "
                "V VARCHAR(200) CHARACTER SET UTF8, N NUMERIC(18, 2))"
            )
    try:
        yield name
    finally:
        # The identifier is generated here; never drop pre-existing user tables.
        with pg_connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE {name}")


def _rows(conn, statement, parameters=None):
    with conn.cursor() as cur:
        cur.execute(statement, parameters)
        return cur.fetchall()


def _assert_usable(conn):
    assert _rows(conn, 'SELECT CAST(42 AS INTEGER) AS "answer" FROM RDB$DATABASE') == [(42,)]


def _native_isql(script):
    executable = _required_env("FB_ISQL")
    native_dsn = _required_env("FB_NATIVE_DSN")
    try:
        address = urlsplit(native_dsn)
        valid_address = (
            address.scheme == "inet"
            and address.hostname in ("127.0.0.1", "::1")
            and address.port is not None
            and address.username is None
            and address.password is None
            and bool(address.path.strip("/"))
            and not address.query
            and not address.fragment
        )
    except ValueError:
        valid_address = False
    if not valid_address:
        pytest.fail(
            "FB_NATIVE_DSN must be inet://127.0.0.1:port/alias or "
            "inet://[::1]:port/alias, without credentials", pytrace=False
        )
    native_password = _required_env("FB_NATIVE_PASSWORD")
    env = os.environ.copy()
    env["ISC_USER"] = _required_env("FB_NATIVE_USER")
    env["ISC_PASSWORD"] = native_password
    result = subprocess.run(
        [executable, "-q", native_dsn],
        input="SET BAIL ON;\nSET LIST ON;\nSET SQL DIALECT 3;\n" + script + "\nQUIT;\n",
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
        check=False,
    )
    # Passwords are passed only via the child environment, never argv or SQL.
    diagnostic = result.stderr.replace(native_password, "<redacted>")
    assert result.returncode == 0, f"Firebird isql failed: {diagnostic}"
    return result.stdout


def test_connect_and_select_metadata(pg_connect):
    with pg_connect() as conn:
        assert conn.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE
        assert conn.get_parameter_status("client_encoding") == "UTF8"
        assert conn.get_parameter_status("standard_conforming_strings") == "on"
        assert (conn.get_parameter_status("DateStyle") or "").startswith("ISO")
        with conn.cursor() as cur:
            cur.execute('SELECT CAST(40 + 2 AS INTEGER) AS "answer" FROM RDB$DATABASE')
            assert cur.fetchall() == [(42,)]
            assert len(cur.description) == 1
            assert cur.description[0].name == "answer"
            assert cur.description[0].type_code == 23
            assert cur.description[0].internal_size == 4
            assert cur.rowcount == 1
            assert cur.statusmessage == "SELECT 1"
        assert conn.get_transaction_status() == extensions.TRANSACTION_STATUS_INTRANS


@pytest.mark.parametrize(
    "expression, expected, oid, python_type",
    [
        ("CAST(-32768 AS SMALLINT)", -32768, 21, int),
        ("CAST(32767 AS SMALLINT)", 32767, 21, int),
        ("CAST(-2147483648 AS INTEGER)", -2147483648, 23, int),
        ("CAST(2147483647 AS INTEGER)", 2147483647, 23, int),
        ("CAST(-9223372036854775808 AS BIGINT)", -9223372036854775808, 20, int),
        ("CAST(9223372036854775807 AS BIGINT)", 9223372036854775807, 20, int),
        ("CAST(TRUE AS BOOLEAN)", True, 16, bool),
        ("CAST(FALSE AS BOOLEAN)", False, 16, bool),
        ("CAST('xy' AS CHAR(4))", "xy  ", 1042, str),
        ("CAST('xy' AS VARCHAR(4))", "xy", 1043, str),
        ("CAST(123456789012345678 AS NUMERIC(18, 0))", Decimal("123456789012345678"), 1700, Decimal),
        ("CAST(1234567890123456.78 AS DECIMAL(18, 2))", Decimal("1234567890123456.78"), 1700, Decimal),
        ("CAST(-1234567890123456.78 AS NUMERIC(18, 2))", Decimal("-1234567890123456.78"), 1700, Decimal),
        ("CAST(-0.01 AS NUMERIC(18, 2))", Decimal("-0.01"), 1700, Decimal),
        ("CAST(0 AS DECIMAL(18, 2))", Decimal("0.00"), 1700, Decimal),
    ],
)
def test_supported_scalar_types(pg_connect, expression, expected, oid, python_type):
    with pg_connect(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'SELECT {expression} AS "value" FROM RDB$DATABASE')
            value, = cur.fetchone()
            assert value == expected
            assert type(value) is python_type
            assert cur.description[0].name == "value"
            assert cur.description[0].type_code == oid


@pytest.mark.parametrize(
    "sql_type, value, oid",
    [
        ("INTEGER", -17, 23),
        ("BOOLEAN", True, 16),
        ("BOOLEAN", False, 16),
        ("NUMERIC(18, 2)", Decimal("12.34"), 1700),
        ("VARCHAR(200)", None, 1043),
        ("VARCHAR(200)", "", 1043),
        ("VARCHAR(200)", "O'Reilly", 1043),
        ("VARCHAR(200)", r"C:\tmp\file", 1043),
        ("VARCHAR(200)", "Привет; мир", 1043),
        ("VARCHAR(200)", "first line\nsecond\tcolumn", 1043),
        ("VARCHAR(200)", "/* ; BEGIN */", 1043),
    ],
)
def test_client_side_parameters(pg_connect, sql_type, value, oid):
    with pg_connect(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'SELECT CAST(%s AS {sql_type}) AS "value" FROM RDB$DATABASE', (value,))
            assert cur.fetchall() == [(value,)]
            assert cur.description[0].type_code == oid


def test_empty_result_metadata(pg_connect):
    with pg_connect(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT CAST(1 AS INTEGER) AS "answer" FROM RDB$DATABASE WHERE 1 = 0')
            assert cur.fetchall() == []
            assert cur.description[0].name == "answer"
            assert cur.description[0].type_code == 23
            assert cur.rowcount == 0
            assert cur.statusmessage == "SELECT 0"


def test_crud_and_rowcounts(pg_connect, table):
    with pg_connect(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f"INSERT INTO {table} (ID, V, N) VALUES (%s, %s, %s)", (1, "first", Decimal("12.34")))
            assert cur.description is None
            assert cur.rowcount == 1
            assert cur.statusmessage == "INSERT 0 1"
            assert _rows(conn, f"SELECT ID, V, N FROM {table}") == [(1, "first", Decimal("12.34"))]
            cur.execute(f"UPDATE {table} SET V = %s WHERE ID = %s", ("second", 1))
            assert cur.rowcount == 1
            assert cur.statusmessage == "UPDATE 1"
            cur.execute(f"UPDATE {table} SET V = %s WHERE ID = %s", ("absent", 2))
            assert cur.rowcount == 0
            assert cur.statusmessage == "UPDATE 0"
            assert _rows(conn, f"SELECT V FROM {table} WHERE ID = 1") == [("second",)]
            cur.execute(f"DELETE FROM {table} WHERE ID = 1")
            assert cur.rowcount == 1
            assert cur.statusmessage == "DELETE 1"
            cur.execute(f"DELETE FROM {table} WHERE ID = 1")
            assert cur.rowcount == 0
            assert cur.statusmessage == "DELETE 0"
        assert conn.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE


def test_commit_visibility(pg_connect, table):
    with pg_connect() as writer, pg_connect(autocommit=True) as observer:
        with writer.cursor() as cur:
            cur.execute(f"INSERT INTO {table} (ID) VALUES (1)")
        assert writer.get_transaction_status() == extensions.TRANSACTION_STATUS_INTRANS
        assert _rows(observer, f"SELECT ID FROM {table}") == []
        writer.commit()
        assert writer.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE
        assert _rows(observer, f"SELECT ID FROM {table}") == [(1,)]


def test_rollback_visibility(pg_connect, table):
    with pg_connect() as writer, pg_connect(autocommit=True) as observer:
        with writer.cursor() as cur:
            cur.execute(f"INSERT INTO {table} (ID) VALUES (1)")
        assert _rows(writer, f"SELECT ID FROM {table}") == [(1,)]
        assert _rows(observer, f"SELECT ID FROM {table}") == []
        writer.rollback()
        assert writer.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE
        assert _rows(observer, f"SELECT ID FROM {table}") == []


@pytest.mark.parametrize("finish", ["rollback", "commit"])
def test_unique_violation_aborts_transaction(pg_connect, table, finish):
    with pg_connect(autocommit=True) as observer:
        with observer.cursor() as cur:
            cur.execute(f"INSERT INTO {table} (ID) VALUES (1)")
        with pg_connect() as writer:
            with writer.cursor() as cur:
                cur.execute(f"INSERT INTO {table} (ID) VALUES (2)")
                with pytest.raises(psycopg2.Error) as error:
                    cur.execute(f"INSERT INTO {table} (ID) VALUES (1)")
                assert error.value.pgcode == "23505"
                assert writer.get_transaction_status() == extensions.TRANSACTION_STATUS_INERROR
                with pytest.raises(psycopg2.Error) as aborted:
                    cur.execute('SELECT CAST(1 AS INTEGER) AS "value" FROM RDB$DATABASE')
                assert aborted.value.pgcode == "25P02"
            # COMMIT while failed must roll back all work, just like ROLLBACK.
            getattr(writer, finish)()
            assert writer.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE
            assert _rows(observer, f"SELECT ID FROM {table} ORDER BY ID") == [(1,)]
            _assert_usable(writer)


def test_disconnect_releases_uncommitted_key(pg_connect, table):
    with pg_connect() as writer:
        with writer.cursor() as cur:
            cur.execute(f"INSERT INTO {table} (ID) VALUES (7)")
    with pg_connect(autocommit=True) as next_writer:
        assert _rows(next_writer, f"SELECT ID FROM {table}") == []
        with next_writer.cursor() as cur:
            cur.execute(f"INSERT INTO {table} (ID) VALUES (7)")
            assert cur.rowcount == 1


@pytest.mark.parametrize("kind, sqlstate", [("syntax", "42601"), ("unknown_table", "42P01")])
def test_sql_error_recovery(pg_connect, kind, sqlstate):
    statement = "SELEC 1 FROM RDB$DATABASE"
    if kind == "unknown_table":
        statement = "SELECT ID FROM PGW_ABSENT_" + uuid4().hex[:16].upper()
    with pg_connect(autocommit=True) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.Error) as error:
                cur.execute(statement)
            assert error.value.pgcode == sqlstate
        assert conn.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE
        _assert_usable(conn)


def test_multiple_statements_rejected_atomically(pg_connect, table):
    with pg_connect(autocommit=True) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.Error) as error:
                cur.execute(f"INSERT INTO {table} (ID) VALUES (1); INSERT INTO {table} (ID) VALUES (2)")
            assert error.value.pgcode == "42601"
        assert conn.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE
        assert _rows(conn, f"SELECT ID FROM {table}") == []


@pytest.mark.parametrize("form", ["insert_values", "insert_select", "update", "delete"])
def test_returning_rejected_before_changes(pg_connect, table, form):
    statements = {
        "insert_values": f"INSERT INTO {table} (ID) VALUES (2) RETURNING ID",
        "insert_select": f"INSERT INTO {table} (ID) SELECT 2 FROM RDB$DATABASE RETURNING ID",
        "update": f"UPDATE {table} SET V = 'after' WHERE ID = 1 RETURNING ID",
        "delete": f"DELETE FROM {table} WHERE ID = 1 RETURNING ID",
    }
    with pg_connect(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f"INSERT INTO {table} (ID, V) VALUES (1, 'before')")
            with pytest.raises(psycopg2.Error) as error:
                cur.execute(statements[form])
            assert error.value.pgcode == "0A000"
        assert conn.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE
        assert _rows(conn, f"SELECT ID, V FROM {table} ORDER BY ID") == [(1, "before")]


@pytest.mark.parametrize(
    "expression",
    [
        "CAST('2026-10-06' AS DATE)",
        "CAST('12:34:56' AS TIME)",
        "CAST('2026-10-06 12:34:56' AS TIMESTAMP)",
        "CAST(1.25 AS FLOAT)",
        "CAST(1.25 AS DOUBLE PRECISION)",
        "CAST(1 AS INT128)",
        "CAST(1 AS NUMERIC(38, 0))",
    ],
)
def test_unsupported_output_types(pg_connect, expression):
    with pg_connect(autocommit=True) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.Error) as error:
                cur.execute(f'SELECT {expression} AS "value" FROM RDB$DATABASE')
            assert error.value.pgcode == "0A000"
        assert conn.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE
        _assert_usable(conn)


def test_python_date_adapter_remains_unsupported_sql(pg_connect):
    statement = 'SELECT CAST(%s AS VARCHAR(32)) AS "value" FROM RDB$DATABASE'
    parameters = (date(2026, 10, 6),)
    with pg_connect(autocommit=True) as conn:
        with conn.cursor() as cur:
            assert b"::date" in cur.mogrify(statement, parameters)
            # VARCHAR output isolates PostgreSQL cast syntax from DATE codecs.
            with pytest.raises(psycopg2.Error) as error:
                cur.execute(statement, parameters)
            assert error.value.pgcode == "42601"
        assert conn.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE
        _assert_usable(conn)


def test_nul_text_rejected_without_truncation(pg_connect):
    with pg_connect(autocommit=True) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.Error) as error:
                cur.execute(
                    'SELECT CAST(\'a\' || ASCII_CHAR(0) || \'b\' AS VARCHAR(3)) '
                    'AS "value" FROM RDB$DATABASE'
                )
            assert error.value.pgcode == "22021"
        assert conn.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE
        _assert_usable(conn)


@pytest.mark.parametrize("kind", ["wrong_password", "unknown_alias"])
def test_login_failures(pg_connect, kind):
    # Establish reachability with valid settings so a missing server cannot pass.
    with pg_connect(autocommit=True) as conn:
        _assert_usable(conn)
    overrides = {"password": "incorrect_" + uuid4().hex}
    if kind == "unknown_alias":
        overrides = {"dbname": "PGW_ABSENT_" + uuid4().hex}
    with pytest.raises(psycopg2.OperationalError):
        with pg_connect(**overrides):
            pytest.fail("Connection unexpectedly accepted invalid login settings")
    # libpq's connect failure normally has no PGresult: psycopg2 then exposes
    # pgcode=None.  Exact startup ErrorResponse C=28P01 / C=3D000 fields require
    # the wire tests in TEST_PLAN.md; do not infer them from translated text.
    with pg_connect(autocommit=True) as conn:
        _assert_usable(conn)


def test_repeated_connections(pg_connect):
    for _ in range(5):
        with pg_connect(autocommit=True) as conn:
            _assert_usable(conn)
            assert conn.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE


def test_connection_context_with_autocommit(pg_connect, table):
    with pg_connect(autocommit=True) as conn, pg_connect(autocommit=True) as observer:
        # Since psycopg2 2.9, this context starts a transaction even in autocommit.
        with conn:
            with conn.cursor() as cur:
                cur.execute(f"INSERT INTO {table} (ID) VALUES (1)")
            assert conn.get_transaction_status() == extensions.TRANSACTION_STATUS_INTRANS
            assert _rows(observer, f"SELECT ID FROM {table}") == []
        assert not conn.closed
        assert conn.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE
        assert _rows(observer, f"SELECT ID FROM {table}") == [(1,)]
        with pytest.raises(RuntimeError, match="rollback probe"):
            with conn:
                with conn.cursor() as cur:
                    cur.execute(f"INSERT INTO {table} (ID) VALUES (2)")
                raise RuntimeError("rollback probe")
        assert not conn.closed
        assert conn.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE
        assert _rows(observer, f"SELECT ID FROM {table} ORDER BY ID") == [(1,)]
        _assert_usable(conn)


@pytest.mark.native
def test_native_firebird_round_trip(pg_connect, table):
    # FB_NATIVE_USER needs access to this table: use the same effective user or
    # an explicitly provisioned equivalent.  SYSDBA is neither assumed nor used.
    pg_marker = "PG_TO_FB_" + uuid4().hex
    fb_marker = "FB_TO_PG_" + uuid4().hex
    with pg_connect(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f"INSERT INTO {table} (ID, V) VALUES (%s, %s)", (1, pg_marker))
        output = _native_isql(
            f'SELECT V AS "marker" FROM {table} WHERE ID = 1;\n'
            f"INSERT INTO {table} (ID, V) VALUES (2, '{fb_marker}');\n"
            "COMMIT;"
        )
        assert pg_marker in output
        assert _rows(conn, f"SELECT V FROM {table} WHERE ID = 2") == [(fb_marker,)]


@pytest.mark.privileges
def test_limited_user_cannot_read_private_table(pg_connect, table):
    # This separately provisioned account must target the same database and
    # have no SELECT privilege on tables created by the main test account.
    with pg_connect(autocommit=True, dsn_env="PGWIRE_LIMITED_DSN",
                    password_env="PGWIRE_LIMITED_PASSWORD") as limited:
        with limited.cursor() as cur:
            with pytest.raises(psycopg2.Error) as error:
                cur.execute(f"SELECT V FROM {table}")
            assert error.value.pgcode == "42501"
        assert limited.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE
        _assert_usable(limited)
