# Записанный профиль 1С: этапы совместимости

13 ноября 2026 года: APP-16 (`78e193be68`) прошёл независимую приёмку. Добавлены проверенные
каталоги открытия базы, transaction advisory locks, serial/native LTT identity,
ANALYZE и fasttruncate для PG LTT. Реальная база перенесена на отдельный стенд;
успешное открытие настоящим тонким клиентом 1С подтверждено. Затем подключён
обычный толстый клиент и начаты проверки самого Гилёва на постоянном стенде.
[Запуск и подключение через GUI](GILEV_GUI.md).

Перенесены и сверены 194 таблицы / 135146 строк, 351 физический индекс
(337 исходных + 14 для правильной NULL-семантики), все 46 primary keys.
Остались 16 expression indexes и 3 циклических sequence defaults; стенд
запускается с явно разрешённым неполным schema profile. Настоящий ibsrv загрузил
libpq 14.2, прошёл Startup и читает перенесённую конфигурацию, включая Config/version,
через настоящий Bind с binary result. Тонкий клиент `1cv8c 8.5.4.1306` открыл
базу и отрисовал начальную страницу «Тест Гилёва : 2.1.1.48».
[Отчёт и доказательства открытия](OPEN_INFOBASE.md).

Текущая цель, **этап 4: успешное открытие базы клиентом 1С**, выполнена. Каталоги,
блокировки/служебные функции и подготовка данных (этапы 1–3) имеют проверенные
ограниченные профили. Этап 5, полный прогон Гилёва, ещё не выполнен; начальная
страница требует толстый клиент для самого теста. Состояние и ограничения — в
[PROGRESS](PROGRESS.md); число необходимых исправлений для нагрузки пока неизвестно.

- [Каталоги и locale](DATABASE_CATALOG.md).
- [Блокировки и statement_timeout](ADVISORY_LOCKS.md).
- [serial и LTT identity](SERIAL_IDENTITY.md).
- [ANALYZE/fasttrun](TEMP_SERVICES.md).
- [NULL-aware equality / fulleq](FULLEQ.md).
- [Прикладные функции datediff2/vassn](APPLICATION_FUNCTIONS.md).
- [format_number](NUMBER_FORMAT.md).
- [Каталог и видимость индексов](INDEX_CATALOG.md).
- [DELETE USING](DELETE_USING.md).
- [Подготовка информационной базы](PREPARE_INFOBASE.md).

APP-15 принят в `7799cd3a54`, NAT-06 — в `516f55bbb5`. APP-16 добавляет bytea,
mchar/mvarchar, BINROWVER/BOOL_OR и regnamespace; требуется ODS 14.1.
[Проверки](../VALIDATION.md) · [текущая готовность](PROGRESS.md) ·
[ограничения](../BACKLOG.md) · [будущая изоляция](../EXTENSION_POINTS.md).

## Общая установка SQL совместимости

[install_compat.py](install_compat.py) устанавливает все восемнадцать текущих SQL-скриптов
в порядке зависимостей на указанную существующую базу. Нужны Python 3 и native
`isql` из этого fork с поддержкой APP-16 / ODS 14.1; запускать владельцем/администратором
без конкурентного DDL. Например, через родной сетевой порт Firebird:

```sh
python3 pg_wire_protocol/1c/install_compat.py \
  --isql gen/Debug/firebird/bin/isql \
  -d localhost/3050:pgwire_test -U sysdba -P masterkey
```

Заменить credentials своими. `-d` принимает native connection string, alias или
путь к `.fdb`; это не PostgreSQL URI и не порт PgWire 15432. При локальном пути
способ attach выбирают штатные providers Firebird. Без `-P` используется
`ISC_PASSWORD`, без `-U` — `ISC_USER` либо SYSDBA. SQL-файлы находятся относительно
самого установщика, поэтому запуск не зависит от текущего каталога.

Порядок: namespace registry → compat.sql → namespace catalog → class catalog →
lowercase PG names → relation visibility → temporary relations → types/expressions → database catalog → advisory locks →
type catalog → temporary-table services → fulleq → application functions → number formatting → tables catalog → attribute catalog → index catalog. Каждый файл выполняется native isql
с BAIL ON, отключённым AUTODDL, UTF8 и client dialect 3. При первой ошибке следующие
файлы не запускаются; сообщение содержит имя неуспешного шага и ненулевой exit code.
**Это первоначальная установка, не повторяемая миграция:** повтор полного набора
получит отказ на существующем PGWIRE_COMPAT. Семантика отдельных SQL сохранена,
включая IF NOT EXISTS и CREATE OR ALTER в compat.sql. Каждый SQL-файл содержит
свой COMMIT, поэтому при поздней ошибке ранее выполненные шаги остаются установленными.
Автоматического пропуска завершённых этапов,
отката всего пакета или исправления частичной установки нет.

Для принятой базы APP-15 используется отдельный путь обновления (owner/admin,
без concurrent DDL):

```sh
gfix -upgrade DATABASE_ALIAS
python3 pg_wire_protocol/1c/install_compat.py --isql PATH_TO_NEW_ISQL \
  --database DATABASE_ALIAS --upgrade-from-app15
```

Credentials передаются через ISC_USER/ISC_PASSWORD. Штатный gfix обновляет ODS
14.0 → 14.1 и intl metadata; это отдельная завершённая native операция. Последующий
восьмой SQL-скрипт атомарно добавляет функции APP-16 и grants, затем установщик
выполняет последующие шаги. Каждый шаг фиксируется отдельно; при ошибке ранее
завершённые шаги и данные сохраняются, ODS обратно не понижается. Повтор/коллизии дают
отказ; старый runtime отвергает ODS 14.1. Новые базы получают новую ODS при CREATE.
[Типы, форматы и ограничения](../COMPATIBILITY.md#app-16-типы-и-выражения-1с).

Для уже установленного APP-16 используется `--upgrade-from-app16`.
Необязательный `--locale ru_RU.UTF-8` добавляет отдельный шаг настройки native ICU
ru_RU перед новыми каталогами. Выполнять его до импорта таблиц; collation старых
столбцов не изменяется. ICU и PostgreSQL libc не эквивалентны для всех строк —
точные границы описаны в [DATABASE_CATALOG](DATABASE_CATALOG.md).

При добавлении нового SQL installer включать его в список SCRIPTS общего
установщика и проверять порядок зависимостей. Отдельные скрипты ниже остаются
доступны для явно выбранного этапа установки.

## APP-15: временные отношения

Профиль принят в `7799cd3a54` после review и независимой приёмки. Native LTT
обеспечивают PG TEMP/pg_temp, PRESERVE/DELETE, DML/индексы, rollback/savepoints и
изоляцию сессий. Живые pg_class/pg_namespace/pg_my_temp_schema и visibility читают
тот же attachment state. `_` names, effective relation path, USAGE/$user и cache
проверены вместе с lifecycle, установкой и native регрессиями.

На исправной базе APP-14 выполнить владельцем/администратором без concurrent DDL:

```sh
isql -bail -user OWNER -i pg_wire_protocol/1c/sql/install_temporary_relations.sql DATABASE_ALIAS
```

Это один атомарный переход, без автоматического repair и без ODS migration.
Повтор/чужой контейнер/изменённые prerequisite projections дают отказ. Прежние
IDs/grants сохраняются; sequence gaps после rollback допустимы. Общий установщик
включает новый файл седьмым для первоначальной установки.

[Полный контракт и права allocator](../COMPATIBILITY.md#app-15-временные-отношения),
[приёмка и hashes](../VALIDATION.md#12-ноября-2026-app-15-полный-цикл-поддержанных-pg-temporary-tables).
APP-16 добавляет типы/regnamespace и проверяет selected04/12. ON COMMIT DROP остаётся открытым; serial/defaults и ANALYZE/fasttrun
добавлены следующим этапом, описанным в начале этого файла. Отдельно найден прежний assertion persistent
view без FROM (NAT-07); сохранён reproducer, исправление не включено в APP-15.

## APP-13: имена и установка lowercase профиля

Реализовано и принято в `4520f371e9`: PG4 unquoted ASCII lowercase, точные quoted
имена, native-safe stored SQL, реальные lowercase schemas/catalog entry points и
начальный путь `"public", "pg_catalog", "SYSTEM"`. Native 1/2/3 сохраняются.
`foo`/`FOO`/`"foo"` совпадают; старые native объекты требуют точных quotes, например
`SELECT "V" FROM "PUBLIC"."TTT"`. Нет uppercase fallback или копирования user data.
Полный [контракт](../COMPATIBILITY.md#app-13-правила-имён-pg4),
[review и независимые проверки](../VALIDATION.md#11-ноября-2026-архитектурный-review-правил-имён-pg4).

На чистой отдельной UTF8/dialect3 базе запустить native isql от владельца/администратора
в следующем порядке (credentials задаются вне SQL):

```sh
isql -bail -user OWNER -i pg_wire_protocol/1c/sql/install_namespace_registry.sql DATABASE_ALIAS
isql -bail -user OWNER -i pg_wire_protocol/1c/compat.sql DATABASE_ALIAS
isql -bail -user OWNER -i pg_wire_protocol/1c/sql/install_namespace_catalog.sql DATABASE_ALIAS
isql -bail -user OWNER -i pg_wire_protocol/1c/sql/install_class_catalog.sql DATABASE_ALIAS
isql -bail -user OWNER -i pg_wire_protocol/1c/sql/install_pg_names.sql DATABASE_ALIAS
```

Поверх исправного APP-10/11/12+compat.sql выполнить только последний скрипт, при
активном namespace hook и без конкурентного DDL. `"pg_catalog"` должен отсутствовать:
повтор/чужая schema означают отказ. `"public"` создаётся IF NOT EXISTS, существующие
owner/data сохраняются. SET BAIL ON, AUTODDL OFF и один COMMIT дают атомарность;
rollback новых schemas может оставить sequence gaps. Автоматических install/repair
на attach нет. Рабочие базы и реальные таблицы 1С автоматически не мигрируются.

Новые DISTINCT views `pg_catalog.pg_namespace(oid,nspname)` и
`pg_catalog.pg_class(oid,relname,relnamespace,relkind)` доступны unquoted и read-only.
Их labels lowercase, значения точные (включая uppercase), типы APP-11/12 прежние:
wire20/1043 и wire20/1043/20/1043. Native PUBLIC/PG_CATALOG и lowercase public/pg_catalog
имеют разные реальные registry IDs; старые IDs не меняются. Обычная PSQL-обёртка
`pg_catalog.pg_backend_pid()` вызывает старую функцию. PUBLIC получает только
USAGE этих schemas, SELECT views и EXECUTE wrapper. CREATE/DML права приложения
задаются отдельно; schemas/views bootstrap не дают их всем. Например, native
`GRANT CREATE TABLE ON SCHEMA "public" TO USER "APP_USER"` выдаёт право создания
таблиц именно в lowercase schema; обычный native `GRANT CREATE TABLE` без ON SCHEMA
относится к текущей native schema, обычно PUBLIC.

После установки через PgWire:

```sql
SELECT pg_catalog.pg_backend_pid();
SELECT oid, nspname FROM pg_namespace ORDER BY oid;
CREATE TABLE Foo(Bar INTEGER); -- при отдельных CREATE правах: "public"."foo"
INSERT INTO FOO VALUES(1);
SELECT bar FROM "foo";
```

ALTER SESSION RESET восстанавливает public-first профиль. Пользовательский SET
SEARCH_PATH действует до явного изменения/reset. Полный PG search_path/USAGE,
regnamespace/oid/name, visibility/temp и selected04/10/11 остаются открытыми.
Quoted trailing space/NUL отвергаются, native длины/лексика сохранены; нет U&/ICU
или усечения 63 bytes. APP-15 разрешает начальный `_` в unquoted PG имени, сохраняя charset introducers (SQL-09).
Stored SQL quotes folded identifiers и сохраняет literal
канонизацию, в том числе strings=off. Старые `$n`/`::`/LIMIT root guards остаются.

## APP-14: видимость постоянных отношений

После APP-10…13 установить [отдельную функцию](sql/install_relation_visibility.sql)
на runtime с builtin `RDB$PG_RELATION_ID`:

```sh
isql -bail -user OWNER -i pg_wire_protocol/1c/sql/install_relation_visibility.sql DATABASE_ALIAS
```

Installer атомарный: BAIL ON, AUTODDL OFF, один COMMIT; plain CREATE FUNCTION
отвергает повтор и чужой одноимённый объект. Существующие schemas/views/grants
сохраняются, PUBLIC получает EXECUTE. Автоматической установки при attach нет.
Функция работает и в закрытой базе APP-13 после смены runtime без ODS migration.

`pg_catalog.pg_table_is_visible(BIGINT) RETURNS BOOLEAN` сопоставляет точное имя
из текущего pg_class с первым native relation ID в текущем пути. NULL/неизвестный
OID → NULL, отсутствие в пути или shadowing → FALSE, совпадение → TRUE. Метаданные
остаются native: аргумент wire20, результат wire16 (binary 01/00/NULL), не OID26.
SQL SECURITY INVOKER; наличие SELECT права не проверяется. Helper использует
штатные SCL и resolver, без нового API/BLR opcode/ODS/registry/codecs.

После APP-15 общий resolver поддерживает implicit/explicit pg_temp, implicit
pg_catalog, exact $user и пропуск missing/no-USAGE schemas. Unmanaged native LTT/GTT
и packaged relations остаются за профилем с явным отказом. Порядок explicit path,
initial/RESET и session lifetime SET сохранены. Helper читает состояние на Execute;
старый native SELECT может сохранять прежнюю привязку, свежий Prepare/wire Bind
разрешает имя заново.

Полный [контракт](../COMPATIBILITY.md#app-14-видимость-постоянных-отношений),
[задание](RELATION_VISIBILITY_TASK.md), результаты — [VALIDATION](../VALIDATION.md).
Review `8ffd22cb76`: 174 libpq results / 46 OO checks PASS; найденный P2 при
аргументе, меняющем состояние attachment, исправлен в принятом `11dee63d2a`.
Текущий guard проверяет состояние после EVL_expr, в том числе при NULL результате.
На момент APP-14 (12 ноября) исходному selected04 ещё требовался regnamespace.
pg_my_temp_schema добавлен APP-15, а исходный selected04 без подмены predicate
прошёл в APP-16. Видимость persistent indexes и общий table/index namespace
добавлены 13 ноября; текущий [контракт](INDEX_CATALOG.md) не меняет native dialects.

Незавершённые этапы, ограничения частичных реализаций и пробелы приёмки ведутся
в общем [BACKLOG](../BACKLOG.md). Обновлять его после каждого этапа и review.

Задание: `/home/aleksey/dev/1c/CODEX_PGWIRE_1C_TASK.md`, 9 ноября 2026 года.
Исходная ревизия: `0e505759d0deb1693d912bbc9fee978a55d1e1f0`.
Корпус: `/home/aleksey/dev/1c/captures/20261109T105635Z`, 24 selected examples
и 243 формы. SQL сохраняется без изменений; DETAIL параметров не считается
записью Bind. Полный журнал, конфигурационные BLOB и credentials в Git не входят.

## Первый срез: SELECT без FROM и идентификация подключения

Исторический объём от 9 ноября 2026 года; последующие результаты указаны выше.

Первый запрос пяти новых соединений — `SELECT pg_catalog.pg_backend_pid()`
(selected `01-backend-pid`, capture_seq 98). Для его выполнения используются:

- клиентский `SQL_DIALECT_PG` (4), передаваемый PgWire в существующий prepare;
  dialect БД/DPB и `SQL_DIALECT_CURRENT` остаются 3;
- необязательный FROM только в PG-режиме штатного parser; пустой список источников уже поддержан
  существующим DSQL/optimizer как одна строка, без подстановки RDB$DATABASE;
- реальная PSQL-функция из [compat.sql](compat.sql), возвращающая
  `CAST(CURRENT_CONNECTION AS INTEGER)`;
- существующие schemas, grants, metadata и INTEGER codec PgWire.

Функция реализует **ограниченный контракт одной базы**: стабильный идентификатор
attachment, различный у одновременных соединений и совпадающий с
`MON$ATTACHMENT_ID`. Это не PID процесса ОС. В Super процесс общий для сессий,
поэтому возвращение SERVER_PID не удовлетворяет наблюдаемой уникальности.
Преобразование в int4 проверяет диапазон и выдаёт ошибку при переполнении;
усечение, modulo и возможные коллизии не применяются. Уникальность между разными
базами и связь с PostgreSQL CancelRequest не заявляются. Это не полный контракт
PostgreSQL backend identity и не завершение этапа 1 или открытия базы 1С.

Новых методов API/ABI, runtime-зависимостей, конфигурационных параметров,
context keys, счётчиков и реестров нет. Добавлена одна публичная константа
к существующему аргументу SQL dialect. Плагин не переписывает SQL.
Проверки и фактические результаты фиксируются в [VALIDATION.md](../VALIDATION.md).

### Граница диалекта

PgWire выбирает dialect 4 при Simple Query, Extended Parse и повторной подготовке
portal. Этот режим включает PG-дополнения грамматики; типы и арифметика сохраняют семантику dialect 3. Обычные одинарные строки учитывают новую настройку; native Q-строки и двойные кавычки сохраняются. Поддержаны `SELECT` без `FROM` и описанный ниже
фиксированный профиль `lc_messages`/`client_encoding`, а также изменяемый `standard_conforming_strings`. Последовательные типизированные `$1` … `$n` поддержаны в ограниченном контракте ниже;
Postfix `::`, literal `LIMIT n` и PG BEGIN без опций поддержаны в ограниченных контрактах ниже.

Ключ штатного statement cache содержит исходный клиентский dialect и эффективный режим строк: PG и
native планы не смешиваются. Обычные native dialects 1/2/3 по-прежнему требуют
FROM. Базу не нужно создавать или переводить в dialect 4; DPB и isql его не
принимают. Режим выбирается явно на границе API подготовки SQL, а не по имени
пользователя или глобальной настройке.

Сохранённые PSQL-объекты исполняют обычный BLR. Наследование PG-режима для
динамического `EXECUTE STATEMENT`, внутреннего SQL DDL и SQL-репликации в этот
срез не входит. Установочная функция написана на native PSQL. Имена колонок и
идентификаторы пока следуют Firebird: результат исходного запроса называется
`PG_BACKEND_PID`, тогда как эталон возвращает `pg_backend_pid`.

## Последовательные numbered parameters: первый срез

Только клиентский PG dialect 4 распознаёт отдельный `$` + ASCII digits. В корневых
SELECT/INSERT/UPDATE/DELETE/MERGE, включая CTE/subqueries, каждый номер должен
встретиться ровно один раз: `$1`, затем `$2`, … . Тип выводится обычным DSQL из
колонки, DML или `CAST($n AS native_type)`. Значения передаются отдельно через
Extended Bind; Simple с типизированным `$1` возвращает 0A000, без исполнения с NULL.

```sql
SELECT CAST($1 AS INTEGER), CAST($2 AS VARCHAR(40));
SELECT ID FROM PARAM_ROWS WHERE NAME = $1;
INSERT INTO PARAM_ROWS(ID, NAME) VALUES($1, $2);
UPDATE PARAM_ROWS SET NAME = $1 WHERE ID = $2;
```

| Форма | Результат |
|---|---|
| Повтор, пропуск, первый `$2`, перестановка, смесь `?`/`$n` | 0A000 до успешной подготовки |
| `$0` / `$000` | 42P02 |
| Номер >32767, включая переполнение при чтении | 54000 до narrowing/allocation |
| `$01` / `$0002` | Номера 1 / 2 |
| `$1foo`, `$1_foo`, `$1$foo` | 42601, не неявный alias |
| Bare `$`, `$-1`, `$ 1`, dollar quotes | Прежние parser errors |
| Синтаксически распознанные CALL/EXECUTE PROCEDURE/BLOCK, USING, DDL/stored PSQL, UPDATE OR INSERT с `$n` | 0A000 до DSQL transformations; неправильная грамматика остаётся syntax error |

`$n` внутри ordinary/Q strings, comments и quoted identifiers — обычный текст;
A$1 и RDB$DATABASE сохраняются. Native dialects 1/2/3 не принимают `$n`, прежний
`?` и идентификаторы с `$` работают. Cache разделён по dialect и string mode;
line/column относятся к исходному SQL. 32767 ограничивает номер и не обещает
исполнения сообщения такого размера: ограничения engine/message/codecs остаются.

Parse OID hints проверяются существующим InputCodec **после** engine inference:
0/omitted используют выведенный тип, совместимые OID принимаются, несовместимые
дают 0A000, избыточный список — 08P01. `SELECT $1` без контекста даёт HY004
как `SELECT ?`, в том числе с supplied OID=23. Новых API/flags/type fallback нет.
MAKE_parameter/GEN_port, Extended и codecs не изменены; номера проверяются в
подтверждённых semantic actions (`!yytrial`), допустимый корень — по AST.

Это ограничение строже PostgreSQL, где номер определяет позицию независимо от
повторов и порядка: [positional parameters](https://www.postgresql.org/docs/18/sql-expressions.html#SQL-EXPRESSIONS-PARAMETERS),
[PREPARE](https://www.postgresql.org/docs/18/sql-prepare.html),
[Parse/Bind messages](https://www.postgresql.org/docs/18/protocol-message-formats.html).
Произвольная нумерация, OID-driven inference, новые codecs и полная
параметризация Psycopg 3 остаются отдельными этапами.

`pgwire_1c_smoke.py --parameters` проверяет реальный libpq Bind, metadata,
92 разных значения с native readback, поиск по FILENAME, CTE/MERGE/RETURNING,
I/T/E/recovery и запреты. Это синтетическая форма selected 06/19, **не** исходная
схема, BinaryData/bytea или открытие базы 1С. Результаты — в [VALIDATION](../VALIDATION.md).

## Postfix cast `expression::type`: первый срез

Исторический объём APP-04 от 10–11 ноября 2026 года. Последующий APP-16 добавил
profile types/casts, включая bytea, mchar/mvarchar и regnamespace; ограничения
корней DDL/stored PSQL для postfix syntax сохраняются.

В клиентском PG dialect 4 непрерывный токен `::` создаёт прежний CastNode.
Значения, округление, truncation, overflow и вывод входных типов соответствуют
**Firebird CAST**, а не всем PostgreSQL coercion/typmod rules. Приоритет `::`
выше unary +/−, арифметики, конкатенации, COLLATE и AT; цепочка левоассоциативна.
Прежние взаимные native приоритеты не меняются. `: :` и `:/*comment*/:` — не оператор.

```sql
SELECT 1::INT8;
SELECT (1.0)::int;
SELECT $1::INTEGER::VARCHAR(12); -- input INTEGER (OID 23), output VARCHAR (1043)
UPDATE CAST_ROWS SET V=$1::BIGINT WHERE ID=$2;
```

Цель переиспользует `non_array_type`: native builtin types без массивов. Проверены
INTEGER/INT, SMALLINT, BIGINT, VARCHAR(n), BOOLEAN, DOUBLE PRECISION и дополнительные
native границы. Bare VARCHAR сохраняет native default 255, NUMERIC — native defaults;
это не unlimited PG VARCHAR/NUMERIC. Существующие codec limits, включая выдачу non-binary BLOB
и типов с time zone, сохраняются; синтаксис не добавляет новых wire codecs.

Единственный новый alias — unquoted INT8 → BIGINT **только** после `::`, без учёта
регистра. INT8 остаётся именем колонки/alias; `CAST(x AS INT8)` и quoted INT8
в обычном CAST по-прежнему разрешают существующий домен. Postfix `::"INT8"`,
`::schema.int8`, домены/TYPE OF/arrays, INT2/INT4/FLOAT4/FLOAT8/BOOL, bytea,
mvarchar и regnamespace не поддерживаются. Alias не зависит от schema/search_path.

Первый срез разрешён только в корневых SELECT/INSERT/UPDATE/DELETE/MERGE, включая
CTE/subqueries. Распознанные DDL/CTAS/VIEW/stored PSQL, EXECUTE BLOCK,
CALL/EXECUTE PROCEDURE, USING и UPDATE OR INSERT с настоящим `::` дают 0A000
до DSQL/эффектов; неверная грамматика остаётся syntax error. Literal `'::'`,
Q-strings, comments и quoted identifiers guard не включают. Прежний CAST в этих
контекстах сохраняется. Это исключает сохранение PG syntax в native source:
serializer, rewriting и расширение внутренних prepare в этот этап не входят.

Parse OID по-прежнему не задаёт тип engine. В `$1::INTEGER::VARCHAR(12)` внутренний
CastNode выводит INTEGER input, внешний меняет результат; при несовместимом OID
остаётся 0A000. Ограничения последовательных `$n` и native `?` сохраняются.
Native dialects 1/2/3 не принимают `::`, но сохраняют CAST, INT8 identifiers и
`:variable`. Source offsets и разделение statement cache по dialect сохранены.

Приёмка — `pgwire_1c_smoke.py --casts` и прежний OO helper внутри `--strings`;
команды, SHA, разбор grammar conflicts и результаты — в [VALIDATION](../VALIDATION.md).
Это синтетические проверки форм из selected 04/08/10/11, не выполнение исходных
запросов с каталогами/custom types/advisory locks/LIMIT и не открытие базы 1С.
Основания: [type casts](https://www.postgresql.org/docs/18/sql-expressions.html#SQL-SYNTAX-TYPE-CASTS),
[precedence](https://www.postgresql.org/docs/18/sql-syntax-lexical.html#SQL-PRECEDENCE),
[integer aliases](https://www.postgresql.org/docs/18/datatype-numeric.html#DATATYPE-INT).

## LIMIT с целочисленным литералом: первый срез

По [LIMIT_TASK](LIMIT_TASK.md), 11 ноября 2026 года, PG4 принимает SELECT-tail
`LIMIT n` после необязательного ORDER BY. Count — только `long_integer` /
NUMBER32BIT, значение 0…2147483647. Сохраняются штатные integer spellings:
ведущие нули, 0x/0o/0b и `_`; отдельного scanner или narrowing нет.

```sql
SELECT 42 LIMIT 0;
SELECT ID FROM LIMIT_ROWS ORDER BY ID LIMIT 2;
SELECT FILENAME FROM LIMIT_FILES WHERE FILENAME=$1 LIMIT 1;
INSERT INTO LIMIT_COPY(ID) SELECT ID FROM LIMIT_ROWS ORDER BY ID LIMIT 2;
```

Ограничивается готовый результат своего SELECT уровня после DISTINCT/GROUP/ORDER.
Внешний LIMIT относится ко всему UNION/UNION ALL, внутренний в скобках — только
к своему operand/subquery. Native правила UNION ORDER BY сохраняются: позиция
колонки, например `ORDER BY 1`; вычисленным столбцам в body wrapper нужны aliases.
Без ORDER BY конкретные строки/порядок не обещаются.
LIMIT 0 сохраняет metadata, не выдаёт DataRow и завершает `SELECT 0`.
Обычные Bind-параметры вне count работают по прежнему контракту. Execute maxRows
приостанавливает курсор; SQL LIMIT сохраняется между Execute, счётчики разных
portals независимы.

LIMIT — reserved keyword только в PG4: `SELECT 1 LIMIT` является ошибкой.
Native1/2/3 сохраняют идентификатор LIMIT; quoted `"LIMIT"` работает в dialect3/4.
ALL/NULL, `$n`/`?` в count, выражения/скобки/знаки, decimal/exponent literals,
числа вне диапазона, PG OFFSET и `LIMIT offset,count` отвергаются parser.
FIRST/SKIP с LIMIT на одном уровне получает прежний `isc_dsql_firstskip_rows`;
разные уровни допустимы. Native ROWS и OFFSET…FETCH отдельно сохраняются,
их смешение с LIMIT на одном уровне — syntax error.

Корневые операторы — SELECT/INSERT/UPDATE/DELETE/MERGE с SELECT subqueries/CTE.
Суффикс LIMIT у UPDATE/DELETE не добавлен. DDL/CTAS/VIEW/stored PSQL/BLOCK,
CALL/PROCEDURE/USING/UPDATE OR INSERT с распознанным LIMIT получают 0A000
до DSQL; неверная грамматика остаётся syntax error. Строки/Q-strings/comments
и quoted identifiers guard не включают; native FIRST/ROWS в сохранённом SQL
продолжают работать.

Production изменён только в Parser.cpp/h и parse.y: PG token, подтверждённый
parser flag, прежний root guard и RowsClause на обоих путях SELECT, включая
DFLAG_BODY_WRAPPER у query_primary. Исполнение использует прежние PASS1_limit,
rse_first и FirstRowsStream; plugin, API, metadata, codecs и optimizer не менялись.
`pgwire_1c_smoke.py --limits`, OO helper в `--strings` и обязательные регрессии
прошли; команды и evidence — в [VALIDATION](../VALIDATION.md).

SQL-01 закрывает только этот literal-срез; SQL-02 остаётся открытым. NULL count
нельзя просто передать native stream: там он означает ноль строк. В исходной
приёмке этого среза (11 ноября) selected04/17 ещё требовали каталогов/custom types;
оба исходных запроса позже проверены в APP-16. Открытие базы тонким клиентом
подтверждено отдельно 13 ноября; оно не входило в приёмку literal LIMIT.

## Binary BLOB как bytea: только output

Ниже исторический контракт APP-07 для BLOB/0. APP-16 добавляет SQL bytea и profile
строки; актуальный общий контракт указан в начале документа.

По [BINARY_BLOB_OUTPUT_TASK](BINARY_BLOB_OUTPUT_TASK.md), 11 ноября 2026 года,
ResultCodec принимает только native SQL_BLOB subtype 0 (BINARY), с корректным
размером ISC_QUAD и прежними проверками offsets/null offsets/message length.
RowDescription: OID 17, typeSize -1, typmod -1. Text output — ASCII `\x` и
lowercase hex по две цифры на байт; binary output — исходные байты. NULL имеет
wire length -1 и не открывает BLOB. Пустой ненулевой BLOB — text `\x` либо
binary length 0. Нулевой ID не заменяет null indicator. NUL и invalid UTF8
в payload разрешены; charset conversion и validateText к содержимому не применяются.

Общий путь работает в Simple и Extended, для SELECT cursor, singleton output,
DML RETURNING и native EXECUTE BLOCK/PROCEDURE. Декодирование получает borrowed
attachment и текущую transaction только на одну строку. Describe/Parse/Bind
ничего не открывают; codec/prepared metadata не сохраняют transaction, handle
или содержимое BLOB. Повторный Bind после commit/rollback и независимые portals
проверены. Все BLOB закрываются до Protocol::dataRow; consuming close использует
RefPtr::clear без повторного release, ошибки сохраняют прежнюю engine mapping.

Чтение идёт фиксированными порциями 4096 байт до RESULT_NO_DATA; RESULT_OK и
RESULT_SEGMENT добавляют байты без разделителей. Пустой сегмент не означает EOF.
Единый бюджет строки берётся из фактического Protocol limits.frameBytes (сейчас
1 MiB): `4 + 2 + 4 * число полей + сумма ненулевых wire values`, без тега D.
Text bytea занимает `2 + 2 * payload length`. Все колонки/форматы делят этот
бюджет; арифметика и размер проверяются до добавления каждой порции/hex output.
При полном заполнении ещё одно чтение отличает EOF от превышения. Размер всей
BLOB заранее не резервируется; Protocol сохраняет независимый preflight.

Превышение даёт 0A000 без усечения и частичного DataRow. Ранее отправленные строки
не отзываются. Ошибка output возникает после engine execution: прежняя обработка
откатывает implicit сегмент либо переводит T→E; для DML RETURNING проверены
rollback/recovery и отсутствие строк через native observer. Это не отказ до
любых side effects. Unsupported TEXT/другие BLOB subtype по-прежнему дают 0A000
на metadata до исполнения.

APP-07 не добавлял input и не менял InputCodec; bounded input добавлен в APP-08
ниже. SQL type/cast `bytea`, literals, сравнения/индексы/OCTETS, large-object API
и SET bytea_output остаются вне обоих этапов. Bind остаётся до startImplicit.
Engine/parser/публичные API/зависимости также не менялись.

`pgwire_1c_smoke.py --blobs` использует прежние libpq/raw/native wrappers и небольшой
native OO fixture helper. Проверена неизменная форма selected06 с WHERE `$1` и
ORDER BY PartNo на синтетической PUBLIC.PARAMS, без исходных данных или записанного
Bind. APP-07 прошёл review и закрыт; типы/large Bind добавлены APP-16,
а открытие базы настоящим клиентом подтверждено отдельно 13 ноября. Команды, SHA, размеры и результаты — в
[VALIDATION](../VALIDATION.md).

## Установка на отдельной тестовой базе

1. Пересобрать сервер и плагины текущей ветки вместе. Использовать отдельную
   UTF8/dialect 3 базу Firebird 6 и настоящего пользователя с правами.
2. Настроить PgWire по [quickstart](../quickstart.md), добавив alias этой базы
   в `databases.conf` и `DatabaseAliases`. В `Config = PgWireConfig` установить
   существующий `Workers = 8`: в записанной нагрузке шесть соединений.
3. Через native isql выполнить [полный порядок bootstrap](#app-13-имена-и-установка-lowercase-профиля).
   Одного старого compat.sql недостаточно для unquoted lowercase entry points.
4. Через PgWire выполнить `SELECT 42 AS "answer"` и `SELECT pg_catalog.pg_backend_pid()`.
   Synthetic fixtures не переносят реальные данные 1С и не подтверждают её открытие.

## История небольших этапов: 9–12 ноября 2026 года

Ниже сохранён объём прежних срезов. Начиная с APP-15 связанные изменения объединяются
в цельный прикладной этап; результат открытия клиента и последующая цель Гилёва
указаны в начале файла и [PROGRESS](PROGRESS.md).

[Синтаксический приём трёх startup SET](IGNORED_STARTUP_SETTINGS_TASK.md)
без эффекта реализован по явному решению пользователя; контракт описан ниже.
[PG BEGIN](BEGIN_TASK.md) реализован отдельным этапом через существующий
SetTransactionNode и общий executor. Оба этапа в `1f9af20db4`/`97e44d0188`
прошли архитектурный review и повторную целевую проверку; результаты — в VALIDATION.

Срез [последовательных $1 … $n](NUMBERED_PARAMETERS_TASK.md) реализован через
штатные Parser/ParameterNode и Bind; ревизия `df37bb11fa` прошла архитектурный
review и повторную целевую приёмку, см. VALIDATION.
Разрешены уникальные номера по порядку в SELECT/INSERT/UPDATE/DELETE/MERGE
с типами из SQL-контекста. Точный контракт приведён выше.

Реализован [postfix cast expression::type](POSTFIX_CAST_TASK.md) через
существующий CastNode и native types, с INT8 → BIGINT только в postfix target.
Первый срез ограничивается SELECT/DML без сохранения PG SQL в metadata.
Ревизия `9f94454527` прошла архитектурный review и повторную целевую приёмку;
контракт описан выше, результаты — в VALIDATION.

Реализован [LIMIT с целочисленным литералом](LIMIT_TASK.md) через RowsClause и
штатное исполнение: count 0…2147483647, без PG OFFSET/ALL/NULL/count-параметров.
Ревизия `ccd81f6473` прошла review и повторную целевую приёмку; SQL-01 закрыт,
расширенный LIMIT/OFFSET остаётся открытым SQL-02 в [BACKLOG](../BACKLOG.md).

Реализована [выдача binary BLOB как bytea](BINARY_BLOB_OUTPUT_TASK.md), APP-07:
ResultCodec + штатный IBlob, text/binary output и общий предел DataRow. Ревизия
`5478a589d8` прошла review и повторную целевую приёмку; APP-07 закрыт. APP-16 добавляет большие
входные BLOB, bytea grammar и рабочий профиль APP-02; полный catalog APP-01 открыт.

Реализован [bytea input через native VARCHAR OCTETS](BINARY_BLOB_INPUT_TASK.md),
APP-08: text hex/escape и raw binary до 65533 decoded bytes на параметр,
physical layout по фактическим длинам Bind. Engine создаёт BLOB штатным MOVE
при Execute; новых handles/transaction lifecycle в плагине не требуется.
Проверены libpq/raw/native readback, selected23 на синтетической PUBLIC.FILES
и 68 BLOB среди 92 mixed параметров; `c27a3a4c5a` прошёл review и независимую
приёмку на свежей сборке, APP-08 закрыт. APP-16 реализует большие значения
и исходные SQL bytea types/casts. Настоящая схема/данные были подготовлены
13 ноября; остающиеся 19 schema limitations перечислены в [PREPARE_INFOBASE](PREPARE_INFOBASE.md).

Срез [фиксированной диагностики](FIXED_DIAGNOSTICS_TASK.md) реализован через
существующий SetPgSessionNode: только `escape_string_warning=off` и
`client_min_messages=error`. Ревизия `4abc5af919` прошла архитектурный review
и повторную целевую проверку; результаты — в VALIDATION.

### Текущее состояние групп исходного задания: 13 ноября 2026 года

Номера групп здесь относятся к исходному заданию, а не к этапам 1–5 в PROGRESS.

| Группа исходного задания | Текущее состояние / границы |
|---|---|
| 1. Начало сессии | SELECT без FROM, identity-функция, последовательные скрипты и семь startup SET; настоящие libpq 14.2/Startup 1С проверены. Эффекты enable_mergejoin, cpu_operator_cost и lock_timeout отложены |
| 2. SQL и параметры | Simple scripts, PG BEGIN без опций, последовательные типизированные `$n`, postfix casts и literal LIMIT поддержаны. Реальные Bind с binary result читают конфигурацию. Произвольная нумерация и общий LIMIT/OFFSET не реализованы |
| 3. Открытие базы | Database/locale/type/proc/extension catalogs, pg_class/pg_tables/pg_attribute/pg_index и visibility реализованы в документированных профилях; настоящие данные перенесены. Открытие начальной страницы тонким клиентом 1С 8.5.4.1306 подтверждено; benchmark не запускался |
| 4. Типы и функции | APP-16 принят после независимого review; последняя регрессия 583 / 10054 / 214 PASS. Fulleq, datediff2/vassn и format_number проверены по oracle. Полная система типов/функций PostgreSQL не заявлена |
| 5. Транзакции/конкуренция | PG BEGIN использует native READ CONSISTENCY; shared/exclusive transaction advisory locks работают с native lock manager, savepoints и recovery. statement_timeout использует native attachment lifetime, не полную PG SET semantics |
| 6. Временные объекты/нагрузка | PG TEMP, serial/serial4 и native LTT identity, реальные ANALYZE/fasttruncate/fasttrun_ext проверены. ON COMMIT DROP и полный тест Гилёва остаются за границей принятого профиля |

### Принятые основы каталогов: история 11–12 ноября 2026 года

Реализован [APP-10: внутренний namespace registry](NAMESPACE_REGISTRY_TASK.md),
ревизия `5bbdaa763d` прошла review и независимую приёмку, см. VALIDATION.
Устанавливаемые SQL table/sequence/view/DDL trigger поддерживают
устойчивые IDs точных native имён; C++/parser/API не меняются. Эта основа использована
последующими каталогами; selected04 прошёл в APP-16. Native DDL под пользовательским savepoint
запрещён (0A000); проверены отказ и ROLLBACK TO recovery без изменения registry.

Реализован [APP-11: публичный pg_namespace](NAMESPACE_CATALOG_TASK.md);
`a3e3c80123` прошёл review без замечаний и независимую приёмку.
SQL-view OID/NSPNAME над этим registry и отдельная установка; имена точные
native, metadata BIGINT/VARCHAR (wire20/1043). Это ограниченная проекция реальных
данных, не полноценные PG oid/name. Name resolution и temp namespace добавлены
APP-13…15, regnamespace — APP-16; полный PG catalog остаётся вне контракта. Metadata
schema/relation/field не заменяет type identity: UNION может сохранить origin
первой ветви при общем BIGINT. C++/API/codecs в APP-11 не меняются. Путь до первого
запуска 1С/Гилёва — в [PROGRESS](PROGRESS.md); открытие UI подтверждено 13 ноября,
полный тест Гилёва не запускался.

Реализован [APP-12: pg_class для постоянных таблиц/views](CLASS_CATALOG_TASK.md);
`13f64d0e00` прошёл review без замечаний и независимую приёмку.
Четыре поля читаются из SYSTEM.RDB$RELATIONS с JOIN PG_NAMESPACE. OID — native
relation ID+1; новый allocator/registry/hook не нужен. Сохраняются точные native
имена и BIGINT/VARCHAR metadata, native reuse после DROP/wrap допустим.
Этот независимый срез предшествовал name resolution/regnamespace и temporary
objects. Они добавлены APP-13…16; persistent index catalog — 13 ноября.
Полный набор полей/видов объектов pg_class по-прежнему не заявлен.

Пользователь выбрал PG-правила имён и укрупнение следующих инструкций.
[APP-13](PG_IDENTIFIER_NAMES_TASK.md) объединяет lowercase unquoted identifiers
в PG4, точные quoted names, сохранение native-safe SQL source, lowercase
bootstrap/catalog labels, initial path/RESET и savepoint journal. Native1/2/3
не меняются; старые uppercase объекты в PG4 требуют точных quoted имён.
Ревизия `4520f371e9` прошла review и независимые names/strings проверки;
установка и новый контракт описаны выше. APP-14 с ограниченной visibility
проверен: целевая матрица PASS, P2 исправлен отдельно 12.11. После NAT-06
приняты APP-15 с общим TEMP lifecycle и APP-16 с regnamespace/типами 1С.
Транзакционный search_path и полный PG catalog остаются отдельными ограничениями.

## Постоянные таблицы и представления в PG_CLASS: APP-12

Исторический контракт от 11 ноября 2026 года. Последующие установщики добавляют
свои PG LTT и [persistent indexes](INDEX_CATALOG.md) в lowercase pg_class;
описанные ниже исключения относятся к исходному срезу APP-12.

[install_class_catalog.sql](sql/install_class_catalog.sql) устанавливает только
PG_CATALOG.PG_CLASS(OID, RELNAME, RELNAMESPACE, RELKIND). Предусловия: исправные
APP-10/11 с активным namespace hook, запуск владельцем/администратором без
конкурентного DDL. PG_CATALOG и её USAGE уже обеспечены APP-11. Пример с паролем,
передаваемым обычными средствами native isql:

```sh
isql -user OWNER -input pg_wire_protocol/1c/sql/install_class_catalog.sql DATABASE_ALIAS
```

SET BAIL ON, SET AUTODDL OFF, квалифицированный CREATE VIEW, GRANT SELECT TO PUBLIC
и один COMMIT. Работа не зависит от search_path; schema, APP-10/11 и compat.sql
не переустанавливаются. Повтор или чужой PG_CLASS дают явный отказ без замены.
Ошибка до COMMIT откатывает view/grants, сохраняя bootstrap, namespace mapping
и counter; gap штатного relation allocator допустим. Отсутствие зависимости —
обычная SQL error. Repair/upgrade и проверка отключённого hook не добавлены.

| Поле | Значение | Native / wire type |
|---|---|---|
| OID | RDB$RELATION_ID + 1; RDB$PAGES ID0 → OID1 | BIGINT / 20 |
| RELNAME | Точное native имя без хвостового metadata CHAR padding | VARCHAR / 1043 |
| RELNAMESPACE | Соответствующий PG_NAMESPACE.OID | BIGINT / 20 |
| RELKIND | `r` для persistent table, `v` для view, без padding | VARCHAR / 1043 |

Все labels остаются uppercase. Binary IDs — 8 bytes network order, имена/kind —
точные UTF8 bytes. PUBLIC/"public", T/"t" и одинаковые имена в разных схемах
не объединяются. Это native типы, не PG oid/name/internal "char"; aliases или
происхождение колонки не меняют type identity.

Область view: SYSTEM.RDB$RELATIONS с type 0 (включая legacy NULL) или 1,
RDB$PACKAGE_NAME IS NULL, JOIN PG_NAMESPACE по точному native schema name.
Подходящие SYSTEM/служебные объекты и сам PG_CLASS включены. Нет фильтра по
SELECT/USAGE/search_path; catalog row не доказывает доступ к данным или visibility.
DISTINCT делает view read-only и для владельца. Новых прав на исходные таблицы
или DDL installer не выдаёт. Read-only lookup не пишет и не выделяет IDs.

OID локален этому каталогу/БД, одинаков между attachments и стабилен при ALTER
в течение жизни объекта. DROP удаляет строку; CREATE берёт текущий native ID.
Диапазон native 0…32767 отображается в 1…32768. Движок допускает wrap/reuse,
поэтому вечная уникальность и монотонность не обещаются; generation registry
не добавлен. После backup/restore/переноса стабильность IDs не проверена.
Числа могут совпадать с namespace IDs. Добавленные 13 ноября persistent indexes
используют отдельный BIGINT диапазон выше 2^32. Advisory locks имеют собственные
ключи приложения и native transaction lifetime; они не выделяют catalog IDs.
Sequence catalog остаётся вне профиля.

External/virtual relations (2/3), GTT (4/5), local/declared/packaged temporary
tables, indexes/sequences и остальные поля pg_class исключены. Это ограниченный
catalog постоянных таблиц/views. Native DDL под user savepoint по-прежнему
запрещён (0A000); поддержка DDL ROLLBACK TO не заявлена.

Проверка: `pgwire_1c_smoke.py --class-catalog`, прежний runtime/native/libpq harness
и helpers namespace-проверок. Результаты и SHA — в
[VALIDATION](../VALIDATION.md#11-ноября-2026-pg_class-для-постоянных-таблиц-и-представлений).
Диагностический SELECT с relkind/relname/relnamespace/LIMIT прошёл; это изменённый
SQL, а не приёмка selected04/10/11 на дату APP-12. Позже selected04 принят
в APP-16, selected10/11 — с каталогами/advisory locks; PG naming, regnamespace,
search-path visibility и temp namespace также добавлены. Открытие
клиентского интерфейса 1С подтверждено отдельно 13 ноября.

## Публичное представление namespace IDs: APP-11

[install_namespace_catalog.sql](sql/install_namespace_catalog.sql) устанавливает
только PG_CATALOG.PG_NAMESPACE(OID, NSPNAME). Требуются уже установленный,
исправный APP-10 и активный DDL hook; проверка его отключения и repair не входят
в installer. Запуск владельцем базы/администратором без конкурентного DDL,
credentials передаются средствами native isql:

```sh
isql -user OWNER -input pg_wire_protocol/1c/sql/install_namespace_catalog.sql DATABASE_ALIAS
```

SET BAIL ON, SET AUTODDL OFF, квалифицированные ссылки и один COMMIT делают
установку независимой от search_path и атомарной. CREATE SCHEMA IF NOT EXISTS
сохраняет существующий bootstrap PG_CATALOG, его owner и PG_BACKEND_PID.
Если schema новая, APP-10 hook регистрирует её в той же transaction.
Отсутствие registry даёт обычную ошибку зависимости. Существующий PG_NAMESPACE
(включая чужой объект) означает отказ: повтор не upgrade, RECREATE не используется.
Ошибка до COMMIT откатывает новый view/grants и созданную schema с registry row;
старые объекты остаются. Sequence gaps после rollback допустимы.

```sql
CREATE VIEW PG_CATALOG.PG_NAMESPACE(OID, NSPNAME) AS
SELECT DISTINCT NAMESPACE_ID, NATIVE_NAME
FROM PGWIRE_COMPAT.NAMESPACES;
```

PUBLIC получает только USAGE PG_CATALOG и SELECT нового view. DISTINCT запрещает
запись даже владельцу; новых прав CREATE SCHEMA, private table/sequence/trigger
не возникает. Все строки registry видны независимо от USAGE перечисленных схем
и search_path. View хранит identity, не вычисляет table visibility. При чтении
нет allocation; CREATE/ALTER/DROP и rollback отражаются штатной transaction.

OID — прежний native BIGINT, wire type 20, decimal text / 8 bytes binary.
NSPNAME — VARCHAR, wire type 1043, полное UTF8 имя в обоих formats.
Native unquoted labels остаются OID/NSPNAME. PUBLIC и quoted "public" различаются;
Unicode не усекается до 63 bytes. PG oid/name/regnamespace и nspowner/nspacl здесь
отсутствуют. Выводить тип из schema/relation/field/alias нельзя: UNION сохраняет
origin первой ветви даже для общего BIGINT с 4294967296.

Проверка: `pgwire_1c_smoke.py --namespace-catalog`, отдельные временные базы,
native isql и настоящий libpq, text/binary metadata/bytes, права, атомарность
установки и transaction visibility. Команда, SHA и результат — в
[VALIDATION](../VALIDATION.md#11-ноября-2026-публичное-представление-namespace-ids).
APP-01/APP-02/APP-06 и selected04 открыты. APP-12 добавляет ограниченные
pg_class/relnamespace rows; затем нужны PG/native имена и namespace resolution,
search-path visibility и temp namespace, чтобы проверить неизменённый selected04.

## Внутренние namespace IDs: APP-10

Одноразовый [install_namespace_registry.sql](sql/install_namespace_registry.sql)
устанавливается владельцем базы/администратором только в выбранную базу, при
отсутствии конкурентного DDL. Подключение и credentials задаются native isql,
в SQL-файле их нет. Пример, пароль передаётся обычными средствами клиента:

```sh
isql -user OWNER -input pg_wire_protocol/1c/sql/install_namespace_registry.sql DATABASE_ALIAS
```

Installer использует SET BAIL ON, SET AUTODDL OFF и один финальный COMMIT.
Первый DDL — CREATE SCHEMA PGWIRE_COMPAT без IF NOT EXISTS. Уже существующая
schema, включая чужую, означает отказ до изменений; повторный запуск не upgrade.
Ошибка до COMMIT откатывает schema и все созданные объекты. Установка не меняет
compat.sql и не выполняется автоматически при подключении.

| SQL-объект | Назначение |
|---|---|
| PGWIRE_COMPAT.NAMESPACE_IDS | NATIVE_NAME VARCHAR(63) UTF8 с binary/case-sensitive UTF8 collation — PK; NAMESPACE_ID BIGINT NOT NULL UNIQUE, CHECK 1…4294967295 |
| PGWIRE_COMPAT.NAMESPACE_SEQ | Одна native sequence, новые IDs; пропуски допустимы, reseed/reuse не выполняются |
| PGWIRE_COMPAT.NAMESPACES | Read-only DISTINCT projection только NATIVE_NAME/NAMESPACE_ID |
| PGWIRE_COMPAT.NAMESPACE_DDL | AFTER ANY DDL STATEMENT, SQL SECURITY DEFINER; фильтр OBJECT_TYPE=SCHEMA, имя из OBJECT_NAME |

Первичное заполнение читает SYSTEM.RDB$SCHEMAS, включая уже существующие schemas,
SYSTEM и саму PGWIRE_COMPAT. Снимается только хвостовое CHAR padding; LOWER/UPPER
не применяется. Native PUBLIC и quoted "public" имеют разные записи/IDs.
Registry хранит identity, остальные свойства схем остаются в native metadata.
Текущий engine ограничивает schema name 56 символами; registry не вводит усечение.

CREATE выдаёт новый ID, ALTER оставляет прежний, DROP удаляет строку. RECREATE
следует фактическим DROP/CREATE hooks; IF NOT EXISTS/IF EXISTS не выделяют ID при
отсутствии реального события. DROP+CREATE одноимённой схемы не переиспользует ID.
Всё выполняется в исходной DDL transaction; ошибка записи registry отменяет DDL.
Native Firebird запрещает DDL при активном пользовательском SAVEPOINT (0A000):
положительный undo schema DDL через ROLLBACK TO не поддерживается. Проверены
сам отказ, ROLLBACK TO recovery и сохранность DDL, сделанного до savepoint.
COMMIT/полный ROLLBACK registry следуют обычной native транзакции.

Installer выдаёт PUBLIC только USAGE на служебную schema и SELECT на view.
Базовая таблица, sequence и управление trigger прикладному пользователю не выданы.
View read-only даже для владельца; обычный schema owner выполняет свой DDL через
definer hook без прямых прав записи в registry. Права CREATE SCHEMA установщик
прикладным пользователям не добавляет — они выдаются администратором отдельно.
Lookup — чистый SELECT, работает в READ ONLY transaction, неизвестное имя даёт
ноль строк и не двигает sequence. Через PgWire NAMESPACE_ID пока обычный BIGINT
(OID 20), а не PostgreSQL oid/regnamespace. ID локален базе, сохраняется между
attachments и после нормального restart сервера; фиксированные PG OID не копируются.

Проверка: `pgwire_1c_smoke.py --namespaces`, native/libpq на одной временной базе,
отдельные install failure/collision базы, конкурентные transactions и restart.
Результаты и SHA — в [VALIDATION](../VALIDATION.md).

Отключённые triggers/-nodbtriggers, ручная порча registry/allocator, удаление
служебной schema и частичный перенос её объектов вне контракта. Пропущенный
DROP+CREATE нельзя восстановить по одному совпадению имён; lookup ничего не
чинит. Upgrade/uninstall/repair не автоматизированы. Backup/restore/migration
не проверены и не заявляются. Ограниченный публичный view добавлен в APP-11 выше.
На дату APP-10 (11 ноября) PG name mapping, regnamespace, search-path visibility
и pg_my_temp_schema ещё не входили в этот срез. Они добавлены последующими
APP-13…16; selected04 принят в APP-16, открытие UI — 13 ноября. Полные
pg_namespace/pg_class и PG oid/name semantics остаются вне профиля.

## Bytea input: ограниченный APP-08

Исторический контракт APP-08 до APP-16. Сейчас InputCodec материализует native
IBlob при Execute, принимает значения больше 65533 и сохраняет прежние budgets.
Предел VARCHAR OCTETS ниже больше не является пределом входного bytea.

InputCodec принимает выведенный engine SQL_BLOB subtype 0, scale 0, ISC_QUAD;
ParameterDescription — OID 17, Parse hint отсутствующий/0/17. Другой hint —
0A000, `SELECT $1` без native типа — прежний HY004. Только transport descriptor
меняется на VARCHAR OCTETS: constructor задаёт capacity 0, portal Bind — реальную
decoded длину каждого BLOB. Native layout пересчитывается через IMetadataBuilder,
с проверкой prefixes, соседних scalar fields, null offsets и общей длины.
Prepared metadata не меняется, scalar-only encode сохраняет прежнюю metadata.
При ошибке encode прежний descriptor остаётся пригоден для прежнего buffer.

Text Bind — уже значение: `\x` в начале включает hex, оба регистра цифр,
space/tab/LF/CR только перед/между/после полных пар. В остальных значениях
обычные bytes сохраняются, `\\` даёт backslash, `\ooo` (0…3/0…7/0…7) — байт.
Нет trim, снятия SQL quotes или зависимости от standard_conforming_strings.
Malformed hex — 22023, malformed escape — 22P02; raw text UTF8/NUL validation
даёт 22021. Binary и уже decoded bytes сохраняют NUL/invalid UTF8. NULL отличается
от empty text/`\x`/empty binary, которые дают ненулевой пустой BLOB.

Предел одного значения — 65533 decoded bytes (native descriptor, не DDL VARCHAR);
65534 — 0A000 на Bind. Лимиты frame/native message/cache остаются 1 MiB/1 MiB/8 MiB;
cache overflow — прежний 54000. Slots не резервируют максимум на каждый BLOB,
сохраняется только итоговый buffer. Никаких plugin BLOB handles, API или новой
transaction state: штатный MOVE создаёт BLOB при Execute в текущей transaction.
Parse/Bind/Describe/Flush/close без Execute не создают transaction/данные/sequence effects.

Приёмка: `pgwire_1c_smoke.py --blob-input`, настоящий libpq и native readback
после commit; обе wire формы до 65533, selected23 UPDATE 1/0 с synthetic 40444 bytes,
68 BLOB из 92 параметров, independent portals/suspension/cache, singleton PROCEDURE,
RETURNING, rollback/savepoint/E/25P02/disconnect. Правила text сверены с отдельным
Postgres Pro 18.6. Это проверка исходной формы SQL на synthetic schema/values;
исходный Bind format не записан, конфигурация 1С не восстановлена и база не открывалась.
На дату APP-08 (11 ноября) большие input и исходные типы оставались отдельными
задачами. Их проверенный профиль добавлен APP-16; настоящие данные перенесены
13 ноября с ограничениями [PREPARE_INFOBASE](PREPARE_INFOBASE.md).

## PG BEGIN через штатную транзакцию Firebird

Клиентский PG dialect 4 принимает только четыре формы без опций:

```sql
BEGIN;
BEGIN WORK;
BEGIN TRANSACTION;
START TRANSACTION;
```

Все возвращают tag BEGIN. Исходный `09-begin.sql` передаётся без изменений.
Существующий SetTransactionNode создаёт READ COMMITTED READ CONSISTENCY,
READ WRITE, WAIT transaction; AUTO COMMIT и дополнительные TPB options не заданы.
Native SET TRANSACTION сохраняет прежние параметры и default SNAPSHOT, в том
числе при вызове через PgWire. ISOLATION LEVEL, READ ONLY/WRITE, DEFERRABLE и
прочие PG options не поддержаны и дают syntax error до начала нового блока.

| Состояние перед PG BEGIN | Результат после успешного prepare |
|---|---|
| I без native handle | Engine execute создаёт transaction; переход в T |
| I с implicit handle | Тот же handle становится explicit, переход в T; прежние записи остаются незавершёнными |
| T | Тот же handle, savepoints, portals и история настроек; tag BEGIN |
| E | 25P02, нужен прежний recovery через rollback |

Один приватный ControlKind::PgStart отличает эту политику от native Start.
Оба сверяются с прежним isc_info_sql_stmt_start_trans после engine prepare.
Общий QueryExecutor::control обслуживает Simple и Extended; SQL rewriting,
новых API/getInfo/AST nodes, состояния attachment или зависимостей нет.
Повтор native SET TRANSACTION остаётся 25001/E. NoticeResponse не добавлен:
повтор BEGIN сохраняет блок без клиентского предупреждения в текущем профиле.

В `INSERT ...; BEGIN` вставка не становится видимой observer до COMMIT;
общий ROLLBACK отменяет и работу перед BEGIN. Аналогично Execute DML → Execute
BEGIN до Sync сохраняет transaction; suspended SELECT portal продолжает чтение
после BEGIN/Sync. Parse/Bind сами explicit блок не открывают, Execute открывает,
Sync оставляет T. Prepared BEGIN переиспользуется после rollback.

История standard_conforming_strings начинается до BEGIN, даже в
`SET standard_conforming_strings=off; BEGIN` без native handle. Promotion и
повтор BEGIN её не сбрасывают; rollback/ошибка/savepoints используют прежний путь.
Разрыв TCP откатывает блок. Native dialects 1/2/3 отвергают новые корневые формы
окончательной token error; AUTOTERM выполняет следующую команду. PSQL BEGIN/END,
DDL START WITH и native SET TRANSACTION сохранены; lexer gate этих токенов не менялся.

Проверены данные через native observer, одна application transaction, её режимы
и ID, чтение свежего commit второго соединения, Simple/Extended, psql AUTOCOMMIT
off и автоматический BEGIN psycopg2. Результаты и ограничения — в
[VALIDATION.md](../VALIDATION.md). Psycopg 3 сценарий обновлён, но этот клиент
в текущем окружении отсутствует и live-повтор не заявляется.

Это ограниченный контракт [BEGIN](https://www.postgresql.org/docs/18/sql-begin.html)
и [transaction flow](https://www.postgresql.org/docs/18/protocol-flow.html#PROTOCOL-FLOW-MULTI-STATEMENT).
Полная эквивалентность конкурентного DML PostgreSQL не заявляется. Произвольные `$n`, custom cast targets,
общий LIMIT/OFFSET, PG transaction options/default_transaction_*, каталоги и запуск 1С
остаются отдельными задачами. Whole-query preparse отложен: ранний COMMIT
сохраняется при поздней syntax error. Прежние native defects не исправлялись.

## Фиксированный профиль SET: английские сообщения и UTF8

Отдельный срез поддерживает запрос `02-lc-messages` из записи и настройку
кодировки из `03-session-batch`:

```sql
SET lc_messages TO 'en_US.UTF-8';
SET client_encoding = 'utf8';
```

Грамматика доступна только в клиентском PG dialect 4. Для обеих команд допустимы
`TO` и `=`, значение — строковый литерал либо идентификатор. Native dialects
1/2/3 этих форм SET не получают; новые слова остаются нерезервированными.
Подготовку и исполнение обслуживает существующий SessionManagementNode,
PgWire только классифицирует команду как SET и передаёт её движку.

| Настройка | Поддержанный контракт |
|---|---|
| `lc_messages` | Только точное значение `en_US.UTF-8`, для любого пользователя. PgWire уже формирует английские клиентские диагностики. Системная locale и native `firebird.msg` не меняются; это не реализация локализации произвольных языков |
| `client_encoding` | `UTF8` и `UTF-8` без учёта регистра, для любого пользователя. Execute дополнительно проверяет действительный charset attachment: при другом charset — 0A000. Смена кодировки подключения не выполняется |

Неподдержанные значения дают 22023. Parse/Bind сами настройки не применяют; значение и charset
проверяются заново при исполнении prepared statement. Значение UTF8 в
ParameterStatus продолжает соответствовать данным на соединении.

**Ограничение совместимости:** по решению пользователя проверка прав
на `lc_messages` пока отсутствует. PostgreSQL-права суперпользователя/SET на
отдельный параметр не воспроизводятся; вернуться к этой модели при расширении
поддержки настроек. Остальные проверки прав Firebird сохраняются.

Execute подтверждает единственный уже действующий режим, не меняя его: изменяемого состояния,
его кэша или журнала отката не добавлено. COMMIT/ROLLBACK/RESET сохраняют English/UTF8.
`SET LOCAL`, `SET SESSION`, `DEFAULT`, SHOW и смена режима здесь не реализованы.
Новые SET внутри PSQL отвергаются с 0A000: штатный wrapper переподготавливает
текст в native dialect, а наследование PG dialect во вложенном SQL — отдельная работа.

Исходный пакет семи SET теперь принимается с исключением для трёх игнорируемых
настроек, описанным ниже. Это не реализация всех запрошенных эффектов.
Результаты и воспроизводимые команды — в [VALIDATION.md](../VALIDATION.md).

## Фиксированный профиль клиентской диагностики

```sql
SET escape_string_warning = off;
SET client_min_messages=error;
```

В PG dialect 4 поддержаны только **off** и **error** соответственно, без учёта
регистра, как bare identifiers или строки. Допустимы `=` и `TO`, например
`SET client_min_messages TO 'ErRoR'`. Обычного пользователя достаточно.
Существующий SetPgSessionNode проверяет полное значение при Execute; Parse/Bind
допускают синтаксически корректное неподдержанное значение. При исполнении
`on`, `notice`, `warning`, `'bad'`, `''` и другие значения дают 22023.
Неподдержанный синтаксис остаётся parser error. Успешный CommandComplete — SET.

Это подтверждение уже действующего ограниченного профиля: PG backslash decoder
не создаёт escape warnings; PgWire передаёт ошибки через ErrorResponse и пока
не имеет канала NoticeResponse. Ошибки не подавляются: malformed Unicode
`'\u12'` при standard_conforming_strings=off даёт 22025, SELECT 1/0 — 22012.
Допустимый `'\q'` возвращает q, `'\n'` — перевод строки без NoticeResponse.
Источники и доставка INFO/NOTICE/WARNING этим этапом не реализованы. Серверный
журнал и native IStatus warnings не перенастраиваются.

Значения PostgreSQL по умолчанию отличаются: escape_string_warning=on,
client_min_messages=NOTICE. PostgreSQL передаёт INFO клиенту независимо от
client_min_messages, даже при error; полного совпадения диагностики не заявляем.
См. [escape_string_warning](https://www.postgresql.org/docs/18/runtime-config-compatible.html#GUC-ESCAPE-STRING-WARNING)
и [client_min_messages](https://www.postgresql.org/docs/18/runtime-config-client.html#GUC-CLIENT-MIN-MESSAGES).

Новых attachment fields, истории, undo, cache invalidation, API/ABI,
ParameterStatus или конфигурации нет. COMMIT/ROLLBACK/ALTER SESSION RESET
сохраняют фиксированный профиль. Неверный SET использует прежний error path:
останавливает Simple Query, откатывает implicit writes, переводит explicit
transaction в E и восстанавливает прежний standard_conforming_strings.
Успешные фиксированные SET строковый режим не меняют. Native dialects 1/2/3
сохраняют имена как unquoted identifiers, отвергают эти SET и продолжают
AUTOTERM после ошибки; PG SET внутри PSQL по-прежнему запрещён.

Эти две команды проверены отдельно и в полном `03-session-batch.sql` с описанным
ниже исключением для трёх настроек. Другие значения fixed SET, SET LOCAL/SESSION/DEFAULT, SHOW,
произвольные startup GUC этим срезом не добавлены. Последовательные `$n`
поддержаны отдельным принятым этапом. Whole-query preparse
по-прежнему отложен; ранний явный COMMIT сохраняется при поздней ошибке.

## Временный приём трёх startup SET без эффекта

По явному решению пользователя от 10 ноября 2026 года PG dialect 4 принимает:

```sql
SET enable_mergejoin = off;
SET cpu_operator_cost = 0.001;
SET lock_timeout = 20000;
```

Это узкое исключение только для трёх имён: их значения **игнорируются**, не
сохраняются и не применяются. Допустимы `=`/`TO` и один scalar: строка,
идентификатор/on/off или штатный числовой литерал, включая знак. Числа разбирает
существующий `u_numeric_constant`; его native ограничения сохраняются. PG-типы,
диапазоны и единицы настройки не проверяются: например, `lock_timeout TO '5s'`
или `cpu_operator_cost = -0.001` тоже успешны и ничего не меняют. Пустой RHS,
выражения и неизвестные имена настроек дают ошибку. Прежний `pg_setting_value`
не расширен: числовой RHS у client_encoding остаётся 42601, неверные fixed values — 22023.

Единственный новый Type существующего SetPgSessionNode получает пустое value
и ничего не делает при Execute. Плагин классифицирует SET и передаёт исходный
SQL движку. В I/T возвращается SET, в E — прежний 25P02; native transaction
сохраняется. Нет новых полей attachment, истории, cache invalidation, API/ABI,
ParameterStatus, SHOW или предупреждений. Native1/2/3 отвергают SET, имена
остаются non-reserved identifiers; прежний PSQL guard даёт 0A000.

Optimizer, оценки стоимости, timeouts, TPB, данные и настройки attachment
не меняются. **lock_timeout=20000 не устанавливает таймаут в 20 секунд**:
действуют обычные правила Firebird. В отличие от fixed off/error, эти SET
не подтверждают запрошенное поведение. Реальные эффекты требуют отдельной работы;
SET OPTIMIZE и statement timeout не используются как приблизительные замены.

Полный неизменённый `03-session-batch.sql` даёт семь SET tags, итог I и
standard_conforming_strings=off. Это подтверждает приём startup batch,
но не семантику всех настроек, открытие базы 1С или тест Гилёва. Результаты
целевого `--settings` и сборки — в [VALIDATION.md](../VALIDATION.md).
SET LOCAL/SESSION/DEFAULT и другие GUC этим исключением не разрешены.

## standard_conforming_strings: редакция 2, 10 ноября 2026

Настоящий SET меняет bool на attachment при Execute, начальное значение — on.
Допустимы `=`/`TO`, идентификаторы и строки `on`/`off` без учёта регистра;
прочие значения дают 22023. Parse/Bind сами SET не исполняют. ALTER SESSION RESET
возвращает on. Пример для интерактивного psql: отправить каждую строку отдельным Query.

```sql
SET standard_conforming_strings = on;
SELECT OCTET_LENGTH('\n');  -- 2
SET standard_conforming_strings = off;
SELECT OCTET_LENGTH('\n');  -- 1
SET TRANSACTION;
SET standard_conforming_strings = on;
ROLLBACK;
SELECT OCTET_LENGTH('\n');  -- 1
```

При off обычные одинарные литералы декодирует существующий engine lexer:
кавычки, backslash, управляющие `b/f/n/r/t/v`, octal (1–3 цифры), hex (1–2),
Unicode `u` (4)/`U` (8) и surrogate pairs. Неизвестный escape оставляет следующий
символ. NUL/неверные UTF8 bytes дают 22021, неверная форма Unicode — 22025,
недопустимый codepoint/surrogate — 42601. Off разрешён только для UTF8 attachment;
иной charset и не-UTF8 introducer обычной строки явно отвергаются с 0A000.
Native Q-strings, hex strings, double-quoted identifiers и codecs не меняются.
Остаются native ограничения длины: 65535 bytes и 16383 UTF8 characters для
строковой константы. Сцепление соседних строк сохраняет Firebird whitespace
правило, а не PostgreSQL newline-only. Escape warnings не создаются; `escape_string_warning` принимает только off
в фиксированном профиле ниже. `backslash_quote` не реализован.

Публичное расширение ограничено `fb_info_standard_conforming_strings = 163`
(4-byte integer 0/1 в существующем attachment getInfo) и
`IStatement::PREPARE_STANDARD_CONFORMING_STRINGS_ON = 0x100` / `OFF = 0x200`.
Без flags PG4 читает attachment; один flag фиксирует только prepare context,
оба дают 22023. Native dialects 1/2/3 не включают PG-разбор даже с OFF.
Методов, версий интерфейсов и vtables не добавлено; CLOOP bindings сгенерированы.
Один разрешённый режим используется parser, cache lookup и cache insertion.

Simple Query фиксирует режим **на всё сообщение**, включая продолжение EXECUTE
BLOCK через AUTOTERM; SET влияет на следующее сообщение. Extended Parse фиксирует
режим prepared statement; повторный prepare при Bind использует его же, даже
после SET. Portals сохраняют независимые cursors; Bind values не являются SQL
и повторно не декодируются. PgWire передаёт исходные SQL bytes без rewriting.
Engine StrMark/transformString сохраняет декодированные литералы в native hex
notation при сохранении DDL/PSQL source, чтобы внутренняя переподготовка
не меняла значение. Для обычного `sql_string` используется `_UTF8 X'...'`;
для bare STRING (DATE/TIME/TIMESTAMP, WHEN SQLSTATE) — `X'...'` без introducer.
Явный `_charset` сохраняется прежним механизмом. Исходные позиции lexer
остаются позициями входного SQL.
PG SET внутри PSQL по-прежнему отвергается существующим guard (0A000).

**Review 9e5b7bd07a выявил ошибку прежней сериализации:** bare STRING получал
недопустимый `_UTF8`, что приводило к 42601 при CTAS или повторном native prepare.
[Исправление формы сохраняемого литерала](STRINGS_SOURCE_FIX_TASK.md) ограничено
приватным StrMark и действием `sql_string: STRING`: контекст отмечается только
в выбранной ветке разбора, до сохранения source. Grammar и публичный API
не расширены. Исправление относится к новой сериализации; ранее ошибочно
сохранённый source автоматически не переписывается. Пересоздавать такие объекты
нужно из исходного SQL. Исторический review и новая приёмка приведены в VALIDATION.md.

История одной настройки принадлежит QueryExecutor: baseline логического блока
и имена/snapshots пользовательских savepoints (не более 1024 активных имён).
Она начинается до SET, даже без native transaction handle. Implicit Simple
сегмент заканчивается на COMMIT/ROLLBACK или конце Query, Extended — на Sync;
Flush и несколько Execute не сбрасывают историю. Explicit transaction живёт до
фактического завершения; COMMIT в E выполняет rollback. При первой ошибке,
включая Parse/Bind/duplicate prepared name, настройка немедленно восстанавливается
к последнему savepoint либо baseline, ещё в E. Повторные ошибки в E внешний
уровень не снимают; native transaction остаётся доступной для recovery.

ROLLBACK TO восстанавливает snapshot и сохраняет целевую точку, удаляя поздние.
Повторное имя заменяет только прежнюю одноимённую точку; RELEASE удаляет целевую
и поздние, RELEASE ONLY — только целевую. Имена следуют MetaString/native правилам
регистра, doubled quotes, UTF8 и концевых пробелов. История изменяется после
успешного native control. Commit сохраняет значение, rollback возвращает baseline;
retaining/reset начинают новую историю. Внутренние PSQL savepoints не учитываются.
Проверены NO AUTO UNDO и EXECUTE STATEMENT внутри внешнего savepoint.

Восстановление использует две фиксированные команды SET через обычные
prepare/execute с NULL transaction, минуя dispatcher. Оно не создаёт application
handle, wire results или повторные записи истории. Readback проверяет результат;
отказ завершает session с FATAL и первичной диагностикой, без ложного
ParameterStatus/ReadyForQuery. ParameterStatus читает действительное состояние
при startup и перед ReadyForQuery на Query/Sync; Flush сам уведомление не выдаёт.
Это соответствует измеренному порядку сообщений эталона Postgres Pro 18.6.

Прямой OO API PG4 поддерживает SET/parser/cache/flags, но его native rollback
**не восстанавливает session bool**. PG lifetime обеспечивает только PgWire;
undo-полей/hooks в jrd_tra/Savepoint нет. Сочетания новой истории с native TPB
AUTO COMMIT и RESET внутри PSQL исключены из подтверждённого профиля: скрытые
границы не описываются одним interface pointer. Native возможности сохранены;
наблюдения и отдельный отказ nested RESET приведены в VALIDATION.
Внутренний SAVEPOINT через EXECUTE STATEMENT также вызывает исходный Debug
assertion на текущей и предыдущей сборках; проверка вложенного SELECT
не подтверждает этот отдельный сценарий. Дефект вынесен за пределы исправления
сериализации и описан в архитектурном review в VALIDATION.

Не реализованы E-literals, dollar quoting, SET LOCAL/SESSION, DEFAULT, SHOW,
RESET parameter, новые startup options, произвольные $n, custom cast targets и общий LIMIT/OFFSET. Whole-query preparse
по-прежнему отложен. Полный startup batch и запуск 1С/Гилёва этим срезом не доказаны.

## Решение от 9 ноября 2026: последовательные скрипты, остановка на ошибке

**Реализовано и проверено 10 ноября 2026 года:** несколько SQL-команд в одном
Simple Query, последовательно, с остановкой
на первой окончательной ошибке, по модели `isql SET BAIL ON`. Предварительную
синтаксическую проверку всего пакета откладываем; к ней можно вернуться позднее.
Это изменение ранее заданной границы: допускается ограниченное лексическое
выделение кандидатов команд в PgWire, а грамматику и семантику каждого кандидата
по-прежнему проверяет engine parser. SQL rewriting не допускается.

Архитектура реализации:

1. Переиспользуются `src/isql/FrontendLexer` и подход `AUTOTERM`. Lexer выделяет
   возможный разделитель вне строк/комментариев; это не отдельный SQL parser.
   Добавлен узкий режим внутренней utility, возвращающий исходную границу сразу
   после разделителя, без trim, сборки нового SQL и чтения комментариев следующей
   команды. Прежнее поведение isql сохранено. Исходник lexer включён в сборку
   PgWire без новой библиотеки или переноса всего isql в сервер.
2. Исходный непрерывный диапазон Query готовится обычным `IAttachment::prepare`
   с `SQL_DIALECT_PG` и `PREPARE_REQUIRE_SEMICOLON`. Только структурный
   `isc_command_end_err2`, без более приоритетной fatal/cancel/permission ошибки,
   означает продолжение до следующего кандидата: например, внутри EXECUTE BLOCK.
   Остальные ошибки окончательные. Последний хвост без `;` готовится без требования
   разделителя; незавершённый SQL в конце сообщения — ошибка, не ожидание нового Query.
   Успешно полученный `IStatement` сразу используется для исполнения.
3. Цикл находится в существующем `QueryExecutor` и переиспользует metadata,
   codecs, cursors, command tags и native controls. Ресурсы команды закрываются
   перед следующей; unnamed objects очищаются один раз на Query. Ошибка немедленно
   завершает цикл, даже если соединение остаётся пригодным для следующего Query.
4. В I используется одна implicit transaction до полного COMMIT/ROLLBACK или конца
   Query. Следующая команда после границы открывает новую transaction по необходимости.
   В T/E сохраняются нынешние правила и единственный текущий handle. Каждая команда
   автоматически не коммитится. Native SET TRANSACTION сохраняет свой контракт;
   PostgreSQL BEGIN добавлен отдельным срезом, описанным выше.
5. Отправляется результат каждой команды и один конечный ReadyForQuery. Финальный
   успешный CommandComplete выдаётся после успешного implicit commit, сохраняя
   нынешнее поведение при ошибке commit. Строки результатов не накапливаются.
   Extended Parse остаётся однокомандным.

Новый публичный API, `ISqlScript`, новые getInfo items, параметры конфигурации и
runtime-зависимости не добавлены. `SET BAIL`/`SET TERM`/`INPUT`
остаются командами утилиты isql, в серверный SQL они не добавляются.

**Принятое отличие от PostgreSQL:** в `INSERT A; COMMIT; SELCT ...` первый INSERT
будет зафиксирован до обнаружения поздней syntax error. PostgreSQL отвергает
такое сообщение до первого исполнения. При ошибке выполнения после COMMIT
зафиксированный префикс сохраняется в обеих моделях. Без промежуточного COMMIT
ошибка откатывает текущую implicit transaction; внутри explicit transaction
остаётся E. Остаток сообщения, включая находящийся там ROLLBACK, не выполняется.

Прочие native session setters меняют attachment напрямую: поздний rollback не отменяет
их изменения. Сохраняется native семантика также для sequences, autonomous
transactions и внешних эффектов; общей отмены таких эффектов не обещаем.
Сам по себе скриптовый executor не реализует эффекты PostgreSQL SET из трассы.
Контракты принятых настроек и три временных исключения описаны выше. Для уже реализованного
`standard_conforming_strings` lexer, engine и statement cache используют
согласованный режим; snapshot Query и история отката описаны выше.

Приёмка — в [TEST_PLAN.md](../TEST_PLAN.md), результаты и команды — в
[VALIDATION.md](../VALIDATION.md). Для существующего build directory после
изменения списка объектов нужно повторить configure, затем make.
Отдельный `;` и пустые команды между разделителями пока остаются syntax errors;
закрытые comments/whitespace в хвосте не создают лишний EmptyQueryResponse.

Для ручной проверки отправить весь скрипт одним Query через `psql -c` либо
`pgwire_client.py -c` (интерактивный psql может отправлять команды отдельно):

```sh
psql 'postgres://USER@127.0.0.1:15432/pgwire_test' \
  -c 'SELECT 1 AS "first"; SELECT 2 AS "second";'
```

`standard_conforming_strings=off` действительно декодирует backslash escapes.
`lock_timeout` нельзя заменить statement timeout: область действия различна.
`SET OPTIMIZE` не эквивалентен `enable_mergejoin` или `cpu_operator_cost`.
По решению пользователя для этих трёх имён реализовано узкое исключение:
успешный SET без изменения поведения. Реализация и границы — в
[IGNORED_STARTUP_SETTINGS_TASK.md](IGNORED_STARTUP_SETTINGS_TASK.md).
Значения не сохраняются; join/cost и ожидание блокировок остаются штатными Firebird.
В частности, `lock_timeout=20000` не устанавливает ограничение в 20 секунд.
Реальная семантика отложена; успешный разбор пакета не доказывает её поддержку.
Успешный SHOW на эталоне не доказывает эффект настройки планировщика/блокировок.

Открытие подготовленной базы настоящим тонким клиентом 1С подтверждено
13 ноября 2026 года; [доказательства](OPEN_INFOBASE.md) включают отрисованный UI.
Сам тест Гилёва не запускался. Его результат нельзя заменить отсутствием parser
errors или успешным открытием. TLS/COPY/pipeline и другие независимые направления
roadmap остаются вне этой приёмки.
