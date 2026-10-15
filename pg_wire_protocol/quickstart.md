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

## Run SQL

At the psql prompt, use Firebird SQL and finish a query with `;`:

```sql
SELECT CAST(42 AS INTEGER) AS "answer" FROM RDB$DATABASE;
SELECT * FROM country;
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
