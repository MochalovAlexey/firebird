-- Initial compatibility objects for the recorded single-database 1C profile.
-- Run through native isql as the database owner. This is not a PostgreSQL catalog.
CREATE SCHEMA IF NOT EXISTS PG_CATALOG;

SET TERM ^;
CREATE OR ALTER FUNCTION PG_CATALOG.PG_BACKEND_PID()
RETURNS INTEGER
SQL SECURITY INVOKER
AS
BEGIN
    -- Attachment identity is stable and unique within this database.
    -- Checked conversion: never truncate or wrap a 64-bit attachment ID.
    RETURN CAST(CURRENT_CONNECTION AS INTEGER);
END^
SET TERM ;^

GRANT USAGE ON SCHEMA PG_CATALOG TO PUBLIC;
GRANT EXECUTE ON FUNCTION PG_CATALOG.PG_BACKEND_PID TO PUBLIC;
COMMIT;
