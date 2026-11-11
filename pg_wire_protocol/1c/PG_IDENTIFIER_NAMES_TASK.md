# APP-13: PostgreSQL-правила имён в PG-диалекте

Решение от 11 ноября 2026 года. Статус: задание на реализацию.
Рабочий каталог: `/home/aleksey/dev/my_firebird/firebird`.
База — master Firebird 6, ветка pg_wire_protocol; проверенный HEAD `13f64d0e00`.
Не переключать ветку и не сбрасывать изменения ради этой ревизии.

## Согласованный объём и результат

Пользователь явно выбрал **PG-правила имён в PG-диалекте для совместимости с 1С**
и попросил увеличить объём одной инструкции. Сделай цельный этап: normalization
идентификаторов → безопасный сохранённый SQL → реальные lowercase schemas/catalogs
→ начальный путь поиска → DDL/DML через libpq с проверкой native клиентом.
Это одна реализация на review, с несколькими проверяемыми частями внутри неё.

**Минимальные необходимые изменения FirebirdSQL, максимальное переиспользование
штатных механизмов, ноль новых runtime-зависимостей.** Не добавлять новый публичный
API/ABI, общий framework совместимости, второй parser, ODS-тип или SQL rewriting
в плагине. Database dialect/DPB/SQL_DIALECT_CURRENT остаются 3; изменения правил
имён изолированы существующим клиентским SQL_DIALECT_PG=4.

**Не выполнять git add, commit, amend, reset или checkout.** Пользователь проверяет
и коммитит сам. Не выполнять весь `/home/aleksey/dev/1c/CODEX_PGWIRE_1C_TASK.md`
и не начинать следующий этап автоматически. Обычные детали реализации уже
разрешены; существенное расширение архитектуры обосновать конкретным препятствием.

Прочитай status/HEAD, применимые инструкции, [README](README.md),
[PROGRESS](PROGRESS.md), [BACKLOG](../BACKLOG.md), начало [VALIDATION](../VALIDATION.md),
Parser/parse.y, QueryExecutor/SqlInfo и APP-10/11/12 installers.
APP-12 прошёл review и независимую приёмку: реальные relations/namespace IDs уже
доступны, metadata пока native BIGINT/VARCHAR. Прежние API/codecs сохраняются.

Изменение PG4 намеренно несовместимо с прежним native uppercase lookup:
unquoted `foo`, `FOO` и quoted `"foo"` означают lowercase объект; `"Foo"` и
`"FOO"` — другие точные имена. Старый native PUBLIC.TTT доступен через
`"PUBLIC"."TTT"`; его columns тоже требуют точного регистра. Native dialects
1/2/3 и родные подключения продолжают работать по прежним правилам.
Не добавлять fallback «сначала lowercase, затем uppercase» и не переименовывать
пользовательские объекты/данные автоматически.

Основание — [PG identifiers](https://www.postgresql.org/docs/18/sql-syntax-lexical.html)
и [UTF8 identifier folding](https://raw.githubusercontent.com/postgres/postgres/REL_18_STABLE/src/backend/parser/scansup.c).
Для нашего UTF8-профиля достаточно ASCII A–Z → a–z у unquoted names;
не вводить Unicode casefold/ICU и не менять байты многобайтных символов.

## 1. Parser: имена отдельно от keywords и значений

Переиспользовать существующий lexer/grammar. Не менять uppercase keyword lookup
или значения всех keyword tokens: native SysFunction lookup ожидает ABS/MOD и
другие canonical names. Lowercase требуется **в позиции идентификатора**,
включая non-reserved keyword, использованный как имя.

Основная точка — `valid_symbol_name` в parse.y с маленьким helper, принимающим
MetaName и исходный Position. Quoted/unquoted различать по исходному токену;
quoted names сохранять, не применять folding к строкам/comments. Обработать также
прямую ветку SYMBOL в `symbol_UDF_call_name`, обходящую valid_symbol_name.
Копировать keyword name при необходимости, не менять shared keyword table.

Проверить schemas/tables/columns/domains/routines, aliases/CTE, variables,
named arguments и savepoints. У valid_symbol_name есть неидентификаторные
потребители: GDSCODE, symbolic BLOB subtype, DECFLOAT traps и подобные значения.
Отделить их от object-name folding либо сохранить native canonical interpretation
локально в этих productions. Не исправлять возникшие ошибки глобальным UPPER
по всему DSQL. Встроенные типы/функции и значения startup SET сохраняют смысл.

Startup database/user/application_name/password — protocol strings, а не SQL
идентификаторы: их не преобразовывать. Native security identities сохраняются;
для обращения к их точному имени в SQL доступны quotes. Новый auth/role mapping
или перенос security database не входит в этап.

Сохранить native ограничения допустимых имён/длин, без тихого усечения до PG
63 bytes. U&-identifiers и другие новые формы лексики не добавлять. Если quoted
форма различается в PG, но native MetaName теряет существенные байты (например,
trailing spaces), отклонять такую PG4 форму явно **до эффектов**, а не склеивать
разные имена. Это ограничение зафиксировать; native dialects не менять.
Такие проверки должны видеть исходный токен до потери байтов при построении
MetaName; одного сравнения уже нормализованного имени недостаточно.
Существующие допустимые quoted Unicode/leading-space/doubled-quote имена проверить.

## 2. Сохранённый SQL и внутренний native reprepare

Одного lowercase AST недостаточно: сохранённый `select Foo from Bar` при native3
reprepare снова разрешил бы FOO/BAR. Использовать существующие
`Parser::transformString`/`makeParseStr`, которые уже канонизируют литералы.
Добавить небольшие marks **действительных identifier spans** из окончательных
grammar actions. Для native source unquoted PG `Foo` сериализуется как `"foo"`.
Уже quoted names сохраняют точное значение и экранирование.

Свести marks с существующими string marks по исходным offsets, без повторной
замены/overlap и без мутаций на speculative parse paths. Keywords/builtin calls
не заключать в quotes; literal contents и комментарии не разбирать как SQL.
Это канонизация источника внутри engine parser, не переписывание пользовательского
SQL в wire-коде. Полный AST serializer и второй parser не нужны.

Покрыть ранее поддержанные native-формы CREATE TABLE, VIEW, DEFAULT/CHECK/COMPUTED,
expression index и stored PSQL; внутренние source captures и CTAS тоже проверить.
Не запрещать всё DDL вместо реализации этого пути. Если отдельная форма не может
быть корректно сохранена, минимальный явный отказ до эффектов допустим с точным
ограничением и воспроизведением; не оставлять успешно созданный испорченный source.
Прежние root guards `$n`/`::`/LIMIT не снимать: SQL-05 ими не закрывается.
Dynamic SQL внутри строки EXECUTE STATEMENT остаётся native по своему действующему
контракту; строки не переводить в другой диалект автоматически.

DSQL cache уже различает клиентский dialect/search path. Проверить, что один
текст SQL в PG4 и native3 разрешает соответствующие разные объекты, включая
cached/prepared reuse; не добавлять отдельный кэш.

## 3. Lowercase bootstrap без копирования пользовательских данных

Отдельный атомарный native isql installer, например `sql/install_pg_names.sql`,
поверх исправных APP-10/11/12 и compat.sql. Старые PGWIRE_COMPAT/PG_CATALOG
объекты остаются источником принятых данных. Новые объекты создаются физически
с quoted lowercase именами; не применять LOWER к catalog values:

- schema `"pg_catalog"` — обычный CREATE без IF NOT EXISTS: существующая schema
  означает отказ до изменений, не неявную миграцию чужих объектов;
- schema `"public"` — CREATE IF NOT EXISTS: уже существующую сохранить с её
  owner/данными; native PUBLIC остаётся отдельной схемой;
- read-only `"pg_catalog"."pg_namespace"("oid","nspname")` над PG_CATALOG.PG_NAMESPACE;
- read-only `"pg_catalog"."pg_class"("oid","relname","relnamespace","relkind")`
  над PG_CATALOG.PG_CLASS;
- `"pg_catalog"."pg_backend_pid"` — обычная PSQL-обёртка над прежней функцией.

APP-10 hook регистрирует новые реальные schemas; IDs прежних схем не меняются.
Native PUBLIC/PG_CATALOG и lowercase public/pg_catalog — разные реальные строки,
соответствующие правилам quoted/unquoted names. Не копировать данные, не создавать
алиасы пользовательских таблиц и не скрывать uppercase объекты из каталога.
View labels lowercase, значения имён точные, типы/область rows APP-11/12 прежние.
PG oid/name/internal char types здесь не появляются.
Автоматические labels без явного alias у вычисляемых выражений могут сохранять
native conventions; это не основание менять все ResultCodec labels глобально.

SET BAIL ON, AUTODDL OFF, один COMMIT, qualified references; credentials вне SQL.
PUBLIC получает USAGE новых schemas, SELECT views, EXECUTE функции. CREATE/DML
права приложения задаются отдельно, installer их всем не выдаёт. Повтор/коллизия/
ошибка до COMMIT сохраняют старые объекты/данные/права; возможны sequence gaps
после rollback новых schemas. Нет автоматической установки при attach, repair,
version framework или изменений рабочих баз. Описать порядок установки на чистой
базе и поверх старого профиля; test fixtures не являются переносом настоящей 1С.

## 4. Инициализация PgWire, RESET и savepoint history

После attach, до успешного ReadyForQuery (предпочтительно до AuthenticationOk),
выполнить один фиксированный служебный native SET:

```sql
SET SEARCH_PATH TO "public", "pg_catalog", "SYSTEM"
```

Переиспользовать session-management execution через существующий attachment,
по образцу restoreStrings: prepare/execute с nullptr transaction, проверка статуса
и отсутствия неожиданного transaction handle, освобождение statement. При ошибке
не отдавать успешную готовую сессию. Общий host/Authentication и attachDatabase ABI
не расширять и не добавлять PG-specific policy в host. Нет SQL из пользовательского
ввода, новой application transaction, скрытого commit или клиентского CommandComplete.

Public-first выбран намеренно: native current schema — первая существующая схема
пути; pg_catalog-first отправил бы обычный CREATE TABLE в каталог. В этом этапе
**не реализуются** implicit приоритет PG pg_catalog, `$user`, effective path по
USAGE и transactional PG SET search_path. Это отдельные части APP-01/SES-06.
SYSTEM остаётся native fallback, native объекты в нём требуют точного регистра
в quotes. Успех SET не заменяет установку lowercase bootstrap.

Обычный пользовательский SET SEARCH_PATH действует до следующего явного изменения:
не перезаписывать его при каждом Query/Parse/Bind/Execute. После успешного прямого
ALTER SESSION RESET в общем QueryExecutor control path повторно применить профиль,
иначе native reset вернёт исходный PUBLIC,SYSTEM. Сделать это при действующем
fatal-reconciliation guard и до объявления успеха; проверка Simple/Extended.
Если native RESET уже прошёл, а служебный SET не удался, сессию завершить:
не сообщать восстановленный профиль и не повторять пользовательский запрос.
Hidden RESET внутри PSQL остаётся существующим ограничением SES-08, без нового API.

Согласовать узкую существующую историю standard_conforming_strings:
`SqlInfo::savepointCommand` сейчас uppercases unquoted savepoint name. Перевести
нормализацию имени на PG lowercase; keyword classification остаётся прежней,
quoted names согласованы с engine. SAVEPOINT a и ROLLBACK TO "a" должны совпадать;
"A" — другой savepoint. SQL исполняет engine, lexer плагина не становится parser.

## 5. Реализация и приёмка одного законченного этапа

Внутренний порядок работ: parser/source preservation → installer/session profile
→ savepoint/cache integration → end-to-end tests. Не выдавать каждую строку SQL
за отдельный завершённый этап. Результат готов, когда цепочка работает целиком.

Обязательные группы проверки на свежей сборке и изолированных /tmp базах:

1. PG4: `foo`, `FOO`, `"foo"` — один объект; `"Foo"`/`"FOO"` отдельные. Columns,
   aliases/CTE, routine names/arguments, non-reserved keyword identifiers, qualified
   names; строковые значения и keywords не меняются. ABS/MOD и startup SET работают.
   Unicode/quotes и явные отказы непредставимых имён. Native1/2/3 прежние.
2. Реальный libpq: CREATE TABLE без schema создаёт `"public"."foo"`; INSERT/SELECT/
   UPDATE/DELETE/ALTER/DROP, Simple scripts и Extended/prepared с Bind. Native клиент
   читает/изменяет те же bytes через точные quoted имена. Старые uppercase таблицы
   остаются доступны native и через quoted PG4, без fallback/смешения данных.
3. VIEW/DEFAULT/CHECK/COMPUTED/expression index/stored PSQL и CTAS с ранее допустимым
   native SQL: mixed-case unquoted identifiers и escaped literals, metadata source,
   reconnect и реальный native reprepare. Проверить функции, переменные, named args;
   также standard_conforming_strings=off. Не выдавать только BLR execution за
   проверку сохранённого source. Прежние `$n`/`::`/LIMIT guards сохранены.
4. Начальный path, пользовательский SET SEARCH_PATH, два одинаково названных объекта
   в разных schemas, cache/dialect isolation, prepared reuse. RESET восстанавливает
   PG default; helper не оставляет handles/transactions и не выдаёт лишних wire results.
5. Savepoints a/"a"/"A": SET strings → ошибка/E → ROLLBACK TO/RELEASE, верное
   значение/ParameterStatus и восстановление T. Не менять transaction lifecycle.
6. Installer на новой базе и с существующей "public"; повтор/коллизия/ошибка перед
   COMMIT, права/read-only, неизменные старые IDs/данные и настоящие новые schemas.
   Нижние catalog names/labels разрешаются unquoted, их строки/ID равны native oracle.
7. Исходные selected01, selected06 и selected23 — без изменения SQL, на synthetic
   lowercase схемах/полях с прежними binary fixtures. Обновить старые harnesses,
   которые обращались unquoted к uppercase native fixtures: явно quoted native
   references либо настоящие PG-created fixtures. Не менять исходную запись 1С
   и не ослаблять проверки bytes/metadata/транзакций ради прохождения.

Переиспользовать существующие tools и native/libpq/raw helpers. Нужна свежая сборка,
релевантные parser/native/unit и затронутые startup/Simple/Extended/strings/catalog/
bytea проверки. Корректировать старые expectations только там, где пользователь
явно изменил naming contract; записать это в validation. Не повторять все suites
многократно после PASS без новой причины. Штатный shutdown/native coexistence.
Рабочий сервер/1С не менять, known NAT-* crash repro не запускать.

## Что оставить следующему этапу

APP-13 не реализует regnamespace/oid/name, pg_table_is_visible, pg_my_temp_schema,
полный search_path или temporary DDL. Не добавлять успешные заглушки ради selected04.
MAKE_DBKEY не проверяет effective PG path по USAGE; MON$LOCAL_TEMPORARY_TABLES —
transaction snapshot, не live presence check. Эти исследования сохранены в VALIDATION.
Исходные selected04/10/11 и реальное открытие 1С пока остаются открытыми.

Обновить PLAN/BACKLOG/PROGRESS/COMPATIBILITY/CODEX_CONTEXT/README, quickstart и
TEST_PLAN/VALIDATION: новый naming contract, миграция примеров/настройки базы,
ограничения, commands/hashes/runtime/source revision, PASS/FAIL/SKIP/server exit.
Оставить единый проверяемый diff, без коммита. Новые крупные этапы далее тоже
формировать вокруг законченного сценария, а не отдельного view/helper.
