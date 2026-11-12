# APP-15: полный рабочий цикл PG temporary tables

Возобновление 12 ноября 2026: [отдельное исправление NAT-06](LTT_METADATA_UNDO_TASK.md)
принято в `516f55bbb5`: review без замечаний, независимый повтор 44 native SQL
сценариев и 38 OO assertions PASS
([review](../VALIDATION.md#12-ноября-2026-архитектурный-review-nat-06)).
Реализация разделов 2–6 выполнена в рабочем diff 12 ноября и оставлена для review.
[Проверки и границы](../VALIDATION.md#12-ноября-2026-app-15-полный-цикл-поддержанных-pg-temporary-tables).
P2 APP-14 уже исправлен в `11dee63d2a`, повторная
реализация не нужна. Ниже также уточнён необходимый guard для PG LTT DDL под
user savepoint: нынешний native код такую DDL ещё не разрешает (SQL-10).

Задание для отдельного диалога разработки, 11 ноября 2026 года.
Рабочий каталог `/home/aleksey/dev/my_firebird/firebird`, master Firebird 6,
ветка `pg_wire_protocol`; проверенная основа `516f55bbb5`.
Не переключать ветку ради этой ревизии. Сначала прочитать применимые инструкции,
status/diff, [README](README.md), [PROGRESS](PROGRESS.md), [BACKLOG](../BACKLOG.md)
и начало [VALIDATION](../VALIDATION.md).

**Минимальные необходимые изменения FirebirdSQL, максимальное переиспользование
штатных механизмов, никаких новых runtime dependencies.** Пользователь просит
законченные прикладные этапы: выполнить все части ниже с одной итоговой приёмкой,
не останавливаться после добавления очередного helper/view. При этом не выполнять
весь roadmap 1С. Пользователь проверяет diff и сам делает коммит: не выполнять
git add, commit, amend, reset или checkout.

Это согласованная инструкция выполненного этапа; review рабочего diff ещё впереди. Он затрагивает APP-14/15, часть APP-01/04,
SQL-09/10. APP-02/03/05/06, полный APP-01/04 и исходный selected04 не закрываются.

## Проверяемый результат

Два libpq-подключения создают одноимённые временные таблицы через PG4. Каждое
работает со своим определением и данными, видит согласованные строки pg_class /
pg_namespace и результат pg_table_is_visible. Работают квалификация pg_temp,
затенение постоянной таблицы, DML и обычные индексы, commit/rollback/savepoints,
ON COMMIT и удаление при завершении подключения. Всё выполняется native LTT
движка Firebird, без копии таблиц или транзакций в PgWire.

Представительный сценарий, с уже поддержанными типами:

```sql
CREATE TABLE public.work_items (_id INTEGER, _value VARCHAR(40));
INSERT INTO public.work_items VALUES (100, 'permanent');
BEGIN;
CREATE TEMP TABLE work_items (_id INTEGER, _value VARCHAR(40));
INSERT INTO work_items VALUES (1, 'temporary');
CREATE UNIQUE INDEX work_items_id ON work_items (_id);
COMMIT;
SELECT * FROM work_items;          -- temporary
SELECT * FROM pg_temp.work_items;  -- та же временная таблица
SELECT * FROM public.work_items;   -- permanent
SELECT pg_catalog.pg_my_temp_schema();
SELECT oid, relname, relnamespace, pg_catalog.pg_table_is_visible(oid)
  FROM pg_catalog.pg_class WHERE relname = 'work_items';
BEGIN;
SAVEPOINT s;
DROP TABLE pg_temp.work_items;
ROLLBACK TO s;
SELECT * FROM pg_temp.work_items;  -- определение, данные и индекс восстановлены
ROLLBACK;
DROP TABLE pg_temp.work_items;
SELECT * FROM work_items;          -- снова permanent
```

Это обычный интеграционный fixture, не подмена исходных запросов 1С. Уже выполненный
oracle Postgres Pro 18.6 через libpq — `/tmp/app15-oracle/summary.json`, описание
в VALIDATION. Использовать его результаты; дополнить проверку только для новых
спорных lifecycle случаев. Не использовать рабочий сервер или базу 1С.

## 1. Сохранить исправление замечания APP-14

P2 исправлен в `11dee63d2a` и прошёл review 12 ноября. В исходном `evlPgRelationId`
guards предшествовали `EVL_expr`. Выражение аргумента
может поменять search_path или создать LTT, после чего lookup работает с уже
непроверенным состоянием. Подтверждённый пример и логи — в начале VALIDATION.

Сохранить вычисление аргумента до захвата и проверки зависимого состояния attachment;
не держать ссылку на прежний path через вызов пользовательской функции. Сохранить
согласованный порядок NULL/ошибок, а после расширения профиля проверить права
и состояние, действующие после вычисления аргумента. Регрессия с PSQL-аргументом,
меняющим путь, и native prepared handle уже добавлена; использовать её.
Не лечить это SQL-обёрткой:
публично доступен и сам builtin. Остальные результаты APP-14 сохраняются, кроме
явно снятых ниже отказов на PG-owned temps / effective relation path.

## 2. Использовать native LTT, различая PG и native семантику

Точки reuse: [описание LTT](../../doc/sql.extensions/README.created_local_temporary_tables.md),
`LocalTemporaryTable.h`, Create/Alter/DropRelationNode в `DdlNodes.epp`,
LTT undo в `Savepoint.cpp`, очистка в `Attachment.cpp`, существующие LTT indexes.
Использовать принятый NAT-06: `LttUndo` в savepoint/транзакции, общий backout и
сохранение metadata undo независимо от row-root. Повторно реализовывать этот fix
или обобщать его в универсальный журнал ради PG namespace не требуется.
Не создавать новый storage engine, shared GTT с фильтром по connection ID,
служебные application attachments или отдельные транзакции для Parse/каталогов.
На PgWire attachment остаётся не более одной активной native транзакции.

PG4 grammar должна создавать существующие DDL nodes с минимальными внутренними
признаками PG temporary table. Признак должен переживать Prepare → Execute,
клонирование LTT metadata и undo; определять его по порту/логину/имени нельзя.
Native dialects 1/2/3 сохраняют свои значения по умолчанию и правила.

**Дополнение SQL-10:** нынешний `DsqlDdlRequest::execute` запрещает любую DDL
под user savepoint до вызова node. `FLAG_ACTUAL_LTT_DDL` выставляется позже и
пока только исключает replication. Добавить узкую engine
проверку до эффектов: под user savepoint разрешены только PG4 операции, чей
фактический target — PG-owned LTT (для CREATE — PG TEMP intent). Для ALTER/DROP/
index operations проверить текущий target и ownership, не только вид SQL или
состояние на прежнем Prepare. Переиспользовать AST/native metadata lookup;
публичный metadata API и wire lexer для этого не расширять. Persistent/GTT и
native dialects 1/2/3 сохраняют текущий запрет. Не удалять общий guard и не
переносить отказ на момент после выполнения DDL. Проверить отказ без эффектов
и stale prepared target после DROP/recreate. Полная positive SAVEPOINT/RELEASE/
ROLLBACK TO матрица PG TEMP входит в этот этап, а не считается готовым native reuse.

В PgWire потребуется только расширить существующий `SqlInfo::ddlObject` для
новых TEMP/TEMPORARY prefixes и правильного CREATE TABLE tag. Сейчас он знает
LOCAL/GLOBAL TEMPORARY, но не все новые формы. SQL передавать неизменённым;
проверка грамматики, target schema, состояние и исполнение остаются в движке.

Обязательный профиль:

- `CREATE [LOCAL | GLOBAL] TEMP[ORARY] TABLE [IF NOT EXISTS]` с native-supported
  колонками и NOT NULL. В PG4 обе формы LOCAL/GLOBAL означают session-local LTT,
  а не native GTT. По умолчанию — ON COMMIT PRESERVE ROWS.
- Явные `ON COMMIT PRESERVE ROWS` и `DELETE ROWS` через native lifecycle.
  `ON COMMIT DROP` — явный отказ до эффектов и ограничение APP-04: в выбранных
  запросах 1С его нет, а корректный вариант требует отдельного precommit cleanup
  с обработкой failed commit/rollback. Не добавлять такой hook ради полноты
  синтаксиса и не имитировать DROP из wire-плагина.
- `DROP TABLE [IF EXISTS]`, DML и native простые/unique indexes над такими LTT;
  сохранение определения, данных и индексов при rollback/savepoint rollback.
  IF NOT EXISTS проверяет целевое temp namespace, не одноимённую public table.
  Проверять PG-only коллизии имён своих temp tables и их indexes в обе стороны:
  в PostgreSQL это одно пространство имён, native проверки разделены. Catalog
  rows индексов для такой проверки не нужны.
- Legacy `WITHOUT OIDS` допустим: user OID column не создаётся. `WITH OIDS`
  не принимать без реализации. Для `DROP ... CASCADE` нужен честный dependency
  contract: в поддержанном LTT-профиле удалить собственные индексы, а остальные
  зависимости либо обработать штатно, либо явно отказать до эффектов. Не игнорировать
  CASCADE на произвольной постоянной таблице.
- Ведущий `_` в PG identifiers (SQL-09), включая поля и индексы: доработать
  существующий lexer, сохранить native charset introducers и канонизацию source.
  Проверить неоднозначные `_UTF8`/quoted/literal формы, native1/2/3 и strings=off.

Не расширять здесь все возможности native LTT: defaults/identity/serial4,
PK/FK/CHECK, expression/partial indexes, CTAS и temp views не входят. Неподдержанные
формы должны давать явный отказ до побочных эффектов. Постоянные stored objects
не должны получать сохраняемую ссылку на session-private relation.

## 3. Namespace и identity: одно состояние в движке

Предпочтительный минимальный вариант — одна устанавливаемая служебная schema
`PGWIRE_TEMP` как физический контейнер native LTT. Она создаётся один раз, не при
каждом соединении. PG-owned LTT сохраняют точные имена в этом контейнере; изоляция
их definitions уже обеспечена native attachment map. В PG-представлении это
собственное виртуальное namespace `pg_temp_<id>` и alias `pg_temp`.

Так `public.work_items` однозначно остаётся постоянной таблицей, а `pg_temp.work_items`
указывает на свою LTT. Размещать обе в native public с одним QualifiedName нельзя:
это смешало бы явную квалификацию с затенением. Не создавать постоянную schema,
таблицу registry или trigger на каждую временную таблицу/каждое подключение.

Граница CREATE задаётся явно: `CREATE TEMP TABLE t` и `CREATE TEMP TABLE pg_temp.t`
поддержаны; TEMP с произвольной permanent schema (например public.t) отклоняется,
не отбрасывая квалификатор. Обычный `CREATE TABLE pg_temp.t` и permanent CREATE
с явным pg_temp как целевым элементом search_path пока отвергать до эффектов.
Их неявное превращение в TEMP не входит в этот профиль; добавить в APP-04.
Прямой доступ по виртуальному имени проверяет PG-owned marker: никакого fallback
к persistent/GTT или unmanaged LTT в физическом контейнере.

Installer выдаёт только необходимые штатные права на контейнер. Защитить его
от persistent/GTT CREATE обычным пользователем, например существующим DDL trigger
(native LTT DDL triggers не вызывает). Не обходить SCL и не повышать права DML.
Чужой существующий контейнер или нарушенная bootstrap-конфигурация — явная ошибка,
не автоматический DROP/repair. Зарезервировать виртуальные имена внутри PG-профиля;
коллизии с настоящими schemas обнаруживать, не менять native правила глобально.

Native LTT IDs повторяются между attachments и переиспользуются. Поэтому для
публичного pg_class недостаточно `native LTT id + 1`. Минимально переиспользовать
существующий database-local `PGWIRE_COMPAT.NAMESPACE_SEQ` как общий allocator:

- installer без concurrent DDL поднимает нижнюю границу allocator до диапазона
  выше постоянных relation OIDs, не понижая текущего значения и не меняя старые IDs;
  на нынешнем native диапазоне persistent mapping занимает 1…32768;
- новые temp namespace/relation identities — уникальные значения из этой sequence,
  в пределах 1…4294967295; проверить реальные константы перед кодированием;
- хранить namespace state в attachment, public relation ID в PG-owned LTT metadata;
  DROP/recreate получает новый public ID, rollback DROP восстанавливает прежний;
  clone/undo сохраняют IDs, sequence gaps после abort допустимы;
- использовать native generator access/проверки, без dynamic SQL с пользовательским
  текстом, autonomous transaction и собственного allocator/cache. Не выдавать
  пользователю UPDATE на registry ради этого механизма.

Это **новая узкая зависимость PG temporary DDL от установленного PGWIRE_COMPAT**.
Она не распространяется на native LTT и не добавляет публичный OO API/ODS-поля.
В отчёте показать точный вызов native allocator и необходимые права. Не прятать
зависимость за универсальным registry/framework. Исчерпание диапазона — явный
отказ до создания объекта; read-only database profile этим решением не обещается.

`pg_my_temp_schema()` возвращает 0 до инициализации и настоящий ID после неё.
Namespace не исчезает просто из-за DROP последней таблицы. Проверить на PG oracle
первое CREATE → rollback, CREATE+DROP в одной транзакции, savepoint rollback/release
и последующее повторное CREATE. Хранить undo инициализации отдельно от конкретной
LTT: namespace живёт и после CREATE+DROP и имеет собственную границу rollback.
Принятый NAT-06 хранит полный список LTT actions без coalescing; удаление одной
таблицы не должно отменять namespace activation. Не вести параллельный
исторический журнал временных объектов в PgWire.

Namespace activation и выделение IDs происходят только при Execute создания,
никогда при Parse/Describe. Неуспешный CREATE откатывает первую активацию своим
statement undo; ранее активное namespace сохраняется. Parse → Describe → Close
без Execute не меняет pg_my_temp_schema, каталог или allocator.

В PG-профиле повторить, что undo не теряется при освобождении большого root savepoint:
CREATE TEMP → достаточно DML для этого пути → ROLLBACK. Native `Savepoint::rollforward`
и его SIZE_THRESHOLD нельзя оценивать только малыми fixtures. Native prerequisite
закрывает root/NO AUTO UNDO/merge/retaining; APP-15 проверяет их вместе с новым
namespace activation и PG-owned DDL под пользовательскими savepoints.

Существующий `ALTER SESSION RESET` — native extension: сохранить его нынешнюю
семантику очистки LTT data с сохранением definitions, проверить согласованность
PG identities/catalog после него. Не объявлять его PostgreSQL DISCARD TEMP.
Disconnect должен освободить всё attachment-local состояние штатным путём.

## 4. Один согласованный поиск отношений

Расширить существующий PG4 DSQL путь и сохранённый `RDB$PG_RELATION_ID` общей
небольшой внутренней политикой relation lookup. Native callers остаются прежними.
DSQL сейчас нормализует client dialect 4 в 3: при необходимости сохранить один
внутренний PG-признак в compiler scratch/node, не вводить новый attachment-wide
dialect и не менять сигнатуры публичных интерфейсов.

Для поддержанных table/view отношений согласовать реальный SELECT и visibility:

- своё temp namespace первым, если `pg_temp` не указан явно; при явном элементе
  использовать его позицию; `pg_temp` и собственное `pg_temp_<id>` работают и
  в schema-qualified именах;
- implicit `pg_catalog` перед обычными элементами, когда его нет в явном пути;
- `$user` — точное native CURRENT_USER identity, missing schemas и schemas без
  USAGE пропускаются; позиция остальных элементов сохраняется;
- явный `public.name` не попадает в temp, чужое temp namespace не открывает чужую
  LTT; путь для CREATE permanent не получает неявный temporary target;
- implicit temp lookup не распространять на функции/операторы. Полные PG types
  и их поиск ещё вне этапа. Существующий initial/RESET список public, pg_catalog,
  SYSTEM и session lifetime SET SEARCH_PATH остаются явными ограничениями.

Переиспользовать MET/SCL/cache resolution. Для USAGE нужен узкий внутренний
non-throwing access decision на существующей SCL-логике; нынешний bool результата
`SCL_check_schema` означает существование schema, а не разрешение доступа.
Не перехватывать все isc_no_priv: этот код может означать повреждённую ACL.
Не копировать permission logic запросами к RDB$USER_PRIVILEGES.

Native GTT, unmanaged native LTT и packaged/declared temporary objects не становятся
PG temporary tables автоматически. Для неподдержанного смешанного lookup сохранять
явный ограничивающий отказ, а не неверный TRUE/FALSE. Снять APP-14 blanket guard
для поддержанных PG-owned LTT. Helper по-прежнему возвращает native INTEGER ID;
public identity mapping остаётся снаружи него.

Не отключать statement cache. Проверить свежий Prepare и существующие PgWire
Bind/reprepare после CREATE/DROP/rollback/path change, повторное использование
native ID и смену прав. Использовать существующие зависимости/invalidation;
не делать второй cache. Старый native prepared handle и свежий PG Prepare — разные
контракты, их результаты проверять отдельно.

## 5. Живые каталоги через существующий механизм selectable procedure

MON$LOCAL_TEMPORARY_TABLES имеет transaction snapshot и не является источником
текущего DDL-state. Не обновлять MON скрытым commit и не опрашивать весь диапазон
native IDs тысячей scalar вызовов. Нужен один ограниченный источник строк живого
состояния текущего attachment.

Предпочтительный путь: один внутренний selectable procedure entrypoint через
существующие `ENGINE SYSTEM`, `SystemProcedureFactory` и `IExternalResultSet`.
Отдельный installer создаёт обычную процедуру в PGWIRE_COMPAT; это работает и в
старой базе той же ODS. Небольшой exact entrypoint dispatch в `SystemEngine` должен
проверять ожидаемый message contract. Нельзя выдавать процедуру за имеющийся
SYSTEM package по совпадению имени: нынешний dispatch не проверяет schema.
Не расширять IAttachment/getInfo, не вводить новый BLR opcode, UDR .so или JSON API.

Один проход provider возвращает собственный namespace, даже пустой после DROP,
и PG-owned LTT: public ID, native ID, exact name, namespace ID/name и минимальный
признак строки. Фиксированный небольшой record layout, native scalar types.
На открытии cursor скопировать нужные значения; не держать указатели на удаляемую
LTT через Fetch. Следующий Execute видит новое состояние в той же транзакции,
даже после раннего чтения MON. Provider ничего не создаёт и не выделяет новые IDs.

SQL слой объединяет persistent projection APP-12/13 и этот источник:

- нижний pg_class сохраняет четыре прежних поля/типы и public IDs постоянных
  tables/views, добавляет собственные PG temp tables с relkind='r';
- pg_namespace добавляет собственный виртуальный namespace, без LOWER имён;
- pg_my_temp_schema читает тот же источник, wire20/BIGINT в нынешнем профиле;
- pg_table_is_visible получает из private projection ожидаемый native ID и
  сравнивает с helper. Формулу oid−1 применять только к persistent rows;
- views остаются read-only, wrappers работают с caller state/правами. Отсутствие
  SELECT на таблицу не отменяет visibility по имени.

Public metadata остаётся native BIGINT/VARCHAR/BOOLEAN. Не retag по происхождению
колонки в OID26/name19; indexes/sequences и чужие temp metadata не добавляются
в pg_class этим этапом. Последнее — явное отличие от полного PostgreSQL catalog:
показываем permanent + свои PG temps, а не глобальный каталог чужих attachments.

Installer — отдельный атомарный переход APP-14 → APP-15 с BAIL ON/AUTODDL OFF,
проверкой ожидаемых старых объектов и сохранением grants/IDs. Проверить fresh,
upgrade, failure rollback, repeat/foreign collision. Не выполнять скрытую установку
при attach. Allocation sequence может иметь gaps после неуспеха, это не повод
перематывать её назад. Старые uppercase compatibility objects не удалять молча.
Добавить новый SQL-файл в общий [install_compat.py](install_compat.py) после его
предпосылок. Проверить полную первоначальную установку этим скриптом и отдельный
APP-14 → APP-15 переход; общий установщик пока не является migration runner.

## 6. Реализация и приёмка одним этапом

Порядок внутри работы: сверка сохранённого oracle и принятого native reuse →
LTT grammar/state/undo → resolver/IDs → live provider и SQL views → общий
libpq/native сценарий. Можно проверять части по мере реализации, но сдавать
согласованный результат всего этапа. Не добавлять самостоятельный harness:
расширить нынешний pgwire_1c_smoke и существующие libpq/raw/native helpers.

| Группа | Обязательные проверки |
|---|---|
| PG oracle | PRESERVE/DELETE; rollback первого namespace CREATE; DROP последней таблицы; CREATE+DROP и savepoint release/rollback; фиксировать actual results и версию |
| DDL/DML | Все принятые TEMP spellings, command tags и schema-qualified CREATE boundaries; `_`/mixed/quoted/Unicode names; NOT NULL; INSERT/UPDATE/DELETE/SELECT; unique duplicate failure и recovery; table/index name collision; IF NOT EXISTS без потери данных |
| Транзакции | implicit и explicit; PRESERVE/DELETE и отказ DROP; rollback CREATE/DROP/index/DML, включая освобождение большого root savepoint; savepoints; первая ошибка Simple batch; T→E→ROLLBACK TO/ROLLBACK, definitions/rows/IDs/catalog после recovery |
| Две сессии | Одинаковые имена с разными колонками/данными/IDs; независимые lifecycle; запрещён чужой pg_temp_N; после reconnect нет прежних definitions/данных |
| Поиск/права | permanent vs temp shadowing, explicit public и pg_temp, позиция pg_temp; implicit catalog; missing/no-USAGE/$user; owner/PUBLIC/role; visibility без SELECT; отсутствие SCL bypass |
| Каталоги | Live после MON и DDL в той же транзакции; empty namespace; unknown/old OID; DROP/recreate; совпадение SELECT с visibility; native metadata, text/binary и NULL |
| Cache/lifetime | Simple и named Extended; Parse/Describe/Close без Execute не меняет state/allocator; повтор Bind/Execute после DDL/path/RESET; paused portal без dangling pointers; native ID reuse; cache включён; обычный EOF и штатный shutdown |
| Установка | APP-14 DB → новый runtime/install без ODS change; прежние данные/IDs/grants; fresh/rollback/repeat/collision; низкий/уже высокий allocator, uint32 exhaustion без wrap |
| Native regressions | dialect1/2/3 LTT/GTT defaults и lookup, MAKE_DBKEY, persistent DDL/DML и права; names/strings/visibility/transaction tests; native endpoint параллельно |

Точки проверки нового жизненного цикла **не требуют** повторять NAT-01…04.
NAT-05 — отдельный известный crash statement cache, воспроизведённый уже на APP-13
нашего fork. Это не доказанный upstream/native-only дефект. Сохранить исходный
reproducer и отдельно указать покрытие; не скрывать падение перестановкой запросов,
увеличением/отключением cache или заменой SQL на пустой результат. Если он блокирует
новую приёмку, локализовать причинную связь. Исправление, необходимое в изменённом
здесь compatibility коде, входит в этап; самостоятельный native fix вне его области
сначала вынести на архитектурное решение. Не объявлять заблокированную проверку PASS.

Тесты только в отдельных /tmp runtime/security/databases. Рабочую 1С и её базы не
трогать, private capture не копировать в Git. Никаких production message timeouts.
После релевантных PASS не запускать бесконечную полную регрессию без нового основания.
Отчёт: source revision/diff и artifact hashes, точные команды, oracle и метаданные,
PASS/FAIL/SKIP, server exit/cleanup, ограничения. Обновить README/COMPATIBILITY/
PLAN/BACKLOG/PROGRESS/TEST_PLAN/VALIDATION, оставить diff для review без коммита.

## Границы и следующий крупный результат

Этап завершается при работающем полном цикле **поддержанных PG temporary tables**
(PRESERVE/DELETE, без ON COMMIT DROP),
а не при одном успешном CREATE. Он ещё не означает исполнение исходных selected12/13:
там bytea, mvarchar, serial4, затем требуются ANALYZE/fasttrun и binary indexes.
Они остаются APP-02/04. Не добавлять successful no-op для этих операций.

Исходный selected04 после этого всё ещё требует настоящего regnamespace. Не делать
специальный AST для одного IN predicate, не подменять тип DOMAIN BIGINT и не
подставлять namespace literal в плагине. Эти обходы добавляют новый ограниченный
язык и не обеспечивают type identity/input/output. Прямые catalog/visibility tests
не выдавать за PASS неизменённого selected04.

Следующий крупный результат планировать вокруг **типов и данных 1С**: binary IDs
с индексами, требуемые строки, serial/default lifecycle и namespace casts с честными
metadata/codecs. Уточнить внутренний порядок по целому прикладному сценарию;
не разбивать снова каждый alias/helper на отдельное задание. Advisory locks,
полные catalogs/current_database/collation, transactional search_path, большие
input BLOB, TLS/cancel и реальная 1С/Гилёв остаются в едином BACKLOG.

Семантические ориентиры: [PG CREATE TABLE](https://www.postgresql.org/docs/18/sql-createtable.html),
[PG search_path](https://www.postgresql.org/docs/18/runtime-config-client.html#GUC-SEARCH-PATH),
[PG visibility/temp functions](https://www.postgresql.org/docs/18/functions-info.html),
[PG object identifier types](https://www.postgresql.org/docs/18/datatype-oid.html).
Это целевая семантика, не заявление её полной реализации текущим fork.
