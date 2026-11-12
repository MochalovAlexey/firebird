"""APP-15 lifecycle checks using the existing isolated 1c/libpq/native harness."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from pgwire_namespace_checks import native, config_for, mapping, allocator, numbers
from pgwire_visibility_checks import prerequisites, VISIBILITY_INSTALLER
from wire_host_smoke import require

INSTALLER = VISIBILITY_INSTALLER.with_name('install_temporary_relations.sql')


def temporary_fixture(h, previous):
    config = config_for(h)
    native(config, 'temporary_admin', f"CREATE OR ALTER USER SYSDBA PASSWORD '{h.password}';\n"
        'GRANT CREATE SCHEMA TO USER WIRE_HOST_TEST;\nCOMMIT;\n', admin=True)
    native(config, 'temporary_prerequisites', prerequisites()+VISIBILITY_INSTALLER.read_text()+
        'GRANT CREATE TABLE ON SCHEMA "public" TO USER WIRE_HOST_TEST;\nGRANT CREATE VIEW ON SCHEMA "public" TO USER WIRE_HOST_TEST;\nCOMMIT;\n', admin=True)
    native(config, 'temporary_permissions',
        'CREATE SCHEMA "no_usage";\nCREATE SCHEMA "role_path";\n'
        'CREATE SCHEMA "WIRE_HOST_TEST";\n'
        'GRANT USAGE ON SCHEMA "WIRE_HOST_TEST" TO USER WIRE_HOST_TEST;\n'
        'CREATE TABLE "WIRE_HOST_TEST"."user_path"("v" INTEGER);\n'
        'INSERT INTO "WIRE_HOST_TEST"."user_path" VALUES(33);\n'
        'GRANT SELECT ON "WIRE_HOST_TEST"."user_path" TO USER WIRE_HOST_TEST;\n'
        'CREATE TABLE "no_usage"."path_items"("v" INTEGER);\n'
        'INSERT INTO "no_usage"."path_items" VALUES(99);\n'
        'GRANT SELECT ON "no_usage"."path_items" TO USER WIRE_HOST_TEST;\n'
        'CREATE TABLE "role_path"."path_items"("v" INTEGER);\n'
        'INSERT INTO "role_path"."path_items" VALUES(77);\n'
        'GRANT SELECT ON "role_path"."path_items" TO PUBLIC;\n'
        'CREATE ROLE TEMP_PATH_ROLE;\nGRANT TEMP_PATH_ROLE TO USER WIRE_HOST_TEST;\n'
        'GRANT USAGE ON SCHEMA "role_path" TO ROLE TEMP_PATH_ROLE;\n'
        'CREATE TABLE "public"."no_select"("v" INTEGER);\nCOMMIT;\n', admin=True)
    native(config,'temporary_current_user_fixture',
        'CREATE SCHEMA SYSDBA;\nCREATE TABLE SYSDBA."user_path"("v" INTEGER);\n'
        'SET TERM ^;\nCREATE FUNCTION "public"."user_resolution" RETURNS INTEGER SQL SECURITY DEFINER AS BEGIN '
        "RETURN RDB$PG_RELATION_ID('user_path'); END^\nSET TERM ;^\n"
        'GRANT EXECUTE ON FUNCTION "public"."user_resolution" TO PUBLIC;\nCOMMIT;\n',admin=True)
    before = mapping(config, 'temporary_before')
    installer = INSTALLER.read_text()
    failed = native(config, 'temporary_install_failure', installer.replace('\nCOMMIT;',
        '\nSELECT 1/0 FROM SYSTEM.RDB$DATABASE;\nCOMMIT;'), admin=True, error=True)
    require(failed['sqlstates']==['22012'], 'Wrong installation failure')
    require(mapping(config,'temporary_failure_mapping')==before, 'Failed install changed namespace IDs')
    # Independent copies of the committed APP-14 fixture: foreign configuration
    # must survive a failed transition byte-for-byte at the SQL object level.
    for label, ddl in (
            ('container', 'CREATE SCHEMA PGWIRE_TEMP;'),
            ('virtual_schema', 'CREATE SCHEMA "pg_temp_123";'),
            ('projection', 'ALTER VIEW "pg_catalog"."pg_class"("oid","relname","relnamespace","relkind") AS '
                'SELECT OID,RELNAME,RELNAMESPACE,RELKIND FROM PG_CATALOG.PG_CLASS;'),
            ('counter_range', 'SET GENERATOR PGWIRE_COMPAT.NAMESPACE_SEQ TO 4294967296;')):
        target=h.database.parent/('temporary_collision_'+label+'.fdb')
        shutil.copy2(h.database,target)
        cfg=dict(config,database=str(target))
        native(cfg,'temporary_collision_setup_'+label,ddl+'\nCOMMIT;\n',admin=True)
        rejected=native(cfg,'temporary_collision_'+label,installer,admin=True,error=True)
        require(rejected['sqlstates']==['HY000'],'Foreign configuration was not rejected: '+label)
        remain=native(cfg,'temporary_collision_clean_'+label,
            "SELECT COUNT(*) AS N FROM SYSTEM.RDB$PROCEDURES WHERE RDB$SCHEMA_NAME='PGWIRE_COMPAT' "
            "AND RDB$PROCEDURE_NAME='TEMP_ROWS_V1';\nROLLBACK;\nQUIT;\n",admin=True)
        require(numbers(remain['output'])==[0],'Failed transition left provider behind')
    high=h.database.parent/'temporary_high.fdb'
    shutil.copy2(h.database,high)
    cfg=dict(config,database=str(high))
    native(cfg,'temporary_high_setup','SET GENERATOR PGWIRE_COMPAT.NAMESPACE_SEQ TO 1000000;\nCOMMIT;\n',admin=True)
    native(cfg,'temporary_high_install',installer,admin=True)
    require(allocator(cfg,'temporary_high_allocator')>1000000,'Upgrade lowered allocator')
    native(config, 'temporary_install', installer, admin=True)
    after = mapping(config,'temporary_after')
    require(all(after.get(k)==v for k,v in before.items()) and
        set(after)-set(before)=={'PGWIRE_TEMP'}, 'Upgrade changed old namespace identities')
    counter = allocator(config,'temporary_counter')
    native(config,'temporary_repeat',installer,admin=True,error=True)
    require(allocator(config,'temporary_repeat_counter')==counter and
        mapping(config,'temporary_repeat_mapping')==after, 'Repeated install changed state')
    for label,sql in (('persistent','CREATE TABLE PGWIRE_TEMP.FORBIDDEN(V INTEGER);'),
                      ('gtt','CREATE GLOBAL TEMPORARY TABLE PGWIRE_TEMP.FORBIDDEN(V INTEGER);')):
        refused=native(config,'temporary_container_'+label,sql,admin=False,error=True)
        require(refused['sqlstates']==['HY000'],'Container protection did not run')
    contract='RETURNS (ROW_KIND SMALLINT,PUBLIC_ID BIGINT,NATIVE_ID INTEGER,RELATION_NAME VARCHAR(63) CHARACTER SET UTF8,NAMESPACE_ID BIGINT,NAMESPACE_NAME VARCHAR(63) CHARACTER SET UTF8)'
    for label,name,layout in (('schema','PUBLIC.TEMP_ROWS_V1',contract),
            ('name','PGWIRE_COMPAT.WRONG_ROWS',contract),
            ('layout','PGWIRE_COMPAT.TEMP_ROWS_V1',contract.replace('ROW_KIND SMALLINT','ROW_KIND INTEGER'))):
        target=h.database.parent/('temporary_contract_'+label+'.fdb')
        shutil.copy2(h.database,target)
        cfg=dict(config,database=str(target))
        refused=native(cfg,'temporary_contract_'+label,
            f"CREATE OR ALTER PROCEDURE {name} {layout} EXTERNAL NAME 'PGWIRE_TEMP_ROWS_V1' ENGINE SYSTEM;\n"
            f'SELECT * FROM {name};\n',admin=True,error=True)
        require('Invalid' in refused['output'] and ('contract' in refused['output'] or 'field' in refused['output']),
            'Incorrect ENGINE SYSTEM dispatch was accepted or failed for a different reason')
    # Initial installation through the actual public Python entry point.
    fresh=h.database.parent/'temporary_fresh.fdb'
    h.isql('temporary_fresh_create',f"CREATE DATABASE '{fresh}' DEFAULT CHARACTER SET UTF8;\nCOMMIT;\nQUIT;\n",embedded=True)
    installed=subprocess.run([sys.executable,'-B',str(INSTALLER.parent.parent/'install_compat.py'),
        '--isql',str(h.runtime/'bin/isql'),'--database',str(fresh)],env=h.env,
        cwd=h.temp,capture_output=True,text=True,timeout=h.timeout)
    (h.work/'temporary_fresh_install.log').write_text(h.redact(installed.stdout+installed.stderr))
    require(installed.returncode==0,'Full initial install failed')
    # Real previous-runtime database, followed by the one-file migration on new runtime.
    require(previous is not None,'--temporary requires --previous-firebird-dir')
    old_runtime=h.work/'previous-runtime'
    shutil.copytree(previous.resolve(),old_runtime,symlinks=False)
    old_db=h.database.parent/'temporary_upgrade.fdb'
    env=h.env.copy()
    env.update(FIREBIRD=str(old_runtime),LD_LIBRARY_PATH=str(old_runtime/'lib'),ISC_USER='SYSDBA')
    old_sql=f"CREATE DATABASE '{old_db}' DEFAULT CHARACTER SET UTF8;\n"+prerequisites()+VISIBILITY_INSTALLER.read_text()+\
        'CREATE TABLE "public"."upgrade_data"("v" INTEGER);\nINSERT INTO "public"."upgrade_data" VALUES(73);\n'\
        'GRANT SELECT ON "public"."upgrade_data" TO USER WIRE_HOST_TEST;\nCOMMIT;\nSET LIST ON;\n'\
        'SELECT MON$ODS_MAJOR AS N,MON$ODS_MINOR AS N FROM SYSTEM.MON$DATABASE;\nROLLBACK;\nQUIT;\n'
    old=subprocess.run([str(old_runtime/'bin/isql'),'-quiet','-bail','-charset','UTF8'],input=old_sql,
        env=env,cwd=h.temp,capture_output=True,text=True,timeout=h.timeout)
    (h.work/'temporary_upgrade_old.log').write_text(h.redact(old.stdout+old.stderr))
    require(old.returncode==0,'Previous runtime fixture failed')
    cfg=dict(config,database=str(old_db))
    old_ids=mapping(cfg,'temporary_upgrade_ids_before')
    upgraded=native(cfg,'temporary_upgrade_new',installer+
        'SELECT MON$ODS_MAJOR AS N,MON$ODS_MINOR AS N FROM SYSTEM.MON$DATABASE;\n'
        'SELECT "v" AS N FROM "public"."upgrade_data";\nROLLBACK;\nQUIT;\n',admin=True)
    new_ids=mapping(cfg,'temporary_upgrade_ids_after')
    require(numbers(upgraded['output'])==numbers(old.stdout)+[73] and
        all(new_ids.get(k)==v for k,v in old_ids.items()),'Runtime upgrade changed ODS/data/identities')
    granted=native(cfg,'temporary_upgrade_grants','SELECT "v" AS N FROM "public"."upgrade_data";\nROLLBACK;\nQUIT;\n')
    require(numbers(granted['output'])==[73],'Upgrade lost old grant')
    h.summary['temporary_install']={'failure_rollback':True,'repeat_refused':True,'old_ids_preserved':True,
        'foreign_collisions':['container','virtual_schema','projection','counter_range'],
        'high_allocator':True,'full_initial_install_exit':installed.returncode,
        'old_engine_sha256':hashlib.sha256((previous/'plugins/libEngine14.so').read_bytes()).hexdigest(),
        'ods_before_after':numbers(old.stdout),'old_exit':old.returncode,'new_exit':upgraded['exit']}



def temporary_client(config, client):
    from pgwire_1c_smoke import Libpq
    report={'checks':[], 'results':0}
    def record(name):
        report['checks'].append(name)
        (Path(config['work'])/'temporary_progress.json').write_text(json.dumps(report,indent=2)+'\n')
    def check(data,rows=None,error='',state=0,oids=None,tag=None,connection=client):
        require(data['sqlstate']==error and connection.pq.PQtransactionStatus(connection.connection)==state and
            (rows is None or data.get('rows')==rows) and
            (oids is None or [c['oid'] for c in data['columns']]==oids) and
            (tag is None or data['tag']==tag), 'Temporary result differs: '+repr(data))
        report['results']+=1
        return data
    def query(sql,connection=client,**expected):
        data=connection.query(sql,True)
        require(len(data)==1,'Unexpected result count: '+sql)
        try:return check(data[0],connection=connection,**expected)
        except RuntimeError as e: raise RuntimeError(sql+' expected '+repr(expected)+': '+str(e)) from e
    def ns(connection=client,state=0):
        return int(query('SELECT pg_catalog.pg_my_temp_schema()',connection=connection,state=state,oids=[20])['rows'][0][0])
    def oid(name,connection=client,state=0):
        r=query("SELECT oid FROM pg_catalog.pg_class WHERE relnamespace=pg_catalog.pg_my_temp_schema() "
                "AND relname='"+name+"'",connection=connection,state=state,oids=[20])['rows']
        require(len(r)==1,'Temporary catalog row missing/ambiguous')
        return int(r[0][0])
    require(ns()==0,'Namespace activated before CREATE')
    query('BEGIN',state=2)
    query('CREATE TEMP TABLE first_temp(_id INTEGER NOT NULL)',state=2,tag='CREATE TABLE')
    require(ns(state=2)>32768,'Namespace allocator overlaps permanent relation IDs')
    query('ROLLBACK')
    require(ns()==0,'Rollback kept first namespace activation')
    query('BEGIN',state=2)
    query('SAVEPOINT s',state=2)
    query('CREATE TEMPORARY TABLE first_temp(v INTEGER)',state=2)
    query('ROLLBACK TO s',state=2)
    require(ns(state=2)==0,'Savepoint kept first namespace activation')
    query('CREATE TEMPORARY TABLE first_temp(v INTEGER)',state=2)
    query('DROP TABLE first_temp',state=2)
    query('RELEASE SAVEPOINT s',state=2)
    query('COMMIT')
    namespace=ns()
    require(namespace>32768,'CREATE+DROP lost namespace activation')
    query('SELECT COUNT(*) FROM pg_catalog.pg_class WHERE relnamespace=pg_catalog.pg_my_temp_schema()',rows=[['0']])
    record('namespace_first_create_rollback_savepoint_create_drop_commit_empty')

    query('CREATE TABLE public.work_items(_id INTEGER, _value VARCHAR(40))')
    query("INSERT INTO public.work_items VALUES(100,'permanent')")
    query('SELECT _id FROM work_items',rows=[['100']])
    query('BEGIN',state=2)
    query('CREATE TEMP TABLE work_items(_id INTEGER NOT NULL, _value VARCHAR(40))',state=2,tag='CREATE TABLE')
    query("INSERT INTO work_items VALUES(1,'temporary')",state=2)
    query('CREATE UNIQUE INDEX _work_id ON work_items(_id)',state=2)
    public_id=oid('work_items',state=2)
    query('COMMIT')
    query('SELECT _id FROM work_items',rows=[['1']])
    query('SELECT _id FROM pg_temp.work_items',rows=[['1']])
    query('SELECT _id FROM public.work_items',rows=[['100']])
    query(f'SELECT _id FROM pg_temp_{namespace}.work_items',rows=[['1']])
    query("SELECT pg_catalog.pg_table_is_visible(oid) FROM pg_catalog.pg_class WHERE relname='work_items' ORDER BY oid",rows=[['f'],['t']],oids=[16])
    query('BEGIN',state=2)
    query('SAVEPOINT s',state=2)
    query('DROP TABLE pg_temp.work_items',state=2)
    query('SELECT _id FROM work_items',rows=[['100']],state=2)
    query('ROLLBACK TO s',state=2)
    query('SELECT _id FROM pg_temp.work_items',rows=[['1']],state=2)
    require(oid('work_items',state=2)==public_id,'DROP rollback changed identity')
    query("INSERT INTO work_items VALUES(1,'duplicate')",error='23505',state=3)
    query('ROLLBACK TO s',state=2)
    query('COMMIT')
    query('CREATE TEMP TABLE IF NOT EXISTS work_items(v INTEGER)',tag='CREATE TABLE')
    query('SELECT _id FROM work_items',rows=[['1']])
    query('DROP TABLE pg_temp.work_items CASCADE')
    query('SELECT _id FROM work_items',rows=[['100']])
    query(f'SELECT pg_catalog.pg_table_is_visible({public_id})',rows=[[None]],oids=[16])
    require(ns()==namespace,'DROP last table removed namespace')
    record('persistent_shadowing_live_visibility_dml_index_drop_savepoint_recovery')

    for prefix in ('TEMP','TEMPORARY','LOCAL TEMP','LOCAL TEMPORARY','GLOBAL TEMP','GLOBAL TEMPORARY'):
        query(f'CREATE {prefix} TABLE spelling(_id INTEGER) WITHOUT OIDS',tag='CREATE TABLE')
        query('INSERT INTO spelling VALUES(7)')
        query('SELECT _id FROM spelling',rows=[['7']])
        query('DROP TABLE spelling')
    query('CREATE TEMP TABLE drows(v INTEGER) ON COMMIT DELETE ROWS')
    query('BEGIN',state=2);query('INSERT INTO drows VALUES(1)',state=2);query('COMMIT')
    query('SELECT COUNT(*) FROM drows',rows=[['0']])
    query('DROP TABLE drows')
    record('all_temp_spellings_default_preserve_explicit_delete_without_oids')

    second=Libpq(config)
    try:
        require(ns(second)==0,'Second attachment inherited namespace')
        query('CREATE TEMP TABLE isolated(a INTEGER)')
        query('INSERT INTO isolated VALUES(11)')
        first_id=oid('isolated')
        query('CREATE TEMP TABLE isolated(b VARCHAR(10))',connection=second)
        query("INSERT INTO isolated VALUES('other')",connection=second)
        second_id=oid('isolated',second)
        second_ns=ns(second)
        require(len({namespace,first_id,second_id,second_ns})==4,'Public temp identities collide')
        query('SELECT a FROM isolated',rows=[['11']])
        query('SELECT b FROM isolated',rows=[['other']],connection=second)
        query(f'SELECT * FROM pg_temp_{second_ns}.isolated',error='42501')
        query('DROP TABLE isolated')
        query('SELECT b FROM isolated',rows=[['other']],connection=second)
    finally:second.close()
    second=Libpq(config)
    try:require(ns(second)==0,'Reconnect retained namespace')
    finally:second.close()
    query('DROP TABLE public.work_items')
    record('two_sessions_isolated_data_shapes_ids_and_reconnect')

    # Exact PG spelling survives native catalog/undo; underscore is also still
    # the native charset introducer when followed by a string literal.
    query('CREATE TEMP TABLE "Смешанная_Table"("Mixed_Поле" INTEGER NOT NULL, _utf8 INTEGER)')
    query('INSERT INTO "Смешанная_Table" VALUES(1,2)')
    query('UPDATE "Смешанная_Table" SET _utf8=3 WHERE "Mixed_Поле"=1')
    query('SELECT "Mixed_Поле", _utf8 FROM "Смешанная_Table"',rows=[['1','3']])
    query('INSERT INTO "Смешанная_Table" VALUES(NULL,4)',error='23000')
    query("SELECT _UTF8 'Привет'",rows=[['Привет']])
    query('SET standard_conforming_strings=off')
    query("SELECT _UTF8 'a\\nb'",rows=[['a\nb']])
    query('SET standard_conforming_strings=on')
    query('DELETE FROM "Смешанная_Table"')
    query('DROP TABLE "Смешанная_Table"')
    for prefix in ('TEMP','TEMPORARY','LOCAL TEMP','LOCAL TEMPORARY','GLOBAL TEMP','GLOBAL TEMPORARY'):
        query(f'CREATE {prefix} TABLE combinations(v INTEGER) ON COMMIT PRESERVE ROWS WITHOUT OIDS')
        query('DROP TABLE combinations')
        query(f'CREATE {prefix} TABLE combinations(v INTEGER) WITHOUT OIDS ON COMMIT DELETE ROWS')
        query('DROP TABLE combinations')
    for sql in ('CREATE TEMP TABLE public.bad(v INTEGER)',
                'CREATE TABLE pg_temp.bad(v INTEGER)',
                'CREATE TEMP TABLE bad(v INTEGER) ON COMMIT DROP',
                'CREATE TEMP TABLE bad(v INTEGER DEFAULT 1)',
                'CREATE TEMP TABLE bad(v INTEGER PRIMARY KEY)',
                'CREATE TEMP TABLE bad(v INTEGER CHECK(v>0))',
                'CREATE TEMP TABLE bad(v INTEGER GENERATED ALWAYS AS IDENTITY)',
                'CREATE TEMP TABLE bad AS SELECT 1 AS v',
                'CREATE TEMP TABLE bad(v INTEGER) WITH OIDS'):
        result=client.query(sql,True)[0]
        require(result['sqlstate'] in ('0A000','42601','42000'), 'Unsupported TEMP accepted or wrong error: '+sql+repr(result))
        require(ns()==namespace,'Rejected DDL changed namespace')
        query("SELECT COUNT(*) FROM pg_catalog.pg_class WHERE relname='bad'",rows=[['0']])
    query('CREATE TEMP TABLE pg_temp.qualified_temp(v INTEGER)')
    query('DROP TABLE pg_temp.qualified_temp')
    query(f'CREATE TEMP TABLE pg_temp_{namespace}.qualified_temp(v INTEGER)')
    query(f'DROP TABLE pg_temp_{namespace}.qualified_temp')
    query('CREATE TEMP TABLE names(v INTEGER)')
    query('CREATE INDEX names ON names(v)',error='23505')
    query('CREATE UNIQUE INDEX temp_index ON names(v)')
    for sql in ('CREATE INDEX expression_idx ON names COMPUTED BY (v+1)',
                'CREATE INDEX partial_idx ON names(v) WHERE v>0',
                'CREATE VIEW public.temp_reference AS SELECT v FROM names'):
        result=client.query(sql,True)[0]
        require(result['sqlstate'] in ('0A000','42601','42000'), 'Unsupported temporary dependency/index accepted: '+repr(result))

    query('CREATE TEMP TABLE temp_index(v INTEGER)',error='23505')
    query('CREATE TEMP TABLE names(v INTEGER)',error='23505')
    query('BEGIN',state=2);query('SAVEPOINT s',state=2)
    query('DROP INDEX temp_index',state=2)
    query('ALTER TABLE names ADD extra INTEGER',state=2)
    query('INSERT INTO names VALUES(5,6)',state=2)
    query('ROLLBACK TO s',state=2)
    query('INSERT INTO names VALUES(5)',state=2)
    query('INSERT INTO names VALUES(5)',error='23505',state=3)
    query('ROLLBACK TO s',state=2);query('COMMIT')
    query('DROP TABLE names CASCADE')
    query('DROP TABLE IF EXISTS pg_temp.missing_temp')
    query('CREATE TABLE public.keep_permanent(v INTEGER)')
    query('DROP TABLE public.keep_permanent CASCADE',error='0A000')
    query('DROP TABLE public.keep_permanent')
    # A zero-stream persistent view has a separate baseline DerivedExprNode
    # assertion (NAT-07, retained reproducer in VALIDATION). This source test
    # uses a real relation and does not count that independent case as PASS.
    query('SET standard_conforming_strings=off')
    query("CREATE VIEW public._stored_underscore AS SELECT _UTF8 X'41' AS _utf8 FROM \"SYSTEM\".\"RDB$DATABASE\"")
    query('SELECT _utf8 FROM public._stored_underscore',rows=[['A']])
    source=query('SELECT CAST("RDB$VIEW_SOURCE" AS VARCHAR(8191)) FROM "SYSTEM"."RDB$RELATIONS" '
        "WHERE \"RDB$SCHEMA_NAME\"='public' AND \"RDB$RELATION_NAME\"='_stored_underscore'")['rows'][0][0]
    require('"_utf8"' in source, 'Underscore name missing native source quotes')
    native(config,'temporary_underscore_reprepare',
        'ALTER VIEW "public"."_stored_underscore" AS '+source+';\nCOMMIT;\n')
    query('SELECT _utf8 FROM public._stored_underscore',rows=[['A']])
    query('DROP VIEW public._stored_underscore')
    query('SET standard_conforming_strings=on')

    record('ddl_boundaries_spelling_names_charset_dml_constraints_index_namespace_and_undo')

    query('CREATE TABLE public.path_items(v INTEGER)')
    query('INSERT INTO public.path_items VALUES(10)')
    query('CREATE TEMP TABLE path_items(v INTEGER)')
    query('INSERT INTO path_items VALUES(20)')
    temp_id=oid('path_items')
    for path, value in (('public, pg_temp',10),('pg_temp, public',20),
                        ('missing, no_usage, public',20),('public',20),
                        (f'public, pg_temp_{namespace}',10)):
        query('SET SEARCH_PATH TO '+path)
        query('SELECT v FROM path_items',rows=[[str(value)]])
        query(f'SELECT pg_catalog.pg_table_is_visible({temp_id})',rows=[['t' if value==20 else 'f']])
    query('SET SEARCH_PATH TO public, pg_temp')
    query('SELECT COUNT(*) FROM pg_class WHERE relname=\'path_items\'',rows=[['4']])
    query('SET SEARCH_PATH TO "$user", public, pg_temp')
    query('SELECT v FROM user_path',rows=[['33']])
    query("SELECT public.user_resolution()=RDB$PG_RELATION_ID('user_path')",rows=[['t']],oids=[16])
    query('SET SEARCH_PATH TO missing, no_usage, public, pg_temp')
    query('SELECT v FROM path_items',rows=[['10']])
    query('SELECT v FROM no_usage.path_items',error='42501')
    query("SELECT pg_catalog.pg_table_is_visible(oid) FROM pg_catalog.pg_class WHERE relname='no_select'",rows=[['t']])
    query('SELECT * FROM public.no_select',error='42501')
    query('SET SEARCH_PATH TO role_path, public, pg_temp')
    for role,value in (('"NONE"',10),('"TEMP_PATH_ROLE"',77),('"NONE"',10)):
        query('SET ROLE '+role)
        query('SELECT v FROM path_items',rows=[[str(value)]])
    query('SET SEARCH_PATH TO pg_temp, public')
    query('CREATE TABLE wrong_target(v INTEGER)',error='0A000')
    query('SET SEARCH_PATH TO public, pg_catalog, "SYSTEM"')
    query('DROP TABLE pg_temp.path_items')
    query('DROP TABLE public.path_items')
    record('shared_resolution_positions_implicit_catalog_exact_user_missing_usage_public_role')

    # Named Extended Parse/Describe are side-effect free, and Bind uses the
    # current PG resolution after DDL or path change with the cache enabled.
    second=Libpq(config)
    try:
        before=allocator(config,'temporary_parse_before')
        check(second.result(second.pq.PQprepare(second.connection,b'only_parse',
            b'CREATE TEMP TABLE parse_only(v INTEGER)',0,None)),connection=second)
        check(second.result(second.pq.PQdescribePrepared(second.connection,b'only_parse')),connection=second)
        require(ns(second)==0 and allocator(config,'temporary_parse_after')==before,'Parse allocated identity')
        # libpq 14 has no PQclosePrepared; raw protocol Close is checked below.
    finally:second.close()
    query('CREATE TABLE public.cache_items(v INTEGER)')
    query('INSERT INTO public.cache_items VALUES(10)')
    check(client.result(client.pq.PQprepare(client.connection,b'temp_cache',b'SELECT v FROM cache_items',0,None)))
    check(client.params('temp_cache',prepared=True),rows=[['10']])
    query('CREATE TEMP TABLE cache_items(v INTEGER)')
    query('INSERT INTO cache_items VALUES(20)')
    cache_id=oid('cache_items')
    check(client.params('temp_cache',prepared=True),rows=[['20']])
    query('SET SEARCH_PATH TO public, pg_temp')
    check(client.params('temp_cache',prepared=True),rows=[['10']])
    query('SET SEARCH_PATH TO public, pg_catalog, "SYSTEM"')
    check(client.params('temp_cache',prepared=True),rows=[['20']])
    query('BEGIN',state=2);query('SAVEPOINT s',state=2)
    query('DROP TABLE pg_temp.cache_items',state=2)
    check(client.params('temp_cache',prepared=True),rows=[['10']],state=2)
    query('ROLLBACK TO s',state=2)
    check(client.params('temp_cache',prepared=True),rows=[['20']],state=2)
    query('COMMIT')
    query('ALTER SESSION RESET')
    require(ns()==namespace and oid('cache_items')==cache_id,'RESET changed definition identities')
    check(client.params('temp_cache',prepared=True),error='26000')
    check(client.result(client.pq.PQprepare(client.connection,b'temp_cache',b'SELECT v FROM cache_items',0,None)))
    check(client.params('temp_cache',prepared=True),rows=[])
    query('DROP TABLE cache_items')
    check(client.params('temp_cache',prepared=True),rows=[['10']])
    query('DROP TABLE public.cache_items')
    record('parse_describe_close_no_effects_named_bind_reprepare_ddl_path_rollback_reset')

    query('BEGIN',state=2)
    query('SELECT COUNT(*) FROM "SYSTEM"."MON$LOCAL_TEMPORARY_TABLES"',rows=[['0']],state=2)
    query('CREATE TEMP TABLE live_rows(v INTEGER)',state=2)
    live_id=oid('live_rows',state=2)
    query('SELECT COUNT(*) FROM "SYSTEM"."MON$LOCAL_TEMPORARY_TABLES"',rows=[['0']],state=2)
    query('DROP TABLE live_rows',state=2)
    query('CREATE TEMP TABLE live_rows(v INTEGER)',state=2)
    require(oid('live_rows',state=2)!=live_id,'Recreate reused public identity')
    query(f'SELECT pg_catalog.pg_table_is_visible({live_id})',rows=[[None]],state=2)
    query('ROLLBACK')
    query('CREATE TEMP TABLE binary_meta(v INTEGER)')
    binary_id=oid('binary_meta')
    check(client.params("SELECT oid,relname,relnamespace,relkind FROM pg_catalog.pg_class WHERE relname='binary_meta'",
        result_format=1),oids=[20,1043,20,1043],rows=[[binary_id.to_bytes(8,'big').hex(),b'binary_meta'.hex(),
            namespace.to_bytes(8,'big').hex(),b'r'.hex()]])
    check(client.params(f'SELECT pg_catalog.pg_table_is_visible({binary_id}),pg_catalog.pg_table_is_visible(0)',
        result_format=1),oids=[16,16],rows=[['01',None]])
    query("DELETE FROM pg_catalog.pg_class WHERE relname='binary_meta'",error='42000')
    query('DROP TABLE binary_meta')
    batch=client.query('BEGIN; SAVEPOINT s; CREATE TEMP TABLE batch_temp(v INTEGER); INSERT INTO batch_temp VALUES(8); '
        'SELECT 1/0; DROP TABLE batch_temp;',True)
    require(len(batch)==5 and batch[-1]['sqlstate']=='22012' and client.pq.PQtransactionStatus(client.connection)==3,
        'Simple batch did not stop at first error')
    query('ROLLBACK TO s',state=2)
    query("SELECT COUNT(*) FROM pg_catalog.pg_class WHERE relname='batch_temp'",rows=[['0']],state=2)
    query('ROLLBACK')
    record('live_catalog_after_mon_recreate_binary_native_types_null_readonly_batch_recovery')
    return report


def temporary_api(h):
    source=Path(__file__).with_name('pgwire_temporary_api.cpp')
    binary=h.work/'temporary_api'
    built=subprocess.run(['g++','-std=c++17','-g','-O0',str(source),'-I'+str(h.runtime/'include'),
        '-L'+str(h.runtime/'lib'),'-Wl,-rpath,'+str(h.runtime/'lib'),'-lfbclient','-o',str(binary)],
        env=h.env,capture_output=True,text=True,timeout=h.timeout)
    (h.work/'temporary_api_compile.log').write_text(built.stdout+built.stderr)
    require(built.returncode==0,'Temporary OO probe compilation failed')
    run=subprocess.run([str(binary),str(h.database.parent/'temporary_api.fdb')],env=h.env,cwd=h.temp,
        capture_output=True,text=True,timeout=h.timeout)
    (h.work/'temporary_api.log').write_text(h.redact(run.stdout+run.stderr))
    require(run.returncode==0,'Temporary OO probe failed')
    return json.loads(run.stdout)


def temporary_protocols(h):
    from pgwire_extended_query_smoke import ExtendedClient, parse, describe, close, bind, execute
    from pgwire_startup_smoke import packet
    config=config_for(h)
    raw=ExtendedClient(h).login()
    try:
        before=allocator(config,'temporary_raw_parse_before')
        raw.exchange(parse('ddl','CREATE TEMP TABLE parse_closed(v INTEGER)')+
            describe(b'S','ddl')+close(b'S','ddl')+packet(b'S'),b'1tn3Z')
        raw.exchange(packet(b'Q',b'SELECT pg_catalog.pg_my_temp_schema()\0'),b'TDCZ',rows=[['0']],tag='SELECT 1')
        require(allocator(config,'temporary_raw_parse_after')==before,'Protocol Parse/Describe/Close allocated IDs')
        raw.exchange(packet(b'Q',b'CREATE TEMP TABLE portal_copy(v INTEGER)\0'),b'CZ',tag='CREATE TABLE')
        raw.exchange(packet(b'Q',b'BEGIN\0'),b'CZ',tag='BEGIN',state='T')
        raw.exchange(parse('rows','SELECT "PUBLIC_ID" FROM \"PGWIRE_COMPAT\".\"TEMP_ROWS_V1\"')+
            bind('paused','rows')+execute('paused',1)+packet(b'H'),b'12Ds',end=b's')
        raw.exchange(packet(b'Q',b'DROP TABLE portal_copy\0'),b'CZ',tag='DROP TABLE',state='T')
        raw.exchange(execute('paused')+packet(b'S'),b'DCZ',tag='SELECT 2',state='T')
        raw.exchange(packet(b'Q',b'ROLLBACK\0'),b'CZ',tag='ROLLBACK')
    finally: raw.close()
    return {'parse_describe_close_no_effects':True,'paused_provider_portal_after_drop':True}
