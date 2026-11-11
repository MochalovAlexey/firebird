# Следующий этап: pg_class для постоянных таблиц и представлений

Архитектурное решение от 11 ноября 2026 года. Статус: реализовано в `13f64d0e00`,
review без замечаний и независимая приёмка PASS;
[доказательства](../VALIDATION.md#11-ноября-2026-архитектурный-review-pg_class).
Следующий укрупнённый этап — [APP-13](PG_IDENTIFIER_NAMES_TASK.md).
Ниже сохранён исходный контракт задания.
Рабочий каталог: `/home/aleksey/dev/my_firebird/firebird`.
База — master Firebird 6, ветка pg_wire_protocol; проверенная ревизия `a3e3c80123`.
[BACKLOG](../BACKLOG.md): **APP-12**, ограниченная часть APP-01.
Не переключать ветку и не сбрасывать изменения ради указанной ревизии.

## Контекст и результат этапа

Сделай только SQL-view `PG_CATALOG.PG_CLASS(OID, RELNAME, RELNAMESPACE, RELKIND)`
для постоянных schema-level таблиц и представлений, installer, целевые проверки
и документацию. **Не выполнять git add, commit, amend, reset или checkout.**
Пользователь проверяет и коммитит сам. Следующую часть автоматически не начинать;
широкое задание `/home/aleksey/dev/1c/CODEX_PGWIRE_1C_TASK.md` целиком не выполнять.

Приоритет — минимальные изменения FirebirdSQL, максимальное переиспользование
существующих механизмов и ноль новых runtime-зависимостей. Здесь достаточно
устанавливаемых SQL-объектов: C++/parser/engine/public API/codecs не менять.
Не добавлять SQL rewriting или специальные ответы на catalog SELECT в плагине.

Прочитай status/HEAD, применимые инструкции, [README](README.md), [PROGRESS](PROGRESS.md),
[BACKLOG](../BACKLOG.md), начало [VALIDATION](../VALIDATION.md), APP-10/11 installers
и `pgwire_namespace_checks.py`. Источники engine: `src/jrd/relations.h`,
`src/jrd/constants.h` (rel_t), `RelationNode::generateRelId` в `src/dsql/DdlNodes.epp`,
документация schemas/local temporary/packaged temporary tables.

APP-10 хранит устойчивые namespace IDs; APP-11 предоставляет PG_NAMESPACE
с точными native именами и BIGINT/VARCHAR metadata. Оба прошли review/приёмку.
Теперь используем эти IDs как relnamespace и читаем реальные таблицы/views
из SYSTEM.RDB$RELATIONS. Новый реестр relation IDs, sequence и DDL trigger
**не нужны**: native relation ID уже существует и управляется движком.

Это часть пути к selected04 и lookup relation OID в selected10/11:

```sql
SELECT 1::INT8 FROM pg_catalog.pg_class WHERE pg_catalog.pg_table_is_visible(OID) AND relkind='r' AND relname='ibversion' AND relnamespace IN ('public'::regnamespace, pg_catalog.pg_my_temp_schema()) LIMIT 1
```

Этот исходный запрос целиком ещё не принимается. Правила PG/native имён,
regnamespace, visibility и temp namespace остаются отдельными задачами.
Порядок уточнён после APP-11: сначала доступные реальные catalog rows, затем
совместное решение об именах и разрешении объектов. Их не смешивать в этом патче.

## Контракт и SQL-архитектура

Минимальная основа определения:

```sql
CREATE VIEW PG_CATALOG.PG_CLASS(OID, RELNAME, RELNAMESPACE, RELKIND) AS
SELECT DISTINCT
    CAST(R.RDB$RELATION_ID + 1 AS BIGINT),
    CAST(TRIM(TRAILING FROM R.RDB$RELATION_NAME)
         AS VARCHAR(63) CHARACTER SET SYSTEM.UTF8),
    N.OID,
    CAST(CASE WHEN R.RDB$RELATION_TYPE = 1 THEN 'v' ELSE 'r' END
         AS VARCHAR(1) CHARACTER SET SYSTEM.UTF8)
FROM SYSTEM.RDB$RELATIONS R
JOIN PG_CATALOG.PG_NAMESPACE N
  ON N.NSPNAME = TRIM(TRAILING FROM R.RDB$SCHEMA_NAME)
WHERE R.RDB$PACKAGE_NAME IS NULL
  AND COALESCE(R.RDB$RELATION_TYPE, 0) IN (0, 1);
```

**Область строк:** native persistent relations (type 0, включая legacy NULL)
и views (1), без package qualifier. Включать подходящие SYSTEM и служебные
объекты, в том числе сам новый view; не фильтровать по владельцу/SELECT/USAGE.
Отсутствие SELECT права на таблицу не означает отсутствие её catalog row.
DISTINCT обеспечивает read-only view, в том числе для владельца.

**Идентификатор:** OID = native RDB$RELATION_ID + 1, с внешним CAST AS BIGINT.
SYSTEM.RDB$PAGES имеет native ID 0; отображение +1 исключает нулевой OID и
однозначно обратимо. Текущий native диапазон 0…32767 даёт OID 1…32768.
Это не ещё один allocator и не хеш. ID одинаков у разных attachments этой БД
и стабилен в течение существования объекта. ALTER не должен менять его.
При DROP строка исчезает; после CREATE используется текущий native ID,
который при reuse может совпасть с прежним.

Native allocator допускает wrap и повторное использование освобождённых IDs:
**не обещать вечную уникальность или строго возрастающую последовательность**,
не добавлять generation registry ради такого обещания. Проверка обычного
DROP+CREATE подтверждает конкретный lifecycle, не отсутствие будущего reuse.
После backup/restore/переноса БД стабильность этих чисел не заявлять без отдельной
приёмки. OID локален каталогу/базе, может численно совпасть с namespace ID.
Будущее включение indexes/sequences в тот же PG_CLASS потребует уникальных
ключей, не пересекающихся с принятым relation mapping; сейчас их не проектировать.
Будущие visibility и locks должны использовать этот же relation mapping;
advisory locks, их lifetime и сценарии DDL/reuse пока остаются APP-03.

**Имена:** RELNAME — точное native имя со снятым metadata CHAR padding,
без LOWER/UPPER, усечения, нормализации или скрытия коллизий. RELNAMESPACE —
ровно соответствующий PG_NAMESPACE.OID. PUBLIC/"public" и T/"t" различаются;
одинаковые имена таблиц в разных схемах дают разные OID и namespace references.
Сохраняются native unquoted labels OID/RELNAME/RELNAMESPACE/RELKIND.

**Типы:** native BIGINT/VARCHAR/BIGINT/VARCHAR, wire OIDs **20/1043/20/1043**.
RELKIND содержит ровно `r` или `v`, без padding. Используются существующие codecs,
text/binary results и SQL-derived parameter types. Это не PG oid/name/internal
"char" (OID 18); не подменять type identity по названию/provenance колонки.

[PostgreSQL pg_class](https://www.postgresql.org/docs/18/catalog-pg-class.html)
охватывает больше видов объектов и колонок. Здесь явно отсутствуют native
external/virtual relations (2/3), GTT (4/5), local/declared/packaged temporary
tables, indexes, sequences и остальные поля pg_class. Не выдавать их за обычные
постоянные таблицы и не создавать фиктивные rows/defaults. LTT хранят определение
в attachment, поэтому один RDB$RELATIONS не может представить их корректно.
Расширение этой области и PG temporary semantics — отдельная работа APP-01/04.
Это ограниченный catalog, не полный перечень всех объектов Firebird/PostgreSQL.

## Установка

Добавь отдельный `pg_wire_protocol/1c/sql/install_class_catalog.sql`:
SET BAIL ON, SET AUTODDL OFF, обычный CREATE VIEW, GRANT SELECT TO PUBLIC,
один финальный COMMIT. Все ссылки квалифицированы, работа не зависит от search_path.
Подключение и credentials задаются native isql, без путей/паролей в SQL-файле.

Предусловие: исправные APP-10/11 уже установлены, hook активен; владелец/администратор,
без конкурентного DDL. PG_CATALOG уже существует, повторно создавать schema или
переустанавливать APP-10/11/compat.sql не нужно. Нет preflight/repair/upgrade framework.
Отсутствие зависимости — обычный SQL error; отключение hook/порча registry вне
контракта, автоматическое обнаружение не обещать.

Существующий PG_CLASS — явный отказ: не RECREATE/CREATE OR ALTER. Коллизия или
ошибка до COMMIT не меняет прежние views, функции, grants и namespace mapping.
При rollback нового view допустим gap штатного relation allocator; не изменять
и не пытаться откатывать его. Namespace allocator этим этапом не выделяет ID.
Публичные USAGE PG_CATALOG и SELECT PG_NAMESPACE уже обеспечены APP-11;
новых прав на исходные пользовательские таблицы или DDL приложению не выдавать.

## Проверки и ограничения приёмки

Сначала сверить небольшой native prototype с текущим engine, затем добавить
целевой режим/проверки в прежний `pgwire_1c_smoke.py`, переиспользуя helpers
`pgwire_namespace_checks.py`. Не копировать все APP-10/11 suites и не строить
новый framework. Все runtime/security/fixtures — отдельные /tmp, рабочую 1С
и пользовательский сервер не изменять. Для SQL/harness-only diff достаточно
проверенной сборки с зафиксированными SHA; полный rebuild C++ не нужен.

Обязательная проверка:

- Fresh install после APP-10/11 при search_path=SYSTEM; отсутствующая зависимость,
  повтор, чужой PG_CLASS и искусственная ошибка перед COMMIT. Сохранность bootstrap,
  namespace mapping/counter, прежних объектов/прав; после ошибки нет частичного view.
- Native oracle по RDB$RELATIONS в установленной области: точные строки и четыре
  поля, уникальный положительный OID, нет потерянных namespace references.
  RDB$PAGES ID0→OID1, собственный PG_CLASS имеет kind `v`.
- Native/libpq Simple/Extended и named prepared lookup по ID/имени/namespace;
  text/binary результаты и metadata 20/1043/20/1043, 8-byte network-order IDs,
  точные UTF8 bytes. Alias/CAST не создают специальные PG-типы.
- Две схемы с одинаковым table name, quoted case/Unicode/пробелы/кавычка,
  таблица и view; namespace join не смешивает PUBLIC/"public" и T/"t".
  Диагностический запрос с relkind/relname/relnamespace/LIMIT возвращает правильные
  строки и отсутствие строки. Изменённый SQL не считать прохождением selected04.
- Native таблица/view без SELECT права и схема без USAGE присутствуют в catalog,
  но прямой доступ к данным по-прежнему запрещён. DML view отклоняется обычному
  пользователю и владельцу. READ ONLY lookup не меняет namespace registry/counter
  и состав/IDs native relation metadata.
- CREATE/ALTER/DROP TABLE/VIEW, COMMIT/full ROLLBACK: свой uncommitted row имеет
  настоящий ID; другой attachment не видит чужой uncommitted CREATE/DROP.
  ALTER сохраняет ID, prepared lookup после DROP/CREATE видит текущий объект;
  rollback не оставляет phantom rows. Сверять эффект с native metadata.
- GTT и packaged temporary table отсутствуют по явному фильтру; virtual relations
  исключены; LTT в отдельной fixture не создаёт фиктивную постоянную catalog row.
  Для external table достаточно безопасной metadata-only проверки в /tmp, если
  fixture доступна без изменения server config; неподтверждённое отметить SKIP.

Не добавлять тест, искусственно исчерпывающий 32767 relation IDs, не менять
системный allocator ради wrap. Полная APP-10 concurrency/restart матрица здесь
не нужна. Native DDL под user savepoint запрещён (0A000); не обходить и не
объявлять положительный DDL ROLLBACK TO. Known NAT-* crash repro не запускать.
При новом дефекте engine сохранить воспроизведение и границу, не расширять патч
исправлением ядра. Целевой сервер завершить штатно, проверить native coexistence.

## Документация и дальнейший путь

Обновить VALIDATION (revision/SQL/harness/runtime SHA, команды, PASS/FAIL/SKIP,
server exit), README/COMPATIBILITY/TEST_PLAN/PLAN/PROGRESS и APP-12 в BACKLOG.
Записать ограниченную область rows/columns, native names/types, relation-ID reuse
и непроверенный restore. Не закрывать APP-01/03/04/06 и selected04/10/11.

После этого нужен отдельный согласованный контракт PG/native name resolution
и regnamespace, затем search-path visibility и temp namespace. Простое LOWER
неоднозначно; наличие catalog row не означает pg_table_is_visible. Не расширять
parser/API или семантику имён для маскировки этих оставшихся задач в APP-12.
