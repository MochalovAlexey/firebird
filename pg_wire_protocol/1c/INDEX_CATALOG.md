# Persistent index catalog

`sql/install_index_catalog.sql` projects actual native indexes into `pg_class`
(`relkind='i'`) and `pg_index(indexrelid,indrelid)`. It preserves the existing
persistent and attachment-local table/view rows. The views are read-only and
catalog installation is transactional.

The public identity is a BIGINT:

```
4294967296 + native_relation_id * 65536 + RDB$INDEX_ID
```

`RDB$INDEX_ID` is the native one-based, per-relation index ID. The projection
requires a positive non-NULL ID and a persistent, unpackaged table. This range
is separate from the existing uint32 table/temporary IDs. These values are not
promised to fit a PostgreSQL oid32. Native IDs can be reused after DROP; there
is no new registry, allocation counter, ODS format or public API.

`RDB$PG_INDEX_ID(exact_name)` uses the existing resolver and native metadata
lookup. It returns the native packed pair without the public catalog offset;
SQL owns that offset. The old relation helper and the new index helper share
argument evaluation, path validation and exact-name checks. Dots and quotes
are ordinary name characters, not SQL to parse.

The PG-only resolver now searches tables/views/indexes together in each effective
schema, including the implicit/explicit temporary namespace and schema USAGE.
An earlier index prevents SELECT from silently finding a later table; an earlier
table similarly prevents index DDL from acting on a later index. Other native
object kinds requested by a caller retain their per-schema fallback. Ordinary
native dialects 1/2/3 keep their previous lookup behavior.

The catalog wrapper dispatches `pg_table_is_visible` to the appropriate native
helper. Temporary indexes are not catalog rows in this profile, but a temporary
index or table with the same name correctly hides a persistent index. Another
attachment's temporary objects never hide it. An inactive persistent index
remains in the catalog with its native identity.

## Explicit limits

- Only persistent indexes on ordinary unpackaged tables are projected. Temporary
  indexes, GTTs and packaged relations are not catalog rows. Unknown IDs return
  NULL; known hidden indexes return false.
- Only `indexrelid` and `indrelid` are exposed. PG index attribute arrays,
  operator classes, validity flags, expression definitions and oid32 typing are
  outside this catalog profile. `pg_tables` and `pg_attribute` exclude index IDs.
- Native schemas can contain a table and index with the same schema/name. PG
  cannot represent that state. Visibility helpers reject it with `0A000` instead
  of choosing a kind; native PSQL wrapping can report `39000`. PG DDL has not
  gained a new collision constraint in this change.
- Wrong-kind ordinary SELECT/index DDL rejects the object using native missing-
  object diagnostics; these can differ from PostgreSQL `42809`.
- GRANT/REVOKE tests use fresh attachments, respecting existing native security
  cache lifetime. No new privilege-cache invalidation mechanism is introduced.
- Native ALTER INDEX RENAME syntax remains unsupported and is rejected. DROP,
  rollback and recreate are tested; there is no promise to preserve an index
  identity across DROP/recreate.

Acceptance includes the actual `v8users/byemail_v8users` 1C query, two sessions,
inactive/active and transactional lifecycle, path/USAGE, mixed object kinds,
temporary shadowing, exact quoted names, BIGINT Bind and native dialect controls:

```sh
python3 -B pg_wire_protocol/tools/pgwire_index_checks.py \
  --firebird-dir /path/to/built/firebird --work-dir /tmp/pgwire-indexes
```

The harness owns its runtime, localhost endpoints and databases. Application
and reference databases are not used.
