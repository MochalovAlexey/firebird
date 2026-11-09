#!/usr/bin/env python3
"""Observe original 1C SQL or test SELECT without FROM in a private runtime.

--baseline reads external CAPTURE/profile/selected; errors are observations,
not acceptance. Extended examples are prepared/described without execution or
guessed Bind values. No captured SQL, parameter DETAIL or result data is saved.
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
from pgwire_extended_query_smoke import ExtendedClient
from pgwire_startup_smoke import StartupHarness, Client
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


def client_child():
    config = json.load(sys.stdin)
    connection = None
    try:
        connection = Libpq(config)
        output = {'libpq_version': connection.pq.PQlibVersion(), 'cases': []}
        if config['baseline']:
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
        config = {'port':pg,'password':self.password,'baseline':args.baseline,
                  'capture_dir':str(args.capture_dir),'libpq':args.libpq, 'native_port':native,
                  'runtime':str(self.runtime),'work':str(self.work),'timeout':self.timeout}
        client_env = {key:value for key,value in self.env.items() if not key.startswith('PG')}
        result = subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--child'],
            input=json.dumps(config),text=True,capture_output=True,env=client_env,cwd=self.temp,timeout=self.timeout)
        (self.work / 'libpq.log').write_text(self.redact(result.stdout + result.stderr))
        child = json.loads(result.stdout)
        self.summary['client'] = child
        require(result.returncode == 0, 'libpq child failed; see libpq.log')
        if not args.baseline:
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
    parser.add_argument('--baseline',action='store_true')
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
    harness.summary.update(completed=False, mode='baseline' if args.baseline else 'select_without_from',
        stage='1C observations / first engine slice', capture_dir=str(args.capture_dir), workers=6,
        module_sha256=hashlib.sha256((source/'plugins/libPgWire.so').read_bytes()).hexdigest(),
        engine_sha256=hashlib.sha256((source/'plugins/libEngine14.so').read_bytes()).hexdigest(),
        tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    repo = Path(__file__).resolve().parents[2]
    harness.summary['source_revision'] = subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    harness.summary['source_diff_sha256'] = hashlib.sha256(subprocess.check_output(
        ['git','diff','HEAD','--','src'],cwd=repo)).hexdigest()
    try:
        harness.prepare()
        if not args.baseline:
            compat = Path(__file__).resolve().parents[1] / '1c/compat.sql'
            harness.isql('install_compat', f"CONNECT '{harness.database}';\n" +
                         compat.read_text() + '\nGRANT CREATE TABLE TO USER WIRE_HOST_TEST;\nCOMMIT;\nQUIT;\n',
                         embedded=True)
            harness.summary['compat_script_sha256'] = hashlib.sha256(compat.read_bytes()).hexdigest()
        harness.run(args)
        harness.summary['completed'] = True
        if not args.baseline:
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
