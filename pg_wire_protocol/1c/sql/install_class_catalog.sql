-- APP-12: persistent schema-level tables/views with native names and types.
-- Run once as database owner/administrator, with concurrent DDL stopped.
-- Requires intact APP-10/11 with an active namespace hook; no repair/upgrade.
-- Supply the database and credentials to native isql, not in this file.
SET BAIL ON;
SET AUTODDL OFF;

-- Native relation IDs may be reused after DROP/wrap. +1 maps native ID 0 to 1.
-- DISTINCT makes the projection read-only, including for its owner.
CREATE VIEW PG_CATALOG.PG_CLASS(OID, RELNAME, RELNAMESPACE, RELKIND) AS
    SELECT DISTINCT
        CAST(R.RDB$RELATION_ID + 1 AS BIGINT),
        CAST(TRIM(TRAILING FROM R.RDB$RELATION_NAME)
             AS VARCHAR(63) CHARACTER SET SYSTEM.UTF8),
        N.OID,
        CAST(CASE WHEN R.RDB$RELATION_TYPE = 1 THEN 'v' ELSE 'r' END
             AS VARCHAR(1) CHARACTER SET SYSTEM.UTF8)
    FROM SYSTEM.RDB$RELATIONS R
    JOIN PG_CATALOG.PG_NAMESPACE N
      ON N.NSPNAME = TRIM(TRAILING FROM R.RDB$SCHEMA_NAME)
    WHERE R.RDB$PACKAGE_NAME IS NULL
      AND COALESCE(R.RDB$RELATION_TYPE, 0) IN (0, 1);

GRANT SELECT ON PG_CATALOG.PG_CLASS TO PUBLIC;

COMMIT;
