# Temporary-table services

`ANALYZE relation` is parsed only in dialect PG4. Its engine node resolves the
actual attachment-local PG temporary table and uses native `IDX_statistics` on
that table's current data instance. The empty base index is not the target.
Cached SQL plans are purged so subsequent prepares use refreshed native index
selectivity and the native page-based cardinality estimate. No PostgreSQL
statistics catalog or second optimizer is introduced.

The installed `public.fasttrun_ext(name)` and `public.fasttruncate(name)` wrappers
call `RDB$PG_FASTTRUNCATE`. It parses a schema/object name with the existing name
parser's optional lowercase policy, then uses the same native PG name resolver
as ordinary queries. It cannot execute arbitrary SQL supplied as a name.

The operation removes the native temporary page instance and recreates its
indexes from existing metadata. Table identity, metadata and serial counter are
preserved. Row undo preceding the truncate is discarded across all metadata
versions of that physical relation; later writes retain ordinary native undo.
The operation deliberately survives full rollback and rollback to a savepoint,
matching Postgres Pro's transaction-unsafe fasttrun extension. Ordinary SQL
`DELETE` would have different semantics.

Only the explicit truncate path requests the new atomic `delPages` guard and
cross-version undo discard. Existing native callers keep their previous default
behavior. A shared page snapshot, another native transaction using the table or
an active cursor prevents cleanup before any data/undo changes. Partial native
instance creation is removed on failure; failure while deleting pages or
releasing old undo invalidates the transaction and closes the affected attachment
rather than replaying stale row identities. Cleanup continues through all matching
actions and savepoints even if releasing one undo allocation fails.

The installer atomically adds a `fasttrun` compatibility-level `2.0` row to
`pg_available_extensions`, conditional on both real wrappers existing. It
preserves the existing `mchar` row. This reports the tested compatibility
profile; it does not load a PostgreSQL extension or implement CREATE EXTENSION.

## Profile limits

- Both operations target this attachment's PG-owned LTTs. Persistent tables,
  native GTTs and unrelated attachments are outside this service profile.
- The functions return native INTEGER `0`, with OID 23, rather than PG `void`.
  The recorded calls ignore their output. NULL returns NULL.
- `ANALYZE` has no options, column list or whole-database form. It refreshes native
  statistics, not PostgreSQL histograms. Existing explicitly prepared plans are
  not promised PostgreSQL's replan policy.
- Active cursors and another native transaction holding the LTT are rejected
  with `55006`. This guard is intentional. In the private Postgres Pro 18.6 oracle,
  fasttruncate with a partially consumed cursor produced a PANIC about a pinned
  local block; the implementation does not reproduce that failure.
- Resource-exhaustion paths use native cleanup/invalidation. They are distinct
  from ordinary SQL errors after a successful truncate; no failure-atomicity
  promise is made for the transaction-unsafe operation.

The private oracle confirmed that rows stay removed after rollback and that
serial continues from its previous allocation: after IDs 1 and 2 are truncated,
next is 3; allocations 4 and 5 remain consumed through truncate and rollback,
so next is 6. It also confirmed strict NULL and persistent-table rejection.

```sh
python3 -B pg_wire_protocol/tools/pgwire_temp_services_checks.py \
  --firebird-dir /path/to/built/firebird --work-dir /tmp/pgwire-temp-services \
  --capture-dir /path/to/capture
```

This creates a private database/runtime and uses genuine libpq plus the native
OO API. The supplied capture is read only; selected14/15/16 run on synthetic LTTs.
Statistics are observed through existing `SYSTEM.RDB$SQL.EXPLAIN.CARDINALITY`,
without adding an engine information item for the test.
