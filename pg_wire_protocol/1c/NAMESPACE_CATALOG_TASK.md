# Следующий этап: публичное представление namespace IDs

Архитектурное решение от 11 ноября 2026 года. Статус: реализовано в `a3e3c80123`,
review без замечаний и независимая приёмка PASS;
[доказательства](../VALIDATION.md#11-ноября-2026-архитектурный-review-публичного-namespace-catalog).
Ниже сохранён исходный контракт задания.
Рабочий каталог: `/home/aleksey/dev/my_firebird/firebird`.
База — master Firebird 6, ветка pg_wire_protocol; проверенная ревизия `5bbdaa763d`.
[BACKLOG](../BACKLOG.md): **APP-11**, следующая ограниченная часть APP-01.
Не переключать ветку и не сбрасывать изменения ради указанной ревизии.

## Контекст и граница этапа

Сделай только устанавливаемое SQL-представление
`PG_CATALOG.PG_NAMESPACE(OID, NSPNAME)` над принятым private registry APP-10,
его целевые проверки и документацию. **Не выполнять git add, commit, amend,
reset или checkout.** Пользователь проверяет и коммитит сам. Следующий этап
автоматически не начинать; широкое задание 1С целиком не выполнять.

Приоритет — минимум изменений FirebirdSQL, максимальное переиспользование
штатных возможностей, без новых зависимостей. Production C++, parser, engine,
public API и codecs не меняются. Не добавлять SQL rewriting или распознавание
catalog SELECT в плагине: запросы исполняет обычный Firebird SQL engine.

Прочитай status/HEAD, применимые инструкции, [README](README.md),
[PROGRESS](PROGRESS.md), [BACKLOG](../BACKLOG.md), начало [VALIDATION](../VALIDATION.md),
[APP-10](NAMESPACE_REGISTRY_TASK.md), его installer и проверки. Существующий
[compat.sql](compat.sql) уже создаёт PG_CATALOG и PG_BACKEND_PID; их сохранить.

APP-10 в `5bbdaa763d` прошёл review и независимую native/libpq приёмку:
стабильные IDs точных native schema names, transactional DDL hook, права,
конкуренция и restart. Его таблицу, sequence и trigger повторно не создавать.
Новый view — публичная SQL-точка чтения этих же реальных данных для будущих
каталогов. Это **не полная реализация PostgreSQL pg_namespace** и ещё не выполнение
selected04 из трассы 1С:

```sql
SELECT 1::INT8 FROM pg_catalog.pg_class WHERE pg_catalog.pg_table_is_visible(OID) AND relkind='r' AND relname='ibversion' AND relnamespace IN ('public'::regnamespace, pg_catalog.pg_my_temp_schema()) LIMIT 1
```

## Контракт представления

Минимальное определение, с квалифицированными ссылками:

```sql
CREATE VIEW PG_CATALOG.PG_NAMESPACE(OID, NSPNAME) AS
SELECT DISTINCT NAMESPACE_ID, NATIVE_NAME
FROM PGWIRE_COMPAT.NAMESPACES;
```

- OID — тот же native BIGINT и тот же ID из APP-10, без второй нумерации,
  хеширования, констант или allocation при чтении.
- NSPNAME — точное native имя без LOWER/UPPER, усечения или скрытия коллизий.
  PUBLIC и quoted "public" остаются разными строками. Длинное допустимое Unicode
  имя возвращается целиком. Нативное разрешение SQL-идентификаторов и labels
  колонок пока сохраняется: unquoted OID/NSPNAME не становятся lowercase labels.
- Все строки registry видны через view; это перечень namespace identity,
  а не проверка USAGE схемы или видимости таблиц по search_path.
- DISTINCT обеспечивает read-only view, включая владельца. PUBLIC получает
  только USAGE PG_CATALOG и SELECT нового view. Новые права CREATE SCHEMA,
  запись в registry или управление sequence/trigger приложению не выдавать.
- Видимость и rollback — штатные в текущей native transaction. Нет кэша,
  дублированного хранилища свойств схемы или отдельного lifecycle в плагине.

### Типы и явные отличия от PostgreSQL

В этом срезе wire metadata соответствует реальным native типам:
OID-колонка — **BIGINT / type OID 20**, NSPNAME — **VARCHAR / type OID 1043**.
Существующие codecs задают text/binary representation и размеры. Название
колонки OID само по себе не означает SQL-тип PostgreSQL oid.

[PostgreSQL pg_namespace](https://www.postgresql.org/docs/18/catalog-pg-namespace.html)
использует типы oid/name и также имеет nspowner/nspacl. Эти типы и две дополнительные
колонки здесь **не реализуются**; не подставлять фиктивные значения владельца/ACL.
Настоящий [oid/regnamespace](https://www.postgresql.org/docs/18/datatype-oid.html)
требует отдельного решения о SQL-типизации, вводе/выводе и именах.

Не выводить PG type identity из IMessageMetadata schema/relation/field или alias.
На текущем engine проверено: `SELECT OID FROM PG_CATALOG.PG_NAMESPACE UNION ALL
SELECT CAST(4294967296 AS BIGINT) FROM SYSTEM.RDB$DATABASE` сохраняет происхождение
первой колонки PG_NAMESPACE.OID для общего BIGINT результата. Перестановка ветвей
меняет provenance, но не тип; input parameters этих имён вообще не получают.
Подмена OID 20 на 26 по происхождению нарушит контракт выражений. Проверка диапазона
не исправляет ошибочный тип. Не решать это дополнительным SQL lexer или API.
Доказательство и путь native probe сохранены в VALIDATION.

## Установка

Добавь отдельный native isql script, например
`pg_wire_protocol/1c/sql/install_namespace_catalog.sql`:

1. `SET BAIL ON`, `SET AUTODDL OFF`; подключение/credentials вне SQL-файла.
2. Предусловие: APP-10 уже установлен, не повреждён и его hook активен.
   Отсутствующий registry даёт обычную ошибку зависимости и rollback.
   Исправный активный hook — административное предусловие, автоматической проверки
   его отключения не обещать: bypass/corruption вне контракта APP-10. Устанавливать,
   чинить registry или добавлять preflight framework не нужно. Установка
   владельцем/администратором, без конкурентного DDL.
3. `CREATE SCHEMA IF NOT EXISTS PG_CATALOG`, как в compat.sql. Существующая
   schema — ожидаемый bootstrap container: не RECREATE, не менять owner, не
   удалять или заменять её функции. Если schema создаётся впервые, действующий
   APP-10 hook регистрирует её в этой же transaction.
4. Обычный `CREATE VIEW`, grants, один COMMIT после полного успеха. Если
   PG_NAMESPACE уже существует — явный отказ, без RECREATE/CREATE OR ALTER.
   Повторная установка не upgrade и не меняет прежние IDs, counter или объекты.

Сохранить независимость от текущего search_path. Проверить atomic rollback
ошибки перед COMMIT: нет частичного view/grants; созданная этим запуском schema
и её registry row откатываются вместе. Sequence gaps после rollback допустимы
по APP-10, поэтому не требовать возврата counter при неудачном первом CREATE.
Если PG_CATALOG существовал, его старые объекты и данные сохраняются.

## Реализация и проверки

Переиспользовать `pgwire_1c_smoke.py`, `pgwire_namespace_checks.py`, native isql
и настоящий libpq. Добавить небольшой режим/набор проверок в существующий harness,
без нового framework и копии всего APP-10 suite. Все базы/runtime/security DB
отдельные в /tmp; рабочие серверы и исходную 1С не изменять.

Проверить:

- Установку после APP-10 с существующим bootstrap PG_CATALOG/PG_BACKEND_PID и
  отдельно без PG_CATALOG. После успеха view точно равен private mapping, IDs
  старых схем прежние, новая PG_CATALOG появляется ровно один раз через hook.
- Установку без APP-10, повторный запуск, коллизию с существующим PG_NAMESPACE
  и ошибку до COMMIT. Отказы оставляют согласованные объекты/права/mapping;
  чужой одноимённый объект сохраняется. Не вводить repair/version framework.
- Native и libpq видят одинаковые IDs/имена: PUBLIC/"public", mixed case,
  Unicode длиннее 63 UTF8 bytes, пробел/кавычка. Отсутствующее имя — ноль строк.
- Simple Query и Extended/prepared lookup по ID/имени с SQL-derived параметрами,
  text и binary results. Сверить реальные bytes/metadata: OIDs 20/1043, BIGINT
  в binary — 8 bytes. Не подавать OID 26/19 и не объявлять их поддержанными.
- Alias, derived SELECT, CAST и UNION с native BIGINT `4294967296` в обоих
  порядках ветвей: тип остаётся 20, значение передаётся без uint32 truncation.
  Это проверка отсутствия скрытой привязки codec к названию catalog-колонки.
- CREATE/ALTER/DROP пользовательской schema, COMMIT/full ROLLBACK и второй
  attachment: view отражает ровно те изменения, что private registry, с прежней
  native transaction visibility. DROP+CREATE меняет lifetime ID, ALTER сохраняет.
  Полную матрицу вариантов DDL/concurrency/restart повторять не нужно: она APP-10.
- Обычный пользователь читает view без новых base grants; DML view отклоняется
  и у владельца. Повторное чтение в read-only transaction не меняет counter.
  Права/definition существующего PG_BACKEND_PID не повреждены, функция работает.

Native DDL под user savepoint запрещён до DDL trigger (0A000). Не обходить этот
guard, не обещать положительный DDL ROLLBACK TO и не запускать known NAT-* crash
repro. При новом дефекте движка записать минимальное воспроизведение и границу,
не расширять этот этап исправлением ядра.

Для SQL/harness-only изменения использовать проверенный runtime с записью SHA;
повторная сборка C++ и все старые suites не нужны. Целевой тест завершается
штатным shutdown; сохранить commands, revision, SQL/harness/runtime SHA,
PASS/FAIL/SKIP и server exit. /tmp из текущего review могут исчезнуть: методику
воспроизвести существующими инструментами, не объявлять отсутствующий PASS.

## Результат этапа

Обновить VALIDATION, README, COMPATIBILITY, TEST_PLAN, PLAN, PROGRESS и APP-11
в BACKLOG. Явно различать принятые namespace values/native metadata и ещё
отсутствующие PG oid/name/regnamespace, name mapping, pg_class/visibility/temp
namespace, nspowner/nspacl. APP-01/APP-02/APP-06 и selected04 остаются открытыми.

Дальнейший путь: согласовать правило PG/native имён и namespace resolution;
затем реальный pg_class с согласованным relnamespace и search-path visibility,
после этого неизменённый selected04. Не подменять visibility наличием строки
или SELECT privilege. Простое LOWER неоднозначно при PUBLIC/"public". Новое
решение о типах/API и эти следующие части в текущую реализацию не включать.
