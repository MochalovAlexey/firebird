-- APP-14: visibility of the persistent relations projected by APP-12/13.
-- Run once as administrator after APP-10..13, without concurrent DDL.
-- Requires the RDB$PG_RELATION_ID builtin; no ODS migration or catalog repair.
SET BAIL ON;
SET AUTODDL OFF;

SET TERM ^;
CREATE FUNCTION "pg_catalog"."pg_table_is_visible"("relation_oid" BIGINT)
RETURNS BOOLEAN
SQL SECURITY INVOKER
AS
DECLARE "exact_name" VARCHAR(63) CHARACTER SET SYSTEM.UTF8;
DECLARE "relation_id" INTEGER;
BEGIN
    SELECT C."relname" FROM "pg_catalog"."pg_class" C
        WHERE C."oid" = :"relation_oid" INTO :"exact_name";
    IF ("exact_name" IS NULL) THEN
        RETURN NULL;

    "relation_id" = RDB$PG_RELATION_ID("exact_name");
    RETURN COALESCE("relation_id" = "relation_oid" - 1, FALSE);
END^
SET TERM ;^

GRANT EXECUTE ON FUNCTION "pg_catalog"."pg_table_is_visible" TO PUBLIC;

COMMIT;
