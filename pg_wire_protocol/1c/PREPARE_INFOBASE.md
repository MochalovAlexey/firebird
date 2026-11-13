# Preparing an existing 1C database

`prepare_infobase.py` copies a PostgreSQL 1C schema and its actual rows into a
**separate, empty** Firebird database with the compatibility SQL already installed.
It uses the installed libpq through the existing Python ctypes connection helper;
no Python packages or server dependencies are added.
Its supported schema forms follow the inspected Gilev profile; this is not a
general PostgreSQL migration utility.
Column collations differing from the type default are rejected. Nondefault
index opclasses/collations are unsupported; unique indexes using them are errors.

Provide full libpq connection strings in `PGWIRE_SOURCE_DSN` and
`PGWIRE_TARGET_DSN` (or `--source-dsn` / `--target-dsn`). The source account needs
read access to the schema, catalogs, tables and sequence state. The destination
account must own the schema. Use UTF8 and an adequate Firebird page size for the
source's composite indexes; the current Gilev preparation uses 32768 bytes.

```sh
python3 pg_wire_protocol/1c/install_compat.py --database TARGET_NATIVE_CONNECTION
python3 pg_wire_protocol/1c/prepare_infobase.py --preflight-only --report /tmp/infobase-ddl.json
python3 pg_wire_protocol/1c/prepare_infobase.py --report /tmp/infobase-copy.json
```

The source runs in one read-only repeatable-read transaction. Rows are transferred
with binary libpq parameters, preserving embedded zero bytes and SQL NULLs.
Each table is read back from Firebird and checked against the source: row count,
two order-independent SHA256 row accumulators, bytea cell count, total bytea bytes,
and largest bytea value. Rows are fetched in bounded batches (`--batch-rows`, 128
by default), and verification uses libpq single-row mode. The report contains
schema names, counts and digests, never row payloads or connection credentials.

Run `--preflight-only` before copying data. It checks each table and index on
empty tables in rolled-back transactions and reports all rejected definitions.
If a nonunique index exceeds Firebird's native key limit even with the largest
page size, its omission must be explicit (`--omit-index NAME`) and remains in
the report. Unique indexes and primary keys cannot be omitted.

For nullable unique keys, PostgreSQL permits duplicates whenever any key column
is NULL; Firebird's ordinary unique index has different semantics. The preparer
uses a native partial UNIQUE index covering fully non-NULL keys and a separate
nonunique index covering all rows. Helper names are collision-checked, bounded
to 63 bytes, and recorded in `index_mappings`. The current source needs 14 such
mappings. PostgreSQL `NULLS NOT DISTINCT` indexes are explicitly rejected.
Future schema changes must also maintain/drop these helper indexes; automatic
synchronization with runtime ALTER/DROP INDEX is outside this preparation tool.

The destination schema must have no base tables. All DDL and data are copied in
one transaction; a failure rolls the destination back. The tool does not drop,
truncate, overwrite, or resume an existing schema. Source routines/extensions
are supplied by the separately installed compatibility profile, not translated
from PostgreSQL function bodies. Storage/autovacuum options have no Firebird
equivalent and are not copied.

The current Gilev source has 194 tables and 353 indexes including primary keys.
Its 16 nonunique composite expression indexes and three cyclic sequence defaults
are reported as pending schema work. By default these stop preparation before
destination changes. `--allow-incomplete-schema` explicitly permits copying all
rows and supported constraints/indexes while retaining this queue in the report;
it **does not** claim complete application compatibility. Unsupported unique
indexes, other defaults, generated columns, triggers, and foreign/check
constraints remain errors. PostgreSQL sequence state is recorded as observed;
sequence changes are not MVCC snapshots, so stop application writers when taking
a preparation copy intended for subsequent use.

An earlier committed preparation can receive the nullable-unique correction
without recopying data:

```sh
python3 pg_wire_protocol/1c/prepare_infobase.py --allow-incomplete-schema \
  --reconcile-index-nulls --copy-report /tmp/infobase-copy.json \
  --report /tmp/infobase-index-reconciliation.json
```

This changes all affected indexes in one transaction, validates their native
metadata, and compares each affected table's binary row fingerprints before and
after with the original committed copy report. The original report is then
marked reconciled. It cannot be applied twice or used with an uncommitted copy.
Source database/schema and a versioned schema fingerprint must still match the
copy report before any destination DDL is executed.

The source capture is external and not embedded in the tool. Source/target
schemas, exact quoted identifiers, types, defaults and indexes are read from
catalogs. Runtime 1C SQL is not changed by this preparation step.
