# Задание на реализацию wire-плагинов Firebird

Этот файл — сохранённое описание задачи. Его чтение само по себе не является отдельным поручением; прямые инструкции пользователя имеют приоритет над приложенными планами и review.

Работать в `pg_wire_protocol` на базе `master` Firebird 6, указанной в BASELINE.md. Сохранить существующие изменения. Прочитать применимые AGENTS.md, затем README → BASELINE → ARCHITECTURE → CODEX_CONTEXT → COMPATIBILITY → PLAN → TEST_PLAN. RESEARCH/SOURCES содержат обоснование решений и исходники.

Реализовать общий host дополнительных wire-протоколов в штатном процессе `firebird` и первый загружаемый плагин `PgWire`. Родной протокол сохраняет текущий путь запуска и доступен независимо от дополнительных модулей. Использовать существующие PluginManager/CLOOP, а не отдельный loader. Добавить позиционные списки `WireProtocolPlugins` / `WireProtocolPorts`; точный синтаксис, defaults и ошибки заданы в ARCHITECTURE.

Общий host владеет TCP listeners, transport, ограниченными workers, authentication, attachment и shutdown. PG plugin — отдельная `.so`/`.dll`, содержащая framing, protocol state и codecs. Host objects и plugin library включаются в сборку отдельно; `dirObjects` не рекурсивен. Native PACKET/XDR и socket dispatch не используются для PG stream. SQL исполняется локальным engine provider без вторичного сетевого attach.

Не создавать SQL lexer/recognizer/classifier в frontend или host. Не искать даже первое ключевое слово, не разрезать statements, не удалять комментарии, не переписывать `$n`, не перехватывать `BEGIN`. Полный SQL передаётся parser движка. Нейтральные parser/DSQL metadata определяют исходную операцию, transaction action, retaining, session action, результат и EMPTY; PG tags формирует plugin. Legacy getType недостаточен для этого и должен сохранить прежнюю семантику. Новая PostgreSQL grammar/functions отложена.

Выполнять P0–P12 последовательно, фиксируя фактические результаты в VALIDATION.md. Сначала штатная сборка/native baseline, решения о ABI/lifecycle/engine metadata и TLS-зависимости. Профиль приложения исследовать рано; его служебный SQL и обращения к каталогам не маскировать фиктивным успехом.

Первый работающий сценарий — C libpq PQexec и psycopg2 с `autocommit=True`, без connection context. Обычный `BEGIN` и `$n` пока получают parser error. Extended Query проверять прямым libpq с нулём параметров и native `?`; не объявлять обычную параметризацию Psycopg 3 работающей до расширения parser. Совместимый DDL/DML, включая RETURNING, не ограничивать произвольным SQL allowlist.

Следовать CODEX_CONTEXT для I/T/E, native controls/savepoints, TPB, ownership интерфейсов, полного ISC status vector и codec metadata. В explicit failed block сохранять transaction для корректного ROLLBACK TO; не переносить старую стратегию немедленного полного rollback. Auth service общий для wire plugins; прямой вызов EDS ValidatePassword до attach невозможен. Providers ограничить локальным движком, сохраняя остальные database policies.

Исходный Python suite — scaffold для миграции, не актуальный gate из 56 обязательных случаев. TEST_PLAN задаёт новые ожидания и группы проверок. Синтетический driver_probe.json не доказывает выполнения запросов Firebird. Отдельно проверять native coexistence, plugin lifetime, malformed clients, отмену, внешнее завершение sessions и shutdown.

Для каждого законченного этапа сообщать, что реализовано, какими проверками подтверждено и что пока ограничивает следующий этап. Не объявлять M3 завершённым при незакрытых SQL-зависимостях выбранного приложения. Не коммитить автоматически без поручения пользователя.
