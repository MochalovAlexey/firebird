# Wire-протоколы через плагины Firebird 6

APP-13 принят в `4520f371e9`: в PG4 unquoted имена теперь ASCII lowercase,
quoted имена точные; native 1/2/3 не меняются. Старые объекты требуют quotes,
например `"PUBLIC"."TTT"` и `"SYSTEM"."RDB$DATABASE"`. Для unquoted CREATE/catalog
запросов нужен отдельный [lowercase bootstrap](1c/README.md#app-13-имена-и-установка-lowercase-профиля).
Сохранённый SQL канонизируется движком для native reprepare. Начальный/RESET path:
`"public", "pg_catalog", "SYSTEM"`. Полный PG search_path/visibility/temp остаётся открытым.
APP-14 добавляет [устанавливаемую pg_table_is_visible](1c/README.md#app-14-видимость-постоянных-отношений)
для текущих постоянных tables/views: SQL wrapper и небольшой builtin штатного
resolver/SCL, с проверкой явного пути и отсутствия LTT на Execute. Типы wire20→16;
regnamespace/temp и исходный selected04 этим этапом не закрываются.

12 ноября: P2 APP-14 исправлен в рабочем diff — аргумент helper вычисляется до
чтения и проверки состояния attachment. [APP-15](1c/TEMPORARY_RELATIONS_TASK.md)
остановлен по решению пользователя: native LTT undo теряется при освобождении
большого root savepoint (NAT-06). Blocker передаётся в архитектурный диалог;
PG temporary lifecycle ещё не реализован. Oracle, reproducer, проверки P2 и
границы результата — в начале [VALIDATION](VALIDATION.md).

Что ещё нужно доделать и проверить: [единый BACKLOG](BACKLOG.md).
Порядок этапов — [PLAN](PLAN.md), фактические проверки — [VALIDATION](VALIDATION.md).
Все текущие SQL-объекты совместимости устанавливает [install_compat.py](1c/install_compat.py)
на указанную существующую базу через native isql; [аргументы и порядок](1c/README.md#общая-установка-sql-совместимости).

Цель — подключать PostgreSQL-драйверы, включая libpq, по TCP непосредственно к процессу `firebird`. Запросы выполняет локальный движок Firebird. Родной сетевой протокол сохраняет существующую реализацию и работает независимо от дополнительных протоколов.

Дополнительные протоколы реализуются загружаемыми библиотеками `.so`/`.dll`; первый плагин — `PgWire`. Ядро управляет listeners, соединениями, workers, authentication и локальным attachment. Плагин реализует framing, сообщения, состояние своего протокола и codecs. Архитектура и конфигурация определены в [ARCHITECTURE.md](ARCHITECTURE.md).

```ini
# firebird.conf — PgWire: Linux/Super, IPv4 loopback
WireProtocolPlugins = PgWire
WireProtocolPorts = 15432
```

```ini
# plugins.conf; pgwire_test заранее определён в databases.conf
Plugin = PgWire {
    Module = $(dir_plugins)/PgWire
    RegisterName = PgWire
    Config = PgWireConfig
}
Config = PgWireConfig {
    BindAddress = 127.0.0.1
    DatabaseAliases = pgwire_test
}
```

Списки позиционные, разделитель — запятая. Имя `PgWire` разрешается существующим PluginManager в библиотеку через `plugins.conf`; пути, расширения и registered factory описаны в ARCHITECTURE. По умолчанию оба списка пусты. Native endpoint в эти списки не включается. Ошибка загрузки дополнительного endpoint диагностируется без остановки native. Плагины работают в одном процессе: разделение модулей не защищает сервер от аварии внутри библиотеки.

## Граница SQL

Отдельная работа по [записанному профилю 1С](1c/README.md) начинается с небольших коммитов. Первый срез добавляет клиентский `SQL_DIALECT_PG` (4): PgWire выбирает его при prepare, база остаётся в dialect 3, native 1/2/3 сохраняются. В PG-режиме поддержан SELECT без FROM; `pg_catalog.pg_backend_pid()` устанавливается отдельным PSQL-скриптом и возвращает attachment ID в пределах одной базы. Второй срез — последовательные Simple Query скрипты — реализован и проверен 10 ноября 2026 года, см. [VALIDATION.md](VALIDATION.md). Добавлен фиксированный English/UTF8 профиль `lc_messages` и `client_encoding`; startup SET, PG BEGIN и первый срез numbered inputs описаны в 1c/README; фактический запуск 1С не проверен.

Предыдущая P5-ревизия прошла целевую Linux/Super loopback приёмку: I/T/E, native transaction/session controls и recovery. В P6 сборка, unit tests и host/startup/Simple Query/transaction/lifecycle/native регрессии прошли. **Известное ограничение native baseline: READ COMMITTED READ CONSISTENCY + AUTO COMMIT не входит в подтверждённую совместимость; SNAPSHOT AUTO COMMIT проверен.** Целевой P6/M1 Linux/Super принят с известными native ограничениями. Shutdown во время ON CONNECT остаётся отдельным исключением: native Debug assertion воспроизводится также без wire endpoints. P7 Extended Query прошёл целевую Linux/Super приёмку: 16 групп. P8 codecs прошли целевую Linux/Super приёмку: восемь групп; P9/M2 принят для Linux/Super и совместимого SQL; следующий этап — P10, TLS и cancellation. Результаты и границы покрытия — в [VALIDATION.md](VALIDATION.md). TLS, Windows и другие server modes требуют отдельной реализации и приёмки.

Исходные байты каждой SQL-команды передаются в engine prepare с явной длиной и без изменения текста. Для Simple Query существующий lexer isql выделяет кандидаты границ, а engine prepare подтверждает завершённость команды по модели AUTOTERM. Ограниченный SqlInfo внутри PgWire определяет вид команды, command tag, профиль transaction/session controls и признак tokenless EMPTY; grammar, имена объектов, privileges и типы проверяет движок. Общий wire host SQL не анализирует.

После успешного engine prepare плагин сочетает lexical descriptor с существующими `getType()`, `getFlags()`, input/output metadata и affected records. `FLAG_HAS_CURSOR` выбирает cursor/fetch; output metadata определяет singleton output и codecs. RETURNING не превращает DML в SELECT. В I/T неподдержанный control либо UNKNOWN, включая USING wrapper, получает 0A000 до execution; в E успешно подготовленные команды, кроме разрешённого recovery, получают 25P02. Ошибка самого prepare имеет отдельный приоритет по CODEX_CONTEXT. Новые публичные DSQL getInfo items, AST annotations и parser diagnostics для PgWire не добавляются.

Запрещены SQL rewriting, удаление comments из передаваемого текста, замена `$n` на `?`, подстановка параметров SQL-литералами и эмуляция успешного SET/BEGIN. Совместимость синтаксиса и реальное исполнение определяет только Firebird.

Simple Query поддерживает [последовательные скрипты](1c/README.md): исходные диапазоны выделяются по модели isql/AUTOTERM, каждая команда проходит обычный engine prepare и исполнение, первая окончательная ошибка прекращает остаток сообщения. Предварительная проверка синтаксиса всего Query отложена; ранний COMMIT сохраняется и при поздней syntax error. В I текущий implicit сегмент откатывается при ошибке, в T остаётся E. Extended Parse по-прежнему принимает одну команду. Приёмка от 10 ноября 2026 года — в [VALIDATION.md](VALIDATION.md).

Совместимые SELECT/DML/DDL, RETURNING и EXECUTE BLOCK/PROCEDURE выполняются движком. P5 добавляет native `SET TRANSACTION`, COMMIT/ROLLBACK, RETAIN, savepoints, session SET и RESET. PG4 принимает BEGIN/START TRANSACTION без опций и ограниченные последовательные `$n`; слово `RETAINING` остаётся parser error. Native retaining записывается `COMMIT/ROLLBACK [WORK] RETAIN [SNAPSHOT]`. Кроме описанных срезов 1С, PostgreSQL grammar/functions/catalog compatibility остаётся отдельной работой.

Пример запроса через `PQexec` или psycopg2 с `autocommit=True`, без `with connection`:

```sql
SELECT CAST(40 + 2 AS INTEGER) AS "answer" FROM "SYSTEM"."RDB$DATABASE"
```

Ожидаются значение `42`, имя колонки `answer` и PostgreSQL OID `23`. Текущие text results: SMALLINT/INTEGER/BIGINT, BOOLEAN, CHAR/VARCHAR, NUMERIC/DECIMAL в SHORT/LONG/INT64/INT128, FLOAT/DOUBLE, DATE/TIME/TIMESTAMP без TZ и NULL. OID соответственно 21/23/20, 16, 1042/1043, 1700, 700/701, 1082/1083/1114; table OID/attribute number 0, typmod=-1, alias из engine metadata. CHAR читается через varying fetch metadata с сохранением logical CHAR OID и пробелов. Поддержаны UTF8/NONE/ASCII с проверкой результата на valid UTF8; OCTETS и другие неподдержанные charsets/types получают 0A000 по metadata до исполнения. Input parameters в Simple Query пока получают 0A000.

Один `QueryExecutor` на сессию владеет текущей application transaction. Prepare получает текущий handle либо NULL, если application transaction ещё нет. Обычный SQL из I выполняется в implicit READ WRITE / READ COMMITTED READ CONSISTENCY / WAIT transaction без заданного плагином lock timeout. В Simple Query команды используют один implicit сегмент до полного COMMIT/ROLLBACK или конца Query. Промежуточные CommandComplete не фиксируют сегмент; последний отправляется после успешного implicit commit. Ошибка откатывает текущий implicit сегмент и прекращает остаток Query. В T/E prepare использует текущий handle. Ошибка explicit SQL сохраняет его и переводит сессию в E; успешный `ROLLBACK TO` либо `ROLLBACK RETAIN` восстанавливает T. Полный COMMIT в E реально откатывает работу и выдаёт tag ROLLBACK. ReadyForQuery сообщает I/T/E; полный контракт и приоритет ошибок — в CODEX_CONTEXT. `AUTO COMMIT` и `NO AUTO UNDO` сохраняют native семантику без сканирования опций.

SQL использует штатные engine timeouts: в firebird.conf StatementTimeout задаётся в секундах, ConnectionIdleTimeout — в минутах, 0 означает отсутствие соответствующего engine cap. Плагин не задаёт собственные statement/lock timeouts. Native idle timeout закрывает attachment с rollback; следующий Query получает FATAL 57P05 и закрытие, без повторного подключения. AUTO COMMIT проверяется с SNAPSHOT, NO AUTO UNDO — отдельно. Известное ограничение native baseline: сочетание AUTO COMMIT с READ COMMITTED READ CONSISTENCY вызывает assertion `Savepoint.cpp:833`, воспроизводимый embedded isql без PgWire/server/triggers; этот профиль не входит в подтверждённую совместимость. Подробности — в VALIDATION.

EMPTY определяется ограниченным lexer только для нулевой строки, whitespace и полностью закрытых comments без SQL tokens. Даже такой текст передаётся в обычный engine prepare неизменённым. Только сочетание этого признака с engine syntax error 42601 после успешного cleanup преобразуется в EmptyQueryResponse; permission, cancellation, resource и другие ошибки не маскируются. Незаконченные строка/comment/statement и отдельный `;` остаются parser errors. Native parser diagnostics и `isql -autoterm` не меняются.

P8 добавляет точные text/binary NUMERIC до native INT128 (NUMERIC/DECIMAL до 38 цифр), IEEE FLOAT/DOUBLE и даты/время без TZ. Binary NUMERIC использует base-10000, даты — дни от 2000-01-01, время/timestamp — микросекунды; native bytes не передаются напрямую. Даты ограничены 0001..9999, время имеет точность 100 мкс; непредставимый ввод получает 22008. `integer_datetimes=on`, TimeZone не объявляется. Подробные диапазоны — в [COMPATIBILITY.md](COMPATIBILITY.md).

PG4 также поддерживает `SELECT … [ORDER BY …] LIMIT n`: только integer literal
0…2147483647 со штатными integer spellings. Используется RowsClause и native
исполнение с правильными уровнями UNION/subqueries; LIMIT 0 сохраняет metadata.
Корневые операторы — SELECT/INSERT/UPDATE/DELETE/MERGE; в последних LIMIT допустим
только внутри SELECT. LIMIT reserved только в PG4. ALL/NULL/count-параметры,
выражения/знаки, PG OFFSET и смешение с ROWS/OFFSET…FETCH не добавлены;
FIRST/SKIP+LIMIT на одном уровне получает прежнюю native ошибку.
DDL/PSQL и прочие исключённые roots с настоящим LIMIT дают 0A000 до DSQL.
Точный контракт и проверки — [1c/README](1c/README.md) и [VALIDATION](VALIDATION.md).
SQL-01 закрыт по реализации и оставлен на review; SQL-02 остаётся открытым.

## Extended Query P7

Parse/Bind/Describe/Execute/Close/Flush/Sync прошли целевую Linux/Super приёмку: 16 групп, см. [VALIDATION.md](VALIDATION.md). Прямой libpq может передать `SELECT CAST(? AS INTEGER) FROM RDB$DATABASE` с отдельным Bind value, без изменения SQL. Text/binary input/output поддержаны для integer/BOOLEAN/CHAR/VARCHAR, NUMERIC/DECIMAL и INT128, FLOAT/DOUBLE, DATE/TIME/TIMESTAMP без TZ и NULL; PG4 BEGIN без опций и последовательные уникальные `$n` с типами из SQL поддержаны отдельными срезами. Повтор/gap/reorder и смесь с `?` — 0A000; CALL/BLOCK/DDL/USING с `$n` исключены. Simple typed input — 0A000, недостаточный контекст — HY004 даже с supplied OID. Postfix `::` добавлен в PG4 через прежний CastNode для native builtin types и postfix-only INT8; сохранённый SQL/DDL/PSQL исключены. Точные пределы и синтетическая приёмка — в [1c/README](1c/README.md).

Named statements переживают commit; portals сохраняются через Sync внутри explicit transaction и удаляются при её завершении/ошибке. RESET инвалидирует все statements/portals. Лимиты session — 128 statements, 128 portals, 8 MiB учитываемых сохранённых SQL/metadata/buffers; другие engine allocations ограничиваются числом объектов. Полный контракт — в [CODEX_CONTEXT.md](CODEX_CONTEXT.md#11-extended-query-p7-принят-для-linuxsuper). P9 принял целевой M2: C libpq и ограниченный Psycopg 3 profile, описанный ниже; совместимость конкретного приложения остаётся отдельной задачей.

P7 включает узкое исправление native release_attachment для нескольких prepared requests одного Statement; падение воспроизведено также через native TCP без PgWire. Grammar, public statement metadata и CLOOP не меняются. Отдельный ON CONNECT shutdown assertion !child остаётся неисправленным и в P7 не перепроверялся. P8 codecs прошли целевую Linux/Super приёмку: восемь групп; P9/M2 принят для совместимого SQL; следующий этап — P10.

Binary BLOB subtype 0 теперь выдаётся как bytea (OID 17, size/typmod -1): text
`\x` + lowercase hex либо исходные binary bytes. NULL и пустой BLOB различаются,
NUL/invalid UTF8 в payload допустимы. Simple/Extended, RETURNING и native PSQL
используют текущую transaction и IBlob только на время decode одной строки.
Общий бюджет DataRow учитывает все поля и фактический frameBytes; превышение —
0A000 без частичного DataRow. Другие BLOB subtypes остаются отвергнутыми;
SQL type/cast bytea и SET bytea_output не добавлены. Контракт и синтетическая
selected06-проверка — в [1c/README](1c/README.md), evidence — в [VALIDATION](VALIDATION.md).

APP-08 добавляет bytea input OID 17 для выведенного движком BLOB subtype 0:
raw binary или text hex/escape, до 65533 декодированных байт на параметр.
InputCodec задаёт physical VARCHAR OCTETS по фактической длине Bind;
BLOB создаётся штатным преобразованием движка при Execute в текущей transaction.
NULL и empty различаются; Parse/Bind не создают BLOB/application transaction.
Общие лимиты frame/native message/cache сохранены: 1 MiB / 1 MiB / 8 MiB.
Проверены selected23 на синтетической PUBLIC.FILES и 68 BLOB среди 92 параметров;
большие входные BLOB остаются APP-09, SQL bytea grammar и открытие 1С не приняты.


## Внутренние namespace IDs

[APP-10](1c/NAMESPACE_REGISTRY_TASK.md) добавляет необязательный одноразовый
[SQL installer](1c/sql/install_namespace_registry.sql) для выбранной базы.
PGWIRE_COMPAT.NAMESPACES выдаёт точное native имя и устойчивый локальный BIGINT ID
в диапазоне 1…4294967295. Одна sequence и definer DDL trigger поддерживают mapping
при CREATE/DROP; ALTER сохраняет ID. PUBLIC/quoted "public" различаются.
Приложению выданы только USAGE schema/SELECT view; lookup ничего не создаёт.

Повтор установки — явная ошибка без изменений, установка выполняется без
конкурентного DDL. Проверены native/libpq, rollback, права, concurrency и restart;
native запрет DDL под пользовательским savepoint (0A000) сохранён и описан в
[контракте](1c/README.md#внутренние-namespace-ids-app-10).
C++/parser/API не менялись. APP-10 принят в `5bbdaa763d`; открытие 1С и Гилёв
этим не подтверждаются.

[APP-11](1c/NAMESPACE_CATALOG_TASK.md) добавляет отдельный одноразовый
[installer публичного view](1c/sql/install_namespace_catalog.sql):
PG_CATALOG.PG_NAMESPACE(OID, NSPNAME) читает те же IDs и точные native имена.
DISTINCT запрещает DML даже владельцу; PUBLIC получает только USAGE PG_CATALOG
и SELECT view. Требуются установленный APP-10 с активным hook и отсутствие
конкурентного DDL. Существующие PG_CATALOG/PG_BACKEND_PID сохраняются;
существующий PG_NAMESPACE означает отказ, ошибка до COMMIT — полный rollback.

Metadata остаётся BIGINT/VARCHAR (wire20/1043), binary ID — 8 bytes, labels —
OID/NSPNAME. Все registry rows доступны независимо от USAGE их схем.
PG oid/name/regnamespace, PG/native name resolution, nspowner/nspacl,
полный pg_class/visibility/temp namespace и selected04 остаются открытыми.
APP-11 прошёл review и независимую приёмку в `a3e3c80123`;
[установка и проверки](1c/README.md#публичное-представление-namespace-ids-app-11).

## Постоянные таблицы и views в PG_CLASS

[APP-12](1c/CLASS_CATALOG_TASK.md) прошёл review и независимую приёмку в `13f64d0e00`:
[отдельный SQL installer](1c/sql/install_class_catalog.sql) создаёт read-only
PG_CATALOG.PG_CLASS(OID, RELNAME, RELNAMESPACE, RELKIND) над RDB$RELATIONS и
PG_NAMESPACE. OID = native relation ID + 1, RELNAME — точное native имя,
RELNAMESPACE — прежний namespace ID, kind `r`/`v`; wire types 20/1043/20/1043.
Новых allocator/registry/hook и изменений C++/API нет.

Принят [APP-13: PG-правила имён](1c/PG_IDENTIFIER_NAMES_TASK.md).
Пользователь согласовал lowercase unquoted lookup только в PG4; этот переход
проверен независимо через libpq/native. В один этап входят parser/source, lowercase bootstrap,
session path/RESET/savepoints и сквозной DDL/DML. Родные подключения сохраняются.

Включены только schema-level persistent tables (type 0/legacy NULL) и views (1),
в том числе SYSTEM и служебные. External/virtual/GTT/local/declared/package temporary
tables, indexes/sequences исключены. Нет фильтра по SELECT/USAGE/search_path.
IDs стабильны при ALTER, но native DROP/wrap допускает reuse; restore не проверен.
Installer требует APP-10/11, сохраняет bootstrap и отказывается при существующем
PG_CLASS. Целевые native/libpq проверки прошли; [контракт и установка](1c/README.md#постоянные-таблицы-и-представления-в-pg_class-app-12).
PG/native names/regnamespace, visibility/temp namespace, полный catalog и
selected04/10/11 остаются открытыми; APP-01/03/04/06 этим срезом не закрыты.

## Клиентская приёмка P9/M2

Целевой Linux/Super профиль прошёл 17 групп libpq/raw/Psycopg 3 и регрессии; [evidence](VALIDATION.md). Проверены настоящий nonblocking libpq 14.24 и Psycopg 3.3.6 с libpq 18.6. Psycopg 3 поддерживает совместимый SQL без parameters при `autocommit=True`, Simple/forced prepared и text/binary results, включая AsyncCursor. Для native transaction controls подтверждён отдельный профиль `prepare_threshold=None`.

Автоматический BEGIN и последовательные типизированные `%s` → `$n` теперь ожидаются успешными; новый live Psycopg 3 повтор пропущен из-за отсутствия клиента. Date literal с `::date` теперь ожидается успешным; DEALLOCATE ALL остаётся parser error. Последний Psycopg может отправить при очистке prepared cache **после успешного ROLLBACK**: ошибка драйверного вызова тогда не означает, что rollback не произошёл. Полный контракт и границы других cache invalidations — в [COMPATIBILITY](COMPATIBILITY.md).

Запуск клиентской приёмки использует [existing Extended harness](tools/pgwire_extended_query_smoke.py) с дополнительным `--psycopg3-python`; он создаёт собственные runtime/security/database в новом каталоге. Команда и установка тестовых зависимостей — в [MANUAL_TESTING](MANUAL_TESTING.md#клиентская-приёмка-p9).


## Ручная работа с SQL

[MANUAL_TESTING.md](MANUAL_TESTING.md) описывает настройку endpoint и [pgwire_client.py](tools/pgwire_client.py) — консольный клиент на Python 3 и настоящей libpq, с интерактивным вводом, `-c`, UTF8-файлами и pipe. SQL отправляется целиком; клиент показывает результаты, OID, NULL, SQLSTATE и transaction state. Инструкция включает два терминала для проверки видимости изменений через PgWire и native isql. CRUD, явные транзакции и session controls прошли целевые проверки с указанным выше native ограничением.

Параметры подключения: `-d ALIAS -U USER -P PASSWORD` или длинные `--database`, `--user`, `--password`. Пароль задаётся аргументом либо через `PGPASSWORD`; скрытого запроса ввода нет. Текущий сервер принимает только разрешённые alias из `databases.conf`, прямой путь к файлу БД пока не поддерживается.

## Этапы

| Результат | Проверяемое содержание |
|---|---|
| M0 | Загружаемый PgWire, реальная authentication, вычисляемый SELECT через libpq и psycopg2 с `autocommit=True`, recovery, cleanup и параллельный native |
| M1 | Simple Query с совместимым SQL, базовыми codecs, RETURNING, plugin-local command tags при engine validation и транзакционным состоянием |
| M2 | Extended Query, prepared statements/portals, text/binary codecs; параметры в поддержанной движком форме `?` через прямой libpq |
| M3 | Удалённый TCP, TLS, cancellation и проверенный профиль приложения с совместимым SQL |
| Будущее расширение движка | BEGIN options, произвольные `$n`, другие grammar/functions по отдельной задаче |

Результаты M0 и прежней P4-ревизии сохранены в VALIDATION. Новый путь использует ограниченный plugin-local lexer и существующие statement interfaces; изменения engine AST/getInfo/EMPTY diagnostics удалены. Его integration suite включает проверки классификации через настоящий TCP и не зависит от отдельного native metadata executable.

Правило одной текущей application transaction относится только к PgWire-сессии. Родные attachments и generic host сохраняют возможности Firebird; Parse/Describe и portals не создают отдельные application transactions. Последовательных транзакций на одном подключении может быть сколько угодно.

Профиль приложения изучается в P0; его выбор и трасса обязательны для M3, но не блокируют серверный M1. Поддержка wire-протокола сама по себе не делает совместимыми SQL и обращения к каталогам, которые генерирует драйвер или ORM. Эти зависимости фиксируются явно. Текущий host включается только в Linux/Super с IPv4 loopback; SuperClassic/Classic диагностируются с сохранением native. Windows ABI учитывается сразу, Windows build/runtime и другие режимы требуют собственной реализации и приёмки.

## Документы и состояние

База — master Firebird 6, рабочая ветка pg_wire_protocol; SHA и исходники — [BASELINE.md](BASELINE.md). Принятый P6/M1 ограничен Linux/Super loopback; полная переносимость, auth-plugin и close/free fault injection не заявляются. Результаты — в [VALIDATION.md](VALIDATION.md).

Host читает настройки через существующий `getPluginConfig(name)` до bind/load со штатным named Config / module `.conf` fallback; NULL означает ошибку endpoint. Конфигурация подготавливается до запуска; named Config рекомендуется для разных aliases. Начальные limits: Workers 1–16 (default 4), QueueSize 1–64 (default 16); всего до 16 endpoints и 64 workers. BindAddress допускает только `127.0.0.1`, Port/TLS в host settings отвергаются. Проверка native-конфликта использует фактический bound socket, включая `-p`. После provider shutdown workers получают общий deadline 5 секунд. Полный контракт — [ARCHITECTURE.md](ARCHITECTURE.md), решения — [IMPLEMENTATION_NOTES.md](IMPLEMENTATION_NOTES.md), ABI — [UsingWireProtocolPlugins.md](../doc/UsingWireProtocolPlugins.md).

`DatabaseAliases` — comma-separated allowlist локальных aliases для endpoint; пустое значение запрещает все attachments. P2 использует Srp256 только при разрешении AuthClient/AuthServer базы, сохраняет RemoteAccess и настоящую identity. Локальная security DB должна разрешать Engine14 в Providers. Startup принимает user, database (по умолчанию user), application_name и UTF8; остальные параметры явно отклоняются. SSL/GSS получают `N`, BackendKeyData не выдаётся. Сетевой ввод-вывод ожидает данные, EOF или shutdown без transport/frame/startup/auth timeout. Очередь ограничена числом соединений; истечения по времени нет. Ограничения и проверяемый startup profile описаны в COMPATIBILITY.

Тестовая библиотека реализует различимый byte echo через два aliases; она не выполняет SQL и не понимает PostgreSQL. `wire_host_smoke.py` проверяет её TCP listeners рядом с настоящим native SQL на изолированной копии runtime. Наличие такого harness не является результатом M0.

Историческая P5-приёмка до удаления wire-таймаутов: full build и четыре unit executable, включая 487 SQL-classifier checks; 14 групп [transaction harness](tools/pgwire_transaction_smoke.py) без skips, 12 Simple Query, 7 lifecycle, 52 startup и 9 native regression checks. Проверены SNAPSHOT AUTO COMMIT, NO AUTO UNDO, настоящий 100 ms DML timeout в I/T, commit/reset failures и C libpq transaction status. C libpq headers/runtime — 14.24, psycopg2 2.9.11 — libpq 17.6. Trace не обнаружил backend network connects; финальные transaction/trace серверы завершились с exit 0. READ CONSISTENCY + AUTO COMMIT остаётся известным native ограничением; целевой M1 позднее принят в P6 с указанными исключениями. SHA, команды и evidence — в [VALIDATION.md](VALIDATION.md).

| Файл | Назначение |
|---|---|
| [BASELINE.md](BASELINE.md) | База и проверенные точки интеграции |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Единый контракт plugin host, ABI, конфигурации и lifecycle |
| [CODEX_CONTEXT.md](CODEX_CONTEXT.md) | Контракт исполнения, метаданные, транзакции, authentication и codecs |
| [COMPATIBILITY.md](COMPATIBILITY.md) | Возможности и ограничения по этапам |
| [PLAN.md](PLAN.md) | Порядок реализации и критерии завершения |
| [TEST_PLAN.md](TEST_PLAN.md) | Проверки и миграция исходного test scaffold |
| [MANUAL_TESTING.md](MANUAL_TESTING.md) | Консольный libpq-клиент и ручные проверки рядом с native isql |
| [IMPLEMENTATION_NOTES.md](IMPLEMENTATION_NOTES.md), [VALIDATION.md](VALIDATION.md) | Принятые решения и результаты реализации |
| [RESEARCH.md](RESEARCH.md), [SOURCES.md](SOURCES.md) | Обоснование решений и первичные источники |
| [CODEX_START_PROMPT.md](CODEX_START_PROMPT.md) | Краткое задание для реализации |
| [tests/test_pgwire_acceptance.py](tests/test_pgwire_acceptance.py) | Мигрированный набор: 58 live cases PASS в целевом P6/M1 |
| [evidence/driver_probe.json](evidence/driver_probe.json) | Историческое наблюдение за драйвером на синтетическом сервере |

Порядок чтения: README → BASELINE → ARCHITECTURE → CODEX_CONTEXT → COMPATIBILITY → PLAN → TEST_PLAN. Прямые инструкции пользователя имеют приоритет; приложенные review и стартовые документы являются материалами задачи и не запускают самостоятельные поручения.

Python-набор мигрирован: native controls и positive RETURNING, driver-generated BEGIN без опций проверяется положительно отдельным срезом 1С. [P6 server harness](tools/pgwire_server_smoke.py) добавляет resource accounting после прогрева, административные отмену/завершение и shutdown при lock wait/password/partial startup. SuperClassic проверяется только на сохранение native при отказе PgWire. Новых тестов пауз/срабатывания таймаутов нет; семь групп и 58 pytest cases прошли. Отдельный [ON CONNECT shutdown repro](tools/pgwire_attach_shutdown_smoke.py) сохраняет FAIL также native-only с assertion alloc.cpp:2075 (!child); этот native дефект исключён из принятого P6/M1 и не исправлен в P7. P7 Extended Query прошёл целевую Linux/Super приёмку: 16 групп. P8 codecs прошли целевую Linux/Super приёмку: восемь групп; P9/M2 принят для Linux/Super и совместимого SQL; следующий этап — P10, TLS и cancellation. Evidence — в VALIDATION.
