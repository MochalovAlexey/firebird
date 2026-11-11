"""APP-08 bytea input: existing libpq/raw/native harness, synthetic data only."""
import ctypes as C
import hashlib
import json
import os
import re
import struct
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from pgwire_extended_query_smoke import ExtendedClient, parse, bind, describe, execute, close
from pgwire_startup_smoke import packet
from wire_host_smoke import Harness, require

SELECT = 'SELECT CAST($1 AS BLOB SUB_TYPE BINARY)'
INSERT = 'INSERT INTO "PUBLIC"."BIN_ROWS"("ID","B","NOTE") VALUES($1,$2,$3) RETURNING "B"'
CONFIG_UPDATE = 'UPDATE public.files SET MODIFIED = $1, DATASIZE = $2, BINARYDATA = $3 WHERE FILENAME = $4 AND PARTNO = $5'


def payload(size):
    cycle = bytes((i * 37 + 11) % 256 for i in range(256))
    return (cycle * ((size + 255) // 256))[:size]


def text_cases():
    # Shared with the private Postgres Pro reference; PQunescapeBytea is not an error oracle.
    good = [(b'', b''), (b'\\x', b''), (b'\\x \t\n\r', b''),
            (b'\\x 00 aB\tCd\nEF\r ', bytes.fromhex('00abcdef')),
            (b'\\000\\377\\200\\\\\'', b'\x00\xff\x80\\\''),
            (" 'Я' ".encode(), " 'Я' ".encode()), (b' \\\\x41', b' \\x41'),
            (b'\\x5c303030', b'\\000'), (b'\\0012', b'\x012')]
    bad = [(value, '22023') for value in
           (b'\\x0', b'\\x0 ', b'\\x0 0', b'\\xgg', b'\\x00\v', b'\\x\f00', b'\\x00z0')]
    bad += [(value, '22P02') for value in
            (b'\\', b'\\0', b'\\00', b'\\400', b'\\378', b'\\q', b' \\x41', b'\\X00', b'\\ x00')]
    bad += [(b'\xc0\x80', '22021')]
    return good, bad


def mixed_shape():
    blobs = [i for i in range(92) if i % 4 != 3 and i != 90]
    values = [None if i in blobs and i % 8 == 0 else
              bytes((i + j) % 256 for j in range(i % 17)) if i in blobs else str(i) for i in range(92)]
    return blobs, values


def blob_input_fixture(h):
    blobs, _ = mixed_shape()
    fields = ','.join(f'C{i} ' + ('BLOB SUB_TYPE BINARY' if i in blobs else 'INTEGER') for i in range(92))
    h.isql('blob_input_schema', f"CONNECT '{h.database}';\n"
        'CREATE SCHEMA "public"; GRANT USAGE ON SCHEMA "public" TO "PUBLIC";\n'
        'CREATE TABLE "PUBLIC"."BIN_ROWS"("ID" INTEGER NOT NULL PRIMARY KEY, "B" BLOB SUB_TYPE BINARY, "NOTE" VARCHAR(12));\n'
        'CREATE SEQUENCE "PUBLIC"."BIN_GUARD";\n'
        'CREATE TABLE "public"."files"("modified" TIMESTAMP, "datasize" BIGINT, "binarydata" BLOB SUB_TYPE BINARY, '
        '"filename" VARCHAR(80) CHARACTER SET UTF8, "partno" INTEGER);\n'
        'INSERT INTO "public"."files" VALUES(TIMESTAMP \'2000-01-01\',0,NULL,\'config\',1);\n'
        'INSERT INTO "public"."files" VALUES(TIMESTAMP \'2000-01-01\',0,NULL,\'config\',2);\n'
        'INSERT INTO "public"."files" VALUES(TIMESTAMP \'2000-01-01\',0,NULL,\'other\',1);\n'
        f'CREATE TABLE "PUBLIC"."BIN_MIXED"({fields});\n'
        'SET TERM ^;\n'
        'CREATE TRIGGER BIN_COUNT FOR "PUBLIC"."BIN_ROWS" ACTIVE BEFORE INSERT POSITION 0 AS DECLARE "N" BIGINT; '
        'BEGIN "N"=GEN_ID("PUBLIC"."BIN_GUARD",1); END^\n'
        'CREATE PROCEDURE "PUBLIC"."BIN_PROC"(A BLOB SUB_TYPE BINARY) RETURNS("B" BLOB SUB_TYPE BINARY) AS BEGIN "B"=A; END^\n'
        'SET TERM ;^\n'
        'GRANT SELECT,INSERT,UPDATE,DELETE ON "PUBLIC"."BIN_ROWS" TO USER WIRE_HOST_TEST;\n'
        'GRANT SELECT,UPDATE ON "public"."files" TO USER WIRE_HOST_TEST;\n'
        'GRANT SELECT,INSERT ON "PUBLIC"."BIN_MIXED" TO USER WIRE_HOST_TEST;\n'
        'GRANT EXECUTE ON PROCEDURE "PUBLIC"."BIN_PROC" TO USER WIRE_HOST_TEST;\n'
        'GRANT USAGE ON SEQUENCE "PUBLIC"."BIN_GUARD" TO USER WIRE_HOST_TEST;\nCOMMIT;\nQUIT;\n', embedded=True)
    source = Path(__file__).with_name('pgwire_blob_fixture.cpp')
    run = subprocess.run(['g++','-std=c++17','-g','-O0',str(source),'-I'+str(h.runtime/'include'),
        '-L'+str(h.runtime/'lib'),'-Wl,-rpath,'+str(h.runtime/'lib'),'-lfbclient','-o',str(h.work/'blob_fixture')],
        env=h.env,capture_output=True,text=True,timeout=h.timeout)
    (h.work/'blob_fixture_compile.log').write_text(run.stdout + run.stderr)
    require(run.returncode == 0, 'Native BLOB readback helper did not compile')
    h.summary['readback_source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()


def native_read(config, client, expected, label):
    work = Path(config['work'])
    manifest = work / (label + '.manifest')
    manifest.write_text(''.join(sql + '\t' + ('NULL' if value is None else value.hex()) + '\n'
                               for sql, value in expected))
    env = os.environ.copy()
    env.update(ISC_USER='WIRE_HOST_TEST', ISC_PASSWORD=config['password'])
    run = subprocess.run([str(work/'blob_fixture'), f"inet://127.0.0.1:{config['native_port']}/wire_host",
        '--read',str(manifest)], env=env, cwd=work/'tmp', capture_output=True,text=True,timeout=config['timeout'])
    (work/(label+'.log')).write_text(client.redact(run.stdout + run.stderr))
    require(run.returncode == 0, 'Independent native byte readback failed: '+label)
    return json.loads(run.stdout)


def blob_input_client(config, client):
    report = {'checks':[], 'native_readbacks':[], 'metadata':{}}
    persisted = {}
    observer = SimpleNamespace(runtime=Path(config['runtime']), env=os.environ.copy(), password=config['password'],
        native_port=config['native_port'], work=Path(config['work']), temp=Path(config['work'])/'tmp',
        phase='blob_input', timeout=config['timeout'], redact=client.redact)

    def checked(label, data, rows=None, error='', state=0, tag=None):
        require(data['sqlstate'] == error and client.pq.PQtransactionStatus(client.connection) == state and
                (rows is None or data.get('rows') == rows) and (tag is None or data['tag'] == tag),
                label + ': ' + repr({k:data.get(k) for k in ('sqlstate','tag','row_count','columns')}) +
                ', state=' + str(client.pq.PQtransactionStatus(client.connection)))
        report['checks'].append({'case':label, 'sqlstate':error, 'state':state})
        (Path(config['work'])/'blob_input_progress.json').write_text(json.dumps(report,indent=2)+'\n')
        return data

    def query(sql, **expect):
        return checked(sql, client.query(sql,True)[0], **expect)

    def params(label, sql, values=(), **expect):
        options = {key:expect.pop(key) for key in ('oids','formats','prepared','result_format') if key in expect}
        return checked(label, client.params(sql,values,**options),**expect)

    def prepare(name, sql, hints=(), inputs=(17,)):
        types = (C.c_uint * len(hints))(*hints) if hints else None
        checked('prepare '+name, client.result(client.pq.PQprepare(client.connection,name.encode(),sql.encode(),len(hints),types)))
        data = checked('describe '+name, client.result(client.pq.PQdescribePrepared(client.connection,name.encode())))
        require(data['parameter_oids'] == list(inputs), 'ParameterDescription OIDs differ')
        report['metadata'][name] = data['parameter_oids']

    def native(extra=(), transactions=0, wait_disconnect=False):
        expected = [(f'SELECT "B" FROM "PUBLIC"."BIN_ROWS" WHERE "ID"={key}', value) for key,value in sorted(persisted.items())]
        label = 'blob_input_native_'+str(len(report['native_readbacks']))
        sql = ('SET ROLE PGWIRE_OBSERVER;\nSET LIST ON;\nSELECT COUNT(*) AS "N" FROM "PUBLIC"."BIN_ROWS";\n'
            'SELECT COUNT(*) AS "N" FROM MON$TRANSACTIONS T JOIN MON$ATTACHMENTS A '
            'ON A.MON$ATTACHMENT_ID=T.MON$ATTACHMENT_ID '
            "WHERE A.MON$REMOTE_VERSION='PgWire' AND A.MON$SYSTEM_FLAG=0;\nROLLBACK;\nQUIT;\n")
        deadline = time.monotonic() + config['timeout']
        while True:
            output = Harness.isql(observer,label+'_count',sql)
            counts = re.findall(r'^N\s+(\d+)\s*$',output,re.M)
            if counts == [str(len(persisted)),str(transactions)]:
                break
            require(wait_disconnect and time.monotonic() < deadline,
                    'Native committed row or application transaction count differs: '+repr(counts))
            # PQfinish closes the socket before the server finishes its rollback.
            time.sleep(0.02)
        report['native_readbacks'].append(native_read(config,client,expected+list(extra),label))

    for name, hints in [('inferred',()),('zero',(0,)),('bytea',(17,))]:
        prepare(name,SELECT,hints)
    params('hint mismatch',SELECT,[b'a'],oids=[25],error='0A000')
    params('untyped remains SQL-04','SELECT $1',[b'a'],error='HY004')
    for sql in ('SELECT CAST($1 AS BLOB SUB_TYPE TEXT)', 'SELECT CAST($1 AS BLOB SUB_TYPE 2)',
                'SELECT CAST($1 AS VARCHAR(20) CHARACTER SET "OCTETS")'):
        params('unsupported input',sql,[b'a'],error='0A000')
    params('SQL bytea cast excluded','SELECT $1::bytea',[b'a'],error='42601')
    prepare('insert',INSERT,inputs=(23,17,1043))
    identifier = 0
    for input_format in (0,1):
        for size in (0,1,32765,32766,40444,65533):
            value = payload(size)
            wire = b'\\x'+value.hex().encode() if input_format == 0 else value
            identifier += 1
            params(f'insert boundary {input_format}/{size}', 'insert', [str(identifier),wire,'boundary'],
                   formats=[0,input_format,0],prepared=True,result_format=input_format,
                   rows=[[(value.hex() if input_format else '\\x'+value.hex())]],tag='INSERT 0 1')
            persisted[identifier] = value
        wire = payload(65534) if input_format else b'\\x'+payload(65534).hex().encode()
        params(f'reject 65534/{input_format}',INSERT,['900',wire,'too large'],formats=[0,input_format,0],error='0A000')
    for value in (None,b'',bytes(range(256)),bytes.fromhex('005c27ffc080eda080f4908080')):
        identifier += 1
        params('raw bytes/null/empty',INSERT,[str(identifier),value,'bytes'],formats=[0,1,0],
               rows=[[None if value is None else '\\x'+value.hex()]])
        persisted[identifier] = value
    good,bad = text_cases()
    for mode in ('on','off'):
        query('SET standard_conforming_strings='+mode)
        for i,(wire,value) in enumerate(good):
            identifier += 1
            params(f'escape/hex {mode}/{i}', 'insert',[str(identifier),wire,'text'],prepared=True,rows=[['\\x'+value.hex()]])
            persisted[identifier] = value
        for i,(wire,error) in enumerate(bad):
            params(f'invalid text {mode}/{i}',INSERT,['900',wire,'bad'],error=error)
    query('SET standard_conforming_strings=on')
    native()
    params('update returning','UPDATE "PUBLIC"."BIN_ROWS" SET "B"=$1 WHERE "ID"=$2 RETURNING "B"',
           [b'\\000\\377','1'],result_format=1,rows=[['00ff']],tag='UPDATE 1')
    persisted[1] = b'\x00\xff'
    for value in (b'',payload(40444),None):
        params('singleton native parameter','EXECUTE PROCEDURE "PUBLIC"."BIN_PROC"(?)',[value],formats=[1],
               result_format=1,rows=[[None if value is None else value.hex()]])
    for control in ('COMMIT','ROLLBACK'):
        query('BEGIN',state=2)
        params('prepared in T','bytea',[payload(32766)],formats=[1],prepared=True,result_format=1,
               rows=[[payload(32766).hex()]],state=2)
        query(control)
        params('prepared reused after '+control,'bytea',[b'x'],formats=[1],prepared=True,rows=[['\\x78']])
    query('BEGIN',state=2)
    params('transaction insert',INSERT,['100',b'abc','tx'],formats=[0,1,0],rows=[['\\x616263']],state=2)
    native(transactions=1)  # A separate native attachment cannot see uncommitted bytes.
    query('SAVEPOINT S',state=2)
    params('savepoint update','UPDATE "PUBLIC"."BIN_ROWS" SET "B"=$1 WHERE "ID"=100 RETURNING "B"',[b'changed'],formats=[1],state=2)
    params('Bind error in T',SELECT,[b'\\400'],error='22P02',state=3)
    query('SELECT 42',error='25P02',state=3)
    query('ROLLBACK TO SAVEPOINT S',state=2)
    query('SELECT "B" FROM "PUBLIC"."BIN_ROWS" WHERE "ID"=100',rows=[['\\x616263']],state=2)
    query('COMMIT'); persisted[100]=b'abc'; native()
    query('BEGIN',state=2)
    params('rolled back insert',INSERT,['101',b'rollback','tx'],formats=[0,1,0],state=2)
    query('ROLLBACK'); native()
    # A duplicate key reaches Execute after conversion. The nontransactional trigger proves execution.
    guard = int(query('SELECT GEN_ID("PUBLIC"."BIN_GUARD",0)',rows=None)['rows'][0][0])
    params('engine error after conversion',INSERT,['1',payload(40444),'duplicate'],formats=[0,1,0],error='23505')
    query('SELECT GEN_ID("PUBLIC"."BIN_GUARD",0)',rows=[[str(guard+1)]])
    native()
    results = client.query('INSERT INTO "PUBLIC"."BIN_ROWS" VALUES(102,CAST(\'x\' AS BLOB),\'batch\'); SELECT 1/0',True)
    require([r['sqlstate'] for r in results] == ['', '22012'], 'Implicit batch failure differs')
    native()
    query('BEGIN',state=2)
    params('explicit engine error',INSERT,['1',payload(40444),'duplicate'],formats=[0,1,0],error='23505',state=3)
    params('failed tx refuses execution','bytea',[b'abc'],prepared=True,error='25P02',state=3)
    query('ROLLBACK'); native()
    # selected23 SQL is used verbatim; only the schema and values are synthetic.
    captured = Path(config['capture_dir'])/'profile/selected/23-binary-config-write.sql'
    if captured.is_file():
        require(captured.read_text().strip() == CONFIG_UPDATE, 'selected23 SQL differs')
    values = ['2026-11-11 12:34:56.1234','40444',payload(40444),'config','1']
    prepare('config_write',CONFIG_UPDATE,inputs=(1114,20,17,1043,23))
    params('selected23 UPDATE 1','config_write',values,formats=[0,0,1,0,0],prepared=True,tag='UPDATE 1')
    for filename,part in [('missing','1'),('config','9')]:
        params('selected23 UPDATE 0',CONFIG_UPDATE,values[:3]+[filename,part],formats=[0,0,1,0,0],tag='UPDATE 0')
    output = Harness.isql(observer,'selected23_native','SET LIST ON;\n'
        'SELECT "modified","datasize",OCTET_LENGTH("binarydata") AS "N","filename","partno" FROM "public"."files" ORDER BY "filename","partno";\nROLLBACK;\nQUIT;\n')
    require('2026-11-11 12:34:56.1234' in output and re.findall(r'^N\s+(\S+)\s*$',output,re.M)==['40444','<null>','<null>'] and
            re.findall(r'^filename\s+(\S+)\s*$',output,re.M)==['config','config','other'] and
            re.findall(r'^partno\s+(\S+)\s*$',output,re.M)==['1','2','1'] and
            re.findall(r'^datasize\s+(\S+)\s*$',output,re.M)==['40444','0','0'], 'selected23 native scalar/WHERE results differ')
    blobs, values = mixed_shape()
    sql = 'INSERT INTO "PUBLIC"."BIN_MIXED" VALUES('+','.join('$'+str(i+1) for i in range(92))+')'
    params('92 parameters / 68 blobs',sql,values,formats=[1 if i in blobs else 0 for i in range(92)],tag='INSERT 0 1')
    query('SELECT '+','.join(f'"C{i}"' for i in range(92) if i not in blobs)+' FROM "PUBLIC"."BIN_MIXED"',
               rows=[[str(i) for i in range(92) if i not in blobs]])
    native([(f'SELECT C{i} FROM "PUBLIC"."BIN_MIXED"',values[i]) for i in blobs]+
           [('SELECT "binarydata" FROM "public"."files" WHERE "filename"=\'config\' AND "partno"=1',payload(40444))])
    from pgwire_1c_smoke import Libpq
    disconnected = Libpq(config)
    try:
        require(not disconnected.query('BEGIN')[0]['sqlstate'], 'Disconnect BEGIN failed')
        require(not disconnected.params(INSERT,['103',b'disconnect','tx'],formats=[0,1,0])['sqlstate'], 'Disconnect insert failed')
    finally:
        disconnected.close()
    native(wait_disconnect=True)
    report['scope'] = 'Subtype 0 input <=65533 bytes; selected23 and 68/92 fixture synthetic, not original schema/Bind or 1C opening'
    return report


def blob_input_protocols(h):
    raw = ExtendedClient(h).login()
    checks = []
    def observe(label):
        output = h.isql('input_'+label,'SET ROLE PGWIRE_OBSERVER;\nSET LIST ON;\n'
            'SELECT COUNT(*) AS "N" FROM MON$TRANSACTIONS T JOIN MON$ATTACHMENTS A '
            'ON A.MON$ATTACHMENT_ID=T.MON$ATTACHMENT_ID '
            "WHERE A.MON$REMOTE_VERSION='PgWire' AND A.MON$SYSTEM_FLAG=0;\n"
            'SELECT COUNT(*) AS "N" FROM "PUBLIC"."BIN_ROWS";\nSELECT GEN_ID("PUBLIC"."BIN_GUARD",0) AS "N" FROM "SYSTEM"."RDB$DATABASE";\nROLLBACK;\nQUIT;\n')
        return [int(n) for n in re.findall(r'^N\s+(\d+)\s*$',output,re.M)]
    try:
        before = observe('before')
        require(before[0] == 0, 'Preflight already has a PG application transaction')
        events = raw.exchange(parse('waiting',INSERT)+describe(b'S','waiting')+packet(b'H'),b'1tT',end=b'T')
        require(events[1][1] == struct.pack('!HIII',3,23,17,1043),'Raw bytea ParameterDescription differs')
        raw.exchange(bind('unexecuted','waiting',['700',payload(40444),'early'],formats=[0,1,0])+
                     describe(b'P','unexecuted')+packet(b'H'),b'2T',end=b'T')
        require(observe('bound') == before, 'Bind changed a table/sequence or started an application transaction')
        raw.exchange(close(b'P','unexecuted')+packet(b'S'),b'3Z')
        require(observe('closed') == before, 'Closing an unexecuted portal had an effect')
        checks.append('parse_bind_describe_flush_and_close_without_transaction_or_effects')
        discard = parse('discarded',INSERT)+bind('discarded','discarded',['701',b'bad','skip'])+execute('discarded')
        for wire,error in [(b'\\x0','22023'),(b'\\400','22P02'),(b'\x00','22021'),(b'\xc0\x80','22021')]:
            raw.exchange(bind('bad','waiting',['700',wire,'bad'])+discard+packet(b'S'),b'EZ',error=error)
            raw.exchange(bind('missing','discarded')+packet(b'S'),b'EZ',error='26000')
        unsupported = 'INSERT INTO "PUBLIC"."BIN_ROWS"("ID","B","NOTE") VALUES($1,$2,$3) RETURNING CAST("B" AS BLOB SUB_TYPE TEXT)'
        raw.exchange(parse('bad',unsupported)+discard+packet(b'S'),b'EZ',error='0A000')
        require(observe('invalid') == before, 'Rejected bytea/output or discarded commands caused effects')
        checks.append('malformed_bytea_raw_encoding_and_unsupported_output_discard_before_execution')
        sql = 'SELECT CAST($1 AS BLOB SUB_TYPE BINARY),42 FROM "PUBLIC"."BIN_ROWS" WHERE "ID" BETWEEN 1 AND 3 ORDER BY "ID"'
        a,b = payload(65533),b'\xff'
        raw.exchange(parse('paged',sql)+bind('a','paged',[a],formats=[1],results=[1,0])+
            bind('b','paged',[b'\\377'],results=[0,1])+execute('a',1)+packet(b'H'),b'122Ds',end=b's',rows=[[a,'42']])
        raw.exchange(execute('b',1)+packet(b'H'),b'Ds',end=b's',rows=[['\\xff',struct.pack('!i',42)]])
        raw.exchange(execute('a')+packet(b'H'),b'DDC',end=b'C',rows=[[a,'42'],[a,'42']],tag='SELECT 3')
        raw.exchange(execute('b')+packet(b'S'),b'DDCZ',rows=[['\\xff',struct.pack('!i',42)]]*2,tag='SELECT 3')
        checks.append('independent_short_long_portals_mixed_formats_suspend_resume')
        raw.exchange(bind('once','waiting',['702',payload(40444),'once'],formats=[0,1,0],results=[1])+
                     execute('once')+packet(b'H'),b'2DC',end=b'C',rows=[[payload(40444)]],tag='INSERT 0 1')
        raw.exchange(execute('once')+packet(b'S'),b'CZ',tag='INSERT 0 1')
        after = observe('once')
        require(after == [0,before[1]+1,before[2]+1], 'A completed portal executed DML twice')
        checks.append('returning_portal_executes_once_implicit_commit')
        # Static scalar capacities make the actual native layout hit 1 MiB while Bind fits its frame.
        sql = 'SELECT OCTET_LENGTH(CAST($1 AS BLOB)) FROM "SYSTEM"."RDB$DATABASE" WHERE ' + ' AND '.join(
            f'CAST(${i} AS VARCHAR(32760) CHARACTER SET "NONE") IS NOT NULL' for i in range(2,33))
        raw.exchange(parse('boundary',sql)+bind('fits','boundary',[payload(32888)]+['a']*31,formats=[1]+[0]*31)+
                     execute('fits')+packet(b'S'),b'12DCZ',rows=[['32888']],tag='SELECT 1')
        raw.exchange(bind('over','boundary',[payload(32889)]+['a']*31,formats=[1]+[0]*31)+
                     execute('over')+packet(b'S'),b'EZ',error='0A000')
        checks.append('native_message_exact_1MiB_and_alignment_overflow_before_execute')
        # A 68-BLOB descriptor with short values remains small even with many bound portals.
        blobs,values = mixed_shape()
        sql = 'INSERT INTO "PUBLIC"."BIN_MIXED" VALUES('+','.join('$'+str(i+1) for i in range(92))+')'
        raw.exchange(parse('mixed',sql)+packet(b'S'),b'1Z')
        for i in range(20):
            raw.exchange(bind('mixed'+str(i),'mixed',values,formats=[1 if j in blobs else 0 for j in range(92)])+
                         packet(b'H'),b'2',end=b'2')
        require(observe('mixed_waiting') == after, '68/92 Bind created a transaction')
        raw.exchange(b''.join(close(b'P','mixed'+str(i)) for i in range(20))+packet(b'S'),b'3'*20+b'Z')
        checks.append('twenty_68_of_92_portals_charge_actual_payload')
        raw.exchange(parse('cache','SELECT OCTET_LENGTH(CAST($1 AS BLOB))')+packet(b'S'),b'1Z')
        accepted = 0
        for i in range(140):
            raw.send(bind('large'+str(i),'cache',[payload(65533)],formats=[1])+packet(b'H'))
            events = raw.events(end=b'2')
            if events[0][0] == b'E':
                from pgwire_startup_smoke import error_fields
                require(error_fields(events[0][1])['C'] == '54000', 'Cache overflow SQLSTATE differs')
                raw.exchange(packet(b'S'),b'Z')
                break
            require([kind for kind,_ in events] == [b'2'], 'Large portal Bind failed')
            accepted += 1
        require(100 < accepted < 129, '8 MiB cache did not account actual BLOB payloads: '+str(accepted))
        raw.exchange(bind('after_cache','cache',[b'x'],formats=[1])+execute('after_cache')+packet(b'S'),
                     b'2DCZ',rows=[['1']],tag='SELECT 1')
        checks.append('8MiB_cache_overflow_and_recovery_'+str(accepted)+'_large_portals')
        require(observe('final') == after, 'Bound portals/cache failures left effects or transactions')
    finally:
        raw.close()
    return {'checks':checks, 'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
