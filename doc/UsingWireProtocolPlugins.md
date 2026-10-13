# Server wire protocol plugins

Wire protocol plugins extend the network protocols understood by the Firebird
server. They are loaded into the server process through the existing plugin
manager under `IPluginManager::TYPE_WIRE_PROTOCOL`. Native Firebird connections
continue to use their existing frontend and do not use these interfaces.

The initial host implementation starts additional TCP listeners on Linux in
`ServerMode = Super`, using IPv4 loopback only. It provides bounded workers and
queues, socket transport, session ownership and server shutdown integration.
SuperClassic and Classic are diagnosed without starting additional listeners;
their native frontend remains available. Other platforms need separate wiring
and acceptance. P2 added host authentication and local attachments, and a separate
`PgWire` production module providing PostgreSQL startup. P3 introduced query
execution; the P4 implementation added Simple Query DML/DDL,
RETURNING, procedures/blocks and basic result codecs. Completion classification
now belongs to the PgWire plugin and uses the existing engine API. The refactor
passed targeted Linux/Super checks. P5 adds explicit transaction/session controls
and passed targeted Linux/Super checks with a documented native AUTO COMMIT
limitation; TLS remains a later step. Recorded checks and coverage limits are in
[`VALIDATION.md`](../pg_wire_protocol/VALIDATION.md).
P6 passed seven Linux/Super loopback server groups, 58 live driver cases, the build,
unit tests and targeted host/startup/query/transaction/lifecycle/native regressions.
The targeted Linux/Super P6/M1 profile is accepted with known native limitations.
Shutdown during a real ON CONNECT trigger is excluded from this acceptance:
it causes a Debug assertion at alloc.cpp:2075 (!child), also reproduced on the
native path with wire endpoints disabled. The [attach/shutdown reproducer](../pg_wire_protocol/tools/pgwire_attach_shutdown_smoke.py)
retains its actual FAIL and nonzero exit; it is not skipped or converted to a pass.
This ON CONNECT defect is not fixed or retested in P7. P7 Extended Query passed
16 targeted Linux/Super groups; its current contract is described below.
No new pause or timeout-expiration tests are added. SuperClassic checks only verify
that native remains available when PgWire is refused; Windows and auth-plugin or
close/free fault injection are outside this acceptance.

The interface definitions are in
[`FirebirdInterface.idl`](../src/include/firebird/FirebirdInterface.idl). The
internal host adapter is
[`ProtocolPlugin.h`](../src/remote/server/wire/ProtocolPlugin.h). C++ and Pascal
bindings are generated with CLOOP. Platform socket handles, TLS objects, STL
containers and C++ exceptions must not cross this ABI.

## Plugin registration and configuration

A module exports `FB_PLUGIN_ENTRY_POINT`, registers its factory with
`registerPluginFactory(TYPE_WIRE_PROTOCOL, name, factory)` and participates in
the normal `IPluginModule` cleanup lifecycle. A factory receives `IPluginConfig`
and returns one owned `IWireProtocol` reference. It reports creation failures
through `IStatus` and returns null. It must not start listeners, create workers
or perform database operations.

Returning both a plugin object and an error from `IPluginFactory::createPlugin`
violates this contract. The existing plugin manager does not forward that object
to the loading adapter for cleanup; a factory must release its partial objects
and return null on failure.

Configured names are distinct from module filenames and registered factory
names. Two aliases may select one factory from one shared library while using
different configuration blocks and separate plugin instances:

```ini
Plugin = FirstEndpoint {
    Module = $(dir_plugins)/SampleWire
    RegisterName = SampleWire
    Config = FirstConfig
}

Plugin = SecondEndpoint {
    Module = $(dir_plugins)/SampleWire
    RegisterName = SampleWire
    Config = SecondConfig
}

Config = FirstConfig {
    BindAddress = 127.0.0.1
    Workers = 4
    QueueSize = 16
    Label = first
}

Config = SecondConfig {
    Label = second
}
```

These are illustrative entries for a module implementing `SampleWire`; they do
not name a shipped protocol. The server configuration associates aliases and
ports by position:

```ini
WireProtocolPlugins = FirstEndpoint, SecondEndpoint
WireProtocolPorts = 15432, 15433
```

Both lists default to empty. Their parser uses commas, permits surrounding ASCII
whitespace and rejects empty positions, unequal counts, duplicate names or
ports, and ports outside 1 through 65535. It does not load plugins or remove
unavailable names. The loader requests each configured name separately because
the plugin manager may skip missing optional modules in a list; skipping an
entry must not change the port assigned to a subsequent entry.

`Config::getPlugins(TYPE_WIRE_PROTOCOL)` rejects implicit iteration. The host
reads the two wire settings together, validates positional pairs, and passes
one explicit configured name to the loading adapter. A direct adapter call
also rejects list separators before invoking the plugin manager.

Before bind or module loading, the host calls the existing
`IConfigManager::getPluginConfig(name)` and validates its transport settings.
Configuration uses the standard plugin lookup and file fallback rules; named
`Config` sections, as shown above, are the usual way to give aliases distinct
settings. The factory receives its configuration through `IPluginConfig`.
Prepare the configuration before starting the server. No additional resolver
or structural rules for `plugins.conf` are introduced. A null lookup result is
an endpoint error; an omitted host setting in a returned configuration uses the
documented default.

| Host setting | Default | Accepted values |
|---|---|---|
| `BindAddress` | `127.0.0.1` | Exactly `127.0.0.1` |
| `Workers` | `4` | Decimal integer from 1 to 16 |
| `QueueSize` | `16` | Decimal integer from 1 to 64 |
| `DatabaseAliases` | Empty | Comma-separated local aliases explicitly allowed for this endpoint |

Repeated host settings are errors. Any `Port` entry is rejected: the positional
list is the only port source. Any `TLS` entry is rejected in this initial
plaintext profile. Plugin-specific entries remain available to its factory.
At most 16 endpoints may be configured, with a combined budget of 64 workers.
A malformed list or excessive endpoint count disables the additional
configuration. Config, load or bind failure for one endpoint is logged while
the remaining endpoints and native frontend continue.

The startup hook borrows the already bound native listener and reads its address
with `getsockname`. Its resolved port includes command-line `-p` and service-name
resolution. A matching port conflicts with IPv4 wildcard or `127.0.0.1`, an IPv6
wildcard accepting IPv4, or an overlapping IPv4-mapped address. An IPv6-only
listener or Unix socket does not reserve the additional IPv4 loopback endpoint.
Native conflict and successful bind are checked before the wire module loads;
the native descriptor is never changed or closed by this host.

Configuration is applied at startup. There is no hot reload. A full accepted
connection queue closes new connections; queued sockets do not create plugin
sessions until a worker is available. Queued sockets do not expire by time.
There are no additional transport, frame, startup, authentication or output
timers. Blocking I/O waits for progress, EOF, an error or shutdown.

## Interface responsibilities

| Interface | Implemented by | Responsibility |
|---|---|---|
| `IWireProtocol` | Plugin | Create sessions for one configured endpoint |
| `IWireSession` | Plugin | Execute one session on a host worker and accept stop requests |
| `IWireConnection` | Host | Byte transport, shutdown and peer information |
| `IWireServerHost` | Host | Per-connection stopping state, diagnostics, authentication and local attachments |

The generated versions are 4 for `IWireProtocol` and `IWireServerHost`, and 3 for
`IWireSession` and `IWireConnection`. These numbers include inherited interface
versions. P2 appends `attachDatabase` and `detachDatabase` to the host interface;
PgWire checks its version before calling them. TLS upgrade requires a later
transport interface version.

Database access uses the host's authentication and local attachment service.
Plugins must not create a secondary native network connection. The PgWire SQL
executor passes the entire SQL text unchanged to the Firebird parser with an
explicit byte length. Its limited leading-command classifier is contained in
the plugin; the host ABI and engine SQL parser remain protocol-independent.
Classification does not rewrite, split or validate SQL.

## Session creation and lifetime

`IWireProtocol::createSession(status, host, connection)` is serialized for one
plugin instance. It must return promptly, without network I/O or engine calls.
On success it returns one owned `IWireSession` reference. A null result or an
error status is a failed session creation.

Session factories should also return null on failure. As a defensive measure,
the host adapter adopts any returned session before checking status, so an error
reported alongside a session still releases that session with its plugin pinned.

The host and connection arguments are **borrowed for the complete session
lifetime**, including the session's final `release()` call. The host adapter
holds references that guarantee this lifetime. A plugin may therefore retain
the borrowed pointers in its session without adding references. Borrowed
pointers must not be released by the plugin. If it elects to add its own
references, it must release only those added references and still stop all use
of the host and connection when the session is released.

`ProtocolPlugin` owns the plugin reference and releases it through
`IPluginManager::releasePlugin()`. Each `ProtocolSession` also retains its
originating `ProtocolPlugin`, so removing the endpoint's owner does not unload
code used by existing sessions.

Destruction proceeds in this order:

1. Release the returned plugin session while its host, connection and plugin
   remain alive.
2. Release the adapter's connection and host references.
3. Release the adapter's plugin owner, after returning from the session's code.

The plugin manager releases the module owner only after the final plugin
`release()` returns. Plugins must not arrange for their own last module reference
to disappear while returning through code in that module. The host does not
call `dlclose` or `FreeLibrary` directly.

The adapter does not manage worker lifetime itself. Its caller must ensure that
`run()` and every concurrent `stop()` invocation have finished before destroying
`ProtocolSession`. An atomic one-run guard does not make concurrent destruction
safe. Hold the owning object or a registry lease for the full duration of each
callback.

## Execution and stopping

`IWireSession::run(status)` runs once, on one host worker. It returns after
session resources have been closed. It does not consume the session reference.
The adapter rejects a second invocation. Sessions from the same plugin instance
may run concurrently; shared plugin state must accommodate this.

`IWireSession::stop()` is an idempotent, nonblocking request that may race with
`run()`, including a request made before `run()` begins. It must not join a
worker, perform an engine call, wait for a mutex held by execution, or release
the session or plugin. A session observes a stop request before starting further
work. Reference counting and the stop flag must support these concurrent calls.

The host adapter calls connection `shutdown()` before session `stop()`, so
transport waits are interrupted before entering plugin code. This also avoids
a lock cycle if a faulty stop callback waits for a mutex held across read().
It does not relax the nonblocking stop contract or guarantee recovery from
arbitrarily blocked plugin code.
The caller must not hold a registry lock across callbacks, object destruction or
plugin release. Copy or retain the necessary owners under the lock, then perform
those operations after releasing it.

Engine cancellation is a host responsibility with separately protected
attachment ownership. Terminal host stop uses `fb_cancel_abort` so cancellation
also prevents engine work between calls; PgWire rechecks stop after reading a
frame. This differs from a recoverable statement cancellation. It must not turn plugin `stop()` into a blocking
cancellation or detach operation. Server shutdown must allow providers to
terminate engine operations; an unconditional join before provider shutdown can
deadlock that process. Synchronous authentication and attach callbacks rely on
stop checks between calls and provider shutdown; the codec applies no timer.
There is no separate socket watcher during an engine call. A client disconnect
is detected after the call returns and I/O resumes, so it does not guarantee
immediate SQL cancellation. Explicit server shutdown cancels engine work.

The Linux host registers `preproviders` and `postproviders` shutdown callbacks.
The first stops accept, wakes connections and requests session stop without
joining workers. After provider shutdown, all endpoints share one five-second
deadline for acceptor and worker completion. Owners are released only after all
callbacks finish. If the deadline expires, shutdown reports failure and retains
owners rather than unloading code still in use. This deadline does not make an
arbitrarily blocking plugin callback safe.

## Transport I/O

One worker serializes the connection's `read` and `write` calls. Both methods
take an explicit buffer size, with no timeout argument. Buffers are borrowed
only for the duration of the call. A nonzero size requires a valid buffer.

| Outcome | Contract |
|---|---|
| Read or write makes progress | Return a count between 1 and the requested size immediately; partial progress is valid |
| Read reaches EOF with nonzero size | Return 0 with no error status |
| Write with nonzero size | Returning 0 without an error is invalid |
| Zero-size operation on an open connection | Successful no-op returning 0; the buffer is not accessed |
| Shutdown or I/O failure | Report an error through status and return 0; progress returned by earlier successful calls remains valid |

The caller checks status before interpreting a zero result. Shutdown is not EOF.
A failing call does not claim successful progress; after a terminal
transport error, it must not be retried as if no bytes could have reached the
peer.

Read/write wait for progress, EOF, an error or shutdown. EINTR is retried without
discarding successful progress. The host and PgWire codec impose no I/O timers.

`shutdown()` is concurrent, idempotent, nonblocking and permanent. It wakes any
pending I/O, and subsequent I/O fails, including zero-size calls. It does not consume a reference. Releasing
the final connection reference closes the host-owned transport; socket or TLS
resources must not be destroyed while a method is still using them.

`getPeerAddress()` returns an immutable numeric address without its port. Its
string is borrowed until the connection's final release. `getPeerPort()` returns
the peer port. `isEncrypted()` reports the actual transport state and does not
initiate encryption. The initial transport profile is plaintext; adding TLS
requires a later upgrade operation and host-managed TLS policy, not exposure of
OpenSSL objects through this ABI.

## Host services and diagnostics

`IWireServerHost` is a per-connection context. `isStopping()` is safe to query
during session execution and reports when the host is terminating that context.
The host's explicit stop and transport shutdown remain necessary to wake a
blocked call.

`attachDatabase(status, database, user, passwordLength, password, applicationName)`
is called only by the session worker. Inputs are borrowed for the call. The host
checks the endpoint's exact alias allowlist and local database configuration,
authenticates the supplied credentials and creates an engine-only attachment.
An empty `DatabaseAliases` setting permits no databases. Aliases must resolve
through `databases.conf` to local files; client paths, URIs and provider settings
are not accepted. Per-database `RemoteAccess`, authentication policy and local
security database configuration remain effective. The local security database
must include the current local engine (`Engine14`) in its `Providers` list; the
helper rejects other profiles before invoking Srp256.

The return value is **borrowed**, owned by the host until `detachDatabase` or the
end of `run()`. Use it only on the session worker. Do not directly release,
detach or drop it. Child statement, transaction and other interfaces retain
their usual ownership rules. Close child interfaces before
`detachDatabase(status, attachment)`, which invalidates the borrowed handle even
on error. The host also closes attachments left after `run()` returns. Neither
this API nor the generic host imposes PgWire's single-transaction rule on
other plugins.

The P2 helper exchanges `Srp256` client/server plugin messages in memory, provided
both are allowed by the database's `AuthClient` and `AuthServer` policy. It allows
at most 16 rounds and 64 KiB per authentication payload. It builds a fresh DPB
containing the verified auth block, UTF8/dialect 3, engine-only providers and
actual peer information. Raw credentials or client-supplied trusted/config DPB
fields are not forwarded. Password buffers are wiped after use. The helper
does not require or modify the EDS attachment context.

The host bounds UTF8 database aliases and input user/application names to 255
bytes; the normalized Srp256 login is at most 63 bytes. Its explicit password
input is at most 4096 bytes and contains no NUL. PgWire imposes the narrower wire
limit of 4096 bytes **including** the framing NUL. Monitoring records the actual
peer, `MON$REMOTE_PROTOCOL = TCPv4` and the configured endpoint alias in
`MON$REMOTE_VERSION` (normally `PgWire`). The endpoint alias passed as protocol
metadata is limited to 63 bytes. Remote security databases and native-client
database-encryption keys are outside the initial profile.

`log(level, length, message)` accepts `LOG_INFO`, `LOG_WARNING` or `LOG_ERROR`.
The byte buffer is borrowed for the call; the length is explicit. The initial
host bounds messages to 512 input bytes, replaces nonprintable bytes and adds
the configured alias and port. It must not treat
the supplied message as a format string. Plugins must exclude credentials,
authentication payloads and SQL parameter values from diagnostics. Logging from
`stop()` is unsuitable if it can block on I/O or a shared lock.

Each concurrent operation uses its own status object. Cleanup must preserve the
original failure. Exceptions are reported through CLOOP status dispatch rather
than crossing the module boundary; methods without a status argument must not
throw.

## Testing the loading layer

The loading tests use a real shared library and the normal plugin manager, with
two aliases and distinct configurations. Their host transport may be an
in-memory test implementation: this verifies ABI calls and ownership without
claiming a working TCP or SQL frontend.

Required cases include missing plugins without positional shifts, null/error
factory results, session creation failures, insufficient interface versions,
partial I/O, EOF, concurrent stop/wakeup and repeated stop. Destroy an
endpoint owner while one of its sessions is active, then verify that the session
and its borrowed arguments remain usable until final release.

Real transport acceptance covers zero-length operations and OS I/O failures.
The mock stream used by
the loading smoke does not validate those transport semantics.

Observe the order of session destruction, plugin destruction and module cleanup.
The plugin manager may delay module unloading; immediate disappearance from the
process map is not the ownership contract. Test configuration changes and global
shutdown in isolated processes because the plugin manager and configuration
caches are process-wide.

`wire_host_smoke.py` prepares an isolated runtime and starts a real Super server
with native TCP and two aliases of the test module. Its labelled byte echo
checks target listeners, queue limits and shutdown. The test module is
built under `tests`, is not installed as a production plugin and performs no SQL.
Only separately recorded successful runs establish acceptance; these fixtures
do not establish PostgreSQL or authentication support on the additional ports.

## PgWire startup profile (Linux/Super)

POSIX and CMake production build definitions include a separate `PgWire` shared
module. Its code is not linked into the native daemon frontend. Windows server
wiring and runtime acceptance remain pending. Configure an existing local alias
and Firebird user before enabling the endpoint:

```ini
# firebird.conf
WireProtocolPlugins = PgWire
WireProtocolPorts = 15432

# plugins.conf
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

Startup accepts protocol 3.0. SSLRequest and GSSENCRequest each receive exactly
one `N`; repeats are rejected. User is required. An absent database defaults to
the user name and is still subject to the alias allowlist; an explicitly empty
database is rejected. `client_encoding` must be absent or exactly `UTF8`;
`application_name` is accepted. Other startup parameters, including options and
replication, are explicitly rejected. Version negotiation, TLS and cancellation
are not implemented; no placeholder BackendKeyData is sent.

Startup frames are limited to 16 KiB, typed frame lengths to 1 MiB and password
bodies to 4096 bytes including NUL. Length checks precede allocation; string
boundaries and UTF8 are validated. AuthenticationOk follows successful engine
attach. Startup then reports encoding, DateStyle, application name and the
compatibility token `16.0 (Firebird pgwire prototype)`, and ends with ReadyForQuery
`I`. The recorded P2 implementation returned `0A000` for every structurally valid
Query. The current Simple Query executor replaces that temporary response.
Firebird prepares and validates the complete SQL before the plugin uses its
limited completion classification.

`ProtocolTest.cpp` tests framing and literal response bytes with partial I/O.
`pgwire_startup_smoke.py` exercises the loaded module, raw PostgreSQL startup,
authentication, native monitoring and session cleanup on an isolated runtime.
Results, commands and coverage limits are recorded in
[`VALIDATION.md`](../pg_wire_protocol/VALIDATION.md). Startup checks and the test
echo do not establish SQL execution support; query acceptance is recorded
separately.

## PgWire Simple Query and transaction profile (P5)

`Execute.cpp` sends the complete Query SQL and its explicit byte length to engine
prepare; only the framing NUL is excluded. The original SQL is never rewritten
or split into statements. Firebird remains responsible for syntax, names,
permissions and execution semantics. Unsupported PostgreSQL syntax, including
standalone BEGIN and `$n` parameters, receives the engine's error.

After successful prepare, the plugin's `SqlInfo.cpp` classifies the leading
command for CommandComplete and current capability checks. It skips native
whitespace and comments, treats quoted strings/identifiers as opaque tokens and
reads only the prefix needed to distinguish a command or DDL object. It does not
parse expressions, walk CTE bodies or implement PostgreSQL grammar. The existing
statement type cross-checks transaction descriptors. Session commands use an
explicit native SET/reset whitelist; unclassified prefixes, including USING
wrappers, receive `0A000` before execution. The engine parser, native statement types and public getInfo API
have no protocol-specific metadata additions.

Cursor execution uses `IStatement::getFlags() & FLAG_HAS_CURSOR` and
openCursor/fetchNext. Other statements use execute with output message metadata
when columns are present. Output columns come from the existing
IMessageMetadata interface; input parameters in Simple Query receive `0A000`.
Supported SQL includes SELECT, INSERT/UPDATE/DELETE/MERGE/UPDATE OR INSERT,
compatible DDL, RETURNING and executable/selectable blocks and procedures.
Unsupported input/output capabilities are checked before execution.

Supported text outputs are SMALLINT/INTEGER/BIGINT (OID 21/23/20), BOOLEAN (16),
CHAR/VARCHAR (1042/1043), NUMERIC/DECIMAL stored in SHORT/LONG/INT64 (1700), and
NULL. Scale and subtype are preserved, and exact numerics do not pass through
floating point. CHAR uses varying fetch conversion while retaining its logical
CHAR OID and meaningful spaces. UTF8/NONE/ASCII strings must contain valid UTF8
on output; OCTETS and unsupported charsets/types receive `0A000` from metadata
checks. Column aliases come from the engine; table OID and attribute number are
zero, and typmod is -1. Column provenance mapping is not implemented.

A QueryExecutor owns at most one current application transaction per session.
In I, prepare uses NULL and the engine's internal system transaction. Ordinary
SQL then starts one implicit READ COMMITTED READ CONSISTENCY/write transaction,
using WAIT without a plugin-imposed lock timeout. Cursors and
statements are closed before its real commit and CommandComplete. Errors roll
back the implicit transaction. In T/E, prepare uses the existing transaction.

Native SET TRANSACTION starts an explicit transaction and returns BEGIN/T.
COMMIT or ROLLBACK ends it and returns I. COMMIT/ROLLBACK RETAIN [SNAPSHOT] keeps
T; SAVEPOINT, RELEASE SAVEPOINT and ROLLBACK TO execute through the native engine.
An ordinary error in T preserves the transaction and changes the wire state to E.
Prepared ordinary statements in E receive 25P02. Successful ROLLBACK TO or
ROLLBACK RETAIN recovers T; full COMMIT in E performs rollback and returns
ROLLBACK/I. Full COMMIT/ROLLBACK in I is a validated no-op; retaining/savepoints
in I receive 25000. A second SET TRANSACTION in T receives 25001/E.

EMPTY preserves I/T/E. A prepare syntax error in E retains 42601; only a real
missing-relation cause is currently mapped to 25P02. Privilege, cancellation,
fatal and unknown semantic errors keep their diagnostics. Invalid handles,
failed cleanup and uncertain commit outcomes close the session. Disconnect
rolls back any remaining transaction before the attachment is detached.

Native SET commands change real attachment settings. ALTER SESSION RESET in T
rolls back and creates a new transaction, possibly behind the same interface,
and keeps T. In I it creates no application transaction; in E it receives 25P02.
P7 caches prepared statements and portals as described below; RESET invalidates
all of them even when the native transaction interface pointer is unchanged.
Savepoint names are not cached by the plugin.
Currently reported ParameterStatus values do not describe the reset options.
Native AUTO COMMIT and NO AUTO UNDO semantics are preserved; AUTO COMMIT may have
already committed changes that a later rollback cannot undo. These native SQL
characteristics do not acquire PostgreSQL transaction semantics.

SELECT tags count transmitted rows. DML tags use the engine's affected-record
count independently of RETURNING rows: `INSERT 0 n`, `UPDATE n`, `DELETE n`,
`MERGE n` and `UPDATE OR INSERT n`. EXECUTE BLOCK/PROCEDURE tags have no count.
DDL retains its operation and object, including RECREATE, CREATE OR ALTER and
DECLARE. SET GENERATOR and SET STATISTICS have standalone tags. These completion
strings are formed exclusively in the plugin.

The tested AUTO COMMIT profile uses SNAPSHOT. The native Debug engine currently
asserts for INSERT under READ COMMITTED READ CONSISTENCY AUTO COMMIT, independently
of PgWire (also reproduced with embedded isql). This engine limitation is recorded
with reproduction evidence in VALIDATION; the plugin passes transaction options
unchanged. Release behavior has not been tested.

Even an empty Query first goes through the engine prepare call. Only a syntax
error (`42601`) combined with the plugin classifier's entirely tokenless input
produces EmptyQueryResponse after query-resource cleanup, preserving I/T/E. Whitespace and fully
closed comments qualify; a standalone semicolon, unfinished string or
unterminated block comment does not. Native block comments do not nest. The
engine's existing diagnostics and isql continuation behavior are unchanged.

Statement-level timeout remains zero so native configuration and session
settings govern execution. `StatementTimeout` in firebird.conf is in seconds;
`ConnectionIdleTimeout` is in minutes. Zero means no corresponding engine cap.
Native idle shutdown closes the attachment and rolls back its transaction; the
next Query receives `57P05` and closes the connection. The plugin neither
reconnects nor issues periodic engine calls that would renew the idle timer.
Native statement timers cover DML, not a hard deadline for prepare, DDL or controls. Error
mapping in `ErrorStatus.cpp` examines the full structured status vector,
including explicit SQLSTATE fallback: parser errors use `42601`, missing
relations `42P01`, permission failures `42501`, unique violations `23505`, foreign
keys `23503`, and CHECK violations `23514`. Native `isc_not_valid` remains the
coarser `23000` because it also represents domain validation. Shutdown,
cancellation and resource failures take precedence. Transport failure
closes the stream without writing into a possibly partial frame.

The earlier P4 refactor passed the full POSIX build, four unit executables
(including 284 SQL classification checks), 12 Simple Query groups, seven lifecycle
groups, 52 startup checks and nine native checks. Real clients were C libpq 14.24
and psycopg2 2.9.11/libpq 17.6. Server traces showed no backend network connections.
The previous P5 revision passed its targeted Linux/Super checks: 14 transaction/session groups,
the full POSIX build, four unit executables (487 SQL classification checks),
and the Simple Query/lifecycle/startup/native regressions. The native AUTO COMMIT
limitation above remains; targeted P6/M1 acceptance excludes the separate native
ON CONNECT shutdown defect. Commands, evidence and coverage limits are maintained in
[`VALIDATION.md`](../pg_wire_protocol/VALIDATION.md). P7 Extended Query passed 16 targeted Linux/Super groups. Additional
codecs, remote TCP/TLS, cancellation messages and other server modes require
subsequent implementation and validation.


## PgWire Extended Query profile (P7)

P7 implements Parse, Bind, Describe statement/portal, Execute, Close, Flush and
Sync and passed 16 targeted Linux/Super groups; this does not establish the full M2 or a
Psycopg 3 application profile. Direct C libpq and raw-wire acceptance use native
`?` parameters, for example `SELECT CAST(? AS INTEGER) FROM RDB$DATABASE`, or no
parameters. PostgreSQL BEGIN and `$n` remain engine syntax errors. P7 leaves
grammar, public statement metadata and CLOOP unchanged.

P7 includes a narrow native cleanup fix in jrd.cpp release_attachment: all
attachment requests are marked unused before CMP_release can free a shared
Statement and its request clones. The original shutdown assertion at
Statement.cpp:781 was also reproduced over native TCP with wire endpoints
disabled. It is separate from the unresolved ON CONNECT assertion above.
Extended.cpp owns prepared objects and Extended message execution; Execute.cpp
provides the shared transaction policy and Simple Query path.

A prepared statement owns its original SQL, native template handle and metadata.
Bind prepares that same SQL into a separate native handle for each portal, so
bound values and cursors have independent execution state. Prepare uses the
current application transaction or NULL/system prepare; it does not start a
hidden application transaction. Named statements survive commit. Closing a
statement or replacing the unnamed statement preserves already bound portals.
Simple Query removes both unnamed objects. Portals end with the transaction;
Sync inside explicit T preserves them, including suspended cursors. RESET
invalidates all statements and portals even with an unchanged native transaction
interface pointer.

InputCodec validates parameter counts, format-count rules (0/1/N), format codes,
NULL, lengths and ranges before execution. Parse OID hints may cover a prefix of
the parameters; zero or an omitted hint uses native inference. Integer, boolean
and numeric OIDs must match exactly. Text OIDs 25/1042/1043 are interchangeable,
and an accepted explicit OID is retained in ParameterDescription. A mismatched
hint receives `0A000`; hints cannot repair missing engine inference or rewrite
SQL. Ambiguous expressions may therefore need a client-written CAST on the
parameter itself, such as `CAST(? AS INTEGER) + ID`; the plugin does not inject
that cast or pass OID hints into the native parser.
Text input/output supports SMALLINT/INTEGER/BIGINT, BOOLEAN, CHAR/VARCHAR and
NUMERIC/DECIMAL stored through INT64. Binary integer, boolean and string input
and output are supported; non-null binary NUMERIC remains `0A000` until P8.
Exact decimal input, including exponent notation, uses no floating point;
nonrepresentable scale/range receives `22003`. CHAR input uses varying metadata.
UTF8/NONE strings require valid UTF8, ASCII input additionally requires ASCII,
and embedded NUL is rejected.

Execute can suspend a cursor at maxRows and resume it later; zero reads the
remainder. Flush does not finish a transaction or send ReadyForQuery. Sync
finishes an implicit cycle and reports one ReadyForQuery, while preserving an
explicit transaction. CommandComplete for an Execute does not promise a commit
before Sync. SET TRANSACTION during an already open implicit cycle receives
`25001`: its TPB cannot replace the existing native transaction. Recoverable
Extended errors roll back an implicit cycle before Sync, invalidate all portals
and discard execution messages until Sync; explicit transactions remain in E.
During discard, bodies of recognized command messages are not decoded; Sync
and Terminate retain normal handling. Invalid frame lengths, truncated frames
and unknown tags remain fatal. Semantic Bind count/format errors are recoverable
`08P01` errors followed by discard-until-Sync.

Per-session limits are 128 statements, 128 portals and 8 MiB of accounted retained
SQL, metadata and buffers. Additional engine allocations are bounded by object
counts; the accounted budget is not a process RSS limit. Five codec unit
executables passed, including 149 input and 487 SQL classification checks;
seven server groups/58 pytest cases and nine native baseline checks also passed.
Acceptance results and remaining limitations are recorded in
[`VALIDATION.md`](../pg_wire_protocol/VALIDATION.md). Expanded codecs and binary
NUMERIC belong to P8; driver/application profiles belong to P9.
