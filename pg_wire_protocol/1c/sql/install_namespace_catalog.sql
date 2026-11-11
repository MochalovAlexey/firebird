-- APP-11: public SQL projection of the installed APP-10 namespace registry.
-- Run once as database owner/administrator, with concurrent DDL stopped.
-- APP-10 must be intact and its DDL hook active; this script does not repair it.
-- Supply the database and credentials to native isql, not in this file.
SET BAIL ON;
SET AUTODDL OFF;

CREATE SCHEMA IF NOT EXISTS PG_CATALOG;

-- Keep native BIGINT/VARCHAR types and exact native names, including case.
-- DISTINCT makes the view read-only. Existing PG_NAMESPACE is an error.
CREATE VIEW PG_CATALOG.PG_NAMESPACE(OID, NSPNAME) AS
    SELECT DISTINCT NAMESPACE_ID, NATIVE_NAME
      FROM PGWIRE_COMPAT.NAMESPACES;

GRANT USAGE ON SCHEMA PG_CATALOG TO PUBLIC;
GRANT SELECT ON PG_CATALOG.PG_NAMESPACE TO PUBLIC;

COMMIT;
