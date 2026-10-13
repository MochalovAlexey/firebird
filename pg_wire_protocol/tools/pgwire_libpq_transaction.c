/* Genuine libpq transaction-state acceptance. Credentials arrive through PG* env. */
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

static PGresult* run(PGconn* connection, const char* sql, ExecStatusType status,
    const char* tag, PGTransactionStatusType transaction)
{
    PGresult* result = PQexec(connection, sql);
    require(result != NULL, "PQexec allocated no result");
    if (PQresultStatus(result) != status || strcmp(PQcmdStatus(result), tag) != 0 ||
        PQtransactionStatus(connection) != transaction)
    {
        const char* state = PQresultErrorField(result, PG_DIAG_SQLSTATE);
        fprintf(stderr, "SQL %s: status=%d tag=%s transaction=%d SQLSTATE=%s; expected status=%d tag=%s transaction=%d\n",
            sql, PQresultStatus(result), PQcmdStatus(result), PQtransactionStatus(connection), state ? state : "none",
            status, tag, transaction);
        exit(1);
    }
    return result;
}

static void command(PGconn* connection, const char* sql, const char* tag, PGTransactionStatusType transaction)
{
    PGresult* result = run(connection, sql, PGRES_COMMAND_OK, tag, transaction);
    require(PQnfields(result) == 0 && PQntuples(result) == 0, "control unexpectedly returned data");
    PQclear(result);
}

static void error(PGconn* connection, const char* sql, const char* state, PGTransactionStatusType transaction)
{
    PGresult* result = run(connection, sql, PGRES_FATAL_ERROR, "", transaction);
    const char* actual = PQresultErrorField(result, PG_DIAG_SQLSTATE);
    if (!actual || strcmp(actual, state) != 0)
    {
        fprintf(stderr, "SQL %s: expected SQLSTATE=%s, actual=%s\n", sql, state, actual ? actual : "none");
        exit(1);
    }
    PQclear(result);
}

static void scalar(PGconn* connection, const char* sql, const char* expected, PGTransactionStatusType transaction)
{
    PGresult* result = run(connection, sql, PGRES_TUPLES_OK, "SELECT 1", transaction);
    require(PQntuples(result) == 1 && PQnfields(result) == 1 && !PQgetisnull(result, 0, 0), "scalar result shape differs");
    require(strcmp(PQgetvalue(result, 0, 0), expected) == 0, "scalar result value differs");
    PQclear(result);
}

int main(void)
{
    PGconn* connection = PQconnectdb("");
    require(connection && PQstatus(connection) == CONNECTION_OK, "C libpq connection failed");
    require(PQprotocolVersion(connection) == 3 && PQtransactionStatus(connection) == PQTRANS_IDLE,
        "initial protocol/transaction state differs");
    command(connection, "COMMIT", "COMMIT", PQTRANS_IDLE);
    command(connection, "ROLLBACK", "ROLLBACK", PQTRANS_IDLE);
    error(connection, "COMMIT RETAIN", "25000", PQTRANS_IDLE);
    error(connection, "BEGIN", "42601", PQTRANS_IDLE);
    command(connection, "SET TRANSACTION READ WRITE READ COMMITTED NO WAIT", "BEGIN", PQTRANS_INTRANS);
    command(connection, "INSERT INTO P5_ROWS VALUES(901,1)", "INSERT 0 1", PQTRANS_INTRANS);
    command(connection, "SAVEPOINT C_POINT", "SAVEPOINT", PQTRANS_INTRANS);
    command(connection, "INSERT INTO P5_ROWS VALUES(902,2)", "INSERT 0 1", PQTRANS_INTRANS);
    error(connection, "INSERT INTO P5_ROWS VALUES(1,999)", "23505", PQTRANS_INERROR);
    error(connection, "SELECT CAST(42 AS INTEGER) FROM RDB$DATABASE", "25P02", PQTRANS_INERROR);
    error(connection, "SELECT * FROM C_MISSING_TABLE", "25P02", PQTRANS_INERROR);
    error(connection, "SELECT", "42601", PQTRANS_INERROR);
    PGresult* empty = run(connection, " /* no SQL */ ", PGRES_EMPTY_QUERY, "", PQTRANS_INERROR);
    PQclear(empty);
    command(connection, "ROLLBACK TO SAVEPOINT C_POINT", "ROLLBACK", PQTRANS_INTRANS);
    scalar(connection, "SELECT COUNT(*) FROM P5_ROWS WHERE ID=902", "0", PQTRANS_INTRANS);
    scalar(connection, "SELECT V FROM P5_ROWS WHERE ID=901", "1", PQTRANS_INTRANS);
    command(connection, "RELEASE SAVEPOINT C_POINT", "RELEASE", PQTRANS_INTRANS);
    command(connection, "COMMIT", "COMMIT", PQTRANS_IDLE);
    command(connection, "SET TRANSACTION", "BEGIN", PQTRANS_INTRANS);
    command(connection, "INSERT INTO P5_ROWS VALUES(903,3)", "INSERT 0 1", PQTRANS_INTRANS);
    error(connection, "SET TRANSACTION", "25001", PQTRANS_INERROR);
    command(connection, "COMMIT", "ROLLBACK", PQTRANS_IDLE);
    scalar(connection, "SELECT COUNT(*) FROM P5_ROWS WHERE ID=903", "0", PQTRANS_IDLE);
    command(connection, "SET TRANSACTION", "BEGIN", PQTRANS_INTRANS);
    command(connection, "COMMIT RETAIN SNAPSHOT", "COMMIT", PQTRANS_INTRANS);
    command(connection, "ROLLBACK RETAIN", "ROLLBACK", PQTRANS_INTRANS);
    command(connection, "ROLLBACK", "ROLLBACK", PQTRANS_IDLE);
    PQfinish(connection);
    printf("{\"libpq_version\":%d,\"compile_header_version\":%d,\"protocol\":3,"
           "\"PQtransactionStatus\":[\"IDLE\",\"INTRANS\",\"INERROR\"],"
           "\"savepoint_recovery\":true,\"failed_commit_rolled_back\":true}\n", PQlibVersion(), PG_VERSION_NUM);
    return 0;
}
