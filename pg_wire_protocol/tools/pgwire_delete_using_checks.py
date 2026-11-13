#!/usr/bin/env python3
"""DELETE USING against saved private PG oracle and an isolated Firebird runtime."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

from pgwire_1c_smoke import Libpq, OneCHarness
from pgwire_namespace_checks import config_for, native, numbers
from pgwire_startup_smoke import Client
from wire_host_smoke import require, reserve_ports

COMPAT = Path(__file__).resolve().parents[1]/'1c'
sys.path.insert(0, str(COMPAT))
from install_compat import SCRIPTS


# PostgreSQL reference outcomes for the four-row fixture below. A string is an
# error SQLSTATE; a list identifies deleted target rows (including SQL NULL).
REFERENCE_CASES = (
    ('duplicate_source', 'DELETE FROM t AS a USING s AS b WHERE a.id=b.id RETURNING a.id', ['1']),
    ('no_where_nonempty', 'DELETE FROM t USING s RETURNING t.id', ['1', '2', '3', None]),
    ('no_where_empty', 'DELETE FROM t USING u RETURNING t.id', []),
    ('null_equality', 'DELETE FROM t USING s WHERE t.id=s.id RETURNING t.id', ['1']),
    ('null_safe', 'DELETE FROM t USING s WHERE t.id IS NOT DISTINCT FROM s.id RETURNING t.id', ['1', None]),
    ('target_alias_hides_name', 'DELETE FROM t AS a USING s AS b WHERE t.id=b.id', '42P01'),
    ('ambiguous_id', 'DELETE FROM t USING s WHERE id=1', '42702'),
    ('duplicate_target_source_alias', 'DELETE FROM t AS a USING s AS a WHERE a.id=1', '42712'),
    ('valid_join', 'DELETE FROM t AS a USING s AS b JOIN u AS c ON b.id=c.id WHERE a.id=b.id RETURNING a.id', ['1']),
    ('target_reference_in_join_on', 'DELETE FROM t AS a USING s AS b JOIN u AS c ON a.id=c.id WHERE a.id=b.id', '42P01'),
    ('target_reference_in_source_derived', 'DELETE FROM t AS a USING (SELECT id FROM s WHERE s.id=a.id) AS b WHERE a.id=b.id', '42P01'),
    ('target_reference_in_source_lateral', 'DELETE FROM t AS a USING LATERAL (SELECT id FROM s WHERE s.id=a.id) AS b WHERE a.id=b.id', '42P10'),
    ('correlated_where_subquery', 'DELETE FROM t AS a USING s AS b WHERE a.id=b.id AND EXISTS(SELECT 1 FROM u AS c WHERE c.id=a.id) RETURNING a.id', ['1']),
    ('self_join', 'DELETE FROM t AS a USING t AS b WHERE a.id=b.id AND b.id=2 RETURNING a.id', ['2']),
    ('returning_source', 'DELETE FROM t AS a USING s AS b WHERE a.id=b.id RETURNING a.id,b.payload', ['1']),
    ('source_alias_hides_name', 'DELETE FROM t AS a USING s AS b WHERE a.id=s.id', '42P01'),
    ('target_only_unqualified', 'DELETE FROM t USING s WHERE v=20 RETURNING t.id', ['2']),
    ('source_only_unqualified', 'DELETE FROM t USING s WHERE payload=10 RETURNING t.id', ['1', '2', '3', None]),
    ('returning_ambiguous_id', 'DELETE FROM t USING s WHERE t.id=s.id RETURNING id', '42702'),
    ('where_aggregate', 'DELETE FROM t USING s WHERE t.id=s.id AND COUNT(*)>0', '42803'),
    ('comma_sources', 'DELETE FROM t AS a USING s AS b,u AS c WHERE a.id=b.id AND b.id=c.id RETURNING a.id', ['1']),
    ('join_alias_hides_children', 'DELETE FROM t AS a USING (s AS b JOIN u AS c ON b.id=c.id) AS j WHERE a.id=b.id', '42P01'),
    ('duplicate_implicit_target', 'DELETE FROM t USING t', '42712'),
    ('source_alias_matches_implicit_target', 'DELETE FROM t USING s AS t', '42712'),
    ('target_alias_matches_implicit_source', 'DELETE FROM t AS s USING s', '42712'),
    ('duplicate_alias_nested_join', 'DELETE FROM t AS a USING s AS a JOIN u AS c ON a.id=c.id WHERE a.id=1', '42712'),
    ('duplicate_implicit_nested_join', 'DELETE FROM t USING t JOIN u ON t.id=u.id', '42712'),
    ('source_alias_matches_target_qualified', 'DELETE FROM t AS a USING s AS a WHERE a.v=10', '42712'),
    ('source_join_using_ambiguous_id', 'DELETE FROM t USING s JOIN u USING(id) WHERE id=1', '42702'),
    ('source_join_using_target_only', 'DELETE FROM t USING s JOIN u USING(id) WHERE v=20 RETURNING t.id', ['2']),
    ('separate_schemas_same_implicit_name', 'DELETE FROM pg_temp.t USING other.t', ['1', '2', '3', None]),
    ('scalar_correlated_where', 'DELETE FROM t AS a USING s AS b WHERE a.id=b.id AND a.id=(SELECT max(c.id) FROM u AS c WHERE c.id=a.id) RETURNING a.id', ['1']),
    ('nested_source_fallback', 'DELETE FROM t AS a USING s AS b WHERE a.id=b.id AND EXISTS(SELECT 1 FROM u AS c WHERE c.id=a.id AND payload=10) RETURNING a.id', ['1']),
    ('nested_target_fallback', 'DELETE FROM t AS a USING s AS b WHERE a.id=b.id AND EXISTS(SELECT 1 FROM u AS c WHERE c.id=a.id AND v=10) RETURNING a.id', ['1']),
    ('nested_local_hides_outer', 'DELETE FROM t AS a USING s AS b WHERE a.id=b.id AND EXISTS(SELECT 1 FROM u AS c WHERE id=2) RETURNING a.id', ['1']),
    ('nested_ambiguous_outer_fallback', 'DELETE FROM t AS a USING s AS b WHERE EXISTS(SELECT 1 WHERE id=1)', '42702'),
    ('aggregate_of_outer_target', 'DELETE FROM t AS a USING s AS b WHERE a.v=(SELECT min(a.v) FROM u AS c)', '42803'),
    ('persistent_source_view', 'DELETE FROM t AS a USING public.source_view AS b WHERE a.id=b.id RETURNING a.id', ['1']),
)


def reference_cases():
    cases = []
    for label, sql, outcome in REFERENCE_CASES:
        state = outcome if isinstance(outcome, str) else ""
        deleted = [] if state else outcome
        cases.append(dict(label=label, sql=sql,
            result=dict(sqlstate=state, tag="" if state else "DELETE "+str(len(deleted)),
                        rows=[[value] for value in deleted] if " RETURNING " in sql else []),
            remaining=dict(rows=[[value] for value in ("1","2","3",None) if value not in deleted])))
    return cases


def ordered(rows):
    return sorted(rows, key=lambda row: tuple((value is None, value or '') for value in row))


def run(h, oracle):
    h.prepare()
    h.isql('admin', f"CONNECT '{h.database}';\nCREATE OR ALTER USER SYSDBA PASSWORD '{h.password}';\nCOMMIT;\nQUIT;", embedded=True)
    native(config_for(h), 'compat', '\n'.join((COMPAT/script).read_text() for script in SCRIPTS), admin=True)
    h.isql('fixture', f"CONNECT '{h.database}';\n"
        'GRANT CREATE TABLE ON SCHEMA "public" TO USER WIRE_HOST_TEST;\n'
        'GRANT CREATE PROCEDURE ON SCHEMA "public" TO USER WIRE_HOST_TEST;\n'
        'CREATE SCHEMA "other";\nGRANT USAGE ON SCHEMA "other" TO PUBLIC;\n'
        'CREATE TABLE "other"."t"("id" INTEGER);\nINSERT INTO "other"."t" VALUES(1);\n'
        'GRANT SELECT ON "other"."t" TO PUBLIC;\n'
        'CREATE VIEW "public"."source_view" AS SELECT "id" FROM "other"."t";\n'
        'GRANT SELECT ON "public"."source_view" TO PUBLIC;\n'
        'CREATE TABLE PUBLIC.NATIVE_DELETE_T(ID INTEGER);\n'
        'CREATE TABLE PUBLIC.NATIVE_DELETE_S(ID INTEGER);\n'
        'INSERT INTO PUBLIC.NATIVE_DELETE_T VALUES(1);\n'
        'INSERT INTO PUBLIC.NATIVE_DELETE_T VALUES(2);\n'
        'INSERT INTO PUBLIC.NATIVE_DELETE_S VALUES(1);\n'
        'GRANT SELECT, DELETE ON PUBLIC.NATIVE_DELETE_T TO PUBLIC;\n'
        'GRANT SELECT ON PUBLIC.NATIVE_DELETE_S TO PUBLIC;\n'
        'CREATE TABLE "public"."_seqb196"("_fld195rref" BLOB SUB_TYPE 10);\n'
        'GRANT SELECT, INSERT, DELETE ON "public"."_seqb196" TO PUBLIC;\n'
        'SET TERM ^;\nCREATE PROCEDURE "public"."using_proc" RETURNS("id" INTEGER) AS '
        'BEGIN "id"=1; SUSPEND; END^\nSET TERM ;^\n'
        'GRANT EXECUTE ON PROCEDURE "public"."using_proc" TO PUBLIC;\n'
        'COMMIT;\nQUIT;', embedded=True)
    native_port, pg = reserve_ports(2)
    h.start('delete_using', native_port, native_port, [{'alias':'PgWire','port':pg}])
    ready = Client(h).login(); ready.close()
    c = Libpq({'port':pg, 'password':h.password})
    report = []
    def q(sql, rows=None, error=None, tag=None):
        result = c.query(sql, True)[-1]
        require(result['sqlstate'] == (error or '') and
                (rows is None or ordered(result['rows']) == ordered(rows)) and
                (tag is None or result['tag'] == tag), sql + ': ' + repr(result))
        return result
    try:
        for sql in ('CREATE TEMP TABLE t(id INTEGER,v INTEGER)',
                    'CREATE TEMP TABLE s(id INTEGER,payload INTEGER)',
                    'CREATE TEMP TABLE u(id INTEGER)'):
            q(sql)
        for table, values in (('t', ('(1,10)','(2,20)','(3,30)','(NULL,40)')),
                              ('s', ('(1,10)','(1,11)','(NULL,12)')), ('u', ('(1)','(2)'))):
            for value in values: q('INSERT INTO '+table+' VALUES'+value)

        for case in oracle:
            sql, label = case['sql'], case['label']
            q('BEGIN')
            if label == 'no_where_empty': q('DELETE FROM u')
            reference = case['result']
            # The bounded grammar accepts a table list, excluding joined or
            # derived sources. RETURNING accepts qualified target columns only.
            excluded_source = bool(re.search(r'\bUSING\s+(?:\(|LATERAL\b)|\bJOIN\b', sql, re.I))
            excluded_returning = label in ('returning_source','returning_ambiguous_id',
                                            'returning_target_expression','returning_star')
            native_error = {'42P01':'42703', '42702':'42702', '42712':'42000', '42803':'42000'}
            result = q(sql, error='42601' if excluded_source else
                       '0A000' if excluded_returning else
                       native_error[reference['sqlstate']] if reference['sqlstate'] else None)
            if not reference['sqlstate'] and not excluded_source and not excluded_returning:
                require(result['tag'] == reference['tag'] and ordered(result['rows']) == ordered(reference['rows']),
                        'Oracle result differs: '+label+' '+repr(result))
                q('SELECT id FROM t ORDER BY id NULLS LAST', case['remaining']['rows'])
            if result['sqlstate']:
                require(c.pq.PQtransactionStatus(c.connection) == 3, 'Error did not enter E')
                q('SELECT 1', error='25P02')
            q('ROLLBACK')
            q('SELECT id FROM t ORDER BY id NULLS LAST', [['1'],['2'],['3'],[None]])
            report.append(dict(label=label, result=result, reference_sqlstate=reference['sqlstate']))

        # Exact recorded 1C query, with native persistent target and PG LTT source.
        q('CREATE TEMP TABLE tt7(_Fld195RRef bytea)')
        for value in (r"'\x0100'::bytea", r"'\x0200'::bytea", 'NULL'):
            q('INSERT INTO _SeqB196 VALUES('+value+')')
        for value in (r"'\x0100'::bytea", r"'\x0100'::bytea", 'NULL'):
            q('INSERT INTO pg_temp.tt7 VALUES('+value+')')
        recorded = 'DELETE FROM _SeqB196 T1 USING pg_temp.tt7 T2 WHERE T1._Fld195RRef=T2._Fld195RRef'
        q('BEGIN'); q('SAVEPOINT before_delete')
        q(recorded, tag='DELETE 1')
        q('SELECT COUNT(*) FROM _SeqB196', [['2']])
        q('ROLLBACK TO SAVEPOINT before_delete')
        q('SELECT COUNT(*) FROM _SeqB196', [['3']])
        q('ROLLBACK')
        # Real libpq prepared parameters; the binary bytea value includes NUL.
        sql = recorded+' AND T1._Fld195RRef=$1 RETURNING T1._Fld195RRef'
        result = c.result(c.pq.PQprepare(c.connection,b'using_delete',sql.encode(),0,None))
        require(not result['sqlstate'], 'DELETE USING prepare failed')
        described = c.result(c.pq.PQdescribePrepared(c.connection,b'using_delete'))
        require(described['parameter_oids'] == [17] and described['columns'][0]['oid'] == 17,
                'DELETE USING bytea input/output metadata differs')
        for binary in (0, 1):
            q('BEGIN')
            result = c.params('using_delete', [b'\x01\x00'], formats=[1], prepared=True, result_format=binary)
            require(not result['sqlstate'] and result['tag'] == 'DELETE 1' and
                    result['rows'] == [['0100' if binary else r'\x0100']], 'Prepared bytea DELETE USING differs')
            q('ROLLBACK')
        q(recorded, tag='DELETE 1')
        cfg = config_for(h)
        observed = native(cfg,'native_committed_delete','SELECT COUNT(*) AS N FROM "public"."_seqb196";',admin=True)
        require(numbers(observed['output']) == [2], 'Native observer did not see committed deletion')
        q('DELETE FROM _SeqB196')
        q(recorded, tag='DELETE 0')
        # Scope restoration/cache: failed preparation must not affect later DSQL.
        for _ in range(3):
            q('DELETE FROM t USING s WHERE id=1', error='42702')
            q('DELETE FROM t a USING s b WHERE a.id=b.id AND a.id=99', tag='DELETE 0')
            q('SELECT id FROM t WHERE id=2', [['2']])
        for sql in ('EXECUTE BLOCK AS BEGIN DELETE FROM t USING s WHERE t.id=s.id; END',
                    'CREATE PROCEDURE using_psql AS BEGIN DELETE FROM t USING s WHERE t.id=s.id; END'):
            q(sql, error='0A000')
        q('DELETE FROM t USING using_proc p WHERE t.id=p.id', error='42P01')
        q('DELETE FROM t USING public.using_proc p WHERE t.id=p.id', error='42P01')
        q('SELECT COUNT(*) FROM t', [['4']])
        q('BEGIN')
        q('DELETE FROM t AS "A" USING s AS "a" WHERE "A".id="a".id RETURNING "A".id',
          rows=[['1']], tag='DELETE 1')
        q('ROLLBACK')
        for returning in ('*', 'id', 's.id', 't.id+1', 't.id AS x'):
            q('DELETE FROM t USING s WHERE t.id=s.id RETURNING '+returning, error='0A000')
            q('SELECT COUNT(*) FROM t', [['4']])
        q('DELETE FROM t USING s WHERE t.id=s.id RETURNING t.*', rows=[['1','10']], tag='DELETE 1')
        q('SELECT COUNT(*) FROM t', [['3']])
        c.recover()
    finally:
        c.close()

    env = h.env.copy(); env.update(ISC_USER='WIRE_HOST_TEST',ISC_PASSWORD=h.password)
    for dialect in (1,2,3):
        sql = ('SET LIST ON;\nDELETE FROM PUBLIC.NATIVE_DELETE_T USING PUBLIC.NATIVE_DELETE_S '
               'WHERE PUBLIC.NATIVE_DELETE_T.ID=PUBLIC.NATIVE_DELETE_S.ID;\n'
               'SELECT COUNT(*) AS BEFORE_COUNT FROM PUBLIC.NATIVE_DELETE_T;\n'
               'DELETE FROM PUBLIC.NATIVE_DELETE_T WHERE ID=1;\n'
               'SELECT COUNT(*) AS AFTER_COUNT FROM PUBLIC.NATIVE_DELETE_T;\nROLLBACK;\n'
               # Native equivalent of the target-only USING predicate: its
               # semijoin has no dependent conjunct to pass to the optimizer.
               'DELETE FROM PUBLIC.NATIVE_DELETE_T T WHERE EXISTS '
               '(SELECT 1 FROM PUBLIC.NATIVE_DELETE_S S WHERE T.ID=1);\n'
               'SELECT COUNT(*) AS EXISTS_COUNT FROM PUBLIC.NATIVE_DELETE_T;\nROLLBACK;\nQUIT;\n')
        result = subprocess.run([str(h.runtime/'bin/isql'),'-quiet','-sql_dialect',str(dialect),
                                 f'inet://127.0.0.1:{h.native_port}/wire_host'],
                                input=sql,env=env,text=True,capture_output=True,timeout=h.timeout)
        output = h.redact(result.stdout+result.stderr)
        (h.work/f'native_dialect_{dialect}.log').write_text(output)
        require(result.returncode != 0 and re.findall(r'SQLSTATE\s*=\s*(\w+)',output) == ['42000'] and
                numbers(output,'BEFORE_COUNT') == [2] and numbers(output,'AFTER_COUNT') == [1],
                'Native dialect guard or ordinary DELETE differs: '+str(dialect))
        require(numbers(output,'EXISTS_COUNT') == [1], 'Native target-only EXISTS differs')
    h.no_attachments(); h.native_probe('native_after_delete_using')
    h.summary.update(oracle_cases=report, oracle_case_count=len(report),
        recorded_delete_using=True, duplicate_source_delete_once=True, target_returning=True,
        bytea_prepared_text_binary=True, transaction_savepoint_rollback=True,
        native_committed_visibility=True, failed_prepare_scope_restored=True,
        psql_refused=True, procedure_source_refused=True, native_target_only_exists=True,
        native_dialects=[1,2,3])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firebird-dir',type=Path,required=True)
    parser.add_argument('--work-dir',type=Path,required=True)
    parser.add_argument('--oracle',type=Path,nargs='+',help='Optional saved private-PG oracle results; defaults to checked-in reference cases')
    parser.add_argument('--timeout',type=float,default=120)
    args = parser.parse_args(); work=args.work_dir.resolve(); work.mkdir(parents=True,exist_ok=False)
    oracle=[] if args.oracle else reference_cases()
    for path in args.oracle or ():
        data=json.loads(path.read_text()); require(data['passed'],'Reference oracle did not pass')
        oracle.extend(data['cases'])
    h=OneCHarness(args.firebird_dir.resolve(),work,args.timeout)
    try:
        run(h,oracle);h.summary['passed']=True
    except Exception as error:h.summary['error']=h.redact(str(error))
    finally:
        try:h.close()
        except Exception as error:h.summary.update(passed=False,shutdown_error=str(error))
        (work/'summary.json').write_text(json.dumps(h.summary,indent=2)+'\n')
        print(json.dumps({'passed':h.summary['passed'],'summary':str(work/'summary.json')}))
    return 0 if h.summary['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
