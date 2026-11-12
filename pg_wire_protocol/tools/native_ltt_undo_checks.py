#!/usr/bin/env python3
"""NAT-06 native SQL regression in new private UTF8/dialect3 databases.

No server or compatibility installer. Retains SQL and actual results for baseline
comparison; every case checks values and expected SQLSTATEs, then drops its DB.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def cases():
    result = []
    def case(name, body, expected, errors=()):
        result.append((name, body, expected, list(errors)))
    def fill(n):
        return ('SET TERM ^;\nEXECUTE BLOCK AS DECLARE I INTEGER = 0; BEGIN '
                f'WHILE (I < {n}) DO BEGIN INSERT INTO FILLER VALUES (:I); I=I+1; END END^\n'
                'SET TERM ;^\n')
    def mon(label='N'):
        return f'SELECT COUNT(*) AS {label} FROM SYSTEM.MON$LOCAL_TEMPORARY_TABLES;\n'
    setup='CREATE TABLE FILLER(V INTEGER);\nINSERT INTO FILLER VALUES(-1);\nCOMMIT;\n'
    for mode in ('PRESERVE', 'DELETE'):
        create=f'CREATE LOCAL TEMPORARY TABLE T(V INTEGER) ON COMMIT {mode} ROWS;\n'
        for size in (0,300000):
            case(f'create_{mode.lower()}_{size}',setup+create+fill(size)+
                'ROLLBACK;\n'+mon()+'SELECT COUNT(*) AS P FROM FILLER;\n'
                'SELECT * FROM T;\n'+create+'SELECT COUNT(*) AS C FROM T;\nROLLBACK;\n',
                {'N':['0'],'P':['1'],'C':['0']},['42S02'])
        for when in ('before','after'):
            case(f'no_auto_{mode.lower()}_{when}',setup+'SET TRANSACTION NO AUTO UNDO;\n'+
                (create+fill(300000) if when=='before' else fill(300000)+create)+
                'ROLLBACK;\n'+mon()+'SELECT COUNT(*) AS P FROM FILLER;\nSELECT * FROM T;\n',
                {'N':['0'],'P':['1']},['42S02'])
        # Committed definitions and rows must survive restoration of the old shape.
        initial=create+'CREATE UNIQUE INDEX UQ_T ON T(V);\nCOMMIT;\nINSERT INTO T VALUES(11);\n'
        if mode=='PRESERVE': initial+='COMMIT;\n'
        for name,ddl in (
            ('alter_drop','ALTER TABLE T ADD Q INTEGER;\nDROP TABLE T;\n'),
            ('repeated_alter','ALTER TABLE T ADD Q INTEGER;\nALTER TABLE T ADD R INTEGER;\n'),
            ('drop_create','DROP TABLE T;\nCREATE LOCAL TEMPORARY TABLE T(X VARCHAR(8)) ON COMMIT PRESERVE ROWS;\n'),
            ('drop_create_alter','DROP TABLE T;\nCREATE LOCAL TEMPORARY TABLE T(X VARCHAR(8));\nALTER TABLE T ADD Y INTEGER;\n'),
            ('index_alter_drop','ALTER INDEX UQ_T INACTIVE;\nDROP INDEX UQ_T;\n'),
        ):
            case(f'{name}_{mode.lower()}',initial+ddl+'ROLLBACK;\n'+
                'SELECT COUNT(*) AS F FROM SYSTEM.MON$LOCAL_TEMPORARY_TABLE_COLUMNS;\n'
                'SELECT COUNT(*) AS C FROM T;\nSELECT Q FROM T;\n'
                'INSERT INTO T VALUES(22);\nINSERT INTO T VALUES(22);\nROLLBACK;\n',
                {'F':['1'],'C':['1' if mode=='PRESERVE' else '0']},['42S22','23000'])
    case('ddl_after_root',setup+fill(300000)+
        'CREATE LOCAL TEMPORARY TABLE T(V INTEGER);\nROLLBACK;\n'+mon()+
        'SELECT COUNT(*) AS P FROM FILLER;\nSELECT * FROM T;\n',{'N':['0'],'P':['1']},['42S02'])
    case('accumulated_root',setup+'CREATE LOCAL TEMPORARY TABLE T(V INTEGER);\n'+fill(50000)*6+
        'ROLLBACK;\n'+mon()+'SELECT COUNT(*) AS P FROM FILLER;\nSELECT * FROM T;\n',
        {'N':['0'],'P':['1']},['42S02'])
    case('create_drop_chain','CREATE LOCAL TEMPORARY TABLE T(V INTEGER);\nALTER TABLE T ADD Q INTEGER;\n'
        'DROP TABLE T;\nCREATE LOCAL TEMPORARY TABLE T(X INTEGER);\nDROP TABLE T;\nROLLBACK;\n'+mon(),{'N':['0']})
    case('index_create_rollback','CREATE LOCAL TEMPORARY TABLE T(V INTEGER) ON COMMIT PRESERVE ROWS;\nCOMMIT;\n'
        'CREATE UNIQUE INDEX UQ_T ON T(V);\nROLLBACK;\n'
        'INSERT INTO T VALUES(1);\nINSERT INTO T VALUES(1);\nSELECT COUNT(*) AS C FROM T;\nROLLBACK;\n',{'C':['2']})
    for no_auto in (False,True):
        begin='SET TRANSACTION NO AUTO UNDO;\n' if no_auto else ''
        for end in ('COMMIT RETAIN','ROLLBACK RETAIN'):
            case(f'retain_{end.split()[0].lower()}_{no_auto}',setup+begin+
                'CREATE LOCAL TEMPORARY TABLE T(V INTEGER) ON COMMIT PRESERVE ROWS;\n'+end+';\n'
                'ROLLBACK;\n'+mon(),{'N':['1' if end.startswith('COMMIT') else '0']})
    case('statement_failure','CREATE LOCAL TEMPORARY TABLE T(V INTEGER) ON COMMIT PRESERVE ROWS;\nCOMMIT;\n'
        'ALTER TABLE T ADD Q INTEGER;\nALTER TABLE T ADD R INTEGER, ADD V INTEGER;\n'
        'INSERT INTO T(V,Q) VALUES(11,12);\nSELECT Q AS QV FROM T;\nSELECT R FROM T;\n'
        'ROLLBACK;\nSELECT Q FROM T;\nSELECT COUNT(*) AS C FROM T;\n',
        {'QV':['12'],'C':['0']},['42000','42S22','42S22'])
    # PSQL exception blocks supply real nested statement savepoints without
    # relaxing the native prohibition on DDL under a user SAVEPOINT.
    case('internal_levels','CREATE LOCAL TEMPORARY TABLE T(V INTEGER) ON COMMIT PRESERVE ROWS;\n'
        'INSERT INTO T VALUES(11);\nCOMMIT;\nSET TERM ^;\n'
        "EXECUTE BLOCK AS BEGIN EXECUTE STATEMENT 'ALTER TABLE T ADD Q INTEGER'; "
        "BEGIN EXECUTE STATEMENT 'DROP TABLE T'; EXECUTE STATEMENT 'CREATE LOCAL TEMPORARY TABLE T(X INTEGER)'; "
        "EXECUTE STATEMENT 'ALTER TABLE T ADD Y INTEGER'; END END^\nSET TERM ;^\n"
        'ROLLBACK;\nSELECT V AS V0 FROM T;\nSELECT Q FROM T;\nSELECT X FROM T;\n',
        {'V0':['11']},['42S22','42S22'])
    for mode in ('PRESERVE','DELETE'):
        for no_auto in (False,True):
            create=f'CREATE LOCAL TEMPORARY TABLE T(V INTEGER) ON COMMIT {mode} ROWS;\n'
            begin='SET TRANSACTION NO AUTO UNDO;\n' if no_auto else ''
            case(f'retaining_data_{mode.lower()}_{no_auto}',setup+begin+create+
                'INSERT INTO T VALUES(1);\nCOMMIT RETAIN;\nCOMMIT RETAIN;\n'
                'INSERT INTO T VALUES(2);\nALTER TABLE T ADD Q INTEGER;\nROLLBACK RETAIN;\n'
                'SELECT SUM(V) AS S FROM T;\nSELECT Q FROM T;\n'
                'ALTER TABLE T ADD R INTEGER;\nCOMMIT RETAIN;\n'
                'ALTER TABLE T ADD Z INTEGER;\nROLLBACK;\n'
                'SELECT COUNT(*) AS C FROM T;\nSELECT Z FROM T;\nINSERT INTO T(V,R) VALUES(3,4);\n'
                'SELECT MAX(R) AS R0 FROM T;\nROLLBACK;\n',
                {'S':['1'],'C':['1' if mode=='PRESERVE' else '0'],'R0':['4']},['42S22','42S22'])
            case(f'no_auto_small_commit_{mode.lower()}_{no_auto}',setup+begin+create+'COMMIT;\n'+begin+
                'ALTER TABLE T ADD Q INTEGER;\nDROP TABLE T;\nROLLBACK;\n'
                'SELECT Q FROM T;\nSELECT COUNT(*) AS C FROM T;\n',{'C':['0']},['42S22'])
    case('commit_dfw_failure','CREATE LOCAL TEMPORARY TABLE T(V INTEGER) ON COMMIT PRESERVE ROWS;\n'
        'INSERT INTO T VALUES(1);\nINSERT INTO T VALUES(1);\nCOMMIT;\n'
        'ALTER TABLE T ADD Q INTEGER;\nCREATE UNIQUE INDEX UQ_T ON T(V);\nCOMMIT;\n'
        'ROLLBACK;\nSELECT Q FROM T;\nSELECT COUNT(*) AS C FROM T;\n'
        'INSERT INTO T VALUES(1);\nSELECT COUNT(*) AS C FROM T;\nROLLBACK;\n',
        {'C':['2','3']},['23000','42S22'])
    for no_auto in (False,True):
        begin='SET TRANSACTION NO AUTO UNDO;\n' if no_auto else ''
        for when in ('before','after'):
            create='CREATE LOCAL TEMPORARY TABLE T(V INTEGER) ON COMMIT PRESERVE ROWS;\n'
            case(f'large_commit_{no_auto}_{when}',setup+begin+
                (create+fill(300000) if when=='before' else fill(300000)+create)+
                'INSERT INTO T VALUES(7);\nCOMMIT;\n'+begin+
                'ALTER TABLE T ADD Q INTEGER;\nDROP TABLE T;\nINSERT INTO FILLER VALUES(400000);\n'
                'ROLLBACK;\nSELECT V AS V0 FROM T;\nSELECT Q FROM T;\n'
                'SELECT COUNT(*) AS P FROM FILLER;\nROLLBACK;\n',
                {'V0':['7'],'P':['300001']},['42S22'])
    case('user_savepoint_drop','CREATE LOCAL TEMPORARY TABLE T(V INTEGER);\nCOMMIT;\n'
        'SAVEPOINT S;\nDROP TABLE T;\nROLLBACK TO S;\n'
        'INSERT INTO T VALUES(1);\nSELECT COUNT(*) AS C FROM T;\nROLLBACK;\n',
        {'C':['1']},['0A000'])
    # Repeated intervals exercise ownership/cleanup without retaining old names.
    cycle='CREATE LOCAL TEMPORARY TABLE T(V INTEGER) ON COMMIT PRESERVE ROWS;\n'
    case('repeated_intervals',(cycle+'ALTER TABLE T ADD Q INTEGER;\nROLLBACK;\n'+
        cycle+'COMMIT;\nALTER TABLE T ADD Q INTEGER;\nDROP TABLE T;\nROLLBACK;\n'
        'SELECT Q FROM T;\nDROP TABLE T;\nCOMMIT;\n')*30+mon(),{'N':['0']},['42S22']*30)
    case('native_controls',setup+'CREATE GLOBAL TEMPORARY TABLE G(V INTEGER);\nCOMMIT;\n'+
        'INSERT INTO G VALUES(1);\nSAVEPOINT S;\nINSERT INTO FILLER VALUES(2);\n'
        'CREATE LOCAL TEMPORARY TABLE T(V INTEGER);\nROLLBACK TO S;\n'
        'SELECT COUNT(*) AS P FROM FILLER;\nSELECT COUNT(*) AS G FROM G;\n'
        'RELEASE SAVEPOINT S;\n'+fill(300000)+'ROLLBACK;\n'
        'SELECT COUNT(*) AS P FROM FILLER;\nSELECT COUNT(*) AS G FROM G;\n',
        {'P':['1','1'],'G':['1','0']},['0A000'])
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firebird-dir',type=Path,required=True)
    parser.add_argument('--work-dir',type=Path,required=True)
    parser.add_argument('--case',action='append')
    args=parser.parse_args()
    work=args.work_dir.resolve();work.mkdir(parents=True,exist_ok=False)
    runtime=work/'runtime'
    shutil.copytree(args.firebird_dir.resolve(),runtime,symlinks=False)
    locks=work/'locks';locks.mkdir()
    env=os.environ.copy();env.update(FIREBIRD=str(runtime),FIREBIRD_LOCK=str(locks),
        LD_LIBRARY_PATH=str(runtime/'lib'),ISC_USER='SYSDBA')
    report={'runtime':str(runtime),'cases':[],'passed':True}
    for name,body,expected,errors in cases():
        if args.case and name not in args.case: continue
        db=work/(name+'.fdb')
        sql=f"SET AUTODDL OFF;\nSET LIST ON;\nCREATE DATABASE '{db}' DEFAULT CHARACTER SET UTF8;\n"+body+'\nROLLBACK;\nDROP DATABASE;\nQUIT;\n'
        (work/(name+'.sql')).write_text(sql)
        r=subprocess.run([str(runtime/'bin/isql'),'-quiet','-charset','UTF8','-sql_dialect','3'],
            input=sql,env=env,cwd=work,capture_output=True,text=True,timeout=180)
        out=r.stdout+r.stderr;(work/(name+'.log')).write_text(out)
        actual={k:re.findall(r'^'+k+r'\s+(\S+)\s*$',r.stdout,re.M) for k in expected}
        states=re.findall(r'SQLSTATE\s*=\s*(\w+)',r.stderr)
        ok=actual==expected and states==errors and r.returncode==(1 if errors else 0) and not db.exists()
        item={'name':name,'passed':ok,'expected':expected,'actual':actual,'expected_errors':errors,'errors':states,
              'exit':r.returncode,'database_removed':not db.exists()}
        report['cases'].append(item);report['passed'] &= ok
        (work/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(item),flush=True)
    return 0 if report['passed'] else 1

if __name__=='__main__': raise SystemExit(main())
