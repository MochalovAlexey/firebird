# Native semijoin with no inner join conditions

A native dialect 3 statement such as this aborted the Debug engine before execution:

```sql
DELETE FROM T
WHERE EXISTS (SELECT 1 FROM S WHERE T.V = 20)
RETURNING T.ID;
```

The predicate only uses the outer target. After native semijoin transformation,
`RseNode::compile` has no conjunct involving the inner source. It still called
`Optimizer::deliverJoinConjuncts`, whose contract requires a nonempty stack.
The assertion was `stack.hasData()` in `Optimizer.h`.

The fix checks for the empty stack at the existing call site. It follows the same
branch as the decision not to deliver conditions, including `firstRows = false`.
No new optimizer operation, protocol flag, SQL API or special PG comparison is added.

The failure was reproduced with native embedded isql on the earlier engine from
`/tmp/pgwire-1c-launch/runtime`, SHA256
`9f64567ea7b4678acc56e426363b10e62f7c5864474dc18078ac71516f8ce3de`, before the
DELETE USING implementation. Evidence:
`/tmp/app17-delete-native-exists-old-02/summary.json` and `runtime/firebird.log`.
This establishes a preexisting defect in this fork, not its upstream provenance.
The equivalent PG DELETE USING failure and stack are retained in
`/tmp/app17-delete-using-review-02/backtrace.log`.

[Native regression SQL](../tools/native_semijoin_empty_conjuncts.sql) runs in a
disposable database using native dialect 3, without compatibility installers.
Its 14 checks cover target-only predicates with/without RETURNING, no match,
NULL, empty sources, source-only/constant predicates, duplicate source rows,
ordinary correlation, and NOT EXISTS. Each check asserts the exact remaining
row count and identity mask, then rolls back. Successful completion removes its
fixture objects. The script is not part of the compatibility installer.

Acceptance on 13 November 2026: all 14 native assertions passed using the
repository SQL file, and its fixture objects were removed.
`/tmp/app17-native-semijoin-final/summary.json` records engine SHA256
`cc84d3c9b345688ef9694bdba74aac314bbedb4114b8e1431164d9370143a0a9`; the retained
`bootstrap-native_exists_cases.log` includes native plans and RETURNING rows.
The formerly failing PG target-only predicate also passed in
`/tmp/app17-delete-using-review-final/summary.json` (30 independent profile
checks, clean server exit 0). No source or application database was modified.
