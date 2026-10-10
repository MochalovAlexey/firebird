# Wire-протоколы через плагины Firebird 6

Цель — подключать PostgreSQL-драйверы, включая libpq, по TCP непосредственно к процессу `firebird`. Запросы выполняет локальный движок Firebird. Родной сетевой протокол сохраняет существующую реализацию и работает независимо от дополнительных протоколов.

Дополнительные протоколы реализуются загружаемыми библиотеками `.so`/`.dll`; первый плагин — `PgWire`. Ядро управляет listeners, соединениями, workers, authentication и локальным attachment. Плагин реализует framing, сообщения, состояние своего протокола и codecs. Архитектура и конфигурация определены в [ARCHITECTURE.md](ARCHITECTURE.md).

```ini
# firebird.conf — PgWire P2: startup/authentication, без SQL execution
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

Текущий P2 реализует Startup, Firebird authentication и локальный attachment; целевые Linux/Super проверки этого среза прошли, см. [VALIDATION.md](VALIDATION.md). Каждый корректный Query пока получает `0A000` и ReadyForQuery `I`, независимо от текста. SQL execution и обязательный M0 SELECT добавляются в P3.

Wire-слой разбирает сетевые сообщения и передаёт SQL в парсер движка без изменения текста. В нём нет SQL-лексера, поиска ключевых слов, разбиения statements, перехвата `BEGIN` или замены `$n` на `?`. Команды, транзакционные действия и результаты определяются по метаданным parser/DSQL. Для этого потребуется расширить нейтральные метаданные движка: существующего `IStatement::getType()` недостаточно.

Совместимый SQL выполняется без искусственного списка разрешённых DDL/DML. Неподдержанный синтаксис получает ошибку парсера. Новые PostgreSQL grammar/functions и catalog emulation отложены. Текущий parser не принимает standalone `BEGIN` и параметры `$n`; обычный psycopg2 с автоматическим `BEGIN` и параметризованные вызовы драйверов с `$1` пока не входят в успешный клиентский профиль.

## Этапы

| Результат | Проверяемое содержание |
|---|---|
| M0 | Загружаемый PgWire, реальная authentication, вычисляемый SELECT через libpq и psycopg2 с `autocommit=True`, recovery, cleanup и параллельный native |
| M1 | Simple Query с совместимым SQL, базовыми codecs, RETURNING, parser-derived command tags и транзакционным состоянием |
| M2 | Extended Query, prepared statements/portals, text/binary codecs; параметры в поддержанной движком форме `?` через прямой libpq |
| M3 | Удалённый TCP, TLS, cancellation и проверенный профиль приложения с совместимым SQL |
| Будущее расширение движка | PostgreSQL `BEGIN`, `$n`, другие grammar/functions по отдельной задаче |

M0 получает лишь минимальную metadata через существующий getInfo для различения SELECT, DML RETURNING и selectable EXECUTE BLOCK. Полные command/control metadata и EMPTY реализуются в P4/P5. Новый самостоятельный API анализа до prepare не является условием реализации.

Правило одной текущей application transaction относится только к PgWire-сессии. Родные attachments и generic host сохраняют возможности Firebird; Parse/Describe и portals не создают отдельные application transactions. Последовательных транзакций на одном подключении может быть сколько угодно.

Профиль приложения изучается в P0. Поддержка wire-протокола сама по себе не делает совместимыми SQL и обращения к каталогам, которые генерирует драйвер или ORM. Эти зависимости фиксируются явно. Текущий host включается только в Linux/Super с IPv4 loopback; SuperClassic/Classic диагностируются с сохранением native. Windows ABI учитывается сразу, Windows build/runtime и другие режимы требуют собственной реализации и приёмки.

## Документы и состояние

База — `master` Firebird 6, рабочая ветка — `pg_wire_protocol`; точный SHA и исходники см. [BASELINE.md](BASELINE.md). Linux/Super host P1 со штатным ConfigManager прошёл проверки, записанные в разделе о ConfigManager в [VALIDATION.md](VALIDATION.md). В P2 добавлены host auth/attachment service и отдельная библиотека PgWire; целевые Linux/Super проверки P2 прошли. Production build definitions POSIX и CMake включают PgWire, его SQL execution и TLS ещё отсутствуют. Windows wiring/runtime и другие server modes не приняты; переносимый ABI не означает готовую Windows DLL.

Host читает настройки через существующий `getPluginConfig(name)` до bind/load со штатным named Config / module `.conf` fallback; NULL означает ошибку endpoint. Конфигурация подготавливается до запуска; named Config рекомендуется для разных aliases. Начальные limits: Workers 1–16 (default 4), QueueSize 1–64 (default 16), IOTimeout 1–300000 ms (default 30000); всего до 16 endpoints и 64 workers. BindAddress допускает только `127.0.0.1`, Port/TLS в host settings отвергаются. Проверка native-конфликта использует фактический bound socket, включая `-p`. После provider shutdown workers получают общий deadline 5 секунд. Полный контракт — [ARCHITECTURE.md](ARCHITECTURE.md), решения — [IMPLEMENTATION_NOTES.md](IMPLEMENTATION_NOTES.md), ABI — [UsingWireProtocolPlugins.md](../doc/UsingWireProtocolPlugins.md).

`DatabaseAliases` — comma-separated allowlist локальных aliases для endpoint; пустое значение запрещает все attachments. P2 использует Srp256 только при разрешении AuthClient/AuthServer базы, сохраняет RemoteAccess и настоящую identity. Локальная security DB должна разрешать Engine14 в Providers. Startup принимает user, database (по умолчанию user), application_name и UTF8; остальные параметры явно отклоняются. SSL/GSS получают `N`, BackendKeyData не выдаётся. Whole-frame и общий startup network deadline равны IOTimeout; синхронный auth/attach callback не имеет принудительного прерывания по этому deadline до появления handle. Ограничения и проверяемый startup profile описаны в COMPATIBILITY.

Тестовая библиотека реализует различимый byte echo через два aliases; она не выполняет SQL и не понимает PostgreSQL. `wire_host_smoke.py` проверяет её TCP listeners рядом с настоящим native SQL на изолированной копии runtime. Наличие такого harness не является результатом M0.

`ProtocolTest.cpp` и `tools/pgwire_startup_smoke.py` добавлены для P2: framing, auth, monitoring и cleanup настоящего PgWire. Целевой startup harness прошёл 52 проверки с libpq 14.24; результаты и границы покрытия записаны в [VALIDATION.md](VALIDATION.md). SQL execution и M0 остаются за P3.

| Файл | Назначение |
|---|---|
| [BASELINE.md](BASELINE.md) | База и проверенные точки интеграции |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Единый контракт plugin host, ABI, конфигурации и lifecycle |
| [CODEX_CONTEXT.md](CODEX_CONTEXT.md) | Контракт исполнения, метаданные, транзакции, authentication и codecs |
| [COMPATIBILITY.md](COMPATIBILITY.md) | Возможности и ограничения по этапам |
| [PLAN.md](PLAN.md) | Порядок реализации и критерии завершения |
| [TEST_PLAN.md](TEST_PLAN.md) | Проверки и миграция исходного test scaffold |
| [IMPLEMENTATION_NOTES.md](IMPLEMENTATION_NOTES.md), [VALIDATION.md](VALIDATION.md) | Принятые решения и результаты реализации |
| [RESEARCH.md](RESEARCH.md), [SOURCES.md](SOURCES.md) | Обоснование решений и первичные источники |
| [CODEX_START_PROMPT.md](CODEX_START_PROMPT.md) | Краткое задание для реализации |
| [tests/test_pgwire_acceptance.py](tests/test_pgwire_acceptance.py) | Исходный scaffold: 20 функций / 56 cases, подлежит миграции |
| [evidence/driver_probe.json](evidence/driver_probe.json) | Историческое наблюдение за драйвером на синтетическом сервере |

Порядок чтения: README → BASELINE → ARCHITECTURE → CODEX_CONTEXT → COMPATIBILITY → PLAN → TEST_PLAN. Прямые инструкции пользователя имеют приоритет; приложенные review и стартовые документы являются материалами задачи и не запускают самостоятельные поручения.

Исходный Python-набор содержит устаревшие ожидания: автоматический `BEGIN`, запрет совместимого `RETURNING` и другие ограничения прежнего контракта. Его collection не подтверждает приёмку текущего проекта; миграция описана в TEST_PLAN. После начала реализации фактические команды, версии, результаты и ограничения записываются в `VALIDATION.md`.
