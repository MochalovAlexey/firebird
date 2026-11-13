-- NULL-aware equality using existing native comparison semantics.
-- Partial fulleq 2.0 profile: no hash API, PG type/operator registry or loader.
SET BAIL ON;
SET AUTODDL OFF;
SET TERM ^;

CREATE FUNCTION "public"."isfulleq_bool"("left_value" BOOLEAN, "right_value" BOOLEAN)
RETURNS BOOLEAN
DETERMINISTIC
SQL SECURITY INVOKER
AS
BEGIN
    RETURN "left_value" IS NOT DISTINCT FROM "right_value";
END^

CREATE FUNCTION "public"."isfulleq_int2"("left_value" SMALLINT, "right_value" SMALLINT)
RETURNS BOOLEAN
DETERMINISTIC
SQL SECURITY INVOKER
AS
BEGIN
    RETURN "left_value" IS NOT DISTINCT FROM "right_value";
END^

CREATE FUNCTION "public"."isfulleq_int4"("left_value" INTEGER, "right_value" INTEGER)
RETURNS BOOLEAN
DETERMINISTIC
SQL SECURITY INVOKER
AS
BEGIN
    RETURN "left_value" IS NOT DISTINCT FROM "right_value";
END^

CREATE FUNCTION "public"."isfulleq_int8"("left_value" BIGINT, "right_value" BIGINT)
RETURNS BOOLEAN
DETERMINISTIC
SQL SECURITY INVOKER
AS
BEGIN
    RETURN "left_value" IS NOT DISTINCT FROM "right_value";
END^

CREATE FUNCTION "public"."isfulleq_date"("left_value" DATE, "right_value" DATE)
RETURNS BOOLEAN
DETERMINISTIC
SQL SECURITY INVOKER
AS
BEGIN
    RETURN "left_value" IS NOT DISTINCT FROM "right_value";
END^

CREATE FUNCTION "public"."isfulleq_time"("left_value" TIME, "right_value" TIME)
RETURNS BOOLEAN
DETERMINISTIC
SQL SECURITY INVOKER
AS
BEGIN
    RETURN "left_value" IS NOT DISTINCT FROM "right_value";
END^

CREATE FUNCTION "public"."isfulleq_timestamp"("left_value" TIMESTAMP, "right_value" TIMESTAMP)
RETURNS BOOLEAN
DETERMINISTIC
SQL SECURITY INVOKER
AS
BEGIN
    RETURN "left_value" IS NOT DISTINCT FROM "right_value";
END^

CREATE FUNCTION "public"."isfulleq_timestamptz"("left_value" TIMESTAMP WITH TIME ZONE, "right_value" TIMESTAMP WITH TIME ZONE)
RETURNS BOOLEAN
DETERMINISTIC
SQL SECURITY INVOKER
AS
BEGIN
    RETURN "left_value" IS NOT DISTINCT FROM "right_value";
END^

CREATE FUNCTION "public"."isfulleq_bytea"("left_value" BLOB SUB_TYPE 10, "right_value" BLOB SUB_TYPE 10)
RETURNS BOOLEAN
DETERMINISTIC
SQL SECURITY INVOKER
AS
BEGIN
    RETURN "left_value" IS NOT DISTINCT FROM "right_value";
END^

-- Equality needs the value/collation, not the mchar typmod header. Native blob
-- conversion strips that header before PSQL's existing UTF8 validation.
CREATE FUNCTION "public"."isfulleq_mchar"("left_value" BLOB SUB_TYPE TEXT CHARACTER SET SYSTEM.PG_MCHAR, "right_value" BLOB SUB_TYPE TEXT CHARACTER SET SYSTEM.PG_MCHAR)
RETURNS BOOLEAN
DETERMINISTIC
SQL SECURITY INVOKER
AS
BEGIN
    RETURN "left_value" IS NOT DISTINCT FROM "right_value";
END^

CREATE FUNCTION "public"."isfulleq_mvarchar"("left_value" BLOB SUB_TYPE TEXT CHARACTER SET SYSTEM.PG_MVARCHAR, "right_value" BLOB SUB_TYPE TEXT CHARACTER SET SYSTEM.PG_MVARCHAR)
RETURNS BOOLEAN
DETERMINISTIC
SQL SECURITY INVOKER
AS
BEGIN
    RETURN "left_value" IS NOT DISTINCT FROM "right_value";
END^

SET TERM ;^
GRANT EXECUTE ON FUNCTION "public"."isfulleq_bool" TO PUBLIC;
GRANT EXECUTE ON FUNCTION "public"."isfulleq_int2" TO PUBLIC;
GRANT EXECUTE ON FUNCTION "public"."isfulleq_int4" TO PUBLIC;
GRANT EXECUTE ON FUNCTION "public"."isfulleq_int8" TO PUBLIC;
GRANT EXECUTE ON FUNCTION "public"."isfulleq_date" TO PUBLIC;
GRANT EXECUTE ON FUNCTION "public"."isfulleq_time" TO PUBLIC;
GRANT EXECUTE ON FUNCTION "public"."isfulleq_timestamp" TO PUBLIC;
GRANT EXECUTE ON FUNCTION "public"."isfulleq_timestamptz" TO PUBLIC;
GRANT EXECUTE ON FUNCTION "public"."isfulleq_bytea" TO PUBLIC;
GRANT EXECUTE ON FUNCTION "public"."isfulleq_mchar" TO PUBLIC;
GRANT EXECUTE ON FUNCTION "public"."isfulleq_mvarchar" TO PUBLIC;

-- Preserve the existing extension profiles and require every installed wrapper.
CREATE OR ALTER VIEW "pg_catalog"."pg_available_extensions"("name", "default_version", "installed_version", "comment") AS
    SELECT DISTINCT CAST('mchar' AS VARCHAR(63) CHARACTER SET UTF8),
           CAST('2.3' AS VARCHAR(32) CHARACTER SET UTF8), CAST('2.3' AS VARCHAR(32) CHARACTER SET UTF8),
           CAST('Firebird built-in mchar 2.3 compatibility profile; no PostgreSQL extension loader' AS VARCHAR(128) CHARACTER SET UTF8)
      FROM SYSTEM.RDB$DATABASE
     WHERE EXISTS (SELECT 1 FROM "pg_catalog"."pg_type" WHERE "oid" = 16449537)
       AND EXISTS (SELECT 1 FROM "pg_catalog"."pg_type" WHERE "oid" = 16449538)
    UNION ALL
    SELECT CAST('fasttrun' AS VARCHAR(63) CHARACTER SET UTF8),
           CAST('2.0' AS VARCHAR(32) CHARACTER SET UTF8), CAST('2.0' AS VARCHAR(32) CHARACTER SET UTF8),
           CAST('Firebird fasttrun 2.0 temporary-table profile; INTEGER result, guarded active cursors' AS VARCHAR(128) CHARACTER SET UTF8)
      FROM SYSTEM.RDB$DATABASE
     WHERE EXISTS (SELECT 1 FROM SYSTEM.RDB$FUNCTIONS
                   WHERE RDB$SCHEMA_NAME='public' AND RDB$FUNCTION_NAME='fasttruncate' AND RDB$PACKAGE_NAME IS NULL)
       AND EXISTS (SELECT 1 FROM SYSTEM.RDB$FUNCTIONS
                   WHERE RDB$SCHEMA_NAME='public' AND RDB$FUNCTION_NAME='fasttrun_ext' AND RDB$PACKAGE_NAME IS NULL)
    UNION ALL
    SELECT CAST('fulleq' AS VARCHAR(63) CHARACTER SET UTF8),
           CAST('2.0' AS VARCHAR(32) CHARACTER SET UTF8), CAST('2.0' AS VARCHAR(32) CHARACTER SET UTF8),
           CAST('Firebird partial fulleq 2.0 profile; typed NULL-aware equality, no hash API or extension loader' AS VARCHAR(128) CHARACTER SET UTF8)
      FROM SYSTEM.RDB$DATABASE
     WHERE 11 = (SELECT COUNT(*) FROM SYSTEM.RDB$FUNCTIONS WHERE RDB$SCHEMA_NAME='public'
                   AND RDB$PACKAGE_NAME IS NULL
                   AND RDB$FUNCTION_NAME IN (
                       'isfulleq_bool',
                       'isfulleq_int2',
                       'isfulleq_int4',
                       'isfulleq_int8',
                       'isfulleq_date',
                       'isfulleq_time',
                       'isfulleq_timestamp',
                       'isfulleq_timestamptz',
                       'isfulleq_bytea',
                       'isfulleq_mchar',
                       'isfulleq_mvarchar'));
GRANT SELECT ON "pg_catalog"."pg_available_extensions" TO PUBLIC;
COMMIT;
