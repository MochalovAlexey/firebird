-- Captured 1C temporary-table service functions.
-- fasttruncate is deliberately transaction-unsafe, like Postgres Pro fasttrun.
-- Native INTEGER 0 result is the current profile's explicit void limitation.
SET BAIL ON;
SET AUTODDL OFF;
SET TERM ^;

CREATE FUNCTION "public"."fasttruncate"("relation_name" VARCHAR(1024) CHARACTER SET SYSTEM.UTF8)
RETURNS INTEGER
SQL SECURITY INVOKER
AS
BEGIN
    RETURN RDB$PG_FASTTRUNCATE("relation_name");
END^

CREATE FUNCTION "public"."fasttrun_ext"("relation_name" VARCHAR(1024) CHARACTER SET SYSTEM.UTF8)
RETURNS INTEGER
SQL SECURITY INVOKER
AS
BEGIN
    RETURN RDB$PG_FASTTRUNCATE("relation_name");
END^

SET TERM ;^
GRANT EXECUTE ON FUNCTION "public"."fasttruncate" TO PUBLIC;
GRANT EXECUTE ON FUNCTION "public"."fasttrun_ext" TO PUBLIC;
-- Only advertise the tested compatibility level after both real wrappers exist.
-- This preserves the mchar projection; no PostgreSQL extension loader is implied.
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
                   WHERE RDB$SCHEMA_NAME='public' AND RDB$FUNCTION_NAME='fasttruncate')
       AND EXISTS (SELECT 1 FROM SYSTEM.RDB$FUNCTIONS
                   WHERE RDB$SCHEMA_NAME='public' AND RDB$FUNCTION_NAME='fasttrun_ext');
GRANT SELECT ON "pg_catalog"."pg_available_extensions" TO PUBLIC;

COMMIT;
