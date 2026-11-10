"""standard_conforming_strings checks used by the existing private 1C harness."""
from pathlib import Path
import json
import re
import subprocess

from pgwire_extended_query_smoke import ExtendedClient, parse, bind, execute, describe, close, row
from pgwire_startup_smoke import packet, error_fields
from wire_host_smoke import require

SETTING = 'SET standard_conforming_strings = '
LITERAL = r"SELECT CAST('\n' AS VARCHAR(8))"
# Expected UTF8 bytes, shared with the independent PostgreSQL reference probe.
ESCAPES = [
    (r'\n', '\n'), (r'\b\f\n\r\t', '\b\f\n\r\t'),
    (r"a\';b", "a';b"), (r"a''b", "a'b"), (r'\\', '\\'),
    (r'\q\v\a\8\9\x\xz', 'q\va89xxz'),
    (r'\1\12\101\x4a\x5', '\x01\nAJ\x05'), (r'\1012\x414', 'A2A4'),
    (r'\400A', None), (r'\777', None), (r'\000', None), (r'\x00', None),
    (r'\xc3\xa9', 'é'), (r'\303\251', 'é'), (r'\x80', None),
    (r'\xc0\x80', None), (r'\xed\xa0\x80', None),
    (r'\u0041\u044F\U0001F642', 'Aя🙂'),
    (r'\uD83D\uDE42', '🙂'), (r'\U0000D83D\uDE42', '🙂'),
    (r'\U0010FFFF', '\U0010ffff'), ('\\\n', '\n'),
]
INVALID_UNICODE = [(r'\u12', '22025'), (r'\u12xz', '22025'), (r'\U1234567', '22025'),
    (r'\u0000', '42601'), (r'\U00110000', '42601'), (r'\UFFFFFFFF', '42601'),
    (r'\uD800', '42601'), (r'\uDC00', '42601'), (r'\uD800\u0041', '42601')]


def strings_client(config, client):
    checks = []
    def mode():
        return client.pq.PQparameterStatus(client.connection, b'standard_conforming_strings').decode()
    def check(sql, wanted='on', state=0, error='', rows=None):
        results = client.query(sql, True)
        actual = [r['sqlstate'] for r in results if r['sqlstate']]
        require(actual == ([error] if error else []) and mode() == wanted and
            client.pq.PQtransactionStatus(client.connection) == state,
            'string query/state differs: ' + repr(sql[:300]) + ' ' + repr((results, mode())))
        if rows is not None:
            require([r['rows'] for r in results if r.get('columns')] == rows,
                    'string values differ: ' + repr(sql[:300]) + ' ' + repr(results))
        checks.append({'sql':sql if len(sql)<350 else sql[:60] + '... (length ' + str(len(sql)) + ')',
                       'mode':wanted, 'state':state, 'error':error})
        return results
    require(mode() == 'on', 'startup ParameterStatus missing or incorrect')
    check(LITERAL, rows=[[['\\n']]])
    # One message uses its entry mode, including continuation of EXECUTE BLOCK.
    check(SETTING+'off; '+LITERAL, 'off', rows=[[['\\n']]])
    check(LITERAL, 'off', rows=[[['\n']]])
    check(r"SELECT 'a\';b'; SELECT 2", 'off', rows=[[["a';b"]], [['2']]])
    check(SETTING+"'ON'; "+LITERAL, rows=[[['\n']]])
    check(LITERAL, rows=[[['\\n']]])
    check(SETTING+'oFf', 'off')
    for source, value in ESCAPES:
        result = check("SELECT '"+source+"'", 'off', error='22021' if value is None else '',
                       rows=None if value is None else [[[value]]])
        if value is not None:
            require(result[0]['columns'][0]['oid'] == 1042, 'literal metadata type changed')
    for source, error in INVALID_UNICODE:
        check("SELECT '"+source+"'", 'off', error=error)
    check("SELECT q'{\\n;}'", 'off', rows=[[['\\n;']]])
    check("SELECT 'a'\n'b'", 'off', rows=[[['ab']]])
    check("EXECUTE BLOCK RETURNS (V VARCHAR(8)) AS BEGIN V='\\n'; SUSPEND; END; SELECT 2;",
          'off', rows=[[['\n']], [['2']]])
    check("SELECT OCTET_LENGTH('" + r'\n'*16383 + "')", 'off', rows=[[['16383']]])
    # Native limit is deliberate; its diagnostic must not become AUTOTERM continuation.
    too_long = client.query("SELECT '" + r'\n'*16384 + "'; SELECT 2", True)
    require(len(too_long)==1 and too_long[0]['sqlstate'] and mode()=='off', 'literal length bound lost')
    check(SETTING+'on')
    check(SETTING+"'yes'", error='22023')
    check(SETTING+'off; SELECT 1/0; SELECT 2', error='22012')
    check('INSERT INTO WIRE_HOST_ROWS VALUES(611); '+SETTING+'off; SELECT 1/0', error='22012')
    check('SELECT COUNT(*) FROM WIRE_HOST_ROWS WHERE ID=611', rows=[[['0']]])
    check(SETTING+'off; SELECT (', error='42601')
    check(SETTING+'off; COMMIT; '+SETTING+'on; SELECT 1/0', 'off', error='22012')
    check('ROLLBACK', 'off')
    check('ALTER SESSION RESET')
    for failure in ('SELECT 1/0', 'SELECT (', SETTING+"'bad'"):
        error = '22012' if failure=='SELECT 1/0' else '42601' if failure=='SELECT (' else '22023'
        check('SET TRANSACTION; '+SETTING+'off', 'off', 2)
        check(failure, 'on', 3, error)
        check('SELECT 42', 'on', 3, '25P02')
        check('COMMIT')
    check('SET TRANSACTION; '+SETTING+'off; SAVEPOINT a; '+SETTING+'on; SAVEPOINT b; '+SETTING+'off', 'off', 2)
    check('SELECT 1/0', 'on', 3, '22012')
    check('SELECT (', 'on', 3, '42601')
    check('ROLLBACK TO b', 'on', 2)
    check(SETTING+'off; SELECT 1/0', 'on', 3, '22012')
    check('ROLLBACK TO a', 'off', 2)
    check('SELECT 1/0', 'off', 3, '22012')
    check('ROLLBACK')
    for identifier, alias in [('low_name', 'LOW_NAME'), ('"q""uote "', '"q""uote"'), ('"я🙂 "','"я🙂"')]:
        check('SET TRANSACTION; SAVEPOINT '+identifier+'; '+SETTING+'off', 'off', 2)
        check('ROLLBACK TO '+alias, 'on', 2)
        check(SETTING+'off', 'off', 2)
        check('ROLLBACK TO '+alias, 'on', 2)
        check('ROLLBACK')
    check('SET TRANSACTION; SAVEPOINT a; '+SETTING+'off; SAVEPOINT b; '+SETTING+'on; SAVEPOINT a; '+SETTING+'off', 'off', 2)
    check('ROLLBACK TO b', 'off', 2)
    check('ROLLBACK TO a', 'off', 3, '3B000')
    check('ROLLBACK')
    check('SET TRANSACTION; '+SETTING+'off; SAVEPOINT a; '+SETTING+'on; SAVEPOINT b; '+SETTING+'off', 'off', 2)
    check('RELEASE SAVEPOINT a ONLY', 'off', 2)
    check('SELECT 1/0', 'on', 3, '22012')
    check('ROLLBACK TO b', 'on', 2)
    check('ROLLBACK')
    check('SET TRANSACTION; SAVEPOINT a; '+SETTING+'off; SAVEPOINT b; RELEASE SAVEPOINT a', 'off', 2)
    check('ROLLBACK TO b', 'on', 3, '3B000')
    check('ROLLBACK')
    for tpb in ('', ' NO AUTO UNDO'):
        check('SET TRANSACTION'+tpb+'; '+SETTING+'off', 'off', 2)
        check('COMMIT RETAIN', 'off', 2)
        check(SETTING+'on', 'on', 2)
        check('ROLLBACK RETAIN', 'off', 2)
        check('ROLLBACK', 'off')
        check('ALTER SESSION RESET')
    check('SET TRANSACTION; '+SETTING+'off; ALTER SESSION RESET', 'on', 2)
    check(SETTING+'off', 'off', 2)
    check('ROLLBACK')
    check('SET TRANSACTION; SAVEPOINT outer_point; '+SETTING+'off', 'off', 2)
    check("EXECUTE BLOCK AS DECLARE V INTEGER; BEGIN EXECUTE STATEMENT 'SELECT 42 FROM RDB$DATABASE' INTO :V; END",
          'off', 2)
    check('ROLLBACK TO outer_point', 'on', 2)
    check('COMMIT')
    # Persisted PSQL source contains canonical native literals; inspect after reconnect below.
    check(SETTING+'off', 'off')
    check("CREATE PROCEDURE STRING_SOURCE RETURNS (V VARCHAR(8)) AS BEGIN V='\\n'; SUSPEND; END", 'off')
    check(SETTING+'on')
    check('SELECT V FROM STRING_SOURCE', rows=[[['\n']]])
    check("EXECUTE BLOCK AS DECLARE S BLOB SUB_TYPE TEXT; BEGIN "
          "SELECT RDB$PROCEDURE_SOURCE FROM RDB$PROCEDURES WHERE RDB$PROCEDURE_NAME='STRING_SOURCE' INTO :S; "
          "EXECUTE STATEMENT 'ALTER PROCEDURE STRING_SOURCE RETURNS (V VARCHAR(8)) AS ' || S; END")
    check('SELECT V FROM STRING_SOURCE', rows=[[['\n']]])
    from pgwire_1c_smoke import Libpq
    other = Libpq(config)
    try:
        require(other.query('SELECT V FROM STRING_SOURCE', True)[0]['rows']==[['\n']], 'stored SQL meaning changed')
        check(SETTING+'off', 'off')
        require(other.query(LITERAL, True)[0]['rows']==[['\\n']], 'setting leaked between attachments')
    finally:
        other.close()
    check('ALTER SESSION RESET')
    check('DROP PROCEDURE STRING_SOURCE')
    sources = strings_sources(config, check)
    return {'passed':True,'queries':checks,'stored_sources':sources,
            'excluded_native_combinations':['TPB AUTO COMMIT with setting history',
                'ALTER SESSION RESET inside PSQL with setting history'],
            'length_overflow_sqlstate':too_long[0]['sqlstate']}


def strings_sources(config, check):
    """Exercise native reprepare of both bare STRING and sql_string contexts."""
    temporal = ["2026-01-01", "12:34:56.1234", "2026-01-01 12:34:56.1234"]
    temporal_oids = [1082, 1083, 1114]
    expressions = "DATE '2026-01-01' AS D, TIME '12:34:56.1234' AS T, " \
                  "TIMESTAMP '2026-01-01 12:34:56.1234' AS TS"
    for mode in ('on', 'off'):
        check(SETTING+mode, mode)
        table = 'STRINGS_CTAS_'+mode.upper()
        check('CREATE TABLE '+table+' AS SELECT '+expressions+' FROM RDB$DATABASE WITH DATA', mode)
        result = check('SELECT * FROM '+table, mode, rows=[[temporal]])[0]
        require([c['oid'] for c in result['columns']] == temporal_oids, 'CTAS temporal types changed')
        check('DROP TABLE '+table, mode)

    # Native EDS has no PG mode: compare to already decoded typed literals.
    check("EXECUTE BLOCK RETURNS (D DATE, T TIME, TS TIMESTAMP) AS BEGIN "
          "EXECUTE STATEMENT q'{SELECT DATE '2026-01-01', TIME '12:34:56.1234', "
          "TIMESTAMP '2026-01-01 12:34:56.1234' FROM RDB$DATABASE}' INTO :D, :T, :TS; "
          "SUSPEND; END", 'off', rows=[[temporal]])

    escaped_temporal = r"DATE '2026-\x30\x31-01' AS D, TIME '\x31\x32:34:56.1234' AS T, " \
                       r"TIMESTAMP '\x32\x30\x32\x36-01-01 12:34:56.1234' AS TS"
    text_expressions = r"'\n' AS S, '\u044F\U0001F642' AS U, 'a\';\\b' AS ESC, " \
                       r"_UTF8 '\u044F' AS INTRO, q'{\n}' AS Q, " \
                       "CAST(X'41' AS VARCHAR(1) CHARACTER SET UTF8) AS H"
    mixed = temporal + ['\n', 'я🙂', "a';\\b", 'я', '\\n', 'A']
    mixed_oids = temporal_oids + [1042]*5 + [1043]
    # Parenthesized CTAS, CASE and nested SELECT exercise btyacc conflict replay.
    check('CREATE TABLE STRINGS_CTAS_MIX AS (SELECT '+escaped_temporal+', '+text_expressions+
          ", CASE WHEN 1=1 THEN (SELECT '\\u044F' FROM RDB$DATABASE) ELSE 'x' END AS C "
          'FROM RDB$DATABASE) WITH DATA', 'off')
    result = check('SELECT * FROM STRINGS_CTAS_MIX', 'off', rows=[[mixed+['я']]])[0]
    require([c['oid'] for c in result['columns']] == mixed_oids+[1042],
            'Mixed CTAS metadata changed: '+repr(result['columns']))
    # Inferred charset catches a bare X literal silently turning text into BINARY.
    charsets = check("SELECT TRIM(RF.RDB$FIELD_NAME), F.RDB$CHARACTER_SET_ID "
          "FROM RDB$RELATION_FIELDS RF JOIN RDB$FIELDS F "
          "ON F.RDB$SCHEMA_NAME=RF.RDB$FIELD_SOURCE_SCHEMA_NAME AND F.RDB$FIELD_NAME=RF.RDB$FIELD_SOURCE "
          "WHERE RF.RDB$RELATION_NAME='STRINGS_CTAS_MIX' AND F.RDB$CHARACTER_SET_ID IS NOT NULL "
          "ORDER BY RF.RDB$FIELD_POSITION", 'off')[0]['rows']
    require(charsets == [[name,'4'] for name in ('S','U','ESC','INTRO','Q','H','C')],
            'Stored expression lost UTF8 charset: '+repr(charsets))
    check('DROP TABLE STRINGS_CTAS_MIX', 'off')

    definitions = [
        ('STRINGS_TEMPORAL_SOURCE',
         'RETURNS (D DATE, T TIME, TS TIMESTAMP, S VARCHAR(8) CHARACTER SET UTF8, '
         'U VARCHAR(8) CHARACTER SET UTF8, ESC VARCHAR(8) CHARACTER SET UTF8, '
         'INTRO VARCHAR(8) CHARACTER SET UTF8, Q VARCHAR(8) CHARACTER SET UTF8, '
         'H VARCHAR(8) CHARACTER SET UTF8)',
         r"BEGIN D=DATE '2026-\x30\x31-01'; T=TIME '\x31\x32:34:56.1234'; "
         r"TS=TIMESTAMP '\x32\x30\x32\x36-01-01 12:34:56.1234'; "
         r"S='\n'; U='\u044F\U0001F642'; ESC='a\';\\b'; INTRO=_UTF8 '\u044F'; "
         r"Q=q'{\n}'; H=CAST(X'41' AS VARCHAR(8) CHARACTER SET UTF8); SUSPEND; END",
         mixed, temporal_oids+[1043]*6),
        ('STRINGS_STATE_SOURCE', 'RETURNS (V INTEGER, S VARCHAR(8) CHARACTER SET UTF8)',
         r"BEGIN BEGIN V=1/0; WHEN SQLSTATE '\x32\x32\x30\x31\x32' "
         r"DO BEGIN V=42; S='\u044F'; END END SUSPEND; END", ['42','я'], [23,1043]),
    ]
    sources = {}
    for name, declaration, body, values, oids in definitions:
        check('CREATE PROCEDURE '+name+' '+declaration+' AS '+body, 'off')
        result = check('SELECT * FROM '+name, 'off', rows=[[values]])[0]
        require([c['oid'] for c in result['columns']] == oids, 'Procedure metadata changed')
        source = check("SELECT CAST(RDB$PROCEDURE_SOURCE AS VARCHAR(8191)) "
                       "FROM RDB$PROCEDURES WHERE RDB$PROCEDURE_NAME='"+name+"'", 'off')[0]['rows'][0][0]
        sources[name] = source
        require(not re.search(r'(?:DATE|TIME|TIMESTAMP|SQLSTATE)\s+_UTF8', source),
                'Bare STRING received an introducer: '+source)
        if name == 'STRINGS_TEMPORAL_SOURCE':
            require("DATE X'323032362D30312D3031'" in source and "_UTF8 X'0A'" in source and
                    "_UTF8 X'D18FF09F9982'" in source and "Q=q'{\\n}'" in source,
                    'Mixed source did not preserve literal contexts: '+source)
        else:
            require("SQLSTATE X'3232303132'" in source and "_UTF8 X'D18F'" in source,
                    'SQLSTATE/text source did not preserve literal contexts: '+source)

        # Read the real BLOB and feed it directly to the existing native EDS prepare.
        # Repeating this also checks that native serialization remains stable.
        for _ in range(2):
            check("EXECUTE BLOCK AS DECLARE SRC BLOB SUB_TYPE TEXT; BEGIN "
                  "SELECT RDB$PROCEDURE_SOURCE FROM RDB$PROCEDURES WHERE RDB$PROCEDURE_NAME='"+name+
                  "' INTO :SRC; EXECUTE STATEMENT 'ALTER PROCEDURE "+name+' '+declaration+
                  " AS ' || SRC; END", 'off')
            check('SELECT * FROM '+name, 'off', rows=[[values]])

    from pgwire_1c_smoke import Libpq
    peer = Libpq(config)
    try:
        for name, _, _, values, oids in definitions:
            result = peer.query('SELECT * FROM '+name, True)
            require(len(result)==1 and not result[0]['sqlstate'] and result[0]['rows']==[values] and
                    [c['oid'] for c in result[0]['columns']]==oids, 'Reconnected procedure changed')
    finally:
        peer.close()
    for name, _, _, _, _ in definitions:
        check('DROP PROCEDURE '+name, 'off')
    check(SETTING+'on')
    return {'passed':True, 'ctas_modes':['on','off'], 'mixed_charsets':charsets,
            'procedure_sources':sources, 'native_alters_per_procedure':2, 'reconnect':True}



class StatusClient(ExtendedClient):
    """Keep ParameterStatus frames, which the general Extended helper discards."""
    def events(self, end=b'Z'):
        events=[]
        for _ in range(1000):
            kind, body=self.message()
            if kind==b'N': continue
            events.append((kind,body))
            if kind==end or (end!=b'Z' and kind in (b'E',b'Z')): return events
        raise RuntimeError('message bound exceeded')

    def check(self, data, kinds, state='I', statuses=(), rows=None, error=None, end=b'Z'):
        self.send(data)
        events=self.events(end)
        require(b''.join(k for k,_ in events)==kinds, 'wire order differs: '+repr(events))
        require([b for k,b in events if k==b'S']==[
            b'standard_conforming_strings\0'+v.encode()+b'\0' for v in statuses], 'ParameterStatus differs')
        if end==b'Z': require(events[-1]==(b'Z',state.encode()), 'transaction state differs')
        if rows is not None: require([row(b) for k,b in events if k==b'D']==rows, 'portal values differ')
        errors=[error_fields(b) for k,b in events if k==b'E']
        require([e['C'] for e in errors]==([error] if error else []), 'wire error differs: '+repr(errors))
        return events


def strings_protocols(harness):
    c=StatusClient(harness).login()
    sync=packet(b'S')
    q=lambda s:packet(b'Q',s.encode()+b'\0')
    try:
        for original, changed, value in [('on','off',b'\\n'),('off','on',b'\n')]:
            c.check(q(SETTING+original), b'CZ')
            c.check(parse('saved'+original,LITERAL)+sync,b'1Z')
            c.check(q(SETTING+changed), b'CSZ',statuses=[changed])
            c.check(bind('p','saved'+original)+describe(b'P','p')+execute('p')+sync,
                    b'2TDCZ',rows=[[value]])
        c.check(parse('off',SETTING+'off')+sync,b'1Z')
        # Parse/Bind alone do not apply SET; Flush retains the logical cycle.
        c.check(bind('set','off')+packet(b'H'),b'2',end=b'2')
        c.check(execute('set')+packet(b'H'),b'C',end=b'C')
        c.check(parse('bad','SELECT (')+sync,b'EZ',error='42601')
        c.check(bind('set','off')+execute('set')+packet(b'H'),b'2C',end=b'C')
        c.check(parse('off','SELECT 1')+sync,b'EZ',error='42P05')
        c.check(parse('param','SELECT CAST(? AS INTEGER)')+sync,b'1Z')
        c.check(bind('set','off')+execute('set')+packet(b'H'),b'2C',end=b'C')
        c.check(bind('bad','param',['not integer'])+sync,b'EZ',error='22P02')
        c.check(q('SET TRANSACTION; SAVEPOINT a; '+SETTING+'off'),b'CCCSZ',state='T',statuses=['off'])
        c.check(parse('off','SELECT 1')+sync,b'ESZ',state='E',statuses=['on'],error='42P05')
        c.check(parse('no','SELECT 1')+sync,b'EZ',state='E',error='25P02')
        c.check(q('ROLLBACK TO a'),b'CZ',state='T')
        c.check(q('ROLLBACK'),b'CZ')
        # Independent cursors and bound strings that must bypass SQL decoding.
        c.check(q(SETTING+'off'),b'CSZ',statuses=['off'])
        c.check(parse('multi',"SELECT CAST('\\n' AS VARCHAR(8)) FROM RDB$TYPES ROWS 2")+
                parse('text','SELECT CAST(? AS VARCHAR(8))')+sync,b'11Z')
        c.check(q('SET TRANSACTION'),b'CZ',state='T')
        c.check(bind('a','multi')+bind('b','multi')+execute('a',1)+execute('b',1)+sync,
                b'22DsDsZ',state='T',rows=[[b'\n'],[b'\n']])
        c.check(execute('a')+execute('b')+sync,b'DCDCZ',state='T',rows=[[b'\n'],[b'\n']])
        c.check(bind('t','text',[b'\\n'])+execute('t')+sync,b'2DCZ',state='T',rows=[[b'\\n']])
        c.check(q('ROLLBACK; ALTER SESSION RESET'),b'CCSZ',statuses=['on'])
    finally:
        c.close()
    env=harness.env.copy()
    env.update(ISC_USER='WIRE_HOST_TEST',ISC_PASSWORD=harness.password)
    for dialect in (1,2,3):
        result=subprocess.run([str(harness.runtime/'bin/isql'),'-quiet','-autoterm','-charset','UTF8',
            '-sql_dialect',str(dialect),f'inet://127.0.0.1:{harness.native_port}/wire_host'],
            input='SET LIST ON;\n'+SETTING+"off;\nSELECT 42 AS STANDARD_CONFORMING_STRINGS FROM RDB$DATABASE;\n"
                  +r"SELECT OCTET_LENGTH('\n') AS N FROM RDB$DATABASE;"+'\nROLLBACK;\nQUIT;\n',
            capture_output=True,text=True,env=env,cwd=harness.temp,timeout=harness.timeout)
        output=result.stdout+result.stderr
        (harness.work/f'native_strings_{dialect}.log').write_text(harness.redact(output))
        require(result.returncode!=0 and re.findall(r'SQLSTATE\s*=\s*(\w+)',output)==['42000'] and
            re.findall(r'^STANDARD_CONFORMING_STRINGS\s+(\d+)\s*$',output,re.M)==['42'] and
            re.findall(r'^N\s+(\d+)\s*$',output,re.M)==['2'], 'native isolation/AUTOTERM differs')
    return {'passed':True,'named_parse_modes':['on','off'],'independent_portals':2,
            'bind_values_unchanged':True,'native_dialects':[1,2,3]}


def strings_api(harness):
    source = Path(__file__).with_name('pgwire_strings_api.cpp')
    binary = harness.work / 'strings_api'
    command = ['g++','-std=c++17','-g','-O0',str(source),'-I'+str(harness.runtime/'include'),
               '-L'+str(harness.runtime/'lib'),'-Wl,-rpath,'+str(harness.runtime/'lib'),'-lfbclient','-o',str(binary)]
    built = subprocess.run(command,env=harness.env,capture_output=True,text=True,timeout=harness.timeout)
    (harness.work/'strings_api_compile.log').write_text(built.stdout+built.stderr)
    require(built.returncode==0,'OO probe compilation failed')
    result = subprocess.run([str(binary),str(harness.database.parent/'strings_api.fdb')],env=harness.env,
        cwd=harness.temp,capture_output=True,text=True,timeout=harness.timeout)
    (harness.work/'strings_api.log').write_text(harness.redact(result.stdout+result.stderr))
    require(result.returncode==0,'OO probe failed; see strings_api.log')
    return json.loads(result.stdout)
