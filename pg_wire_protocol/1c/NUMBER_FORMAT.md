# 1C number formatting

`sql/install_number_format.sql` installs a real native PSQL `public.format_number`
function. It uses existing decimal-to-text conversion, character-length,
substring, concatenation and replacement operations. The original 1C function's
grouping, secondary group size, separators, sign and NULL behavior are retained.
The common installer applies it after `install_1c_functions.sql`.

The first parameter is **VARCHAR**, not a claim to implement PostgreSQL's
variable-scale NUMERIC signature. Native SMALLINT/INTEGER/BIGINT and explicit
NUMERIC(p,s)/DECIMAL(p,s) SQL values coerce to text with their exact digits and
scale, including the native 38-digit boundary. No scale is imposed by the
function parameter. DECFLOAT(34) was not selected: it can round values beyond
34 significant digits and render scientific notation.

The reference behavior depends on scale: it trims the character set `0.` from
the right whenever a decimal point exists. Consequently `100.00` becomes `1`,
integer `100` remains `100`, and `0.00` becomes an empty string. This source
behavior is preserved deliberately. It is not ordinary trailing-fraction-zero
trimming.

Finite decimal text inputs accept a sign, leading zeroes, an optional decimal
point and surrounding ordinary spaces. They are normalized as PG NUMERIC would
be before applying the source function. Exponents, NaN, infinity and invalid
text are explicitly rejected. Group and decimal separators retain all characters,
including trailing spaces; their declared VARCHAR length in a PG function does
not restrict them to one character.

## Parameter and size limits

- Direct libpq numeric OID1700 parameters do not match the native VARCHAR
  descriptor and return `0A000`, in both text and binary format. Use an explicit
  SQL type, for example `format_number(CAST($1 AS NUMERIC(31,4)),3,0,',','.')`.
  Numeric text/binary Bind through that existing native cast is tested. Untyped
  or VARCHAR parameters accept decimal text directly.
- Only exact native integer/fixed-point SQL arguments are in this profile.
  Native floating and scientific SQL literals are outside it: their conversion
  to text can already lose original PG numeric scale before the function runs.
  A VARCHAR parameter cannot recover the original argument type. Use an explicit
  NUMERIC(p,s) SQL expression when that distinction matters. No core numeric type
  or implicit floating-to-PG-numeric conversion is added.
- Inputs, separators and output are bounded by native VARCHAR(8191) UTF8. Values
  outside native precision or string bounds fail; no truncation is performed to
  make them fit. This does not implement unbounded PostgreSQL NUMERIC or VARCHAR.
- Invalid decimal text raises the native user exception; the current PgWire
  error mapping reports `XX000`. General PostgreSQL `22P02` error equivalence is
  outside this SQL-only function. The native profile's existing charset rules
  and numeric overflow diagnostics remain in force.

Acceptance compares saved pure-SELECT results from a read-only reference session:

```sh
python3 -B pg_wire_protocol/tools/pgwire_number_format_checks.py \
  --firebird-dir /path/to/built/firebird --work-dir /tmp/pgwire-number-format \
  --oracle /path/to/number-format-oracle.json
```

The harness uses a private runtime/database. It checks exact literals, real
NUMERIC(31,s)/NUMERIC(38,s) columns, numeric binary/text Bind, unsupported parameter
forms, installation rollback/repetition and callable catalog visibility.
