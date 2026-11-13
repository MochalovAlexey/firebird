"""Run recorded type-operation families on synthetic, genuinely typed fixtures.

The original SQL is read at run time; no captured literal or payload is stored here.
This module is shared by the private PostgreSQL oracle and Firebird acceptance.
"""
import hashlib
import json
import re
from pathlib import Path
from wire_host_smoke import require


def profile_type_families(client, capture, *, firebird=False):
    families=[json.loads(line) for line in (Path(capture)/'profile/families.jsonl').read_text().splitlines()]
    report=[]
    def query(sql, rows=None):
        result=client.query(sql,True)[-1]
        operation=re.match(r'(?:CREATE|ALTER|INSERT INTO|DELETE FROM)\s+(?:TEMP\s+)?(?:TABLE\s+)?(\w+)',sql,re.I)
        label=(operation.group(0) if operation else sql.split()[0])+' '+hashlib.sha256(sql.encode()).hexdigest()[:12]
        require(result['status'] in (1,2) and not result['sqlstate'] and (rows is None or result['rows']==rows),
                'Type operation family '+label+': '+str(result))
        return result
    query('SET standard_conforming_strings=off')
    # Existing selected18 owns _Reference19; add its real code column.
    query('ALTER TABLE _Reference19 ADD _Code mchar(9)')
    for table,column in [('_Reference22','_Code'),('_Reference23','_Code'),
                          ('_Document30','_Number'),('_Task12','_Number')]:
        query(f'CREATE TABLE {table}({column} mchar(9))')
    for table,column in [('_Reference19','_Code'),('_Reference22','_Code'),('_Reference23','_Code'),
                          ('_Document30','_Number'),('_Task12','_Number')]:
        for value in ("'000000009'","'000000010'",'NULL'):
            query(f'INSERT INTO {table}({column}) VALUES({value})')
    query('CREATE TABLE _AccRg45(_Period timestamp,_RecorderRRef bytea,_Active boolean)')
    query('CREATE TABLE _AccumRg183(_Period timestamp,_RecorderRRef bytea,_Active boolean)')
    query('CREATE TABLE _InfoRg165(_Period timestamp,_Fld166RRef bytea,_Fld167 numeric(10,3))')
    query('CREATE TABLE _SeqB196(_Period timestamp,_Fld195RRef bytea,_Recorder_TYPE bytea,'
          '_Recorder_RTRef bytea,_Recorder_RRRef bytea)')
    for n in (3,4,5,6,11,12):
        query(f'DROP TABLE IF EXISTS pg_temp.tt{n}')
        query(f'CREATE TEMP TABLE tt{n}(_Period timestamp,_RecorderRRef bytea,_Fld184RRef bytea,'
              '_Dim0RRef bytea,_Fld195RRef bytea)')
    query("INSERT INTO _SeqB196 VALUES(timestamp '2026-01-03','\\\\001'::bytea,'\\\\011'::bytea,"
          "'\\\\000'::bytea,'\\\\001'::bytea)")
    for family in families:
        normalized=family.get('normalized_for_analysis','')
        if not re.search(r'\b(min|max|bool_or)\s*\(',normalized):
            continue
        sql=family['example']['sql']
        if re.search(r'\bMAX\(T1\._(?:Code|Number)\)',sql,re.I):
            rows=[['000000010']]
        elif re.search(r'\bFROM\s+_(?:AccRg45|AccumRg183)\s',sql,re.I):
            table=re.search(r'\bFROM\s+(\w+)',sql,re.I)[1]
            literal=re.search(r"'[^']*'::bytea",sql)[0]
            for period,active in [("timestamp '2026-01-03'",'false'),("timestamp '2026-01-02'",'true'),('NULL','NULL')]:
                query(f"INSERT INTO {table} VALUES({period},{literal},{active})")
            rows=[['2026-01-02 00:00:00']+(['t'] if 'bool_or' in normalized else [])]
        elif '_inforg165' in normalized:
            literal=re.search(r"'[^']*'::bytea",sql)[0]
            for period,value in [('2026-01-02','1.234'),('2026-01-03','2.345')]:
                query(f"INSERT INTO _InfoRg165 VALUES(timestamp '{period}',{literal},{value})")
            rows=[['2.345']]
        else:
            source=re.search(r'FROM\s+pg_temp\.(tt\d+)',sql,re.I)[1]
            target=re.search(r'INSERT INTO\s+pg_temp\.(tt\d+)',sql,re.I)[1]
            query('DELETE FROM pg_temp.'+source)
            query('DELETE FROM pg_temp.'+target)
            for date in ('2026-01-03','2026-01-02','2026-01-02'):
                query(f"INSERT INTO pg_temp.{source} VALUES(timestamp '{date}','\\\\001'::bytea,"
                      "'\\\\001'::bytea,'\\\\001'::bytea,'\\\\001'::bytea)")
            result=query(sql)
            require(result['tag']=='INSERT 0 1','Recorded aggregate INSERT affected row count')
            query(f'SELECT _Period,_RecorderRRef FROM pg_temp.{target}',[['2026-01-02 00:00:00',r'\x01']])
            report.append({'family_id':family['family_id'],'sha256':hashlib.sha256(sql.encode()).hexdigest(),
                           'tag':result['tag'],'checked_rows':1})
            continue
        result=query(sql,rows)
        report.append({'family_id':family['family_id'],'sha256':hashlib.sha256(sql.encode()).hexdigest(),
                       'result':result})
    require(len(report)==13,'The recorded type aggregate inventory changed')
    query('CREATE TABLE _Seq194(_Fld195RRef bytea)')
    query("INSERT INTO _Seq194 VALUES('\\\\001'::bytea)")
    for n in (7,8):
        query(f'CREATE TEMP TABLE tt{n}(_Fld195RRef bytea)')
    for family in families:
        sql=family['example']['sql']
        normalized=family.get('normalized_for_analysis','')
        if '_seq194' in normalized and 'exists' in normalized:
            source=re.search(r'FROM\s+pg_temp\.(tt\d+)',sql,re.I)[1]
            target=re.search(r'INSERT INTO\s+pg_temp\.(tt\d+)',sql,re.I)[1]
            query('DELETE FROM pg_temp.'+source)
            query('DELETE FROM pg_temp.'+target)
            for value in ('001','001\\\\000','001\\\\000\\\\000'):
                query(f"INSERT INTO pg_temp.{source} VALUES('\\\\{value}'::bytea)")
            result=query(sql)
            require(result['tag']=='INSERT 0 2','Recorded NOT EXISTS binary equality')
            query(f'SELECT _Fld195RRef FROM pg_temp.{target} ORDER BY _Fld195RRef',
                  [[r'\x0100'],[r'\x010000']])
            report.append({'family_id':family['family_id'],'sha256':hashlib.sha256(sql.encode()).hexdigest(),
                           'tag':result['tag'],'checked_rows':2})
        elif re.fullmatch(r'select count \( \* \) from (dbschema|_accumrg290 t1)',normalized):
            table=re.search(r'\bFROM\s+(\w+)',sql,re.I)[1]
            query(f'CREATE TABLE {table}(b bytea)')
            for value in ("NULL","''::bytea","'\\\\001'::bytea"):
                query(f'INSERT INTO {table} VALUES({value})')
            result=query(sql,[['3']])
            report.append({'family_id':family['family_id'],'sha256':hashlib.sha256(sql.encode()).hexdigest(),
                           'result':result})
    require(len(report)==17,'The recorded type-operation inventory changed')
    # The full pg_tables catalog remains a separate stage. Preserve COUNT/IN
    # expression text on an explicitly synthetic relation, and report that scope.
    family=next(f for f in families if 'count' in f['normalized_for_analysis'] and
                'pg_catalog . pg_tables' in f['normalized_for_analysis'])
    sql=family['example']['sql']
    literals=re.findall(r"'(?:[^']|'')*'",sql)
    query('CREATE TEMP TABLE type_catalog_rows(tablename mvarchar(63),schemaname mvarchar(63))')
    for name in literals[:2]:
        query(f'INSERT INTO type_catalog_rows VALUES({name},{literals[-1]})')
    query("INSERT INTO type_catalog_rows VALUES('synthetic_other','synthetic_other')")
    expression='SELECT COUNT(*) FROM type_catalog_rows '+sql[sql.upper().index('WHERE'):]
    result=query(expression,[['2']])
    report.append({'family_id':family['family_id'],'sha256':hashlib.sha256(sql.encode()).hexdigest(),
                   'scope':'COUNT/IN expression on synthetic typed fixture; full pg_tables query not accepted',
                   'result':result})
    query('SET standard_conforming_strings=on')
    return report
