"""PG postfix casts in the existing private 1c libpq/raw/native harness."""
import ctypes as C
import hashlib
import importlib.util
import json
import os
import re
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace

from pgwire_extended_query_smoke import ExtendedClient, parse, bind, describe, execute
from pgwire_startup_smoke import packet
from wire_host_smoke import Harness, require


def casts_fixture(h):
    h.isql('casts_fixture', f"CONNECT '{h.database}';\n"
        'CREATE TABLE CAST_ROWS(ID INTEGER NOT NULL PRIMARY KEY, V BIGINT, TXT VARCHAR(40) CHARACTER SET UTF8);\n'
        "INSERT INTO CAST_ROWS VALUES(1,10,'first');\n"
        'CREATE DOMAIN INT8 AS VARCHAR(8) CHARACTER SET UTF8;\n'
        'SET TERM ^;\nCREATE PROCEDURE CAST_PROC(A INTEGER) RETURNS(B INTEGER) AS BEGIN B=A+1; END^\nSET TERM ;^\n'
        'GRANT EXECUTE ON PROCEDURE CAST_PROC TO USER WIRE_HOST_TEST;\n'
        'GRANT SELECT, INSERT, UPDATE, DELETE ON CAST_ROWS TO USER WIRE_HOST_TEST;\n'
        'GRANT CREATE PROCEDURE TO USER WIRE_HOST_TEST;\nGRANT CREATE TABLE TO USER WIRE_HOST_TEST;\n'
        'GRANT CREATE VIEW TO USER WIRE_HOST_TEST;\nCOMMIT;\nQUIT;\n', embedded=True)


def casts_client(config, client):
    report = {'checks': [], 'equivalence': [], 'metadata': {}, 'native_observations': 0}
    observer = SimpleNamespace(runtime=Path(config['runtime']), env=os.environ.copy(), password=config['password'],
        native_port=config['native_port'], work=Path(config['work']), temp=Path(config['work'])/'tmp',
        phase='casts', timeout=config['timeout'], redact=client.redact)

    def checked(label, result, rows=None, oids=None, error='', state=0):
        require(result['sqlstate'] == error and client.pq.PQtransactionStatus(client.connection) == state and
                (rows is None or result.get('rows') == rows) and
                (oids is None or [c['oid'] for c in result['columns']] == oids),
                label + ': ' + json.dumps(result, ensure_ascii=False))
        report['checks'].append({'case':label, 'sqlstate':error, 'state':state})
        (Path(config['work'])/'casts_progress.json').write_text(json.dumps(report, indent=2)+'\n')
        return result

    def query(sql, **expected):
        results = client.query(sql, True)
        require(len(results) == 1, 'expected one query result: ' + sql)
        return checked(sql, results[0], **expected)

    def params(sql, values=(), hints=(), formats=(), prepared=False, result_format=0, **expected):
        return checked(sql, client.params(sql, values, hints, formats, prepared, result_format), **expected)

    def prepare(name, sql, inputs, outputs, hints=()):
        types = (C.c_uint * len(hints))(*hints) if hints else None
        checked('prepare '+name, client.result(client.pq.PQprepare(
            client.connection, name.encode(), sql.encode(), len(hints), types)))
        result = checked('describe '+name, client.result(client.pq.PQdescribePrepared(client.connection, name.encode())), oids=outputs)
        require(result['parameter_oids'] == inputs, 'wrong input metadata: ' + repr(result))
        report['metadata'][name] = {'input':inputs, 'output':outputs}

    def native(sql):
        report['native_observations'] += 1
        return Harness.isql(observer, 'casts_observer_'+str(report['native_observations']),
            'SET ROLE PGWIRE_OBSERVER;\nSET LIST ON;\n'+sql+';\nROLLBACK;\nQUIT;\n')

    def seen(identifier, expected):
        output = native(f'SELECT V FROM CAST_ROWS WHERE ID={identifier}')
        require(re.findall(r'^V[ \t]+(-?\d+)[ \t]*$',output,re.M) ==
                ([] if expected is None else [str(expected)]), 'native row differs: '+output)

    pairs = [
        ('1::INT8', 'CAST(1 AS BIGINT)', '', [['1']], 20),
        ('1::int8', 'CAST(1 AS BIGINT)', '', [['1']], 20),
        ('1::InT8', 'CAST(1 AS BIGINT)', '', [['1']], 20),
        ('(1.0)::int', 'CAST(1.0 AS INTEGER)', '', [['1']], 23),
        ('NULL::INTEGER', 'CAST(NULL AS INTEGER)', '', [[None]], 23),
        ('32767::SMALLINT', 'CAST(32767 AS SMALLINT)', '', [['32767']], 21),
        ("'Я🙂'::VARCHAR(12)", "CAST('Я🙂' AS VARCHAR(12))", '', [['Я🙂']], 1043),
        ("'true'::BOOLEAN", "CAST('true' AS BOOLEAN)", '', [['t']], 16),
        ('1.25::DOUBLE PRECISION', 'CAST(1.25 AS DOUBLE PRECISION)', '', [['1.25']], 701),
        ('1.2 + 1.6::int', '1.2 + CAST(1.6 AS INTEGER)', '', [['3.2']], 1700),
        ('(1.2 + 1.6)::int', 'CAST(1.2+1.6 AS INTEGER)', '', [['3']], 23),
        ('-32768::SMALLINT', '-CAST(32768 AS SMALLINT)', '22003', None, None),
        ('(-32768)::SMALLINT', 'CAST(-32768 AS SMALLINT)', '', [['-32768']], 21),
        ('+12::BIGINT', '+CAST(12 AS BIGINT)', '', [['12']], 20),
        ('1::INTEGER::BIGINT', 'CAST(CAST(1 AS INTEGER) AS BIGINT)', '', [['1']], 20),
        ('40000::SMALLINT::BIGINT', 'CAST(CAST(40000 AS SMALLINT) AS BIGINT)', '22003', None, None),
        ("'bad'::INTEGER", "CAST('bad' AS INTEGER)", '22018', None, None),
        ("'123'::INTEGER::VARCHAR(2)", "CAST(CAST('123' AS INTEGER) AS VARCHAR(2))", '22001', None, None),
        ("'a' || 12::VARCHAR(2)", "'a' || CAST(12 AS VARCHAR(2))", '', [['a12']], 1043),
        ("('a' || 'b')::VARCHAR(2)", "CAST('a' || 'b' AS VARCHAR(2))", '', [['ab']], 1043),
        ("'a'::VARCHAR(3) COLLATE UNICODE_CI = 'A'", "CAST('a' AS VARCHAR(3)) COLLATE UNICODE_CI = 'A'", '', [['t']], 16),
        ("'a'||'b'::VARCHAR(3) COLLATE UNICODE_CI = 'AB'", "'a'||CAST('b' AS VARCHAR(3)) COLLATE UNICODE_CI = 'AB'", '', [['f']], 16),
        ("('a' COLLATE UNICODE_CI)::VARCHAR(3) = 'A'", "CAST('a' COLLATE UNICODE_CI AS VARCHAR(3)) = 'A'", '', [['f']], 16),
        ("'a'::VARCHAR(3) CHARACTER SET UTF8 COLLATE UNICODE_CI = 'A'", "CAST('a' AS VARCHAR(3) CHARACTER SET UTF8 COLLATE UNICODE_CI) = 'A'", '', [['t']], 16),
        ("'a'::VARCHAR(3) COLLATE UNICODE_CI COLLATE UNICODE = 'A'", "CAST('a' AS VARCHAR(3)) COLLATE UNICODE_CI COLLATE UNICODE = 'A'", '', [['f']], 16),
        ("(TIMESTAMP '2001-02-03 04:05:06'::TIMESTAMP AT TIME ZONE 'UTC')::VARCHAR(50)",
         "CAST(CAST(TIMESTAMP '2001-02-03 04:05:06' AS TIMESTAMP) AT TIME ZONE 'UTC' AS VARCHAR(50))", '', None, 1043),
        ("(TIMESTAMP '2001-02-03 04:05:06'::TIMESTAMP WITH TIME ZONE AT TIME ZONE 'UTC')::VARCHAR(50)",
         "CAST(CAST(TIMESTAMP '2001-02-03 04:05:06' AS TIMESTAMP WITH TIME ZONE) AT TIME ZONE 'UTC' AS VARCHAR(50))", '', None, 1043),
        ("EXTRACT(HOUR FROM TIME '04:05:06'::TIME WITH TIME ZONE AT TIME ZONE 'UTC'::VARCHAR(10))",
         "EXTRACT(HOUR FROM CAST(TIME '04:05:06' AS TIME WITH TIME ZONE) AT TIME ZONE CAST('UTC' AS VARCHAR(10)))", '', None, None),
        ("OCTET_LENGTH('abc'::BLOB SUB_TYPE TEXT SEGMENT SIZE 80 CHARACTER SET UTF8)",
         "OCTET_LENGTH(CAST('abc' AS BLOB SUB_TYPE TEXT SEGMENT SIZE 80 CHARACTER SET UTF8))", '', [['3']], 20),
        ('1.25::NUMERIC', 'CAST(1.25 AS NUMERIC)', '', None, None),
        ("'abc'::VARCHAR", "CAST('abc' AS VARCHAR)", '', [['abc']], 1043),
        ("'2000-01-01'::DATE", "CAST('2000-01-01' AS DATE)", '', [['2000-01-01']], 1082),
    ]
    for postfix, explicit, error, rows, oid in pairs:
        results = [query('SELECT '+expr+' AS V', error=error, rows=rows,
                         oids=None if oid is None else [oid]) for expr in (postfix, explicit)]
        require(results[0] == results[1], 'postfix/native CAST mismatch: '+repr((postfix,results)))
        report['equivalence'].append({'postfix':postfix, 'native':explicit, 'result':results[0]})
    # WITH can continue the type or the SELECT. The latter reaches the existing
    # engine restriction on locking a SELECT without a physical table (42000).
    for value, target in (('CURRENT_TIME','TIME'), ('CURRENT_TIMESTAMP','TIMESTAMP')):
        actual=query(f'SELECT {value}::{target} WITH LOCK', error='42000')
        expected=query(f'SELECT CAST({value} AS {target}) WITH LOCK', error='42000')
        require(actual == expected, 'WITH LOCK was consumed as a cast type clause')
    query('SELECT INT8, "INT8" FROM (SELECT 42 AS INT8 FROM RDB$DATABASE)', rows=[['42','42']], oids=[23,23])
    query("SELECT CAST('domain' AS INT8), CAST('quoted' AS \"INT8\")", rows=[['domain','quoted']], oids=[1043,1043])
    query('SELECT ID::bigint FROM CAST_ROWS WHERE ID=1', rows=[['1']], oids=[20])
    query('WITH C AS (SELECT 41::BIGINT AS V FROM RDB$DATABASE) SELECT (SELECT V+1 FROM C)::INTEGER', rows=[['42']], oids=[23])
    # Resolve optional BLOB clauses versus implicit aliases through native backtracking.
    for alias in ('SUB_TYPE', 'SEGMENT'):
        query(f"SELECT OCTET_LENGTH({alias}) FROM (SELECT 'abc'::BLOB {alias} FROM RDB$DATABASE)", rows=[['3']], oids=[20])

    for hints in ((), (0,), (23,)):
        params('SELECT $1::INTEGER', ['42'], hints=hints, rows=[['42']], oids=[23])
    params('SELECT ?::INTEGER', ['43'], rows=[['43']], oids=[23])
    prepare('chain', 'SELECT $1::INTEGER::VARCHAR(12)', [23], [1043])
    for value in ('42','-7654',None):
        params('chain', [value], prepared=True, rows=[[value]], oids=[1043])
    params('chain', [struct.pack('!i',-4567)], formats=[1], prepared=True, result_format=1,
           rows=[['-4567'.encode().hex()]], oids=[1043])
    prepare('wide', 'SELECT $1::INT8, $2::SMALLINT, $3::VARCHAR(12), $4::BOOLEAN, $5::DOUBLE PRECISION',
            [20,21,1043,16,701], [20,21,1043,16,701], [20,0,1043,16,701])
    params('wide', ['9000000001','-17','Я🙂','true','1.25'], prepared=True,
           rows=[['9000000001','-17','Я🙂','t','1.25']])
    values=[struct.pack('!q',-9000000002),struct.pack('!h',123),'Я🙂'.encode(),b'\x00',struct.pack('!d',-2.5)]
    params('wide', values, formats=[1]*5, prepared=True, result_format=1, rows=[[v.hex() for v in values]])
    params('SELECT $1::INT8', [struct.pack('!q',-99)], formats=[1], result_format=1, rows=[[struct.pack('!q',-99).hex()]])
    params('SELECT $1::INTEGER', ['42'], hints=[1043], error='0A000')
    params('SELECT $1', ['42'], hints=[23], error='HY004')

    params('INSERT INTO CAST_ROWS(ID,V,TXT) VALUES($1::INTEGER,$2::BIGINT,$3::VARCHAR(40)) RETURNING ID::BIGINT,V,TXT',
           ['810','9876543210','Я'], rows=[['810','9876543210','Я']]); seen(810,9876543210)
    params('UPDATE CAST_ROWS SET V=$1::BIGINT WHERE ID=$2 RETURNING V::VARCHAR(20)', ['-321','810'], rows=[['-321']]); seen(810,-321)
    merge = ('MERGE INTO CAST_ROWS T USING (SELECT $1::INTEGER AS ID FROM RDB$DATABASE) S ON T.ID=S.ID '
             'WHEN MATCHED THEN UPDATE SET V=$2::BIGINT WHEN NOT MATCHED THEN INSERT(ID,V) VALUES(S.ID,$3::BIGINT) RETURNING T.V::BIGINT')
    params(merge, ['811','111','222'], rows=[['222']]); seen(811,222)
    params(merge, ['811','333','444'], rows=[['333']]); seen(811,333)
    params('DELETE FROM CAST_ROWS WHERE ID=$1::INTEGER RETURNING V::VARCHAR(20)', ['811'], rows=[['333']]); seen(811,None)
    for identifier, control in ((812,'COMMIT'),(813,'ROLLBACK')):
        query('BEGIN', state=2)
        params('INSERT INTO CAST_ROWS(ID,V) VALUES($1::INTEGER,$2::INT8)', [str(identifier),'77'], state=2)
        seen(identifier,None); query(control); seen(identifier,77 if control=='COMMIT' else None)
    query('BEGIN', state=2)
    params('INSERT INTO CAST_ROWS(ID,V) VALUES($1::INTEGER,$2::BIGINT)', ['814','88'], state=2)
    params('SELECT $1::VARCHAR(12)::INTEGER', ['bad'], error='22018', state=3)
    query('SELECT 42::INT8', error='25P02', state=3)
    query('ROLLBACK'); seen(814,None)
    params('chain',['42'],prepared=True,rows=[['42']])
    results=client.query("INSERT INTO CAST_ROWS(ID,V) VALUES(815,1); SELECT 'bad'::INTEGER; INSERT INTO CAST_ROWS(ID,V) VALUES(816,2)", True)
    require([r['sqlstate'] for r in results]==['','22018'], 'Simple script continued after cast error')
    seen(815,None); seen(816,None)
    query('SELECT 42::INT8', rows=[['42']])
    for mode in ('on','off'):
        query('SET standard_conforming_strings='+mode)
        query("SELECT '::', q'{::INT8}', 7 AS \"::\" /* ::INT8 */ -- ::INTEGER\nFROM RDB$DATABASE", rows=[['::','::INT8','7']])
        query("SELECT '\\x34\\x32'::INTEGER" if mode=='off' else "SELECT '42'::INTEGER", rows=[['42']])
    query('SET standard_conforming_strings=on')

    # Ordinary CAST and literal :: remain valid in excluded root contexts.
    for sql in ('CREATE TABLE CAST_OK AS SELECT CAST(42 AS INTEGER) AS V FROM RDB$DATABASE WITH DATA',
                'CREATE VIEW CAST_OK_VIEW AS SELECT CAST(42 AS INTEGER) AS V FROM RDB$DATABASE',
                "CREATE PROCEDURE CAST_OK_PROC RETURNS(V VARCHAR(8)) AS BEGIN V=CAST('::' AS VARCHAR(8)); END"):
        query(sql)
    query('SELECT V FROM CAST_OK', rows=[['42']]); query('SELECT V FROM CAST_OK_VIEW', rows=[['42']])
    query('EXECUTE PROCEDURE CAST_OK_PROC', rows=[['::']])
    query('EXECUTE BLOCK RETURNS(V INTEGER) AS BEGIN V=CAST(42 AS INTEGER); SUSPEND; END', rows=[['42']])
    query('CALL CAST_PROC(CAST(41 AS INTEGER), ?)', rows=[['42']])
    query('EXECUTE PROCEDURE CAST_PROC(CAST(41 AS INTEGER))', rows=[['42']])
    query('UPDATE OR INSERT INTO CAST_ROWS(ID,V) VALUES(817,CAST(42 AS INTEGER)) MATCHING(ID)'); seen(817,42)
    query('DROP TABLE CAST_OK'); query('DROP VIEW CAST_OK_VIEW'); query('DROP PROCEDURE CAST_OK_PROC')
    report['psycopg3']='available' if importlib.util.find_spec('psycopg') else 'SKIP: not installed; no dependencies added'
    report['scope']='synthetic cast semantics, not original selected 04/08/10/11 queries or 1C opening'
    return report


def casts_protocols(h):
    raw=ExtendedClient(h).login(); rejected=[]
    try:
        invalid=[
            *[('SELECT 1::'+target,'42601') for target in ('','INT4','INT2','FLOAT4','FLOAT8','BOOL','bytea','mvarchar','regnamespace','"INT8"','CAST_SCHEMA.INT8','TYPE OF INT8','INTEGER[2]','INTEGER[]')],
            ('SELECT 1: :INTEGER','42601'), ('SELECT 1:/*comment*/:INTEGER','42601'),
            ('SELECT $1::INTEGER+$1::INTEGER','0A000'), ('SELECT $2::INTEGER','0A000'),
            ('SELECT $1::INTEGER,$3::INTEGER','0A000'), ('SELECT $2::INTEGER,$1::INTEGER','0A000'),
            ('SELECT ?::INTEGER,$1::INTEGER','0A000'), ('SELECT $1::INTEGER,?::INTEGER','0A000'),
            ('CREATE TABLE CAST_BAD_CTAS AS SELECT 1::INT8 AS V FROM RDB$DATABASE WITH DATA','0A000'),
            ('CREATE TABLE CAST_BAD_CTE AS WITH C AS (SELECT 1::INTEGER AS V FROM RDB$DATABASE) SELECT V FROM C WITH DATA','0A000'),
            ('CREATE TABLE CAST_BAD_CHECK(V INTEGER CHECK(V>1::INTEGER))','0A000'),
            ('CREATE VIEW CAST_BAD_VIEW AS SELECT (SELECT 1::INT8 FROM RDB$DATABASE) AS V FROM RDB$DATABASE','0A000'),
            ('CREATE PROCEDURE CAST_BAD_PROC RETURNS(V INTEGER) AS BEGIN V=1::INTEGER; END','0A000'),
            ('EXECUTE BLOCK RETURNS(V INTEGER) AS BEGIN V=1::INTEGER; SUSPEND; END','0A000'),
            ('EXECUTE BLOCK AS DECLARE FUNCTION F(A INTEGER) RETURNS INTEGER AS BEGIN RETURN A::INTEGER; END BEGIN END','0A000'),
            ('CALL CAST_PROC(1::INTEGER,?)','0A000'), ('EXECUTE PROCEDURE CAST_PROC(1::INTEGER)','0A000'),
            ('USING(A INTEGER=?) DO SELECT :A::BIGINT FROM RDB$DATABASE','0A000'),
            ('UPDATE OR INSERT INTO CAST_ROWS(ID,V) VALUES(899,1::INT8) MATCHING(ID)','0A000'),
            ('CREATE VIEW CAST_BAD_SYNTAX AS SELECT 1::','42601'),
            ('SELECT 1::BLOB','0A000'),  # Existing output codec limit, not a substitute type.
        ]
        for sql,error in invalid:
            raw.exchange(parse('bad',sql)+parse('discarded','INSERT INTO CAST_ROWS(ID,V) VALUES(899,999)')+
                         bind('discarded','discarded')+execute('discarded')+packet(b'S'), b'EZ', error=error)
            raw.exchange(bind('missing','discarded')+packet(b'S'),b'EZ',error='26000')
            raw.run('SELECT $1::INTEGER::VARCHAR(12)',values=['42'],rows=[['42']],tag='SELECT 1')
            rejected.append({'sql':sql,'sqlstate':error})
        raw.exchange(parse('chain','SELECT $1::INTEGER::VARCHAR(12)')+describe(b'S','chain')+packet(b'S'),b'1tTZ')
        raw.exchange(bind('bad','chain',['no'])+execute('bad')+packet(b'S'),b'EZ',error='22P02')
        raw.exchange(bind('ok','chain',[struct.pack('!i',42)],[1],[1])+execute('ok')+packet(b'S'),b'2DCZ',rows=[[b'42']])
        raw.exchange(parse('convert','SELECT $1::VARCHAR(12)::INTEGER')+bind('convert','convert',['bad'])+
                     execute('convert')+parse('discarded','SELECT 99')+packet(b'S'),b'12EZ',error='22018')
        raw.run('SELECT $1::INT8',values=['42'],rows=[['42']])
        raw.exchange(packet(b'Q',b'SELECT $1::INTEGER\0'),b'EZ',error='0A000')
    finally:
        raw.close()
    output=h.isql('casts_no_side_effects','SET LIST ON;\n'
        "SELECT COUNT(*) AS N FROM RDB$RELATIONS WHERE RDB$RELATION_NAME STARTING WITH 'CAST_BAD';\n"
        "SELECT COUNT(*) AS N FROM RDB$PROCEDURES WHERE RDB$PROCEDURE_NAME STARTING WITH 'CAST_BAD';\n"
        'SELECT COUNT(*) AS N FROM CAST_ROWS WHERE ID=899;\nROLLBACK;\nQUIT;\n')
    require(re.findall(r'^N\s+(\d+)\s*$',output,re.M)==['0','0','0'], 'cast guard/discard left effects')
    env=h.env.copy();env.update(ISC_USER='WIRE_HOST_TEST',ISC_PASSWORD=h.password)
    for dialect in (1,2,3):
        script=('SET LIST ON;\nSELECT 1::INTEGER FROM RDB$DATABASE;\n'
                'SELECT CAST(42 AS INTEGER) AS RECOVERY FROM RDB$DATABASE;\n'
                'SELECT INT8 FROM (SELECT 43 AS INT8 FROM RDB$DATABASE);\n'
                'EXECUTE BLOCK RETURNS(V INTEGER) AS BEGIN SELECT CAST(44 AS INTEGER) FROM RDB$DATABASE INTO :V; SUSPEND; END;\n'
                'ROLLBACK;\nQUIT;\n')
        run=subprocess.run([str(h.runtime/'bin/isql'),'-quiet','-autoterm','-sql_dialect',str(dialect),
                            f'inet://127.0.0.1:{h.native_port}/wire_host'],input=script,text=True,
                           capture_output=True,env=env,cwd=h.temp,timeout=h.timeout)
        output=run.stdout+run.stderr;(h.work/f'casts-native-{dialect}.log').write_text(h.redact(output))
        require(run.returncode!=0 and re.findall(r'SQLSTATE\s*=\s*(\w+)',output)==['42000'] and
                re.findall(r'^RECOVERY\s+(\d+)\s*$',output,re.M)==['42'] and
                re.findall(r'^INT8\s+(\d+)\s*$',output,re.M)==['43'] and
                re.findall(r'^V\s+(\d+)\s*$',output,re.M)==['44'], 'native AUTOTERM/CAST/INT8/variable differs: '+output)
    return {'rejected':rejected,'discard_until_sync':True,'native_dialects':[1,2,3],
            'native_autoterm_recovery':True,'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
