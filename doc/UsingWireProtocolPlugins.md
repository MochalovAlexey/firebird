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
and acceptance. P2 adds host authentication and local attachments, and a separate
`PgWire` production module providing PostgreSQL startup. The targeted
Linux/Super P2 checks passed; SQL execution and TLS remain subsequent steps.
Recorded checks and coverage limits are in `pg_wire_protocol/VALIDATION.md`,
including the completed P1 run using the existing configuration API. Those P1
results are separate from the recorded P2 startup/authentication checks.

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
    IOTimeout = 30000
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
| `IOTimeout` | `30000` | Decimal milliseconds from 1 to 300000 |
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
sessions until a worker is available. `IOTimeout` bounds queue wait and each
transport operation. Protocol plugins must additionally bound complete frames
and startup sequences across partial operations. PgWire uses this value for
whole-frame deadlines and one shared startup network deadline, including the
encryption refusals, password exchange and startup replies.

## Interface responsibilities

| Interface | Implemented by | Responsibility |
|---|---|---|
| `IWireProtocol` | Plugin | Create sessions for one configured endpoint |
| `IWireSession` | Plugin | Execute one session on a host worker and accept stop requests |
| `IWireConnection` | Host | Byte transport, deadlines, shutdown and peer information |
| `IWireServerHost` | Host | Per-connection stopping state, diagnostics, authentication and local attachments |

The generated versions are 4 for `IWireProtocol` and `IWireServerHost`, and 3 for
`IWireSession` and `IWireConnection`. These numbers include inherited interface
versions. P2 appends `attachDatabase` and `detachDatabase` to the host interface;
PgWire checks its version before calling them. TLS upgrade requires a later
transport interface version.

Database access uses the host's authentication and local attachment service.
Plugins must not create a secondary native network connection. Any SQL executor
added later must pass SQL unchanged to the Firebird parser; these interfaces are
not a SQL rewriting or classification layer.

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
attachment ownership. It must not turn plugin `stop()` into a blocking
cancellation or detach operation. Server shutdown must allow providers to
terminate engine operations; an unconditional join before provider shutdown can
deadlock that process. The synchronous authentication and attach callbacks cannot
be forcibly preempted by the startup network deadline, particularly before an
attachment handle exists. Stop checks between callbacks and provider shutdown
remain necessary; the codec's deadline is not a hard upper bound on those calls.

The Linux host registers `preproviders` and `postproviders` shutdown callbacks.
The first stops accept, wakes connections and requests session stop without
joining workers. After provider shutdown, all endpoints share one five-second
deadline for acceptor and worker completion. Owners are released only after all
callbacks finish. If the deadline expires, shutdown reports failure and retains
owners rather than unloading code still in use. This deadline does not make an
arbitrarily blocking plugin callback safe.

## Transport I/O

One worker serializes the connection's `read` and `write` calls. Both methods
take an explicit buffer size and a timeout in milliseconds. Buffers are borrowed
only for the duration of the call. A nonzero size requires a valid buffer.

| Outcome | Contract |
|---|---|
| Read or write makes progress | Return a count between 1 and the requested size immediately; partial progress is valid |
| Read reaches EOF with nonzero size | Return 0 with no error status |
| Write with nonzero size | Returning 0 without an error is invalid |
| Zero-size operation on an open connection | Successful no-op returning 0; the buffer is not accessed |
| Timeout, shutdown or I/O failure | Report an error through status and return 0; progress returned by earlier successful calls remains valid |

The caller checks status before interpreting a zero result. Timeout and shutdown
are not EOF. A failing call does not claim successful progress; after a terminal
transport error, it must not be retried as if no bytes could have reached the
peer.

`timeoutMs == 0` selects the host's finite default. A positive timeout may shorten
that limit, never lengthen it. Retries inside one I/O operation, including EINTR
handling, must retain the original deadline. Protocol framing also needs an
overall startup/frame deadline across partial reads to bound slow input.

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
this API nor the generic host imposes PgWire's future single-transaction rule on
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

Real transport acceptance must additionally cover timeout values, deadline
clamping, zero-length operations and OS I/O failures. The mock stream used by
the loading smoke does not validate those transport semantics.

Observe the order of session destruction, plugin destruction and module cleanup.
The plugin manager may delay module unloading; immediate disappearance from the
process map is not the ownership contract. Test configuration changes and global
shutdown in isolated processes because the plugin manager and configuration
caches are process-wide.

`wire_host_smoke.py` prepares an isolated runtime and starts a real Super server
with native TCP and two aliases of the test module. Its labelled byte echo
checks target listeners, queue limits, timeout and shutdown. The test module is
built under `tests`, is not installed as a production plugin and performs no SQL.
Only separately recorded successful runs establish acceptance; these fixtures
do not establish PostgreSQL or authentication support on the additional ports.

## PgWire startup profile (P2, Linux/Super checks passed)

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
    IOTimeout = 30000
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
`I`. Every structurally valid Query receives `0A000` and ReadyForQuery `I`,
including empty or incompatible SQL. No SQL text is parsed or classified by the
wire layer. P3 adds execution through the Firebird parser and the first M0 SELECT.

`ProtocolTest.cpp` tests framing and literal response bytes with partial I/O.
`pgwire_startup_smoke.py` exercises the loaded module, raw PostgreSQL startup,
authentication, native monitoring and session cleanup on an isolated runtime.
The targeted Linux/Super run passed all 52 startup harness checks using libpq
14.24. Results, commands and remaining coverage limits are recorded in
[`VALIDATION.md`](../pg_wire_protocol/VALIDATION.md). Startup and the test echo do
not constitute M0; engine SQL execution starts in P3.
