/* P4 acceptance using genuine libpq headers/runtime. Credentials use PG* env. */
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

static PGresult* query(PGconn* connection, const char* sql, ExecStatusType status,
    const char* tag, int rows, int columns)
{
    PGresult* result = PQexec(connection, sql);
    require(result != NULL, "PQexec allocated no result");
    if (PQresultStatus(result) != status || strcmp(PQcmdStatus(result), tag) != 0)
    {
        const char* state = PQresultErrorField(result, PG_DIAG_SQLSTATE);
        fprintf(stderr, "Unexpected result: status=%d tag=%s SQLSTATE=%s, expected status=%d tag=%s\n",
            PQresultStatus(result), PQcmdStatus(result), state ? state : "none", status, tag);
        exit(1);
    }
    require(PQntuples(result) == rows && PQnfields(result) == columns, "result dimensions differ");
    require(PQtransactionStatus(connection) == PQTRANS_IDLE, "implicit query retained a transaction");
    return result;
}

static void command(PGconn* connection, const char* sql, const char* tag)
{
    PGresult* result = query(connection, sql, PGRES_COMMAND_OK, tag, 0, 0);
    PQclear(result);
}

static void sql_error(PGconn* connection, const char* sql, const char* state)
{
    PGresult* result = PQexec(connection, sql);
    require(result && PQresultStatus(result) == PGRES_FATAL_ERROR, "expected SQL failure");
    const char* actual = PQresultErrorField(result, PG_DIAG_SQLSTATE);
    require(actual && strcmp(actual, state) == 0, "SQL error state differs");
    require(PQtransactionStatus(connection) == PQTRANS_IDLE, "failed implicit query retained a transaction");
    PQclear(result);
    result = query(connection, "SELECT CAST(42 AS INTEGER) FROM RDB$DATABASE", PGRES_TUPLES_OK, "SELECT 1", 1, 1);
    require(strcmp(PQgetvalue(result, 0, 0), "42") == 0, "connection did not recover after SQL error");
    PQclear(result);
}

int main(void)
{
    PGconn* connection = PQconnectdb("");
    require(connection && PQstatus(connection) == CONNECTION_OK, "C libpq startup failed");
    require(PQprotocolVersion(connection) == 3, "protocol version differs");
    PGresult* result = query(connection,
        "SELECT TRUE, FALSE, CAST(NULL AS BOOLEAN), CAST('Я' AS CHAR(4)), CAST('Я🙂 ' AS VARCHAR(10)), "
        "CAST('' AS VARCHAR(4)), CAST(NULL AS VARCHAR(4)), "
        "CAST('-92233720368547758.08' AS NUMERIC(18,2)), CAST(12 AS NUMERIC(9,0)), "
        "CAST(NULL AS NUMERIC(18,2)) FROM RDB$DATABASE", PGRES_TUPLES_OK, "SELECT 1", 1, 10);
    const Oid oids[] = {16, 16, 16, 1042, 1043, 1043, 1043, 1700, 1700, 1700};
    const char* values[] = {"t", "f", NULL, "Я   ", "Я🙂 ", "", NULL, "-92233720368547758.08", "12", NULL};
    for (int column = 0; column < 10; ++column)
    {
        require(PQftype(result, column) == oids[column], "scalar logical OID differs");
        require(PQfsize(result, column) == (column < 3 ? 1 : -1), "scalar type length differs");
        require(PQfformat(result, column) == 0 && PQfmod(result, column) == -1, "scalar format/modifier differs");
        require(PQftable(result, column) == 0 && PQftablecol(result, column) == 0, "column origin was invented");
        if (!values[column])
            require(PQgetisnull(result, 0, column) && PQgetlength(result, 0, column) == 0, "NULL encoding differs");
        else
        {
            require(!PQgetisnull(result, 0, column), "non-NULL scalar became NULL");
            require(PQgetlength(result, 0, column) == (int) strlen(values[column]), "UTF8 byte length differs");
            require(strcmp(PQgetvalue(result, 0, column), values[column]) == 0, "scalar text value differs");
        }
    }
    PQclear(result);

    command(connection, "CREATE TABLE P4_C_TABLE(ID INTEGER PRIMARY KEY, V NUMERIC(18,2), B BOOLEAN, T CHAR(4))", "CREATE TABLE");
    command(connection, "CREATE INDEX P4_C_INDEX ON P4_C_TABLE(V)", "CREATE INDEX");
    command(connection, "ALTER INDEX P4_C_INDEX INACTIVE", "ALTER INDEX");
    result = query(connection, "INSERT INTO P4_C_TABLE VALUES(1,-123.40,FALSE,'Я') RETURNING ID,V,B,T",
        PGRES_TUPLES_OK, "INSERT 0 1", 1, 4);
    require(strcmp(PQcmdTuples(result), "1") == 0, "libpq INSERT affected count differs");
    require(strcmp(PQgetvalue(result, 0, 1), "-123.40") == 0 && strcmp(PQgetvalue(result, 0, 3), "Я   ") == 0,
        "singleton RETURNING types differ");
    PQclear(result);
    result = query(connection, "INSERT INTO P4_C_TABLE(ID,V) SELECT 2,20 FROM RDB$DATABASE "
        "UNION ALL SELECT 3,30 FROM RDB$DATABASE RETURNING ID", PGRES_TUPLES_OK, "INSERT 0 2", 2, 1);
    require(strcmp(PQcmdTuples(result), "2") == 0, "INSERT SELECT affected count differs");
    PQclear(result);
    result = query(connection, "UPDATE P4_C_TABLE SET V=12.30 WHERE ID>1 RETURNING V",
        PGRES_TUPLES_OK, "UPDATE 2", 2, 1);
    require(strcmp(PQcmdTuples(result), "2") == 0 && strcmp(PQgetvalue(result, 1, 0), "12.30") == 0,
        "UPDATE RETURNING value/count differs");
    PQclear(result);
    result = query(connection, "UPDATE P4_C_TABLE SET V=0 WHERE ID<0 RETURNING ID",
        PGRES_TUPLES_OK, "UPDATE 0", 0, 1);
    require(strcmp(PQcmdTuples(result), "0") == 0, "zero-row UPDATE count differs");
    PQclear(result);
    result = query(connection, "DELETE FROM P4_C_TABLE WHERE ID>1 RETURNING ID", PGRES_TUPLES_OK, "DELETE 2", 2, 1);
    require(strcmp(PQcmdTuples(result), "2") == 0, "DELETE count differs");
    PQclear(result);
    result = query(connection, "MERGE INTO P4_C_TABLE T USING (SELECT 1 AS ID FROM RDB$DATABASE "
        "UNION ALL SELECT 4 FROM RDB$DATABASE) S ON T.ID=S.ID "
        "WHEN MATCHED THEN UPDATE SET T.V=1 WHEN NOT MATCHED THEN INSERT(ID,V) VALUES(S.ID,4) RETURNING T.ID",
        PGRES_TUPLES_OK, "MERGE 2", 2, 1);
    /* Older libpq versions need not parse MERGE/custom tags in PQcmdTuples. */
    PQclear(result);
    command(connection, "UPDATE OR INSERT INTO P4_C_TABLE(ID,V) VALUES(4,42) MATCHING(ID)", "UPDATE OR INSERT 1");
    sql_error(connection, "INSERT INTO P4_C_TABLE(ID) VALUES(1)", "23505");
    command(connection, "DROP INDEX P4_C_INDEX", "DROP INDEX");
    command(connection, "DROP TABLE P4_C_TABLE", "DROP TABLE");

    const char* empty[] = {"", " \t\r\n", "/* comment only */", "-- comment only"};
    for (unsigned i = 0; i < sizeof(empty) / sizeof(empty[0]); ++i)
    {
        result = query(connection, empty[i], PGRES_EMPTY_QUERY, "", 0, 0);
        PQclear(result);
    }
    sql_error(connection, "/* unfinished", "42601");
    sql_error(connection, "'unfinished", "42601");
    sql_error(connection, "BEGIN", "42601");
    sql_error(connection, "SET TRANSACTION", "0A000");
    PQfinish(connection);
    printf("{\"libpq_version\":%d,\"compile_header_version\":%d,\"protocol\":3,"
           "\"types\":[16,1042,1043,1700],\"dml_ddl_returning_empty\":true}\n", PQlibVersion(), PG_VERSION_NUM);
    return 0;
}
