# Wire-протоколы через плагины Firebird 6

Цель — подключать PostgreSQL-драйверы, включая libpq, по TCP непосредственно к процессу `firebird`. Запросы выполняет локальный движок Firebird. Родной сетевой протокол сохраняет существующую реализацию и работает независимо от дополнительных протоколов.

Дополнительные протоколы реализуются загружаемыми библиотеками `.so`/`.dll`; первый плагин — `PgWire`. Ядро управляет listeners, соединениями, workers, authentication и локальным attachment. Плагин реализует framing, сообщения, состояние своего протокола и codecs. Архитектура и конфигурация определены в [ARCHITECTURE.md](ARCHITECTURE.md).

```ini
# firebird.conf — предлагаемая конфигурация
WireProtocolPlugins = PgWire
WireProtocolPorts = 15432
```

Списки позиционные, разделитель — запятая. Имя `PgWire` разрешается существующим PluginManager в библиотеку через `plugins.conf`; пути, расширения и registered factory описаны в ARCHITECTURE. По умолчанию оба списка пусты. Native endpoint в эти списки не включается. Ошибка загрузки дополнительного endpoint диагностируется без остановки native. Плагины работают в одном процессе: разделение модулей не защищает сервер от аварии внутри библиотеки.

## Граница SQL

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

Профиль приложения изучается в P0. Поддержка wire-протокола сама по себе не делает совместимыми SQL и обращения к каталогам, которые генерирует драйвер или ORM. Эти зависимости фиксируются явно. Linux/Super — первая платформа проверки; SuperClassic использует общий проект host, но проверяется отдельно. Windows ABI учитывается сразу, Windows build/runtime требует собственной приёмки.

## Документы и состояние

База — `master` Firebird 6, рабочая ветка — `pg_wire_protocol`; точный SHA и исходники см. [BASELINE.md](BASELINE.md). Пакет дополнен 9 октября 2026 года с учётом замечаний review и уточнения границы транзакций. Реализация начата с P0 и конфигурационной части P1. Загрузка wire plugins, listener, auth bridge и SQL execution PgWire ещё не реализованы. Текущие решения — [IMPLEMENTATION_NOTES.md](IMPLEMENTATION_NOTES.md); фактически выполненные проверки — [VALIDATION.md](VALIDATION.md).

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
