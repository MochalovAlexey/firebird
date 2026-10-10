#!/usr/bin/env python3
"""Run SQL over PostgreSQL TCP using the installed libpq and Python stdlib.

Examples:
  python3 pgwire_client.py -d pgwire_test -U myuser -P mypassword
  python3 pgwire_client.py -d pgwire_test -U myuser -P mypassword -c 'SELECT 42 FROM RDB$DATABASE'

The database name/path is sent verbatim. The current Firebird PgWire server
requires an alias configured in databases.conf and allowed by DatabaseAliases.

Each -c or -f is one complete request, in argument order, on one connection.
Interactive input is sent with a standalone \\g; semicolons do not submit or
split SQL. No initialization SQL, BEGIN or automatic retry is sent.
"""

import argparse
import ctypes
import ctypes.util
import os
from pathlib import Path
import sys


class ClientError(Exception):
    """A local setup, transport or unsupported client operation failed."""


def decode(value):
    return value.decode("utf-8", errors="backslashreplace") if value else ""


def visible(value):
    # Keep cell boundaries and terminal output intact without changing SQL.
    return "".join(char if char.isprintable() else f"\\x{ord(char):02x}" for char in value)


class Connection:
    def __init__(self, args, password):
        self.connection = None
        self.password = password
        name = args.libpq or ctypes.util.find_library("pq") or ("libpq.dll" if os.name == "nt" else "libpq.so.5")
        try:
            self.pq = ctypes.CDLL(name)
            strings = ctypes.POINTER(ctypes.c_char_p)
            pointer = ctypes.c_void_p
            integer = ctypes.c_int
            signatures = {
                "PQconnectdbParams": (pointer, [strings, strings, integer]),
                "PQfinish": (None, [pointer]),
                "PQstatus": (integer, [pointer]),
                "PQtransactionStatus": (integer, [pointer]),
                "PQprotocolVersion": (integer, [pointer]),
                "PQlibVersion": (integer, []),
                "PQerrorMessage": (ctypes.c_char_p, [pointer]),
                "PQexec": (pointer, [pointer, ctypes.c_char_p]),
                "PQclear": (None, [pointer]),
                "PQresultStatus": (integer, [pointer]),
                "PQresultErrorField": (ctypes.c_char_p, [pointer, integer]),
                "PQresultErrorMessage": (ctypes.c_char_p, [pointer]),
                "PQnfields": (integer, [pointer]),
                "PQntuples": (integer, [pointer]),
                "PQfname": (ctypes.c_char_p, [pointer, integer]),
                "PQftype": (ctypes.c_uint, [pointer, integer]),
                "PQgetisnull": (integer, [pointer, integer, integer]),
                "PQgetlength": (integer, [pointer, integer, integer]),
                "PQgetvalue": (pointer, [pointer, integer, integer]),
                "PQcmdStatus": (ctypes.c_char_p, [pointer]),
            }
            for symbol, (result, arguments) in signatures.items():
                function = getattr(self.pq, symbol)
                function.restype, function.argtypes = result, arguments
        except (OSError, AttributeError) as error:
            raise ClientError(f"Cannot load libpq: {error}. Install its runtime or use --libpq PATH.") from error

        options = {"host": args.host, "port": str(args.port), "dbname": args.database,
                   "user": args.user, "password": password, "connect_timeout": str(args.connect_timeout),
                   "sslmode": "disable", "gssencmode": "disable", "client_encoding": "UTF8",
                   "application_name": "pgwire_client", "target_session_attrs": "any"}
        for key, value in options.items():
            if "\0" in value:
                raise ClientError(f"NUL is not allowed in connection parameter {key}")
        keys = (ctypes.c_char_p * (len(options) + 1))(*(key.encode() for key in options), None)
        values = (ctypes.c_char_p * (len(options) + 1))(*(value.encode("utf-8") for value in options.values()), None)
        # expand_dbname=0 keeps the database name/path literal, never a conninfo/URI.
        # Keep explicit endpoint/password arguments ahead of ambient libpq defaults.
        # This CLI is single-threaded; restore the environment after connecting.
        defaults = {key: os.environ.pop(key) for key in ("PGHOSTADDR", "PGSERVICE", "PGPASSWORD") if key in os.environ}
        try:
            self.connection = self.pq.PQconnectdbParams(keys, values, 0)
        finally:
            os.environ.update(defaults)
        if not self.connection:
            raise ClientError("libpq could not allocate a connection")
        if self.pq.PQstatus(self.connection) != 0:
            message = self.redact(decode(self.pq.PQerrorMessage(self.connection)).strip())
            self.close()
            raise ClientError(message or "Connection failed")

    def redact(self, message):
        return message.replace(self.password, "[REDACTED]") if self.password else message

    def close(self):
        if self.connection:
            self.pq.PQfinish(self.connection)
            self.connection = None

    def transaction(self):
        return {0: "idle (I)", 1: "active", 2: "in transaction (T)",
                3: "failed transaction (E)", 4: "unknown"}.get(
                    self.pq.PQtransactionStatus(self.connection), "unknown")

    def cell(self, result, row, column):
        if self.pq.PQgetisnull(result, row, column):
            return "NULL"
        length = self.pq.PQgetlength(result, row, column)
        address = self.pq.PQgetvalue(result, row, column)
        return visible(decode(ctypes.string_at(address, length)))

    def table(self, result):
        columns, rows = self.pq.PQnfields(result), self.pq.PQntuples(result)
        names = [visible(decode(self.pq.PQfname(result, col))) for col in range(columns)]
        widths = [min(80, len(name)) for name in names]
        for row in range(rows):
            for col in range(columns):
                widths[col] = max(widths[col], min(80, len(self.cell(result, row, col))))
        print(" | ".join(name.ljust(width) for name, width in zip(names, widths)))
        print("-+-".join("-" * width for width in widths))
        for row in range(rows):
            print(" | ".join(self.cell(result, row, col).ljust(widths[col]) for col in range(columns)))
        print(f"({rows} {'row' if rows == 1 else 'rows'})")
        print("OIDs: " + ", ".join(str(self.pq.PQftype(result, col)) for col in range(columns)))

    def execute(self, sql):
        if "\0" in sql:
            print("Input error: SQL contains NUL; request was not sent.", file=sys.stderr)
            return False
        # PQexec sends a Simple Query exactly as supplied; it adds no BEGIN.
        result = self.pq.PQexec(self.connection, sql.encode("utf-8"))
        if not result:
            raise ClientError(self.redact(decode(self.pq.PQerrorMessage(self.connection)).strip()) or
                              "libpq returned no result")
        try:
            status = self.pq.PQresultStatus(result)
            if status in (0, 1, 2):  # EMPTY_QUERY, COMMAND_OK, TUPLES_OK
                if status == 2:
                    self.table(result)
                tag = decode(self.pq.PQcmdStatus(result))
                print("Command: " + visible(tag or "(empty query)"))
                success = True
            elif status in (5, 6, 7):  # BAD_RESPONSE, NONFATAL_ERROR, FATAL_ERROR
                state = decode(self.pq.PQresultErrorField(result, ord("C")))
                message = decode(self.pq.PQresultErrorField(result, ord("M")))
                if not message:
                    message = decode(self.pq.PQresultErrorMessage(result)).strip()
                print(f"ERROR [SQLSTATE {visible(state or 'unavailable')}]: {visible(self.redact(message))}",
                      file=sys.stderr)
                success = False
            else:
                raise ClientError(f"Unsupported libpq result status {status}; closing connection")
            if self.pq.PQstatus(self.connection) != 0:
                raise ClientError("Connection closed; query was not retried")
            print("Transaction: " + self.transaction(), flush=True)
            return success
        finally:
            self.pq.PQclear(result)


HELP = """Enter SQL, then use a separate line containing \\g to send it.
Semicolons remain SQL text. All requests use the same TCP connection.
  \\g  execute buffer    \\p  show buffer    \\r  clear buffer
  \\q  quit              \\?  help
"""


def interactive(connection, stop_on_error):
    print(HELP, file=sys.stderr)
    lines = []
    while True:
        print("...> " if lines else f"pgwire [{connection.transaction()}]> ", end="", file=sys.stderr, flush=True)
        line = sys.stdin.buffer.readline().decode("utf-8")
        command = line.strip()
        if not line or command == "\\q":
            if lines:
                print("Unsent SQL discarded.", file=sys.stderr)
            return 0
        if command == "\\g":
            sql = "".join(lines)
            lines.clear()
            if not connection.execute(sql) and stop_on_error:
                return 1
        elif command == "\\r":
            lines.clear()
        elif command == "\\p":
            print("".join(lines), end="", file=sys.stderr)
        elif command == "\\?":
            print(HELP, file=sys.stderr)
        else:
            lines.append(line)


class RequestAction(argparse.Action):
    def __call__(self, parser, namespace, value, option_string=None):
        requests = list(getattr(namespace, self.dest) or [])
        requests.append(("file" if option_string in ("-f", "--file") else "command", value))
        setattr(namespace, self.dest, requests)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-H", "--host", default=os.environ.get("PGHOST", "127.0.0.1"), help="TCP host (default: 127.0.0.1)")
    parser.add_argument("-p", "--port", type=int, default=os.environ.get("PGPORT", "15432"), help="PgWire TCP port (default: 15432)")
    parser.add_argument("-d", "--database", default=os.environ.get("PGDATABASE"), metavar="DATABASE",
                        help="database name/path sent verbatim; current server requires an alias; or PGDATABASE")
    parser.add_argument("-U", "--user", default=os.environ.get("PGUSER"), help="Firebird user, or PGUSER")
    parser.add_argument("-P", "--password", "-W", default=os.environ.get("PGPASSWORD"), metavar="PASSWORD",
                        help="password value, or PGPASSWORD; no interactive password prompt")
    parser.add_argument("-c", "--command", action=RequestAction, dest="requests", metavar="SQL", help="send one request; repeatable")
    parser.add_argument("-f", "--file", action=RequestAction, dest="requests", metavar="FILE", help="send a whole UTF8 file as one request (- for stdin)")
    parser.add_argument("-i", "--interactive", action="store_true", help="interactive mode, also after -c/-f")
    parser.add_argument("--stop-on-error", action="store_true", help="exit on the first SQL error")
    parser.add_argument("--connect-timeout", type=int, default=5, metavar="SECONDS")
    parser.add_argument("--libpq", metavar="PATH", help="explicit libpq shared library path")
    args = parser.parse_args()
    if not args.database or not args.user:
        parser.error("provide -d DATABASE and -U USER, or PGDATABASE and PGUSER")
    if args.password is None:
        parser.error("provide -P PASSWORD / --password PASSWORD, or set PGPASSWORD")
    if not args.host or args.host.startswith(("/", "@")) or "," in args.host:
        parser.error("--host must be one TCP hostname or address, not a Unix socket or host list")
    if not 1 <= args.port <= 65535 or not 1 <= args.connect_timeout <= 300:
        parser.error("port must be 1..65535 and connect timeout 1..300 seconds")
    return args


def main():
    args = arguments()
    connection = None
    try:
        connection = Connection(args, args.password)
        use_interactive = args.interactive or (not args.requests and sys.stdin.isatty())
        if use_interactive:
            print(f"Connected to {visible(args.host)}:{args.port}/{visible(args.database)} as {visible(args.user)}; "
                  f"libpq {connection.pq.PQlibVersion()}, protocol {connection.pq.PQprotocolVersion(connection.connection)}.",
                  file=sys.stderr)
        failed = False
        requests = args.requests or ([] if use_interactive else [("file", "-")])
        for kind, value in requests:
            if kind == "command":
                sql = value
            else:
                # Read bytes to preserve CRLF and every SQL byte in UTF8 input.
                sql = (sys.stdin.buffer.read() if value == "-" else Path(value).read_bytes()).decode("utf-8")
            if not connection.execute(sql):
                failed = True
                if args.stop_on_error:
                    return 1
        if use_interactive:
            result = interactive(connection, args.stop_on_error)
            return result or int(failed)
        return int(failed)
    except KeyboardInterrupt:
        print("Interrupted; closing connection.", file=sys.stderr)
        return 130
    except (ClientError, OSError, UnicodeError) as error:
        message = str(error)
        if connection:
            message = connection.redact(message)
        print("Client error: " + visible(message), file=sys.stderr)
        return 2
    finally:
        if connection:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
