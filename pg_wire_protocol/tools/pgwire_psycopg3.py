#!/usr/bin/env python3
"""Real Psycopg 3 profiles against the PG* environment supplied by an isolated runner.

Requires P9_ROWS(ID INTEGER PRIMARY KEY,V INTEGER) with DML rights. IDs9301..9304
belong to this fixture. No SQL rewriting, pipeline or custom driver adapters.
The date literal case records an unsupported stock driver adaptation. Optional libpq traces begin only after authentication.
API references: https://www.psycopg.org/psycopg3/docs/api/cursors.html
https://www.psycopg.org/psycopg3/docs/advanced/prepare.html
https://www.psycopg.org/psycopg3/docs/api/pq.html
"""
import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
import json
import os
from pathlib import Path
import platform

import psycopg
from psycopg import pq, sql


ANSWER = 'SELECT CAST(42 AS INTEGER) AS "answer" FROM RDB$DATABASE'
SCALARS = """SELECT CAST(-32768 AS SMALLINT),CAST('9223372036854775807' AS BIGINT),
CAST(TRUE AS BOOLEAN),CAST('Я x ' AS VARCHAR(8)),CAST('Я' AS CHAR(4)),CAST(NULL AS INTEGER),
CAST(1.25 AS FLOAT),CAST(-2.5 AS DOUBLE PRECISION),
CAST('-170141183460469231731687303715884105728' AS INT128),
CAST('0.00000000000000000000000000000000000001' AS NUMERIC(38,38)),
DATE '1999-12-31',TIME '00:00:00.0001',TIMESTAMP '1999-12-31 23:59:59.9999'
FROM RDB$DATABASE"""
EXPECTED = (-32768,9223372036854775807,True,'Я x ','Я   ',None,1.25,-2.5,
            Decimal('-170141183460469231731687303715884105728'),Decimal('1e-38'),
            date(1999,12,31),time(0,0,0,100),datetime(1999,12,31,23,59,59,999900))
OIDS = [21,20,16,1043,1042,23,700,701,1700,1700,1082,1083,1114]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def state(connection, expected):
    require(connection.info.transaction_status == expected,
            f'expected transaction {expected.name}, got {connection.info.transaction_status.name}')


def scalar_metadata(cursor, binary):
    require([column.type_code for column in cursor.description] == OIDS, 'P8 scalar OIDs differ')
    require([cursor.pgresult.fformat(i) for i in range(len(OIDS))] == [int(binary)]*len(OIDS),
            'driver did not receive requested result format')
    require(cursor.rowcount == 1 and cursor.statusmessage == 'SELECT 1', 'scalar rowcount/tag differs')


def expected_error(action, expected):
    try:
        action()
    except psycopg.Error as error:
        require(error.sqlstate == expected, f'expected SQLSTATE {expected}, got {error.sqlstate}')
    else:
        raise RuntimeError(f'expected SQLSTATE {expected}, command succeeded')


class Profiles:
    def __init__(self, trace_dir):
        self.trace_dir = trace_dir
        self.summary = {'passed':False,'psycopg_version':psycopg.__version__,
            'libpq_runtime_version':pq.version(),'libpq_build_version':pq.__build_version__,
            'pq_implementation':pq.__impl__,'python_version':platform.python_version(),
            'checks':[],'expected_limits':[],'traces':[],
            'scope':'Actual Cursor/AsyncCursor; zero parameters; native SQL; no pipeline or SQL rewrite'}
        if trace_dir:
            trace_dir.mkdir(mode=0o700,parents=True,exist_ok=True)

    @contextmanager
    def trace(self, connection, name):
        stream, path = None, None
        if self.trace_dir:
            path = self.trace_dir / (name + '.trace')
            stream = path.open('xb')
            connection.pgconn.trace(stream.fileno())
            connection.pgconn.set_trace_flags(pq.Trace.SUPPRESS_TIMESTAMPS)
        try:
            yield
        finally:
            if stream:
                if not connection.closed:
                    connection.pgconn.untrace()
                stream.close()
                counts = Counter()
                for line in path.read_text(errors='replace').splitlines():
                    fields = line.split('\t')
                    if len(fields) >= 3 and fields[0] == 'F':
                        counts[fields[2]] += 1
                self.summary['traces'].append({'profile':name,'path':str(path),
                    'frontend_messages':dict(counts),'started_after_authentication':True})

    @contextmanager
    def connect(self, name, **kwargs):
        connection = psycopg.connect('',application_name='pgwire_p9_'+name,**kwargs)
        try:
            require(connection.pgconn.protocol_version == 3, 'driver did not connect using protocol 3')
            with self.trace(connection,name):
                yield connection
        finally:
            connection.close()

    def sync_simple(self):
        with self.connect('simple',autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(SCALARS,prepare=False)
                require(cursor.fetchone() == EXPECTED and cursor.fetchone() is None, 'Simple scalar decoding differs')
                scalar_metadata(cursor,False)
                state(connection,pq.TransactionStatus.IDLE)
                cursor.execute('SELECT CAST(1 AS INTEGER) FROM RDB$DATABASE WHERE 1=0',prepare=False)
                require(cursor.fetchall() == [] and cursor.rowcount == 0, 'zero-row Simple result differs')
        self.summary['checks'].append('ordinary_cursor_autocommit_simple_p8_scalars_and_empty_result')

    def sync_prepared(self):
        with self.connect('prepared',autocommit=True) as connection:
            with connection.cursor() as cursor:
                for binary in (False,True,True):
                    cursor.execute(SCALARS,prepare=True,binary=binary)
                    require(cursor.fetchone() == EXPECTED, 'prepared P8 scalar decoding differs')
                    scalar_metadata(cursor,binary)
                    state(connection,pq.TransactionStatus.IDLE)
                cursor.execute('SET TRANSACTION READ WRITE READ COMMITTED NO WAIT',prepare=False)
                state(connection,pq.TransactionStatus.INTRANS)
                cursor.execute(ANSWER,prepare=True)
                require(cursor.fetchone() == (42,), 'prepared query in explicit transaction differs')
                cursor.execute('COMMIT',prepare=False)
                state(connection,pq.TransactionStatus.IDLE)
                cursor.execute(ANSWER,prepare=True)
                require(cursor.fetchone() == (42,), 'prepared query did not survive COMMIT')
                connection.prepared_max = 2
                for number in (61,62,63,64):
                    cursor.execute(f'SELECT CAST({number} AS INTEGER) FROM RDB$DATABASE',prepare=True)
                    require(cursor.fetchone() == (number,), 'prepared LRU query differs')
                state(connection,pq.TransactionStatus.IDLE)
        self.summary['checks'].append('forced_extended_text_binary_prepared_reuse_commit_and_named_lru_close')

    def native_transactions(self):
        # This is an explicit, public driver setting: no prepared cache whose
        # rollback maintenance would emit unsupported DEALLOCATE ALL.
        with self.connect('native_transactions',autocommit=True,prepare_threshold=None) as connection:
            with connection.cursor() as cursor:
                def command(text, expected, tag):
                    cursor.execute(text)
                    state(connection,expected)
                    require(cursor.statusmessage == tag, f'{text}: unexpected command tag {cursor.statusmessage}')
                command('SET TRANSACTION READ WRITE READ COMMITTED NO WAIT',pq.TransactionStatus.INTRANS,'BEGIN')
                command('INSERT INTO P9_ROWS VALUES(9301,11)',pq.TransactionStatus.INTRANS,'INSERT 0 1')
                command('COMMIT',pq.TransactionStatus.IDLE,'COMMIT')
                command('SET TRANSACTION',pq.TransactionStatus.INTRANS,'BEGIN')
                command('INSERT INTO P9_ROWS VALUES(9302,22)',pq.TransactionStatus.INTRANS,'INSERT 0 1')
                command('ROLLBACK',pq.TransactionStatus.IDLE,'ROLLBACK')
                command('SET TRANSACTION',pq.TransactionStatus.INTRANS,'BEGIN')
                command('INSERT INTO P9_ROWS VALUES(9303,33)',pq.TransactionStatus.INTRANS,'INSERT 0 1')
                command('SAVEPOINT PSYCO_RECOVER',pq.TransactionStatus.INTRANS,'SAVEPOINT')
                command('INSERT INTO P9_ROWS VALUES(9304,44)',pq.TransactionStatus.INTRANS,'INSERT 0 1')
                expected_error(lambda:cursor.execute('INSERT INTO P9_ROWS VALUES(9301,99)'),'23505')
                state(connection,pq.TransactionStatus.INERROR)
                expected_error(lambda:cursor.execute(ANSWER),'25P02')
                command('ROLLBACK TO SAVEPOINT PSYCO_RECOVER',pq.TransactionStatus.INTRANS,'ROLLBACK')
                command('RELEASE SAVEPOINT PSYCO_RECOVER',pq.TransactionStatus.INTRANS,'RELEASE')
                command('COMMIT',pq.TransactionStatus.IDLE,'COMMIT')
                command('SET TRANSACTION',pq.TransactionStatus.INTRANS,'BEGIN')
                command('INSERT INTO P9_ROWS VALUES(9304,44)',pq.TransactionStatus.INTRANS,'INSERT 0 1')
                expected_error(lambda:cursor.execute('INSERT INTO P9_ROWS VALUES(9301,99)'),'23505')
                command('COMMIT',pq.TransactionStatus.IDLE,'ROLLBACK')
                cursor.execute('SELECT ID,V FROM P9_ROWS WHERE ID BETWEEN 9301 AND 9304 ORDER BY ID',binary=True)
                require(cursor.fetchall() == [(9301,11),(9303,33)], 'native commit/rollback effects differ')
        self.summary['checks'].append('native_transaction_controls_I_T_E_savepoint_and_failed_commit_no_prepare_cache')
        self.summary['committed_fixture_rows'] = [[9301,11],[9303,33]]
        self.summary['absent_fixture_ids'] = [9302,9304]

    def grammar_limits(self):
        with self.connect('default_begin',autocommit=False) as connection:
            expected_error(lambda:connection.execute(ANSWER),'42601')
            state(connection,pq.TransactionStatus.IDLE)
        self.summary['expected_limits'].append({'profile':'default_autocommit_false_implicit_BEGIN','sqlstate':'42601'})
        with self.connect('parameter_and_literal',autocommit=True) as connection:
            expected_error(lambda:connection.execute('SELECT CAST(%s AS INTEGER) FROM RDB$DATABASE',(42,)),'42601')
            state(connection,pq.TransactionStatus.IDLE)
            query = sql.SQL('SELECT {} FROM RDB$DATABASE').format(sql.Literal(date(2000,1,1)))
            rendered = query.as_string(connection)
            require('::date' in rendered, 'installed driver did not generate the expected date literal cast')
            expected_error(lambda:connection.execute(query),'42601')
            require(connection.execute(ANSWER,prepare=False).fetchone() == (42,), 'no recovery after parser limits')
        self.summary['expected_limits'].extend([
            {'profile':'ordinary_cursor_percent_s_generates_dollar_parameter','sqlstate':'42601'},
            {'profile':'sql_Literal_date_generates_postgresql_cast','sqlstate':'42601','generated_sql':rendered}])

    def rollback_cache_limit(self):
        with self.connect('prepared_rollback_limit',autocommit=True) as connection:
            require(connection.execute(ANSWER,prepare=True).fetchone() == (42,), 'prepared-cache setup failed')
            connection.execute('SET TRANSACTION',prepare=False)
            connection.execute('INSERT INTO P9_ROWS VALUES(9304,44)',prepare=False)
            # ROLLBACK itself succeeds; stock Psycopg then emits DEALLOCATE ALL.
            expected_error(lambda:connection.execute('ROLLBACK',prepare=False),'42601')
            state(connection,pq.TransactionStatus.IDLE)
            require(connection.execute('SELECT COUNT(*) FROM P9_ROWS WHERE ID=9304',prepare=False).fetchone() == (0,),
                    'ROLLBACK did not happen before the driver maintenance error')
            require(connection.execute(ANSWER,prepare=False).fetchone() == (42,), 'maintenance error did not recover')
        self.summary['expected_limits'].append({'profile':'prepared_cache_ROLLBACK_maintenance_DEALLOCATE_ALL',
            'sqlstate':'42601','rollback_completed':True,
            'supported_native_control_profile':'autocommit=True, prepare_threshold=None'})

    async def asynchronous(self):
        connection = await psycopg.AsyncConnection.connect('',autocommit=True,
            application_name='pgwire_p9_async')
        try:
            with self.trace(connection,'async'):
                async with connection.cursor() as cursor:
                    for prepare,binary in ((False,False),(True,False),(True,True)):
                        await cursor.execute(SCALARS,prepare=prepare,binary=binary)
                        require(await cursor.fetchone() == EXPECTED, 'AsyncCursor scalar decoding differs')
                        scalar_metadata(cursor,binary)
                        state(connection,pq.TransactionStatus.IDLE)
                    await cursor.execute('SET TRANSACTION',prepare=False)
                    state(connection,pq.TransactionStatus.INTRANS)
                    await cursor.execute(ANSWER,prepare=True)
                    require(await cursor.fetchone() == (42,), 'AsyncCursor prepared transaction result differs')
                    await cursor.execute('COMMIT',prepare=False)
                    state(connection,pq.TransactionStatus.IDLE)
                    await cursor.execute(ANSWER,prepare=True)
                    require(await cursor.fetchone() == (42,), 'AsyncCursor prepared reuse after COMMIT failed')
        finally:
            await connection.close()
        self.summary['checks'].append('async_connection_simple_extended_binary_and_native_commit')

    def validate_traces(self):
        if not self.trace_dir:
            return
        traces = {entry['profile']:entry for entry in self.summary['traces']}
        require(traces['simple']['frontend_messages'].get('Query',0) >= 2, 'Simple profile lacks actual Query frames')
        for name in ('prepared','async'):
            counts = traces[name]['frontend_messages']
            require(all(counts.get(kind,0) for kind in ('Parse','Bind','Execute','Sync')),
                    f'{name}: actual Extended frames absent')
        require(traces['prepared']['frontend_messages'].get('Close',0) > 0, 'LRU did not send native protocol Close')
        for name, fragment in (('default_begin','"BEGIN"'),('parameter_and_literal','$1'),
                               ('prepared_rollback_limit','DEALLOCATE ALL')):
            require(fragment in Path(traces[name]['path']).read_text(), f'{name}: expected driver SQL absent in trace')
        self.summary['checks'].append('actual_simple_extended_close_and_driver_limit_wire_traces')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace-dir',type=Path,default=os.environ.get('PGWIRE_TRACE_DIR'))
    args = parser.parse_args()
    profiles = Profiles(args.trace_dir)
    try:
        profiles.sync_simple()
        profiles.sync_prepared()
        profiles.native_transactions()
        profiles.grammar_limits()
        profiles.rollback_cache_limit()
        asyncio.run(profiles.asynchronous())
        profiles.validate_traces()
        profiles.summary['passed'] = True
    except Exception as error:
        message = f'{type(error).__name__}: {error}'
        password = os.environ.get('PGPASSWORD')
        if password:
            message = message.replace(password,'[REDACTED]')
        profiles.summary['error'] = message
    print(json.dumps(profiles.summary,ensure_ascii=False))
    return 0 if profiles.summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
