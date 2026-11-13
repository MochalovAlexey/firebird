"""APP-16 synthetic type acceptance in the existing isolated 1c harness."""
import json
import hashlib
import re
import ctypes as C
import shutil
import subprocess
import sys
from pathlib import Path
from pgwire_namespace_checks import native, config_for
from pgwire_visibility_checks import prerequisites, VISIBILITY_INSTALLER
from pgwire_temporary_checks import INSTALLER as TEMP_INSTALLER
from wire_host_smoke import require
from pgwire_namespace_checks import mapping, numbers


def types_fixture(h, previous=None):
    config = config_for(h)
    native(config, 'types_admin', f"CREATE OR ALTER USER SYSDBA PASSWORD '{h.password}';\n"
           'GRANT CREATE SCHEMA TO USER WIRE_HOST_TEST;\nCOMMIT;\n', admin=True)
    native(config, 'types_prerequisites', prerequisites()+VISIBILITY_INSTALLER.read_text()+
           TEMP_INSTALLER.read_text()+
           'GRANT CREATE TABLE ON SCHEMA "public" TO USER WIRE_HOST_TEST;\n'
           'GRANT CREATE VIEW ON SCHEMA "public" TO USER WIRE_HOST_TEST;\nCOMMIT;\n', admin=True)
    native(config,'types_function_grant',
           'GRANT CREATE FUNCTION ON SCHEMA "public" TO USER WIRE_HOST_TEST;\nCOMMIT;\n',admin=True)
    installer=TEMP_INSTALLER.with_name('install_types_and_expressions.sql').read_text()
    before=mapping(config,'types_ids_before')
    failed=native(config,'types_install_failure',installer.replace('\nCOMMIT;',
        '\nSELECT 1/0 FROM SYSTEM.RDB$DATABASE;\nCOMMIT;'),admin=True,error=True)
    require(failed['sqlstates']==['22012'],'Unexpected installer failure')
    probe="SELECT COUNT(*) AS N FROM SYSTEM.RDB$FUNCTIONS WHERE RDB$SCHEMA_NAME='public' "\
          "AND RDB$FUNCTION_NAME IN ('binrowver','bool_or');\nROLLBACK;\nQUIT;\n"
    require(numbers(native(config,'types_failure_atomic',probe,admin=True)['output'])==[0],
            'Failed installer retained functions')
    for name in ('binrowver','bool_or'):
        db=h.database.with_name('types_collision_'+name+'.fdb')
        shutil.copy2(h.database,db)
        cfg=dict(config,database=str(db),native_port=None)
        native(cfg,'types_collision_create_'+name,
               f'SET TERM ^;\nCREATE FUNCTION "public"."{name}" RETURNS INTEGER AS BEGIN RETURN 73; END^\nSET TERM ;^\nCOMMIT;\n',admin=True)
        native(cfg,'types_collision_'+name,installer,admin=True,error=True)
        require(numbers(native(cfg,'types_collision_preserved_'+name,
            f'SELECT "public"."{name}"() AS N FROM SYSTEM.RDB$DATABASE;\n'+probe,admin=True)['output'])==[73,1],
            'Collision overwrote foreign function or left partial install')
    native(config,'types_install',installer,admin=True)
    native(config,'types_repeat',installer,admin=True,error=True)
    require(mapping(config,'types_ids_after')==before,'APP-16 installation changed namespace IDs')
    # Exercise the public fresh installer, including all eight dependency-ordered scripts.
    fresh=h.database.with_name('types_fresh.fdb')
    h.isql('types_fresh_create',f"CREATE DATABASE '{fresh}' DEFAULT CHARACTER SET UTF8;\nCOMMIT;\nQUIT;\n",embedded=True)
    def install(db,upgrade=False):
        command=[sys.executable,'-B',str(TEMP_INSTALLER.parent.parent/'install_compat.py'),
                 '--isql',str(h.runtime/'bin/isql'),'--database',str(db)]
        if upgrade: command.append('--upgrade-from-app15')
        result=subprocess.run(command,env=h.env,cwd=h.temp,capture_output=True,text=True,timeout=h.timeout)
        (h.work/('types_upgrade_install.log' if upgrade else 'types_fresh_install.log')).write_text(
            h.redact(result.stdout+result.stderr))
        require(result.returncode==0,'Public APP-16 installer failed')
    install(fresh)
    report={'fresh':True,'rollback':True,'collision':True,'repeat_refused':True,'ids_preserved':True}
    if previous:
        old_runtime=h.work/'previous-runtime'
        shutil.copytree(previous.resolve(),old_runtime,symlinks=False)
        (old_runtime/'firebird.conf').write_text(
            f'ServerMode = Super\nProviders = Engine14\nDatabaseAccess = Restrict {h.database.parent}\n'
            f'SecurityDatabase = {old_runtime}/security6.fdb\n')
        (old_runtime/'databases.conf').write_text('')
        old_db=h.database.with_name('types_upgrade.fdb')
        env=h.env.copy()
        env.update(FIREBIRD=str(old_runtime),LD_LIBRARY_PATH=str(old_runtime/'lib'),ISC_USER='SYSDBA')
        sql=f"CREATE DATABASE '{old_db}' DEFAULT CHARACTER SET UTF8;\n"+prerequisites()+\
            VISIBILITY_INSTALLER.read_text()+TEMP_INSTALLER.read_text()+\
            'CREATE TABLE "public"."upgrade_data"("v" INTEGER);\nINSERT INTO "public"."upgrade_data" VALUES(73);\n'\
            'GRANT SELECT ON "public"."upgrade_data" TO USER WIRE_HOST_TEST;\nCOMMIT;\nQUIT;\n'
        result=subprocess.run([str(old_runtime/'bin/isql'),'-quiet','-bail','-charset','UTF8'],input=sql,
            env=env,cwd=h.temp,capture_output=True,text=True,timeout=h.timeout)
        (h.work/'types_upgrade_old.log').write_text(h.redact(result.stdout+result.stderr))
        require(result.returncode==0,'Previous APP-15 fixture failed')
        cfg=dict(config,database=str(old_db),native_port=None)
        old_ids=mapping(cfg,'types_upgrade_ids_before')
        native(cfg,'types_upgrade_refuse_before_ods',installer,admin=True,error=True)
        result=subprocess.run([str(h.runtime/'bin/gfix'),'-upgrade',str(old_db)],
                              env=dict(h.env,ISC_USER='SYSDBA',ISC_PASSWORD=h.password),cwd=h.temp,
                              capture_output=True,text=True,timeout=h.timeout)
        (h.work/'types_upgrade_gfix.log').write_text(h.redact(result.stdout+result.stderr))
        require(result.returncode==0,'Native gfix upgrade failed')
        install(old_db,True)
        require(mapping(cfg,'types_upgrade_ids_after')==old_ids,'Upgrade changed old namespace IDs')
        data=native(cfg,'types_upgrade_data',
            'SELECT "v" AS N FROM "public"."upgrade_data";\n'
            'SELECT MON$ODS_MAJOR AS N,MON$ODS_MINOR AS N FROM SYSTEM.MON$DATABASE;\n'
            'SELECT COUNT(*) AS N FROM SYSTEM.RDB$TYPES WHERE RDB$FIELD_NAME=\'RDB$CHARACTER_SET_NAME\' '
            'AND RDB$TYPE IN (70,71);\nROLLBACK;\nQUIT;\n')
        require(numbers(data['output'])==[73,14,1,2],'Upgrade lost data/grant/charset metadata')
        result=subprocess.run([str(old_runtime/'bin/isql'),'-quiet','-bail',str(old_db)],input='QUIT;\n',
                              env=env,cwd=h.temp,capture_output=True,text=True,timeout=h.timeout)
        (h.work/'types_old_runtime_refusal.log').write_text(h.redact(result.stdout+result.stderr))
        require(result.returncode!=0 and 'unsupported on-disk structure' in (result.stdout+result.stderr).lower(),
                'Previous runtime did not explicitly reject new capabilities')
        report['app15_upgrade']=True
        report['previous_engine_sha256']=hashlib.sha256((previous/'plugins/libEngine14.so').read_bytes()).hexdigest()
    h.summary['types_install']=report


def types_client(config, client):
    results = []
    def check(label, sql, rows=None, error='', tags=None):
        response = client.query(sql, True)
        result = response[-1]
        results.append({'label': label, 'result': result})
        (Path(config['work'])/'types-progress.json').write_text(json.dumps(results, ensure_ascii=False, indent=2)+'\n')
        require(result['status'] == (7 if error else (2 if result['columns'] else 1)) and
                result['sqlstate'] == error and (rows is None or result['rows'] == rows),
                f'{label}: {response}')
        if not error:
            require(all(r['status'] in (1,2) and not r['sqlstate'] for r in response),
                    f'{label}: script failed: {response}')
        if tags is not None:
            require([r['tag'] for r in response] == tags, f'{label}: command tags: {response}')
        return result
    check('bytea_literal', r"SELECT '\x0100'::bytea", [[r'\x0100']])
    check('bytea_length_sensitive', r"SELECT '\x01'::bytea = '\x0100'::bytea", [['f']])
    check('bytea_literal_octal', r"SELECT '\001\000'::bytea", [[r'\x0100']])
    for mode, prefix in [('on','\\'),('off','\\\\')]:
        check('bytea_strings_'+mode, 'SET standard_conforming_strings='+mode)
        check('bytea_hex_layers', "SELECT '"+prefix+"x0100'::bytea", [[r'\x0100']])
        check('bytea_escape_layers', "SELECT '"+prefix+"001"+prefix+"000'::bytea", [[r'\x0100']])
        for value,state in [('x0','22023'),('xgg','22023'),('400','22P02'),('q','22P02')]:
            check('bytea_invalid_layers',"SELECT '"+prefix+value+"'::bytea",error=state)
        result=client.params('SELECT $1::bytea',[r'\001\000'],[17])
        require(result['rows']==[[r'\x0100']],'Bind incorrectly used SQL string lexer')
    check('bytea_restore_strings','SET standard_conforming_strings=on')
    check('bytea_table', 'CREATE TABLE typed_binary(id integer, b bytea)')
    for i, value in enumerate((None, b'', b'\0', b'\1', b'\1\0', b'\1\0\0', b'\xff')):
        result = client.params('INSERT INTO typed_binary VALUES($1,$2)', [str(i), value], [23,17], [0,1])
        results.append({'label': f'binary_insert_{i}', 'result': result})
        require(not result['sqlstate'], str(result))
    check('bytea_unique', 'CREATE UNIQUE INDEX typed_binary_idx ON typed_binary(b)')
    check('bytea_lookup', r"SELECT id FROM typed_binary WHERE b='\x0100'::bytea", [['4']])
    check('bytea_natural_lookup', r"SELECT id FROM typed_binary WHERE b='\x0100'::bytea PLAN (typed_binary NATURAL)", [['4']])
    check('bytea_index_lookup', r"SELECT id FROM typed_binary WHERE b='\x0100'::bytea PLAN (typed_binary INDEX (typed_binary_idx))", [['4']])
    check('bytea_order', 'SELECT id FROM typed_binary ORDER BY b NULLS FIRST', [[str(i)] for i in range(7)])
    check('bytea_distinct', 'SELECT DISTINCT b FROM typed_binary ORDER BY b NULLS FIRST',
          [[v] for v in (None, r'\x', r'\x00', r'\x01', r'\x0100', r'\x010000', r'\xff')])
    check('bytea_duplicate', r"INSERT INTO typed_binary VALUES(99,'\x0100'::bytea)", error='23505')
    for expression in ['b','CASE WHEN TRUE THEN b ELSE NULL END','COALESCE(NULL,b)']:
        result=check('bytea_expression_identity','SELECT '+expression+' FROM typed_binary WHERE id=4',[[r'\x0100']])
        require(result['columns'][0]['oid']==17,'Bytea expression lost semantic identity')
    result=check('bytea_union_identity','SELECT b FROM typed_binary WHERE id=4 UNION ALL '
                 'SELECT b FROM typed_binary WHERE id=4',[[r'\x0100'],[r'\x0100']])
    require(result['columns'][0]['oid']==17,'Bytea UNION lost semantic identity')
    check('large_table', 'CREATE TABLE typed_payload(id integer, b bytea)')
    for size in (65533, 65534, 65537, 150001):
        value = bytes(range(256))*(size//256) + bytes(range(size%256))
        result = client.params('INSERT INTO typed_payload VALUES($1,$2)', [str(size),value], [23,17], [0,1])
        require(not result['sqlstate'], str(result))
        result = client.params('SELECT b FROM typed_payload WHERE id=$1', [str(size)], [23], result_format=1)
        require(result['rows'] == [[value.hex()]], f'large Bind {size}: bytes differ')
        results.append({'label': f'large_bind_{size}', 'bytes_equal': True})
    check('long_compare_table','CREATE TABLE long_compare(id integer,b bytea)')
    for i,value in enumerate([b'\0'*65536+b'\1',b'\0'*65536+b'\1\0',b'\0'*65536+b'\2',b'\0'*65536+b'\1']):
        result=client.params('INSERT INTO long_compare VALUES($1,$2)',[str(i),value],[23,17],[0,1])
        require(not result['sqlstate'],'Long comparison fixture')
    check('long_compare_order','SELECT id FROM long_compare ORDER BY b,id',[['0'],['3'],['1'],['2']])
    check('long_compare_desc','SELECT id FROM long_compare ORDER BY b DESC,id',[['2'],['1'],['0'],['3']])
    check('long_compare_distinct','SELECT COUNT(DISTINCT b) FROM long_compare',[['3']])
    check('long_compare_join','SELECT COUNT(*) FROM long_compare a JOIN long_compare b ON a.b=b.b',[['6']])
    check('reg_public', "SELECT 'public'::regnamespace", [['public']])
    check('reg_numeric', "SELECT '0'::regnamespace,'4294967295'::regnamespace", [['-', '4294967295']])
    check('reg_missing', "SELECT 'app16_missing'::regnamespace", error='3F000')
    check('reg_table', 'CREATE TABLE typed_ns(n regnamespace)')
    require(not client.params('INSERT INTO typed_ns VALUES($1)', ['public'], [4089])['sqlstate'], 'reg Bind')
    check('reg_stored', 'SELECT n FROM typed_ns', [['public']])
    for name, oid in [('mchar', 0x00fb0001), ('mvarchar', 0x00fb0002)]:
        result = check(name+'_literal', f"SELECT 'Ё😀'::public.{name}", [['Ё😀']])
        require(result['columns'][0]['oid'] == oid, str(result))
        check(name+'_cast', f"SELECT 'a😀b'::{name}(2)", [['a😀']])
        check(name+'_ops', f"SELECT 'a'::{name}='A'::{name},'a'::{name}='a '::{name},"
              f"'é'::{name}='e'::{name},'Ё'::{name}='ё'::{name},"
              f"'a'::{name} LIKE 'A'::{name},substr('a😀b'::{name},2,1)",
              [['t','t','f','t','t','😀']])
        check(name+'_table', f'CREATE TABLE typed_{name}(v {name}(2))')
        result = client.params(f'INSERT INTO typed_{name} VALUES($1)', ['Ё😀'], [oid])
        require(not result['sqlstate'], str(result))
        result = client.params(f'SELECT v FROM typed_{name}', result_format=1)
        require(result['rows'] == [['Ё😀'.encode('utf-16le').hex()]], str(result))
        result = client.params(f'SELECT $1::{name}', ['Ё😀'.encode('utf-16le')], [oid], [1])
        require(result['rows'] == [['Ё😀']], str(result))
    for value in (0,1,255,256,65535,65536,16777215,16777216,2147483647,-1,-256,-2147483648):
        check('binrowver_'+str(value), f'SELECT binrowver({value})',
              [[r'\x00000000'+(value & 0xffffffff).to_bytes(4,'big').hex()]])
    check('binrowver_null', 'SELECT binrowver(NULL)', [[None]])
    check('bool_or', 'SELECT bool_or(v) FROM (SELECT CAST(NULL AS BOOLEAN) v UNION ALL SELECT FALSE UNION ALL SELECT TRUE)', [['t']])
    check('bool_or_null', 'SELECT bool_or(CAST(NULL AS BOOLEAN))', [[None]])
    string_identity(client, check)
    namespace_lifecycle(client, check)
    string_operations(client, check)
    if config.get('types_oracle_details'):
        oracle=json.loads(Path(config['types_oracle_details']).read_text())
        check('oracle_bare_storage','CREATE TABLE bare_mchar(c mchar)')
        check('oracle_bare_value',"INSERT INTO bare_mchar VALUES('abc'::mchar(3))")
        count=0
        for sql,expected in oracle['cases'].items():
            if not sql.startswith('SELECT '): continue
            result=check('oracle_expression_'+str(count),sql,expected[-1]['rows'])
            def signature(columns, oracle_oids=False):
                mapping={oracle['oids']['mchar']:0x00fb0001,oracle['oids']['mvarchar']:0x00fb0002} if oracle_oids else {}
                return [(mapping.get(c['oid'],c['oid']),c['mod'],c['size']) for c in columns]
            require(signature(result['columns'])==signature(expected[-1]['columns'],True),'Oracle expression metadata')
            count+=1
        results.append({'label':'oracle_expression_matrix','cases':count,
                        'sha256':hashlib.sha256(Path(config['types_oracle_details']).read_bytes()).hexdigest()})
    binary_operations(client, check)
    sort_and_key_boundaries(client, check)
    stored_identity(config, client, check)
    original_application(config, client, check)
    from pgwire_profile_type_checks import profile_type_families
    results.append({'label':'recorded_type_families',
                    'results':profile_type_families(client,config['capture_dir'],firebird=True)})
    concurrent_types(config, client, check)
    return {'results': results, 'scope': 'synthetic APP-16 type and recorded-operation checks; native API/restart reported separately'}


def namespace_lifecycle(client, check):
    for name in ('Mixed Name', 'select', 'integer', 'a$b', 'A"quote'):
        identifier = '"'+name.replace('"', '""')+'"'
        check('namespace_create', 'CREATE SCHEMA '+identifier)
        for fmt in (0, 1):
            result = client.params('SELECT $1::regnamespace', [identifier], [4089], result_format=fmt)
            require(not result['sqlstate'] and result['columns'][0]['oid'] == 4089 and
                    result['columns'][0]['size'] == 4, 'regnamespace metadata: '+str(result))
            if fmt == 0:
                require(result['rows'] == [[identifier]], 'regnamespace quoting: '+str(result))
            else:
                number = bytes.fromhex(result['rows'][0][0])
                require(len(number) == 4, 'regnamespace binary size')
                result = client.params('SELECT $1::regnamespace', [number], [4089], [1])
                require(result['rows'] == [[identifier]], 'regnamespace binary input: '+str(result))
    for value, state in [('1a','3F000'), ('','42602'), ('Mixed Name','42602'),
                         ('4294967296','22003'), ('-1','3F000'), ('pg_temp','3F000')]:
        check('namespace_bad_'+value, "SELECT '"+value+"'::regnamespace", error=state)
    check('namespace_signed', 'SELECT (-1)::integer::regnamespace,(-1)::smallint::regnamespace',
          [['4294967295','4294967295']])
    check('namespace_bigint_overflow','SELECT (-1)::bigint::regnamespace',error='22003')
    sql = b'SELECT \'"Mixed Name"\'::regnamespace'
    require(not client.result(client.pq.PQprepare(client.connection,b'namespace_live',sql,0,None))['sqlstate'],
            'namespace Prepare failed')
    check('namespace_drop','DROP SCHEMA "Mixed Name"')
    require(client.params('namespace_live',prepared=True)['sqlstate']=='3F000','Prepared namespace cached old ID')
    check('namespace_recreate','CREATE SCHEMA "Mixed Name"')
    require(client.params('namespace_live',prepared=True)['rows']==[['"Mixed Name"']], 'Prepared namespace recreation')
    check('namespace_temp_table','CREATE TEMP TABLE namespace_temp(b bytea)')
    name = check('namespace_temp_name', 'SELECT nspname FROM pg_catalog.pg_namespace '
                 'WHERE oid=pg_catalog.pg_my_temp_schema()')['rows'][0][0]
    check('namespace_live_temp', "SELECT '"+name+"'::regnamespace", [[name]])
    for expr in ['n','CASE WHEN TRUE THEN n ELSE NULL END','COALESCE(NULL,n)',
                 "CAST('public' AS regnamespace)"]:
        result=check('namespace_identity','SELECT '+expr+' FROM typed_ns',[['public']])
        require(result['columns'][0]['oid']==4089, 'Lost namespace identity: '+str(result))
    result=check('namespace_union','SELECT n FROM typed_ns UNION ALL SELECT n FROM typed_ns',
                 [['public'],['public']])
    require(result['columns'][0]['oid']==4089, 'Lost namespace UNION identity')


def string_operations(client, check):
    check('mchar_bounded_like', "SELECT 'a'::mchar(3) LIKE 'a','a'::mchar(3) LIKE 'a  ',"
          "'a'::mchar(3) LIKE 'a%','a'::mchar(3) LIKE 'a ','a'::mchar(3) LIKE 'a_'", [['t','t','t','f','f']])
    for temporary in ('','TEMP '):
        for name, oid in [('mchar',0x00fb0001),('mvarchar',0x00fb0002)]:
            table=('temp_' if temporary else '')+'indexed_'+name
            check(table+'_create', f'CREATE {temporary}TABLE {table}(id integer,v {name}(4))')
            for i,value in enumerate([None,'','😀','a','e','é','Ёж']):
                result=client.params(f'INSERT INTO {table} VALUES($1,$2)',[str(i),value],[23,oid])
                require(not result['sqlstate'],'String assignment: '+str(result))
            check(table+'_index',f'CREATE UNIQUE INDEX {table}_i ON {table}(v)')
            check(table+'_descending',f'CREATE DESCENDING INDEX {table}_d ON {table}(v)')
            check(table+'_order',f'SELECT id FROM {table} ORDER BY v NULLS FIRST',[[str(i)] for i in range(7)])
            for plan in ('NATURAL',f'INDEX ({table}_i)',f'INDEX ({table}_d)'):
                check(table+'_mixed', f"SELECT id FROM {table} WHERE v='A' AND 'A'=v PLAN ({table} {plan})",[['3']])
                check(table+'_unicode', f"SELECT id FROM {table} WHERE v='ёж' PLAN ({table} {plan})",[['6']])
            for value in ['A','a ','a  ']:
                result=client.params(f'INSERT INTO {table} VALUES(99,$1)',[value],[oid])
                require(result['sqlstate']=='23505','String unique equality: '+str(result))
            result=client.params(f'INSERT INTO {table} VALUES(98,$1)',['12345'],[oid])
            require(result['sqlstate']==('' if name=='mchar' else 'XX000'),'String assignment length: '+str(result))
            check(table+'_transaction','BEGIN')
            check(table+'_update',f"UPDATE {table} SET v='z' WHERE id=3")
            check(table+'_delete',f'DELETE FROM {table} WHERE id=6')
            check(table+'_rollback','ROLLBACK')
            check(table+'_restore',f"SELECT id FROM {table} WHERE v IN ('A','ёж') ORDER BY id",[['3'],['6']])
    for name in ('mchar','mvarchar'):
        check(name+'_null_aggregate',f'SELECT MIN(v),MAX(v),COUNT(v) FROM indexed_{name} WHERE id<0',[[None,None,'0']])
        table='bare_sort_'+name
        check(table+'_create',f'CREATE TABLE {table}(v {name})')
        for value in ('NULL','NULL',"'a'","'A'","'b'"):
            check(table+'_insert',f'INSERT INTO {table} VALUES({value})')
        check(table+'_groups',f'SELECT COUNT(*) FROM {table} GROUP BY v ORDER BY v NULLS FIRST',
              [['2'],['2'],['1']])
        check(table+'_distinct',f'SELECT COUNT(DISTINCT v) FROM {table}',[['2']])
    check('bool_or_empty','SELECT BOOL_OR(id>0) FROM typed_binary WHERE id<0',[[None]])
    check('bool_or_false_null','SELECT BOOL_OR(v) FROM (SELECT FALSE v UNION ALL SELECT CAST(NULL AS BOOLEAN))',[['f']])


def string_identity(client, check):
    def typed(label, sql, rows, oids, mods):
        result=check(label,sql,rows)
        require([c['oid'] for c in result['columns']]==oids and
                [c['mod'] for c in result['columns']]==mods, 'Type identity: '+str(result))
    for name, oid in [('mchar',0x00fb0001),('mvarchar',0x00fb0002)]:
        for suffix, expression, mod in [
            ('column','v',2), ('cast',f'v::{name}',-1),
            ('case','CASE WHEN TRUE THEN v ELSE v END',2), ('coalesce','COALESCE(v,v)',2),
            ('substr','SUBSTR(v,2,1)',-1),
        ]:
            expected='😀 ' if name=='mchar' else '😀'
            typed(name+'_identity_'+suffix,f'SELECT {expression} FROM typed_{name}',
                  [[expected if suffix=='substr' else 'Ё😀']],[oid],[mod])
        typed(name+'_identity_union',f'SELECT v FROM typed_{name} UNION ALL SELECT v FROM typed_{name}',
              [['Ё😀'],['Ё😀']],[oid],[2])
        typed(name+'_identity_aggregate',f'SELECT MIN(v),MAX(v) FROM typed_{name}',[['Ё😀','Ё😀']],
              [oid,oid],[-1,-1])
        result=client.params(f'SELECT $1::{name}(2)',['a😀b'],[25])
        require(result['rows']==[['a😀']] and not result['sqlstate'],'Explicit typed Bind cast: '+str(result))
    check('mchar_value_storage','CREATE TABLE typed_mchar_value(v mchar)')
    check('mchar_value_insert',"INSERT INTO typed_mchar_value VALUES('abc'::mchar(3))")
    typed('mchar_value_substr','SELECT SUBSTR(v,1,1) FROM typed_mchar_value',[['a  ']],[0x00fb0001],[-1])
    typed('mchar_bare_typmod',"SELECT SUBSTR('abc'::mchar(3)::mchar,1,1)",[['a  ']],[0x00fb0001],[-1])
    typed('mchar_concat',"SELECT 'a'::mchar(3)||'b'::mchar(3)",[['a  b  ']],[0x00fb0001],[-1])
    typed('mchar_concat_unbounded',"SELECT SUBSTR(('a'::mchar(3)||'b'::mchar(3)),1,1)",[['a']],[0x00fb0001],[-1])
    typed('mixed_concat',"SELECT 'a'::mchar(3)||'b '::mvarchar(3)",[['a  b ']],[0x00fb0002],[-1])
    typed('mchar_substr_blank',"SELECT SUBSTR('abc'::mchar(3),1,0)",[['   ']],[0x00fb0001],[-1])


def binary_operations(client, check):
    check('binary_duplicates_table', 'CREATE TABLE binary_duplicates(id integer,b bytea)')
    for i, value in enumerate([b'\1\0',b'\1',b'\1\0',b'',None,b'\0\0',b'\xff',b'\1\0\0']):
        result=client.params('INSERT INTO binary_duplicates VALUES($1,$2)',[str(i),value],[23,17],[0,1])
        require(not result['sqlstate'], str(result))
    check('binary_groups', 'SELECT b,COUNT(*) FROM binary_duplicates GROUP BY b ORDER BY b NULLS FIRST',
          [[None,'1'],[r'\x','1'],[r'\x0000','1'],[r'\x01','1'],[r'\x0100','2'],[r'\x010000','1'],[r'\xff','1']])
    check('binary_join', 'SELECT COUNT(*) FROM binary_duplicates a JOIN binary_duplicates b ON a.b=b.b', [['9']])
    check('binary_minmax', 'SELECT MIN(b),MAX(b),COUNT(DISTINCT b) FROM binary_duplicates', [[r'\x',r'\xff','6']])
    for prefix, temporary in [('permanent_keys',''),('temp_keys','TEMP ' )]:
        check(prefix+'_table', f'CREATE {temporary}TABLE {prefix}(id integer,b bytea,c bytea)')
        for i, value in enumerate([b'',b'\0',b'\1',b'\1\0',b'\1\0\0',b'\xff']):
            result=client.params(f'INSERT INTO {prefix} VALUES($1,$2,$3)',[str(i),value,value],[23,17,17],[0,1,1])
            require(not result['sqlstate'],str(result))
        for name, declaration in [('regular','INDEX'),('descending','DESCENDING INDEX'),
                                  ('unique','UNIQUE INDEX'),('composite','UNIQUE DESCENDING INDEX')]:
            columns='b,c,id' if name=='composite' else 'b'
            check(prefix+'_'+name, f'CREATE {declaration} {prefix}_{name} ON {prefix}({columns})')
        for name in ('regular','descending','unique','composite'):
            check(prefix+'_'+name+'_lookup', f"SELECT id FROM {prefix} WHERE b='\\x0100'::bytea "
                  f"PLAN ({prefix} INDEX ({prefix}_{name}))", [['3']])
            check(prefix+'_'+name+'_empty', f"SELECT id FROM {prefix} WHERE b='\\x'::bytea "
                  f"PLAN ({prefix} INDEX ({prefix}_{name}))", [['0']])
        check(prefix+'_range', f"SELECT id FROM {prefix} WHERE b>='\\x01'::bytea AND b<'\\xff'::bytea "
              f'PLAN ({prefix} INDEX ({prefix}_regular)) ORDER BY b', [['2'],['3'],['4']])
        check(prefix+'_begin','BEGIN')
        check(prefix+'_update', f"UPDATE {prefix} SET b='\\x010001'::bytea WHERE id=3")
        check(prefix+'_delete', f'DELETE FROM {prefix} WHERE id=4')
        check(prefix+'_rollback','ROLLBACK')
        check(prefix+'_restored', f"SELECT id FROM {prefix} WHERE b='\\x0100'::bytea", [['3']])
        check(prefix+'_drop',f'DROP INDEX {prefix}_unique')
        check(prefix+'_rebuild',f'CREATE UNIQUE INDEX {prefix}_unique ON {prefix}(b)')
        check(prefix+'_overflow_begin','BEGIN')
        check(prefix+'_overflow_savepoint','SAVEPOINT size_sp')
        result=client.params(f'INSERT INTO {prefix} VALUES(99,$1,$2)',[b'\1'*20000,b''],[17,17],[1,1])
        require(result['sqlstate'] == '54000', 'actual B-tree key overflow: '+str(result))
        check(prefix+'_overflow_rollback','ROLLBACK TO size_sp')
        check(prefix+'_overflow_atomic',f'SELECT COUNT(*) FROM {prefix}', [['6']])
        check(prefix+'_overflow_end','ROLLBACK')


def sort_and_key_boundaries(client, check):
    # >128 KiB of sort records forces native multiple runs, including equal
    # content in separately allocated BLOBs and secondary descending keys.
    check('sort_runs_table','CREATE TABLE sort_runs(id integer,b bytea,c mchar(8))')
    check('sort_runs_fill', "EXECUTE BLOCK AS DECLARE i INTEGER=0; BEGIN WHILE (i<12000) DO BEGIN "
          "INSERT INTO sort_runs VALUES(:i,CAST(LPAD(CAST(MOD(:i,127) AS VARCHAR(3)),3,'0') AS bytea),"
          "CAST(LPAD(CAST(MOD(:i,127) AS VARCHAR(3)),3,'0') AS mchar(8))); i=i+1; END END")
    expected=sorted(range(12000),key=lambda i:(i%127,-i))
    for column in ('b','c'):
        check('sort_runs_'+column, f'SELECT id FROM sort_runs ORDER BY {column},id DESC',
              [[str(i)] for i in expected])
        result=check('sort_groups_'+column, f'SELECT {column},COUNT(*) FROM sort_runs GROUP BY {column} ORDER BY {column}')
        require([int(row[1]) for row in result['rows']]==[95 if i<62 else 94 for i in range(127)],
                'Multiple-run grouping differs')
        check('sort_distinct_'+column, f'SELECT COUNT(DISTINCT {column}) FROM sort_runs', [['127']])
    pages=int(check('key_page_size','SELECT "MON$PAGE_SIZE" FROM "SYSTEM"."MON$DATABASE"')['rows'][0][0])
    largest=(pages//4-2)//2
    for temporary in ('','TEMP '):
        table=('temp_' if temporary else '')+'key_boundary'
        check(table+'_create',f'CREATE {temporary}TABLE {table}(id integer,b bytea)')
        check(table+'_index',f'CREATE UNIQUE INDEX {table}_i ON {table}(b)')
        for size,state in [(largest,''),(largest+1,'54000')]:
            result=client.params(f'INSERT INTO {table} VALUES($1,$2)',[str(size),b'\1'*size],[23,17],[0,1])
            require(result['sqlstate']==state,'Actual key boundary: '+str(result))
        check(table+'_begin','BEGIN')
        check(table+'_savepoint','SAVEPOINT boundary_sp')
        result=client.params(f'UPDATE {table} SET b=$1',[b'\1'*(largest+1)],[17],[1])
        require(result['sqlstate']=='54000','Oversized update was not rejected')
        check(table+'_rollback','ROLLBACK TO boundary_sp')
        check(table+'_atomic',f'SELECT OCTET_LENGTH(b) FROM {table}',[[str(largest)]])
        check(table+'_commit','COMMIT')
        check(table+'_drop_rollback_begin','BEGIN')
        check(table+'_drop_rollback_drop',f'DROP INDEX {table}_i')
        result=client.params(f'INSERT INTO {table} VALUES(99999,$1)',[b'\2'*(largest+1)],[17],[1])
        require(not result['sqlstate'],'Transactional DROP retained index limit: '+str(result))
        check(table+'_drop_rollback','ROLLBACK')
        check(table+'_drop_rollback_restored',f"SELECT COUNT(*) FROM {table} WHERE b>=CAST('' AS bytea) PLAN ({table} INDEX ({table}_i))",[['1']])
        check(table+'_drop_index',f'DROP INDEX {table}_i')
        result=client.params(f'INSERT INTO {table} VALUES(99999,$1)',[b'\2'*(largest+1)],[17],[1])
        require(not result['sqlstate'],'Unindexed bytea inherited index limit: '+str(result))
        check(table+'_failed_build',f'CREATE INDEX {table}_i ON {table}(b)',error='54000')
        check(table+'_build_atomic',f'SELECT COUNT(*) FROM {table} WHERE id=99999',[['1']])
        check(table+'_clean',f'DELETE FROM {table} WHERE id=99999')
        check(table+'_rebuild',f'CREATE INDEX {table}_i ON {table}(b)')
    check('temp_duplicate_build_table','CREATE TEMP TABLE duplicate_build(b bytea)')
    check('temp_duplicate_build_values',"INSERT INTO duplicate_build VALUES('a'::bytea);"
          "INSERT INTO duplicate_build VALUES('a'::bytea)")
    check('temp_duplicate_build','CREATE UNIQUE INDEX duplicate_build_i ON duplicate_build(b)',error='23505')
    check('temp_duplicate_build_atomic','SELECT COUNT(*) FROM duplicate_build',[['2']])
    check('temp_duplicate_build_clean','DELETE FROM duplicate_build')
    check('temp_duplicate_build_retry','CREATE UNIQUE INDEX duplicate_build_i ON duplicate_build(b)')
    # PQexecParams reaches the same deferred build through Execute/Sync.
    check('temp_extended_build_table','CREATE TEMP TABLE extended_build(b bytea)')
    result=client.params('INSERT INTO extended_build VALUES($1)',[b'\1'*20000],[17],[1])
    require(result['status']==1,'Extended build fixture')
    result=client.params('CREATE INDEX extended_build_i ON extended_build(b)')
    require(result['sqlstate']=='54000' and result['severity']=='ERROR','Extended deferred build: '+str(result))
    check('temp_extended_build_atomic','SELECT COUNT(*) FROM extended_build',[['1']])
    check('temp_extended_build_clean','DELETE FROM extended_build')
    check('temp_extended_build_retry','CREATE INDEX extended_build_i ON extended_build(b)')


def stored_identity(config, client, check):
    check('typed_view','CREATE VIEW typed_view AS SELECT CAST(b AS bytea) b,'
          'CAST(\'abc\' AS mchar(3)) c,CAST(\'public\' AS regnamespace) n FROM typed_binary WHERE id=4')
    result=check('typed_view_rows','SELECT b,c,n FROM typed_view',[[r'\x0100','abc','public']])
    require([c['oid'] for c in result['columns']]==[17,0x00fb0001,4089], 'Persisted view identity')
    source=check('typed_view_source','SELECT CAST("RDB$VIEW_SOURCE" AS VARCHAR(8191)) FROM "SYSTEM"."RDB$RELATIONS" '
                 'WHERE "RDB$SCHEMA_NAME"=\'public\' AND "RDB$RELATION_NAME"=\'typed_view\'')['rows'][0][0]
    native(config,'types_native_reprepare','SET SEARCH_PATH TO "public","pg_catalog",SYSTEM;\n'
           'ALTER VIEW "public"."typed_view" AS '+source+';\nCOMMIT;\nQUIT;\n')
    result=check('typed_view_reprepared','SELECT b,c,n FROM typed_view',[[r'\x0100','abc','public']])
    require([c['oid'] for c in result['columns']]==[17,0x00fb0001,4089], 'Native reprepare lost type identity')
    for name,typ in [('binary','bytea'),('namespace','regnamespace'),('mchar','mchar')]:
        argument='mchar(3)' if name=='mchar' else typ
        check('typed_function_'+name,f'CREATE FUNCTION typed_{name}_fn(v {argument}) RETURNS {typ} AS BEGIN RETURN v; END')
    result=check('typed_function_values',"SELECT typed_binary_fn('\\x0100'::bytea),"
                 "typed_namespace_fn('public'::regnamespace),SUBSTR(typed_mchar_fn('abc'),1,1)",
                 [[r'\x0100','public','a  ']])
    require([c['oid'] for c in result['columns']]==[17,4089,0x00fb0001], 'Function result type identity')
    source=check('typed_function_source','SELECT CAST("RDB$FUNCTION_SOURCE" AS VARCHAR(8191)) FROM "SYSTEM"."RDB$FUNCTIONS" '
                 'WHERE "RDB$SCHEMA_NAME"=\'public\' AND "RDB$FUNCTION_NAME"=\'typed_namespace_fn\'')['rows'][0][0]
    native(config,'types_native_function_reprepare','SET SEARCH_PATH TO "public","pg_catalog",SYSTEM;\nSET TERM ^;\n'
           'ALTER FUNCTION "public"."typed_namespace_fn"("v" BIGINT SUB_TYPE 3) RETURNS BIGINT SUB_TYPE 3 AS '+
           source+'^\nSET TERM ;^\nCOMMIT;\nQUIT;\n')
    check('typed_function_reprepared',"SELECT typed_namespace_fn('public'::regnamespace)",[['public']])
    check('typed_substr_view','CREATE VIEW typed_substr_view AS SELECT '
          "SUBSTR(SUBSTR(CAST('abc' AS mchar(3)),1,2),1,1) c FROM typed_binary WHERE id=4")
    source=check('typed_substr_source','SELECT CAST("RDB$VIEW_SOURCE" AS VARCHAR(8191)) FROM "SYSTEM"."RDB$RELATIONS" '
                 'WHERE "RDB$SCHEMA_NAME"=\'public\' AND "RDB$RELATION_NAME"=\'typed_substr_view\'')['rows'][0][0]
    require('SUBSTRING(' in source and ' FROM ' in source and ' FOR ' in source,'SUBSTR native source')
    native(config,'types_native_substr_reprepare','SET SEARCH_PATH TO "public","pg_catalog",SYSTEM;\n'
           'ALTER VIEW "public"."typed_substr_view" AS '+source+';\nCOMMIT;\nQUIT;\n')
    result=check('typed_substr_reprepared','SELECT c FROM typed_substr_view',[['a  ']])
    require(result['columns'][0]['oid']==0x00fb0001,'SUBSTR reprepare lost type identity')


def concurrent_types(config, client, check):
    from pgwire_types_oracle import TypeLibpq
    other=TypeLibpq(config)
    try:
        for sql in ['CREATE TEMP TABLE permanent_keys(b bytea,c mchar(4),n regnamespace)',
                    'CREATE UNIQUE INDEX private_key ON permanent_keys(b)',
                    "INSERT INTO permanent_keys VALUES('\\x0100'::bytea,'Ё','public')"]:
            result=other.query(sql,True)[-1]
            require(not result['sqlstate'],'Independent TEMP: '+str(result))
        result=other.query('SELECT b,c,n FROM permanent_keys',True)[-1]
        require(result['rows']==[[r'\x0100','Ё   ','public']] and
                [c['oid'] for c in result['columns']]==[17,0x00fb0001,4089],'Independent temporary typed rows')
        check('concurrent_permanent_visible','SELECT COUNT(*) FROM permanent_keys',[['6']])
        other.query('BEGIN')
        other.query("INSERT INTO permanent_keys VALUES('\\x01'::bytea,'a','public')")
        # Disconnect must undo the native transaction and remove all local definitions.
    finally:
        other.close()
    reopened=TypeLibpq(config)
    try:
        result=reopened.query('SELECT COUNT(*) FROM permanent_keys',True)[-1]
        require(result['rows']==[['6']],'Reconnect retained somebody else\'s temporary relation')
        result=reopened.query('SELECT b,c,n FROM typed_view',True)[-1]
        require(result['rows']==[[r'\x0100','abc','public']] and
                [c['oid'] for c in result['columns']]==[17,0x00fb0001,4089], 'Persisted type metadata after reconnect')
    finally:
        reopened.close()


def types_restart(h):
    from pgwire_types_oracle import TypeLibpq
    from pgwire_1c_smoke import Client
    from wire_host_smoke import reserve_ports
    source=Path(__file__).with_name('pgwire_types_api.cpp')
    result=subprocess.run(['g++','-std=c++17','-g','-O0',str(source),'-I'+str(h.runtime/'include'),
        '-L'+str(h.runtime/'lib'),'-Wl,-rpath,'+str(h.runtime/'lib'),'-lfbclient','-o',str(h.work/'types_api')],
        env=h.env,capture_output=True,text=True,timeout=h.timeout)
    (h.work/'types_api_compile.log').write_text(result.stdout+result.stderr)
    require(result.returncode==0,'Native type API helper compilation')
    result=subprocess.run([str(h.work/'types_api'),f'inet://127.0.0.1:{h.native_port}/wire_host'],
        env=dict(h.env,ISC_USER='WIRE_HOST_TEST',ISC_PASSWORD=h.password),cwd=h.temp,
        capture_output=True,text=True,timeout=h.timeout)
    (h.work/'types_api.log').write_text(h.redact(result.stdout+result.stderr))
    require(result.returncode==0,'Native type API assertions; see types_api.log')
    api=json.loads(result.stdout)
    h.no_attachments()
    h.stop()
    native_port,port=reserve_ports(2)
    h.start('types_restart',native_port,native_port,[{'alias':'PgWire','port':port}])
    ready=Client(h).login()
    try: ready.query_integer()
    finally: ready.close()
    config=dict(config_for(h),port=port)
    client=TypeLibpq(config)
    try:
        for sql,rows in [
            ('SELECT b,c,n FROM typed_view',[[r'\x0100','abc','public']]),
            ('SELECT SUBSTR(v,1,1) FROM typed_mchar_value',[['a  ']]),
            ('SELECT c FROM typed_substr_view',[['a  ']]),
            ("SELECT id FROM permanent_keys WHERE b='\\x0100'::bytea PLAN (permanent_keys INDEX (permanent_keys_composite))",[['3']]),
            ("SELECT id FROM indexed_mchar WHERE v='ёж' PLAN (indexed_mchar INDEX (indexed_mchar_i))",[['6']]),
            ('SELECT pg_catalog.pg_my_temp_schema()',[['0']]),
        ]:
            result=client.query(sql,True)[-1]
            require(not result['sqlstate'] and result['rows']==rows,'Restart: '+str(result))
    finally:
        client.close()
    # Native dialects retain their own identifiers, trailing-zero binary rules,
    # case-sensitive text comparison and the prohibition on ordinary BLOB indexes.
    for dialect in (1,2,3):
        sql='SET LIST ON;\nSELECT IIF(CAST(x\'01\' AS VARBINARY(8))=CAST(x\'0100\' AS VARBINARY(8)),1,0) AS N, '\
            'IIF(CAST(x\'01\' AS BLOB SUB_TYPE 0)=CAST(x\'0100\' AS BLOB SUB_TYPE 0),1,0) AS N, '\
            "IIF(CAST('a' AS VARCHAR(4))=CAST('A' AS VARCHAR(4)),1,0) AS N, "\
            "IIF(CAST('a' AS CHAR(4))=CAST('a ' AS CHAR(4)),1,0) AS N FROM SYSTEM.RDB$DATABASE;\nROLLBACK;\nQUIT;\n"
        env=h.env.copy();env.update(ISC_USER='WIRE_HOST_TEST',ISC_PASSWORD=h.password)
        result=subprocess.run([str(h.runtime/'bin/isql'),'-quiet','-bail','-charset','UTF8','-sql_dialect',str(dialect),
                               f'inet://127.0.0.1:{native_port}/wire_host'],input=sql,env=env,cwd=h.temp,
                              capture_output=True,text=True,timeout=h.timeout)
        (h.work/f'types_native_dialect_{dialect}.log').write_text(h.redact(result.stdout+result.stderr))
        require(result.returncode==0 and numbers(result.stdout)==[1,1,0,1], 'Native dialect semantics changed')
    return {'restart':True,'native_dialects':[1,2,3],'typed_indexes_and_value_typmod':True,'api':api,
            'api_source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}


def original_application(config, client, check):
    capture=Path(config['capture_dir'])
    selected={n:next((capture/'profile/selected').glob(f'{n:02}-*.sql')).read_text()
              for n in (4,6,7,8,12,17,18,23,24)}
    (Path(config['work'])/'types-selected-hashes.json').write_text(json.dumps(
        {str(n):hashlib.sha256(sql.encode()).hexdigest() for n,sql in selected.items()},indent=2)+'\n')
    check('app_strings','SET standard_conforming_strings=off')
    for sql in [
        'CREATE TABLE ibversion(v integer)',
        'CREATE TABLE _ExtensionsRestruct(_ExtDataID bytea,_RestructDataType numeric,_RestructDataInt integer)',
        'CREATE TABLE _InfoRg168(_Period timestamp,_RecorderRRef bytea,_Active boolean,_Fld169RRef bytea,_Fld170RRef bytea)',
        'CREATE TABLE _Reference19(_IDRRef bytea,_Version integer)',
        'CREATE TABLE _ExtensionsInfo(_IDRRef bytea,_ExtensionOrder integer,_ExtName mvarchar(150),'
        '_UpdateTime timestamp,_ExtensionUsePurpose integer,_ExtensionScope integer,'
        '_ExtensionZippedInfo bytea,_MasterNode mchar(40),_UsedInDistributedInfoBase boolean,_Version integer)',
        'CREATE TEMP TABLE tt9(_IDRRef bytea)',
    ]:
        check('app_fixture',sql)
    def literal(number):
        return re.search(r"'[^']*'::bytea",selected[number]).group(0)
    for sql in [
        'INSERT INTO _ExtensionsRestruct VALUES('+literal(7)+',0,19)',
        "INSERT INTO _InfoRg168 VALUES(timestamp '2026-01-02',"+literal(17)+",true,NULL,NULL)",
        'INSERT INTO _Reference19 VALUES('+literal(18)+',0)',
        "INSERT INTO _ExtensionsInfo VALUES('\\\\001'::bytea,1,'Synthetic',timestamp '2026-01-02',"
        "0,0,'\\\\000'::bytea,'0:00000000000000000000000000000000',true,0)",
    ]:
        check('app_values',sql)
    for number in (4,7,8,12,17,24):
        tags={12:['DROP TABLE','CREATE TABLE'],24:['DROP INDEX','CREATE INDEX']}.get(number,['SELECT 1'])
        result=check(f'selected{number:02}_original',selected[number],tags=tags)
        if number in (4,7):
            require(result['rows'] == [[str(1 if number==4 else 19)]],str(result))
        if config.get('types_oracle'):
            oracle=json.loads(Path(config['types_oracle']).read_text())
            expected=oracle['cases'][f'selected{number:02}_original_synthetic'][-1]
            require(result['rows']==expected['rows'],'Selected query differs from oracle: '+str(number))
            mapping={oracle['oids']['mchar']:0x00fb0001,oracle['oids']['mvarchar']:0x00fb0002}
            require([c['oid'] for c in result['columns']]==
                    [mapping.get(c['oid'],c['oid']) for c in expected['columns']],
                    'Selected query type identity differs: '+str(number))
    check('selected18_begin','BEGIN')
    require(check('selected18_match',selected[18])['tag']=='DELETE 1','Version predicate did not match')
    check('selected18_rollback','ROLLBACK')
    check('selected18_restored','SELECT COUNT(*) FROM _Reference19',[['1']])
    check('selected18_version','UPDATE _Reference19 SET _Version=1')
    require(check('selected18_mismatch',selected[18])['tag']=='DELETE 0','Version predicate matched wrong version')
    check('version_index','CREATE UNIQUE INDEX version_id ON _Reference19(_IDRRef)')
    predicate='_IDRRef='+literal(18)+' AND BINROWVER(_Version)=$1'
    check('version_update_begin','BEGIN')
    for version,tag in [(0,'UPDATE 0'),(1,'UPDATE 1')]:
        result=client.params('UPDATE _Reference19 SET _Version=2 WHERE '+predicate,
                             [b'\0'*4+version.to_bytes(4,'big')],[17],[1])
        require(result['status']==1 and result['tag']==tag,'Indexed version UPDATE: '+str(result))
    check('version_update_rollback','ROLLBACK')
    check('version_update_restored','SELECT _Version FROM _Reference19',[['1']])
    result=client.params('UPDATE _Reference19 SET _Version=2 WHERE '+predicate,
                         [b'\0'*7+b'\1'],[17],[1])
    require(result['tag']=='UPDATE 1','Committed version UPDATE')
    check('version_update_committed','SELECT _Version FROM _Reference19',[['2']])
    check('app_min_bool_or','SELECT MIN(_Period),BOOL_OR(_Active) FROM _InfoRg168', [['2026-01-02 00:00:00','t']])
    check('app_string_aggregates','SELECT MAX(_ExtName),MIN(_MasterNode) FROM _ExtensionsInfo',
          [['Synthetic','0:00000000000000000000000000000000      ']])
    check('app_restore_strings','SET standard_conforming_strings=on')
