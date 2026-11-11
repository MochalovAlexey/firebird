# Следующий этап: PostgreSQL postfix cast `expression::type`

Архитектурное решение от 10 ноября 2026 года. Статус: задание на реализацию.
Рабочий каталог: `/home/aleksey/dev/my_firebird/firebird`.
База — master Firebird 6, ветка pg_wire_protocol; проверенная ревизия `df37bb11fa`.
Не переключать ветку и не сбрасывать изменения ради совпадения с этой ревизией.

## Задание и контекст

Реализуй один ограниченный этап: postfix cast в клиентском PG dialect 4 через
существующий CastNode. Собери сервер, выполни проверки и оставь результат на review.
**Не выполнять git add, commit, amend, reset или checkout.** Пользователь сам
проверяет и коммитит. Следующий этап автоматически не начинать.

**Приоритет: минимальные необходимые изменения FirebirdSQL, максимальное
переиспользование существующих механизмов, без новых зависимостей.**
Широкое задание `/home/aleksey/dev/1c/CODEX_PGWIRE_1C_TASK.md` целиком не выполнять.
Прочитай HEAD/status, применимые инструкции репозитория, [README](README.md),
начало [VALIDATION](../VALIDATION.md), Parser/parse.y/CastNode и текущие тесты.

Уже работают SELECT без FROM в PG4, Simple scripts, Extended Query и codecs,
PG BEGIN без опций, строковая настройка и её история. Последовательные уникальные
`$1` … `$n` поддержаны в SELECT/INSERT/UPDATE/DELETE/MERGE с типами из SQL-контекста.
Повторы/перестановки/пропуски и смесь с `?` отвергаются; Parse OIDs не задают тип
движку. Native dialects 1/2/3 сохраняются. Семь startup SET принимаются;
enable_mergejoin/cpu_operator_cost/lock_timeout по решению пользователя игнорируются.

В `/home/aleksey/dev/1c/captures/20261109T105635Z/profile/selected` есть
`1::INT8` (04), `oid::bigint` (10/11), `(1.0)::int` (08).
Это основания для этапа. Полные запросы дополнительно требуют каталогов,
regnamespace, mvarchar, advisory locks и LIMIT: их выполнение здесь не обещается.

## Ограниченный контракт

В PG4 добавить `expression::type` как другую запись **существующего Firebird
CAST**, с тем же выводом типов, преобразованием значений и ошибками движка.
Это не реализация всех правил PostgreSQL coercion/typmods.

Для цели преобразования переиспользовать существующую грамматику встроенных
типов без массивов (`non_array_type`). Обязательная приёмка: INTEGER/INT,
SMALLINT, BIGINT, VARCHAR(n), BOOLEAN и DOUBLE PRECISION, уже обслуживаемые
движком и wire codecs. Остальные native types не требуют новых codecs:
существующие ограничения выдачи через PgWire продолжают действовать.

Из новых имён добавить только **INT8 → native BIGINT**, только в правой части
нового postfix cast. INT уже существует. INT8 не должен стать зарезервированным
идентификатором или изменить native dialects и разрешение доменов в обычном
`CAST(x AS INT8)`. Quoted `"INT8"` и `schema.int8` не превращать в этот alias.
Не устанавливать домен INT8 через compat.sql: alias не должен зависеть от
search_path, наличия объектов и прав на схему. INT2/INT4/FLOAT4/FLOAT8/BOOL
и общий механизм регистрации aliases в этот этап не входят.

Примеры:

```sql
SELECT 1::INT8;                         -- как CAST(1 AS BIGINT)
SELECT (1.0)::int;                      -- как CAST(1.0 AS INTEGER)
SELECT $1::INTEGER::VARCHAR(12);         -- Extended: input INTEGER, output VARCHAR
SELECT ID::bigint FROM CAST_ROWS;
UPDATE CAST_ROWS SET V = $1::BIGINT WHERE ID = $2;
```

`::` — один непрерывный токен; `: :` или `:/*comment*/:` его не заменяют.
Оператор левоассоциативный, с приоритетом выше unary +/- и существующих
арифметики/конкатенации/COLLATE/AT. Остальные native приоритеты не менять.
`a::INTEGER::BIGINT` означает `CAST(CAST(a AS INTEGER) AS BIGINT)`.

Домены, TYPE OF, schema-qualified/custom types и массивы справа от `::`
в первом срезе не поддерживаются. Не подменять bytea/mvarchar/regnamespace
ближайшим native типом. Bare VARCHAR и NUMERIC сохраняют требования и defaults
native grammar: unlimited PG types, правила truncation, PG rounding и прочие
отличия преобразований остаются отдельными задачами. `CAST(... AS native_type)`
сохраняет прежний полный контракт во всех ранее поддержанных контекстах.

## Граница сохранённого SQL

Первый срез разрешён только в корневых SELECT/INSERT/UPDATE/DELETE/MERGE,
включая их CTE/subqueries. Настоящий postfix cast в DDL, CTAS, stored PSQL,
EXECUTE BLOCK, CALL/EXECUTE PROCEDURE, USING и UPDATE OR INSERT отклоняется
до DSQL/исполнения. Для распознанной грамматики — 0A000; неверный SQL остаётся
обычной syntax error. Текст `'::'` в строках/comments/quoted identifiers
этот запрет не включает.

Причина: transformString сохраняет литералы в native notation, но не переводит
дерево выражения в SQL. Новый `::` остался бы в сохранённом источнике. CTAS
переподготавливает querySource, а stored source может позднее разбираться
native parser. Одного CastNode для корректности этих сценариев недостаточно.
В этом этапе не добавлять SQL serializer, rewriting или передачу PG dialect
во все внутренние prepare. Поддержка сохранённого SQL будет отдельным решением.

## Архитектура и порядок реализации

1. На отдельном runtime зафиксировать исходный отказ `SELECT 1::INT8` и успех
   эквивалентного native CAST. Рабочие серверы/базы пользователя не трогать.
2. В штатном lexer распознавать `::` только при pgDialect. Сохранить исходные
   offsets и native разбор `:variable`. Не переписывать входной SQL в плагине.
   INT8, если нужен отдельный token, должен иметь PG-only/non-reserved поведение.
3. В grammar добавить postfix production, создающую прежний
   `CastNode(expression, targetField)`. Переиспользовать native type productions.
   У non_array_type уже есть optional collate_clause: проверить неоднозначность
   с внешним COLLATE и результат, не полагаться только на декларацию precedence.
   Для INT8 использовать тот же descriptor и проверки client/database dialect,
   что для BIGINT; допустимо минимальное выделение общего helper/production,
   без расширения INT8 на остальные контексты типов.
4. Признак настоящего postfix cast ставить только в подтверждённой semantic
   action. Проверить generated parser: trial/lookahead не меняет состояние.
   Для ограничения корневого statement переиспользовать уже имеющийся
   parsedDmlNode/проверку конкретных AST nodes из `$n`. Не вводить новые публичные
   типы операций, statement info, AST annotations или альтернативный SQL parser.
5. Вывод типа `$1::INTEGER` выполняет прежний CastNode/ParameterNode. Не менять
   GEN_port, MAKE_parameter, Parse OID contract, Bind или codecs. По штатному пути
   wire production не требует правок; conversion/overflow проходят прежнюю
   обработку ошибок и I/T/E. Не добавлять SQL обходы в SqlInfo.
6. Собрать, проверить grammar conflicts и целевые сценарии, обновить документы.
   Если ограничение не удаётся обеспечить без расширения сохранённого SQL/API,
   описать конкретный блокер; не начинать такой рефакторинг автоматически.

## Проверки

Использовать существующий 1c/Extended harness, libpq wrappers и native OO helper;
не создавать новый framework или зависимости. Каждый runtime — в новом каталоге
/tmp с собственной базой/security DB и свободными портами. Не использовать
employee, pgwire_test или рабочую базу 1С.

- Simple Query: примеры выше без Bind, NULL, выражения/колонки, разные регистры
  INT8; совпадение результата и metadata/OID с соответствующим native CAST.
- Приоритет и ассоциативность: `1.2 + 1.6::int` против `(1.2 + 1.6)::int`,
  `-32768::SMALLINT` против `(-32768)::SMALLINT`, цепочки casts и parentheses.
  Сравнить с явно расставленными native CAST, включая ошибку промежуточного
  преобразования. Добавить проверки границ с CONCATENATE/COLLATE/AT для
  допустимых типов; не менять их существующий взаимный приоритет.
- Реальные PQexecParams и PQprepare/PQdescribePrepared/PQexecPrepared:
  `$1::INTEGER`, `$1::INT8`, `$1::INTEGER::VARCHAR(12)`, несколько уникальных
  параметров, text/binary Bind и results, NULL, repeated execution. В цепочке
  input OID=23, output OID=1043; нельзя вывести input только из внешнего cast.
  Совместимые/нулевые OID используют прежний путь, несовместимые отклоняются.
- DML с cast в SELECT/INSERT/UPDATE/DELETE/MERGE, CTE/subquery, RETURNING;
  native observer подтверждает значения и commit/rollback. Не требуется новая
  копия всей транзакционной матрицы: целевые DML, ошибка преобразования в T,
  E/25P02, rollback и следующий правильный запрос достаточны.
- Ошибки преобразования и переполнения совпадают с native CAST; Extended
  discard-until-Sync/recovery и остановка Simple script на ошибке сохраняются.
  Ошибочный target и неполный `::` не дают успешного prepare или подмены типа.
- `::` внутри обычных/Q-строк при on/off, comments и quoted identifiers не
  является оператором. Исходные line/column после оператора сохраняются.
  INT8 как имя колонки/alias, quoted имя и существующий домен INT8 в обычном
  CAST сохраняют прежнее разрешение; postfix domain/schema syntax отвергнут.
- Native dialects 1/2/3 отвергают настоящую форму `::`, принимают прежний CAST,
  идентификатор INT8 и PSQL `:variable`. На одном OO attachment проверить
  PG→native→PG/cache; ошибка native AUTOTERM не поглощает следующую команду.
- DDL/CTAS/VIEW/PROCEDURE/EXECUTE BLOCK и остальные исключённые root contexts
  с настоящим `::` отказаны до эффектов; native observer проверяет отсутствие
  объектов/изменений. Их прежний CAST работает; literal `'::'` разрешён.
  Не обходить guard через вложенное выражение, CTE или PSQL local function.
- Прежние ограничения `$n`, прежний `?` и контекстный вывод типов сохраняются.
  Новая форма не делает допустимыми repeated/gapped/reordered inputs.

Обновить только непосредственно связанные отрицательные ожидания `::`, если
они есть. Исторический P3-тест `pgwire_libpq_query.c` ожидает 0A000 от VARCHAR,
хотя текущий PgWire возвращает `x`: это зафиксированное устаревшее ожидание,
не основание менять engine или скрывать FAIL. Модернизация всего P3 сюда не входит.

Сборка: `/tmp/firebird-pgwire-1c-build`, `make -j2`, затем
`make -C gen -j2 TARGET=Debug pgwire_tests`. Parser baseline — 154 shift/reduce,
11 reduce/reduce; новые конфликты не принимать без разбора причин.
Регрессии: штатные SQL/session/isql units, --parameters, --strings (включая
native/cache/stored source), Extended и Simple suites. --settings/--begin
повторять при изменении общих keywords/control paths либо выявленной регрессии.
Известные native crash repro и нагрузку 1С не запускать.

Записать в VALIDATION revision/diff, SHA модулей, команды, фактические результаты,
пропуски и exit каждого сервера; обновить README/COMPATIBILITY/PLAN/TEST_PLAN.
Разделить новую syntax support и остающиеся типы/каталоги. Не объявлять успешное
выполнение исходного запроса 04 или открытие базы 1С по синтетическим проверкам.

Основания: [PG type casts](https://www.postgresql.org/docs/18/sql-expressions.html#SQL-SYNTAX-TYPE-CASTS),
[operator precedence](https://www.postgresql.org/docs/18/sql-syntax-lexical.html#SQL-PRECEDENCE),
[integer aliases](https://www.postgresql.org/docs/18/datatype-numeric.html#DATATYPE-INT).
