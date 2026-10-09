# Server wire protocol plugins

Wire protocol plugins extend the network protocols understood by the Firebird
server. They are loaded into the server process through the existing plugin
manager under `IPluginManager::TYPE_WIRE_PROTOCOL`. Native Firebird connections
continue to use their existing frontend and do not use these interfaces.

This first interface revision provides plugin creation, session ownership and
host transport contracts. TCP listener startup, database authentication,
attachments and a production PostgreSQL protocol plugin are separate subsequent
implementation steps. Registering `WireProtocolPlugins` and `WireProtocolPorts`
currently supplies configuration infrastructure; it does not start a listener.

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

The host must validate endpoint configuration before creating listeners. The
current loading adapter validates its supplied name and port, but does not
implement Bind/TLS settings or listener creation. Future transport settings
belong in named per-alias configuration blocks, without additional positional
lists. The existing configuration manager can read a named plugin configuration
before loading its module. Explicit missing Config references and the existing
differences in ConfigFile resolution need validation by that host layer.

## Interface responsibilities

| Interface | Implemented by | Responsibility |
|---|---|---|
| `IWireProtocol` | Plugin | Create sessions for one configured endpoint |
| `IWireSession` | Plugin | Execute one session on a host worker and accept stop requests |
| `IWireConnection` | Host | Byte transport, deadlines, shutdown and peer information |
| `IWireServerHost` | Host | Per-connection stopping state and bounded diagnostic logging |

The initial generated versions are 4 for `IWireProtocol` and 3 for the other
three interfaces. These numbers include their inherited interface versions.
The host verifies the required interface version before invoking its methods.
Authentication and TLS upgrade methods will be appended in later CLOOP interface
versions after their contracts are established.

`IWireServerHost` grants no database access in this revision. Plugins must not
create a secondary native network connection as a substitute for the future
host authentication and local attachment service. Any SQL executor added later
must pass SQL unchanged to the Firebird parser; this interface is not a SQL
rewriting or classification layer.

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

The host adapter calls session `stop()` and connection `shutdown()`. Transport
shutdown wakes blocked I/O independently of the plugin's stop implementation.
The caller must not hold a registry lock across callbacks, object destruction or
plugin release. Copy or retain the necessary owners under the lock, then perform
those operations after releasing it.

Future engine cancellation is a host responsibility with separately protected
attachment ownership. It must not turn `stop()` into a blocking cancellation or
detach operation. Server shutdown must allow providers to terminate active SQL;
an unconditional join before provider shutdown can deadlock that process.

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

`log(level, length, message)` accepts `LOG_INFO`, `LOG_WARNING` or `LOG_ERROR`.
The byte buffer is borrowed for the call; the length is explicit. The host bounds
and escapes the message and adds endpoint/session identity. It must not treat
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
