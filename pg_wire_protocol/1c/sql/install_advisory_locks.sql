-- Transaction advisory locks for the captured 1C bigint-key calls.
-- Native lock manager arbitration, including savepoint undo and disconnect.
-- Profile limitation: the result is integer 0, not PostgreSQL's void pseudo-type.
SET BAIL ON;
SET AUTODDL OFF;
SET TERM ^;

CREATE FUNCTION "pg_catalog"."pg_advisory_xact_lock_shared"("key" BIGINT)
RETURNS INTEGER
SQL SECURITY INVOKER
AS
BEGIN
    RETURN RDB$PG_ADVISORY_LOCK("key", 1);
END^

CREATE FUNCTION "pg_catalog"."pg_advisory_xact_lock"("key" BIGINT)
RETURNS INTEGER
SQL SECURITY INVOKER
AS
BEGIN
    RETURN RDB$PG_ADVISORY_LOCK("key", 2);
END^

SET TERM ;^
GRANT EXECUTE ON FUNCTION "pg_catalog"."pg_advisory_xact_lock_shared" TO PUBLIC;
GRANT EXECUTE ON FUNCTION "pg_catalog"."pg_advisory_xact_lock" TO PUBLIC;
COMMIT;
