"""Consecutive $n checks in the existing private 1c libpq/raw/native harness."""
import ctypes as C
import hashlib
import importlib.util
import json
import os
import re
import struct
from pathlib import Path
from types import SimpleNamespace

from pgwire_extended_query_smoke import ExtendedClient, parse, bind, describe, execute, close
from pgwire_startup_smoke import packet
from wire_host_smoke import Harness, require


TYPED = 'SELECT CAST($1 AS INTEGER)'


def parameters_fixture(h):
    columns = ','.join(f'C{i} INTEGER' for i in range(1, 93))
    h.isql('parameters_fixture', f"CONNECT '{h.database}';\n"
        'CREATE TABLE PARAM_ROWS(ID INTEGER NOT NULL PRIMARY KEY, NAME VARCHAR(40) CHARACTER SET UTF8, V BIGINT);\n'
        f'CREATE TABLE PARAM_92({columns});\n'
        'CREATE TABLE PARAM_FILES(FILENAME VARCHAR(100) CHARACTER SET UTF8 PRIMARY KEY, CONTENT_TEXT VARCHAR(100));\n'
        "INSERT INTO PARAM_FILES VALUES('config/Я.xml','synthetic-config');\n"
        'SET TERM ^;\nCREATE PROCEDURE PARAM_PROC(A INTEGER) RETURNS(B INTEGER) AS BEGIN B=A+1; END^\nSET TERM ;^\n'
        'GRANT EXECUTE ON PROCEDURE PARAM_PROC TO USER WIRE_HOST_TEST;\n'
        + ''.join(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO USER WIRE_HOST_TEST;\n'
                  for table in ('PARAM_ROWS', 'PARAM_92', 'PARAM_FILES')) +
        'GRANT CREATE PROCEDURE TO USER WIRE_HOST_TEST;\nGRANT CREATE TABLE TO USER WIRE_HOST_TEST;\nCOMMIT;\nQUIT;\n', embedded=True)


def parameters_client(config, client):
    observer = SimpleNamespace(runtime=Path(config['runtime']), env=os.environ.copy(),
        password=config['password'], native_port=config['native_port'], work=Path(config['work']),
        temp=Path(config['work'])/'tmp', phase='parameters', timeout=config['timeout'], redact=client.redact)
    report = {'checks': [], 'native_observations': 0}

    def checked(label, result, rows=None, tag=None, error='', state=0):
        require(result['sqlstate'] == error and client.pq.PQtransactionStatus(client.connection) == state and
                (rows is None or result.get('rows') == rows) and (tag is None or result['tag'] == tag),
                label + ': ' + json.dumps(result, ensure_ascii=False))
        report['checks'].append({'case': label, 'sqlstate': error, 'state': state})
        (Path(config['work']) / 'parameters_progress.json').write_text(json.dumps(report, indent=2) + '\n')
        return result

    def query(sql, **expected):
        results = client.query(sql, True)
        require(len(results) == 1, 'expected one result: ' + sql)
        return checked(sql, results[0], **expected)

    def params(sql, values=(), oids=(), formats=(), prepared=False, **expected):
        return checked(sql, client.params(sql, values, oids, formats, prepared), **expected)

    def prepare(name, sql, expected_oids, hints=()):
        types = (C.c_uint * len(hints))(*hints) if hints else None
        checked('prepare ' + name, client.result(client.pq.PQprepare(
            client.connection, name.encode(), sql.encode(), len(hints), types)))
        result = checked('describe ' + name, client.result(client.pq.PQdescribePrepared(client.connection, name.encode())))
        require(result['parameter_oids'] == expected_oids, 'input metadata order: ' + json.dumps(result))
        report.setdefault('metadata', {})[name] = result['parameter_oids']

    def native(sql):
        report['native_observations'] += 1
        return Harness.isql(observer, 'parameters_observer_' + str(report['native_observations']),
            'SET NAMES UTF8;\nSET ROLE PGWIRE_OBSERVER;\nSET LIST ON;\n' + sql + ';\nROLLBACK;\nQUIT;\n')

    def seen(identifier, expected):
        output = native(f'SELECT ID, NAME, V FROM PARAM_ROWS WHERE ID={identifier}')
        actual = [re.findall(r'^' + col + r'[ \t]+([^\n]*?)[ \t]*$', output, re.M) for col in ('ID', 'NAME', 'V')]
        require(actual == ([[], [], []] if expected is None else [[str(v)] for v in expected]),
                'native row differs: ' + repr(actual))

    for hints in ((), (0,), (23,)):
        params(TYPED, ['42'], oids=hints, rows=[['42']])
    sql = 'SELECT CAST($1 AS SMALLINT), CAST($2 AS BIGINT), CAST($3 AS VARCHAR(40)), CAST($4 AS BOOLEAN)'
    prepare('typed', sql, [21, 20, 1043, 16])
    params('typed', ['-123', '1234567890123', 'Я🙂', 'true'], prepared=True,
           rows=[['-123', '1234567890123', 'Я🙂', 't']])
    params('typed', [struct.pack('!h', 321), struct.pack('!q', -9876543210), b'', b'\x00'],
           formats=[1, 1, 1, 1], prepared=True, rows=[['321', '-9876543210', '', 'f']])
    params('typed', [None, '7', None, None], prepared=True, rows=[[None, '7', None, None]])
    params('typed', [struct.pack('!h', -7), struct.pack('!q', 78), 'Я🙂', None],
           formats=[1, 1, 1, 1], prepared=True, rows=[['-7', '78', 'Я🙂', None]])
    prepare('insert_row', 'INSERT INTO PARAM_ROWS VALUES($1,$2,$3)', [23, 1043, 20], [0, 1043, 20])
    params('insert_row', ['701', 'Первый', '9000000001'], prepared=True, tag='INSERT 0 1')
    seen(701, [701, 'Первый', 9000000001])
    params('SELECT ID FROM PARAM_ROWS WHERE NAME=$1', ['Первый'], rows=[['701']])
    params('SELECT CONTENT_TEXT FROM PARAM_FILES WHERE FILENAME=$1', ['config/Я.xml'], rows=[['synthetic-config']])
    params('UPDATE PARAM_ROWS SET NAME=$1, V=$2 WHERE ID=$3 RETURNING ID, NAME, V',
           ['', '-9000000002', '701'], rows=[['701', '', '-9000000002']])
    seen(701, [701, '', -9000000002])
    params('UPDATE PARAM_ROWS SET NAME=$1 WHERE ID=$2 RETURNING NAME', [None, '701'], rows=[[None]])
    seen(701, [701, '<null>', -9000000002])

    # CTE traversal may differ from source order; unused CTE inputs must remain present.
    for name, sql, values, rows in (
        ('unused_cte', 'WITH U AS (SELECT CAST($1 AS INTEGER) AS V FROM RDB$DATABASE) '
         'SELECT CAST($2 AS INTEGER)', ['111', '222'], [['222']]),
        ('used_cte', 'WITH U AS (SELECT CAST($1 AS INTEGER) AS V FROM RDB$DATABASE) '
         'SELECT CAST($2 AS INTEGER), U.V FROM U', ['111', '222'], [['222', '111']]),
        ('subquery', 'SELECT CAST($1 AS INTEGER), (SELECT CAST($2 AS INTEGER) FROM RDB$DATABASE) '
         'WHERE EXISTS(SELECT 1 FROM RDB$DATABASE WHERE 3=CAST($3 AS INTEGER))', ['11', '22', '3'], [['11', '22']]),
    ):
        prepare(name, sql, [23] * len(values))
        params(name, values, prepared=True, rows=rows)
    merge = ('MERGE INTO PARAM_ROWS T USING (SELECT CAST($1 AS INTEGER) AS ID FROM RDB$DATABASE) S '
             'ON T.ID=S.ID WHEN MATCHED THEN UPDATE SET NAME=$2 '
             'WHEN NOT MATCHED THEN INSERT(ID,NAME,V) VALUES(S.ID,$3,$4) RETURNING T.ID,T.NAME,T.V')
    prepare('merge_row', merge, [23, 1043, 1043, 20])
    params('merge_row', ['702', 'unused-update', 'inserted', '987'], prepared=True, rows=[['702', 'inserted', '987']])
    seen(702, [702, 'inserted', 987])
    params('merge_row', ['702', 'updated', 'unused-insert', '654'], prepared=True, rows=[['702', 'updated', '987']])
    seen(702, [702, 'updated', 987])
    params('DELETE FROM PARAM_ROWS WHERE ID=$1 RETURNING ID, NAME', ['702'], rows=[['702', 'updated']])
    seen(702, None)

    insert92 = 'INSERT INTO PARAM_92 VALUES(' + ','.join(f'${i}' for i in range(1, 93)) + ')'
    prepare('insert92', insert92, [23] * 92)
    values92 = [str(10000 + i * 37) for i in range(1, 93)]
    params('insert92', values92, prepared=True, tag='INSERT 0 1')
    output = native('SELECT * FROM PARAM_92')
    actual92 = re.findall(r'^C(\d+)\s+(-?\d+)\s*$', output, re.M)
    require(actual92 == [(str(i), v) for i, v in enumerate(values92, 1)], 'native 92-column readback differs')
    report['synthetic_92_values'] = values92

    for identifier, control in ((710, 'ROLLBACK'), (711, 'COMMIT')):
        query('BEGIN', tag='BEGIN', state=2)
        params('insert_row', [str(identifier), control, str(identifier + 100)], prepared=True, tag='INSERT 0 1', state=2)
        seen(identifier, None)
        query(control, tag=control)
        seen(identifier, [identifier, control, identifier + 100] if control == 'COMMIT' else None)
    query('BEGIN', state=2)
    params('insert_row', ['712', 'rollback-on-error', '1'], prepared=True, state=2)
    params('SELECT CAST($2 AS INTEGER)', ['1'], error='0A000', state=3)
    params(TYPED, ['42'], error='25P02', state=3)
    query('ROLLBACK', tag='ROLLBACK'); seen(712, None)
    params(TYPED, ['42'], rows=[['42']])

    # Simple cannot execute an input statement with an implicit NULL.
    query('INSERT INTO PARAM_ROWS(ID) VALUES($1)', error='0A000')
    query(TYPED, error='0A000')
    for hints in ((), (23,)):
        params('SELECT $1', ['1'], oids=hints, error='HY004')
        client.recover()
    params('SELECT ?', ['1'], error='HY004')
    params(TYPED, ['42'], oids=[1043], error='0A000')
    params(TYPED, ['oops'], error='22P02')
    params(TYPED, [b'\x00'], formats=[1], error='22P03')
    client.recover()

    # Native parameter contexts retain their existing metadata/semantics.
    for name, sql, metadata, values, expected in (
        ('call_native', 'CALL PARAM_PROC(?, ?)', [23], ['41'], [['42']]),
        ('call_named', 'CALL PARAM_PROC(A => ?, B => ?)', [23], ['44'], [['45']]),
        ('procedure_native', 'EXECUTE PROCEDURE PARAM_PROC(?)', [23], ['45'], [['46']]),
        ('block_native', 'EXECUTE BLOCK(A INTEGER=?) RETURNS(B INTEGER) AS BEGIN B=A; SUSPEND; END', [23], ['46'], [['46']]),
    ):
        prepare(name, sql, metadata)
        params(name, values, prepared=True, rows=expected)
    # USING is accepted by the native OO API, but remains outside wire execution.
    params('USING (A INTEGER=?) DO SELECT :A FROM RDB$DATABASE', ['47'], error='0A000')
    params('UPDATE OR INSERT INTO PARAM_ROWS VALUES(?,?,?) MATCHING(ID)', ['720','native','20'], tag='UPDATE OR INSERT 1')
    seen(720, [720, 'native', 20])
    # Synthetic local function arguments call make_parameter(), but are not real '?'.
    query('EXECUTE BLOCK RETURNS(V INTEGER) AS DECLARE FUNCTION F(A INTEGER) RETURNS INTEGER '
          'AS BEGIN RETURN A; END BEGIN V=F(42); SUSPEND; END', rows=[['42']])
    query("CREATE PROCEDURE PARAM_LITERAL RETURNS(V VARCHAR(10)) AS BEGIN V='$1'; END")
    query('EXECUTE PROCEDURE PARAM_LITERAL', rows=[['$1']])
    query("CREATE TABLE PARAM_LITERAL_TABLE(V VARCHAR(10) DEFAULT '$1')")
    query('DROP TABLE PARAM_LITERAL_TABLE')
    query('DROP PROCEDURE PARAM_LITERAL')
    report['psycopg3'] = 'available' if importlib.util.find_spec('psycopg') else 'SKIP: not installed; no dependencies added'
    report['scope'] = 'synthetic 06 lookup / 19 92-column shape; not original schema/types or 1C database opening'
    return report


def parameters_protocols(h):
    raw = ExtendedClient(h).login()
    rejected = []
    try:
        invalid = [
            ('SELECT CAST($1 AS INTEGER), CAST($1 AS INTEGER)', '0A000'),
            ('INSERT INTO PARAM_ROWS(ID,V) VALUES($1,$1)', '0A000'),
            ('SELECT CAST($1 AS INTEGER), CAST($3 AS INTEGER)', '0A000'),
            ('SELECT CAST($2 AS INTEGER), CAST($1 AS INTEGER)', '0A000'),
            ('SELECT CAST($32767 AS INTEGER)', '0A000'),
            ('SELECT CAST(? AS INTEGER), CAST($1 AS INTEGER)', '0A000'),
            ('SELECT CAST($1 AS INTEGER), CAST(? AS INTEGER)', '0A000'),
            ('SELECT CAST($0 AS INTEGER)', '42P02'),
            ('SELECT CAST($000 AS INTEGER)', '42P02'),
            ('SELECT CAST($32768 AS INTEGER)', '54000'),
            ('SELECT CAST($' + '9' * 200 + ' AS INTEGER)', '54000'),
            *[('SELECT ' + token, '42601') for token in ('$1foo', '$1_foo', '$1$foo', '$', '$-1', '$ 1', '$$text$$')],
            ('CALL PARAM_PROC($1,$2)', '0A000'),
            ('CALL PARAM_PROC(B => $1, A => $2)', '0A000'),
            ('EXECUTE PROCEDURE PARAM_PROC($1)', '0A000'),
            ('EXECUTE BLOCK(A INTEGER=$1) AS BEGIN END', '0A000'),
            ('EXECUTE BLOCK RETURNS(B INTEGER) AS BEGIN B=$1; SUSPEND; END', '0A000'),
            ('USING (A INTEGER=$1) DO SELECT :A FROM RDB$DATABASE', '0A000'),
            ('CREATE VIEW PARAM_BAD_VIEW AS SELECT CAST($1 AS INTEGER) AS V FROM RDB$DATABASE', '0A000'),
            ('CREATE PROCEDURE PARAM_BAD_PROC RETURNS(V INTEGER) AS BEGIN V=$1; END', '0A000'),
            ('UPDATE OR INSERT INTO PARAM_ROWS(ID) VALUES($1) MATCHING(ID)', '0A000'),
            ('CALL PARAM_PROC($1,)', '42601'),
            ('SELECT CAST($1 AS INTEGER) +', '42601'),
        ]
        for sql, error in invalid:
            # Neither a queued DML Parse nor Execute may run after the first error.
            raw.exchange(parse('bad', sql) + parse('discarded', 'INSERT INTO PARAM_ROWS(ID) VALUES(799)') +
                         bind('ignored', 'discarded') + execute('ignored') + packet(b'S'), b'EZ', error=error)
            raw.exchange(bind('check', 'discarded') + packet(b'S'), b'EZ', error='26000')
            raw.run(TYPED, values=['42'], rows=[['42']], tag='SELECT 1')
            rejected.append({'sql': sql, 'sqlstate': error, 'recovery': True})

        events = raw.exchange(parse('numbered', 'SELECT CAST($1 AS INTEGER), CAST($2 AS VARCHAR(20))') +
                              describe(b'S', 'numbered') + packet(b'S'), b'1tTZ')
        require([body for kind, body in events if kind == b't'] == [struct.pack('!HII', 2, 23, 1043)],
                'raw ParameterDescription count/order differs')
        raw.exchange(bind('left', 'numbered', ['101', 'left']) + bind('right', 'numbered', ['202', 'right']) +
                     execute('right') + execute('left') + packet(b'S'), b'22DCDCZ',
                     rows=[['202', 'right'], ['101', 'left']])
        for values, formats, error in ((['1'], [], '08P01'), (['1','x','3'], [], '08P01'),
                                       (['no','x'], [], '22P02'), ([b'x',b'y'], [1], '22P03')):
            raw.exchange(bind('badbind','numbered',values,formats) + execute('badbind') + packet(b'S'), b'EZ', error=error)
            raw.exchange(bind('again','numbered',['303','again']) + execute('again') + packet(b'S'),
                         b'2DCZ', rows=[['303','again']], tag='SELECT 1')
        raw.exchange(parse('', TYPED, [23,23]) + packet(b'S'), b'EZ', error='08P01')
        raw.run('SELECT CAST($01 AS INTEGER), CAST($0002 AS INTEGER)', values=['13','24'], rows=[['13','24']])
        raw.run('SELECT CAST($' + '0'*100 + '1 AS INTEGER)', values=['91'], rows=[['91']])
        for mode in ('on','off'):
            raw.exchange(packet(b'Q', ('SET standard_conforming_strings='+mode).encode()+b'\0'), b'CZ', tag='SET')
            raw.run("SELECT '$1', q'{$2}', CAST($1 AS INTEGER) AS \"$3\", 7 AS A$1 "
                    '/* $4 */ -- $5\nFROM RDB$DATABASE', values=['42'], rows=[['$1','$2','42','7']])
        raw.exchange(close(b'S','numbered') + packet(b'S'), b'3Z')
    finally:
        raw.close()
    output = h.isql('parameters_rejected_side_effects', 'SET LIST ON;\n'
        'SELECT COUNT(*) AS N FROM PARAM_ROWS WHERE ID=799;\n'
        "SELECT COUNT(*) AS N FROM RDB$RELATIONS WHERE RDB$RELATION_NAME='PARAM_BAD_VIEW';\n"
        "SELECT COUNT(*) AS N FROM RDB$PROCEDURES WHERE RDB$PROCEDURE_NAME='PARAM_BAD_PROC';\nROLLBACK;\nQUIT;\n")
    require(re.findall(r'^N\s+(\d+)\s*$',output,re.M)==['0','0','0'], 'rejected statement had side effects')
    return {'rejected': rejected, 'raw_describe': [23,1043], 'independent_portals': 2,
            'discard_until_sync': True, 'bad_bind_recovery': True, 'leading_zeros': True,
            'lexical_contexts_on_off': True, 'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
