# APP-14: видимость постоянных отношений

Задание от 11 ноября 2026 года для отдельного диалога разработки.
Рабочий каталог `/home/aleksey/dev/my_firebird/firebird`, master Firebird 6,
ветка `pg_wire_protocol`; проверенный HEAD `4520f371e9`. Не переключать ветку
и не сбрасывать изменения ради этой ревизии. APP-13 прошёл review и независимые
проверки имён/строк, см. начало [VALIDATION](../VALIDATION.md).

**Минимальные необходимые изменения FirebirdSQL, максимальное переиспользование
штатных механизмов, никаких новых runtime dependencies.** Сделать весь описанный
сценарий с проверками, оставить результат пользователю. Не выполнять git add,
commit, amend, reset или checkout. Не реализовывать весь исходный 1С roadmap
и не переходить к следующему этапу автоматически.

Прочитать применимые инструкции, status/diff, [README](README.md),
[PROGRESS](PROGRESS.md), [BACKLOG](../BACKLOG.md), текущую COMPATIBILITY и APP-13.
Затрагиваемые ID: новый APP-14, часть APP-01. APP-02/03/04/06, SQL-09 не закрывать.

## Результат и граница

Установленная функция `pg_catalog.pg_table_is_visible(BIGINT) RETURNS BOOLEAN`
определяет, выберет ли обращение по точному имени без схемы именно данное
постоянное отношение из нынешнего pg_class. Это рабочий путь:
native catalog → lookup → функция → libpq → сопоставление с реальным SELECT.
Не отдельный helper без публичного результата и не проверка наличия строки.

В двух схемах создать одноимённые таблицы с различимыми данными. При перестановке
пути поиска одновременно меняются выбранная SELECT таблица и её visibility.
При этом отсутствие SELECT права не должно само по себе делать таблицу невидимой.
Для сравнения после смены пути готовить SELECT oracle заново. Уже подготовленный
native SELECT может сохранять прежнюю привязку; не вводить новый механизм его
перепривязки. Повторно подготовленный вызов visibility не требуется: сам helper
должен читать актуальное состояние при каждом Execute. Действующие механизмы
PgWire Bind/reprepare не менять.

**Это ограниченный профиль на существующем явно заданном пути APP-13.**
Порядок `"public", "pg_catalog", "SYSTEM"` и session lifetime SET SEARCH_PATH
сохраняются. Поддержаны явные списки существующих/отсутствующих schemas без
специальных `$user`/`pg_temp`, с явно перечисленным lowercase `pg_catalog`.
Все существующие schemas пути должны быть доступны вызывающему по USAGE;
native local temporary tables в attachment отсутствуют. Проверить эти условия
при выполнении helper, а не только при установке или Prepare.

Если условий нет, не возвращать правдоподобный FALSE/TRUE. Отсутствие явного
pg_catalog, специальные элементы пути и активная native LTT дают `0A000` с
понятной причиной. Проверка USAGE использует штатный SCL; обычный native отказ
в доступе допустим и документируется как ограничение. Не перехватывать все
`isc_no_priv`: тот же код может сопровождать повреждение ACL. Прочие ошибки
также не превращать в FALSE/NULL.

Этот контракт позволяет отложить полноценный effective PG search_path, сохранив
правильный результат на поддержанном пути. Запрет native LTT здесь относится
к вызову функции видимости, а не к остальным запросам сессии.

## Архитектура: один небольшой engine helper и SQL-обёртка

1. **SQL-каталог остаётся владельцем PG identity.** В отдельном атомарном installer
   создать lowercase функцию в существующей `"pg_catalog"`. По входному OID
   получить точное relname из текущего view, вызвать helper, сравнить native
   relation ID с `oid - 1`. Сохранить mapping APP-12 и его область rows.
   Публичный аргумент пока native BIGINT/wire20; результат BOOLEAN/wire16.
   Не объявлять OID 26 и не менять codecs или metadata по происхождению колонки.
2. **Engine helper разрешает точное имя.** Предлагаемое имя
   `RDB$PG_RELATION_ID(exact_name)`; результат nullable native INTEGER.
   Это явно ограниченный служебный SQL helper данного профиля, а не новый общий
   metadata API. Реализовать через существующий SysFunction/BLR dispatch с
   минимальной регистрацией builtin в parser. Не вводить отдельный BLR opcode,
   публичный OO API, ODS-поля, registry, UDR-модуль или новый тип данных.
3. На Execute helper сначала проверяет условия профиля выше. Для поиска
   переиспользовать цепочку из `evlMakeDbkey`: `Attachment::qualifyExistingName`
   / `MET_qualify_existing_name`, `MetadataCache::getVersioned<Cached::Relation>`,
   `getId()`. Не копировать resolver и не разбирать SQL-текст. Аргумент — значение
   точного имени объекта, не SQL identifier с кавычками и не schema-qualified
   строка; точка и двойная кавычка внутри имени — обычные символы имени.
   Отсутствие отношения в пути возвращает NULL. Ошибки metadata не маскировать.
4. USAGE проверять штатным `SCL_check_schema` для существующих schemas пути,
   в контексте вызывающего. Не проверять SELECT на целевую таблицу и не создавать
   копию ACL-логики запросами к RDB$USER_PRIVILEGES. Не повышать права.
   Наличие LTT читать непосредственно из `att_local_temporary_tables`.
   MON$LOCAL_TEMPORARY_TABLES не годится: его transaction snapshot может отставать.
5. SQL-обёртка — `SQL SECURITY INVOKER`, без автономных транзакций, dynamic SQL,
   user-data SELECT, DDL и служебной записи на каждом вызове. Установка даёт
   PUBLIC только необходимые EXECUTE; существующие schemas/views/grants сохраняет.
   Нативные API, PgWire executor/session и нормализация имён не расширяются.

Почему выбран builtin: добавление метода в SYSTEM.RDB$SQL потребовало бы отдельно
решать доступность новой system-package metadata в уже созданной базе с той же
ODS. Для этого этапа не менять ODS и не править системные таблицы вручную.
Builtin использует существующий способ хранения/разбора системных функций,
а отдельно устанавливаемая SQL-обёртка работает и на базе, созданной предыдущей
сборкой. Доказать это отдельной проверкой обновления runtime.

Не связывать engine helper с именем `ibversion` или выбранными запросами 1С.
В engine не обращаться к PGWIRE_COMPAT/pg_class: это ответственность SQL-обёртки.
Не распознавать PG-режим по имени пользователя, порту или имени attachment.
Helper вызывается явно; старые native resolver/MAKE_DBKEY/DDL сохраняются.

## Точный контракт результатов

| Вход/состояние | Результат публичной функции |
|---|---|
| NULL | NULL |
| OID отсутствует в нынешнем pg_class, включая 0/отрицательное/большое BIGINT | NULL в принятом native BIGINT профиле |
| Отношение есть в каталоге, но не находится через текущий путь | FALSE |
| Первым находится другое отношение с таким точным именем | FALSE |
| Найден тот же native relation ID | TRUE |
| Вызывающий имеет USAGE схем пути, но не SELECT на таблицу | visibility по имени; реальный SELECT отдельно получает native permission error |
| Неподдержанное состояние пути/LTT или ошибка SCL/metadata | Явная ошибка, не FALSE/NULL |

Для NULL/неизвестного catalog OID обёртка может вернуть NULL без вызова helper.
Для известного OID нельзя обходить guard даже если он заранее выглядит вне пути.
Границы native BIGINT не являются полноценным PostgreSQL oid input contract.
Indexes/sequences/external/virtual/GTT/LTT/package relations не расширяют
catalog rows этого этапа; известные native reuse/restore ограничения остаются.
NULL helper при отсутствии lookup превращается обёрткой в FALSE, не SQL NULL.

Helper зависит от текущих прав, пути и metadata: нельзя вычислять/кэшировать
его результат при Prepare или объявлять DETERMINISTIC. Не добавлять свой cache.
Ввод helper с NUL/непредставимым именем не должен тихо нормализоваться в другой
объект. Проверить аргументы до потери bytes в MetaName; длины остаются native.

## Порядок реализации и проверки

1. **Зафиксировать основу.** Проверить существующий путь и точки reuse, baseline
   ветки/артефактов. Минимальный код helper, неизменный native resolver; затем
   installer публичной функции. Не делать отдельный большой рефакторинг SCL.
2. **Проверить цельный сценарий.** В отдельной UTF8/dialect3 базе с APP-10…13
   получить OIDs реальных таблиц/views, вызвать функцию через настоящий libpq
   и сопоставить результаты с native каталогом и SELECT из тех же объектов.
3. **Расширить существующий harness.** Один новый mode/helper в нынешних tools;
   переиспользовать runtime, libpq/raw/native, users, installer и cleanup helpers.
   Свежая сборка и релевантные unit/parser/имена/catalog/session регрессии.
   После PASS повторять проверки только при новых изменениях или основаниях.
4. **Оставить проверяемый diff и отчёт.** Обновить README/COMPATIBILITY/PLAN/
   BACKLOG/PROGRESS/TEST_PLAN/VALIDATION. Команды, source revision/diff и hashes,
   PASS/FAIL/SKIP, metadata, native oracle, server exit; без staging/commit.

Обязательная матрица:

- Два одноимённых persistent table в разных schemas, view, отношение вне пути,
  несуществующая schema в пути, quoted mixed-case/Unicode/dot/doubled-quote names.
  Перестановка SET SEARCH_PATH с явным pg_catalog: реальный SELECT и visibility
  согласованы. Проверить верхнерегистровые native objects через точные quotes.
- NULL/0/неизвестный OID/границы BIGINT; boolean text/binary metadata и bytes;
  все результаты проверяются, не только успешность prepare/execute.
- Simple Query и named Extended Parse/Bind/Execute, повторное исполнение после
  SET path/RESET, независимые подключения. SET сохраняет существующий session
  lifetime; не заявлять PG rollback semantics для этой настройки.
- Ordinary user с USAGE, без SELECT на пользовательской таблице: visibility
  успешна, чтение данных запрещено. Другая schema пути без USAGE: явный отказ;
  owner/role/PUBLIC cases сверить с native SCL. Обёртка не использует права owner
  вместо вызывающего. GRANT/REVOKE проверить в рамках native cache semantics.
- Путь без явного pg_catalog и с `$user`/`pg_temp`: 0A000. Отдельно сохранить
  прежнюю возможность выполнить обычный SELECT с таким native SET path.
- Native LTT создать в той же транзакции после первого чтения MON; visibility
  обязана отказать. DROP LTT снимает ограничение по живому состоянию. Использовать
  поддержанные native команды, не известные NAT-* crash combinations. Наличие
  LTT во втором attachment не мешает первому. Ошибка в T даёт обычные E/recovery.
- DDL lifetime/rollback на поддержанных границах; после DROP OID не выдаёт
  фиктивный TRUE. Native DDL под user savepoint по-прежнему запрещён, не менять.
- Installer fresh/success/rollback/repeat/collision, права, отсутствие лишних
  объектов после ошибки. Старая база, созданная runtime APP-13, после штатного
  закрытия открывается новым runtime; installer/function доступны без ODS migration.
- Native1/2/3, прежний MAKE_DBKEY и поиск объектов не меняются. Штатный shutdown,
  cleanup всех временных ресурсов, coexistence endpoints.

Рабочий сервер/1С и их базы не менять. Все runtime/security/databases — отдельные
`/tmp`; приватный capture не копировать в Git. Нужные loopback действия выполнять
обычным разрешённым для тестов способом. Не добавлять таймауты в production wire.

## Что остаётся после этапа

Исходный selected04 всё ещё требует `regnamespace` и `pg_my_temp_schema`:

```sql
SELECT 1::INT8 FROM pg_catalog.pg_class
WHERE pg_catalog.pg_table_is_visible(OID)
  AND relkind='r' AND relname='ibversion'
  AND relnamespace IN ('public'::regnamespace, pg_catalog.pg_my_temp_schema())
LIMIT 1
```

Заменённый namespace predicate использовать лишь как отдельный synthetic test,
не считать прохождением исходного SQL. Не добавлять постоянный TRUE или фиктивную
temporary schema. Не подменять regnamespace простым BIGINT: его type identity,
привязка имён, text output и binary format требуют отдельного решения.
Полный PG effective path, пропуск schemas без USAGE, implicit pg_catalog, `$user`,
temporary namespace и transactional SET остаются в APP-01/04/SES-06.
Начальный `_` (SQL-09) — отдельная лексическая доработка с сохранением introducers.
Открытие реальной 1С и Гилёв не входят в приёмку APP-14.

Семантические ориентиры: [PG visibility functions](https://www.postgresql.org/docs/18/functions-info.html),
[PG search_path](https://www.postgresql.org/docs/18/runtime-config-client.html#GUC-SEARCH-PATH),
[PG object identifier types](https://www.postgresql.org/docs/18/datatype-oid.html).
Они описывают целевое полное поведение; ограничения этого этапа перечислены выше.
