-- Read-only table names from the installed live relation/namespace catalogs.
-- Includes persistent tables and the current attachment's PG temporary tables.
-- Inherits the existing pg_class scope; views and native GTT/unmanaged LTT
-- are not represented as PostgreSQL tables. Run once as database owner.
SET BAIL ON;
SET AUTODDL OFF;

CREATE VIEW "pg_catalog"."pg_tables"("schemaname", "tablename") AS
    SELECT DISTINCT N."nspname", C."relname"
      FROM "pg_catalog"."pg_class" C
      JOIN "pg_catalog"."pg_namespace" N ON N."oid" = C."relnamespace"
     WHERE C."relkind" = 'r';

GRANT SELECT ON "pg_catalog"."pg_tables" TO PUBLIC;
COMMIT;
