"""APP-14: installed visibility, native SELECT/SCL oracles and runtime upgrade."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from pgwire_namespace_checks import (INSTALLER, CATALOG_INSTALLER, native, config_for,
    mapping, allocator, catalog_snapshot, identifier, numbers)
from pgwire_class_checks import CLASS_INSTALLER
from pgwire_names_checks import NAMES_INSTALLER, PATH
from wire_host_smoke import require

VISIBILITY_INSTALLER = INSTALLER.with_name('install_relation_visibility.sql')
FUNCTION = 'pg_catalog.pg_table_is_visible'
A_PATH = '"va", "vb", "missing", "public", "pg_catalog", "SYSTEM"'
B_PATH = '"vb", "va", "public", "pg_catalog", "SYSTEM"'


def prerequisites():
    return '\n'.join(p.read_text() for p in (INSTALLER, INSTALLER.parent.parent/'compat.sql',
        CATALOG_INSTALLER, CLASS_INSTALLER, NAMES_INSTALLER))


def visibility_fixture(h, previous):
    config = config_for(h)
    native(config,'visibility_admin',f"CREATE OR ALTER USER SYSDBA PASSWORD '{h.password}';\n"
        'GRANT CREATE SCHEMA TO USER WIRE_HOST_TEST;\nCOMMIT;\n',admin=True)
    native(config,'visibility_prerequisites',prerequisites()+
        'GRANT CREATE TABLE ON SCHEMA "public" TO USER WIRE_HOST_TEST;\nCOMMIT;\n',admin=True)
    installer = VISIBILITY_INSTALLER.read_text()
    before = mapping(config,'visibility_before')
    counter = allocator(config,'visibility_counter')
    snapshot = catalog_snapshot(config,'visibility_catalog_before',True)
    count_sql = "SELECT COUNT(*) AS N FROM SYSTEM.RDB$FUNCTIONS WHERE RDB$SCHEMA_NAME='pg_catalog' " \
        "AND RDB$FUNCTION_NAME='pg_table_is_visible';\nROLLBACK;\nQUIT;\n"
    failure = installer.replace('\nCOMMIT;', '\nSELECT 1/0 FROM SYSTEM.RDB$DATABASE;\nCOMMIT;',1)
    failed = native(config,'visibility_install_rollback',failure,admin=True,error=True)
    require(failed['sqlstates']==['22012'] and numbers(native(config,'visibility_rollback_objects',
        count_sql,admin=True)['output'])==[0], 'Failed install left function behind')
    native(config,'visibility_install',installer,admin=True)
    repeated = native(config,'visibility_repeat',installer,admin=True,error=True)
    require('already exists' in repeated['output'].lower() and
        mapping(config,'visibility_after')==before and allocator(config,'visibility_counter_after')==counter and
        catalog_snapshot(config,'visibility_catalog_after',True)==snapshot, 'Installer changed existing catalogs')
    props = native(config,'visibility_function_properties',
        "SELECT RDB$SQL_SECURITY AS N,RDB$DETERMINISTIC_FLAG AS D FROM SYSTEM.RDB$FUNCTIONS "
        "WHERE RDB$SCHEMA_NAME='pg_catalog' AND RDB$FUNCTION_NAME='pg_table_is_visible';\n"
        "SELECT COUNT(*) AS N FROM SYSTEM.RDB$USER_PRIVILEGES WHERE RDB$RELATION_SCHEMA_NAME='pg_catalog' "
        "AND RDB$RELATION_NAME='pg_table_is_visible' AND RDB$USER='PUBLIC' AND RDB$PRIVILEGE='X';\n"
        'ROLLBACK;\nQUIT;\n',admin=True)['output']
    require('<false>' in props.lower() and numbers(props)==[1] and numbers(props,'D')==[0], 'Wrapper security or PUBLIC EXECUTE differs')
    collision = h.database.parent/'visibility_collision.fdb'
    h.isql('visibility_collision_create',f"CREATE DATABASE '{collision}' DEFAULT CHARACTER SET UTF8;\nCOMMIT;\nQUIT;\n",embedded=True)
    cfg = dict(config,database=str(collision))
    native(cfg,'visibility_collision_setup',prerequisites()+
        'SET TERM ^;\nCREATE FUNCTION "pg_catalog"."pg_table_is_visible"(X BIGINT) RETURNS INTEGER AS BEGIN RETURN 17; END^\nSET TERM ;^\nCOMMIT;\n',admin=True)
    rejected = native(cfg,'visibility_collision',installer,admin=True,error=True)
    require('already exists' in rejected['output'].lower() and numbers(native(cfg,'visibility_collision_preserved',
        'SELECT "pg_catalog"."pg_table_is_visible"(1) AS N FROM SYSTEM.RDB$DATABASE;\nROLLBACK;\nQUIT;\n',admin=True)['output'])==[17],
        'Foreign function was changed')
    native(config,'visibility_owner_fixture',
        'CREATE SCHEMA "va";\nCREATE SCHEMA "vb";\nCREATE SCHEMA "outside";\n'
        'CREATE TABLE "va"."same"("v" INTEGER);\nINSERT INTO "va"."same" VALUES(11);\n'
        'CREATE TABLE "vb"."same"("v" INTEGER);\nINSERT INTO "vb"."same" VALUES(22);\n'
        'CREATE VIEW "va"."visible_view" AS SELECT "v" FROM "va"."same";\n'
        'CREATE TABLE "outside"."away"("v" INTEGER);\n'
        'COMMIT;\n')
    native(config,'visibility_acl_fixture',
        'CREATE SCHEMA "vpublic";\nGRANT USAGE ON SCHEMA "vpublic" TO PUBLIC;\n'
        'CREATE TABLE "vpublic"."secret"("v" INTEGER);\nINSERT INTO "vpublic"."secret" VALUES(77);\n'
        'CREATE SCHEMA "vhidden";\nCREATE TABLE "vhidden"."hidden"("v" INTEGER);\n'
        'CREATE SCHEMA "vrole";\nCREATE TABLE "vrole"."role_table"("v" INTEGER);\n'
        'CREATE ROLE VISIBILITY_ROLE;\nGRANT VISIBILITY_ROLE TO USER WIRE_HOST_TEST;\n'
        'GRANT USAGE ON SCHEMA "vrole" TO ROLE VISIBILITY_ROLE;\n'
        'CREATE TABLE PUBLIC.VIS_NATIVE(V INTEGER);\nINSERT INTO PUBLIC.VIS_NATIVE VALUES(91);\n'
        'GRANT SELECT ON PUBLIC.VIS_NATIVE TO PUBLIC;\nCOMMIT;\n',admin=True)
    # CREATE TABLE privilege is granted by the owner of public, SYSDBA.
    h.summary['visibility_install'] = {'checks':['fresh','rollback','repeat','foreign_collision',
        'catalogs_and_namespace_ids_preserved','invoker','public_execute'],
        'installer_sha256':hashlib.sha256(VISIBILITY_INSTALLER.read_bytes()).hexdigest()}
    require(previous is not None, '--visibility requires --previous-firebird-dir for the runtime upgrade proof')
    previous = previous.resolve()
    old_runtime = h.work/'previous-runtime'
    shutil.copytree(previous,old_runtime,symlinks=False)
    old_db = h.database.parent/'visibility_upgrade.fdb'
    env = h.env.copy()
    env.update(FIREBIRD=str(old_runtime),LD_LIBRARY_PATH=str(old_runtime/'lib'),ISC_USER='SYSDBA')
    old_sql = f"CREATE DATABASE '{old_db}' DEFAULT CHARACTER SET UTF8;\n"+prerequisites()+\
        'CREATE TABLE "public"."upgrade_table"("v" INTEGER);\nINSERT INTO "public"."upgrade_table" VALUES(53);\nCOMMIT;\n'\
        'SET LIST ON;\nSELECT MON$ODS_MAJOR AS N,MON$ODS_MINOR AS N FROM SYSTEM.MON$DATABASE;\nROLLBACK;\nQUIT;\n'
    old = subprocess.run([str(old_runtime/'bin/isql'),'-quiet','-bail','-charset','UTF8'],input=old_sql,
        env=env,cwd=h.temp,capture_output=True,text=True,timeout=h.timeout)
    (h.work/'visibility_upgrade_old.log').write_text(h.redact(old.stdout+old.stderr))
    require(old.returncode==0,'Old runtime setup failed')
    old_ods = numbers(old.stdout)
    upgraded = native(config,'visibility_upgrade_new',installer+f'SET SEARCH_PATH TO {PATH};\n'
        'SELECT MON$ODS_MAJOR AS N,MON$ODS_MINOR AS N FROM SYSTEM.MON$DATABASE;\n'
        'SELECT "v" AS N FROM "upgrade_table";\n'
        'SELECT CASE WHEN "pg_catalog"."pg_table_is_visible"("oid") THEN 1 ELSE 0 END AS N '
        'FROM "pg_catalog"."pg_class" WHERE "relname"=\'upgrade_table\';\nROLLBACK;\nQUIT;\n',
        admin=True,database=old_db)
    require(len(old_ods)==2 and numbers(upgraded['output'])==old_ods+[53,1],'Upgrade changed ODS or visibility/data')
    h.summary['visibility_upgrade'] = {'previous_runtime':str(previous),
        'previous_engine_sha256':hashlib.sha256((previous/'plugins/libEngine14.so').read_bytes()).hexdigest(),
        'ods_before_and_after':old_ods,'old_process_exit':old.returncode,'new_process_exit':upgraded['exit']}

    native(config,'visibility_app15_install',VISIBILITY_INSTALLER.with_name('install_temporary_relations.sql').read_text(),admin=True)


def visibility_client(config,client):
    from pgwire_1c_smoke import Libpq
    report = {'checks':[], 'results':0}
    def record(name):
        report['checks'].append(name)
        (Path(config['work'])/'visibility_progress.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    def check(data,rows=None,error='',state=0,oids=None,connection=client):
        require(data['sqlstate']==error and connection.pq.PQtransactionStatus(connection.connection)==state and
            (rows is None or data.get('rows')==rows) and (oids is None or [c['oid'] for c in data['columns']]==oids),
            'Visibility result differs: '+repr(data))
        report['results'] += 1
        return data
    def query(sql,connection=client,**expected):
        data=connection.query(sql,True)
        require(len(data)==1,'Unexpected result count: '+sql)
        try: return check(data[0],connection=connection,**expected)
        except RuntimeError as error: raise RuntimeError(sql+': '+str(error)) from error
    def path(value,**kw): query('SET SEARCH_PATH TO '+value,**kw)
    def oid(schema,name):
        data=check(client.params('SELECT c.oid FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n '
            'ON c.relnamespace=n.oid WHERE n.nspname=$1 AND c.relname=$2',[schema,name]),oids=[20])
        require(len(data['rows'])==1,'Catalog object missing/ambiguous')
        return data['rows'][0][0]
    def visible(value,expected,**kw):
        kw.setdefault('oids',None if kw.get('error') else [16])
        query(f'SELECT {FUNCTION}({value if value is not None else "NULL"})',rows=None if kw.get('error') else [[expected]],**kw)
    a,b,view,away = (oid(s,n) for s,n in (('va','same'),('vb','same'),('va','visible_view'),('outside','away')))
    # Fixture identities are read before lifetime/role cases. A late Extended catalog
    # refresh after the combined RESET/LTT sequence exposes baseline NAT-05 (also APP-13).
    secret,role_id = oid('vpublic','secret'),oid('vrole','role_table')
    require(a!=b,'Native relation identities collide')
    for order,first,second,val in ((A_PATH,a,b,'11'),(B_PATH,b,a,'22')):
        path(order)
        query('SELECT v FROM same',rows=[[val]])
        visible(first,'t'); visible(second,'f'); visible(view,'t'); visible(away,'f')
        query('SELECT v FROM visible_view',rows=[['11']])
        observed=numbers(native(config,'visibility_native_'+val,f'SET SEARCH_PATH TO {order};\n'
            'SELECT "v" AS N FROM "same";\nSELECT RDB$PG_RELATION_ID(\'same\') AS N FROM SYSTEM.RDB$DATABASE;\n'
            'ROLLBACK;\nQUIT;\n')['output'])
        require(observed==[int(val),int(first)-1],'Native SELECT/relation ID disagrees')
    path(A_PATH)
    for name in ('MixedName','Я🙂name','dot.name','double"quote','Я'*63,'🙂'*63):
        quoted=identifier(name)
        query(f'CREATE TABLE va.{quoted}(v INTEGER)')
        query(f'INSERT INTO va.{quoted} VALUES(33)')
        visible(oid('va',name),'t')
        query(f'SELECT v FROM {quoted}',rows=[['33']])
        check(client.params('SELECT RDB$PG_RELATION_ID($1)',[name]),rows=[[str(int(oid('va',name))-1)]],oids=[23])
    path('"PUBLIC", "pg_catalog", "SYSTEM"')
    visible(oid('PUBLIC','VIS_NATIVE'),'t')
    query('SELECT "V" FROM "VIS_NATIVE"',rows=[['91']])
    path(A_PATH)
    record('exact_names_native_ids_and_fresh_select_oracles')
    for value in (None,'0','-1','-9223372036854775808','9223372036854775807','4294967295'):
        visible(value,None)
    sql=f'SELECT {FUNCTION}($1) AS visible'
    check(client.result(client.pq.PQprepare(client.connection,b'visibility',sql.encode(),0,None)))
    description=check(client.result(client.pq.PQdescribePrepared(client.connection,b'visibility')),oids=[16])
    require(description['parameter_oids']==[20],'Public parameter is not native BIGINT')
    report['metadata']=description
    for order,expected in ((A_PATH,'t'),(B_PATH,'f'),(PATH,'f'),(A_PATH,'t')):
        path(order)
        check(client.params('visibility',[a],prepared=True),rows=[[expected]],oids=[16])
        check(client.params('visibility',[int(a).to_bytes(8,'big',signed=True)],formats=[1],prepared=True,result_format=1),
            rows=[['01' if expected=='t' else '00']],oids=[16])
    check(client.params('visibility',[None],prepared=True,result_format=1),rows=[[None]],oids=[16])
    query('ALTER SESSION RESET')
    check(client.params('visibility',[a],prepared=True),error='26000')
    report['reset_statement_lifetime']='Existing RESET clears named statements: 26000, then fresh Parse'
    check(client.result(client.pq.PQprepare(client.connection,b'visibility',sql.encode(),0,None)))
    check(client.params('visibility',[a],prepared=True),rows=[['f']])
    second=Libpq(config)
    try:
        path(A_PATH)
        visible(a,'t'); visible(a,'f',connection=second)
        query('BEGIN',state=2); path(B_PATH,state=2); query('ROLLBACK')
        visible(a,'f')  # Native SET SEARCH_PATH has session lifetime.
        path(A_PATH)
        query('CREATE TEMP TABLE other_ltt(v INTEGER) ON COMMIT PRESERVE ROWS',connection=second)
        visible(a,'t')
        query('DROP TABLE pg_temp.other_ltt',connection=second)
    finally: second.close()
    record('simple_named_extended_binary_reset_independent_attachments_session_set')
    for bad in ('', 'same ', 'same\x00suffix', 'x'*64, 'Я'*64, 'x'*253):
        check(client.params('SELECT RDB$PG_RELATION_ID(CAST($1 AS VARCHAR(300)))',[bad],oids=[1043],formats=[1]),error='22021' if '\x00' in bad else '22023')
    check(client.params('SELECT RDB$PG_RELATION_ID($1)',['va.same']),rows=[[None]])
    for order in ('"va", "SYSTEM"','"va", "$user", "pg_catalog", "SYSTEM"',
                  '"va", "pg_temp", "pg_catalog", "SYSTEM"'):
        path(order)
        query('SELECT v FROM same',rows=[['11']])
        visible(away,'f'); visible(a,'t')
        visible(None,None); visible('0',None)
    path(A_PATH)
    query('BEGIN',state=2)
    query('SELECT COUNT(*) FROM "SYSTEM"."MON$LOCAL_TEMPORARY_TABLES"',state=2,rows=[['0']])
    query('CREATE TEMP TABLE live_ltt(v INTEGER) ON COMMIT PRESERVE ROWS',state=2)
    query(f'SELECT {FUNCTION}({a})',rows=[['t']],state=2)
    query('SELECT 1/0',error='22012',state=3)
    query('SELECT 1',error='25P02',state=3)
    query('ROLLBACK')
    # LTT DDL follows native lifetime rules; the API probe tests live DROP on one transaction.
    query('CREATE TEMP TABLE drop_ltt(v INTEGER) ON COMMIT PRESERVE ROWS')
    query('SELECT v FROM same',rows=[['11']])
    query(f'SELECT {FUNCTION}({away})',rows=[['f']])
    query('DROP TABLE pg_temp.drop_ltt')
    visible(a,'t')
    record('invalid_names_path_guards_live_ltt_and_error_recovery')
    path('"vpublic", "pg_catalog", "SYSTEM"')
    visible(secret,'t')
    native_public=native(config,'visibility_native_public','SET SEARCH_PATH TO "vpublic", "pg_catalog", "SYSTEM";\n'
        "SELECT RDB$PG_RELATION_ID('secret') AS N FROM SYSTEM.RDB$DATABASE;\nROLLBACK;\nQUIT;\n")
    require(numbers(native_public['output'])==[int(secret)-1],'Native PUBLIC USAGE disagrees')
    native_select=native(config,'visibility_native_no_select','SELECT "v" FROM "vpublic"."secret";\n',error=True)
    require(native_select['sqlstates']==['28000'],'Native no-SELECT denial changed')
    denied=query('SELECT v FROM secret',error='42501')
    report['no_select_sqlstate']=denied['sqlstate']
    path('"va", "vhidden", "pg_catalog", "SYSTEM"')
    query('SELECT v FROM same',rows=[['11']])
    query(f'SELECT {FUNCTION}({a})',rows=[['t']])
    path('"vhidden", "pg_catalog", "SYSTEM"')
    denied=query(f'SELECT {FUNCTION}({secret})',rows=[['f']])
    report['no_usage_sqlstate']=denied['sqlstate']
    for role,visible_value in (('"NONE"','f'),('"VISIBILITY_ROLE"','t')):
        query('SET ROLE '+role)
        path('"vrole", "pg_catalog", "SYSTEM"')
        query(f'SELECT {FUNCTION}({role_id})',rows=[[visible_value]])
    query('SET ROLE "NONE"')
    native_role=native(config,'visibility_native_role','SET ROLE VISIBILITY_ROLE;\n'
        'SET SEARCH_PATH TO "vrole", "pg_catalog", "SYSTEM";\n'
        "SELECT RDB$PG_RELATION_ID('role_table') AS N FROM SYSTEM.RDB$DATABASE;\nROLLBACK;\nQUIT;\n")
    require(numbers(native_role['output'])==[int(role_id)-1],'Native role SCL disagrees')
    native_denied=native(config,'visibility_native_no_usage','SET SEARCH_PATH TO "vhidden", "pg_catalog", "SYSTEM";\n'
        'SELECT \"v\" FROM \"vhidden\".\"hidden\";\n',error=True)
    require(native_denied['sqlstates']==['28000'],'Native SCL denial changed')
    for grant in (True,False):
        native(config,'visibility_usage_'+str(grant),
            ('GRANT USAGE ON SCHEMA "vhidden" TO USER WIRE_HOST_TEST;' if grant else
             'REVOKE USAGE ON SCHEMA "vhidden" FROM USER WIRE_HOST_TEST;')+'\nCOMMIT;\n',admin=True)
        fresh=Libpq(config)
        try:
            path('"vhidden", "pg_catalog", "SYSTEM"',connection=fresh)
            query(f'SELECT {FUNCTION}({secret})',connection=fresh,rows=[['f']])
        finally: fresh.close()
    report['acl_cache_scope']='GRANT/REVOKE verified with fresh attachments (native cache boundary)'
    record('native_scl_owner_public_role_invoker_no_select_and_grant_revoke')
    path(A_PATH)
    query('CREATE TABLE va.lifetime(v INTEGER)')
    life=oid('va','lifetime'); visible(life,'t')
    query('BEGIN',state=2); query('DROP TABLE va.lifetime',state=2); visible(life,None,state=2)
    query('ROLLBACK'); visible(life,'t')
    query('DROP TABLE va.lifetime'); visible(life,None)
    query('BEGIN',state=2); query('SAVEPOINT ddl_guard',state=2)
    query('CREATE TABLE va.must_not_exist(v INTEGER)',error='0A000',state=3)
    query('ROLLBACK')
    query("SELECT COUNT(*) FROM pg_catalog.pg_class WHERE relname='must_not_exist'",rows=[['0']])
    query(f'SELECT 1::INT8 FROM pg_catalog.pg_class WHERE {FUNCTION}(oid) '
        f"AND relkind='r' AND relname='same' AND oid={a} LIMIT 1",rows=[['1']],oids=[20])
    report['selected04']='SKIP: original regnamespace is still unsupported; synthetic query only'
    record('ddl_rollback_drop_savepoint_guard_and_synthetic_predicate')
    return report


def visibility_api(h):
    source=Path(__file__).with_name('pgwire_visibility_api.cpp')
    binary=h.work/'visibility_api'
    built=subprocess.run(['g++','-std=c++17','-g','-O0',str(source),'-I'+str(h.runtime/'include'),
        '-L'+str(h.runtime/'lib'),'-Wl,-rpath,'+str(h.runtime/'lib'),'-lfbclient','-o',str(binary)],
        env=h.env,capture_output=True,text=True,timeout=h.timeout)
    (h.work/'visibility_api_compile.log').write_text(built.stdout+built.stderr)
    require(built.returncode==0,'Visibility OO probe compilation failed')
    run=subprocess.run([str(binary),str(h.database.parent/'visibility_api.fdb')],env=h.env,cwd=h.temp,
        capture_output=True,text=True,timeout=h.timeout)
    (h.work/'visibility_api.log').write_text(h.redact(run.stdout+run.stderr))
    require(run.returncode==0,'Visibility OO probe failed')
    return json.loads(run.stdout)
