#!/usr/bin/env python3
"""P5 real TCP transaction/session acceptance on a new private Firebird runtime."""

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import time

from pgwire_query_smoke import QueryHarness
from pgwire_startup_smoke import Client, StartupHarness, error_fields, packet
from wire_host_smoke import require, reserve_ports


class TransactionClient(Client):
    """Decode actual ReadyForQuery I/T/E, with no substitution of wire state."""
    def query_response(self):
        result = {'columns': None, 'rows': [], 'command': None, 'error': None, 'empty': False}
        for _ in range(100000):
            kind, payload = self.message()
            if kind == b'T':
                require(result['columns'] is None and len(payload) >= 2, 'invalid RowDescription')
                columns, offset = [], 2
                for _ in range(struct.unpack('!H', payload[:2])[0]):
                    end = payload.find(b'\0', offset)
                    require(end >= offset and end + 19 <= len(payload), 'truncated metadata')
                    table, attribute, oid, size, modifier, format_code = struct.unpack('!IhIhih', payload[end + 1:end + 19])
                    columns.append({'name': payload[offset:end].decode('utf-8'), 'table': table,
                                    'attribute': attribute, 'oid': oid, 'size': size,
                                    'modifier': modifier, 'format': format_code})
                    offset = end + 19
                require(offset == len(payload), 'trailing metadata bytes')
                result['columns'] = columns
            elif kind == b'D':
                require(result['columns'] is not None and result['command'] is None and result['error'] is None,
                        'DataRow outside result')
                require(len(payload) >= 2 and struct.unpack('!H', payload[:2])[0] == len(result['columns']),
                        'DataRow column count differs')
                row, offset = [], 2
                for _ in result['columns']:
                    require(offset + 4 <= len(payload), 'truncated DataRow length')
                    length = struct.unpack('!i', payload[offset:offset + 4])[0]
                    offset += 4
                    require(length >= -1 and offset + max(0, length) <= len(payload), 'invalid DataRow length')
                    row.append(None if length == -1 else payload[offset:offset + length].decode('utf-8'))
                    offset += max(0, length)
                require(offset == len(payload), 'trailing DataRow bytes')
                result['rows'].append(row)
            elif kind == b'C':
                require(result['command'] is None and result['error'] is None and payload.endswith(b'\0'),
                        'invalid CommandComplete')
                result['command'] = payload[:-1].decode('utf-8')
            elif kind == b'E':
                require(result['error'] is None and result['command'] is None, 'error after completion')
                result['error'] = error_fields(payload)
                require(result['error']['S'] != 'FATAL', f"unexpected FATAL: {result['error']}")
            elif kind == b'I':
                require(not payload and result['columns'] is None and result['command'] is None and
                        result['error'] is None and not result['empty'], 'invalid EmptyQueryResponse')
                result['empty'] = True
            elif kind == b'N':
                error_fields(payload)  # A control in I may produce a NoticeResponse.
            elif kind == b'S':
                require(payload.endswith(b'\0') and payload.count(b'\0') == 2, 'invalid ParameterStatus')
            elif kind == b'Z':
                require(payload in (b'I', b'T', b'E'), 'invalid ReadyForQuery state')
                require(result['command'] is not None or result['error'] is not None or result['empty'],
                        'ReadyForQuery without result')
                result['state'] = payload.decode('ascii')
                return result
            else:
                raise RuntimeError(f'unexpected backend Query message {kind!r}')
        raise RuntimeError('Query exceeded message bound')


class TransactionHarness(QueryHarness):
    def __init__(self, source, work, timeout, args):
        super().__init__(source, work, timeout, args)
        self.summary['stage'] = 'P5 explicit native transactions and session controls'
        self.summary['known_limitations'] = [
            'Native Debug Engine14 READ COMMITTED READ CONSISTENCY AUTO COMMIT INSERT '
            'reproduces Savepoint.cpp:833 assertion without PgWire or triggers. '
            'The AUTO COMMIT fixture uses explicit SNAPSHOT; the failing native combination '
            'is outside this integration fixture and is not claimed as supported.']

    def prepare(self):
        StartupHarness.prepare(self)
        self.isql('transaction_fixtures', f"""CONNECT '{self.database}';
CREATE TABLE P5_ROWS (ID INTEGER NOT NULL PRIMARY KEY, V INTEGER);
INSERT INTO P5_ROWS VALUES (1,10);
CREATE TABLE P5_SECRET (ID INTEGER);
CREATE TABLE P5_RESET_FAULTS (ID INTEGER);
CREATE TABLE P5_MANY (ID INTEGER);
CREATE EXCEPTION P5_COMMIT_DENIED 'P5 deliberately rejected commit';
CREATE EXCEPTION P5_RESET_DENIED 'P5 deliberately rejected reset';
CREATE TABLE P5_PATH_ROWS (ID INTEGER);
INSERT INTO P5_PATH_ROWS VALUES(10);
CREATE SCHEMA P5_EXTRA;
CREATE TABLE P5_EXTRA.P5_PATH_ROWS (ID INTEGER);
INSERT INTO P5_EXTRA.P5_PATH_ROWS VALUES(20);
CREATE SEQUENCE P5_GUARD;
CREATE SEQUENCE P5_TIMEOUT_GUARD;
GRANT SELECT, INSERT, UPDATE, DELETE ON P5_ROWS TO USER WIRE_HOST_TEST;
GRANT SELECT ON P5_PATH_ROWS TO USER WIRE_HOST_TEST;
GRANT USAGE ON SCHEMA P5_EXTRA TO USER WIRE_HOST_TEST;
GRANT SELECT ON P5_EXTRA.P5_PATH_ROWS TO USER WIRE_HOST_TEST;
GRANT USAGE ON SEQUENCE P5_GUARD TO USER WIRE_HOST_TEST;
GRANT USAGE ON SEQUENCE P5_TIMEOUT_GUARD TO USER WIRE_HOST_TEST;
GRANT INSERT, DELETE ON P5_RESET_FAULTS TO USER WIRE_HOST_TEST;
GRANT ALTER DATABASE TO USER WIRE_HOST_TEST;
SET TERM ^ ;
EXECUTE BLOCK AS DECLARE VARIABLE I INTEGER = 0;
BEGIN
  WHILE (I < 257) DO
  BEGIN
    INSERT INTO P5_MANY VALUES(:I);
    I = I + 1;
  END
END^
CREATE PROCEDURE P5_TIMEOUT(P_ID INTEGER) RETURNS(V BIGINT) AS
BEGIN
  INSERT INTO P5_ROWS VALUES(:P_ID,1);
  V = GEN_ID(P5_TIMEOUT_GUARD,1);
  SELECT COUNT(*) FROM P5_MANY A CROSS JOIN P5_MANY B CROSS JOIN P5_MANY C
    CROSS JOIN P5_MANY D CROSS JOIN P5_MANY E INTO :V;
  SUSPEND;
END^
CREATE TRIGGER P5_REJECT_COMMIT INACTIVE ON TRANSACTION COMMIT POSITION 0 AS
BEGIN
  IF (CURRENT_USER = 'WIRE_HOST_TEST' AND
      RDB$GET_CONTEXT('USER_TRANSACTION', 'P5_FAIL_COMMIT') = '1') THEN
    EXCEPTION P5_COMMIT_DENIED;
END^
CREATE TRIGGER P5_REJECT_RESET INACTIVE ON CONNECT POSITION 0 AS
BEGIN
  IF (RESETTING AND CURRENT_USER = 'WIRE_HOST_TEST' AND
      EXISTS(SELECT 1 FROM P5_RESET_FAULTS WHERE ID=1)) THEN
    EXCEPTION P5_RESET_DENIED;
END^
SET TERM ; ^
GRANT USAGE ON EXCEPTION P5_COMMIT_DENIED TO TRIGGER P5_REJECT_COMMIT;
GRANT USAGE ON EXCEPTION P5_RESET_DENIED TO TRIGGER P5_REJECT_RESET;
GRANT SELECT ON P5_RESET_FAULTS TO TRIGGER P5_REJECT_RESET;
GRANT EXECUTE ON PROCEDURE P5_TIMEOUT TO USER WIRE_HOST_TEST;
GRANT INSERT ON P5_ROWS TO PROCEDURE P5_TIMEOUT;
GRANT SELECT ON P5_MANY TO PROCEDURE P5_TIMEOUT;
GRANT USAGE ON SEQUENCE P5_TIMEOUT_GUARD TO PROCEDURE P5_TIMEOUT;
COMMIT;
QUIT;
""", embedded=True)

    def transaction_count(self, expected):
        count = self.scalar_native("SELECT COUNT(*) AS OBSERVED FROM MON$TRANSACTIONS T "
            "JOIN MON$ATTACHMENTS A ON A.MON$ATTACHMENT_ID=T.MON$ATTACHMENT_ID "
            "WHERE A.MON$REMOTE_VERSION='PgWire' AND A.MON$SYSTEM_FLAG=0", 'transaction_count')
        require(count == expected, f'expected {expected} application transaction(s), observed {count}')

    def monitor(self, column):
        require(column in ('MON$TRANSACTION_ID', 'MON$READ_ONLY', 'MON$ISOLATION_MODE',
                           'MON$AUTO_COMMIT', 'MON$AUTO_UNDO'), 'unknown monitor column')
        return self.scalar_native(f'SELECT T.{column} AS OBSERVED FROM MON$TRANSACTIONS T '
            'JOIN MON$ATTACHMENTS A ON A.MON$ATTACHMENT_ID=T.MON$ATTACHMENT_ID '
            "WHERE A.MON$REMOTE_VERSION='PgWire' AND A.MON$SYSTEM_FLAG=0", 'transaction_attributes')

    def attachment_setting(self, column):
        require(column in ('MON$STATEMENT_TIMEOUT', 'MON$IDLE_TIMEOUT'), 'unknown attachment setting')
        return self.scalar_native(f'SELECT {column} AS OBSERVED FROM MON$ATTACHMENTS '
            "WHERE MON$REMOTE_VERSION='PgWire' AND MON$SYSTEM_FLAG=0", 'attachment_settings')

    def sql(self, client, sql, state='I', tag=None, rows=None, error=None, empty=False):
        result = client.query(sql.encode('utf-8') if isinstance(sql, str) else sql)
        require(result['state'] == state, f'{sql!r}: expected state {state}, result {result!r}')
        if error:
            require(result['error'] is not None and result['error']['C'] == error and result['command'] is None,
                    f'{sql!r}: expected SQLSTATE {error}, result {result!r}')
        else:
            require(result['error'] is None and result['command'] == tag and result['empty'] == empty,
                    f'{sql!r}: expected command {tag!r}/empty={empty}, result {result!r}')
            expected = [] if rows is None else [[None if value is None else str(value) for value in row] for row in rows]
            require(result['rows'] == expected, f'{sql!r}: row values differ: {result!r}')
        self.transaction_count(0 if state == 'I' else 1)
        return result

    def seen(self, identifier, expected):
        count = self.scalar_native(f'SELECT COUNT(*) AS OBSERVED FROM P5_ROWS WHERE ID={identifier}')
        require(count == expected, f'native observer sees ID {identifier}: count {count}, expected {expected}')

    def start_transaction(self, client, suffix='READ WRITE READ COMMITTED NO WAIT'):
        self.sql(client, 'SET TRANSACTION ' + suffix, 'T', 'BEGIN')

    def close_client(self, client, terminate=True):
        client.close(terminate=terminate)
        self.no_attachments()
        self.transaction_count(0)

    def visibility(self):
        client = TransactionClient(self).login()
        self.sql(client, 'SELECT CAST(MON$ISOLATION_MODE AS INTEGER), CAST(MON$LOCK_TIMEOUT AS INTEGER) '
                 'FROM MON$TRANSACTIONS WHERE MON$TRANSACTION_ID=CURRENT_TRANSACTION',
                 tag='SELECT 1', rows=[(4, -1)])
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(101,1)', tag='INSERT 0 1')
        self.seen(101, 1)
        self.start_transaction(client)
        transaction = self.monitor('MON$TRANSACTION_ID')
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(102,2)', 'T', 'INSERT 0 1')
        self.seen(102, 0)
        self.sql(client, 'SELECT V FROM P5_ROWS WHERE ID=102', 'T', 'SELECT 1', [(2,)])
        require(self.monitor('MON$TRANSACTION_ID') == transaction, 'ordinary SQL replaced explicit transaction')
        self.sql(client, 'COMMIT', tag='COMMIT')
        self.seen(102, 1)
        self.start_transaction(client)
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(103,3)', 'T', 'INSERT 0 1')
        self.sql(client, 'ROLLBACK', tag='ROLLBACK')
        self.seen(103, 0)
        self.close_client(client)

    def failed_state_and_savepoints(self):
        client = TransactionClient(self).login()
        self.start_transaction(client)
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(201,1)', 'T', 'INSERT 0 1')
        self.sql(client, 'SAVEPOINT P5_POINT', 'T', 'SAVEPOINT')
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(202,2)', 'T', 'INSERT 0 1')
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(1,999)', 'E', error='23505')
        transaction = self.monitor('MON$TRANSACTION_ID')
        for sql, error in [
            ('SELECT GEN_ID(P5_GUARD,1) FROM RDB$DATABASE', '25P02'),
            ('SELECT * FROM P5_MISSING_TABLE', '25P02'),
            ('SELECT P5_MISSING_COLUMN FROM P5_ROWS', '42703'),
            ('SELECT * FROM P5_SECRET', '42501'),
            ('SELECT', '42601'), ('BEGIN', '42601'), ('COMMIT RETAINING', '42601'),
            ('SAVEPOINT P5_LATER', '25P02'), ('RELEASE SAVEPOINT P5_POINT', '25P02'),
            ('COMMIT RETAIN', '25P02'), ('ALTER SESSION RESET', '25P02')]:
            self.sql(client, sql, 'E', error=error)
        require(self.monitor('MON$TRANSACTION_ID') == transaction, 'E-state errors replaced native transaction')
        require(self.scalar_native('SELECT GEN_ID(P5_GUARD,0) AS OBSERVED FROM RDB$DATABASE') == 0,
                '25P02 query was executed')
        self.sql(client, 'ROLLBACK TO SAVEPOINT P5_POINT', 'T', 'ROLLBACK')
        self.sql(client, 'SELECT ID FROM P5_ROWS WHERE ID IN(201,202) ORDER BY ID', 'T', 'SELECT 1', [(201,)])
        self.sql(client, 'RELEASE SAVEPOINT P5_POINT', 'T', 'RELEASE')
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(203,3)', 'T', 'INSERT 0 1')
        self.sql(client, 'COMMIT', tag='COMMIT')
        self.seen(201, 1)
        self.seen(202, 0)
        self.seen(203, 1)
        self.close_client(client)

    def controls_and_retaining(self):
        client = TransactionClient(self).login()
        for command in ('COMMIT', 'ROLLBACK'):
            self.sql(client, command, tag=command)
            self.sql(client, command + ' RETAIN', error='25000')
            self.sql(client, command + ' RETAINING', error='42601')
        self.start_transaction(client)
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(301,1)', 'T', 'INSERT 0 1')
        transaction = self.monitor('MON$TRANSACTION_ID')
        self.sql(client, 'SET TRANSACTION', 'E', error='25001')
        require(self.monitor('MON$TRANSACTION_ID') == transaction, 'repeat start replaced transaction')
        self.sql(client, 'COMMIT', tag='ROLLBACK')
        self.seen(301, 0)
        self.start_transaction(client)
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(302,2)', 'T', 'INSERT 0 1')
        self.sql(client, 'COMMIT RETAIN SNAPSHOT', 'T', 'COMMIT')
        self.seen(302, 1)
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(303,3)', 'T', 'INSERT 0 1')
        self.sql(client, 'ROLLBACK RETAIN', 'T', 'ROLLBACK')
        self.seen(303, 0)
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(304,4)', 'T', 'INSERT 0 1')
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(1,999)', 'E', error='23505')
        self.sql(client, 'ROLLBACK TO SAVEPOINT P5_MISSING', 'E', error='3B000')
        self.sql(client, 'ROLLBACK RETAIN SNAPSHOT', 'T', 'ROLLBACK')
        self.sql(client, 'SELECT COUNT(*) FROM P5_ROWS WHERE ID=304', 'T', 'SELECT 1', [(0,)])
        self.sql(client, 'ROLLBACK', tag='ROLLBACK')
        self.seen(304, 0)
        self.close_client(client)

    def empty_states(self):
        client = TransactionClient(self).login()
        for state in ('I', 'T', 'E'):
            if state == 'T':
                self.start_transaction(client)
            elif state == 'E':
                self.sql(client, 'SELECT', 'E', error='42601')
            for sql in ('', ' \t\r\n', '/* COMMIT; BEGIN */ -- closed\n'):
                self.sql(client, sql, state, empty=True)
        self.sql(client, 'ROLLBACK', tag='ROLLBACK')
        self.close_client(client)

    def isolation_and_readonly(self):
        client = TransactionClient(self).login()
        self.start_transaction(client, 'READ ONLY SNAPSHOT NO WAIT')
        require(self.monitor('MON$READ_ONLY') == 1 and self.monitor('MON$ISOLATION_MODE') == 1,
                'native READ ONLY/SNAPSHOT characteristics were not applied')
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(401,1)', 'E', error='25006')
        self.sql(client, 'ROLLBACK', tag='ROLLBACK')
        self.seen(401, 0)
        self.start_transaction(client, 'READ WRITE READ COMMITTED NO WAIT')
        require(self.monitor('MON$READ_ONLY') == 0 and self.monitor('MON$ISOLATION_MODE') in (2, 3, 4),
                'native READ COMMITTED characteristics were not applied')
        self.sql(client, 'SELECT V FROM P5_ROWS WHERE ID=1', 'T', 'SELECT 1', [(10,)])
        self.isql('concurrent_committed_change', 'UPDATE P5_ROWS SET V=11 WHERE ID=1;\nCOMMIT;\nQUIT;\n')
        self.sql(client, 'SELECT V FROM P5_ROWS WHERE ID=1', 'T', 'SELECT 1', [(11,)])
        self.sql(client, 'ROLLBACK', tag='ROLLBACK')
        self.isql('restore_baseline_value', 'UPDATE P5_ROWS SET V=10 WHERE ID=1;\nCOMMIT;\nQUIT;\n')
        self.close_client(client)

    def session_reset(self):
        client = TransactionClient(self).login()
        set_context = "SELECT RDB$SET_CONTEXT('USER_SESSION','P5_CONTEXT','kept') FROM RDB$DATABASE"
        get_context = "SELECT CAST(RDB$GET_CONTEXT('USER_SESSION','P5_CONTEXT') AS VARCHAR(8)) FROM RDB$DATABASE"
        self.sql(client, set_context, tag='SELECT 1', rows=[(0,)])
        self.sql(client, 'SET ROLE PGWIRE_OBSERVER', tag='SET')
        self.sql(client, "SELECT CURRENT_ROLE FROM RDB$DATABASE", tag='SELECT 1', rows=[('PGWIRE_OBSERVER',)])
        self.sql(client, "SET TIME ZONE '+02:00'", tag='SET')
        self.sql(client, "SELECT RDB$GET_CONTEXT('SYSTEM','SESSION_TIMEZONE') FROM RDB$DATABASE",
                 tag='SELECT 1', rows=[('+02:00',)])
        self.sql(client, 'ALTER SESSION RESET', tag='RESET')
        self.sql(client, get_context, tag='SELECT 1', rows=[(None,)])
        self.sql(client, 'SELECT CURRENT_ROLE FROM RDB$DATABASE', tag='SELECT 1', rows=[('NONE',)])
        self.start_transaction(client)
        self.sql(client, set_context, 'T', 'SELECT 1', [(0,)])
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(501,1)', 'T', 'INSERT 0 1')
        transaction = self.monitor('MON$TRANSACTION_ID')
        self.sql(client, 'ALTER SESSION RESET', 'T', 'RESET')
        require(self.monitor('MON$TRANSACTION_ID') != transaction, 'RESET retained underlying transaction')
        self.seen(501, 0)
        self.sql(client, get_context, 'T', 'SELECT 1', [(None,)])
        self.sql(client, set_context, 'T', 'SELECT 1', [(0,)])
        self.sql(client, 'SAVEPOINT P5_RESET_RECOVERY', 'T', 'SAVEPOINT')
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(1,999)', 'E', error='23505')
        transaction = self.monitor('MON$TRANSACTION_ID')
        self.sql(client, 'ALTER SESSION RESET', 'E', error='25P02')
        require(self.monitor('MON$TRANSACTION_ID') == transaction, 'RESET executed inside E')
        self.sql(client, 'ROLLBACK TO SAVEPOINT P5_RESET_RECOVERY', 'T', 'ROLLBACK')
        self.sql(client, get_context, 'T', 'SELECT 1', [('kept',)])
        self.sql(client, 'ROLLBACK', tag='ROLLBACK')
        self.close_client(client)

    def auto_commit_characteristics(self):
        # Native control path independently establishes AUTO COMMIT semantics.
        self.isql('native_auto_commit', 'COMMIT;\nSET TRANSACTION READ WRITE SNAPSHOT AUTO COMMIT;\n'
                  'INSERT INTO P5_ROWS VALUES(601,1);\nROLLBACK;\nQUIT;\n')
        self.seen(601, 1)
        client = TransactionClient(self).login()
        self.start_transaction(client, 'READ WRITE SNAPSHOT AUTO COMMIT')
        require(self.monitor('MON$AUTO_COMMIT') == 1, 'AUTO COMMIT was not applied')
        require(self.monitor('MON$ISOLATION_MODE') == 1, 'AUTO COMMIT did not use the SNAPSHOT fixture profile')
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(602,2)', 'T', 'INSERT 0 1')
        self.seen(602, 1)
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(1,999)', 'E', error='23505')
        self.sql(client, 'COMMIT', tag='ROLLBACK')
        self.seen(602, 1)  # A later failed block cannot undo an earlier native auto-commit.
        self.close_client(client)

    def no_auto_undo_characteristics(self):
        client = TransactionClient(self).login()
        self.start_transaction(client, 'READ WRITE READ COMMITTED NO AUTO UNDO')
        require(self.monitor('MON$AUTO_UNDO') == 0, 'NO AUTO UNDO was not applied')
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(603,3)', 'T', 'INSERT 0 1')
        self.sql(client, 'SAVEPOINT P5_UNDO', 'T', 'SAVEPOINT')
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(604,4)', 'T', 'INSERT 0 1')
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(1,999)', 'E', error='23505')
        self.sql(client, 'ROLLBACK TO SAVEPOINT P5_UNDO', 'T', 'ROLLBACK')
        self.sql(client, 'COMMIT', tag='COMMIT')
        self.seen(603, 1)
        self.seen(604, 0)
        self.close_client(client)

    def session_effects(self):
        client = TransactionClient(self).login()
        self.sql(client, 'SET STATEMENT TIMEOUT 2500 MILLISECOND', tag='SET')
        self.sql(client, 'SET SESSION IDLE TIMEOUT 120 SECOND', tag='SET')
        # Monitoring.cpp publishes statement milliseconds and attachment idle seconds.
        require(self.attachment_setting('MON$STATEMENT_TIMEOUT') == 2500, 'statement timeout was not applied')
        require(self.attachment_setting('MON$IDLE_TIMEOUT') == 120, 'session idle timeout was not applied')
        self.sql(client, 'SELECT ID FROM P5_PATH_ROWS', tag='SELECT 1', rows=[(10,)])
        self.sql(client, 'SET SEARCH_PATH TO P5_EXTRA, PUBLIC', tag='SET')
        self.sql(client, 'SELECT ID FROM P5_PATH_ROWS', tag='SELECT 1', rows=[(20,)])
        self.sql(client, 'SELECT ID FROM PUBLIC.P5_PATH_ROWS', tag='SELECT 1', rows=[(10,)])
        value_sql = 'SELECT CAST(12.5 AS DOUBLE PRECISION) AS "coerced" FROM RDB$DATABASE'
        self.sql(client, value_sql, tag='SELECT 1', rows=[('12.5',)])
        self.sql(client, 'SET BIND OF DOUBLE PRECISION TO VARCHAR(40)', tag='SET')
        result = client.query(value_sql.encode())
        require(result['error'] is None and result['command'] == 'SELECT 1' and result['state'] == 'I',
                f'SET BIND did not produce a supported query result: {result!r}')
        require(len(result['columns']) == 1 and result['columns'][0]['oid'] == 1043 and
                result['columns'][0]['name'] == 'coerced' and len(result['rows']) == 1 and
                Decimal(result['rows'][0][0]) == Decimal('12.5'), 'SET BIND value/logical metadata differs')
        self.transaction_count(0)
        self.sql(client, 'ALTER SESSION RESET', tag='RESET')
        require(self.attachment_setting('MON$STATEMENT_TIMEOUT') == 0 and
                self.attachment_setting('MON$IDLE_TIMEOUT') == 0, 'RESET did not restore timeout settings')
        self.sql(client, 'SELECT ID FROM P5_PATH_ROWS', tag='SELECT 1', rows=[(10,)])
        self.sql(client, value_sql, tag='SELECT 1', rows=[('12.5',)])
        self.close_client(client)

    def disconnect_rollback(self):
        for identifier, terminate in ((701, True), (702, False)):
            client = TransactionClient(self).login()
            self.start_transaction(client)
            self.sql(client, f'INSERT INTO P5_ROWS VALUES({identifier},1)', 'T', 'INSERT 0 1')
            if not terminate:
                self.sql(client, 'INSERT INTO P5_ROWS VALUES(1,999)', 'E', error='23505')
            self.close_client(client, terminate)
            self.seen(identifier, 0)

    def fatal_control(self, client, sql, state):
        try:
            client.send(packet(b'Q', sql.encode('utf-8') + b'\0'))
            kind, payload = client.message()
            require(kind == b'E', f'{sql}: produced completion or ReadyForQuery before failure')
            diagnostic = error_fields(payload)
            require(diagnostic['S'] == 'FATAL' and diagnostic['V'] == 'FATAL' and
                    diagnostic['C'] == state, f'{sql}: incorrect fatal diagnostic {diagnostic!r}')
            client.connection.settimeout(5)
            try:
                require(client.connection.recv(1) == b'', f'{sql}: data after FATAL ErrorResponse')
            except ConnectionResetError:
                pass
        finally:
            client.close(terminate=False)
        self.no_attachments()
        self.transaction_count(0)

    def statement_timeout_effect(self):
        client = TransactionClient(self).login()
        self.sql(client, 'SET STATEMENT TIMEOUT 100 MILLISECOND', tag='SET')
        for identifier, expected_state in ((811, 'I'), (812, 'E')):
            if expected_state == 'E':
                self.start_transaction(client)
                self.sql(client, 'INSERT INTO P5_ROWS VALUES(813,3)', 'T', 'INSERT 0 1')
            client.connection.settimeout(4)
            started = time.monotonic()
            self.sql(client, f'SELECT V FROM P5_TIMEOUT({identifier})', expected_state, error='57014')
            elapsed = time.monotonic() - started
            require(elapsed < 3, f'attachment statement timeout was overridden: {elapsed:.3f}s')
            client.connection.settimeout(5)
            if expected_state == 'E':
                self.sql(client, 'ROLLBACK', tag='ROLLBACK')
            self.seen(identifier, 0)
        require(self.scalar_native('SELECT GEN_ID(P5_TIMEOUT_GUARD,0) AS OBSERVED FROM RDB$DATABASE') == 2,
                'timeout did not interrupt both real engine executions')
        self.seen(813, 0)
        self.sql(client, 'SELECT CAST(42 AS INTEGER) FROM RDB$DATABASE', tag='SELECT 1', rows=[(42,)])
        self.close_client(client)

    def fatal_commit_and_reset(self):
        # Fault triggers belong only to this group. Native AUTO COMMIT has its
        # own savepoint path and must be tested without injected commit hooks.
        self.isql('activate_fault_triggers', 'ALTER TRIGGER P5_REJECT_COMMIT ACTIVE;\n'
                  'ALTER TRIGGER P5_REJECT_RESET ACTIVE;\nCOMMIT;\nQUIT;\n')
        try:
            self.fatal_commit_and_reset_active()
        finally:
            self.isql('deactivate_fault_triggers', 'ALTER TRIGGER P5_REJECT_COMMIT INACTIVE;\n'
                      'ALTER TRIGGER P5_REJECT_RESET INACTIVE;\nCOMMIT;\nQUIT;\n')

    def fatal_commit_and_reset_active(self):
        client = TransactionClient(self).login()
        self.start_transaction(client)
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(801,1)', 'T', 'INSERT 0 1')
        self.sql(client, "SELECT RDB$SET_CONTEXT('USER_TRANSACTION','P5_FAIL_COMMIT','1') "
                 'FROM RDB$DATABASE', 'T', 'SELECT 1', [(0,)])
        self.fatal_control(client, 'COMMIT', 'XX000')
        self.seen(801, 0)

        client = TransactionClient(self).login()
        self.start_transaction(client)
        self.sql(client, 'INSERT INTO P5_ROWS VALUES(802,2)', 'T', 'INSERT 0 1')
        self.isql('enable_reset_fault', 'INSERT INTO P5_RESET_FAULTS VALUES(1);\nCOMMIT;\nQUIT;\n')
        try:
            # ON CONNECT runs after rollback and marks isc_ses_reset_failed (08003).
            self.fatal_control(client, 'ALTER SESSION RESET', '08003')
        finally:
            self.isql('disable_reset_fault', 'DELETE FROM P5_RESET_FAULTS;\nCOMMIT;\nQUIT;\n')
        self.seen(802, 0)
        healthy = TransactionClient(self).login()
        self.sql(healthy, 'SELECT CAST(42 AS INTEGER) FROM RDB$DATABASE', tag='SELECT 1', rows=[(42,)])
        self.close_client(healthy)

    def build_c_driver(self):
        source = Path(__file__).with_name('pgwire_libpq_transaction.c')
        self.c_driver = self.work / 'pgwire_libpq_transaction'
        result = subprocess.run([self.args.cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
            '-I', str(self.args.libpq_include_dir), str(source), str(self.args.libpq_library), '-o', str(self.c_driver)],
            capture_output=True, text=True, timeout=self.timeout)
        (self.work / 'libpq-compile.log').write_text(result.stdout + result.stderr)
        require(result.returncode == 0, 'C libpq transaction compilation failed')
        self.summary['c_build'] = {'headers': str(self.args.libpq_include_dir),
            'library': str(self.args.libpq_library), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}

    def c_transactions(self):
        self.c_libpq()
        self.no_attachments()
        self.seen(901, 1)
        self.seen(902, 0)
        self.seen(903, 0)

    def run_transactions(self):
        native, configured, port = reserve_ports(3)
        self.start('transactions', native, configured, [{'alias': 'PgWire', 'port': port}])
        self.close_client(TransactionClient(self).login())
        for name, action in (
            ('implicit_explicit_commit_rollback_and_native_visibility', self.visibility),
            ('failed_state_prepare_error_priority_and_savepoint_recovery', self.failed_state_and_savepoints),
            ('idle_controls_repeat_start_retaining_and_failed_commit', self.controls_and_retaining),
            ('empty_preserves_idle_transaction_and_failed_state', self.empty_states),
            ('real_readonly_and_read_committed_characteristics', self.isolation_and_readonly),
            ('real_session_settings_and_reset_in_I_T_E', self.session_reset),
            ('session_timeout_search_path_and_bind_have_real_effects', self.session_effects),
            ('attachment_statement_timeout_interrupts_implicit_and_explicit_sql', self.statement_timeout_effect),
            ('native_snapshot_auto_commit_dml_semantics', self.auto_commit_characteristics),
            ('native_no_auto_undo_savepoint_recovery', self.no_auto_undo_characteristics),
            ('terminate_and_eof_roll_back_explicit_work', self.disconnect_rollback),
            ('real_commit_and_reset_failure_fatal_close_and_rollback', self.fatal_commit_and_reset),
            ('direct_c_libpq_transaction_status_and_recovery', self.c_transactions)):
            self.check(name, action)
        self.native_probe('native_after_transactions')
        self.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firebird-dir', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--libpq-include-dir', type=Path, required=True)
    parser.add_argument('--libpq-library', type=Path, required=True)
    parser.add_argument('--cc', default='cc')
    parser.add_argument('--timeout', type=float, default=30)
    args = parser.parse_args()
    source, work = args.firebird_dir.resolve(), args.work_dir.absolute()
    if not 0 < args.timeout <= 300 or work.exists() or work.is_symlink() or source in work.resolve().parents:
        parser.error('use timeout 0..300 and a new work directory outside the build')
    if not re.fullmatch(r'[A-Za-z0-9_./-]+', str(work)):
        parser.error('work directory must use safe ASCII path characters')
    for name in ('bin/firebird', 'bin/isql', 'security6.fdb', 'plugins/libPgWire.so'):
        if not (source / name).is_file():
            parser.error(f'completed build required: missing {source / name}')
    args.libpq_include_dir = args.libpq_include_dir.resolve()
    args.libpq_library = args.libpq_library.resolve()
    if not args.libpq_library.is_file():
        parser.error('real libpq runtime is required')
    for name in ('libpq-fe.h', 'postgres_ext.h', 'pg_config.h'):
        if not (args.libpq_include_dir / name).is_file():
            parser.error(f'genuine libpq development headers required: missing {name}')
    args.cc = shutil.which(args.cc)
    if not args.cc:
        parser.error('C compiler is unavailable')
    work.mkdir(mode=0o700)
    harness = TransactionHarness(source, work.resolve(), args.timeout, args)
    try:
        harness.prepare()
        harness.build_c_driver()
        harness.run_transactions()
        harness.run_trace()
        harness.summary['passed'] = True
    except (Exception, KeyboardInterrupt) as error:
        harness.summary['error'] = harness.redact(f'{type(error).__name__}: {error}')
    finally:
        try:
            harness.close()
        except Exception as error:
            harness.summary['passed'] = False
            harness.summary['shutdown_error'] = harness.redact(str(error))
        harness.summary['checks'] = harness.cases
        path = work / 'summary.json'
        path.write_text(json.dumps(harness.summary, indent=2) + '\n')
        print(json.dumps({'passed': harness.summary['passed'], 'summary': str(path)}, indent=2))
    return 0 if harness.summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
