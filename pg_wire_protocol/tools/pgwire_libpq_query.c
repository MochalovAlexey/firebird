/* Direct libpq PQexec acceptance; build with genuine PostgreSQL client headers.
 * Connection settings, including the private test password, come from libpq's
 * PG* environment variables. No credentials are printed or passed on argv.
 */
#include <libpq-fe.h>
#include <pg_config.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void require(int condition, const char* message)
{
    if (!condition)
    {
        fprintf(stderr, "%s\n", message);
        exit(1);
    }
}

static PGresult* tuples(PGconn* connection, const char* sql, int rows, int columns)
{
    PGresult* result = PQexec(connection, sql);
    require(result != NULL, "PQexec returned no result");
    require(PQresultStatus(result) == PGRES_TUPLES_OK, "PQexec SELECT failed");
    require(PQntuples(result) == rows && PQnfields(result) == columns, "result dimensions differ");
    require(PQtransactionStatus(connection) == PQTRANS_IDLE, "implicit transaction remained active");
    return result;
}

static void integer(PGconn* connection, int value)
{
    PGresult* result = tuples(connection,
        "SELECT CAST(6 * 7 AS INTEGER) AS \"answer\" FROM RDB$DATABASE", 1, 1);
    require(strcmp(PQfname(result, 0), "answer") == 0, "column alias differs");
    require(PQftype(result, 0) == 23 && PQfsize(result, 0) == 4, "INTEGER metadata differs");
    require(PQfformat(result, 0) == 0 && PQfmod(result, 0) == -1, "text format metadata differs");
    require(PQftable(result, 0) == 0 && PQftablecol(result, 0) == 0, "unknown column origin was invented");
    require(!PQgetisnull(result, 0, 0) && atoi(PQgetvalue(result, 0, 0)) == value, "computed value differs");
    require(strcmp(PQcmdStatus(result), "SELECT 1") == 0 && strcmp(PQcmdTuples(result), "1") == 0,
        "SELECT command tag differs");
    PQclear(result);
}

static void error(PGconn* connection, const char* sql, const char* state)
{
    PGresult* result = PQexec(connection, sql);
    require(result != NULL && PQresultStatus(result) == PGRES_FATAL_ERROR, "expected engine/capability error");
    const char* actual = PQresultErrorField(result, PG_DIAG_SQLSTATE);
    require(actual != NULL && strcmp(actual, state) == 0, "error SQLSTATE differs");
    require(PQtransactionStatus(connection) == PQTRANS_IDLE, "error left an active implicit transaction");
    PQclear(result);
    integer(connection, 42);
}

static void connection_error(PGconn* connection)
{
    const char* message = connection ? PQerrorMessage(connection) : "connection allocation failed";
    const char* password = getenv("PGPASSWORD");
    const size_t length = password ? strlen(password) : 0;
    fputs("libpq connection failed: ", stderr);
    while (*message)
    {
        if (length && strncmp(message, password, length) == 0)
        {
            fputs("[REDACTED]", stderr);
            message += length;
        }
        else
            fputc((unsigned char) *message++, stderr);
    }
    fputc('\n', stderr);
}

int main(void)
{
    PGconn* connection = PQconnectdb("");
    if (connection == NULL || PQstatus(connection) != CONNECTION_OK)
    {
        connection_error(connection);
        if (connection)
            PQfinish(connection);
        return 1;
    }
    require(PQprotocolVersion(connection) == 3, "wire protocol is not version 3");
    integer(connection, 42);
    PGresult* result = tuples(connection,
        "SELECT CAST(7 * 7 AS INTEGER) AS \"different\" FROM RDB$DATABASE", 1, 1);
    require(strcmp(PQgetvalue(result, 0, 0), "49") == 0, "second expression was not evaluated");
    PQclear(result);
    result = tuples(connection,
        "SELECT CAST(-32768 AS SMALLINT), CAST(32767 AS SMALLINT), "
        "CAST('-2147483648' AS INTEGER), CAST(2147483647 AS INTEGER), "
        "CAST('-9223372036854775808' AS BIGINT), CAST('9223372036854775807' AS BIGINT), "
        "CAST(NULL AS INTEGER) FROM RDB$DATABASE", 1, 7);
    const Oid oids[] = {21, 21, 23, 23, 20, 20, 23};
    const int sizes[] = {2, 2, 4, 4, 8, 8, 4};
    const char* values[] = {"-32768", "32767", "-2147483648", "2147483647",
                            "-9223372036854775808", "9223372036854775807"};
    for (int column = 0; column < 7; ++column)
    {
        require(PQftype(result, column) == oids[column] && PQfsize(result, column) == sizes[column],
            "integer boundary OID/size differs");
        require(PQfformat(result, column) == 0, "integer result is not text");
        if (column < 6)
            require(!PQgetisnull(result, 0, column) && strcmp(PQgetvalue(result, 0, column), values[column]) == 0,
                "integer boundary value differs");
        else
            require(PQgetisnull(result, 0, column) && PQgetlength(result, 0, column) == 0, "NULL encoding differs");
    }
    PQclear(result);
    result = tuples(connection, "SELECT ID FROM PGWIRE_ROWS WHERE ID < 0", 0, 1);
    require(strcmp(PQcmdStatus(result), "SELECT 0") == 0, "empty SELECT command tag differs");
    PQclear(result);
    result = tuples(connection, "SELECT ID, PAYLOAD FROM PGWIRE_ROWS ORDER BY ID", 3, 2);
    require(strcmp(PQgetvalue(result, 0, 1), "31") == 0 && strcmp(PQgetvalue(result, 2, 1), "-9") == 0,
        "table data was not fetched");
    PQclear(result);
    error(connection, "not SQL", "42601");
    error(connection, "BEGIN", "42601");
    error(connection, "SELECT $1 FROM RDB$DATABASE", "42601");
    error(connection, "SELECT CAST('x' AS VARCHAR(1)) FROM RDB$DATABASE", "0A000");
    PQfinish(connection);
    printf("{\"libpq_version\":%d,\"compile_header_version\":%d,\"protocol\":3,"
           "\"computed_answer\":42,\"answer_oid\":23,\"checks\":10}\n", PQlibVersion(), PG_VERSION_NUM);
    return 0;
}
