#!/usr/bin/env python3
"""Number formatting against a saved read-only oracle, using a private runtime."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from pgwire_1c_smoke import Libpq, OneCHarness
from pgwire_namespace_checks import config_for, native, numbers
from pgwire_startup_smoke import Client
from wire_host_smoke import require, reserve_ports

PROFILE = Path(__file__).resolve().parents[1] / '1c'
sys.path.insert(0, str(PROFILE))
from install_compat import SCRIPTS


def literal(value):
    return 'NULL' if value is None else "'"+value.replace("'", "''")+"'"


def cases():
    values = ['NULL','0','0.00','-0.00','100','100.00','100.10','1234.567800',
              '-1234.567800','-1','-123','-1234','0.0000100','1000000.000',
              'CAST(12345678901234567890123456789.12 AS NUMERIC(31,2))',
              'CAST(1234567890123456789012345.123456 AS NUMERIC(31,6))',
              'CAST(99999999999999999999999999999999999999 AS NUMERIC(38,0))',
              'CAST(-99999999999999999999999999999999999999 AS NUMERIC(38,0))',
              'CAST(12345678901234567890.123456789012345678 AS NUMERIC(38,18))',
              'CAST(0 AS NUMERIC(31,2))',"'  +00100.00  '","'.5000'","'1.'","'-000.000'","'000'"]
    result=[]
    for value in values:
        for group, secondary in ((0,0),(1,0),(2,0),(3,0),(3,2),(2,3),(8,2),(-1,0),
                                 (None,2),(3,None),(3,-1),(2147483647,0),(3,2147483647)):
            for group_sep, decimal_sep in ((',', '.'), ('_', ','), ('\u202f', ','),
                                           ('abc','::'), ('',''), (None,'.'), (',',None),
                                           (' ','. '), ('x ',' .')):
                g='NULL' if group is None else str(group)
                s='NULL' if secondary is None else str(secondary)
                result.append('SELECT public.format_number('+','.join((value,g,s,literal(group_sep),literal(decimal_sep)))+')')
    return result


def numeric_binary(digits, scale):
    # Exact PostgreSQL numeric binary representation, used only by fixed-type Bind.
    negative=digits.startswith('-');digits=digits.lstrip('+-')
    integer,_,fraction=digits.partition('.')
    integer=integer or '0';fraction=fraction.ljust(scale,'0')
    integer=integer.rjust((len(integer)+3)//4*4,'0')
    fraction=fraction.ljust((len(fraction)+3)//4*4,'0')
    groups=[int((integer+fraction)[i:i+4]) for i in range(0,len(integer+fraction),4)]
    weight=len(integer)//4-1
    while groups and not groups[0]:groups.pop(0);weight-=1
    while groups and not groups[-1]:groups.pop()
    import struct
    return struct.pack('!HhHH',len(groups),weight,0x4000 if negative else 0,scale)+b''.join(struct.pack('!H',x) for x in groups)


def run(h, oracle):
    h.prepare()
    h.isql('admin',f"CONNECT '{h.database}';\nCREATE OR ALTER USER SYSDBA PASSWORD '{h.password}';\nCOMMIT;\nQUIT;",embedded=True)
    cfg=config_for(h);filename='sql/install_number_format.sql'
    native(cfg,'prerequisites','\n'.join((PROFILE/s).read_text() for s in SCRIPTS[:SCRIPTS.index(filename)]),admin=True)
    script=(PROFILE/filename).read_text()
    native(cfg,'failed_install',script.replace('\nCOMMIT;','\nSELECT 1/0 FROM SYSTEM.RDB$DATABASE;\nCOMMIT;'),admin=True,error=True)
    count=native(cfg,'failed_install_empty',"SELECT COUNT(*) AS N FROM SYSTEM.RDB$FUNCTIONS WHERE RDB$SCHEMA_NAME='public' AND RDB$FUNCTION_NAME='format_number';",admin=True)
    require(numbers(count['output'])==[0],'Failed install retained function')
    native(cfg,'number_install',script,admin=True)
    native(cfg,'number_repeat',script,admin=True,error=True)
    native_port,pg=reserve_ports(2);h.start('number_format',native_port,native_port,[{'alias':'PgWire','port':pg}])
    ready=Client(h).login();ready.close()
    c=Libpq({'port':pg,'password':h.password});results=[]
    try:
        for sql in cases():
            r=c.query(sql,True)[-1]
            require(not r['sqlstate'] and r['rows']==[[oracle[sql]]],sql+': '+repr(r))
            require(r['columns'][0]['oid']==1043,'Unexpected return OID')
            results.append({'sql':sql,'value':r['rows'][0][0]})
        def q(sql,rows=None,error=None):
            r=c.query(sql,True)[-1]
            require(r['sqlstate']==(error or '') and (rows is None or r['rows']==rows),sql+': '+repr(r))
            return r
        q('CREATE TEMP TABLE format_values(a NUMERIC(31,2),b NUMERIC(31,6),c NUMERIC(38,0),d NUMERIC(38,18))')
        q('INSERT INTO format_values VALUES(12345678901234567890123456789.12,1234567890123456789012345.123456,99999999999999999999999999999999999999,12345678901234567890.123456789012345678)')
        for column,expression in [('a','CAST(12345678901234567890123456789.12 AS NUMERIC(31,2))'),
                                  ('b','CAST(1234567890123456789012345.123456 AS NUMERIC(31,6))'),
                                  ('c','CAST(99999999999999999999999999999999999999 AS NUMERIC(38,0))'),
                                  ('d','CAST(12345678901234567890.123456789012345678 AS NUMERIC(38,18))')]:
            reference="SELECT public.format_number("+expression+",3,2,'_',',')"
            # This exact grouping is already included with the same value.
            q(f"SELECT public.format_number({column},3,2,'_',',') FROM format_values",[[oracle[reference]]])
        for value,typ,scale in [('123.4500','NUMERIC(31,4)',4),
                               ('12345678901234567890123456789.12','NUMERIC(31,2)',2),
                               ('99999999999999999999999999999999999999','NUMERIC(38,0)',0)]:
            sql=f"SELECT public.format_number(CAST($1 AS {typ}),3,0,',','.')"
            baseline=q(f"SELECT public.format_number(CAST({value} AS {typ}),3,0,',','.')")['rows']
            for binary in (0,1):
                payload=numeric_binary(value,scale) if binary else value.encode()
                r=c.params(sql,[payload],[1700],[binary])
                require(not r['sqlstate'] and r['rows']==baseline,'Typed numeric Bind: '+repr(r))
        for oid,form,payload in [(0,0,b'+00123.4500'),(1043,0,b'123.4500'),(1043,1,b'123.4500')]:
            r=c.params("SELECT public.format_number($1,3,0,',','.')",[payload],[oid],[form])
            require(not r['sqlstate'] and r['rows']==[['123.45']],'Text decimal Bind: '+repr(r))
        for form in (0,1):
            payload=numeric_binary('123.4500',4) if form else b'123.4500'
            r=c.params("SELECT public.format_number($1,3,0,',','.')",[payload],[1700],[form])
            require(r['sqlstate']=='0A000','Uncast numeric Bind must reject: '+repr(r))
        for value in ('1e3','NaN','Infinity','12x','--1',''):
            r=q('SELECT public.format_number('+literal(value)+",3,0,',','.')",error='XX000')
        q("SELECT COUNT(*) FROM pg_catalog.pg_proc WHERE proname='format_number' AND pronamespace='public'::regnamespace",[['1']])
    finally:c.close()
    native(dict(config_for(h)),'native_after',"SELECT \"public\".\"format_number\"(CAST(100.00 AS NUMERIC(31,2)),3,0,',','.') AS N FROM SYSTEM.RDB$DATABASE;",admin=True)
    h.no_attachments();h.summary.update(number_cases=results,oracle_count=len(results),columns=True,numeric_bind_cast=True,uncast_numeric_rejected=True,atomic_install=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firebird-dir',type=Path,required=True)
    parser.add_argument('--work-dir',type=Path,required=True)
    parser.add_argument('--oracle',type=Path,required=True)
    args=parser.parse_args();work=args.work_dir.resolve();work.mkdir(parents=True,exist_ok=False)
    h=OneCHarness(args.firebird_dir.resolve(),work,120)
    try:run(h,json.loads(args.oracle.read_text()));h.summary['passed']=True
    except Exception as error:h.summary['error']=h.redact(str(error))
    finally:
        try:h.close()
        except Exception as error:h.summary.update(passed=False,shutdown_error=str(error))
        (work/'summary.json').write_text(json.dumps(h.summary,indent=2)+'\n')
        print(json.dumps({'passed':h.summary['passed'],'summary':str(work/'summary.json')}))
    return 0 if h.summary['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
