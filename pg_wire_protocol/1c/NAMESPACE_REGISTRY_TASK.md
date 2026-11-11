# Следующий этап: устойчивые идентификаторы native schemas

Архитектурное решение от 11 ноября 2026 года. Статус: задание на реализацию.
Рабочий каталог: `/home/aleksey/dev/my_firebird/firebird`.
База — master Firebird 6, ветка pg_wire_protocol, ревизия исследования `c27a3a4c5a`.
[BACKLOG](../BACKLOG.md): **APP-10**, первая часть APP-01. Не переключать ветку
и не сбрасывать изменения ради этой ревизии.

## Контекст и цель

Сделай только SQL-основу будущих каталогов: постоянное соответствие точного
native schema name и локального числового namespace ID, установку и проверки.
**Не выполнять git add, commit, amend, reset или checkout.** Пользователь
проверяет и коммитит сам. Следующие части APP-01 автоматически не начинать.

Приоритет — минимальные изменения FirebirdSQL и максимальное переиспользование
штатных механизмов, без новых зависимостей. Production C++/parser/API/PgWire
на этом этапе менять не требуется: используются обычные таблица, генератор
identity/sequence, view и DDL trigger. Это устанавливаемые SQL-объекты отдельной
базы с совместимостью, а не новый обязательный системный каталог Firebird.

Прочитай status/HEAD, применимые инструкции, [README](README.md),
[PROGRESS](PROGRESS.md), [BACKLOG](../BACKLOG.md), начало [VALIDATION](../VALIDATION.md)
и native `doc/sql.extensions/README.schemas.md`. Широкое задание
`/home/aleksey/dev/1c/CODEX_PGWIRE_1C_TASK.md` целиком не выполнять.

Транспорт, базовые SQL/транзакции и ограниченный bytea input/output уже работают.
Следующий блокер открытия базы — selected04:

```sql
SELECT 1::INT8 FROM pg_catalog.pg_class WHERE pg_catalog.pg_table_is_visible(OID) AND relkind='r' AND relname='ibversion' AND relnamespace IN ('public'::regnamespace, pg_catalog.pg_my_temp_schema()) LIMIT 1
```

Для relnamespace и regnamespace нужен один согласованный ID схемы во всех
соединениях. `RDB$SCHEMAS` содержит настоящие имена/свойства схем, но не числовой
ID (`src/jrd/relations.h`, relation 56). Хеш, номер строки, RDB$DB_KEY или suffix
security class не заменяют контракт устойчивой уникальной идентичности.

Этот этап **ещё не выполняет selected04**. Он предоставляет необходимую основу.
pg_class, публичный pg_namespace, PG name mapping, regnamespace cast,
search-path visibility, pg_my_temp_schema и wire type oid остаются APP-01.
SQL bytea types, большие input BLOB, mchar/mvarchar и BINROWVER сюда не входят.

## Контракт и архитектура

### Хранение

1. Отдельная служебная schema `PGWIRE_COMPAT`, установленная владельцем/администратором
   только в выбранную базу. Не создавать её автоматически при каждом attach.
2. Одна таблица соответствий, например `PGWIRE_COMPAT.NAMESPACE_IDS`:
   точное native имя — уникальный ключ, числовой ID — уникальный NOT NULL BIGINT
   с CHECK `1 <= ID AND ID <= 4294967295`. Ноль не выдавать; зафиксировать SQL
   имена колонок, например NATIVE_NAME и NAMESPACE_ID.
3. Штатная identity либо одна sequence выдаёт IDs; выбрать меньший корректный
   вариант. Пропуски допустимы. Не сбрасывать счётчик, не переиспользовать ID
   после DROP и не копировать PostgreSQL OID 11/2200 из эталонной базы.
4. Сравнение имён регистрозависимое, соответствует native metadata. Учитывать
   служебное padding CHAR, но не применять LOWER/UPPER к смысловому имени.
   Native PUBLIC и quoted "public" должны иметь разные записи и IDs.
5. Приватное read-only представление `PGWIRE_COMPAT.NAMESPACES` выдаёт только
   NATIVE_NAME/NAMESPACE_ID. Разрешить прикладному пользователю SELECT/USAGE,
   без INSERT/UPDATE/DELETE базовой таблицы и управления sequence/DDL trigger.
   Проверить native grants/owner semantics, а не предполагать права view.

Registry хранит только identity, не копию всех свойств RDB$SCHEMAS. Lookup —
обычный SELECT, пригодный для read-only transaction. Отсутствующее имя даёт
отсутствующую строку; чтение никогда не вставляет ID и не исправляет registry.
BIGINT здесь — внутреннее хранилище, не заявление поддержки wire PostgreSQL
oid (OID 26) или regnamespace. Эти типы требуют отдельного следующего решения.

### Установка

Минимальный одноразовый native isql script, например
`pg_wire_protocol/1c/sql/install_namespace_registry.sql`:

- `SET BAIL ON`, `SET AUTODDL OFF`; подключение задаётся аргументами isql,
  без credentials/путей к рабочей базе в файле.
- В начале `CREATE SCHEMA PGWIRE_COMPAT` **без** IF NOT EXISTS. Существующая
  schema — явный отказ до изменений. Повторный запуск намеренно не является
  upgrade: он завершается ошибкой и сохраняет объекты/IDs/счётчик.
- Создать таблицу/allocator/view/права и заполнить из настоящего RDB$SCHEMAS,
  включая уже существующие схемы и саму служебную schema; установить trigger.
  Все ссылки на system/служебные объекты квалифицировать.
- Один COMMIT после полной успешной установки. Проверить rollback при ошибке
  до COMMIT. Установку выполнять при отсутствии конкурентного DDL; не добавлять
  онлайн-миграцию/repair/version framework ради этого этапа.

Это осознанный контракт для маленького этапа. Upgrade/uninstall и повреждение
служебных объектов администратором пока не автоматизируются. Не использовать
RECREATE/DELETE/reseed для повторного запуска и не менять чужую существующую
schema PGWIRE_COMPAT, даже если её имя совпало с выбранным служебным именем.

### Поддержание identity

Использовать существующий `AFTER ANY DDL STATEMENT` trigger с проверенными
правами definer. В начале отфильтровать `DDL_TRIGGER.OBJECT_TYPE = 'SCHEMA'`;
для остального DDL сразу выйти. Имена schema для этих событий берутся из
**DDL_TRIGGER.OBJECT_NAME**, не SCHEMA_NAME: schema hooks передают QualifiedName
без внешнего schema qualifier (`src/dsql/DdlNodes.epp`, schema nodes).

- CREATE — новая строка и новый ID.
- ALTER — прежний ID; менять registry не требуется.
- DROP — удалить строку; повторный CREATE того же имени получает другой ID.
- Все действия — в исходной транзакции DDL; без autonomous transaction.
  Ошибка обновления registry должна откатить DDL, а не оставлять рассогласование.
- Существующие CREATE OR ALTER/IF NOT EXISTS/RECREATE/DROP IF EXISTS должны
  следовать фактическим событиям движка; не определять действие разбором SQL_TEXT.

Пользователь с native правом создать/изменить/удалить свою schema не должен
получать отказ только из-за отсутствия прямых прав на служебную таблицу.
Для этого проверить штатный SQL SECURITY DEFINER/необходимые grants.
Не давать всем пользователям прямую запись в registry.

Вне контракта: DDL с отключёнными triggers, `-nodbtriggers`, ручная порча allocator/
таблицы, удаление служебной schema и перенос части её объектов отдельно от базы.
После пропущенного DROP+CREATE одноимённой schema восстановление только по именам
не может доказать lifetime ID. Записать ограничение; не маскировать его выдачей
нового ID при SELECT. Резервные копии/restore/migration отдельно не заявлять без
проверки. Обычный restart сервера/новый attachment должны сохранять IDs.

## Реализация и приёмка

Начать с небольшого native probe существующих schema hooks, OBJECT_NAME,
транзакционности и definer-полномочий. Затем добавить установочный SQL и целевой
тест в существующие tools/harness, без нового framework. Все базы/runtime/security
DB — отдельные /tmp; рабочий сервер и исходную 1С не изменять.

Обязательные проверки:

- Fresh install на базе с несколькими заранее созданными схемами: registry
  соответствует RDB$SCHEMAS, IDs уникальны/ненулевые/в диапазоне. Имена отличаются
  регистром, содержат quoted Unicode/пробел/кавычку; сравнивать реальное имя.
- Повторный запуск installer и чужая уже существующая PGWIRE_COMPAT дают явный
  отказ без изменения объектов/IDs. Искусственная ошибка середины первой установки
  оставляет rollback, без частично установленной схемы/trigger.
- CREATE/ALTER/DROP, варианты IF EXISTS/IF NOT EXISTS/CREATE OR ALTER/RECREATE,
  DROP CASCADE пользовательской схемы; ALTER сохраняет ID, DROP+CREATE меняет его.
  Non-schema DDL/DML не меняет registry и сохраняет native поведение.
- COMMIT, ROLLBACK и ROLLBACK TO SAVEPOINT для schema DDL/registry; sequence gaps
  допустимы. Не запускать known native crash repro из NAT-*.
- Два соединения: одинаковый ID после commit, отсутствие чужих uncommitted rows,
  rollback не оставляет phantom namespace. Конкурентное создание разных имён
  даёт разные IDs; конфликт одного имени следует native результату без дубликатов.
  Синхронизировать шаги явно, без тестов на случайные паузы.
- Обычный пользователь с подходящими native schema-правами проходит DDL;
  SELECT из view работает без прямого DML-доступа к registry/sequence. Прямые
  попытки изменить служебную таблицу/allocator/trigger отклоняются.
- Повторные lookup из read-only transaction не меняют таблицу или allocator;
  новый attachment и нормальный restart сервера сохраняют mapping.
- Через настоящий libpq прочитать те же внутренние IDs, которые видны native
  клиенту; проверить native schema DDL через PgWire, если он проходит существующий
  контракт. Для этого не расширять SQL rewriting, public APIs или command types.

Готовую сборку из актуального review можно переиспользовать с записью SHA.
Если изменения остались SQL/harness-only, повторная полная сборка движка не нужна.
Целевая приёмка включает native и libpq на одном временном сервере и штатное
завершение; затронутые регрессии добавлять по фактическим изменениям. Не запускать
все предыдущие suites без причины. При неожиданном дефекте движка зафиксировать
конкретное воспроизведение, а не расширять этот этап исправлением ядра.

Обновить VALIDATION (SQL/harness SHA, runtime SHA, команды, PASS/FAIL/SKIP,
exit серверов), README/PLAN/TEST_PLAN и APP-10 в BACKLOG. В COMPATIBILITY описать
только установленный внутренний mapping. APP-01/APP-06 и selected04 остаются
открыты; следующий публичный catalog contract согласовать отдельно.

Основания: native schema metadata/hooks и
[PG pg_namespace](https://www.postgresql.org/docs/18/catalog-pg-namespace.html),
[OID/regnamespace](https://www.postgresql.org/docs/18/datatype-oid.html).
Не добавлять пустые успешные ответы каталогов, фиксированные ID по имени базы
или фиктивный успех отсутствующего объекта.
