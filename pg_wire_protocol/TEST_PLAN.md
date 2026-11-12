# План приёмки wire plugins и PostgreSQL endpoint

## APP-15: полный согласованный профиль

Реализация и локальная приёмка выполнены; рабочий diff ждёт review. Повторяемая
команда — `tools/pgwire_1c_smoke.py --temporary --firebird-dir RUNTIME
--previous-firebird-dir ACCEPTED_NAT06_RUNTIME --work-dir NEW_TMP_DIR --timeout 300`.
Продолжает существующий harness: `pgwire_temporary_checks.py`, raw ExtendedClient
и `pgwire_temporary_api.cpp`, без отдельного test framework.

Проверены все TEMP spellings, qualifier/unsupported boundaries, `_`/quoted/Unicode,
source reprepare, DML/indexes/коллизии, PRESERVE/DELETE/savepoints/rollback, две
сессии, namespace undo/empty state/IDs, live catalogs после MON, NULL/text/binary,
read-only views, path/USAGE/$user/PUBLIC/role и SCL без SELECT. Cache включён:
named Bind после DDL/path/RESET, Parse/Describe/Close без allocation, paused portal,
1030 public IDs при reuse 1024 native IDs и отдельный old native prepared handle.
OO: NO AUTO UNDO, force, retaining, большой root (300000 rows), native1/2/3 defaults,
DDL guard/stale target, MAKE_DBKEY и allocator exhaustion. Installer: fresh через
install_compat.py, upgrade с принятого runtime/ODS14.0, rollback/repeat/collisions,
низкий/высокий counter, native container guard, exact provider contract negatives.

Существующие names/strings/visibility/BEGIN регрессии PASS; P2 guard-after-argument
сохранён и проверен, включая NULL после создания unmanaged LTT аргументом.
NAT-01…04 не повторялись; исходный NAT-05 reproducer не изменён и не объявлен
исправленным. Oracle 18.6 переиспользован. Точные commands/hashes/результаты и
cleanup — [VALIDATION](VALIDATION.md). Исходные selected04/12/13 и реальная 1С/Гилёв
не входят в PASS этой матрицы.

## APP-14: приёмка visibility

`tools/pgwire_1c_smoke.py --visibility --firebird-dir NEW_RUNTIME --previous-firebird-dir APP13_RUNTIME --work-dir NEW_TMP --timeout 180`
использует прежние isolated runtime/libpq/native helpers и OO probe одного
подготовленного statement. [Задание и полная матрица](1c/RELATION_VISIBILITY_TASK.md)
проверяют pg_table_is_visible на текущем persistent catalog: совпадение с новым
Prepare реального SELECT, shadowing/перестановка пути, неизвестные OIDs, text/binary
BOOLEAN, права вызывающего, Simple/Extended/RESET и явные отказы вне профиля.
LTT guard проверяется после создания LTT в той же транзакции, когда MON snapshot
уже получен. Проверка старой базы под новым runtime исключает скрытую зависимость
от новой system-package metadata. Исходный selected04 не объявлять PASS:
regnamespace/temp namespace и полный PG search_path остаются отдельными задачами.
Независимый review повторил 174 libpq results и 46 OO assertions PASS, но выявил
отдельный P2 (порядок guards/EVL_expr), не покрытый прежней матрицей. После fix
12.11 проходят 174 libpq / 60 OO assertions, с red/green контролем регрессии. Cache/NAT-05
существует до APP-14 в нашем fork; это не доказательство upstream дефекта или его исправления.

## APP-13: PG names и native source

Свежая developer build, отдельные /tmp runtime/security/application databases.
`tools/pgwire_1c_smoke.py --pg-names --firebird-dir BUILD/gen/Debug/firebird --work-dir NEW_TMP`
проверяет ASCII folding, quoted case/Unicode/ограничения, CTE/aliases/non-reserved
names, routines/named args, native DML readback, source VIEW/DEFAULT/CHECK/COMPUTED/
index/PSQL и native reprepare, CTAS, root guards, search_path/RESET Simple+Extended,
savepoint strings/E/T/ParameterStatus, installer fresh/existing public/atomic failure/
repeat/collision/read-only и native catalog oracle. С `--capture-dir CAPTURE` читает
исходный selected01; `--blobs`/`--blob-input` проверяют неизменённые selected06/23 и
binary fixtures. Нельзя подменять metadata/byte/state expectations ради naming.

`--strings` включает прямой API probe: native1/2/3, cache с одинаковым SQL в разных
dialects, embedded NUL и AUTOTERM. Дополнительно startup/settings, Simple/Extended,
script, namespace/class, parameters/casts/limits/begin, девять unit executables.
В старых fixtures PG SQL явно quotes uppercase native objects; native1/2/3 probe
SQL остаётся исходным. Credentials не сохраняются в reports. Known NAT-* crash
repro не запускать; selected04/10/11 и реальное открытие 1С не объявлять PASS.
Команды, runtime/source SHA, FAIL до исправлений и server exit — в VALIDATION.

База — `master` Firebird 6, SHA из [BASELINE.md](BASELINE.md). Архитектура и SQL boundary определены в [ARCHITECTURE.md](ARCHITECTURE.md), transaction contract — в [CODEX_CONTEXT.md](CODEX_CONTEXT.md), заявляемые возможности — в [COMPATIBILITY.md](COMPATIBILITY.md).

Python scaffold мигрирован под текущий контракт: 58 live cases прошли в P6. Начальные host tests расположены в src/common/tests/WireProtocolConfigTest.cpp, src/remote/server/wire/tests и isolated tools harness. P2/P3/P4 приёмка сохранена в VALIDATION. Предыдущая P5-ревизия прошла целевую Linux/Super приёмку с native ограничением READ CONSISTENCY + AUTO COMMIT; P6 повторил сборку, unit tests и целевые регрессии по [VALIDATION.md](VALIDATION.md). Целевой P6/M1 Linux/Super принят с известными native ограничениями, включая отдельно исключённый ON CONNECT shutdown. P7 Extended Query прошёл целевую Linux/Super приёмку: 16 групп. P8 codecs прошли целевую Linux/Super приёмку: восемь групп. P9 прошёл 17 групп с реальными C libpq, raw-wire и ограниченным профилем Psycopg 3; целевой M2 Linux/Super для совместимого SQL и реализованных codecs принят. Следующий этап — P10. PASS относится к зафиксированным cases/fixtures, а наличие строки в полной матрице не означает её прохождения.

## Подэтап 1С: постоянные relations в PG_CLASS

`pgwire_1c_smoke.py --class-catalog` / `pgwire_class_checks.py` переиспользуют
native/libpq harness и helpers `pgwire_namespace_checks.py`. До полной проверки —
небольшой native prototype; runtime по SHA, отдельные /tmp fixtures/security/БД.

- Fresh installer после APP-10/11 с search_path=SYSTEM; missing PG_NAMESPACE,
  повтор, чужой PG_CLASS и injection перед COMMIT. Сохраняются bootstrap
  owner/source/BLR, прежние grants/objects, namespace mapping/counter; нет partial view.
- Независимый native oracle читает все RDB$RELATIONS, затем фильтрует type 0/1
  и отсутствие package в Python и использует private namespace mapping.
  Совпадают все четыре поля; OID уникален/положителен, нет потерянных namespace
  references, RDB$PAGES 0→1, self PG_CLASS kind `v`.
- Simple/Extended/named prepared по ID, имени+namespace и namespace: text/binary
  inputs/results, SQL-derived params, metadata 20/1043/20/1043, sizes 8/-1/8/-1,
  typmods -1, uppercase labels. IDs — 8 bytes network order, имена/kind — точные
  UTF8 bytes, включая 56 Я / 112 bytes. Alias/CAST сохраняют BIGINT type 20.
- PUBLIC/"public", T/"t", одинаковые имена в двух схемах, mixed case/Unicode/
  пробел/кавычка, table/view. Diagnostic relkind/relname/relnamespace/LIMIT даёт
  правильные строки/missing name; это не PASS исходного selected04/10/11.
- Таблица/view без SELECT и schema без USAGE присутствуют, прямое чтение данных
  запрещено. DML view запрещён и owner. Новых data/DDL grants нет; repeated
  native/libpq READ ONLY сохраняет relation metadata и namespace mapping/counter.
- TABLE и VIEW CREATE/ALTER/DROP, COMMIT/full ROLLBACK, второй attachment:
  настоящий own uncommitted ID, отсутствие чужих uncommitted CREATE/DROP,
  стабильный ALTER ID, prepared lookup после DROP/CREATE сверяется с текущим
  native ID без требования монотонности; rollback не оставляет phantom rows.
- GTT обоих видов, packaged temporary table, virtual и metadata-only external
  table исключены; external fixture только /tmp, без изменения config (иначе
  явно SKIP). Index/sequence не дают rows. Отдельный native attachment использует
  LTT/declared temporary table без фиктивной постоянной catalog row.

Обязательны native coexistence, отсутствие оставшихся attachments и normal
shutdown; команды/revision/SHA/PASS/FAIL/SKIP в VALIDATION. Wrap exhaustion,
allocator mutation, backup/restore, full APP-10/11 suites и known NAT-* crash repro
не запускаются. Native DDL под user savepoint запрещён; положительный DDL
ROLLBACK TO не принимается. Полный PG catalog/types/name resolution/visibility/
temp namespace, APP-01/03/04/06 и selected04/10/11 остаются открытыми.

## Подэтап 1С: публичное представление namespace IDs

`pgwire_1c_smoke.py --namespace-catalog` / `pgwire_namespace_checks.py` расширяют
прежний native isql/libpq harness. Только SQL/harness, проверенный runtime по SHA,
отдельные БД/security/runtime в /tmp; rebuild и полная матрица APP-10 не нужны.

- Установка после APP-10 при search_path=SYSTEM с bootstrap PG_CATALOG и без неё.
  View равен private mapping; прежние IDs, owner/source/BLR/grants PG_BACKEND_PID
  сохранены. Новая PG_CATALOG регистрируется hook ровно один раз.
- Отсутствующий APP-10, повтор и чужой PG_NAMESPACE: явные отказы без изменения
  старого объекта, данных, owner, grants и counter. Ошибка перед COMMIT откатывает
  view/grants и новую schema с registry row. Допустимы sequence gaps.
- Native/Simple/Extended/prepared lookup по имени и ID: exact case, PUBLIC/"public",
  пробел/кавычка, mixed case, Unicode имя 56 символов / 112 UTF8 bytes, missing name.
  SQL-derived input OIDs 1043/20; text/binary results и inputs, metadata 20/1043,
  sizes 8/-1, typmods -1, uppercase labels. BIGINT — ровно 8 bytes, имена — UTF8.
- Alias, derived SELECT, CAST и UNION с BIGINT 4294967296 в обоих порядках ветвей:
  type 20, точные text/binary values без усечения до uint32 или подмены по origin.
- Обычный пользователь читает view, включая schema без USAGE; новых base/sequence
  или CREATE SCHEMA grants нет. INSERT/UPDATE/DELETE запрещены и владельцу.
  Повторные native/libpq READ ONLY lookups не меняют counter; PG_BACKEND_PID работает.
- CREATE/ALTER/DROP, COMMIT/full ROLLBACK, два attachments: private/public mapping
  совпадают, uncommitted changes остаются локальны, ALTER сохраняет ID, DROP+CREATE
  меняет его, prepared lookup видит новую identity. DDL-право выдаёт сама фикстура
  перед созданием DDL attachment; installer его не выдаёт.

Нормальный server shutdown обязателен. Результаты, commands, revision и SHA —
в VALIDATION. PG oid/name/regnamespace, nspowner/nspacl, PG name resolution,
pg_class/visibility/temp namespace и selected04 этим срезом не принимаются.
Сохранён native запрет DDL под user savepoint (0A000); known NAT-* crash repro,
полные concurrency/restart и старые suites повторно не запускаются.

## Подэтап 1С: native namespace registry

`pgwire_1c_smoke.py --namespaces` / `pgwire_namespace_checks.py` используют
существующий private runtime/native isql/libpq harness, без C++ изменений.
Переиспользуется проверенная сборка; SQL-only этап не требует полного rebuild.

- Fresh installer с SET BAIL/AUTODDL OFF/одним COMMIT при search_path=SYSTEM;
  preexisting schemas + служебная schema, точный native case/Unicode/пробелы/
  кавычки, leading space и native максимальное имя 56 символов. Уникальные
  имена и IDs, NOT NULL и диапазон 1…4294967295; initial mapping равен RDB$SCHEMAS.
- Повторный installer и чужая PGWIRE_COMPAT: явный отказ до изменений mapping,
  объектов, owner, данных и counters. Искусственные ошибки в середине и перед
  COMMIT: нет частичной schema/table/view/sequence/trigger, свежий install успешен.
- Native и libpq schema CREATE/ALTER/DROP, CREATE OR ALTER, RECREATE, IF NOT EXISTS/
  IF EXISTS, failed RESTRICT и DROP CASCADE. ALTER/no-op сохраняют ID/counter,
  DROP+CREATE даёт новый ID; non-schema DDL/DML не меняет registry.
- Ordinary user создаёт/изменяет/удаляет свою schema через definer hook. Native
  SELECT view не требует прав на base table; прямые SELECT/DML base, allocation/
  reseed/drop sequence, alter/drop trigger и alter base запрещены. DISTINCT view
  read-only даже для owner. CHECK failure registry отменяет исходный CREATE SCHEMA.
- Full COMMIT/ROLLBACK, допустимые sequence gaps; native запрет DDL под user
  savepoint (0A000) и PgWire E/ROLLBACK TO recovery. DDL до savepoint сохраняется
  после ROLLBACK TO и исчезает после полного ROLLBACK. Положительный undo schema
  DDL под savepoint не поддерживается движком и не считается PASS.
- Два одновременно активных transactions: разные schemas получают разные IDs,
  uncommitted rows не видны, после commit mapping одинаков. Same-name conflict
  сверяется с native NO WAIT при явно завершённом первом CREATE, без sleeps;
  rollback/retry не оставляет phantom/duplicate namespace.
- Повторные native/libpq READ ONLY lookups и missing name не меняют mapping или
  sequence. Native и libpq видят одинаковые IDs, wire column — BIGINT/OID 20.
  Новый attachment и normal server restart сохраняют mapping/counter; оба exit 0.

Сравниваются SQL/harness и runtime SHA. Все БД/security/configs отдельные в /tmp.
Known native crash repro и несвязанные старые suites не запускаются. Disabled
hooks/repair, backup/restore/migration, public catalogs/oid/regnamespace/name
mapping/visibility не заявляются; APP-01/APP-06/selected04 остаются открытыми.

## Подэтап 1С: bytea input через VARCHAR OCTETS

`pgwire_1c_smoke.py --blob-input` / `pgwire_blob_input_checks.py` используют прежний
libpq/raw/native harness. У существующего `pgwire_blob_fixture.cpp` добавлен режим
`DB --read MANIFEST`: новое native TCP attachment читает BLOB по SELECT и сверяет
все bytes с synthetic hex/NULL manifest, без PgWire output codec.

- OID 17, absent/zero/17 hint и mismatch, subtype/scale/ISC_QUAD descriptor gate,
  SQL-04 HY004, обычный OCTETS и ::bytea negatives. PQexecParams и named prepared.
- Text hex/escape, оба регистра и четыре whitespace только между парами;
  malformed 22023/22P02, raw UTF8/NUL 22021; NULL/empty, 256 bytes, decoded invalid
  UTF8/NUL/backslash/quote, printable UTF8, отсутствие trim/SQL unescape и эффекта
  standard_conforming_strings. Отдельный Postgres Pro 18.6 через libpq как reference.
- Длины 0/1/32765/32766/40444/65533 в обоих formats, 65534 -> 0A000 до Execute.
  Layout ровно 1 MiB и overflow с scalar neighbours/alignment; actual cache 8 MiB.
  68 BLOB среди 92 mixed params, 20 одновременно bound short-value portals.
- Unit long -> NULL/empty -> short -> long, согласованность buffer/metadata,
  неизменная metadata scalar-only codec, сохранение прежнего layout после ошибки.
- Дословный selected23 UPDATE на synthetic PUBLIC.FILES: все пять параметров,
  UPDATE 1/0, WHERE и native byte/scalar readback, payload 40444 synthetic bytes.
- Parse/Bind/Describe/Flush и Close без Execute: нет transaction/table/sequence
  effects; invalid bytea/unsupported output и discarded messages до Sync.
- INSERT/UPDATE RETURNING, typed SELECT и singleton native `?` EXECUTE PROCEDURE;
  независимые длинный/короткий portals, mixed formats, suspend/resume, execute-once,
  prepared reuse через commit/rollback. I/T/E/25P02/savepoint/disconnect с native
  visibility, ровно одна application transaction в T и отсутствие её после cleanup.
  Engine duplicate-key error 23505 после conversion отличается от Bind refusal:
  BEFORE INSERT sequence фиксирует исполнение, данные откатываются.

Обязательные проверки APP-08: make -j2, make -C gen -j2 TARGET=Debug pgwire_tests,
девять pgwire units и wire_socket_test, --blob-input, прежний --blobs,
pgwire_codec_smoke.py, Extended Query и --parameters. Production diff ограничен
InputCodec.cpp/h, engine/parser/ResultCodec/API не меняются; DT_NEEDED прежний.
APP-09/APP-02/SQL-06/APP-05 остаются открытыми; Psycopg 3 SKIP, старый VAL-02 FAIL
и known native crash exclusions не переоцениваются этим прогоном.

## Подэтап 1С: binary BLOB output

`pgwire_1c_smoke.py --blobs` / `pgwire_blob_checks.py` используют существующий
libpq/raw/native harness. Небольшой `pgwire_blob_fixture.cpp` создаёт IBlob API
fixtures и перечитывает их с нового native attachment после commit.

- SQL NULL, empty, все 256 байтов, NUL/backslash/quote/invalid UTF8; segmented и
  stream, пустые и несколько сегментов, данные больше read buffer. Тип stream
  проверяется getInfo; native bytes сравниваются независимо от wire.
- Настоящие PQexec/execParams/prepare/Describe/execPrepared: OID 17, size/typmod -1,
  точные text hex/binary payload и длины; PQunescapeBytea с PQfreemem для text.
  Неизменный selected06 SQL на синтетической PUBLIC.PARAMS, несколько PartNo.
- Simple/Extended cursor и singleton: SELECT/RETURNING, native BLOCK/PROCEDURE,
  repeated prepared execution через commit/rollback; два portals, suspension/
  resume/close/rebind, смешанные formats, NULL против empty. Parse/Bind не открывают
  application transaction; $n root guard сохраняется, в PROCEDURE используется `?`.
- Точная граница и превышение DataRow на байт в binary и text/mixed строках;
  несколько BLOB, scalar и NULL. Огромный BLOB отвергается с ограниченным накоплением.
  Ошибка следующей строки сохраняет предыдущую DataRow, новую частично не посылает.
- Ошибки результата в I/T, Simple script rollback/stop, Extended E/25P02/Sync,
  DML RETURNING overflow и native readback после rollback; SELECT recovery.
  TEXT/другие subtype, ::bytea и SET bytea_output отказаны; subtype-0 input/NULL теперь проверяются положительно (APP-08).
- ResultCodec/Protocol units: уменьшенный фактический frameLimit, общий budget,
  scalar после BLOB, пустые сегменты, EOF после полного бюджета и последующий
  непустой сегмент, ID zero без NULL indicator, malformed descriptor/context.
  Минимальные ABI endpoints по образцу SessionTest проверяют ошибки open/read/close,
  неожиданный read result/length, consuming close и release без двойного освобождения.
  Unit injection не заменяет реальные engine I/O faults — оставшаяся область VAL-04.

Обязательные регрессии: все pgwire unit executables, wire_socket_test,
pgwire_codec_smoke.py, Simple/Extended и --casts. Меняются только непосредственно
затронутые negative expectations binary BLOB; для APP-07 InputCodec и parser/core diff пустые.
readelf DT_NEEDED сравнивается с baseline. Historical P3 VARCHAR FAIL (VAL-02)
и known native crash repro не включаются в переоценку. APP-07 закрывает только
output; APP-02/SQL-06, оригинальная схема/Bind и запуск 1С остаются вне приёмки.

## Подэтап 1С: literal LIMIT

`pgwire_1c_smoke.py --limits` / `pgwire_limit_checks.py` расширяют существующий
libpq/raw/native harness, OO checks — в прежнем `--strings`. Fixtures: четыре
строки и отдельная таблица для DML; большие count не требуют больших данных.

- Simple 0/1/2/100001/MAX: точные rows/OIDs/tags как FIRST/ROWS, no-FROM/empty,
  leading zero, decimal/non-decimal integer spellings и `_`.
- ORDER по уникальному ID, DISTINCT, GROUP/aggregate, CTE, derived/scalar; внешний
  UNION/UNION ALL limit и parenthesized operands с различимыми результатами,
  включая LIMIT 0 без ORDER. Native UNION ORDER BY position сохраняется.
- INSERT SELECT с commit/rollback, SELECT LIMIT внутри UPDATE/DELETE/MERGE,
  значения и число строк проверяет отдельный native observer.
- PQexecParams, PQprepare/Describe/execPrepared: WHERE `$1` + literal LIMIT,
  повторный Bind, NULL/text/binary, прежний `?` и сочетание `$1::INTEGER`.
  Raw maxRows < SQL count, suspension/resume, независимые portals и LIMIT 0 T/C.
- Неподдержанные counts, overflow/non-decimal boundary/огромное число, incomplete
  и repeated LIMIT, `SELECT 1 LIMIT`, PG OFFSET и ROWS/OFFSET…FETCH mixing;
  FIRST/SKIP на том же/разных уровнях, запрет UPDATE/DELETE suffix LIMIT.
  После каждого raw отказа queued DML отбрасывается до Sync, SELECT работает.
  Simple прекращает скрипт и откатывает текущий implicit сегмент; explicit T→E.
- CTAS/CTE/VIEW/PROCEDURE/BLOCK/local function/CALL/PROCEDURE/USING/UPDATE OR INSERT
  с LIMIT дают 0A000 до эффектов; observer проверяет отсутствие объектов/строк.
  Native FIRST/ROWS и текст LIMIT в stored source сохраняются.
- Native1/2/3 identifier LIMIT, AUTOTERM recovery и прежние row limits;
  quoted LIMIT в 3/4, PG→native→PG на одном attachment/cache, strings/Q/comments
  при on/off и исходные offsets. LIMIT flag устанавливается только под !yytrial.

Baseline 158 shift/reduce + 11 reduce/reduce; после изменения те же конфликтующие
правила/токены и количество. Обязательные регрессии: SQL/session/isql units,
--parameters, --casts, --strings, Simple/Extended. Known native crash repro не
запускать. SQL-02, полный selected 04/17 и открытие 1С этим не принимаются;
исторический P3 VARCHAR FAIL сохраняется. Результаты — в VALIDATION.

## Подэтап 1С: postfix casts

`pgwire_1c_smoke.py --casts` / `pgwire_cast_checks.py` используют прежний
libpq/raw/native harness и синтетические fixtures. OO helper расширен в `--strings`.

- Simple: равенство результата/metadata и ошибок явному native CAST; обязательные
  типы, NULL, регистры INT8, колонки, CTE/subqueries. `1.2+1.6::int`, parentheses,
  `-32768::SMALLINT`, цепочки с промежуточным overflow/truncation, concat/COLLATE/AT.
- PQexecParams и named PQprepare/PQdescribePrepared/PQexecPrepared: input INTEGER
  и output VARCHAR в цепочке, несколько типов, NULL, text/binary Bind/results,
  повторное исполнение, zero/compatible/incompatible OID и прежний HY004 без контекста.
- DML/RETURNING/MERGE, native readback, BEGIN/commit/rollback; conversion error
  переводит T→E, 25P02 и rollback/recovery; Simple останавливается, Extended
  отбрасывает сообщения до Sync. Старые repeated/gapped/mixed `$n` отказаны.
- Target boundaries: unknown/quoted/schema/domain/TYPE OF/arrays, неполный `::`,
  `: :`/`:/*comment*/:`, неподдержанные aliases и non-binary BLOB output без подмены типа; binary BLOB проверяется в APP-07.
- Root guard: CTAS с CTE, VIEW/subquery, CHECK, stored PROCEDURE, BLOCK/local function,
  CALL/PROCEDURE/USING/UPDATE OR INSERT; native observer не видит объектов/строк.
  Прежний CAST и literal `::` сохраняются в исключённых контекстах.
- Ordinary/Q strings при on/off, comments/quoted identifiers, INT8 column/alias,
  существующий INT8 domain в обычном CAST, native1/2/3, PSQL `:variable`, AUTOTERM
  recovery, PG→native→PG на одном attachment/cache и исходные line/column.
- Необязательные части native типов: WITH TIME ZONE, BLOB SUB_TYPE/SEGMENT SIZE,
  и fallback SUB_TYPE/SEGMENT в implicit aliases; COLLATE target/expression,
  bare VARCHAR/NUMERIC defaults. Разбор новых grammar conflicts обязателен.

Регрессии: SQL/session/isql, --parameters, --strings, Extended и Simple. Settings/
BEGIN повторяются при изменении общих keywords/control paths или регрессии.
Итог — 158 shift/reduce и 11 reduce/reduce: четыре новых конфликта optional type
clauses разобраны и проверены по обеим веткам в VALIDATION; known native
crash repro и нагрузка 1С не запускаются. Исторический P3 VARCHAR FAIL не скрывается.

## Подэтап 1С: последовательные numbered inputs

`pgwire_1c_smoke.py --parameters` и `pgwire_parameters_checks.py` используют прежние
libpq/raw/native wrappers и новую синтетическую базу. Проверяются:

- PQexecParams, PQprepare/PQdescribePrepared/PQexecPrepared, omitted/zero/compatible
  OID hints, несовместимые/excess OIDs, текстовый и binary Bind нескольких типов,
  UTF8/NULL/empty, named reuse и два независимых portals с обратным порядком Execute.
- Column inference, FILENAME lookup (форма 06), INSERT с 92 уникальными номерами
  и значениями (форма 19), metadata count/order и все 92 native readback значения.
- CTE с обратным порядком DSQL обхода, unused CTE с input #1 и результатом #2,
  subquery/WHERE EXISTS, MERGE insert/update, DML RETURNING без сдвига входов.
- BEGIN/DML/COMMIT и ROLLBACK, native visibility, ошибка в T→E, 25P02 и recovery;
  Simple typed input отказан, после Parse/Bind errors команды до Sync отброшены.
- Повторы/gaps/reorder/mixed `?`, ноль/32767/32768/длинные цифры/leading zeros,
  token junk, bare `$`, отсутствие dollar quotes, comments/quoted/Q/ordinary
  strings при on/off; после каждой ошибки успешный typed Bind.
- Guard CALL input/output/named, EXECUTE PROCEDURE/BLOCK header/body, USING,
  VIEW/PROCEDURE DDL, UPDATE OR INSERT; прежние native `?` metadata/поведение
  сохраняются, USING остаётся вне wire исполнения. DDL/PSQL с literal `$1` разрешён.
- OO helper в `--strings`: native dialects 1/2/3, `?`/A$1, PG→native→PG на одном
  attachment/cache, исходные line/column при обоих строковых flags.

Baseline этапа numbered inputs — 154/11; текущий postfix-срез выше — 158/11.
Generated numbering actions защищены `!yytrial`.
Обязательны штатные SQL/Session/isql units, Extended и --settings/--begin/--strings.
C/Python negative cases меняются только в новом контракте: недостаточный контекст
остаётся HY004 даже с supplied OID; non-binary BLOB, custom types и DEALLOCATE ALL limits сохранены. Postfix casts проверяются отдельным срезом ниже.
Psycopg 3 запускается при наличии; отсутствие — SKIP без установки зависимостей.
Фактические команды, SHA и PASS/FAIL/skip — в VALIDATION. Known crash repro исключены.

## Подэтап 1С: standard_conforming_strings, редакция 2

[pgwire_1c_smoke.py --strings](tools/pgwire_1c_smoke.py) переиспользует частный
runtime/security DB/loopback и реальный libpq; checks находятся в
[pgwire_strings_checks.py](tools/pgwire_strings_checks.py). OO helper
[pgwire_strings_api.cpp](tools/pgwire_strings_api.cpp) собирается тем же harness.

Приёмка исправления сериализации расширяет тот же `--strings`: CTAS WITH DATA
для DATE/TIME/TIMESTAMP при on/off, off с числовыми escapes, сравнение с native
EDS typed literals, смешанный CTAS с проверкой inferred charset через metadata.
Процедуры с temporal и WHEN SQLSTATE сохраняют обычный UTF8, Unicode, quote/backslash,
явный `_UTF8`, Q-strings и hex strings. Проверяются source, исполнение до и после
двух ALTER из настоящего RDB$PROCEDURE_SOURCE через native EDS, результаты/типы
после reconnect. Parenthesized CTAS, CASE и nested SELECT дополняют проверку
редукций parser; generated action должен быть защищён `if (!yytrial)` и
сохранять `$$ = $1`. OO API проверяет исходные line/column после escapes
при обоих режимах и сохраняет запреты на другие charset.

| Группа | Проверяемый контракт |
|---|---|
| Значения и диагностики | Escape bytes, Unicode/surrogates, malformed UTF8/NUL, длины и metadata; общий набор с эталоном 18.6 |
| Prepare/execute/cache | ON→OFF→ON, override≠attachment, оба flags, SET только при Execute/reuse, native1/2/3, WIN1251 rejection |
| Simple | Один snapshot всего Query, escaped quote/semicolon, PSQL continuation, stop/error, data rollback, сегмент после COMMIT |
| Extended/raw | Parse snapshot в обе стороны, независимые portals, Bind bytes, несколько Execute/Flush до Sync, точный ParameterStatus order |
| История | Implicit без handle, T/E, runtime/syntax/invalid SET, Parse/Bind/duplicate name failures, повторный E, commit-in-E |
| Native controls | Quoted/doubled/UTF8/trailing-space names, replacement/ONLY, failed controls, retaining/NO AUTO UNDO, внешние точки после EXECUTE STATEMENT с вложенным SELECT |
| Сохранённый SQL/reset | Stored procedure после reconnect и native source reprepare, два attachments, RESET в I/T |
| Служебное восстановление | SessionTest: success, prepare failure, execute failure, неверный readback, отсутствующий info item; без transaction/results/leaks, original error/FATAL |
| Регрессии | Все pgwire unit executables, полный isql suite, fixed settings, scripts, Simple/Extended, transactions и native baseline |

Native TPB AUTO COMMIT и вложенный RESET совместно с историей исключены из
подтверждённого профиля. Их исключение не превращает отдельное наблюдение сбоя
в PASS. Команды, результаты, эталон и ограничения — в [VALIDATION.md](VALIDATION.md).
Внутренний SAVEPOINT через EXECUTE STATEMENT имеет отдельный исходный Debug
assertion в exe.cpp:1351, подтверждённый review также на прежних бинарниках.
Он не исправляется и не включается в обычный acceptance; вложенный SELECT
не подтверждает этот сценарий.


## Подэтап 1С: PG BEGIN без options

`pgwire_1c_smoke.py --begin` использует прежние libpq/raw clients и native isql
observer. Набор изолирован от полного baseline 1С, исходный 09-begin.sql неизменён.

- BEGIN / BEGIN WORK / BEGIN TRANSACTION / START TRANSACTION, case/comments:
  tag BEGIN, T; observer подтверждает одну transaction, READ CONSISTENCY (4),
  READ WRITE, WAIT (-1), AUTO COMMIT=0, AUTO UNDO=1.
- INSERT/UPDATE/DELETE, commit/rollback/disconnect/EOF: проверяется native visibility;
  второй PG connection фиксирует UPDATE, новый SELECT в BEGIN видит новое значение,
  native SET TRANSACTION без опций сохраняет SNAPSHOT и старое значение.
- INSERT перед BEGIN в Simple и Extended до Sync: тот же ID/handle, без скрытого
  commit; общий rollback отменяет предшествующую работу, commit делает её видимой.
- Повтор BEGIN сохраняет savepoint/ID/данные; SET TRANSACTION повторно — 25001/E,
  BEGIN в E — 25P02. Ошибка останавливает Simple script; ранний COMMIT сохраняется.
- SET string mode перед BEGIN с handle и без него: история сохраняется при promotion,
  повторе и savepoint recovery; rollback/ошибка восстанавливают прежнее значение.
- Named Parse/Bind не открывают block, Execute открывает; Sync оставляет T,
  повторное prepared исполнение после rollback успешно. Suspended portal продолжает
  чтение после BEGIN/Sync; ошибки сохраняют discard-until-Sync.
- Native1/2/3: после каждой отвергнутой формы AUTOTERM исполняет следующий SELECT;
  PSQL BEGIN/END, DDL START WITH и SNAPSHOT SET TRANSACTION работают. Неподдержанные
  options дают окончательную syntax error без частичного start.
- psql AUTOCOMMIT off и psycopg2 default/context генерируют рабочий BEGIN.
  Psycopg 3 ожидание обновлено; отсутствие клиента фиксируется отдельно, не как PASS.

Регрессии: classifier, SessionTest, isql, --settings/--strings, transaction,
Extended и Simple/libpq; изменённые psycopg2 acceptance tests запускаются существующим
ServerHarness. Parser baseline 154/11, команды/бинарники/фактические результаты —
в VALIDATION. Известные native crash repro не включаются.

## Подэтап 1С: фиксированные профили и временные startup SET

`pgwire_1c_smoke.py --settings` проверяет английский/UTF8 профиль, исходный запрос
02 от обычного пользователя без административной роли, неподдержанные
значения, кодировку данных, Simple Query stop/rollback и Extended named reuse.
Native dialects 1/2/3 с AUTOTERM должны отвергать новые SET и выполнять следующий
SQL; оба имени остаются допустимыми идентификаторами. Отдельный OO probe проверяет
не-UTF8 attachment, same-attachment dialect cache и запрет вложенного PSQL.
Целевой PASS и точные ограничения зафиксированы в [VALIDATION.md](VALIDATION.md).
Исходный неизменённый пакет 03 теперь проверяется положительно: семь SET tags,
I и standard_conforming_strings=off. [Исключение пользователя](1c/IGNORED_STARTUP_SETTINGS_TASK.md)
принимает enable_mergejoin/cpu_operator_cost/lock_timeout без эффекта.
Каждая исходная команда проверяется отдельно; `=`/TO, comments/case,
строки/идентификаторы/on/off, штатные числа со знаком и альтернативные значения
успешны. Пустой RHS, выражение, DEFAULT/LOCAL/SESSION/SHOW и неизвестное имя — 42601.
Прежний client_encoding с числовым RHS также остаётся 42601, fixed values — 22023.
Native1/2/3/AUTOTERM, non-reserved identifiers и PSQL guard включают все три имени.
Named Extended Parse/Bind/Execute и reuse проверяются с чтением всех frames:
нет новых ParameterStatus/NoticeResponse, строковый режим не меняется.
CURRENT_TRANSACTION до/после каждого SET совпадает в SET TRANSACTION → SET →
COMMIT/ROLLBACK, при on/off; E даёт 25P02, после ROLLBACK команды снова успешны.
Это приёмка синтаксиса, не optimizer/lock effects или запуска 1С.
Целевые проверки этапа: полный make (parser baseline 154/11), classifier,
SessionTest, isql и --settings; широкий --strings, lock waits и benchmarks не требуются.
Проверка прав на `lc_messages` отложена; 42501 для этой настройки сейчас не ожидается.

Тот же --settings проверяет escape_string_warning=off/client_min_messages=error:
отдельные исходные фрагменты и вместе, TO/`=`, quoted/bare/case и SET tags,
22023 на полные неподдержанные значения, stop/rollback writes, I/T/E и восстановление
standard_conforming_strings, COMMIT/ROLLBACK/reset. Named Parse/Bind плохих
значений проходят до 22023 на Execute; хорошие statements исполняются повторно.
Raw-проверка читает каждый frame через message(), без helper-фильтра N/S:
newline и неизвестный escape успешны без N, malformed Unicode даёт E/22025,
division by zero — E/22012, затем Z/I. Проверяются native1/2/3 AUTOTERM,
unquoted identifiers, прежний SET BIND и запрет PG SET внутри PSQL.
В исходной приёмке fixed diagnostics также прошли --strings, classifier,
SessionTest и полный isql suite; исторические результаты сохранены в VALIDATION.


## Подэтап 1С: последовательные скрипты — проверен 10 ноября 2026 года

Решение пользователя от 9 ноября 2026 года: отказаться пока от предварительной
синтаксической проверки всего Simple Query и останавливать скрипт при первой
окончательной ошибке. [Архитектура и ограничение](1c/README.md).
[pgwire_script_smoke.py](tools/pgwire_script_smoke.py) прошёл восемь групп через
raw TCP и настоящую libpq; изменённый pytest-сценарий прошёл через psycopg2.
Регрессии Simple Query, транзакций, Extended Query и native прошли 12/14/16/9
групп/проверок. Точные результаты и границы покрытия — в [VALIDATION.md](VALIDATION.md).
Extended Parse остаётся однокомандным.

| Проверка | Критерий приёмки |
|---|---|
| Несколько SELECT через PQsendQuery/PQgetResult и raw TCP | Все строки/OID/tags, один ReadyForQuery; один PQexec недостаточен для проверки всех результатов |
| CREATE TABLE; INSERT; SELECT | Последовательный prepare видит результат предыдущего DDL |
| INSERT A; COMMIT; INSERT B; syntax/runtime error; INSERT C | A сохранён, B откатан в implicit transaction, C не исполнен; подтверждение через native observer. Поздняя syntax error — зафиксированное отличие от PostgreSQL |
| INSERT A; ROLLBACK; INSERT B | A откатан, B зафиксирован в конце Query |
| Два INSERT без явной границы / ошибка после первого | Один implicit commit в конце / rollback текущего сегмента и остановка хвоста |
| SET TRANSACTION; INSERT; ошибка; ROLLBACK | Оставшийся ROLLBACK не исполняется, итог E; отдельный Query с ROLLBACK; SELECT восстанавливает работу |
| Native session SET перед поздней ошибкой | Проверить настоящий эффект; rollback не отменяет native attachment setting. Не выдавать это за PG transactional SET |
| Строки, Q-strings и comments с `;`, EXECUTE BLOCK и определения PROCEDURE с внутренними `;` | Исходные диапазоны без trim/rewrite; продолжение только при настоящем isc_command_end_err2; строки/PSQL не исполняются фрагментами |
| Нет конечного `;`, comments до EOF, незаконченная строка/комментарий после COMMIT | Корректный хвост выполняется; ошибочный хвост останавливает Query, уже выполненный COMMIT сохраняется |
| Ошибка финального commit / ошибка после ранее успешного COMMIT | Нет финального успешного CommandComplete при commit failure; последующая обычная ошибка не становится fatal только из-за раннего COMMIT |
| Однокомандные Simple/Extended, native isql/AUTOTERM, cleanup | Прежние сценарии и diagnostics сохранены; ресурсы освобождены, нет скрытых application transactions при пробных prepare |

Наличие опечатки в хвосте до первого исполнения не проверяется: это принятое
ограничение этапа. Приёмка совместимых скриптов не означает поддержку стартовых
PostgreSQL SET или фактического запуска 1С.

## Этапы и обязательный минимум

| Этап | Принимаемый результат |
|---|---|
| M0 | Динамический PgWire внутри firebird, authentication, вычисляемый SELECT, error/recovery, native coexistence и cleanup |
| M1 | Simple Query с совместимым SQL, ограниченный plugin descriptor + существующие metadata, basic codecs, native transaction controls и lifecycle |
| M2 | Extended Query, prepared statements/portals, native placeholders и расширенные codecs |
| M3 | TLS/удалённый TCP/cancellation и проверенный профиль приложения с явными SQL dependencies |

RETAINING, произвольная нумерация `$n` и SQL, который parser пока не поддерживает, не входят в успешный profile. Native retaining проверяется через `COMMIT/ROLLBACK [WORK] RETAIN [SNAPSHOT]`. Прежние M0/P3 результаты и SELECT-only guards являются историей в VALIDATION; текущие RETURNING/blocks проверяются положительно при поддержанных metadata. Удалённый native metadata executable не является зависимостью harness.

Минимальные серверные gates сохраняются независимо от внутреннего способа classification:

| ID | Проверка и критерий |
|---|---|
| M0-01 | Native baseline собирается и обслуживает TCP при пустых WireProtocolPlugins/Ports |
| M0-02 | PgWire загружен отдельной библиотекой в firebird; review и connect trace исключают backend network hop |
| M0-03 | Authentication сохраняет CURRENT_USER и privileges; bad password не даёт рабочего attachment |
| M0-04 | C libpq PQexec и psycopg2 autocommit без connection context возвращают вычисляемый 42/OID 23 |
| M0-05 | Настоящий parser error и следующий успешный query на том же connection; BEGIN не эмулируется |
| M0-06 | Отсутствующий module/занятый PG port не отключает native listener |
| M0-07 | EOF/incomplete startup/shutdown освобождают attachments/sessions/workers до unload |
| M0-08 | Prepare использует исходный SQL; descriptor и штатные input/output/flags проверяются до execution. Unsupported capabilities не дают частичных эффектов, supported RETURNING работает |

## P6/M1 — целевой Linux/Super принят с известными native ограничениями

[pgwire_server_smoke.py](tools/pgwire_server_smoke.py) прошёл семь групп на отдельной копии Linux/Super runtime и БД: pytest 58 passed, 16 warmup + 24 measured sessions, FD count 15 и диагностический thread count 11 во всех измеряемых batches. Оба сервера завершились exit 0; регрессии и точные команды — в VALIDATION. Новых сценариев пауз и срабатывания таймаутов нет; клиентские пределы ожидания ограничивают только harness.

Отдельный [pgwire_attach_shutdown_smoke.py](tools/pgwire_attach_shutdown_smoke.py) воспроизвёл SIGABRT при shutdown во время реального ON CONNECT: assertion alloc.cpp:2075 (!child), путь internalAttach → unwindAttach → shutdownDatabase → Database destructor. Native-only run с пустыми wire lists воспроизводит тот же сбой. Этот native дефект исключён из целевой P6/M1 приёмки; в P7 он не исправлялся и повторно не проверялся. Reproducer сохраняет настоящий FAIL и ненулевой exit, без xfail/skip или преобразования результата в PASS.

| ID | Проверка и критерий |
|---|---|
| P6-01 | Мигрированный pytest-набор: 58 cases; autocommit/native controls, positive RETURNING, ошибки, права и native roundtrip. Live PASS записан отдельно; skip/xfail не принимаются |
| P6-02 | 40 sessions: 16 warmup и 24 измеряемых. После query/disconnect нет session-owned statements/transactions/attachments; FD count не растёт относительно прогретого baseline; общее число threads — диагностика native worker cache, wire workers проверяются при shutdown |
| P6-03 | MON$STATEMENTS отменяет активный SQL: 57014/I либо 57014/E, recovery и native observer подтверждают rollback |
| P6-04 | MON$ATTACHMENTS завершает session из I/T: следующий Query получает FATAL 57P01/close; незавершённые изменения откатываются |
| P6-05 | gfix shutdown базы: FATAL 57P01 на следующем Query, rollback; после online работает новое подключение |
| P6-06 | Server shutdown при lock wait, password exchange и partial startup: engine cancellation, EOF, exit 0, rollback и завершение workers |
| P6-07 | SuperClassic: native работает, PgWire не загружен, listener отсутствует и есть диагностика неподдержанного режима |

Результаты повторных конфигурационных, ABI, codec и host/startup/Simple Query/transaction/lifecycle/native проверок записаны в VALIDATION. PASS ограничивается фактически запущенными cases. Windows, работа PgWire в SuperClassic/Classic, auth-plugin и close/free fault injection не входят в этот результат; READ CONSISTENCY + AUTO COMMIT остаётся известным native исключением. Клиентский EOF во время engine call не имеет отдельного watcher: немедленная отмена по disconnect не обещается, server shutdown отменяет операцию явно.

## Исторический P5 — целевая Linux/Super приёмка PASS с native ограничением

Финальный transaction harness прошёл 14 групп без skips: I/T/E, raw ReadyForQuery/PQtransactionStatus, visibility через native observer, savepoint/retaining recovery, session SET/RESET, 100 ms DML timeout в I/T, error precedence, SNAPSHOT AUTO COMMIT, NO AUTO UNDO, disconnect и реальные commit/reset errors, C libpq и connect trace. Backend connects — 0; серверы завершились с exit 0. Full build и четыре unit executable (487 classifier checks), Simple Query 12, lifecycle 7, startup 52 и native 9 прошли отдельно. Evidence — в VALIDATION. Этот исторический результат не означает PASS всех вариантов T01–T14 и остальной матрицы; целевой M1 позднее принят в P6 с указанными исключениями.

Текущий [pgwire_transaction_smoke.py](tools/pgwire_transaction_smoke.py) проверяет AUTO COMMIT DML с SNAPSHOT и отдельно NO AUTO UNDO. Native SNAPSHOT baseline положительный. Дефект AUTO COMMIT с READ COMMITTED READ CONSISTENCY воспроизводит assertion `Savepoint.cpp:833` в restart path через embedded isql без PgWire/server/triggers; этот profile не входит в подтверждённую совместимость и требует повторного теста после native fix. Evidence независимых native repro и точная команда прогона записываются в VALIDATION; core/SQL options для обхода не меняются.

## Исторический P4 после переноса классификации — Linux/Super loopback PASS

P4-ревизия [pgwire_simple_query_smoke.py](tools/pgwire_simple_query_smoke.py) прошла 12 групп, включая raw TCP, C libpq, psycopg2 autocommit, lexical boundary, native AUTOTERM и trace. [pgwire_lifecycle_smoke.py](tools/pgwire_lifecycle_smoke.py) прошёл 7 групп, startup/native regressions — 52/9 checks. Full make и четыре unit executable прошли; SQL classifier — 284 assertions. Версии: C headers/runtime libpq 14.24, psycopg2 2.9.11/libpq 17.6. Backend network connects — 0, все серверы завершились с exit 0. Evidence и ограничения — в [VALIDATION.md](VALIDATION.md). Прежние negative expectations controls в P4-08/10 заменяются P5 positive/state checks; остальная regression сохраняется.

| ID | Проверка и критерий |
|---|---|
| P4-01 | Ограниченный lexer: comments, mixed case, CTE, quoted keyword identifiers, literals с semicolon/BEGIN/$n; исходные SQL bytes передаются prepare неизменёнными |
| P4-02 | SELECT/DML/MERGE/upsert/RETURNING/EXECUTE BLOCK/PROCEDURE: command tag от plugin descriptor, cursor flag и output metadata выбирают API; actual affected records не заменяются rowcount |
| P4-03 | CREATE/ALTER/DROP/RECREATE/CREATE OR ALTER, GLOBAL TEMPORARY TABLE, UNIQUE DESCENDING INDEX; PACKAGE с именем BODY отличается от PACKAGE BODY; правильные tags и реальные engine effects |
| P4-04 | EMPTY/whitespace/closed comments отправляются в prepare; только engine syntax error плюс tokenless признак даёт EmptyQueryResponse. Незаконченный SQL/comment/string, vertical tab и отдельный `;` сохраняют 42601 |
| P4-05 | Native diagnostics и isql AUTOTERM работают без новых marker/getInfo/AST extensions; malformed SQL и batch errors имеют приоритет над unsupported classification |
| P4-06 | Integer/boolean/CHAR/VARCHAR/exact numeric/NULL: OID/alias/UTF8/trailing spaces/INT64_MIN/scale/subtype; table OID/attribute 0, typmod-1 |
| P4-07 | DML 0/1/many, singleton/cursor RETURNING, procedures/blocks; native observer подтверждает commit и rollback |
| P4-08 | Unsupported input/output metadata, controls и UNKNOWN USING получают 0A000 после prepare до execution; sequence guard не меняется, ALTER SESSION RESET не сбрасывает USER_SESSION context |
| P4-09 | Parser 42601, relation 42P01, permission 42501, unique 23505, FK 23503, CHECK 23514, coarse NOT NULL/domain 23000. Реальный commit trigger даёт FATAL/EOF без CommandComplete/Ready, native observer видит rollback |
| P4-10 | C libpq/psycopg2 autocommit, native coexistence, startup/lifecycle/trace; P4 остаётся I-only, explicit controls принимаются отдельно в P5 |

Неподдержанный USING wrapper проверяется после engine prepare: валидный `USING IN AUTONOMOUS TRANSACTION DO ...` получает 0A000. USING без обязательной clause сохраняет native semantic 42000; malformed SQL сохраняет syntax 42601. Эти ошибки не маскируются capability refusal; GEN_ID fixture исключает исполнение отвергнутой команды. После добавления скриптов negative batch fixtures содержат ошибку в первой команде; успешный префикс и транзакционные границы проверяются отдельным script harness. Отдельно проверяются следующие успешные queries после recoverable ошибок. Ошибки close/free и engine timeout отмечаются по фактическому evidence.

## P2 — историческая startup приёмка и regression

Целевой Linux/Super P2 startup harness прошёл 52 проверки с libpq 14.24, см. [VALIDATION.md](VALIDATION.md). В том срезе все корректные Query получали 0A000 без engine prepare. P3 заменяет эту заглушку SELECT execution; startup/authentication/cleanup остаются regression checks, query expectations должны соответствовать текущему этапу.

| ID | Проверка и критерий |
|---|---|
| P2-01 | Production PgWire загружен отдельной библиотекой в Linux/Super; native продолжает выполнять SQL. POSIX/CMake definitions не включают PG objects в daemon/Remote_Common; Windows runtime не заявляется |
| P2-02 | Raw startup и реальный libpq/psycopg2 connect: AuthenticationOk только после attach, ожидаемые ParameterStatus/Ready I, без фиктивного BackendKeyData. Исторический P2 давал всем Query 0A000/I; текущие query/recovery expectations проверяются в P4-01–10 |
| P2-03 | Правильные credentials, неверный пароль/unknown user 28P01, unknown/disallowed alias 3D000, RemoteAccess=false и запрещённый Srp256 policy; native observer подтверждает реальные раздельные identities и peer |
| P2-04 | ON CONNECT trigger отклоняет вход даже с правильным паролем: ErrorResponse до AuthenticationOk, attachment освобождён; следующий разрешённый connect работает |
| P2-05 | Неполные/coalesced frames, SSL/GSS N, duplicate/unknown startup keys, неправильная version, lengths/NUL/UTF8, границы 16 KiB startup / 1 MiB typed frame / 4096 bytes Password с NUL; bounds проверены до payload allocation |
| P2-06 | User обязателен; absent database=user проходит лишь при разрешённом alias, explicit empty database отвергается. Empty DatabaseAliases запрещает attachment; клиентский path/URI/config не меняет provider route |
| P2-07 | Unit golden bytes, short writes/reads и сохранение partial progress; прежние transport/startup timeout expectations удалены |
| P2-08 | EOF, Terminate, failed auth, idle и incomplete startup при shutdown: attachments/FDs освобождены, module жив до возврата callbacks. MON$REMOTE_PROTOCOL=TCPv4, MON$REMOTE_VERSION=endpoint alias; password отсутствует в diagnostics |

Auth helper отдельно ограничен 16 rounds и 64 KiB на payload. Проверяются UTF8 и host bounds alias/user/application_name 255 bytes, нормализованный Srp256 login 63 bytes; Password codec limit включает NUL. Дополнительных сетевых/auth timers нет. Такой вызов проверяется с provider shutdown и fault-injection отдельно; PASS idle transport не доказывает preemption auth plugin. Наряду с live harness выполнить code review engine-only provider route и наблюдение server connect(), чтобы исключить дополнительный backend hop.

## Fixtures и свидетельства

- Disposable БД dialect 3/UTF8, alias `pgwire_test`, локальная security database и проверенный Srp256 profile. Начальный режим — Linux/Super, PgWire `127.0.0.1:15432`.
- Native observer — отдельный настоящий TCP-клиент к той же БД, например `inet://127.0.0.1:3050/pgwire_test`. Его исходящие sockets отделяются от sockets сервера.
- Пользователь `PGWIRE_TEST` создаёт собственные объекты; `PGWIRE_LIMITED` не имеет доступа к ним. Отдельный административный fixture нужен для monitoring shutdown/timeout cases.
- Alias allowlist, `RemoteAccess=false`, неподходящий auth profile и missing plugins проверяются отдельными server configs. Тесты не меняют рабочий сервер пользователя.
- Для cross-visibility observer начинает новую транзакцию; DDL fixtures коммитятся до дальнейшего DML. Cleanup удаляет только объекты текущего теста.
- Лимиты connections/workers/frames/queues/buffers, независимые native statement/lock settings и общий shutdown deadline записываются в отчёте.
- Credentials передаются через отдельное окружение; DSN, command output, status vectors и трассы не должны раскрывать пароль.

Для каждого этапа нужны разные уровни evidence: unit tests codec/state/metadata; прямой libpq и Python client; native observer; process/build/provider/lifecycle checks. Совпадение `MON$SERVER_PID` само по себе не исключает gateway.

## A. Plugin host, конфигурация и сборка

| ID | Сценарий | Критерий |
|---|---|---|
| A01 | Сборка/установка/загрузка | Generic host входит в server target; PostgreSQL-specific objects находятся в отдельной plugin library, а не в daemon или Remote_Common; экспортирован штатный entrypoint |
| A02 | Пустые default lists | Ни additional listener, ни его workers/attachments не появляются; native regression проходит |
| A03 | Один тестовый module/factory, два configured aliases `StubA`, `StubB` с разными Config/ответами | Созданы отдельные instances; alias точно соответствует своему positional port/config, mutable state изолирован. Две библиотеки и второй СУБД-протокол не требуются |
| A04 | `StubA, MissingPlugin, StubB` и три порта; StubA/StubB используют один module/factory | Средний endpoint диагностирован как недоступный; StubB остаётся на третьем порту со своей Config. Первая и родная службы работают; manager не сдвигает позиции и не переиспользует ошибочную instance |
| A05 | Разная длина/один пустой список/пустая позиция/trailing comma | Вся некорректная дополнительная конфигурация отклонена до загрузки модулей; native работает |
| A06 | Порт 0, отрицательный, нечисловой, overflow, >65535, дубликат/конфликт native | Предсказуемый отказ без автоматической подмены порта; native listener не закрывается |
| A07 | Missing/bad module, wrong factory/type, exception в factory, bind failure | Ошибка содержит plugin/port/reason без секретов; частичные resources освобождены; другие корректные endpoints доступны |
| A08 | `plugins.conf` Module/RegisterName | Файл с отличающимся именем загружается по логическому имени; неверный RegisterName даёт явный отказ |
| A09 | Startup integration | Один listener для каждой настроенной пары alias/port в server process, без повторного bind на других startup paths; нет второго `SRVR_multi_thread()`, native PACKET/XDR или startup в client library |
| A10 | Provider/network route | Review actual call path и наблюдение исходящих server `connect()` при auth/SQL: engine-only, без Remote/Loopback/native TCP/Unix backend |
| A11 | Super, SuperClassic, Classic | Super тестируется явно; SuperClassic не считается автоматически покрытым Super. Неподдержанные modes диагностируются и сохраняют native |
| A12 | Другие build paths | CLOOP bindings согласованы; CMake/Windows имеют отдельные build results. Проектный переносимый ABI не выдаётся за проверенную DLL |
| A13 | Разные bind/TLS/protocol settings через Config двух aliases | Каждый instance получает свою секцию Config. Сохраняются только два positional списка plugins/ports; host проверяет bind/TLS policy. До M3 remote/TLS настройки не включают необъявленную capability; с M3 проверяются разные сертификаты/policies без переноса настроек между endpoints |
| A14 | Штатный getPluginConfig: module `.conf` fallback для первого alias, named Config для второго | Оба endpoint получают ожидаемые host settings и различимые ответы plugin. Отсутствие named Config само по себе не является ошибкой; NULL getter отклоняет endpoint. Заданные host settings проверяются на тип, диапазон и повтор независимо от источника |

Strict positional validation проверяется отдельно от PluginManager iterator: optional module skipping не должен менять mapping. Конфигурационные ошибки не проверяют изоляцию от segfault в доверенном плагине: весь код работает в одном процессе.

Для A04 дополнительно проверяется отказ implicit Config::getPlugins(TYPE_WIRE_PROTOCOL) и direct loader name с запятой до module entrypoint. Для native-conflict части A06 нужны startup cases с явным RemoteServicePort, override `-p`, RemoteServiceName, отключённым TCP и пересекающимися bind/dual-stack адресами; конфликтный endpoint не должен вызывать module entrypoint, native продолжает работать. Повторный IPv4 loopback smoke с override `-p` и штатным getPluginConfig записан в VALIDATION; остальные варианты требуют отдельных запусков.

Конфигурация подготавливается до запуска через стандартные средства Firebird. Для разных aliases рекомендуется named Config. Приёмка host не вводит отдельную проверку структуры Plugin/Config blocks или новых правил разрешения конфигурации; она проверяет настройки, возвращённые существующим getter, их defaults и совместную работу endpoint/native.

## S. Authentication и identity

| ID | Сценарий | Критерий |
|---|---|---|
| S01 | Правильный/неправильный пароль, неизвестный пользователь | Правильный вход либо startup `28P01`; AuthenticationOk только после attach; нет рабочего user attachment после отказа |
| S02 | Auth rounds и ошибочные plugin statuses | Не более 16 rounds и 64 KiB на payload, cleanup, без trusted/OS/service-account fallback |
| S03 | Srp256 запрещён per-database policy, security DB недоступна или не имеет Engine14 в Providers | Диагностируемый отказ; native auth configuration не подменяется |
| S04 | CURRENT_USER, concurrent users, denied SELECT | Реальные разные identities; `42501` без GRANT; credentials/auth blocks не переходят между sessions |
| S05 | Alias allowlist, неизвестный alias, paths/URIs/options | Только server-authorized local database; неизвестный alias `3D000`; клиент не внедряет DPB/config/trusted identity |
| S06 | RemoteAccess=false и peer metadata | Отказ удалённому клиенту; реальный peer, MON$REMOTE_PROTOCOL=TCPv4 и MON$REMOTE_VERSION=endpoint alias не маскируют TCP origin как embedded |
| S07 | Native auth и затронутые EDS paths | Native regression обязательна; при выделении helper из EDS сохранить его positive/negative/precondition cases |

Startup SQLSTATE проверять raw ErrorResponse field `C`: `psycopg2.connect()` может бросить OperationalError без `pgcode`. Внутренние security attachments/caches учитывать отдельно от session-owned application attachments.

## W. Framing и SQL boundary

| ID | Сценарий | Критерий |
|---|---|---|
| W01 | Startup без tag, typed messages, fragmentation по каждой границе, coalesced frames | Точные lengths/network order, каждый frame обработан один раз, transport read size не ограничивает размер frame |
| W02 | Partial write, slow reader, EOF в header/payload/password | Ограниченная память, отсутствие ложного успеха, cleanup только нужной session |
| W03 | Invalid/overflow/oversized length, NUL/хвост, UTF8 | Отказ до опасной allocation или исполнения; SQL не усекается |
| W04 | Startup 3.0/неподдержанные version/options, SSLRequest/GSSENCRequest | Реальный профиль: до TLS ровно `N`; неизвестные обещания не подтверждены успешным startup |
| W05 | Независимые golden bytes результатов/errors | OID, lengths, NULL=-1, empty=0, required ErrorResponse fields, ReadyForQuery и tags; не только собственный encode→decode |
| W06 | Native keepalive/crypt/compression и parallel sessions | PG stream не получает native bytes; framing/auth/SQL state разных sessions независимы |
| W07 | Байты SQL на входе и в parser | Code review неизменной передачи bytes/length исходных диапазонов команд и TCP fixtures для comments, quotes, semicolons, whitespace и UTF8; wire frontend не нормализует и не переписывает SQL |
| W08 | EMPTY и malformed SQL | Исходный SQL проходит engine prepare; только tokenless closed-comments/whitespace + syntax 42601 даёт EMPTY. Незаконченные tokens, vertical tab и отдельный `;` остаются errors; non-syntax errors сохраняются |
| W09 | `BEGIN READ ONLY`, `START TRANSACTION ISOLATION LEVEL SERIALIZABLE`, `COMMIT RETAINING`, `$1foo`, `::regnamespace` и похожие tokens внутри literals/comments | Engine syntax errors в неподдержанных позициях; внутри literals/comments смысл определяет parser. Ошибка в T переводит в E; в E syntax сохраняет 42601. Native RETAIN [SNAPSHOT] проверяется положительно, frontend replacement отсутствует |
| W10 | Несколько statements в одном Query/Parse | Simple Query последовательно исполняет команды и прекращает остаток при первой окончательной ошибке по таблице выше. Нет предварительной проверки всего Query: ранний COMMIT сохраняется и при поздней syntax error. Extended Parse отвергает несколько statements |
| W11 | Native callers и isql -autoterm | Штатные parser diagnostics, continuation и trace не меняются; PgWire не добавляет public EMPTY marker или metadata API |

W07 требует code review неизменной передачи SQL и TCP fixtures с comments/quotes/CTE/semicolons. Ограниченный plugin lexer проверяется отдельно: он задаёт descriptor/EMPTY, а engine по-прежнему валидирует каждый SQL. Unsupported wire messages получают protocol/capability error; ожидания уточняются при Extended/TLS/cancel.

## Q/T. DSQL metadata, выполнение и транзакции

Используются штатные getType/getFlags/input/output/affected records и plugin-local command/control descriptor. Новые public engine metadata, parser observer и pre-prepare analysis API не требуются. P5 E-state использует обычный prepare и реальный transaction handle; ниже задана матрица ожидаемого поведения. Точный принятый P6/M1 profile и исключения указаны выше, результаты отдельных cases — в VALIDATION.

| ID | Сценарий | Критерий |
|---|---|---|
| Q01 | Вычисляемые SELECT, 0/1/many rows, aliases | Реальные значения, правильная description и SELECT row count |
| Q02 | CREATE/DROP/ALTER и другие доступные DDL | Tag задаётся ограниченным plugin descriptor после engine prepare; изменение видно native observer после commit |
| Q03 | INSERT VALUES/SELECT, UPDATE, DELETE, MERGE, UPDATE OR INSERT с/без RETURNING | Реальные rows и affected counts, tag исходной операции; cursor не превращает DML в SELECT, coarse INSERT type не маскирует MERGE |
| Q04 | Supported SQL с unsupported input/output capability | Отказ по metadata до side effects; не blanket rejection синтаксической формы RETURNING |
| Q05 | SET TRANSACTION, COMMIT/ROLLBACK RETAIN [SNAPSHOT], SAVEPOINT и session operations | Plugin-local descriptor различает actions после engine prepare и сверяется с legacy type. Tags: BEGIN/COMMIT/ROLLBACK/SAVEPOINT/RELEASE/SET/RESET; фактический effect и transaction handle контролируются |
| Q06 | Native/PG cross-visibility в обе стороны | Одна база, штатные права и commit/rollback; native клиент не участвует в PG execution path |
| Q07 | Prepare/fetch/execute/commit errors | Нет ложного CommandComplete; исходная ошибка не затёрта cleanup; uncertain transaction state завершает connection |
| T01 | Обычный implicit Query | Prepare с NULL, затем READ WRITE / READ COMMITTED READ CONSISTENCY / WAIT TPB без заданного плагином lock timeout. Commit до CommandComplete, rollback после ошибки, state I; retaining не подменяет завершение |
| T02 | Native SET TRANSACTION из I, native COMMIT/ROLLBACK | Реальная transaction, T/I, isolation/read-only и cross-session visibility. Повторный SET TRANSACTION в explicit T — 25001/E с сохранением transaction |
| T03 | Ошибка в explicit transaction | Native handle сохраняется, state E; успешно подготовленные обычный SQL/SAVEPOINT/RELEASE/COMMIT RETAIN получают 25P02 без execution и открытия новой transaction. Ошибки самого prepare имеют precedence из T11 |
| T04 | Полный COMMIT/ROLLBACK из E | COMMIT выполняет rollback и возвращает ROLLBACK/I; ROLLBACK также завершает transaction; ещё незафиксированные изменения не сохранены; native AUTO COMMIT проверяется отдельно T09 |
| T05 | Native COMMIT/ROLLBACK [WORK] RETAIN [SNAPSHOT] в T/E/I | В T retaining остаётся T с реальным commit/rollback; E + ROLLBACK RETAIN восстанавливает T; E + COMMIT RETAIN даёт 25P02/E; в I RETAIN — 25000/I. Lifecycle savepoints сверяется с native API |
| T06 | SAVEPOINT → изменение → ошибка → ROLLBACK TO → продолжение; RELEASE [ONLY] | Rollback-to возвращает T и сохраняет прежние изменения; неверный savepoint даёт error/E. Имена в quotes и имена RETAIN/WORK не путаются с controls. RELEASE SAVEPOINT name [ONLY] имеет native effect; все savepoint actions в I дают 25000/I |
| T07 | Полный COMMIT/ROLLBACK в I | Подготовленный native control и локальный descriptor дают no-op/I, optional notice |
| T08 | Disconnect с незавершённой transaction | Rollback и освобождение lock; native observer подтверждает отсутствие uncommitted changes |
| T09 | Native SNAPSHOT AUTO COMMIT / NO AUTO UNDO | Lexer не сканирует опции; SNAPSHOT AUTO COMMIT проверяется native/PG visibility и сохранением прежних commits после rollback. NO AUTO UNDO/savepoint recovery проверяется независимо. AUTO COMMIT + READ COMMITTED READ CONSISTENCY имеет отдельный native Savepoint assertion и не входит в заявленное покрытие |
| T10 | ALTER SESSION RESET в I/T/E, включая ошибку reset | Проверяются сброс USER_SESSION/settings, rollback и новая native transaction при возможном прежнем interface pointer. В T сохраняется T, в I — I, в E — 25P02 без reset. P7 RESET инвалидирует все statements/portals; cache/lifetime cases заданы отдельно в E11. История имён и bool snapshots standard_conforming_strings очищается; native undo не дублируется. Invalid attachment/handle после reset требует FATAL/close |
| T11 | Prepare в E: actual missing relation, syntax, permission, cancel/timeout, fatal и unknown error | Только native isc_dsql_relation_err с итоговым nonfatal 42P01 даёт 25P02; explicit SQLSTATE 42P01 без причины не входит в whitelist. Syntax 42601 и permission 42501 сохраняются. Отмена/timeout, shutdown/invalid handle, resource/I/O и unknown errors не маскируются. Проверяются full-vector priority и отсутствие execution |
| T12 | Число current application transactions и phase boundaries | QueryExecutor владеет максимум одним handle; prepare в I получает NULL, в T/E — current и видит uncommitted DDL. Нет cached statement transaction. Engine system/security/autonomous transactions отделяются при подсчёте; native и другие plugins этим правилом не ограничиваются |
| T13 | Native session SET whitelist в I/T/E | ROLE/TRUSTED ROLE, DEBUG OPTION, DECFLOAT ROUND/TRAPS, idle/statement timeout, TIME ZONE, BIND, OPTIMIZE, SEARCH_PATH: settings реально меняются и выдаётся SET, I/T сохраняются; в E после successful prepare — 25P02 без effect. Native configuration/session timeout ограничивает DML без дополнительного лимита плагина; проверить 100 ms timeout в I/T, реальный interrupt и recovery. Не заявлять hard deadline prepare/DDL/controls |
| T14 | EMPTY в I/T/E | EmptyQueryResponse сохраняет прежнее состояние и transaction; изменения/savepoints не теряются. Отдельный semicolon и unfinished SQL/comment остаются ошибками, не EMPTY |

Заявленные transaction characteristics и session operations меняют реальное engine state. Неподдержанные descriptor/API/codecs получают отказ после prepare до execution. Legacy getType/getFlags и native diagnostics не изменяются; control classification покрывается отдельными tests вместе с фактическими handles/effects.

T11 не утверждает, что prepare лишён compile/cache/lock/trace/external-initialization effects: проверяется отсутствие исполнения подготовленного SQL и освобождение ресурсов. Редкие resource/I/O failures воспроизводятся контролируемой инъекцией ошибок, без фактического исчерпания ресурсов рабочего сервера.

## D. Типы, строки и диагностика

P8 прошёл целевую Linux/Super приёмку: восемь групп [codec harness](tools/pgwire_codec_smoke.py), восемь unit executables и регрессии. Результаты — в [VALIDATION.md](VALIDATION.md). Новые типы проверяются в text/binary Bind/result и Simple Query/RETURNING: FLOAT/DOUBLE, numeric через INT128, finite DATE/TIME/TIMESTAMP без TZ. Приёмка P9/M2 завершена по профилю ниже; следующий этап — P10. Наличие строки в матрице само по себе не является PASS.

| ID | Сценарий | Критерий |
|---|---|---|
| D01 | M1 integers, BOOLEAN, CHAR/VARCHAR, exact numeric, NULL | Верные OID/Python types, bounds, INT64_MIN, scale=0 и fractional numeric без float intermediate |
| D02 | UTF8 CHAR, varying fetch metadata, trailing spaces | Значения сверяются с native fetch через SQL_VARYING; нет byte-padding повреждения или rtrim логических пробелов; проверяются ASCII/многобайтные значения, пустое и полностью пробельное CHAR |
| D03 | Quoted aliases, NULL/empty, апостроф/backslash/LF/TAB/semicolon | Metadata и значения не теряются; U+0000 даёт 22021 вместо truncation |
| D04 | P8 FLOAT/DOUBLE, temporal без TZ, INT128/NUMERIC(38), aggregates | OID 700/701/1082/1083/1114/1700 из metadata; full signed INT128 min/max и scale −38, NUMERIC(38); float NaN/Infinity поддержаны, numeric specials22003; finite temporal0001..9999 и exact100 µs, без потери precision |
| D05 | P8 text/binary input/output | Независимые base-10000/IEEE/date/time/timestamp fixtures, endian/epoch/units/lengths; numeric malformed22P03/overflow/inexact22003; temporal strictISO22007/range/precision22008, binary length22P03; integer_datetimes=on без TimeZone; native buffer не копируется напрямую в PG binary |
| D06 | Unsupported type/format или malformed Bind | Capability error до исполнения; действительно неподдержанные types не превращаются в text/NULL |
| D07 | Full ISC status vector с wrapper и nested causes | SQLSTATE выбирается по полной структуре, включая update-conflict/deadlock/constraint causes; не по локализованному тексту и не только первому коду |
| D08 | Syntax, unknown table, unique, permission, login | Проверены 42601/42P01/23505/42501/28P01 и fallback; messages сохраняют смысл и не раскрывают credentials |
| D09 | Native observer удаляет MON$ATTACHMENTS для PG session; отдельно shutdown базы | FATAL 57P01 и закрытие PG connection; target не выполняет последующий SQL |
| D10 | Native observer отменяет MON$STATEMENTS или срабатывает statement timeout | 57014 в запросе, корректные I/E, освобождение statement/cursor и возможность предусмотренного recovery |
| D11 | Engine idle timeout | Attachment закрыт с rollback; следующий Query получает FATAL 57P05/close, без повторного attach и периодических engine calls, продлевающих idle timer |

Для D09/D11 delivery contract: active operation получает terminal error при возврате из engine; для idle socket обязательна доставка на следующем запросе, если writable channel сохраняется. Harness после administrative action/timeout посылает запрос и проверяет raw ErrorResponse и close; поддержанная eager notification может прийти раньше с тем же кодом. Транспортный обрыв при уже закрытом socket фиксируется отдельно и не подменяет проверку mapping на рабочем канале. Если engine callback позволяет иной контракт, его сначала зафиксировать в CONTEXT и согласовать тест, а не обещать unsolicited сообщения без реализации.

## E. Extended Query с совместимым SQL

**Целевая Linux/Super приёмка P7 — PASS: 16 групп; evidence в [VALIDATION.md](VALIDATION.md).** В принятом P7 были basic text input/output и binary integer/BOOLEAN/строки; текущий P8 реализует binary NUMERIC, FLOAT/temporal/INT128, прошедшие отдельную целевую приёмку. Наличие строк ниже не означает PASS. P7 использовал C libpq и raw-wire, с отрицательными `$1`/BEGIN cases; Psycopg 3 тогда не проверялся. Ограниченный live profile P9 приведён ниже.

Пять codec unit executables прошли, включая 149 input и 487 SQL-classifier checks; повторные семь server groups/58 pytest cases и девять native baseline checks прошли. Suspended-portal shutdown потребовал узкого native release_attachment fix для нескольких requests одного Statement; падение подтверждено также без PgWire. [Native prepared-shutdown harness](tools/pgwire_native_prepared_shutdown_smoke.py) после исправления прошёл три проверки, обе остановки — exit 0. Grammar/public metadata/CLOOP не менялись. Отдельный ON CONNECT !child остаётся неисправленным и не перепроверялся. Остальные команды и evidence — в VALIDATION; целевой M2 принят в P9 после P8.

Прямой C libpq harness фиксирует `PQlibVersion`, compile headers, OS и SHA. Он использует `PQexecParams`/`PQprepare` с нулём параметров и с native SQL `SELECT CAST(? AS INTEGER) FROM RDB$DATABASE`; libpq передаёт текст серверу. Это проверяет wire Bind, но не поддержку PostgreSQL `$n`.

| ID | Сценарий | Критерий |
|---|---|---|
| E01 | PQexecParams без параметров и с native `?` | Реальное execution через Parse/Bind/Execute/Sync; input metadata и значения согласованы |
| E02 | PQprepare/PQexecPrepared, OID=0/явные OID, NULL/text/binary | Partial/zero hints дают inference, любой нестроковый OID точный, string 25/1042/1043 совместимы; formats 0/1/N, range/UTF8/NUL и counts проверены; binary NUMERIC и P8 типы принимаются с точным payload; malformed/range errors до execution, binary NULL принимается |
| E03 | Named/unnamed objects, replacement, Close, Describe | Named statement переживает commit; Close statement/unnamed Parse replacement сохраняют bound portals; Q удаляет unnamed statement/portal; NoData/RowDescription/ParameterDescription согласованы |
| E04 | Несколько portals, Execute maxRows, PortalSuspended | Нет потерь/дубликатов; buffers/cursors/formats изолированы; CommandComplete только по завершении |
| E05 | Parse/Bind/Execute error, последующие сообщения до Sync | Все portals инвалидируются; discard-until-Sync без effects отброшенных команд, explicit transaction остаётся E; malformed frame fatal, semantic count/format error recoverable до Sync |
| E06 | Flush без Sync, несколько Execute до Sync; SET TRANSACTION при pending implicit cycle | Ответы доступны, implicit commit и ReadyForQuery появляются на правильной границе; ошибка откатывает implicit cycle до Sync. Native SET TRANSACTION получает 25001 без замены TPB, pending write откатывается до Sync, затем I и recovery |
| E07 | Sync внутри native explicit transaction, Simple/Extended switching | T и suspended portals сохраняются через Sync; transaction end удаляет portals, named statement остаётся; ошибка сохраняет transaction/E с инвалидированными portals |
| E08 | C libpq 14.24 sync/async без pipeline | PQsendPrepare/DescribePrepared/QueryParams/QueryPrepared; PQflush + poll READ/WRITE + PQconsumeInput + PQisBusy перед каждым PQgetResult; полный drain до NULL и PQclear всех результатов. Named/unnamed reuse, Parse42601/Bind22003/Execute22012, recovery, explicit T→E→25P02→ROLLBACK/I и настоящий rollback эффекта |
| E09 | Limits, EOF/shutdown с prepared objects и suspended portal | 128 statements, 128 portals, 8 MiB учитываемых SQL/metadata/buffers; при отказе нет частично опубликованного object; закрываются handles/cursors, module lifetime сохраняется |
| E10 | Psycopg 3: `%s` → `$n` и automatic BEGIN | Последовательные `$n` с типами из SQL ожидаются успешными; BEGIN без опций — T и rollback→I. Сценарий обновлён, новый live-повтор Psycopg 3 недоступен; parameterized compatibility не заявляется |
| E11 | Несколько Parse/Describe/statements/portals перед ALTER SESSION RESET | Prepare использует текущую application transaction либо NULL/system prepare; собственных transactions у prepared objects нет. RESET не получает ошибку из-за скрытой prepare-transaction, корректно заменяет текущую transaction, инвалидирует все statements/portals, включая same-pointer native reset; следующий запрос работает |

### Дополнение P9: целевой M2 Linux/Super PASS

Версии фиксируются отдельно: C libpq 14.24 headers/runtime и Psycopg 3.3.6 binary implementation/libpq 18.6 на Python 3.10.12. Профиль Psycopg 3 прошёл пять live TCP групп с четырьмя ожидаемыми ограничениями. Реальные Cursor/AsyncCursor проверены без параметров и без pipeline; traces подтверждают Simple/Extended/Close, а native observer — effects. P9 прошёл 17 групп, целевой M2 для совместимого SQL и реализованных codecs принят. C async дал 20 commands/20 results и 40 poll calls. Повторные регрессии прошли: Simple Query 12, transactions 14, startup 49, server 7 с 58 pytest cases, P8 codecs 8, native baseline 9. Evidence фиксируется в VALIDATION; следующий этап — P10.

| ID | Клиентский сценарий | Критерий |
|---|---|---|
| P9-01 | Psycopg 3 autocommit=True, zero-argument SELECT | PASS: реальные P8 values/OID/type conversions и пустой результат; Simple Query отдельно от Extended |
| P9-02 | Psycopg 3 forced prepare=True / binary=True | PASS: реальный Extended обмен, named reuse через COMMIT, text/binary codecs и Close eviction; отдельная connection с включённым preparation. AsyncCursor Simple/Extended/binary и native COMMIT также PASS |
| P9-03 | Psycopg 3 native controls с prepare_threshold=None | PASS: fresh autocommit connection, SET TRANSACTION/COMMIT/ROLLBACK, savepoint, I/T/E, failed COMMIT и effects; без автоматического BEGIN и cache maintenance SQL |
| P9-04 | Prepared cache, затем ROLLBACK; source boundaries DROP/ALTER/RESET | Подтверждённый negative case: после завершённого ROLLBACK драйвер посылает DEALLOCATE ALL и получает 42601; состояние I и следующий запрос работают. DROP/ALTER cache clear и RESET stale cache отдельно live не проверялись |
| P9-05 | C/raw-wire compatible SQL и регрессии P8/M1 | PASS выбранной целевой матрицы: M2 принят; SQL неизменённый, `$n`/BEGIN negative cases сохранены, driver versions и cleanup подтверждены |

В исторической приёмке P9 live подтверждены три syntax boundaries с 42601: автоматический BEGIN, `%s` → `$n` и `sql.Literal(date)` → `::date`. Prepared trace содержит Parse 6, Bind/Execute по 9, Sync 19 и Close 4; одного Simple Query для этого результата недостаточно. В `prepare_threshold=None` даже forced prepare=True отключён, поэтому P9-02 и P9-03 используют разные connections. Реальный event loop E08 не добавляет внутренних deadline/pause tests; тестовый subprocess ограничен существующим внешним watchdog.

Первый срез `$n` проверяет отказ repeated/reordered indices/gaps, overflow и references в literals/comments. Обычный Psycopg 3 Cursor с переходом к named preparation требует отдельного live-повтора (клиент отсутствует). Эти проверки обязательны для будущего заявления о таком клиенте. ClientCursor и клиентская подстановка не заменяют Extended Query. Pipeline и COPY требуют самостоятельных матриц, если входят в профиль приложения.

В E11 проверить оба входа: несколько Parse/Describe из I без собственной transaction на statement и те же операции при уже открытой explicit transaction. Создание implicit cycle допускает только один общий current handle. Сравнить ownership counters и native monitoring, учитывая внутренние engine transactions отдельно; одинаковый interface pointer после RESET не доказывает прежнюю underlying transaction.

## L. Resource ownership и остановка

| ID | Сценарий | Критерий |
|---|---|---|
| L01 | Серии connect/SQL/error/disconnect после warmup | Session attachments, transactions, cursors, plugin sessions и FDs возвращаются к baseline; documented caches выделены отдельно |
| L02 | Малформированный/медленный клиент и исчерпание лимита | Другие PG sessions и native обслуживаются; очереди/память ограничены |
| L03 | Shutdown в accept, idle read, password exchange, incomplete frame/write | Readers/writers разбужены, workers завершены, registry освобождён |
| L04 | Shutdown active SQL/lock wait | Native WAIT без plugin lock timeout; явный shutdown отменяет операцию и не ждёт освобождения конкурирующего lock |
| L05 | Provider phases | Нет бесконечного join в preproviders; cancellation позволяет providers завершиться; после shutdown не запускаются engine operations |
| L06 | Library lifetime | Инструментированный plugin подтверждает, что callbacks/sessions завершены до releasePlugin/module cleanup; нет direct dlclose и use-after-unload |
| L07 | Частичная ошибка создания session/plugin и repeated stop | Идемпотентный cleanup, нет double release; initial status не затёрт вторичной ошибкой |

Подходящее свидетельство выбирается под риск: ownership counters, monitoring, FDs, инструментированный тестовый plugin, при необходимости sanitizer. Только отсутствие lock не доказывает отсутствие утечек. Проверять cleanup и worker completion независимо от завершения клиентского процесса.

В P6 lock-wait fixture синхронизируется по MON$RECORD_WAITS > 0 и вызывает server shutdown при удерживаемом lock. Собственного engine/lock timeout он не задаёт и его срабатывания не ожидает; общий postproviders join по-прежнему ограничен 5 секундами.

## R/C/U. Удалённый профиль, TLS, отмена и приложение

| ID | Сценарий | Критерий |
|---|---|---|
| R01 | Отдельный узел/network namespace | Реальная TCP-топология зафиксирована; listener и engine в одном firebird |
| R02 | SSLRequest → TLS, sslmode=verify-full | Проверены trust/hostname, неверный/истёкший сертификат и отказ handshake; выбранная в P0 TLS dependency отражена в build |
| R03 | Non-loopback без TLS; partial/slow TLS | Password не передаётся в открытом канале; shutdown прерывает handshake, без вмешательства native WireCrypt |
| C01 | BackendKeyData/CancelRequest при SQL и lock wait | 57014 в основном connection, безопасная cancellation ownership, correct I/E |
| C02 | Invalid/stale/чужой key; races с complete/detach | Чужая session не отменяется; cancel socket не создаёт application attachment; нет UAF/hang |
| U01 | Startup/session commands выбранного приложения | Фиксированы driver/libpq/version/options и trace без секретов; SQL понимает engine parser, settings имеют реальный эффект |
| U02 | Неизменённый драйвер, сценарии приложения, pooling если нужен | Все обязательные операции работают либо их parser dependencies явно блокируют приёмку; специальный клиентский режим не скрывается |
| U03 | Итоговые native/PG concurrency и regression | Cross-visibility, privileges, transaction/resource/lifecycle gates повторены для итогового профиля |

Приложение пока не выбрано. Если его driver автоматически посылает `BEGIN`, `$n`, `SET` или catalog queries, эти реальные dependencies нельзя исключить из acceptance. Их неподдержка пока ограничивает применимость endpoint, хотя совместимый SQL уже работает. Исходный Python helper принудительно использует loopback и `sslmode=disable`; R/C/U требуют отдельного harness.

## Миграция Python scaffold

Миграция выполнена, 58 cases прошли live; результат записан в VALIDATION. По сравнению с исходными 56 cases default connection стал autocommit, explicit transactions используют native SET TRANSACTION/COMMIT/ROLLBACK. В исходной миграции driver-generated BEGIN и connection context проверялись отрицательно. Этап PG BEGIN переводит оба случая в положительные проверки SELECT/DML/commit/rollback; новая приёмка записана отдельно в VALIDATION. RETURNING проверяется положительно, а unsupported output — отдельным отказом до side effects. Scalar/CRUD/errors/permissions/native roundtrip сохранены; Postfix `::date` добавлен отдельным parser-срезом; его live Psycopg 3 повтор остаётся SKIP при отсутствии клиента.

Skip/xfail не заменяют приёмку; отсутствие обязательного сервера или fixture — ошибка. Будущие Extended Query/parser-dependent client profiles относятся к следующим этапам.

Пример окружения для самостоятельного запуска, без настоящих секретов:

```sh
export PGWIRE_TEST_DSN='host=127.0.0.1 port=15432 dbname=pgwire_test user=PGWIRE_TEST'
export PGPASSWORD='<пароль тестового пользователя>'
export PGWIRE_LIMITED_DSN='host=127.0.0.1 port=15432 dbname=pgwire_test user=PGWIRE_LIMITED'
export PGWIRE_LIMITED_PASSWORD='<пароль ограниченного пользователя>'
export FB_NATIVE_DSN='inet://127.0.0.1:3050/pgwire_test'
export FB_NATIVE_USER='PGWIRE_TEST'
export FB_NATIVE_PASSWORD='<пароль native observer>'
export FB_ISQL='/absolute/path/to/firebird/isql'
```

Python dependencies закреплены в `requirements-tests.txt`; фактический runtime libpq записывать отдельно. Из каталога пакета подготовительная проверка выполняет `python -m py_compile tests/test_pgwire_acceptance.py` и `python -m pytest --collect-only -q`. Запуск выбранного integration набора оборачивается во внешний deadline, например `timeout 180s python -m pytest -q -m integration`. `connect_timeout` не ограничивает зависший SQL; native subprocess и серверные ожидания также имеют собственные deadlines.

## Отчёт VALIDATION

Отдельно записать SHA/build/OS/ServerMode, module filename и configured name/port, limits, driver/header/runtime versions, реальные commands и результаты. Для каждой строки выбранного этапа указать test/node ID, evidence и pass/fail/not-run; collection, unit и live integration отделяются.

Обязательно приложить A01/A10 process/provider evidence, auth/identity/policy, parser byte/metadata checks, native visibility, shutdown/module lifetime и capability matrix. Указать точный принятый этап и оставшиеся parser/client dependencies. Работающий SELECT, положительный probe, собранные tests или несколько вручную выбранных driver calls не заменяют обязательные gates.
