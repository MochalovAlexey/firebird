"""PG BEGIN checks for the existing 1c libpq/raw/native harness."""
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

from pgwire_extended_query_smoke import ExtendedClient, parse, bind, execute
from pgwire_startup_smoke import packet
from wire_host_smoke import Harness, require


FORMS = ('BEGIN', 'BEGIN WORK', 'BEGIN TRANSACTION', 'START TRANSACTION')
TRANSACTIONS = ('FROM MON$TRANSACTIONS "T" JOIN MON$ATTACHMENTS "A" ON "A".MON$ATTACHMENT_ID="T".MON$ATTACHMENT_ID WHERE "A".MON$REMOTE_VERSION=\'PgWire\' AND "A".MON$SYSTEM_FLAG=0')


def begin_client(config, client):
    from pgwire_1c_smoke import Libpq
    observer = SimpleNamespace(runtime=Path(config['runtime']), env=os.environ.copy(),
        password=config['password'], native_port=config['native_port'], work=Path(config['work']),
        temp=Path(config['work'])/'tmp', phase='begin', timeout=config['timeout'], redact=client.redact)
    queries, observations = 0, 0

    def native(sql):
        nonlocal observations
        observations += 1
        return Harness.isql(observer, 'observer_'+str(observations),
            'SET ROLE PGWIRE_OBSERVER;\nSET LIST ON;\n'+sql+';\nROLLBACK;\nQUIT;\n')

    def seen(identifier, value):
        output = native(f'SELECT "V" AS OBSERVED FROM "PUBLIC"."BEGIN_ROWS" WHERE "ID"={identifier}')
        require(re.findall(r'^OBSERVED\s+(-?\d+)\s*$', output, re.M)==([] if value is None else [str(value)]),
                'native visibility differs for '+str(identifier))

    def check(sql, tags=(), state=0, error=''):
        nonlocal queries
        results = client.query(sql, True)
        queries += 1
        require([r['tag'] for r in results]==list(tags)+([''] if error else []) and
                [r['sqlstate'] for r in results]==['']*len(tags)+([error] if error else []) and
                client.pq.PQtransactionStatus(client.connection)==state,
                'BEGIN query differs: '+repr(sql)+' '+json.dumps(results))
        return results

    def mode(value):
        require(client.pq.PQparameterStatus(client.connection,b'standard_conforming_strings')==value.encode(),
                'BEGIN changed or lost string history')

    captured = b'BEGIN TRANSACTION'
    if config['capture_dir'] != 'None':
        captured = (Path(config['capture_dir'])/'profile/selected/09-begin.sql').read_bytes()
    profiles = []
    for sql in (*FORMS, 'begin/* comment */work', 'start-- comment\ntransaction', captured):
        check(sql, ['BEGIN'], state=2)
        output = native('SELECT "T".MON$TRANSACTION_ID AS TXID, "T".MON$ISOLATION_MODE AS ISO, "T".MON$READ_ONLY AS RO, "T".MON$LOCK_TIMEOUT AS WAIT_TIME, "T".MON$AUTO_COMMIT AS AC, "T".MON$AUTO_UNDO AS AU '+TRANSACTIONS)
        ids = re.findall(r'^TXID\s+(\d+)\s*$',output,re.M)
        require(len(ids)==1, 'BEGIN did not create exactly one transaction')
        for name,value in (('ISO','4'),('RO','0'),('WAIT_TIME','-1'),('AC','0'),('AU','1')):
            require(re.findall(r'^'+name+r'\s+(-?\d+)\s*$',output,re.M)==[value], 'BEGIN TPB differs: '+output)
        profiles.append(ids[0])
        check('ROLLBACK',['ROLLBACK'])

    for control,number in (('ROLLBACK',601),('COMMIT',602)):
        check('BEGIN',['BEGIN'],state=2)
        check(f'INSERT INTO "PUBLIC"."BEGIN_ROWS" VALUES({number},1)',['INSERT 0 1'],state=2)
        seen(number,None)
        check(f'UPDATE "PUBLIC"."BEGIN_ROWS" SET "V"=2 WHERE "ID"={number}',['UPDATE 1'],state=2)
        check(f'INSERT INTO "PUBLIC"."BEGIN_ROWS" VALUES({number+10},9); DELETE FROM "PUBLIC"."BEGIN_ROWS" WHERE "ID"={number+10}',
              ['INSERT 0 1','DELETE 1'],state=2)
        seen(number,None)
        check(control,[control]); seen(number,2 if control=='COMMIT' else None); seen(number+10,None)
    check('BEGIN; DELETE FROM "PUBLIC"."BEGIN_ROWS" WHERE "ID"=602',['BEGIN','DELETE 1'],state=2)
    seen(602,2); check('ROLLBACK',['ROLLBACK']); seen(602,2)

    peer = Libpq(config)
    try:
        for start,expected in (('BEGIN','20'),('SET TRANSACTION','10')):
            check('UPDATE "PUBLIC"."BEGIN_ROWS" SET "V"=10 WHERE "ID"=602',['UPDATE 1'])
            check(start,['BEGIN'],state=2)
            require(check('SELECT "V" FROM "PUBLIC"."BEGIN_ROWS" WHERE "ID"=602',['SELECT 1'],state=2)[0]['rows']==[['10']], 'old value differs')
            changed=peer.query('UPDATE "PUBLIC"."BEGIN_ROWS" SET "V"=20 WHERE "ID"=602')
            require(changed[0]['tag']=='UPDATE 1' and not changed[0]['sqlstate'], 'peer update failed')
            require(check('SELECT "V" FROM "PUBLIC"."BEGIN_ROWS" WHERE "ID"=602',['SELECT 1'],state=2)[0]['rows']==[[expected]],
                    'READ CONSISTENCY / native SNAPSHOT visibility differs')
            output=native('SELECT MON$ISOLATION_MODE AS ISO '+TRANSACTIONS)
            require(re.findall(r'^ISO\s+(\d+)\s*$',output,re.M)==['4' if start=='BEGIN' else '1'], 'isolation changed')
            check('ROLLBACK',['ROLLBACK'])
    finally:
        peer.close()

    promoted=[]
    for control,number in (('ROLLBACK',620),('COMMIT',621)):
        results=check(f'INSERT INTO "PUBLIC"."BEGIN_ROWS" VALUES({number},1); SELECT CURRENT_TRANSACTION; '
                      'BEGIN; SELECT CURRENT_TRANSACTION', ['INSERT 0 1','SELECT 1','BEGIN','SELECT 1'],state=2)
        require(results[1]['rows']==results[3]['rows'],'BEGIN replaced implicit transaction')
        txid=results[1]['rows'][0][0]
        require(re.findall(r'^TXID\s+(\d+)\s*$',native('SELECT MON$TRANSACTION_ID AS TXID '+TRANSACTIONS),re.M)==[txid],
                'promotion created a second transaction')
        seen(number,None); check(control,[control]); seen(number,1 if control=='COMMIT' else None)
        promoted.append(txid)

    check('BEGIN; INSERT INTO "PUBLIC"."BEGIN_ROWS" VALUES(630,1); SAVEPOINT kept; INSERT INTO "PUBLIC"."BEGIN_ROWS" VALUES(631,2)',
          ['BEGIN','INSERT 0 1','SAVEPOINT','INSERT 0 1'],state=2)
    before=check('SELECT CURRENT_TRANSACTION',['SELECT 1'],state=2)[0]['rows']
    for sql in FORMS: check(sql,['BEGIN'],state=2)
    require(check('SELECT CURRENT_TRANSACTION',['SELECT 1'],state=2)[0]['rows']==before, 'repeat BEGIN changed transaction')
    check('ROLLBACK TO kept',['ROLLBACK'],state=2)
    require(check('SELECT "ID" FROM "PUBLIC"."BEGIN_ROWS" WHERE "ID" IN (630,631)',['SELECT 1'],state=2)[0]['rows']==[['630']], 'savepoint lost')
    check('ROLLBACK',['ROLLBACK']); seen(630,None); seen(631,None)
    check('BEGIN',['BEGIN'],state=2)
    check('SET TRANSACTION',state=3,error='25001')
    for sql in FORMS: check(sql,state=3,error='25P02')
    check('ROLLBACK',['ROLLBACK'])

    check('BEGIN; INSERT INTO "PUBLIC"."BEGIN_ROWS" VALUES(640,1); COMMIT; INSERT INTO "PUBLIC"."BEGIN_ROWS" VALUES(641,2); SELECT 1/0; INSERT INTO "PUBLIC"."BEGIN_ROWS" VALUES(642,3)', ['BEGIN','INSERT 0 1','COMMIT','INSERT 0 1'],error='22012')
    seen(640,1); seen(641,None); seen(642,None)
    check('BEGIN; SELECT 1/0; ROLLBACK',['BEGIN'],state=3,error='22012')
    check('ROLLBACK',['ROLLBACK'])

    for dml in ('', 'INSERT INTO "PUBLIC"."BEGIN_ROWS" VALUES(650,1); '):
        for failing in (False,True):
            check('SET standard_conforming_strings=off; '+dml+'BEGIN',
                  ['SET']+(['INSERT 0 1'] if dml else [])+['BEGIN'],state=2)
            mode('off'); check('BEGIN',['BEGIN'],state=2)
            if failing:
                check('SELECT 1/0',state=3,error='22012'); mode('on')
            check('ROLLBACK',['ROLLBACK']); mode('on'); seen(650,None)
    check('BEGIN; SET standard_conforming_strings=off; SAVEPOINT kept; '
          'SET standard_conforming_strings=on; BEGIN', ['BEGIN','SET','SAVEPOINT','SET','BEGIN'],state=2)
    check('SELECT 1/0',state=3,error='22012'); mode('off')
    check('ROLLBACK TO kept',['ROLLBACK'],state=2); mode('off')
    check('ROLLBACK',['ROLLBACK']); mode('on')

    for sql in ('BEGIN ISOLATION LEVEL READ COMMITTED','BEGIN READ ONLY','BEGIN READ WRITE',
                'BEGIN DEFERRABLE','START TRANSACTION READ ONLY','START WORK','BEGIN WORK TRANSACTION'):
        check(sql,error='42601')
        require(re.findall(r'^N\s+(\d+)\s*$',native('SELECT COUNT(*) AS "N" '+TRANSACTIONS),re.M)==['0'],
                'invalid options opened a transaction')
    check('BEGIN',['BEGIN'],state=2)
    check('BEGIN READ ONLY',state=3,error='42601')
    check('ROLLBACK',['ROLLBACK'])

    peer=Libpq(config)
    try:
        result=peer.query('BEGIN; INSERT INTO "PUBLIC"."BEGIN_ROWS" VALUES(660,1)')
        require([r['tag'] for r in result]==['BEGIN','INSERT 0 1'],'disconnect fixture failed')
        seen(660,None)
    finally:
        peer.close()
    # A fresh native transaction sees no uncommitted row after disconnect.
    seen(660,None)
    return {'queries_checked':queries,'native_observations':observations,'profile_transaction_ids':profiles,
            'promoted_transaction_ids':promoted,'capture_sha256':hashlib.sha256(captured).hexdigest(),
            'original_capture':config['capture_dir']!='None','read_consistency_and_native_snapshot':True,
            'same_handle_and_savepoints':True,'string_history':True,'disconnect_rollback':True}


def begin_protocols(h):
    def count(expected):
        output=h.isql('begin_transaction_count','SET LIST ON;\nSELECT COUNT(*) AS "N" '+TRANSACTIONS+';\nROLLBACK;\nQUIT;\n')
        require(re.findall(r'^N\s+(\d+)\s*$',output,re.M)==[str(expected)],'wrong transaction count')

    def seen(identifier,expected):
        output=h.isql('begin_seen_'+str(identifier),f'SET LIST ON;\nSELECT COUNT(*) AS "N" FROM "PUBLIC"."BEGIN_ROWS" WHERE "ID"={identifier};\nROLLBACK;\nQUIT;\n')
        require(re.findall(r'^N\s+(\d+)\s*$',output,re.M)==[str(expected)],'extended visibility differs')

    raw=ExtendedClient(h).login()
    try:
        raw.exchange(parse('begin','BEGIN')+packet(b'S'),b'1Z'); count(0)
        raw.exchange(bind('start','begin')+packet(b'H'),b'2',end=b'2'); count(0)
        raw.exchange(execute('start')+packet(b'S'),b'CZ',tag='BEGIN',state='T'); count(1)
        for _ in range(2):
            raw.exchange(packet(b'Q',b'ROLLBACK\0'),b'CZ',tag='ROLLBACK')
            raw.exchange(bind('start','begin')+execute('start')+packet(b'S'),b'2CZ',tag='BEGIN',state='T')
        raw.exchange(packet(b'Q',b'ROLLBACK\0'),b'CZ',tag='ROLLBACK'); count(0)

        for number,control in ((670,'ROLLBACK'),(671,'COMMIT')):
            raw.exchange(parse('',f'INSERT INTO "PUBLIC"."BEGIN_ROWS" VALUES({number},1)')+bind()+execute()+packet(b'H'),
                         b'12C',end=b'C',tag='INSERT 0 1')
            raw.exchange(parse('id'+str(number),'SELECT CURRENT_TRANSACTION')+bind('id','id'+str(number))+
                         execute('id')+packet(b'H'),b'12DC',end=b'C',tag='SELECT 1')
            # Native observer records the handle independently of wire metadata.
            before=h.isql('begin_id_before','SET LIST ON;\nSELECT MON$TRANSACTION_ID AS "ID" '+TRANSACTIONS+';\nROLLBACK;\nQUIT;\n')
            raw.exchange(bind('promote','begin')+execute('promote')+packet(b'S'),b'2CZ',tag='BEGIN',state='T')
            after=h.isql('begin_id_after','SET LIST ON;\nSELECT MON$TRANSACTION_ID AS "ID" '+TRANSACTIONS+';\nROLLBACK;\nQUIT;\n')
            require(re.findall(r'^ID\s+(\d+)\s*$',before,re.M)==re.findall(r'^ID\s+(\d+)\s*$',after,re.M), 'Extended promotion replaced handle')
            count(1); seen(number,0)
            raw.exchange(packet(b'Q',control.encode()+b'\0'),b'CZ',tag=control); count(0)
            seen(number,int(control=='COMMIT'))

        raw.exchange(parse('rows','SELECT "ID" FROM "PUBLIC"."BEGIN_ROWS" WHERE "ID" IN (602,621,640) ORDER BY "ID"')+
                     bind('rows','rows')+execute('rows',1)+packet(b'H'),b'12Ds',end=b's',rows=[['602']])
        count(1)
        raw.exchange(bind('promote','begin')+execute('promote')+packet(b'S'),b'2CZ',tag='BEGIN',state='T')
        raw.exchange(execute('rows')+packet(b'S'),b'DDCZ',rows=[['621'],['640']],tag='SELECT 3',state='T')
        raw.exchange(packet(b'Q',b'ROLLBACK\0'),b'CZ',tag='ROLLBACK'); count(0)

        raw.exchange(parse('bad','BEGIN READ ONLY')+parse('discarded','BEGIN')+packet(b'S'),b'EZ',error='42601')
        raw.exchange(bind('x','discarded')+packet(b'S'),b'EZ',error='26000')
        raw.exchange(bind('ok','begin')+execute('ok')+packet(b'S'),b'2CZ',tag='BEGIN',state='T')
        raw.exchange(parse('fail','SELECT 1/0')+bind('fail','fail')+execute('fail')+execute('ok')+packet(b'S'),
                     b'12EZ',error='22012',state='E')
        raw.exchange(bind('again','begin')+packet(b'S'),b'EZ',error='25P02',state='E')
        raw.exchange(packet(b'Q',b'ROLLBACK\0'),b'CZ',tag='ROLLBACK'); count(0)
    finally:
        raw.close()
    h.no_attachments()
    raw=ExtendedClient(h).login()
    try:
        raw.exchange(packet(b'Q',b'BEGIN\0'),b'CZ',tag='BEGIN',state='T')
        raw.exchange(packet(b'Q',b'INSERT INTO "PUBLIC"."BEGIN_ROWS" VALUES(681,1)\x00'),b'CZ',tag='INSERT 0 1',state='T')
        seen(681,0)
    finally:
        raw.close(terminate=False)
    h.no_attachments(); count(0); seen(681,0)

    env=h.env.copy(); env.update(ISC_USER='WIRE_HOST_TEST',ISC_PASSWORD=h.password)
    for dialect in (1,2,3):
        # A recovery SELECT immediately after every rejected form catches AUTOTERM swallowing.
        script='SET LIST ON;\n'+''.join(sql+';\nSELECT 42 AS RECOVERY FROM RDB$DATABASE;\n' for sql in FORMS)
        script+=('EXECUTE BLOCK RETURNS(V INTEGER) AS BEGIN V=42; SUSPEND; END;\n'
                 f'CREATE SEQUENCE BEGIN_SEQ_{dialect} START WITH 7;\nCOMMIT;\n'
                 f'SELECT NEXT VALUE FOR BEGIN_SEQ_{dialect} AS SEQ_VALUE FROM RDB$DATABASE;\nCOMMIT;\n'
                 'SET TRANSACTION;\nSELECT MON$ISOLATION_MODE AS ISO FROM MON$TRANSACTIONS '
                 'WHERE MON$TRANSACTION_ID=CURRENT_TRANSACTION;\nROLLBACK;\nQUIT;\n')
        run=subprocess.run([str(h.runtime/'bin/isql'),'-quiet','-autoterm','-sql_dialect',str(dialect),
                            f'inet://127.0.0.1:{h.native_port}/wire_host'],input=script,text=True,
                           capture_output=True,env=env,cwd=h.temp,timeout=h.timeout)
        output=run.stdout+run.stderr
        (h.work/f'begin-native-{dialect}.log').write_text(h.redact(output))
        require(run.returncode!=0 and re.findall(r'SQLSTATE\s*=\s*(\w+)',output)==['42000']*4 and
                re.findall(r'^RECOVERY\s+(\d+)\s*$',output,re.M)==['42']*4 and
                re.findall(r'^V\s+(\d+)\s*$',output,re.M)==['42'] and
                re.findall(r'^SEQ_VALUE\s+(\d+)\s*$',output,re.M)==['7'] and
                re.findall(r'^ISO\s+(\d+)\s*$',output,re.M)==['1'], 'native BEGIN/AUTOTERM regression')

    psql=shutil.which('psql')
    require(psql is not None,'psql is unavailable')
    env.update(PGPASSWORD=h.password,PGCLIENTENCODING='UTF8')
    env.pop('PGOPTIONS',None)
    run=subprocess.run([psql,'-X','-h','127.0.0.1','-p',str(h.pg_port),'-U','WIRE_HOST_TEST','-d','wire_host',
                        '-v','ON_ERROR_STOP=1','-v','AUTOCOMMIT=off','-At'],
        input='SELECT 42;\nINSERT INTO "PUBLIC"."BEGIN_ROWS" VALUES(680,1);\nROLLBACK;\nSELECT COUNT(*) FROM "PUBLIC"."BEGIN_ROWS" WHERE "ID"=680;\nROLLBACK;\n',
        capture_output=True,text=True,env=env,cwd=h.temp,timeout=h.timeout)
    (h.work/'begin-psql.log').write_text(h.redact(run.stdout+run.stderr))
    require(run.returncode==0 and run.stdout.splitlines()==['42','INSERT 0 1','ROLLBACK','0','ROLLBACK'],
            'psql AUTOCOMMIT off failed: '+run.stdout+run.stderr)
    seen(680,0); h.no_attachments()
    return {'named_prepare_bind_idle':True,'reuse':True,'promotion_commit_rollback':True,
            'suspended_portal_survived':True,'discard_until_sync':True,'native_dialects':[1,2,3],
            'native_psql_and_start_with':True,'psql_autocommit_off':True,'eof_rollback':True}
