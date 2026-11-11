"""APP-13: PG names, native-safe stored SQL and the installed session profile."""
import ctypes as C
import hashlib
import json
from pathlib import Path

from pgwire_namespace_checks import (INSTALLER, CATALOG_INSTALLER, native, config_for,
    mapping, allocator, catalog_snapshot, identifier, numbers)
from pgwire_class_checks import CLASS_INSTALLER
from wire_host_smoke import require

NAMES_INSTALLER = INSTALLER.with_name('install_pg_names.sql')
PATH = '"public", "pg_catalog", "SYSTEM"'


def names_fixture(h):
    config = config_for(h)
    bootstrap = INSTALLER.parent.parent/'compat.sql'
    prerequisites = INSTALLER.read_text()+bootstrap.read_text()+CATALOG_INSTALLER.read_text()+CLASS_INSTALLER.read_text()
    native(config,'names_admin',f"CREATE OR ALTER USER SYSDBA PASSWORD '{h.password}';\nCOMMIT;\n",admin=True)
    native(config,'names_prerequisites',prerequisites,admin=True)
    native(config,'names_grants','GRANT CREATE SCHEMA TO USER WIRE_HOST_TEST;\n'
        'CREATE TABLE PUBLIC.TTT(V INTEGER);\nINSERT INTO PUBLIC.TTT VALUES(91);\n'
        'GRANT SELECT,INSERT,UPDATE,DELETE ON PUBLIC.TTT TO USER WIRE_HOST_TEST;\nCOMMIT;\n',admin=True)
    native(config,'names_existing_public','CREATE SCHEMA "public";\nCREATE TABLE "public"."keep"("v" INTEGER);\n'
        'INSERT INTO "public"."keep" VALUES(73);\nCOMMIT;\n')
    before = mapping(config,'names_before')
    snapshot = catalog_snapshot(config,'names_bootstrap_before',True)
    installer = NAMES_INSTALLER.read_text()
    failure = installer.replace('\nCOMMIT;','\nSELECT 1/0 FROM SYSTEM.RDB$DATABASE;\nCOMMIT;',1)
    error = native(config,'names_failure',failure,admin=True,error=True)
    require(error['sqlstates']==['22012'] and mapping(config,'names_failed_mapping')==before and
            catalog_snapshot(config,'names_bootstrap_failed',True)==snapshot,'Failed install damaged prior profile')
    native(config,'names_install','SET SEARCH_PATH TO SYSTEM;\n'+installer,admin=True)
    after = mapping(config,'names_after')
    require(set(after)==set(before)|{'pg_catalog'} and all(after[k]==v for k,v in before.items()) and
            after['PUBLIC']!=after['public'] and after['PG_CATALOG']!=after['pg_catalog'] and
            catalog_snapshot(config,'names_bootstrap_after',True)==snapshot,'Installer changed namespace identities/bootstrap')
    counter = allocator(config,'names_counter')
    error = native(config,'names_repeat',installer,admin=True,error=True)
    require('already exists' in error['output'].lower() and mapping(config,'names_repeat_mapping')==after and
            allocator(config,'names_repeat_counter')==counter,'Repeated install changed objects or allocator')
    output = native(config,'names_preserved_public','SELECT "v" AS N FROM "public"."keep";\n'
        "SELECT COUNT(*) AS N FROM SYSTEM.RDB$SCHEMAS WHERE RDB$SCHEMA_NAME='public' AND RDB$OWNER_NAME='WIRE_HOST_TEST';\n"
        'ROLLBACK;\nQUIT;\n')['output']
    require(numbers(output)==[73,1],'Existing public owner/data lost')
    report = {'checks':['existing_public_owner_data_and_uppercase_objects_preserved','atomic_rollback','repeat_refused'],
              'installer_sha256':hashlib.sha256(NAMES_INSTALLER.read_bytes()).hexdigest()}
    for case in ('fresh','collision'):
        database = h.database.parent/('names_'+case+'.fdb')
        h.isql('names_create_'+case,f"CREATE DATABASE '{database}' DEFAULT CHARACTER SET UTF8;\nCOMMIT;\nQUIT;\n",embedded=True)
        cfg = dict(config,database=str(database))
        native(cfg,'names_'+case+'_prerequisites',prerequisites,admin=True)
        if case=='collision':
            native(cfg,'names_collision_object','CREATE SCHEMA "pg_catalog";\n'
                'CREATE TABLE "pg_catalog"."marker"(V INTEGER);\nINSERT INTO "pg_catalog"."marker" VALUES(17);\nCOMMIT;\n',admin=True)
        prior = mapping(cfg,'names_'+case+'_before')
        n = allocator(cfg,'names_'+case+'_counter_before')
        rejected = native(cfg,'names_'+case+'_failure',failure if case=='fresh' else installer,admin=True,error=True)
        require(mapping(cfg,'names_'+case+'_rollback')==prior,'Failed install changed schemas')
        if case=='fresh':
            require(rejected['sqlstates']==['22012'],'Wrong failure injection')
            native(cfg,'names_fresh_install',installer,admin=True)
            current = mapping(cfg,'names_fresh_after')
            require(set(current)==set(prior)|{'public','pg_catalog'} and all(current[k]==v for k,v in prior.items()),'Fresh names install differs')
        else:
            require('already exists' in rejected['output'].lower() and allocator(cfg,'names_collision_counter_after')==n,'Collision allocated ID')
            require(numbers(native(cfg,'names_collision_data','SELECT V AS N FROM "pg_catalog"."marker";\nROLLBACK;\nQUIT;\n',admin=True)['output'])==[17],
                    'Foreign schema data changed')
    report['checks'] += ['fresh_both_schemas_atomic','foreign_lowercase_catalog_refused']
    for owner in (False,True):
        for view in ('pg_namespace','pg_class'):
            denied = native(config,f'names_readonly_{owner}_{view}',f'DELETE FROM "pg_catalog"."{view}";\nROLLBACK;\n',admin=owner,error=True)
            require('read-only' in denied['output'].lower() or 'permission' in denied['output'].lower(),'Catalog not read-only')
    report['checks'].append('lowercase_catalogs_readonly')
    h.summary['pg_names_install'] = report


def names_client(config,client):
    from pgwire_1c_smoke import Libpq
    report = {'checks':[], 'results':0}
    def record(name):
        report['checks'].append(name)
        (Path(config['work'])/'names_progress.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    def result(data,*,rows=None,error='',state=0,labels=None,oids=None):
        require(data['sqlstate']==error and client.pq.PQtransactionStatus(client.connection)==state and
            (rows is None or data.get('rows')==rows) and (labels is None or [c['name'] for c in data['columns']]==labels) and
            (oids is None or [c['oid'] for c in data['columns']]==oids),'Names result differs: '+repr(data))
        report['results'] += 1
        return data
    def query(sql,**expected):
        data=client.query(sql,True)
        require(len(data)==1,'Unexpected Simple result count: '+sql)
        try: return result(data[0],**expected)
        except RuntimeError as error: raise RuntimeError(sql+': '+str(error)) from error
    def params(sql,values=(),**expected):
        return result(client.params(sql,values),**expected)
    def path(expected=PATH):
        query("SELECT RDB$GET_CONTEXT('SYSTEM','SEARCH_PATH')",rows=[[expected]])
    def mode(value):
        require(client.pq.PQparameterStatus(client.connection,b'standard_conforming_strings')==value.encode(),'Wrong ParameterStatus')
    path()
    identity_sql = 'SELECT pg_catalog.pg_backend_pid()'
    capture = Path(config['capture_dir'])/'profile/selected/01-backend-pid.sql'
    if capture.is_file():
        require(capture.read_text().strip()==identity_sql, 'selected01 SQL differs')
        identity_sql = capture.read_text()
    query(identity_sql,oids=[23])
    report['selected01'] = {'original_capture':capture.is_file(),
                            'sha256':hashlib.sha256(identity_sql.encode()).hexdigest()}
    query('SELECT pg_backend_pid()',oids=[23])
    query('SELECT v FROM keep',rows=[['73']],labels=['v'])
    query('SELECT "V" FROM "PUBLIC"."TTT"',rows=[['91']],labels=['V'])
    query('SELECT v FROM ttt',error='42P01')
    query('CREATE TABLE Foo(Bar INTEGER, Name VARCHAR(40))')
    query("INSERT INTO FOO VALUES(1,'Foo BAR')")
    for name in ('foo','FOO','"foo"'):
        query('SELECT BAR AS MixedAlias, Name FROM '+name,rows=[['1','Foo BAR']],labels=['mixedalias','name'])
    for name in ('"Foo"','"FOO"'):
        query('CREATE TABLE '+name+'("Bar" INTEGER)')
        query('INSERT INTO '+name+' VALUES(2)')
        query('SELECT "Bar" FROM '+name,rows=[['2']],labels=['Bar'])
    query('WITH MixedCte(Item) AS (SELECT 3) SELECT Item AS Result FROM MixedCte',rows=[['3']],labels=['result'])
    query('CREATE TABLE Action(Type INTEGER)')
    query('INSERT INTO ACTION VALUES(4)')
    query('SELECT Type FROM Action',rows=[['4']],labels=['type'])
    query('SELECT ABS(-5), MOD(9,4)',rows=[['5','1']])
    # Unquoted lexical forms retain native restrictions; quoted UTF8 is below.
    query('CREATE TABLE ЯMixed(Val INTEGER)',error='42601')
    query('DELETE FROM Action WHERE Type=4 RETURNING Type',rows=[['4']])
    query('DROP TABLE Action')
    query('SET DECFLOAT ROUND HALF_EVEN')
    query('SET DECFLOAT TRAPS TO Division_by_zero, Invalid_operation, Overflow')
    query('CREATE DOMAIN MyDomain AS INTEGER CHECK(VALUE>0)')
    query('ALTER TABLE foo ADD Extra MyDomain')
    params('UPDATE foo SET bar=$1 WHERE bar=$2 RETURNING bar',['7','1'],rows=[['7']],labels=['bar'])
    params('SELECT bar FROM foo WHERE bar=$1',['7'],rows=[['7']])
    native(config,'names_native_shared','UPDATE "public"."foo" SET "bar"=8 WHERE "bar"=7;\nCOMMIT;\n')
    query('SELECT bar,name FROM foo',rows=[['8','Foo BAR']])
    for name in ('Схема A',' leading','A"quote','Я'*56):
        query('CREATE TABLE '+identifier(name)+'(Val INTEGER)')
        query('INSERT INTO '+identifier(name)+' VALUES(6)')
        query('SELECT Val FROM '+identifier(name),rows=[['6']])
    for sql in ('CREATE TABLE "bad "(v INTEGER)','SELECT 1 AS "bad "','SAVEPOINT "bad "'):
        query(sql,error='42000')
    query('SELECT 1 AS '+identifier('x'*64),error='42000')
    record('ascii_fold_exact_quoted_unicode_keywords_domains_cte_aliases_ddl_dml_native_readback')

    # Search path is a session setting; changing it is explicit, not per-query.
    query('CREATE SCHEMA OtherPath')
    query('CREATE TABLE OtherPath.foo(bar INTEGER)')
    query('INSERT INTO OtherPath.foo VALUES(19)')
    query('SET SEARCH_PATH TO OtherPath, public, pg_catalog, "SYSTEM"')
    query('SELECT bar FROM foo',rows=[['19']])
    result(client.result(client.pq.PQprepare(client.connection,b'path_prepared',b'SELECT bar FROM foo',0,None)))
    result(client.params('path_prepared',prepared=True),rows=[['19']])
    query('SET SEARCH_PATH TO public, pg_catalog, "SYSTEM"')
    query('SELECT bar FROM foo',rows=[['8']])
    result(client.params('path_prepared',prepared=True),rows=[['8']])
    query('SET SEARCH_PATH TO OtherPath, "SYSTEM"')
    query('ALTER SESSION RESET')
    path()
    query('SELECT bar FROM foo',rows=[['8']])
    query('SET SEARCH_PATH TO OtherPath, "SYSTEM"')
    params('ALTER SESSION RESET')
    path()
    query('SELECT bar FROM foo',rows=[['8']])
    record('initial_public_first_custom_path_cached_prepared_reuse_simple_extended_reset')

    query('BEGIN',state=2)
    query('SAVEPOINT a',state=2)
    query('SET standard_conforming_strings=off',state=2)
    mode('off')
    query('SAVEPOINT "A"',state=2)
    query('SET standard_conforming_strings=on',state=2)
    query('SELECT 1/0',state=3,error='22012'); mode('off')
    query('ROLLBACK TO "A"',state=2); mode('off')
    query('RELEASE SAVEPOINT "A"',state=2)
    query('ROLLBACK TO "a"',state=2); mode('on')
    query('ROLLBACK')
    record('savepoint_pg_case_strings_error_rollback_release_parameter_status')

    ns=query('SELECT oid,nspname FROM pg_namespace ORDER BY oid',labels=['oid','nspname'],oids=[20,1043])['rows']
    require({name:int(oid) for oid,name in ns}==mapping(config,'names_catalog_oracle'),'Lowercase namespace values changed')
    rows=query('SELECT oid,relname,relnamespace,relkind FROM pg_class ORDER BY oid',labels=['oid','relname','relnamespace','relkind'],oids=[20,1043,20,1043])['rows']
    old=query('SELECT "OID","RELNAME","RELNAMESPACE","RELKIND" FROM "PG_CATALOG"."PG_CLASS" ORDER BY "OID"')['rows']
    require(rows==old,'Lowercase catalog is not an exact public projection')
    record('lowercase_catalog_labels_exact_native_values_and_types')
    names_sources(config,client,query,report)
    other=Libpq(config)
    try:
        data=other.query('SELECT bar FROM foo',True)[0]
        require(not data['sqlstate'] and data['rows']==[['8']],'Reconnected naming changed')
    finally: other.close()
    report['source_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return report


def names_sources(config,client,query,report):
    query('SET standard_conforming_strings=off')
    query(r"CREATE TABLE SourceTable (BaseVal INTEGER DEFAULT 3, TextVal VARCHAR(30) DEFAULT 'Foo\nBAR', "
          'CalcVal COMPUTED BY (BaseVal+1), CONSTRAINT PositiveVal CHECK(BaseVal>0))')
    query('INSERT INTO SourceTable DEFAULT VALUES')
    query('SELECT BaseVal,TextVal,CalcVal FROM SourceTable',rows=[['3','Foo\nBAR','4']])
    query(r"CREATE VIEW MixedView AS SELECT BaseVal AS MyAlias, TextVal || '\tX' AS TextAlias FROM SourceTable")
    query('CREATE INDEX ExprIdx ON SourceTable COMPUTED BY (BaseVal+2)')
    query(r"CREATE FUNCTION MixFn(ArgOne INTEGER) RETURNS VARCHAR(40) AS DECLARE LocalVal INTEGER; "
          r"BEGIN LocalVal=ArgOne+1; RETURN CAST(LocalVal AS VARCHAR(10)) || '\nFn'; END")
    query('CREATE PROCEDURE MixProc(ArgOne INTEGER) RETURNS (OutVal INTEGER) AS DECLARE LocalVal INTEGER; '
          'BEGIN LocalVal=ArgOne+2; OutVal=LocalVal; SUSPEND; END')
    query('SELECT MixFn(ArgOne => 5)',rows=[['6\nFn']])
    query('SELECT OutVal FROM MixProc(ArgOne => 5)',rows=[['7']])
    query('CREATE TABLE Copied AS (SELECT BaseVal AS CopyVal, TextVal FROM SourceTable) WITH DATA')
    query('SELECT CopyVal,TextVal FROM Copied',rows=[['3','Foo\nBAR']])
    query('SET standard_conforming_strings=on')
    # Read actual metadata source and send it through a new native dialect-3
    # attachment. This verifies reprepare, not merely existing BLR execution.
    def source(table,field,predicate):
        data=query(f'SELECT CAST("{field}" AS VARCHAR(8191)) FROM "SYSTEM"."{table}" WHERE '+predicate)['rows']
        require(len(data)==1 and data[0][0], 'Missing stored source: '+field)
        return data[0][0]
    view=source('RDB$RELATIONS','RDB$VIEW_SOURCE',"\"RDB$SCHEMA_NAME\"='public' AND \"RDB$RELATION_NAME\"='mixedview'")
    fn=source('RDB$FUNCTIONS','RDB$FUNCTION_SOURCE',"\"RDB$SCHEMA_NAME\"='public' AND \"RDB$FUNCTION_NAME\"='mixfn'")
    proc=source('RDB$PROCEDURES','RDB$PROCEDURE_SOURCE',"\"RDB$SCHEMA_NAME\"='public' AND \"RDB$PROCEDURE_NAME\"='mixproc'")
    index=source('RDB$INDICES','RDB$EXPRESSION_SOURCE',"\"RDB$SCHEMA_NAME\"='public' AND \"RDB$INDEX_NAME\"='expridx'")
    field_source=query('SELECT TRIM("RDB$FIELD_SOURCE") FROM "SYSTEM"."RDB$RELATION_FIELDS" '
        "WHERE \"RDB$SCHEMA_NAME\"='public' AND \"RDB$RELATION_NAME\"='sourcetable' AND \"RDB$FIELD_NAME\"='calcval'")['rows'][0][0]
    computed=source('RDB$FIELDS','RDB$COMPUTED_SOURCE',"\"RDB$SCHEMA_NAME\"='public' AND \"RDB$FIELD_NAME\"='"+field_source+"'")
    default=source('RDB$RELATION_FIELDS','RDB$DEFAULT_SOURCE',"\"RDB$SCHEMA_NAME\"='public' AND \"RDB$RELATION_NAME\"='sourcetable' AND \"RDB$FIELD_NAME\"='textval'")
    check=query('SELECT CAST(T."RDB$TRIGGER_SOURCE" AS VARCHAR(8191)) FROM "SYSTEM"."RDB$TRIGGERS" T '
        'JOIN "SYSTEM"."RDB$CHECK_CONSTRAINTS" C ON C."RDB$TRIGGER_NAME"=T."RDB$TRIGGER_NAME" '
        'AND C."RDB$SCHEMA_NAME"=T."RDB$SCHEMA_NAME" '
        "WHERE C.\"RDB$SCHEMA_NAME\"='public' AND C.\"RDB$CONSTRAINT_NAME\"='positiveval'")['rows'][0][0]
    sources={'view':view,'function':fn,'procedure':proc,'index':index,'computed':computed,'default':default,'check':check}
    require('"baseval"' in view and '"argone"' in fn and '"localval"' in fn and '"outval"' in proc and
            '"baseval"' in index and '"baseval"' in computed and '"baseval"' in check and
            "_UTF8 X'" in default,'Identifier/string source marks missing: '+repr(sources))
    sql='SET SEARCH_PATH TO "public", "pg_catalog", SYSTEM;\nSET TERM ^;\n'
    sql+='ALTER VIEW "public"."mixedview" AS '+view+'^\n'
    sql+='ALTER FUNCTION "public"."mixfn"("argone" INTEGER) RETURNS VARCHAR(40) AS '+fn+'^\n'
    sql+='ALTER PROCEDURE "public"."mixproc"("argone" INTEGER) RETURNS ("outval" INTEGER) AS '+proc+'^\n'
    sql+='SET TERM ;^\nDROP INDEX "public"."expridx";\nCREATE INDEX "public"."expridx" ON "public"."sourcetable" COMPUTED BY '+index+';\n'
    sql+='ALTER TABLE "public"."sourcetable" ALTER "textval" SET '+default+';\n'
    sql+='ALTER TABLE "public"."sourcetable" DROP CONSTRAINT "positiveval";\n'
    sql+='ALTER TABLE "public"."sourcetable" ADD CONSTRAINT "positiveval" '+check+';\n'
    sql+='CREATE TABLE "public"."computed_copy"("baseval" INTEGER,"calcval" COMPUTED BY '+computed+');\n'
    sql+='INSERT INTO "public"."computed_copy"("baseval") VALUES(10);\nCOMMIT;\nQUIT;\n'
    native(config,'names_native_reprepare',sql)
    query('SELECT MyAlias,TextAlias FROM MixedView',rows=[['3','Foo\nBAR\tX']])
    query('SELECT MixFn(ArgOne => 5)',rows=[['6\nFn']])
    query('SELECT OutVal FROM MixProc(ArgOne => 5)',rows=[['7']])
    query('SELECT CalcVal FROM computed_copy',rows=[['11']])
    query('INSERT INTO SourceTable(BaseVal) VALUES(-1)',error='23514')
    # Dynamic SQL literals keep native semantics. There is no nested dialect rewrite.
    query('EXECUTE BLOCK RETURNS (Val INTEGER) AS BEGIN '
        "EXECUTE STATEMENT 'SELECT V FROM PUBLIC.TTT' INTO :Val; SUSPEND; END",rows=[['91']])
    for sql in ('CREATE VIEW bad_param AS SELECT CAST($1 AS INTEGER)',
                'CREATE VIEW bad_cast AS SELECT 1::INTEGER','CREATE VIEW bad_limit AS SELECT 1 LIMIT 1'):
        query(sql,error='0A000')
    report['stored_sources']=sources
    report['checks'].append('stored_sources_view_default_check_computed_index_psql_ctas_native_reprepare_and_root_guards')
