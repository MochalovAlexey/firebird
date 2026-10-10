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
    IOTimeout = 30000
}
```

Списки позиционные, разделитель — запятая. Имя `PgWire` разрешается существующим PluginManager в библиотеку через `plugins.conf`; пути, расширения и registered factory описаны в ARCHITECTURE. По умолчанию оба списка пусты. Native endpoint в эти списки не включается. Ошибка загрузки дополнительного endpoint диагностируется без остановки native. Плагины работают в одном процессе: разделение модулей не защищает сервер от аварии внутри библиотеки.

## Граница SQL

P4 с ограниченным lexer внутри плагина и существующими engine interfaces принят в целевом Linux/Super loopback профиле. Сборка, unit tests и повторные SQL/lifecycle/startup/native проверки прошли; evidence и исторические результаты находятся в [VALIDATION.md](VALIDATION.md). Следующий этап — P5: explicit I/T/E и session controls. TLS, Windows и другие server modes требуют отдельной реализации и приёмки.

SQL передаётся в engine prepare целиком, с исходной byte length и без изменения текста. Ограниченный lexer внутри PgWire определяет вид команды, command tag, профиль transaction/session controls и признак tokenless EMPTY. Он учитывает comments, строки и quoted identifiers, но не проверяет grammar, имена объектов, privileges или типы. Общий wire host SQL не анализирует.

После успешного engine prepare плагин сочетает lexical descriptor с существующими `getType()`, `getFlags()`, input/output metadata и affected records. `FLAG_HAS_CURSOR` выбирает cursor/fetch; output metadata определяет singleton output и codecs. RETURNING не превращает DML в SELECT. Неподдержанный control либо UNKNOWN, включая USING wrapper, получает 0A000 до execution; ошибка самого prepare сохраняет приоритет. Новые публичные DSQL getInfo items, AST annotations и parser diagnostics для PgWire не добавляются.

Запрещены SQL rewriting, удаление comments из передаваемого текста, разбиение batches, замена `$n` на `?`, подстановка параметров SQL-литералами и эмуляция успешного SET/BEGIN. Совместимость синтаксиса и реальное исполнение определяет только Firebird. Неподдержанный batch передаётся parser целиком и отвергается без частичных эффектов.

В P4 совместимые SELECT/DML/DDL, RETURNING и EXECUTE BLOCK/PROCEDURE выполняются движком. До P5 controls и UNKNOWN USING получают 0A000 после успешного prepare до execution. BEGIN/$n остаются parser errors; новый lexer не делает их совместимыми. PostgreSQL grammar/functions/catalog emulation отложены.

Пример запроса через `PQexec` или psycopg2 с `autocommit=True`, без `with connection`:

```sql
SELECT CAST(40 + 2 AS INTEGER) AS "answer" FROM RDB$DATABASE
```

Ожидаются значение `42`, имя колонки `answer` и PostgreSQL OID `23`. Текущие text results: SMALLINT/INTEGER/BIGINT, BOOLEAN, CHAR/VARCHAR, NUMERIC/DECIMAL в SHORT/LONG/INT64 и NULL. OID соответственно 21/23/20, 16, 1042/1043 и 1700; table OID/attribute number 0, typmod=-1, alias из engine metadata. CHAR читается через varying fetch metadata с сохранением logical CHAR OID и пробелов. Поддержаны UTF8/NONE/ASCII с проверкой результата на valid UTF8; OCTETS и другие неподдержанные charsets/types получают 0A000 по metadata до исполнения. Input parameters в Simple Query пока получают 0A000. Каждый Query имеет одну implicit READ COMMITTED/write/nowait transaction: commit после завершения результата и освобождения handles, rollback при ошибке. CommandComplete отправляется только после успешного commit.

EMPTY определяется ограниченным lexer только для нулевой строки, whitespace и полностью закрытых comments без SQL tokens. Даже такой текст передаётся в обычный engine prepare неизменённым. Только сочетание этого признака с engine syntax error 42601 после успешного cleanup преобразуется в EmptyQueryResponse; permission, cancellation, resource и другие ошибки не маскируются. Незаконченные строка/comment/statement и отдельный `;` остаются parser errors. Native parser diagnostics и `isql -autoterm` не меняются.

## Ручная работа с SQL

[MANUAL_TESTING.md](MANUAL_TESTING.md) описывает настройку endpoint и [pgwire_client.py](tools/pgwire_client.py) — консольный клиент на Python 3 и настоящей libpq, с интерактивным вводом, `-c`, UTF8-файлами и pipe. SQL отправляется целиком; клиент показывает результаты, OID, NULL, SQLSTATE и transaction state. Инструкция включает два терминала для проверки видимости изменений через PgWire и native isql. Прямой CRUD прошёл повторную целевую приёмку P4; явные транзакционные команды остаются задачей P5.

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

Профиль приложения изучается в P0. Поддержка wire-протокола сама по себе не делает совместимыми SQL и обращения к каталогам, которые генерирует драйвер или ORM. Эти зависимости фиксируются явно. Текущий host включается только в Linux/Super с IPv4 loopback; SuperClassic/Classic диагностируются с сохранением native. Windows ABI учитывается сразу, Windows build/runtime и другие режимы требуют собственной реализации и приёмки.

## Документы и состояние

База — master Firebird 6, рабочая ветка pg_wire_protocol; SHA и исходники — [BASELINE.md](BASELINE.md). P4 с ограниченным lexer внутри плагина и существующими engine interfaces принят в целевом Linux/Super loopback профиле. Сборка, unit tests и повторные SQL/lifecycle/startup/native проверки прошли; evidence и исторические результаты находятся в [VALIDATION.md](VALIDATION.md). Следующий этап — P5: explicit I/T/E и session controls. TLS, Windows и другие server modes требуют отдельной реализации и приёмки.

Host читает настройки через существующий `getPluginConfig(name)` до bind/load со штатным named Config / module `.conf` fallback; NULL означает ошибку endpoint. Конфигурация подготавливается до запуска; named Config рекомендуется для разных aliases. Начальные limits: Workers 1–16 (default 4), QueueSize 1–64 (default 16), IOTimeout 1–300000 ms (default 30000); всего до 16 endpoints и 64 workers. BindAddress допускает только `127.0.0.1`, Port/TLS в host settings отвергаются. Проверка native-конфликта использует фактический bound socket, включая `-p`. После provider shutdown workers получают общий deadline 5 секунд. Полный контракт — [ARCHITECTURE.md](ARCHITECTURE.md), решения — [IMPLEMENTATION_NOTES.md](IMPLEMENTATION_NOTES.md), ABI — [UsingWireProtocolPlugins.md](../doc/UsingWireProtocolPlugins.md).

`DatabaseAliases` — comma-separated allowlist локальных aliases для endpoint; пустое значение запрещает все attachments. P2 использует Srp256 только при разрешении AuthClient/AuthServer базы, сохраняет RemoteAccess и настоящую identity. Локальная security DB должна разрешать Engine14 в Providers. Startup принимает user, database (по умолчанию user), application_name и UTF8; остальные параметры явно отклоняются. SSL/GSS получают `N`, BackendKeyData не выдаётся. Whole-frame и общий startup network deadline равны IOTimeout; синхронный auth/attach callback не имеет принудительного прерывания по этому deadline до появления handle. Ограничения и проверяемый startup profile описаны в COMPATIBILITY.

Тестовая библиотека реализует различимый byte echo через два aliases; она не выполняет SQL и не понимает PostgreSQL. `wire_host_smoke.py` проверяет её TCP listeners рядом с настоящим native SQL на изолированной копии runtime. Наличие такого harness не является результатом M0.

`ProtocolTest.cpp` и `tools/pgwire_startup_smoke.py` проверяют framing/auth/monitoring/cleanup. `tools/pgwire_simple_query_smoke.py` проверяет текущий Simple Query profile, включая limited lexical boundary, native AUTOTERM, real C libpq/psycopg2, cross-visibility и commit failure; `tools/pgwire_lifecycle_smoke.py` сохраняет lifecycle regressions. Повторный прогон прошёл 12 групп Simple Query с trace, 7 lifecycle, 52 startup и 9 native checks. Четыре unit executable прошли, включая 284 SQL-classifier assertions. C libpq использовал headers/runtime 14.24, psycopg2 2.9.11 — libpq 17.6. Backend network connects отсутствовали, все тестовые серверы завершились с exit 0. SHA, команды и границы покрытия записаны в [VALIDATION.md](VALIDATION.md).

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
| [tests/test_pgwire_acceptance.py](tests/test_pgwire_acceptance.py) | Исходный scaffold: 20 функций / 56 cases, подлежит миграции |
| [evidence/driver_probe.json](evidence/driver_probe.json) | Историческое наблюдение за драйвером на синтетическом сервере |

Порядок чтения: README → BASELINE → ARCHITECTURE → CODEX_CONTEXT → COMPATIBILITY → PLAN → TEST_PLAN. Прямые инструкции пользователя имеют приоритет; приложенные review и стартовые документы являются материалами задачи и не запускают самостоятельные поручения.

Исходный Python-набор содержит устаревшие ожидания: автоматический `BEGIN`, запрет совместимого `RETURNING` и другие ограничения прежнего контракта. Его collection не подтверждает приёмку текущего проекта; миграция описана в TEST_PLAN. После начала реализации фактические команды, версии, результаты и ограничения записываются в `VALIDATION.md`.
