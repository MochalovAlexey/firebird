#!/usr/bin/env python3
"""APP-16 oracle probes using the existing libpq helper and synthetic values.

Captured application SQL is read from an external directory, never embedded here.
All server state and result artifacts belong to a fresh private work directory.
"""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from pgwire_1c_smoke import Libpq
from wire_host_smoke import reserve_ports


class TypeLibpq(Libpq):
    def __init__(self, config):
        super().__init__(config)
        for name in ('PQfmod', 'PQfsize', 'PQfformat'):
            fn = getattr(self.pq, name)
            fn.argtypes, fn.restype = [C.c_void_p, C.c_int], C.c_int

    def result(self, handle, values=False, binary=False):
        message = self.pq.PQresultErrorField(handle, ord('M'))
        message = message.decode('utf8') if message else ''
        extra = [{name[3:]: getattr(self.pq, name)(handle, i)
                  for name in ('PQfmod', 'PQfsize', 'PQfformat')}
                 for i in range(self.pq.PQnfields(handle))]
        result = super().result(handle, values, binary)
        if message:
            result['message'] = message
        for column, fields in zip(result['columns'], extra):
            column.update(fields)
        return result


def run(work, capture, pgroot, expression_details=False, profile_families_only=False):
    work.mkdir(parents=True, exist_ok=False)
    report = {'synthetic_fixtures': True, 'cases': {}, 'sources': {}}
    def command(*args):
        result = subprocess.run([str(pgroot/'bin'/args[0]), *map(str, args[1:])],
                                capture_output=True, text=True, timeout=60)
        with (work/'commands.log').open('a') as log:
            log.write(repr(args)+'\n'+result.stdout+result.stderr)
        if result.returncode:
            raise RuntimeError(f'{args[0]} failed: {result.stderr}')
        return result
    for path in [capture/'schema.sql', capture/'profile/families.jsonl',
                 pgroot/'share/extension/mchar--2.3.sql', pgroot/'share/extension/mchar.control']:
        report['sources'][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    families = [json.loads(line) for line in (capture/'profile/families.jsonl').read_text().splitlines()]
    operations = {}
    for family in families:
        sql = family.get('normalized_for_analysis', '')
        for name in set(re.findall(r'\b([a-zA-Z_][a-zA-Z_0-9]*)\s*\(', sql)):
            operations[name.lower()] = operations.get(name.lower(), 0) + 1
    report['family_operations'] = operations
    command('initdb', '-D', work/'data', '-U', 'WIRE_HOST_TEST', '-A', 'trust',
            '--encoding=UTF8', '--locale=C.UTF-8')
    port = reserve_ports(1)[0]
    started = False
    client = None
    def query(label, sql):
        result = client.query(sql, True)
        report['cases'][label] = result
        return result
    def params(label, sql, values=(), oids=(), formats=(), result_format=0):
        result = client.params(sql, values, oids, formats, result_format=result_format)
        report['cases'][label] = result
        return result
    try:
        command('pg_ctl', '-D', work/'data', '-l', work/'server.log', '-o',
                f'-h 127.0.0.1 -p {port} -k {work}', '-w', 'start')
        started = True
        command('createdb', '-h', '127.0.0.1', '-p', port, '-U', 'WIRE_HOST_TEST', 'wire_host')
        client = TypeLibpq({'port': port, 'password': ''})
        report['libpq_version'] = client.pq.PQlibVersion()
        for path in (pgroot/'bin/postgres', pgroot/'lib/mchar.so', pgroot/'lib/libpq.so.5'):
            if path.exists():
                report['sources'][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
                (work/(path.name+'.ldd')).write_text(subprocess.check_output(['ldd', str(path)], text=True))
        query('versions', "SELECT version(), current_setting('server_encoding'), "
              "current_setting('lc_collate'), (SELECT collversion FROM pg_collation WHERE collname='unicode')")
        query('extension', 'CREATE EXTENSION mchar')
        query('extension_version', "SELECT extversion FROM pg_extension WHERE extname='mchar'")
        types = query('type_metadata', "SELECT oid,typname,typlen,typinput::regproc::text,"
                      "typoutput::regproc::text,typsend::regproc::text,typreceive::regproc::text "
                      "FROM pg_type WHERE typname IN ('bytea','mchar','mvarchar','regnamespace') ORDER BY typname")
        oids = {r[1]: int(r[0]) for r in types[0]['rows']}
        report['oids'] = oids
        backend=int(query('oracle_backend_pid','SELECT pg_backend_pid()')[0]['rows'][0][0])
        report['loaded_libraries']={}
        for owner,maps in [('client',Path('/proc/self/maps')),('oracle_backend',Path(f'/proc/{backend}/maps'))]:
            libraries={}
            for line in maps.read_text().splitlines():
                name=line.split()[-1]
                if '/libpq.so' in name or '/libicu' in name or '/mchar.so' in name:
                    path=Path(name)
                    if path.is_file(): libraries[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
            report['loaded_libraries'][owner]=libraries
            report['sources'].update(libraries)
        if profile_families_only:
            from pgwire_profile_type_checks import profile_type_families
            query('family_fixture','CREATE TABLE _Reference19(_IDRRef bytea,_Version integer)')
            report['families']=profile_type_families(client,capture)
            report['completed']=True
            return
        if expression_details:
            query('bounded_fixture', '''CREATE TABLE bounded(c mchar(3),v mvarchar(3));
                INSERT INTO bounded VALUES('a','a ')''')
            for label, sql in {
                'bounded_cast': 'SELECT c,c::mchar,c::mvarchar,c::text,v,v::mchar,v::mvarchar FROM bounded',
                'bounded_concat': 'SELECT c||c,c||v,v||c,v||v FROM bounded',
                'bounded_substr': 'SELECT substr(c,1,2),substr(v,1,2) FROM bounded',
                'bounded_case': 'SELECT CASE WHEN true THEN c ELSE c END,COALESCE(c,c) FROM bounded',
                'bounded_union': 'SELECT c FROM bounded UNION ALL SELECT c FROM bounded',
                'bounded_minmax': 'SELECT min(c),max(c),min(v),max(v) FROM bounded',
                'namespace_keywords': '''CREATE SCHEMA "select"; CREATE SCHEMA "integer";
                    CREATE SCHEMA "a$b"; SELECT '"select"'::regnamespace,'"integer"'::regnamespace,'"a$b"'::regnamespace''',
                'namespace_zero': "SELECT ''::regnamespace",
                'namespace_negative_short': 'SELECT (-1)::smallint::regnamespace',
                'namespace_integer_text': "SELECT '1a'::regnamespace",
                'numeric_alias': 'SELECT CAST(1 AS regnamespace),COALESCE(NULL::regnamespace,0::regnamespace)',
            }.items():
                query(label, sql)
            query('quoted_keywords',"SELECT word FROM pg_get_keywords() WHERE catcode <> 'U' ORDER BY word")
            query('bare_value_fixture',"CREATE TABLE bare_mchar(c mchar); INSERT INTO bare_mchar VALUES('abc'::mchar(3))")
            for sql in ["SELECT substr(c,1,1) FROM bare_mchar",
                        "SELECT substr('abc'::mchar(3)::mchar,1,1)",
                        "SELECT substr(('abc'::mchar(3)||'def'::mchar(3)),1,1)",
                        "SELECT substr(('a'::mchar(3))::mvarchar::mchar,1,1)",
                        "SELECT 'a'::mchar='A','A'='a'::mchar,'a'::mvarchar='A','A'='a'::mvarchar",
                        "SELECT 'a'::mchar='A'::mvarchar,'A'::mvarchar='a'::mchar"]:
                query(sql,sql)
            for typ in ['mchar','mchar(3)','mvarchar','mvarchar(3)']:
                for value in ['a','a ','a  ','']:
                    for pattern in ['a','a ','a  ','a   ','a%','a_','a__','']:
                        sql=f"SELECT '{value}'::{typ} LIKE '{pattern}'"
                        query(sql,sql)
            for typ in ['mchar(3)','mvarchar(3)']:
                for value in ['a','ab','a b','   ','a😀']:
                    for start,length in [(1,1),(1,2),(2,1),(2,2),(3,1),(0,2),(-1,3),(1,0)]:
                        sql=f"SELECT substr('{value}'::{typ},{start},{length})"
                        query(sql,sql)
            report['completed'] = True
            return
        for mode in ('on', 'off'):
            query('strings_'+mode, f'SET standard_conforming_strings={mode}')
            for text in (r'\x0100', r'\\x0100', r'\001\000', r'\\001\\000'):
                query(f'bytea_literal_{mode}_{text}', "SELECT '"+text+"'::bytea")
            # Exact external function definition; its SQL string layers are part of the oracle.
            schema = (capture/'schema.sql').read_text()
            function = re.search(r'CREATE FUNCTION public\.binrowver\(.*?\$\$;', schema, re.S | re.I)
            if not function:
                raise RuntimeError('external BINROWVER definition was not found')
            query('drop_binrowver_'+mode, 'DROP FUNCTION IF EXISTS public.binrowver(integer)')
            query('install_binrowver_'+mode, function.group(0))
            for value in (None, 0, 1, 255, 256, 65535, 65536, 16777215, 16777216, 2147483647,
                          -1, -256, -65536, -16777216, -2147483648):
                query(f'binrowver_{mode}_{value}', 'SELECT encode(binrowver('+str(value if value is not None else 'NULL')+"),'hex')")
        query('strings_default', 'SET standard_conforming_strings=on')
        query('binary_table', 'CREATE TABLE binary_values(id int, b bytea)')
        values = [None, b'', b'\0', b'\1', b'\1\0', b'\1\0\0', b'\xff', b'\0\1', b'\0\1\0']
        for i, value in enumerate(values):
            params(f'binary_insert_{i}', 'INSERT INTO binary_values VALUES($1,$2)',
                   [str(i), value], [23, 17], [0, 1])
        query('binary_pairs', 'SELECT a.id,b.id,a.b=b.b,a.b<b.b,a.b>b.b FROM binary_values a CROSS JOIN binary_values b ORDER BY a.id,b.id')
        query('binary_order', "SELECT id,octet_length(b),encode(b,'hex') FROM binary_values ORDER BY b NULLS FIRST")
        query('binary_unique', 'CREATE UNIQUE INDEX binary_idx ON binary_values(b)')
        query('binary_groups', 'SELECT min(b),max(b),count(distinct b) FROM binary_values')
        for name, typ in oids.items():
            if name not in ('mchar', 'mvarchar'):
                continue
            for i, value in enumerate(('', 'a', 'a ', 'a  ', 'A', 'Ёж', 'ёж', 'é', 'e', '😀', 'a😀b')):
                for fmt in (0, 1):
                    params(f'{name}_text_{i}_{fmt}', f'SELECT $1::{name}', [value], [typ], [0], fmt)
            for size in ('', '(1)', '(2)', '(3)'):
                for value in ('ab', 'a😀b', 'ab ', ''):
                    params(f'{name}_cast_{size}_{value}', f'SELECT $1::{name}{size}', [value], [25])
            query(name+'_create', f'CREATE TABLE t_{name}(v {name}(2))')
            for value in ('ab', 'abc', 'ab ', 'a😀', '😀😀', 'a', ''):
                params(f'{name}_assignment_{value}', f'INSERT INTO t_{name} VALUES($1)', [value])
            query(name+'_stored', f'SELECT v,length(v),octet_length(v::text) FROM t_{name}')
            query(name+'_ops', f"SELECT 'a'::{name}='A'::{name},'a'::{name}='a '::{name},"
                  f"'é'::{name}='e'::{name},'Ё'::{name}='ё'::{name},"
                  f"'a'::{name} LIKE 'A'::{name},'a '::{name} LIKE 'a'::{name},"
                  f"substr('a😀b'::{name},2,1),length('a😀b'::{name}),''::{name} IS NULL")
            for payload in (b'A', b'\0A', b'A\0', 'Ё😀'.encode('utf8'), 'Ё😀'.encode('utf-16le'), 'Ё😀'.encode('utf-16be')):
                params(f'{name}_binary_{payload.hex()}', f'SELECT $1::{name}', [payload], [typ], [1], 1)
        query('reg_namespace', 'CREATE SCHEMA "Mixed Name"')
        query('reg_temp', 'CREATE TEMP TABLE temp_probe(i integer)')
        for text in ('public', '"Mixed Name"', 'Mixed Name', 'missing', '0', '4294967295',
                     '4294967296', '-1', 'pg_temp', '  public  '):
            for fmt in (0, 1):
                params(f'reg_{text}_{fmt}', 'SELECT $1::regnamespace', [text], [4089], [0], fmt)
        query('reg_prepared', "PREPARE ns AS SELECT '\"Mixed Name\"'::regnamespace")
        query('reg_execute_before_drop', 'EXECUTE ns')
        query('reg_drop', 'DROP SCHEMA "Mixed Name"')
        query('reg_execute_dropped', 'EXECUTE ns')
        query('reg_recreate', 'CREATE SCHEMA "Mixed Name"')
        query('reg_execute_recreated', 'EXECUTE ns')
        # Use the original application statements with deliberately synthetic rows.
        # Literal bytes are obtained from the external capture only at run time.
        selected = {}
        for number in (4, 6, 7, 8, 12, 17, 18, 23, 24):
            path = next((capture/'profile/selected').glob(f'{number:02}-*.sql'))
            selected[number] = path.read_text()
            report['sources'][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        query('app_session_strings', 'SET standard_conforming_strings=off')
        query('app_fixture', '''CREATE TABLE ibversion(v integer);
            CREATE TABLE _ExtensionsRestruct(_ExtDataID bytea, _RestructDataType numeric, _RestructDataInt integer);
            CREATE TABLE _InfoRg168(_Period timestamp, _RecorderRRef bytea, _Active boolean,
                _Fld169RRef bytea, _Fld170RRef bytea);
            CREATE TABLE _Reference19(_IDRRef bytea, _Version integer);
            CREATE TABLE _ExtensionsInfo(_IDRRef bytea, _ExtensionOrder integer, _ExtName mvarchar(150),
                _UpdateTime timestamp, _ExtensionUsePurpose integer, _ExtensionScope integer,
                _ExtensionZippedInfo bytea, _MasterNode mchar(40), _UsedInDistributedInfoBase boolean, _Version integer);
            CREATE TEMP TABLE tt9(_IDRRef bytea)''')
        def literal(number):
            return re.search(r"'[^']*'::bytea", selected[number]).group(0)
        query('app_fixture_values',
              'INSERT INTO _ExtensionsRestruct VALUES('+literal(7)+',0,19);'
              "INSERT INTO _InfoRg168 VALUES(timestamp '2026-01-02',"+literal(17)+",true,NULL,NULL);"
              'INSERT INTO _Reference19 VALUES('+literal(18)+',0);'
              "INSERT INTO _ExtensionsInfo VALUES('\\\\001'::bytea,1,'Synthetic',timestamp '2026-01-02',"
              "0,0,'\\\\000'::bytea,'0:00000000000000000000000000000000',true,0)")
        for number in (4, 7, 8, 12, 17, 24):
            query(f'selected{number:02}_original_synthetic', selected[number])
        query('selected18_begin', 'BEGIN')
        query('selected18_matching', selected[18])
        query('selected18_rollback', 'ROLLBACK')
        query('selected18_restored', 'SELECT count(*) FROM _Reference19')
        query('selected18_change_version', 'UPDATE _Reference19 SET _Version=1')
        query('selected18_nonmatching', selected[18])
        query('profile_aggregates', '''SELECT min(_Period),bool_or(_Active) FROM _InfoRg168;
            SELECT max(_ExtName),min(_MasterNode) FROM _ExtensionsInfo;
            SELECT bool_or(v) FROM (VALUES (NULL::boolean),(false),(true)) AS q(v);
            SELECT bool_or(v) FROM (VALUES (NULL::boolean)) AS q(v)''')
        for source in ('(-1)::integer', '(-1)::bigint', '4294967295::bigint', '4294967296::bigint'):
            query('reg_numeric_'+source, f'SELECT ({source})::regnamespace')
        for left, right in (('mchar','mchar'),('mchar','mvarchar'),('mvarchar','mchar'),('mvarchar','mvarchar')):
            query('concat_'+left+'_'+right, f"SELECT 'a '::{left} || 'b '::{right}")
        for label in ('selected04_original_synthetic','selected07_original_synthetic',
                      'selected08_original_synthetic','selected12_original_synthetic',
                      'selected17_original_synthetic','selected24_original_synthetic',
                      'selected18_matching','selected18_nonmatching','profile_aggregates'):
            if any(result['sqlstate'] for result in report['cases'][label]):
                raise AssertionError(label+' failed in the oracle')
        report['completed'] = True
    finally:
        if client:
            client.close()
        if started:
            report['stop_exit'] = command('pg_ctl', '-D', work/'data', '-m', 'fast', '-w', 'stop').returncode
        (work/'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'work': str(work), 'completed': report['completed'], 'cases': len(report['cases']),
                      'stop_exit': report.get('stop_exit')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--capture-dir', type=Path, required=True)
    parser.add_argument('--pg-root', type=Path, default=Path('/opt/pgpro/1c-18'))
    parser.add_argument('--expression-details', action='store_true', help='only additional typmod/identity observations')
    parser.add_argument('--profile-families-only', action='store_true', help='original aggregate families on synthetic typed fixtures')
    args = parser.parse_args()
    run(args.work, args.capture_dir, args.pg_root, args.expression_details,args.profile_families_only)
