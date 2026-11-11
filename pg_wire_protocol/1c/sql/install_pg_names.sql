-- APP-13: real lowercase schemas and read-only catalog entry points for PG4.
-- Requires intact APP-10/11/12 and compat.sql, with the namespace hook active.
-- Run once as owner/administrator without concurrent DDL. No repair/migration.
SET BAIL ON;
SET AUTODDL OFF;

CREATE SCHEMA "pg_catalog";
CREATE SCHEMA IF NOT EXISTS "public";

CREATE VIEW "pg_catalog"."pg_namespace"("oid", "nspname") AS
    SELECT DISTINCT OID, NSPNAME FROM PG_CATALOG.PG_NAMESPACE;

CREATE VIEW "pg_catalog"."pg_class"("oid", "relname", "relnamespace", "relkind") AS
    SELECT DISTINCT OID, RELNAME, RELNAMESPACE, RELKIND FROM PG_CATALOG.PG_CLASS;

SET TERM ^;
CREATE FUNCTION "pg_catalog"."pg_backend_pid" RETURNS INTEGER
SQL SECURITY INVOKER
AS
BEGIN
    RETURN PG_CATALOG.PG_BACKEND_PID();
END^
SET TERM ;^

GRANT USAGE ON SCHEMA "public" TO PUBLIC;
GRANT USAGE ON SCHEMA "pg_catalog" TO PUBLIC;
GRANT SELECT ON "pg_catalog"."pg_namespace" TO PUBLIC;
GRANT SELECT ON "pg_catalog"."pg_class" TO PUBLIC;
GRANT EXECUTE ON FUNCTION "pg_catalog"."pg_backend_pid" TO PUBLIC;

COMMIT;
