# NULL-aware equality profile

The PG4 parser accepts `==` with PostgreSQL generic-operator precedence and
lowers it to the existing native `IS NOT DISTINCT FROM` operation. No new BLR,
persisted type, public API, comparison implementation or operator registry is
introduced. Stored source uses a parenthesized native expression so native
reprepare keeps its original grouping. Native dialects continue to reject `==`.

The accepted types are BOOLEAN, SMALLINT/INTEGER/BIGINT, DATE, TIME without time
zone, TIMESTAMP with/without time zone, bytea, mchar and mvarchar. NULL equals
NULL; only one NULL compares false. Binary values retain exact length-sensitive
comparison. mchar/mvarchar retain their existing case-insensitive, accent-sensitive
and trailing-space rules. Unknown string literals can adopt the typed operand;
mixed mchar/mvarchar or profile/native textual operands are rejected.

`sql/install_fulleq.sql` installs eleven ordinary native SQL `isfulleq_*`
functions and advertises a **partial fulleq 2.0 compatibility profile** only while
all eleven standalone wrappers exist. Existing mchar/fasttrun rows are preserved.
This is not a PostgreSQL library loader; CREATE/ALTER EXTENSION remains unsupported.
The common installer applies this script after temporary services;
`--upgrade-from-app17` adds it to an initial APP-17 installation.

The mchar wrapper receives native text BLOBs using PG_MCHAR. Existing blob
conversion removes the mchar typmod header before PSQL parameter validation;
equality does not depend on that header. No native parameter-validator change is
needed for these functions.

## Explicit limits

- Ordinary CHAR/VARCHAR/TEXT and unrelated native BLOBs are rejected by `==`.
  Their padding/collation can differ from fulleq's exact text comparison.
- NUMERIC/DECIMAL, INT128 and DECFLOAT are rejected. The reference extension has
  no numeric overload and implicitly chooses float8: even adjacent integers
  9007199254740992 and 9007199254740993 compare equal after NUMERIC conversion.
  Native exact arithmetic must not silently substitute a different operation.
- REAL/DOUBLE are rejected: PG treats NaN equal to NaN, while native comparison
  does not. This includes binary Bind NaN; no float wrapper is advertised.
- TIME WITH TIME ZONE is rejected: PG distinguishes original offsets even when
  UTC time is equal, while native comparison uses UTC time. TIMESTAMP WITH TIME
  ZONE compares actual instants and is supported within existing timestamp limits.
- There is no fullhash API, PG hash operator class, OPERATOR(schema.==) syntax or
  support for other PG-only types. Native optimizer/index behavior is reused.
- Untyped `$1 == $2` is not inferred from libpq parameter OIDs. Use an explicit
  SQL cast on one operand, as with the existing Extended Query profile.
- Existing NAT-07 (persistent views without FROM) and the postfix-cast DDL guard
  remain unchanged. Stored-source checks use CAST and a real FROM relation.

The APP-16 PSQL parameter-validation gap where direct subtype-11 mchar parameters
validate header bytes as UTF8 is tracked as [SQL-11](../BACKLOG.md). The text-BLOB declarations above
avoid that path; broadening arbitrary mchar PSQL parameter support is follow-up work.

Acceptance uses a saved oracle of pure SELECT expressions from a read-only
Postgres Pro session:

```sh
python3 -B pg_wire_protocol/tools/pgwire_fulleq_checks.py \
  --firebird-dir /path/to/built/firebird --work-dir /tmp/pgwire-fulleq \
  --oracle /path/to/fulleq-oracle.json
```

The harness creates its own runtime/database, checks atomic failed/repeated
installation, actual libpq results and OIDs, explicit native source reprepare,
indexed predicates/joins and server restart. Source application data is not used.
