"""APP-10/11 SQL-only namespace registry and catalog, using the private 1c harness."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from wire_host_smoke import require, reserve_ports

INSTALLER = Path(__file__).resolve().parents[1]/'1c/sql/install_namespace_registry.sql'
VIEW = '"PGWIRE_COMPAT"."NAMESPACES"'
TABLE = '"PGWIRE_COMPAT"."NAMESPACE_IDS"'
SEQUENCE = '"PGWIRE_COMPAT"."NAMESPACE_SEQ"'
TRIGGER = '"PGWIRE_COMPAT"."NAMESPACE_DDL"'
NAMES = ['PREEXISTING', 'public', 'MiXeD', 'Схема с пробелом', 'A"quote', ' leading', 'Я'*56]
LOOKUP = f'SELECT "NATIVE_NAME","NAMESPACE_ID" FROM {VIEW} ORDER BY "NAMESPACE_ID"'


def identifier(name):
    return '"'+name.replace('"','""')+'"'


def config_for(h):
    return {'runtime':str(h.runtime), 'work':str(h.work), 'database':str(h.database),
            'password':h.password, 'timeout':h.timeout, 'native_port':getattr(h,'native_port',None)}


def native(config, label, sql, *, admin=False, error=False, database=None):
    """One native isql attachment; negative cases must produce a real SQL error."""
    runtime,work = Path(config['runtime']),Path(config['work'])
    env = os.environ.copy()
    env.update(FIREBIRD=str(runtime), FIREBIRD_LOCK=str(work/'locks'), FIREBIRD_TMP=str(work/'tmp'),
               LD_LIBRARY_PATH=str(runtime/'lib'), ISC_USER='SYSDBA' if admin else 'WIRE_HOST_TEST',
               ISC_PASSWORD=config['password'])
    env.pop('ISC_ROLE',None)
    target = str(database or (f"inet://127.0.0.1:{config['native_port']}/wire_host" if config.get('native_port')
                             else config['database']))
    run = subprocess.run([str(runtime/'bin/isql'),'-quiet','-bail','-charset','UTF8',target],
        input='SET BAIL ON;\nSET AUTODDL OFF;\nSET LIST ON;\n'+sql,
        env=env,cwd=work/'tmp',capture_output=True,text=True,timeout=config['timeout'])
    output = (run.stdout+run.stderr).replace(config['password'],'[REDACTED]')
    (work/(label+'.log')).write_text(output)
    states = re.findall(r'SQLSTATE\s*=\s*([0-9A-Z]{5})',output)
    require(bool(run.returncode) == bool(error) and (bool(states) if error else 'Statement failed' not in output),
            'Native '+label+' differs; see retained log')
    return {'exit':run.returncode, 'sqlstates':states, 'output':output}


def numbers(output, column='N'):
    return [int(value) for value in re.findall(r'^'+column+r'[ \t]+(-?\d+)[ \t]*$',output,re.M)]


def mapping(config, label, *, system=False):
    sql = ('SELECT HEX_ENCODE(TRIM(TRAILING FROM "RDB$SCHEMA_NAME")) AS H FROM "SYSTEM"."RDB$SCHEMAS"'
           if system else f'SELECT HEX_ENCODE("NATIVE_NAME") AS H,"NAMESPACE_ID" AS I FROM {VIEW} ORDER BY "NAMESPACE_ID"')
    output = native(config,label,sql+';\nROLLBACK;\nQUIT;\n',admin=system)['output']
    names = [bytes.fromhex(value).decode('utf8') for value in re.findall(r'^H[ \t]+([0-9A-Fa-f]+)[ \t]*$',output,re.M)]
    if system:
        return set(names)
    ids = numbers(output,'I')
    require(len(names)==len(ids) and len(set(names))==len(names) and len(set(ids))==len(ids) and
            all(1<=value<=4294967295 for value in ids), 'Native registry uniqueness/range differs')
    return dict(zip(names,ids))


def allocator(config,label):
    output = native(config,label,f'SELECT GEN_ID({SEQUENCE},0) AS N FROM "SYSTEM"."RDB$DATABASE";\nROLLBACK;\nQUIT;\n',admin=True)
    values = numbers(output['output']); require(len(values)==1,'Allocator observation missing')
    return values[0]


def service_counts(config,label,database=None):
    sql = ''.join(f'SELECT COUNT(*) AS N FROM "SYSTEM".{table} WHERE "RDB$SCHEMA_NAME"=\'PGWIRE_COMPAT\'' +
                  (" AND COALESCE(RDB$SYSTEM_FLAG,0)=0" if table=='RDB$TRIGGERS' else '') + ';\n'
                  for table in ('RDB$SCHEMAS','RDB$RELATIONS','RDB$GENERATORS','RDB$TRIGGERS'))
    return numbers(native(config,label,sql+'ROLLBACK;\nQUIT;\n',admin=True,database=database)['output'])


def namespaces_fixture(h):
    config = config_for(h)
    h.isql('namespace_preexisting', f"CONNECT '{h.database}';\n"
        f"CREATE OR ALTER USER SYSDBA PASSWORD '{h.password}';\n"
        'GRANT CREATE SCHEMA TO USER "WIRE_HOST_TEST";\n'+
        ''.join('CREATE SCHEMA '+identifier(name)+';\n' for name in NAMES)+'COMMIT;\nQUIT;\n',embedded=True)
    installer = INSTALLER.read_text()
    # Qualification must make installation independent of the current search path.
    native(config,'namespace_install','SET SEARCH_PATH TO SYSTEM;\n'+installer,admin=True)
    initial = mapping(config,'namespace_initial')
    require(set(initial)==mapping(config,'namespace_initial_system',system=True) and set(NAMES)<=set(initial) and
            initial['PUBLIC']!=initial['public'], 'Fresh install differs from native schemas/names')
    before = allocator(config,'namespace_initial_allocator')
    counts = service_counts(config,'namespace_initial_objects')
    require(counts==[1,2,1,1], 'Installer did not create exactly schema/table/view/sequence/trigger')
    repeat = native(config,'namespace_install_repeat',installer,admin=True,error=True)
    require('already exists' in repeat['output'].lower() and mapping(config,'namespace_repeat_mapping')==initial and
            allocator(config,'namespace_repeat_allocator')==before and service_counts(config,'namespace_repeat_objects')==counts,
            'Repeated install altered objects, IDs or allocator')
    report = {'checks':['fresh_exact_native_names_and_unsigned_range','repeat_refused_without_changes'],
              'initial_mapping':initial, 'repeat_sqlstates':repeat['sqlstates']}

    failed = h.database.parent/'namespace_failed.fdb'
    h.isql('namespace_failure_db',f'CREATE DATABASE \'{failed}\' DEFAULT CHARACTER SET UTF8;\nQUIT;\n',embedded=True)
    injected = installer.replace('CREATE VIEW "PGWIRE_COMPAT"."NAMESPACES"',
        'SELECT 1/0 FROM "SYSTEM"."RDB$DATABASE";\nCREATE VIEW "PGWIRE_COMPAT"."NAMESPACES"',1)
    failure = native(config,'namespace_install_failure',injected,admin=True,error=True,database=failed)
    require(failure['sqlstates']==['22012'] and service_counts(config,'namespace_failure_objects',failed)==[0,0,0,0],
            'Mid-install failure left partial objects')
    # Also prove that the hook is transactional when failure occurs immediately before COMMIT.
    injected = installer.replace('\nCOMMIT;', '\nSELECT 1/0 FROM "SYSTEM"."RDB$DATABASE";\nCOMMIT;',1)
    native(config,'namespace_install_late_failure',injected,admin=True,error=True,database=failed)
    require(service_counts(config,'namespace_late_failure_objects',failed)==[0,0,0,0], 'Failed install left a trigger')
    native(config,'namespace_install_after_failure',installer,admin=True,database=failed)
    require(service_counts(config,'namespace_after_failure_objects',failed)==[1,2,1,1], 'Clean install after failure failed')
    report['checks'].append('mid_and_late_install_failure_rollback_then_fresh_install')

    foreign = h.database.parent/'namespace_foreign.fdb'
    h.isql('namespace_foreign_db',f'CREATE DATABASE \'{foreign}\' DEFAULT CHARACTER SET UTF8;\n'
        'GRANT CREATE SCHEMA TO USER "WIRE_HOST_TEST";\nCOMMIT;\nQUIT;\n',embedded=True)
    native(config,'namespace_foreign_owner','CREATE SCHEMA "PGWIRE_COMPAT";\n'
        'CREATE TABLE "PGWIRE_COMPAT".KEEP_ME(V INTEGER);\nINSERT INTO "PGWIRE_COMPAT".KEEP_ME VALUES(73);\n'
        'CREATE SEQUENCE "PGWIRE_COMPAT".KEEP_SEQ;\nSELECT GEN_ID("PGWIRE_COMPAT".KEEP_SEQ,9) AS N FROM "SYSTEM"."RDB$DATABASE";\n'
        'COMMIT;\nQUIT;\n',database=foreign)
    collision = native(config,'namespace_foreign_refusal',installer,admin=True,error=True,database=foreign)
    require('already exists' in collision['output'].lower() and service_counts(config,'namespace_foreign_objects',foreign)==[1,1,1,0],
            'Installer altered a foreign service schema')
    output = native(config,'namespace_foreign_preserved',
        'SELECT V AS N FROM "PGWIRE_COMPAT".KEEP_ME;\nSELECT GEN_ID("PGWIRE_COMPAT".KEEP_SEQ,0) AS N FROM "SYSTEM"."RDB$DATABASE";\n'
        'SELECT TRIM("RDB$OWNER_NAME") AS OWNER_NAME FROM "SYSTEM"."RDB$SCHEMAS" WHERE "RDB$SCHEMA_NAME"=\'PGWIRE_COMPAT\';\nROLLBACK;\nQUIT;\n',
        admin=True,database=foreign)['output']
    require(numbers(output)==[73,9] and re.findall(r'^OWNER_NAME\s+(\S+)\s*$',output,re.M)==['WIRE_HOST_TEST'],
            'Foreign schema owner/data/sequence changed')
    report['checks'].append('foreign_schema_collision_preserves_owner_objects_data_and_counter')
    report['installer_sha256'] = hashlib.sha256(INSTALLER.read_bytes()).hexdigest()
    h.summary['namespace_install'] = report


def namespaces_client(config,client):
    from pgwire_1c_smoke import Libpq
    report = {'checks':[], 'denied':[], 'observations':0}
    def record(name):
        report['checks'].append(name)
        (Path(config['work'])/'namespace_progress.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    def query(sql, *, connection=client, state=0, error=None):
        result = connection.query(sql,True)
        require(len(result)==1 and (bool(result[0]['sqlstate']) if error is True else result[0]['sqlstate']==(error or '')) and
                connection.pq.PQtransactionStatus(connection.connection)==state,
                'Namespace query differs: '+sql+' '+repr(result))
        return result[0]
    def wire(connection=client,state=0):
        data = query(LOOKUP,connection=connection,state=state)
        require([column['oid'] for column in data['columns']]==[1043,20], 'Internal IDs must remain native BIGINT / wire OID 20')
        return {name:int(value) for name,value in data['rows']}
    def consistent(label):
        report['observations'] += 1
        actual = wire()
        require(actual==mapping(config,label) and set(actual)==mapping(config,label+'_system',system=True),
                'Native and libpq registry/schema state differ: '+label)
        return actual

    consistent('namespace_client_initial')
    before = allocator(config,'namespace_before_ddl')
    query('CREATE SCHEMA "NS_LIFETIME"')
    created = wire()['NS_LIFETIME']
    query('ALTER SCHEMA "NS_LIFETIME" SET DEFAULT CHARACTER SET "UTF8"')
    query('ALTER SCHEMA "NS_LIFETIME" SET DEFAULT SQL SECURITY DEFINER')
    query('ALTER SCHEMA "NS_LIFETIME" DROP DEFAULT CHARACTER SET')
    query('ALTER SCHEMA "NS_LIFETIME" DROP DEFAULT SQL SECURITY')
    query('CREATE SCHEMA IF NOT EXISTS "NS_LIFETIME"')
    query('CREATE OR ALTER SCHEMA "NS_LIFETIME" DEFAULT CHARACTER SET "UTF8"')
    require(wire()['NS_LIFETIME']==created and allocator(config,'namespace_after_alter')==before+1,
            'ALTER/IF NOT EXISTS allocated a new identity')
    query('CREATE OR ALTER SCHEMA "NS_COA"')
    query('RECREATE SCHEMA NS_RECREATE')
    old = wire()['NS_RECREATE']
    query('RECREATE SCHEMA NS_RECREATE')
    require(wire()['NS_RECREATE']!=old, 'RECREATE reused an ID')
    query('DROP SCHEMA IF EXISTS "NS_RECREATE"')
    after_drop = allocator(config,'namespace_drop_counter')
    query('DROP SCHEMA IF EXISTS "NS_RECREATE"')
    require('NS_RECREATE' not in wire() and allocator(config,'namespace_missing_drop_counter')==after_drop,
            'Missing DROP changed identity/counter')
    query('DROP SCHEMA "NS_LIFETIME"')
    query('CREATE SCHEMA "NS_LIFETIME"')
    require(wire()['NS_LIFETIME']!=created,'Committed DROP+CREATE reused an ID')
    record('native_schema_ddl_via_libpq_create_alter_if_exists_coa_recreate_drop_lifetimes')
    quoted = ['HookCase','hookcase','hook \"Тест']
    for name in quoted:
        query('CREATE SCHEMA '+identifier(name))
        data = client.params(f'SELECT "NAMESPACE_ID" FROM {VIEW} WHERE "NATIVE_NAME"=$1',[name])
        require(not data['sqlstate'] and data['rows']==[[str(wire()[name])]],'Exact quoted-name lookup differs')
    require(len({wire()[name] for name in quoted})==len(quoted),'Quoted schema names were folded')
    for name in quoted:
        query('DROP SCHEMA '+identifier(name))
    record('hook_exact_quoted_case_unicode_space_quote_names')
    query('CREATE TABLE "NS_LIFETIME".T(V INTEGER)')
    query('INSERT INTO "NS_LIFETIME".T VALUES(7)')
    steady = wire(); counter = allocator(config,'namespace_non_schema_before')
    query('ALTER TABLE "NS_LIFETIME".T ADD W INTEGER')
    query('UPDATE "NS_LIFETIME".T SET W=8')
    require(query('SELECT V,W FROM "NS_LIFETIME".T')['rows']==[['7','8']] and wire()==steady and
            allocator(config,'namespace_non_schema_after')==counter,'Non-schema DDL/DML changed registry or native data')
    query('DROP SCHEMA "NS_LIFETIME"',error=True)
    require(wire()==steady,'Failed RESTRICT drop changed registry')
    query('DROP SCHEMA "NS_LIFETIME" CASCADE')
    require('NS_LIFETIME' not in wire(),'CASCADE retained schema identity')
    consistent('namespace_cascade')
    record('non_schema_ddl_dml_unchanged_and_schema_cascade')

    # Native-only checks supplement PgWire; no new command types or rewriting.
    output = native(config,'namespace_native_owner_ddl',
        'CREATE SCHEMA "Native Тест";\nCOMMIT;\n'
        f'SELECT "NAMESPACE_ID" AS N FROM {VIEW} WHERE "NATIVE_NAME"=\'Native Тест\';\n'
        'ALTER SCHEMA "Native Тест" SET DEFAULT CHARACTER SET "UTF8";\nCOMMIT;\n'
        f'SELECT "NAMESPACE_ID" AS N FROM {VIEW} WHERE "NATIVE_NAME"=\'Native Тест\';\n'
        'DROP SCHEMA "Native Тест";\nROLLBACK;\n'
        f'SELECT "NAMESPACE_ID" AS N FROM {VIEW} WHERE "NATIVE_NAME"=\'Native Тест\';\n'
        'DROP SCHEMA "Native Тест";\nCOMMIT;\nQUIT;\n')['output']
    ids = numbers(output); require(len(ids)==3 and len(set(ids))==1 and 'Native Тест' not in wire(),'Native schema DDL identity differs')
    record('ordinary_native_owner_ddl_definer_hook_without_base_grants')
    denied = [f"SELECT * FROM {TABLE}", f"INSERT INTO {TABLE} VALUES('denied',123)",
        f'UPDATE {TABLE} SET "NAMESPACE_ID"=123', f'DELETE FROM {TABLE}', f'DELETE FROM {VIEW}',
        f'SELECT NEXT VALUE FOR {SEQUENCE} FROM "SYSTEM"."RDB$DATABASE"',
        f'ALTER SEQUENCE {SEQUENCE} RESTART WITH 1', f'DROP SEQUENCE {SEQUENCE}',
        f'ALTER TRIGGER {TRIGGER} INACTIVE', f'DROP TRIGGER {TRIGGER}',
        f'ALTER TABLE {TABLE} ADD BAD INTEGER']
    steady = wire(); counter = allocator(config,'namespace_denied_before')
    for i,sql in enumerate(denied):
        result = native(config,'namespace_denied_'+str(i),sql+';\nCOMMIT;\nQUIT;\n',error=True)
        require('permission' in result['output'].lower() or 'access' in result['output'].lower() or
                (sql==f'DELETE FROM {VIEW}' and 'read-only' in result['output'].lower()),
                'Negative case did not fail on access rights/read-only view')
        report['denied'].append({'sql':sql,'sqlstates':result['sqlstates']})
    # DISTINCT makes the view intrinsically read-only, even for its SYSDBA owner.
    result = native(config,'namespace_readonly_view',f'DELETE FROM {VIEW};\nCOMMIT;\nQUIT;\n',admin=True,error=True)
    require('read-only' in result['output'].lower(),'View was not intrinsically read-only')
    require(wire()==steady and allocator(config,'namespace_denied_after')==counter,'Denied writes changed registry/allocator')
    record('view_select_only_base_allocator_trigger_access_denied_and_owner_readonly_view')

    # Constraint failures abort the original schema DDL, not just the registry insert.
    native(config,'namespace_failure_guard',f'ALTER TABLE {TABLE} ADD CONSTRAINT FAIL_NS CHECK ("NATIVE_NAME" <> \'NS_REJECT\');\nCOMMIT;\nQUIT;\n',admin=True)
    query('CREATE SCHEMA "NS_REJECT"',error=True)
    require('NS_REJECT' not in consistent('namespace_rejected_create'),'Failed registry write left native schema')
    native(config,'namespace_remove_guard',f'ALTER TABLE {TABLE} DROP CONSTRAINT FAIL_NS;\nCOMMIT;\nQUIT;\n',admin=True)
    record('registry_insert_failure_rolls_back_original_schema_ddl')
    for i,value in enumerate(('0','-1','4294967296','NULL')):
        native(config,'namespace_range_'+str(i),f"INSERT INTO {TABLE} VALUES('INVALID_RANGE',{value});\nCOMMIT;\nQUIT;\n",admin=True,error=True)
    native(config,'namespace_max_id',f"INSERT INTO {TABLE} VALUES('MAX_ID_TEST',4294967295);\nROLLBACK;\nQUIT;\n",admin=True)
    for i,sql in enumerate((f'INSERT INTO {TABLE} SELECT "NATIVE_NAME",4294967295 FROM {TABLE} WHERE "NATIVE_NAME"=\'PUBLIC\'',
                           f'INSERT INTO {TABLE} SELECT \'DUP_ID\',"NAMESPACE_ID" FROM {TABLE} WHERE "NATIVE_NAME"=\'PUBLIC\'')):
        native(config,'namespace_unique_'+str(i),sql+';\nCOMMIT;\nQUIT;\n',admin=True,error=True)
    record('not_null_unique_name_unique_id_and_uint32_range_constraints')

    query('BEGIN',state=2)
    query('CREATE SCHEMA "NS_TX_ROLLBACK"',state=2)
    first = wire(state=2)['NS_TX_ROLLBACK']
    require('NS_TX_ROLLBACK' not in mapping(config,'namespace_uncommitted_native'), 'Native observer saw uncommitted ID')
    query('ROLLBACK')
    require('NS_TX_ROLLBACK' not in wire(),'Rolled-back CREATE left a row')
    query('CREATE SCHEMA "NS_TX_ROLLBACK"')
    require(wire()['NS_TX_ROLLBACK']!=first,'Rollback gap was reused')
    stable = wire()['NS_TX_ROLLBACK']
    # Native Firebird explicitly prohibits DDL under a user savepoint. Verify that
    # refusal/recovery leaves both catalogs unchanged; do not add a core workaround.
    saved = wire()
    for i,sql in enumerate(('DROP SCHEMA "NS_TX_ROLLBACK"','CREATE SCHEMA "NS_SAVEPOINT"')):
        result = native(config,'namespace_native_savepoint_'+str(i),
            'SET TRANSACTION READ WRITE READ COMMITTED;\nSAVEPOINT S;\n'+sql+';\nCOMMIT;\nQUIT;\n',error=True)
        require(result['sqlstates']==['0A000'] and 'DDL prohibited' in result['output'], 'Native savepoint guard differs')
        query('BEGIN',state=2)
        query('SAVEPOINT S',state=2)
        query(sql,state=3,error='0A000')
        query('ROLLBACK TO SAVEPOINT S',state=2)
        require(wire(state=2)==saved,'Rejected DDL/savepoint recovery changed registry')
        query('COMMIT')
    # DDL completed before the savepoint remains in the transaction after ROLLBACK TO.
    query('BEGIN',state=2)
    query('CREATE SCHEMA "NS_BEFORE_SAVEPOINT"',state=2)
    before_savepoint = wire(state=2)
    query('SAVEPOINT S',state=2)
    query('ROLLBACK TO SAVEPOINT S',state=2)
    require(wire(state=2)==before_savepoint,'Rollback to a later savepoint lost earlier DDL')
    query('ROLLBACK')
    require(wire()==saved and wire()['NS_TX_ROLLBACK']==stable,'Full rollback left pre-savepoint DDL')
    consistent('namespace_transactions')
    report['savepoint_limit'] = 'Native DDL under user savepoint is prohibited (0A000); positive schema-DDL undo to savepoint is unsupported'
    record('commit_rollback_gaps_and_native_savepoint_refusal_recovery')

    other = Libpq(config)
    try:
        require(wire(other)==wire(),'Two attachments disagree on committed IDs')
        query('BEGIN',state=2)
        query('CREATE SCHEMA "NS_CONCURRENT_A"',state=2)
        own = wire(state=2)['NS_CONCURRENT_A']
        query('BEGIN',connection=other,state=2)
        require('NS_CONCURRENT_A' not in wire(other,2),'Other connection saw uncommitted schema')
        query('CREATE SCHEMA "NS_CONCURRENT_B"',connection=other,state=2)
        theirs = wire(other,2)['NS_CONCURRENT_B']
        require(own!=theirs and 'NS_CONCURRENT_B' not in wire(state=2),'Concurrent allocation collided or exposed uncommitted ID')
        query('COMMIT',connection=other)
        query('COMMIT')
        require(wire()==wire(other),'Concurrent commits did not converge')
        record('two_active_transactions_distinct_names_unique_ids_and_commit_visibility')
        query('BEGIN',state=2)
        query('CREATE SCHEMA "NS_CONFLICT"',state=2)
        # First CREATE has completed before either contender starts. NO WAIT makes the
        # native conflict deterministic; no sleeps, scheduler races or timing assertions.
        result = native(config,'namespace_native_conflict','SET TRANSACTION READ WRITE READ COMMITTED NO WAIT;\n'
            'CREATE SCHEMA "NS_CONFLICT";\nCOMMIT;\nQUIT;\n',error=True)
        report['native_conflict_sqlstates'] = result['sqlstates']
        query('SET TRANSACTION READ WRITE READ COMMITTED NO WAIT',connection=other,state=2)
        conflict = query('CREATE SCHEMA "NS_CONFLICT"',connection=other,state=3,error=True)
        require(conflict['sqlstate'] in result['sqlstates'],'PgWire schema conflict differs from native SQLSTATE')
        query('ROLLBACK',connection=other)
        query('ROLLBACK')
        require('NS_CONFLICT' not in wire(other),'Conflict/rollback left a phantom')
        query('CREATE SCHEMA "NS_CONFLICT"',connection=other)
        require(wire()==wire(other),'Retry after conflict did not converge')
        record('same_name_no_wait_conflict_matches_native_and_rollback_retry')
    finally:
        other.close()
    steady = consistent('namespace_readonly_before'); counter = allocator(config,'namespace_readonly_counter_before')
    output = native(config,'namespace_readonly_lookup','SET TRANSACTION READ ONLY;\n'+
        (f'SELECT COUNT(*) AS N FROM {VIEW};\n'*3)+
        f'SELECT COUNT(*) AS N FROM {VIEW} WHERE "NATIVE_NAME"=\'NOT_PRESENT\';\nROLLBACK;\nQUIT;\n')['output']
    require(numbers(output)==[len(steady)]*3+[0],'Read-only lookup or missing-name result differs')
    query('SET TRANSACTION READ ONLY',state=2)
    require(wire(state=2)==steady and wire(state=2)==steady,'Read-only PgWire lookup changed mapping')
    query('ROLLBACK')
    require(wire()==steady and allocator(config,'namespace_readonly_counter_after')==counter,'Lookup wrote registry or advanced sequence')
    record('native_and_libpq_readonly_lookup_no_writes_or_allocation')
    report['final_mapping'] = consistent('namespace_final')
    report['allocator'] = allocator(config,'namespace_final_counter')
    report['source_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return report


def namespaces_restart(h):
    from pgwire_1c_smoke import Libpq, Client
    config = config_for(h)
    before = mapping(config,'namespace_before_restart')
    counter = allocator(config,'namespace_before_restart_counter')
    h.no_attachments()
    h.stop()
    native_port,pg = reserve_ports(2)
    h.start('namespace_restart',native_port,native_port,[{'alias':'PgWire','port':pg}])
    ready = Client(h).login()
    try:
        ready.query_integer()
    finally:
        ready.close()
    config = config_for(h)
    require(mapping(config,'namespace_after_restart')==before and allocator(config,'namespace_after_restart_counter')==counter,
            'Normal restart changed persistent namespace identities')
    config['port'] = pg
    connection = Libpq(config)
    try:
        result = connection.query(LOOKUP,True)[0]
        require(not result['sqlstate'] and {name:int(value) for name,value in result['rows']}==before,
                'New libpq attachment after restart changed mapping')
    finally:
        connection.close()
    return {'passed':True,'mapping':before,'allocator':counter,'server_restarted':True}


CATALOG_INSTALLER = INSTALLER.with_name('install_namespace_catalog.sql')
CATALOG_VIEW = '"PG_CATALOG"."PG_NAMESPACE"'
CATALOG_LOOKUP = f'SELECT "OID","NSPNAME" FROM {CATALOG_VIEW} ORDER BY "OID"'


def catalog_mapping(config,label):
    output = native(config,label,f'SELECT HEX_ENCODE("NSPNAME") AS H,"OID" AS I FROM {CATALOG_VIEW} ORDER BY "OID";\n'
                    'ROLLBACK;\nQUIT;\n')['output']
    names = [bytes.fromhex(value).decode('utf8') for value in re.findall(r'^H[ \t]+([0-9A-Fa-f]+)[ \t]*$',output,re.M)]
    ids = numbers(output,'I')
    require(len(names)==len(ids) and len(set(names))==len(names), 'Native public namespace rows differ')
    return dict(zip(names,ids))


def catalog_snapshot(config,label,include_view=False):
    # Compare native owner, source/BLR and grants; no dependence on wire provenance.
    sql = 'SELECT TRIM("RDB$OWNER_NAME") AS SCHEMA_OWNER FROM "SYSTEM"."RDB$SCHEMAS" WHERE "RDB$SCHEMA_NAME"=\'PG_CATALOG\';\n'
    sql += 'SELECT "RDB$OWNER_NAME","RDB$SQL_SECURITY",HEX_ENCODE(CAST("RDB$FUNCTION_SOURCE" AS VARCHAR(4096) CHARACTER SET "UTF8")) AS SOURCE_HEX,' \
           'HEX_ENCODE(CAST("RDB$FUNCTION_BLR" AS VARCHAR(4096) CHARACTER SET "OCTETS")) AS BLR_HEX ' \
           'FROM "SYSTEM"."RDB$FUNCTIONS" WHERE "RDB$SCHEMA_NAME"=\'PG_CATALOG\' AND "RDB$FUNCTION_NAME"=\'PG_BACKEND_PID\';\n'
    sql += 'SELECT "RDB$USER","RDB$USER_TYPE","RDB$GRANTOR","RDB$PRIVILEGE","RDB$GRANT_OPTION","RDB$RELATION_SCHEMA_NAME","RDB$RELATION_NAME","RDB$OBJECT_TYPE" ' \
           'FROM "SYSTEM"."RDB$USER_PRIVILEGES" WHERE "RDB$RELATION_SCHEMA_NAME"=\'PGWIRE_COMPAT\' OR ' \
           "(RDB$RELATION_SCHEMA_NAME='PG_CATALOG' AND RDB$RELATION_NAME='PG_BACKEND_PID') " \
           "ORDER BY 1,2,3,4,5,6,7,8;\n"
    if include_view:
        sql += 'SELECT "RDB$OWNER_NAME","RDB$RELATION_TYPE",HEX_ENCODE(CAST("RDB$VIEW_SOURCE" AS VARCHAR(4096) CHARACTER SET "UTF8")) AS VIEW_HEX ' \
               'FROM "SYSTEM"."RDB$RELATIONS" WHERE "RDB$SCHEMA_NAME"=\'PG_CATALOG\' AND "RDB$RELATION_NAME"=\'PG_NAMESPACE\';\n'
        sql += 'SELECT "RDB$USER","RDB$USER_TYPE","RDB$GRANTOR","RDB$PRIVILEGE","RDB$GRANT_OPTION" ' \
               'FROM "SYSTEM"."RDB$USER_PRIVILEGES" WHERE "RDB$RELATION_SCHEMA_NAME"=\'PG_CATALOG\' AND "RDB$RELATION_NAME"=\'PG_NAMESPACE\' ' \
               "ORDER BY 1,2,3,4,5;\n"
    return native(config,label,sql+'ROLLBACK;\nQUIT;\n',admin=True)['output']


def catalog_objects(config,label):
    sql = 'SELECT COUNT(*) AS N FROM "SYSTEM"."RDB$SCHEMAS" WHERE "RDB$SCHEMA_NAME"=\'PG_CATALOG\';\n'
    sql += 'SELECT COUNT(*) AS N FROM "SYSTEM"."RDB$RELATIONS" WHERE "RDB$SCHEMA_NAME"=\'PG_CATALOG\' AND "RDB$RELATION_NAME"=\'PG_NAMESPACE\';\n'
    sql += 'SELECT COUNT(*) AS N FROM "SYSTEM"."RDB$USER_PRIVILEGES" WHERE "RDB$RELATION_SCHEMA_NAME"=\'PG_CATALOG\' AND "RDB$RELATION_NAME"=\'PG_NAMESPACE\';\n'
    return numbers(native(config,label,sql+'ROLLBACK;\nQUIT;\n',admin=True)['output'])


def namespace_catalog_fixture(h):
    config = config_for(h)
    h.isql('catalog_preexisting',f"CONNECT '{h.database}';\nCREATE OR ALTER USER SYSDBA PASSWORD '{h.password}';\n"+
        ''.join('CREATE SCHEMA '+identifier(name)+';\n' for name in NAMES)+
        'CREATE TABLE PREEXISTING.PRIVATE_ROWS(V INTEGER);\nGRANT SELECT ON PREEXISTING.PRIVATE_ROWS TO "PUBLIC";\n'
        'COMMIT;\nQUIT;\n',embedded=True)
    registry,installer = INSTALLER.read_text(),CATALOG_INSTALLER.read_text()
    bootstrap = INSTALLER.parent.parent/'compat.sql'
    native(config,'catalog_registry',registry,admin=True)
    native(config,'catalog_bootstrap',bootstrap.read_text(),admin=True)
    before = mapping(config,'catalog_private_before'); counter = allocator(config,'catalog_counter_before')
    original = catalog_snapshot(config,'catalog_bootstrap_before')
    injected = installer.replace('\nCOMMIT;', '\nSELECT 1/0 FROM "SYSTEM"."RDB$DATABASE";\nCOMMIT;',1)
    error = native(config,'catalog_existing_failure',injected,admin=True,error=True)
    require(error['sqlstates']==['22012'] and catalog_objects(config,'catalog_existing_failure_objects')==[1,0,0] and
            mapping(config,'catalog_existing_failure_mapping')==before and allocator(config,'catalog_existing_failure_counter')==counter and
            catalog_snapshot(config,'catalog_existing_failure_bootstrap')==original, 'Failure damaged existing bootstrap/registry')
    native(config,'catalog_install','SET SEARCH_PATH TO SYSTEM;\n'+installer,admin=True)
    require(catalog_mapping(config,'catalog_native_initial')==before and mapping(config,'catalog_private_initial')==before and
            allocator(config,'catalog_counter_after')==counter and catalog_snapshot(config,'catalog_bootstrap_after')==original,
            'Public view changed IDs, allocator, bootstrap or private grants')
    snapshot = catalog_snapshot(config,'catalog_repeat_before',True)
    error = native(config,'catalog_repeat',installer,admin=True,error=True)
    require('already exists' in error['output'].lower() and catalog_snapshot(config,'catalog_repeat_after',True)==snapshot and
            mapping(config,'catalog_repeat_mapping')==before and allocator(config,'catalog_repeat_counter')==counter,
            'Repeat install changed objects/IDs/counter')
    report = {'checks':['existing_bootstrap_preserved_and_atomic_failure','fresh_public_projection_exact_private_ids','repeat_refuses_unchanged'],
              'initial_mapping':before,'bootstrap_sha256':hashlib.sha256(bootstrap.read_bytes()).hexdigest()}

    def fresh(name,with_registry=True):
        database = h.database.parent/('catalog_'+name+'.fdb')
        h.isql('catalog_create_'+name,f'CREATE DATABASE \'{database}\' DEFAULT CHARACTER SET UTF8;\nCOMMIT;\nQUIT;\n',embedded=True)
        cfg = dict(config,database=str(database))
        if with_registry:
            native(cfg,'catalog_registry_'+name,registry,admin=True)
        return cfg

    new = fresh('new_schema')
    prior = mapping(new,'catalog_new_before'); prior_counter = allocator(new,'catalog_new_counter_before')
    error = native(new,'catalog_new_failure',injected,admin=True,error=True)
    require(error['sqlstates']==['22012'] and catalog_objects(new,'catalog_new_failure_objects')==[0,0,0] and
            mapping(new,'catalog_new_failure_mapping')==prior and allocator(new,'catalog_new_failure_counter')>=prior_counter,
            'New schema/view/grants/registry row did not roll back atomically')
    native(new,'catalog_new_install',installer,admin=True)
    current = mapping(new,'catalog_new_mapping')
    require(set(current)==set(prior)|{'PG_CATALOG'} and all(current[name]==value for name,value in prior.items()) and
            catalog_mapping(new,'catalog_new_public')==current, 'New PG_CATALOG was not registered exactly once')
    report['checks'].append('new_catalog_schema_hook_atomic_rollback_gaps_and_success')
    missing = fresh('missing_registry',False)
    error = native(missing,'catalog_without_registry',installer,admin=True,error=True)
    require('PGWIRE_COMPAT' in error['output'] and catalog_objects(missing,'catalog_missing_objects')==[0,0,0],
            'Missing APP-10 did not reject/roll back catalog installation')
    report['missing_registry_sqlstates'] = error['sqlstates']
    report['checks'].append('missing_registry_dependency_error_rolls_back')
    foreign = fresh('collision')
    native(foreign,'catalog_collision_grant','GRANT CREATE SCHEMA TO USER "WIRE_HOST_TEST";\nCOMMIT;\nQUIT;\n',admin=True)
    native(foreign,'catalog_collision_owner','CREATE SCHEMA "PG_CATALOG";\n'
        'CREATE TABLE "PG_CATALOG"."PG_NAMESPACE"("OID" BIGINT,"NSPNAME" VARCHAR(30));\n'
        'INSERT INTO "PG_CATALOG"."PG_NAMESPACE" VALUES(73,\'foreign marker\');\nCOMMIT;\nQUIT;\n')
    original = catalog_snapshot(foreign,'catalog_collision_before',True)
    prior = mapping(foreign,'catalog_collision_private_before'); prior_counter = allocator(foreign,'catalog_collision_counter_before')
    error = native(foreign,'catalog_collision_install',installer,admin=True,error=True)
    require('already exists' in error['output'].lower() and catalog_snapshot(foreign,'catalog_collision_after',True)==original and
            mapping(foreign,'catalog_collision_private_after')==prior and allocator(foreign,'catalog_collision_counter_after')==prior_counter and
            catalog_mapping(foreign,'catalog_collision_data')=={'foreign marker':73}, 'Collision damaged foreign object/owner/data/grants')
    report['checks'].append('foreign_pg_namespace_collision_preserves_owner_object_data_grants')
    report['installer_sha256'] = hashlib.sha256(CATALOG_INSTALLER.read_bytes()).hexdigest()
    report['registry_installer_sha256'] = hashlib.sha256(INSTALLER.read_bytes()).hexdigest()
    h.summary['namespace_catalog_install'] = report


def namespace_catalog_client(config,client):
    import ctypes as C
    import struct
    from pgwire_1c_smoke import Libpq
    report = {'checks':[], 'queries':0, 'metadata':{}}
    def capture_metadata(connection):
        for name in ('PQfsize','PQfmod','PQfformat'):
            function=getattr(connection.pq,name);function.restype=C.c_int;function.argtypes=[C.c_void_p,C.c_int]
        original_result = connection.result
        def result(handle,values=False,binary=False):
            require(handle,'libpq returned no result')
            metadata = [{'size':connection.pq.PQfsize(handle,i),'modifier':connection.pq.PQfmod(handle,i),
                         'format':connection.pq.PQfformat(handle,i)} for i in range(connection.pq.PQnfields(handle))]
            data = original_result(handle,values,binary)
            data['metadata'] = metadata
            return data
        connection.result = result
    capture_metadata(client)
    def record(name):
        report['checks'].append(name)
        (Path(config['work'])/'namespace_catalog_progress.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    def checked(data, *, state=0,error='',rows=None,oids=None,binary=False):
        require(data['sqlstate']==error and client.pq.PQtransactionStatus(client.connection)==state and
                (rows is None or data.get('rows')==rows) and (oids is None or [c['oid'] for c in data['columns']]==oids),
                'Catalog result differs: '+repr(data))
        if oids is not None:
            require(data['metadata']==[{'size':8 if oid==20 else -1,'modifier':-1,'format':int(binary)} for oid in oids],
                    'Catalog native size/typmod/format differs')
        report['queries'] += 1
        return data
    def query(sql,**expected):
        data = client.query(sql,True)
        require(len(data)==1,'Catalog Simple Query returned unexpected results')
        try:
            return checked(data[0],**expected)
        except RuntimeError as error:
            raise RuntimeError(sql+': '+str(error)) from error
    def params(sql,values=(),*,binary=False,prepared=False,formats=(),**expected):
        return checked(client.params(sql,values,prepared=prepared,formats=formats,result_format=int(binary)),binary=binary,**expected)
    def wire(state=0):
        data = query(CATALOG_LOOKUP,state=state,oids=[20,1043])
        require([column['name'] for column in data['columns']]==['OID','NSPNAME'],'Native unquoted labels changed case')
        private = query(LOOKUP,state=state,oids=[1043,20])
        values = {name:int(value) for value,name in data['rows']}
        require(values=={name:int(value) for name,value in private['rows']},'Public view differs from private mapping in current transaction')
        return values
    def prepare(name,sql,input_oid):
        checked(client.result(client.pq.PQprepare(client.connection,name.encode(),sql.encode(),0,None)))
        data = checked(client.result(client.pq.PQdescribePrepared(client.connection,name.encode())),oids=[20,1043])
        require(data['parameter_oids']==[input_oid],'SQL-derived parameter OID differs')
        report['metadata'][name]={'input':[input_oid],'output':[20,1043],'labels':[c['name'] for c in data['columns']]}

    initial = wire(); counter = allocator(config,'catalog_client_counter_before')
    require(initial==mapping(config,'catalog_client_private')==catalog_mapping(config,'catalog_client_public') and
            initial['PUBLIC']!=initial['public'] and all(name in initial for name in NAMES), 'Native/libpq exact names or IDs differ')
    text_rows = [[str(value),name] for name,value in sorted(initial.items(),key=lambda item:item[1])]
    binary_rows = [[struct.pack('!q',int(value)).hex(),name.encode().hex()] for value,name in text_rows]
    params(CATALOG_LOOKUP,rows=text_rows,oids=[20,1043])
    params(CATALOG_LOOKUP,binary=True,rows=binary_rows,oids=[20,1043])
    prepare('catalog_by_name',f'SELECT "OID","NSPNAME" FROM {CATALOG_VIEW} WHERE "NSPNAME"=$1',1043)
    prepare('catalog_by_id',f'SELECT "OID","NSPNAME" FROM {CATALOG_VIEW} WHERE "OID"=$1',20)
    for name,value in initial.items():
        query(f'SELECT "OID","NSPNAME" FROM {CATALOG_VIEW} WHERE "OID"={value}',rows=[[str(value),name]],oids=[20,1043])
        literal = "'"+name.replace("'","''")+"'"
        query(f'SELECT "OID","NSPNAME" FROM {CATALOG_VIEW} WHERE "NSPNAME"={literal}',rows=[[str(value),name]],oids=[20,1043])
        for binary in (False,True):
            expected = [[struct.pack('!q',value).hex(),name.encode().hex()]] if binary else [[str(value),name]]
            params('catalog_by_name',[name],binary=binary,prepared=True,formats=[int(binary)],rows=expected,oids=[20,1043])
            params('catalog_by_id',[struct.pack('!q',value) if binary else str(value)],binary=binary,prepared=True,
                   formats=[int(binary)],rows=expected,oids=[20,1043])
    params('catalog_by_name',['absent'],prepared=True,rows=[],oids=[20,1043])
    params('catalog_by_id',['4294967296'],prepared=True,rows=[],oids=[20,1043])
    record('native_simple_extended_prepared_exact_names_bigint8_and_utf8_binary_metadata')
    prefix = f'SELECT "OID" FROM {CATALOG_VIEW} WHERE "NSPNAME"=\'PUBLIC\''
    constant = 'SELECT CAST(4294967296 AS BIGINT) AS "OID" FROM "SYSTEM"."RDB$DATABASE"'
    expressions = [(prefix.replace('SELECT "OID"','SELECT "OID" AS "oid_alias"'),[initial['PUBLIC']]),
        (f'SELECT D."OID" FROM ({prefix}) D',[initial['PUBLIC']]),
        (prefix.replace('SELECT "OID"','SELECT CAST("OID" AS BIGINT) AS "OID"'),[initial['PUBLIC']]),
        (prefix+' UNION ALL '+constant+' ORDER BY 1',[initial['PUBLIC'],4294967296]),
        (constant+' UNION ALL '+prefix+' ORDER BY 1',[initial['PUBLIC'],4294967296])]
    for sql,values in expressions:
        data = query(sql,rows=[[str(value)] for value in values],oids=[20])
        require([column['name'] for column in data['columns']]==['oid_alias' if sql==expressions[0][0] else 'OID'],
                'Alias/native expression label differs')
        params(sql,binary=True,rows=[[struct.pack('!q',value).hex()] for value in values],oids=[20])
    record('alias_derived_cast_and_both_union_orders_keep_bigint_without_uint32_truncation')
    snapshot = catalog_snapshot(config,'catalog_client_bootstrap_before')
    identity = query('SELECT "PG_CATALOG"."PG_BACKEND_PID"() AS "PID"')['rows']
    require(len(identity)==1 and int(identity[0][0])>0 and query('SELECT CURRENT_CONNECTION AS PID')['rows']==identity,
            'Bootstrap function stopped returning the real attachment ID')
    output = native(config,'catalog_native_backend_pid','SELECT "PG_CATALOG"."PG_BACKEND_PID"() AS N,CURRENT_CONNECTION AS NATIVE_ID '
        'FROM "SYSTEM"."RDB$DATABASE";\nROLLBACK;\nQUIT;\n')['output']
    require(numbers(output)==numbers(output,'NATIVE_ID'),'Native bootstrap function changed')
    query('CREATE SCHEMA "NS_CATALOG_NO_GRANT"',error='42000')
    denied = native(config,'catalog_no_schema_grant','CREATE SCHEMA "NS_CATALOG_NO_GRANT";\nCOMMIT;\nQUIT;\n',error=True)
    require('permission' in denied['output'].lower(),'CREATE SCHEMA failed for a reason other than privileges')
    denied = native(config,'catalog_no_usage','SELECT * FROM PREEXISTING.PRIVATE_ROWS;\nROLLBACK;\nQUIT;\n',error=True)
    require('USAGE' in denied['output'] and 'PREEXISTING' in denied['output'],'Expected missing schema USAGE')
    require('PREEXISTING' in initial,'Catalog filtered a namespace without schema USAGE')
    for admin in (False,True):
        for i,sql in enumerate((f"INSERT INTO {CATALOG_VIEW} VALUES(123,'bad')",f'UPDATE {CATALOG_VIEW} SET "OID"=123',f'DELETE FROM {CATALOG_VIEW}')):
            denied = native(config,f'catalog_readonly_{int(admin)}_{i}',sql+';\nCOMMIT;\nQUIT;\n',admin=admin,error=True)
            require('read-only' in denied['output'].lower() or (not admin and 'permission' in denied['output'].lower()),
                    'Public view write did not fail on read-only/access rules')
    for label,sql in (('base',f'SELECT * FROM {TABLE}'),('allocator',f'SELECT NEXT VALUE FOR {SEQUENCE} FROM "SYSTEM"."RDB$DATABASE"')):
        denied = native(config,'catalog_no_'+label+'_grants',sql+';\nROLLBACK;\nQUIT;\n',error=True)
        require('permission' in denied['output'].lower(),'Expected missing '+label+' privileges')
    query('SET TRANSACTION READ ONLY',state=2)
    require(wire(state=2)==initial and wire(state=2)==initial,'Read-only catalog lookup changed mapping')
    query('ROLLBACK')
    output = native(config,'catalog_native_readonly','SET TRANSACTION READ ONLY;\n'+
        (f'SELECT COUNT(*) AS N FROM {CATALOG_VIEW};\n'*2)+
        f'SELECT COUNT(*) AS N FROM {CATALOG_VIEW} WHERE "NSPNAME"=\'absent\';\nROLLBACK;\nQUIT;\n')['output']
    require(numbers(output)==[len(initial),len(initial),0],'Native read-only lookup differs')
    require(allocator(config,'catalog_client_counter_readonly')==counter and
            catalog_snapshot(config,'catalog_client_bootstrap_after')==snapshot,'Read/denied write changed allocator/bootstrap/private grants')
    record('read_only_owner_and_user_no_new_privileges_no_visibility_filter_bootstrap_preserved')

    # Schema permissions are fixture-only. A new attachment loads the new grant;
    # the original reader stays connected to observe committed/uncommitted rows.
    native(config,'catalog_fixture_ddl_grant','GRANT CREATE SCHEMA TO USER "WIRE_HOST_TEST";\nCOMMIT;\nQUIT;\n',admin=True)
    other = client
    client = Libpq(config)
    capture_metadata(client)
    def other_map():
        data=other.query(CATALOG_LOOKUP,True)[0]
        require(not data['sqlstate'],'Second attachment could not read catalog')
        values = {name:int(value) for value,name in data['rows']}
        private = other.query(LOOKUP,True)[0]
        require(not private['sqlstate'] and values=={name:int(value) for name,value in private['rows']},
                'Second attachment public/private mapping differs')
        return values
    try:
        prepare('catalog_by_name',f'SELECT "OID","NSPNAME" FROM {CATALOG_VIEW} WHERE "NSPNAME"=$1',1043)
        require(other_map()==initial,'Second attachment has different initial IDs')
        query('BEGIN',state=2)
        query('CREATE SCHEMA "NS_CATALOG_TX"',state=2)
        first = wire(state=2)['NS_CATALOG_TX']
        require('NS_CATALOG_TX' not in other_map() and 'NS_CATALOG_TX' not in catalog_mapping(config,'catalog_uncommitted_native'),
                'Public view exposed another transaction\'s uncommitted schema')
        query('COMMIT')
        require(other_map()['NS_CATALOG_TX']==first,'Committed identity not visible to second attachment')
        query('ALTER SCHEMA "NS_CATALOG_TX" SET DEFAULT CHARACTER SET "UTF8"')
        require(wire()['NS_CATALOG_TX']==first,'ALTER changed public identity')
        query('BEGIN',state=2)
        query('DROP SCHEMA "NS_CATALOG_TX"',state=2)
        require('NS_CATALOG_TX' not in wire(state=2) and other_map()['NS_CATALOG_TX']==first,'Uncommitted DROP visibility differs')
        query('ROLLBACK')
        require(wire()['NS_CATALOG_TX']==first,'DROP rollback lost public identity')
        query('DROP SCHEMA "NS_CATALOG_TX"')
        require('NS_CATALOG_TX' not in other_map(),'Committed DROP retained public identity')
        query('CREATE SCHEMA "NS_CATALOG_TX"')
        second=wire()['NS_CATALOG_TX'];require(second!=first and other_map()['NS_CATALOG_TX']==second,'Recreated public identity reused old ID')
        params('catalog_by_name',['NS_CATALOG_TX'],prepared=True,rows=[[str(second),'NS_CATALOG_TX']],oids=[20,1043])
        query('BEGIN',state=2)
        query('CREATE SCHEMA "NS_CATALOG_ROLLBACK"',state=2)
        require('NS_CATALOG_ROLLBACK' in wire(state=2),'Own uncommitted public row missing')
        query('ROLLBACK')
        require('NS_CATALOG_ROLLBACK' not in wire() and other_map()==wire(),'CREATE rollback left phantom catalog row')
        require(wire()==mapping(config,'catalog_final_private')==catalog_mapping(config,'catalog_final_public'), 'Final catalog/private/native state differs')
    finally:
        client.close()
    record('schema_create_alter_drop_lifetime_commit_full_rollback_and_second_attachment')
    report['source_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report['scope'] = 'Native BIGINT/VARCHAR values and metadata only; PG oid/name/regnamespace, nspowner/nspacl and selected04 remain outside APP-11'
    return report
