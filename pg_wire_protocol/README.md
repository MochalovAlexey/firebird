# Wire-протоколы через плагины Firebird 6

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

Предыдущая P5-ревизия прошла целевую Linux/Super loopback приёмку: I/T/E, native transaction/session controls и recovery. В P6 сборка, unit tests и host/startup/Simple Query/transaction/lifecycle/native регрессии прошли. **Известное ограничение native baseline: READ COMMITTED READ CONSISTENCY + AUTO COMMIT не входит в подтверждённую совместимость; SNAPSHOT AUTO COMMIT проверен.** Целевой P6/M1 Linux/Super принят с известными native ограничениями. Shutdown во время ON CONNECT остаётся отдельным исключением: native Debug assertion воспроизводится также без wire endpoints. P7 Extended Query прошёл целевую Linux/Super приёмку: 16 групп. P8 codecs прошли целевую Linux/Super приёмку: восемь групп; следующий этап — P9, клиентская приёмка; M2 до P9 не заявляется. Результаты и границы покрытия — в [VALIDATION.md](VALIDATION.md). TLS, Windows и другие server modes требуют отдельной реализации и приёмки.

SQL передаётся в engine prepare целиком, с исходной byte length и без изменения текста. Ограниченный lexer внутри PgWire определяет вид команды, command tag, профиль transaction/session controls и признак tokenless EMPTY. Он учитывает comments, строки и quoted identifiers, но не проверяет grammar, имена объектов, privileges или типы. Общий wire host SQL не анализирует.

После успешного engine prepare плагин сочетает lexical descriptor с существующими `getType()`, `getFlags()`, input/output metadata и affected records. `FLAG_HAS_CURSOR` выбирает cursor/fetch; output metadata определяет singleton output и codecs. RETURNING не превращает DML в SELECT. В I/T неподдержанный control либо UNKNOWN, включая USING wrapper, получает 0A000 до execution; в E успешно подготовленные команды, кроме разрешённого recovery, получают 25P02. Ошибка самого prepare имеет отдельный приоритет по CODEX_CONTEXT. Новые публичные DSQL getInfo items, AST annotations и parser diagnostics для PgWire не добавляются.

Запрещены SQL rewriting, удаление comments из передаваемого текста, разбиение batches, замена `$n` на `?`, подстановка параметров SQL-литералами и эмуляция успешного SET/BEGIN. Совместимость синтаксиса и реальное исполнение определяет только Firebird. Неподдержанный batch передаётся parser целиком и отвергается без частичных эффектов.

Совместимые SELECT/DML/DDL, RETURNING и EXECUTE BLOCK/PROCEDURE выполняются движком. P5 добавляет native `SET TRANSACTION`, COMMIT/ROLLBACK, RETAIN, savepoints, session SET и RESET. PostgreSQL `BEGIN`, `START TRANSACTION`, `$n` и слово `RETAINING` остаются parser errors. Native retaining записывается `COMMIT/ROLLBACK [WORK] RETAIN [SNAPSHOT]`. PostgreSQL grammar/functions/catalog emulation отложены.

Пример запроса через `PQexec` или psycopg2 с `autocommit=True`, без `with connection`:

```sql
SELECT CAST(40 + 2 AS INTEGER) AS "answer" FROM RDB$DATABASE
```

Ожидаются значение `42`, имя колонки `answer` и PostgreSQL OID `23`. Текущие text results: SMALLINT/INTEGER/BIGINT, BOOLEAN, CHAR/VARCHAR, NUMERIC/DECIMAL в SHORT/LONG/INT64/INT128, FLOAT/DOUBLE, DATE/TIME/TIMESTAMP без TZ и NULL. OID соответственно 21/23/20, 16, 1042/1043, 1700, 700/701, 1082/1083/1114; table OID/attribute number 0, typmod=-1, alias из engine metadata. CHAR читается через varying fetch metadata с сохранением logical CHAR OID и пробелов. Поддержаны UTF8/NONE/ASCII с проверкой результата на valid UTF8; OCTETS и другие неподдержанные charsets/types получают 0A000 по metadata до исполнения. Input parameters в Simple Query пока получают 0A000.

Один `QueryExecutor` на сессию владеет текущей application transaction. В I prepare получает NULL, затем обычный SQL выполняется в implicit READ WRITE / READ COMMITTED READ CONSISTENCY / WAIT transaction без заданного плагином lock timeout. В Simple Query результат завершается настоящим commit до CommandComplete; ошибка откатывает implicit transaction. В T/E prepare использует текущий handle. Ошибка explicit SQL сохраняет его и переводит сессию в E; успешный `ROLLBACK TO` либо `ROLLBACK RETAIN` восстанавливает T. Полный COMMIT в E реально откатывает работу и выдаёт tag ROLLBACK. ReadyForQuery сообщает I/T/E; полный контракт и приоритет ошибок — в CODEX_CONTEXT. `AUTO COMMIT` и `NO AUTO UNDO` сохраняют native семантику без сканирования опций.

SQL использует штатные engine timeouts: в firebird.conf StatementTimeout задаётся в секундах, ConnectionIdleTimeout — в минутах, 0 означает отсутствие соответствующего engine cap. Плагин не задаёт собственные statement/lock timeouts. Native idle timeout закрывает attachment с rollback; следующий Query получает FATAL 57P05 и закрытие, без повторного подключения. AUTO COMMIT проверяется с SNAPSHOT, NO AUTO UNDO — отдельно. Известное ограничение native baseline: сочетание AUTO COMMIT с READ COMMITTED READ CONSISTENCY вызывает assertion `Savepoint.cpp:833`, воспроизводимый embedded isql без PgWire/server/triggers; этот профиль не входит в подтверждённую совместимость. Подробности — в VALIDATION.

EMPTY определяется ограниченным lexer только для нулевой строки, whitespace и полностью закрытых comments без SQL tokens. Даже такой текст передаётся в обычный engine prepare неизменённым. Только сочетание этого признака с engine syntax error 42601 после успешного cleanup преобразуется в EmptyQueryResponse; permission, cancellation, resource и другие ошибки не маскируются. Незаконченные строка/comment/statement и отдельный `;` остаются parser errors. Native parser diagnostics и `isql -autoterm` не меняются.

P8 добавляет точные text/binary NUMERIC до native INT128 (NUMERIC/DECIMAL до 38 цифр), IEEE FLOAT/DOUBLE и даты/время без TZ. Binary NUMERIC использует base-10000, даты — дни от 2000-01-01, время/timestamp — микросекунды; native bytes не передаются напрямую. Даты ограничены 0001..9999, время имеет точность 100 мкс; непредставимый ввод получает 22008. `integer_datetimes=on`, TimeZone не объявляется. Подробные диапазоны — в [COMPATIBILITY.md](COMPATIBILITY.md).

## Extended Query P7

Parse/Bind/Describe/Execute/Close/Flush/Sync прошли целевую Linux/Super приёмку: 16 групп, см. [VALIDATION.md](VALIDATION.md). Прямой libpq может передать `SELECT CAST(? AS INTEGER) FROM RDB$DATABASE` с отдельным Bind value, без изменения SQL. Text/binary input/output поддержаны для integer/BOOLEAN/CHAR/VARCHAR, NUMERIC/DECIMAL и INT128, FLOAT/DOUBLE, DATE/TIME/TIMESTAMP без TZ и NULL; BEGIN/$n требуют будущего расширения parser.

Named statements переживают commit; portals сохраняются через Sync внутри explicit transaction и удаляются при её завершении/ошибке. RESET инвалидирует все statements/portals. Лимиты session — 128 statements, 128 portals, 8 MiB учитываемых сохранённых SQL/metadata/buffers; другие engine allocations ограничиваются числом объектов. Полный контракт — в [CODEX_CONTEXT.md](CODEX_CONTEXT.md#11-extended-query-p7-принят-для-linuxsuper). P7 не заявляет полный M2 или совместимость Psycopg 3; driver/application profile проверяется отдельно в P9.

P7 включает узкое исправление native release_attachment для нескольких prepared requests одного Statement; падение воспроизведено также через native TCP без PgWire. Grammar, public statement metadata и CLOOP не меняются. Отдельный ON CONNECT shutdown assertion !child остаётся неисправленным и в P7 не перепроверялся. P8 codecs прошли целевую Linux/Super приёмку: восемь групп; следующий этап — P9, клиентская приёмка.

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
| Будущее расширение движка | PostgreSQL `BEGIN`, `$n`, другие grammar/functions по отдельной задаче |

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

Python-набор мигрирован: native controls и positive RETURNING, driver-generated BEGIN остаётся отрицательным случаем. [P6 server harness](tools/pgwire_server_smoke.py) добавляет resource accounting после прогрева, административные отмену/завершение и shutdown при lock wait/password/partial startup. SuperClassic проверяется только на сохранение native при отказе PgWire. Новых тестов пауз/срабатывания таймаутов нет; семь групп и 58 pytest cases прошли. Отдельный [ON CONNECT shutdown repro](tools/pgwire_attach_shutdown_smoke.py) сохраняет FAIL также native-only с assertion alloc.cpp:2075 (!child); этот native дефект исключён из принятого P6/M1 и не исправлен в P7. P7 Extended Query прошёл целевую Linux/Super приёмку: 16 групп. P8 codecs прошли целевую Linux/Super приёмку: восемь групп; следующий этап — P9, клиентская приёмка; M2 до P9 не заявляется. Evidence — в VALIDATION.
