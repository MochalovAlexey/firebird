#!/usr/bin/env python3
"""Install all PgWire compatibility SQL on an existing Firebird database."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


# Keep the dependency order explicit; append future installers here.
SCRIPTS = (
    "sql/install_namespace_registry.sql",
    "compat.sql",
    "sql/install_namespace_catalog.sql",
    "sql/install_class_catalog.sql",
    "sql/install_pg_names.sql",
    "sql/install_relation_visibility.sql",
    "sql/install_temporary_relations.sql",
)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Initial installation only. Each SQL file commits separately; "
               "installation stops on the first error. Not an upgrade or repair tool.")
    parser.add_argument("-d", "--database", required=True,
                        help="Native Firebird connection string, alias or database file path")
    parser.add_argument("-U", "--user", default=os.environ.get("ISC_USER", "SYSDBA"),
                        help="Database owner/admin (default: ISC_USER or SYSDBA)")
    parser.add_argument("-P", "--password",
                        help="Firebird password (default: inherited ISC_PASSWORD)")
    parser.add_argument("--isql", default="isql",
                        help="Native isql from this Firebird fork (default: isql in PATH)")
    args = parser.parse_args()
    if not args.database or args.database.startswith("-"):
        parser.error("database must be a connection string, alias or file path")

    isql = shutil.which(args.isql)
    if isql is None:
        parser.error(f"native isql executable not found: {args.isql}")
    base = Path(__file__).resolve().parent
    for name in SCRIPTS:
        if not (base / name).is_file():
            parser.error(f"SQL file not found: {base / name}")

    env = os.environ.copy()
    env["ISC_USER"] = args.user
    if args.password is not None:
        env["ISC_PASSWORD"] = args.password

    for index, name in enumerate(SCRIPTS, 1):
        print(f"[{index}/{len(SCRIPTS)}] {name}", flush=True)
        # -noautocommit also covers compat.sql, which does not set AUTODDL OFF.
        # Do not enter interactive mode after the input file has been consumed.
        result = subprocess.run(
            [isql, "-quiet", "-bail", "-noautocommit", "-charset", "UTF8",
             "-sql_dialect", "3", "-input", str(base / name), args.database],
            env=env, stdin=subprocess.DEVNULL)
        if result.returncode:
            print(f"Installation stopped at {name} (isql exit {result.returncode}). "
                  "Earlier scripts remain committed.", file=sys.stderr)
            return 1

    print("All PgWire compatibility SQL scripts installed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OSError as exc:
        print(f"Installation failed: {exc}. Earlier scripts may remain committed.",
              file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Installation interrupted. Earlier scripts may remain committed.",
              file=sys.stderr)
        sys.exit(130)
