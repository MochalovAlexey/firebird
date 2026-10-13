/* Real libpq Extended Query acceptance. Credentials arrive only through PG* env. */
#include <libpq-fe.h>
#include <pg_config.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void require(int ok, const char* message)
{
    if (!ok) { fprintf(stderr, "%s\n", message); exit(1); }
}

static PGresult* checked(PGconn* c, PGresult* r, ExecStatusType status, const char* tag)
{
    require(r != NULL, "libpq returned no PGresult");
    if (PQresultStatus(r) != status || (tag && strcmp(PQcmdStatus(r), tag)))
    {
        const char* state = PQresultErrorField(r, PG_DIAG_SQLSTATE);
        fprintf(stderr, "libpq result status=%d tag=%s SQLSTATE=%s, expected status=%d tag=%s\n",
            PQresultStatus(r), PQcmdStatus(r), state ? state : "none", status, tag ? tag : "any");
        exit(1);
    }
    require(PQstatus(c) == CONNECTION_OK, "Extended Query closed connection");
    return r;
}

static void value(PGresult* r, int column, const char* expected, Oid oid)
{
    require(PQntuples(r) == 1 && PQftype(r, column) == oid && PQfformat(r, column) == 0,
        "libpq text result metadata differs");
    require(expected ? (!PQgetisnull(r, 0, column) && !strcmp(PQgetvalue(r, 0, column), expected)) :
        PQgetisnull(r, 0, column), "libpq text/NULL value differs");
}

static void error(PGconn* c, PGresult* r, const char* expected)
{
    checked(c, r, PGRES_FATAL_ERROR, "");
    const char* actual = PQresultErrorField(r, PG_DIAG_SQLSTATE);
    require(actual && !strcmp(actual, expected), "libpq SQLSTATE differs");
    require(PQtransactionStatus(c) == PQTRANS_IDLE, "Extended error did not finish implicit cycle");
    PQclear(r);
}

int main(void)
{
    PGconn* c = PQconnectdb("");
    require(c && PQstatus(c) == CONNECTION_OK, "C libpq connection failed");
    require(PQprotocolVersion(c) == 3, "libpq did not negotiate protocol 3");
    const char* sql = "SELECT CAST(? AS SMALLINT),CAST(? AS INTEGER),CAST(? AS BIGINT),"
        "CAST(? AS VARCHAR(20)),CAST(? AS BOOLEAN),CAST(? AS INTEGER) FROM RDB$DATABASE";
    const char* values[] = {"-32768", "2147483647", "-9223372036854775808", "Я x ", "true", NULL};
    const Oid inferred[] = {0,0,0,0,0,0};
    const Oid explicit_types[] = {21,23,20,1043,16,23};
    PGresult* r = checked(c, PQexecParams(c, "SELECT CAST(42 AS INTEGER) FROM RDB$DATABASE",
        0, NULL, NULL, NULL, NULL, 0), PGRES_TUPLES_OK, "SELECT 1");
    value(r, 0, "42", 23); PQclear(r);
    r = checked(c, PQexecParams(c, sql, 6, inferred, values, NULL, NULL, 0), PGRES_TUPLES_OK, "SELECT 1");
    value(r,0,values[0],21); value(r,1,values[1],23); value(r,2,values[2],20);
    value(r,3,values[3],1043); value(r,4,"t",16); value(r,5,NULL,23); PQclear(r);
    r = checked(c, PQprepare(c, "c_values", sql, 6, explicit_types), PGRES_COMMAND_OK, NULL); PQclear(r);
    r = checked(c, PQdescribePrepared(c, "c_values"), PGRES_COMMAND_OK, NULL);
    require(PQnparams(r) == 6 && PQnfields(r) == 6, "prepared metadata counts differ");
    for (int i=0; i<6; ++i) require(PQparamtype(r,i) == explicit_types[i], "inferred parameter OID differs");
    PQclear(r);
    values[1] = "-42";
    r = checked(c, PQexecPrepared(c,"c_values",6,values,NULL,NULL,0), PGRES_TUPLES_OK,"SELECT 1");
    value(r,1,"-42",23); PQclear(r);
    require(PQtransactionStatus(c) == PQTRANS_IDLE, "prepared execute did not commit at Sync");
    values[1] = "43";
    r = checked(c, PQexecPrepared(c,"c_values",6,values,NULL,NULL,0), PGRES_TUPLES_OK,"SELECT 1");
    value(r,1,"43",23); PQclear(r);

    const unsigned char short_bytes[] = {0x80,0};
    const unsigned char int_bytes[] = {0xff,0xff,0xff,0xd6};
    const unsigned char long_bytes[] = {0x80,0,0,0,0,0,0,0};
    const unsigned char true_bytes[] = {1};
    const char* binary[] = {(const char*)short_bytes,(const char*)int_bytes,(const char*)long_bytes,
        "Я x ",(const char*)true_bytes,NULL};
    const int lengths[] = {2,4,8,5,1,0};
    const int formats[] = {1,1,1,1,1,1};
    r = checked(c, PQexecPrepared(c,"c_values",6,binary,lengths,formats,1), PGRES_TUPLES_OK,"SELECT 1");
    for (int i=0; i<6; ++i)
    {
        require(PQfformat(r,i) == 1 && PQftype(r,i) == explicit_types[i], "binary result metadata differs");
        if (i == 5) require(PQgetisnull(r,0,i), "binary NULL differs");
        else require(PQgetlength(r,0,i) == lengths[i] && !memcmp(PQgetvalue(r,0,i),binary[i],lengths[i]),
            "binary result is not PostgreSQL network representation");
    }
    PQclear(r);
    require(PQsetnonblocking(c,1) == 0, "PQsetnonblocking failed");
    values[1] = "44";
    require(PQsendQueryPrepared(c,"c_values",6,values,NULL,NULL,0), "PQsendQueryPrepared failed");
    r = checked(c, PQgetResult(c), PGRES_TUPLES_OK,"SELECT 1");
    value(r,1,"44",23); PQclear(r);
    require(PQgetResult(c) == NULL, "async prepared result stream did not terminate");
    require(PQsendQueryParams(c,"SELECT CAST(? AS INTEGER) FROM RDB$DATABASE",1,NULL,&values[1],NULL,NULL,0),
        "PQsendQueryParams failed");
    r = checked(c, PQgetResult(c), PGRES_TUPLES_OK,"SELECT 1");
    value(r,0,"44",23); PQclear(r);
    require(PQgetResult(c) == NULL, "async parameter result stream did not terminate");
    require(PQsetnonblocking(c,0) == 0, "restore blocking failed");
    error(c,PQexecParams(c,"SELECT $1 FROM RDB$DATABASE",1,NULL,&values[1],NULL,NULL,0),"42601");
    error(c,PQexecParams(c,"BEGIN",0,NULL,NULL,NULL,NULL,0),"42601");
    r = checked(c,PQexec(c,"SET TRANSACTION READ WRITE READ COMMITTED NO WAIT"),PGRES_COMMAND_OK,"BEGIN"); PQclear(r);
    r = checked(c,PQexecPrepared(c,"c_values",6,values,NULL,NULL,0),PGRES_TUPLES_OK,"SELECT 1"); PQclear(r);
    require(PQtransactionStatus(c) == PQTRANS_INTRANS, "Extended Sync ended explicit transaction");
    r = checked(c,PQexec(c,"COMMIT"),PGRES_COMMAND_OK,"COMMIT"); PQclear(r);
    r = checked(c,PQexecPrepared(c,"c_values",6,values,NULL,NULL,0),PGRES_TUPLES_OK,"SELECT 1"); PQclear(r);
    require(PQtransactionStatus(c) == PQTRANS_IDLE, "prepared reuse after commit failed");
    PQfinish(c);
    printf("{\"libpq_version\":%d,\"compile_header_version\":%d,\"protocol\":3,"
        "\"PQexecParams\":true,\"PQprepare_PQexecPrepared\":true,\"async_nonblocking_api\":true,"
        "\"native_question_mark_parameters\":true,\"postgres_dollar_parameters\":\"42601\"}\n",
        PQlibVersion(),PG_VERSION_NUM);
    return 0;
}
