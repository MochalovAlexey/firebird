# Проверки реализации

## 11 ноября 2026: публичное представление namespace IDs

[APP-11](1c/NAMESPACE_CATALOG_TASK.md) реализован поверх
`5bbdaa763d5ff35f22c6969503a1811d7f4aac7d`, оставлен на review без git add/commit.
Production — только [install_namespace_catalog.sql](1c/sql/install_namespace_catalog.sql):
DISTINCT view PG_CATALOG.PG_NAMESPACE(OID, NSPNAME) над принятым APP-10,
PUBLIC USAGE/SELECT и один COMMIT. Таблица/sequence/hook APP-10 и compat.sql
не изменены. `git diff HEAD -- src` пуст, SHA256:
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
Новых API, codecs, parser changes, SQL rewriting или зависимостей нет.

Проверенный runtime APP-10 сохранён в
`/tmp/firebird-pgwire-namespaces-review-20261111/runtime`; исходный build path
`/tmp/firebird-pgwire-input-review-build-20261111` уже отсутствовал.
Из сохранённого runtime подготовлена отдельная чистая копия
`/tmp/firebird-pgwire-catalog-source-20261111`: без data/test databases и logs,
с копией чистого `gen/Debug/firebird/security6.fdb`. Исходники, security template
и review-runtime не изменялись. Происхождение и SHA записаны в
`/tmp/firebird-pgwire-catalog-source-20261111/catalog_source.json`.
Сборка C++ не повторялась: SHA бинарников совпали с принятой APP-10 сборкой.

Воспроизведение подготовки при наличии сохранённых файлов (новый каталог назначения):

```sh
python3 - <<'PY'
from pathlib import Path
import shutil
source = Path('/tmp/firebird-pgwire-namespaces-review-20261111/runtime')
target = Path('/tmp/firebird-pgwire-catalog-source-20261111')
shutil.copytree(source, target, ignore=shutil.ignore_patterns('data', 'firebird.log', 'memdebug*'))
shutil.copyfile('gen/Debug/firebird/security6.fdb', target / 'security6.fdb')
PY
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-catalog-source-20261111 \
  --work-dir /tmp/firebird-pgwire-namespace-catalog-03 \
  --namespace-catalog --timeout 180
```

Harness сам создаёт отдельные runtime/security/main и negative-install БД,
выбирает loopback TCP ports, использует native isql и libpq без новых пакетов.
Для повтора нужны новые пути source/work; при исчезновении /tmp нужен проверенный
runtime с чистой security template, а этот исторический результат не заменяет
новый запуск. Пароли не записаны в команды/SQL.

Итог `/tmp/firebird-pgwire-namespace-catalog-03/summary.json`:
**PASS**, completed=true, namespace_catalog_passed=true. 6 групп установки,
4 native/libpq группы, 128 проверенных libpq results (включая prepare/describe).
libpq 14.24; сервер PID 1318248, native port 39237, PgWire 57613,
exit_code=0, forced_kill=false, shutdown 0.164 s. Native probe до/после и проверка
отсутствия оставшихся attachments прошли. Все базы в /tmp; рабочая 1С не затронута.

| Проверка | Результат |
|---|---|
| Bootstrap / new PG_CATALOG | PASS: точный private mapping, прежние IDs, новая schema зарегистрирована hook ровно один раз; owner/source/BLR/grants PG_BACKEND_PID сохранены |
| Missing APP-10 / repeat / foreign PG_NAMESPACE | PASS: dependency 42S02; повтор и коллизия явно отказали, объект/owner/data/grants/IDs/counter сохранены |
| Ошибка перед COMMIT | PASS: rollback view/grants; новая PG_CATALOG и registry row исчезают вместе. Sequence 3→4 остаётся допустимым gap; существующий bootstrap не повреждён |
| Native / Simple / Extended / named prepared | PASS: одинаковые IDs/имена, PUBLIC и "public" различны, mixed case/пробел/кавычка/leading space и 56 Я = 112 UTF8 bytes без усечения, missing name — ноль строк |
| Wire metadata / bytes | PASS: SQL-derived inputs 1043/20, outputs 20/1043, sizes 8/-1, typmods -1, labels OID/NSPNAME. Text и binary inputs/results; BIGINT network-order 8 bytes, UTF8 bytes имени точные |
| Alias / derived / CAST / обе UNION ветви | PASS: BIGINT type 20, 4294967296 не усечён; происхождение/alias не подменяют тип |
| Права / READ ONLY | PASS: обычный пользователь читает все registry rows, включая schema без USAGE; новых CREATE SCHEMA/base/sequence grants нет. INSERT/UPDATE/DELETE отклонены и владельцу; native/libpq чтения не меняют counter |
| Bootstrap-функция | PASS: native и libpq PG_BACKEND_PID возвращают CURRENT_CONNECTION; definition/privileges прежние |
| DDL / transaction visibility | PASS: собственные public/private rows совпадают; второй attachment не видит uncommitted CREATE/DROP. COMMIT/full ROLLBACK согласованы; ALTER сохраняет ID, DROP+CREATE меняет, prepared lookup видит новый ID |

| Артефакт | SHA256 |
|---|---|
| libPgWire.so | `adb3eacaadf560a73e7ce9d9ce586d1a2145fea0c869d86d9bc6849c7c4c3395` |
| libEngine14.so | `ba439210e32962db216a7853c8ff8857c70a77d8dc045e8abda0a7d86d011bf7` |
| libfbclient.so.2 | `1a6b50a5e3e13e7c81cb5c9d1da599b21154d94895c53f163cb543cd0a5e5a85` |
| install_namespace_catalog.sql | `3512cba9248c6e4d97c99e0bf488e6a8e1a4ec1c06a1aa8797d4293a4119b6fe` |
| pgwire_1c_smoke.py | `b4e8396a8fde37f6a1947ec2865d3f68324810b7218adb4b38ea03db93fcd887` |
| pgwire_namespace_checks.py | `229e5803cd3b90c69d1622dc16ae3402253cd733288dde02d51aed86e2d8265b` |
| install_namespace_registry.sql (прежний) | `2921fec5381a180021656a6a3d6182c39de92ace4ff5900ab61547ac1766eafe` |
| compat.sql (прежний) | `dbffdaff007e797370ee5d78b285f5338163d4911c015d6b973e2be38fdff7d5` |

Промежуточные FAIL сохранены в namespace-catalog-01/02. В 01 тест ожидал
28000 для PgWire CREATE SCHEMA без права, фактически получен 42000; проверка
исправлена с дополнительной native проверкой причины permission denied.
В 02 GRANT CREATE SCHEMA после открытия libpq attachment не разрешил ему DDL.
Фикстура выдаёт право явно после проверки отсутствия installer grants и создаёт
новый DDL attachment; исходное соединение остаётся reader. Это исправления
проверок, production SQL не менялся. Оба промежуточных сервера завершились exit 0.

SKIP по границе этапа: rebuild/старые suites, полная APP-10 concurrency/restart
матрица и known NAT-* crash repro. Native DDL под user savepoint запрещён (0A000),
положительный DDL ROLLBACK TO не заявлен. Установка требует исправного APP-10
с активным hook, администратора и отсутствия конкурентного DDL; repair и
автоматическое обнаружение disabled hook не добавлены.

Это namespace values с native metadata, не полная PG catalog compatibility.
PG oid/name/regnamespace, PG/native names и resolution, nspowner/nspacl,
pg_class/visibility/temp namespace отсутствуют; APP-01/APP-02/APP-06 и selected04
остаются открытыми. Далее требуется отдельное решение о namespace resolution,
затем pg_class/relnamespace и search-path visibility, после — неизменённый selected04.

## 11 ноября 2026: архитектурный review namespace registry

Проверена ревизия `5bbdaa763d5ff35f22c6969503a1811d7f4aac7d` относительно
`c27a3a4c5a`: installer, подключение к прежнему harness, проверки и ограничения.
Замечаний к реализации APP-10 не найдено. Production diff ограничен SQL;
`git diff c27a3a4c5a 5bbdaa763d -- src` пуст. Native запрет DDL под user savepoint
подтверждён в DsqlDdlRequest::execute; проверенный отказ/recovery не выдаётся за
поддержку schema DDL с последующим ROLLBACK TO. APP-10 закрыт в своей границе;
APP-01 и исходный selected04 остаются открытыми.

Независимо повторена целевая приёмка из чистого worktree этой ревизии:

```sh
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-input-review-build-20261111/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-namespaces-review-20261111 \
  --namespaces --timeout 180
```

Итог `/tmp/firebird-pgwire-namespaces-review-20261111/summary.json`:
completed=true, namespaces_passed=true. PASS: 4 группы установки, 11 native/libpq
групп, 11 адресных отказов доступа, 6 сверок состояния и отдельный restart.
libpq 14.24; оба server runs exit_code=0, forced_kill=false:
PID 1311191 (native 60309/PgWire 39427), restart PID 1311392 (56815/36825).
Runtime, installer и оба harness SHA256 совпали с таблицей реализации ниже;
source_diff_sha256 — SHA пустого diff. Готовая сборка соответствует неизменённым
C++ исходникам, rebuild не выполнялся. Остальные suites не повторялись.
Все fixtures/security DB/runtime отдельные в /tmp, рабочая 1С не затрагивалась;
known NAT-* crash repro не запускались.

При выборе следующего этапа выполнен отдельный native OO metadata probe
на том же Engine14, без изменений production: артефакты
`/tmp/pgwire-catalog-metadata-probe-20261111-hal80k8l/`
(`probe.cpp`, `result.log`, `summary.json`), exit=0. SHA256 probe.cpp:
`b781403885a2a7aa3ed39a2a8b6e1abbcbb59c8fb5c5233f642502f25334fc36`.
Это исследование metadata в отдельной embedded БД, не приёмка нового catalog.

Прямой SELECT/alias/derived/CTE сохраняют schema/relation/field исходной view;
CAST/арифметика/CASE/MIN/MAX их не сохраняют. UNION с первым полем
PG_CATALOG.PG_NAMESPACE.OID и второй ветвью `CAST(4294967296 AS BIGINT)` имеет
общий native BIGINT, но сохраняет provenance первой ветви. Перестановка ветвей
меняет provenance. Input параметры имён исходной колонки не имеют.
Следовательно, эти getters нельзя использовать как надёжную идентичность SQL-типа
для подмены native BIGINT на PG oid; range guard не исправит общий тип UNION.

Выбран [APP-11](1c/NAMESPACE_CATALOG_TASK.md): только SQL-view реальных namespace
данных, native BIGINT/VARCHAR и wire20/1043. На момент этого review реализация
ещё не была выполнена; её отдельная приёмка приведена выше. В PostgreSQL
[pg_namespace](https://www.postgresql.org/docs/18/catalog-pg-namespace.html)
использует oid/name; полноценные типы, правила PG/native имён и остальные
catalog queries остаются отдельными задачами. API/codecs для имитации type identity
по названию колонки не расширяются.

## 11 ноября 2026: внутренний namespace registry

[APP-10](1c/NAMESPACE_REGISTRY_TASK.md) реализован поверх
`c27a3a4c5a964775ac759bd2d7ebcf6e35323351` и оставлен на review, без git add/commit.
Production — только устанавливаемый
[install_namespace_registry.sql](1c/sql/install_namespace_registry.sql).
Изменения harness: новый `--namespaces` в прежнем `pgwire_1c_smoke.py` и
`pgwire_namespace_checks.py`, без нового framework/dependencies.
`git diff HEAD -- src` пустой (SHA256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`):
C++/parser/engine/PgWire/public API не менялись, повторная сборка не требовалась.
Исходные пользовательские README/PLAN/BACKLOG/VALIDATION, PROGRESS и файл задания
сохранены и дополнены.

Installer создаёт PGWIRE_COMPAT.NAMESPACE_IDS (NATIVE_NAME/NAMESPACE_ID), одну
NAMESPACE_SEQ, read-only DISTINCT view NAMESPACES и NAMESPACE_DDL с SQL SECURITY
DEFINER. Имя — VARCHAR(63) UTF8/case-sensitive UTF8, PK; ID — NOT NULL UNIQUE BIGINT,
CHECK 1…4294967295. Initial fill — SYSTEM.RDB$SCHEMAS, только TRIM TRAILING CHAR
padding, без LOWER/UPPER. Служебная schema также включена. Hook отфильтровывает
OBJECT_TYPE=SCHEMA и использует OBJECT_NAME; CREATE выделяет ID, ALTER сохраняет,
DROP удаляет строку. Sequence не сбрасывается; пропуски допустимы.
PUBLIC получает только schema USAGE и SELECT view. Native CREATE SCHEMA права
приложению installer не выдаёт, они остаются отдельным решением администратора.

Использована готовая независимая сборка из review APP-08:
`/tmp/firebird-pgwire-input-review-build-20261111/gen/Debug/firebird`.
Фактически загруженные SHA256 совпали с review:

| Артефакт | SHA256 |
|---|---|
| `libPgWire.so` | `adb3eacaadf560a73e7ce9d9ce586d1a2145fea0c869d86d9bc6849c7c4c3395` |
| `libEngine14.so` | `ba439210e32962db216a7853c8ff8857c70a77d8dc045e8abda0a7d86d011bf7` |
| `libfbclient.so.2` | `1a6b50a5e3e13e7c81cb5c9d1da599b21154d94895c53f163cb543cd0a5e5a85` |
| `install_namespace_registry.sql` | `2921fec5381a180021656a6a3d6182c39de92ace4ff5900ab61547ac1766eafe` |
| `pgwire_namespace_checks.py` | `e89e15e8c18b2bc251cbc25a5ee20dbafa64bdab57d2293d368821aabb6d65f7` |
| `pgwire_1c_smoke.py` | `89d6d6a88ef3e433e7cb66ca6de160a57e801d9a3bb42f7ec9bb6a0dac065985` |

Начальный native probe: `/tmp/namespace-hooks-probe.py`, артефакты
`/tmp/firebird-pgwire-namespaces-probe-01/{hook_schema.log,hook_owner.log,summary.json}`.
На этой сборке подтверждены CREATE/ALTER/DROP SCHEMA events, точный OBJECT_NAME
`MiXeD Тест`, пустой SCHEMA_NAME и EFFECTIVE_USER=SYSDBA при DDL обычного
WIRE_HOST_TEST. Hook пишет в закрытую таблицу владельца через DEFINER; ROLLBACK
удаляет и schema DDL, и запись hook. Прежний native prototype ниже использован
как исследование, новый probe — отдельная проверка текущего review runtime.

Целевая команда из корня репозитория:

```sh
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-input-review-build-20261111/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-namespaces-04 --namespaces --timeout 180
```

Итог `/tmp/firebird-pgwire-namespaces-04/summary.json`: completed=true,
namespaces_passed=true. 4 группы установки, 11 native/libpq групп и отдельная
restart-проверка; 76 namespace isql logs, 11 адресных отказов доступа, 6 сверок
registry с native schemas/ID. Настоящий libpq 14.24 (140024), native isql той же
сборки, одна private database/server для cross-visibility; failure/collision
fixtures — две дополнительные БД того же private runtime.

| Проверка | Результат |
|---|---|
| Fresh install | PASS при search_path=SYSTEM: 7 заранее созданных schemas, SYSTEM/PUBLIC и PGWIRE_COMPAT; registry точно равен RDB$SCHEMAS |
| Exact native names | PASS: PUBLIC/quoted public имеют разные IDs; mixed case, кириллица, пробел, leading space, двойная кавычка и имя 56 Unicode characters; отдельные quoted CREATE/DROP через hook и parameterized libpq lookup |
| One-shot/atomic install | PASS: повторный запуск 42000 без изменения mapping/counter/объектов; чужая PGWIRE_COMPAT сохраняет owner/table/data/sequence. Ошибки 22012 в середине и непосредственно до COMMIT оставляют ноль объектов; последующий fresh install успешен |
| Lifecycle | PASS: CREATE/ALTER/DROP, CREATE OR ALTER, RECREATE, IF EXISTS/IF NOT EXISTS; ALTER/no-op без allocation, новый lifetime без reuse, failed RESTRICT и DROP CASCADE |
| Other DDL/DML | PASS: native table ALTER/INSERT/UPDATE работают, registry и allocator неизменны |
| Access/definer | PASS: обычный schema owner выполняет DDL и читает view без base grants; direct table SELECT/DML, sequence allocation/reseed/drop, trigger alter/drop, table alter — 28000; view DELETE — 42000, также для SYSDBA owner |
| Constraints/DDL failure | PASS: NULL/0/-1/4294967296 и duplicate names/IDs отвергнуты; 4294967295 принят в откатанном test insert. Временный CHECK guard в registry отменяет исходный CREATE SCHEMA целиком |
| COMMIT/full ROLLBACK | PASS: нет phantom namespace, uncommitted ID не виден native observer, rollback gaps не переиспользуются |
| Schema DDL под user savepoint | **Native NOT SUPPORTED: 0A000**. Положительный undo такого DDL через ROLLBACK TO не пройден; refusal/E/ROLLBACK TO recovery и сохранность mapping PASS |
| Two connections | PASS: два одновременно открытых transactions с разными именами получают разные IDs; до commit нет чужих rows, после commit mapping одинаков |
| Same-name conflict | PASS: после завершённого первого uncommitted CREATE native и PgWire NO WAIT возвращают одинаковый 42000/schema already exists; rollback/retry без phantom/duplicates. Синхронизация шагов явная, sleeps отсутствуют |
| Read-only lookup | PASS native/libpq: повторные SELECT не меняют таблицу/counter, отсутствующее имя даёт ноль строк, OID ID-колонки остаётся BIGINT/20 |
| New attachment/restart | PASS: после обычного restart 15 committed mappings и counter=27 прежние; значения — артефакт fixture, не зарезервированные IDs |
| Unrelated build/suites | Не повторялись: SQL/harness-only diff; предыдущий PASS не выдаётся за новый прогон |

Ограничение savepoint воспроизведено отдельно native isql, без PgWire:

```sql
SET AUTODDL OFF;
SET TRANSACTION READ WRITE READ COMMITTED;
SAVEPOINT S;
DROP SCHEMA NS_TX_ROLLBACK;
-- SQLSTATE 0A000: Running under user savepoint S, DDL prohibited
```

Это штатный guard DsqlDdlRequest::execute в `src/dsql/DsqlRequests.cpp`:
проверка user savepoints предшествует node->executeDdl. Он не возникает из registry
и не относится к known nested-EDS crash repro. CREATE имеет тот же отказ.
Проверены native отказ и PgWire E -> ROLLBACK TO -> T с неизменным mapping.
Также CREATE до SAVEPOINT сохраняется после ROLLBACK TO и откатывается полным
ROLLBACK. Положительная схема DDL-after-savepoint требует отдельного решения по
native движку; обход/изменение ядра в APP-10 не добавлялись.

Оба server runs завершились exit_code=0, forced_kill=false, shutdown около 0.164 s:
первый PID 1301359 (native 40615, PgWire 51805), после restart PID 1301568
(native 42641, PgWire 46783). Нет оставшихся attachments; native probes до/после
успешны. Все runtime/config/security DB/fixtures в отдельных /tmp; рабочие базы,
серверы и исходная 1С не изменялись. Known NAT-* crash repro не запускались.

Промежуточные результаты сохранены: namespaces-01 остановился до server start
на native schema-name limit 56 (фикстура ошибочно использовала 63); namespaces-02
считал системные CHECK triggers как пользовательские (3 вместо 1); namespaces-03
выявил описанный native savepoint guard, server exit 0. Проверки исправлены по
реальной семантике; финальный namespaces-04 прошёл. Первая попытка подготовки
probe в sandbox остановилась на создании test socket; native hooks затем проверены
отдельным embedded probe, TCP-приёмка — с разрешённым локальным TCP.

[PG pg_namespace](https://www.postgresql.org/docs/18/catalog-pg-namespace.html)
и [OID/regnamespace](https://www.postgresql.org/docs/18/datatype-oid.html) описывают
будущий публичный контракт; текущая BIGINT/view основа не выдаётся за эти типы
и catalog. APP-10 реализован и ожидает review с зафиксированной native границей
savepoint. APP-01/APP-06 и selected04 остаются открытыми. Disabled hooks/
-nodbtriggers, manual corruption/deletion, upgrade/uninstall/repair вне контракта;
backup/restore/migration не проверены. Восстановление пропущенного DROP+CREATE
по имени не гарантирует identity, lookup ничего автоматически не исправляет.


## 11 ноября 2026: архитектурный review bytea input

Проверена ревизия `c27a3a4c5a964775ac759bd2d7ebcf6e35323351` относительно
`5478a589d8` по [APP-08](1c/BINARY_BLOB_INPUT_TASK.md). Замечаний к ограниченному
контракту не обнаружено. Проверены logical OID/physical OCTETS, динамический
layout, пределы и NULL/empty, hex/escape, сохранение старого layout при ошибке,
владение metadata и отдельный codec каждого portal. Production ограничен
InputCodec.cpp/h; engine/parser/Extended/API не менялись.

Source diffs и четыре harness/helper SHA совпадают с отчётом реализации ниже.
Но прежний `/tmp/firebird-pgwire-1c-build` и итоговые runtime summaries уже
отсутствовали; их PASS нельзя было повторно прочитать. Сохранены unit JSON,
build logs и reference script. Сборка в repo gen/Debug была старше изменения.
Поэтому выполнена новая независимая сборка, без изменения рабочего runtime:

```sh
mkdir /tmp/firebird-pgwire-input-review-build-20261111
cd /tmp/firebird-pgwire-input-review-build-20261111
/home/aleksey/dev/my_firebird/firebird/configure --enable-binreloc --enable-developer
make -j2
make -C gen -j2 TARGET=Debug pgwire_tests
```

Debug/Linux/Super, configure/build/units PASS. Логи configure.log, build.log,
unit-build.log, units.json и девять pgwire_*_test.log находятся в build directory.
Units запускались из gen/Debug/firebird/tests с LD_LIBRARY_PATH на build/lib.

| Независимо повторённая проверка | Результат |
|---|---|
| Девять pgwire executables | PASS: Protocol, ErrorStatus, ResultCodec, Input 559, Float 112, Numeric 502, Temporal 313, SQL 575, Session 5 |
| `--blob-input` | PASS: 125 libpq checks, 7 raw groups, 9 native readback/visibility observations |
| `--blobs` | PASS: 92 checks, 32 PQunescapeBytea roundtrips, 9 native observations, 7 raw groups |

TCP-команды из корня репозитория, с разрешённым локальным TCP:

```sh
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --firebird-dir /tmp/firebird-pgwire-input-review-build-20261111/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-input-review-20261111 --blob-input --capture-dir /home/aleksey/dev/1c/captures/20261109T105635Z --timeout 180
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --firebird-dir /tmp/firebird-pgwire-input-review-build-20261111/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-input-output-review-20261111 --blobs --timeout 180
```

Оба summary: completed=true, соответствующий passed=true, revision=c27a3a4c5a,
пустой production diff; оба server runs exit_code=0, forced_kill=false.
Только отдельные runtime/security/test DB, рабочие базы не затрагивались.
Остальные runtime suites и Postgres Pro reference live заново не запускались;
их код/сохранённые записи рассмотрены, прежние результаты не выданы за новый PASS.

SHA256 фактически проверенной новой сборки:

- PgWire: `adb3eacaadf560a73e7ce9d9ce586d1a2145fea0c869d86d9bc6849c7c4c3395`;
- Engine: `ba439210e32962db216a7853c8ff8857c70a77d8dc045e8abda0a7d86d011bf7`;
- fbclient: `1a6b50a5e3e13e7c81cb5c9d1da599b21154d94895c53f163cb543cd0a5e5a85`.

DT_NEEDED PgWire совпадает с repo baseline: libfbclient.so.2, libtommath.so.1,
libtomcrypt.so.1, libm, libgcc_s, libc, loader. В старой /tmp сборке была
libtommath.so.0; новая использует установленную .so.1, как repo baseline.
Новые зависимости не устанавливались и не добавлялись кодом.

APP-08 закрыт. APP-09 (>65533 input bytes), APP-02/SQL-06 (SQL types/casts/semantics),
APP-01/05/06 и сквозной запуск 1С/Гилёва остаются открыты. Текущая оценка готовности
сохранена в [1c/PROGRESS](1c/PROGRESS.md). В архитектурном диалоге изменена только
документация; реализации следующего этапа в repo и commit нет.

### Native proof следующего этапа namespace identity

Для [APP-10](1c/NAMESPACE_REGISTRY_TASK.md) отдельно проверены штатные SQL schema
hooks и одноразовый installer. Артефакты:
`/tmp/pgwire-schema-registry-proof-20261111-sdzk641h` — install.sql, run_checks.py,
quoted.sql, логи и summary.json. Это исследовательский prototype, не установленная
production-функция и не приёмка pg_catalog/selected04.

Использовались read-only binaries repo gen/Debug и отдельные configs/locks/новые
БД в /tmp, без сервера/TCP. Engine SHA:
`35d44dd6c7fba3840a2495ec1d379344d22a1a4e4ccbdc209e306b30f5a45935`.
Это другая сборка, чем bytea review выше; доказательство относится к native
DDL-механизму. Первая попытка со старым отсутствующим /tmp build остановилась
до запуска isql/создания базы.

PASS: атомарный install; CREATE rollback, ALTER сохраняет ID, DROP rollback
восстанавливает ID, committed DROP+CREATE даёт новый ID; обычный schema owner
читает view в READ ONLY и выполняет собственный schema DDL через definer hook;
прямой DELETE registry отказан. Повтор install отказан до изменений, прежние
IDs сохранены; rollback полной установки убирает schema/table/view/sequence/
trigger. PUBLIC и quoted "public" различаются, mixed case/пробел/кириллица
сохраняются. Проверено OBJECT_NAME для schema events. Summary SHA256:
`c6deef1743411d5bc140cc08031d629763601406311d54993b0e921ae30d8cff`.

Остальные будущие проверки — concurrency, restart, savepoints, все DDL-варианты,
libpq coexistence и install failure injection — заданы в инструкции APP-10,
этим prototype не объявлены пройденными. Public PG name mapping/oid/regnamespace,
каталоги/visibility и bypass-disabled-hook recovery здесь не реализованы.

## 11 ноября 2026: bytea input через native VARCHAR OCTETS

[APP-08](1c/BINARY_BLOB_INPUT_TASK.md) реализован поверх
`5478a589d8fc403618d7b05bb00a19d3ed3d08fe`, ветка `pg_wire_protocol`;
изменения оставлены на review без git add/commit. Исходные незакоммиченные
README/BACKLOG/PLAN/VALIDATION и файл задания сохранены и дополнены.
Production diff — только `src/plugins/wire/pg/InputCodec.cpp/h`.
Engine/parser/ResultCodec/Extended/public API diff пустой; новых зависимостей нет.

InputCodec принимает logical BLOB subtype=scale=0, length=ISC_QUAD, wire OID 17.
Через существующий IMetadataBuilder создаётся physical VARCHAR OCTETS: capacity 0
при Parse, фактические decoded lengths при Bind. Portal-specific metadata и buffer
меняются вместе только после успешного encode; scalar-only metadata прежняя.
Временные decoded strings уничтожаются после encode. Штатный native MOVE создаёт
BLOB при Execute в текущей transaction, без handles/materialization в плагине.
Исходный SQL не переписывается.

SHA256 проверенной сборки/исходников:

| Артефакт | SHA256 |
|---|---|
| `git diff HEAD -- src` | `43a70c205521047805c177c2aa02f1a76805b69404add8aa9964ba2225e061ad` |
| Production diff InputCodec.cpp/h | `1a6d77e7961d7934e43d9e7681de48b40d5f668030abe2f247a9d30991836ca8` |
| `libPgWire.so` | `0e4b2a4a90bc9f86d091d6019e01854906ea0ae5e532eda92961f0542cc6b405` |
| `libEngine14.so`, неизменённый | `36de1e2dcf6320439195e79efa153b4f18acace6a1b1326ab46e0ec4d3bf4cb9` |
| `pgwire_1c_smoke.py` | `2fc0b37ad968f6263b665d5839871df0292003218bd259e80b618c4c70c0aa50` |
| `pgwire_blob_input_checks.py` | `bf831dc8088c839f0561470978b32f7e17cc9961639dba717c241fbf3873fceb` |
| `pgwire_blob_checks.py` | `a0c65feac46c51e46e974518d1f0a00afd533495e68923a89642953df302689e` |
| `pgwire_blob_fixture.cpp` | `85b9b7d2b6cd2766372f7b47c9aa4f87bf3eb227ee56aeca6a0b33ecbb528e07` |

`readelf -d` сравнивался с предыдущим PgWire SHA
`b38d3389ce8235b31bc2c3f89a1fa6ad5b41f4192bf2a99075f3b587f069a5ee`
в `/tmp/pgwire-input-native-probe-20261111/runtime/plugins`.
DT_NEEDED одинаков: `libfbclient.so.2`, `libtommath.so.0`, `libtomcrypt.so.1`,
`libm.so.6`, `libgcc_s.so.1`, `libc.so.6`, `ld-linux-x86-64.so.2`.

Baseline `/tmp/firebird-pgwire-bytea-input-baseline-02/summary.json` подтвердил
0A000 на `SELECT CAST($1 AS BLOB SUB_TYPE BINARY)` с binary input 00ff80,
при уже работающем output OID 17 для native bytes 005c27ffc080. Сервер exit 0.
Native bridge исследован ранее в `/tmp/pgwire-input-native-probe-20261111`;
ниже сохранены его результаты до 65533 bytes и dynamic 68-BLOB layout.
Новый harness дополнительно читает записанные PgWire bytes через отдельное
native TCP attachment и IBlob, без использования ResultCodec для сравнения.

Команды выполнялись из корня репозитория (build — в указанном каталоге):

```sh
make -C /tmp/firebird-pgwire-1c-build -j2
make -C /tmp/firebird-pgwire-1c-build/gen -j2 TARGET=Debug pgwire_tests

# Девять executables pgwire_*_test; LD_LIBRARY_PATH указывает на build/lib.
# Дополнительно wire_socket_test. Результаты: /tmp/firebird-pgwire-bytea-units.json.

python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-bytea-input-05 --blob-input \
  --capture-dir /home/aleksey/dev/1c/captures/20261109T105635Z --timeout 180
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-bytea-output-02 --blobs --timeout 180
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-bytea-parameters-02 --parameters --timeout 180
python3 -B pg_wire_protocol/tools/pgwire_codec_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-bytea-codecs \
  --libpq-include-dir /tmp/firebird-pgwire-1c-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5 --timeout 180
python3 -B pg_wire_protocol/tools/pgwire_extended_query_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-bytea-extended \
  --libpq-include-dir /tmp/firebird-pgwire-1c-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5 --timeout 180
python3 -B /tmp/pgwire-bytea-reference.py
```

| Проверка | Результат |
|---|---|
| Build + pgwire_tests | PASS; `/tmp/firebird-pgwire-bytea-build.log`, `/tmp/firebird-pgwire-bytea-tests-build.log` |
| Units | PASS: все 9 pgwire executables; InputCodec 559, float 112, numeric 502, temporal 313, SqlInfo 575, session 5; wire_socket_test PASS |
| `--blob-input` | PASS: 125 libpq checks, 7 raw groups, 9 независимых native readback/visibility observations |
| `--blobs` | PASS: 92 checks, 32 PQunescapeBytea roundtrips, 9 native observations, 7 raw groups |
| `--parameters` | PASS: 73 checks, 13 native observations, 29 raw refusal/recovery cases |
| Scalar codecs | PASS: 8 групп, включая syscall trace без backend TCP hop |
| Extended Query | PASS: 16 групп, включая object/cache limits, cleanup и syscall trace |
| Postgres Pro reference | PASS: 66 libpq cases; `/tmp/pgwire-bytea-reference-m4m_zu6t/summary.json` |
| Psycopg 3 | SKIP: клиент отсутствует; новый профиль bytea не принят этим драйвером |
| VAL-02 | Сохраняется historical P3 VARCHAR FAIL; отдельный suite не перепроверялся |

Reference использует отдельный кластер `/opt/pgpro/1c-18`, server version 18.6,
libpq 14.24 (140024), UTF8; зависимости не устанавливались. Hex whitespace/case,
escape/odd nibble/invalid digits, неполные escapes и `\400` совпали с byteain:
22023 для hex, 22P02 для escape, 22021 для raw invalid UTF8. Оба значения
standard_conforming_strings дают одинаковые parameter bytes. PostgreSQL принимает
65534 bytes; локальный отказ 0A000 при этой длине — заявленный предел native
transport descriptor, не ограничение PostgreSQL. PQunescapeBytea не использовался
как эталон malformed input. Источники правил:
[PG 18 bytea](https://www.postgresql.org/docs/18/datatype-binary.html),
[byteain](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/backend/utils/adt/varlena.c),
[hex_decode_safe](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/backend/utils/adt/encode.c).

Binary и text hex/escape проверены через настоящий PQexecParams и
PQprepare/PQexecPrepared. NULL/empty, все 256 bytes, decoded NUL/invalid UTF8,
quotes/backslash и printable UTF8 сохраняются; raw text NUL проверен raw Bind
(обычный libpq text parameter использует NUL terminator). Parse/Describe возвращают
OID 17; absent/0/17 hints приняты, mismatch 0A000. TEXT/custom BLOB и обычный OCTETS
остались неподдержанными, SQL bytea cast — parser refusal. Unit проверяет scale,
размер ISC_QUAD и повторный encode long → NULL/empty → short → long, включая
scalar neighbours и сохранение старого layout после неуспешного encode.

Реальные границы: 0/1/32765/32766/40444/65533 bytes в обоих formats приняты
и перечитаны native после commit; 65534 отказан до Execute. Unit достигает
ровно 1 MiB с 16 BLOB и SMALLINT. Raw wire достигает того же размера с 31 scalar
VARCHAR capacity 32760 и BLOB payload 32888: `31*32764 + 32888 + 4 = 1048576`.
При 32889 payload native alignment увеличивает message ещё на 2 bytes — Bind 0A000.
Bind frame при этом значительно меньше лимита, поэтому проверяется именно layout.
68 BLOB среди 92 mixed parameters записаны с короткими разными values/NULL и
независимо прочитаны; 20 таких portals одновременно Bind проходят без исполнения.
8 MiB cache сохраняет 111 дополнительных portals с payload 65533 при уже retained
prepared descriptors, следующий Bind даёт прежний 54000; Sync и короткий Bind
восстанавливаются. Максимального slot reservation на каждый BLOB нет.

Selected23 использован дословно и сопоставлен с external capture SQL: UPDATE 1/0,
все пять parameters (1114/20/17/1043/23), WHERE filename/partno, synthetic payload
40444 bytes. Native isql проверяет timestamp/datasize/filename/partno и нетронутые
соседние строки; IBlob helper сравнивает все bytes. Synthetic PUBLIC.FILES не
выдаётся за original schema; исходный format code/Bind и конфигурация не восстановлены.

Raw Parse/Bind/Describe/Flush/Close не создают application transaction и не меняют
таблицу/sequence. Invalid bytea и unsupported output отказывают до execution,
последующие Parse/Bind/Execute отброшены до Sync. Независимые long/short portals
с mixed formats проходят suspension/resume; повторный Execute завершённого
RETURNING portal не повторяет DML. SELECT cursor, INSERT/UPDATE RETURNING и
singleton `EXECUTE PROCEDURE BIN_PROC(?)` проверены. Prepared работает через
commit/rollback. Native observer видит ровно одну application transaction в T,
не видит uncommitted rows, подтверждает commit/rollback/savepoint/disconnect.
В E возвращается 25P02; ROLLBACK TO восстанавливает T. Duplicate-key 23505 после
native conversion выполняет BEFORE INSERT sequence, но не сохраняет данные:
этот случай явно отделён от Bind preflight refusal.

Все успешные Firebird runs (включая две trace-фазы codecs/Extended) завершились
с exit 0, без forced kill, shutdown около 0.164 s; private PostgreSQL exit 0.
Рабочие серверы/БД/employee/pgwire_test/1С не изменялись. Known native crash repro
не запускались. APP-08 закрыт по реализации и ожидает review; APP-09, APP-02,
SQL-06, APP-05, VAL-02/VAL-04 и фактическое открытие 1С остаются открытыми.

Промежуточные FAIL сохранены отдельно: baseline-01 использовал INSERT без grant;
input-01 ожидал общий 23000 вместо native 23505; input-02 использовал пустую
WIRE_HOST_ROWS для cursor fixture; input-04 пытался задавать DDL VARCHAR сверх
native SQL предела для проверки message boundary (HY004). Все исправления —
в harness/fixtures, production после первой успешной сборки не менялся.
Input-03 и первые output/parameters copies не стартовали из-за ENOSPC; часть
пустых summary не удалось записать. Для освобождения места удалены только
дубликаты binaries собственных завершённых/незапущенных bytea runtimes; logs,
summaries и тестовые БД сохранены. После исправлений input-05/output-02/
parameters-02 прошли полностью; прошедшие codecs/Extended повторно не запускались.


## 11 ноября 2026: архитектурный review binary BLOB output

Проверена ревизия `5478a589d8fc403618d7b05bb00a19d3ed3d08fe` относительно
`ccd81f6473` по [APP-07](1c/BINARY_BLOB_OUTPUT_TASK.md). Замечаний к ограниченному
контракту не обнаружено. Проверены subtype/descriptor gate, оба формата bytea,
NULL/empty, четыре decode paths с текущей transaction, общий DataRow budget,
EOF на точной границе, consuming close/RefPtr::clear и cleanup при ошибках.
Parser/core/InputCodec и публичные интерфейсы не изменены.

Рабочее дерево перед review чистое. Использована существующая Debug/Linux/Super
сборка `/tmp/firebird-pgwire-1c-build/gen/Debug/firebird`, без новой сборки.
Хеши обоих source diffs, Engine/PgWire и трёх BLOB harness files совпали с
отчётом реализации ниже. PgWire:
`b38d3389ce8235b31bc2c3f89a1fa6ad5b41f4192bf2a99075f3b587f069a5ee`;
Engine: `36de1e2dcf6320439195e79efa153b4f18acace6a1b1326ab46e0ec4d3bf4cb9`.

| Независимо повторённая проверка | Результат |
|---|---|
| Девять pgwire unit executables | PASS: Protocol, ErrorStatus, ResultCodec, Input 154, Float 112, Numeric 502, Temporal 313, SQL 575, Session 5 |
| `--blobs` | PASS: 90 libpq checks, 31 PQunescapeBytea round-trip, 9 native observations, 7 raw groups, native fixture/readback |
| `pgwire_codec_smoke.py` | PASS: 8 групп скалярных codecs, включая no-server-network-hop |

Unit logs: `/tmp/pgwire-blob-review-pgwire_*_test.log`; executable из tests/
указанной сборки, LD_LIBRARY_PATH указывает на её lib/. TCP-команды из корня
репозитория с разрешённым локальным TCP:

```sh
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --blobs --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-blob-review-20261111 --libpq /lib/x86_64-linux-gnu/libpq.so.5 --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_codec_smoke.py --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-blob-codecs-review-20261111 --libpq-include-dir /tmp/firebird-pgwire-1c-deps/libpq-dev/usr/include/postgresql --libpq-library /lib/x86_64-linux-gnu/libpq.so.5 --timeout 120
```

Итоговые summaries успешны, revision=5478a589d8; BLOB report фиксирует пустой
production diff. Все три server runs завершились exit_code=0, forced_kill=false.
Только отдельные runtime/security/test DB в /tmp, рабочие базы не затрагивались.
Simple/Extended/casts заново не запускались: их итоговые отчёты и покрытие
проверены, новых оснований для повторения после целевой приёмки не найдено.

APP-07 закрыт. Native live I/O fault injection остаётся VAL-04; ABI mocks
проверяют ownership, но не заменяют такие faults. APP-02, SQL bytea types/casts,
input, каталоги и исходный запуск 1С этим не закрываются. Изменения текущего
архитектурного диалога — только документация; feature implementation и commit
не выполнялись.

### Native probe для архитектуры следующего input-этапа

Отдельная native OO проверка подтвердила существующий путь SQL_VARYING/CS_BINARY
input descriptor → engine BLOB subtype 0 без создания IBlob в PgWire. Исходники
плагина не менялись. Использована изолированная копия указанной сборки с
Providers=Engine14, своими locks/tmp и новыми базами; сервер и TCP не запускались.

Команды исследования:

```sh
python3 -B /tmp/pgwire-input-native-probe-20261111/run.py
python3 -B /tmp/pgwire-input-native-probe-20261111/run_dynamic.py
```

Скрипты создавали новые probe.fdb/dynamic_probe.fdb и завершились 0; повторять
их следует с новыми путями, а не поверх этих баз. Первый probe задавал physical
capacity 65533. Второй начинал с capacity 0 и перестраивал metadata по длине
перед Execute на одном IStatement. NULL, empty и размеры 1/256/32765/32766/40444/
65532/65533 прошли в обоих; independent readback после commit и нового attachment
сравнивает все bytes, включая NUL/невалидные UTF8-последовательности.

Дополнительно dynamic probe: 68 BLOB плюс INTEGER/SMALLINT, смесь NULL/empty/
16-byte payload, physical message 1162 bytes, persisted readback PASS. Исходная
таблица selected19 имеет 68 bytea из 92 параметров: фиксированный максимум на
каждый давал бы более 4 MiB. Поэтому [APP-08](1c/BINARY_BLOB_INPUT_TASK.md)
использует layout конкретного Bind. Probe не выполнял исходный selected19 и
не проверял mchar/mvarchar/индексы; APP-05 остаётся открытым.

SHA256 файлов в `/tmp/pgwire-input-native-probe-20261111`:

- probe.cpp: `ce14978d36a745bfa363eb4dffc14b01520618d51e25718085c3aa848387225b`;
- probe_dynamic.cpp: `16b29c868ef7ae34edc41b3ad062d5203bff14b11ee120095ee295354bc560ab`;
- run_dynamic.py: `42a832f451d1fef01267f82b3f097efb518e5b65d61e6d8c43e36fca7c2a85e9`;
- dynamic_result.log: `e28d0bbc92c339c1c422b6037eb5452843a0223bf7192732de9db407f0208a74`.

Engine SHA тот же; fbclient:
`c89d18fd00f443fef848040a144d55282ab9cb7f5dfc6086195d41d892a3c422`.
65534+ native API не отправлялись: ограничение 65533 следует из 16-битной длины
descriptor с двухбайтовым prefix. Отказ codec на превышение — обязательная
проверка будущей реализации. Эти probes подтверждают архитектуру APP-08,
а не готовность PgWire input; большие входные значения записаны как APP-09.

## 11 ноября 2026: binary BLOB output

[APP-07](1c/BINARY_BLOB_OUTPUT_TASK.md) реализован поверх
`ccd81f6473b8017ffa5fc8da1a8560fc508f5de2`, ветка `pg_wire_protocol`.
Изменения оставлены на review без git add/commit/amend/reset/checkout; исходные
документальные изменения сохранены. Production — только ResultCodec.cpp/h,
Protocol.h (getter фактического frameBytes) и четыре decode calls в Execute.cpp/
Extended.cpp. Parser/core/публичные интерфейсы/InputCodec не менялись.

SHA256 production diff
`git diff HEAD -- src/plugins/wire/pg ':!src/plugins/wire/pg/tests'`:
`a91c8ca4f4b852e418c2b813bf470f5f8c89104ce76868b4adfc8964ad6a8e13`.
SHA256 полного `git diff HEAD -- src`, включая финальные units:
`a16ef02b82ca794a75a1efce1e3a484a2cc2922f76390f9d9b1a53303fb3f639`.
Аудит сохранён в `/tmp/firebird-pgwire-1c-build/blob-source-audit.json`.

До правки `python3 -B /tmp/firebird-pgwire-blob-baseline.py` подтвердил metadata
refusal 0A000 для `SELECT B FROM BLOB_BASELINE WHERE ID=1`. Native fixture BINARY
содержала `005c27ffc080`, OCTET_LENGTH вернул 6. Отчёт/логи сохранены в
`/tmp/firebird-pgwire-blob-baseline-01`; сервер завершился 0 без forced kill.
Baseline PgWire SHA:
`56302018b7cec2c643d90e99570f56bbdee1c795b8bfcd730ae07a2830244ced`.

Сборка Debug/Linux/Super в `/tmp/firebird-pgwire-1c-build`:

```sh
make -j2
make -C gen -j2 TARGET=Debug pgwire_tests
```

Логи `blob-build-{01,02}.log`, `blob-unit-build.log` и
`blob-unit-build-{02,03}.log`. После финального расширения unit coverage
повторно прошёл ResultCodec executable. Итоговый PgWire SHA256:
`b38d3389ce8235b31bc2c3f89a1fa6ad5b41f4192bf2a99075f3b587f069a5ee`.
Engine SHA256 остался baseline:
`36de1e2dcf6320439195e79efa153b4f18acace6a1b1326ab46e0ec4d3bf4cb9`.
`readelf -d` (`blob-readelf.log`) сравнен с baseline runtime: DT_NEEDED прежний,
libfbclient, tommath, tomcrypt, m, gcc_s, c, dynamic loader; libpq/новых зависимостей
в PgWire нет. Зависимости не устанавливались, grammar regeneration не требовалась.

| Проверка | Результат |
|---|---|
| Девять pgwire unit executables | PASS: Protocol, ErrorStatus, ResultCodec, Input 154, Float 112, Numeric 502, Temporal 313, SQL 575, Session 5 |
| wire_socket_test | PASS: shutdown, ownership, TCP backpressure |
| Native OO fixture/readback | PASS: 14 строк, новый attachment после commit, 8853 чтения, 8590 partial segments; stream ID 5 подтверждён getInfo |
| `--blobs`, финальный `output-04` | PASS: 90 libpq checks, 31 PQunescapeBytea round-trip с PQfreemem, 9 native observations |
| Raw BLOB | PASS: 7 групп — Describe/Bind без application transaction, mixed formats NULL/empty, independent portals/resume/close/rebind, shared budget, exact text boundary, later-row failures, unsupported forms/discard/Sync recovery |
| `pgwire_codec_smoke.py` | PASS: 8 групп прежних scalar codecs |
| Simple / Extended | PASS: 12 / 16 групп, C libpq, psycopg2, raw wire и прежний no-server-network-hop check |
| `--casts` | PASS: 129 checks, 35 raw refusals/recovery, 13 native observations; negative BLOB заменён только на SUB_TYPE TEXT |

Unit executables запускались из `gen/Debug/firebird/tests` со сборочным lib/:

```sh
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_protocol_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_error_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_result_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_input_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_float_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_numeric_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_temporal_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_sql_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_session_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/wire_socket_test
```

Их логи — `blob-pgwire_*_test.log` и `blob-wire-socket-unit.log` в build.
Для локальных TCP/socket проверок использовано разрешение sandbox; серверы/
security DB/fixtures — только новые /tmp runtime с собственными свободными портами.
TCP-команды из корня репозитория:

```sh
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --blobs --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-blob-output-04 --libpq /lib/x86_64-linux-gnu/libpq.so.5 --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_codec_smoke.py --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-blob-codecs-01 --libpq-include-dir /tmp/firebird-pgwire-1c-deps/libpq-dev/usr/include/postgresql --libpq-library /lib/x86_64-linux-gnu/libpq.so.5 --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --casts --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-blob-casts-01 --libpq /lib/x86_64-linux-gnu/libpq.so.5 --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_simple_query_smoke.py --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-blob-simple-01 --libpq-include-dir /tmp/firebird-pgwire-1c-deps/libpq-dev/usr/include/postgresql --libpq-library /lib/x86_64-linux-gnu/libpq.so.5 --driver-python /usr/bin/python3 --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_extended_query_smoke.py --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-blob-extended-01 --libpq-include-dir /tmp/firebird-pgwire-1c-deps/libpq-dev/usr/include/postgresql --libpq-library /lib/x86_64-linux-gnu/libpq.so.5 --timeout 120
```

Каждый каталог содержит summary.json и логи. Реальный libpq — 14.24;
C helpers собраны с существующими libpq headers, Python harness использует ctypes.
SHA256 1c harness:
`fb3e0c9f33a1d6e90c636843da674d45e08c97cff2f7b000ec3e39949ca1a281`;
новый `pgwire_blob_checks.py`:
`674d02872440d7499576d511d6067625f085ba48c1a76b85a8558c93327b3b06`;
новый `pgwire_blob_fixture.cpp`:
`92d3a98903ab842e23ce2c498ae0b2ddac3f8129d583edf4c305041be5d0e24d`.
Fixture helper компилируется самим `--blobs`, команда и stdout сохранены в
blob_fixture_compile.log / blob_fixture.log. Все запущенные серверы завершились
`exit_code=0`, `forced_kill=false`; codecs/Simple/Extended имеют по два запуска.
Рабочий сервер, employee, pgwire_test и исходная база 1С не затрагивались.

Проверены NULL, empty, все байты 0…255, NUL/quote/backslash/invalid UTF8,
segmented/stream и пустые сегменты, payload 10000/65537 байт и больше 2 MiB.
Значения text/binary и длины сравнены с независимым Python expected payload;
OID/size/typmod проверены libpq и raw RowDescription. Native selected06 SQL
с WHERE `$1` и ORDER BY PartNo сохранён дословно, PUBLIC.PARAMS и значения
параметра синтетические (остальные колонки fixture имеют простые numeric types).
Это не исходная схема/данные/Bind 1С.

Границы: одиночный binary payload 1048566 байт даёт frameBytes=1048576,
следующий байт отказан; text payload 524282 байта даёт тот же frame size.
Text + scalar и mixed строка из двух BLOB, scalar и NULL проверяют точный предел
и превышение ровно на один wire byte. Большой BLOB не усечён, DataRow не посылается
частично; предыдущая строка при последующей ошибке остаётся на wire. В units
Protocol frameLimit уменьшен до 15/22 байт; превышение выявляет decode, serializer
проверен независимо. Проверены EOF после заполнения бюджета и дополнительный
непустой сегмент, общий бюджет и scalar после BLOB. Для 20000-byte BLOB при малом
бюджете прочитана только одна порция до 4096 байт, затем cleanup.

Open/read/close faults инжектированы в минимальных ABI endpoints по прежнему
образцу SessionTest. Unit проверяет сохранение первичного engine error,
неожиданный result/length, отсутствие контекста, consuming close без release
и ровно один release на ошибке/превышении. Реальные engine I/O faults не
инжектировались; этот live-пробел и остальные fault scenarios сохраняются в
VAL-04. Success/overflow native lifetime проверен через fetch, singleton,
portal close, commit/rollback и завершение всех attachments. DML RETURNING
при ошибке размера в I откатывает implicit segment, в T даёт E/25P02;
Simple и Extended rollback проверены native observer. Это output error после
исполнения, в отличие от прежнего metadata refusal TEXT/прочих BLOB subtype.

Промежуточные отчёты сохранены: `output-01` остановился до старта сервера
(runs=[]), потому что fixture пыталась native CAST TEXT→subtype 2 без BLOB filter.
Для metadata-negative subtype 2 оставлен NULL; создавать unsupported conversion
не требуется. `output-02` остановился на тесте EXECUTE PROCEDURE с `$1`: прежний
root guard правильно дал 0A000, тест исправлен на native `?`. `output-03` прошёл;
в `output-04` добавлены Extended explicit T overflow/rollback и NULL input rejection.
Production для исправления этих тестов не расширялся.

Psycopg 3 live — SKIP (клиент отсутствует), остальные suites вне указанного
перечня повторно не запускались. Known native crash repro не запускались.
Исторический **P3 VARCHAR FAIL (VAL-02)** сохранён ниже, без модернизации P3.
APP-07 закрыт по реализации и ожидает review; APP-02, SQL-06, input BLOB/bytea,
SQL type/casts/literals/storage/comparisons/index semantics и открытие базы 1С
не закрываются. SET bytea_output и large-object API не добавлены.

## 11 ноября 2026: архитектурный review literal LIMIT

Проверена ревизия `ccd81f6473b8017ffa5fc8da1a8560fc508f5de2` по
[LIMIT_TASK](1c/LIMIT_TASK.md), SQL-01. Замечаний к ограниченному контракту
не обнаружено. Проверены PG-only reserved token, оба SELECT grammar пути,
области UNION/subqueries, native ROWS/OFFSET/FETCH, root guard и `!yytrial`.
Нормализованные grammar conflicts остаются прежними 158/11. Native ограничения
имён колонок body wrapper и ORDER BY у UNION не являются регрессиями LIMIT.

Рабочее дерево перед review чистое. Использована существующая Debug/Linux/Super
сборка `/tmp/firebird-pgwire-1c-build/gen/Debug/firebird`, без новой сборки.
SHA256 production diff `git diff 9f94454527 ccd81f6473 -- src`:
`077cb5380bac50d0be324fcec46f9914b6542a973c92b4810a76db4d451c8105`.
Engine `36de1e2dcf6320439195e79efa153b4f18acace6a1b1326ab46e0ec4d3bf4cb9`
и PgWire `56302018b7cec2c643d90e99570f56bbdee1c795b8bfcd730ae07a2830244ced`
совпадают с отчётом реализации.

| Независимо повторённая проверка | Результат |
|---|---|
| SQL/session/isql units | PASS: 575 / 5 / 13 |
| `--limits` | PASS: 112 libpq checks, 5 сравнений LIMIT/FIRST/ROWS, 9 native observations, 44 raw refusals/recovery, portals |
| `--strings` | PASS: 156 запросов, raw/native/cache/stored source, 1126 OO checks |

Units запускались из tests/ указанной сборки с LD_LIBRARY_PATH на её lib/.
TCP-команды из корня репозитория, с разрешённым локальным TCP:

```sh
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --limits --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-limit-review-20261111 --libpq /lib/x86_64-linux-gnu/libpq.so.5 --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --strings --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-limit-native-review-20261111 --libpq /lib/x86_64-linux-gnu/libpq.so.5 --timeout 120
```

Оба summary.json: completed=true, соответствующий passed=true, exit_code=0,
forced_kill=false, revision=ccd81f6473, пустой production diff. Runtime/security
DB/test DB отдельные; рабочие базы не затрагивались. Остальные ранее записанные
suites заново не запускались; Psycopg 3 live по-прежнему SKIP, исходные 04/17
и запуск 1С не приняты. SQL-01 закрыт, SQL-02 остаётся открытым.

Следующим выделен APP-07, [binary BLOB output](1c/BINARY_BLOB_OUTPUT_TASK.md),
для чтения BinaryData в selected06. Исследование каталогов APP-01 выявило
отсутствие numeric schema ID и отдельные вопросы PG/native имён и collation;
они сохранены в BACKLOG. В этом архитектурном диалоге изменена только документация,
новая реализация и git commit не выполнялись.

## 11 ноября 2026: literal LIMIT в PG4

[Задание SQL-01](1c/LIMIT_TASK.md) реализовано поверх
`9f94454527e0ce12f1b9d2a811d353aac80bd865`, ветка `pg_wire_protocol`.
Результат — незакоммиченный diff для review пользователя; git add/commit/amend/
reset/checkout не выполнялись. Исходные изменения документов сохранены.
Production ограничен Parser.cpp, Parser.h и parse.y: PG-only reserved token,
подтверждённый flag/root guard, RowsClause в select_expr и query_primary с
прежним DFLAG_BODY_WRAPPER. API/plugin/codecs/optimizer/streams не менялись.
SHA256 `git diff HEAD -- src`:
`077cb5380bac50d0be324fcec46f9914b6542a973c92b4810a76db4d451c8105`.

Перед правкой на отдельном runtime `/tmp/firebird-pgwire-limit-baseline-01`
подтверждены `SELECT 42 LIMIT 1` → 42601, `SELECT FIRST 1 42` и
`SELECT 42 ROWS 1` → строка 42, OID 23, SELECT 1. Старый `SELECT 1 LIMIT`
принимался как alias; новый контракт его отвергает. Baseline probe:
`python3 -B /tmp/firebird-pgwire-limit-baseline.py`; исходный summary сохранён
(поля `postfix` / `native_cast` унаследованы от probe предыдущего этапа, фактические
SQL приведены в скрипте). Baseline engine SHA
`f19fefaf762406c3629a793c650ffededd91b65293abf72ce2673554ee5dd009`.

Сборка Debug/Linux/Super — `/tmp/firebird-pgwire-1c-build`, команды из этого каталога:

```sh
make -j2
make -C gen -j2 TARGET=Debug pgwire_tests
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_sql_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_session_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/isql_test --log_level=message
```

Логи: `limit-build-01.log`, `limit-unit-build.log`, `limit-{sql,session,isql}-unit.log`.
Итоговый engine SHA256
`36de1e2dcf6320439195e79efa153b4f18acace6a1b1326ab46e0ec4d3bf4cb9`;
PgWire SHA256 остался прежним:
`56302018b7cec2c643d90e99570f56bbdee1c795b8bfcd730ae07a2830244ced`.

Standalone btyacc до/после: `python3 /tmp/limit-grammar.py baseline` и
`python3 /tmp/limit-grammar.py 01`, отчёты
`/tmp/firebird-pgwire-limit-grammar-{baseline,01}/{generate.log,y.output}`.
Оба результата — **158 shift/reduce, 11 reduce/reduce**. После удаления номеров
states/rules и подстановки текста правил все 169 конфликтов совпадают по
типу, rule и lookahead token; новых/исчезнувших нет. Нормализованный multiset —
`/tmp/firebird-pgwire-limit-conflicts.json`. В generated
`temp/Debug/dsql/parse.cpp` action `pgLimit = true` находится под `if (!yytrial)`.
Ожидаемое количество конфликтов не менялось.

| Проверка | Результат |
|---|---|
| Build и SQL/session/isql units | PASS: 575 / 5 / 13 |
| `--limits`, финальный `limits-04` | PASS: 112 libpq checks, пять сравнений LIMIT/FIRST/ROWS, 9 native observations, 44 raw refusals + discard/recovery |
| LIMIT область действия | PASS: 0/1/2/100001/MAX, integer spellings, ORDER/DISTINCT/GROUP/CTE/derived/scalar, UNION/UNION ALL и скобки |
| LIMIT DML/Extended | PASS: INSERT SELECT commit/rollback; UPDATE/DELETE/MERGE subqueries; PQexecParams/prepare/Describe/execPrepared, NULL/text/binary, maxRows/suspension и независимые portals |
| LIMIT границы | PASS: unsupported count/overflow/mixing/roots, отсутствие объектов/строк, Simple rollback, T→E/25P02, native1/2/3/AUTOTERM и preserved FIRST/ROWS/OFFSET…FETCH |
| `--parameters` | PASS: 73 checks, 29 raw refusals/recovery, 13 native observations, прежний 92-input fixture |
| `--casts` | PASS: 129 checks, 35 raw refusals/recovery, 13 native observations |
| `--strings`, финальный `strings-02` | PASS: 156 queries, stored source/raw/native/cache, 1126 OO checks, включая LIMIT dialect/cache/quoted identifiers/on/off/offsets |
| Simple / Extended | PASS: 12 / 16 групп, прямой C libpq, psycopg2 и raw protocol |

TCP-команды из корня репозитория (все каталоги перед запуском новые):

```sh
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --limits --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-limit-limits-04 --libpq /lib/x86_64-linux-gnu/libpq.so.5 --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --parameters --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-limit-parameters-01 --libpq /lib/x86_64-linux-gnu/libpq.so.5 --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --casts --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-limit-casts-01 --libpq /lib/x86_64-linux-gnu/libpq.so.5 --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --strings --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-limit-strings-02 --libpq /lib/x86_64-linux-gnu/libpq.so.5 --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_simple_query_smoke.py --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-limit-simple-01 --libpq-include-dir /tmp/firebird-pgwire-1c-deps/libpq-dev/usr/include/postgresql --libpq-library /lib/x86_64-linux-gnu/libpq.so.5 --driver-python /usr/bin/python3 --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_extended_query_smoke.py --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-limit-extended-01 --libpq-include-dir /tmp/firebird-pgwire-1c-deps/libpq-dev/usr/include/postgresql --libpq-library /lib/x86_64-linux-gnu/libpq.so.5 --timeout 120
```

В каждом каталоге сохранены summary.json и логи; libpq 14.24, psycopg2 из
`/usr/bin/python3`. Итоговый 1c tool SHA256
`fb5f22c9a73c4a1725916f1a787df058783e88ea9ce6e843e02c70df33a71df9`;
новый `pgwire_limit_checks.py` SHA256
`166bdefc3644e0d304d081bea866f05cc354eca92e6f5731e12c92a6435ac5ec`.
Серверы всех перечисленных запусков, baseline и промежуточных попыток завершились
с `exit_code=0`, `forced_kill=false`; Simple/Extended содержат по два запуска.
Использованы собственные /tmp runtime/security DB/test DB и свободные loopback
порты. Сервер пользователя, employee, pgwire_test и база 1С не затрагивались.

Промежуточные FAIL сохранены и относятся к ожиданиям новых тестов:
`limits-01` использовал ORDER BY ID у UNION, где native требует позицию;
`strings-01` ожидал колонку 33 вместо фактической 35 для последней `1`;
`limits-03` не задал alias вычисленному столбцу внутри body wrapper.
Последнее отдельно воспроизведено embedded isql с native FETCH FIRST:
42000 / «no column name specified … in derived table»; с `AS V` возвращается 99.
Финальные тесты проверяют обе native границы рядом с LIMIT и используют нужные
aliases/позиции для положительных случаев. `limits-02` также прошёл, затем
добавлены проверки scalar/parenthesized LIMIT 0 и сравнения с native границами;
окончательный полный набор — `limits-04`. Production после первой сборки
для этих корректировок тестов не менялся.

Psycopg 3 live — SKIP (не установлен; новые зависимости не добавлялись).
Остальные suites вне требуемых регрессий повторно не запускались; known native
crash repro исключены. Исторический **P3 VARCHAR FAIL** остаётся ниже без
переоценки или модернизации P3. SQL-01 закрыт по реализации с ожиданием review;
SQL-02 остаётся открытым. Полные selected 04/17 требуют каталогов/custom types;
синтетические LIMIT cases не подтверждают их приёмку, открытие базы или нагрузку 1С.

## 11 ноября 2026: архитектурный review postfix casts

Проверен результат [POSTFIX_CAST_TASK](1c/POSTFIX_CAST_TASK.md), вошедший в
`9f94454527e0ce12f1b9d2a811d353aac80bd865`. Замечаний к ограниченному контракту
не обнаружено. Проверены PG-only tokens, INT8/domain isolation, общий BIGINT
descriptor helper, precedence, подтверждённые parser actions и root guard.
Четыре новых shift/reduce conflicts соответствуют описанным ниже продолжениям
TIME/TIMESTAMP и BLOB; анализ и проверки обеих трактовок согласованы с diff.

Review начат на незакоммиченном изменении поверх `df37bb11fa`; во время проверки
появился commit `9f94454527`. SHA256 его production diff совпадает с рассмотренным:
`2a8d8675f0138b80f25e4d9d63dadb3f6cca9a5359578d531fb322449f398f04`.
Использована существующая Debug/Linux/Super сборка
`/tmp/firebird-pgwire-1c-build/gen/Debug/firebird`, без новой сборки. SHA engine
`f19fefaf762406c3629a793c650ffededd91b65293abf72ce2673554ee5dd009`
и PgWire `56302018b7cec2c643d90e99570f56bbdee1c795b8bfcd730ae07a2830244ced`
совпадают с отчётом реализации.

Независимо повторены:

| Проверка | Результат |
|---|---|
| SQL/session/isql units | PASS: 575 / 5 / 13 |
| `--casts` | PASS: 129 libpq checks, 32 пары выражений + две пары WITH LOCK, 13 native observations, 35 raw rejections/recovery |
| `--strings` | PASS: 156 запросов, raw/stored source/native/cache и 873 OO checks |

Units запускались из tests/ указанной сборки с LD_LIBRARY_PATH на её lib/;
TCP-команды из корня репозитория:

```sh
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --casts --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-cast-review-20261111-tcp --libpq /lib/x86_64-linux-gnu/libpq.so.5 --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --strings --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-cast-strings-review-20261111-tcp --libpq /lib/x86_64-linux-gnu/libpq.so.5 --timeout 120
```

Оба summary.json: completed=true, соответствующий passed=true, exit_code=0,
forced_kill=false, revision=9f94454527 и пустой production diff. Исходные попытки
в каталогах с теми же именами без `-tcp` остановились до запуска сервера:
sandbox запретил socket (PermissionError, runs=[]). Повтор с разрешённым локальным
TCP прошёл; исходные отчёты сохранены. Рабочие серверы/базы пользователя не трогались.

Psycopg 3 остаётся SKIP, полного запуска 1С нет. Остальные ранее записанные suites
в этом review заново не запускались. Ограничения native type semantics, root
contexts и исторического P3 сохраняются. Подготовлено отдельное
[задание LIMIT](1c/LIMIT_TASK.md); в архитектурном диалоге изменена только
документация, реализация следующего этапа и git commit не выполнялись.

## 10 ноября 2026: postfix casts, реализация для review

Реализован ограниченный PG4 `expression::type` через прежний CastNode.
Production-изменения: Parser.cpp, Parser.h, parse.y и обновлённый после разбора
конфликтов parse-conflicts.txt. CastNode, ParameterNode, GEN_port, MAKE_parameter,
wire production, codecs и API/flags не менялись. Общий helper BIGINT сохраняет
прежний descriptor и client/database dialect checks; INT8 alias применяется
только в postfix target. Git add/commit/amend/reset/checkout не выполнялись;
исходные изменения пользователя в документации и POSTFIX_CAST_TASK сохранены.

База: `df37bb11faa61ec9aa7e4970cbb48a3c5e667d18`, ветка `pg_wire_protocol`.
SHA256 `git diff HEAD -- src`:
`2a8d8675f0138b80f25e4d9d63dadb3f6cca9a5359578d531fb322449f398f04`.
Сборка Debug/Linux/Super: `/tmp/firebird-pgwire-1c-build`.

| Бинарник / исходник | SHA256 |
|---|---|
| Исходный libEngine14.so (`df37bb11fa`) | `5c12e32835d10e6dac40750f58ec6dd18de934210b8b01acd25cfcb594041785` |
| Новый libEngine14.so | `f19fefaf762406c3629a793c650ffededd91b65293abf72ce2673554ee5dd009` |
| libPgWire.so до и после | `56302018b7cec2c643d90e99570f56bbdee1c795b8bfcd730ae07a2830244ced` |
| pgwire_1c_smoke.py | `86592298d25c217b94ee9834b252df67c93d6fb376b3c31e542b4021f3ac533a` |
| pgwire_cast_checks.py | `1480b65cf3ebf637198db1b5bff694b272329f9c26feaf275fa1f951ad08fd18` |
| pgwire_strings_api.cpp | `d3bb82b18372fef8157b18033db6a5f49fc4e02322edabad85ee3e5247c16d7a` |
| pgwire_psycopg3.py | `4b40dcc7ac2c40e3a147bd82e55bf13c50cfd0b5fb28a79a38f09417e7820bab` |

### PASS, пропуски и результаты

Все обязательные suites прошли. Все запущенные серверы, включая промежуточные
прогоны с ошибочными тестовыми ожиданиями, завершились с `exit_code=0`,
`forced_kill=false`. У каждого runtime свои security DB, синтетическая база,
порты и логи в новом `/tmp` каталоге; `runs` в summary фиксирует shutdown.
Рабочие базы и база 1С не использовались. Новые зависимости не устанавливались.

| Проверка | Результат | Work-dir (`/tmp/firebird-pgwire-…`) |
|---|---|---|
| Исходный отказ | `SELECT 1::INT8` → 42601; `SELECT CAST(1 AS BIGINT)` → 1, OID 20 | `cast-baseline-01` |
| `make -j2` | PASS, exit 0 | build log `postfix-cast-build-01.log` |
| `pgwire_tests` / isql | PASS: SQL 575, Session 5, isql 13 | build logs `postfix-cast-*-unit.log` |
| `--casts` | PASS: 129 libpq checks, 32 пары выражений + две пары WITH LOCK, 13 native observations; 35 raw Parse rejection cases и recovery | `cast-03` |
| `--strings` | PASS: 156 запросов, raw/stored source, 873 OO checks | `cast-strings-04` |
| `--parameters` | PASS: 73 libpq checks, 13 native observations, 29 raw rejections, все 92 значения | `cast-parameters-01` |
| Extended | PASS: 16 групп, C libpq/raw; async 20 commands/20 results, 40 poll calls | `cast-extended-01` |
| Simple | PASS: 12 групп, C libpq/psycopg2/raw | `cast-simple-01` |
| `--settings` | PASS: 190 запросов, raw/native и исходный startup batch | `cast-settings-01` |
| `--begin` | PASS: 88 запросов, 43 native observations, raw/native/psql | `cast-begin-01` |
| WITH diagnostic probe | Обе формы TIME/TIMESTAMP + WITH LOCK совпали с явным CAST, 42000 | `cast-with-01` |
| Psycopg 3 | **SKIP**, модуль отсутствует; ожидание `sql.Literal(date)`/`::date` обновлено, без установки зависимостей | `cast-03/summary.json` |

Settings/BEGIN повторены дополнительно из-за нового PG-only non-reserved token
INT8. Общий keyword table и control paths не менялись. C libpq headers/runtime
14.24, Python 3.10.12, psycopg2 2.9.2; compiler/platform записаны в harness summary.

Сравнение с native CAST подтвердило INTEGER/INT, SMALLINT, BIGINT/INT8,
VARCHAR(n), BOOLEAN, DOUBLE PRECISION, NULL, column/CTE/subquery, case-insensitive
INT8. `1.2 + 1.6::int` → 3.2, `(1.2+1.6)::int` → 3; `-32768::SMALLINT` → 22003,
`(-32768)::SMALLINT` → −32768. Промежуточный overflow в цепочке сохраняет 22003,
невалидное значение — 22018, truncation — 22001. CONCATENATE/COLLATE/AT,
TIMESTAMP/TIME WITH TIME ZONE внутри поддержанного выражения, BLOB внутри
OCTET_LENGTH, native NUMERIC defaults и bare VARCHAR проверены относительно
явного CAST. Bare VARCHAR сохраняет 255 characters, без unlimited PG semantics.

Настоящие PQexecParams и PQprepare/PQdescribePrepared/PQexecPrepared подтвердили
input OID=23/output=1043 для `$1::INTEGER::VARCHAR(12)`, несколько типов,
повторные Bind, NULL, UTF8 и text/binary inputs/results (binary bytes в отчёте — hex).
Omitted/zero/compatible OID работают; incompatible — прежний 0A000.
`SELECT $1` с supplied OID=23 остаётся HY004. Raw также проверяет bad Bind 22P02,
conversion Execute 22018, discard-until-Sync и восстановление. Simple typed input
остаётся 0A000, повтор/gap/reorder/mixed `$n` не становятся допустимыми.

INSERT/UPDATE/DELETE/MERGE/RETURNING с casts, BEGIN/commit/rollback и ошибка
преобразования в T проверены native observer: реальные values, отсутствие
uncommitted/rolled-back rows, E/25P02 и последующий recovery. Simple script
останавливается на cast error и откатывает текущий сегмент.

Root guard отказал CTAS (в том числе CTE), CHECK, VIEW/subquery, stored PROCEDURE,
EXECUTE BLOCK/local function, CALL, EXECUTE PROCEDURE, USING и UPDATE OR INSERT;
неверная grammar остаётся 42601. Объектов `CAST_BAD*` и строк после отказов нет.
Прежний CAST в этих контекстах работает, включая native OO USING; literal `::`
разрешён в stored source. Dollar/parameter constraints, unsupported BLOB output,
unknown/custom/quoted/schema/TYPE OF/array targets сохранены без подмены типов.

Native1/2/3 отвергают настоящий `::`, принимают CAST, INT8 identifiers и PSQL
`:variable`; AUTOTERM выполняет следующий SELECT после ошибки. OO подтверждает
PG→native→PG/cache на одном attachment, сохранение domain INT8 в обычном CAST
(quoted domain — dialects 3/4), исходные line/column при обоих string flags,
прежний отказ native client dialect 1 для BIGINT (HY000). PG postfix INT8 остаётся
BIGINT даже при существующем VARCHAR-домене INT8. Общий helper содержит также
неизменённую проверку database dialect; отдельная dialect-1 БД здесь не создавалась.

### Разбор grammar conflicts: 154/11 → 158/11

Новые конфликты не приняты вслепую. Verbose btyacc выполнен со штатными `types.y`
и `y.y` до/после; артефакты `/tmp/firebird-pgwire-cast-grammar-baseline/y.output`
и `/tmp/firebird-pgwire-cast-grammar-01/y.output`. Нормализованное сравнение
reduce productions, tokens и количества в
`/tmp/firebird-pgwire-cast-grammar-comparison.json` показывает ровно четыре
добавленных shift/reduce, **ни одного удалённого**, без новых reduce/reduce:

| Token / production | Причина | Проверка обеих трактовок |
|---|---|---|
| WITH / пустой `without_time_zone_opt`, два состояния TIME/TIMESTAMP | `WITH TIME ZONE` продолжает native type; `WITH LOCK` начинает SELECT clause | WITH TIME ZONE работает внутри выражения; WITH LOCK доходит до прежнего engine отказа 42000 для SELECT без physical table, как явный CAST |
| SUB_TYPE / пустой `blob_subtype` | Продолжение BLOB type либо implicit alias SUB_TYPE | BLOB SUB_TYPE TEXT и BLOB с alias SUB_TYPE, обёрнутый в OCTET_LENGTH, возвращают 3 |
| SEGMENT / пустой `blob_segsize` | SEGMENT SIZE либо implicit alias SEGMENT | BLOB SEGMENT SIZE 80 и BLOB с alias SEGMENT возвращают ту же длину |

Это применение существующего `non_array_type` в новой позиции выражения;
штатный backtracking выбирает завершённую grammar branch. Список native типов
и их optional productions не дублируется/не ограничивается, native precedence
не меняется. Поэтому после разбора и runtime проверок parse-conflicts.txt
обновлён на **158 shift/reduce, 11 reduce/reduce**.

Два конфликта COLLATE в `simple_type`/`non_charset_simple_type` уже были в baseline.
Повторное использование этих productions добавляет новый контекст, но не новый
конфликт. Native type grammar может поглотить COLLATE в target descriptor;
проверены явный outer COLLATE, character set/collation target, repeated COLLATE,
cast поверх collated expression и concat с collated правым операндом. Результаты
совпадают с явным CAST. В частности, collation правого аргумента не меняет
native collation левого аргумента concat: сравнение `'a'||'b'... = 'AB'` даёт false.
Generated postfix action защищён `if (!yytrial)`; признак root guard и создание
CastNode не исполняются на trial/lookahead. Оператор имеет `%left DOUBLE_COLON`
после AT, а результат/ошибки unary, arithmetic, concat, COLLATE и AT подтверждены.

### Команды

В `/tmp/firebird-pgwire-1c-build`:

```sh
make -j2 > postfix-cast-build-01.log 2>&1
make -C gen -j2 TARGET=Debug pgwire_tests > postfix-cast-units-build-01.log 2>&1
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_sql_test > postfix-cast-sql-unit.log 2>&1
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_session_test > postfix-cast-session-unit.log 2>&1
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/isql_test --log_level=message > postfix-cast-isql-unit.log 2>&1
```

Из репозитория (переменные сокращают фактические пути; work-dir должен быть новым):

```sh
CAST_RUNTIME=/tmp/firebird-pgwire-1c-build/gen/Debug/firebird
CAST_LIBPQ=/lib/x86_64-linux-gnu/libpq.so.5
CAST_HEADERS=/tmp/firebird-pgwire-1c-deps/libpq-dev/usr/include/postgresql
CAST_CAPTURE=/home/aleksey/dev/1c/captures/20261109T105635Z
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --casts --firebird-dir "$CAST_RUNTIME" --work-dir /tmp/firebird-pgwire-cast-03 --libpq "$CAST_LIBPQ" --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --strings --firebird-dir "$CAST_RUNTIME" --work-dir /tmp/firebird-pgwire-cast-strings-04 --libpq "$CAST_LIBPQ" --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --parameters --firebird-dir "$CAST_RUNTIME" --work-dir /tmp/firebird-pgwire-cast-parameters-01 --libpq "$CAST_LIBPQ" --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_extended_query_smoke.py --firebird-dir "$CAST_RUNTIME" --work-dir /tmp/firebird-pgwire-cast-extended-01 --libpq-include-dir "$CAST_HEADERS" --libpq-library "$CAST_LIBPQ"
python3 -B pg_wire_protocol/tools/pgwire_simple_query_smoke.py --firebird-dir "$CAST_RUNTIME" --work-dir /tmp/firebird-pgwire-cast-simple-01 --libpq-include-dir "$CAST_HEADERS" --libpq-library "$CAST_LIBPQ" --driver-python /usr/bin/python3
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --settings --firebird-dir "$CAST_RUNTIME" --work-dir /tmp/firebird-pgwire-cast-settings-01 --capture-dir "$CAST_CAPTURE" --libpq "$CAST_LIBPQ" --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --begin --firebird-dir "$CAST_RUNTIME" --work-dir /tmp/firebird-pgwire-cast-begin-01 --capture-dir "$CAST_CAPTURE" --libpq "$CAST_LIBPQ" --timeout 120
```

Baseline/diagnostic probes использовали прежние OneCHarness/Libpq, отдельные
runtime и scripts `/tmp/firebird-pgwire-cast-baseline.py` /
`/tmp/firebird-pgwire-cast-with-probe.py`; новый клиент/framework не создавался.
Python source syntax и `git diff --check` проверены.

### Промежуточные FAIL и границы

`cast-01` **FAIL**: тест ошибочно ожидал true от concat с collation только правого
аргумента; фактический native CAST даёт false, ожидание исправлено. `cast-strings-01`
**FAIL**: quoted domain ошибочно проверялся в dialect 1, где double quotes имеют
другое значение; проверка quoted domain ограничена dialects 3/4, unquoted INT8
сохранена во всех 1/2/3/4. Production после первой успешной сборки не менялся.
`cast-02` PASS: 125 checks; `cast-03` добавил четыре проверки WITH LOCK.
`cast-strings-02` PASS: 863 checks; `-03` добавил native BIGINT/dialect-1 отказ
(872), `-04` добавил native USING с обычным CAST (873). Все серверы exit 0,
без forced kill. Эти промежуточные каталоги сохранены.

Исторический P3 VARCHAR FAIL из предыдущего этапа **не перезапускался и не
исправлялся**: модернизация P3 не относится к заданию. Known native crash repro
(nested SAVEPOINT/RESET, ON CONNECT shutdown, READ CONSISTENCY + AUTO COMMIT)
и нагрузка 1С не запускались. Нет новых codecs, PG coercion/typmods,
custom/schema/domain/array postfix targets, serializer или расширения internal
prepare. Исходные 04/08/10/11 с каталогами/regnamespace/mvarchar/advisory locks/LIMIT
не выполнялись и не объявлены совместимыми; запуск 1С не проверен. Изменения
оставлены для review без commit.

## 10 ноября 2026: архитектурный review последовательных `$n`

Проверена ревизия `df37bb11faa61ec9aa7e4970cbb48a3c5e667d18` по
[NUMBERED_PARAMETERS_TASK](1c/NUMBERED_PARAMETERS_TASK.md). Замечаний к реализации
ограниченного контракта не обнаружено. Проверены lexer boundaries/overflow,
подтверждённые parser actions, root guard до DSQL transformations, прежний
ParameterNode/Bind и ограничения inference. Production diff затрагивает только
Parser.cpp, Parser.h и parse.y; API, codecs, GEN_port и MAKE_parameter не изменены.

Для повторной проверки использована уже собранная Debug/Linux/Super версия
из `/tmp/firebird-pgwire-1c-build/gen/Debug/firebird`. Новая сборка не выполнялась:
SHA engine/PgWire совпадают с приведёнными в следующем разделе, а SHA256
`git diff 97e44d0188 df37bb11fa -- src` равен записанному там production diff.
Рабочее дерево перед review было чистым. Независимо повторены:

| Проверка | Результат |
|---|---|
| SQL/session/isql units | PASS: 575 / 5 / 13 |
| `--parameters` | PASS: 73 libpq checks, 13 native observations, 29 raw rejections с recovery; все 92 значения прочитаны native observer |
| `--strings` | PASS: 156 запросов, raw/native/cache/stored source, 546 OO checks |

Команды units — как в следующем разделе. TCP-команды из корня репозитория:

```sh
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --parameters --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-params-review-01 --libpq /lib/x86_64-linux-gnu/libpq.so.5 --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --strings --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --work-dir /tmp/firebird-pgwire-params-native-review-01 --libpq /lib/x86_64-linux-gnu/libpq.so.5 --timeout 120
```

Оба summary.json содержат completed=true, соответствующий passed=true,
exit_code=0 и forced_kill=false. Реальный libpq 14.24; Psycopg 3 в окружении
отсутствует и остаётся SKIP. Остальные ранее записанные suites заново не запускались.
Рабочие базы пользователя и база 1С не затрагивались.

Исторический P3 FAIL означает устаревшую проверку: `pgwire_libpq_query.c`
ожидает 0A000 для `SELECT CAST('x' AS VARCHAR(1)) FROM RDB$DATABASE`, но поддержка
VARCHAR уже есть. Приложенные baseline/current probes возвращают одинаковые
`x`, OID 1043 и SELECT 1. Здесь «исходный engine» — **ревизия `97e44d0188`
до добавления `$n`**, не чистый master. Совпадение результатов исключает именно
регрессию этого запроса от `$n`; оно не превращает старый P3-набор в PASS.
Проверка и её отчёты изучены; повторный исторический P3 в этом review не запускался.

Подготовлено отдельное [задание на postfix casts](1c/POSTFIX_CAST_TASK.md).
В архитектурном review изменена только документация; реализация нового этапа
и git commit не выполнялись.

## 10 ноября 2026: последовательные `$1` … `$n`, реализация для review

Первый срез реализован: PG4 lexer/parser распознаёт уникальные последовательные
inputs в корневых SELECT/INSERT/UPDATE/DELETE/MERGE и использует прежние
ParameterNode/metadata/Bind. Production diff — только Parser.cpp, Parser.h,
parse.y; MAKE_parameter, GEN_port, Extended, InputCodec, API/flags не изменены.
Новые зависимости не устанавливались. Git add/commit/amend/reset/checkout
не выполнялись; прежние изменения пользователя в документации сохранены.

База: `97e44d0188020177b18c2a713880796890e13a17`, ветка `pg_wire_protocol`.
SHA256 `git diff HEAD -- src`:
`f161f3f1ea387c4194bf525d29dcebef294555a376d6a124c8f06a7f00d2bad2`.
Сборка Debug/Linux/Super: `/tmp/firebird-pgwire-1c-build`.

| Бинарник / исходник | SHA256 |
|---|---|
| Исходный libEngine14.so | `c0832e0b14dd5513a25ccff22c772c3dc9240d950d4a31ed0f3ca20f791f2304` |
| Новый libEngine14.so | `5c12e32835d10e6dac40750f58ec6dd18de934210b8b01acd25cfcb594041785` |
| libPgWire.so до и после | `56302018b7cec2c643d90e99570f56bbdee1c795b8bfcd730ae07a2830244ced` |
| pgwire_1c_smoke.py | `5f5235dbb1f26a0ffd3e0baacb7dc3de1fd9e03af8e6b5d68b4b54545160ac3b` |
| pgwire_parameters_checks.py | `e8100b552f07babf0d76aa547add052bf84911f4c809dc302f0730bf709b7469` |
| pgwire_strings_api.cpp | `70cfd0a21dfb9ed39956004cb526ac4fec1ba0b809862c1d0f07e8c075440edb` |

### Проверенные результаты

Все обязательные проверки PASS. У каждого запущенного сервера в перечисленных
наборах `exit_code=0`, `forced_kill=false`; runtime/security DB/fixtures находятся
в отдельных `/tmp` каталогах. Поля `runs` в summary подтверждают shutdown,
а parent harness проверяет native recovery и отсутствие оставшихся attachments.

| Набор | Фактический результат | Work-dir (`/tmp/firebird-pgwire-…`) |
|---|---|---|
| Исходный отказ | Typed `SELECT CAST($1 AS INTEGER)` → 42601; native unused CTE с `?` описывает `[23,23]` | `params-baseline-01` |
| `make -j2` | PASS, exit 0; parser 154 shift/reduce, 11 reduce/reduce | build log `numbered-parameters-build-01.log` |
| `pgwire_tests` / isql | PASS: SQL classification 575, Session restoration 5, isql 13 | build logs `numbered-parameters-*-unit.log` |
| `--parameters` | PASS: 73 libpq checks, 13 native observations, 29 raw Parse rejections с recovery; дополнительно Describe/portals/bad Bind/leading zeros/string modes | `params-08` |
| `--settings` | PASS: 190 запросов, raw/native; исходный startup batch | `params-settings-01` |
| `--begin` | PASS: 88 запросов, 43 native observations; raw/native/psql | `params-begin-01` |
| `--strings` | PASS: 156 запросов, raw; OO API 546 checks с новым native/cache/offset покрытием | `params-strings-02` |
| Extended | PASS: 16 групп, C libpq/raw; async 20 commands/20 results, 40 poll calls | `params-extended-02` |
| Simple Query | PASS: 12 групп, C libpq/psycopg2/raw | `params-simple-01` |
| Startup | PASS: 49 групп, включая заменённый syntax-negative `$1foo` | `params-startup-01` |
| Psycopg 3 | **SKIP**, модуль не установлен; ожидание typed `%s` обновлено, новые зависимости не ставились | отсутствие подтверждено в `params-08/summary.json` |

Реальные PQexecParams и named PQprepare/PQdescribePrepared/PQexecPrepared
подтвердили native CAST/column/DML inference, SMALLINT/BIGINT/VARCHAR/BOOLEAN,
UTF8, NULL и empty text, text/binary Bind, повторное исполнение. Raw Describe
содержит input OIDs `[23,1043]`; два portals с разными values исполняются в обратном
порядке без смешения. Ошибки Bind count/excess Parse OIDs дают 08P01,
incompatible OID — 0A000, bad text/binary payload — 22P02/22P03, recovery работает.

Синтетический INSERT с `$1` … `$92` описывает ровно 92 input slots; native isql
проверил все 92 разные значения `10000 + 37*i` по каждой колонке. Поиск
`FILENAME=$1` реально возвращает синтетическую строку. Это формы selected 19/06,
не исходная схема/типы BinaryData/bytea, не replay записанного Bind и не открытие
базы 1С. Неиспользуемый CTE сохраняет два inputs, результат использует второй Bind;
used CTE/subquery/WHERE EXISTS, MERGE insert/update, UPDATE/DELETE RETURNING
не сдвигают входы. BEGIN/DML/COMMIT и ROLLBACK проверены независимым native observer;
ошибка в T даёт E, последующая команда — 25P02, rollback восстанавливает I и данные.

Повтор/gap/reorder/смешение `?`/`$n` отказаны с 0A000. `$0`/`$000` — 42P02;
`$32767` первым номером — 0A000 (диапазон допустим, последовательность нет),
`$32768` и 200 девяток — 54000. `$01`, `$0002` и сто ведущих нулей работают.
Identifier suffix `$1foo`/`$1_foo`/`$1$foo` — 42601. Bare `$`, `$-1`, `$ 1`,
dollar quotes не добавлены. Comments/quoted identifiers/ordinary/Q strings при
on/off не создают inputs; A$1 и RDB$DATABASE сохраняются.

Guard отклоняет распознанные CALL с output/named arguments, EXECUTE PROCEDURE,
EXECUTE BLOCK header/body, USING, VIEW/PROCEDURE DDL, UPDATE OR INSERT до DSQL.
Неверная grammar остаётся 42601. Native `?` CALL сохраняет compacted input count=1
при input/output аргументах, named CALL/PROCEDURE/BLOCK и UPDATE OR INSERT работают;
USING с `?` принимается native OO, но остаётся вне wire execution с прежним 0A000.
Stored PSQL/DDL с literal `$1` и локальная функция с синтетическими параметрами
работают. Отброшенные после ошибки команды и отвергнутые DDL не оставляют объектов/строк.

Native dialects 1/2/3 отвергают `$n` с engine 42000 и принимают `?`/A$1.
На одном OO attachment/cache проверено PG→native→PG; исходные line/column
сохранены для suffix/zero/overflow/sequence и токена после `$01` при ON/OFF flags.
Wire mapping syntax → 42601 не менялся. В generated parser root/parameter actions
защищены `if (!yytrial)`; настоящие `?` отмечаются в production, make_parameter()
синтетических локальных функций не изменён. DSQL checkUnusedCTEs уже сохраняет
unused input slots, поэтому GEN_port не требует изменений.

`SELECT $1` без SQL-контекста даёт **HY004**, как `SELECT ?`, с omitted OID и
supplied OID=23. Это прежний inference limit, без text fallback/нового prepare API.
Simple typed `$1` даёт 0A000 и не выполняется с неявным NULL. Предел 32767 не
обещает поддержку engine message такого размера. Повторы/перестановки не реализованы.

### Команды

Сборка и units (рабочий каталог `/tmp/firebird-pgwire-1c-build`):

```sh
make -j2 > numbered-parameters-build-01.log 2>&1
make -C gen -j2 TARGET=Debug pgwire_tests > numbered-parameters-units-build-01.log 2>&1
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_sql_test > numbered-parameters-sql-unit.log 2>&1
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_session_test > numbered-parameters-session-unit.log 2>&1
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/isql_test --log_level=message > numbered-parameters-isql-unit.log 2>&1
```

TCP harness (рабочий каталог репозитория; переменные сокращают повторение
фактических путей, каждый work-dir должен быть новым):

```sh
PARAM_RUNTIME=/tmp/firebird-pgwire-1c-build/gen/Debug/firebird
PARAM_HEADERS=/tmp/firebird-pgwire-1c-deps/libpq-dev/usr/include/postgresql
PARAM_LIBPQ=/lib/x86_64-linux-gnu/libpq.so.5
PARAM_CAPTURE=/home/aleksey/dev/1c/captures/20261109T105635Z
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --parameters --firebird-dir "$PARAM_RUNTIME" --work-dir /tmp/firebird-pgwire-params-08 --libpq "$PARAM_LIBPQ" --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --settings --firebird-dir "$PARAM_RUNTIME" --work-dir /tmp/firebird-pgwire-params-settings-01 --capture-dir "$PARAM_CAPTURE" --libpq "$PARAM_LIBPQ" --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --begin --firebird-dir "$PARAM_RUNTIME" --work-dir /tmp/firebird-pgwire-params-begin-01 --capture-dir "$PARAM_CAPTURE" --libpq "$PARAM_LIBPQ" --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --strings --firebird-dir "$PARAM_RUNTIME" --work-dir /tmp/firebird-pgwire-params-strings-02 --libpq "$PARAM_LIBPQ" --timeout 120
python3 -B pg_wire_protocol/tools/pgwire_extended_query_smoke.py --firebird-dir "$PARAM_RUNTIME" --work-dir /tmp/firebird-pgwire-params-extended-02 --libpq-include-dir "$PARAM_HEADERS" --libpq-library "$PARAM_LIBPQ"
python3 -B pg_wire_protocol/tools/pgwire_simple_query_smoke.py --firebird-dir "$PARAM_RUNTIME" --work-dir /tmp/firebird-pgwire-params-simple-01 --libpq-include-dir "$PARAM_HEADERS" --libpq-library "$PARAM_LIBPQ" --driver-python /usr/bin/python3
python3 -B pg_wire_protocol/tools/pgwire_startup_smoke.py --firebird-dir "$PARAM_RUNTIME" --work-dir /tmp/firebird-pgwire-params-startup-01
```

C libpq headers/runtime 14.24, Python 3.10.12, psycopg2 2.9.2, psql 18.6;
версии C driver и compiler записаны существующими harness. OO helper собирается
внутри `--strings`. Python source syntax и `git diff --check` также проверены.

### Отрицательные результаты и границы

Дополнительный исторический `pgwire_query_smoke.py` (**не** обязательный текущий
Simple/Extended acceptance) завершился **FAIL** в C P3-наборе: он всё ещё ожидает
0A000 для `SELECT CAST('x' AS VARCHAR(1)) FROM RDB$DATABASE`, тогда как VARCHAR
давно поддержан. Рабочий каталог `params-query-01`, команда как для Simple выше,
но `pgwire_query_smoke.py` и этот work-dir. Вне `$n` его старые ожидания не менялись.
Отдельные наблюдения на исходном/new engine подтвердили одинаковый успех и `x`:
`params-p3-baseline-02` / `params-p3-current-01`, серверы exit 0, без forced kill.
Для исходного engine использован сохранённый libEngine14.so с указанным выше SHA
и новая security DB; начальная попытка скопировать уже использованный runtime
(`params-p3-baseline-01`) отказала на bootstrap до запуска TCP из-за существующего
тестового пользователя. Это ошибка подготовки probe, не engine regression.

Промежуточные `params-01` … `params-06` сохранены как **FAIL**: исправлялись только
тестовые ожидания/fixtures (чтение пустой NAME native observer, квалификация MERGE
RETURNING, фактический HY004, `:A` в USING и его прежний wire 0A000, tag UPDATE OR
INSERT). Production после первой сборки не менялся. `params-07` PASS; `params-08`
добавил binary UTF8/NULL и repeated INSERT и также PASS. `params-strings-01` PASS
с 545 OO checks; `-02` добавил native USING и дал 546. `params-extended-01` PASS,
но его C JSON ещё содержал устаревшую метку `$n=42601`; `-02` проверяет исправленный
отчёт `typed_numbered_parameter=true`, `untyped_parameter_sqlstate=HY004`.
Все запущенные серверы промежуточных прогонов завершились с exit 0 без forced kill.

Известные crash repro (nested SAVEPOINT/RESET, ON CONNECT shutdown,
READ CONSISTENCY + AUTO COMMIT) не запускались. Реальные эффекты трёх ignored SET,
`::`, LIMIT, произвольная PG-нумерация, новые codecs/OID inference и полный запуск
1С остаются отложенными. Результат передан на review, без commit.

## 10 ноября 2026: review ignored SET и PG BEGIN

Проверены `1f9af20db4` и `97e44d0188020177b18c2a713880796890e13a17`
по заданиям IGNORED_STARTUP_SETTINGS_TASK/BEGIN_TASK. Замечаний не обнаружено.
Три SET действительно не применяют и не сохраняют значения; старые проверки
настроек/native dialects сохранены. PG BEGIN использует SetTransactionNode;
promotion/повтор сохраняют handle, portals, savepoints и string history.
Отказ E проверяется до общего control(); native SET TRANSACTION не изменён.
Guard грамматики не создаёт ложного command_end_err2; generated parser проверен.
Новых API/ABI, metadata, состояния или зависимостей нет.

Независимо повторены на чистом HEAD:

| Проверка | Результат |
| --- | --- |
| Classifier / SessionTest / isql | **575 checks / 5 scenarios / 13 cases PASS** |
| --settings с capture | **190 libpq queries PASS**, полный пакет семи SET, raw/Extended/native1/2/3 |
| --begin с capture | **88 libpq queries, 43 native observations PASS**, Simple/Extended promotion, повтор BEGIN, история, suspended portal, EOF rollback, native1/2/3 и psql AUTOCOMMIT off |

Отчёты: `/tmp/firebird-pgwire-startup-settings-review-01/summary.json` и
`/tmp/firebird-pgwire-begin-review-01/summary.json`. Оба частных сервера завершились
exit 0 без forced kill. Рабочие базы и сервер пользователя не менялись.
Повторная сборка не выполнялась: SHA Engine14/PgWire совпали с приёмкой BEGIN ниже;
`git diff 1f9af20db4 HEAD -- src` также совпал с её source diff SHA
`a1077950ff040246be9685b1c6ab0326a012a45a1d2c738da899cf5acb168bd8`.
Предыдущие отчёты --strings/широких suites не заменяются этим прогоном;
они повторно не запускались. Исходники реализации в review не изменены.

Подготовлена [инструкция первого среза numbered parameters](1c/NUMBERED_PARAMETERS_TASK.md):
последовательные уникальные номера и типы из SQL-контекста, без нового API.
Этот следующий этап ещё не реализован.

## 10 ноября 2026: PG BEGIN через штатные транзакции

Выполнено [BEGIN_TASK.md](1c/BEGIN_TASK.md) на базе
`1f9af20db4bbf9022d0eb791bfa5db4f085f4836`. Изменения оставлены на review;
git add/commit/amend/reset/checkout не выполнялись. Три ignored startup SET
сохраняют прежний контракт без эффекта; следующие этапы не начинались.

Production diff ограничен четырьмя файлами: parse.y, SqlInfo.h/.cpp, Execute.cpp.
Новая корневая PG4 production создаёт прежний SetTransactionNode с isoLevel
READ_COMMITTED_READ_CONSISTENCY, readOnly=false и wait=true; прочие TPB options
не заданы. Native SET TRANSACTION/default SNAPSHOT не меняется. Guard вызывает
штатный yyerror_detailed с token error; позднего YYERROR и нового lexer gate нет.
Сгенерированный parse.cpp подтверждает: guard исполняется и в trial, а создание
node — только под `if (!yytrial)`. Parser baseline остаётся **154/11**.

Один приватный PgStart классифицирует BEGIN/START TRANSACTION; оба вида start
проверяются через прежний isc_info_sql_stmt_start_trans. Общий control() при
существующем handle сохраняет его и переводит I→T либо оставляет T. Без handle
выполняется подготовленный engine start. E блокируется прежним 25P02 до control().
Новых API/ABI, AST nodes, attachment fields, истории, конфигурации, rewriting или
зависимостей нет. Portals/savepoints/string history не сбрасываются при promotion.

### Baseline и проверенные эффекты

На сохранённых бинарниках коммита `1f9af20db4` исходный `09-begin.sql` дал
**42601/I**, затем recovery SELECT успешен. Отчёт:
`/tmp/firebird-pgwire-begin-baseline-02/summary.json`, сервер exit 0.
Engine14/PgWire SHA совпадают с приёмкой ignored SET ниже. SHA исходного запроса:
`bb93df2c6b017c587ca5880bb37f85fbe6a5c95e2fcdd6d2f40257fd926162bb`.

Первый baseline-probe (`begin-baseline-01`) не дождался готовности listener:
connection refused, SQL ещё не проверялся, остановка сервера до инициализации
не прошла clean-shutdown assertion harness. Во втором probe использовано
прежнее Client.login с ожиданием готовности; старые модули взяты из первого
частного runtime. Это исправление запуска probe, не production-кода.

Итоговый `--begin` расширяет существующий 1c harness; функции проверок вынесены
в pgwire_begin_checks.py по образцу strings checks. Использованы прежние Libpq,
ExtendedClient и native Harness.isql, без нового клиента/framework.

| Проверка | Результат |
|---|---|
| Все четыре формы, case/comments, неизменённый capture | BEGIN/T; native observer видит одну transaction, isolation=4, read-only=0, lock timeout=-1 (WAIT), auto-commit=0, auto-undo=1 |
| INSERT/UPDATE/DELETE, COMMIT/ROLLBACK | Незавершённые изменения невидимы native observer; commit публикует их, rollback отменяет |
| Два соединения | После commit UPDATE второго соединения следующий SELECT в PG BEGIN видит новое значение; native SET TRANSACTION без опций сохраняет старое значение и isolation=1 (SNAPSHOT) |
| Simple promotion | INSERT/SELECT ID до BEGIN и SELECT ID после — один ID; observer видит ровно этот handle; rollback отменяет и раннюю вставку, commit публикует её |
| Повтор / E | Повтор четырёх форм в T сохраняет ID/savepoint; rollback-to и rollback отменяют ожидаемые строки. Native повтор SET TRANSACTION — 25001/E; BEGIN в E — 25P02 |
| Simple script | Ранний COMMIT сохраняется, последующий implicit INSERT откатывается при 22012, остаток не выполняется; BEGIN; error; ROLLBACK оставляет E |
| Строковая история | SET off до BEGIN с DML и без native handle, повтор BEGIN, ошибка/rollback/savepoint recovery сохраняют исходную историю |
| Extended | Named Parse/Bind оставляют I и ноль transactions; Execute/Sync — T; reuse после rollback. DML→BEGIN до Sync сохраняет ID и видимость. Suspended SELECT portal продолжает чтение после promotion/Sync; discard-until-Sync сохранён |
| Disconnect | Libpq close и отдельный raw TCP EOF без Terminate откатывают открытый PG BEGIN; после cleanup ноль transactions, строки отсутствуют |
| Native1/2/3 | Четыре формы дают 42000; SELECT непосредственно после каждой ошибки AUTOTERM выполняется. PSQL BEGIN/END, DDL START WITH и native SNAPSHOT сохранены |
| Options | ISOLATION LEVEL, READ ONLY/WRITE, DEFERRABLE и лишние слова дают 42601; native observer подтверждает отсутствие частичного start |
| psql / psycopg2 | psql 18.6 с AUTOCOMMIT off выполняет SELECT/DML/ROLLBACK; обновлённые driver-generated BEGIN и context commit/rollback проходят в pytest |

### Сборка и регрессии

Linux x86-64, GCC 13.4.0, Python 3.10.12, прежний developer/Debug configure с
builtin tommath/tomcrypt; C libpq 14.24, системный psycopg2 2.9.2.

| Прогон | Фактический результат / work-dir в /tmp |
|---|---|
| Full make / unit build | PASS; build-dir: begin-build-01.log, begin-unit-build-01.log |
| Classifier / SessionTest / isql | **575 checks / 5 scenarios / 13 cases PASS**; begin-units.json |
| 1c --begin | **PASS, 88 libpq queries, 43 native observations**, плюс raw/native/psql; firebird-pgwire-begin-02 |
| 1c --settings | **PASS, 190 queries** и прежний полный startup batch; firebird-pgwire-begin-settings-01 |
| 1c --strings | **PASS, 156 queries, 350 OO API checks** и raw/source regressions; firebird-pgwire-begin-strings-01 |
| Transaction | **PASS, 14 групп**, включая C libpq/E/25P02/native controls; firebird-pgwire-begin-transactions-01 |
| Extended | **PASS, 16 групп**, включая C PQexecParams BEGIN/T/ROLLBACK; firebird-pgwire-begin-extended-01 |
| Simple | **PASS, 12 групп**, включая C libpq BEGIN/T/ROLLBACK; firebird-pgwire-begin-simple-01 |
| Driver acceptance | **58 pytest cases PASS, 24.31 s** через существующий ServerHarness.driver_acceptance; firebird-pgwire-begin-driver-01 |
| Статические проверки | Python AST, git diff --check и strict C syntax check старого pgwire_libpq_query.c PASS |

Все серверы итоговых прогонов завершились **exit 0, forced_kill=false**.
Первый `begin-01` также PASS; финальный `begin-02` добавляет отдельный EOF case.
Перед transaction-прогоном уточнён устаревший путь libpq headers, перед Simple —
обязательный --driver-python; оба CLI отказа произошли до запуска runtime.
Настоящие PASS/FAIL прежних этапов ниже не заменены.

Команды сборки и целевых запусков:

```sh
cd /tmp/firebird-pgwire-1c-build
make -j2 > begin-build-01.log 2>&1
make -C gen -j2 TARGET=Debug pgwire_tests > begin-unit-build-01.log 2>&1
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_sql_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_session_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/isql_test --log_level=message

cd /home/aleksey/dev/my_firebird/firebird
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --begin \
  --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-begin-02 \
  --capture-dir /home/aleksey/dev/1c/captures/20261109T105635Z \
  --libpq /lib/x86_64-linux-gnu/libpq.so.5
# --settings и --strings: та же команда с соответствующим флагом и новым work-dir из таблицы.
python3 -B pg_wire_protocol/tools/pgwire_transaction_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-begin-transactions-01 \
  --libpq-include-dir /tmp/firebird-pgwire-1c-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5
python3 -B pg_wire_protocol/tools/pgwire_extended_query_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-begin-extended-01 \
  --libpq-include-dir /tmp/firebird-pgwire-1c-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5
python3 -B pg_wire_protocol/tools/pgwire_simple_query_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-begin-simple-01 \
  --libpq-include-dir /tmp/firebird-pgwire-1c-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5 --driver-python /usr/bin/python3
python3 -B /tmp/firebird-pgwire-begin-driver-check.py
```

Driver probe вызывает только prepare/start_phase/driver_acceptance/close прежнего
ServerHarness; весь P6 server suite заново пройденным не объявляется.
Для повторного запуска всегда выбирать новые work-dir.

SHA256 `git diff HEAD -- src`:
`a1077950ff040246be9685b1c6ab0326a012a45a1d2c738da899cf5acb168bd8`.
Engine14: `c0832e0b14dd5513a25ccff22c772c3dc9240d950d4a31ed0f3ca20f791f2304`;
PgWire: `56302018b7cec2c643d90e99570f56bbdee1c795b8bfcd730ae07a2830244ced`.
Новый checks module: `719b0cc908c8b16ea4e7e42cd25253a13870e269c07e337bf1d551d8f6bc5516`.
Отчёт модулей/DT_NEEDED: `/tmp/firebird-pgwire-1c-build/begin-modules.json`;
зависимости совпадают с baseline. Summary каждого runtime хранит результаты и exit.

Psycopg 3 в текущем окружении отсутствует, прежний /tmp venv недоступен:
default autocommit=false сценарий обновлён на BEGIN/T/rollback/I, проверен AST,
но новый live PASS не заявляется. Отрицательные `$n`/`::`/DEALLOCATE ALL сохранены.
Прежние BEGIN-negative ожидания актуальных C/Python suites заменены положительными
или действительно ошибочной формой BEGIN READ ONLY; исторические P3/P9 результаты
не пересчитываются. Полный startup suite/P3/P9, Windows/CMake, известные native
crash repro и база 1С/Гилёв не запускались. Работающий сервер и базы пользователя
не затрагивались. Реальные эффекты ignored SET, остальные PG transaction options
и полная эквивалентность concurrent DML PostgreSQL остаются за границей этапа.

## 10 ноября 2026: временный синтаксический приём трёх startup SET

Выполнено [IGNORED_STARTUP_SETTINGS_TASK.md](1c/IGNORED_STARTUP_SETTINGS_TASK.md)
на базе `4abc5af9194068cffe04bffd3bd358b142e8964a`. Исходные изменения документации
и задания пользователя сохранены; git add/commit/amend не выполнялись.

В PG4 принимаются enable_mergejoin/cpu_operator_cost/lock_timeout: `=`/`TO`,
строка, identifier/on/off или штатный numeric literal со знаком. Значения
игнорируются без PG-проверки типа, диапазона или единиц. Это явное исключение
пользователя только для трёх имён. Lock timeout в 20 секунд не устанавливается;
optimizer, costs, timeouts, TPB, данные и настройки attachment не меняются.
Успех полного startup batch не означает реализацию всех его эффектов или запуск 1С.

Production diff: шесть файлов, 34 additions / 1 deletion. ParserTokens/Parser.cpp
добавляют три non-reserved имени и прежний PG-only gate; parse.y выделяет группу
с существующими pg_setting_value/u_numeric_constant и отдельными знаками,
не расширяя RHS прежних SET. Один Type SetPgSessionNode получает пустое value;
Execute явно ничего не делает. SqlInfo добавляет только Session/SET prefixes;
исходный SQL передаётся engine без rewriting. Нет новых состояния, истории,
cache invalidation, API/ABI, ParameterStatus, конфигурации или зависимостей.

### Команды и результаты

Использована прежняя Linux x86-64 developer/Debug сборка в
`/tmp/firebird-pgwire-1c-build`, с builtin tommath/tomcrypt.

```sh
cd /tmp/firebird-pgwire-1c-build
make -j2 > ignored-startup-build-01.log 2>&1
make -C gen -j2 TARGET=Debug pgwire_tests > ignored-startup-unit-build-01.log 2>&1
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_sql_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_session_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/isql_test --log_level=message

cd /home/aleksey/dev/my_firebird/firebird
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --settings \
  --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-ignored-startup-01 \
  --capture-dir /home/aleksey/dev/1c/captures/20261109T105635Z \
  --libpq /lib/x86_64-linux-gnu/libpq.so.5
git diff --check
```

| Проверка | Фактический результат |
|---|---|
| Full make / pgwire_tests build | PASS; parser **154 shift/reduce, 11 reduce/reduce**, без новых конфликтов |
| Classifier / SessionTest / isql | **554 checks / 5 scenarios / 13 cases PASS**; `ignored-startup-units.json` в build-dir |
| --settings | **PASS**, 190 libpq queries через helper, обычный WIRE_HOST_TEST, libpq 14.24 |
| Capture | Три исходных SET отдельно успешны; полный неизменённый 03-session-batch.sql — **семь SET tags, I, standard_conforming_strings=off** |
| Scalar grammar | `=`/TO, comments/case, строки, identifiers/on/off, дробные/целые/exponent/NUM128 и знаки успешны; альтернативные значения игнорируются. Пустой RHS, выражения, DEFAULT/LOCAL/SESSION/SHOW и неизвестное имя — 42601 |
| Прежние настройки | Числовые client_encoding/standard_conforming_strings — 42601; неподдержанные fixed values — 22023. Сохранены English/UTF8, stop/rollback writes и восстановление строкового режима при ошибке |
| Native transaction | CURRENT_TRANSACTION совпадает до/после каждого нового SET в четырёх циклах: COMMIT/ROLLBACK × on/off. В E каждый SET даёт 25P02; ROLLBACK восстанавливает исполнение |
| Extended / raw frames | Named Parse/Bind/Execute и повторное исполнение: 12CZ / 2CZ, SET, Z/I, без S/N. Simple отдельные SET — CZ, три вместе — CCCZ. При off SELECT '\n' после новых SET по-прежнему возвращает newline |
| Native1/2/3 / PSQL | Все семь проверяемых fixed/ignored SET отвергнуты с 42000; AUTOTERM продолжает SQL, имена допустимы без кавычек, SET BIND сохранён. PG SET в PSQL — 0A000 |
| Статические проверки | Python AST и git diff --check PASS; DT_NEEDED Engine14/PgWire совпадает с fixed-diagnostics-02 |

Harness использовал отдельные runtime/security DB/test databases в /tmp;
рабочий сервер и базы пользователя не изменялись. Сервер завершился **exit 0**
за 0.164 s, **forced_kill=false**. Полный отчёт:
`/tmp/firebird-pgwire-ignored-startup-01/summary.json`.
Новый клиент, framework или режим тестирования не добавлены.

SHA256 для воспроизведения:

- `git diff HEAD -- src`: `164ed916858b25aa9a3039cbfaae7cff0765b6beb6da4f7c2370e6e45d6ebc45`.
- `libEngine14.so`: `0645af3f26fb38601086b443685405a079fee772f03d56bfd054ec4439a22dfa`.
- `libPgWire.so`: `3144c44569a8bc11b6a67e7c8d9bc5c0d079da4ea39d5d368298718af3c061e0`.
- `pgwire_1c_smoke.py`: `01b0f58171b802e08b18771fbe7d734733792a4abc980816423baef3be38cfaf`.
- Исходный пакет 03: `b6477b5952ffc849b022725fe9f60ee45ff15fb3fb36e6d325d40a88e260d576`.

Модули и сравнение зависимостей: `ignored-startup-modules.json` в build-dir.
Исторические FAIL/PASS, включая прежний ожидаемый 42601 полного пакета,
сохранены ниже. Широкий --strings, lock waits/optimizer benchmarks, известные
crash repro, Windows/CMake и 1С/Гилёв не запускались. Реальные эффекты трёх
настроек отложены. BEGIN и другие GUC не реализовывались; этап завершён на review.

## 10 ноября 2026: архитектурный review фиксированной диагностики

Проверена чистая ревизия `4abc5af9194068cffe04bffd3bd358b142e8964a` по
[FIXED_DIAGNOSTICS_TASK.md](1c/FIXED_DIAGNOSTICS_TASK.md). Замечаний не обнаружено:
production diff ограничен PG-only grammar/token gate, существующим
SetPgSessionNode и классификацией SET. Значения проверяются полностью при Execute;
состояние, история, API/ABI, metadata и зависимости не добавлены. Документация
явно ограничивает контракт off/error и не заявляет успешный полный startup.

Независимо повторены classifier (**527 checks**), SessionTest (**5 scenarios**),
isql (**13 cases**) и `pgwire_1c_smoke.py --settings` — **PASS**.
В runtime проверены **72 libpq queries**, исходные фрагменты capture,
named Extended statements, raw diagnostic frames без NoticeResponse,
I/T/E и восстановление строкового режима, native dialects 1/2/3/AUTOTERM,
идентификаторы и native observer. Сервер завершился exit 0, без forced kill.
Отчёт: `/tmp/firebird-pgwire-fixed-diagnostics-review-01/summary.json`.
Использованы отдельные runtime/security DB/test databases; рабочий сервер не менялся.

Повторная сборка не выполнялась. SHA256 текущих Engine14/PgWire совпали с
указанными ниже бинарниками приёмки; `git diff a31c890e1b HEAD -- src` имеет
тот же SHA256 `53b93221dd98fd192fcd25fd69db3a6c826970305a30629d855d3fafd8855b66`.
Предыдущий успешный --strings отчёт проверен, но этот suite повторно не запускался.
Широкие suites и native crash repro не запускались; исходники реализации не менялись.

Подготовлено отдельное [задание PG BEGIN](1c/BEGIN_TASK.md). Оно ещё не реализовано;
оставшиеся optimizer SET и lock_timeout автоматически в его scope не включаются.

## 10 ноября 2026: фиксированный профиль клиентской диагностики

Выполнено [FIXED_DIAGNOSTICS_TASK.md](1c/FIXED_DIAGNOSTICS_TASK.md) на базе
`a31c890e1b378926f2a15a4cb5608c412980c3a2`. Поддержаны только
`escape_string_warning=off` и `client_min_messages=error`, case-insensitive,
quoted/bare, `=`/`TO`. Другие значения проверяются при Execute и дают 22023.
Обычного пользователя достаточно. Предыдущий review документации сохранён ниже;
новые изменения оставлены пользователю без staging/commit.

Перед реализацией проверены основания контракта: Parser::lexPgString не вызывает
warning path, diagnose читает getErrors(), Protocol не формирует NoticeResponse.
Единственная отправка одиночного N — отказ SSL/GSS negotiation, не typed notice.
Существующие native numeric warnings не изменены и не превращаются в PG notices.
Фиксированные SET подтверждают этот профиль; новые фильтры/transport не добавлены.

| Файлы | Изменение |
|---|---|
| `ParserTokens.h`, `Parser.cpp`, `parse.y` | Два non-reserved имени и PG-only token gate; две альтернативы прежнего set_pg_session с неизменёнными assignment/value rules |
| `StmtNodes.h/.cpp` | Два внутренних Type; полная длина и equalsNoCase проверяются в существующем Execute, без нового состояния |
| `SqlInfo.cpp`, `SqlInfoTest.cpp` | Session/SET classification двух имён; comments/case, invalid values и keyword boundaries |
| `pgwire_1c_smoke.py` | Расширены существующие --settings libpq/raw/native checks; каждый diagnostic frame читается без фильтра N/S |

Нет новых API/ABI, getInfo, flags, attachment fields, истории, cache reset,
ParameterStatus или конфигурации. StrMark::introducerAllowed и прежняя
архитектура standard_conforming_strings сохранены.

### Сборка и проверки

Linux x86-64, developer/Debug Firebird `LI-T6.0.0.2199-dev`, GCC 13.4.0,
GNU Make 4.3, Python 3.10.12, libpq 14.24. Использована прежняя out-of-tree
конфигурация `--enable-developer --with-builtin-tommath --with-builtin-tomcrypt`.

```sh
cd /tmp/firebird-pgwire-1c-build
make -j2
make -C gen -j2 TARGET=Debug pgwire_tests
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_sql_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_session_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/isql_test --log_level=message

cd /home/aleksey/dev/my_firebird/firebird
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --settings \
  --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-fixed-diagnostics-02 \
  --capture-dir /home/aleksey/dev/1c/captures/20261109T105635Z \
  --libpq /lib/x86_64-linux-gnu/libpq.so.5
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --strings \
  --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-fixed-diagnostics-strings-01 \
  --libpq /lib/x86_64-linux-gnu/libpq.so.5
```

| Проверка | Результат |
|---|---|
| Полный make / unit build | PASS, `fixed-diagnostics-build-01.log`, `fixed-diagnostics-unit-build-01.log` в build-dir; parser **154 shift/reduce, 11 reduce/reduce**, без новых конфликтов |
| Classifier / SessionTest / isql | **527 checks / 5 scenarios / 13 cases PASS**; `fixed-diagnostics-units.json` |
| --settings, обычный WIRE_HOST_TEST | **PASS**, 72 запроса проверены libpq helper; исходные фрагменты по отдельности/вместе, регистр/формы, SET tags, полные неверные значения и прежний English/UTF8 профиль |
| Extended | Хорошие named statements повторно Bind/Execute; плохие значения проходят Parse/Bind и дают 22023 при Execute, затем Sync/recovery |
| Ошибки/транзакции | Stop script, native observer видит 0 строк 511–516; I/T/E, неверный новый SET восстанавливает standard_conforming_strings, хорошие SET его не меняют; COMMIT/ROLLBACK/reset сохраняют фиксированный профиль |
| Native1/2/3 | Новые SET отвергнуты с 42000, AUTOTERM исполняет следующий SQL; все четыре fixed-setting имени работают как identifiers, SET BIND сохранён; PG SET в PSQL даёт 0A000 |
| --strings | **PASS**, 156 libpq queries, raw-wire, **350 OO API checks**, CTAS/PSQL source, повторный native ALTER/reconnect, charset/cache/offset regressions |

Raw-wire при standard_conforming_strings=off прочитан полностью до Z/I:

| Запрос | Frames | Проверенный результат |
|---|---|---|
| Оба fixed SET | CCZ | SET/SET, без новых ParameterStatus |
| SELECT '\n' | TDCZ | Один перевод строки, N отсутствует |
| SELECT '\q' | TDCZ | q, N отсутствует |
| SELECT '\u12' | EZ | ErrorResponse 22025, N отсутствует |
| SELECT 1/0 | TEZ | ErrorResponse 22012 после RowDescription, N отсутствует |

Предварительный `fixed-diagnostics-01` остановился на неверном ожидании теста EZ
для runtime division by zero: фактически до ошибки fetch уже отправлен T.
Ожидание исправлено на TEZ; production код после сборки не менялся. Только
окончательный `fixed-diagnostics-02` считается приёмкой --settings.
Оба итоговых сервера завершились **exit 0**, без forced kill. Использованы частные
runtime/security DB/test databases; работающий сервер и базы пользователя не менялись.

Evidence: `summary.json` в обоих work-dir. Общий source_revision указан выше;
SHA256 `git diff HEAD -- src`:
`53b93221dd98fd192fcd25fd69db3a6c826970305a30629d855d3fafd8855b66`.
Engine14 SHA256: `3e444daa93d525917564c38a053bf032c1b77b4b369ced1a6d894a665b469068`;
PgWire SHA256: `cda722c8de13cd16698c4b6aa408342c1ef9023728d8dadcb8ac622bf474b3a0`.
`readelf -d` сравнил оба модуля с `strings-source-fix-02/runtime`: DT_NEEDED
совпадает. Новых build/test/runtime dependencies нет; отчёт
`/tmp/firebird-pgwire-1c-build/fixed-diagnostics-dependencies.json`.

### Ограничения

Это off/error-only профиль, не переключаемые GUC и не полная PG диагностика.
Defaults PostgreSQL — on/NOTICE; INFO в PostgreSQL отправляется даже при error,
а здесь источники/доставка INFO/NOTICE/WARNING не реализованы. Native IStatus
warnings и server log не перенастраиваются. Официальные источники и контракт —
в [1c/README.md](1c/README.md).

Приняты именно два фрагмента записи. Полный исходный `03-session-batch.sql`
остаётся **42601** на первом enable_mergejoin (его SHA сохранён в отчёте).
Остались enable_mergejoin, cpu_operator_cost и lock_timeout; приблизительных
подстановок нет. Другие значения, SET LOCAL/SESSION/DEFAULT, SHOW, startup GUC,
BEGIN/$n и следующие этапы не реализованы. Whole-query preparse отложен.
Широкие suites, Windows/CMake, 1С/Гилёв и известные native crash repro повторно
не запускались; отложенные engine defects этим изменением не исправляются.

## 10 ноября 2026: повторный архитектурный review исправления строк

Проверена чистая ревизия `a31c890e1b378926f2a15a4cb5608c412980c3a2`.
Замечаний к исправлению не осталось: выбор introducer перенесён в существующий
контекст `sql_string`, bare STRING сохраняет допустимую native форму X'hex'.
Production diff ограничен Parser.h/Parser.cpp/parse.y; API, native grammar,
история настроек и зависимости не расширены.

Независимо повторены classifier (**509 checks**), SessionTest (**5 scenarios**),
isql (**13 cases**) и целевой `pgwire_1c_smoke.py --strings` — **PASS**.
Последний прогон: **156 libpq queries, 350 OO API checks**, raw-wire проверки,
native dialects 1/2/3, CTAS при on/off, смешанные charsets, сохранённые temporal
и SQLSTATE процедуры с двумя native ALTER и reconnect. Server exit 0,
forced kill не применялся. Отчёт:
`/tmp/firebird-pgwire-strings-source-review-01/summary.json`.
Использованы отдельные runtime/security DB/test databases; рабочий сервер не менялся.

Повторная сборка не выполнялась: SHA256 Engine14/PgWire совпали с приведёнными
ниже бинарниками исправления. Широкие suites и прежние native crash repro
повторно не запускались. Исходники реализации в ходе review не изменены.
Подготовлено задание следующего этапа —
[фиксированный профиль диагностики](1c/FIXED_DIAGNOSTICS_TASK.md); он ещё не реализован.

## 10 ноября 2026: исправление сериализации bare STRING после review

Выполнено [STRINGS_SOURCE_FIX_TASK.md](1c/STRINGS_SOURCE_FIX_TASK.md) на базе
`9e5b7bd07a95d0d20caf390689bca0cc6995dbc0`. Исходные изменения документации
архитектурного review сохранены ниже. Исправление оставлено без staging/commit.

Production diff ограничен `Parser.h`, `Parser.cpp`, `parse.y`: приватный
`StrMark::introducerAllowed` изначально false; редукция `sql_string: STRING`
устанавливает его и явно сохраняет `$$ = $1`. transformString добавляет
неявный `_UTF8` только для escaped sql_string без явного introducer.
Остальные escaped STRING сериализуются в штатный bare `X'hex'`.
Native grammar, SQL positions, API/metadata, session history и undo не расширены.

Проверен сгенерированный `temp/Debug/dsql/parse.cpp`: новое действие находится
в `case 1989` под **`if (!yytrial)`**. Оно не исполняется в отвергнутой пробной
ветке btyacc. Редукции дочерних sql_string завершаются до makeParseStr в CTAS и
psql_procedure_clause; обработчик позиций остаётся штатным. Live CTAS с
parentheses/CASE/nested SELECT и смешанный PSQL подтвердили выбор формы на момент
сохранения source; повторный native ALTER не меняет значения. Реальные фрагменты:

```sql
D=DATE X'323032362D30312D3031';
S= _UTF8 X'0A';
WHEN SQLSTATE X'3232303132' DO BEGIN V=42; S= _UTF8 X'D18F'; END
```

### Сборка и целевые проверки

Linux x86-64, developer/Debug Firebird `LI-T6.0.0.2199-dev`, GCC 13.4.0,
GNU Make 4.3, Python 3.10.12, libpq 14.24. Использован существующий configure
с `--enable-developer --with-builtin-tommath --with-builtin-tomcrypt`.

```sh
cd /tmp/firebird-pgwire-1c-build
make -j2
make -C gen -j2 TARGET=Debug pgwire_tests
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_sql_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_session_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/isql_test --log_level=message

cd /home/aleksey/dev/my_firebird/firebird
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --strings \
  --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-strings-source-fix-02 \
  --libpq /lib/x86_64-linux-gnu/libpq.so.5
```

| Проверка | Фактический результат |
|---|---|
| Расширенный тест на исходных бинарниках | Ожидаемый FAIL: CTAS on проходит, CTAS off даёт 42601; `/tmp/firebird-pgwire-strings-source-baseline-01/summary.json`, server exit 0 |
| Полный make | PASS, `strings-source-fix-build-01.log`; parser **154 shift/reduce, 11 reduce/reduce**, новых конфликтов нет |
| Classifier / SessionTest / isql | **509 checks / 5 scenarios / 13 cases PASS**; `strings-source-fix-units.json` в build-dir |
| Итоговый --strings | **PASS**, 156 учтённых libpq queries, raw-wire cases, native dialects 1/2/3 и **350 OO API checks** |
| CTAS WITH DATA | DATE/TIME/TIMESTAMP при on/off: правильные строки и OID 1082/1083/1114; off с реальными escapes совпадает с native EDS decoded literals |
| Смешанный CTAS | Unicode, quote/backslash, newline, explicit UTF8, Q и hex: bytes/OID сохранены; все семь текстовых полей имеют inferred charset 4 (UTF8), включая CASE |
| Сохранённые процедуры | Temporal и WHEN SQLSTATE: исходное исполнение, два ALTER из реального RDB$PROCEDURE_SOURCE через native EDS, повторное исполнение и reconnect с проверкой значений/типов — PASS |
| Диагностики | OO проверяет line/column исходного SQL после escapes при on/off, отсутствие ложного command_end_err2; прежние invalid UTF8/Unicode/charset guards сохранены |

Первый прогон исправленной сборки остановился на неверном ожидании теста:
CASE над CHAR-литералами даёт native CHAR/OID 1042, не VARCHAR/1043.
Тип и charset подтверждены native metadata; ожидание исправлено, production
код после первой сборки не менялся. Этот предварительный прогон не считается PASS.
Итоговый сервер завершился exit 0, forced kill не применялся. Проверки использовали
частные runtime/security DB/test databases; рабочие базы и сервер не менялись.

Engine14 SHA256: `d4bc1bc5adf3a593232eecfc83db4b9c1e4b653273dbb34f9f570d61553cfcb5`.
PgWire SHA256 остался `30ae096fead56aba0651911ac15572babcc4a39b0d4f3f6d9ece6275f97b2a17`.
`readelf -d` подтвердил прежний DT_NEEDED **обоих** модулей относительно baseline;
новых build/test/runtime dependencies нет. Отчёт:
`/tmp/firebird-pgwire-1c-build/strings-source-fix-dependencies.json`.

### Границы результата

Исправлена новая сериализация; ошибочно сохранённые старые исходники автоматически
не переписываются. Их пересоздание требует исходного SQL. Положительный EDS SELECT
не доказывает внутренние SAVEPOINT: отдельный исходный assertion exe.cpp:1351
из review не исправлялся и не включался в обычный suite. Nested RESET,
ON CONNECT shutdown, READ CONSISTENCY + AUTO COMMIT и сочетание новой истории
с AUTO COMMIT также не перепроверялись и не исправлялись.

После успешной целевой приёмки широкие прежние suites, Windows/CMake и полный
запуск 1С/Гилёва не повторялись. Новые SET, BEGIN/$n и whole-query preparse
не добавлены. Следующий кандидат escape_string_warning/client_min_messages
остаётся отдельным этапом после принятия этого diff пользователем.

## 10 ноября 2026: архитектурный review ревизии 9e5b7bd07a

Проверен закоммиченный результат standard_conforming_strings. Перед review
рабочее дерево было чистым; production code в этом review не изменялся.
Engine/parser/cache и PgWire/session history просмотрены отдельно.
Согласованное разделение ответственности выполнено: журнал находится в плагине,
native transaction undo не расширен, getInfo/prepare сохранили сигнатуры.

Повторены существующие проверки на бинарниках с теми же SHA256 Engine14/PgWire,
что указаны в предыдущем отчёте:

- --strings: **PASS**, 126 libpq-запросов, raw wire и **292 OO API checks**.
  Evidence: /tmp/firebird-pgwire-strings-review-01/summary.json;
  source_revision = 9e5b7bd07a, исходный src diff пуст; server exit 0.
- SessionTest: **5 scenarios PASS**, classifier: **509 checks PASS**,
  isql suite: **13 cases PASS**.

Однако дополнительный целевой repro выявил **P2: неверную сериализацию
голого STRING в typed literals и SQLSTATE**.
При off Parser::transformString добавляет _UTF8 также к STRING, который
не допускает introducer по native grammar. Результат:

| Проверка через libpq | Фактический результат |
| --- | --- |
| SELECT DATE '2026-01-01' при on/off | Оба возвращают 2026-01-01 |
| CTAS WITH DATA с таким DATE при on | Успех |
| Такой же CTAS при off | **42601** при внутреннем prepare |
| Процедура с DATE при off | Создаётся и исполняется; source содержит DATE _UTF8 X'...' |
| ALTER из этого сохранённого source через native EDS | **42601**; контрольный source, созданный при on, работает |
| Процедура с WHEN SQLSTATE '22012' при off | Первое исполнение возвращает 42, но сохранённый WHEN SQLSTATE _UTF8 X'...' не проходит повторный ALTER |

Evidence: /tmp/firebird-pgwire-strings-literals-review-01/review.json,
22 запроса, server exit 0. Reproducer: /tmp/pgwire_strings_arch_review_v2.py
с режимом literals. Исправление ещё не выполнено.
Архитектура и следующий отдельный этап —
[STRINGS_SOURCE_FIX_TASK.md](1c/STRINGS_SOURCE_FIX_TASK.md).
Предыдущая положительная проверка stored source использовала обычную строку
и не покрывала эти контексты; её PASS не отменяет найденный дефект.

Дополнительно проверен настоящий внутренний SAVEPOINT через EXECUTE STATEMENT:
EXECUTE BLOCK AS BEGIN EXECUTE STATEMENT 'SAVEPOINT inner_point'; END
внутри SET TRANSACTION; SAVEPOINT outer_point.
Он вызывает Debug assertion в exe.cpp:1351 и SIGABRT как на текущей сборке,
так и на точных предыдущих бинарниках Engine14/PgWire из settings-04.
Baseline запуск использовал свежую security DB и отдельные тестовые данные;
хеши исполняемых модулей сверены с предыдущим runtime.
Evidence: /tmp/firebird-pgwire-eds-baseline-review-02/review.json (exit -6),
его runtime/firebird.log и
/tmp/firebird-pgwire-strings-arch-review-01/runtime/firebird.log.
Первый baseline setup с копией готовых data не состоялся из-за уже существующего
файла базы; этот setup не считается результатом проверки.
Это исходный engine defect, отдельный от найденной регрессии сериализации.
Тест с вложенным SELECT не доказывает корректность внутреннего SAVEPOINT.
Рабочие серверы и базы пользователя не менялись.

## 10 ноября 2026: standard_conforming_strings, редакция 2

База — `da23d2d60cf011fec192696e9c4e5abd6b797365`. Реализовано согласованное
разделение: bool на attachment, история отката одной настройки в PgWire,
восстановление фиксированным SET через существующий API. Результат не закоммичен;
пользовательская [инструкция](1c/STANDARD_CONFORMING_STRINGS_TASK.md) не изменялась.
Подробный контракт и ручной пример — в [1c/README.md](1c/README.md).

| Группа файлов | Необходимое изменение / использованный механизм |
|---|---|
| `jrd/Attachment.*`, `jrd/inf.cpp`, `inf_pub.h` | Один session bool, существующий reset и attachment getInfo (item 163, integer 0/1). В transaction/savepoint engine undo изменений нет |
| `StmtNodes.*`, `ParserTokens.h`, `parse.y` | Существующий SetPgSessionNode/SessionManagementNode, PG-only token gate, Execute-only on/off; штатное разрешение charset/schema для introducer |
| `Parser.*` | Обычный engine lexer декодирует off-литералы; StrMark/transformString сохраняет native hex notation для внутренней переподготовки, с исходными offsets |
| `dsql.cpp`, `DsqlStatementCache.*`, IDL/generated bindings | Один effective prepare context для parser и обеих операций cache; два flags, без методов, версий и vtables |
| `Execute.*`, `Extended.cpp`, `PgWire.cpp`, `SqlInfo.*` | Narrow history, native savepoint names/ONLY через MetaString, общий abort path, fixed maintenance SET/readback, Simple/Parse snapshots, фактический ParameterStatus |
| `FrontendLexer.*` | Опциональный skip backslash пары для границ исходных строк; isql/native defaults сохранены |
| Existing tests/build targets, `SessionTest.cpp`, `pgwire_1c_smoke.py`, `pgwire_strings_checks.py`, `pgwire_strings_api.cpp` | Переиспользованы unit targets, private runtime harness, libpq/raw-wire и OO API; нового framework нет |

### Сборка и зависимости

Linux x86-64, Firebird `LI-T6.0.0.2199-dev`, GCC 13.4.0, GNU Make 4.3,
Python 3.10.12. Существующий out-of-tree configure:

```sh
cd /tmp/firebird-pgwire-1c-build
/home/aleksey/dev/my_firebird/firebird/configure \
  --enable-developer --with-builtin-tommath --with-builtin-tomcrypt
make -j2
make -C gen -j2 TARGET=Debug pgwire_tests
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_session_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/isql_test --log_level=message
```

Configure повторно вручную не запускался; make использовал существующий build и
перегенерировал затронутые outputs. Финальный полный build — **PASS**,
`/tmp/firebird-pgwire-1c-build/strings-build-10.log`. Parser baseline сохранён:
**154 shift/reduce, 11 reduce/reduce**, новых конфликтов нет. CLOOP bindings
перегенерированы штатной целью `updateCloopInterfaces`.

Build dependencies прежние: toolchain/autotools, bundled btyacc/CLOOP и
tommath/tomcrypt, существующие Firebird/ICU dependencies. Tests используют
существующие Boost/isql targets, Python stdlib, libpq headers/runtime 14.24;
Simple driver probe — psycopg2 2.9.2/libpq 14.24 на `/usr/bin/python3`.
OO helper собирается g++ с runtime headers/libfbclient, без новой библиотеки.

`readelf -d` и `ldd` сравнили текущий PgWire с
`/tmp/firebird-pgwire-1c-settings-04/runtime/plugins/libPgWire.so`.
**DT_NEEDED одинаков**: libfbclient.so.2, libtommath.so.0, libtomcrypt.so.1,
libm.so.6, libgcc_s.so.1, libc.so.6, ld-linux-x86-64.so.2. Все зависимости
разрешились; нового runtime dependency нет. Полный отчёт:
`/tmp/firebird-pgwire-1c-build/strings-dependencies.json`.

### Приёмка

Команды запускались из корня репозитория, серверы/базы были частными копиями,
рабочий сервер и базы пользователя не менялись. Финальный целевой запуск:

```sh
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --strings \
  --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-strings-10 \
  --libpq /lib/x86_64-linux-gnu/libpq.so.5
```

**PASS**: 126 запросов настоящей libpq; raw-wire Parse/Bind/Execute/Flush/Sync
и ParameterStatus; native dialects 1/2/3 с AUTOTERM; **292 OO API checks**.
OO покрывает getInfo, flags/cache/metadata, prepare без изменения режима,
повторный SET Execute, native rollback без PG undo, UTF8/WIN1251,
introduced/Q strings и отсутствие ложного command_end_err2.
Проверены immediate restore в E, repeated errors, implicit без handle,
COMMIT из E, retaining/NO AUTO UNDO, имена savepoints/ONLY/replacement,
stored PSQL после reconnect и native source reprepare, два attachments/reset.
Сервер завершился exit 0, forced kill не применялся.
Evidence — `/tmp/firebird-pgwire-strings-10/summary.json`.
SHA256 PgWire: `30ae096fead56aba0651911ac15572babcc4a39b0d4f3f6d9ece6275f97b2a17`;
Engine14: `d850255dfc7f295c13ff80201010eb51598b7bfc96fcc52d76c38fa25090585a`.

Unit acceptance: **9 PgWire executables PASS** (SQL classifier 509,
SessionTest 5 fault/success scenarios, остальные codecs/protocol/error tests);
**полный isql suite: 13 cases, no errors**.
Логи/сводка: `/tmp/firebird-pgwire-1c-build/strings-unit-results.json`,
`strings-tests-build-08.log`. SessionTest проверяет original error/FATAL,
отсутствие лишних результатов/handles, освобождение служебного statement,
prepare/execute/readback failure и отсутствие обязательного info item.

Регрессионные команды (общие значения переменных приведены для воспроизведения;
фактические вызовы использовали эти же полные пути):

```sh
fb_strings_runtime=/tmp/firebird-pgwire-1c-build/gen/Debug/firebird
fb_strings_headers=/tmp/firebird-pgwire-1c-deps/libpq-dev/usr/include/postgresql
fb_strings_libpq=/lib/x86_64-linux-gnu/libpq.so.5
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --settings \
  --firebird-dir "$fb_strings_runtime" --work-dir /tmp/firebird-pgwire-strings-settings-01 \
  --capture-dir /home/aleksey/dev/1c/captures/20261109T105635Z --libpq "$fb_strings_libpq"
python3 -B pg_wire_protocol/tools/pgwire_script_smoke.py \
  --firebird-dir "$fb_strings_runtime" --work-dir /tmp/firebird-pgwire-strings-scripts-01 --libpq "$fb_strings_libpq"
python3 -B pg_wire_protocol/tools/pgwire_simple_query_smoke.py \
  --firebird-dir "$fb_strings_runtime" --work-dir /tmp/firebird-pgwire-strings-simple-01 \
  --libpq-include-dir "$fb_strings_headers" --libpq-library "$fb_strings_libpq" --driver-python /usr/bin/python3
python3 -B pg_wire_protocol/tools/pgwire_transaction_smoke.py \
  --firebird-dir "$fb_strings_runtime" --work-dir /tmp/firebird-pgwire-strings-transactions-01 \
  --libpq-include-dir "$fb_strings_headers" --libpq-library "$fb_strings_libpq"
python3 -B pg_wire_protocol/tools/pgwire_extended_query_smoke.py \
  --firebird-dir "$fb_strings_runtime" --work-dir /tmp/firebird-pgwire-strings-extended-01 \
  --libpq-include-dir "$fb_strings_headers" --libpq-library "$fb_strings_libpq"
python3 -B pg_wire_protocol/tools/native_baseline.py \
  --firebird-dir "$fb_strings_runtime" --work-dir /tmp/firebird-pgwire-strings-native-01
```

| Проверка | Фактический результат |
|---|---|
| Fixed settings | PASS, исходные capture bytes/ordinary user, UTF8/English, unsupported batch, native AUTOTERM |
| Scripts | PASS, 8 групп |
| Simple Query | PASS, 12 групп, включая C libpq/psycopg2/native observer/trace |
| Transactions | PASS, 14 групп, в том числе прежние SNAPSHOT AUTO COMMIT и NO AUTO UNDO |
| Extended Query | PASS, 16 групп, C libpq/raw-wire; optional Psycopg 3 profile не запускался |
| Native baseline | PASS, 9 checks |

Каждый work-dir содержит `summary.json`; все серверы этих регрессий завершились
exit 0. Регрессии и unit suite выполнены на build-08; затем изменена только
проверка charset/schema introduced off-литералов (`parse.y`). Полный build-10
и целевой --strings подтвердили окончательный код, включая `_UTF8`/`_WIN1251`.
Предварительные неуспешные strings runs не считаются приёмкой.

### Эталон и ограничения

Использованы прежние 14 запросов abort/savepoint reference:
`/tmp/pgwire-1c-reference-iss_20tu/summary.json`. Дополнительно проверены 32
escape/error cases (`/tmp/pgwire-1c-reference-b7ja5gp8/summary.json`) и точный
wire order с Flush (`/tmp/pgwire-1c-reference-r5lqv8my/summary.json`).
Эталон — `/opt/pgpro/1c-18/bin`, сервер сообщает PostgreSQL 18.6,
gcc Ubuntu 11.4.0, 64-bit; libpq 14.24. Частные reference servers завершились
exit 0. Набор ESCAPES/INVALID_UNICODE в test helper использован и эталонным probe.
Подтверждено: ParameterStatus перед ReadyForQuery, без промежуточного уведомления
на Flush; SET, отменённый ошибкой до Sync, не публикует транзитный режим.

Гарантия нового PG undo **не включает TPB AUTO COMMIT и RESET внутри PSQL**.
Старые native AUTO COMMIT возможности не удалены; их positive data baseline не
доказывает согласованность новой истории при скрытом retaining.
Отдельный nested RESET probe выявил существующий сбой и сохранён как **FAIL**:

```sql
SET TRANSACTION;
SAVEPOINT boundary_point;
EXECUTE BLOCK AS BEGIN ALTER SESSION RESET; END;
```

Через PgWire этот пример без нового SET вызывает Debug assertion
`ExtDS.cpp:2021 (!isAllocated())`, SIGABRT/exit -6 **и на исходной сборке**
из `firebird-pgwire-1c-settings-04`, и на текущей. Артефакты:
`/tmp/pgwire-strings-nested-wire-{baseline,current}-02/summary.json`,
reproducer `/tmp/pgwire_strings_nested_wire.py`.
Embedded isql на обеих сборках вместо этого возвращает 01002 (2 active transactions),
`/tmp/pgwire-strings-nested-reset-{baseline,current}-02/summary.json`.
Этот отдельный дефект не исправлялся; данные работающего сервера не затрагивались.

Off ограничен UTF8, сохраняются native длины и whitespace concatenation.
Прямой OO rollback оставляет session bool. E/dollar strings, SET LOCAL/SESSION,
DEFAULT/SHOW/RESET parameter, новые startup keys и BEGIN/$n/::/LIMIT не добавлены.
Не выполнялись полная CMake/Windows/Classic/SuperClassic приёмка, TLS/cancellation,
фактический запуск 1С/Гилёва и полный startup batch. Отложенные ON CONNECT shutdown
и READ CONSISTENCY + AUTO COMMIT defects не исправлялись и не перепроверялись.

## 10 ноября 2026: фиксированный English/UTF8 профиль двух SET

База изменения — `5374cc50e5`. Отдельный срез реализует исходный запрос
`02-lc-messages` и `SET client_encoding = 'utf8'` из `03-session-batch`.
Точный [ограниченный контракт](1c/README.md) допускает только уже обеспечиваемые
английские сообщения PgWire и UTF8, без смены режима. Другие значения отвергаются.
По уточнению пользователя проверка прав на lc_messages отложена; это явное
ограничение совместимости с PostgreSQL, к которому нужно вернуться позднее.
Исходный пакет семи SET целиком и фактический запуск 1С остаются неподдержанными.

| Файлы | Необходимость и переиспользование |
|---|---|
| `ParserTokens.h`, `Parser.cpp`, `parse.y` | Две формы SET только в PG dialect 4. Engine lexer выдаёт новые tokens только в PG-режиме, иначе сохраняет обычные identifiers. Новых конфликтов grammar нет: 154 shift/reduce, 11 reduce/reduce |
| `StmtNodes.h/.cpp` | Узкий SetPgSessionNode на существующем SessionManagementNode: при Execute проверяет значение и настоящий attachment charset. Проверка прав на lc_messages отложена по решению пользователя. Новых public API/metadata/state/cache нет |
| `SqlInfo.cpp` | Два префикса используют прежний путь session controls и tag SET; проверка значений остаётся в engine |
| `SqlInfoTest.cpp`, `pgwire_1c_smoke.py` | Существующие проверки классификации и новый режим `--settings` существующего harness, без новых зависимостей |

До сборки оба SET возвращали 42601 через настоящую libpq, с успешным recovery:
`/tmp/firebird-pgwire-settings-baseline-01/summary.json`. Сборка Linux x86-64,
GCC 13.4.0, developer/Debug, Firebird `LI-T6.0.0.2199-dev`; исходные параметры
configure сохранены (`--enable-developer --with-builtin-tommath --with-builtin-tomcrypt`).
Полный `make -j2` в `/tmp/firebird-pgwire-1c-build` — PASS; окончательный лог
`settings-without-admin-build.log`. Проверка классификации до удаления admin guard:

```sh
cd /tmp/firebird-pgwire-1c-build
make -C gen -j2 TARGET=Debug \
  /tmp/firebird-pgwire-1c-build/gen/Debug/firebird/tests/pgwire_sql_test
LD_LIBRARY_PATH=/tmp/firebird-pgwire-1c-build/gen/Debug/firebird/lib \
  gen/Debug/firebird/tests/pgwire_sql_test
```

**501 checks PASS**, логи `settings-sql-test-build.log`, `settings-sql-test.log`.
Целевая проверка из корня репозитория:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py \
  --settings --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-1c-settings-04 \
  --capture-dir /home/aleksey/dev/1c/captures/20261109T105635Z \
  --libpq /lib/x86_64-linux-gnu/libpq.so.5
```

Harness создаёт отдельные runtime/security DB/UTF8 database и loopback ports;
повторный запуск требует нового work directory. Оба SET выполняет обычный
WIRE_HOST_TEST без выдачи RDB$ADMIN; отдельная административная подготовка удалена.
Пользовательский сервер не менялся. Клиенты: libpq 14.24, psycopg2 2.9.2/libpq 14.24 в Simple regression.

| Проверка | Результат / свидетельство |
|---|---|
| Fixed settings через libpq/raw TCP, после удаления admin guard | **PASS**: обычный WIRE_HOST_TEST успешно выполняет оригинальные bytes запроса 02 и оба SET; неподдержанные значения дают 22023. Настоящий UTF8 charset и кириллица/emoji, английская диагностика, I/T/E, остановка скрипта и native observer rollback сохранены. Extended named statements повторно исполняются обычным пользователем, значение проверяется при Execute. Native dialects 1/2/3 с AUTOTERM отвергают новые SET и исполняют следующий native SQL; имена колонок сохранены. `/tmp/firebird-pgwire-1c-settings-04/summary.json` |
| Прямой OO API, до удаления admin guard | **25 checks PASS**: UTF8 и WIN1251 attachments, 0A000 при несоответствии charset без его смены, prepare(NULL) без применения настройки/пользовательской транзакции, PG4→native1/2/3→PG4 cache isolation, отсутствие ложного isc_command_end_err2, PSQL rejection и recovery. `/tmp/firebird-pgwire-settings-api-01/result-03/summary.json`; source/runner рядом, команда ниже |
| Предыдущий срез 1С, до удаления admin guard | **PASS**: 14 SELECT cases, identity, numeric/DDL/rollback, native dialect isolation и Extended parameters. `/tmp/firebird-pgwire-settings-select-01/summary.json` |
| Simple / Extended / native regressions, до удаления admin guard | **12 / 16 / 9 групп/проверок PASS**. `/tmp/firebird-pgwire-settings-simple-01/summary.json`, `...-extended-01/summary.json`, `...-native-01/summary.json` |
| Source checks | Python syntax, локальные Markdown links, git diff --check: PASS |

OO probe запускается командой `python3 -B /tmp/firebird-pgwire-settings-api-01/run.py
--firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird --run-name result-03`
(для повторения — новый result-NN). Он использует только новую локальную БД в `/tmp`,
без TCP. Регрессии запускаются прежними tools с тем же `--firebird-dir`, новыми
`--work-dir` и параметрами libpq из раздела «последовательные скрипты» ниже.
Все тестовые серверы завершились с exit 0, без forced kill; Simple/Extended
syscall trace подтверждает backend connect attempts = 0.

Промежуточные проверки выявили native AUTOTERM regression: grammar-only dialect
gate давал isc_command_end_err2 и поглощал следующий текст. Исходный и перенесённый
grammar gate не прошли OO probe result-01/02; settings-02 также сохранил FAIL.
Изоляция двух tokens в engine lexer исправила проблему, что подтверждено
settings-03/API result-03. Общая обработка parser errors не менялась.

Состав прямых/транзитивных зависимостей и plugin modules сохранён:
`/tmp/firebird-pgwire-1c-settings-03/dependencies.json`. Проверенные SHA256:
PgWire `a5831a0014f8411f392a0fc43555e26785a09c8328bd28b9ddb543d61764fda1`,
Engine14 после удаления admin guard `6ffcd834f6c936733696cbb5919d57e288346be1dec652db0f8f7ba7863065b4`.
Это не поддержка произвольной locale/encoding или PostgreSQL parameter grants.
PSQL, LOCAL/SESSION/DEFAULT/SHOW и изменяемые настройки не входят в срез.
Windows/CMake runtime, полный pytest и нагрузка 1С/Гилёва здесь не проверялись;
ранее отложенные native дефекты не исправлялись и не воспроизводились повторно.

## 10 ноября 2026: последовательные скрипты Simple Query

База изменения — `83885f5759`. Реализован отдельный подэтап [профиля 1С](1c/README.md):
несколько SQL-команд в одном Query, остановка при первой окончательной ошибке,
один implicit transaction segment до полного COMMIT/ROLLBACK или конца сообщения.
Предварительная проверка синтаксиса всего Query отложена по решению пользователя.
Пакет семи SET из selected example 03 требует этой возможности, но сами настройки
PostgreSQL ещё не реализованы. Открытие базы 1С и тест Гилёва не выполнялись.

| Изменение | Переиспользование и граница |
|---|---|
| `FrontendLexer.*`, POSIX/CMake lists | В существующий lexer isql добавлен внутренний метод исходных offsets разделителей без trim/rewrite и чтения следующей команды; прежний путь isql сохранён. Исходник включён в PgWire без новой библиотеки |
| `Execute.cpp/.h` | Цикл в QueryExecutor использует обычный prepare, PREPARE_REQUIRE_SEMICOLON и структурный isc_command_end_err2 по модели AUTOTERM. Успешный statement сразу исполняется; существующие codecs, controls, error handling и cleanup сохранены. Последний CommandComplete ждёт успешного implicit commit |
| Unit tests и `pgwire_script_smoke.py` | Проверки границ и реального исполнения через существующие harness, raw decoder и libpq. Прежние batch-rejection fixtures заменены проверками ошибки первой команды и остановки хвоста |

Новых public API/ABI, engine metadata, parser changes, конфигурационных параметров
или runtime-зависимостей в этом подэтапе нет. Extended Parse остаётся однокомандным.

Сборка: Linux x86-64, GCC 13.4.0, developer/Debug, Firebird `LI-T6.0.0.2199-dev`.
Для существующего build directory повторён configure, чтобы обновить список
объектов. Полная сборка завершилась с exit 0:

```sh
cd /tmp/firebird-pgwire-1c-build
/home/aleksey/dev/my_firebird/firebird/configure \
  --enable-developer --with-builtin-tommath --with-builtin-tomcrypt
make -j2
make -C gen -j2 TARGET=Debug \
  /tmp/firebird-pgwire-1c-build/gen/Debug/firebird/tests/isql_test
LD_LIBRARY_PATH=/tmp/firebird-pgwire-1c-build/gen/Debug/firebird/lib \
  gen/Debug/firebird/tests/isql_test \
  --run_test=ISqlSuite --report_level=detailed --log_level=error
```

Логи в build directory: `script-configure.log`, `script-build-final.log`,
`script-isql-test-build.log`, `script-isql-test.log`. ISqlSuite: **12 cases,
317 assertions PASS**, включая четыре новых случая reader; прежние frontend
lexer/parser tests также прошли.

Целевой прогон из корня репозитория:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B pg_wire_protocol/tools/pgwire_script_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-script-01 \
  --libpq /lib/x86_64-linux-gnu/libpq.so.5
```

Каждый harness использовал собственные runtime, security DB, UTF8 database и
loopback ports в `/tmp`; для повторения нужен новый work directory. Пользовательский
сервер не менялся. Клиенты: libpq headers/runtime 14.24, psycopg2 2.9.2/libpq 14.24,
pytest 7.4.3. Результаты:

| Проверка | Результат / свидетельство |
|---|---|
| Новые скрипты | **8 групп PASS**: порядок всех результатов и один ReadyForQuery, DDL→DML→SELECT, строки/Q-strings/comments/UTF8, EXECUTE BLOCK, implicit rollback, сохранение раннего COMMIT при syntax/runtime/незакрытых tokens, T/E и recovery, native SET/RESET/savepoint/retaining, реальная ошибка финального commit, PQsendQuery/PQgetResult и native observer. `/tmp/firebird-pgwire-script-01/summary.json` |
| Simple Query regression | **12 групп PASS**, включая native isql AUTOTERM и определения PROCEDURE. `/tmp/firebird-pgwire-script-simple-01/summary.json` |
| Transaction regression | **14 групп PASS**. `/tmp/firebird-pgwire-script-transaction-01/summary.json` |
| Extended Query regression | **16 групп PASS**, включая отказ batch в Parse. `/tmp/firebird-pgwire-script-extended-01/summary.json` |
| Native TCP baseline | **9 проверок PASS**. `/tmp/firebird-pgwire-script-native-01/summary.json` |
| Изменённый pytest case | **1 passed**: `test_multiple_statements_sequential_commit_and_stop`, psycopg2 и native visibility. `/tmp/firebird-pgwire-script-pytest-01/summary.json`; весь pytest-набор в этом подэтапе не запускался |
| Source checks | Python syntax, локальные Markdown links и git diff --check: PASS |

Регрессии запускаются через `pgwire_simple_query_smoke.py`,
`pgwire_transaction_smoke.py`, `pgwire_extended_query_smoke.py`, `native_baseline.py`
с тем же `--firebird-dir` и отдельными `--work-dir`. Первые три используют
`--libpq-include-dir /tmp/firebird-pgwire-1c-deps/libpq-dev/usr/include/postgresql`
и `--libpq-library /lib/x86_64-linux-gnu/libpq.so.5`; Simple дополнительно
`--driver-python /usr/bin/python3`. Использованы уже доступные зависимости.
Все серверы завершились штатно с exit 0. В syscall trace Simple/Transaction/Extended
backend connect attempts = 0; новый script harness отдельный trace не выполняет.

Сравнение с runtime предыдущего подэтапа (`firebird-pgwire-1c-select-03`) сохранено
в `/tmp/firebird-pgwire-script-01/dependencies.json`: прямые NEEDED, транзитивный
ldd и список plugin modules совпадают. SHA256 Engine14 также совпадает:
`a395ba7a1349090bc4eec3dca3975e83486514add55151906da2d16e024310c1`.
Проверенный PgWire: `3fa6a84b13d8cfb95a419a212052f51e1cbbe705cde3c62652ae293a549d2d68`.

Ограничения: ранний COMMIT сохраняется и при поздней syntax error — принятое
отличие от полного предварительного разбора PostgreSQL. Native session SET,
sequences, autonomous transactions и внешние эффекты сохраняют native семантику;
общая отмена этих эффектов не обещается. Отдельный `;` и пустые команды между
разделителями остаются syntax errors. PG SET, BEGIN, `$n`, casts и остальные
формы профиля требуют следующих этапов. CMake/Windows runtime и весь профиль 1С
этим прогоном не проверены. Ранее установленные native ограничения ON CONNECT
shutdown и READ CONSISTENCY + AUTO COMMIT не исправлялись и не воспроизводились
повторно; regression использует принятые профили.

## 9 ноября 2026: первый подэтап записанного профиля 1С

База изменения — `0e505759d0deb1693d912bbc9fee978a55d1e1f0`. Проверен небольшой
подэтап: клиентский `SQL_DIALECT_PG` (4), SELECT без FROM только в этом режиме и
реальная PSQL-функция `pg_catalog.pg_backend_pid()`. Диалект базы/DPB остаётся 3.
[Контракт и установка](1c/README.md) явно ограничивают identity одной базой;
имя колонки пока `PG_BACKEND_PID`, в эталоне — `pg_backend_pid`.
Полный этап 1 задания, открытие базы 1С и тест Гилёва **ещё не выполнены**.

Изменения используют существующие механизмы:

| Файлы | Необходимость и реализация |
|---|---|
| `sqlda_pub.h`, `dsql.cpp`, `Parser.*`, `parse.y`, `btyacc_fb.ske`, `pass1.cpp` | Разрешить исходный SELECT из примера 01, изолировав грамматику через аргумент dialect. Типы нормализуются к 3, штатный cache сохраняет исходный dialect. Пустой RSE уже исполняется как одна строка; SELECT * без источника отвергается до создания пустой output metadata. YYERROR восстанавливает стандартное отбрасывание альтернативы при trial/backtracking |
| `Execute.cpp`, `Extended.cpp` | Три существующих prepare передают PG dialect; SQL и Bind не изменяются |
| `1c/compat.sql` | Schema/function/grants штатного PSQL; проверяемый cast CURRENT_CONNECTION в int4, без новых счётчиков или библиотек |
| `tools/pgwire_1c_smoke.py` | Расширение существующих StartupHarness, libpq client и ExtendedClient; baseline-наблюдения и целевые runtime проверки без нового framework |

Штатный генератор parser обновил `parse-conflicts.txt`: 153/8 → 154/11
(shift/reduce и reduce/reduce). Добавленные неоднозначности связаны с окончанием
select list и словами AS/RETURNING/SQL/OPTIMIZE; прежние alias-формы проверены
через native и PgWire. PG gate выполняется также во время пробного разбора.

Сборка: Linux x86-64, GCC 13.4.0, developer/Debug, Firebird
`LI-T6.0.0.2199-dev`. Полная сборка и финальная пересборка после изоляции dialect
завершились с exit 0; логи `configure.log`, `build.log`, `dialect-build.log`:

```sh
mkdir -p /tmp/firebird-pgwire-1c-build
cd /tmp/firebird-pgwire-1c-build
/home/aleksey/dev/my_firebird/firebird/configure \
  --enable-developer --with-builtin-tommath --with-builtin-tomcrypt
make -j2
```

Целевой прогон из корня репозитория:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-1c-select-03
```

Для повторного запуска нужен новый work directory. Harness создаёт собственные
runtime/security DB/UTF8 database, loopback endpoints, Workers=6 и устанавливает
compat.sql. Рабочие экземпляры пользователя не менялись. Реальные клиенты:
libpq 14.24, Python 3.10, psycopg2 2.9.2/libpq 14.24 в Simple Query regression.

| Проверка | Результат / свидетельство |
|---|---|
| Новый harness | PASS: 14 SELECT cases (значения, OID, tags), точный NUMERIC(18,2)/DDL/DML/rollback, оригинальный пример 01 без изменения bytes, две libpq-сессии и native MON observer, стабильность ID после COMMIT/ROLLBACK/RESET, четыре ошибки с recovery, native dialects 1/2/3 и aliases, Extended `?` с/без FROM, cleanup. `/tmp/firebird-pgwire-1c-select-03/summary.json` |
| Кэш на одной attachment | PASS, 7 проверок: PG4 → native1/2/3 rejection → PG4, отказ dialect5, native3 с FROM. Statements освобождаются между prepare, cache 2 MiB. Разовая OO API проверка: `/tmp/firebird-pgwire-1c-cache-01/{probe.cpp,probe.log,command.json,summary.json}` |
| Simple Query regression | PASS, 12 групп, C libpq и psycopg2. `/tmp/firebird-pgwire-1c-simple-03/summary.json` |
| Extended Query regression | PASS, 16 групп, C libpq/raw, включая nonblocking и I/T/E. `/tmp/firebird-pgwire-1c-extended-03/summary.json` |
| Native TCP baseline | PASS, 9 проверок. `/tmp/firebird-pgwire-1c-native-03/summary.json` |
| Source checks | Python syntax, локальные Markdown links, git diff --check: PASS |

Все тестовые серверы остановились штатно, exit 0. В strace Simple/Extended
backend connect attempts = 0. Прогоны `*-02` этих трёх регрессий остановились
на sandbox EPERM при создании socket; `*-03` выполнены с разрешённым локальным
TCP. Это ограничение среды, не отказ SQL. Предварительные `select-01/02` выявили
SELECT * без FROM с нулём колонок и fatal XX000; guard в pass1 исправил этот
новый случай: теперь ERROR 42000 и recovery в I, проверено в `select-03`.

Повторить регрессии можно существующими инструментами
`pgwire_simple_query_smoke.py`, `pgwire_extended_query_smoke.py`,
`native_baseline.py` с тем же `--firebird-dir` и новыми `--work-dir`.
Для первых двух использованы
`--libpq-include-dir /tmp/firebird-pgwire-1c-deps/libpq-dev/usr/include/postgresql`
и `--libpq-library /lib/x86_64-linux-gnu/libpq.so.5`; Simple дополнительно
`--driver-python /usr/bin/python3`. Заголовки libpq-dev 14.24 только распакованы
в `/tmp` через `apt-get download`/`dpkg-deb -x`, без системной установки.

До изменения на отдельной пустой БД сделаны 24 наблюдения исходных selected SQL:
21 syntax error, один unknown relation, COMMIT/ROLLBACK — успешные no-op;
после каждого восстановлен SELECT 42/I. Для recorded execute использовался
только PQprepare/Describe, без догадок о Bind. Это baseline, не replay/приёмка:
`/tmp/firebird-pgwire-1c-baseline-01/summary.json`. Вызов harness с `--baseline
--capture-dir /home/aleksey/dev/1c/captures/20261109T105635Z` сохраняет provenance
и SHA исходного SQL; полный capture в Git не копируется.

Эталонный Postgres Pro 18.6 проверен в собственном initdb-кластере:
`/tmp/pgwire-1c-reference-4icgiiyt/summary.json`, шесть групп PASS (original
01–03, metadata/identity, фактические SHOW values, batch preparse/rollback/E,
backslash escapes). Рабочая `gilev_demo_ru` не менялась. Эти измерения
подготавливают следующие этапы, но не означают их реализации в Firebird.
Эффекты planner settings, PG locks, mchar/bytea и реальный запуск 1С здесь
не проверялись. Прежние ON CONNECT shutdown и READ CONSISTENCY + AUTO COMMIT
ограничения не исправлялись и заново не исследовались.

Новых build/runtime зависимостей или явно загружаемых модулей нет; тесты
используют прежние Python/libpq/compiler/strace. `readelf -d` и `ldd` сравнены
с исходной сборкой в `gen/Debug/firebird`; полный manifest:
`/tmp/firebird-pgwire-1c-deps/dependencies.json`. Прямые зависимости PgWire:
libfbclient.so.2, libtommath, libtomcrypt.so.1, libm.so.6, libgcc_s.so.1,
libc.so.6, ld-linux-x86-64.so.2. В данной Debug сборке tommath.so.0 вместо
системной tommath.so.1 из baseline: это существующая configure-опция builtin,
а не новая зависимость кода. У baseline также есть транзитивная libgmp.so.10,
у bundled-варианта её нет. Список plugin libraries одинаков; Engine14/Srp
используются как прежде, libpq остаётся только клиентом проверок.

Проверенные SHA256: PgWire
`4e01d83bf9e293a2b9f5744ef09fe1962dd41f92e589c0850c8517d91812e9cb`,
Engine14 `a395ba7a1349090bc4eec3dca3975e83486514add55151906da2d16e024310c1`.
Новый harness сохраняет также source HEAD/diff, tool и compat script hashes.

## История предыдущих этапов

Правка от 14 октября 2026 года — P9 клиентская приёмка, база `241c234627`. Целевой M2 Linux/Super IPv4 loopback принят для совместимого SQL и реализованных codecs: 17 клиентских/raw-wire групп и регрессии ниже. Проверен настоящий Psycopg 3.3.6 с libpq 18.6; его grammar/cache ограничения зафиксированы отдельно. Следующий этап — P10: remote TCP/TLS и CancelRequest. В P9 production-код не менялся. Известные native ON CONNECT shutdown и READ CONSISTENCY + AUTO COMMIT ограничения сохраняются; предыдущие разделы оставлены как история.

Дата: 12 октября 2026 года. HEAD до текущего P5: `3657d6a5c1`, baseline master Firebird 6 `bf94598f8deefeb7cc4a8217ee43b3040d5d6650`. Предыдущие разделы сохраняют историю проверок, включая впоследствии удалённые engine metadata и native metadata executable.

Текущий этап: **P5 прошёл целевую Linux/Super IPv4 loopback приёмку** с указанным ниже native AUTO COMMIT ограничением. Production changes ограничены PgWire; parser/DSQL/public metadata не менялись. Полный SQL передаётся штатному prepare без изменений. Прямые драйверы — C libpq 14.24 и psycopg2 2.9.11/libpq 17.6. Следующий этап — P6, серверная приёмка M1; M1–M3 не приняты. При проверках найден отдельный native Debug defect сочетания AUTO COMMIT с READ COMMITTED READ CONSISTENCY, воспроизводимый без PgWire.

## Окружение и сборка

Linux x86-64, GCC 13.4.0, GNU Make 4.3, autoconf 2.71. Используется штатная POSIX developer/Debug сборка вне дерева исходников. ICU/zlib доступны; tommath/tomcrypt выбраны bundled. Отсутствие pkg-config не мешает этому configure workflow.

```sh
mkdir -p /tmp/firebird-pgwire-build
cd /tmp/firebird-pgwire-build
/home/aleksey/dev/my_firebird/firebird/autogen.sh --enable-developer --with-builtin-tommath --with-builtin-tomcrypt > configure-output.log 2>&1
make -j2 > build-output.log 2>&1
```

Configure и штатная baseline-сборка завершились с кодом 0. Финальный проход `make -j4 > baseline-final-build.log 2>&1` подтвердил сборку исходного baseline; конфигурационные изменения P1 применялись после baseline smoke. Логи расположены в указанной build directory и не входят в git.

## Native baseline

```sh
python3 pg_wire_protocol/tools/native_baseline.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-native-baseline-tcp-20261009
```

PASS. Отдельная копия runtime, собственные UTF8 database/security DB/locks, `ServerMode=Super`, `AuthServer=AuthClient=Srp256`, `WireCrypt=Required`. Версия сервера/isql — `LI-T6.0.0.2199-dev Firebird 6.0 Initial`. Проверено настоящее native TCPv4 соединение с созданным пользователем PGWIRE_TEST: SELECT 42, CURRENT_USER, MON$SERVER_PID, commit, rollback, отказ неверному паролю и успешный reconnect после отказа. Сервер остановлен штатно, exit 0.

Свидетельство: `/tmp/firebird-pgwire-native-baseline-tcp-20261009/summary.json` и соседние sanitized logs. Временный TCP port — 52813; для повторного запуска скрипт выбирает свободный порт и требует новый work directory. Пароль генерируется в памяти и не передаётся в command arguments или отчёт.

Первый запуск внутри sandbox был остановлен запретом создания socket (`EPERM`), до bootstrap. После разрешённого запуска вне sandbox проверка прошла; это ограничение среды, а не ошибка Firebird. Используемые рабочие экземпляры пользователя не изменялись.

## Проверка изменений P1

После добавления двух global parameters полная пересборка `make -j4 > p1-build-output.log 2>&1` завершилась успешно, exit 0.

Команды сборки/запуска конфигурационных tests:

```sh
cd /tmp/firebird-pgwire-build/gen
make -j4 TARGET=Debug /tmp/firebird-pgwire-build/gen/Debug/firebird/tests/common_test > /tmp/firebird-pgwire-build/p1-tests-build.log 2>&1
/tmp/firebird-pgwire-build/gen/Debug/firebird/tests/common_test --run_test=WireProtocolConfigTests --report_level=detailed --log_level=test_suite > /tmp/firebird-pgwire-build/p1-wire-config-tests.log 2>&1
/tmp/firebird-pgwire-build/gen/Debug/firebird/tests/common_test --run_test=CommonClassesSuite/ConfigFileTests --report_level=detailed --log_level=error > /tmp/firebird-pgwire-build/p1-config-regression.log 2>&1
```

| Проверка | Результат |
|---|---|
| Сборка common_test | PASS, exit 0 |
| WireProtocolConfigTests | PASS: 11 cases, 168 assertions |
| Существующие ConfigFileTests | PASS: 3 cases, 4 assertions |
| Native TCP smoke на изменённой сборке | PASS: все проверки baseline, shutdown exit 0 |
| Markdown links, Python syntax, git diff --check | PASS |

Новые tests проверяют global/default registration и невозможность per-database override; blank lists; сохранение позиций до загрузки; диапазон/overflow/формат портов; пустые элементы; несовпадающие длины; дубли; недопустимые разделители в configured name; отсутствие частичного результата при ошибке. Остальные suites common_test не входят в этот целевой запуск.

Повторный runtime запуск из корня workspace:

```sh
python3 pg_wire_protocol/tools/native_baseline.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-native-p1-20261009
```

Свидетельство: `/tmp/firebird-pgwire-native-p1-20261009/summary.json`; TCPv4 port 37677, Super/Srp256, версия `LI-T6.0.0.2199-dev`. Запуск использовал разрешённый локальный TCP вне sandbox; runtime и security database изолированы. Успех относится к native protocol, а не к пока отсутствующему PostgreSQL endpoint.

Первая конфигурационная часть P1 проверена. Результаты следующего среза ABI/loading приведены ниже; workers/listeners и интеграция lifecycle сервера остаются будущей работой. Source changes не затрагивают SQL parser и родной wire dispatch.

## Границы первого среза

Проверяются регистрация двух global settings и чистый parser позиционных списков. Проверки загрузки библиотек, listeners, native-port conflicts, TLS, SQL metadata и PgWire clients относятся к последующим частям P1–P3 и пока не выполнены.

Для будущего прямого C libpq harness отсутствуют development headers/linker symlink в стандартных путях этой машины; runtime libpq сам по себе их не заменяет. Это не препятствует текущей сборке Firebird и native smoke, но должно быть закрыто до клиентской приёмки M0.

## Второй срез P1: ABI, loading и session ownership

9 октября 2026 года, HEAD до изменений `e912b12560`. В том же Linux/Debug build добавлены TYPE_WIRE_PROTOCOL и четыре CLOOP интерфейса. Существующие номера plugin types и версии старых интерфейсов сохранены. C++/Pascal исходники сгенерированы штатным CLOOP; C header создаётся в build runtime, а не как новый tracked source.

```sh
cd /tmp/firebird-pgwire-build/gen
make TARGET=Debug updateCloopInterfaces > /tmp/firebird-pgwire-build/p1-abi-cloop.log 2>&1
cd /tmp/firebird-pgwire-build
make -j4 > p1-abi-build.log 2>&1
./config.status --file=gen/Makefile:builds/posix/Makefile.in --file=gen/make.shared.variables:builds/posix/make.shared.variables > p1-abi-make-config.log 2>&1
make -C gen -j4 TARGET=Debug wire_tests > p1-abi-tests-build.log 2>&1
make -j4 > p1-abi-final-build.log 2>&1
```

Все команды PASS, exit 0. Финальная сборка включает ProtocolPlugin.o в daemon. Для частичного config.status нужны явные `--file=output:input`: сокращённый вызов с именами targets в этом build не восстанавливает MAKE_SRC_DIR и завершается до генерации Makefile.

Новый C API проверен GCC с `-std=c11 -Wall -Wextra -Werror -fsyntax-only`: создание/запуск/остановка/release session, read/write/shutdown connection, значения TYPE_WIRE_PROTOCOL/TYPE_COUNT. Проверочный source — `/tmp/firebird-pgwire-build/wire_abi_c_check.c`. Pascal binding сгенерирован; Pascal compiler отсутствует, отдельная компиляция им не проводилась. Windows и другие server modes в этом срезе не проверялись.

### Настоящий PluginManager и тестовая библиотека

```sh
cd /home/aleksey/dev/my_firebird/firebird
python3 pg_wire_protocol/tools/wire_plugin_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-wire-abi-final-20261009
```

PASS, exit 0. Свидетельства — `summary.json`, `events.log`, `stdout.log`, `stderr.log` в этом work directory. Отдельные firebird.conf/plugins.conf/locks; runner использует пересобранный libfbclient и динамически загруженный `tests/libWireTestPlugin.so`. Он не поднимает сервер и не открывает TCP; byte stream реализован mock-классом в runner.

| Проверка | Результат |
|---|---|
| First и Second: одна библиотека/factory, разные named Config и instances | PASS; один module.load |
| Missing между ними | PASS; отклонён, Second сохранил порт 31003 |
| Required missing module и неверный RegisterName | PASS; отказ загрузки |
| Factory NULL/error, createSession NULL/error и object+error, run error | PASS; исходная injected error сохранена, session references освобождены |
| Недостаточная версия IWireProtocol/IWireSession | PASS; isc_interface_version_too_old до первого вызова нового метода, объекты освобождены |
| Partial read/write, EOF и повторный run | PASS; bytes сохранены, второй run отклонён |
| Удаление endpoint до завершения session | PASS; host/connection/plugin удерживаются wrapper |
| First освобождается при активном run второго alias | PASS; final callbacks Second остаются валидными |
| Stop до run, repeated stop, stop при blocked read | PASS; ожидание прервано менее чем за 1 секунду, shutdown идемпотентен |
| Session release → plugin release → module cleanup/unload | PASS; библиотека выгружена при явном fb_shutdown после освобождения sessions |

Отдельно прошёл `make TARGET=Debug run_wire_tests` из build `gen`; лог — `p1-abi-make-smoke.log`. После добавления version cases тестовые binaries пересобраны (`p1-abi-tests-final-build.log`), итоговый ручной запуск приведён выше. Targets имеют явную зависимость от актуального libfbclient; dry-run с изменённым PluginManager.cpp подтвердил его пересборку (`p1-abi-library-dependency.log`). Тестовая библиотека не включена в production plugin/install targets. Проверены callback lifetime и loader shutdown в runner; это не server shutdown с работающими TCP/SQL sessions. Transport timeout/clamping и OS I/O failures mock-тест не проверяет.

### Регрессия и границы

После финальной сборки пересобран common_test и повторены прежние два фильтра: WireProtocolConfigTests — 11 cases / 168 assertions PASS; ConfigFileTests — 3 cases / 4 assertions PASS. Логи — `p1-abi-common-tests-build.log`, `p1-abi-config-tests.log`, `p1-abi-config-regression.log`.

```sh
python3 pg_wire_protocol/tools/native_baseline.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-native-abi-20261009
```

PASS: все 9 checks, Srp256/Super/TCPv4, SELECT, identity, commit/rollback, wrong password, reconnect и штатный shutdown exit 0. Временный port 41291; сервер/isql `LI-T6.0.0.2199-dev`. Запуск выполнен с разрешённым локальным TCP вне sandbox на отдельной копии runtime/security DB; сервер после проверки остановлен. Результат — `/tmp/firebird-pgwire-native-abi-20261009/summary.json`.

ABI/loader/session ownership проверены для этого среза. Python AST, локальные ссылки документации и git diff --check — PASS. До завершения P1 остаются strict configuration на общем cached snapshot PluginManager, bind/listeners, resource limits, registry/workers и интеграция startup/shutdown. Auth/TLS/engine services пока отсутствуют; M0 не принят. Native smoke подтверждает прежний native путь при отключённых дополнительных endpoints, а не сосуществование с ещё отсутствующим TCP listener.

## Исправления по review второго среза

9 октября 2026 года, HEAD до изменений `0fd7261a53`. Transport shutdown перенесён перед plugin stop. Implicit `Config::getPlugins(TYPE_WIRE_PROTOCOL)` теперь отказывает: positional settings должны проверяться вместе, затем loader получает одно имя. Direct loader name с запятой отвергается до обращения к PluginManager.

- Полная `make -j4` — PASS (`p1-review-build.log`).
- Пересборка wire_tests/common_test — PASS (`p1-review-tests-build.log`, финальное уточнение fixture — `p1-review-tests-final-build.log`).
- WireProtocolConfigTests — 12 cases / 171 assertions PASS; ConfigFileTests — 3 cases / 4 assertions PASS (`p1-review-config-tests.log`, `p1-review-config-regression.log`).
- Real-plugin smoke — PASS; итоговые `summary.json` и `events.log` в `/tmp/firebird-pgwire-review-final-20261009`. Команда прежняя, с новым `--work-dir`.
- StopMutex fixture намеренно берёт в stop mutex, удерживаемый вокруг read. Stop завершается менее чем за 1 секунду; отдельно проверяется, что mock read не вышел по своему страховочному timeout. Это защитная проверка порядка, а не разрешение блокирующего plugin stop в ABI.
- Для имени `First,Second` отказ получен до module entrypoint. Marker `host.valid_load_begin` предшествует единственному module.load.
- Проверка чувствительности тестов: отдельные копии loader под `/tmp` с прежним порядком stop и разрешённой запятой отклонены harness. Первая достигает страховочного timeout, вторая преждевременно загружает модуль. Рабочие исходники и binaries при этом не заменялись; итог — `/tmp/firebird-pgwire-review-mutants-20261009/summary.json`.
- Native TCP smoke — PASS, все 9 checks, shutdown exit 0. Изолированная копия `/tmp/firebird-pgwire-native-review-20261009`, port 51649, Super/Srp256/TCPv4, версия `LI-T6.0.0.2199-dev`.

На момент этого среза native-port conflict ещё не проверялся кодом: server startup/listeners оставались следующим срезом. ARCHITECTURE/PLAN/TEST_PLAN уточнены: до загрузки модулей нужны фактические native endpoints с учётом `-p`, service-name resolution, disabled TCP и bind/dual-stack. Результат следующего среза приведён ниже.

## Третий срез P1: strict Config и Linux/Super TCP host

Этот раздел сохраняет результаты первоначального варианта host. По решению пользователя расширение ConfigManager и отдельный strict resolver затем удалены; соответствующие config tests ниже относятся только к тому варианту. Актуальное поведение и повторная проверка описаны в следующем разделе.

9 октября 2026 года, HEAD до изменений `3e9fa366e2`. В той же Linux/Debug сборке реализованы строгий lookup named Config на cached snapshot PluginManager, TCP host, POSIX transport и startup/shutdown integration. `IConfigManager` расширен до версии 4 с сохранением legacy lookup; C++, Pascal и C bindings обновлены штатным `updateCloopInterfaces`.

```sh
cd /tmp/firebird-pgwire-build
./config.status --file=gen/Makefile:builds/posix/Makefile.in --file=gen/make.shared.variables:builds/posix/make.shared.variables
make -j4 > p1-host-checked-build.log 2>&1
make -C gen -j4 TARGET=Debug wire_tests /tmp/firebird-pgwire-build/gen/Debug/firebird/tests/common_test > p1-host-checked-tests-build.log 2>&1
gen/Debug/firebird/tests/common_test --run_test=WireProtocolConfigTests --report_level=detailed --log_level=error > p1-host-config-tests.log 2>&1
gen/Debug/firebird/tests/common_test --run_test=CommonClassesSuite/ConfigFileTests --report_level=detailed --log_level=error > p1-host-config-regression.log 2>&1
```

Все команды PASS. Полная штатная сборка включает host/transport в daemon. WireProtocolConfigTests: 12 cases / 171 assertions; существующие ConfigFileTests: 3 cases / 4 assertions. C binding проверен `gcc -std=c11 -Wall -Wextra -Werror -fsyntax-only` для нового lookup, его версии/flag и прежних wire interfaces; sources — `wire_config_c_check.c` и `wire_abi_c_check.c` в build directory. Pascal compiler отсутствует.

Из корня workspace выполнены:

```sh
python3 pg_wire_protocol/tools/plugin_config_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-plugin-config-20261009
python3 pg_wire_protocol/tools/wire_plugin_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-plugin-host-20261009
LD_LIBRARY_PATH=/tmp/firebird-pgwire-build/gen/Debug/firebird/lib \
  /tmp/firebird-pgwire-build/gen/Debug/firebird/tests/wire_socket_test
python3 pg_wire_protocol/tools/wire_host_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-host-tcp-20261009
python3 pg_wire_protocol/tools/native_baseline.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-native-host-disabled-20261009
```

| Проверка | Результат |
|---|---|
| Strict plugin config | PASS: 26 изолированных cases; missing/duplicate Plugin/Config, ConfigFile, nested reference, loader keys, includes/case, legacy fallback и bad arguments |
| Общий config snapshot | PASS: после инициализации cache и замены файла strict lookup и factory видят исходный named Config |
| ABI/plugin lifecycle regression | PASS: настоящая shared library, aliases/failure paths/ownership/stop, mock transport |
| POSIX socket transport | PASS: partial I/O, EOF, deadline/clamping, EINTR, sticky shutdown, пробуждение read, fd ownership/setup failure, TCP backpressure и broken peer без SIGPIPE |
| Native + два wire aliases | PASS: native SQL и два дополнительных TCP listener в одном процессе; одна test library, разные Config/instances; Missing между aliases не сдвигает ports |
| Ошибки endpoint | PASS: отсутствующий Config, Port override, remote bind, TLS, duplicates и недопустимые Workers/QueueSize/IOTimeout; валидные endpoints и native работают |
| Workers/queue/deadline | PASS: переполнение закрывает лишний socket, queued client получает worker после освобождения, queued socket истекает при занятом worker, idle read завершается по timeout |
| Server shutdown | PASS: SIGTERM пробуждает idle/partial sessions, session release предшествует plugin/module cleanup, exit 0 без forced kill |
| Native-port conflict | PASS: `-p` отличается от RemoteServicePort; конфликт с фактическим портом отклонён до module entrypoint, native SQL работает |
| Пустые списки по умолчанию | PASS: 9 native baseline checks — Srp256/TCPv4, вычисляемый SELECT, identity, commit/rollback, неверный пароль, reconnect и shutdown exit 0 |

Сетевые проверки запускались с разрешённым локальным TCP вне sandbox. Каждый server harness использует отдельные runtime/security DB/data/locks под `/tmp`; пароли генерируются в памяти и не попадают в argv или отчёт. Все тестовые серверы остановлены.

Свидетельства: `summary.json` и соседние logs/events в указанных work directories. Transport log — `/tmp/firebird-pgwire-build/p1-host-socket-tests.log`. В coexist run native port 50237, First 55369, Second 33753; shutdown 0.114 секунды. Native conflict run: фактический port 57697 при configured 45271, shutdown 0.114 секунды. Default-disabled native run: port 35041. Это временные порты конкретного запуска; harness выбирает свободные заново.

В CMake UNIX server source list добавлены три host objects, чтобы POSIX startup hook имел реализацию. `cmake -P /tmp/firebird-wire-cmake-check.cmake` — PASS: declarations включают каждый существующий source ровно один раз в UNIX fbserver, без добавления в client library/Windows target. Это проверка source lists; отдельная полная CMake-сборка, Windows, SuperClassic/Classic runtime, service-name/Unix-only/IPv6 native cases пока не проверялись. Проверка конфликта использует `getsockname`, но runtime PASS выше относится только к IPv4 loopback с `-p`. Общие максимумы 16 endpoints / 64 workers заданы кодом; их граничная нагрузочная приёмка остаётся отдельной проверкой.

После обновления комментариев firebird.conf финальный `make -j4 > p1-host-packaging-build.log 2>&1` — PASS, exit 0. Python AST четырёх tools, локальные ссылки 13 документов и `git diff --check` — PASS.

Начальный Linux/Super host проверен с тестовым byte echo. Он не реализует PostgreSQL framing, authentication или SQL. Следующий функциональный этап — P2; полное межплатформенное покрытие P1 и M0 этим результатом не закрываются.

## Упрощение конфигурации: существующий ConfigManager

9 октября 2026 года, тот же HEAD `3e9fa366e2`. По решению пользователя host переведён на существующий `getPluginConfig(name)` со штатными правилами разрешения конфигурации. Конфигурация подготавливается до запуска сервера. Новый метод, flag и strict resolver удалены, отдельные PluginConfigSmoke/tool и build target также удалены. Проверки transport settings и native-port conflict сохранены.

После штатного `updateCloopInterfaces` IDL, C++/Pascal bindings и `PluginManager.cpp` побайтно совпадают с HEAD. Generated C binding имеет прежнюю `IConfigManager_VERSION == 3`; `gcc -std=c11 -Wall -Wextra -Werror -fsyntax-only` для `wire_config_existing_api_check.c` и `wire_abi_c_check.c` в build directory — PASS.

`wire_host_smoke.py` теперь проверяет первый alias через module `.conf` fallback, второй — через named Config. Greeting первого alias подтверждает чтение настроек factory, проверки его Workers/QueueSize/IOTimeout — чтение host. Отдельный отказ из-за отсутствия named Config больше не требуется. Результаты strict resolver из предыдущего раздела не являются проверкой текущего кода.

```sh
cd /tmp/firebird-pgwire-build
./config.status --file=gen/Makefile:builds/posix/Makefile.in --file=gen/make.shared.variables:builds/posix/make.shared.variables > p1-config-api-revert-make.log 2>&1
make -C gen TARGET=Debug updateCloopInterfaces > p1-config-api-revert-cloop.log 2>&1
make -j4 > p1-config-api-revert-build.log 2>&1
make -C gen -j4 TARGET=Debug wire_tests > p1-config-api-revert-wire-tests-build.log 2>&1
cd /home/aleksey/dev/my_firebird/firebird
python3 -B pg_wire_protocol/tools/wire_plugin_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-plugin-config-revert-20261009
python3 -B pg_wire_protocol/tools/wire_host_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-host-file-fallback-20261009
```

Все команды PASS, exit 0. Plugin smoke подтвердил загрузку/ошибки/ownership и порядок stop/release с настоящей библиотекой. TCP host smoke: 11 checks PASS, включая file fallback для host и factory, named Config второго alias, native SQL/auth/transactions, queue/worker limits, timeouts, конфигурационные ошибки endpoint, конфликт фактического native-порта и shutdown. Оба тестовых сервера завершились с exit 0 без forced kill; summary/logs сохранены в указанных work directories. Сетевой запуск выполнен с разрешённым локальным TCP на изолированном runtime под `/tmp`.

Python AST оставшихся трёх tools, ссылки документации и `git diff --check` — PASS. Интерфейс ConfigManager, его реализация и сгенерированные C++/Pascal bindings в текущем рабочем дереве не отличаются от HEAD до среза TCP host.

## P2: PgWire Startup, authentication и локальный attachment

9 октября 2026 года, HEAD до изменений `69712ca686`. Новый production модуль `libPgWire.so` загружается через существующий wire plugin host. PgWire разбирает сообщения протокола; SQL не классифицирует и не изменяет. Host проверяет `DatabaseAliases` и per-database policy, выполняет обмен между Srp256 auth plugins в памяти и создаёт attachment через локальный Engine14. Новые методы добавлены только в `IWireServerHost` (version 4). `IConfigManager` остаётся version 3; его IDL и `PluginManager.cpp` не изменены.

### Сборка и codec

В прежней POSIX Debug build directory выполнены штатная генерация CLOOP и полная пересборка:

```sh
cd /tmp/firebird-pgwire-build
./config.status --file=gen/Makefile:builds/posix/Makefile.in --file=gen/make.shared.variables:builds/posix/make.shared.variables
make -C gen TARGET=Debug updateCloopInterfaces
make -j4 > p2-build.log 2>&1
make -C gen -j4 TARGET=Debug wire_tests pgwire_tests > p2-tests-final-build.log 2>&1
```

PASS. Последующие исправления auth helper пересобраны полной `make -j4`, финальный production build — `p2-auth-pool-build.log`, exit 0. Исходный `wire_socket_test` target захватывал лишний `Server.o`; его зависимости ограничены `SocketConnection.o` и общими объектами. Production PgWire входит в стандартный plugins target; codec test остаётся отдельным opt-in target.

```sh
LD_LIBRARY_PATH=/tmp/firebird-pgwire-build/gen/Debug/firebird/lib \
  /tmp/firebird-pgwire-build/gen/Debug/firebird/tests/pgwire_protocol_test \
  > /tmp/firebird-pgwire-build/p2-codec-tests.log 2>&1
```

PASS: golden bytes, fragmented/coalesced input, partial writes, EOF/transport errors, limits, NUL/UTF8, SSL/GSS отказ, сохранение SQL bytes и общие deadlines. Password buffer очищается после использования. Startup и Password используют один сетевой deadline; прогресс отдельными байтами не продлевает его.

Generated C API проверен `gcc -std=c11 -Wall -Wextra -Werror -fsyntax-only`: `/tmp/firebird-pgwire-build/p2_host_abi_c_check.c` и прежний `wire_abi_c_check.c`. PASS, включая версии ConfigManager/host и новые attachment methods. Pascal binding сгенерирован, Pascal compiler отсутствует. Проверка CMake source lists (`cmake -P /tmp/firebird-p2-cmake-check.cmake`) подтверждает server-only размещение host/authentication; это не полная сборка CMake.

### Настоящий PostgreSQL TCP endpoint

```sh
cd /home/aleksey/dev/my_firebird/firebird
python3 -B pg_wire_protocol/tools/pgwire_startup_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p2-startup-04
```

PASS: **52 checks**, exit 0. Использованы изолированные runtime, security database, application databases и временные loopback ports. Пароли генерируются тестом и не включаются в command line или отчёт. Сетевой запуск разрешён вне sandbox. Свидетельство — `/tmp/firebird-pgwire-p2-startup-04/summary.json`, sanitized logs и `server-connect.trace` рядом.

| Проверка | Результат |
|---|---|
| Настоящий libpq через ctypes в отдельном процессе | PASS: libpq 14.24 (`140024`), protocol 3, connect, два Query с `0A000`, transaction status IDLE |
| Raw Startup/authentication и две identities | PASS: native observer видит разных пользователей, Srp256, тот же server PID, TCPv4/peer port, UTF8 и application name |
| AuthenticationOk только после успешного attach | PASS: ON CONNECT trigger может отклонить вход после проверки credentials; AuthenticationOk в этом случае отсутствует |
| Неверный пароль, неизвестный/inactive user | PASS: отказ до AuthenticationOk, следующий допустимый connect работает |
| Alias allowlist, unknown alias, path/URI | PASS: только разрешённые локальные aliases; отсутствие database означает user, explicit empty database отклонён |
| RemoteAccess, AuthServer policy, remote security DB | PASS: отказ; клиентские параметры не меняют выбранную конфигурацию |
| Application Providers=Remote,Loopback | PASS: host всё равно создаёт локальный engine attachment |
| SSLRequest/GSSENCRequest и malformed packets | PASS: `N`, отказ повторного negotiation, bounds до payload allocation, version/options/keys/NUL/UTF8/EOF |
| Общие startup/password/query deadlines | PASS: частичные чтения с прогрессом не обновляют timeout; проверено с IOTimeout=1000 ms |
| Query recovery | PASS: `0A000` + Ready `I`, следующий Query обработан; SQL execution не заявляется |
| Cleanup и shutdown | PASS: session attachments закрыты; idle authenticated, password wait и incomplete startup не мешают остановке |
| Native protocol вместе с PgWire | PASS: SQL, credentials, commit и rollback |

Все три запуска сервера (основной, deadlines, syscall trace) завершились exit 0 без forced kill; shutdown занял около 0.165 секунды.

`strace` наблюдал `connect()` сервера и всех threads от запуска до shutdown, включая успешный/неуспешный auth, проверку policy и native probe. Backend network connects: **0**. Обнаружен 51 вызов к службам ОС через Unix sockets: 46 к `/run/systemd/userdb/io.systemd.DynamicUser` и 5 к `/var/run/nscd/socket`. Auditor разрешает только эти два точных NSS-пути и учитывает их отдельно; TCP/IPv6, другие Unix sockets, abstract addresses и нераспознанные sockaddr дают FAIL. Сохранённый trace и семь отрицательных синтетических примеров проверили это различие.

В предварительных запусках исправлены: использование stack-only default memory pool в heap `ClientBlock`, активация роли native observer через `SET ROLE`, классификация обращений к NSS в trace auditor. Итоговый PASS относится к полному повторному запуску после исправлений, а не к частично прошедшим попыткам.

### Регрессия и границы результата

```sh
python3 -B pg_wire_protocol/tools/wire_plugin_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p2-plugin-regression-20261009
python3 -B pg_wire_protocol/tools/wire_host_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p2-host-regression-20261009
```

PASS. Real-plugin regression проверил loading/errors/ownership/stop/release. TCP host regression: 11 checks, включая module `.conf` fallback, named Config второго alias, native auth/transactions, positional ports, queues/workers/timeouts, native-port conflict до module load и shutdown. Оба сервера host regression завершились exit 0 без forced kill. Python AST, локальные Markdown links и `git diff --check` также прошли.

Результат относится к Linux/Super и IPv4 loopback без TLS. Psycopg 2/3 в окружении отсутствуют; проверен именно runtime libpq через ctypes, C development headers для будущего harness по-прежнему отсутствуют. Windows, другие server modes и полная CMake сборка не проверялись. Security database должна быть локальной и иметь Engine14 среди Providers. Синхронный auth/attach до получения handle не имеет жёсткого прерывания по сетевому IOTimeout: helper проверяет stop между callbacks, а protocol повторно проверяет deadline перед AuthenticationOk. Проверки transport shutdown не доказывают preemption произвольного auth plugin.

P2 не выполняет engine prepare/execute. SELECT, типы результатов, транзакции, Extended Query, Cancel и TLS остаются в следующих этапах; M0 ожидает P3.

## P3/M0: настоящий SELECT через движок — исторический Linux/Super PASS

9 октября 2026 года, HEAD до изменений `d072686153`. `Execute.cpp` передаёт исходные SQL bytes и длину в `IAttachment::prepare`, затем использует `IStatement::openCursor` и `IResultSet::fetchNext`. Одну implicit READ COMMITTED/write/nowait transaction разделяют prepare/open/fetch. После EOF закрываются cursor/statement и выполняется commit; только затем отправляется CommandComplete. Ошибка приводит к rollback, ошибка cleanup или commit — к завершению сессии. SQL lexer/splitter/rewriter в plugin не добавлен.

Новый `isc_info_sql_stmt_origin=34` сохраняет исходный AST category до DSQL transformations: SELECT=1, OTHER=0. В сочетании с прежними type/flags это позволяет отклонять DML RETURNING и selectable EXECUTE BLOCK до исполнения. Сам origin не означает read-only: SELECT может вызывать routines с эффектами. Legacy type/flags и ABI интерфейсов не меняются; Pascal constants перегенерированы.

Начальный result profile: SMALLINT/INTEGER/BIGINT при scale=0/subtype=0, NULL, text format, OID 21/23/20, engine aliases. Прочие output types и input parameters отвергаются по metadata до openCursor. RowDescription/DataRow/CommandComplete проверяют sizes/UTF8 до allocation и записи. Ошибка частичной записи завершает transport без дописывания нового frame. `ErrorStatus.cpp` рассматривает полный структурированный status vector, включая explicit SQLSTATE; строки и числовые аргументы не считаются кодами ошибок.

### Сборка и самостоятельные unit tests

```sh
cd /tmp/firebird-pgwire-build
./config.status --file=gen/Makefile:builds/posix/Makefile.in --file=gen/make.shared.variables:builds/posix/make.shared.variables
make -j2 > p3-final-build.log 2>&1
make -C gen -j2 TARGET=Debug pgwire_tests statement_origin_tests > p3-tests-build.log 2>&1
LD_LIBRARY_PATH=/tmp/firebird-pgwire-build/gen/Debug/firebird/lib \
  gen/Debug/firebird/tests/pgwire_protocol_test > p3-codec-tests.log 2>&1
LD_LIBRARY_PATH=/tmp/firebird-pgwire-build/gen/Debug/firebird/lib \
  gen/Debug/firebird/tests/pgwire_error_test > p3-error-tests.log 2>&1
```

Все команды PASS, exit 0. Полная пересборка после public header change записана в `p3-build-retry.log`; финальный проход и тестовые targets — в логах выше. Первый `make -j4` остановился из-за внутреннего compiler error GCC 13.4.0 в неизменённом `TimeZoneUtil.cpp`/`<atomic>`. Повторный `make -j2` прошёл без изменения этого кода.

Codec unit tests включают независимые literal bytes metadata/rows/tags, NULL/empty/UTF8, integer text, partial writes, signed metadata, exact bounds/field counts и обрыв DataRow по deadline. Error-status unit tests проверяют nested cause и explicit SQLSTATE за пределами первых 20 элементов, `cstring`, ложные code values внутри аргументов и приоритеты shutdown/cancel/permission. Оба executable сообщили PASS.

Production PgWire содержит отдельные Execute/ErrorStatus objects. POSIX build проверен; CMake definitions обновлены, полная CMake сборка не выполнялась. Pascal constants сгенерированы штатным `updateCloopInterfaces`; Pascal compiler отсутствует.

### Клиенты и изолированный runtime

Для прямого C test использованы настоящие headers из Ubuntu `libpq-dev 14.24-0ubuntu0.22.04.1`, извлечённого в `/tmp/firebird-pgwire-p3-deps/libpq-dev` без системной установки. Системный libpq — 14.24. В отдельном venv `/tmp/firebird-pgwire-p3-deps/venv` установлен psycopg2-binary 2.9.11, который использует свой libpq 17.6. Версии, SHA headers/source и build command фиксирует harness; ctypes-only smoke не подменяет C gate.

```sh
cd /home/aleksey/dev/my_firebird/firebird
python3 -B pg_wire_protocol/tools/pgwire_query_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p3-query-02 \
  --libpq-include-dir /tmp/firebird-pgwire-p3-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5 \
  --driver-python /tmp/firebird-pgwire-p3-deps/venv/bin/python \
  --statement-origin-test /tmp/firebird-pgwire-build/gen/Debug/firebird/tests/statement_origin_test
```

PASS: **13 групп проверок**, все четыре server phases завершились exit 0 без forced kill. Shutdown — 0.114–0.164 секунды, включая остановку с выполняющимся SELECT. Результаты и sanitized logs: `/tmp/firebird-pgwire-p3-query-02/summary.json` и соседние файлы. Использованы отдельные runtime/security DB/application databases/locks и временные TCP ports. Сетевые тесты и strace запускались с разрешением вне sandbox; рабочие экземпляры пользователя не менялись.

| Проверка | Свидетельство |
|---|---|
| Независимая engine metadata | `statement-origin.log`: 21 SQL case × 2 прохода prepare/free, SELECT/WITH/UNION, RETURNING, BLOCK, DDL/controls; legacy type/flags, cache, combined/single/truncated getInfo и отсутствие execution — PASS |
| Прямой C libpq | `libpq-direct-c.log`: compile headers/runtime 140024, PQexec, 42/OID23/alias answer, 49, integer limits/NULL, 0/many rows, command tags, errors/recovery — PASS |
| psycopg2 | `psycopg2.log`: 2.9.11, compile/runtime libpq 170006, explicit autocommit=True без connection context, значения/типы/NULL/rows/errors/recovery — PASS |
| Raw results и SQL boundary | 0/1/257 rows, UTF8/quoted aliases, integer metadata, partial/coalesced Query, comments/literals с BEGIN/$1/semicolon, batch parser refusal — PASS |
| Identity и права | CURRENT_USER подтверждён integer predicate; второй пользователь получает 42501 на таблицу и затем успешно выполняет свой SELECT — PASS |
| Refusal до execution | INSERT SELECT/UPDATE RETURNING с legacy SELECT cursor, INSERT VALUES RETURNING, selectable BLOCK и unsupported output — 0A000 без RowDescription; sequences не изменились, поэтому rollback не скрывает исполнения |
| Implicit commit/rollback | SELECT над процедурой сохраняет insert после успеха; division error после уже отправленной DataRow откатывает insert, CommandComplete отсутствует, следующий SELECT работает |
| Disconnect/active shutdown | Streaming SELECT действительно исполнился по sequence marker; disconnect откатывает insert и удаляет attachment. Долгий SELECT виден в MON$STATEMENTS и оставляет sequence marker; server shutdown отменяет работу и откатывает insert |
| Ошибки endpoint | Отсутствующий production PgWire module и занятый дополнительный порт диагностируются; native SQL продолжает работать; при bind failure модуль ещё не загружен |
| Локальный engine route | Trace успешного auth/SELECT, неуспешного auth/policy и native observer: 0 backend connects; 51 OS/NSS Unix вызов по тем же двум точным разрешённым путям |

После query/error native monitoring подтверждает отсутствие оставшейся application transaction; после disconnect — отсутствие session attachment. Подготовленный SQL выполняется только после проверки origin/type/flags/input/output metadata. Batch с GEN_ID отвергнут parser до изменения sequence.

Первый запуск `p3-query-01` остановился при C connect: harness ожидал только готовности native listener. Перед driver suite добавлено ожидание успешного PgWire login/Terminate/cleanup и информативная диагностика libpq с удалением пароля. Полный повторный `p3-query-02` прошёл; production binaries между этими попытками не менялись. Первый неуспешный запуск не входит в PASS.

### Регрессия и итог M0

```sh
python3 -B pg_wire_protocol/tools/pgwire_startup_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p3-startup-01
python3 -B pg_wire_protocol/tools/native_baseline.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p3-native-01
```

PASS: startup regression — 52 checks, теперь с реальным SELECT/recovery; native baseline при отключённых wire plugins — 9 checks, прежние SQL/auth/commit/rollback. Все серверы остановлены с exit 0. Native также работал параллельно с PgWire во время query/startup suites. Python AST, локальные Markdown links и `git diff --check` прошли.

M0-01…08 закрыты для указанного Linux/Super loopback profile. Это не приёмка M1: DML/DDL/control statements, дополнительные codecs, EMPTY marker, Extended Query, PostgreSQL grammar, TLS и удалённый bind остаются последующим этапам.

Границы проверки: runtime Windows/Classic/SuperClassic и полная CMake сборка отсутствуют; ошибки close/free/commit не инъецировались, их порядок и consuming ownership проверены review. Истечение engine statement timeout отдельно не измерялось; проверены network deadlines и cancellation при active SQL shutdown. IOTimeout передаётся в IStatement::setTimeout после prepare и не является hard deadline для startTransaction/prepare/auth/cleanup/commit. C++/C tests и actual clients подтверждают текущий профиль, а не прозрачную работу произвольного PostgreSQL-приложения.

## Консольный SQL-клиент для ручной проверки — Linux PASS

9 октября 2026 года, HEAD `0d9546336f`. Добавлен `tools/pgwire_client.py`: Python stdlib/ctypes и настоящая libpq, Simple Query по TCP, повторные `-c`/`-f` на одном соединении и интерактивный ввод с отдельной командой `\g`. Ввод UTF8 передаётся целиком, включая CRLF внутри SQL literals. Клиент показывает результаты, OID, NULL, SQLSTATE и transaction state. Инструкция настройки и совместной проверки с native isql находится в [MANUAL_TESTING.md](MANUAL_TESTING.md). Production-код и серверные возможности этим изменением не расширяются.

Временный integration harness использует существующий `StartupHarness`, отдельную копию runtime/security DB, тестовую application database и свободные TCP ports:

```sh
python3 -B /tmp/pgwire_manual_client_smoke.py \
  --repository /home/aleksey/dev/my_firebird/firebird \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-manual-client-02
```

PASS: **15 проверок**, результат — `/tmp/firebird-pgwire-manual-client-02/summary.json`. Проверены результат 42/OID23/NULL, сохранение attachment между запросами, восстановление после SQL-ошибки, порядок смешанных `-c`/`-f`, UTF8 и сохранение CRLF в file/stdin, отсутствие разбиения SQL, `--stop-on-error`, ошибки authentication/file, интерактивные `\g`/`\p`/`\r`/`\q`. Унаследованные `PGHOSTADDR`/`PGSERVICE` не меняют выбранный endpoint; `PGTARGETSESSIONATTRS` не вызывает дополнительных SQL-проверок.

Через отдельное native TCP-подключение подтверждены тот же server PID и двусторонняя видимость committed changes: native INSERT читается PgWire-клиентом; INSERT внутри selectable procedure, вызванной через PgWire SELECT, виден native observer. Это проверяет implicit commit, а не поддержку прямого INSERT через PgWire. Сервер завершился exit 0 за 0.164 секунды без forced kill. Рабочая БД пользователя не изменялась.

Первый запуск `manual-client-01` прошёл 12 проверок; финальный `manual-client-02` повторил их после исправлений и добавления CRLF/environment cases. Python syntax, `--help`, локальные Markdown links и `git diff --check` также прошли. Новая проверка относится к утилите на Linux/Super loopback; серверная приёмка M0 и её ограничения приведены выше.

По запросу пользователя скрытый ввод пароля заменён на `-P PASSWORD` / `--password PASSWORD` (`-W PASSWORD` — дополнительное короткое имя). Аргумент имеет приоритет перед `PGPASSWORD`; отсутствие обоих даёт ошибку аргументов до подключения. `-d` / `--database` передаёт имя или путь буквально, однако production PgWire по-прежнему принимает только разрешённые alias. Прямое подключение по пути этим изменением не реализуется.

Повторный запуск той же команды с `--work-dir /tmp/firebird-pgwire-manual-client-03` — **18 проверок PASS**. Добавлены успешный вход с аргументом пароля при неверном `PGPASSWORD`, отказ неверному аргументу при правильном `PGPASSWORD` и отказ при отсутствии пароля без интерактивного prompt или загрузки libpq. Пароли не записываются в отчёт; тестовые argv credentials используются согласно выбранному интерфейсу клиента. Результат — `/tmp/firebird-pgwire-manual-client-03/summary.json`. Все прежние CLI/native checks повторены; рабочий экземпляр пользователя не менялся.

## P4 — Simple Query DML/DDL, codecs и EMPTY: исторический Linux/Super PASS

12 октября 2026 года, HEAD до изменений `03be167a24`. Полная POSIX Debug сборка выполнена в `/tmp/firebird-pgwire-build`; рабочий сервер пользователя и его базы не изменялись. Проверенный профиль — Linux x86_64, Super, IPv4 loopback, UTF8/dialect 3, отдельный production PgWire module и локальный Engine14. Публичные metadata items 35–38 и EMPTY diagnostic реализованы движком; SQL lexer/splitter/rewrite во frontend отсутствуют. Generated Pascal constants обновлены штатной сборкой.

### Сборка и unit tests

```sh
cd /tmp/firebird-pgwire-build
./config.status --file=gen/Makefile:builds/posix/Makefile.in \
  --file=gen/make.shared.variables:builds/posix/make.shared.variables
make -j2 > p4-build.log 2>&1
make -C gen -j2 TARGET=Debug pgwire_tests statement_origin_tests > p4-tests-build.log 2>&1
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_protocol_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_error_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_result_test
```

PASS: full build и все три executable unit tests. Protocol test сверяет EmptyQueryResponse с независимыми literal bytes; ErrorStatus test проверяет UNIQUE/FK/CHECK, полные status vectors и приоритет fatal/cancel/permission; ResultCodec test проверяет native buffer layout/bounds, integer limits, точное scaled numeric/INT64_MIN, boolean, NULL, CHAR/VARCHAR, UTF8/NUL и отказ неподдержанным metadata. Новая test link rule включает COMMON_LIB, поскольку internal UTF8/status helpers не экспортируются libfbclient.

Первый build выявил два использования запрещённого copy constructor StatusVector в новых engine diagnostics. Исправлено отдельным созданием и наполнением vector; полный повторный build прошёл. После успешной полной сборки production-код не менялся. Последующие изменения затрагивали ожидания тестов и документацию; финальный native test пересобран через `statement_origin_tests`, лог `p4-metadata-build.log`.

### Полная P4 приёмка

```sh
cd /home/aleksey/dev/my_firebird/firebird
python3 -B pg_wire_protocol/tools/pgwire_simple_query_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p4-simple-02 \
  --libpq-include-dir /tmp/firebird-pgwire-p3-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5 \
  --driver-python /tmp/firebird-pgwire-p3-deps/venv/bin/python \
  --statement-origin-test /tmp/firebird-pgwire-build/gen/Debug/firebird/tests/statement_origin_test
```

PASS: **12 групп**, результат `/tmp/firebird-pgwire-p4-simple-02/summary.json`. Harness создаёт копию runtime, отдельные security/application databases, пользователей, locks и свободные TCP ports. Использованы настоящие headers/runtime C libpq 14.24 и psycopg2 2.9.11 с libpq 17.6; Python connection работает с `autocommit=True`, без connection context. Сетевые проверки/strace выполнялись с разрешением вне sandbox.

| Проверка | Фактический результат |
|---|---|
| Native metadata | 31 DML/control/session + 46 DDL statements, два прохода: origin/command/result/DDL, legacy type/flags, cache reuse, single/combined/truncated getInfo, отсутствие execution при prepare — PASS |
| Native EMPTY | Пустой текст, whitespace/comments, explicit length; сохранение isc_dsql_error/isc_sqlerr/isc_command_end_err2, отсутствие marker у неполного SQL и `;`; PREPARE_REQUIRE_SEMICOLON и отдельный native isql AUTOTERM — PASS |
| Scalar codecs | Integer limits, BOOLEAN, CHAR с ASCII/кириллицей/пробелами, VARCHAR UTF8/emoji/пустая строка, NULL, exact scaled NUMERIC/DECIMAL, OID/size/typmod — PASS |
| DML и RETURNING | INSERT/UPDATE/DELETE, MERGE, UPDATE OR INSERT, 0/1/many affected records, singleton/cursor RETURNING, исходные tags; trigger effects не увеличивают внешний affected count — PASS |
| DDL и PSQL | CREATE/ALTER/DROP, RECREATE, CREATE OR ALTER, таблицы/индексы/views/domains/sequences/procedures/exceptions, EXECUTE BLOCK с/без результата и EXECUTE PROCEDURE — PASS |
| Native cross-visibility | Committed DML/DDL из PgWire видны отдельному native observer; после обычной ошибки записи откатываются, transaction не остаётся — PASS |
| Отказ до execution | Unsupported DOUBLE/BLOB/INT128, input parameters и controls, batch refusal; GEN_ID не меняется, поэтому rollback не скрывает исполнения — PASS |
| Ошибки и recovery | UNIQUE 23505, CHECK 23514, общий NOT NULL/domain 23000, parser 42601, продолжение того же соединения; division error после DataRow без CommandComplete и с rollback — PASS |
| Реальная ошибка commit | ON TRANSACTION COMMIT trigger вызывает exception после выдачи DataRow: FATAL XX000 → EOF, без CommandComplete/ReadyForQuery; native observer подтверждает rollback, новое PgWire соединение успешно — PASS |
| EMPTY по wire | EmptyQueryResponse/Ready I и следующий SELECT; незавершённый SQL и вложенный block comment получают parser error — PASS |
| Настоящие драйверы | C PQexec и psycopg2: DDL/DML/RETURNING, преобразование новых типов, tags, ошибки/recovery, EMPTY в C — PASS |
| Локальное исполнение | Syscall trace всех server threads от startup до shutdown: 0 backend connect attempts; 49 OS/NSS Unix calls по двум прежним явно разрешённым путям — PASS |

Обе server phases завершились exit 0 без forced kill; shutdown около 0.164 секунды. Логи metadata/C/Python/native AUTOTERM и trace находятся рядом с summary. Ранний `p4-simple-01` остановился на неверном ожидании native CALL: без output targets он имеет result NONE. В тестах также исправлены native syntax `CREATE OR ALTER SEQUENCE ... START WITH` и ожидание внешнего status prefix isc_dsql_error. SQL debug attempts обнаружили только неверное положительное ожидание вложенного block comment: текущий Firebird parser его не поддерживает. После исправлений отдельный debug run прошёл 10 SQL-групп; он не заменяет приведённый полный `simple-02` PASS. Production binaries для этих повторов не менялись.

### Lifecycle, startup и native regression

```sh
python3 -B pg_wire_protocol/tools/pgwire_lifecycle_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p4-lifecycle-02
python3 -B pg_wire_protocol/tools/pgwire_startup_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p4-startup-01
python3 -B pg_wire_protocol/tools/native_baseline.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p4-native-01
```

PASS: **7 lifecycle groups**, **52 startup checks**, **9 native checks**. Lifecycle runner повторно использует прежние P3 fixtures/methods: 0/1/257 rows, права второго пользователя, implicit commit/rollback, реальные streaming disconnect и active-query shutdown с rollback, cleanup attachments, missing module/occupied port при работающем native. Исторический полный `pgwire_query_smoke.py` содержит SELECT-only отказы P3; его запуск не выдаётся за регрессию текущего P4. Эти устаревшие отказы заменены успешными P4 cases в новом Simple Query harness.

Все server phases завершились exit 0 без forced kill, shutdown 0.114–0.164 секунды. Startup trace: 0 backend connects, 51 учтённый OS/NSS Unix call. Python syntax, локальные Markdown links и `git diff --check` прошли. MANUAL_TESTING обновлён двусторонним PgWire/native CRUD сценарием и примерами новых типов.

P4 принят для описанного профиля; **следующий этап P5 — explicit transactions I/T/E и session controls**. Сейчас каждый Query автоматически commit/rollback, а явные SET TRANSACTION/COMMIT/ROLLBACK/savepoints получают 0A000. BEGIN и `$n` остаются ограничениями grammar. Это ещё не M1: P5/P6 не завершены. Extended Query, расширенные codecs, TLS, Windows/другие server modes и full CMake build не проверены. Ошибки close/free не инъецировались, отдельное истечение engine statement timeout не измерялось. FK SQLSTATE проверен unit status vectors, отдельный TCP FK fixture не запускался. IOTimeout не является hard deadline для prepare/auth/cleanup/commit. Для рабочего экземпляра пользователя изменения вступят в силу после его пересборки и перезапуска.

## Рефакторинг P4: классификация внутри PgWire — Linux/Super PASS

12 октября 2026 года, HEAD до изменений `26d4cc3704`. Удалены дополнительные origin/command/result/DDL info items, поля и методы DSQL/AST, изменения grammar/lexer и `isc_dsql_empty_statement`. Pascal constants перегенерированы штатным CLOOP. Удалены `StatementOriginTest.cpp`, его build targets и зависимость Python harness от этого executable. Проверка ниже завершилась exit 0: затронутые файлы движка, public constants и их документация совпадают с состоянием до этих дополнений.

```sh
git diff --exit-code d072686153 -- src/dsql \
  src/include/firebird/impl/inf_pub.h src/include/firebird/impl/msg/jrd.h \
  src/include/gen/Firebird.pas doc/README.isc_info_xxx
```

Plugin-only `SqlInfo` определяет command tag, способ подсчёта строк и поддержанный профиль по ограниченному начальному префиксу. Полный неизменённый SQL всегда проходит штатный prepare первым. Выбор cursor/singleton execution использует существующие `getFlags()` и output metadata; affected records берутся из существующего API. EMPTY получается только при реальной синтаксической ошибке prepare и полностью tokenless тексте. Остальные ошибки движка сохраняют приоритет перед capability checks.

### Сборка и unit tests

```sh
cd /tmp/firebird-pgwire-build
./config.status --file=gen/Makefile:builds/posix/Makefile.in \
  --file=gen/make.shared.variables:builds/posix/make.shared.variables
make -C gen TARGET=Debug updateCloopInterfaces > p4-lex-cloop.log 2>&1
make -j2 > p4-lex-build.log 2>&1
make -C gen -j2 TARGET=Debug pgwire_tests > p4-lex-tests-final-build.log 2>&1
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_protocol_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_error_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_result_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_sql_test
```

PASS: полная POSIX Debug сборка и все четыре executable. Новый SqlInfo test — **284 проверки**: comments/whitespace, quotes, границы keywords/identifiers, CTE и скобочный SELECT, DML/DDL modifiers, PACKAGE BODY против package с именем BODY, EMPTY и malformed input, отказ controls/unknown wrappers. Отдельная сборка этого чистого C++ теста с `-Wall -Wextra -Werror` также прошла. Первоначальная test link rule ошибочно включала общие LINK_LIBS с decFloat без его search path; ненужная для этого теста зависимость удалена, финальный build прошёл. Production binaries после успешной полной сборки не менялись.

### Повторная сетевая приёмка

```sh
cd /home/aleksey/dev/my_firebird/firebird
python3 -B pg_wire_protocol/tools/pgwire_simple_query_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p4-lex-simple-02 \
  --libpq-include-dir /tmp/firebird-pgwire-p3-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5 \
  --driver-python /tmp/firebird-pgwire-p3-deps/venv/bin/python
python3 -B pg_wire_protocol/tools/pgwire_lifecycle_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p4-lex-lifecycle-01
python3 -B pg_wire_protocol/tools/pgwire_startup_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p4-lex-startup-01
python3 -B pg_wire_protocol/tools/native_baseline.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p4-lex-native-01
```

PASS: **12 Simple Query groups**, **7 lifecycle groups**, **52 startup checks**, **9 native checks**. Для каждого запуска сохранён `summary.json` и sanitized logs в соответствующем work directory. Настоящие C headers/runtime — libpq 14.24; psycopg2 2.9.11 использует libpq 17.6 и explicit autocommit. Все запуски выполнялись на отдельных runtime/security/application databases/locks и свободных TCP ports в `/tmp`, с разрешённым TCP/strace вне sandbox.

| Проверка | Результат |
|---|---|
| SELECT, CRUD, MERGE/upsert, RETURNING, DDL, PSQL и scalar codecs | PASS: прежние значения, tags, row counts, native cross-visibility и implicit commit/rollback сохранены |
| Ограниченная классификация | PASS: mixed case/comments, keywords в quoted names/literals, CTE, скобочный SELECT, GTT/index modifiers; PACKAGE с именем BODY и настоящий PACKAGE BODY различаются |
| Приоритет native prepare | PASS: malformed controls/batches дают 42601, семантически неверный USING сохраняет 42000; SQL не делится и не исполняется частично |
| Отказ до execution | PASS: valid unknown autonomous USING и session controls дают 0A000; sequence guard остаётся 0, ALTER SESSION RESET не сбрасывает USER_SESSION context |
| EMPTY и native parser | PASS: whitespace/comments дают EmptyQueryResponse, `;`, vertical tab, незакрытые quotes/comments и неполный SQL дают 42601; native isql AUTOTERM continuation сохранён |
| Commit/fetch failures, disconnect, shutdown | PASS: настоящий rollback, отсутствие ложного CommandComplete, cleanup transactions/attachments; real commit failure закрывает session через FATAL |
| Драйверы и native protocol | PASS: C PQexec и psycopg2, concurrent native observer, native protocol с отключёнными wire plugins |
| Локальное исполнение | PASS: оба syscall trace содержат 0 backend connect attempts; Simple Query — 49, startup — 51 OS/NSS Unix calls по ранее разрешённым двум путям |

Все server phases завершились exit 0 без forced kill; shutdown в Simple Query/lifecycle/startup — около 0.114–0.164 секунды. Python AST, локальные Markdown links и `git diff --check` прошли.

Первый `simple-01` остановился на неверной fixture `USING DO INSERT ...`: native USING требует parameters, local declarations или autonomous clause и правильно выдал 42000 при prepare. Положительный unsupported case заменён на `USING IN AUTONOMOUS TRANSACTION DO INSERT ...`, исходный оставлен отрицательной проверкой native semantic error. Отдельный SQL-only debug подтвердил исправление; приведённый полный `simple-02` повторил все группы и trace. Production-код для исправления fixture не менялся.

Текущая приёмка относится к Linux/Super loopback и Simple Query. Следующий этап — P5: explicit transactions/session controls; явные COMMIT/ROLLBACK пока получают 0A000, а implicit commit/rollback работают. Extended Query, TLS, Windows/другие server modes и полная CMake сборка не проверены. Close/free fault injection и отдельное истечение engine statement timeout не выполнялись. Рабочий сервер пользователя и его базы не изменялись; для использования текущих исходников в нём нужна пересборка и перезапуск.


## P5: explicit transactions и session controls

12 октября 2026 года, HEAD до изменений `3657d6a5c1`. Production-код изменён только в `src/plugins/wire/pg`; diff по `src/dsql`, `src/jrd`, `src/include` пустой. Сборка и все runtime checks используют отдельные `/tmp` runtime/database/security/locks. Рабочий сервер и employee не менялись.

```sh
cd /tmp/firebird-pgwire-build
make -j2 > p5-build.log 2>&1
make -j2 > p5-final-build.log 2>&1
make -C gen -j2 TARGET=Debug pgwire_tests > p5-tests-build.log 2>&1
```

Оба полных build passes и сборка tests завершились exit 0. Четыре executables `gen/Debug/firebird/tests/pgwire_{protocol,error,result,sql}_test`, запущенные с `LD_LIBRARY_PATH=/tmp/firebird-pgwire-build/gen/Debug/firebird/lib`, прошли; SQL classifier — 487 assertions. Проверяются native control prefixes, quoted identifiers/comments, отсутствие замены SQL, failed-prepare whitelist и приоритет syntax/privilege/cancel/fatal diagnostics над 25P02.

Runtime запускается из корня workspace. Для повторения нужны **новые** work directories:

```sh
python3 -B pg_wire_protocol/tools/pgwire_transaction_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p5-transactions-03 \
  --libpq-include-dir /tmp/firebird-pgwire-p3-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5

python3 -B pg_wire_protocol/tools/pgwire_simple_query_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p5-simple-01 \
  --libpq-include-dir /tmp/firebird-pgwire-p3-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5 \
  --driver-python /tmp/firebird-pgwire-p3-deps/venv/bin/python

python3 -B pg_wire_protocol/tools/pgwire_lifecycle_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p5-lifecycle-01
python3 -B pg_wire_protocol/tools/pgwire_startup_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p5-startup-01
python3 -B pg_wire_protocol/tools/native_baseline.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p5-native-01
```

P5 checks наблюдают фактические wire I/T/E и `PQtransactionStatus`, сравнивают видимость с независимым native TCP attachment и считают application transactions через MON$TRANSACTIONS после каждого запроса. В I их 0, в T/E — 1; обычный Query сохраняет transaction ID, RESET в T меняет underlying ID. Поддержаны full controls в I, native RETAIN, повторный start, commit failed block как rollback, recovery через savepoints с сохранением ранних изменений, EMPTY в I/T/E, READ ONLY/SNAPSHOT, READ COMMITTED visibility, NO AUTO UNDO и disconnect rollback. Failed prepare проверяет missing relation, missing column, syntax и permission отдельно; sequence guard доказывает отсутствие execution в E.

Session checks проверяют реальные ROLE, TIME ZONE, USER_SESSION context, timeouts, SEARCH_PATH и BIND. Длинный native procedure SELECT после `SET STATEMENT TIMEOUT 100 MILLISECOND` прерывается с 57014 как в implicit, так и explicit transaction, откатывает побочные эффекты и сохраняет правильное I/E. Statement timeout выбирается как минимум IOTimeout и ненулевого native attachment timeout; раньше request timeout перекрывал attachment setting.

Реальные database triggers вызывают ошибки explicit COMMIT и ALTER SESSION RESET. Клиент получает FATAL без CommandComplete/ReadyForQuery, затем EOF; native observer подтверждает rollback, следующий login работает. Fault triggers активны только на время этой группы. P4 regression отдельно повторяет реальную ошибку implicit commit. Synthetic failures cursor close/statement free не инъецировались.

### Найденный native AUTO COMMIT defect

Первый запуск `/tmp/firebird-pgwire-p5-transactions-01/summary.json` прошёл первые восемь групп, затем native isql оборвал Debug server с SIGABRT до соответствующего PgWire AUTO COMMIT test. Assertion: `m_transaction->tra_save_point->getNumber() == m_number`, `src/jrd/Savepoint.cpp:833`.

Независимое воспроизведение в `/tmp/firebird-native-autocommit-xolgpdwd` использует только embedded isql, `Providers=Engine14`, отдельную БД, без сервера и PgWire. Явный `SET AUTODDL OFF` исключает автокоммит DDL клиентом:

```sql
SET AUTODDL OFF;
CREATE TABLE T (ID INTEGER);
COMMIT;
SET TRANSACTION READ WRITE READ COMMITTED READ CONSISTENCY AUTO COMMIT;
INSERT INTO T VALUES (1);
```

INSERT вызывает тот же SIGABRT. Backtrace (`gdb_autoddl_off.log`): `JStatement::execute` → `DsqlDmlRequest::execute` → `executeReceiveWithRestarts` → `AutoSavePoint::release`. По исходникам, wrapper read-consistency restart держит AutoSavePoint, тогда как AUTO COMMIT внутри execution завершает retaining и меняет savepoint. Падение воспроизводится без triggers; изначальная гипотеза о commit-trigger interaction не подтвердилась.

Native `SET TRANSACTION ... AUTO COMMIT; ROLLBACK` без DML завершился успешно. `SNAPSHOT AUTO COMMIT` с INSERT, затем ROLLBACK также завершился успешно, после чего COUNT(*)=1 подтверждает сохранённый auto-commit effect. Поэтому итоговый P5 fixture проверяет AUTO COMMIT в SNAPSHOT; сочетание с READ COMMITTED READ CONSISTENCY остаётся известным ограничением текущего native Debug baseline. В плагин не добавлены разбор этих опций, их подмена или обход assertion; engine не менялся. Поведение Release-сборки не проверялось.

Промежуточный `/tmp/firebird-pgwire-p5-transactions-02/summary.json` прошёл 13 групп с явным пропуском AUTO COMMIT DML; это не итоговая проверка AUTO COMMIT. Окончательный результат полного fixture приведён ниже.


### Итог P5

| Проверка | Результат / свидетельство |
|---|---|
| Полная Debug сборка и 4 unit executables | PASS; `p5-final-build.log`, `p5-tests-build.log`; 487 SqlInfo assertions |
| P5 native transactions/session controls | PASS, 14 групп, без пропусков; `/tmp/firebird-pgwire-p5-transactions-03/summary.json` |
| Simple Query regression | PASS, 12 групп; `/tmp/firebird-pgwire-p5-simple-01/summary.json` |
| Lifecycle regression | PASS, 7 групп; `/tmp/firebird-pgwire-p5-lifecycle-01/summary.json` |
| Startup/authentication regression | PASS, 52 checks; `/tmp/firebird-pgwire-p5-startup-01/summary.json` |
| Native baseline | PASS, 9 checks; `/tmp/firebird-pgwire-p5-native-01/summary.json` |

P5 C libpq использует headers/runtime 14.24, реально наблюдает IDLE/INTRANS/INERROR и savepoint recovery. Simple Query regression дополнительно проверяет psycopg2 2.9.11 с libpq 17.6 в autocommit mode. Финальный AUTO COMMIT case — native/PgWire SNAPSHOT с проверкой MON$AUTO_COMMIT, MON$ISOLATION_MODE, durable visibility и невозможности отменить предыдущий commit поздним rollback.

Отдельный P5 trace phase (startup/authentication/контрольный SELECT/shutdown) показал 0 backend connect attempts; 49 Unix OS/NSS attempts учтены отдельно. Это не трасса каждого transactional statement. Simple Query и startup trace также показали 0 backend connects. В итоговых runtime groups все servers завершились exit 0 без forced kill; SIGTERM shutdown занял 0.114–0.166 s.

Область результата — P5 на Linux/Super, IPv4 loopback, native compatible SQL. READ COMMITTED READ CONSISTENCY AUTO COMMIT остаётся известным native Debug defect. Остальные transaction options не обещают семантику PostgreSQL. Полная M1 приёмка P6, SuperClassic/Windows/TLS, Extended Query, PostgreSQL BEGIN/$n, close/free failure injection и отдельные live effects DECFLOAT/OPTIMIZE/TRUSTED ROLE не объявляются выполненными. Native timers ограничивают DML execution/fetch, без hard deadline для prepare, DDL или transaction/session controls. Планы и ручной двухклиентский сценарий обновлены.

## Удаление дополнительных wire-таймаутов — 13 октября 2026

База правки — `5f873c9eba`. Удалены `IOTimeout`, сетевые/frame/startup/auth/output deadlines, истечение очереди по времени и задаваемые плагином statement/lock timeout. Сохраняются штатные настройки Firebird. `IWireConnection::read/write` больше не принимают timeout; C++/Pascal bindings пересозданы. Сервер и protocol plugins необходимо пересобрать вместе и перезапустить.

| Проверка | Результат / свидетельство |
|---|---|
| Полная Debug сборка, `make -j2` | PASS; `/tmp/firebird-pgwire-build/remove-wire-timeouts-build.log` |
| `make -C gen -j2 TARGET=Debug wire_tests pgwire_tests` | PASS; `/tmp/firebird-pgwire-build/remove-wire-timeouts-tests-build.log` |
| Четыре PgWire unit executables: protocol, error, result, sql | PASS; включая 487 SqlInfo checks |
| `wire_socket_test`: partial I/O, EOF, ownership, shutdown, backpressure | PASS; запуск с разрешёнными локальными sockets |
| Существующий wire host smoke | PASS, 9 checks; `/tmp/firebird-wire-no-timeouts-host-01/summary.json` |
| Существующий Simple Query smoke | PASS, 12 групп; `/tmp/firebird-pgwire-no-timeouts-simple-01/summary.json` |
| Python syntax, Markdown links, `git diff --check` | PASS |

Сборка и тестовые runtime находятся в `/tmp`; запущенный сервер и базы пользователя не изменялись. Wire host проверяет совместную работу native listener и двух plugin endpoints, ограничение размера очереди и остановку blocked I/O. Simple Query проверяет DML/DDL/RETURNING и видимость через native connection с C libpq 14.24 и psycopg2 2.9.11/libpq 17.6. Все четыре тестовых server phases завершились с exit 0 без forced kill; trace показал 0 backend network connects.

Новых тестов с паузами или срабатываниями таймаутов не добавлено; прежние проверки удалённых таймаутов убраны. Полный P5 transaction/session, startup и native baseline suites в этой правке повторно не запускались; их результаты выше относятся к предыдущей ревизии.

## P6: серверная приёмка — 13 октября 2026

База — `228966d5c6`, Linux x86_64, Debug, Engine14, Super, IPv4 loopback. Изменены только wire shutdown и проверочные материалы: terminal stop использует `fb_cancel_abort`, потому что однократный `fb_cancel_raise` может попасть между engine calls; PgWire повторно проверяет остановку после чтения сообщения. Parser, DSQL, native transaction semantics и ABI в этой правке не менялись. Все runtime и БД изолированы в `/tmp`; рабочий сервер пользователя не изменялся.

**P6 завершён; целевой M1 для Linux/Super принят с известными native ограничениями.** По согласованному объёму исправление падения при shutdown во время ON CONNECT исключено из этого этапа. Сценарий остаётся FAIL и воспроизводится также при полностью отключённом PgWire; его результат не входит в PASS. Ранее выявленный READ CONSISTENCY + AUTO COMMIT также остаётся вне подтверждённой совместимости. Следующий этап — P7, Extended Query.

| Проверка | Результат / свидетельство |
|---|---|
| Полная сборка `make -j2` и `make -C gen -j2 TARGET=Debug wire_tests pgwire_tests` | PASS; `/tmp/firebird-pgwire-build/p6-build.log`, `p6-tests-build.log` |
| PgWire protocol/error/result/sql unit executables | PASS, включая 487 SqlInfo checks |
| P6 server harness | PASS, 7 групп; `/tmp/firebird-pgwire-p6-server-03/summary.json` |
| Мигрированный psycopg2 pytest | PASS, 58 cases без skips; `/tmp/firebird-pgwire-p6-server-03/pytest.log` |
| P5 transactions/session regression | PASS, 14 групп; `/tmp/firebird-pgwire-p6-transactions-01/summary.json` |
| Simple Query regression | PASS, 12 групп; `/tmp/firebird-pgwire-p6-simple-01/summary.json` |
| Lifecycle regression | PASS, 7 групп; `/tmp/firebird-pgwire-p6-lifecycle-01/summary.json` |
| Startup/authentication regression | PASS, 49 checks; `/tmp/firebird-pgwire-p6-startup-01/summary.json` |
| Native baseline | PASS, 9 checks; `/tmp/firebird-pgwire-p6-native-01/summary.json` |
| Wire host и plugin lifecycle | PASS, 9 host checks; `/tmp/firebird-wire-p6-host-01/summary.json`; `wire_plugin_smoke.py` подтвердил aliases/stop/cleanup/unload через реальный PluginManager |
| Shutdown во время ON CONNECT: PgWire | FAIL, SIGABRT; `/tmp/firebird-pgwire-p6-attach-shutdown-01/summary.json` и `backtrace.log` |
| Тот же ON CONNECT: только native TCP, wire endpoints выключены | FAIL, тот же SIGABRT; `/tmp/firebird-pgwire-p6-native-attach-shutdown-01/summary.json` и `backtrace.log` |

Новый server harness выполняет 58 driver cases, затем 16 warmup и 24 измеряемых connect/query/error/explicit transaction/disconnect цикла. Attachments/transactions/statements освобождаются; FD baseline — 15, все три измеренных batch — 15. Потоков было 11 во всех финальных snapshots, но это диагностическое число: native server сохраняет workers в пуле, поэтому его точное совпадение не является gate. Возврат RSS к исходному значению не проверяется.

DELETE MON$STATEMENTS действительно отменяет работу с 57014 и правильным I/E/recovery. DELETE MON$ATTACHMENTS и `gfix -shut full -force 0` дают FATAL 57P01 на следующем Query, с rollback и последующим успешным подключением после online. Для gfix используется случайный пароль SYSDBA только клонированной security DB. Остановка при record lock wait синхронизируется с MON$RECORD_WAITS > 0; одновременно существуют idle transaction, password wait и неполный Startup. Оба server phases завершаются exit 0 без SIGKILL. SuperClassic проверен только на явный отказ дополнительного endpoint при работоспособном native listener.

Реальные драйверы: C libpq headers/runtime 14.24 и psycopg2 2.9.11/libpq 17.6, pytest 8.4.2. Traced startup/Simple Query/transactions показали 0 backend network connects. Новых проверок пауз или срабатывания timeout нет; существующие P5 session tests повторены как regression.

Основная команда из корня workspace (нужен новый work directory):

```sh
python3 -B pg_wire_protocol/tools/pgwire_server_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p6-server-03 \
  --driver-python /tmp/firebird-pgwire-p3-deps/venv/bin/python
```

Промежуточные `server-01` и `server-02` не входят в PASS: первый выявил некорректное требование точного native thread count, второй — отсутствовавшее право DELETE на MON$STATEMENTS у тестового observer. В debug fixture также заменён role-based gfix на SYSDBA клонированной security DB, чтобы вернуть полностью остановленную БД online. Эти исправления относятся к harness, production binaries не менялись после `p6-build.log`.

### Известное исключение приёмки: ON CONNECT и shutdown

Trigger для WIRE_PG_OTHER вставляет marker, увеличивает sequence и выполняет длительный COUNT. Native observer подтверждает вход по sequence, после чего harness посылает SIGTERM. Оба варианта падают на `Assertion (!child)`, `src/common/classes/alloc.cpp:2075`, server exit -6 без принудительного kill. Проверки EOF/rollback после штатной остановки не достигнуты.

Стек: `JProvider::internalAttach` → `unwindAttach` → `JRD_shutdown_database` → `Database::~Database` → `MemPool::~MemPool`. В native-only варианте нижняя ветка — `DatabaseAuth::accept`, список wire endpoints пуст. Это отделяет дефект native attach cleanup от lifecycle PgWire/plugin unload. Полные stacks, fixture SQL и логи сохранены в двух каталогах, указанных в таблице. Ошибка не маскируется успешным skip/xfail: скрипт возвращает ненулевой код, а падение явно исключено из объёма текущей приёмки M1.

В PgWire core оставшийся child pool принадлежит Request для TRIGGER_CONNECT, созданному в Statement::getRequest. Request уже не используется и отвязан от attachment/transaction/caller; statement и его request generation остаются живы. Причина пропуска освобождения ещё не установлена. Принудительное удаление child pools или обход в плагине не добавлены; исправление native cleanup остаётся отдельной задачей.

Сохранённый в репозитории reproducer повторил оба FAIL с `trigger_entered=1`, server exit -6 и тем же assertion. Его summary находятся в `/tmp/firebird-pgwire-p6-attach-repro-02/` и `/tmp/firebird-native-p6-attach-repro-02/`. Команды (work directories должны быть новыми):

```sh
python3 -B pg_wire_protocol/tools/pgwire_attach_shutdown_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p6-attach-repro-02
python3 -B pg_wire_protocol/tools/pgwire_attach_shutdown_smoke.py --native-only \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-native-p6-attach-repro-02
```

## P7: Extended Query — 13 октября 2026

База — `c94f8e84ff`, Linux x86_64, Debug, Engine14, Super, IPv4 loopback. Реализованы Parse/Bind/Describe/Execute/Close/Flush/Sync, prepared statements/portals, native input metadata и text/binary basic codecs. Все SQL bytes передаются движку без изменения. Parser, публичные statement metadata и CLOOP не расширялись. Отдельно исправлено освобождение нескольких native requests общего Statement при shutdown attachment; причина и native regression описаны ниже. Рабочий сервер пользователя и его базы не изменялись.

**P7 прошёл целевую приёмку.** Следующий этап — P8, расширенные codecs; P9 должен завершить M2. Параметры проверены через native `?`, PostgreSQL `$1` и BEGIN по-прежнему дают ошибку парсера. Фактический Psycopg 3 profile в P7 не проверен. Binary NUMERIC, floating point/temporal/INT128 codecs, TLS и CancelRequest остаются последующими этапами.

| Проверка | Результат / свидетельство |
|---|---|
| Полная сборка `make -j2` | PASS; `/tmp/firebird-pgwire-build/p7-build.log` |
| Сборка opt-in `wire_tests pgwire_tests`, пять PgWire unit executables | PASS; `/tmp/firebird-pgwire-build/p7-tests-build.log`; InputCodec 149 checks, SqlInfo 487 checks; Protocol/ErrorStatus/ResultCodec PASS |
| Extended Query | PASS, 16 групп; `/tmp/firebird-pgwire-p7-extended-04/summary.json` |
| Simple Query regression | PASS, 12 групп; `/tmp/firebird-pgwire-p7-simple-01/summary.json` |
| Transaction/session regression | PASS, 14 групп; `/tmp/firebird-pgwire-p7-transactions-01/summary.json` |
| Startup/authentication regression | PASS, 49 checks; `/tmp/firebird-pgwire-p7-startup-01/summary.json` |
| Server regression после engine cleanup fix | PASS, 7 групп и 58 pytest cases (23.85 s); `/tmp/firebird-pgwire-p7-server-02/summary.json` |
| Native TCP baseline после fix | PASS, 9 checks; `/tmp/firebird-pgwire-p7-native-01/summary.json` |
| Native two-prepared-handle shutdown | PASS, 3 checks, обе остановки exit 0; `/tmp/firebird-pgwire-p7-native-prepared-shutdown-02/summary.json` |

Прямой C libpq 14.24 (headers/runtime) проверил PQexecParams, PQprepare/PQexecPrepared, PQsendQueryParams/PQsendQueryPrepared/PQgetResult в nonblocking режиме, ноль параметров, native `?`, NULL, text/binary и настоящий parser failure `$1`. Raw TCP проверил порядок сообщений, Describe/OIDs/formats, named/unnamed replacement/Close и сохранение bound portals после закрытия source statement. Несколько portals одного SQL имеют независимые cursors и значения; maxRows/Suspended/resume сохраняют строки и итоговый command count.

Native observer подтвердил отсутствие commit до Sync, rollback всего implicit cycle при ошибке, сохранение explicit T/E через Sync, восстановление savepoints и prepared reuse после commit. COMMIT/ROLLBACK/RETAIN и переход COMMIT из E в ROLLBACK проверены непосредственно через Extended Execute. Ошибки Bind отклоняют неподдержанные значения/форматы до необратимого GEN_ID effect. Recovery игнорирует тела полных командных frames до Sync; повреждённая внешняя framing остаётся fatal. RESET из I/T очищает objects без дополнительных prepare-transactions, даже при замене underlying transaction за тем же интерфейсом.

Проверены 128 statements/128 portals и суммарный byte limit: 51 SQL по 163889 bytes помещается, следующий получает 54000 до достижения count limit. Это лимит учитываемых SQL/metadata/buffers, а не измеренный RSS cap. EOF/Terminate и shutdown освобождают suspended cursors и откатывают незавершённые изменения. Traced startup/Simple Query повторно показал 0 backend network connect attempts; scope trace указан в summary, он не выдаётся за отдельную трассу всех Extended сообщений.

Команда основной приёмки (work directory должен быть новым):

```sh
python3 -B pg_wire_protocol/tools/pgwire_extended_query_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p7-extended-04 \
  --libpq-include-dir /tmp/firebird-pgwire-p3-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5
```

### Native cleanup нескольких prepared requests

P7 выявил assertion `Statement.cpp:781`, `!((*instance)->isUsed())`, при остановке с двумя native prepares одного SQL. Независимый native TCP запуск с пустым списком wire endpoints воспроизвёл SIGABRT: `/tmp/firebird-pgwire-p7-native-cursor-01/summary.json`. Это отдельный дефект; ранее отложенный ON CONNECT assertion `alloc.cpp:2075 (!child)` не исправлялся и не проверялся повторно в P7.

Стек нового падения: `Statement::release → CMP_release → release_attachment → purge_attachment → shutdownAttachments`. `release_attachment` снимал used только с очередного request, но CMP_release освобождал весь Statement вместе с другими занятыми clones. Исправление в `src/jrd/jrd.cpp` сначала снимает used у всех requests уничтожаемого attachment, затем освобождает statements прежним циклом. Коллекция меняется только во втором проходе; parser, execution semantics и новые metadata/API этим не затрагиваются.

Сохранён воспроизводимый regression с родным OO API и TCP, отключённым PgWire, двумя prepared handles и отдельным сценарием cursor/rollback:

```sh
python3 -B pg_wire_protocol/tools/pgwire_native_prepared_shutdown_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p7-native-prepared-shutdown-02
```

Промежуточные Extended `-01/-02/-03` не входят в PASS. Они выявили требование native inference (`ID + CAST(? AS INTEGER)`; исходный `CAST(ID + ? AS INTEGER)` даёт HY004 также в native isql SET PLANONLY), неверную fatal обработку отрицательного Execute maxRows и описанный cleanup assertion. Эти причины исправлены и покрыты финальным `-04`. Native inference evidence сохранён в `extended-01/native-parameter-inference.json`; SQL rewriting ради прохождения fixture не добавлялся. Первый запуск native regression остановился до старта сервера из-за отсутствовавшего linker search path для libtommath; harness теперь задаёт runtime library path через `-rpath-link`.


## P8: расширенные codecs — 14 октября 2026

База — `598d487095`, Linux x86_64, Debug, Engine14, Super, IPv4 loopback. Production changes находятся в PgWire: общие FloatCodec/NumericCodec/TemporalCodec подключены к InputCodec и ResultCodec; startup добавляет `integer_datetimes=on`. Ядро, parser, public statement metadata, host ABI и таймауты не менялись. Рабочий сервер пользователя и его базы не изменялись.

**P8 прошёл целевую приёмку.** Добавлены text/binary FLOAT/DOUBLE, конечные DATE/TIME/TIMESTAMP без TZ, INT128 и точные NUMERIC/DECIMAL до precision 38, включая binary NUMERIC. Pure INT128 сохраняет полный signed диапазон. Temporal input должен точно представляться native 100 µs ticks, поддерживаются годы 0001..9999; меньшая точность не округляется. Профиль и SQLSTATE описаны в COMPATIBILITY. Следующий этап — P9/M2; `$n`, PostgreSQL BEGIN и `::date` по-прежнему не входят в grammar.

| Проверка | Результат / свидетельство |
|---|---|
| Полная сборка `make -j2` | PASS; `/tmp/firebird-pgwire-build/p8-build.log` |
| Сборка `wire_tests pgwire_tests` | PASS; `/tmp/firebird-pgwire-build/p8-tests-build.log` |
| Восемь PgWire unit executables | PASS: Float 112, Numeric 502, Temporal 313, Input 154, SqlInfo 487 checks; Result/Protocol/ErrorStatus PASS |
| Numeric standalone UBSan | PASS, 502 checks, без diagnostics |
| Новые codecs через TCP/libpq | PASS, 8 групп; `/tmp/firebird-pgwire-p8-codecs-02/summary.json` |
| Extended Query regression | PASS, 16 групп; `/tmp/firebird-pgwire-p8-extended-01/summary.json` |
| Simple Query regression | PASS, 12 групп; `/tmp/firebird-pgwire-p8-simple-01/summary.json` |
| Transaction/session regression | PASS, 14 групп; `/tmp/firebird-pgwire-p8-transactions-01/summary.json` |
| Startup/authentication regression | PASS, 49 checks; `/tmp/firebird-pgwire-p8-startup-01/summary.json` |
| Server regression и live psycopg2 | PASS, 7 групп и 58 pytest cases (23.98 s); `/tmp/firebird-pgwire-p8-server-01/summary.json` |
| Python AST и `git diff --check` | PASS |

Реальные клиенты: C libpq headers/runtime 14.24 и psycopg2 2.9.11/libpq 17.6. Новый C client использует PQexecParams, PQprepare, PQdescribePrepared и PQexecPrepared с native `?`, восемью раздельными параметрами, text/binary input/output и NULL. Эталонные wire bytes заданы независимо от серверных codecs. Проверены числовые OID и ParameterDescription, typmod=-1, повторное исполнение после ошибки.

Новый raw-wire harness проверяет IEEE signed zero, subnormal/min/max, NaN/±Infinity; full INT128 min/max, 38 fractional digits, exact exponent и base-10000 numeric; epochs 1858/2000, отрицательный timestamp, даты 0001/9999, temporal100 µs. Unit fixtures дополнительно проверяют leap years, unaligned native buffers и отсутствие частичной записи при ошибке. Simple SELECT и Extended INSERT/Simple UPDATE RETURNING сохраняют значения; отдельный native TCP observer подтверждает обе стороны cross-visibility. В 26 negative Bind cases ошибки формата/диапазона/точности возникают до необратимого GEN_ID effect.

Старые negative FLOAT/DOUBLE/DATE/TIME/TIMESTAMP/INT128 tests заменены положительными; guards неподдержанного output используют DECFLOAT/BLOB. SET BIND regression теперь проверяет исходный DOUBLE, coercion к VARCHAR и восстановление DOUBLE после RESET. Existing server resource checks: после 16 warmup и 24 измеряемых sessions FD baseline 15, все три batch 15; native monitoring не оставляет attachments/transactions/statements. Существующие P5 timeout cases повторены без добавления новых pause/timeout scenarios.

Trace harness зафиксировал 0 backend network connects в своём startup/Simple Query scope; полный Extended codec обмен этим syscall trace не заявляется покрытым. Неисправленный native ON CONNECT shutdown defect и READ CONSISTENCY + AUTO COMMIT остаются ранее записанными исключениями и в P8 не исправлялись. Windows, другие server modes, TLS, CancelRequest и обычная параметризация Psycopg 3 требуют последующих этапов.

В промежуточном `/tmp/firebird-pgwire-p8-codecs-01/` прямой C libpq и float checks прошли, затем сам Python runner получил ValueError: Python 3.10 `fromisoformat` не принимает четыре fractional digits. В test-only helper добавлено дополнение дроби нулями до шести цифр; production codecs не менялись. Полный повтор `codecs-02` прошёл.

Воспроизводимый запуск (из корня workspace, work directory должен быть новым):

```sh
python3 -B pg_wire_protocol/tools/pgwire_codec_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p8-codecs-new \
  --libpq-include-dir /tmp/firebird-pgwire-p3-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5
```

Unit targets собираются через `make -C gen -j2 TARGET=Debug wire_tests pgwire_tests` в build directory. Выполняемые файлы — `gen/Debug/firebird/tests/pgwire_*_test`, с `LD_LIBRARY_PATH` на `gen/Debug/firebird/lib` этой же сборки. Для обновления make templates при существующем configure: `MAKE_SRC_DIR=builds/posix ./config.status gen/Makefile gen/make.shared.variables`.


## P9: клиентская приёмка M2 — 14 октября 2026

База — `241c23462721316cec071bf9c02a0e517bf568ae`, Linux x86_64, Debug, Engine14, Super, IPv4 loopback. Использована прошедшая P8 сборка; P9 меняет C/Python harnesses и документацию, production sources не меняются. SHA256 проверенного libPgWire.so: `b4a42e244d7f905458055aadff48697f2e857dd6a0a83e03d8bb7b2242f9c2bc`. Рабочий сервер пользователя не перезапускался, тесты создавали собственные runtime/security/databases в /tmp.

**Целевой M2 принят:** Extended wire, supported codecs и совместимый SQL, прямой libpq с native `?` и ограниченный профиль Psycopg 3 без параметров. Это не означает совместимость произвольного PostgreSQL-приложения. Следующий этап — P10, remote TCP/TLS и CancelRequest; M3 остаётся отдельной прикладной приёмкой.

| Проверка | Результат / свидетельство |
|---|---|
| C libpq fixture build, `-std=c11 -Wall -Wextra -Werror -O2` | PASS; `clients-02/libpq-compile.log` |
| P9 C libpq/raw/Psycopg 3 | PASS, 17 групп; `/tmp/firebird-pgwire-p9-clients-02/summary.json` |
| Реальный Psycopg 3 child | PASS, 5 checks, 4 ожидаемых limits, 7 post-auth traces; `clients-02/psycopg3.log`, `clients-02/psycopg3-trace/` |
| P8 codecs regression | PASS, 8 групп; `/tmp/firebird-pgwire-p9-codecs-01/summary.json` |
| Simple Query/M1 regression | PASS, 12 групп; `/tmp/firebird-pgwire-p9-simple-01/summary.json` |
| Transaction/session/M1 regression | PASS, 14 групп; `/tmp/firebird-pgwire-p9-transactions-01/summary.json` |
| Startup/authentication regression | PASS, 49 checks; `/tmp/firebird-pgwire-p9-startup-01/summary.json` |
| P6 server/M1 regression и psycopg2 | PASS, 7 групп, 58 pytest cases (24.28 s); `/tmp/firebird-pgwire-p9-server-01/summary.json` |
| Native TCP baseline | PASS, 9 checks; `/tmp/firebird-pgwire-p9-native-01/summary.json` |
| Python AST и `git diff --check` | PASS |

В таблице `clients-02/` означает `/tmp/firebird-pgwire-p9-clients-02/`. Существующие P8 production build/unit результаты сохранены выше; их код и binaries в P9 не изменялись. Полный первый прогон `clients-01` тоже прошёл; финальный `clients-02` дополнительно подтверждает native cross-visibility и сохраняет Python/build-libpq versions.

### C libpq и raw-wire

Headers/runtime libpq 14.24 (`140024`). Sync PQexecParams/PQprepare/PQexecPrepared и Describe работают с нулём параметров и native `?`, named/unnamed replacement/reuse, text/binary и NULL. Новый async path использует poll и PQflush, обслуживает чтение при queued output, вызывает PQconsumeInput/PQisBusy перед каждым PQgetResult и дренирует поток до NULL, в том числе после PGRES_FATAL_ERROR. Зафиксированы 20 async commands, 20 результатов, 40 poll calls; pipeline выключен. Loopback не гарантирует ветку PQflush==1, поэтому специально вызванный backpressure этим результатом не заявляется.

Проверены Parse 42601, Bind 22003 и Execute 22012, последующее успешное reuse; native SET TRANSACTION → T → SQL error/E → 25P02 → ROLLBACK/I и отсутствие записанной строки. Named statement переживает rollback/commit. Исходный P7 check использовал send/get API, но его непосредственный PQgetResult мог блокироваться; P9 закрывает именно это ограничение старого test fixture.

К существующим raw sequences добавлен E06: Execute INSERT+Flush без Sync оставляет одну native transaction и невидимую строку, последующий SET TRANSACTION получает 25001. До Sync уже подтверждены rollback, ноль transactions и отсутствие строки, затем Sync/I и успешный SELECT. Полный набор повторяет prepared/portal lifecycle, explicit/implicit cycles, formats, discard/recovery, object/memory limits, EOF/shutdown cleanup. Все тестовые servers завершились exit 0 без forced kill.

### Реальный Psycopg 3

Psycopg `3.3.6`, implementation `binary`, build/runtime libpq `180006` (18.6), Python `3.10.12`. Установлен из PyPI в отдельное `/tmp/firebird-pgwire-p9-deps/venv`; `psycopg2 2.9.11/libpq 17.6` повторно проверялся из прежнего отдельного окружения. Тест не использует подменённый драйвер, adapters или SQL rewriting.

Обычные Cursor/AsyncCursor проходят native SQL без parameters при autocommit=True: Simple Query, forced preparation и text/binary декодирование всех P8 scalar families, NULL и empty result. Prepared reuse после COMMIT работает. При prepared_max=2 stock driver отправил четыре Close messages при вытеснении named statements. В отдельном native-control profile `prepare_threshold=None` подтверждены I/T/E, commit/rollback, savepoint recovery и COMMIT failed block→ROLLBACK. Независимый native TCP observer видит только строки (9301,11) и (9303,33); 9302/9304 отсутствуют. После child нет PgWire attachments/transactions.

Четыре ограничения подтверждены реальным запросом и SQLSTATE 42601:

- Default autocommit=False автоматически отправляет BEGIN.
- Обычные Cursor parameters `%s` превращаются в `$1`.
- `sql.Literal(date(...))` создаёт PostgreSQL `::date`.
- При непустом prepared cache успешный ROLLBACK сопровождается SQL DEALLOCATE ALL от драйвера, который parser отвергает.

В последнем случае trace содержит ROLLBACK → CommandComplete ROLLBACK → ReadyForQuery I, затем DEALLOCATE ALL → ErrorResponse 42601 → I. Fixture подтверждает отсутствие строки и успешный следующий SELECT. Это ошибка обслуживания cache после уже выполненного rollback. SQL по-прежнему поступает штатному parser; дополнительные engine metadata/grammar и фиктивный успех не вводились. DROP/ALTER cache invalidation и native RESET/stale driver cache описаны в COMPATIBILITY по source audit, без заявления отдельного live coverage этих комбинаций.

Trace включается после authentication и не содержит password exchange. Prepared profile: Parse 6, Bind/Describe/Execute по 9, Sync 19, Close 4; async profile: Parse 2, Bind/Describe/Execute по 4, Sync 6. Эти трассы доказывают фактический wire обмен драйвера. Отдельный syscall trace показал 0 backend connects в своём startup/Simple scope; весь P9/Psycopg обмен этим syscall trace не покрыт.

### Воспроизведение

```sh
python3 -B pg_wire_protocol/tools/pgwire_extended_query_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p9-clients-new \
  --libpq-include-dir /tmp/firebird-pgwire-p3-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5 \
  --psycopg3-python /tmp/firebird-pgwire-p9-deps/venv/bin/python
```

Без последнего аргумента запускается прежний C/raw profile; отсутствие Psycopg 3 нельзя выдавать за его PASS. Для установки отдельного окружения и ожидаемых driver limits см. MANUAL_TESTING. Новые timer/pause tests не добавлялись. Известные native ON CONNECT shutdown и READ CONSISTENCY + AUTO COMMIT исключения сохраняются, не исправлялись и специально не перепроверялись. Windows/другие server modes, pipeline, TLS/CancelRequest и полноценный `$n`/BEGIN application profile остаются вне этой приёмки.
