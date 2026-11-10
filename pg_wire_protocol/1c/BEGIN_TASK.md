# Следующий этап: PG BEGIN через штатные транзакции Firebird

Архитектурное решение от 10 ноября 2026 года. Статус: задание на реализацию.
Рабочий каталог: `/home/aleksey/dev/my_firebird/firebird`.
База — master Firebird 6, ветка `pg_wire_protocol`.
Проверенная ревизия: `4abc5af919` — фиксированный профиль клиентской диагностики.
Ревизия дана для ориентира, не для переключения ветки или сброса изменений.

## Задание и контекст

Реализуй ограниченный PG BEGIN, собери сервер, проверь реальные транзакции через
libpq и native observer, обнови документацию. Оставь результат пользователю
на review. **Не выполняй git add, commit, amend, reset или checkout.**
Пользователь проверяет и коммитит самостоятельно. Этот диалог — исполнитель;
следующие архитектурные этапы обсуждаются отдельно.

**Приоритет: минимальные необходимые изменения FirebirdSQL и максимальное
переиспользование существующих механизмов; ноль новых runtime-зависимостей.**
Не выполняй целиком `/home/aleksey/dev/1c/CODEX_PGWIRE_1C_TASK.md`.
Сначала прочитай HEAD/status, инструкции репозитория, [README](README.md),
начало [VALIDATION](../VALIDATION.md), текущие `SetTransactionNode`, `SqlInfo`
и `QueryExecutor::control()`.

Уже работают PG dialect 4, SELECT без FROM, последовательные Simple Query
scripts, Extended Query, native transaction/savepoint controls, фиксированные
English/UTF8 и off/error настройки диагностики, изменяемый standard_conforming_strings.
История последней настройки хранится в PgWire, текущее значение — в attachment.
API/prepare flags и исправление сериализации строк этого этапа не расширяются.
Native dialects 1/2/3 и диалект базы 3 должны сохранить своё поведение.

## Место этапа BEGIN

Перед ним выполняется отдельный [этап игнорируемых startup SET](IGNORED_STARTUP_SETTINGS_TASK.md).
Не объединять эти реализации в один результат.

В записи есть точный запрос `BEGIN TRANSACTION`:
`/home/aleksey/dev/1c/captures/20261109T105635Z/profile/selected/09-begin.sql`.
Он открывает реальную транзакцию и нужен обычному режиму работы PG-клиентов.
Штатные SetTransactionNode, TPB, ITransaction и plugin I/T/E уже решают основную задачу.

Три оставшихся startup SET пока отложены: enable_mergejoin меняет выбор плана,
cpu_operator_cost не имеет прямого эквивалента в стоимости Firebird, а lock_timeout
нельзя корректно заменить StatementTimeout или только TPB lock timeout.
Реальные эффекты потребуют отдельных решений. По последующему указанию пользователя
для этих трёх имён разрешён временный успешный SET без эффекта; это исключение
описано в отдельном задании выше. В этапе BEGIN их поведение не менять.

## Контракт и архитектура

Поддержать только формы без transaction options:

```sql
BEGIN;
BEGIN WORK;
BEGIN TRANSACTION;
START TRANSACTION;
```

Грамматика доступна только в клиентском PG dialect 4. Все формы возвращают
CommandComplete `BEGIN`. Пока не поддерживать ISOLATION LEVEL, READ ONLY/WRITE,
DEFERRABLE и изменение default_transaction_*; лишние опции должны дать ошибку,
а не молча игнорироваться. Native SET TRANSACTION со своими опциями остаётся прежним.

### Движок: существующий SetTransactionNode

Добавить отдельную альтернативу `tra_statement` в `src/dsql/parse.y`.
Создавать **существующий** SetTransactionNode с явно заданными полями:

- `isoLevel = ISO_LEVEL_READ_COMMITTED_READ_CONSISTENCY`;
- `readOnly = false`, `wait = true`;
- без AUTO COMMIT, NO AUTO UNDO и других дополнительных TPB опций.

Его штатные dsqlPass/execute формируют TPB и создают транзакцию.
Новый AST node, публичный тип операции, API/getInfo, конфигурация или wrapper
SQL не нужны. Не менять default SNAPSHOT у native SET TRANSACTION, даже когда
эта native команда вызывается через PgWire. READ CONSISTENCY здесь соответствует
уже используемому профилю implicit PgWire transaction; полная тождественность
всех concurrent DML эффектов PostgreSQL не заявляется.

BEGIN, WORK, START и TRANSACTION уже есть среди токенов. BEGIN/START используются
также в native PSQL/DDL: **не включать их в глобальный PG-only lexer gate**.
Ограничение диалекта должно относиться только к новой корневой production.
Использовать существующие parser predicates/диагностики, без нового pre-parser.
Native отказ должен быть окончательной syntax error; поздний YYERROR не должен
превращать `BEGIN;` в ложный command_end_err2 и поглощать следующий SQL при AUTOTERM.
Проверить это фактически, включая PSQL BEGIN/END и DDL START WITH.

### Плагин: различить PG BEGIN и native SET TRANSACTION

Существующий `ControlKind::Start` не подходит без различения: `control()` сейчас
отклоняет Start и в T, и в I с живым implicit handle. Для native SET TRANSACTION
это поведение сохранить. Добавить один **приватный** вид control, например PgStart,
в существующий SqlInfo; это не новый тип команды в ядре или публичных metadata.

SqlInfo распознаёт только префикс PG BEGIN/START TRANSACTION. Грамматику и весь
текст сначала проверяет engine prepare. `checkStatement()` сверяет оба вида
старта с прежним `isc_info_sql_stmt_start_trans`. SQL не переписывать и не
подменять строкой SET TRANSACTION. EXECUTE BLOCK/PSQL BEGIN не классифицировать
как transaction command.

После успешного prepare и проверки metadata выполнить следующую логику через
общий `QueryExecutor::control()`, используемый Simple и Extended:

| Состояние перед PG BEGIN | Действие |
| --- | --- |
| I, native transaction отсутствует | Исполнить подготовленный SetTransactionNode, сохранить возвращённый handle, перейти в T |
| I, native implicit transaction уже существует | Сохранить тот же handle и перейти в T; не исполнять второй start, не делать commit/rollback |
| T | Оставить тот же handle, savepoints и настройки, вернуть BEGIN; новый start не исполнять |
| E | Существующий отказ 25P02; BEGIN не является recovery |

Повтор в T — штатная PG семантика. PostgreSQL выдаёт предупреждение, которое
при принятом фиксированном client_min_messages=error клиент не получает.
Новый NoticeResponse для этого этапа не нужен. Не распространять это правило
на native SET TRANSACTION: его повтор по-прежнему даёт 25001 и обычный error path.

Переход I с живым handle в T включает предшествующую работу implicit сегмента
в явный блок. Например, `INSERT ...; BEGIN;` не должен сделать INSERT видимым
другому соединению до COMMIT; последующий ROLLBACK отменяет и этот INSERT.
Такой же переход нужен для Extended Execute до Sync. Не закрывать portals
и не создавать дополнительную транзакцию только из-за BEGIN.

Сохранить существующую историю standard_conforming_strings, в том числе для
`SET standard_conforming_strings=off; BEGIN` без native handle: начало логического
сегмента предшествует BEGIN. Не сбрасывать исходное значение при promotion или
повторном BEGIN. COMMIT/ROLLBACK/savepoints/reset используют прежние пути.
На одно PgWire-подключение по-прежнему не более одной активной application transaction.
Это ограничение плагина, не глобальное ограничение Firebird attachment.

Источники семантики: [BEGIN](https://www.postgresql.org/docs/18/sql-begin.html),
[START TRANSACTION](https://www.postgresql.org/docs/18/sql-start-transaction.html),
[Simple/Extended transaction flow](https://www.postgresql.org/docs/18/protocol-flow.html#PROTOCOL-FLOW-MULTI-STATEMENT),
[Read Committed](https://www.postgresql.org/docs/18/transaction-iso.html#XACT-READ-COMMITTED).

## План реализации и приёмка

1. На отдельном runtime зафиксировать исходный отказ `09-begin.sql`; проверить
   актуальные seams и ограничения выше. Не запускать рабочую базу 1С.
2. Добавить ограниченную грамматику поверх SetTransactionNode и PG-only guard.
3. Добавить приватное различение controls и переходы через общий executor.
   Не дублировать транзакционную логику в Simple и Extended.
4. Дополнить существующие classifier, 1c/transaction/extended тесты. Переиспользовать
   Connection/libpq, raw-wire clients и native monitoring helpers; не создавать
   новый harness, клиентскую библиотеку или framework. Новый режим существующего
   1c harness допустим, если он изолирует этот этап и не запускает весь baseline.
5. Собрать, выполнить целевую приёмку, обновить README/COMPATIBILITY/PLAN/TEST_PLAN
   и фактический отчёт VALIDATION. Затем остановиться для review пользователя.

Обязательные сценарии:

- Четыре формы, регистр/comments, исходные bytes `09-begin.sql`; BEGIN tag и T.
  Native observer подтверждает одну транзакцию, READ WRITE, WAIT и isolation
  READ COMMITTED READ CONSISTENCY. Сам CommandComplete успех не доказывает.
- В libpq BEGIN → INSERT/UPDATE/DELETE → COMMIT/ROLLBACK; native observer видит
  изменения только после commit. Разрыв соединения откатывает открытый блок.
- Два соединения: после BEGIN первый SELECT видит старое значение; второе
  соединение фиксирует UPDATE, следующий SELECT первого видит новое значение.
  Native SET TRANSACTION без опций по-прежнему SNAPSHOT. Использовать свежие
  observer transactions, не читать устаревший monitoring/data snapshot.
- `INSERT ...; BEGIN; SELECT CURRENT_TRANSACTION` и выборка transaction ID до
  BEGIN: тот же ID/handle, данные до BEGIN остаются невидимыми observer и
  откатываются общим ROLLBACK. Проверить и последующий COMMIT.
- Повтор BEGIN в T после DML/SAVEPOINT: ID и savepoint сохраняются, rollback
  действительно откатывает исходную работу. Повтор native SET TRANSACTION
  сохраняет прежний 25001. BEGIN в E даёт 25P02; ROLLBACK восстанавливает I.
- `BEGIN; INSERT ...; COMMIT; INSERT ...; SELECT 1/0; INSERT ...`:
  первый INSERT зафиксирован, второй отменён, третий не исполнен, итог I.
  `BEGIN; SELECT 1/0; ROLLBACK` оставляет E: остаток Query не исполняется.
- `SET standard_conforming_strings=off; BEGIN` и вариант с DML до BEGIN,
  последующий ROLLBACK/ошибка восстанавливают исходное значение. Повтор BEGIN
  в T не стирает историю; savepoint recovery сохраняется.
- Extended named Parse/Bind BEGIN сами явного блока не открывают; Execute
  открывает, Sync оставляет T. Переиспользовать prepared statement после rollback.
  Execute DML → Execute BEGIN до Sync сохраняет ту же транзакцию и данные.
  Suspended SELECT portal до BEGIN должен продолжать чтение после BEGIN/Sync,
  пока реальный COMMIT/ROLLBACK не завершит блок. Ошибки сохраняют discard-until-Sync.
- Native dialects 1/2/3 отвергают новые формы, AUTOTERM исполняет следующий SQL.
  EXECUTE BLOCK с BEGIN/END, native DDL START WITH и SET TRANSACTION работают
  как раньше. Неподдержанные transaction options не выполняются частично.
- Имеющийся psql с AUTOCOMMIT off автоматически посылает BEGIN и проходит
  SELECT/DML/ROLLBACK. Не добавлять зависимость сервера от psql/libpq.

Регрессии: classifier, SessionTest, isql, прежние settings и strings checks,
целевые transaction/Extended сценарии без известных native crash repro.
Проверять данные и I/T/E, отсутствие скрытого commit и второй транзакции;
не ограничиваться количеством ответов. Parser baseline — 154 shift/reduce,
11 reduce/reduce; новые конфликты нельзя принимать без разбора причины.

Найти прежние отрицательные проверки BEGIN во всех актуальных suites: они теперь
не могут ожидать 42601. В частности, `pgwire_transaction_smoke.py` в состоянии E
должен ожидать 25P02; Simple/Extended libpq tests в I — успех и явный ROLLBACK,
чтобы не изменить состояние последующих проверок. Перенести новые формы из unknown
в положительные classifier cases; обновить проверки автоматического BEGIN в
`pg_wire_protocol/tests/test_pgwire_acceptance.py`. Проверить также default
autocommit=false сценарий в `pgwire_psycopg3.py`: устранение отказа BEGIN ещё
не означает поддержку `$n` и других неподдержанных запросов драйвера. Обновлять
только связанные ожидания/описания, исторические suites не объявлять заново
пройденными без запуска. Сохранить проверки действительно ошибочного SQL.

Текущий out-of-tree build: `/tmp/firebird-pgwire-1c-build`.
Штатные команды: `make -j2`, затем `make -C gen -j2 TARGET=Debug pgwire_tests`;
бинарники — `gen/Debug/firebird`, tests запускаются с его `lib` в LD_LIBRARY_PATH.
Примеры запуска прежних --settings/--strings находятся в VALIDATION.
Для каждого runtime брать новый work-dir в /tmp; рабочий сервер, employee,
pgwire_test и базы 1С не менять. Сохранить команды, revision/diff, SHA модулей,
фактические PASS/FAIL и exit сервера. Недоступные проверки указать явно.

## Оставшиеся ограничения

Полный startup 1С, optimizer SET, lock_timeout, `$n`, `::`, LIMIT, каталоги
и прочие transaction options в этот этап не входят. PG SET TRANSACTION для
изменения характеристик уже открытого блока тоже не реализуется: существующая
native команда сохраняет свой контракт. Предварительный parse всего Simple Query
остаётся отложенным; ранний COMMIT сохраняется и при поздней syntax error.
Известные native defects, AUTO COMMIT/history и nested RESET ограничения
не исправлять попутно. Не объявлять эту приёмку полной совместимостью 1С или PG.
