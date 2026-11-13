# Starting the prepared Gilev GUI stand

Use the existing Linux stand at `/home/aleksey/dev/1c/firebird-gilev`.
Its Firebird database, compatibility SQL, imported data and registered 1C cluster
must already exist. The launcher does not create databases, register infobases,
install SQL, migrate data or rebuild binaries.

Add the installed 1C directory to the existing private `connection.json`:

```json
"one_c_dir": "/opt/1cv8/x86_64/8.5.4.1306"
```

Alternatively pass `--one-c-dir /opt/1cv8/x86_64/8.5.4.1306` to each command.
Keep the existing configuration and credentials private; the launcher neither
uses nor prints the database password. It uses the saved cluster registration.

From this repository, in the normal graphical login session:

```sh
python3 pg_wire_protocol/1c/run_gilev.py start \
  --stand-dir /home/aleksey/dev/1c/firebird-gilev

python3 pg_wire_protocol/1c/run_gilev.py status \
  --stand-dir /home/aleksey/dev/1c/firebird-gilev

python3 pg_wire_protocol/1c/run_gilev.py stop \
  --stand-dir /home/aleksey/dev/1c/firebird-gilev
```

`start` starts Firebird, the existing 1C cluster, RAS, and the **ordinary thick
client**. Exact matching running processes are reused. The current ports are
Firebird native 23050, PgWire 25432, agent 12540, cluster 12541, RAS 12545;
working processes use `12560:12589` and RAS monitoring uses 12555. The latter two
defaults can be overridden by `process_port_range` and `monitor_port` in the
same JSON. Other endpoints and the infobase name come from that JSON.

The GUI connects to `127.0.0.1:12541/GilevFirebird`. Start the benchmark yourself
using its normal GUI controls. Opening the client does not click the test button
or submit results publicly. Successful opening is separate from completion of
the benchmark; current results and limitations belong in the stage report.
See [GILEV_RUN.md](GILEV_RUN.md) for the tested build, recorded attempts and
acceptance evidence.

To add this existing base manually in the normal 1C launcher, choose **Add →
Add an existing infobase → On a 1C:Enterprise server**. Set the server to
`127.0.0.1:12541` and the infobase name to `GilevFirebird`. Choose the thick client
and add `/RunModeOrdinaryApplication` to its startup parameters. This registers
the connection in your launcher list; do not choose creation of a new database.
The script does not install the platform or modify a global `ibases.v8i`.

`status` only checks the four process identities. It does not query the database,
create files or test application health. The launcher validates the executable,
exact arguments, user, isolated process session and recorded process start time.
Existing PID files can be adopted only when these executable/argument checks
match; start times are then recorded in `launcher-state.json`.

`stop` sends SIGTERM in reverse startup order and waits for each process session
to finish. For the cluster, this includes verified processes in ragent's private
session and process group, using the same 1C installation. Each target is held
by a pidfd before signals are sent. It does not send SIGKILL. If a component or child remains alive, it
stops the shutdown sequence, keeping downstream services available. Close the
GUI normally after finishing the test before stopping the stand. A startup
failure can leave already started components running; inspect them with `status`
and use `stop` when appropriate.

This stand uses a regular 1C server cluster. An `ibsrv` instance must not serve
the same database simultaneously; the ordinary application required by this
benchmark is outside the standalone server's supported application profile.
