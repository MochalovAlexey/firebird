#!/usr/bin/env python3
"""Observe original 1C SQL or test PG dialect features in a private runtime.

--baseline reads external CAPTURE/profile/selected; errors are observations,
not acceptance. Extended examples are prepared/described without execution or
guessed Bind values. No captured SQL, parameter DETAIL or result data is saved.
--settings checks the fixed English/UTF8 and client diagnostics profile.
The external subprocess watchdog configures no server timeout.
"""
import argparse
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

from pgwire_client import Connection, decode
from pgwire_extended_query_smoke import ExtendedClient, parse, bind, execute, close, row
from pgwire_startup_smoke import StartupHarness, Client, packet, error_fields
from wire_host_smoke import require, reserve_ports


RECOVERY = 'SELECT CAST(42 AS INTEGER) AS ANSWER FROM RDB$DATABASE'
SELECTS = [
    ('constant', 'SELECT 42 AS ANSWER', ['42'], 23),
    ('where_false', 'SELECT 42 AS ANSWER WHERE 1=0', [], 23),
    ('count', 'SELECT COUNT(*) AS ANSWER', ['1'], 20),
    ('empty_count', 'SELECT COUNT(*) AS ANSWER WHERE 1=0', ['0'], 20),
    ('sum', 'SELECT SUM(CAST(7 AS INTEGER)) AS ANSWER', ['7'], 20),
    ('subquery', 'SELECT CAST((SELECT 41) + 1 AS INTEGER) AS ANSWER', ['42'], 23),
    ('union', 'SELECT 1 AS ANSWER UNION ALL SELECT 2 ORDER BY 1', ['1', '2'], 23),
    ('cast', "SELECT CAST('42' AS INTEGER) AS ANSWER", ['42'], 23),
    ('group', 'SELECT 7 AS ANSWER GROUP BY 1 HAVING COUNT(*)=1', ['7'], 23),
    ('old_as', 'SELECT CAST((SELECT 42 FROM RDB$DATABASE) AS INTEGER) AS "AS" FROM RDB$DATABASE', ['42'], 23),
    ('old_returning_alias', 'SELECT 42 RETURNING FROM RDB$DATABASE', ['42'], 23),
    ('old_sql_alias', 'SELECT 42 SQL FROM RDB$DATABASE', ['42'], 23),
    ('old_optimize_alias', 'SELECT 42 OPTIMIZE FROM RDB$DATABASE', ['42'], 23),
    ('optimize_clause', 'SELECT 1 OPTIMIZE FOR FIRST ROWS', ['1'], 23),
]
COLUMN_NAMES = {'old_as':'AS', 'old_returning_alias':'RETURNING', 'old_sql_alias':'SQL',
                'old_optimize_alias':'OPTIMIZE', 'optimize_clause':'CONSTANT'}


class Libpq(Connection):
    def __init__(self, config):
        super().__init__(SimpleNamespace(host='127.0.0.1', port=config['port'], database='wire_host',
            user='WIRE_HOST_TEST', connect_timeout=5, libpq=config.get('libpq')), config['password'])
        for name, result, args in [
            ('PQsendQuery', C.c_int, [C.c_void_p, C.c_char_p]),
            ('PQgetResult', C.c_void_p, [C.c_void_p]),
            ('PQprepare', C.c_void_p, [C.c_void_p,C.c_char_p,C.c_char_p,C.c_int,C.c_void_p]),
            ('PQdescribePrepared', C.c_void_p, [C.c_void_p,C.c_char_p]),
            ('PQnparams', C.c_int, [C.c_void_p]),
            ('PQparamtype', C.c_uint, [C.c_void_p,C.c_int]),
            ('PQparameterStatus', C.c_char_p, [C.c_void_p,C.c_char_p]),
        ]:
            function = getattr(self.pq, name)
            function.restype, function.argtypes = result, args

    def result(self, handle, values=False):
        require(handle, 'libpq returned no result')
        try:
            n = self.pq.PQnfields(handle)
            data = {'status': self.pq.PQresultStatus(handle),
                'sqlstate': decode(self.pq.PQresultErrorField(handle, ord('C'))),
                'severity': decode(self.pq.PQresultErrorField(handle, ord('S'))),
                'tag': decode(self.pq.PQcmdStatus(handle)), 'row_count': self.pq.PQntuples(handle),
                'columns': [{'name': decode(self.pq.PQfname(handle,i)), 'oid': self.pq.PQftype(handle,i)}
                            for i in range(n)],
                'parameter_oids': [self.pq.PQparamtype(handle,i) for i in range(self.pq.PQnparams(handle))]}
            if values:
                data['rows'] = [[None if self.pq.PQgetisnull(handle,j,i) else
                    C.string_at(self.pq.PQgetvalue(handle,j,i),self.pq.PQgetlength(handle,j,i)).decode('utf8')
                    for i in range(n)] for j in range(data['row_count'])]
            return data
        finally:
            self.pq.PQclear(handle)

    def query(self, sql, values=False):
        require(self.pq.PQsendQuery(self.connection, sql if isinstance(sql,bytes) else sql.encode()) == 1,
                'libpq failed to send query')
        results = []
        while True:
            handle = self.pq.PQgetResult(self.connection)
            if not handle:
                break
            results.append(self.result(handle, values))
        require(results, 'query returned no results')
        return results

    def recover(self):
        if self.pq.PQtransactionStatus(self.connection) in (2,3):
            self.query('ROLLBACK')
        result = self.query(RECOVERY, True)
        require(result[-1].get('rows') == [['42']] and not result[-1]['sqlstate'] and
                self.pq.PQtransactionStatus(self.connection) == 0, 'session did not recover to SELECT 42 / I')


def backend_identity(first, config):
    second = Libpq(config)
    try:
        def identity(client, state=0):
            result = client.query('SELECT pg_catalog.pg_backend_pid()', True)[0]
            require(not result['sqlstate'] and client.pq.PQtransactionStatus(client.connection) == state and
                len(result['rows']) == 1 and len(result['columns']) == 1 and result['columns'][0]['oid'] == 23,
                'backend identity result/metadata differs')
            value = int(result['rows'][0][0])
            require(value > 0, 'backend attachment identity is not positive')
            return value, result['columns'][0]['name']
        identities = [identity(first), identity(second)]
        require(identities[0][0] != identities[1][0], 'two libpq connections share an attachment identity')
        for sql, state in [('SET TRANSACTION',2),('COMMIT',0),('SET TRANSACTION',2),
                           ('ROLLBACK',0),('ALTER SESSION RESET',0)]:
            require(not first.query(sql)[0]['sqlstate'], 'identity control failed: ' + sql)
            require(identity(first,state) == identities[0], 'identity changed after ' + sql)
        env = os.environ.copy()
        env.update(ISC_USER='WIRE_HOST_TEST', ISC_PASSWORD=config['password'])
        observer = subprocess.run([str(Path(config['runtime'])/'bin/isql'),'-quiet','-bail',
            f"inet://127.0.0.1:{config['native_port']}/wire_host"],
            input="SET ROLE PGWIRE_OBSERVER;\nSET LIST ON;\nSELECT MON$ATTACHMENT_ID AS PG_ID FROM MON$ATTACHMENTS "
                "WHERE MON$REMOTE_VERSION = 'PgWire' AND MON$SYSTEM_FLAG = 0;\nROLLBACK;\nQUIT;\n",
            text=True,capture_output=True,env=env,timeout=config['timeout'])
        output = first.redact(observer.stdout + observer.stderr)
        (Path(config['work'])/'backend_identities.log').write_text(output)
        observed = sorted(int(v) for v in re.findall(r'^PG_ID\s+(\d+)\s*$',output,re.MULTILINE))
        require(observer.returncode == 0 and observed == sorted(value for value, _ in identities),
                'native monitoring identities differ')
        return {'ids':observed,'column_name':identities[0][1],'oid':23,'driver':'two simultaneous libpq connections',
                'stable_after':['COMMIT','ROLLBACK','ALTER SESSION RESET'],
                'scope':'attachment ID within one database; not OS PID or cluster-wide identity'}
    finally:
        second.close()


def settings_client(config, client):
    """Ordinary-user settings; optional original startup SQL bytes."""
    queries_checked = 0
    def check(sql, tags=(), error='', state=0):
        nonlocal queries_checked
        results = client.query(sql, True)
        require([r['tag'] for r in results] == list(tags) + ([''] if error else []) and
                [r['sqlstate'] for r in results] == [''] * len(tags) + ([error] if error else []) and
                client.pq.PQtransactionStatus(client.connection) == state,
                'settings query differs: ' + repr(sql) + ' ' + json.dumps(results))
        queries_checked += 1
        return results

    def string_mode(expected):
        actual = client.pq.PQparameterStatus(client.connection, b'standard_conforming_strings')
        require(actual == expected.encode(), 'fixed setting changed string mode: '+repr(actual))

    locale_sql = b"SET lc_messages TO 'en_US.UTF-8'"
    locale_source = {'original_capture': False}
    if config['capture_dir'] != 'None':
        original = Path(config['capture_dir']) / 'profile/selected/02-lc-messages.sql'
        locale_sql = original.read_bytes()
        provenance = json.loads(original.with_suffix('.json').read_text())
        locale_source = {key: provenance[key] for key in ('capture_seq', 'session_id', 'kind', 'sql_id')}
        locale_source.update(original_capture=True, source=str(original))
    locale_source['sha256'] = hashlib.sha256(locale_sql).hexdigest()
    check(locale_sql, ['SET'])
    check("SET lc_messages = 'en_US.UTF-8'; SET client_encoding = 'utf8'", ['SET', 'SET'])
    check("SET client_encoding TO UTF8; SET client_encoding = 'UTF-8'", ['SET', 'SET'])
    fragments = [b'SET escape_string_warning = off;', b'SET client_min_messages=error;']
    if config['capture_dir'] != 'None':
        batch_source = (Path(config['capture_dir'])/'profile/selected/03-session-batch.sql').read_bytes()
        fragments = [next(line for line in batch_source.splitlines(keepends=True) if line.startswith(prefix))
                     for prefix in (b'SET escape_string_warning ', b'SET client_min_messages=')]
    for sql in fragments:
        check(sql, ['SET'])
    fixed = 'SET escape_string_warning = off; SET client_min_messages=error'
    check(b'\n'.join(fragments), ['SET','SET'])
    for sql in ("SET escape_string_warning TO 'OfF'", "set ESCAPE_STRING_WARNING = OFF",
                "SET client_min_messages TO 'ErRoR'", "set CLIENT_MIN_MESSAGES = ERROR"):
        check(sql, ['SET'])
    identifiers = check('SELECT 3 AS ESCAPE_STRING_WARNING, 4 AS CLIENT_MIN_MESSAGES', ['SELECT 1'])[0]
    require(identifiers['rows']==[['3','4']], 'PG non-reserved setting identifiers changed')
    for name, prefix in (('escape_string_warning','off'), ('client_min_messages','error')):
        for bad in ('on', 'notice', 'warning', "'bad'", "''", "'"+prefix+"x'", "'"+prefix+" '"):
            check('SET '+name+' = '+bad, error='22023')
            string_mode('on')
        check('SET '+name+' = 1', error='42601')
        check('EXECUTE BLOCK AS BEGIN SET '+name+' = '+prefix+'; END', error='0A000')
    for sql in ('SET LOCAL escape_string_warning=off', 'SET SESSION client_min_messages=error',
                'SET client_min_messages=DEFAULT', 'SHOW client_min_messages'):
        check(sql, error='42601')
    string_mode('on')

    # Fixed SET failures use the existing abort path and undo earlier mutable SETs.
    for number, invalid in enumerate(('SET escape_string_warning=on', 'SET client_min_messages=notice')):
        first = 513 + 2*number
        check(f'INSERT INTO WIRE_HOST_ROWS VALUES({first}); SET standard_conforming_strings=off; '+
              invalid+f'; INSERT INTO WIRE_HOST_ROWS VALUES({first+1})', ['INSERT 0 1','SET'], error='22023')
        string_mode('on')
        check('SET TRANSACTION; SET standard_conforming_strings=off', ['BEGIN','SET'], state=2)
        string_mode('off')
        check(invalid, error='22023', state=3)
        string_mode('on')
        check(fixed, error='25P02', state=3)
        check('ROLLBACK', ['ROLLBACK'])
        check(fixed, ['SET','SET'])
    for control in ('COMMIT', 'ROLLBACK', 'ALTER SESSION RESET'):
        check('SET standard_conforming_strings=off', ['SET'])
        check('SET TRANSACTION; '+fixed, ['BEGIN','SET','SET'], state=2)
        string_mode('off')
        resetting = control == 'ALTER SESSION RESET'
        check(control, ['RESET' if resetting else control], state=2 if resetting else 0)
        string_mode('on' if resetting else 'off')
        check(fixed, ['SET','SET'], state=2 if resetting else 0)
        string_mode('on' if resetting else 'off')
        if resetting:
            check('ROLLBACK', ['ROLLBACK'])
    check('ALTER SESSION RESET', ['RESET'])
    string_mode('on')
    result = check("SELECT CAST('Я🙂' AS VARCHAR(10)) FROM RDB$DATABASE", ['SELECT 1'])[0]
    require(result['rows'] == [['Я🙂']] and result['columns'][0]['oid'] == 1043,
            'UTF8 text changed after SET client_encoding')
    charset = check('SELECT TRIM(C.RDB$CHARACTER_SET_NAME) FROM MON$ATTACHMENTS A '
        'JOIN RDB$CHARACTER_SETS C ON C.RDB$CHARACTER_SET_ID=A.MON$CHARACTER_SET_ID '
        'WHERE A.MON$ATTACHMENT_ID=CURRENT_CONNECTION', ['SELECT 1'])
    require(charset[0]['rows'] == [['UTF8']], 'SET accepted an attachment without actual UTF8')
    for sql in ("SET client_encoding TO 'LATIN1'", "SET lc_messages TO 'ru_RU.UTF-8'",
                "SET lc_messages TO ''", "SET client_encoding TO ''"):
        check(sql, error='22023')
        client.recover()
    # An accepted fixed English setting must leave real error diagnostics usable.
    handle = client.pq.PQexec(client.connection, b'SELECT (')
    require(handle, 'libpq returned no diagnostic')
    try:
        message = decode(client.pq.PQresultErrorField(handle, ord('M')))
        require(decode(client.pq.PQresultErrorField(handle, ord('C'))) == '42601' and
                message == 'SQL syntax error', 'fixed English diagnostic differs: ' + message)
    finally:
        client.pq.PQclear(handle)
    client.recover()
    check('INSERT INTO WIRE_HOST_ROWS VALUES(511); '
          "SET client_encoding TO 'LATIN1'; INSERT INTO WIRE_HOST_ROWS VALUES(512)",
          ['INSERT 0 1'], error='22023')
    check('SET TRANSACTION; ' + "SET client_encoding = 'utf8'", ['BEGIN', 'SET'], state=2)
    check("SET lc_messages TO 'ru_RU.UTF-8'; ROLLBACK", error='22023', state=3)
    check("SET client_encoding = 'utf8'", error='25P02', state=3)
    check("ROLLBACK; SET client_encoding = 'UTF8'", ['ROLLBACK', 'SET'])
    remaining = check('SELECT COUNT(*) FROM WIRE_HOST_ROWS WHERE ID BETWEEN 511 AND 516', ['SELECT 1'])
    require(remaining[0]['rows'] == [['0']], 'settings error committed or continued script writes')
    if config['capture_dir'] != 'None':
        original = Path(config['capture_dir']) / 'profile/selected/03-session-batch.sql'
        batch = original.read_bytes()
    else:
        batch = b"SET enable_mergejoin = off; SET client_encoding = 'utf8'"
    check(batch, error='42601')
    client.recover()
    return {'user': 'WIRE_HOST_TEST', 'lc_messages_query': locale_source,
            'queries_checked':queries_checked,
            'diagnostics_fragments': {'original_capture':config['capture_dir'] != 'None',
                'sha256':[hashlib.sha256(sql).hexdigest() for sql in fragments], 'whole_batch_passed':False},
            'fixed_diagnostics':['escape_string_warning=off','client_min_messages=error'],
            'mutable_setting_restored_on_error':True,
            'postgresql_privilege_model': 'deferred',
            'unsupported_value': '22023', 'english_error': message, 'utf8_text': 'Я🙂',
            'transaction_states': ['I', 'T', 'E', 'I'], 'script_writes_rolled_back': True,
            'remaining_startup_batch': {'sqlstate': '42601', 'sha256': hashlib.sha256(batch).hexdigest(),
                'original_capture': config['capture_dir'] != 'None'}}


def client_child():
    config = json.load(sys.stdin)
    connection = None
    try:
        connection = Libpq(config)
        output = {'libpq_version': connection.pq.PQlibVersion(), 'cases': []}
        if config.get('strings'):
            from pgwire_strings_checks import strings_client
            output['strings'] = strings_client(config, connection)
        elif config.get('settings'):
            output['settings'] = settings_client(config, connection)
        elif config['baseline']:
            selected = Path(config['capture_dir']) / 'profile/selected'
            index = json.loads((selected / 'index.json').read_text())
            require(len(index) == 24, 'expected the 24 selected capture cases')
            for item in index:
                name = item['case']
                require(re.fullmatch(r'[0-9]{2}-[a-z0-9-]+', name), 'invalid selected case name')
                sql = (selected / (name + '.sql')).read_bytes()
                digest = hashlib.sha256(sql).hexdigest()
                require(digest == item['sql_sha256'], 'captured SQL hash differs: ' + name)
                observed = {key: item[key] for key in ('case','capture_seq','session_id','kind','sql_id')}
                observed.update(sql_sha256=digest, sql_bytes=len(sql))
                if item['kind'] == 'simple':
                    observed.update(operation='Simple Query', results=connection.query(sql))
                else:
                    require(item['kind'] == 'execute', 'unknown recorded query kind')
                    prepared = connection.result(connection.pq.PQprepare(connection.connection,b'',sql,0,None))
                    observed.update(operation='Parse/Describe only; no Bind or Execute', results=[prepared])
                    if not prepared['sqlstate']:
                        observed['results'].append(connection.result(
                            connection.pq.PQdescribePrepared(connection.connection,b'')))
                observed['transaction_status'] = connection.pq.PQtransactionStatus(connection.connection)
                connection.recover()
                observed['recovery_select_42_idle'] = True
                output['cases'].append(observed)
            output['compatibility_claim'] = 'none: independent observations on an empty application schema, not replay'
        else:
            for name, sql, expected, oid in SELECTS:
                result = connection.query(sql, True)
                require(len(result) == 1 and result[0].get('rows') == [[v] for v in expected] and
                    result[0]['tag'] == 'SELECT ' + str(len(expected)) and
                    [c['oid'] for c in result[0]['columns']] == [oid], 'SELECT case differs: ' + name)
                connection.recover()
                output['cases'].append({'case': name, 'sql': sql, 'result': result[0]})
            for sql, tag in [('CREATE TABLE ONE_C_DIALECT_NUMERIC(V NUMERIC(18,2))','CREATE TABLE'),
                    ("INSERT INTO ONE_C_DIALECT_NUMERIC VALUES(CAST('1234567890123456.78' AS NUMERIC(18,2)))",'INSERT 0 1'),
                    ('SET TRANSACTION','BEGIN'), ('UPDATE ONE_C_DIALECT_NUMERIC SET V=0','UPDATE 1'),
                    ('ROLLBACK','ROLLBACK')]:
                result = connection.query(sql)
                require(len(result) == 1 and result[0]['tag'] == tag and not result[0]['sqlstate'],
                        'dialect-4 numeric DDL/DML/control failed: ' + sql + ' ' + json.dumps(result))
            numeric = connection.query('SELECT V FROM ONE_C_DIALECT_NUMERIC', True)[0]
            require(numeric['rows'] == [['1234567890123456.78']] and numeric['columns'][0]['oid'] == 1700,
                    'dialect-4 native numeric precision/metadata/rollback differs')
            require(connection.query('DROP TABLE ONE_C_DIALECT_NUMERIC')[0]['tag'] == 'DROP TABLE',
                    'dialect-4 numeric fixture DROP failed')
            connection.recover()
            output['dialect_numeric_ddl_rollback'] = numeric
            identity = connection.query('SELECT pg_catalog.pg_backend_pid()', True)[0]
            require(not identity['sqlstate'] and identity['columns'][0]['oid'] == 23 and
                len(identity['rows']) == 1 and int(identity['rows'][0][0]) > 0, 'libpq backend identity differs')
            output['backend_identity'] = identity
            output['concurrent_backend_identity'] = backend_identity(connection, config)
            for sql, expected in [('SELECT (','42601'),('SELECT 1/0 AS ANSWER','22012'),
                                  ('SELECT *','42000'),('SELECT NO_SUCH_COLUMN','42703')]:
                result = connection.query(sql)
                require(result[-1]['sqlstate'].startswith(expected) and result[-1]['severity'] != 'FATAL',
                        'negative SELECT diagnostic differs: ' + sql + ' ' + json.dumps(result))
                connection.recover()
                output.setdefault('error_recovery', []).append({'sql':sql, 'result':result[-1]})
        print(json.dumps(output))
        return 0
    except Exception as error:
        print(json.dumps({**locals().get('output', {}),
            'error': str(error).replace(config['password'],'[REDACTED]')}))
        return 1
    finally:
        if connection:
            connection.close()


class OneCHarness(StartupHarness):
    def configure(self, configured_native_port, endpoints):
        super().configure(configured_native_port, endpoints)
        with (self.runtime / 'firebird.conf').open('a') as config:
            config.write('MaxStatementCacheSize = 2097152\n')
        if endpoints:
            path = self.runtime / 'plugins.conf'
            path.write_text(path.read_text().replace('Workers = 4','Workers = 6'))

    def native_dialect_isolation(self, raw):
        env = self.env.copy()
        env.update(ISC_USER='WIRE_HOST_TEST',ISC_PASSWORD=self.password)
        sql = 'SELECT 42 AS ANSWER'
        for dialect in (1,2,3):
            before = raw.query(sql.encode())
            require(not before['error'] and before['rows'] == [['42']], 'PG cache warmup failed')
            result = subprocess.run([str(self.runtime/'bin/isql'),'-quiet','-charset','UTF8','-sql_dialect',str(dialect),
                f'inet://127.0.0.1:{self.native_port}/wire_host'],
                input='SET LIST ON;\n' + sql + ';\n' + RECOVERY + ';\nROLLBACK;\nQUIT;\n',
                capture_output=True,text=True,env=env,cwd=self.temp,timeout=self.timeout)
            output = result.stdout + result.stderr
            (self.work/f'native_dialect_{dialect}.log').write_text(self.redact(output))
            require(result.returncode != 0 and re.findall(r'SQLSTATE\s*=\s*(\w+)',output) == ['42000'] and
                re.findall(r'^ANSWER\s+(\S+)\s*$',output,re.MULTILINE) == ['42'],
                f'native dialect {dialect} did not reject no-FROM and recover on the same connection')
            after = raw.query(sql.encode())
            require(not after['error'] and after['rows'] == [['42']] and after['state'] == 'I',
                    'native prepare changed PG result/cache')
        self.summary['native_dialect_isolation'] = {'rejected_dialects':[1,2,3], 'native_sqlstate':'42000',
            'same_sql_pg_native_pg':True, 'pg_connection_held_open':True, 'native_recovery_select_42':True,
            'max_statement_cache_bytes':2097152, 'scope':'separate native/PG attachments to one database'}

    def settings_protocols(self):
        statements = ("SET lc_messages TO 'en_US.UTF-8'", "SET client_encoding = 'utf8'",
                      'SET escape_string_warning = off', 'SET client_min_messages=error')
        raw = ExtendedClient(self).login()
        wire_diagnostics = []
        try:
            for name, sql in zip(('locale', 'encoding', 'escape', 'messages'), statements):
                raw.exchange(parse(name, sql) + packet(b'S'), b'1Z')
                for attempt in range(2):
                    portal = name + str(attempt)
                    raw.exchange(bind(portal, name) + packet(b'H'), b'2', end=b'2')
                    raw.exchange(execute(portal) + packet(b'S'), b'CZ', tag='SET')
                raw.exchange(close(b'S', name) + packet(b'S'), b'3Z')
            for index, sql in enumerate(("SET client_encoding TO 'LATIN1'", 'SET escape_string_warning=on',
                                        'SET client_min_messages=notice')):
                name = 'invalid_value'+str(index)
                raw.exchange(parse(name, sql) + packet(b'S'), b'1Z')
                raw.exchange(bind(name, name) + packet(b'H'), b'2', end=b'2')
                raw.exchange(execute(name) + packet(b'S'), b'EZ', error='22023')
                raw.exchange(close(b'S', name) + packet(b'S'), b'3Z')

            # Read every frame directly: ExtendedClient.events deliberately drops N/S.
            for sql, kinds, rows, error in [
                ('SET standard_conforming_strings=off', b'CSZ', [], None),
                ('; '.join(statements[2:]), b'CCZ', [], None),
                (r"SELECT '\n'", b'TDCZ', [[b'\n']], None),
                (r"SELECT '\q'", b'TDCZ', [[b'q']], None),
                (r"SELECT '\u12'", b'EZ', [], '22025'),
                ('SELECT 1/0', b'TEZ', [], '22012'),
            ]:
                raw.send(packet(b'Q', sql.encode()+b'\0'))
                events = []
                for _ in range(1000):
                    kind, body = raw.message()
                    events.append((kind, body))
                    if kind == b'Z': break
                require(all(kind != b'N' for kind, _ in events), 'Unexpected NoticeResponse: '+repr(events))
                require(b''.join(kind for kind, _ in events)==kinds and events[-1]==(b'Z', b'I'),
                        'Diagnostic wire order differs: '+repr(events))
                require([row(body) for kind, body in events if kind==b'D']==rows, 'Escape bytes changed')
                errors = [error_fields(body) for kind, body in events if kind==b'E']
                require([e['C'] for e in errors]==([error] if error else []) and
                        all(e['S']=='ERROR' for e in errors), 'ErrorResponse was suppressed or changed')
                wire_diagnostics.append({'sql':sql, 'frames':kinds.decode(), 'sqlstate':error, 'notices':0})
        finally:
            raw.close()
        env = self.env.copy()
        env.update(ISC_USER='WIRE_HOST_TEST', ISC_PASSWORD=self.password)
        for dialect in (1, 2, 3):
            result = subprocess.run([str(self.runtime/'bin/isql'), '-quiet', '-autoterm', '-charset', 'UTF8',
                '-sql_dialect', str(dialect), f'inet://127.0.0.1:{self.native_port}/wire_host'],
                input='SET LIST ON;\n' + ';\n'.join(statements) + ';\nSET BIND OF DOUBLE PRECISION TO VARCHAR(40);\n' +
                      'SELECT 1 AS LC_MESSAGES, 2 AS CLIENT_ENCODING, 3 AS ESCAPE_STRING_WARNING, '
                      '4 AS CLIENT_MIN_MESSAGES FROM RDB$DATABASE;\n' +
                      RECOVERY + ';\nROLLBACK;\nQUIT;\n', capture_output=True, text=True,
                env=env, cwd=self.temp, timeout=self.timeout)
            output = result.stdout + result.stderr
            (self.work/f'native_settings_dialect_{dialect}.log').write_text(self.redact(output))
            require(result.returncode != 0 and re.findall(r'SQLSTATE\s*=\s*(\w+)', output) == ['42000']*4 and
                    re.findall(r'^ANSWER\s+(\S+)\s*$', output, re.MULTILINE) == ['42'] and
                    re.findall(r'^LC_MESSAGES\s+(\S+)\s*$', output, re.MULTILINE) == ['1'] and
                    re.findall(r'^CLIENT_ENCODING\s+(\S+)\s*$', output, re.MULTILINE) == ['2'] and
                    re.findall(r'^ESCAPE_STRING_WARNING\s+(\S+)\s*$', output, re.MULTILINE) == ['3'] and
                    re.findall(r'^CLIENT_MIN_MESSAGES\s+(\S+)\s*$', output, re.MULTILINE) == ['4'],
                    f'native dialect {dialect} SET rejection/recovery differs')
        output = self.isql('settings_rollback_observer', 'SET LIST ON;\nSELECT COUNT(*) AS SETTINGS_ROWS '
                           'FROM WIRE_HOST_ROWS WHERE ID BETWEEN 511 AND 516;\nROLLBACK;\nQUIT;\n')
        require(re.findall(r'^SETTINGS_ROWS\s+(\S+)\s*$', output, re.MULTILINE) == ['0'],
                'native observer saw script writes after settings failure')
        self.summary['settings_protocols'] = {'extended_prepare_allowed': True,
            'invalid_value_checked_at_execute': True, 'named_statement_reuse': 2,
            'native_rejected_dialects': [1,2,3], 'native_autoterm_recovery': True,
            'native_set_bind_preserved': True, 'native_identifiers_preserved': True,
            'wire_diagnostics':wire_diagnostics,
            'non_utf8_attachment': 'not exercised by this UTF8 TCP harness'}

    def run(self, args):
        native, pg = reserve_ports(2)
        self.start('1c', native, native, [{'alias':'PgWire','port':pg}])
        ready = Client(self)
        try:
            ready.login()
            ready.query_integer()
        finally:
            ready.close()
        self.native_probe('native_before')
        config = {'port':pg,'password':self.password,'baseline':args.baseline,'settings':args.settings,'strings':args.strings,
                  'capture_dir':str(args.capture_dir),'libpq':args.libpq, 'native_port':native,
                  'runtime':str(self.runtime),'work':str(self.work),'timeout':self.timeout}
        client_env = {key:value for key,value in self.env.items() if not key.startswith('PG')}
        result = subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--child'],
            input=json.dumps(config),text=True,capture_output=True,env=client_env,cwd=self.temp,timeout=self.timeout)
        (self.work / 'libpq.log').write_text(self.redact(result.stdout + result.stderr))
        require(result.stdout, 'libpq child produced no report; exit ' + str(result.returncode))
        child = json.loads(result.stdout)
        self.summary['client'] = child
        require(result.returncode == 0, 'libpq child failed; see libpq.log')
        if args.strings:
            from pgwire_strings_checks import strings_protocols, strings_api
            self.summary['strings_protocols'] = strings_protocols(self)
            self.summary['strings_api'] = strings_api(self)
        elif args.settings:
            self.settings_protocols()
        elif not args.baseline:
            for name, sql, expected, _ in SELECTS:
                if not name.startswith('old_'):
                    continue
                output = self.isql('native_' + name, 'SET LIST ON;\n' + sql + ';\nROLLBACK;\nQUIT;\n')
                column = COLUMN_NAMES.get(name, 'ANSWER')
                require(re.findall(r'^' + column + r'\s+(\S+)\s*$',output,re.MULTILINE) == expected,
                        'native SELECT case differs: ' + name)
            raw = ExtendedClient(self)
            try:
                raw.login()
                self.native_dialect_isolation(raw)
                for sql in ('SELECT CAST(? AS INTEGER) AS ANSWER',
                            'SELECT CAST(? AS INTEGER) AS ANSWER FROM RDB$DATABASE'):
                    raw.run(sql, values=['42'], oids=[23], rows=[['42']], tag='SELECT 1')
            finally:
                raw.close()
            self.summary['native_select_cases'] = sum(name.startswith('old_') for name, *_ in SELECTS)
            self.summary['raw_extended_native_parameter'] = True
        self.no_attachments()
        self.native_probe('native_after')


def main():
    if sys.argv[1:] == ['--child']:
        return client_child()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firebird-dir',type=Path,required=True)
    parser.add_argument('--work-dir',type=Path,required=True)
    parser.add_argument('--capture-dir',type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--strings',action='store_true',help='test standard_conforming_strings')
    mode.add_argument('--baseline',action='store_true')
    mode.add_argument('--settings',action='store_true',help='test fixed English/UTF8 and client diagnostics settings')
    parser.add_argument('--libpq')
    parser.add_argument('--timeout',type=float,default=60)
    args = parser.parse_args()
    source, work = args.firebird_dir.resolve(), args.work_dir.absolute()
    if work.exists() or work.is_symlink() or source in work.resolve().parents:
        parser.error('--work-dir must be new and outside --firebird-dir')
    if not re.fullmatch(r'[A-Za-z0-9_./-]+',str(work)) or not 0 < args.timeout <= 300:
        parser.error('use an ASCII work path and an external timeout in (0,300] seconds')
    if args.baseline and not args.capture_dir:
        parser.error('--baseline requires --capture-dir')
    if args.capture_dir:
        args.capture_dir = args.capture_dir.resolve()
    for name in ('bin/firebird','bin/isql','security6.fdb','plugins/libPgWire.so'):
        if not (source / name).is_file():
            parser.error('completed build required: missing ' + str(source / name))
    work.mkdir(mode=0o700)
    harness = OneCHarness(source,work.resolve(),args.timeout)
    harness.summary.pop('passed')
    harness.summary.update(completed=False, mode='strings' if args.strings else 'settings' if args.settings else 'baseline' if args.baseline else 'select_without_from',
        stage='standard_conforming_strings / revision 2' if args.strings else
            'Fixed English/UTF8 and client diagnostics settings' if args.settings else '1C observations / first engine slice',
        capture_dir=str(args.capture_dir), workers=6,
        module_sha256=hashlib.sha256((source/'plugins/libPgWire.so').read_bytes()).hexdigest(),
        engine_sha256=hashlib.sha256((source/'plugins/libEngine14.so').read_bytes()).hexdigest(),
        tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    repo = Path(__file__).resolve().parents[2]
    harness.summary['source_revision'] = subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    harness.summary['source_diff_sha256'] = hashlib.sha256(subprocess.check_output(
        ['git','diff','HEAD','--','src'],cwd=repo)).hexdigest()
    try:
        harness.prepare()
        if args.strings:
            harness.isql('strings_grants', f"CONNECT '{harness.database}';\nGRANT CREATE PROCEDURE TO USER WIRE_HOST_TEST;\n"
                         "GRANT CREATE TABLE TO USER WIRE_HOST_TEST;\nCOMMIT;\nQUIT;\n", embedded=True)
        elif not args.settings and not args.baseline:
            compat = Path(__file__).resolve().parents[1] / '1c/compat.sql'
            harness.isql('install_compat', f"CONNECT '{harness.database}';\n" +
                         compat.read_text() + '\nGRANT CREATE TABLE TO USER WIRE_HOST_TEST;\nCOMMIT;\nQUIT;\n',
                         embedded=True)
            harness.summary['compat_script_sha256'] = hashlib.sha256(compat.read_bytes()).hexdigest()
        harness.run(args)
        harness.summary['completed'] = True
        if args.strings:
            harness.summary['strings_passed'] = True
        elif args.settings:
            harness.summary['settings_passed'] = True
        elif not args.baseline:
            harness.summary['select_without_from_passed'] = True
    except (Exception,KeyboardInterrupt) as error:
        harness.summary['error'] = harness.redact(f'{type(error).__name__}: {error}')
    finally:
        try:
            harness.close()
        except Exception as error:
            harness.summary.update(completed=False,shutdown_error=harness.redact(error))
        path = work / 'summary.json'
        path.write_text(json.dumps(harness.summary,indent=2) + '\n')
        print(json.dumps({'completed':harness.summary['completed'],'mode':harness.summary['mode'],'summary':str(path)}))
    return 0 if harness.summary['completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
