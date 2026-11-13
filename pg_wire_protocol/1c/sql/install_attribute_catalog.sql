-- Live columns of persistent schema-level tables/views, matching pg_class IDs.
-- attnum uses native stable FIELD_ID + 1, not mutable display FIELD_POSITION.
-- Dropped columns have no live row; no historical tombstones are synthesized.
-- Current PG temporary columns are outside this SQL-only projection: MON$
-- exposes a monitoring snapshot and positions, not their stable field IDs.
SET BAIL ON;
SET AUTODDL OFF;

CREATE VIEW "pg_catalog"."pg_attribute"("attrelid", "attname", "attnum", "attisdropped") AS
    SELECT DISTINCT
           CAST(R.RDB$RELATION_ID + 1 AS BIGINT),
           CAST(TRIM(TRAILING FROM F.RDB$FIELD_NAME) AS VARCHAR(63) CHARACTER SET SYSTEM.UTF8),
           CAST(F.RDB$FIELD_ID + 1 AS INTEGER),
           FALSE
      FROM SYSTEM.RDB$RELATION_FIELDS F
      JOIN SYSTEM.RDB$RELATIONS R
        ON R.RDB$SCHEMA_NAME = F.RDB$SCHEMA_NAME
       AND R.RDB$RELATION_NAME = F.RDB$RELATION_NAME
     WHERE F.RDB$PACKAGE_NAME IS NULL AND R.RDB$PACKAGE_NAME IS NULL
       AND COALESCE(R.RDB$RELATION_TYPE, 0) IN (0, 1);

GRANT SELECT ON "pg_catalog"."pg_attribute" TO PUBLIC;
COMMIT;
