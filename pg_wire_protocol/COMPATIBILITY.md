# Матрица совместимости PostgreSQL wire plugin

Открытые задачи и отложенные части этого контракта собраны в [BACKLOG.md](BACKLOG.md).

Матрица M0–M3 задаёт границы этапов. База — master Firebird 6, SQL dialect 3, UTF8, wire 3.0. Архитектура — [ARCHITECTURE.md](ARCHITECTURE.md), execution contract — [CODEX_CONTEXT.md](CODEX_CONTEXT.md). Предыдущая P5-ревизия прошла целевую Linux/Super loopback приёмку: I/T/E, native transaction/session controls и recovery. В P6 сборка, unit tests и host/startup/Simple Query/transaction/lifecycle/native регрессии прошли. **Известное ограничение native baseline: READ COMMITTED READ CONSISTENCY + AUTO COMMIT не входит в подтверждённую совместимость; SNAPSHOT AUTO COMMIT проверен.** Целевой P6/M1 Linux/Super принят с известными native ограничениями. Shutdown во время ON CONNECT остаётся отдельным исключением: native Debug assertion воспроизводится также без wire endpoints. P7 Extended Query прошёл целевую Linux/Super приёмку: 16 групп. P8 codecs прошли целевую Linux/Super приёмку: восемь групп. P9 прошёл 17 групп с реальными C libpq, raw-wire и ограниченным профилем Psycopg 3; целевой M2 Linux/Super для совместимого SQL и реализованных codecs принят. Следующий этап — P10. Результаты и границы покрытия — в [VALIDATION.md](VALIDATION.md). TLS, Windows и другие server modes требуют отдельной реализации и приёмки.

## Срезы записанного профиля 1С

PgWire использует клиентский `SQL_DIALECT_PG` (4), сохраняя dialect 3 базы и
семантику типов. Только PG-режим принимает SELECT без FROM; native 1/2/3
сохраняются. Установочный PSQL-скрипт добавляет `pg_catalog.pg_backend_pid()`:
int4 attachment ID, стабильный и различный у подключений к одной базе, с
проверяемым переполнением. Это не OS PID, не cluster-wide identity и не
CancelRequest key. Имя колонки остаётся Firebird `PG_BACKEND_PID`.

Контракт, установка и оставшиеся этапы — в [1c/README.md](1c/README.md).
Последовательные Simple Query скрипты реализованы и проверены 10 ноября 2026 года;
результаты и отличие от полного PostgreSQL preparse — в [VALIDATION.md](VALIDATION.md).
Поддержан фиксированный English/UTF8 профиль SET `lc_messages` (для любого пользователя)
и `client_encoding` с проверками при Execute, без смены режима. Другие значения —
22023, не-UTF8 attachment — 0A000. Проверка PostgreSQL-прав на `lc_messages`
отложена по решению пользователя; точные ограничения — в 1c/README.md.
Для enable_mergejoin/cpu_operator_cost/lock_timeout реализован разрешённый пользователем временный
успешный SET без эффекта: значения не сохраняются, planner и ожидания блокировок
не меняются; lock_timeout=20000 не устанавливает 20 секунд. Только PG4,
`=`/`TO` и scalar string/identifier/on/off/native number со знаком; PG-типы,
диапазоны и единицы не проверяются. В I/T — SET, в E — 25P02. Нет новых
ParameterStatus, SHOW, состояния или истории; native1/2/3 и PSQL guard сохранены.
Исходный пакет 03 даёт семь SET tags, I и standard_conforming_strings=off.
Это [узкое исключение](1c/IGNORED_STARTUP_SETTINGS_TASK.md) только для трёх имён.
Реальная семантика этих настроек,
PG-параметры/касты, каталоги и неподдержанная нагрузка остаются отдельной работой.
Полная совместимость и фактический запуск 1С не заявляются.

## PG BEGIN без transaction options

Поддержаны BEGIN / BEGIN WORK / BEGIN TRANSACTION / START TRANSACTION только в PG4.
Штатный SetTransactionNode задаёт READ COMMITTED READ CONSISTENCY, READ WRITE, WAIT;
native SET TRANSACTION без опций сохраняет SNAPSHOT. Tag BEGIN, состояние T.
При implicit handle BEGIN сохраняет его и предшествующую работу; повтор в T
сохраняет handle/savepoints/portals/строковую историю. В E — 25P02; native повтор
SET TRANSACTION — прежний 25001. Parse/Bind блок не открывают; Execute открывает,
Sync оставляет T. Неподдержанные options — syntax error, не частичное исполнение.
Native1/2/3/AUTOTERM, PSQL и DDL START WITH сохранены. Нет нового API или SQL rewriting.
Проверены libpq, raw-wire, native visibility/monitoring, psql AUTOCOMMIT off,
psycopg2 transactional mode и connection context. Контракт и границы — в
[1c/README.md](1c/README.md); результаты — в [VALIDATION.md](VALIDATION.md).
Это не полная PG transaction compatibility или запуск 1С; остальные options,
default_transaction_*, произвольные `$n`, custom cast targets, общий LIMIT/OFFSET и каталоги остаются за границей.

## Literal LIMIT в PG4

`SELECT … [ORDER BY …] LIMIT n` поддержан для integer literal 0…2147483647
через штатный NUMBER32BIT (включая 0x/0o/0b, `_` и ведущие нули). RowsClause
ограничивает готовый результат своего SELECT уровня: DISTINCT/GROUP/ORDER,
весь внешний UNION либо отдельный operand/subquery в скобках. Native UNION
сортируется по позиции колонки. LIMIT 0 сохраняет metadata и tag `SELECT 0`.
Extended maxRows/suspension не сбрасывает SQL count; portals независимы.

Count ALL/NULL/параметры/выражения/скобки/знаки/decimal/exponent, PG OFFSET,
`LIMIT offset,count` и выход за диапазон — parser error. FIRST/SKIP+LIMIT
на одном уровне — прежняя native ошибка, на разных — разрешены. ROWS и
OFFSET…FETCH отдельно работают, смешение с LIMIT на одном уровне не добавлено.
LIMIT reserved только в PG4; native1/2/3 сохраняют имя LIMIT, quoted `"LIMIT"`
остаётся идентификатором в dialect3/4.

Разрешены только корневые SELECT/INSERT/UPDATE/DELETE/MERGE с SELECT subqueries/CTE;
UPDATE/DELETE suffix LIMIT отсутствует. Исключённые roots (DDL/CTAS/VIEW/PSQL,
BLOCK/CALL/PROCEDURE/USING/UPDATE OR INSERT) с распознанным LIMIT — 0A000 до DSQL.
Неверная грамматика — syntax error; текст LIMIT в strings/comments/quoted identifiers
guard не включает. API, plugin, codecs, optimizer и native stream не менялись.
Точный контракт — [1c/README](1c/README.md), проверки — [VALIDATION](VALIDATION.md).
SQL-01 закрыт по реализации; SQL-02, полные selected 04/17 и запуск 1С остаются открыты.

## Postfix casts в PG4

`expression::type` использует существующий CastNode с native преобразованиями,
ошибками и input inference. Тип — штатный `non_array_type`; только INT8 добавлен
как alias BIGINT, только в postfix target, без резервирования имени. Обычный CAST
и домен INT8 сохраняются. Домены/TYPE OF/schema-qualified/arrays справа от `::`,
INT2/INT4/FLOAT4/FLOAT8/BOOL и custom bytea/mvarchar/regnamespace не добавлены.

Оператор непрерывный, левоассоциативный и выше unary/арифметики/concat/COLLATE/AT.
Разрешены корневые SELECT/INSERT/UPDATE/DELETE/MERGE с CTE/subqueries;
DDL/CTAS/PSQL/BLOCK/CALL/PROCEDURE/USING/UPDATE OR INSERT с настоящим `::` —
0A000 до DSQL, неправильная грамматика — syntax error. Literal `::` допустим.
Native1/2/3, `:variable`, source offsets/cache и прежний CAST сохранены.

Wire API/codecs/Parse OID contract не менялись; цепочка `$1::INTEGER::VARCHAR(12)`
имеет input 23/output 1043. Синтаксис остальных native типов сохраняет прежние
codec limits, а преобразования не заявляются как полная PG coercion semantics.
Подробности — [1c/README](1c/README.md), evidence — [VALIDATION](VALIDATION.md).
Каталоги, исходные запросы 1С и открытие базы этим не подтверждены.

## Последовательные `$1` … `$n` в PG4

Разрешены только уникальные номера по порядку исходного SQL в корневых
SELECT/INSERT/UPDATE/DELETE/MERGE, с обычными CTE/subqueries. Типы выводятся
из колонки/DML/native CAST. Повтор, gap, reorder и смесь с `?` — 0A000;
`$0` — 42P02, номер >32767 — 54000, identifier suffix — 42601; `$01` означает 1.
CALL/EXECUTE PROCEDURE/BLOCK, USING, DDL/PSQL и UPDATE OR INSERT с настоящим `$n`
отклоняются до DSQL transformations. Native `?`, dollar identifiers и текст `$n`
в literals/comments не меняются; native dialects 1/2/3 `$n` не принимают.

Extended использует прежние engine metadata и codecs без rewriting/нового API.
Simple с типизированным `$1` — 0A000. OID hints не выводят тип: `SELECT $1`
остаётся HY004 даже с OID=23; incompatible OID — 0A000, excess OIDs — 08P01.
Значения Bind text/binary, NULL/empty/UTF8, named reuse, portals, CTE, MERGE,
RETURNING и синтетическая форма 92 параметров проверены с native observer.
Предел номера не гарантирует максимально большое engine message. Полный
контракт — [1c/README](1c/README.md), evidence — [VALIDATION](VALIDATION.md).
Оригинальная схема/типы 1С и открытие базы не проверены; Psycopg 3 отсутствует.

## Фиксированная клиентская диагностика PG4

`escape_string_warning` принимает только off, `client_min_messages` — только
error, без учёта регистра, quoted/bare и `=`/`TO`. Проверка при Execute даёт
22023 для других значений; Parse/Bind не применяют настройку. Успех подтверждает
нынешнее отсутствие escape warnings и NoticeResponse, ошибки остаются видимыми.
Новых состояния, истории, API, ParameterStatus и warning transport нет.
Native warnings и серверный журнал не меняются. Ошибка SET использует прежний
I/T/E и rollback standard_conforming_strings; RESET не меняет фиксированный профиль.

Это ограниченный профиль, не полный PostgreSQL GUC: defaults PostgreSQL — on/NOTICE,
а INFO в PostgreSQL отправляется даже при client_min_messages=error. Источники
и доставка INFO/NOTICE/WARNING здесь отсутствуют. Контракт и официальные источники —
в [1c/README.md](1c/README.md). Успех полного startup batch использует описанное
выше исключение и не подтверждает все запрошенные эффекты или запуск 1С.

## Изменяемый режим строк PG4

`standard_conforming_strings` поддерживает on/off, реальные decoded bytes,
prepare/cache isolation, Simple message snapshot и Extended Parse snapshot.
PgWire восстанавливает bool при ошибке уже в E и при rollback/savepoints,
публикует фактический ParameterStatus. История одной настройки допускает native
repeated names/RELEASE ONLY; undo данных остаётся engine-owned.
Attachment getInfo + два prepare flags — единственное расширение публичного
контракта, без новых методов. Прямой OO API не откатывает session bool автоматически.

Off требует UTF8 attachment и UTF8 introducer обычных строк; Q-strings и native
1/2/3 сохраняются. Длины и сцепление литералов остаются Firebird; E/dollar strings,
SET LOCAL/SESSION/DEFAULT/SHOW/RESET parameter и новые startup keys не добавлены.
Новая история совместно с TPB AUTO COMMIT либо nested RESET не заявляется;
старый positive SNAPSHOT AUTO COMMIT baseline относится к native данным,
не к этому новому контракту. Полные ограничения — в [1c/README.md](1c/README.md).

## Startup/authentication: Linux/Super P2 PASS

Отдельный PgWire module добавлен в production POSIX/CMake build definitions. На Linux/Super IPv4 loopback он реализует protocol 3.0 Startup, SSL/GSS refusal N, cleartext Password exchange с настоящим Firebird Srp256 и локальный engine attachment. AuthClient/AuthServer policy, RemoteAccess и точный DatabaseAliases allowlist сохраняются; локальная security DB требует Engine14 в Providers; пустой allowlist запрещает attachments. Host attachment borrowed, plugin закрывает его через host detach. Identity и peer проверены native observer: MON$REMOTE_PROTOCOL=TCPv4, MON$REMOTE_VERSION=configured endpoint alias.

User обязателен, database по умолчанию равен user; явно пустой database отвергается. Допустимы application_name и отсутствующий/точный UTF8 client_encoding; иные startup keys/options/replication явно отклоняются. Startup ограничен 16 KiB, typed frame 1 MiB, Password body 4096 bytes с NUL. Собственных transport/frame/startup/auth/output timers нет. Ввод-вывод ждёт progress, EOF или shutdown; queued sockets ограничены числом без истечения по времени.

AuthenticationOk отправляется после attach, затем ParameterStatus и ReadyForQuery I. Version token — `16.0 (Firebird pgwire prototype)`, DateStyle — `ISO, MDY`, с P8 `integer_datetimes=on`; TimeZone не публикуется. Исторический P2 отвечал одинаковым 0A000 на все корректные Query; P3 заменяет это поведение engine execution, описанным ниже. TLS и BackendKeyData/cancel ещё отсутствуют; P7 Extended Query прошёл целевую Linux/Super приёмку. P2 PASS относится к записанным startup/authentication checks, не к полной матрице M0–M3.

## Simple Query и P5: историческая приёмка и текущие регрессии

Совместимые SELECT/DML/DDL, RETURNING и EXECUTE BLOCK/PROCEDURE выполняются движком. Используются существующие getType/getFlags, input/output metadata, affected records и plugin-local lexical descriptor. Дополнительные public statement info items и EMPTY diagnostics удалены из движка. Предыдущая P5-ревизия принята в целевом Linux/Super loopback profile: 14 transaction groups без skips, full build и четыре unit executable (487 classifier checks), повторные 12 Simple Query, 7 lifecycle, 52 startup и 9 native checks. C libpq 14.24 и psycopg2 2.9.11/libpq 17.6 проверены отдельно; backend connects — 0. SNAPSHOT AUTO COMMIT прошёл, проблемный native READ CONSISTENCY + AUTO COMMIT исключён из подтверждённого profile. Целевой M1 позднее принят в P6 с указанными исключениями; evidence — в [VALIDATION.md](VALIDATION.md).

Текущий Simple Query использует text output из набора P8 ниже; поддержка M1 типов сохраняется. Формат 0, typmod-1, table OID/attribute 0; logical CHAR padding сохраняется через varying fetch metadata. Действительно неподдержанные types/charsets и любые input parameters в Simple Query получают 0A000 по metadata до execution.

`QueryExecutor` живёт всю session и выдаёт ReadyForQuery I/T/E. Prepare использует текущий handle либо NULL, если application transaction ещё нет. Обычный SQL из I получает implicit READ WRITE / READ COMMITTED READ CONSISTENCY / WAIT transaction без заданного плагином lock timeout. В Simple Query implicit сегмент продолжается до полного COMMIT/ROLLBACK или конца Query; промежуточные CommandComplete не означают commit, последний ждёт успешного implicit commit. В T/E prepare использует current native handle. DML tag отражает affected records, включая RETURNING; EXECUTE BLOCK/PROCEDURE не добавляют count. Recoverable explicit error сохраняет handle и E, implicit error откатывается в I; uncertain commit/cleanup закрывает connection. EMPTY сохраняет прежнее I/T/E.

Statement-level timeout остаётся 0: native configuration/session settings управляют SQL execution, плагин не задаёт собственные statement/lock timeouts. Firebird.conf StatementTimeout измеряется в секундах, ConnectionIdleTimeout — в минутах; 0 отключает соответствующий engine cap. Native idle shutdown закрывает attachment с rollback; следующий Query получает 57P05/close, без повторного подключения. Native DML timer не является hard deadline prepare/DDL/controls.

Parser error —42601, unknown relation —42P01, permission —42501, unique —23505, FK —23503, CHECK —23514; общий native NOT NULL/domain status остаётся 23000 без угадывания subclass по message.

## Extended Query P7: целевая Linux/Super приёмка пройдена

Реализованы Parse/Bind/Describe/Execute/Close/Flush/Sync, named/unnamed objects, maxRows/PortalSuspended и discard-until-Sync. Параметры передаются через native metadata в исходный SQL с `?`; PostgreSQL BEGIN/$n не переписываются. В P7 проверены прямой C libpq и raw-wire; Psycopg 3 тогда не проверялся. Ограниченный проверенный профиль P9 описан ниже. P9 принимает M2 для совместимого SQL и конкретного клиентского профиля ниже; произвольное PostgreSQL приложение этим не покрывается.

В принятом P7 input/output включал text SMALLINT/INTEGER/BIGINT, BOOLEAN, CHAR/VARCHAR и NUMERIC/DECIMAL до INT64, NULL; binary integer/BOOLEAN/строки. Текущий P8 расширяет этот набор, как описано ниже. Для Parse hints любого нестрокового типа OID должен точно совпадать с inferred; строковые 25/1042/1043 совместимы. 0/отсутствующий hint — inference; несовместимый hint — 0A000. Text numeric кодируется точно, без float и потери дробных цифр; непредставимое значение — 22003.

Named statements переживают commit; portals живут в transaction, сохраняются через Sync в T и удаляются при завершении transaction или Extended error. Close statement и unnamed Parse replacement сохраняют ранее связанные portals; Simple Query удаляет unnamed statement и portal. RESET удаляет все statements/portals даже при том же native transaction pointer. Bind готовит отдельный native statement того же SQL на portal, без своей application transaction. Лимиты: 128 statements, 128 portals, 8 MiB учитываемых SQL/metadata/buffers; прочие engine allocations ограничены количеством объектов. SET TRANSACTION при открытом implicit cycle получает 25001; ошибка откатывает implicit cycle до Sync. Подробный state/ownership contract — в CODEX_CONTEXT, результаты — в VALIDATION.

P7 включает native cleanup fix нескольких requests одного Statement в release_attachment (jrd.cpp), подтверждённый воспроизведением через native TCP без PgWire. Grammar, public statement metadata и CLOOP не меняются. ON CONNECT shutdown assertion !child остаётся отдельным неисправленным и не перепроверенным ограничением.

## Codecs P8: целевая Linux/Super приёмка пройдена

Для Linux/Super реализованы text/binary input/output всех поддержанных scalar types: SMALLINT/INTEGER/BIGINT, BOOLEAN, CHAR/VARCHAR, NUMERIC/DECIMAL на SHORT/LONG/INT64/INT128, FLOAT/DOUBLE и DATE/TIME/TIMESTAMP без TZ. NULL сохраняется отдельно от payload. Новый набор используется и Simple Query output, и Extended Query; синтаксис SQL и native inference не меняются.

| Тип | PG OID | Границы |
|---|---:|---|
| FLOAT / DOUBLE PRECISION | 700 / 701 | IEEE float32/float64 binary в network order; text locale-independent; NaN и ±Infinity поддержаны |
| INT128, NUMERIC/DECIMAL до precision 38 | 1700 | Exact text и base-10000 binary numeric; pure INT128 сохраняет весь signed диапазон −2^127..2^127−1, scale 0..−38 |
| NUMERIC/DECIMAL на SHORT/LONG/INT64 | 1700 | Exact native range, scale 0..−18; binary numeric теперь поддержан |
| DATE | 1082 | Конечные даты 0001-01-01..9999-12-31; binary int32 дней от 2000-01-01 |
| TIME / TIMESTAMP без TZ | 1083 / 1114 | Native точность 100 µs; binary int64 µs, timestamp от 2000-01-01; время в пределах суток |

Numeric не проходит через float и не округляется: overflow, непредставимая дробь и numeric NaN/Infinity дают 22003. Malformed numeric binary — 22P03, включая ненулевые digits, скрываемые dscale; malformed text — 22P02. Float decimal overflow/underflow вне представимого диапазона — 22003, malformed text — 22P02, неверная binary length — 22P03.

Temporal text принимает строгий ISO `YYYY-MM-DD`, `HH:MM:SS[.ffffff]`, `YYYY-MM-DD HH:MM:SS[.ffffff]` либо разделитель `T`. Дробь содержит до шести цифр; пятая/шестая должны быть нулевыми для точного 100 µs native значения. Binary µs также должны делиться на 100 без остатка. Invalid text format — 22007; календарные/временные границы, непредставимая точность и infinity — 22008; неверная binary length — 22P03. Текстовые BC/TZ suffix не входят в ISO grammar и дают 22007; binary даты до 0001 года, 24:00 и leap second дают 22008. `integer_datetimes=on` отражает binary layout; TimeZone не публикуется. BLOB/ARRAY/OCTETS/DECFLOAT/TZ остаются отдельной работой.

## P9: клиентский профиль и подтверждённые ограничения

Зафиксированы C libpq 14.24 (headers/runtime) и Psycopg 3.3.6 с binary implementation/libpq 18.6 на Python 3.10.12. В Linux/Super IPv4 loopback профиль Psycopg 3 прошёл пять групп с четырьмя подтверждёнными ограничениями. Использованы настоящие Cursor/AsyncCursor, SQL без параметров и без rewriting; pipeline не проверялся. Полная целевая P9 приёмка прошла 17 групп; M2 принят для совместимого SQL и реализованных codecs.

| Клиентский режим P9 | Проверяемый контракт |
|---|---|
| C libpq sync | PASS: native `?` или ноль параметров; PQexecParams, named/unnamed PQprepare/PQexecPrepared, metadata, text/binary, повторное исполнение после commit/rollback |
| C libpq async без pipeline | PASS: 20 commands/20 results, 40 poll calls; PQsendPrepare/DescribePrepared/QueryParams/QueryPrepared с настоящим PQflush/PQconsumeInput/PQisBusy/poll loop; каждый поток PQgetResult полностью прочитан до NULL, ошибки Parse/Bind/Execute и дальнейшее recovery |
| Psycopg 3, autocommit=True, SQL без параметров | PASS: Simple SELECT с P8 values и пустым результатом; отдельно forced `prepare=True`/`binary=True`, named reuse через COMMIT и protocol Close при eviction. AsyncCursor также проверен в Simple/Extended/binary и с native COMMIT |
| Psycopg 3, native transaction controls | PASS: свежая connection с `autocommit=True, prepare_threshold=None`; SET TRANSACTION/COMMIT/ROLLBACK, savepoint, I/T/E, failed COMMIT и подтверждённые effects |
| Psycopg 3, обычные `%s` параметры | Ограниченный последовательный `$n` с типами из SQL теперь поддержан; новый live Psycopg 3 пропущен (клиент отсутствует), полная параметризация не заявляется |
| Psycopg 3, обычный transactional mode | Исторический P9 фиксировал 42601 на BEGIN; engine теперь поддерживает BEGIN без опций, сценарий обновлён, live-повтор Psycopg 3 в текущем окружении недоступен |
| Psycopg 3, `sql.Literal(date)` | Postfix DATE поддержан через native CastNode; ожидание обновлено, live-повтор Psycopg 3 пропущен из-за отсутствия клиента |

**Подтверждённая граница prepared cache:** после native ROLLBACK при непустом cache Psycopg 3 послал SQL `DEALLOCATE ALL` и получил 42601. Сам rollback завершился, состояние осталось I, следующий совместимый запрос выполнился. Отдельное удаление prepared statement через protocol Close прошло; это не доказывает поддержку whole-cache clear. В prepared trace зафиксированы Parse 6, Bind/Execute по 9, Sync 19 и Close 4.

Source-аудит Psycopg 3.3.6 также показывает cache invalidation после DROP/ALTER: `clear()` планирует `_deallocate(None)`, который использует SQL `DEALLOCATE ALL` даже с libpq 18.6. Эти два триггера очистки отдельно в live profile не проверены. RESET инвалидирует server objects, но не входит в список cache invalidation драйвера; возможный stale cache после RESET тоже остаётся source boundary без live утверждения. См. [_preparing.py](https://github.com/psycopg/psycopg/blob/3.3.6/psycopg/psycopg/_preparing.py) и [_connection_base.py](https://github.com/psycopg/psycopg/blob/3.3.6/psycopg/psycopg/_connection_base.py). Для forced preparation используется отдельная connection с включённым preparation: `prepare_threshold=None` отключает и явный `prepare=True`.

Целевой M2 Linux/Super принят по прямому libpq и обязательным raw-wire sequences на неизменённом совместимом SQL, с подтверждёнными P8 codecs и регрессиями M1. Это не обещает обычную `$n`-параметризацию Psycopg 3, DEALLOCATE ALL или произвольное приложение. SQL-границы сохраняются, pipeline/COPY/TLS/другие драйверы требуют отдельных профилей. Evidence и точные границы приёмки зафиксированы в VALIDATION; следующий этап — P10.

## Главная граница

Исходные байты каждой SQL-команды передаются в engine prepare с явной длиной и без изменения текста. Для Simple Query существующий lexer isql выделяет кандидаты границ, а engine prepare подтверждает завершённость команды по модели AUTOTERM. Ограниченный SqlInfo внутри PgWire определяет вид команды, command tag, профиль transaction/session controls и признак tokenless EMPTY; grammar, имена объектов, privileges и типы проверяет движок. Общий wire host SQL не анализирует.

После успешного engine prepare плагин сочетает lexical descriptor с существующими `getType()`, `getFlags()`, input/output metadata и affected records. `FLAG_HAS_CURSOR` выбирает cursor/fetch; output metadata определяет singleton output и codecs. RETURNING не превращает DML в SELECT. В I/T неподдержанный control либо UNKNOWN, включая USING wrapper, получает 0A000 до execution; в E успешно подготовленные команды, кроме разрешённого recovery, получают 25P02. Ошибка самого prepare имеет отдельный приоритет по CODEX_CONTEXT. Для классификации SQL новые публичные DSQL metadata, AST annotations и parser diagnostics не добавляются. Узкое исключение для standard_conforming_strings — attachment getInfo item и два prepare flags, описанные в 1c/README.md; методов и vtables оно не меняет.

Запрещены SQL rewriting, удаление comments из передаваемого текста, замена `$n` на `?`, подстановка параметров SQL-литералами и эмуляция успешного SET/BEGIN. Совместимость синтаксиса и реальное исполнение определяет только Firebird.

Simple Query поддерживает [последовательные скрипты](1c/README.md): исходные диапазоны выделяются по модели isql/AUTOTERM, каждая команда проходит обычный engine prepare и исполнение, первая окончательная ошибка прекращает остаток сообщения. Предварительная проверка синтаксиса всего Query отложена; ранний COMMIT сохраняется и при поздней syntax error. В I текущий implicit сегмент откатывается при ошибке, в T остаётся E. Extended Parse по-прежнему принимает одну команду. Приёмка от 10 ноября 2026 года — в [VALIDATION.md](VALIDATION.md).

EMPTY определяется ограниченным lexer только для нулевой строки, whitespace и полностью закрытых comments без SQL tokens. Даже такой текст передаётся в обычный engine prepare неизменённым. Только сочетание этого признака с engine syntax error 42601 после успешного cleanup преобразуется в EmptyQueryResponse; permission, cancellation, resource и другие ошибки не маскируются. Незаконченные строка/comment/statement и отдельный `;` остаются parser errors. Native parser diagnostics и `isql -autoterm` не меняются.

Последовательные типизированные `$n` добавлены в PG4; остальные SQL-границы сохраняются. SELECT без FROM, ограниченные SET и BEGIN реализованы движком; последовательный reader и разрешённая классификация не добавляют новые SQL-конструкции.

## Возможности по этапам

Колонки M0–M3 задают целевой объём этапов. P7 прошёл приёмку базовой части Extended Query; расширенные codecs P8 прошли целевую runtime-приёмку; целевой M2 принят в P9 по указанному клиентскому профилю.

| Возможность | M0 | M1 | M2 | M3 |
|---|---|---|---|---|
| Wire host и PG plugin в процессе `firebird` | Загрузка и первый endpoint | Lifecycle/config tests | Сохранить | Сохранить |
| Native listener при пустом списке и при включённых plugins | Да | Да | Да | Да |
| Firebird credentials, local engine, реальные privileges | Да | Да | Да | Да |
| Simple Query с неизменённым Firebird SQL | SELECT и error recovery | CRUD, совместимый DDL/session SQL, RETURNING | Сохранить, общие codecs | Сохранить |
| Command/control descriptor | Минимальный SELECT-профиль | Ограниченный lexer в plugin + штатные engine interfaces | Общий для обоих paths | Общий |
| EMPTY / whitespace/comment-only SQL | Не критерий первого SELECT | Plugin tokenless признак + engine prepare syntax error; native diagnostics неизменны | Общий для обоих paths | Общий |
| Прямой C libpq | Connect/PQexec | PQexec, metadata, state/error checks | PQexecParams, PQprepare/PQexecPrepared, Describe, async send/get | Прикладные сценарии |
| psycopg2 2.9.11 | Autocommit smoke без connection context | Autocommit и явный native transaction SQL, без автоматического BEGIN | Регрессия допустимого профиля | Если соответствует выбранной трассе |
| Parse/Bind/Describe/Execute/Close/Flush/Sync | Явный отказ | Явный отказ | Да | Да |
| Named/unnamed statements и portals | Нет | Нет | Да, с лимитами и проверенным lifetime | Да |
| Bind с native `?` либо zero parameters | Нет | Нет | Да, по engine metadata | Да |
| Postfix casts `::` | Исторически parser error | Исторически parser error | PG4: native non-array builtin targets + postfix-only INT8, только корневые SELECT/DML | Custom targets/сохранённый SQL требуют отдельного этапа |
| Literal `LIMIT n` | Исторически parser error | Исторически parser error | PG4 SELECT tail, integer 0…2147483647 в корневых SELECT/DML; native RowsClause | Общий LIMIT/OFFSET и сохранённый SQL требуют отдельного этапа |
| SQL `$n` | Исторически parser error | Исторически parser error | PG4: только последовательные уникальные inputs в SELECT/INSERT/UPDATE/DELETE/MERGE | Произвольная нумерация требует отдельного этапа |
| I/T/E, native SET TRANSACTION/COMMIT/ROLLBACK | Implicit Query transaction | Да, plugin-local controls после engine prepare | Да, включая implicit cycle до Sync | Да |
| Native SAVEPOINT/ROLLBACK TO/RELEASE и RETAIN | Не критерий smoke | Реальный effect и state policy | Тот же контракт | Тот же контракт |
| SMALLINT/INTEGER/BIGINT, BOOLEAN, CHAR/VARCHAR, NUMERIC до INT64, NULL | Три integer-типа, scale/subtype 0, NULL; text results | Text results | Text/binary input/output | Да |
| FLOAT/DOUBLE, DATE/TIME/TIMESTAMP без TZ, INT128/NUMERIC до 38 цифр | Не критерий smoke | Явное ограничение codec | Text/binary input/output | Да |
| DML RETURNING | Не критерий smoke | Да для поддержанных типов, правильная исходная операция | Общие расширенные codecs | Да |
| Native session commands/transaction characteristics | Не критерий smoke | Проверяет и выполняет движок; plugin descriptor и handles отражают эффект | Тот же контракт | Нужные приложению формы проверены |
| Подключение с другого сетевого узла | Loopback | Loopback | Loopback | Да |
| PostgreSQL SSLRequest / TLS | Ответ N | Ответ N | Ответ N | Upgrade в том же endpoint, проверка сертификата клиентом |
| BackendKeyData / CancelRequest | Нет | Нет | Общий lifecycle подготовлен | Да |

Общий host использует позиционные `WireProtocolPlugins` / `WireProtocolPorts`; пустая конфигурация отключает дополнительные endpoints. PG plugin загружается штатным PluginManager из `.so`/`.dll` по правилам ARCHITECTURE.md. Ошибка дополнительного plugin не должна делать native protocol зависимым от него. Один PG plugin — первая реализация общего extension point; реализация MySQL/MSSQL plugins в этот план не входит.

Одна библиотека/factory допускает несколько configured aliases с отдельными endpoint instances и секциями `Config`. Bind/TLS/protocol settings относятся к alias; единственными позиционными списками остаются plugins и ports. Независимость instances проверяется одним тестовым модулем с двумя aliases, а не требует двух СУБД-протоколов или двух библиотек.

## Синтаксис и клиентские режимы

| Клиентский сценарий | Результат без расширения grammar |
|---|---|
| `PQexec` с совместимым SQL | Simple Query M0/M1 |
| psycopg2 `autocommit=True`, обычный cursor без `with connection` | Совместимый SQL M0/M1 |
| psycopg2 transactional mode либо connection context, генерирующий BEGIN | BEGIN без опций поддержан; live SELECT/DML/commit/rollback проверены в этапе BEGIN |
| Прямой libpq, native SET TRANSACTION затем совместимый SQL/COMMIT/ROLLBACK | Explicit transaction tests M1 |
| `PQexecParams` с `CAST(? AS INTEGER)` в совместимом Firebird statement | Native binding M2 |
| `PQprepare`/`PQexecPrepared` с native `?` либо без parameters | Statement/portal lifecycle M2 |
| `PQexecParams`/`PQprepare`/`PQexecPrepared` с SQL `$n` | PASS ограниченного PG4 контракта: последовательные уникальные номера, типы из SQL, прежние metadata/codecs |
| Драйвер/ORM, отправляющий PostgreSQL SET, catalog queries или функции | Успех только если конкретный SQL уже совместим; иначе настоящая ошибка |

В Simple Query `%s` — placeholder клиентской библиотеки, сервер его не заменяет. Адаптер может добавить PostgreSQL cast, BEGIN или catalog query. Поэтому даже обычный SELECT приложения не доказывает совместимость всего поведения драйвера.

В M2 количество, порядок и типы входных native slots берутся из engine metadata. OID hints из Parse и значения Bind проверяются и преобразуются без изменений SQL. Одинаковое значение для нескольких native `?` передаётся несколькими protocol parameters. Будущие повторные `$1` и перестановка индексов должны описываться engine binding metadata после расширения parser; plugin не ищет их в SQL.

Postfix `::DATE` теперь использует прежний DATE codec, а поддержка Bind не означает произвольную PG-нумерацию `$n`. Если Firebird не может вывести тип параметра, например в нетипизированном выражении, возвращается ошибка; SQL не оборачивается в CAST автоматически. Тесты отдельно различают syntax, inference и codec limitations.

Ограниченный профиль Psycopg 3 проверен в разделе P9 выше; pgjdbc, Npgsql, pgx и другие клиенты не объявляются поддержанными по факту Extended Query. Их startup, автоматически генерируемый SQL и metadata discovery проверяются на выбранных версиях. Для минимального M2 acceptance достаточно прямого libpq с native SQL и обязательных raw-wire последовательностей.

## Семантика результатов и транзакций

Совместимые CREATE/ALTER/DROP, index и privilege операции покрываются descriptor и тестами. Неописанная команда получает явный 0A000 после prepare до исполнения. Command tag дополняет coarse getType сведениями ограниченного lexer: MERGE, UPDATE OR INSERT, RETURNING, DDL modifiers и control/session forms не смешиваются.

Прежний M0 SELECT-only фильтр не является текущим execution gate. P4 поддерживает обычные DML/DDL; P5 добавляет plugin-local `ControlKind` и state policy без новых getInfo items или parser observers. Native `SET TRANSACTION` выдаёт BEGIN после реального начала transaction. Полные COMMIT/ROLLBACK в I завершают существующий implicit сегмент либо являются no-op при отсутствии handle; retaining и savepoints — 25000/I. Повторный SET TRANSACTION в T даёт 25001/E. PG BEGIN/START TRANSACTION без опций поддержаны, как описано выше; RETAINING остаётся parser error; native формы — `COMMIT/ROLLBACK [WORK] RETAIN [SNAPSHOT]`.

P5 сохраняет native transaction при ошибке explicit block и ставит E. Обычный prepare выполняется на этом handle; descriptor после prepare разрешает recovery, остальные успешно подготовленные команды блокируются с 25P02 без execution. Failed-prepare whitelist содержит только настоящий `isc_dsql_relation_err` с итоговой nonfatal диагностикой 42P01; explicit SQLSTATE без причины недостаточен. Syntax остаётся 42601, permission — 42501; fatal/shutdown, cancel/timeout, invalid handle, resource/I/O и unknown errors не маскируются. Полный COMMIT из E откатывает текущую работу и выдаёт ROLLBACK/I; ROLLBACK завершает её; успешный ROLLBACK TO восстанавливает T и сохраняет изменения до savepoint. ROLLBACK RETAIN также восстанавливает T, COMMIT RETAIN в E получает 25P02. Полная таблица и edge cases заданы в CODEX_CONTEXT. Факт I/T/E не обещает совпадения isolation, locks или DDL semantics с PostgreSQL.

PgWire session управляет максимум одной текущей application transaction на своём attachment. P7 хранит prepared statements и portals по контракту выше; для standard_conforming_strings добавлена узкая история пользовательских savepoint names/snapshots. Native session SET реализованы по whitelist CODEX_CONTEXT, с tag SET и реальным изменением engine state. ALTER SESSION RESET выдаёт RESET и учитывает смену underlying transaction при том же interface pointer: I остаётся I, T — T, E получает 25P02. Внутренние engine system/security/autonomous transactions исключены из подсчёта application handles. Parse/Describe/statements/portals используют current handle либо prepare с NULL без скрытых transactions; RESET инвалидирует их кэш, а runtime cache/lifetime tests входят в P7 acceptance. Это ограничение PgWire, не native protocol или общего host для других plugins.

Явные native transaction characteristics сохраняют семантику Firebird. В частности, `SET TRANSACTION AUTO COMMIT` может зафиксировать изменения до конца логического блока: последующий rollback не отменяет прежние commits. AUTO COMMIT / NO AUTO UNDO не сканируются lexer и не извлекаются в descriptor; их применяет native engine, а приёмка сравнивает effects с native API. I/T/E не подменяет это поведение обещанием PostgreSQL atomicity.

**AUTO COMMIT с READ COMMITTED READ CONSISTENCY не входит в подтверждённую совместимость.** Native Debug baseline воспроизводит assertion `Savepoint.cpp:833` в restart path через embedded isql без PgWire/server/triggers. SNAPSHOT AUTO COMMIT имеет положительный native baseline и входит в текущий harness; NO AUTO UNDO проверяется отдельно. Дефект не обходится изменением core или SQL options; полный repro и границы покрытия — в VALIDATION.

EMPTY определяется ограниченным lexer только для нулевой строки, whitespace и полностью закрытых comments без SQL tokens. Даже такой текст передаётся в обычный engine prepare неизменённым. Только сочетание этого признака с engine syntax error 42601 после успешного cleanup преобразуется в EmptyQueryResponse; permission, cancellation, resource и другие ошибки не маскируются. Незаконченные строка/comment/statement и отдельный `;` остаются parser errors. Native parser diagnostics и `isql -autoterm` не меняются. CHAR/UTF8 padding и full status vector имеют отдельные tests и не меняют эту границу.

## Отдельные расширения и ограничения

- PostgreSQL grammar/functions/catalog compatibility — будущий engine этап. Если он станет обязательным для целевого приложения, M3 этого приложения зависит от него явно.
- Protocol 3.2, direct TLS negotiation, GSS encryption, PostgreSQL SCRAM/MD5 и channel binding — отдельные возможности. M3 начинает с password exchange внутри TLS и существующих Firebird credentials.
- Полный предварительный разбор Simple Query остаётся отложенным: поддержаны последовательные скрипты с остановкой на первой окончательной ошибке. COPY, libpq pipeline mode, SQL named cursors, large-object API, LISTEN/NOTIFY и replication — отдельные wire/session contracts.
- BLOB/bytea, ARRAY, OCTETS, DECFLOAT, типы с часовыми поясами и PostgreSQL custom types — отдельные codecs. Firebird BLOB handle не выдаётся за bytea.
- Remote security database и базы, требующие ключ шифрования от native клиента, не входят в начальный authentication profile.
- Текущая платформа — Linux/Super. SuperClassic/Classic пока диагностируются с сохранением native; для них требуется отдельная работа и приёмка. Windows host wiring/runtime отсутствуют; `.dll` входит в архитектуру plugin ABI, но не доказывает готовность Windows build.

Обрыв клиентского socket во время engine call обнаруживается после возврата из движка при следующем I/O; отдельного disconnect watcher нет. Явный server shutdown отменяет engine operation и закрывает session. P6 проверяет эти серверные пути без добавления сценариев пауз или срабатывания таймаутов.

Неподдержанный тип или интерфейсный режим возвращает явную диагностическую ошибку до side effects, когда metadata позволяет это определить. Ограничения не скрываются успешными пустыми ответами или coercion всех результатов в text. Узкое явное исключение пользователя — enable_mergejoin/cpu_operator_cost/lock_timeout: их временный SET без эффекта и отложенная семантика описаны выше.

## Прикладной профиль и доказательства

Приложение и трасса — обязательная зависимость M3; серверный профиль M1 от выбора приложения не зависит. В `IMPLEMENTATION_NOTES.md` фиксируются версии приложения/драйвера/libpq, ОС, connection settings без секретов, wire operations, автоматически генерируемый SQL, типы, isolation/autocommit, reset/reuse и cancellation. Трасса определяет зависимости от будущих grammar/catalog изменений заранее.

Критерий M3 — воспроизводимый сценарий конкретного приложения через PostgreSQL-драйвер по TCP с другого узла, с TLS и заявленным cancellation/session поведением. Допустимы endpoint/database/credentials и совместимый набор SQL. Изменения кода приложения или специальный режим драйвера записываются как ограничения. Если неизменённый клиент требует произвольные `$n` или другие неподдержанные формы, M3 такого профиля не принимается до engine расширения; нельзя подменить его тестом autocommit/native `?` и объявить прозрачную совместимость.

`server_version` в ParameterStatus — проверяемое обещание для ветвления клиента, не версия движка и не общее обещание SQL совместимости. Значение из исторического probe проверяется на каждом заявленном клиентском профиле.

`tests/test_pgwire_acceptance.py` мигрирован под текущий контракт; 58 cases прошли live в P6 harness. ON CONNECT shutdown остаётся отдельно исключённым native дефектом с фактическим FAIL reproducer; целевой P6/M1 принят без его исправления. Отчёт `VALIDATION.md` указывает реально исполненные checks, версии и принятый milestone; driver-only probe доказательством Firebird integration не служит.
