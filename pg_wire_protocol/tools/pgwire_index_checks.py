#!/usr/bin/env python3
"""Persistent index catalogs and shared PG relation-name lookup, isolated runtime."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from pgwire_1c_smoke import Libpq, OneCHarness
from pgwire_namespace_checks import config_for, native, numbers
from pgwire_startup_smoke import Client
from wire_host_smoke import require, reserve_ports

PROFILE = Path(__file__).resolve().parents[1] / '1c'
sys.path.insert(0, str(PROFILE))
from install_compat import SCRIPTS
INSTALLER='sql/install_index_catalog.sql'
OPENING_QUERY='''SELECT 1::INT8
FROM pg_catalog.pg_class AS CT
INNER JOIN pg_catalog.pg_index AS I ON CT.oid = I.indrelid
INNER JOIN pg_catalog.pg_class AS CI ON CI.oid = I.indexrelid
WHERE pg_catalog.pg_table_is_visible(CT.oid) AND CT.relkind = 'r'
AND CT.relnamespace IN ('public'::regnamespace, pg_catalog.pg_my_temp_schema())AND CT.relname='v8users' AND pg_catalog.pg_table_is_visible(CI.oid)AND CI.relkind = 'i'
AND CI.relnamespace IN ('public'::regnamespace, pg_catalog.pg_my_temp_schema())AND CI.relname='byemail_v8users' LIMIT 1'''


def quoted(value):return "'"+value.replace("'","''")+"'"


def run(h):
    h.prepare()
    h.isql('admin',f"CONNECT '{h.database}';\nCREATE OR ALTER USER SYSDBA PASSWORD '{h.password}';\nCOMMIT;\nQUIT;",embedded=True)
    cfg=config_for(h)
    native(cfg,'prerequisites','\n'.join((PROFILE/s).read_text() for s in SCRIPTS[:SCRIPTS.index(INSTALLER)]),admin=True)
    script=(PROFILE/INSTALLER).read_text()
    native(cfg,'atomic_failure',script.replace('\nCOMMIT;','\nSELECT 1/0 FROM SYSTEM.RDB$DATABASE;\nCOMMIT;'),admin=True,error=True)
    absent=native(cfg,'atomic_absent',"SELECT COUNT(*) AS N FROM SYSTEM.RDB$RELATIONS WHERE RDB$SCHEMA_NAME='pg_catalog' AND RDB$RELATION_NAME='pg_index';",admin=True)
    require(numbers(absent['output'])==[0],'Failed index install retained pg_index')
    native(cfg,'index_install',script,admin=True)
    native(cfg,'index_repeat',script,admin=True,error=True)
    native(cfg,'fixture_privileges','''GRANT CREATE SCHEMA TO USER WIRE_HOST_TEST;
GRANT CREATE TABLE ON SCHEMA "public" TO USER WIRE_HOST_TEST;
CREATE SCHEMA "idx_hidden";
CREATE TABLE "idx_hidden"."hidden_base"(ID INTEGER);
CREATE INDEX "idx_hidden"."shared" ON "idx_hidden"."hidden_base"(ID);
COMMIT;''',admin=True)
    native(cfg,'readonly_index_view','DELETE FROM "pg_catalog"."pg_index";',admin=True,error=True)
    n,p=reserve_ports(2);h.start('indexes',n,n,[{'alias':'PgWire','port':p}])
    ready=Client(h).login();ready.close()
    cfg=dict(config_for(h));first,second=Libpq({'port':p,'password':h.password}),Libpq({'port':p,'password':h.password})
    records=[];h.summary['index_cases']=records
    def q(sql,rows=None,error=None,c=None):
        if c is None:c=first
        r=c.query(sql,True)[-1]
        require((bool(r['sqlstate']) if error is True else r['sqlstate']==(error or '')) and
                (rows is None or r['rows']==rows),sql+': '+repr(r))
        records.append({'sql':sql,'result':r});return r
    def oid(schema,name,kind):
        rows=q('SELECT c.oid FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace '
               'WHERE n.nspname='+quoted(schema)+' AND c.relname='+quoted(name)+' AND c.relkind='+quoted(kind))['rows']
        require(len(rows)==1,'Expected exactly one catalog identity');return int(rows[0][0])
    def visible(identity,expected,c=None):q('SELECT pg_catalog.pg_table_is_visible('+str(identity)+')',[[expected]],c=c)
    try:
        q(OPENING_QUERY,[])
        q('CREATE TABLE v8users(id integer,email varchar(50))')
        q('CREATE INDEX byemail_v8users ON v8users(email)')
        q(OPENING_QUERY,[['1']]);q(OPENING_QUERY,[['1']],c=second)
        index=oid('public','byemail_v8users','i');table=oid('public','v8users','r')
        require(index>4294967296 and table<4294967296,'Identity domains overlap')
        q(f'SELECT indexrelid,indrelid FROM pg_catalog.pg_index WHERE indexrelid={index}',[[str(index),str(table)]])
        q("SELECT RDB$PG_INDEX_ID('byemail_v8users')",[[str(index-4294967296)]])
        q("SELECT RDB$PG_RELATION_ID('v8users')",[[str(table-1)]])
        q("SELECT tablename FROM pg_catalog.pg_tables WHERE tablename='byemail_v8users'",[])
        q(f'SELECT attname FROM pg_catalog.pg_attribute WHERE attrelid={index}',[])
        q('ALTER INDEX byemail_v8users INACTIVE');q(OPENING_QUERY,[['1']])
        require(oid('public','byemail_v8users','i')==index,'Inactive index changed identity')
        q('ALTER INDEX byemail_v8users ACTIVE');q(OPENING_QUERY,[['1']])
        q('ALTER INDEX byemail_v8users RENAME TO other_index',error='42601');q(OPENING_QUERY,[['1']])
        q('BEGIN');q('DROP INDEX byemail_v8users');q(OPENING_QUERY,[]);q('ROLLBACK');q(OPENING_QUERY,[['1']])
        q('DROP INDEX byemail_v8users');q(OPENING_QUERY,[]);q('CREATE INDEX byemail_v8users ON v8users(email)');q(OPENING_QUERY,[['1']])
        index=oid('public','byemail_v8users','i')
        for schema in ('idx_a','idx_b'):
            q('CREATE SCHEMA '+schema);q('CREATE TABLE '+schema+'.base(id integer)')
            q('CREATE INDEX '+schema+'.shared ON '+schema+'.base(id)')
        a,b=oid('idx_a','shared','i'),oid('idx_b','shared','i')
        q('SET SEARCH_PATH TO idx_a,idx_b,public,pg_catalog,"SYSTEM"');visible(a,'t');visible(b,'f')
        q('SET SEARCH_PATH TO idx_b,idx_a,public,pg_catalog,"SYSTEM"');visible(a,'f');visible(b,'t')
        q('SET SEARCH_PATH TO idx_hidden,idx_a,idx_b,public,pg_catalog,"SYSTEM"')
        hidden=oid('idx_hidden','shared','i');visible(hidden,'f');visible(a,'t')
        native(cfg,'grant_hidden','GRANT USAGE ON SCHEMA "idx_hidden" TO USER WIRE_HOST_TEST;\nCOMMIT;',admin=True)
        # Native attachment security caches are refreshed by a new attachment.
        first.close();first=Libpq({'port':p,'password':h.password})
        q('SET SEARCH_PATH TO idx_hidden,idx_a,idx_b,public,pg_catalog,"SYSTEM"')
        visible(hidden,'t');visible(a,'f')
        native(cfg,'revoke_hidden','REVOKE USAGE ON SCHEMA "idx_hidden" FROM USER WIRE_HOST_TEST;\nCOMMIT;',admin=True)
        first.close();first=Libpq({'port':p,'password':h.password})
        q('SET SEARCH_PATH TO idx_hidden,idx_a,idx_b,public,pg_catalog,"SYSTEM"')
        visible(hidden,'f');visible(a,'t')
        q('CREATE INDEX idx_a.index_first ON idx_a.base(id)');q('CREATE TABLE idx_b.index_first(id integer)')
        q('INSERT INTO idx_b.index_first VALUES(77)')
        q('CREATE TABLE idx_a.table_first(id integer)');q('CREATE INDEX idx_b.table_first ON idx_b.base(id)')
        q('SET SEARCH_PATH TO idx_a,idx_b,public,pg_catalog,"SYSTEM"')
        visible(oid('idx_a','index_first','i'),'t');visible(oid('idx_b','index_first','r'),'f')
        visible(oid('idx_a','table_first','r'),'t');visible(oid('idx_b','table_first','i'),'f')
        q('SELECT * FROM index_first',error=True);q('SELECT * FROM idx_b.index_first',[['77']])
        q('ALTER INDEX table_first INACTIVE',error=True)
        native(cfg,'native_lookup_control','SET SEARCH_PATH TO "idx_a","idx_b",SYSTEM;\nSELECT * FROM "index_first";',admin=False)
        q('CREATE INDEX idx_a."NATIVE_INDEX_FIRST" ON idx_a.base(id)')
        q('CREATE TABLE idx_b."NATIVE_INDEX_FIRST"("ID" integer)')
        q('INSERT INTO idx_b."NATIVE_INDEX_FIRST" VALUES(77)')
        q('CREATE TEMP TABLE temp_base(id integer)');q('CREATE INDEX shared ON temp_base(id)')
        visible(a,'f');visible(b,'f')
        q('SET SEARCH_PATH TO idx_a,idx_b,public,pg_catalog,"SYSTEM"',c=second);visible(a,'t',second)
        q("SELECT COUNT(*) FROM pg_catalog.pg_class WHERE relkind='i' AND relname='shared'",[['3']])
        q('SET SEARCH_PATH TO idx_a,pg_temp,idx_b,public,pg_catalog,"SYSTEM"');visible(a,'t');visible(b,'f')
        q('DROP INDEX pg_temp.shared');q('SET SEARCH_PATH TO idx_a,idx_b,public,pg_catalog,"SYSTEM"');visible(a,'t')
        q('CREATE TEMP TABLE shared(id integer)');visible(a,'f');q('SELECT * FROM shared',[])
        q('DROP TABLE shared');visible(a,'t')
        q('CREATE INDEX idx_a."Odd.Quote""Ix" ON idx_a.base(id)')
        exact=oid('idx_a','Odd.Quote"Ix','i');visible(exact,'t')
        q('SELECT RDB$PG_INDEX_ID('+quoted('Odd.Quote"Ix')+')',[[str(exact-4294967296)]])
        q("SELECT RDB$PG_INDEX_ID(NULL),RDB$PG_INDEX_ID('missing')",[[None,None]])
        for bad in ('','space ','x'*64):q('SELECT RDB$PG_INDEX_ID('+quoted(bad)+')',error='22023')
        for fmt in (0,1):
            r=first.params('SELECT pg_catalog.pg_table_is_visible($1)',[str(a).encode()],[20],result_format=fmt)
            require(not r['sqlstate'] and r['rows']==[['01' if fmt else 't']],'BIGINT index identity Bind differs')
        q('CREATE TABLE idx_a.collision(id integer)');q('CREATE INDEX idx_a.collision ON idx_a.base(id)')
        collision=oid('idx_a','collision','r');visible_sql='SELECT pg_catalog.pg_table_is_visible('+str(collision)+')'
        collision_result=q(visible_sql,error=True)
        require(collision_result['sqlstate'] in ('0A000','39000'),'Unexpected collision SQLSTATE')
        q("SELECT RDB$PG_INDEX_ID('collision')",error='0A000')
        q('DROP INDEX idx_a.collision');q('DROP TABLE idx_a.collision')
        q('SELECT 42',[['42']]);q('SET SEARCH_PATH TO public,pg_catalog,"SYSTEM"');q(OPENING_QUERY,[['1']])
    finally:first.close();second.close()
    h.no_attachments();h.native_probe('native_after');h.stop()
    api=h.work/'index_api'
    compiled=subprocess.run(['c++','-std=c++17','-I',str(h.runtime/'include'),
        str(Path(__file__).with_name('pgwire_index_api.cpp')),'-L',str(h.runtime/'lib'),'-lfbclient','-o',str(api)],
        capture_output=True,text=True,timeout=h.timeout)
    (h.work/'native-api-build.log').write_text(compiled.stdout+compiled.stderr)
    require(compiled.returncode==0,'Native dialect probe compilation failed')
    checked=subprocess.run([str(api),str(h.database)],env=h.env,capture_output=True,text=True,timeout=h.timeout)
    (h.work/'native-api.log').write_text(checked.stdout+checked.stderr)
    require(checked.returncode==0,'Native dialect controls failed')
    h.summary.update(persistent_indexes=True,shared_namespace=True,temporary_shadow=True,native_api=json.loads(checked.stdout))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--firebird-dir',type=Path,required=True);p.add_argument('--work-dir',type=Path,required=True)
    args=p.parse_args();work=args.work_dir.resolve();work.mkdir(parents=True,exist_ok=False);h=OneCHarness(args.firebird_dir.resolve(),work,120)
    try:run(h);h.summary['passed']=True
    except Exception as error:h.summary['error']=h.redact(str(error))
    finally:
        try:h.close()
        except Exception as error:h.summary.update(passed=False,shutdown_error=str(error))
        (work/'summary.json').write_text(json.dumps(h.summary,indent=2)+'\n');print(json.dumps({'passed':h.summary['passed'],'summary':str(work/'summary.json')}))
    return 0 if h.summary['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
