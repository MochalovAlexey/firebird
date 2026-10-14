#!/usr/bin/env python3
"""P8 scalar codecs over real TCP/libpq in a new private Firebird runtime."""

import argparse
from datetime import date, datetime, time
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess

from pgwire_extended_query_smoke import (
    ExtendedHarness, ExtendedClient, bind, columns, describe, execute, parse, row)
from pgwire_startup_smoke import StartupHarness, packet
from wire_host_smoke import require, reserve_ports


# Independent PostgreSQL numeric fixtures: ndigits, weight, sign, display scale,
# then unsigned base-10000 digits. These do not call the implementation codec.
def numeric(weight, scale, digits, negative=False):
    return struct.pack('!HhHH', len(digits), weight, 0x4000 if negative else 0, scale) + \
        b''.join(struct.pack('!H', digit) for digit in digits)


MIN128 = '-170141183460469231731687303715884105728'
MAX128 = '170141183460469231731687303715884105727'
DECIMAL38 = '123456789012345678901234567890123456.78'


def iso_value(datatype, text):
    # Python 3.10 accepts only three or six fractional digits in fromisoformat.
    text = re.sub(r'\.(\d{1,6})$', lambda match: '.' + match[1].ljust(6, '0'), text)
    return {'DATE': date.fromisoformat, 'TIME': time.fromisoformat,
            'TIMESTAMP': datetime.fromisoformat}[datatype](text)


class CodecHarness(ExtendedHarness):
    def __init__(self, source, work, timeout, args):
        super().__init__(source, work, timeout, args)
        self.summary['stage'] = 'P8 float, finite temporal, INT128 and binary NUMERIC codecs'
        self.summary['codec_scope'] = ('Native SQL unchanged; finite AD dates 0001..9999, '
            'time/timestamp precision 100 microseconds; TZ, BC and temporal infinity unsupported')

    def prepare(self):
        StartupHarness.prepare(self)
        self.isql('codec_fixtures', f"""CONNECT '{self.database}';
CREATE TABLE P8_ROWS (ID INTEGER NOT NULL PRIMARY KEY, F4 FLOAT, F8 DOUBLE PRECISION,
  D DATE, TM TIME, TS TIMESTAMP, I INT128, N NUMERIC(38,2));
CREATE SEQUENCE P8_GUARD;
GRANT SELECT, INSERT, UPDATE, DELETE ON P8_ROWS TO USER WIRE_HOST_TEST;
GRANT USAGE ON SEQUENCE P8_GUARD TO USER WIRE_HOST_TEST;
INSERT INTO P8_ROWS VALUES(1,1.25,-2.5,DATE '1999-12-31',TIME '00:00:00.0001',
  TIMESTAMP '1999-12-31 23:59:59.9999',CAST('{MIN128}' AS INT128),
  CAST('{DECIMAL38}' AS NUMERIC(38,2)));
COMMIT;
QUIT;
""", embedded=True)

    def value(self, client, datatype, value, oid, input_binary=False, output_binary=False):
        events = client.exchange(parse('', f'SELECT CAST(? AS {datatype}) FROM RDB$DATABASE', [oid]) +
            bind(values=[value], formats=[int(input_binary)], results=[int(output_binary)]) +
            describe(b'P') + execute() + packet(b'S'), b'12TDCZ', tag='SELECT 1')
        metadata = columns(events[2][1])
        require(len(metadata) == 1 and metadata[0]['oid'] == oid and metadata[0]['modifier'] == -1 and
                metadata[0]['format'] == int(output_binary), f'{datatype}: result metadata differs')
        return row(events[3][1])[0]

    def floating_point(self):
        c = ExtendedClient(self).login()
        for datatype, oid, code, fixtures in (
            ('FLOAT', 700, 'f', ('00000000', '80000000', '3fa00000', '00800000',
                                '00000001', '7f7fffff', '7f800000', 'ff800000', '7fc00000')),
            ('DOUBLE PRECISION', 701, 'd', ('0000000000000000', '8000000000000000',
                'c004000000000000', '0010000000000000', '0000000000000001',
                '7fefffffffffffff', '7ff0000000000000', 'fff0000000000000', '7ff8000000000000'))):
            for hexadecimal in fixtures:
                wire = bytes.fromhex(hexadecimal)
                require(self.value(c, datatype, wire, oid, True, True) == wire,
                        f'{datatype}: network IEEE bytes changed for {hexadecimal}')
                text = self.value(c, datatype, wire, oid, True).decode()
                decoded = float(text)
                original = struct.unpack('!' + code, wire)[0]
                if math.isnan(original):
                    require(math.isnan(decoded) and text == 'NaN', 'NaN text representation differs')
                else:
                    require(struct.pack('!' + code, decoded) == wire,
                            f'{datatype}: text {text!r} does not round trip, including signed zero')
                require(self.value(c, datatype, text, oid, output_binary=True) == wire,
                        f'{datatype}: text input did not restore binary value')
        self.finish(c)

    def temporal(self):
        c = ExtendedClient(self).login()
        settings = dict(body[:-1].split(b'\0', 1) for kind, body in c.messages if kind == b'S')
        require(settings.get(b'integer_datetimes') == b'on', 'integer_datetimes must reflect int64 microseconds')
        fixtures = (
            ('DATE', 1082, '2000-01-01', struct.pack('!i', 0)),
            ('DATE', 1082, '1999-12-31', struct.pack('!i', -1)),
            ('DATE', 1082, '1858-11-17', struct.pack('!i', -51544)),
            ('DATE', 1082, '0001-01-01', struct.pack('!i', (date(1,1,1)-date(2000,1,1)).days)),
            ('DATE', 1082, '9999-12-31', struct.pack('!i', (date(9999,12,31)-date(2000,1,1)).days)),
            ('TIME', 1083, '00:00:00', struct.pack('!q', 0)),
            ('TIME', 1083, '00:00:00.0001', struct.pack('!q', 100)),
            ('TIME', 1083, '23:59:59.9999', struct.pack('!q', 86400000000-100)),
            ('TIMESTAMP', 1114, '2000-01-01 00:00:00', struct.pack('!q', 0)),
            ('TIMESTAMP', 1114, '1999-12-31 23:59:59.9999', struct.pack('!q', -100)),
            ('TIMESTAMP', 1114, '2000-02-29 12:34:56.1234', struct.pack('!q',
                (59*86400+12*3600+34*60+56)*1000000+123400)))
        for datatype, oid, text, wire in fixtures:
            require(self.value(c, datatype, text, oid, output_binary=True) == wire,
                    f'{datatype} {text}: epoch or precision conversion differs')
            require(self.value(c, datatype, wire, oid, True, True) == wire,
                    f'{datatype}: binary round trip differs')
            actual = self.value(c, datatype, wire, oid, True).decode()
            require(iso_value(datatype, actual) == iso_value(datatype, text), f'{datatype}: ISO text value differs: {actual!r}')
        self.finish(c)

    def exact_numeric(self):
        c = ExtendedClient(self).login()
        fixtures = (
            ('INT128', MIN128, numeric(9,0,[170,1411,8346,469,2317,3168,7303,7158,8410,5728],True)),
            ('INT128', MAX128, numeric(9,0,[170,1411,8346,469,2317,3168,7303,7158,8410,5727])),
            ('INT128', '0', numeric(0,0,[])),
            ('NUMERIC(18,4)', '123.4500', numeric(0,4,[123,4500])),
            ('NUMERIC(18,4)', '-123.4500', numeric(0,4,[123,4500],True)),
            ('NUMERIC(18,4)', '0.0000', numeric(0,4,[])),
            ('NUMERIC(38,2)', DECIMAL38, numeric(8,2,[1234,5678,9012,3456,7890,1234,5678,9012,3456,7800])),
            ('NUMERIC(38,38)', '-0.'+'0'*37+'1', numeric(-10,38,[100],True)),
            ('NUMERIC(38,38)', '0.12345678901234567890123456789012345678',
                numeric(-1,38,[1234,5678,9012,3456,7890,1234,5678,9012,3456,7800])))
        for datatype, text, wire in fixtures:
            require(self.value(c, datatype, text, 1700, output_binary=True) == wire,
                    f'{datatype}: independent base-10000 fixture differs')
            require(self.value(c, datatype, wire, 1700, True, True) == wire,
                    f'{datatype}: binary exact numeric round trip differs')
            require(self.value(c, datatype, wire, 1700, True).decode() == text,
                    f'{datatype}: exact decimal text or declared scale differs')
        # Text exponent parsing must remain exact across all 38 fractional digits.
        require(self.value(c,'NUMERIC(38,38)','1e-38',1700,output_binary=True) == numeric(-10,38,[100]),
                'numeric exponent was rounded or converted through floating point')
        self.finish(c)

    def simple_null_and_visibility(self):
        c = ExtendedClient(self).login()
        result = c.query(b'SELECT F4,F8,D,TM,TS,I,N FROM P8_ROWS WHERE ID=1')
        require(result['error'] is None and result['command'] == 'SELECT 1' and result['state'] == 'I',
                f'Simple Query extended types failed: {result!r}')
        require([col['oid'] for col in result['columns']] == [700,701,1082,1083,1114,1700,1700],
                'Simple Query type OIDs differ')
        values = result['rows'][0]
        require(float(values[0]) == 1.25 and float(values[1]) == -2.5 and
                date.fromisoformat(values[2]) == date(1999,12,31) and
                iso_value('TIME', values[3]) == time(0,0,0,100) and
                iso_value('TIMESTAMP', values[4]) == datetime(1999,12,31,23,59,59,999900) and
                values[5] == MIN128 and Decimal(values[6]) == Decimal(DECIMAL38),
                'native INSERT is not visible through matching PgWire codecs')
        for datatype, oid in (('FLOAT',700),('DOUBLE PRECISION',701),('DATE',1082),('TIME',1083),
                              ('TIMESTAMP',1114),('INT128',1700),('NUMERIC(38,38)',1700)):
            for binary in (False, True):
                require(self.value(c,datatype,None,oid,binary,binary) is None,
                        f'{datatype}: NULL was replaced by zero/empty')
        self.finish(c)

    def returning(self):
        c = ExtendedClient(self).login()
        sql = ('INSERT INTO P8_ROWS(ID,F4,F8,D,TM,TS,I,N) VALUES(2,?,?,?,?,?,?,?) '
               'RETURNING F4,F8,D,TM,TS,I,N')
        values = ['1.25','-2.5','1999-12-31','00:00:00.0001','1999-12-31 23:59:59.9999',MIN128,DECIMAL38]
        expected = [bytes.fromhex('3fa00000'),bytes.fromhex('c004000000000000'),struct.pack('!i',-1),
                    struct.pack('!q',100),struct.pack('!q',-100),
                    numeric(9,0,[170,1411,8346,469,2317,3168,7303,7158,8410,5728],True),
                    numeric(8,2,[1234,5678,9012,3456,7890,1234,5678,9012,3456,7800])]
        events = c.run(sql, values, results=[1], rows=[expected], tag='INSERT 0 1')
        require([column['oid'] for column in columns(events[2][1])] == [700,701,1082,1083,1114,1700,1700],
                'RETURNING codec metadata differs')
        actual = self.scalar_native("SELECT COUNT(*) AS OBSERVED FROM P8_ROWS WHERE ID=2 AND F4=1.25 "
            "AND F8=-2.5 AND D=DATE '1999-12-31' AND TM=TIME '00:00:00.0001' "
            "AND TS=TIMESTAMP '1999-12-31 23:59:59.9999' "
            f"AND I=CAST('{MIN128}' AS INT128) AND N=CAST('{DECIMAL38}' AS NUMERIC(38,2))")
        require(actual == 1, 'native observer did not see exact committed Extended INSERT values')
        result = c.query(b'UPDATE P8_ROWS SET D=DATE \'2000-01-01\' WHERE ID=2 RETURNING D,I,N')
        require(result['error'] is None and result['command'] == 'UPDATE 1' and
                result['rows'] == [['2000-01-01',MIN128,DECIMAL38]], 'Simple UPDATE RETURNING values differ')
        self.finish(c)

    def invalid_before_effects(self):
        c = ExtendedClient(self).login()
        before = self.scalar_native('SELECT GEN_ID(P8_GUARD,0) AS OBSERVED FROM RDB$DATABASE')
        invalid = (
            ('FLOAT','1e100',False,'22003'), ('FLOAT','1e-100',False,'22003'),
            ('DOUBLE PRECISION','1e10000',False,'22003'), ('DOUBLE PRECISION','1,25',False,'22P02'),
            ('FLOAT',b'\0'*3,True,'22P03'), ('DOUBLE PRECISION',b'\0'*7,True,'22P03'),
            ('DATE','2000-02-30',False,'22008'), ('DATE','2000-01-01 BC',False,'22007'),
            ('DATE','infinity',False,'22008'), ('DATE',struct.pack('!i',2147483647),True,'22008'),
            ('TIME','00:00:00.000001',False,'22008'), ('TIME','24:00:00',False,'22008'),
            ('TIME','12:00:00+03:00',False,'22007'), ('TIME',struct.pack('!q',1),True,'22008'),
            ('TIME',struct.pack('!q',86400000000),True,'22008'),
            ('TIMESTAMP',struct.pack('!q',-1),True,'22008'),
            ('TIMESTAMP','2000-01-01T00:00:00Z',False,'22007'),
            ('TIMESTAMP',b'\0'*7,True,'22P03'),
            ('INT128','170141183460469231731687303715884105728',False,'22003'),
            ('NUMERIC(38,2)','1.001',False,'22003'),
            ('NUMERIC(38,2)','NaN',False,'22003'), ('NUMERIC(38,2)','Infinity',False,'22003'),
            ('NUMERIC(38,2)',struct.pack('!HhHH',0,0,0xC000,0),True,'22003'),
            ('NUMERIC(38,2)',struct.pack('!HhHH',0,0,0xD000,0),True,'22003'),
            ('NUMERIC(38,2)',struct.pack('!HhHHH',1,0,0,0,10000),True,'22P03'),
            ('NUMERIC(38,2)',numeric(0,2,[1,1]),True,'22P03'))
        for datatype, value, binary, state in invalid:
            sql = f'SELECT CAST(? AS {datatype}),GEN_ID(P8_GUARD,1) FROM RDB$DATABASE'
            try:
                c.exchange(parse('',sql) + bind(values=[value],formats=[int(binary)]) + execute() +
                           packet(b'S'), b'1EZ', error=state)
            except RuntimeError as error:
                raise RuntimeError(f'{datatype} value={value!r}: {error}') from error
            require(self.scalar_native('SELECT GEN_ID(P8_GUARD,0) AS OBSERVED FROM RDB$DATABASE') == before,
                    f'invalid {datatype} input executed irreversible sequence effect')
        self.transaction_count(0)
        self.finish(c)

    def build_c_driver(self):
        source = Path(__file__).with_name('pgwire_libpq_codecs.c')
        self.c_driver = self.work / 'pgwire_libpq_codecs'
        result = subprocess.run([self.args.cc,'-std=c11','-Wall','-Wextra','-Werror','-O2',
            '-I',str(self.args.libpq_include_dir),str(source),str(self.args.libpq_library),'-o',str(self.c_driver)],
            capture_output=True,text=True,timeout=self.timeout)
        (self.work/'libpq-compile.log').write_text(result.stdout+result.stderr)
        require(result.returncode == 0, 'C libpq codec compilation failed; see libpq-compile.log')
        self.summary['c_build'] = {'headers':str(self.args.libpq_include_dir),
            'library':str(self.args.libpq_library),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}

    def run_codecs(self):
        native, configured, port = reserve_ports(3)
        self.start('codecs',native,configured,[{'alias':'PgWire','port':port}])
        self.finish(ExtendedClient(self).login())
        for name, action in (
            ('direct_c_libpq_extended_codec_text_binary_null_and_inference',self.c_libpq),
            ('ieee_binary_signed_zero_subnormal_extremes_and_text_roundtrip',self.floating_point),
            ('temporal_epochs_iso_bounds_and_exact_100us',self.temporal),
            ('int128_full_range_scale38_exact_numeric_base10000',self.exact_numeric),
            ('simple_query_native_visibility_and_nulls',self.simple_null_and_visibility),
            ('extended_insert_and_simple_update_returning_native_visibility',self.returning),
            ('invalid_codec_values_rejected_before_irreversible_effects',self.invalid_before_effects)):
            self.check(name,action)
        self.native_probe('native_after_codecs')
        self.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firebird-dir',type=Path,required=True)
    parser.add_argument('--work-dir',type=Path,required=True)
    parser.add_argument('--libpq-include-dir',type=Path,required=True)
    parser.add_argument('--libpq-library',type=Path,required=True)
    parser.add_argument('--cc',default='cc')
    parser.add_argument('--timeout',type=float,default=30,help='external test watchdog, not a server setting')
    args = parser.parse_args()
    source, work = args.firebird_dir.resolve(), args.work_dir.absolute()
    if work.exists() or work.is_symlink() or source in work.resolve().parents or not 0 < args.timeout <= 300:
        parser.error('use a new work directory outside the build and timeout 0..300')
    if not re.fullmatch(r'[A-Za-z0-9_./-]+',str(work)):
        parser.error('work directory must use safe ASCII path characters')
    for name in ('bin/firebird','bin/isql','security6.fdb','plugins/libPgWire.so'):
        if not (source/name).is_file(): parser.error(f'completed build required: {source/name}')
    args.libpq_include_dir = args.libpq_include_dir.resolve()
    args.libpq_library = args.libpq_library.resolve()
    for name in ('libpq-fe.h','postgres_ext.h','pg_config.h'):
        if not (args.libpq_include_dir/name).is_file(): parser.error(f'genuine libpq header required: {name}')
    args.cc = shutil.which(args.cc)
    if not args.cc or not args.libpq_library.is_file(): parser.error('C compiler and real libpq library required')
    work.mkdir(mode=0o700)
    h = CodecHarness(source,work.resolve(),args.timeout,args)
    try:
        h.prepare()
        h.build_c_driver()
        h.run_codecs()
        h.run_trace()
        h.summary['passed'] = True
    except (Exception,KeyboardInterrupt) as error:
        h.summary['error'] = h.redact(f'{type(error).__name__}: {error}')
    finally:
        try: h.close()
        except Exception as error:
            h.summary['passed'] = False
            h.summary['shutdown_error'] = h.redact(str(error))
        h.summary['checks'] = h.cases
        path = work/'summary.json'
        path.write_text(json.dumps(h.summary,indent=2)+'\n')
        print(json.dumps({'passed':h.summary['passed'],'summary':str(path)},indent=2))
    return 0 if h.summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
