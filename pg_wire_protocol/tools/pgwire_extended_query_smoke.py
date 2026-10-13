#!/usr/bin/env python3
"""P7 Extended Query over real TCP and libpq, in a new private runtime.

The external --timeout bounds test subprocesses; it configures no server timeout.
SQL contains native question-mark parameters and is never rewritten or split.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess

from pgwire_query_smoke import QueryHarness
from pgwire_startup_smoke import StartupHarness, error_fields, packet
from pgwire_transaction_smoke import TransactionClient
from wire_host_smoke import field, require, require_closed, reserve_ports


ANSWER = 'SELECT CAST(42 AS INTEGER) FROM RDB$DATABASE'


def string(value):
    return (value.encode('utf-8') if isinstance(value, str) else value) + b'\0'


def parse(name, sql, oids=()):
    return packet(b'P', string(name) + string(sql) + struct.pack('!H', len(oids)) +
                  b''.join(struct.pack('!I', oid) for oid in oids))


def bind(portal='', statement='', values=(), formats=(), results=()):
    body = string(portal) + string(statement) + struct.pack('!H', len(formats))
    body += b''.join(struct.pack('!H', value) for value in formats) + struct.pack('!H', len(values))
    for value in values:
        if isinstance(value, str):
            value = value.encode('utf-8')
        body += struct.pack('!i', -1 if value is None else len(value))
        if value is not None:
            body += value
    body += struct.pack('!H', len(results)) + b''.join(struct.pack('!H', value) for value in results)
    return packet(b'B', body)


def describe(kind, name=''):
    return packet(b'D', kind + string(name))


def execute(portal='', rows=0):
    return packet(b'E', string(portal) + struct.pack('!I', rows))


def close(kind, name=''):
    return packet(b'C', kind + string(name))


def columns(body):
    require(len(body) >= 2, 'RowDescription count missing')
    result, offset = [], 2
    for _ in range(struct.unpack('!H', body[:2])[0]):
        end = body.find(b'\0', offset)
        require(end >= offset and end + 19 <= len(body), 'RowDescription field truncated')
        table, attribute, oid, size, modifier, format_code = struct.unpack('!IhIhih', body[end + 1:end + 19])
        result.append({'name': body[offset:end].decode('utf-8'), 'table': table,
                       'attribute': attribute, 'oid': oid, 'size': size,
                       'modifier': modifier, 'format': format_code})
        offset = end + 19
    require(offset == len(body), 'RowDescription trailing bytes')
    return result


def row(body):
    require(len(body) >= 2, 'DataRow count missing')
    values, offset = [], 2
    for _ in range(struct.unpack('!H', body[:2])[0]):
        require(offset + 4 <= len(body), 'DataRow length truncated')
        size = struct.unpack('!i', body[offset:offset + 4])[0]
        offset += 4
        require(size >= -1 and offset + max(size, 0) <= len(body), 'DataRow value truncated')
        values.append(None if size == -1 else body[offset:offset + size])
        offset += max(size, 0)
    require(offset == len(body), 'DataRow trailing bytes')
    return values


class ExtendedClient(TransactionClient):
    def events(self, end=b'Z'):
        result = []
        for _ in range(100000):
            try:
                kind, body = self.message()
            except (OSError, RuntimeError) as error:
                recent = [(kind.decode(), error_fields(body) if kind == b'E' else repr(body[:100]))
                          for kind, body in result[-8:]]
                raise RuntimeError(f'{error}; expected terminator {end!r}, recent events {recent!r}') from error
            if kind in (b'N', b'S'):
                continue
            result.append((kind, body))
            if kind == end or (end != b'Z' and kind in (b'E', b'Z')):
                return result
        raise RuntimeError('Extended Query response exceeded message bound')

    def exchange(self, data, kinds, end=b'Z', state='I', error=None, rows=None, tag=None):
        self.send(data)
        events = self.events(end)
        actual = b''.join(kind for kind, _ in events)
        diagnostic = [(kind.decode(), error_fields(body) if kind == b'E' else repr(body[:100]))
                      for kind, body in events]
        require(actual == kinds, f'expected message order {kinds!r}, received {diagnostic!r}')
        if end == b'Z':
            require(events[-1][1] == state.encode(), f'expected ReadyForQuery {state}: {diagnostic!r}')
        for kind, body in events:
            if kind in (b'1', b'2', b'3', b'n', b's', b'I'):
                require(not body, f'{kind!r} must have an empty body')
        if error:
            failures = [error_fields(body) for kind, body in events if kind == b'E']
            require(len(failures) == 1 and failures[0]['C'] == error and failures[0]['S'] != 'FATAL',
                    f'expected recoverable SQLSTATE {error}: {diagnostic!r}')
        else:
            require(not any(kind == b'E' for kind, _ in events), f'unexpected error {diagnostic!r}')
        if rows is not None:
            expected = [[value.encode() if isinstance(value, str) else value for value in record] for record in rows]
            require([row(body) for kind, body in events if kind == b'D'] == expected,
                    f'DataRow values differ: {diagnostic!r}')
        if tag is not None:
            require([body for kind, body in events if kind == b'C'] == [string(tag)],
                    f'CommandComplete differs: {diagnostic!r}')
        return events

    def run(self, sql, values=(), oids=(), formats=(), results=(), state='I', rows=None, tag=None):
        return self.exchange(parse('', sql, oids) + bind(values=values, formats=formats, results=results) +
            describe(b'P') + execute() + packet(b'S'), b'12T' + b'D' * len(rows or []) + b'CZ',
            state=state, rows=rows, tag=tag)


class ExtendedHarness(QueryHarness):
    def __init__(self, source, work, timeout, args):
        super().__init__(source, work, timeout, args)
        self.summary['stage'] = 'P7 Extended Query, prepared statements and portals'
        self.summary['sql_scope'] = 'Native ? parameters; PostgreSQL $n and BEGIN remain parser errors'
        self.summary['driver_scope'] = 'Real C libpq and raw TCP; actual Psycopg3 profile is deferred to P9'

    def prepare(self):
        StartupHarness.prepare(self)
        self.isql('extended_fixtures', f"""CONNECT '{self.database}';
CREATE TABLE P7_ROWS (ID INTEGER NOT NULL PRIMARY KEY, V INTEGER);
INSERT INTO P7_ROWS VALUES(1,10);
INSERT INTO P7_ROWS VALUES(2,20);
INSERT INTO P7_ROWS VALUES(3,30);
CREATE TABLE P7_EFFECTS (ID INTEGER NOT NULL PRIMARY KEY);
CREATE SEQUENCE P7_GUARD;
GRANT SELECT, INSERT, UPDATE, DELETE ON P7_ROWS TO USER WIRE_HOST_TEST;
GRANT SELECT, INSERT, UPDATE, DELETE ON P7_EFFECTS TO USER WIRE_HOST_TEST;
GRANT USAGE ON SEQUENCE P7_GUARD TO USER WIRE_HOST_TEST;
SET TERM ^ ;
CREATE PROCEDURE P7_STREAM(P_ID INTEGER) RETURNS(V INTEGER) AS
BEGIN
  INSERT INTO P7_EFFECTS VALUES(:P_ID);
  FOR SELECT ID FROM P7_ROWS ORDER BY ID INTO :V DO SUSPEND;
END^
SET TERM ; ^
GRANT EXECUTE ON PROCEDURE P7_STREAM TO USER WIRE_HOST_TEST;
GRANT INSERT ON P7_EFFECTS TO PROCEDURE P7_STREAM;
GRANT SELECT ON P7_ROWS TO PROCEDURE P7_STREAM;
COMMIT;
QUIT;
""", embedded=True)

    def transaction_count(self, expected):
        actual = self.scalar_native('SELECT COUNT(*) AS OBSERVED FROM MON$TRANSACTIONS T '
            'JOIN MON$ATTACHMENTS A ON A.MON$ATTACHMENT_ID=T.MON$ATTACHMENT_ID '
            "WHERE A.MON$REMOTE_VERSION='PgWire' AND A.MON$SYSTEM_FLAG=0", 'extended_transaction_count')
        require(actual == expected, f'expected {expected} application transactions, observed {actual}')

    def seen(self, identifier, expected):
        actual = self.scalar_native(f'SELECT COUNT(*) AS OBSERVED FROM P7_EFFECTS WHERE ID={identifier}')
        require(actual == expected, f'native observer ID {identifier}: expected {expected}, observed {actual}')

    def simple(self, client, sql, tag=None, state='I', error=None):
        result = client.query(sql.encode())
        require(result['state'] == state, f'{sql!r}: expected state {state}, result {result!r}')
        if error:
            require(result['error'] and result['error']['C'] == error and result['command'] is None,
                    f'{sql!r}: expected {error}, result {result!r}')
        else:
            require(result['error'] is None and result['command'] == tag, f'{sql!r}: result {result!r}')
        return result

    def finish(self, client, terminate=True):
        client.close(terminate=terminate)
        self.no_attachments()
        self.transaction_count(0)

    def descriptions_and_lifetimes(self):
        c = ExtendedClient(self).login()
        sql = 'SELECT CAST(? AS INTEGER) AS "answer" FROM RDB$DATABASE'
        events = c.exchange(parse('named', sql, [0]) + describe(b'S', 'named') + packet(b'H'), b'1tT', end=b'T')
        require(events[1][1] == struct.pack('!HI', 1, 23), 'ParameterDescription did not infer integer')
        require(columns(events[2][1]) == [{'name': 'answer', 'table': 0, 'attribute': 0,
                'oid': 23, 'size': 4, 'modifier': -1, 'format': 0}], 'statement metadata differs')
        self.transaction_count(0)
        c.exchange(bind('p', 'named', ['42']) + describe(b'P', 'p') + execute('p') + packet(b'S'),
                   b'2TDCZ', rows=[['42']], tag='SELECT 1')
        c.exchange(bind('p', 'named', ['43']) + execute('p') + packet(b'S'), b'2DCZ', rows=[['43']], tag='SELECT 1')
        c.exchange(describe(b'P', 'p') + packet(b'S'), b'EZ', error='34000')
        c.exchange(parse('named', sql) + packet(b'S'), b'EZ', error='42P05')
        c.exchange(parse('', ANSWER) + parse('', 'SELECT CAST(43 AS INTEGER) FROM RDB$DATABASE') +
                   bind() + execute() + packet(b'S'), b'112DCZ', rows=[['43']], tag='SELECT 1')
        c.exchange(parse('keep', ANSWER) + bind('keep', 'keep') + close(b'S', 'keep') +
                   execute('keep') + packet(b'S'), b'123DCZ', rows=[['42']], tag='SELECT 1')
        c.exchange(parse('', ANSWER) + bind('old_unnamed', '') +
                   parse('', 'SELECT CAST(44 AS INTEGER) FROM RDB$DATABASE') + bind('new_unnamed', '') +
                   execute('old_unnamed') + execute('new_unnamed') + packet(b'S'),
                   b'1212DCDCZ', rows=[['42'], ['44']])
        c.exchange(bind('', 'named', ['45']) + bind('', 'named', ['46']) + execute() + packet(b'S'),
                   b'22DCZ', rows=[['46']], tag='SELECT 1')
        c.exchange(close(b'S', 'missing') + close(b'P', 'missing') + packet(b'S'), b'33Z')
        c.exchange(parse('dml', 'INSERT INTO P7_EFFECTS VALUES(?)', [23]) + describe(b'S', 'dml') +
                   packet(b'S'), b'1tnZ')
        c.exchange(close(b'S', 'named') + describe(b'S', 'named') + packet(b'S'), b'3EZ', error='26000')
        c.exchange(parse('', ANSWER) + packet(b'S'), b'1Z')
        self.simple(c, ANSWER, 'SELECT 1')
        c.exchange(bind() + packet(b'S'), b'EZ', error='26000')
        self.finish(c)

    def formats(self):
        c = ExtendedClient(self).login()
        sql = ('SELECT CAST(? AS SMALLINT),CAST(? AS INTEGER),CAST(? AS BIGINT),'
               'CAST(? AS BOOLEAN),CAST(? AS VARCHAR(10)),CAST(? AS CHAR(4)),'
               'CAST(? AS NUMERIC(18,2)),CAST(? AS INTEGER) FROM RDB$DATABASE')
        values = ['-32768', '2147483647', '-9223372036854775808', 'true', 'Я ', 'Я', '-123.45', None]
        events = c.run(sql, values, rows=[['-32768', '2147483647', '-9223372036854775808', 't',
                                         'Я ', 'Я   ', '-123.45', None]], tag='SELECT 1')
        require([x['oid'] for x in columns(events[2][1])] == [21, 23, 20, 16, 1043, 1042, 1700, 23],
                'scalar input/output OID inference differs')
        sql = 'SELECT CAST(? AS INTEGER),CAST(? AS BOOLEAN),CAST(? AS VARCHAR(10)) FROM RDB$DATABASE'
        values = [struct.pack('!i', -42), 'false', 'Я '.encode()]
        events = c.run(sql, values, [23, 16, 25], [1, 0, 1], [1, 0, 1],
                       rows=[[struct.pack('!i', -42), 'f', 'Я ']], tag='SELECT 1')
        require([x['format'] for x in columns(events[2][1])] == [1, 0, 1], 'mixed result formats differ')
        c.run('SELECT CAST(? AS INTEGER),CAST(? AS INTEGER) FROM RDB$DATABASE',
              ['1', '2'], formats=[0], results=[1], rows=[[struct.pack('!i', 1), struct.pack('!i', 2)]],
              tag='SELECT 1')
        self.finish(c)

    def portals(self):
        c = ExtendedClient(self).login()
        # Native prepare needs the parameter itself typed; an outer CAST returns HY004.
        sql = 'SELECT CAST(ID + CAST(? AS INTEGER) AS INTEGER) FROM P7_ROWS ORDER BY ID'
        c.exchange(parse('rows', sql) + bind('left', 'rows', ['0']) + bind('right', 'rows', ['10']) +
                   execute('left', 2) + packet(b'H'), b'122DDs', end=b's', rows=[['1'], ['2']])
        self.transaction_count(1)
        c.exchange(execute('right', 1) + packet(b'H'), b'Ds', end=b's', rows=[['11']])
        c.exchange(execute('left', 2) + packet(b'H'), b'DC', end=b'C', rows=[['3']], tag='SELECT 3')
        c.exchange(execute('right') + packet(b'S'), b'DDCZ', rows=[['12'], ['13']], tag='SELECT 3')
        self.transaction_count(0)
        c.exchange(bind('duplicate', 'rows', ['0']) + bind('duplicate', 'rows', ['1']) + packet(b'S'),
                   b'2EZ', error='42P03')
        c.exchange(bind('closed', 'rows', ['0']) + close(b'P', 'closed') + execute('closed') + packet(b'S'),
                   b'23EZ', error='34000')
        self.finish(c)

    def cycle_boundaries(self):
        c = ExtendedClient(self).login()
        c.exchange(parse('write', 'INSERT INTO P7_EFFECTS VALUES(?)') + bind('write1', 'write', ['101']) +
                   execute('write1') + packet(b'H'), b'12C', end=b'C', tag='INSERT 0 1')
        self.seen(101, 0)
        self.transaction_count(1)
        c.exchange(packet(b'S'), b'Z')
        self.seen(101, 1)
        self.transaction_count(0)
        c.exchange(bind('write2', 'write', ['102']) + execute('write2') + packet(b'H'),
                   b'2C', end=b'C', tag='INSERT 0 1')
        # Failure rolls back the whole implicit cycle, including prior completed Execute.
        c.exchange(parse('bad', 'SELECT CAST(1/0 AS INTEGER) FROM RDB$DATABASE') + bind('bad', 'bad') +
                   execute('bad') + bind('ignored', 'write', ['103']) + execute('ignored') + packet(b'H') +
                   packet(b'S'), b'12EZ', error='22012')
        self.seen(102, 0)
        self.seen(103, 0)
        self.transaction_count(0)
        c.exchange(bind('write3', 'write', ['104']) + execute('write3') + packet(b'H'),
                   b'2C', end=b'C', tag='INSERT 0 1')
        self.simple(c, ANSWER, 'SELECT 1')
        self.seen(104, 1)
        self.transaction_count(0)
        self.finish(c)

    def discard_and_recovery(self):
        c = ExtendedClient(self).login()
        guard = self.scalar_native('SELECT GEN_ID(P7_GUARD,0) AS OBSERVED FROM RDB$DATABASE')
        ignored = parse('ignored', 'SELECT GEN_ID(P7_GUARD,1) FROM RDB$DATABASE') + bind('ignored', 'ignored') + execute('ignored')
        for sql in ('SELECT $1 FROM RDB$DATABASE', 'BEGIN', 'SELECT', ';',
                    'SELECT GEN_ID(P7_GUARD,1) FROM RDB$DATABASE; SELECT 42 FROM RDB$DATABASE'):
            c.exchange(parse('invalid', sql) + ignored + packet(b'H') + packet(b'S'), b'EZ', error='42601')
            c.run(ANSWER, rows=[['42']], tag='SELECT 1')
        require(self.scalar_native('SELECT GEN_ID(P7_GUARD,0) AS OBSERVED FROM RDB$DATABASE') == guard,
                'discarded or multi-statement SQL produced an irreversible effect')
        c.exchange(parse('param', 'SELECT CAST(? AS INTEGER) FROM RDB$DATABASE') + packet(b'S'), b'1Z')
        for bad_bind in (bind('p', 'param'), bind('p', 'param', ['1'], [0, 0]),
                         bind('p', 'param', ['1'], results=[0, 0]), bind('p', 'param', ['1'], [2])):
            c.exchange(bad_bind + ignored + packet(b'S'), b'EZ', error='08P01')
        c.exchange(bind('p', 'missing') + ignored + packet(b'S'), b'EZ', error='26000')
        c.exchange(execute('missing') + ignored + packet(b'S'), b'EZ', error='34000')
        # Recovery ignores bodies of complete command frames, even malformed
        # SQL/body contents; their framing and the final Sync still matter.
        c.exchange(execute('missing') + packet(b'Q', b'\xffwithout-nul') + packet(b'H', b'ignored') +
                   packet(b'P', b'ignored') + ignored + packet(b'S'), b'EZ', error='34000')
        require(self.scalar_native('SELECT GEN_ID(P7_GUARD,0) AS OBSERVED FROM RDB$DATABASE') == guard,
                'discarded messages after Bind/Execute failure executed')
        c.exchange(bind('negative_rows', 'param', ['1']) + execute('negative_rows', 0xFFFFFFFF) +
                   packet(b'S'), b'2EZ', error='08P01')
        c.run('SELECT CAST(? AS INTEGER) FROM RDB$DATABASE', ['9'], rows=[['9']], tag='SELECT 1')
        self.finish(c)

    def explicit_transactions(self):
        c = ExtendedClient(self).login()
        self.simple(c, 'SET TRANSACTION READ WRITE READ COMMITTED NO WAIT', 'BEGIN', 'T')
        c.exchange(parse('rows', 'SELECT ID FROM P7_ROWS ORDER BY ID') + bind('hold', 'rows') + execute('hold', 1) +
                   packet(b'S'), b'12DsZ', state='T', rows=[['1']])
        self.transaction_count(1)
        c.exchange(execute('hold') + packet(b'S'), b'DDCZ', state='T', rows=[['2'], ['3']], tag='SELECT 3')
        c.exchange(parse('ins', 'INSERT INTO P7_EFFECTS VALUES(?)') + bind('ins', 'ins', ['201']) +
                   execute('ins') + packet(b'S'), b'12CZ', state='T', tag='INSERT 0 1')
        self.seen(201, 0)
        self.simple(c, 'COMMIT', 'COMMIT')
        self.seen(201, 1)
        c.exchange(bind('reuse', 'rows') + execute('reuse') + packet(b'S'), b'2DDDCZ',
                   rows=[['1'], ['2'], ['3']], tag='SELECT 3')
        self.simple(c, 'SET TRANSACTION', 'BEGIN', 'T')
        self.simple(c, 'SAVEPOINT P7_SAVE', 'SAVEPOINT', 'T')
        c.exchange(bind('dup', 'ins', ['201']) + execute('dup') + packet(b'S'), b'2EZ', state='E', error='23505')
        self.transaction_count(1)
        c.exchange(parse('syntax', 'SELECT') + packet(b'S'), b'EZ', state='E', error='42601')
        c.exchange(parse('blocked', ANSWER) + bind('blocked', 'blocked') + execute('blocked') + packet(b'S'),
                   b'EZ', state='E', error='25P02')
        self.simple(c, 'ROLLBACK TO SAVEPOINT P7_SAVE', 'ROLLBACK', 'T')
        c.run(ANSWER, state='T', rows=[['42']], tag='SELECT 1')
        self.simple(c, 'ROLLBACK', 'ROLLBACK')
        self.finish(c)

    def extended_controls(self):
        c = ExtendedClient(self).login()

        def command(sql, tag, state='I', error=None):
            c.exchange(parse('', sql) + bind() + describe(b'P') + execute() + packet(b'S'),
                       b'12nEZ' if error else b'12nCZ', state=state, error=error, tag=tag)
            self.transaction_count(0 if state == 'I' else 1)

        command('COMMIT', 'COMMIT')
        command('ROLLBACK', 'ROLLBACK')
        command('SET TRANSACTION READ WRITE READ COMMITTED NO WAIT', 'BEGIN', 'T')
        command('INSERT INTO P7_EFFECTS VALUES(601)', 'INSERT 0 1', 'T')
        command('SAVEPOINT P7_EXT_SAVE', 'SAVEPOINT', 'T')
        command('INSERT INTO P7_EFFECTS VALUES(602)', 'INSERT 0 1', 'T')
        command('INSERT INTO P7_EFFECTS VALUES(601)', None, 'E', '23505')
        command('ROLLBACK TO SAVEPOINT P7_EXT_SAVE', 'ROLLBACK', 'T')
        command('RELEASE SAVEPOINT P7_EXT_SAVE', 'RELEASE', 'T')
        self.seen(601, 0)
        command('COMMIT', 'COMMIT')
        self.seen(601, 1)
        self.seen(602, 0)
        command('SET TRANSACTION', 'BEGIN', 'T')
        command('INSERT INTO P7_EFFECTS VALUES(603)', 'INSERT 0 1', 'T')
        command('ROLLBACK', 'ROLLBACK')
        self.seen(603, 0)
        command('SET TRANSACTION', 'BEGIN', 'T')
        command('INSERT INTO P7_EFFECTS VALUES(604)', 'INSERT 0 1', 'T')
        command('INSERT INTO P7_EFFECTS VALUES(601)', None, 'E', '23505')
        command('COMMIT', 'ROLLBACK')
        self.seen(604, 0)
        command('SET TRANSACTION', 'BEGIN', 'T')
        command('INSERT INTO P7_EFFECTS VALUES(605)', 'INSERT 0 1', 'T')
        command('COMMIT RETAIN SNAPSHOT', 'COMMIT', 'T')
        self.seen(605, 1)
        command('INSERT INTO P7_EFFECTS VALUES(606)', 'INSERT 0 1', 'T')
        command('ROLLBACK RETAIN', 'ROLLBACK', 'T')
        command('ROLLBACK', 'ROLLBACK')
        self.seen(606, 0)
        self.finish(c)

    def bind_guards_before_effects(self):
        c = ExtendedClient(self).login()
        before = self.scalar_native('SELECT GEN_ID(P7_GUARD,0) AS OBSERVED FROM RDB$DATABASE')
        sql = ('SELECT CAST(? AS INTEGER),CAST(GEN_ID(P7_GUARD,1) AS NUMERIC(18,2)) '
               'FROM RDB$DATABASE')
        c.exchange(parse('guard', sql) + packet(b'S'), b'1Z')
        for values, formats, results, expected in (
            (['not an integer'], (), (), '22P02'),
            (['1'], (), [0, 1], '0A000'),
            (['1'], (), [0, 0, 0], '08P01'),
            ([None], [2], (), '08P01')):
            c.exchange(bind('guard', 'guard', values, formats, results) + execute('guard') + packet(b'S'),
                       b'EZ', error=expected)
            actual = self.scalar_native('SELECT GEN_ID(P7_GUARD,0) AS OBSERVED FROM RDB$DATABASE')
            require(actual == before, f'Bind failure {expected} executed irreversible GEN_ID effect')
            self.transaction_count(0)
        # Prove the same prepared SQL has an observable effect when validly bound.
        c.exchange(bind('guard', 'guard', ['7']) + execute('guard') + packet(b'S'), b'2DCZ',
                   rows=[['7', f'{before + 1}.00']], tag='SELECT 1')
        require(self.scalar_native('SELECT GEN_ID(P7_GUARD,0) AS OBSERVED FROM RDB$DATABASE') == before + 1,
                'positive control did not execute the sequence effect')
        self.finish(c)

    def reset(self):
        for explicit in (False, True):
            c = ExtendedClient(self).login()
            if explicit:
                self.simple(c, 'SET TRANSACTION', 'BEGIN', 'T')
            state = 'T' if explicit else 'I'
            c.exchange(parse('one', ANSWER) + describe(b'S', 'one') + parse('two', ANSWER) +
                       describe(b'S', 'two') + packet(b'S'), b'1tT1tTZ', state=state)
            self.transaction_count(int(explicit))
            if explicit:
                c.exchange(parse('many', 'SELECT ID FROM P7_ROWS ORDER BY ID') + bind('suspended', 'many') +
                           execute('suspended', 1) + packet(b'H'), b'12Ds', end=b's', rows=[['1']])
                self.simple(c, 'INSERT INTO P7_EFFECTS VALUES(301)', 'INSERT 0 1', 'T')
            c.exchange(parse('reset', 'ALTER SESSION RESET') + bind('reset', 'reset') + execute('reset') +
                       packet(b'S'), b'12CZ', state=state, tag='RESET')
            self.transaction_count(int(explicit))
            self.seen(301, 0)
            c.exchange(describe(b'S', 'one') + packet(b'S'), b'EZ', state='E' if explicit else 'I', error='26000')
            if explicit:
                self.simple(c, 'ROLLBACK', 'ROLLBACK')
            c.run(ANSWER, rows=[['42']], tag='SELECT 1')
            self.finish(c)

    def limits(self):
        c = ExtendedClient(self).login()
        c.exchange(b''.join(parse(f's{i}', ANSWER) for i in range(128)) + packet(b'S'), b'1' * 128 + b'Z')
        self.transaction_count(0)
        c.exchange(parse('overflow', ANSWER) + packet(b'S'), b'EZ', error='54000')
        c.exchange(close(b'S', 's0') + parse('replacement', ANSWER) + packet(b'S'), b'31Z')
        c.exchange(b''.join(bind(f'p{i}', 's1') for i in range(128)) + bind('overflow', 's1') + packet(b'S'),
                   b'2' * 128 + b'EZ', error='54000')
        c.exchange(bind('after_limit', 's1') + execute('after_limit') + packet(b'S'), b'2DCZ',
                   rows=[['42']], tag='SELECT 1')
        self.finish(c)

    def retained_bytes_limit(self):
        c = ExtendedClient(self).login()
        # Below the per-frame and object-count limits; retained SQL alone exceeds 8 MiB.
        sql = ANSWER + ' /*' + 'x' * (160 * 1024) + '*/'
        accepted = 0
        for number in range(64):
            c.send(parse(f'large{number}', sql) + packet(b'H'))
            kind, body = c.message()
            if kind == b'E':
                failure = error_fields(body)
                require(failure['C'] == '54000' and failure['S'] != 'FATAL',
                        f'retained-byte limit returned {failure!r}')
                break
            require(kind == b'1' and not body, 'large valid Parse failed before retained-byte limit')
            accepted += 1
        else:
            raise RuntimeError('retained SQL exceeded 8 MiB without resource rejection')
        require(1 <= accepted < 64, 'retained-byte fixture never stored a statement')
        c.exchange(packet(b'S'), b'Z')
        c.exchange(close(b'S', 'large0') + parse('small_after_limit', ANSWER) +
                   bind('small', 'small_after_limit') + execute('small') + packet(b'S'),
                   b'312DCZ', rows=[['42']], tag='SELECT 1')
        self.summary['retained_sql_limit'] = {'accepted_statements': accepted,
            'sql_bytes_each': len(sql.encode()), 'count_limit_not_reached': True}
        self.finish(c)

    def empty_and_codec_errors(self):
        c = ExtendedClient(self).login()
        c.exchange(parse('empty', ' /* closed comment */ ') + describe(b'S', 'empty') +
                   bind('empty', 'empty') + execute('empty') + packet(b'S'), b'1tn2IZ')
        for cast, value, formats, expected in (
            ('INTEGER', 'not a number', (), '22P02'),
            ('SMALLINT', '32768', (), '22003'),
            ('BIGINT', '9223372036854775808', (), '22003'),
            ('INTEGER', b'\x00', [1], '22P03'),
            ('BOOLEAN', b'\x02', [1], '22P03'),
            ('VARCHAR(10)', b'\xff', (), '22021'),
            ('NUMERIC(18,2)', b'\x00' * 8, [1], '0A000')):
            sql = f'SELECT CAST(? AS {cast}) FROM RDB$DATABASE'
            c.exchange(parse('', sql) + bind(values=[value], formats=formats) + execute() + packet(b'S'),
                       b'1EZ', error=expected)
        c.exchange(parse('', 'SELECT CAST(? AS INTEGER) FROM RDB$DATABASE', [20]) + packet(b'S'),
                   b'EZ', error='0A000')
        c.exchange(parse('', 'SELECT CAST(1 AS NUMERIC(18,2)) FROM RDB$DATABASE') +
                   bind(results=[1]) + execute() + packet(b'S'), b'1EZ', error='0A000')
        c.run(ANSWER, rows=[['42']], tag='SELECT 1')
        self.finish(c)

    def malformed_frames(self):
        malformed = (
            packet(b'B', string('') + string('') + struct.pack('!HHi', 0, 1, -2)),
            packet(b'P', string('') + string(ANSWER) + struct.pack('!H', 1)),
            packet(b'E', string('') + b'\0\0\0'),
            describe(b'?', 'missing'))
        for frame in malformed:
            c = ExtendedClient(self).login()
            c.send(frame)
            kind, body = c.message()
            failure = error_fields(body) if kind == b'E' else {}
            require(kind == b'E' and failure.get('C') == '08P01' and failure.get('S') == 'FATAL',
                    f'malformed Extended frame was not fatal: {kind!r}, {failure!r}')
            require_closed(c.connection)
            self.finish(c, terminate=False)

    def disconnect_and_shutdown(self):
        for identifier, terminate in ((401, True), (402, False)):
            c = ExtendedClient(self).login()
            c.exchange(parse('stream', 'SELECT V FROM P7_STREAM(?)') + bind('stream', 'stream', [str(identifier)]) +
                       execute('stream', 1) + packet(b'H'), b'12Ds', end=b's', rows=[['1']])
            self.transaction_count(1)
            self.finish(c, terminate)
            self.seen(identifier, 0)
        c = ExtendedClient(self).login()
        c.exchange(parse('stream', 'SELECT V FROM P7_STREAM(403)') + bind('stream', 'stream') +
                   execute('stream', 1) + packet(b'H'), b'12Ds', end=b's', rows=[['1']])
        self.native_probe('native_while_portal_suspended')
        self.stop()
        require_closed(c.connection)
        c.close(terminate=False)
        output = self.isql('shutdown_portal_rollback', f"CONNECT '{self.database}';\nSET LIST ON;\n"
            'SELECT COUNT(*) AS OBSERVED FROM P7_EFFECTS WHERE ID=403;\nCOMMIT;\nQUIT;\n', embedded=True)
        require(field(output, 'OBSERVED') == '0', 'server shutdown committed suspended portal work')

    def build_c_driver(self):
        source = Path(__file__).with_name('pgwire_libpq_extended.c')
        self.c_driver = self.work / 'pgwire_libpq_extended'
        result = subprocess.run([self.args.cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
            '-I', str(self.args.libpq_include_dir), str(source), str(self.args.libpq_library), '-o', str(self.c_driver)],
            capture_output=True, text=True, timeout=self.timeout)
        (self.work / 'libpq-compile.log').write_text(result.stdout + result.stderr)
        require(result.returncode == 0, 'C libpq Extended Query compilation failed')
        self.summary['c_build'] = {'headers': str(self.args.libpq_include_dir),
            'library': str(self.args.libpq_library), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}

    def run_extended(self):
        native, configured, port = reserve_ports(3)
        self.start('extended', native, configured, [{'alias': 'PgWire', 'port': port}])
        self.finish(ExtendedClient(self).login())
        for name, action in (
            ('direct_c_libpq_params_prepared_async_and_binary', self.c_libpq),
            ('describe_named_unnamed_replacement_close_and_simple_switch', self.descriptions_and_lifetimes),
            ('input_output_inference_null_utf8_text_binary_and_format_vectors', self.formats),
            ('independent_portals_suspend_resume_close_and_original_rowcount', self.portals),
            ('flush_sync_commit_rollback_and_simple_cycle_boundary', self.cycle_boundaries),
            ('parse_bind_execute_discard_until_sync_no_side_effects', self.discard_and_recovery),
            ('explicit_transactions_failed_state_savepoint_and_prepared_reuse', self.explicit_transactions),
            ('extended_native_controls_commit_rollback_savepoint_failed_commit_and_retaining', self.extended_controls),
            ('bind_input_output_format_errors_precede_irreversible_effects', self.bind_guards_before_effects),
            ('reset_invalidates_objects_without_hidden_prepare_transactions', self.reset),
            ('statement_portal_limits_and_close_recovery', self.limits),
            ('retained_sql_byte_limit_and_close_recovery', self.retained_bytes_limit),
            ('empty_queries_input_validation_and_deferred_binary_numeric', self.empty_and_codec_errors),
            ('malformed_extended_frames_are_fatal_and_release_attachment', self.malformed_frames),
            ('terminate_eof_shutdown_release_suspended_portals_and_rollback', self.disconnect_and_shutdown)):
            self.check(name, action)


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
    harness = ExtendedHarness(source, work.resolve(), args.timeout, args)
    try:
        harness.prepare()
        harness.build_c_driver()
        harness.run_extended()
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
