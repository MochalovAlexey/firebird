# Записанный профиль 1С: этапы совместимости

Незавершённые этапы, ограничения частичных реализаций и пробелы приёмки ведутся
в общем [BACKLOG](../BACKLOG.md). Обновлять его после каждого этапа и review.

Задание: `/home/aleksey/dev/1c/CODEX_PGWIRE_1C_TASK.md`, 9 ноября 2026 года.
Исходная ревизия: `0e505759d0deb1693d912bbc9fee978a55d1e1f0`.
Корпус: `/home/aleksey/dev/1c/captures/20261109T105635Z`, 24 selected examples
и 243 формы. SQL сохраняется без изменений; DETAIL параметров не считается
записью Bind. Полный журнал, конфигурационные BLOB и credentials в Git не входят.

## Первый срез: SELECT без FROM и идентификация подключения

Первый запрос пяти новых соединений — `SELECT pg_catalog.pg_backend_pid()`
(selected `01-backend-pid`, capture_seq 98). Для его выполнения используются:

- клиентский `SQL_DIALECT_PG` (4), передаваемый PgWire в существующий prepare;
  dialect БД/DPB и `SQL_DIALECT_CURRENT` остаются 3;
- необязательный FROM только в PG-режиме штатного parser; пустой список источников уже поддержан
  существующим DSQL/optimizer как одна строка, без подстановки RDB$DATABASE;
- реальная PSQL-функция из [compat.sql](compat.sql), возвращающая
  `CAST(CURRENT_CONNECTION AS INTEGER)`;
- существующие schemas, grants, metadata и INTEGER codec PgWire.

Функция реализует **ограниченный контракт одной базы**: стабильный идентификатор
attachment, различный у одновременных соединений и совпадающий с
`MON$ATTACHMENT_ID`. Это не PID процесса ОС. В Super процесс общий для сессий,
поэтому возвращение SERVER_PID не удовлетворяет наблюдаемой уникальности.
Преобразование в int4 проверяет диапазон и выдаёт ошибку при переполнении;
усечение, modulo и возможные коллизии не применяются. Уникальность между разными
базами и связь с PostgreSQL CancelRequest не заявляются. Это не полный контракт
PostgreSQL backend identity и не завершение этапа 1 или открытия базы 1С.

Новых методов API/ABI, runtime-зависимостей, конфигурационных параметров,
context keys, счётчиков и реестров нет. Добавлена одна публичная константа
к существующему аргументу SQL dialect. Плагин не переписывает SQL.
Проверки и фактические результаты фиксируются в [VALIDATION.md](../VALIDATION.md).

### Граница диалекта

PgWire выбирает dialect 4 при Simple Query, Extended Parse и повторной подготовке
portal. Этот режим включает PG-дополнения грамматики; типы и арифметика сохраняют семантику dialect 3. Обычные одинарные строки учитывают новую настройку; native Q-строки и двойные кавычки сохраняются. Поддержаны `SELECT` без `FROM` и описанный ниже
фиксированный профиль `lc_messages`/`client_encoding`, а также изменяемый `standard_conforming_strings`. Последовательные типизированные `$1` … `$n` поддержаны в ограниченном контракте ниже;
Postfix `::`, literal `LIMIT n` и PG BEGIN без опций поддержаны в ограниченных контрактах ниже.

Ключ штатного statement cache содержит исходный клиентский dialect и эффективный режим строк: PG и
native планы не смешиваются. Обычные native dialects 1/2/3 по-прежнему требуют
FROM. Базу не нужно создавать или переводить в dialect 4; DPB и isql его не
принимают. Режим выбирается явно на границе API подготовки SQL, а не по имени
пользователя или глобальной настройке.

Сохранённые PSQL-объекты исполняют обычный BLR. Наследование PG-режима для
динамического `EXECUTE STATEMENT`, внутреннего SQL DDL и SQL-репликации в этот
срез не входит. Установочная функция написана на native PSQL. Имена колонок и
идентификаторы пока следуют Firebird: результат исходного запроса называется
`PG_BACKEND_PID`, тогда как эталон возвращает `pg_backend_pid`.

## Последовательные numbered parameters: первый срез

Только клиентский PG dialect 4 распознаёт отдельный `$` + ASCII digits. В корневых
SELECT/INSERT/UPDATE/DELETE/MERGE, включая CTE/subqueries, каждый номер должен
встретиться ровно один раз: `$1`, затем `$2`, … . Тип выводится обычным DSQL из
колонки, DML или `CAST($n AS native_type)`. Значения передаются отдельно через
Extended Bind; Simple с типизированным `$1` возвращает 0A000, без исполнения с NULL.

```sql
SELECT CAST($1 AS INTEGER), CAST($2 AS VARCHAR(40));
SELECT ID FROM PARAM_ROWS WHERE NAME = $1;
INSERT INTO PARAM_ROWS(ID, NAME) VALUES($1, $2);
UPDATE PARAM_ROWS SET NAME = $1 WHERE ID = $2;
```

| Форма | Результат |
|---|---|
| Повтор, пропуск, первый `$2`, перестановка, смесь `?`/`$n` | 0A000 до успешной подготовки |
| `$0` / `$000` | 42P02 |
| Номер >32767, включая переполнение при чтении | 54000 до narrowing/allocation |
| `$01` / `$0002` | Номера 1 / 2 |
| `$1foo`, `$1_foo`, `$1$foo` | 42601, не неявный alias |
| Bare `$`, `$-1`, `$ 1`, dollar quotes | Прежние parser errors |
| Синтаксически распознанные CALL/EXECUTE PROCEDURE/BLOCK, USING, DDL/stored PSQL, UPDATE OR INSERT с `$n` | 0A000 до DSQL transformations; неправильная грамматика остаётся syntax error |

`$n` внутри ordinary/Q strings, comments и quoted identifiers — обычный текст;
A$1 и RDB$DATABASE сохраняются. Native dialects 1/2/3 не принимают `$n`, прежний
`?` и идентификаторы с `$` работают. Cache разделён по dialect и string mode;
line/column относятся к исходному SQL. 32767 ограничивает номер и не обещает
исполнения сообщения такого размера: ограничения engine/message/codecs остаются.

Parse OID hints проверяются существующим InputCodec **после** engine inference:
0/omitted используют выведенный тип, совместимые OID принимаются, несовместимые
дают 0A000, избыточный список — 08P01. `SELECT $1` без контекста даёт HY004
как `SELECT ?`, в том числе с supplied OID=23. Новых API/flags/type fallback нет.
MAKE_parameter/GEN_port, Extended и codecs не изменены; номера проверяются в
подтверждённых semantic actions (`!yytrial`), допустимый корень — по AST.

Это ограничение строже PostgreSQL, где номер определяет позицию независимо от
повторов и порядка: [positional parameters](https://www.postgresql.org/docs/18/sql-expressions.html#SQL-EXPRESSIONS-PARAMETERS),
[PREPARE](https://www.postgresql.org/docs/18/sql-prepare.html),
[Parse/Bind messages](https://www.postgresql.org/docs/18/protocol-message-formats.html).
Произвольная нумерация, OID-driven inference, новые codecs и полная
параметризация Psycopg 3 остаются отдельными этапами.

`pgwire_1c_smoke.py --parameters` проверяет реальный libpq Bind, metadata,
92 разных значения с native readback, поиск по FILENAME, CTE/MERGE/RETURNING,
I/T/E/recovery и запреты. Это синтетическая форма selected 06/19, **не** исходная
схема, BinaryData/bytea или открытие базы 1С. Результаты — в [VALIDATION](../VALIDATION.md).

## Postfix cast `expression::type`: первый срез

В клиентском PG dialect 4 непрерывный токен `::` создаёт прежний CastNode.
Значения, округление, truncation, overflow и вывод входных типов соответствуют
**Firebird CAST**, а не всем PostgreSQL coercion/typmod rules. Приоритет `::`
выше unary +/−, арифметики, конкатенации, COLLATE и AT; цепочка левоассоциативна.
Прежние взаимные native приоритеты не меняются. `: :` и `:/*comment*/:` — не оператор.

```sql
SELECT 1::INT8;
SELECT (1.0)::int;
SELECT $1::INTEGER::VARCHAR(12); -- input INTEGER (OID 23), output VARCHAR (1043)
UPDATE CAST_ROWS SET V=$1::BIGINT WHERE ID=$2;
```

Цель переиспользует `non_array_type`: native builtin types без массивов. Проверены
INTEGER/INT, SMALLINT, BIGINT, VARCHAR(n), BOOLEAN, DOUBLE PRECISION и дополнительные
native границы. Bare VARCHAR сохраняет native default 255, NUMERIC — native defaults;
это не unlimited PG VARCHAR/NUMERIC. Существующие codec limits, включая выдачу non-binary BLOB
и типов с time zone, сохраняются; синтаксис не добавляет новых wire codecs.

Единственный новый alias — unquoted INT8 → BIGINT **только** после `::`, без учёта
регистра. INT8 остаётся именем колонки/alias; `CAST(x AS INT8)` и quoted INT8
в обычном CAST по-прежнему разрешают существующий домен. Postfix `::"INT8"`,
`::schema.int8`, домены/TYPE OF/arrays, INT2/INT4/FLOAT4/FLOAT8/BOOL, bytea,
mvarchar и regnamespace не поддерживаются. Alias не зависит от schema/search_path.

Первый срез разрешён только в корневых SELECT/INSERT/UPDATE/DELETE/MERGE, включая
CTE/subqueries. Распознанные DDL/CTAS/VIEW/stored PSQL, EXECUTE BLOCK,
CALL/EXECUTE PROCEDURE, USING и UPDATE OR INSERT с настоящим `::` дают 0A000
до DSQL/эффектов; неверная грамматика остаётся syntax error. Literal `'::'`,
Q-strings, comments и quoted identifiers guard не включают. Прежний CAST в этих
контекстах сохраняется. Это исключает сохранение PG syntax в native source:
serializer, rewriting и расширение внутренних prepare в этот этап не входят.

Parse OID по-прежнему не задаёт тип engine. В `$1::INTEGER::VARCHAR(12)` внутренний
CastNode выводит INTEGER input, внешний меняет результат; при несовместимом OID
остаётся 0A000. Ограничения последовательных `$n` и native `?` сохраняются.
Native dialects 1/2/3 не принимают `::`, но сохраняют CAST, INT8 identifiers и
`:variable`. Source offsets и разделение statement cache по dialect сохранены.

Приёмка — `pgwire_1c_smoke.py --casts` и прежний OO helper внутри `--strings`;
команды, SHA, разбор grammar conflicts и результаты — в [VALIDATION](../VALIDATION.md).
Это синтетические проверки форм из selected 04/08/10/11, не выполнение исходных
запросов с каталогами/custom types/advisory locks/LIMIT и не открытие базы 1С.
Основания: [type casts](https://www.postgresql.org/docs/18/sql-expressions.html#SQL-SYNTAX-TYPE-CASTS),
[precedence](https://www.postgresql.org/docs/18/sql-syntax-lexical.html#SQL-PRECEDENCE),
[integer aliases](https://www.postgresql.org/docs/18/datatype-numeric.html#DATATYPE-INT).

## LIMIT с целочисленным литералом: первый срез

По [LIMIT_TASK](LIMIT_TASK.md), 11 ноября 2026 года, PG4 принимает SELECT-tail
`LIMIT n` после необязательного ORDER BY. Count — только `long_integer` /
NUMBER32BIT, значение 0…2147483647. Сохраняются штатные integer spellings:
ведущие нули, 0x/0o/0b и `_`; отдельного scanner или narrowing нет.

```sql
SELECT 42 LIMIT 0;
SELECT ID FROM LIMIT_ROWS ORDER BY ID LIMIT 2;
SELECT FILENAME FROM LIMIT_FILES WHERE FILENAME=$1 LIMIT 1;
INSERT INTO LIMIT_COPY(ID) SELECT ID FROM LIMIT_ROWS ORDER BY ID LIMIT 2;
```

Ограничивается готовый результат своего SELECT уровня после DISTINCT/GROUP/ORDER.
Внешний LIMIT относится ко всему UNION/UNION ALL, внутренний в скобках — только
к своему operand/subquery. Native правила UNION ORDER BY сохраняются: позиция
колонки, например `ORDER BY 1`; вычисленным столбцам в body wrapper нужны aliases.
Без ORDER BY конкретные строки/порядок не обещаются.
LIMIT 0 сохраняет metadata, не выдаёт DataRow и завершает `SELECT 0`.
Обычные Bind-параметры вне count работают по прежнему контракту. Execute maxRows
приостанавливает курсор; SQL LIMIT сохраняется между Execute, счётчики разных
portals независимы.

LIMIT — reserved keyword только в PG4: `SELECT 1 LIMIT` является ошибкой.
Native1/2/3 сохраняют идентификатор LIMIT; quoted `"LIMIT"` работает в dialect3/4.
ALL/NULL, `$n`/`?` в count, выражения/скобки/знаки, decimal/exponent literals,
числа вне диапазона, PG OFFSET и `LIMIT offset,count` отвергаются parser.
FIRST/SKIP с LIMIT на одном уровне получает прежний `isc_dsql_firstskip_rows`;
разные уровни допустимы. Native ROWS и OFFSET…FETCH отдельно сохраняются,
их смешение с LIMIT на одном уровне — syntax error.

Корневые операторы — SELECT/INSERT/UPDATE/DELETE/MERGE с SELECT subqueries/CTE.
Суффикс LIMIT у UPDATE/DELETE не добавлен. DDL/CTAS/VIEW/stored PSQL/BLOCK,
CALL/PROCEDURE/USING/UPDATE OR INSERT с распознанным LIMIT получают 0A000
до DSQL; неверная грамматика остаётся syntax error. Строки/Q-strings/comments
и quoted identifiers guard не включают; native FIRST/ROWS в сохранённом SQL
продолжают работать.

Production изменён только в Parser.cpp/h и parse.y: PG token, подтверждённый
parser flag, прежний root guard и RowsClause на обоих путях SELECT, включая
DFLAG_BODY_WRAPPER у query_primary. Исполнение использует прежние PASS1_limit,
rse_first и FirstRowsStream; plugin, API, metadata, codecs и optimizer не менялись.
`pgwire_1c_smoke.py --limits`, OO helper в `--strings` и обязательные регрессии
прошли; команды и evidence — в [VALIDATION](../VALIDATION.md).

SQL-01 закрывает только этот literal-срез; SQL-02 остаётся открытым. NULL count
нельзя просто передать native stream: там он означает ноль строк. Исходные
selected 04/17 ещё требуют каталогов/custom types; их полная приёмка и открытие
базы 1С не заявляются.

## Binary BLOB как bytea: только output

По [BINARY_BLOB_OUTPUT_TASK](BINARY_BLOB_OUTPUT_TASK.md), 11 ноября 2026 года,
ResultCodec принимает только native SQL_BLOB subtype 0 (BINARY), с корректным
размером ISC_QUAD и прежними проверками offsets/null offsets/message length.
RowDescription: OID 17, typeSize -1, typmod -1. Text output — ASCII `\x` и
lowercase hex по две цифры на байт; binary output — исходные байты. NULL имеет
wire length -1 и не открывает BLOB. Пустой ненулевой BLOB — text `\x` либо
binary length 0. Нулевой ID не заменяет null indicator. NUL и invalid UTF8
в payload разрешены; charset conversion и validateText к содержимому не применяются.

Общий путь работает в Simple и Extended, для SELECT cursor, singleton output,
DML RETURNING и native EXECUTE BLOCK/PROCEDURE. Декодирование получает borrowed
attachment и текущую transaction только на одну строку. Describe/Parse/Bind
ничего не открывают; codec/prepared metadata не сохраняют transaction, handle
или содержимое BLOB. Повторный Bind после commit/rollback и независимые portals
проверены. Все BLOB закрываются до Protocol::dataRow; consuming close использует
RefPtr::clear без повторного release, ошибки сохраняют прежнюю engine mapping.

Чтение идёт фиксированными порциями 4096 байт до RESULT_NO_DATA; RESULT_OK и
RESULT_SEGMENT добавляют байты без разделителей. Пустой сегмент не означает EOF.
Единый бюджет строки берётся из фактического Protocol limits.frameBytes (сейчас
1 MiB): `4 + 2 + 4 * число полей + сумма ненулевых wire values`, без тега D.
Text bytea занимает `2 + 2 * payload length`. Все колонки/форматы делят этот
бюджет; арифметика и размер проверяются до добавления каждой порции/hex output.
При полном заполнении ещё одно чтение отличает EOF от превышения. Размер всей
BLOB заранее не резервируется; Protocol сохраняет независимый preflight.

Превышение даёт 0A000 без усечения и частичного DataRow. Ранее отправленные строки
не отзываются. Ошибка output возникает после engine execution: прежняя обработка
откатывает implicit сегмент либо переводит T→E; для DML RETURNING проверены
rollback/recovery и отсутствие строк через native observer. Это не отказ до
любых side effects. Unsupported TEXT/другие BLOB subtype по-прежнему дают 0A000
на metadata до исполнения.

APP-07 не добавлял input и не менял InputCodec; bounded input добавлен в APP-08
ниже. SQL type/cast `bytea`, literals, сравнения/индексы/OCTETS, large-object API
и SET bytea_output остаются вне обоих этапов. Bind остаётся до startImplicit.
Engine/parser/публичные API/зависимости также не менялись.

`pgwire_1c_smoke.py --blobs` использует прежние libpq/raw/native wrappers и небольшой
native OO fixture helper. Проверена неизменная форма selected06 с WHERE `$1` и
ORDER BY PartNo на синтетической PUBLIC.PARAMS, без исходных данных или записанного
Bind. APP-07 закрыт по реализации и оставлен на review; APP-02/SQL-06 и открытие
базы 1С остаются отдельными задачами. Команды, SHA, размеры и результаты — в
[VALIDATION](../VALIDATION.md).

## Установка на отдельной тестовой базе

1. Пересобрать сервер и плагины текущей ветки вместе. Использовать отдельную
   UTF8/dialect 3 базу Firebird 6 и настоящего пользователя с правами.
2. Настроить PgWire по [quickstart](../quickstart.md), добавив alias этой базы
   в `databases.conf` и `DatabaseAliases`. В `Config = PgWireConfig` установить
   существующий `Workers = 8`: в записанной нагрузке шесть соединений.
3. Через native isql, от владельца базы, установить SQL-функцию:

   ```sh
   /path/to/firebird/bin/isql -bail -user SYSDBA -password YOUR_PASSWORD \
     -i pg_wire_protocol/1c/compat.sql inet://127.0.0.1:3050/pgwire_1c
   ```

4. Через psql на PgWire-порту выполнить исходный запрос:

   ```sql
   SELECT 42 AS "answer";
   SELECT pg_catalog.pg_backend_pid();
   ```

Скрипт создаёт schema при отсутствии и устанавливает/обновляет одну функцию;
USAGE schema и EXECUTE function выдаются PUBLIC. Он не создаёт таблицы 1С,
не переносит её конфигурационные данные и не меняет глобальную конфигурацию
сервера. Существующую PostgreSQL/1С базу он не подготавливает автоматически.

## Следующие небольшие этапы

[Синтаксический приём трёх startup SET](IGNORED_STARTUP_SETTINGS_TASK.md)
без эффекта реализован по явному решению пользователя; контракт описан ниже.
[PG BEGIN](BEGIN_TASK.md) реализован отдельным этапом через существующий
SetTransactionNode и общий executor. Оба этапа в `1f9af20db4`/`97e44d0188`
прошли архитектурный review и повторную целевую проверку; результаты — в VALIDATION.

Срез [последовательных $1 … $n](NUMBERED_PARAMETERS_TASK.md) реализован через
штатные Parser/ParameterNode и Bind; ревизия `df37bb11fa` прошла архитектурный
review и повторную целевую приёмку, см. VALIDATION.
Разрешены уникальные номера по порядку в SELECT/INSERT/UPDATE/DELETE/MERGE
с типами из SQL-контекста. Точный контракт приведён выше.

Реализован [postfix cast expression::type](POSTFIX_CAST_TASK.md) через
существующий CastNode и native types, с INT8 → BIGINT только в postfix target.
Первый срез ограничивается SELECT/DML без сохранения PG SQL в metadata.
Ревизия `9f94454527` прошла архитектурный review и повторную целевую приёмку;
контракт описан выше, результаты — в VALIDATION.

Реализован [LIMIT с целочисленным литералом](LIMIT_TASK.md) через RowsClause и
штатное исполнение: count 0…2147483647, без PG OFFSET/ALL/NULL/count-параметров.
Ревизия `ccd81f6473` прошла review и повторную целевую приёмку; SQL-01 закрыт,
расширенный LIMIT/OFFSET остаётся открытым SQL-02 в [BACKLOG](../BACKLOG.md).

Реализована [выдача binary BLOB как bytea](BINARY_BLOB_OUTPUT_TASK.md), APP-07:
ResultCodec + штатный IBlob, text/binary output и общий предел DataRow. Ревизия
`5478a589d8` прошла review и повторную целевую приёмку; APP-07 закрыт. Большие
входные BLOB, bytea grammar и полный APP-02 остаются открытыми, как и каталоговый APP-01.

Реализован [bytea input через native VARCHAR OCTETS](BINARY_BLOB_INPUT_TASK.md),
APP-08: text hex/escape и raw binary до 65533 decoded bytes на параметр,
physical layout по фактическим длинам Bind. Engine создаёт BLOB штатным MOVE
при Execute; новых handles/transaction lifecycle в плагине не требуется.
Проверены libpq/raw/native readback, selected23 на синтетической PUBLIC.FILES
и 68 BLOB среди 92 mixed параметров; APP-08 оставлен на review. Большие значения
остаются APP-09; исходные SQL bytea types/casts и APP-05 не закрываются.

Срез [фиксированной диагностики](FIXED_DIAGNOSTICS_TASK.md) реализован через
существующий SetPgSessionNode: только `escape_string_warning=off` и
`client_min_messages=error`. Ревизия `4abc5af919` прошла архитектурный review
и повторную целевую проверку; результаты — в VALIDATION.

| Группа исходного задания | Текущее состояние / что требуется |
|---|---|
| 1. Начало сессии | SELECT без FROM, ограниченная identity-функция, последовательные скрипты и все семь startup SET принимаются; реальные эффекты enable_mergejoin, cpu_operator_cost и lock_timeout отложены |
| 2. SQL и параметры | Simple Query выполняет скрипты совместимого SQL; PG BEGIN без опций и последовательные типизированные `$n` поддержаны; postfix `::` добавлен для native builtin types; literal LIMIT 0…2147483647 поддержан; произвольная нумерация и общий LIMIT/OFFSET ещё не реализованы |
| 3. Открытие базы | pg_class/pg_database/visibility и служебные таблицы 1С ещё не реализованы; нужны настоящие metadata и данные |
| 4. Типы и функции | Binary BLOB output выдаётся как bytea; input до 65533 decoded bytes поддержан. Большие input и SQL bytea type/casts/storage/index semantics не добавлены. mchar/mvarchar, regnamespace и BINROWVER остаются открытыми |
| 5. Транзакции/конкуренция | PG BEGIN без опций использует реальные READ CONSISTENCY транзакции; native controls сохраняются; advisory locks и PG timeout semantics ещё не реализованы |
| 6. Временные объекты/нагрузка | PG temporary DDL, serial4, ANALYZE и fasttrun_ext ещё не реализованы; нужен эталон их эффектов |

## Bytea input: ограниченный APP-08

InputCodec принимает выведенный engine SQL_BLOB subtype 0, scale 0, ISC_QUAD;
ParameterDescription — OID 17, Parse hint отсутствующий/0/17. Другой hint —
0A000, `SELECT $1` без native типа — прежний HY004. Только transport descriptor
меняется на VARCHAR OCTETS: constructor задаёт capacity 0, portal Bind — реальную
decoded длину каждого BLOB. Native layout пересчитывается через IMetadataBuilder,
с проверкой prefixes, соседних scalar fields, null offsets и общей длины.
Prepared metadata не меняется, scalar-only encode сохраняет прежнюю metadata.
При ошибке encode прежний descriptor остаётся пригоден для прежнего buffer.

Text Bind — уже значение: `\x` в начале включает hex, оба регистра цифр,
space/tab/LF/CR только перед/между/после полных пар. В остальных значениях
обычные bytes сохраняются, `\\` даёт backslash, `\ooo` (0…3/0…7/0…7) — байт.
Нет trim, снятия SQL quotes или зависимости от standard_conforming_strings.
Malformed hex — 22023, malformed escape — 22P02; raw text UTF8/NUL validation
даёт 22021. Binary и уже decoded bytes сохраняют NUL/invalid UTF8. NULL отличается
от empty text/`\x`/empty binary, которые дают ненулевой пустой BLOB.

Предел одного значения — 65533 decoded bytes (native descriptor, не DDL VARCHAR);
65534 — 0A000 на Bind. Лимиты frame/native message/cache остаются 1 MiB/1 MiB/8 MiB;
cache overflow — прежний 54000. Slots не резервируют максимум на каждый BLOB,
сохраняется только итоговый buffer. Никаких plugin BLOB handles, API или новой
transaction state: штатный MOVE создаёт BLOB при Execute в текущей transaction.
Parse/Bind/Describe/Flush/close без Execute не создают transaction/данные/sequence effects.

Приёмка: `pgwire_1c_smoke.py --blob-input`, настоящий libpq и native readback
после commit; обе wire формы до 65533, selected23 UPDATE 1/0 с synthetic 40444 bytes,
68 BLOB из 92 параметров, independent portals/suspension/cache, singleton PROCEDURE,
RETURNING, rollback/savepoint/E/25P02/disconnect. Правила text сверены с отдельным
Postgres Pro 18.6. Это проверка исходной формы SQL на synthetic schema/values;
исходный Bind format не записан, конфигурация 1С не восстановлена и база не открывалась.
Большие input — APP-09; APP-02/SQL-06/APP-05 и все прочие открытые задачи сохраняются.

## PG BEGIN через штатную транзакцию Firebird

Клиентский PG dialect 4 принимает только четыре формы без опций:

```sql
BEGIN;
BEGIN WORK;
BEGIN TRANSACTION;
START TRANSACTION;
```

Все возвращают tag BEGIN. Исходный `09-begin.sql` передаётся без изменений.
Существующий SetTransactionNode создаёт READ COMMITTED READ CONSISTENCY,
READ WRITE, WAIT transaction; AUTO COMMIT и дополнительные TPB options не заданы.
Native SET TRANSACTION сохраняет прежние параметры и default SNAPSHOT, в том
числе при вызове через PgWire. ISOLATION LEVEL, READ ONLY/WRITE, DEFERRABLE и
прочие PG options не поддержаны и дают syntax error до начала нового блока.

| Состояние перед PG BEGIN | Результат после успешного prepare |
|---|---|
| I без native handle | Engine execute создаёт transaction; переход в T |
| I с implicit handle | Тот же handle становится explicit, переход в T; прежние записи остаются незавершёнными |
| T | Тот же handle, savepoints, portals и история настроек; tag BEGIN |
| E | 25P02, нужен прежний recovery через rollback |

Один приватный ControlKind::PgStart отличает эту политику от native Start.
Оба сверяются с прежним isc_info_sql_stmt_start_trans после engine prepare.
Общий QueryExecutor::control обслуживает Simple и Extended; SQL rewriting,
новых API/getInfo/AST nodes, состояния attachment или зависимостей нет.
Повтор native SET TRANSACTION остаётся 25001/E. NoticeResponse не добавлен:
повтор BEGIN сохраняет блок без клиентского предупреждения в текущем профиле.

В `INSERT ...; BEGIN` вставка не становится видимой observer до COMMIT;
общий ROLLBACK отменяет и работу перед BEGIN. Аналогично Execute DML → Execute
BEGIN до Sync сохраняет transaction; suspended SELECT portal продолжает чтение
после BEGIN/Sync. Parse/Bind сами explicit блок не открывают, Execute открывает,
Sync оставляет T. Prepared BEGIN переиспользуется после rollback.

История standard_conforming_strings начинается до BEGIN, даже в
`SET standard_conforming_strings=off; BEGIN` без native handle. Promotion и
повтор BEGIN её не сбрасывают; rollback/ошибка/savepoints используют прежний путь.
Разрыв TCP откатывает блок. Native dialects 1/2/3 отвергают новые корневые формы
окончательной token error; AUTOTERM выполняет следующую команду. PSQL BEGIN/END,
DDL START WITH и native SET TRANSACTION сохранены; lexer gate этих токенов не менялся.

Проверены данные через native observer, одна application transaction, её режимы
и ID, чтение свежего commit второго соединения, Simple/Extended, psql AUTOCOMMIT
off и автоматический BEGIN psycopg2. Результаты и ограничения — в
[VALIDATION.md](../VALIDATION.md). Psycopg 3 сценарий обновлён, но этот клиент
в текущем окружении отсутствует и live-повтор не заявляется.

Это ограниченный контракт [BEGIN](https://www.postgresql.org/docs/18/sql-begin.html)
и [transaction flow](https://www.postgresql.org/docs/18/protocol-flow.html#PROTOCOL-FLOW-MULTI-STATEMENT).
Полная эквивалентность конкурентного DML PostgreSQL не заявляется. Произвольные `$n`, custom cast targets,
общий LIMIT/OFFSET, PG transaction options/default_transaction_*, каталоги и запуск 1С
остаются отдельными задачами. Whole-query preparse отложен: ранний COMMIT
сохраняется при поздней syntax error. Прежние native defects не исправлялись.

## Фиксированный профиль SET: английские сообщения и UTF8

Отдельный срез поддерживает запрос `02-lc-messages` из записи и настройку
кодировки из `03-session-batch`:

```sql
SET lc_messages TO 'en_US.UTF-8';
SET client_encoding = 'utf8';
```

Грамматика доступна только в клиентском PG dialect 4. Для обеих команд допустимы
`TO` и `=`, значение — строковый литерал либо идентификатор. Native dialects
1/2/3 этих форм SET не получают; новые слова остаются нерезервированными.
Подготовку и исполнение обслуживает существующий SessionManagementNode,
PgWire только классифицирует команду как SET и передаёт её движку.

| Настройка | Поддержанный контракт |
|---|---|
| `lc_messages` | Только точное значение `en_US.UTF-8`, для любого пользователя. PgWire уже формирует английские клиентские диагностики. Системная locale и native `firebird.msg` не меняются; это не реализация локализации произвольных языков |
| `client_encoding` | `UTF8` и `UTF-8` без учёта регистра, для любого пользователя. Execute дополнительно проверяет действительный charset attachment: при другом charset — 0A000. Смена кодировки подключения не выполняется |

Неподдержанные значения дают 22023. Parse/Bind сами настройки не применяют; значение и charset
проверяются заново при исполнении prepared statement. Значение UTF8 в
ParameterStatus продолжает соответствовать данным на соединении.

**Ограничение совместимости:** по решению пользователя проверка прав
на `lc_messages` пока отсутствует. PostgreSQL-права суперпользователя/SET на
отдельный параметр не воспроизводятся; вернуться к этой модели при расширении
поддержки настроек. Остальные проверки прав Firebird сохраняются.

Execute подтверждает единственный уже действующий режим, не меняя его: изменяемого состояния,
его кэша или журнала отката не добавлено. COMMIT/ROLLBACK/RESET сохраняют English/UTF8.
`SET LOCAL`, `SET SESSION`, `DEFAULT`, SHOW и смена режима здесь не реализованы.
Новые SET внутри PSQL отвергаются с 0A000: штатный wrapper переподготавливает
текст в native dialect, а наследование PG dialect во вложенном SQL — отдельная работа.

Исходный пакет семи SET теперь принимается с исключением для трёх игнорируемых
настроек, описанным ниже. Это не реализация всех запрошенных эффектов.
Результаты и воспроизводимые команды — в [VALIDATION.md](../VALIDATION.md).

## Фиксированный профиль клиентской диагностики

```sql
SET escape_string_warning = off;
SET client_min_messages=error;
```

В PG dialect 4 поддержаны только **off** и **error** соответственно, без учёта
регистра, как bare identifiers или строки. Допустимы `=` и `TO`, например
`SET client_min_messages TO 'ErRoR'`. Обычного пользователя достаточно.
Существующий SetPgSessionNode проверяет полное значение при Execute; Parse/Bind
допускают синтаксически корректное неподдержанное значение. При исполнении
`on`, `notice`, `warning`, `'bad'`, `''` и другие значения дают 22023.
Неподдержанный синтаксис остаётся parser error. Успешный CommandComplete — SET.

Это подтверждение уже действующего ограниченного профиля: PG backslash decoder
не создаёт escape warnings; PgWire передаёт ошибки через ErrorResponse и пока
не имеет канала NoticeResponse. Ошибки не подавляются: malformed Unicode
`'\u12'` при standard_conforming_strings=off даёт 22025, SELECT 1/0 — 22012.
Допустимый `'\q'` возвращает q, `'\n'` — перевод строки без NoticeResponse.
Источники и доставка INFO/NOTICE/WARNING этим этапом не реализованы. Серверный
журнал и native IStatus warnings не перенастраиваются.

Значения PostgreSQL по умолчанию отличаются: escape_string_warning=on,
client_min_messages=NOTICE. PostgreSQL передаёт INFO клиенту независимо от
client_min_messages, даже при error; полного совпадения диагностики не заявляем.
См. [escape_string_warning](https://www.postgresql.org/docs/18/runtime-config-compatible.html#GUC-ESCAPE-STRING-WARNING)
и [client_min_messages](https://www.postgresql.org/docs/18/runtime-config-client.html#GUC-CLIENT-MIN-MESSAGES).

Новых attachment fields, истории, undo, cache invalidation, API/ABI,
ParameterStatus или конфигурации нет. COMMIT/ROLLBACK/ALTER SESSION RESET
сохраняют фиксированный профиль. Неверный SET использует прежний error path:
останавливает Simple Query, откатывает implicit writes, переводит explicit
transaction в E и восстанавливает прежний standard_conforming_strings.
Успешные фиксированные SET строковый режим не меняют. Native dialects 1/2/3
сохраняют имена как unquoted identifiers, отвергают эти SET и продолжают
AUTOTERM после ошибки; PG SET внутри PSQL по-прежнему запрещён.

Эти две команды проверены отдельно и в полном `03-session-batch.sql` с описанным
ниже исключением для трёх настроек. Другие значения fixed SET, SET LOCAL/SESSION/DEFAULT, SHOW,
startup GUC, $n и следующие этапы не добавлены. Whole-query preparse
по-прежнему отложен; ранний явный COMMIT сохраняется при поздней ошибке.

## Временный приём трёх startup SET без эффекта

По явному решению пользователя от 10 ноября 2026 года PG dialect 4 принимает:

```sql
SET enable_mergejoin = off;
SET cpu_operator_cost = 0.001;
SET lock_timeout = 20000;
```

Это узкое исключение только для трёх имён: их значения **игнорируются**, не
сохраняются и не применяются. Допустимы `=`/`TO` и один scalar: строка,
идентификатор/on/off или штатный числовой литерал, включая знак. Числа разбирает
существующий `u_numeric_constant`; его native ограничения сохраняются. PG-типы,
диапазоны и единицы настройки не проверяются: например, `lock_timeout TO '5s'`
или `cpu_operator_cost = -0.001` тоже успешны и ничего не меняют. Пустой RHS,
выражения и неизвестные имена настроек дают ошибку. Прежний `pg_setting_value`
не расширен: числовой RHS у client_encoding остаётся 42601, неверные fixed values — 22023.

Единственный новый Type существующего SetPgSessionNode получает пустое value
и ничего не делает при Execute. Плагин классифицирует SET и передаёт исходный
SQL движку. В I/T возвращается SET, в E — прежний 25P02; native transaction
сохраняется. Нет новых полей attachment, истории, cache invalidation, API/ABI,
ParameterStatus, SHOW или предупреждений. Native1/2/3 отвергают SET, имена
остаются non-reserved identifiers; прежний PSQL guard даёт 0A000.

Optimizer, оценки стоимости, timeouts, TPB, данные и настройки attachment
не меняются. **lock_timeout=20000 не устанавливает таймаут в 20 секунд**:
действуют обычные правила Firebird. В отличие от fixed off/error, эти SET
не подтверждают запрошенное поведение. Реальные эффекты требуют отдельной работы;
SET OPTIMIZE и statement timeout не используются как приблизительные замены.

Полный неизменённый `03-session-batch.sql` даёт семь SET tags, итог I и
standard_conforming_strings=off. Это подтверждает приём startup batch,
но не семантику всех настроек, открытие базы 1С или тест Гилёва. Результаты
целевого `--settings` и сборки — в [VALIDATION.md](../VALIDATION.md).
SET LOCAL/SESSION/DEFAULT и другие GUC этим исключением не разрешены.

## standard_conforming_strings: редакция 2, 10 ноября 2026

Настоящий SET меняет bool на attachment при Execute, начальное значение — on.
Допустимы `=`/`TO`, идентификаторы и строки `on`/`off` без учёта регистра;
прочие значения дают 22023. Parse/Bind сами SET не исполняют. ALTER SESSION RESET
возвращает on. Пример для интерактивного psql: отправить каждую строку отдельным Query.

```sql
SET standard_conforming_strings = on;
SELECT OCTET_LENGTH('\n');  -- 2
SET standard_conforming_strings = off;
SELECT OCTET_LENGTH('\n');  -- 1
SET TRANSACTION;
SET standard_conforming_strings = on;
ROLLBACK;
SELECT OCTET_LENGTH('\n');  -- 1
```

При off обычные одинарные литералы декодирует существующий engine lexer:
кавычки, backslash, управляющие `b/f/n/r/t/v`, octal (1–3 цифры), hex (1–2),
Unicode `u` (4)/`U` (8) и surrogate pairs. Неизвестный escape оставляет следующий
символ. NUL/неверные UTF8 bytes дают 22021, неверная форма Unicode — 22025,
недопустимый codepoint/surrogate — 42601. Off разрешён только для UTF8 attachment;
иной charset и не-UTF8 introducer обычной строки явно отвергаются с 0A000.
Native Q-strings, hex strings, double-quoted identifiers и codecs не меняются.
Остаются native ограничения длины: 65535 bytes и 16383 UTF8 characters для
строковой константы. Сцепление соседних строк сохраняет Firebird whitespace
правило, а не PostgreSQL newline-only. Escape warnings не создаются; `escape_string_warning` принимает только off
в фиксированном профиле ниже. `backslash_quote` не реализован.

Публичное расширение ограничено `fb_info_standard_conforming_strings = 163`
(4-byte integer 0/1 в существующем attachment getInfo) и
`IStatement::PREPARE_STANDARD_CONFORMING_STRINGS_ON = 0x100` / `OFF = 0x200`.
Без flags PG4 читает attachment; один flag фиксирует только prepare context,
оба дают 22023. Native dialects 1/2/3 не включают PG-разбор даже с OFF.
Методов, версий интерфейсов и vtables не добавлено; CLOOP bindings сгенерированы.
Один разрешённый режим используется parser, cache lookup и cache insertion.

Simple Query фиксирует режим **на всё сообщение**, включая продолжение EXECUTE
BLOCK через AUTOTERM; SET влияет на следующее сообщение. Extended Parse фиксирует
режим prepared statement; повторный prepare при Bind использует его же, даже
после SET. Portals сохраняют независимые cursors; Bind values не являются SQL
и повторно не декодируются. PgWire передаёт исходные SQL bytes без rewriting.
Engine StrMark/transformString сохраняет декодированные литералы в native hex
notation при сохранении DDL/PSQL source, чтобы внутренняя переподготовка
не меняла значение. Для обычного `sql_string` используется `_UTF8 X'...'`;
для bare STRING (DATE/TIME/TIMESTAMP, WHEN SQLSTATE) — `X'...'` без introducer.
Явный `_charset` сохраняется прежним механизмом. Исходные позиции lexer
остаются позициями входного SQL.
PG SET внутри PSQL по-прежнему отвергается существующим guard (0A000).

**Review 9e5b7bd07a выявил ошибку прежней сериализации:** bare STRING получал
недопустимый `_UTF8`, что приводило к 42601 при CTAS или повторном native prepare.
[Исправление формы сохраняемого литерала](STRINGS_SOURCE_FIX_TASK.md) ограничено
приватным StrMark и действием `sql_string: STRING`: контекст отмечается только
в выбранной ветке разбора, до сохранения source. Grammar и публичный API
не расширены. Исправление относится к новой сериализации; ранее ошибочно
сохранённый source автоматически не переписывается. Пересоздавать такие объекты
нужно из исходного SQL. Исторический review и новая приёмка приведены в VALIDATION.md.

История одной настройки принадлежит QueryExecutor: baseline логического блока
и имена/snapshots пользовательских savepoints (не более 1024 активных имён).
Она начинается до SET, даже без native transaction handle. Implicit Simple
сегмент заканчивается на COMMIT/ROLLBACK или конце Query, Extended — на Sync;
Flush и несколько Execute не сбрасывают историю. Explicit transaction живёт до
фактического завершения; COMMIT в E выполняет rollback. При первой ошибке,
включая Parse/Bind/duplicate prepared name, настройка немедленно восстанавливается
к последнему savepoint либо baseline, ещё в E. Повторные ошибки в E внешний
уровень не снимают; native transaction остаётся доступной для recovery.

ROLLBACK TO восстанавливает snapshot и сохраняет целевую точку, удаляя поздние.
Повторное имя заменяет только прежнюю одноимённую точку; RELEASE удаляет целевую
и поздние, RELEASE ONLY — только целевую. Имена следуют MetaString/native правилам
регистра, doubled quotes, UTF8 и концевых пробелов. История изменяется после
успешного native control. Commit сохраняет значение, rollback возвращает baseline;
retaining/reset начинают новую историю. Внутренние PSQL savepoints не учитываются.
Проверены NO AUTO UNDO и EXECUTE STATEMENT внутри внешнего savepoint.

Восстановление использует две фиксированные команды SET через обычные
prepare/execute с NULL transaction, минуя dispatcher. Оно не создаёт application
handle, wire results или повторные записи истории. Readback проверяет результат;
отказ завершает session с FATAL и первичной диагностикой, без ложного
ParameterStatus/ReadyForQuery. ParameterStatus читает действительное состояние
при startup и перед ReadyForQuery на Query/Sync; Flush сам уведомление не выдаёт.
Это соответствует измеренному порядку сообщений эталона Postgres Pro 18.6.

Прямой OO API PG4 поддерживает SET/parser/cache/flags, но его native rollback
**не восстанавливает session bool**. PG lifetime обеспечивает только PgWire;
undo-полей/hooks в jrd_tra/Savepoint нет. Сочетания новой истории с native TPB
AUTO COMMIT и RESET внутри PSQL исключены из подтверждённого профиля: скрытые
границы не описываются одним interface pointer. Native возможности сохранены;
наблюдения и отдельный отказ nested RESET приведены в VALIDATION.
Внутренний SAVEPOINT через EXECUTE STATEMENT также вызывает исходный Debug
assertion на текущей и предыдущей сборках; проверка вложенного SELECT
не подтверждает этот отдельный сценарий. Дефект вынесен за пределы исправления
сериализации и описан в архитектурном review в VALIDATION.

Не реализованы E-literals, dollar quoting, SET LOCAL/SESSION, DEFAULT, SHOW,
RESET parameter, новые startup options, произвольные $n, custom cast targets и общий LIMIT/OFFSET. Whole-query preparse
по-прежнему отложен. Полный startup batch и запуск 1С/Гилёва этим срезом не доказаны.

## Решение от 9 ноября 2026: последовательные скрипты, остановка на ошибке

**Реализовано и проверено 10 ноября 2026 года:** несколько SQL-команд в одном
Simple Query, последовательно, с остановкой
на первой окончательной ошибке, по модели `isql SET BAIL ON`. Предварительную
синтаксическую проверку всего пакета откладываем; к ней можно вернуться позднее.
Это изменение ранее заданной границы: допускается ограниченное лексическое
выделение кандидатов команд в PgWire, а грамматику и семантику каждого кандидата
по-прежнему проверяет engine parser. SQL rewriting не допускается.

Архитектура реализации:

1. Переиспользуются `src/isql/FrontendLexer` и подход `AUTOTERM`. Lexer выделяет
   возможный разделитель вне строк/комментариев; это не отдельный SQL parser.
   Добавлен узкий режим внутренней utility, возвращающий исходную границу сразу
   после разделителя, без trim, сборки нового SQL и чтения комментариев следующей
   команды. Прежнее поведение isql сохранено. Исходник lexer включён в сборку
   PgWire без новой библиотеки или переноса всего isql в сервер.
2. Исходный непрерывный диапазон Query готовится обычным `IAttachment::prepare`
   с `SQL_DIALECT_PG` и `PREPARE_REQUIRE_SEMICOLON`. Только структурный
   `isc_command_end_err2`, без более приоритетной fatal/cancel/permission ошибки,
   означает продолжение до следующего кандидата: например, внутри EXECUTE BLOCK.
   Остальные ошибки окончательные. Последний хвост без `;` готовится без требования
   разделителя; незавершённый SQL в конце сообщения — ошибка, не ожидание нового Query.
   Успешно полученный `IStatement` сразу используется для исполнения.
3. Цикл находится в существующем `QueryExecutor` и переиспользует metadata,
   codecs, cursors, command tags и native controls. Ресурсы команды закрываются
   перед следующей; unnamed objects очищаются один раз на Query. Ошибка немедленно
   завершает цикл, даже если соединение остаётся пригодным для следующего Query.
4. В I используется одна implicit transaction до полного COMMIT/ROLLBACK или конца
   Query. Следующая команда после границы открывает новую transaction по необходимости.
   В T/E сохраняются нынешние правила и единственный текущий handle. Каждая команда
   автоматически не коммитится. Native SET TRANSACTION сохраняет свой контракт;
   PostgreSQL BEGIN добавлен отдельным срезом, описанным выше.
5. Отправляется результат каждой команды и один конечный ReadyForQuery. Финальный
   успешный CommandComplete выдаётся после успешного implicit commit, сохраняя
   нынешнее поведение при ошибке commit. Строки результатов не накапливаются.
   Extended Parse остаётся однокомандным.

Новый публичный API, `ISqlScript`, новые getInfo items, параметры конфигурации и
runtime-зависимости не добавлены. `SET BAIL`/`SET TERM`/`INPUT`
остаются командами утилиты isql, в серверный SQL они не добавляются.

**Принятое отличие от PostgreSQL:** в `INSERT A; COMMIT; SELCT ...` первый INSERT
будет зафиксирован до обнаружения поздней syntax error. PostgreSQL отвергает
такое сообщение до первого исполнения. При ошибке выполнения после COMMIT
зафиксированный префикс сохраняется в обеих моделях. Без промежуточного COMMIT
ошибка откатывает текущую implicit transaction; внутри explicit transaction
остаётся E. Остаток сообщения, включая находящийся там ROLLBACK, не выполняется.

Прочие native session setters меняют attachment напрямую: поздний rollback не отменяет
их изменения. Сохраняется native семантика также для sequences, autonomous
transactions и внешних эффектов; общей отмены таких эффектов не обещаем.
Сам по себе скриптовый executor не реализует эффекты PostgreSQL SET из трассы.
Контракты принятых настроек и три временных исключения описаны выше. Для уже реализованного
`standard_conforming_strings` lexer, engine и statement cache используют
согласованный режим; snapshot Query и история отката описаны выше.

Приёмка — в [TEST_PLAN.md](../TEST_PLAN.md), результаты и команды — в
[VALIDATION.md](../VALIDATION.md). Для существующего build directory после
изменения списка объектов нужно повторить configure, затем make.
Отдельный `;` и пустые команды между разделителями пока остаются syntax errors;
закрытые comments/whitespace в хвосте не создают лишний EmptyQueryResponse.

Для ручной проверки отправить весь скрипт одним Query через `psql -c` либо
`pgwire_client.py -c` (интерактивный psql может отправлять команды отдельно):

```sh
psql 'postgres://USER@127.0.0.1:15432/pgwire_test' \
  -c 'SELECT 1 AS "first"; SELECT 2 AS "second";'
```

`standard_conforming_strings=off` действительно декодирует backslash escapes.
`lock_timeout` нельзя заменить statement timeout: область действия различна.
`SET OPTIMIZE` не эквивалентен `enable_mergejoin` или `cpu_operator_cost`.
По решению пользователя для этих трёх имён реализовано узкое исключение:
успешный SET без изменения поведения. Реализация и границы — в
[IGNORED_STARTUP_SETTINGS_TASK.md](IGNORED_STARTUP_SETTINGS_TASK.md).
Значения не сохраняются; join/cost и ожидание блокировок остаются штатными Firebird.
В частности, `lock_timeout=20000` не устанавливает ограничение в 20 секунд.
Реальная семантика отложена; успешный разбор пакета не доказывает её поддержку.
Успешный SHOW на эталоне не доказывает эффект настройки планировщика/блокировок.

Открытие существующей базы 1С и тест Гилёва на Firebird ещё не выполнены.
Выдача строк несколькими SQL или отсутствие parser errors не заменяет такую
приёмку. TLS/COPY/pipeline и другие независимые этапы roadmap сюда не включаются.
