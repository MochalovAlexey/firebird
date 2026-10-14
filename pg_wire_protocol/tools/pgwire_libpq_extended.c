/* Real libpq Extended Query acceptance. Credentials arrive only through PG* env. */
#include <libpq-fe.h>
#include <pg_config.h>
#include <errno.h>
#include <poll.h>
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

static void error(PGconn* c, PGresult* r, const char* expected, PGTransactionStatusType transaction)
{
    checked(c, r, PGRES_FATAL_ERROR, "");
    const char* actual = PQresultErrorField(r, PG_DIAG_SQLSTATE);
    require(actual && !strcmp(actual, expected), "libpq SQLSTATE differs");
    require(PQtransactionStatus(c) == transaction, "Extended error transaction state differs");
    PQclear(r);
}

static unsigned async_commands = 0;
static unsigned async_results = 0;
static unsigned poll_calls = 0;

static void wait_socket(PGconn* c, short events)
{
    struct pollfd socket = {PQsocket(c), events, 0};
    require(socket.fd >= 0, "libpq has no socket");
    int result;
    do {
        ++poll_calls;
        result = poll(&socket, 1, -1);
    } while (result < 0 && errno == EINTR);
    require(result > 0 && !(socket.revents & POLLNVAL), "libpq socket poll failed");
    /* Read even when output remains queued: the peer may be waiting for us to
       drain its responses before it can read more. HUP/ERR go through libpq. */
    if (socket.revents & (POLLIN | POLLHUP | POLLERR))
        require(PQconsumeInput(c), "libpq consume after socket readiness failed");
}

/* https://www.postgresql.org/docs/14/libpq-async.html
   PQgetResult alone can block even with PQsetnonblocking. Drain to NULL after
   every command, including an error, before the next send (no pipeline). The
   existing Python subprocess watchdog owns the test deadline. */
static PGresult* collect_single(PGconn* c)
{
    require(PQisnonblocking(c), "async helper requires nonblocking libpq");
    int pending;
    while ((pending = PQflush(c)) == 1)
        wait_socket(c, POLLIN | POLLOUT);
    require(pending == 0, "libpq output flush failed");

    PGresult* first = NULL;
    unsigned count = 0;
    for (;;)
    {
        require(PQconsumeInput(c), "libpq consume input failed");
        while (PQisBusy(c))
            wait_socket(c, POLLIN);
        PGresult* next = PQgetResult(c);
        if (!next)
            break;
        ++count;
        ++async_results;
        if (!first)
            first = next;
        else
            PQclear(next);
    }
    ++async_commands;
    require(PQstatus(c) == CONNECTION_OK, "async result stream lost connection");
    if (count != 1)
    {
        PQclear(first);
        require(0, "single async command produced an unexpected result count");
    }
    return first;
}

static PGresult* send_params(PGconn* c, const char* sql, int count, const char* const* values)
{
    require(PQsendQueryParams(c, sql, count, NULL, values, NULL, NULL, 0),
        "PQsendQueryParams failed");
    return collect_single(c);
}

static void asynchronous(PGconn* c, const char* sql, const Oid* types, const char** values)
{
    const char* scalar = "SELECT CAST(? AS INTEGER) FROM RDB$DATABASE";
    PGresult* r;
    require(PQsetnonblocking(c, 1) == 0, "PQsetnonblocking failed");

    values[1] = "44";
    require(PQsendQueryPrepared(c, "c_values", 6, values, NULL, NULL, 0),
        "PQsendQueryPrepared failed");
    r = checked(c, collect_single(c), PGRES_TUPLES_OK, "SELECT 1");
    value(r, 1, "44", 23); PQclear(r);
    require(PQtransactionStatus(c) == PQTRANS_IDLE, "async implicit Sync did not commit");
    r = checked(c, send_params(c, scalar, 1, &values[1]), PGRES_TUPLES_OK, "SELECT 1");
    value(r, 0, "44", 23); PQclear(r);

    require(PQsendPrepare(c, "c_async_values", sql, 6, types), "PQsendPrepare failed");
    r = checked(c, collect_single(c), PGRES_COMMAND_OK, NULL); PQclear(r);
    require(PQsendDescribePrepared(c, "c_async_values"), "PQsendDescribePrepared failed");
    r = checked(c, collect_single(c), PGRES_COMMAND_OK, NULL);
    require(PQnparams(r) == 6 && PQnfields(r) == 6, "async prepared metadata counts differ");
    for (int i = 0; i < 6; ++i)
        require(PQparamtype(r, i) == types[i], "async prepared parameter OID differs");
    PQclear(r);
    for (int i = 0; i < 2; ++i)
    {
        values[1] = i ? "46" : "45";
        require(PQsendQueryPrepared(c, "c_async_values", 6, values, NULL, NULL, 0),
            "async named reuse failed");
        r = checked(c, collect_single(c), PGRES_TUPLES_OK, "SELECT 1");
        value(r, 1, values[1], 23); value(r, 5, NULL, 23); PQclear(r);
        require(PQtransactionStatus(c) == PQTRANS_IDLE, "async named reuse left a transaction");
    }
    require(PQsendPrepare(c, "", scalar, 0, NULL), "async unnamed prepare failed");
    r = checked(c, collect_single(c), PGRES_COMMAND_OK, NULL); PQclear(r);
    for (int i = 0; i < 2; ++i)
    {
        values[1] = i ? "48" : "47";
        require(PQsendQueryPrepared(c, "", 1, &values[1], NULL, NULL, 0),
            "async unnamed reuse failed");
        r = checked(c, collect_single(c), PGRES_TUPLES_OK, "SELECT 1");
        value(r, 0, values[1], 23); PQclear(r);
    }

    /* Parse, Bind and Execute errors are distinct server paths. Each libpq
       command supplies Sync; collect_single must reach NULL before reuse. */
    error(c, send_params(c, "SELECT $1 FROM RDB$DATABASE", 1, &values[1]), "42601", PQTRANS_IDLE);
    values[1] = "2147483648";
    require(PQsendQueryPrepared(c, "c_async_values", 6, values, NULL, NULL, 0),
        "async invalid Bind send failed");
    error(c, collect_single(c), "22003", PQTRANS_IDLE);
    const char* divisor = "0";
    error(c, send_params(c, "SELECT 1 / CAST(? AS INTEGER) FROM RDB$DATABASE", 1, &divisor),
        "22012", PQTRANS_IDLE);
    values[1] = "49";
    require(PQsendQueryPrepared(c, "c_async_values", 6, values, NULL, NULL, 0),
        "async reuse after errors failed");
    r = checked(c, collect_single(c), PGRES_TUPLES_OK, "SELECT 1");
    value(r, 1, "49", 23); PQclear(r);

    r = checked(c, send_params(c, "SET TRANSACTION READ WRITE READ COMMITTED NO WAIT", 0, NULL),
        PGRES_COMMAND_OK, "BEGIN"); PQclear(r);
    require(PQtransactionStatus(c) == PQTRANS_INTRANS, "async native transaction did not start");
    r = checked(c, send_params(c, "INSERT INTO P7_EFFECTS(ID) VALUES(901)", 0, NULL),
        PGRES_COMMAND_OK, "INSERT 0 1"); PQclear(r);
    require(PQtransactionStatus(c) == PQTRANS_INTRANS, "async Sync committed explicit transaction");
    error(c, send_params(c, "SELECT 1 / CAST(? AS INTEGER) FROM RDB$DATABASE", 1, &divisor),
        "22012", PQTRANS_INERROR);
    require(PQsendQueryPrepared(c, "c_async_values", 6, values, NULL, NULL, 0),
        "async failed transaction query send failed");
    error(c, collect_single(c), "25P02", PQTRANS_INERROR);
    r = checked(c, send_params(c, "ROLLBACK", 0, NULL), PGRES_COMMAND_OK, "ROLLBACK"); PQclear(r);
    require(PQtransactionStatus(c) == PQTRANS_IDLE, "async rollback did not leave failed transaction");
    r = checked(c, send_params(c,
        "SELECT CAST(COUNT(*) AS INTEGER) FROM P7_EFFECTS WHERE ID=901", 0, NULL),
        PGRES_TUPLES_OK, "SELECT 1");
    value(r, 0, "0", 23); PQclear(r);
    values[1] = "50";
    require(PQsendQueryPrepared(c, "c_async_values", 6, values, NULL, NULL, 0),
        "async named statement did not survive rollback");
    r = checked(c, collect_single(c), PGRES_TUPLES_OK, "SELECT 1");
    value(r, 1, "50", 23); PQclear(r);
    require(PQtransactionStatus(c) == PQTRANS_IDLE, "async final result left a transaction");
    require(async_commands == async_results, "async command/result accounting differs");
    require(PQsetnonblocking(c, 0) == 0, "restore blocking failed");
}

int main(void)
{
    PGconn* c = PQconnectdb("");
    require(c && PQstatus(c) == CONNECTION_OK, "C libpq connection failed");
    require(PQprotocolVersion(c) == 3, "libpq did not negotiate protocol 3");
    require(PQpipelineStatus(c) == PQ_PIPELINE_OFF, "P9 fixture must not use pipeline mode");
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

    /* The unnamed statement also survives its implicit transaction boundary
       until another unnamed Parse (including PQexecParams) replaces it. */
    r = checked(c, PQprepare(c, "", sql, 6, inferred), PGRES_COMMAND_OK, NULL); PQclear(r);
    for (int i = 0; i < 2; ++i)
    {
        values[1] = i ? "102" : "101";
        r = checked(c, PQexecPrepared(c, "", 6, values, NULL, NULL, 0), PGRES_TUPLES_OK, "SELECT 1");
        value(r, 1, values[1], 23); PQclear(r);
        require(PQtransactionStatus(c) == PQTRANS_IDLE, "sync unnamed reuse left a transaction");
    }
    r = checked(c, PQprepare(c, "", "SELECT CAST(? AS INTEGER) FROM RDB$DATABASE", 0, NULL),
        PGRES_COMMAND_OK, NULL); PQclear(r);
    r = checked(c, PQdescribePrepared(c, ""), PGRES_COMMAND_OK, NULL);
    require(PQnparams(r) == 1 && PQparamtype(r, 0) == 23 && PQnfields(r) == 1,
        "unnamed replacement retained old metadata");
    PQclear(r);
    r = checked(c, PQexecPrepared(c, "", 1, &values[1], NULL, NULL, 0), PGRES_TUPLES_OK, "SELECT 1");
    value(r, 0, "102", 23); PQclear(r);

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
    asynchronous(c, sql, explicit_types, values);
    error(c,PQexecParams(c,"SELECT $1 FROM RDB$DATABASE",1,NULL,&values[1],NULL,NULL,0),"42601",PQTRANS_IDLE);
    error(c,PQexecParams(c,"BEGIN",0,NULL,NULL,NULL,NULL,0),"42601",PQTRANS_IDLE);
    r = checked(c,PQexec(c,"SET TRANSACTION READ WRITE READ COMMITTED NO WAIT"),PGRES_COMMAND_OK,"BEGIN"); PQclear(r);
    r = checked(c,PQexecPrepared(c,"c_values",6,values,NULL,NULL,0),PGRES_TUPLES_OK,"SELECT 1"); PQclear(r);
    require(PQtransactionStatus(c) == PQTRANS_INTRANS, "Extended Sync ended explicit transaction");
    r = checked(c,PQexec(c,"COMMIT"),PGRES_COMMAND_OK,"COMMIT"); PQclear(r);
    r = checked(c,PQexecPrepared(c,"c_values",6,values,NULL,NULL,0),PGRES_TUPLES_OK,"SELECT 1"); PQclear(r);
    require(PQtransactionStatus(c) == PQTRANS_IDLE, "prepared reuse after commit failed");
    PQfinish(c);
    printf("{\"libpq_version\":%d,\"compile_header_version\":%d,\"protocol\":3,"
        "\"PQexecParams\":true,\"PQprepare_PQexecPrepared\":true,\"async_nonblocking_api\":true,"
        "\"nonblocking_poll_loop\":true,\"async_commands\":%u,\"async_results\":%u,\"poll_calls\":%u,"
        "\"named_unnamed_reuse\":true,\"async_error_recovery\":true,\"explicit_E_rollback\":true,"
        "\"native_question_mark_parameters\":true,\"postgres_dollar_parameters\":\"42601\"}\n",
        PQlibVersion(),PG_VERSION_NUM,async_commands,async_results,poll_calls);
    return 0;
}
