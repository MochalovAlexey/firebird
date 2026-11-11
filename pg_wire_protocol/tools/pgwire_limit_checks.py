"""Literal SELECT LIMIT in the existing private 1c libpq/raw/native harness."""
import hashlib
import json
import os
import re
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace

from pgwire_extended_query_smoke import ExtendedClient, parse, bind, describe, execute, columns
from pgwire_startup_smoke import packet
from wire_host_smoke import Harness, require


def limits_fixture(h):
    h.isql('limits_fixture', f"CONNECT '{h.database}';\n"
        'CREATE TABLE LIMIT_ROWS(ID INTEGER NOT NULL PRIMARY KEY, G INTEGER);\n'
        'INSERT INTO LIMIT_ROWS VALUES(1,10);\nINSERT INTO LIMIT_ROWS VALUES(2,10);\n'
        'INSERT INTO LIMIT_ROWS VALUES(3,20);\nINSERT INTO LIMIT_ROWS VALUES(4,20);\n'
        'CREATE TABLE LIMIT_COPY(ID INTEGER NOT NULL PRIMARY KEY, V INTEGER);\n'
        'CREATE TABLE LIMIT_FILES(ID INTEGER, FILENAME VARCHAR(40) CHARACTER SET UTF8);\n'
        "INSERT INTO LIMIT_FILES VALUES(1,'Я🙂');\nINSERT INTO LIMIT_FILES VALUES(2,'second');\n"
        'SET TERM ^;\nCREATE PROCEDURE LIMIT_PROC(A INTEGER) RETURNS(B INTEGER) AS BEGIN B=A+1; END^\nSET TERM ;^\n'
        'GRANT EXECUTE ON PROCEDURE LIMIT_PROC TO USER WIRE_HOST_TEST;\n'
        'GRANT SELECT ON LIMIT_ROWS TO USER WIRE_HOST_TEST;\n'
        'GRANT SELECT ON LIMIT_FILES TO USER WIRE_HOST_TEST;\n'
        'GRANT SELECT, INSERT, UPDATE, DELETE ON LIMIT_COPY TO USER WIRE_HOST_TEST;\n'
        'GRANT CREATE TABLE TO USER WIRE_HOST_TEST;\nGRANT CREATE VIEW TO USER WIRE_HOST_TEST;\n'
        'GRANT CREATE PROCEDURE TO USER WIRE_HOST_TEST;\nCOMMIT;\nQUIT;\n', embedded=True)


def limits_client(config, client):
    report = {'checks': [], 'equivalence': [], 'metadata': {}, 'native_observations': 0}
    observer = SimpleNamespace(runtime=Path(config['runtime']), env=os.environ.copy(), password=config['password'],
        native_port=config['native_port'], work=Path(config['work']), temp=Path(config['work'])/'tmp',
        phase='limits', timeout=config['timeout'], redact=client.redact)

    def checked(label, result, rows=None, oids=None, error='', state=0, tag=None):
        require(result['sqlstate'] == error and client.pq.PQtransactionStatus(client.connection) == state and
                (rows is None or result.get('rows') == rows) and
                (oids is None or [c['oid'] for c in result['columns']] == oids) and
                (tag is None or result['tag'] == tag), label + ': ' + json.dumps(result, ensure_ascii=False))
        report['checks'].append({'case':label, 'sqlstate':error, 'state':state})
        (Path(config['work'])/'limits_progress.json').write_text(json.dumps(report, indent=2)+'\n')
        return result

    def query(sql, **expected):
        results = client.query(sql, True)
        require(len(results) == 1, 'expected one query result: ' + sql)
        return checked(sql, results[0], **expected)

    def params(sql, values=(), formats=(), prepared=False, result_format=0, **expected):
        return checked(sql, client.params(sql, values, (), formats, prepared, result_format), **expected)

    def prepare(name, sql, inputs, outputs):
        checked('prepare '+name, client.result(client.pq.PQprepare(
            client.connection, name.encode(), sql.encode(), 0, None)))
        result = checked('describe '+name, client.result(client.pq.PQdescribePrepared(client.connection, name.encode())), oids=outputs)
        require(result['parameter_oids'] == inputs, 'wrong input metadata: '+repr(result))
        report['metadata'][name] = {'input':inputs, 'output':outputs}

    def native(sql):
        report['native_observations'] += 1
        return Harness.isql(observer, 'limits_observer_'+str(report['native_observations']),
            'SET ROLE PGWIRE_OBSERVER;\nSET LIST ON;\n'+sql+';\nROLLBACK;\nQUIT;\n')

    def seen(ids, values=None):
        output = native('SELECT ID,V FROM LIMIT_COPY ORDER BY ID')
        require(re.findall(r'^ID[ \t]+(\d+)[ \t]*$',output,re.M) == [str(i) for i in ids],
                'native IDs differ: '+output)
        if values is not None:
            require(re.findall(r'^V[ \t]+(\d+)[ \t]*$',output,re.M) == [str(v) for v in values],
                    'native values differ: '+output)

    for count in (0,1,2,100001,2147483647):
        rows = [[str(i)] for i in range(1,min(count,4)+1)]
        sqls = [f'SELECT ID FROM LIMIT_ROWS ORDER BY ID LIMIT {count}',
                f'SELECT FIRST {count} ID FROM LIMIT_ROWS ORDER BY ID',
                f'SELECT ID FROM LIMIT_ROWS ORDER BY ID ROWS {count}']
        results = [query(sql, rows=rows, oids=[23], tag=f'SELECT {len(rows)}') for sql in sqls]
        require(results[0] == results[1] == results[2], 'LIMIT/FIRST/ROWS mismatch')
        report['equivalence'].append({'sql':sqls, 'result':results[0]})
    for count in ('0002','0x2','0o2','0b10','0_2','2_147_483_647','0x7fff_ffff','0o17_777_777_777','0b1111111111111111111111111111111'):
        rows = [['1'],['2']] if count in ('0002','0x2','0o2','0b10','0_2') else [['1'],['2'],['3'],['4']]
        query('SELECT ID FROM LIMIT_ROWS ORDER BY ID LIMIT '+count, rows=rows, oids=[23])
    for count in (0,1,2):
        query(f'SELECT 42 LIMIT {count}', rows=[] if not count else [['42']], oids=[23], tag=f'SELECT {bool(count)*1}')
    query('SELECT ID FROM LIMIT_ROWS WHERE ID<0 LIMIT 1', rows=[], oids=[23], tag='SELECT 0')
    query('SELECT ID FROM LIMIT_ROWS ORDER BY ID DESC LIMIT 2', rows=[['4'],['3']])
    query('SELECT DISTINCT G FROM LIMIT_ROWS ORDER BY G LIMIT 2', rows=[['10'],['20']])
    query('SELECT G,COUNT(*) FROM LIMIT_ROWS GROUP BY G ORDER BY G DESC LIMIT 1', rows=[['20','2']], oids=[23,20])
    query('SELECT COUNT(*) FROM LIMIT_ROWS LIMIT 1', rows=[['4']], oids=[20])
    query('WITH C AS (SELECT ID FROM LIMIT_ROWS ORDER BY ID DESC LIMIT 3) SELECT ID FROM C ORDER BY ID LIMIT 2', rows=[['2'],['3']])
    query('SELECT ID FROM (SELECT ID FROM LIMIT_ROWS ORDER BY ID DESC LIMIT 2) ORDER BY ID LIMIT 1', rows=[['3']])
    query('SELECT (SELECT ID FROM LIMIT_ROWS ORDER BY ID DESC LIMIT 1)', rows=[['4']])
    query('SELECT (SELECT ID FROM LIMIT_ROWS LIMIT 0)', rows=[[None]], oids=[23])
    query('(SELECT 42 AS V LIMIT 0) UNION ALL SELECT 99 AS V', rows=[['99']])
    # The existing parenthesized body wrapper requires named derived columns.
    for suffix in ('LIMIT 0','FETCH FIRST 0 ROWS ONLY'):
        query(f'(SELECT 42 FROM RDB$DATABASE {suffix}) UNION ALL SELECT 99 FROM RDB$DATABASE',error='42000')
    for union in ('UNION','UNION ALL'):
        left='SELECT ID FROM LIMIT_ROWS WHERE ID<=2'
        right='SELECT ID FROM LIMIT_ROWS WHERE ID>=3'
        query(f'{left} {union} {right} ORDER BY 1 DESC LIMIT 2', rows=[['4'],['3']])
        query(f'({left} ORDER BY 1 DESC LIMIT 1) {union} {right} ORDER BY 1', rows=[['2'],['3'],['4']])
        query(f'({left} ORDER BY 1 DESC LIMIT 1) {union} ({right} ORDER BY 1 LIMIT 1) ORDER BY 1', rows=[['2'],['3']])
        query(f'({left} {union} {right} ORDER BY 1 DESC LIMIT 2) ORDER BY 1 LIMIT 1', rows=[['3']])
    # UNION ordering still follows native SQL, which requires a column position.
    for suffix in ('LIMIT 2','ROWS 2'):
        query('SELECT ID FROM LIMIT_ROWS UNION SELECT ID FROM LIMIT_ROWS ORDER BY ID '+suffix,error='42000')
    query('SELECT FIRST 1 ID FROM (SELECT ID FROM LIMIT_ROWS ORDER BY ID LIMIT 2) ORDER BY ID DESC', rows=[['2']])
    query('SELECT ID FROM (SELECT FIRST 2 SKIP 1 ID FROM LIMIT_ROWS ORDER BY ID) ORDER BY ID DESC LIMIT 1', rows=[['3']])
    for suffix in ('ROWS 2 TO 3','OFFSET 1 ROWS FETCH NEXT 2 ROWS ONLY'):
        query('SELECT ID FROM LIMIT_ROWS ORDER BY ID '+suffix, rows=[['2'],['3']])
    query('(SELECT ID FROM LIMIT_ROWS ORDER BY ID OFFSET 1 ROW FETCH FIRST 1 ROW ONLY) UNION ALL SELECT 4 ORDER BY 1', rows=[['2'],['4']])

    files='SELECT FILENAME FROM LIMIT_FILES WHERE FILENAME=$1 LIMIT 1'
    prepare('file',files,[1043],[1043])
    for value in ('Я🙂','second',None,'missing','Я🙂'):
        rows=[] if value in (None,'missing') else [[value]]
        params(files,[value],rows=rows,oids=[1043])
        params('file',[value],prepared=True,rows=rows,oids=[1043],tag=f'SELECT {len(rows)}')
    params('file',['Я🙂'.encode()],formats=[1],prepared=True,result_format=1,rows=[['Я🙂'.encode().hex()]])
    ints='SELECT ID FROM LIMIT_ROWS WHERE ID>=$1 ORDER BY ID LIMIT 2'
    prepare('ids',ints,[23],[23])
    params(ints,['2'],rows=[['2'],['3']])
    for value in (1,3,4):
        params('ids',[struct.pack('!i',value)],formats=[1],prepared=True,result_format=1,
               rows=[[struct.pack('!i',i).hex()] for i in range(value,min(value+2,5))])
    params('ids',[None],prepared=True,rows=[],tag='SELECT 0')
    params('SELECT ID FROM LIMIT_ROWS WHERE ID=? ORDER BY ID LIMIT 1',['2'],rows=[['2']])
    params('SELECT $1::INTEGER LIMIT 1',['42'],rows=[['42']])

    insert='INSERT INTO LIMIT_COPY(ID,V) SELECT ID,G FROM LIMIT_ROWS ORDER BY ID LIMIT 2'
    for control in ('ROLLBACK','COMMIT'):
        query('BEGIN',state=2); query(insert,state=2,tag='INSERT 0 2'); seen([])
        query(control); seen([1,2] if control=='COMMIT' else [], [10,10] if control=='COMMIT' else [])
    query('UPDATE LIMIT_COPY SET V=(SELECT ID FROM LIMIT_ROWS ORDER BY ID DESC LIMIT 1) WHERE ID=1',tag='UPDATE 1')
    seen([1,2],[4,10])
    query('DELETE FROM LIMIT_COPY WHERE ID IN (SELECT ID FROM LIMIT_ROWS ORDER BY ID LIMIT 1)',tag='DELETE 1'); seen([2],[10])
    query('MERGE INTO LIMIT_COPY T USING (SELECT ID,G FROM LIMIT_ROWS ORDER BY ID LIMIT 3) S ON T.ID=S.ID '
          'WHEN MATCHED THEN UPDATE SET V=S.G WHEN NOT MATCHED THEN INSERT(ID,V) VALUES(S.ID,S.G)',tag='MERGE 3')
    seen([1,2,3],[10,10,20])
    results=client.query('INSERT INTO LIMIT_COPY VALUES(800,1); SELECT 42 LIMIT -1; INSERT INTO LIMIT_COPY VALUES(801,2)',True)
    require([r['sqlstate'] for r in results]==['','42601'], 'Simple script continued after LIMIT error')
    seen([1,2,3],[10,10,20]); query('SELECT 42 LIMIT 1',rows=[['42']])
    query('BEGIN',state=2)
    query('INSERT INTO LIMIT_COPY VALUES(802,1)',state=2)
    params('SELECT 42 LIMIT $1',['1'],error='42601',state=3)
    query('SELECT 42 LIMIT 1',error='25P02',state=3)
    query('ROLLBACK'); seen([1,2,3],[10,10,20])

    for mode in ('on','off'):
        query('SET standard_conforming_strings='+mode)
        query("SELECT 'LIMIT 1', q'{LIMIT 1}', 7 AS \"LIMIT\" /* LIMIT 1 */ -- LIMIT 1\nFROM RDB$DATABASE",
              rows=[['LIMIT 1','LIMIT 1','7']])
        query("EXECUTE BLOCK RETURNS(V VARCHAR(8)) AS BEGIN V=q'{LIMIT 1}'; /* LIMIT 1 */ SUSPEND; END",rows=[['LIMIT 1']])
        query('SELECT 42 /* LIMIT 9 */ liMiT 1',rows=[['42']])
    query('SET standard_conforming_strings=on')
    # Native source remains storable; LIMIT text alone must not activate the root guard.
    for sql in ('CREATE TABLE LIMIT_OK_CTAS AS SELECT FIRST 1 ID FROM LIMIT_ROWS ORDER BY ID WITH DATA',
                'CREATE VIEW LIMIT_OK_VIEW AS SELECT ID FROM LIMIT_ROWS ORDER BY ID ROWS 1',
                "CREATE PROCEDURE LIMIT_OK_PROC RETURNS(V VARCHAR(8)) AS BEGIN V='LIMIT 1'; END"):
        query(sql)
    query('SELECT ID FROM LIMIT_OK_CTAS',rows=[['1']]); query('SELECT ID FROM LIMIT_OK_VIEW',rows=[['1']])
    query('EXECUTE PROCEDURE LIMIT_OK_PROC',rows=[['LIMIT 1']])
    query('EXECUTE BLOCK RETURNS(V INTEGER) AS BEGIN SELECT FIRST 1 ID FROM LIMIT_ROWS ORDER BY ID INTO :V; SUSPEND; END',rows=[['1']])
    query('CALL LIMIT_PROC((SELECT 41 FROM RDB$DATABASE ROWS 1),?)',rows=[['42']])
    query('EXECUTE PROCEDURE LIMIT_PROC((SELECT FIRST 1 41 FROM RDB$DATABASE))',rows=[['42']])
    query('DROP TABLE LIMIT_OK_CTAS'); query('DROP VIEW LIMIT_OK_VIEW'); query('DROP PROCEDURE LIMIT_OK_PROC')
    report['scope']='synthetic literal LIMIT semantics, not original selected 04/17 or 1C opening'
    return report


def limits_protocols(h):
    raw=ExtendedClient(h).login(); rejected=[]
    try:
        invalid=[('SELECT 42 LIMIT '+count,'42601') for count in
                 ('$1','?','ALL','NULL','-1','+1','1.0','1e0','(1)','1+1','2147483648',
                  '0x80000000','0xffffffff','0o20000000000','0b10000000000000000000000000000000',
                  '9'*200,'','1 LIMIT 1','1 OFFSET 1','1 OFFSET 1 ROWS','1,2','1 ROWS 1',
                  '1 FETCH FIRST 1 ROW ONLY')]
        invalid += [('SELECT 42 '+suffix,'42601') for suffix in
                    ('ROWS 1 LIMIT 1','OFFSET 1 ROWS LIMIT 1','FETCH FIRST 1 ROW ONLY LIMIT 1','AS LIMIT')]
        invalid += [('SELECT '+prefix+' ID FROM LIMIT_ROWS LIMIT 1','42000') for prefix in
                    ('FIRST 1','SKIP 1','FIRST 1 SKIP 1')]
        invalid += [
            ('SELECT 1 LIMIT','42601'),
            ('UPDATE LIMIT_COPY SET V=1 LIMIT 1','42601'), ('DELETE FROM LIMIT_COPY LIMIT 1','42601'),
            ('CREATE TABLE LIMIT_BAD_CTAS AS SELECT ID FROM LIMIT_ROWS LIMIT 1 WITH DATA','0A000'),
            ('CREATE TABLE LIMIT_BAD_CTE AS WITH C AS (SELECT ID FROM LIMIT_ROWS LIMIT 1) SELECT ID FROM C WITH DATA','0A000'),
            ('CREATE VIEW LIMIT_BAD_VIEW AS SELECT (SELECT ID FROM LIMIT_ROWS LIMIT 1) AS V FROM RDB$DATABASE','0A000'),
            ('CREATE PROCEDURE LIMIT_BAD_PROC RETURNS(V INTEGER) AS BEGIN SELECT ID FROM LIMIT_ROWS LIMIT 1 INTO :V; END','0A000'),
            ('EXECUTE BLOCK RETURNS(V INTEGER) AS BEGIN SELECT ID FROM LIMIT_ROWS LIMIT 1 INTO :V; SUSPEND; END','0A000'),
            ('EXECUTE BLOCK AS DECLARE FUNCTION F() RETURNS INTEGER AS BEGIN RETURN (SELECT ID FROM LIMIT_ROWS LIMIT 1); END BEGIN END','0A000'),
            ('CALL LIMIT_PROC((SELECT ID FROM LIMIT_ROWS LIMIT 1),?)','0A000'),
            ('EXECUTE PROCEDURE LIMIT_PROC((SELECT ID FROM LIMIT_ROWS LIMIT 1))','0A000'),
            ('USING(A INTEGER=?) DO SELECT :A FROM RDB$DATABASE LIMIT 1','0A000'),
            ('UPDATE OR INSERT INTO LIMIT_COPY(ID,V) VALUES(899,(SELECT ID FROM LIMIT_ROWS LIMIT 1)) MATCHING(ID)','0A000'),
            ('CREATE VIEW LIMIT_BAD_SYNTAX AS SELECT 1 LIMIT','42601'),
        ]
        for sql,error in invalid:
            try:
                raw.exchange(parse('bad',sql)+parse('discarded','INSERT INTO LIMIT_COPY VALUES(899,999)')+
                             bind('discarded','discarded')+execute('discarded')+packet(b'S'),b'EZ',error=error)
            except Exception as failure:
                raise RuntimeError(sql+': '+str(failure)) from failure
            raw.exchange(bind('missing','discarded')+packet(b'S'),b'EZ',error='26000')
            raw.run('SELECT ID FROM LIMIT_ROWS WHERE ID=$1 LIMIT 1',values=['2'],rows=[['2']],tag='SELECT 1')
            rejected.append({'sql':sql,'sqlstate':error})
        events=raw.exchange(parse('paged','SELECT ID FROM LIMIT_ROWS WHERE ID>=$1 ORDER BY ID LIMIT 3')+
                            describe(b'S','paged')+packet(b'S'),b'1tTZ')
        require(events[1][1] == struct.pack('!HI',1,23) and [c['oid'] for c in columns(events[2][1])]==[23],
                'raw LIMIT parameter/output Describe differs')
        raw.exchange(bind('a','paged',['1'])+bind('b','paged',['2'])+execute('a',1)+packet(b'H'),b'22Ds',end=b's',rows=[['1']])
        raw.exchange(execute('b',1)+packet(b'H'),b'Ds',end=b's',rows=[['2']])
        raw.exchange(execute('a')+packet(b'H'),b'DDC',end=b'C',rows=[['2'],['3']],tag='SELECT 3')
        raw.exchange(execute('b')+packet(b'S'),b'DDCZ',rows=[['3'],['4']],tag='SELECT 3')
        raw.run('SELECT ID FROM LIMIT_ROWS LIMIT 0',rows=[],tag='SELECT 0')
    finally:
        raw.close()
    output=h.isql('limits_no_side_effects','SET LIST ON;\n'
        "SELECT COUNT(*) AS N FROM RDB$RELATIONS WHERE RDB$RELATION_NAME STARTING WITH 'LIMIT_BAD';\n"
        "SELECT COUNT(*) AS N FROM RDB$PROCEDURES WHERE RDB$PROCEDURE_NAME STARTING WITH 'LIMIT_BAD';\n"
        'SELECT COUNT(*) AS N FROM LIMIT_COPY WHERE ID>=800;\nROLLBACK;\nQUIT;\n')
    require(re.findall(r'^N\s+(\d+)\s*$',output,re.M)==['0','0','0'], 'LIMIT guard/discard left effects')
    env=h.env.copy();env.update(ISC_USER='WIRE_HOST_TEST',ISC_PASSWORD=h.password)
    for dialect in (1,2,3):
        script=('SET LIST ON;\nSELECT ID FROM LIMIT_ROWS LIMIT 1;\n'
                'SELECT FIRST 1 42 AS RECOVERY FROM RDB$DATABASE;\n'
                'SELECT LIMIT FROM (SELECT 43 AS LIMIT FROM RDB$DATABASE);\n'
                'SELECT ID FROM LIMIT_ROWS ORDER BY ID ROWS 2 TO 3;\n'
                'SELECT ID AS OFF_ID FROM LIMIT_ROWS ORDER BY ID OFFSET 1 ROWS FETCH NEXT 2 ROWS ONLY;\n'
                'ROLLBACK;\nQUIT;\n')
        run=subprocess.run([str(h.runtime/'bin/isql'),'-quiet','-autoterm','-sql_dialect',str(dialect),
                            f'inet://127.0.0.1:{h.native_port}/wire_host'],input=script,text=True,
                           capture_output=True,env=env,cwd=h.temp,timeout=h.timeout)
        output=run.stdout+run.stderr;(h.work/f'limits-native-{dialect}.log').write_text(h.redact(output))
        require(run.returncode!=0 and re.findall(r'SQLSTATE\s*=\s*(\w+)',output)==['42000'] and
                re.findall(r'^RECOVERY\s+(\d+)\s*$',output,re.M)==['42'] and
                re.findall(r'^LIMIT\s+(\d+)\s*$',output,re.M)==['43'] and
                re.findall(r'^ID\s+(\d+)\s*$',output,re.M)==['2','3'] and
                re.findall(r'^OFF_ID\s+(\d+)\s*$',output,re.M)==['2','3'], 'native LIMIT/AUTOTERM differs: '+output)
    return {'rejected':rejected, 'discard_until_sync':True, 'portal_suspension_and_independence':True,
            'limit_zero_metadata':True, 'native_dialects':[1,2,3], 'native_autoterm_recovery':True,
            'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
