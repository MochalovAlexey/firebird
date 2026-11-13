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
    "sql/install_types_and_expressions.sql",
    "sql/install_database_catalog.sql",
    "sql/install_advisory_locks.sql",
    "sql/install_type_catalog.sql",
    "sql/install_temp_services.sql",
    "sql/install_fulleq.sql",
    "sql/install_1c_functions.sql",
    "sql/install_number_format.sql",
    "sql/install_tables_catalog.sql",
    "sql/install_attribute_catalog.sql",
    "sql/install_index_catalog.sql",
)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="By default, install a fresh profile: each SQL file commits separately. "
               "--upgrade-from-app15 starts with the APP-16 migration; "
               "--upgrade-from-app16 installs catalogs, advisory locks and temporary-table services. "
               "--upgrade-from-app17 adds the fulleq profile and native 1C functions. "
               "Installation stops on the first error; existing objects are not repaired.")
    parser.add_argument("-d", "--database", required=True,
                        help="Native Firebird connection string, alias or database file path")
    parser.add_argument("-U", "--user", default=os.environ.get("ISC_USER", "SYSDBA"),
                        help="Database owner/admin (default: ISC_USER or SYSDBA)")
    parser.add_argument("-P", "--password",
                        help="Firebird password (default: inherited ISC_PASSWORD)")
    parser.add_argument("--isql", default="isql",
                        help="Native isql from this Firebird fork (default: isql in PATH)")
    upgrade = parser.add_mutually_exclusive_group()
    upgrade.add_argument("--upgrade-from-app15", action="store_true",
                         help="Upgrade APP-15 after native gfix -upgrade")
    upgrade.add_argument("--upgrade-from-app16", action="store_true",
                         help="Install catalogs, advisory locks and temporary-table services on APP-16")
    upgrade.add_argument("--upgrade-from-app17", action="store_true",
                         help="Add the fulleq profile and native 1C functions to an initial APP-17 installation")
    parser.add_argument("--locale", choices=("ru_RU.UTF-8",),
                        help="Configure native ICU ru_RU as the default UTF8 collation before "
                             "importing application tables (existing columns keep their collation)")
    args = parser.parse_args()
    scripts = (SCRIPTS[7:] if args.upgrade_from_app15 else SCRIPTS[8:] if args.upgrade_from_app16
               else SCRIPTS[12:] if args.upgrade_from_app17 else SCRIPTS)
    if args.locale:
        if args.upgrade_from_app17:
            parser.error("--locale must be configured before importing application tables")
        scripts = list(scripts)
        scripts.insert(scripts.index("sql/install_database_catalog.sql"), "sql/configure_ru_collation.sql")
    if not args.database or args.database.startswith("-"):
        parser.error("database must be a connection string, alias or file path")

    isql = shutil.which(args.isql)
    if isql is None:
        parser.error(f"native isql executable not found: {args.isql}")
    base = Path(__file__).resolve().parent
    for name in scripts:
        if not (base / name).is_file():
            parser.error(f"SQL file not found: {base / name}")

    env = os.environ.copy()
    env["ISC_USER"] = args.user
    if args.password is not None:
        env["ISC_PASSWORD"] = args.password

    for index, name in enumerate(scripts, 1):
        print(f"[{index}/{len(scripts)}] {name}", flush=True)
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

    print("Requested PgWire compatibility SQL scripts installed.")
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
