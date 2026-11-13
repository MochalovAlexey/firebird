#!/usr/bin/env python3
"""ANALYZE/fasttrun acceptance on synthetic LTTs and a private native runtime."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from pgwire_1c_smoke import Libpq, OneCHarness
from pgwire_namespace_checks import config_for, native
from pgwire_startup_smoke import Client
from wire_host_smoke import require, reserve_ports

PROFILE = Path(__file__).resolve().parents[1] / '1c'
sys.path.insert(0, str(PROFILE))
from install_compat import SCRIPTS


def checks(config):
    first, other = Libpq(config), Libpq(config)
    report = []
    def query(sql, rows=None, error=None, client=first):
        result = client.query(sql, True)[-1]
        require(result['sqlstate'] == (error or '') and
                (rows is None or result['rows'] == rows), sql + ': ' + repr(result))
        return result
    def clear(table='tt1'):
        query("SELECT fasttrun_ext('pg_temp." + table + "')", [['0']])
    try:
        query("SELECT name,installed_version FROM pg_catalog.pg_available_extensions ORDER BY name",
              [['fasttrun','2.0'],['fulleq','2.0'],['mchar','2.3']])
        query('CREATE TEMP TABLE tt1(id serial4,v integer,b bytea)')
        query('CREATE UNIQUE INDEX ix_tt1 ON tt1(v)')
        query("INSERT INTO tt1(v,b) VALUES(10,'\\x0100'::bytea)")
        query("INSERT INTO tt1(v,b) VALUES(20,'\\xff'::bytea)")
        query('BEGIN'); clear(); query('SELECT COUNT(*) FROM tt1', [['0']])
        query('ROLLBACK'); query('SELECT COUNT(*) FROM tt1', [['0']])
        query('INSERT INTO tt1(v) VALUES(30) RETURNING id', [['3']])
        query('BEGIN'); query('INSERT INTO tt1(v) VALUES(40)')
        clear(); query('INSERT INTO tt1(v) VALUES(50)'); query('ROLLBACK')
        query('SELECT COUNT(*) FROM tt1', [['0']])
        query('INSERT INTO tt1(v) VALUES(60) RETURNING id', [['6']])
        report.append('irreversible truncate survives rollback, serial is not reset or rolled back')

        query('BEGIN'); query('SAVEPOINT s'); query('ALTER TABLE tt1 ADD extra integer')
        query('INSERT INTO tt1(v,extra) VALUES(70,7)'); clear()
        query('INSERT INTO tt1(v,extra) VALUES(80,8)'); query('ROLLBACK TO s')
        query('SELECT COUNT(*) FROM tt1', [['0']])
        query('SELECT extra FROM tt1', error='42703'); query('ROLLBACK TO s')
        query('INSERT INTO tt1(v) VALUES(90)'); query('COMMIT')
        query('SELECT v FROM tt1', [['90']])
        query('BEGIN'); clear(); query('DROP TABLE tt1'); query('ROLLBACK')
        query('SELECT COUNT(*) FROM tt1', [['0']])
        query('INSERT INTO tt1(v) VALUES(100)')
        query('BEGIN'); query('SAVEPOINT s'); clear()
        query('INSERT INTO tt1(v) VALUES(110)')
        query('SELECT 1/0', error='22012'); query('ROLLBACK TO s')
        query('SELECT COUNT(*) FROM tt1', [['0']]); query('COMMIT')
        query('INSERT INTO tt1(v) VALUES(120)')
        query('INSERT INTO tt1(v) VALUES(120)', error='23505')
        query('SET TRANSACTION NO AUTO UNDO')
        query('INSERT INTO tt1(v) VALUES(150)'); clear()
        query('INSERT INTO tt1(v) VALUES(160)'); query('ROLLBACK')
        query('SELECT COUNT(*) FROM tt1', [['0']])
        query('INSERT INTO tt1(v) VALUES(170)'); query('BEGIN')
        query('DROP TABLE tt1'); query('CREATE TEMP TABLE tt1(v integer)')
        query('INSERT INTO tt1 VALUES(999)'); clear(); query('ROLLBACK')
        query('SELECT v FROM tt1', [['170']])
        report.append('mixed ALTER/DROP metadata undo, recreated identities, NO AUTO UNDO and failed statements')

        query('CREATE TEMP TABLE tt1(v integer)', client=other)
        query('INSERT INTO tt1 VALUES(777)', client=other)
        clear(); query('SELECT v FROM tt1', [['777']], client=other)
        query('CREATE TEMP TABLE "MiX.With Dot"(v integer)')
        query('INSERT INTO "MiX.With Dot" VALUES(1)')
        query("SELECT fasttrun_ext('pg_temp.\"MiX.With Dot\"')", [['0']])
        query('SELECT COUNT(*) FROM "MiX.With Dot"', [['0']])
        query('CREATE TEMP TABLE _mixed(v integer)')
        query('INSERT INTO _mixed VALUES(1)')
        query("SELECT fasttrun_ext('PG_TEMP._MIXED')", [['0']])
        query('SELECT COUNT(*) FROM _mixed', [['0']])
        query('SELECT fasttrun_ext(NULL)', [[None]])
        query("SELECT fasttrun_ext('pg_temp.missing')", error='42P01')
        query('ANALYZE pg_temp.missing', error='42P01')
        query("SELECT fasttrun_ext('SYSTEM.RDB$DATABASE')", error='42P01')
        query('INSERT INTO "PUBLIC"."WIRE_HOST_ROWS" VALUES(1)')
        query("SELECT fasttrun_ext('\"PUBLIC\".\"WIRE_HOST_ROWS\"')", error='42809')
        query('ANALYZE "PUBLIC"."WIRE_HOST_ROWS"', error='42809')
        query('SELECT "ID" FROM "PUBLIC"."WIRE_HOST_ROWS"', [['1']])
        query('DELETE FROM "PUBLIC"."WIRE_HOST_ROWS"')
        report.append('session isolation, exact quoted names, PG folding, strict NULL, missing/persistent rejection')

        query('CREATE TEMP TABLE stats(v integer)')
        query('INSERT INTO stats VALUES(0)')
        query('CREATE INDEX stats_ix ON stats(v)')
        query('EXECUTE BLOCK AS DECLARE i INTEGER=1; BEGIN WHILE(i<1000) DO '
              'BEGIN INSERT INTO "PGWIRE_TEMP"."stats" VALUES(:i); i=i+1; END END')
        explain = '''SELECT "CARDINALITY" FROM "SYSTEM"."RDB$SQL"."EXPLAIN"(
          'SELECT * FROM PGWIRE_TEMP."stats" WHERE "v"=99 PLAN ("stats" INDEX ("stats_ix"))')
          WHERE "OBJECT_NAME"='stats' AND "CARDINALITY" IS NOT NULL'''
        before = query(explain)['rows']
        query('BEGIN'); query('SAVEPOINT stats_scope')
        result = query('ANALYZE pg_temp.stats')
        require(result['tag'] == 'ANALYZE', 'ANALYZE completion tag')
        after = query(explain)['rows']
        require(before and after and min(float(row[0]) for row in after) < min(float(row[0]) for row in before),
                'Native optimizer cardinality did not improve: ' + repr((before, after)))
        query('ROLLBACK TO stats_scope'); query('COMMIT')
        query('SELECT COUNT(*) FROM stats', [['1000']])
        report.append({'native_index_statistics': {'before': before, 'after': after}})
        query('CREATE TEMP TABLE tt3(v integer)'); query('CREATE TEMP TABLE tt4(v integer)')
        query('INSERT INTO tt3 VALUES(3)'); query('INSERT INTO tt4 VALUES(4)')
        if config.get('capture'):
            for name in ('14-analyze.sql', '15-fasttrun-ext.sql', '16-analyze-fasttrun-batch.sql'):
                query((Path(config['capture'])/'profile/selected'/name).read_text())
            report.append('original selected14/15/16 executed unchanged on synthetic tables')
        query('SELECT COUNT(*) FROM tt3', [['0']] if config.get('capture') else [['1']])
        return report
    finally:
        first.close(); other.close()


def main():
    if sys.argv[1:] == ['--child']:
        try:
            print(json.dumps({'passed': True, 'checks': checks(json.load(sys.stdin))}))
            return 0
        except Exception as error:
            print(json.dumps({'passed': False, 'error': str(error)}))
            return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firebird-dir', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--capture-dir', type=Path)
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args()
    work = args.work_dir.resolve(); work.mkdir(parents=True, exist_ok=False)
    h = OneCHarness(args.firebird_dir.resolve(), work, args.timeout)
    try:
        h.prepare()
        h.isql('admin', f"CONNECT '{h.database}';\nCREATE OR ALTER USER SYSDBA PASSWORD '{h.password}';\nCOMMIT;\nQUIT;\n", embedded=True)
        native(config_for(h), 'compatibility', '\n'.join((PROFILE/name).read_text() for name in SCRIPTS), admin=True)
        api = work/'temp_services_api'
        compiled = subprocess.run(['c++','-std=c++17','-I',str(h.runtime/'include'),
            str(Path(__file__).with_name('pgwire_temp_services_api.cpp')),'-L',str(h.runtime/'lib'),
            '-lfbclient','-o',str(api)],capture_output=True,text=True,timeout=args.timeout)
        (work/'native-api-build.log').write_text(compiled.stdout+compiled.stderr)
        require(compiled.returncode == 0, 'Native service probe compilation failed')
        checked = subprocess.run([str(api),str(h.database)],env=h.env,capture_output=True,text=True,timeout=args.timeout)
        (work/'native-api.log').write_text(checked.stdout+checked.stderr)
        require(checked.returncode == 0, 'Native service guards failed')
        h.summary['native_api'] = json.loads(checked.stdout)
        native_port, pg = reserve_ports(2)
        h.start('temp_services', native_port, native_port, [{'alias':'PgWire','port':pg}])
        ready = Client(h).login(); ready.close()
        config = {'port':pg, 'password':h.password, 'capture':str(args.capture_dir) if args.capture_dir else None}
        result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--child'],
            input=json.dumps(config), capture_output=True, text=True, timeout=args.timeout)
        (work/'libpq.log').write_text(h.redact(result.stdout+result.stderr))
        require(result.stdout, 'No child report')
        h.summary['services'] = json.loads(result.stdout)
        require(result.returncode == 0, 'Temporary services failed: '+result.stdout)
        h.no_attachments(); h.native_probe('native_after')
        h.summary['passed'] = True
    except Exception as error:
        h.summary['error'] = h.redact(str(error))
    finally:
        try: h.close()
        except Exception as error: h.summary.update(passed=False,shutdown_error=str(error))
        (work/'summary.json').write_text(json.dumps(h.summary,indent=2)+'\n')
        print(json.dumps({'passed':h.summary['passed'],'summary':str(work/'summary.json')}))
    return 0 if h.summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
