/* P8 real libpq text/binary parameters and results; secrets only in PG* env. */
#include <libpq-fe.h>
#include <pg_config.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void require(int ok, const char* message)
{
    if (!ok) { fprintf(stderr, "%s\n", message); exit(1); }
}

static PGresult* checked(PGconn* c, PGresult* r, ExecStatusType expected)
{
    require(r != NULL, "libpq returned no result");
    if (PQresultStatus(r) != expected)
    {
        const char* state = PQresultErrorField(r, PG_DIAG_SQLSTATE);
        fprintf(stderr, "libpq result=%d SQLSTATE=%s, expected=%d\n",
            PQresultStatus(r), state ? state : "none", expected);
        exit(1);
    }
    require(PQstatus(c) == CONNECTION_OK && PQtransactionStatus(c) == PQTRANS_IDLE,
            "codec operation did not restore ReadyForQuery I");
    return r;
}

static void binary_row(PGresult* r, const Oid* types, const unsigned char** bytes,
    const int* lengths, int nulls)
{
    require(PQnfields(r) == 8 && PQntuples(r) == 1, "codec row shape differs");
    for (int i = 0; i < 8; ++i)
    {
        require(PQftype(r,i) == types[i] && PQfformat(r,i) == 1 && PQfmod(r,i) == -1,
                "binary metadata differs");
        require(PQgetisnull(r,0,i) == nulls, "NULL differs from non-null value");
        if (!nulls)
            require(PQgetlength(r,0,i) == lengths[i] && !memcmp(PQgetvalue(r,0,i), bytes[i], lengths[i]),
                    "binary value differs from independent PostgreSQL fixture");
    }
}

int main(void)
{
    PGconn* c = PQconnectdb("");
    require(c && PQstatus(c) == CONNECTION_OK, "C libpq codec connection failed");
    require(PQparameterStatus(c,"integer_datetimes") &&
            !strcmp(PQparameterStatus(c,"integer_datetimes"),"on"), "integer_datetimes not advertised");
    const char* sql = "SELECT CAST(? AS FLOAT),CAST(? AS DOUBLE PRECISION),CAST(? AS DATE),"
        "CAST(? AS TIME),CAST(? AS TIMESTAMP),CAST(? AS INT128),CAST(? AS NUMERIC(38,2)),"
        "CAST(? AS NUMERIC(38,38)) FROM RDB$DATABASE";
    const Oid types[] = {700,701,1082,1083,1114,1700,1700,1700};
    const char* text[] = {"1.25","-2.5","1999-12-31","00:00:00.0001",
        "1999-12-31 23:59:59.9999","-170141183460469231731687303715884105728",
        "123456789012345678901234567890123456.78","0.00000000000000000000000000000000000001"};
    /* IEEE network floats, PostgreSQL epoch offsets, then base-10000 numeric. */
    static const unsigned char f4[] = {0x3f,0xa0,0,0};
    static const unsigned char f8[] = {0xc0,4,0,0,0,0,0,0};
    static const unsigned char date[] = {0xff,0xff,0xff,0xff};
    static const unsigned char time[] = {0,0,0,0,0,0,0,100};
    static const unsigned char stamp[] = {0xff,0xff,0xff,0xff,0xff,0xff,0xff,0x9c};
    const unsigned short min_words[] = {10,9,0x4000,0,170,1411,8346,469,2317,3168,7303,7158,8410,5728};
    const unsigned short decimal_words[] = {10,8,0,2,1234,5678,9012,3456,7890,1234,5678,9012,3456,7800};
    const unsigned short tiny_words[] = {1,0xfff6,0,38,100};
    unsigned char minimum[28], decimal[28], tiny[10];
    for (int i=0; i<14; ++i)
    {
        minimum[2*i] = min_words[i] >> 8; minimum[2*i+1] = min_words[i] & 255;
        decimal[2*i] = decimal_words[i] >> 8; decimal[2*i+1] = decimal_words[i] & 255;
    }
    for (int i=0; i<5; ++i) { tiny[2*i] = tiny_words[i] >> 8; tiny[2*i+1] = tiny_words[i] & 255; }
    const unsigned char* binary[] = {f4,f8,date,time,stamp,minimum,decimal,tiny};
    const int lengths[] = {4,8,4,8,8,28,28,10};
    int formats[] = {1,1,1,1,1,1,1,1};
    PGresult* r = checked(c,PQexecParams(c,sql,8,types,text,NULL,NULL,1),PGRES_TUPLES_OK);
    binary_row(r,types,binary,lengths,0); PQclear(r);
    r = checked(c,PQprepare(c,"wide_codecs",sql,8,NULL),PGRES_COMMAND_OK); PQclear(r);
    r = checked(c,PQdescribePrepared(c,"wide_codecs"),PGRES_COMMAND_OK);
    require(PQnparams(r) == 8, "parameter count differs");
    for (int i=0; i<8; ++i) require(PQparamtype(r,i) == types[i], "native input inference differs");
    PQclear(r);
    r = checked(c,PQexecPrepared(c,"wide_codecs",8,(const char* const*)binary,lengths,formats,1),PGRES_TUPLES_OK);
    binary_row(r,types,binary,lengths,0); PQclear(r);
    r = checked(c,PQexecPrepared(c,"wide_codecs",8,(const char* const*)binary,lengths,formats,0),PGRES_TUPLES_OK);
    for (int i=0; i<8; ++i)
    {
        require(PQfformat(r,i) == 0 && PQftype(r,i) == types[i], "text metadata differs");
        const char* actual = PQgetvalue(r,0,i);
        const size_t n = strlen(text[i]);
        require(!PQgetisnull(r,0,i) && !strncmp(actual,text[i],n), "exact text codec value differs");
        const char* tail = actual + n;
        if (i == 3 || i == 4) while (*tail == '0') ++tail;
        require(*tail == '\0', "text codec has unexpected trailing characters");
    }
    PQclear(r);
    const char* nulls[] = {NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL};
    r = checked(c,PQexecPrepared(c,"wide_codecs",8,nulls,lengths,formats,1),PGRES_TUPLES_OK);
    binary_row(r,types,binary,lengths,1); PQclear(r);
    const char* invalid = "00:00:00.000001";
    r = checked(c,PQexecParams(c,"SELECT CAST(? AS TIME) FROM RDB$DATABASE",1,NULL,&invalid,NULL,NULL,0),PGRES_FATAL_ERROR);
    require(PQresultErrorField(r,PG_DIAG_SQLSTATE) &&
            !strcmp(PQresultErrorField(r,PG_DIAG_SQLSTATE),"22008"), "inexact native time was not rejected");
    PQclear(r);
    r = checked(c,PQexecPrepared(c,"wide_codecs",8,text,NULL,NULL,1),PGRES_TUPLES_OK);
    binary_row(r,types,binary,lengths,0); PQclear(r);
    PQfinish(c);
    printf("{\"libpq_version\":%d,\"compile_header_version\":%d,\"parameters\":8,"
        "\"real_text_binary_and_null\":true,\"temporal_epoch_and_100us\":true,"
        "\"int128_exact_and_base10000\":true}\n",PQlibVersion(),PG_VERSION_NUM);
    return 0;
}
