"""APP-12 SQL catalog checks in the existing private native/libpq harness."""
import ctypes as C
import hashlib
import json
import re
import struct
from pathlib import Path

from pgwire_namespace_checks import (INSTALLER, CATALOG_INSTALLER, allocator,
    catalog_mapping, catalog_snapshot, config_for, identifier, mapping, native, numbers)
from wire_host_smoke import require

CLASS_INSTALLER = INSTALLER.with_name('install_class_catalog.sql')
CLASS_VIEW = 'PG_CATALOG.PG_CLASS'
LOOKUP = f'SELECT OID,RELNAME,RELNAMESPACE,RELKIND FROM {CLASS_VIEW}'
TYPES = [20,1043,20,1043]
LABELS = ['OID','RELNAME','RELNAMESPACE','RELKIND']
NAMES = ['T','t','MiXeD','Таблица с пробелом','A"quote','Я'*56]


def values(output, label):
    return re.findall(r'^'+label+r'[ \t]+(\S+)[ \t]*$',output,re.M)


def relation_metadata(config,label):
    # Read all native relations without the view's JOIN/filter/casts. The oracle
    # selects the supported scope in Python, using the independent private mapping.
    sql = "SELECT RDB$RELATION_ID AS I,HEX_ENCODE(TRIM(TRAILING FROM RDB$RELATION_NAME)) AS H," \
          "HEX_ENCODE(TRIM(TRAILING FROM RDB$SCHEMA_NAME)) AS S,COALESCE(RDB$RELATION_TYPE,0) AS T," \
          "COALESCE(HEX_ENCODE(TRIM(TRAILING FROM RDB$PACKAGE_NAME)),'-') AS P " \
          "FROM SYSTEM.RDB$RELATIONS ORDER BY RDB$RELATION_ID;\nROLLBACK;\nQUIT;\n"
    output = native(config,label,sql,admin=True)['output']
    columns = [values(output,label) for label in ('I','H','S','T','P')]
    require(columns[0] and len({len(column) for column in columns})==1,'Native relation metadata is incomplete')
    return [(int(i),bytes.fromhex(h).decode(),bytes.fromhex(s).decode(),int(t),p) for i,h,s,t,p in zip(*columns)]


def expected_rows(relations,namespaces):
    rows = []
    for value,name,schema,kind,package in relations:
        if kind in (0,1) and package=='-':
            require(schema in namespaces,'Native relation has no namespace reference: '+schema)
            rows.append([str(value+1),name,str(namespaces[schema]),'v' if kind==1 else 'r'])
    require(len({row[0] for row in rows})==len(rows) and all(1<=int(row[0])<=32768 for row in rows),
            'Native relation OID uniqueness/range differs')
    return rows


def native_rows(config,label,*,readonly=False):
    sql = ('SET TRANSACTION READ ONLY;\n' if readonly else '') + \
        f'SELECT OID AS I,HEX_ENCODE(RELNAME) AS H,RELNAMESPACE AS S,RELKIND AS K FROM {CLASS_VIEW} ORDER BY OID;\nROLLBACK;\nQUIT;\n'
    output = native(config,label,sql)['output']
    columns = [values(output,label) for label in ('I','H','S','K')]
    require(len({len(column) for column in columns})==1,'Native PG_CLASS rows are incomplete')
    return [[i,bytes.fromhex(h).decode(),s,k] for i,h,s,k in zip(*columns)]


def class_objects(config,label):
    sql = "SELECT COUNT(*) AS N FROM SYSTEM.RDB$RELATIONS WHERE RDB$SCHEMA_NAME='PG_CATALOG' AND RDB$RELATION_NAME='PG_CLASS';\n" \
          "SELECT COUNT(*) AS N FROM SYSTEM.RDB$USER_PRIVILEGES WHERE RDB$RELATION_SCHEMA_NAME='PG_CATALOG' AND RDB$RELATION_NAME='PG_CLASS';\n"
    return numbers(native(config,label,sql+'ROLLBACK;\nQUIT;\n',admin=True)['output'])


def protected_state(config,label):
    # Includes bootstrap owner/source/BLR/grants, APP-10 and PG_NAMESPACE definition/grants.
    snapshot = catalog_snapshot(config,label+'_bootstrap',True)
    grants = native(config,label+'_grants',"SELECT RDB$USER,RDB$USER_TYPE,RDB$GRANTOR,RDB$PRIVILEGE,RDB$GRANT_OPTION,"
        "RDB$RELATION_SCHEMA_NAME,RDB$RELATION_NAME,RDB$OBJECT_TYPE FROM SYSTEM.RDB$USER_PRIVILEGES "
        "WHERE RDB$RELATION_NAME IS DISTINCT FROM 'PG_CLASS' AND RDB$USER IS DISTINCT FROM 'PG_CLASS' "
        "ORDER BY 1,2,3,4,5,6,7,8;\nROLLBACK;\nQUIT;\n",admin=True)['output']
    return snapshot,grants,mapping(config,label+'_namespaces'),allocator(config,label+'_counter')


def class_catalog_fixture(h):
    config = config_for(h)
    native(config,'class_admin',f"CREATE OR ALTER USER SYSDBA PASSWORD '{h.password}';\nCOMMIT;\n",admin=True)
    bootstrap = INSTALLER.parent.parent/'compat.sql'
    prerequisites = INSTALLER.read_text()+bootstrap.read_text()+CATALOG_INSTALLER.read_text()
    native(config,'class_prerequisites',prerequisites,admin=True)
    fixtures = 'CREATE SCHEMA "public";\nCREATE SCHEMA CLS_HIDDEN;\n' + \
        ''.join('CREATE TABLE PUBLIC.'+identifier(name)+'(V INTEGER);\n' for name in NAMES) + \
        'CREATE TABLE "public".T(V INTEGER);\nCREATE VIEW PUBLIC.CLS_V AS SELECT V FROM PUBLIC.T;\n' \
        'CREATE TABLE PUBLIC.CLS_SECRET(V INTEGER);\nCREATE VIEW PUBLIC.CLS_SECRET_V AS SELECT V FROM PUBLIC.CLS_SECRET;\n' \
        'CREATE TABLE CLS_HIDDEN.CLS_NO_USAGE(V INTEGER);\nGRANT SELECT ON CLS_HIDDEN.CLS_NO_USAGE TO PUBLIC;\n' \
        'CREATE GLOBAL TEMPORARY TABLE PUBLIC.CLS_GTT4(V INTEGER) ON COMMIT PRESERVE ROWS;\n' \
        'CREATE GLOBAL TEMPORARY TABLE PUBLIC.CLS_GTT5(V INTEGER) ON COMMIT DELETE ROWS;\n' \
        'CREATE INDEX PUBLIC.CLS_INDEX ON PUBLIC.T(V);\nCREATE SEQUENCE PUBLIC.CLS_SEQUENCE;\n' \
        'SET TERM ^;\nCREATE PACKAGE PUBLIC.CLS_PKG AS BEGIN\n' \
        'TEMPORARY TABLE CLS_PT(V INTEGER) ON COMMIT PRESERVE ROWS;\nEND^\nSET TERM ;^\n' \
        'GRANT CREATE SCHEMA TO USER WIRE_HOST_TEST;\nCOMMIT;\n'
    native(config,'class_fixtures',fixtures,admin=True)
    # Only this explicit fixture grant enables the later ordinary-user DDL.
    native(config,'class_writer_schema','CREATE SCHEMA CLS_WRITER;\nCOMMIT;\n')
    external = h.work/'external.dat'
    native(config,'class_external_fixture',f"CREATE TABLE PUBLIC.CLS_EXTERNAL EXTERNAL FILE '{external}' (V INTEGER);\nCOMMIT;\n",admin=True)
    installer = CLASS_INSTALLER.read_text()
    before = protected_state(config,'class_before')
    relations = relation_metadata(config,'class_relations_before')
    injected = installer.replace('\nCOMMIT;', '\nSELECT 1/0 FROM SYSTEM.RDB$DATABASE;\nCOMMIT;',1)
    error = native(config,'class_install_failure',injected,admin=True,error=True)
    require(error['sqlstates']==['22012'] and class_objects(config,'class_failure_objects')==[0,0] and
            protected_state(config,'class_failure')==before and relation_metadata(config,'class_failure_relations')==relations,
            'Failed class install changed prior objects/grants/namespace state')
    native(config,'class_install','SET SEARCH_PATH TO SYSTEM;\n'+installer,admin=True)
    current = relation_metadata(config,'class_relations_installed')
    require([row for row in current if row[1:3]!=('PG_CLASS','PG_CATALOG')]==relations and
            protected_state(config,'class_installed')==before,'Fresh install changed prior metadata/grants/namespace state')
    expected = expected_rows(current,before[2])
    require(native_rows(config,'class_native_installed')==expected,'Fresh native view differs from oracle')
    objects = class_objects(config,'class_installed_objects')
    error = native(config,'class_repeat',installer,admin=True,error=True)
    require('already exists' in error['output'].lower() and class_objects(config,'class_repeat_objects')==objects and
            relation_metadata(config,'class_repeat_relations')==current and protected_state(config,'class_repeated')==before and
            native_rows(config,'class_repeat_rows')==expected,'Repeat install changed prior state')
    report = {'checks':['fresh_qualified_install_and_independent_native_oracle','atomic_failure_preserves_bootstrap_namespaces_grants',
                        'repeat_refuses_without_changes'], 'initial_rows':len(expected),'external':'PASS: metadata-only /tmp, unchanged config'}

    def fresh(name):
        database = h.database.parent/('class_'+name+'.fdb')
        h.isql('class_create_'+name,f"CREATE DATABASE '{database}' DEFAULT CHARACTER SET UTF8;\nCOMMIT;\nQUIT;\n",embedded=True)
        return dict(config,database=str(database))

    missing = fresh('missing_namespace')
    native(missing,'class_missing_prerequisites',INSTALLER.read_text()+bootstrap.read_text(),admin=True)
    old = protected_state(missing,'class_missing_before')
    error = native(missing,'class_missing_install',installer,admin=True,error=True)
    require('PG_NAMESPACE' in error['output'] and class_objects(missing,'class_missing_objects')==[0,0] and
            protected_state(missing,'class_missing_after')==old,'Missing dependency damaged existing state')
    report['missing_dependency_sqlstates'] = error['sqlstates']
    report['checks'].append('missing_namespace_dependency_refuses_without_partial_view')
    foreign = fresh('collision')
    native(foreign,'class_collision_registry',INSTALLER.read_text()+'GRANT CREATE SCHEMA TO USER WIRE_HOST_TEST;\nCOMMIT;\n',admin=True)
    native(foreign,'class_collision_object','CREATE SCHEMA PG_CATALOG;\nCREATE TABLE PG_CATALOG.PG_CLASS(V INTEGER);\n'
           'INSERT INTO PG_CATALOG.PG_CLASS VALUES(73);\nCOMMIT;\n')
    native(foreign,'class_collision_dependencies',bootstrap.read_text()+CATALOG_INSTALLER.read_text(),admin=True)
    old = protected_state(foreign,'class_collision_before')
    rels = relation_metadata(foreign,'class_collision_relations_before')
    sql = "SELECT RDB$OWNER_NAME FROM SYSTEM.RDB$RELATIONS WHERE RDB$SCHEMA_NAME='PG_CATALOG' AND RDB$RELATION_NAME='PG_CLASS';\n" \
          "SELECT RDB$USER,RDB$PRIVILEGE,RDB$GRANT_OPTION FROM SYSTEM.RDB$USER_PRIVILEGES WHERE RDB$RELATION_NAME='PG_CLASS' ORDER BY 1,2,3;\n" \
          'SELECT V FROM PG_CATALOG.PG_CLASS;\nROLLBACK;\nQUIT;\n'
    original = native(foreign,'class_collision_snapshot_before',sql,admin=True)['output']
    error = native(foreign,'class_collision_install',installer,admin=True,error=True)
    require('already exists' in error['output'].lower() and protected_state(foreign,'class_collision_after')==old and
            relation_metadata(foreign,'class_collision_relations_after')==rels and
            native(foreign,'class_collision_snapshot_after',sql,admin=True)['output']==original,
            'Collision changed foreign object/owner/data/grants')
    report['checks'].append('foreign_class_object_owner_data_grants_preserved')
    report['installer_sha256'] = hashlib.sha256(CLASS_INSTALLER.read_bytes()).hexdigest()
    report['prerequisite_sha256'] = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (INSTALLER,CATALOG_INSTALLER,bootstrap)}
    h.summary['class_catalog_install'] = report


def class_catalog_client(config,client):
    from pgwire_1c_smoke import Libpq
    report = {'checks':[], 'results':0, 'metadata':{}}
    def capture_metadata(connection):
        for name in ('PQfsize','PQfmod','PQfformat'):
            function = getattr(connection.pq,name)
            function.restype,function.argtypes = C.c_int,[C.c_void_p,C.c_int]
        original = connection.result
        def result(handle,values=False,binary=False):
            require(handle,'Missing libpq result')
            metadata = [[connection.pq.PQfsize(handle,i),connection.pq.PQfmod(handle,i),connection.pq.PQfformat(handle,i)]
                        for i in range(connection.pq.PQnfields(handle))]
            data = original(handle,values,binary)
            data['metadata'] = metadata
            return data
        connection.result = result
    capture_metadata(client)
    def record(name):
        report['checks'].append(name)
        (Path(config['work'])/'class_catalog_progress.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    def checked(data,*,connection=client,state=0,error='',rows=None,oids=None,binary=False):
        require(data['sqlstate']==error and connection.pq.PQtransactionStatus(connection.connection)==state and
                (rows is None or data.get('rows')==rows) and (oids is None or [c['oid'] for c in data['columns']]==oids),
                'Class result differs: '+repr(data))
        if oids is not None:
            require(data['metadata']==[[8 if oid==20 else -1,-1,int(binary)] for oid in oids],
                    'Class size/typmod/format differs: '+repr(data))
        report['results'] += 1
        return data
    def query(sql,*,connection=client,**expected):
        data = connection.query(sql,True)
        require(len(data)==1,'Unexpected Simple Query result count')
        try:
            return checked(data[0],connection=connection,**expected)
        except RuntimeError as error:
            raise RuntimeError(sql+': '+str(error)) from error
    def params(sql,values=(),*,binary=False,prepared=False,formats=(),**expected):
        return checked(client.params(sql,values,prepared=prepared,formats=formats,result_format=int(binary)),binary=binary,**expected)
    def prepare(name,sql,input_types):
        checked(client.result(client.pq.PQprepare(client.connection,name.encode(),sql.encode(),0,None)))
        data = checked(client.result(client.pq.PQdescribePrepared(client.connection,name.encode())),oids=TYPES)
        require(data['parameter_oids']==input_types and [c['name'] for c in data['columns']]==LABELS,'Class SQL-derived input metadata differs')
        report['metadata'][name] = {'input':input_types,'output':TYPES,'labels':LABELS,'size_typmod_format':data['metadata']}
    def wire(*,connection=client,state=0):
        data = query(LOOKUP+' ORDER BY OID',connection=connection,state=state,oids=TYPES)
        require([c['name'] for c in data['columns']]==LABELS,'Native labels changed')
        raw = query('SELECT RDB$RELATION_ID,TRIM(TRAILING FROM RDB$RELATION_NAME),TRIM(TRAILING FROM RDB$SCHEMA_NAME),'
            'COALESCE(RDB$RELATION_TYPE,0),RDB$PACKAGE_NAME FROM SYSTEM.RDB$RELATIONS ORDER BY RDB$RELATION_ID',
            connection=connection,state=state)['rows']
        namespaces = query('SELECT NATIVE_NAME,NAMESPACE_ID FROM PGWIRE_COMPAT.NAMESPACES',connection=connection,state=state)['rows']
        rows = expected_rows([(int(i),name,schema,int(kind),'-' if package is None else package) for i,name,schema,kind,package in raw],
                             {name:int(value) for name,value in namespaces})
        require(data['rows']==rows,'Public view differs from native relation metadata in current transaction')
        return rows
    def binary_rows(rows):
        return [[struct.pack('!q',int(value)).hex() if i in (0,2) else value.encode().hex() for i,value in enumerate(row)] for row in rows]

    namespaces = mapping(config,'class_client_namespaces')
    require(catalog_mapping(config,'class_client_public_namespaces')==namespaces,'Namespace projections disagree')
    relations = relation_metadata(config,'class_client_relations')
    initial = wire()
    require(initial==expected_rows(relations,namespaces)==native_rows(config,'class_client_native'), 'Native/libpq relation rows differ')
    by_name = {(int(row[2]),row[1]):row for row in initial}
    require(by_name[(namespaces['SYSTEM'],'RDB$PAGES')]==['1','RDB$PAGES',str(namespaces['SYSTEM']),'r'] and
            by_name[(namespaces['PG_CATALOG'],'PG_CLASS')][3]=='v','ID0 mapping or self row differs')
    targets = [by_name[(namespaces['PUBLIC'],name)] for name in NAMES] + \
              [by_name[(namespaces['public'],'T')],by_name[(namespaces['PUBLIC'],'CLS_V')]]
    require(len({row[0] for row in targets})==len(targets),'Case/schema collision mixed relation identities')
    params(LOOKUP+' ORDER BY OID',rows=initial,oids=TYPES)
    params(LOOKUP+' ORDER BY OID',binary=True,rows=binary_rows(initial),oids=TYPES)
    prepare('class_by_id',LOOKUP+' WHERE OID=$1',[20])
    prepare('class_by_name',LOOKUP+' WHERE RELNAME=$1 AND RELNAMESPACE=$2',[1043,20])
    prepare('class_by_namespace',LOOKUP+' WHERE RELNAMESPACE=$1 ORDER BY OID',[20])
    for row in targets:
        oid,name,namespace,kind = row
        literal = "'"+name.replace("'","''")+"'"
        query(LOOKUP+f' WHERE OID={oid}',rows=[row],oids=TYPES)
        query(LOOKUP+f' WHERE RELNAME={literal} AND RELNAMESPACE={namespace}',rows=[row],oids=TYPES)
        for binary in (False,True):
            result = binary_rows([row]) if binary else [row]
            params('class_by_id',[struct.pack('!q',int(oid)) if binary else oid],prepared=True,formats=[int(binary)],
                   binary=binary,rows=result,oids=TYPES)
            params('class_by_name',[name,struct.pack('!q',int(namespace)) if binary else namespace],prepared=True,
                   formats=[int(binary)]*2,binary=binary,rows=result,oids=TYPES)
    for schema in ('PUBLIC','public','PG_CATALOG'):
        value = namespaces[schema]
        rows = [row for row in initial if row[2]==str(value)]
        query(LOOKUP+f' WHERE RELNAMESPACE={value} ORDER BY OID',rows=rows,oids=TYPES)
        for binary in (False,True):
            params('class_by_namespace',[struct.pack('!q',value) if binary else str(value)],prepared=True,formats=[int(binary)],
                   binary=binary,rows=binary_rows(rows) if binary else rows,oids=TYPES)
    params('class_by_name',['absent',str(namespaces['PUBLIC'])],prepared=True,rows=[],oids=TYPES)
    params('class_by_id',['0'],prepared=True,rows=[],oids=TYPES)
    for expression in ('OID AS "oid_alias"','CAST(OID AS BIGINT) AS OID'):
        sql = f'SELECT {expression} FROM {CLASS_VIEW} WHERE OID=1'
        query(sql,rows=[['1']],oids=[20])
        params(sql,binary=True,rows=[[struct.pack('!q',1).hex()]],oids=[20])
    diagnostic = f"SELECT 1::INT8 FROM {CLASS_VIEW} WHERE RELKIND='r' AND RELNAME='T' AND RELNAMESPACE={namespaces['PUBLIC']} LIMIT 1"
    query(diagnostic,rows=[['1']],oids=[20])
    query(diagnostic.replace("RELNAME='T'","RELNAME='absent'"),rows=[],oids=[20])
    query(diagnostic.replace("RELNAME='T'","RELNAME='CLS_V'"),rows=[],oids=[20])
    record('native_oracle_exact_names_ids_namespaces_simple_extended_prepared_binary_metadata_diagnostic')

    excluded = [row for row in relations if row[3] not in (0,1) or row[4]!='-']
    require(all(str(row[0]+1) not in {r[0] for r in initial} for row in excluded),'Excluded native relation leaked into catalog')
    require({2,3,4,5}<={row[3] for row in excluded} and any(row[4]!='-' for row in excluded), 'Exclusion fixtures incomplete')
    require(not {'CLS_INDEX','CLS_SEQUENCE'} & {row[1] for row in initial},'Non-relation object leaked into catalog')
    report['excluded_types'] = sorted({row[3] for row in excluded})
    # Separate native attachment: actual LTT and a declared temporary table have
    # usable data, but no permanent catalog rows. No savepoint/crash repro.
    output = native(config,'class_ltt',
        'CREATE LOCAL TEMPORARY TABLE CLS_WRITER.CLS_LTT(V INTEGER) ON COMMIT PRESERVE ROWS;\n'
        'INSERT INTO CLS_WRITER.CLS_LTT VALUES(7);\nCOMMIT;\nSELECT V AS N FROM CLS_WRITER.CLS_LTT;\n'
        f"SELECT COUNT(*) AS N FROM {CLASS_VIEW} WHERE RELNAME='CLS_LTT';\n"
        "SELECT COUNT(*) AS N FROM SYSTEM.RDB$RELATIONS WHERE RDB$RELATION_NAME='CLS_LTT';\n"
        'SET TERM ^;\nEXECUTE BLOCK RETURNS (N INTEGER) AS DECLARE LOCAL TEMPORARY TABLE CLS_DECL(V INTEGER);\n'
        'BEGIN INSERT INTO CLS_DECL VALUES(8); SELECT V FROM CLS_DECL INTO :N; SUSPEND; END^\nSET TERM ;^\n'
        f"SELECT COUNT(*) AS N FROM {CLASS_VIEW} WHERE RELNAME='CLS_DECL';\nROLLBACK;\nQUIT;\n")['output']
    require(numbers(output)==[7,0,0,8,0] and wire()==initial and relation_metadata(config,'class_after_ltt')==relations,
            'Local/declared temporary tables changed permanent catalog')
    record('gtt_package_virtual_external_index_sequence_and_local_declared_temporary_exclusions')

    before = protected_state(config,'class_access_before')
    for name in ('PUBLIC.CLS_SECRET','PUBLIC.CLS_SECRET_V','CLS_HIDDEN.CLS_NO_USAGE'):
        denied = native(config,'class_denied_'+name.replace('.','_'),f'SELECT * FROM {name};\nROLLBACK;\nQUIT;\n',error=True)
        require('permission' in denied['output'].lower(),'Direct data access failed for a different reason')
        schema,table = name.split('.')
        require((namespaces[schema],table) in by_name,'Privileges filtered catalog row')
    for admin in (False,True):
        for i,sql in enumerate((f"INSERT INTO {CLASS_VIEW} VALUES(1,'bad',1,'r')",f'UPDATE {CLASS_VIEW} SET OID=1',f'DELETE FROM {CLASS_VIEW}')):
            denied = native(config,f'class_readonly_{int(admin)}_{i}',sql+';\nCOMMIT;\nQUIT;\n',admin=admin,error=True)
            require('read-only' in denied['output'].lower() or (not admin and 'permission' in denied['output'].lower()),
                    'View write failed for a different reason')
    denied = native(config,'class_no_ddl_grant','CREATE TABLE PUBLIC.CLS_DENIED(V INTEGER);\nCOMMIT;\nQUIT;\n',error=True)
    require('permission' in denied['output'].lower(),'Installer unexpectedly enabled DDL in PUBLIC')
    query('SET TRANSACTION READ ONLY',state=2)
    require(wire(state=2)==initial and wire(state=2)==initial,'READ ONLY lookup changed relation rows')
    query('ROLLBACK')
    require(native_rows(config,'class_native_readonly',readonly=True)==initial and
            native_rows(config,'class_native_readonly_repeat',readonly=True)==initial and
            relation_metadata(config,'class_after_readonly')==relations and protected_state(config,'class_access_after')==before,
            'Reads/denied writes changed relation metadata, namespace counter or grants')
    require(query('SELECT PG_CATALOG.PG_BACKEND_PID()')['rows']==query('SELECT CURRENT_CONNECTION')['rows'], 'Bootstrap function changed')
    record('unfiltered_catalog_without_data_privileges_owner_readonly_no_new_grants_readonly_no_allocation')

    other = Libpq(config)
    capture_metadata(other)
    try:
        require(wire(connection=other)==initial,'Second attachment initially differs')
        ns = str(namespaces['CLS_WRITER'])
        for kind in ('TABLE','VIEW'):
            name = 'CLS_TX_'+kind
            create = ('CREATE TABLE CLS_WRITER.'+name+'(V INTEGER)' if kind=='TABLE' else
                      'CREATE VIEW CLS_WRITER.'+name+' AS SELECT 1 AS V FROM SYSTEM.RDB$DATABASE')
            drop = 'DROP '+kind+' CLS_WRITER.'+name
            current = wire()
            query('BEGIN',state=2)
            query(create,state=2)
            own = [row for row in wire(state=2) if row[1]==name and row[2]==ns]
            require(len(own)==1 and wire(connection=other)==current and native_rows(config,'class_uncommitted_'+kind)==current,
                    'Uncommitted CREATE visibility differs')
            query('COMMIT')
            require(wire()==wire(connection=other)==native_rows(config,'class_committed_'+kind), 'Committed CREATE not visible')
            native_state = relation_metadata(config,'class_oracle_created_'+kind)
            require(wire()==expected_rows(native_state,namespaces),'Committed CREATE native ID differs')
            alter = ('ALTER TABLE CLS_WRITER.'+name+' ADD W INTEGER' if kind=='TABLE' else
                     'ALTER VIEW CLS_WRITER.'+name+' AS SELECT 2 AS V FROM SYSTEM.RDB$DATABASE')
            query(alter)
            require([row for row in wire() if row[1]==name and row[2]==ns]==own,'ALTER changed relation identity')
            query('BEGIN',state=2)
            query(drop,state=2)
            require(wire(state=2)==current and own[0] in wire(connection=other),'Uncommitted DROP visibility differs')
            query('ROLLBACK')
            require(own[0] in wire(),'DROP rollback lost relation row')
            query(drop)
            require(wire()==wire(connection=other)==current,'Committed DROP retained row')
            params('class_by_name',[name,ns],prepared=True,rows=[],oids=TYPES)
            query(create)
            recreated = [row for row in wire() if row[1]==name and row[2]==ns]
            require(len(recreated)==1 and wire()==expected_rows(relation_metadata(config,'class_recreated_'+kind),namespaces),
                    'Recreated object differs from current native ID')
            # Do not assert monotonicity/different ID: native allocator can reuse IDs.
            params('class_by_name',[name,ns],prepared=True,rows=recreated,oids=TYPES)
            report[kind.lower()+'_lifetime'] = {'before':own[0][0],'after':recreated[0][0],'reuse_allowed':True}
            query(drop)
            query('BEGIN',state=2)
            query(create,state=2)
            require(any(row[1]==name and row[2]==ns for row in wire(state=2)),'Own CREATE missing')
            query('ROLLBACK')
            require(wire()==wire(connection=other)==current,'CREATE rollback left phantom row')
        require(wire()==initial==native_rows(config,'class_final_native') and
                relation_metadata(config,'class_final_relations')==relations and protected_state(config,'class_final')==before,
                'DDL lifecycle changed prior catalog/namespace state')
    finally:
        other.close()
    record('table_view_create_alter_drop_commit_full_rollback_native_ids_and_second_attachment')
    report['source_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report['namespace_helpers_sha256'] = hashlib.sha256(Path(__file__).with_name('pgwire_namespace_checks.py').read_bytes()).hexdigest()
    report['scope'] = 'Persistent schema-level tables/views, native IDs+1 and types only; selected04/10/11 and PG name resolution/visibility/temp remain open'
    return report
