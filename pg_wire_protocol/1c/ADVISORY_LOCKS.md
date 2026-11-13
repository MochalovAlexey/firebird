# Transaction advisory locks

The captured bigint calls `pg_catalog.pg_advisory_xact_lock` and
`pg_catalog.pg_advisory_xact_lock_shared` use Firebird's database lock manager.
Their keys occupy a distinct native lock class; there is no per-worker lock map.
The installer adds two ordinary SQL wrappers over `RDB$PG_ADVISORY_LOCK`.

A native transaction owns each lock and its effective mode. Repeated acquisition
is idempotent; shared-to-exclusive conversion waits for other shared holders.
Native savepoints record acquisitions and upgrades. Rollback releases new locks
and restores earlier modes; release merges undo into the enclosing savepoint.
Commit, rollback, retaining boundaries and attachment cleanup release the locks.
Different native transactions in one attachment remain separate owners of their
logical locks; PostgreSQL connections continue using their single active native
transaction.

PgWire retains its native transaction in failed state `E`. On the first transition
into `E`, fixed internal SQL invokes mode zero of the helper to release locks of
the failed PostgreSQL scope, including errors originating entirely in the wire
plugin. Outer savepoint locks and native row undo remain intact for recovery.
Repeated errors in `E` do not release the enclosing scope a second time.

## Profile limits

`SET statement_timeout TO milliseconds`, `= milliseconds`, and `TO DEFAULT`
map to the existing Firebird attachment statement timeout; DEFAULT and zero clear
the attachment override, leaving the server's timeout policy in force. There is
no transport or connection-idle timer. The alias is available in PG4 direct SQL;
native dialects keep their existing `SET STATEMENT TIMEOUT` syntax. This setting
has native attachment lifetime: COMMIT/ROLLBACK do not undo it. PostgreSQL's
transactional GUC behavior, SET LOCAL and quoted time/unit strings are not part
of this profile. The captured lock scripts explicitly restore DEFAULT.

- The wrappers return native INTEGER `0` (OID 23), not PostgreSQL's `void`
  pseudo-type (OID 2278). The recorded 1C calls ignore this value. This is an
  explicit wire-result limitation; no new persisted type or public ABI was added.
- Only the captured bigint transaction functions are installed. Session locks,
  try-lock functions and two-int-key overloads are not provided.
- Waits use native cancellation, statement timeout and transaction lock policy.
  The previously accepted syntax-only `SET lock_timeout` remains syntax-only.
- Lock identity is scoped to the native database; unrelated databases do not
  share advisory locks. Signed bigint keys retain all 64 bits.

Run the isolated real-libpq and native API acceptance:

```sh
python3 -B pg_wire_protocol/tools/pgwire_advisory_checks.py \
  --firebird-dir /path/to/built/firebird --work-dir /tmp/pgwire-advisory-check
```

The work directory must not exist. The check creates its own database, runtime,
credentials and localhost ports; it does not use the application database.
