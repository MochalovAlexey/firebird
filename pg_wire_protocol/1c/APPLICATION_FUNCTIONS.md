# 1C application functions

`sql/install_1c_functions.sql` installs real native PSQL functions required by
the observed 1C bootstrap. The common installer applies it after `fulleq`.
Functions are visible through the existing `pg_proc` projection and callable
through both native Firebird and PgWire; no query interception or PL/pgSQL
interpreter is involved. Installation is transactional and refuses existing
objects instead of replacing user code. `PUBLIC` receives execution rights.

`public.datediff2(mode varchar, d2 timestamp, d1 timestamp)` returns an INTEGER
for **d1 minus d2**:

| Mode | Semantics |
| --- | --- |
| `SECOND` | Difference of float8 epoch seconds, rounded to nearest integer; ties to even |
| `MINUTE`, `HOUR` | Same elapsed seconds divided by 60/3600, truncated toward zero |
| `DAY`, `WEEK` | Difference of calendar dates, divided by seven with truncation for weeks |
| `MONTH`, `QUARTER`, `YEAR` | Difference of calendar period numbers |

The float8 epoch calculation follows the original 1C function, including
floating-point subtraction near epoch boundaries. Native `DATEDIFF`, `EXTRACT`,
`TRUNC` and ordinary integer assignment provide the remaining behavior and
overflow checks. Exact uppercase mode names are required; trailing spaces are
significant regardless of the database collation. Valid modes with NULL
timestamps return NULL. Integer overflow is an error (`22003`).

`public.vassn(boolean)` returns INTEGER `0` for TRUE and `2000000000` for FALSE
or NULL, using native `IIF` as in the source function's IF/ELSE branches.

Current limitations:

- Timestamp range and precision remain native Firebird (years 1–9999,
  100 microseconds); PostgreSQL infinities and finer precision are unsupported.
- Unknown/NULL mode raises a native exception (`HY000`, exposed by PgWire as
  `XX000`), whereas the source PL/pgSQL missing-RETURN error is `2F005`.
  Both reject the call; exact SQLSTATE compatibility remains deferred.
- The mode parameter is native UTF8 VARCHAR(8191), not unlimited PostgreSQL
  VARCHAR. Longer values already fail native argument conversion.
- Other application functions and user-supplied PL/pgSQL are not implemented
  by this installer; add them only after their actual SQL/semantics are known.

Run the reproducible acceptance against private runtimes (no live source or
application database is contacted):

```sh
python3 -B pg_wire_protocol/tools/pgwire_1c_functions_checks.py \
  --firebird-dir /path/to/built/firebird \
  --pg-root /opt/pgpro/1c-18 \
  --work-dir /tmp/pgwire-1c-functions-check
```

It compares native SQL and real libpq results with a temporary PostgreSQL
running the source function body, checks integer/OID/binary results, NULLs,
overflow, case/trailing-space rejection, atomic installation, public grants
and the real `pg_proc` entry. Temporary servers are stopped on exit.
