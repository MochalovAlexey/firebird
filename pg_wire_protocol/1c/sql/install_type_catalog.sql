-- Installed APP-16 parser aliases with the actual PgWire codec OIDs.
-- This is a bounded type catalog, not an enumeration of native user domains.
SET BAIL ON;
SET AUTODDL OFF;

SET TERM ^;
EXECUTE BLOCK AS
BEGIN
    IF (NOT EXISTS (SELECT 1 FROM SYSTEM.RDB$CHARACTER_SETS WHERE RDB$CHARACTER_SET_ID = 70) OR
        NOT EXISTS (SELECT 1 FROM SYSTEM.RDB$CHARACTER_SETS WHERE RDB$CHARACTER_SET_ID = 71)) THEN
        EXCEPTION PGWIRE_COMPAT.DATABASE_PROFILE_ERROR;
END^
SET TERM ;^

CREATE VIEW "pg_catalog"."pg_type"("oid", "typname", "typnamespace") AS
    SELECT CAST(16449537 AS BIGINT), CAST('mchar' AS VARCHAR(63) CHARACTER SET UTF8), N.NAMESPACE_ID
      FROM PGWIRE_COMPAT.NAMESPACES N
     WHERE N.NATIVE_NAME = 'public' AND EXISTS
           (SELECT 1 FROM SYSTEM.RDB$CHARACTER_SETS
             WHERE RDB$CHARACTER_SET_ID = 70 AND RDB$CHARACTER_SET_NAME = 'PG_MCHAR')
    UNION ALL
    SELECT 16449538, 'mvarchar', N.NAMESPACE_ID FROM PGWIRE_COMPAT.NAMESPACES N
     WHERE N.NATIVE_NAME = 'public' AND EXISTS
           (SELECT 1 FROM SYSTEM.RDB$CHARACTER_SETS
             WHERE RDB$CHARACTER_SET_ID = 71 AND RDB$CHARACTER_SET_NAME = 'PG_MVARCHAR')
    UNION ALL
    SELECT 17, 'bytea', N.NAMESPACE_ID FROM PGWIRE_COMPAT.NAMESPACES N WHERE N.NATIVE_NAME = 'pg_catalog'
    UNION ALL
    SELECT 20, 'int8', N.NAMESPACE_ID FROM PGWIRE_COMPAT.NAMESPACES N WHERE N.NATIVE_NAME = 'pg_catalog'
    UNION ALL
    SELECT 4089, 'regnamespace', N.NAMESPACE_ID FROM PGWIRE_COMPAT.NAMESPACES N WHERE N.NATIVE_NAME = 'pg_catalog';

-- Actual schema-level native functions/aggregates, including installed PSQL
-- wrappers. Parser-only aliases have no invented callable function entries.
CREATE VIEW "pg_catalog"."pg_proc"("oid", "proname", "pronamespace") AS
    SELECT DISTINCT CAST(F.RDB$FUNCTION_ID AS BIGINT) + 1,
           CAST(TRIM(TRAILING FROM F.RDB$FUNCTION_NAME) AS VARCHAR(63) CHARACTER SET UTF8),
           N.NAMESPACE_ID
      FROM SYSTEM.RDB$FUNCTIONS F
      JOIN PGWIRE_COMPAT.NAMESPACES N ON N.NATIVE_NAME = TRIM(TRAILING FROM F.RDB$SCHEMA_NAME)
     WHERE F.RDB$PACKAGE_NAME IS NULL AND F.RDB$FUNCTION_ID IS NOT NULL;

-- Version is the tested mchar compatibility level, implemented by Firebird's
-- built-in APP-16 types/codecs. No PostgreSQL shared library is loaded here.
CREATE VIEW "pg_catalog"."pg_available_extensions"("name", "default_version", "installed_version", "comment") AS
    SELECT DISTINCT CAST('mchar' AS VARCHAR(63) CHARACTER SET UTF8),
           CAST('2.3' AS VARCHAR(32) CHARACTER SET UTF8), CAST('2.3' AS VARCHAR(32) CHARACTER SET UTF8),
           CAST('Firebird built-in mchar 2.3 compatibility profile; no PostgreSQL extension loader' AS VARCHAR(128) CHARACTER SET UTF8)
      FROM SYSTEM.RDB$DATABASE
     WHERE EXISTS (SELECT 1 FROM "pg_catalog"."pg_type" WHERE "oid" = 16449537)
       AND EXISTS (SELECT 1 FROM "pg_catalog"."pg_type" WHERE "oid" = 16449538);

SET TERM ^;
CREATE FUNCTION "pg_catalog"."pg_type_is_visible"("type_oid" BIGINT) RETURNS BOOLEAN
SQL SECURITY INVOKER
AS
DECLARE VISIBLE BOOLEAN;
BEGIN
    -- Parser::makePgType recognizes these aliases independently of search_path.
    -- A missing/unknown type OID returns NULL, not a fictional catalog row.
    SELECT TRUE FROM "pg_catalog"."pg_type" WHERE "oid" = :"type_oid" INTO VISIBLE;
    RETURN VISIBLE;
END^
SET TERM ;^
GRANT SELECT ON "pg_catalog"."pg_type" TO PUBLIC;
GRANT SELECT ON "pg_catalog"."pg_proc" TO PUBLIC;
GRANT SELECT ON "pg_catalog"."pg_available_extensions" TO PUBLIC;
GRANT EXECUTE ON FUNCTION "pg_catalog"."pg_type_is_visible" TO PUBLIC;
COMMIT;
