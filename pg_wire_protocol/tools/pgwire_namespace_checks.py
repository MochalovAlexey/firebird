"""APP-10 SQL-only namespace registry, using the existing private 1c harness."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from wire_host_smoke import require, reserve_ports

INSTALLER = Path(__file__).resolve().parents[1]/'1c/sql/install_namespace_registry.sql'
VIEW = 'PGWIRE_COMPAT.NAMESPACES'
TABLE = 'PGWIRE_COMPAT.NAMESPACE_IDS'
SEQUENCE = 'PGWIRE_COMPAT.NAMESPACE_SEQ'
TRIGGER = 'PGWIRE_COMPAT.NAMESPACE_DDL'
NAMES = ['PREEXISTING', 'public', 'MiXeD', 'Схема с пробелом', 'A"quote', ' leading', 'Я'*56]
LOOKUP = f'SELECT NATIVE_NAME,NAMESPACE_ID FROM {VIEW} ORDER BY NAMESPACE_ID'


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
    sql = ('SELECT HEX_ENCODE(TRIM(TRAILING FROM RDB$SCHEMA_NAME)) AS H FROM SYSTEM.RDB$SCHEMAS'
           if system else f'SELECT HEX_ENCODE(NATIVE_NAME) AS H,NAMESPACE_ID AS I FROM {VIEW} ORDER BY NAMESPACE_ID')
    output = native(config,label,sql+';\nROLLBACK;\nQUIT;\n',admin=system)['output']
    names = [bytes.fromhex(value).decode('utf8') for value in re.findall(r'^H[ \t]+([0-9A-Fa-f]+)[ \t]*$',output,re.M)]
    if system:
        return set(names)
    ids = numbers(output,'I')
    require(len(names)==len(ids) and len(set(names))==len(names) and len(set(ids))==len(ids) and
            all(1<=value<=4294967295 for value in ids), 'Native registry uniqueness/range differs')
    return dict(zip(names,ids))


def allocator(config,label):
    output = native(config,label,f'SELECT GEN_ID({SEQUENCE},0) AS N FROM SYSTEM.RDB$DATABASE;\nROLLBACK;\nQUIT;\n',admin=True)
    values = numbers(output['output']); require(len(values)==1,'Allocator observation missing')
    return values[0]


def service_counts(config,label,database=None):
    sql = ''.join(f"SELECT COUNT(*) AS N FROM SYSTEM.{table} WHERE RDB$SCHEMA_NAME='PGWIRE_COMPAT'" +
                  (" AND COALESCE(RDB$SYSTEM_FLAG,0)=0" if table=='RDB$TRIGGERS' else '') + ';\n'
                  for table in ('RDB$SCHEMAS','RDB$RELATIONS','RDB$GENERATORS','RDB$TRIGGERS'))
    return numbers(native(config,label,sql+'ROLLBACK;\nQUIT;\n',admin=True,database=database)['output'])


def namespaces_fixture(h):
    config = config_for(h)
    h.isql('namespace_preexisting', f"CONNECT '{h.database}';\n"
        f"CREATE OR ALTER USER SYSDBA PASSWORD '{h.password}';\n"
        'GRANT CREATE SCHEMA TO USER WIRE_HOST_TEST;\n'+
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
    h.isql('namespace_failure_db',f"CREATE DATABASE '{failed}' DEFAULT CHARACTER SET UTF8;\nQUIT;\n",embedded=True)
    injected = installer.replace('CREATE VIEW PGWIRE_COMPAT.NAMESPACES',
        'SELECT 1/0 FROM SYSTEM.RDB$DATABASE;\nCREATE VIEW PGWIRE_COMPAT.NAMESPACES',1)
    failure = native(config,'namespace_install_failure',injected,admin=True,error=True,database=failed)
    require(failure['sqlstates']==['22012'] and service_counts(config,'namespace_failure_objects',failed)==[0,0,0,0],
            'Mid-install failure left partial objects')
    # Also prove that the hook is transactional when failure occurs immediately before COMMIT.
    injected = installer.replace('\nCOMMIT;', '\nSELECT 1/0 FROM SYSTEM.RDB$DATABASE;\nCOMMIT;',1)
    native(config,'namespace_install_late_failure',injected,admin=True,error=True,database=failed)
    require(service_counts(config,'namespace_late_failure_objects',failed)==[0,0,0,0], 'Failed install left a trigger')
    native(config,'namespace_install_after_failure',installer,admin=True,database=failed)
    require(service_counts(config,'namespace_after_failure_objects',failed)==[1,2,1,1], 'Clean install after failure failed')
    report['checks'].append('mid_and_late_install_failure_rollback_then_fresh_install')

    foreign = h.database.parent/'namespace_foreign.fdb'
    h.isql('namespace_foreign_db',f"CREATE DATABASE '{foreign}' DEFAULT CHARACTER SET UTF8;\n"
        'GRANT CREATE SCHEMA TO USER WIRE_HOST_TEST;\nCOMMIT;\nQUIT;\n',embedded=True)
    native(config,'namespace_foreign_owner','CREATE SCHEMA PGWIRE_COMPAT;\n'
        'CREATE TABLE PGWIRE_COMPAT.KEEP_ME(V INTEGER);\nINSERT INTO PGWIRE_COMPAT.KEEP_ME VALUES(73);\n'
        'CREATE SEQUENCE PGWIRE_COMPAT.KEEP_SEQ;\nSELECT GEN_ID(PGWIRE_COMPAT.KEEP_SEQ,9) AS N FROM SYSTEM.RDB$DATABASE;\n'
        'COMMIT;\nQUIT;\n',database=foreign)
    collision = native(config,'namespace_foreign_refusal',installer,admin=True,error=True,database=foreign)
    require('already exists' in collision['output'].lower() and service_counts(config,'namespace_foreign_objects',foreign)==[1,1,1,0],
            'Installer altered a foreign service schema')
    output = native(config,'namespace_foreign_preserved',
        "SELECT V AS N FROM PGWIRE_COMPAT.KEEP_ME;\nSELECT GEN_ID(PGWIRE_COMPAT.KEEP_SEQ,0) AS N FROM SYSTEM.RDB$DATABASE;\n"
        "SELECT TRIM(RDB$OWNER_NAME) AS OWNER_NAME FROM SYSTEM.RDB$SCHEMAS WHERE RDB$SCHEMA_NAME='PGWIRE_COMPAT';\nROLLBACK;\nQUIT;\n",
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
    query('CREATE SCHEMA NS_LIFETIME')
    created = wire()['NS_LIFETIME']
    query('ALTER SCHEMA NS_LIFETIME SET DEFAULT CHARACTER SET UTF8')
    query('ALTER SCHEMA NS_LIFETIME SET DEFAULT SQL SECURITY DEFINER')
    query('ALTER SCHEMA NS_LIFETIME DROP DEFAULT CHARACTER SET')
    query('ALTER SCHEMA NS_LIFETIME DROP DEFAULT SQL SECURITY')
    query('CREATE SCHEMA IF NOT EXISTS NS_LIFETIME')
    query('CREATE OR ALTER SCHEMA NS_LIFETIME DEFAULT CHARACTER SET UTF8')
    require(wire()['NS_LIFETIME']==created and allocator(config,'namespace_after_alter')==before+1,
            'ALTER/IF NOT EXISTS allocated a new identity')
    query('CREATE OR ALTER SCHEMA NS_COA')
    query('RECREATE SCHEMA NS_RECREATE')
    old = wire()['NS_RECREATE']
    query('RECREATE SCHEMA NS_RECREATE')
    require(wire()['NS_RECREATE']!=old, 'RECREATE reused an ID')
    query('DROP SCHEMA IF EXISTS NS_RECREATE')
    after_drop = allocator(config,'namespace_drop_counter')
    query('DROP SCHEMA IF EXISTS NS_RECREATE')
    require('NS_RECREATE' not in wire() and allocator(config,'namespace_missing_drop_counter')==after_drop,
            'Missing DROP changed identity/counter')
    query('DROP SCHEMA NS_LIFETIME')
    query('CREATE SCHEMA NS_LIFETIME')
    require(wire()['NS_LIFETIME']!=created,'Committed DROP+CREATE reused an ID')
    record('native_schema_ddl_via_libpq_create_alter_if_exists_coa_recreate_drop_lifetimes')
    quoted = ['HookCase','hookcase','hook \"Тест']
    for name in quoted:
        query('CREATE SCHEMA '+identifier(name))
        data = client.params(f'SELECT NAMESPACE_ID FROM {VIEW} WHERE NATIVE_NAME=$1',[name])
        require(not data['sqlstate'] and data['rows']==[[str(wire()[name])]],'Exact quoted-name lookup differs')
    require(len({wire()[name] for name in quoted})==len(quoted),'Quoted schema names were folded')
    for name in quoted:
        query('DROP SCHEMA '+identifier(name))
    record('hook_exact_quoted_case_unicode_space_quote_names')
    query('CREATE TABLE NS_LIFETIME.T(V INTEGER)')
    query('INSERT INTO NS_LIFETIME.T VALUES(7)')
    steady = wire(); counter = allocator(config,'namespace_non_schema_before')
    query('ALTER TABLE NS_LIFETIME.T ADD W INTEGER')
    query('UPDATE NS_LIFETIME.T SET W=8')
    require(query('SELECT V,W FROM NS_LIFETIME.T')['rows']==[['7','8']] and wire()==steady and
            allocator(config,'namespace_non_schema_after')==counter,'Non-schema DDL/DML changed registry or native data')
    query('DROP SCHEMA NS_LIFETIME',error=True)
    require(wire()==steady,'Failed RESTRICT drop changed registry')
    query('DROP SCHEMA NS_LIFETIME CASCADE')
    require('NS_LIFETIME' not in wire(),'CASCADE retained schema identity')
    consistent('namespace_cascade')
    record('non_schema_ddl_dml_unchanged_and_schema_cascade')

    # Native-only checks supplement PgWire; no new command types or rewriting.
    output = native(config,'namespace_native_owner_ddl',
        'CREATE SCHEMA "Native Тест";\nCOMMIT;\n'
        f"SELECT NAMESPACE_ID AS N FROM {VIEW} WHERE NATIVE_NAME='Native Тест';\n"
        'ALTER SCHEMA "Native Тест" SET DEFAULT CHARACTER SET UTF8;\nCOMMIT;\n'
        f"SELECT NAMESPACE_ID AS N FROM {VIEW} WHERE NATIVE_NAME='Native Тест';\n"
        'DROP SCHEMA "Native Тест";\nROLLBACK;\n'
        f"SELECT NAMESPACE_ID AS N FROM {VIEW} WHERE NATIVE_NAME='Native Тест';\n"
        'DROP SCHEMA "Native Тест";\nCOMMIT;\nQUIT;\n')['output']
    ids = numbers(output); require(len(ids)==3 and len(set(ids))==1 and 'Native Тест' not in wire(),'Native schema DDL identity differs')
    record('ordinary_native_owner_ddl_definer_hook_without_base_grants')
    denied = [f"SELECT * FROM {TABLE}", f"INSERT INTO {TABLE} VALUES('denied',123)",
        f'UPDATE {TABLE} SET NAMESPACE_ID=123', f'DELETE FROM {TABLE}', f'DELETE FROM {VIEW}',
        f'SELECT NEXT VALUE FOR {SEQUENCE} FROM SYSTEM.RDB$DATABASE',
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
    native(config,'namespace_failure_guard',f"ALTER TABLE {TABLE} ADD CONSTRAINT FAIL_NS CHECK (NATIVE_NAME <> 'NS_REJECT');\nCOMMIT;\nQUIT;\n",admin=True)
    query('CREATE SCHEMA NS_REJECT',error=True)
    require('NS_REJECT' not in consistent('namespace_rejected_create'),'Failed registry write left native schema')
    native(config,'namespace_remove_guard',f'ALTER TABLE {TABLE} DROP CONSTRAINT FAIL_NS;\nCOMMIT;\nQUIT;\n',admin=True)
    record('registry_insert_failure_rolls_back_original_schema_ddl')
    for i,value in enumerate(('0','-1','4294967296','NULL')):
        native(config,'namespace_range_'+str(i),f"INSERT INTO {TABLE} VALUES('INVALID_RANGE',{value});\nCOMMIT;\nQUIT;\n",admin=True,error=True)
    native(config,'namespace_max_id',f"INSERT INTO {TABLE} VALUES('MAX_ID_TEST',4294967295);\nROLLBACK;\nQUIT;\n",admin=True)
    for i,sql in enumerate((f"INSERT INTO {TABLE} SELECT NATIVE_NAME,4294967295 FROM {TABLE} WHERE NATIVE_NAME='PUBLIC'",
                           f"INSERT INTO {TABLE} SELECT 'DUP_ID',NAMESPACE_ID FROM {TABLE} WHERE NATIVE_NAME='PUBLIC'")):
        native(config,'namespace_unique_'+str(i),sql+';\nCOMMIT;\nQUIT;\n',admin=True,error=True)
    record('not_null_unique_name_unique_id_and_uint32_range_constraints')

    query('BEGIN',state=2)
    query('CREATE SCHEMA NS_TX_ROLLBACK',state=2)
    first = wire(state=2)['NS_TX_ROLLBACK']
    require('NS_TX_ROLLBACK' not in mapping(config,'namespace_uncommitted_native'), 'Native observer saw uncommitted ID')
    query('ROLLBACK')
    require('NS_TX_ROLLBACK' not in wire(),'Rolled-back CREATE left a row')
    query('CREATE SCHEMA NS_TX_ROLLBACK')
    require(wire()['NS_TX_ROLLBACK']!=first,'Rollback gap was reused')
    stable = wire()['NS_TX_ROLLBACK']
    # Native Firebird explicitly prohibits DDL under a user savepoint. Verify that
    # refusal/recovery leaves both catalogs unchanged; do not add a core workaround.
    saved = wire()
    for i,sql in enumerate(('DROP SCHEMA NS_TX_ROLLBACK','CREATE SCHEMA NS_SAVEPOINT')):
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
    query('CREATE SCHEMA NS_BEFORE_SAVEPOINT',state=2)
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
        query('CREATE SCHEMA NS_CONCURRENT_A',state=2)
        own = wire(state=2)['NS_CONCURRENT_A']
        query('BEGIN',connection=other,state=2)
        require('NS_CONCURRENT_A' not in wire(other,2),'Other connection saw uncommitted schema')
        query('CREATE SCHEMA NS_CONCURRENT_B',connection=other,state=2)
        theirs = wire(other,2)['NS_CONCURRENT_B']
        require(own!=theirs and 'NS_CONCURRENT_B' not in wire(state=2),'Concurrent allocation collided or exposed uncommitted ID')
        query('COMMIT',connection=other)
        query('COMMIT')
        require(wire()==wire(other),'Concurrent commits did not converge')
        record('two_active_transactions_distinct_names_unique_ids_and_commit_visibility')
        query('BEGIN',state=2)
        query('CREATE SCHEMA NS_CONFLICT',state=2)
        # First CREATE has completed before either contender starts. NO WAIT makes the
        # native conflict deterministic; no sleeps, scheduler races or timing assertions.
        result = native(config,'namespace_native_conflict','SET TRANSACTION READ WRITE READ COMMITTED NO WAIT;\n'
            'CREATE SCHEMA NS_CONFLICT;\nCOMMIT;\nQUIT;\n',error=True)
        report['native_conflict_sqlstates'] = result['sqlstates']
        query('SET TRANSACTION READ WRITE READ COMMITTED NO WAIT',connection=other,state=2)
        conflict = query('CREATE SCHEMA NS_CONFLICT',connection=other,state=3,error=True)
        require(conflict['sqlstate'] in result['sqlstates'],'PgWire schema conflict differs from native SQLSTATE')
        query('ROLLBACK',connection=other)
        query('ROLLBACK')
        require('NS_CONFLICT' not in wire(other),'Conflict/rollback left a phantom')
        query('CREATE SCHEMA NS_CONFLICT',connection=other)
        require(wire()==wire(other),'Retry after conflict did not converge')
        record('same_name_no_wait_conflict_matches_native_and_rollback_retry')
    finally:
        other.close()
    steady = consistent('namespace_readonly_before'); counter = allocator(config,'namespace_readonly_counter_before')
    output = native(config,'namespace_readonly_lookup','SET TRANSACTION READ ONLY;\n'+
        (f'SELECT COUNT(*) AS N FROM {VIEW};\n'*3)+
        f"SELECT COUNT(*) AS N FROM {VIEW} WHERE NATIVE_NAME='NOT_PRESENT';\nROLLBACK;\nQUIT;\n")['output']
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
