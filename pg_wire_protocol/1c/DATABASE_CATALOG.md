# Current database and locale

`install_database_catalog.sql` adds `pg_catalog.current_database()` and a read-only
`pg_catalog.pg_database(datname, datcollate)` view using native PSQL and metadata.
It returns exactly the attached database, with the canonical identity from
`RDB$GET_CONTEXT('SYSTEM', 'DB_NAME')`. The identity can be a physical path, rather
than the PgWire startup alias. There is no cluster database enumeration or new
engine/plugin API. Columns retain native VARCHAR wire metadata.

`pg_catalog.pg_tablespace(spcname)` represents the empty PostgreSQL tablespace
namespace: Firebird has no named PG tablespaces, including `v81c_data` and
`v81c_index`. It is read-only; native storage locations are not misrepresented
as PostgreSQL tablespaces. `CREATE TABLESPACE` is unsupported. The real 1C 8.5
startup existence query consequently returns zero rows.

`install_type_catalog.sql` adds the five installed APP-16 aliases `mchar`,
`mvarchar`, `bytea`, `int8`, `regnamespace` to `pg_catalog.pg_type`, using the
actual codec OIDs and database-local namespace registry. It does not enumerate
arbitrary native domains or promise the remaining PostgreSQL catalog columns.
`pg_type_is_visible` follows the current parser's fixed alias recognition,
independent of search_path; unknown/NULL OIDs return NULL. Native type rules
and search-path-independent aliases are not redefined by the catalog.

`pg_proc(oid, proname, pronamespace)` projects real nonpackaged entries in
`RDB$FUNCTIONS`, including installed functions and aggregates, with native
function ID + 1 and registry namespace IDs. Exact name case is preserved.
The parser's type syntax does not create callable `mchartypmod_in` or other
fictional extension functions; their absence remains observable.

`pg_available_extensions` has one row while both native string aliases exist:
`mchar`, compatibility version `2.3`. This is the APP-16 built-in implementation
tested against the reference's installed mchar 2.3, including the matching binary
codecs and [documented type/expression profile](../COMPATIBILITY.md). Its `comment` explicitly identifies
the Firebird implementation. It does not claim an installed PostgreSQL `.so`,
the full extension's auxiliary SQL API, an extension loader or arbitrary
`CREATE EXTENSION` support. Unimplemented extension functions still fail normally.
After `install_temp_services.sql`, the view also exposes compatibility level
`fasttrun 2.0` while both real service wrappers exist. Its comment states the
native INTEGER result and active-cursor guard differences; the
[service contract](TEMP_SERVICES.md) defines the implemented behavior.

For an existing APP-16 database:

```sh
python3 pg_wire_protocol/1c/install_compat.py --isql /path/to/bin/isql \
  -d /path/to/database.fdb -U SYSDBA --upgrade-from-app16
```

The default UTF8 binary collation is reported as `C.UTF-8`. For a new 1C database
requiring the Russian locale, add `--locale ru_RU.UTF-8` **before importing the
application schema**. This installs a native ICU `ru_RU` collation and changes
the UTF8 default through `ALTER CHARACTER SET`. Existing columns keep their
collation. Existing `PG_MCHAR`/`PG_MVARCHAR` collations also remain independent.
The option is a one-time configuration, not a repair of an existing profile.
Each SQL installer commits separately; failure stops subsequent files.

`datcollate` is calculated from the live database charset and default collation.
An unsupported charset/collation fails explicitly at installation and at query
time, including changes made after installation. The view never reports a locale
constant disconnected from native metadata.

The Russian profile uses native ICU 70.1 in the tested build, with case/accent
sensitivity and native PAD SPACE behavior. It is **not** identical to PostgreSQL
libc `ru_RU.UTF-8`. A synthetic comparison with Postgres Pro 18.6 / libc 2.35
found differences for mixed Latin/Cyrillic ordering, punctuation and composed
accents. The captured database locale query and application language check are
supported; complete libc ordering and PostgreSQL text padding remain limitations.
Changing a default collation does not convert existing application columns.

Focused acceptance uses a copied runtime and private databases:

```sh
python3 -B pg_wire_protocol/tools/pgwire_database_checks.py \
  --firebird-dir /path/to/completed/firebird \
  --work-dir /tmp/pgwire-database-checks \
  --capture-dir /path/to/capture
```

It covers atomic installation/collision refusal, the general fresh/upgrade
installer, namespace identity preservation, two ordinary libpq connections,
Simple/Extended Query, binary results, transaction/reset stability, read-only
catalog behavior and changes to native default collation.
