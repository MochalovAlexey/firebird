-- Optional database profile, before importing application tables.
-- This changes the default for NEW UTF8 columns/expressions; existing columns
-- retain their declared collations. PG_MCHAR/PG_MVARCHAR are independent.
-- Native ICU ru_RU is not a promise of byte-for-byte libc ordering equivalence.
SET BAIL ON;
SET AUTODDL OFF;

CREATE COLLATION PGWIRE_COMPAT.RU_RU_UTF8 FOR SYSTEM.UTF8
    FROM SYSTEM.UNICODE PAD SPACE CASE SENSITIVE ACCENT SENSITIVE 'LOCALE=ru_RU';
ALTER CHARACTER SET SYSTEM.UTF8 SET DEFAULT COLLATION PGWIRE_COMPAT.RU_RU_UTF8;
COMMIT;
