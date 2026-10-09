#!/usr/bin/env python3
"""Run the real wire plugin ABI smoke with isolated config and a mock stream.

Build with `make TARGET=Debug wire_tests` first. No server or TCP listener is
started here; PostgreSQL and native network acceptance have separate harnesses.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def run(firebird_dir, work_dir):
    executable = firebird_dir / "tests" / "wire_plugin_smoke"
    modules = list((firebird_dir / "tests").glob("libWireTestPlugin.*"))
    modules = [path for path in modules if path.suffix in (".so", ".dylib")]
    if not executable.is_file() or len(modules) != 1:
        raise RuntimeError("build the wire_tests target before running this harness")
    module = modules[0]
    if any(char in str(module) for char in "\r\n#"):
        raise RuntimeError("module path cannot be represented in plugins.conf")

    # Only these private configuration files are written. Libraries and the
    # executable are read directly from the selected build, never installed.
    (work_dir / "locks").mkdir()
    (work_dir / "tmp").mkdir()
    events_path = work_dir / "events.log"
    (work_dir / "firebird.conf").write_text(
        "Providers = Remote\nWireProtocolPlugins =\nWireProtocolPorts =\n",
        encoding="utf-8")
    modes = {
        "First": ("first", "echo"),
        "Second": ("second", "echo"),
        "FactoryError": ("factory_error", "factory_error"),
        "FactoryNull": ("factory_null", "factory_null"),
        "CreateError": ("create_error", "create_error"),
        "CreateNull": ("create_null", "create_null"),
        "CreateErrorObject": ("create_error_object", "create_error_object"),
        "RunError": ("run_error", "run_error"),
        "OldProtocol": ("old_protocol", "protocol_old"),
        "OldSession": ("old_session", "session_old"),
        "Blocked": ("blocked", "echo"),
        "PreStopped": ("pre_stopped", "echo"),
    }
    sections = []
    for alias, (label, mode) in modes.items():
        sections.append(
            f"Plugin = {alias}\n{{\nModule = {module}\nRegisterName = WireTest\n"
            f"Config = {alias}Config\n}}\n"
            f"Config = {alias}Config\n{{\nLabel = {label}\nMode = {mode}\n}}\n")
    sections.append(
        f"Plugin = WrongRegistration\n{{\nModule = {module}\n"
        "RegisterName = NoSuchFactory\n}\n"
        "Plugin = RequiredMissing\n{\nModule = "
        f"{work_dir / 'does_not_exist'}\nRequired = true\n}}\n")
    (work_dir / "plugins.conf").write_text("\n".join(sections), encoding="utf-8")

    env = os.environ.copy()
    for name in ("ISC_USER", "ISC_PASSWORD", "ISC_ROLE"):
        env.pop(name, None)
    env.update(FIREBIRD=str(work_dir), FIREBIRD_LOCK=str(work_dir / "locks"),
               FIREBIRD_TMP=str(work_dir / "tmp"), FIREBIRD_MSG=str(firebird_dir),
               FB_WIRE_TEST_EVENTS=str(events_path),
               LD_LIBRARY_PATH=str(firebird_dir / "lib"),
               DYLD_LIBRARY_PATH=str(firebird_dir / "lib"))
    result = subprocess.run([str(executable)], env=env, cwd=work_dir,
                            capture_output=True, text=True, timeout=20)
    (work_dir / "stdout.log").write_text(result.stdout, encoding="utf-8")
    (work_dir / "stderr.log").write_text(result.stderr, encoding="utf-8")
    events = events_path.read_text(encoding="utf-8").splitlines() if events_path.exists() else []
    if result.returncode:
        raise RuntimeError(f"smoke exited {result.returncode}: {result.stderr.strip()}\n"
                           + "\n".join(events))
    for name in ("module.load", "module.cleanup", "module.unload"):
        if events.count(name) != 1:
            raise RuntimeError(f"expected one {name}, got events: {events}")
    for alias, (label, mode) in modes.items():
        if mode.startswith("factory_"):
            continue
        if events.count(f"{label}:plugin.create") != 1 or events.count(f"{label}:plugin.destroy") != 1:
            raise RuntimeError(f"plugin ownership mismatch for {alias}: {events}")
        destroy = events.index(f"{label}:plugin.destroy")
        if mode not in ("create_error", "create_null", "protocol_old"):
            session_destroy = events.index(f"{label}:session.destroy")
            if session_destroy >= destroy:
                raise RuntimeError(f"plugin released before its session for {alias}")
        if destroy >= events.index("module.cleanup"):
            raise RuntimeError(f"module cleaned before final plugin release for {alias}")
    if "old_protocol:session.create" in events or "old_session:session.run" in events:
        raise RuntimeError("old interface was used beyond its supported methods")
    if events.index("module.cleanup") >= events.index("module.unload"):
        raise RuntimeError("module unloaded before PluginManager cleanup")
    if not (events.index("second:session.run") < events.index("first:plugin.destroy")
            < events.index("second:session.stopped")):
        raise RuntimeError("first alias was not released while the second session was active")
    # Darwin intentionally retains modules (PluginManager.cpp, GH-7112), so its
    # module destructor may run at process exit. Linux must unload at shutdown.
    if sys.platform != "darwin" and not (
            events.index("host.before_shutdown") < events.index("module.cleanup")
            < events.index("module.unload") < events.index("host.after_shutdown")):
        raise RuntimeError("module was not unloaded by the explicit shutdown")
    summary = {"result": "passed", "transport": "mock byte stream (no TCP)",
               "plugin_manager": "real fbclient and dynamically loaded shared module",
               "same_module_aliases": ["First", "Second"], "missing_middle": "rejected",
               "lifecycle": "sessions released before plugins, module cleaned and unloaded",
               "work_dir": str(work_dir)}
    (work_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firebird-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, help="new directory to retain config and diagnostics")
    args = parser.parse_args()
    firebird_dir = args.firebird_dir.resolve()
    if args.work_dir:
        work_dir = args.work_dir.absolute()
        work_dir.mkdir(parents=True, exist_ok=False)
        run(firebird_dir, work_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="firebird-wire-plugin-") as path:
            run(firebird_dir, Path(path))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"wire plugin smoke failed: {error}", file=sys.stderr)
        sys.exit(1)
