# Незавершённые задачи PgWire и профиля 1С

Актуализировано 11 ноября 2026 года: APP-11 прошёл review в `a3e3c80123`; APP-12 реализован и оставлен на review.
Это единый список незакрытых работ и ограничений. [PLAN](PLAN.md) задаёт порядок,
[COMPATIBILITY](COMPATIBILITY.md) — действующий контракт,
[VALIDATION](VALIDATION.md) — доказательства. Подробности профиля — в [1c/README](1c/README.md).
Готовность к реальному открытию 1С и Гилёву — в [1c/PROGRESS](1c/PROGRESS.md).

## Как поддерживать список

- Перед следующим этапом сверять список с фактическим кодом и результатами.
  В задании указывать затрагиваемые ID; после реализации/review обновлять их статус.
- Частичная поддержка не закрывает всю возможность. Например, literal LIMIT
  закрывает SQL-01, но не SQL-02; успешный startup SET не закрывает его реальные эффекты.
- Закрытый пункт сохранять с датой, ревизией и ссылкой на проверки, не удалять.
  SKIP, неподтверждённый результат и синтетический пример не считать приёмкой
  исходного сценария приложения. Новое обнаруженное ограничение добавлять сюда.
- «Отложено пользователем» не означает отменено. «Условно» означает, что сначала
  нужно подтвердить необходимость в согласованном профиле; это не обязательство
  реализовать всю совместимость PostgreSQL. Отменённые решения перечислены отдельно.
- Перед объявлением завершения профиля пройти все строки: обязательные должны
  быть закрыты, остальные — явно оставлены ограничениями согласованного результата.
  Пользователь проверяет изменения и создаёт коммиты сам.

## SQL: оставшиеся части небольших этапов

| ID | Статус | Что осталось / условие закрытия | Основание |
|---|---|---|---|
| SQL-01 | Закрыто, 11.11.2026 | `ccd81f6473`: literal `LIMIT 0…2147483647`, native RowsClause; review без замечаний, независимый повтор libpq/raw/native/cache PASS. SQL-02 этим не закрыт. | [LIMIT_TASK](1c/LIMIT_TASK.md), [review](VALIDATION.md#11-ноября-2026-архитектурный-review-literal-limit) |
| SQL-02 | Открыто | LIMIT ALL/NULL, параметры/выражения count, более широкий диапазон и PG OFFSET. Учесть отличие native NULL count; не закрывать вместе с SQL-01. | [LIMIT_TASK](1c/LIMIT_TASK.md) |
| SQL-03 | Открыто | Повторы/пропуски/перестановки `$n`, согласование типов и сохранение Bind positions. Сейчас разрешены только уникальные последовательные номера; предел 32767 не подтверждает поддержку максимального engine message. | [NUMBERED_PARAMETERS_TASK](1c/NUMBERED_PARAMETERS_TASK.md) |
| SQL-04 | Открыто | Типизация параметров из Parse OIDs при недостаточном SQL-контексте. Сейчас `SELECT $1` даёт HY004 даже с OID=23; нужен отдельный минимальный проект и проверки. | [Параметры](1c/README.md#последовательные-numbered-parameters-первый-срез) |
| SQL-05 | Открыто | Допустимость новых PG-конструкций в DDL/CTAS/stored PSQL и остальных исключённых roots; корректное сохранение и native reprepare SQL. У `$n` дополнительно проверить перестановку input/output в CALL и другие DSQL transformations. PG SET внутри PSQL тоже пока исключён. | [Casts](1c/POSTFIX_CAST_TASK.md), [параметры](1c/NUMBERED_PARAMETERS_TASK.md), [1c/README](1c/README.md) |
| SQL-06 | Открыто по нужным типам | Postfix domain/schema/array/custom targets, необходимые aliases и отличия PG conversions/typmods. Сейчас `::` использует native CAST, добавлен только postfix INT8. | [POSTFIX_CAST_TASK](1c/POSTFIX_CAST_TASK.md) |
| SQL-07 | Отложено пользователем | Предварительный синтаксический разбор всего Simple Query. Сейчас ошибка останавливает остаток, но ранний COMMIT сохраняется при поздней syntax error. Вернуться отдельным решением, без автоматического расширения API. | [Решение о скриптах](1c/README.md#решение-от-9-ноября-2026-последовательные-скрипты-остановка-на-ошибке) |
| SQL-08 | Условно | E-literals, dollar quoting и другие требуемые отличия строк/грамматики PG. Сейчас действуют описанные native длины/сцепление и UTF8-профиль. Сначала подтвердить потребность. | [Строковый режим](1c/README.md#standard_conforming_strings-редакция-2-10-ноября-2026) |

## Настройки и транзакции

| ID | Статус | Что осталось / условие закрытия | Основание |
|---|---|---|---|
| SES-01 | Отложено пользователем | Реальное действие enable_mergejoin, включая значения и применение. Сейчас SET успешен, значение не сохраняется и optimizer не меняется. | [IGNORED_STARTUP_SETTINGS_TASK](1c/IGNORED_STARTUP_SETTINGS_TASK.md) |
| SES-02 | Отложено пользователем | Реальное действие cpu_operator_cost. Сейчас SET успешен без проверки PG-типа/диапазона и без изменения планирования. | [IGNORED_STARTUP_SETTINGS_TASK](1c/IGNORED_STARTUP_SETTINGS_TASK.md) |
| SES-03 | Отложено пользователем | Реальный lock_timeout: единицы, значение, ожидания блокировок, ошибки и lifetime. Сейчас `20000` не задаёт 20 секунд; значение игнорируется. | [IGNORED_STARTUP_SETTINGS_TASK](1c/IGNORED_STARTUP_SETTINGS_TASK.md) |
| SES-04 | Отложено пользователем | Модель прав на lc_messages. Сейчас фиксированное значение разрешено любому пользователю; отдельного PG permission check нет. | [Фиксированный SET](1c/README.md#фиксированный-профиль-set-английские-сообщения-и-utf8) |
| SES-05 | Условно | Другие значения/эффекты lc_messages, client_encoding, escape_string_warning, client_min_messages и доставка INFO/NOTICE/WARNING. Сейчас только фиксированный English/UTF8/error/off профиль. | [FIXED_DIAGNOSTICS_TASK](1c/FIXED_DIAGNOSTICS_TASK.md), [COMPATIBILITY](COMPATIBILITY.md) |
| SES-06 | Условно | SET LOCAL/SESSION/DEFAULT, SHOW, RESET отдельной настройки и дополнительные startup options, с настоящим состоянием и lifetime по нужному профилю. | [1c/README](1c/README.md) |
| SES-07 | Открыто по профилю | PG BEGIN options, default_transaction_* и необходимые отличия isolation/locks/DDL semantics. Сейчас BEGIN без опций использует native READ CONSISTENCY; полная эквивалентность PG не заявляется. | [BEGIN_TASK](1c/BEGIN_TASK.md) |
| SES-08 | Исключено из текущей приёмки | История standard_conforming_strings при скрытых границах TPB AUTO COMMIT и RESET внутри PSQL. Прямой OO rollback намеренно не восстанавливает bool: PG lifetime обеспечивает плагин. Не обещать эти сочетания без отдельного решения и проверки. | [Строковый режим](1c/README.md#standard_conforming_strings-редакция-2-10-ноября-2026), NAT-02/03 |

## Прикладной профиль 1С

| ID | Статус | Что осталось / условие закрытия | Основание |
|---|---|---|---|
| APP-01 | Открыто | Настоящие pg_class/pg_database/namespaces/visibility, current_database, pg_my_temp_schema и требуемые catalog queries. Согласованные объекты, данные и права; не пустые успешные ответы. | [Следующие этапы 1С](1c/README.md#следующие-небольшие-этапы) |
| APP-02 | Открыто | bytea/BLOB, mchar/mvarchar, regnamespace, BINROWVER и используемые функции/агрегаты; форматы, сравнения, сортировка, metadata и codecs. Output закрыт в APP-07, input ≤65533 реализован в APP-08, большие значения — APP-09; SQL types/casts/comparisons/index semantics ими не закрываются. | [Профиль 1С](1c/README.md), [COMPATIBILITY](COMPATIBILITY.md) |
| APP-03 | Открыто | Shared/exclusive advisory locks и их transaction/session lifetime; реальные конфликты между соединениями. Таймаут настройки отдельно в SES-03. | [Профиль 1С](1c/README.md) |
| APP-04 | Открыто | PG temporary DDL/pg_temp, serial4, WITHOUT OIDS, ANALYZE и fasttrun_ext; сначала установить требуемые реальные эффекты. | [Профиль 1С](1c/README.md) |
| APP-05 | Открыто | Чтение настоящих служебных таблиц/конфигурации и DML на исходной схеме/типах 1С. Синтетический поиск и 92 параметра этого не подтверждают. | [NUMBERED_PARAMETERS_TASK](1c/NUMBERED_PARAMETERS_TASK.md), [VALIDATION](VALIDATION.md) |
| APP-06 | Открыто | Сквозной сценарий 1С/Гилёва с зафиксированными версиями, настройками и фактической библиотекой клиента. Открытие базы и нагрузка ещё не приняты. | [Профиль 1С](1c/README.md), P11/P12 в [PLAN](PLAN.md) |
| APP-07 | Закрыто, 11.11.2026 | `5478a589d8`: subtype 0 → bytea OID 17 через IBlob, text/binary, общий DataRow budget, lifetime/cleanup. Review без замечаний; независимые libpq/raw/native/unit и scalar codecs PASS. InputCodec/engine/API/dependencies прежние; APP-02/SQL-06 остаются открыты. | [BINARY_BLOB_OUTPUT_TASK](1c/BINARY_BLOB_OUTPUT_TASK.md), [review](VALIDATION.md#11-ноября-2026-архитектурный-review-binary-blob-output) |
| APP-08 | Закрыто, 11.11.2026 | `c27a3a4c5a`: bytea input → native BLOB через VARCHAR OCTETS/MOVE, ≤65533 bytes, layout конкретного Bind. Review без замечаний; свежая сборка, 9 unit executables, input 125/output 92 libpq checks и raw/native PASS. Production только InputCodec.cpp/h; engine/API прежние. | [BINARY_BLOB_INPUT_TASK](1c/BINARY_BLOB_INPUT_TASK.md), [review](VALIDATION.md#11-ноября-2026-архитектурный-review-bytea-input) |
| APP-09 | Открыто | Входные BLOB больше 65533 decoded bytes: выбрать отдельный native путь с корректным transaction/portal lifetime и bounded memory. Не повышать общие лимиты вместо решения. APP-08 этого не закрывает; выходной предел APP-07 остаётся общим DataRow budget. | [BINARY_BLOB_INPUT_TASK](1c/BINARY_BLOB_INPUT_TASK.md) |
| APP-10 | Закрыто, 11.11.2026 | `5bbdaa763d`: private namespace IDs в PGWIRE_COMPAT, одноразовый SQL installer, sequence/read-only view/definer hook. Review без замечаний, независимые native/libpq, права, rollback, concurrency/restart PASS. Native DDL под user savepoint запрещён (0A000): refusal/recovery проверены, положительный schema-DDL undo через ROLLBACK TO не заявлен. C++/API прежние; APP-01 и selected04 не закрыты. | [NAMESPACE_REGISTRY_TASK](1c/NAMESPACE_REGISTRY_TASK.md), [review](VALIDATION.md#11-ноября-2026-архитектурный-review-namespace-registry) |
| APP-11 | Закрыто, 11.11.2026 | `a3e3c80123`: SQL-only PG_NAMESPACE над APP-10, точные IDs/native имена, read-only/atomic install. Review без замечаний; независимый PASS: 6 групп установки, 4 native/libpq группы, 128 results и normal shutdown. Native BIGINT/VARCHAR, wire20/1043; PG oid/name/regnamespace и nspowner/nspacl не добавлены. | [NAMESPACE_CATALOG_TASK](1c/NAMESPACE_CATALOG_TASK.md), [review](VALIDATION.md#11-ноября-2026-архитектурный-review-публичного-namespace-catalog) |
| APP-12 | Реализовано, на review | SQL-only PG_CLASS(OID, RELNAME, RELNAMESPACE, RELKIND) для schema-level persistent tables/views. Native relation ID+1, JOIN PG_NAMESPACE, wire20/1043/20/1043, без нового allocator/hook. PASS: 5 групп установки, 4 native/libpq группы, 228 results, exclusions/права/lifecycle и normal shutdown. Reuse ID допустим, restore не проверен; полный catalog/visibility/temp и selected04/10/11 открыты. | [CLASS_CATALOG_TASK](1c/CLASS_CATALOG_TASK.md), [проверки](VALIDATION.md#11-ноября-2026-pg_class-для-постоянных-таблиц-и-представлений) |

Для APP-01 исследование выявило отдельные архитектурные вопросы: RDB$SCHEMAS
не содержит числового schema ID; простое LOWER(native name) неоднозначно при
совместном существовании quoted/unquoted имён. Native default collation label
также не является PostgreSQL LC_COLLATE. Стабильные native namespace IDs приняты в APP-10; правила PG имён
и collation contract нужно решить до объявления совместимости catalog queries.
Ограниченный публичный view реализован в APP-11 с точными native именами
и native metadata, без заявления полной PG-совместимости. Остаются PG/native
name resolution/column labels, SQL/wire types oid/name/regnamespace, nspowner/nspacl,
полный pg_class/visibility/temp namespace и current_database (native DB_NAME может быть путём,
а не Startup database alias). Работа с отключённым DDL hook, upgrade/uninstall/
recovery registry не входят в APP-10; lookup ничего не исправляет автоматически.
Native DDL под user savepoint (0A000) также остаётся ограничением: APP-10 его не
исправляет. Metadata provenance не заменяет type identity: native UNION сохраняет
origin первой ветви при общем BIGINT, input parameters не имеют column origin.
Не выводить PG oid/name из schema/relation/field или aliases; probe и решение
сохранены в [review](VALIDATION.md#11-ноября-2026-архитектурный-review-namespace-registry).
APP-01 не закрыт.

Реализованный APP-12 использует native relation IDs с обратимым +1, без второй
таблицы identity. Native allocator допускает reuse после DROP/wrap; стабильность
при backup/restore пока не проверена. Полный pg_class (external/virtual/temp/package
objects, indexes/sequences и остальные columns) не входит в этот срез. Для APP-03
отдельно проверить взаимодействие advisory keys с DDL/ID reuse; name resolution,
visibility и temporary schemas остаются APP-01/04. Эти ограничения не снимаются
успехом чтения четырёх полей постоянных tables/views.
Будущие indexes/sequences внутри PG_CLASS требуют непересекающихся с native
relation mapping ключей; их native ID сам по себе может не быть глобально уникальным.

## Драйверы, wire и платформы

| ID | Статус | Что осталось / условие закрытия | Основание |
|---|---|---|---|
| DRV-01 | Открыто | DEALLOCATE ALL и whole-cache cleanup драйвера после rollback; сейчас rollback выполнен, но последующий SQL получает 42601. Отдельно проверить DROP/ALTER и stale cache после native RESET. | [P9](COMPATIBILITY.md#p9-клиентский-профиль-и-подтверждённые-ограничения) |
| WIRE-01 | P10, не реализовано | Удалённый TCP, конфигурируемый bind, TLS/SSLRequest, сертификаты и libpq verify-full; приёмка с другого узла/network namespace. | P10 в [PLAN](PLAN.md) |
| WIRE-02 | P10, не реализовано | BackendKeyData/CancelRequest, связь с session и настоящая engine cancellation, stale keys/races/lock wait. Ограниченный pg_backend_pid не является cancel key или cluster-wide ID. | P10 в [PLAN](PLAN.md), [identity](1c/README.md#первый-срез-select-без-from-и-идентификация-подключения) |
| WIRE-03 | Открыто | Windows wiring/build/runtime и поддержка SuperClassic/Classic. Сейчас подтверждён только Linux/Super; наличие plugin ABI не закрывает платформенную приёмку. | [COMPATIBILITY](COMPATIBILITY.md#отдельные-расширения-и-ограничения) |
| WIRE-04 | Условно | COPY, pipeline, SQL named cursors, large-object API, LISTEN/NOTIFY, replication; protocol 3.2/direct TLS/GSS/SCRAM/MD5/channel binding. Отдельные contracts только при потребности профиля. | [COMPATIBILITY](COMPATIBILITY.md#отдельные-расширения-и-ограничения) |
| WIRE-05 | Условно | Остальные codecs (ARRAY/OCTETS/DECFLOAT/timezone и custom types), remote security DB, encryption-key callbacks и немедленная отмена SQL при client EOF. Сейчас EOF во время engine call обнаруживается при следующем I/O; отдельного watcher нет. | [COMPATIBILITY](COMPATIBILITY.md#отдельные-расширения-и-ограничения) |

## Проверки и известные дефекты

| ID | Статус | Что осталось / условие закрытия | Основание |
|---|---|---|---|
| VAL-01 | SKIP | Live-повтор Psycopg 3 с уже реализованными BEGIN, typed `$n`, `::date` и named preparation. В текущем окружении клиент отсутствует; обновление теста не является PASS. | [VALIDATION](VALIDATION.md), [P9](COMPATIBILITY.md#p9-клиентский-профиль-и-подтверждённые-ограничения) |
| VAL-02 | Открыто | Актуализировать или явно вывести из действующей приёмки исторический P3 с устаревшими отказами VARCHAR/NUMERIC/DML. Не менять корректный engine ради старого отрицательного ожидания; не скрывать FAIL. | [VALIDATION](VALIDATION.md), tools/pgwire_query_smoke.py |
| VAL-03 | Не проверено | Отдельная database dialect 1 для проверки BIGINT helper; проверка client dialect 1 на dialect-3 БД уже есть и не заменяет её. | [Postfix cast validation](VALIDATION.md) |
| VAL-04 | Частично проверено; открыто | APP-07: ABI unit injection ошибок BLOB open/read/close и consuming close/release PASS. Реальные engine BLOB I/O faults не инжектировались. Auth-plugin preemption и остальные close/free/commit faults не проверены; unit mocks и обычный shutdown не заменяют live-проверки. | [TEST_PLAN](TEST_PLAN.md), [VALIDATION](VALIDATION.md) |
| VAL-05 | P12, не принято | Финальный M3: реальный прикладной профиль по удалённому TCP с TLS/cancellation, native coexistence, permissions, cross-visibility, concurrency и cleanup. Сверить все открытые ID и ограничения, прежде чем объявлять завершение. | P12 в [PLAN](PLAN.md) |
| NAT-01 | Отдельный baseline defect | Shutdown во время ON CONNECT: assertion alloc.cpp `!child`, воспроизводился также native-only. По решению пользователя исключён из P6, не исправлен. | [TEST_PLAN](TEST_PLAN.md), [VALIDATION](VALIDATION.md) |
| NAT-02 | Отдельный baseline defect | READ COMMITTED READ CONSISTENCY + AUTO COMMIT: assertion Savepoint.cpp:833. Успех SNAPSHOT AUTO COMMIT не закрывает это сочетание. | [COMPATIBILITY](COMPATIBILITY.md#семантика-результатов-и-транзакций) |
| NAT-03 | Отдельный baseline defect | RESET внутри PSQL после SAVEPOINT: PgWire Debug assertion ExtDS.cpp:2021 и на исходной сборке; embedded вариант даёт 01002. Не смешивать с обычным ALTER SESSION RESET. | [VALIDATION](VALIDATION.md) |
| NAT-04 | Отдельный baseline defect | Внутренний SAVEPOINT через EXECUTE STATEMENT: assertion на исходной и изменённой сборках. Положительный вложенный SELECT этот сценарий не покрывает. | [VALIDATION](VALIDATION.md), [строковый режим](1c/README.md#standard_conforming_strings-редакция-2-10-ноября-2026) |
| OPS-01 | При наличии старых объектов | Ранее неверно сериализованный SQL автоматически не исправлен. Такие объекты пересоздать из исходного SQL; исправление новых сохранений не считать миграцией старых. | [STRINGS_SOURCE_FIX_TASK](1c/STRINGS_SOURCE_FIX_TASK.md), [1c/README](1c/README.md) |

Known crash repro не запускать в обычной регрессии; исправления NAT-* выполнять
отдельными согласованными этапами. Этот список сохраняет дефекты, но не отменяет
решение оставить их за пределами текущей реализации wire/SQL-среза.

## Отменённое и не входящее в обязательную цель

Не возвращать автоматически в работу собственные сетевые/message/auth таймауты,
публичные типы операций/`isc_info_sql_stmt_origin` ради PgWire, `getPluginConfig2`
или обязательную полную поддержку pgcli: пользователь отказался от этих решений.
psql принят как CLI для ручной проверки. Новые MySQL/MSSQL wire plugins и полная
совместимость со всеми PostgreSQL-приложениями не являются текущей задачей.
