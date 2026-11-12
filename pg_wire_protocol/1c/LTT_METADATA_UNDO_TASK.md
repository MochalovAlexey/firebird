# NAT-06: сохранить native LTT metadata undo до конца транзакции

Инструкция для отдельного диалога разработки, 12 ноября 2026 года.
Работать в `/home/aleksey/dev/my_firebird/firebird`, master Firebird 6,
ветка `pg_wire_protocol`. Проверенная основа `11dee63d2a`: P2 APP-14 исправлен,
production change в нём прошёл review без замечаний. Рабочее дерево перед новым
архитектурным заданием было чистым; не переключать ветку ради указанной ревизии.

**Минимальные необходимые изменения FirebirdSQL, максимальное переиспользование
существующих механизмов, без новых runtime dependencies.** Это отдельное исправление
native LTT, необходимое перед [APP-15](TEMPORARY_RELATIONS_TASK.md). Сделать его
с проверками и оставить для review. Не выполнять git add/commit/amend/reset/checkout,
не начинать PG TEMP реализацию в этом же diff. Коммит создаёт пользователь.

Сначала прочитать применимые инструкции, status/diff, начало [VALIDATION](../VALIDATION.md),
[BACKLOG](../BACKLOG.md) и native [описание LTT](../../doc/sql.extensions/README.created_local_temporary_tables.md).
Старый baseline reproducer и артефакты сохранять, рабочую 1С и её базы не трогать.

## Подтверждённый дефект и результат исправления

В исходном `8ffd22cb76` обычный native dialect3 без PgWire даёт:

```sql
SET AUTODDL OFF;
CREATE TABLE FILLER (V INTEGER);
COMMIT;
CREATE LOCAL TEMPORARY TABLE T (V INTEGER) ON COMMIT PRESERVE ROWS;
-- Одним EXECUTE BLOCK вставить 300000 строк в FILLER.
ROLLBACK;
```

Строки FILLER откатываются, но запись T остаётся в attachment LTT map и новом
MON snapshot. Прямой SELECT из T получает `HY000: Relation ... unavailable`.
Это несогласованное metadata state, а не работоспособная таблица после rollback.
Малый контроль без большого DML успешно удаляет определение.

GDB подтвердил `Savepoint.cpp:584 → :715`: удаление root savepoint освобождает
`LTT_UNDO_CREATE`. SIZE_THRESHOLD=32768 — оценка row bitmap bytes, не лимит строк.
Вторая ветка удаления root находится после merge (`:737`). Root может отсутствовать
и с начала транзакции при `NO AUTO UNDO`; LTT DDL после удаления root также требует undo.

Дополнительно воспроизведён дефект нынешнего coalescing без большого DML:
committed T(V), затем `ALTER TABLE T ADD Q INTEGER; DROP TABLE T; ROLLBACK;`
восстанавливает T(V,Q), хотя должна восстановиться T(V). Сам перенос ошибочной
merge-логики в другое место не решит transactional metadata rollback.

Цель: CREATE/ALTER/DROP и изменения LTT indexes корректно откатываются независимо
от наличия row-root. COMMIT сохраняет новый вид объектов. Native GC, MVCC, flags
транзакции, user-data rollback и штатные границы retaining остаются корректными.

## Минимальная архитектура

### 1. Transaction-owned список LTT undo

Сохранить нынешние `LttUndoItem`, владение `original`, вызовы `createLttAction`
из native DDL и локальные списки savepoints. Добавить один владеющий список
LTT actions в `jrd_tra` для завершённых уровней без parent; он не содержит row
bitmaps/versions. Инициализация, освобождение и ownership должны быть явными.

При обычном `Savepoint::rollforward`:

- с parent перенести LTT actions в его список;
- без parent перенести их в transaction-owned список вместо уничтожения;
- оставить нынешнюю логику освобождения row-root и row actions без увеличения
  SIZE_THRESHOLD и без повторного включения row undo после threshold.

Это покрывает root release до/после большого merge и последующие DDL statements,
когда root уже нет. Не вводить отдельный metadata-only root: нынешний `TRA_rollback`
считает успешный откат root основанием поставить `tra_committed`, что опасно,
если построчный откат фактически уже отключён.

### 2. Порядок и reuse backout

Самый простой корректный перенос — сохранить полный список в обратном порядке
операций: newest → oldest. При merge дочернего уровня его список целиком ставится
перед parent list, без разворота. `pop` каждого элемента и `push` в parent меняет
порядок и непригоден. Текущий coalescing CREATE/ALTER/DROP удалить либо заменить
только доказанными сокращениями; не копировать ветку ALTER→DROP, теряющую старый snapshot.

Предпочтение для первого fix — простое соединение списков с сохранением порядка.
Так `DROP → CREATE` откатывается как удаление новой таблицы, затем восстановление
старой; `ALTER → DROP` — восстановление dropped state, затем pre-ALTER state.
Использовать прежние действия над attachment map, relation versions и METD cache,
вынеся backout/освобождение LTT items в небольшой общий внутренний helper для
savepoint и transaction. Не дублировать switch на две реализации.

Цена простого списка: metadata snapshots сохраняются до конца транзакции, память
пропорциональна числу LTT DDL, не числу строк DML. Отразить это в отчёте и проверить
повторные ALTER/DROP/CREATE и освобождение после commit/rollback. Не обрезать журнал
лимитом и не добавлять общий event framework ради оптимизации. Более компактный
snapshot-per-name допустим только с обоснованием, если он действительно проще
корректного полного списка; не менять глобальный undo engine.

### 3. Полный rollback

В начале `TRA_rollback`, до веток force/TRA_invalidated и уничтожения savepoints,
перенести LTT actions **всех активных savepoints** в transaction-owned список.
Порядок: самый новый savepoint → более старые → прежний transaction list.
При обходе сверху вниз добавлять старые списки в хвост; повторный prepend
переворачивает историю уровней. У savepoints после переноса LTT списки пусты.

Затем оставить обычный row backout/GC выбор движка. Выполнить LTT backout отдельно,
до `retain_context` / `MetadataCache::release_temp_tables` и освобождения transaction.
User `ROLLBACK TO` продолжает откатывать только локальные savepoint actions;
более ранний transaction list при этом не затрагивается.

Важно: существующий catch в `TRA_rollback` проглатывает ошибки row undo, поскольку
TIP/GC может завершить откат данных. Он не должен скрывать ошибки metadata backout:
TIP не восстанавливает attachment map. Не возвращать успешный rollback с испорченной
схемой. Сохранить pending ownership при отказе, использовать native invalidation/
cleanup policy и явно проверить доступный failure path. Не уничтожать ещё нужный
undo в деструкторе до его применения.
Не терять item до потенциально бросающего backout и не повторять его вслепую
после `original.release()` или частичной смены map/cache: ownership и безопасное
состояние attachment при исключении должны быть определены явно.

Force rollback тоже важен: release native ITransaction вызывает его при живом
attachment. Это не только завершение процесса, где map можно просто забыть.
Проверить этот путь OO API probe и дальнейшую пригодность attachment.

### 4. Commit и retaining

Уничтожать transaction-owned snapshots после успешной фиксации, когда они уже
не требуются для rollback; обычное освобождение root перед flush не является
commit point. Если commit завершился ошибкой до фиксации, журнал должен сохраняться.
Проверить DFW и очистку relation versions, не менять их протокол наугад.

COMMIT RETAIN и ROLLBACK RETAIN завершают старый интервал metadata undo: новый
интервал не может откатить ранее закоммиченные definitions. Учесть быстрый no-op
COMMIT RETAIN в `TRA_commit`, где root не освобождается. Нельзя оставлять старый
журнал на следующий интервал только потому, что не было persistent row writes.
Native сохранение данных DELETE ROWS при retaining не переопределять.

`NO AUTO UNDO` меняет механизм отката record versions, а не означает разрешение
сохранять DDL после ROLLBACK. Для LTT metadata журнал обязателен и в этом режиме.
Не включать обратно обычный row-root ради поддержки LTT.

## Граница SQL и следующего APP-15

Новый факт: `DsqlDdlRequest::execute` (`DsqlRequests.cpp:980–988`) безусловно
запрещает DDL под пользовательским savepoint до `executeDdl`. Runtime подтвердил
0A000 для CREATE/DROP LTT. `FLAG_ACTUAL_LTT_DDL` выставляется позже и сейчас
используется для исключения DDL replication, не для снятия этого guard.
Положительное утверждение о native user-savepoint LTT DDL в прежнем плане было
неверным; native README описывает желаемое поведение, но текущий код его не даёт.

**В этом prerequisite не менять допустимость SQL.** Сохранить нынешний 0A000
для DDL под user savepoint как отрицательный контроль. Проверить native внутренние
statement savepoints, statement failure, обычные user savepoints с DML и правильный
порядок list transfer. Полная проверка LTT DDL через user SAVEPOINT будет в APP-15,
вместе с узким engine разрешением actual PG-owned LTT DDL. Persistent/GTT/native
SQL ограничения не ослабляются автоматически. Отдельный общий native redesign
savepoint DDL не входит в этот fix.

PG grammar, allocator, namespaces, catalogs/provider, PgWire state, installer и
public API/ABI/ODS этим заданием не меняются. Native multiple transactions на одном
attachment не запрещать: проверить независимые LTT и существующие правила конфликтов.
NAT-01…05 не запускать и не исправлять попутно. Новый PG namespace undo сюда не
добавлять; его activation имеет собственный lifetime в последующем APP-15.

## Проверки и сдача

Сначала зафиксировать baseline FAIL, затем теми же проверками modified PASS.
Использовать отдельный /tmp runtime и UTF8/dialect3 базы, native isql и небольшой
OO probe только там, где SQL не выражает release/TPB/ownership. Не создавать новый
тестовый framework. Проверки должны анализировать результаты, не только exit code.

Обязательная матрица:

1. Исходный большой CREATE→DML→ROLLBACK и малый контроль. MON в новой транзакции,
   реальный SELECT, повторное CREATE того же имени и сохранность persistent data.
2. LTT DDL **до и после** освобождения row-root; по возможности оба threshold paths
   (один большой statement и накопление меньших). GDB/Debug подтверждение того,
   что root действительно освобождён, без нового production tracing/порогов.
3. CREATE, ALTER columns, DROP, создание/изменение/удаление native LTT indexes;
   PRESERVE и DELETE ROWS. После rollback проверять форму, данные и применимость
   unique/index constraints, а не одно наличие строки в MON.
4. Составные цепочки committed table: ALTER→DROP, DROP→CREATE с другой формой,
   CREATE→DROP, повторные ALTER, DROP→CREATE→ALTER и повторные имена в нескольких
   внутренних уровнях. Проверить reverse chronology и statement failure rollback.
5. NO AUTO UNDO: небольшой и большой сценарии, DDL до/после DML, commit и rollback;
   отсутствие row-root не отменяет metadata undo. Native API TPB и SQL option.
6. COMMIT/ROLLBACK, COMMIT RETAIN/ROLLBACK RETAIN, затем ещё один interval с rollback;
   DDL-only и DDL+DML. Уже закоммиченные definitions не исчезают; rollback data
   остаётся правильным и на соединении-наблюдателе с новой транзакцией.
7. Native ITransaction release/force rollback при живом attachment; cleanup без
   stale map/double-free. Ошибка commit до фиксации, затем rollback, если доступен
   детерминированный штатный сценарий; непокрытые fault cases явно перечислить.
8. Native обычные таблицы/GTT, большой DML без LTT, user savepoint DML, прежний
   запрет DDL под user savepoint, два native transaction handles с независимыми
   LTT на attachment. Исходные native конфликты same-LTT DDL не переопределять.
9. Сборка и релевантные существующие native/PgWire transaction/visibility проверки;
   штатный disconnect/shutdown. Проверить освобождение snapshots после повторных
   commit/rollback; полный sanitizer campaign не требуется без обнаруженной причины.

После PASS обновить VALIDATION/BACKLOG/PLAN/PROGRESS и статус prerequisite в APP-15.
Отчёт: revision/diff/artifact hashes, точные команды, baseline/modified expected
и actual results, PASS/FAIL/SKIP, память/ownership ограничения, cleanup и server exit.
Не заявлять общий PG temporary lifecycle принятым: это только native prerequisite.
Сдать отдельный diff пользователю; возобновление APP-15 — после review этого fix.

Опора на native rollback/NO AUTO UNDO semantics:
[локальное описание](../../doc/sql.extensions/README.set_transaction.txt),
[внутренние savepoints](../../doc/sql.extensions/README.savepoints),
[официальная документация Firebird](https://firebirdsql.org/refdocs/langrefupd21-savepoint.html).
