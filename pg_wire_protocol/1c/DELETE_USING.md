# DELETE USING for the recorded 1C cleanup

PG dialect 4 accepts a target and a comma-separated list of table or view names,
with optional aliases and WHERE. The observed Gilev cleanup is:

```sql
DELETE FROM _SeqB196 T1 USING pg_temp.tt7 T2
WHERE T1._Fld195RRef = T2._Fld195RRef;
```

The engine parser builds an ordinary DELETE with a correlated EXISTS over the
USING sources. Existing Firebird execution, transactions, privileges and type
comparisons apply. Duplicate source matches delete a target row once; NULL
comparisons retain their normal SQL meaning. Without WHERE, any source row
allows deletion, while an empty source leaves the target unchanged.

Target and sources share the SQL name-resolution level while the predicate is
compiled: ambiguous unqualified fields and conflicting aliases are rejected.
Explicit aliases hide the original relation name. The target's original scope
is restored before generating native execution code. Source names are resolved
as relations before native FROM compilation, so a selectable procedure name
cannot enter the relation-only path.

No wire SQL rewriting, new public API, on-disk metadata, or dependencies are
added. Native dialects 1/2/3 retain ordinary DELETE and reject USING. Ordinary
DELETE RETURNING is unchanged.

## Current limits

- USING accepts table/view names, including accessible PG temporary tables.
  Joined sources, derived tables, LATERAL, functions and CTE sources are outside
  this profile; comma-separated sources and subqueries in WHERE are supported.
- RETURNING accepts qualified target fields (`target.id`, `target.*`). Source
  fields, bare `*`, unqualified fields and expressions/aliases are refused with
  0A000 before effects. PostgreSQL also allows these forms; retaining a selected
  source row would require a broader execution contract.
- USING is refused in PSQL, including EXECUTE BLOCK and stored procedures, with
  0A000. This avoids persisting dialect-specific source for native recompilation.
- Existing native diagnostics remain: hidden-name references produce 42703,
  alias conflicts and aggregate misuse produce 42000. PostgreSQL's corresponding
  codes can differ; ambiguity remains 42702.

## Verification

The focused harness uses the existing isolated Firebird setup and real libpq.
It includes 38 reference cases obtained from a private PostgreSQL oracle, the
exact recorded cleanup with binary bytea, prepared text/binary results,
commit/native visibility, rollback/savepoints, failed-prepare cache recovery,
quoted aliases, routine-source rejection, PSQL restrictions and native dialect
controls. Each oracle case rolls back its changes.

```sh
python3 pg_wire_protocol/tools/pgwire_delete_using_checks.py \
  --firebird-dir /path/to/built/firebird \
  --work-dir /tmp/delete-using-check
```

The optional `--oracle summary.json [summary.json ...]` uses independently saved
reference results instead of the checked-in cases. Only isolated test databases
are created; the prepared 1C database and the PostgreSQL source are not used.

The target-only predicate case also exposed an existing native EXISTS optimizer
assertion, reproduced without USING on a pre-change engine. Its separate fix
keeps the conjunct-delivery heuristic from receiving an empty stack. The harness
retains that native DELETE/EXISTS regression for all three native dialects.

13 November 2026: the full focused run passed with the final engine build:
`/tmp/app18-delete-using-05/summary.json` (38 reference cases plus the checks above;
server exit 0). Parser conflicts remain at the existing 165 shift/reduce and 12
reduce/reduce baseline. This proves the supported cleanup operation; completion
of the real Gilev benchmark is a separate acceptance result.
