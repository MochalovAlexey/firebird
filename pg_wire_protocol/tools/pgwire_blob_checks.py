"""Binary BLOB output using the existing private 1c/libpq/raw/native harness."""
import ctypes as C
import hashlib
import json
import os
import re
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace

from pgwire_extended_query_smoke import ExtendedClient, parse, bind, describe, execute, close, columns
from pgwire_startup_smoke import packet
from wire_host_smoke import Harness, require


CONFIG_QUERY = 'SELECT Creation,Modified,Attributes,DataSize,BinaryData FROM public.params WHERE FileName = $1 ORDER BY PartNo'


def payload(identifier):
    if identifier == 0:
        return None
    if identifier == 1:
        return b''
    if identifier == 2:
        return bytes(range(256))
    if identifier == 3:
        return bytes.fromhex('005c27ffc080eda080f4908080')
    sizes = {4:10000, 5:10000, 6:65537, 7:1048566, 8:1048567, 9:524282,
             10:524283, 11:2097169, 12:262140, 13:524270}
    cycle = bytes((i*37+11) % 256 for i in range(256))
    return (cycle * ((sizes[identifier]+255)//256))[:sizes[identifier]]


def encoded(identifier, binary=False):
    value = payload(identifier)
    return None if value is None else ('' if binary else '\\x') + value.hex()


def blobs_fixture(h):
    h.isql('blobs_schema', f"CONNECT '{h.database}';\n"
        'CREATE SCHEMA "public"; GRANT USAGE ON SCHEMA "public" TO "PUBLIC";\n'
        'CREATE TABLE "PUBLIC"."BLOB_ROWS"("ID" INTEGER NOT NULL PRIMARY KEY, "B" BLOB SUB_TYPE BINARY, "BT" BLOB SUB_TYPE TEXT, "BX" BLOB SUB_TYPE 2);\n'
        'CREATE TABLE "PUBLIC"."BLOB_COPY"("ID" INTEGER NOT NULL PRIMARY KEY, "B" BLOB SUB_TYPE BINARY);\n'
        'CREATE TABLE "public"."params"("creation" INTEGER, "modified" INTEGER, "attributes" INTEGER, "datasize" BIGINT, '
        '"binarydata" BLOB SUB_TYPE BINARY, "filename" VARCHAR(40) CHARACTER SET UTF8, "partno" INTEGER);\n'
        'SET TERM ^;\nCREATE PROCEDURE "PUBLIC"."BLOB_PROC"("N" INTEGER) RETURNS("PAYLOAD" BLOB SUB_TYPE BINARY) AS '
        'BEGIN SELECT "B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"=:"N" INTO :"PAYLOAD"; END^\nSET TERM ;^\n'
        'GRANT SELECT ON "PUBLIC"."BLOB_ROWS" TO USER WIRE_HOST_TEST;\n'
        'GRANT SELECT ON "public"."params" TO USER WIRE_HOST_TEST;\n'
        'GRANT SELECT, INSERT, UPDATE, DELETE ON "PUBLIC"."BLOB_COPY" TO USER WIRE_HOST_TEST;\n'
        'GRANT EXECUTE ON PROCEDURE "PUBLIC"."BLOB_PROC" TO USER WIRE_HOST_TEST;\nCOMMIT;\nQUIT;\n', embedded=True)
    source = Path(__file__).with_name('pgwire_blob_fixture.cpp')
    binary = h.work/'blob_fixture'
    built = subprocess.run(['g++','-std=c++17','-g','-O0',str(source),'-I'+str(h.runtime/'include'),
        '-L'+str(h.runtime/'lib'),'-Wl,-rpath,'+str(h.runtime/'lib'),'-lfbclient','-o',str(binary)],
        env=h.env,capture_output=True,text=True,timeout=h.timeout)
    (h.work/'blob_fixture_compile.log').write_text(built.stdout+built.stderr)
    require(built.returncode == 0, 'BLOB fixture compilation failed')
    run = subprocess.run([str(binary),str(h.database)],env=h.env,cwd=h.temp,
                         capture_output=True,text=True,timeout=h.timeout)
    (h.work/'blob_fixture.log').write_text(h.redact(run.stdout+run.stderr))
    require(run.returncode == 0, 'Native BLOB fixture/readback failed')
    h.summary['blob_fixture'] = json.loads(run.stdout)
    h.summary['blob_fixture']['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    h.isql('blobs_params', f"CONNECT '{h.database}';\n"
        'INSERT INTO "public"."params" SELECT 1,2,3,OCTET_LENGTH("B"),"B",\'config\',"ID" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID" BETWEEN 1 AND 3;\n'
        'UPDATE "PUBLIC"."BLOB_ROWS" SET "BT"=\'text\' WHERE "ID"=2;\nCOMMIT;\nQUIT;\n', embedded=True)


def blobs_client(config, client):
    report = {'checks':[], 'unescaped':0, 'native_observations':0, 'metadata':{}}
    for name, result, args in (
        ('PQfsize',C.c_int,[C.c_void_p,C.c_int]), ('PQfmod',C.c_int,[C.c_void_p,C.c_int]),
        ('PQfformat',C.c_int,[C.c_void_p,C.c_int]),
        ('PQunescapeBytea',C.c_void_p,[C.c_char_p,C.POINTER(C.c_size_t)]),
        ('PQfreemem',None,[C.c_void_p])):
        function=getattr(client.pq,name);function.restype=result;function.argtypes=args
    original_result=client.result

    def result(handle, values=False, binary=False):
        require(handle, 'libpq allocated no result')
        metadata=[(client.pq.PQftype(handle,i),client.pq.PQfsize(handle,i),client.pq.PQfmod(handle,i),
                   client.pq.PQfformat(handle,i)) for i in range(client.pq.PQnfields(handle))]
        data=original_result(handle,values,binary)
        for i,(oid,size,modifier,format_code) in enumerate(metadata):
            if oid != 17:
                continue
            require(size == -1 and modifier == -1 and format_code == int(binary), 'bytea metadata differs')
            if values and not binary:
                for row in data['rows']:
                    if row[i] is None:
                        continue
                    length=C.c_size_t()
                    pointer=client.pq.PQunescapeBytea(row[i].encode('ascii'),C.byref(length))
                    require(pointer, 'PQunescapeBytea returned NULL')
                    try:
                        actual=C.string_at(pointer,length.value)
                    finally:
                        client.pq.PQfreemem(pointer)
                    require(row[i] == '\\x'+actual.hex(), 'PQunescapeBytea differs from exact hex profile')
                    report['unescaped'] += 1
        return data

    client.result=result
    observer=SimpleNamespace(runtime=Path(config['runtime']),env=os.environ.copy(),password=config['password'],
        native_port=config['native_port'],work=Path(config['work']),temp=Path(config['work'])/'tmp',
        phase='blobs',timeout=config['timeout'],redact=client.redact)

    def checked(label, data, rows=None, error='', state=0, oids=None, tag=None):
        require(data['sqlstate']==error and client.pq.PQtransactionStatus(client.connection)==state and
                (rows is None or data.get('rows')==rows) and
                (oids is None or [c['oid'] for c in data['columns']]==oids) and
                (tag is None or data['tag']==tag), label+': '+repr({k:data[k] for k in ('sqlstate','row_count','columns','tag')}))
        report['checks'].append({'case':label,'sqlstate':error,'state':state})
        (Path(config['work'])/'blobs_progress.json').write_text(json.dumps(report,indent=2)+'\n')
        return data

    def query(sql, **expected):
        # Exercise actual PQexec as well as PQexecParams/PQexecPrepared below.
        return checked(sql,client.result(client.pq.PQexec(client.connection,sql.encode()),True),**expected)

    def params(sql, values=(), prepared=False, binary=False, **expected):
        return checked(sql,client.params(sql,values,prepared=prepared,result_format=int(binary)),**expected)

    def prepare(name, sql, inputs, outputs):
        checked('prepare '+name,client.result(client.pq.PQprepare(client.connection,name.encode(),sql.encode(),0,None)))
        data=checked('describe '+name,client.result(client.pq.PQdescribePrepared(client.connection,name.encode())),oids=outputs)
        require(data['parameter_oids']==inputs,'BLOB prepared input metadata differs')
        report['metadata'][name]={'input':inputs,'output':outputs}

    def seen(ids):
        report['native_observations'] += 1
        output=Harness.isql(observer,'blobs_observer_'+str(report['native_observations']),
            'SET ROLE PGWIRE_OBSERVER;\nSET LIST ON;\nSELECT "ID" FROM "PUBLIC"."BLOB_COPY" ORDER BY "ID";\nROLLBACK;\nQUIT;\n')
        require(re.findall(r'^ID[ \t]+(\d+)[ \t]*$',output,re.M)==[str(i) for i in ids], 'Native BLOB DML readback differs: '+output)

    sql='SELECT "B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"=$1'
    prepare('blob',sql,[23],[17])
    for identifier in range(7):
        query(f'SELECT "B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"={identifier}',rows=[[encoded(identifier)]],oids=[17],tag='SELECT 1')
        params(sql,[str(identifier)],rows=[[encoded(identifier)]],oids=[17])
        params('blob',[str(identifier)],prepared=True,binary=True,rows=[[encoded(identifier,True)]],oids=[17])
    params('blob',[None],prepared=True,rows=[],tag='SELECT 0')
    prepare('config',CONFIG_QUERY,[1043],[23,23,23,20,17])
    expected=[['1','2','3',str(len(payload(i))),encoded(i)] for i in (1,2,3)]
    params(CONFIG_QUERY,['config'],rows=expected)
    for value in ('config','missing',None,'config'):
        params('config',[value],prepared=True,rows=expected if value=='config' else [])
    params('config',['config'],prepared=True,binary=True,
           rows=[[struct.pack('!i',1).hex(),struct.pack('!i',2).hex(),struct.pack('!i',3).hex(),
                  struct.pack('!q',len(payload(i))).hex(),encoded(i,True)] for i in (1,2,3)])
    for control in ('COMMIT','ROLLBACK'):
        query('BEGIN',state=2)
        params('blob',['2'],prepared=True,binary=True,rows=[[encoded(2,True)]],state=2)
        query(control)
        params('blob',['3'],prepared=True,rows=[[encoded(3)]])

    for identifier in (0,1,3,5):
        query(f'EXECUTE PROCEDURE "PUBLIC"."BLOB_PROC"({identifier})',rows=[[encoded(identifier)]],oids=[17])
        params('EXECUTE PROCEDURE "PUBLIC"."BLOB_PROC"(?)',[str(identifier)],binary=True,rows=[[encoded(identifier,True)]],oids=[17])
    prepare('procedure','EXECUTE PROCEDURE "PUBLIC"."BLOB_PROC"(?)',[23],[17])
    for identifier in (1,2,3):
        params('procedure',[str(identifier)],prepared=True,binary=True,rows=[[encoded(identifier,True)]])
    block='EXECUTE BLOCK RETURNS("V" BLOB SUB_TYPE BINARY) AS BEGIN SELECT "B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"=3 INTO :"V"; SUSPEND; END'
    query(block,rows=[[encoded(3)]],oids=[17])
    params(block,binary=True,rows=[[encoded(3,True)]],oids=[17])
    query("SELECT CAST(x'005c27ffc080' AS BLOB SUB_TYPE BINARY)",rows=[['\\x005c27ffc080']],oids=[17])
    query('SELECT 1::BLOB',rows=[['\\x31']],oids=[17])
    query('INSERT INTO "PUBLIC"."BLOB_COPY" SELECT 100,"B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"=2 RETURNING "B"',rows=[[encoded(2)]],oids=[17]);seen([100])
    params('UPDATE "PUBLIC"."BLOB_COPY" SET "B"=(SELECT "B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"=$1) WHERE "ID"=100 RETURNING "B"',['3'],
           binary=True,rows=[[encoded(3,True)]],oids=[17]);seen([100])
    params('DELETE FROM "PUBLIC"."BLOB_COPY" WHERE "ID"=$1 RETURNING "B"',['100'],binary=True,rows=[[encoded(3,True)]]);seen([])

    query('SELECT "B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"=9',rows=[[encoded(9)]],oids=[17])
    params('blob',['7'],prepared=True,binary=True,rows=[[encoded(7,True)]])
    for identifier in (10,11):
        query(f'SELECT "B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"={identifier}',error='0A000')
        query('SELECT 42',rows=[['42']])
    for identifier in (8,11):
        params('blob',[str(identifier)],prepared=True,binary=True,error='0A000')
        params('blob',['2'],prepared=True,binary=True,rows=[[encoded(2,True)]])
    # A BLOB fitting by itself may exceed the shared budget with another column.
    query('SELECT "B", 42 FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"=9',error='0A000')
    query('INSERT INTO "PUBLIC"."BLOB_COPY" SELECT 200,"B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"=11 RETURNING "B"',error='0A000');seen([])
    params('INSERT INTO "PUBLIC"."BLOB_COPY" SELECT 201,"B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"=$1 RETURNING "B"',['11'],binary=True,error='0A000');seen([])
    query('BEGIN',state=2)
    query('INSERT INTO "PUBLIC"."BLOB_COPY" SELECT 202,"B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"=2',state=2)
    query('INSERT INTO "PUBLIC"."BLOB_COPY" SELECT 203,"B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"=11 RETURNING "B"',error='0A000',state=3)
    query('SELECT 42',error='25P02',state=3)
    query('ROLLBACK');seen([])
    query('BEGIN',state=2)
    params('INSERT INTO "PUBLIC"."BLOB_COPY" SELECT 206,"B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"=$1 RETURNING "B"',['11'],
           binary=True,error='0A000',state=3)
    params('blob',['2'],prepared=True,binary=True,error='25P02',state=3)
    query('ROLLBACK');seen([])
    results=client.query('INSERT INTO "PUBLIC"."BLOB_COPY" SELECT 204,"B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"=2; '
                         'SELECT "B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"=11; INSERT INTO "PUBLIC"."BLOB_COPY" SELECT 205,"B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"=2',True)
    require([r['sqlstate'] for r in results]==['','0A000'], 'Simple continued after BLOB output failure')
    seen([]);query('SELECT 42',rows=[['42']])
    for statement in ('SELECT "BT" FROM "PUBLIC"."BLOB_ROWS"','SELECT "BX" FROM "PUBLIC"."BLOB_ROWS"',
                      'INSERT INTO "PUBLIC"."BLOB_COPY" VALUES(300,CAST(\'x\' AS BLOB)) RETURNING CAST(\'x\' AS BLOB SUB_TYPE TEXT)'):
        query(statement,error='0A000')
    params('SELECT CAST($1 AS BLOB SUB_TYPE BINARY)',['data'],rows=[['\\x64617461']],oids=[17])
    params('SELECT CAST($1 AS BLOB SUB_TYPE BINARY)',[None],rows=[[None]],oids=[17])
    params('SELECT CAST($1 AS BLOB SUB_TYPE TEXT)',['data'],error='0A000')
    params('SELECT CAST($1 AS BLOB SUB_TYPE 2)',['data'],error='0A000')
    query('SELECT 1::bytea',error='42601')
    query("SET bytea_output='hex'",error='42601')
    seen([])
    report['scope']='Output-only subtype 0; synthetic selected06 SQL, not original schema/data/Bind or 1C opening'
    return report


def blobs_protocols(h):
    raw=ExtendedClient(h).login(); checks=[]
    try:
        events=raw.exchange(parse('blob','SELECT "B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"=$1')+
                            describe(b'S','blob')+packet(b'S'),b'1tTZ')
        require(events[1][1]==struct.pack('!HI',1,23) and columns(events[2][1])[0]==
                {'name':'B','table':0,'attribute':0,'oid':17,'size':-1,'modifier':-1,'format':0}, 'Raw BLOB Describe differs')
        raw.exchange(bind('waiting','blob',['2'])+describe(b'P','waiting')+packet(b'H'),b'2T',end=b'T')
        output=h.isql('blobs_no_early_transaction','SET ROLE PGWIRE_OBSERVER;\nSET LIST ON;\n'
            'SELECT COUNT(*) AS "N" FROM MON$TRANSACTIONS T JOIN MON$ATTACHMENTS A ON A.MON$ATTACHMENT_ID=T.MON$ATTACHMENT_ID '
            "WHERE A.MON$REMOTE_VERSION='PgWire' AND A.MON$SYSTEM_FLAG=0;\nROLLBACK;\nQUIT;\n")
        require(re.findall(r'^N\s+(\d+)\s*$',output,re.M)==['0'], 'Parse/Bind started an application transaction')
        raw.exchange(execute('waiting')+packet(b'S'),b'DCZ',rows=[[encoded(2)]],tag='SELECT 1')
        checks.append('describe_bind_no_transaction_then_execute')
        sql='SELECT "B","B","B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"<=2 ORDER BY "ID"'
        raw.run(sql,results=[0,1,0],rows=[[encoded(i),payload(i),encoded(i)] for i in range(3)],tag='SELECT 3')
        checks.append('mixed_formats_null_empty_all_bytes')
        raw.exchange(parse('paged','SELECT "B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID" BETWEEN $1 AND 3 ORDER BY "ID"')+
                     bind('a','paged',['1'],results=[1])+bind('b','paged',['2'],results=[0])+
                     execute('a',1)+packet(b'H'),b'122Ds',end=b's',rows=[[b'']])
        raw.exchange(execute('b',1)+packet(b'H'),b'Ds',end=b's',rows=[[encoded(2)]])
        raw.exchange(execute('a')+packet(b'H'),b'DDC',end=b'C',rows=[[payload(2)],[payload(3)]],tag='SELECT 3')
        raw.exchange(execute('b')+packet(b'S'),b'DCZ',rows=[[encoded(3)]],tag='SELECT 2')
        raw.exchange(bind('c','paged',['3'],results=[1])+execute('c',1)+packet(b'H'),b'2Ds',end=b's',rows=[[payload(3)]])
        raw.exchange(close(b'P','c')+packet(b'S'),b'3Z')
        checks.append('independent_portals_resume_close_and_rebind_after_commit')
        # Two BLOB values, one scalar and a NULL: all share the 1 MiB budget.
        sql=('SELECT A."B",42,"B"."B",CAST(NULL AS INTEGER) FROM "PUBLIC"."BLOB_ROWS" A CROSS JOIN "PUBLIC"."BLOB_ROWS" "B" '
             'WHERE A."ID"=12 AND "B"."ID"=13')
        raw.run(sql,results=[0,0,1,0],rows=[[encoded(12),'42',payload(13),None]],tag='SELECT 1')
        raw.exchange(parse('',sql.replace(',42,',',420,'))+bind(results=[0,0,1,0])+describe(b'P')+execute()+packet(b'S'),
                     b'12TEZ',error='0A000')
        raw.run('SELECT 42',rows=[['42']])
        checks.append('aggregate_mixed_row_exact_and_one_byte_over')
        # Single text BLOB with a neighbouring scalar, exact frame and +1 byte.
        text_sql='SELECT SUBSTRING("B" FROM 1 FOR 524279),42 FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"=9'
        raw.run(text_sql,rows=[['\\x'+payload(9)[:524279].hex(),'42']],tag='SELECT 1')
        raw.exchange(parse('',text_sql.replace(',42 ', ',420 '))+bind()+describe(b'P')+execute()+packet(b'S'),b'12TEZ',error='0A000')
        raw.run('SELECT 42',rows=[['42']])
        checks.append('text_row_exact_and_one_wire_byte_over')
        for results in ([0],[1]):
            raw.exchange(parse('','SELECT "B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID" IN (2,11) ORDER BY "ID"')+
                         bind(results=results)+describe(b'P')+execute()+packet(b'S'),b'12TDEZ',error='0A000',
                         rows=[[payload(2) if results==[1] else encoded(2)]])
            raw.run('SELECT 42',rows=[['42']])
        raw.exchange(packet(b'Q',b'SELECT "B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID" IN (2,11) ORDER BY "ID"\x00'),b'TDEZ',error='0A000',rows=[[encoded(2)]])
        raw.run('SELECT 42',rows=[['42']])
        checks.append('later_row_failure_sends_no_partial_datarow_simple_and_extended')
        for sql in ('SELECT "BT" FROM "PUBLIC"."BLOB_ROWS"','SELECT "BX" FROM "PUBLIC"."BLOB_ROWS"','SELECT CAST($1 AS BLOB SUB_TYPE TEXT)','SELECT 1::bytea'):
            error='42601' if sql.endswith('::bytea') else '0A000'
            raw.exchange(parse('bad',sql)+parse('discarded','INSERT INTO "PUBLIC"."BLOB_COPY" VALUES(900,NULL)')+
                         bind('discarded','discarded')+execute('discarded')+packet(b'S'),b'EZ',error=error)
            raw.exchange(bind('missing','discarded')+packet(b'S'),b'EZ',error='26000')
            raw.run('SELECT "B" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID"=2',results=[1],rows=[[payload(2)]])
        checks.append('unsupported_metadata_input_and_grammar_discard_until_sync')
    finally:
        raw.close()
    output=h.isql('blobs_final_native','SET LIST ON;\nSELECT COUNT(*) AS "N" FROM "PUBLIC"."BLOB_COPY";\n'
        'SELECT OCTET_LENGTH("B") AS "N" FROM "PUBLIC"."BLOB_ROWS" WHERE "ID" IN (2,5) ORDER BY "ID";\nROLLBACK;\nQUIT;\n')
    require(re.findall(r'^N\s+(\d+)\s*$',output,re.M)==['0','256','10000'],'BLOB errors left DML effects or altered native data')
    return {'checks':checks,'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
