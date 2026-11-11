# PgWire quickstart

Use a Linux build of this Firebird fork with `ServerMode = Super` and
`plugins/libPgWire.so`. Rebuild the server and plugins together. This example
uses the existing `employee.fdb` sample database and a valid Firebird user.

## Configure Firebird

Edit the configuration files in the runtime directory you actually start
(for example, `gen/Debug/firebird` for an in-tree Debug build).

`firebird.conf`:

```ini
ServerMode = Super
RemoteServicePort = 3050
WireProtocolPlugins = PgWire
WireProtocolPorts = 15432
AuthServer = Srp256
AuthClient = Srp256
```

`plugins.conf`:

```ini
Plugin = PgWire {
    Module = $(dir_plugins)/PgWire
    RegisterName = PgWire
    Config = PgWireConfig
}

Config = PgWireConfig {
    BindAddress = 127.0.0.1
    DatabaseAliases = pgwire_test
}
```

`databases.conf`:

```ini
pgwire_test = $(dir_sampleDb)/employee.fdb
```

Alternatively, use an absolute path to an existing database accessible to the
Firebird process. Keep the security database local with `Engine14` enabled in
its `Providers`; database overrides must allow `RemoteAccess` and `Srp256`.

Start or restart this Firebird instance after editing the files. Native clients
use port 3050; psql uses port 15432. PgWire currently supports loopback only,
without TLS.

## Install psql and connect

On Ubuntu/Debian:

```bash
sudo apt install postgresql-client -y
psql -X 'postgresql://sysdba:masterkey@127.0.0.1:15432/pgwire_test?sslmode=disable'
```

Replace `sysdba:masterkey` with your Firebird credentials. `-X` skips psql startup
files. Use the configured database alias, not a file path, in the connection URI.

## SQL names and application bootstrap

PG4 folds unquoted ASCII names to lowercase; quoted names are exact. Native dialects
1/2/3 still use uppercase. Existing employee objects therefore require quoted native
schema/table/column names. There is no uppercase fallback and no automatic migration.

For unqualified CREATE on the default path or unquoted catalog queries, install the five SQL scripts
in the [application bootstrap order](1c/README.md#app-13-имена-и-установка-lowercase-профиля)
on a separate test database. An intact APP-10/11/12+compat.sql database only needs
`1c/sql/install_pg_names.sql`. Use the owner/admin; credentials stay outside SQL.
The installer refuses an existing lowercase pg_catalog and preserves an existing
lowercase public and its data. Application CREATE/DML grants are separate.

For APP-14 relation visibility, run `1c/sql/install_relation_visibility.sql` once
after that bootstrap, on a runtime with `RDB$PG_RELATION_ID`. It adds
`pg_catalog.pg_table_is_visible(BIGINT)` with BOOLEAN results. The supported path
must explicitly contain lowercase pg_catalog, grant USAGE on every existing
schema, and contain no $user/pg_temp; this attachment must have no active native
local temporary tables. See the [visibility contract](COMPATIBILITY.md#app-14-видимость-постоянных-отношений).

PgWire sets `"public", "pg_catalog", "SYSTEM"` before successful startup and after
direct ALTER SESSION RESET. User SET SEARCH_PATH persists between commands.
This is a public-first native profile; implicit PG catalog priority, `$user`, full
USAGE/path semantics and transactional SET search_path are not implemented.

For manual SQL against an existing native database, you can instead select its
uppercase schema for this connection; the application bootstrap is not needed
for these ordinary queries:

```sql
SET SEARCH_PATH TO "PUBLIC", "SYSTEM";
SELECT * FROM "COUNTRY";
CREATE TABLE hoba1 (id INTEGER);
```

The CREATE makes `"PUBLIC"."hoba1"` with column `"id"`; PG4 name folding still
applies. Reconnect or ALTER SESSION RESET restores the default path. Without the
lowercase bootstrap or an explicit path change, CREATE can select SYSTEM as the
first existing schema and fail because user objects cannot be created there.
Check the target with `SELECT CURRENT_SCHEMA;`. Some errors currently have only
the generic message `Firebird SQL operation failed`; psql `\errverbose` shows
the SQLSTATE of the preceding error.

## Run SQL

At the psql prompt, use Firebird SQL and finish a query with `;`:

```sql
SELECT CAST(42 AS INTEGER) AS "answer" FROM "SYSTEM"."RDB$DATABASE";
SELECT * FROM "PUBLIC"."COUNTRY";
```

The first query returns 42; the second reads the employee sample database.

For `EXECUTE BLOCK`, write internal semicolons as `\;` so psql sends the whole
block as one request. The final ordinary `;` submits it:

```sql
EXECUTE BLOCK RETURNS (out1 CHAR(13))
AS
BEGIN
  out1 = 'hello PgWire'\;
  SUSPEND\;
END;
```

Expected result: one row containing `hello PgWire`.
When passing the entire block through `psql -c "..."`, use ordinary internal
semicolons instead: `-c` already sends its SQL argument as one request.

Autocommit is on by default. Exit with `\q`.
