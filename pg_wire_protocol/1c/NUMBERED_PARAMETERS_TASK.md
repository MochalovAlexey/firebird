# Следующий этап: последовательные параметры $1 … $n

Архитектурное решение от 10 ноября 2026 года. Статус: задание на реализацию.
Рабочий каталог: `/home/aleksey/dev/my_firebird/firebird`.
База — master Firebird 6, ветка pg_wire_protocol; проверенная ревизия `97e44d0188`.
Не переключать ветку и не сбрасывать изменения ради совпадения с этой ревизией.

## Задание и контекст

Реализуй первый ограниченный срез numbered parameters, собери сервер и проверь
реальную передачу Bind через libpq. Оставь результат пользователю на review.
**Не выполнять git add, commit, amend, reset или checkout.** Пользователь сам
проверяет и коммитит. Этот диалог — исполнитель, архитектурные этапы обсуждаются отдельно.

**Приоритет: минимальные необходимые изменения FirebirdSQL, максимальное
переиспользование существующих механизмов, без новых зависимостей.**
Широкое задание `/home/aleksey/dev/1c/CODEX_PGWIRE_1C_TASK.md` целиком не выполнять.
Сначала прочитай HEAD/status, инструкции репозитория, [README](README.md),
начало [VALIDATION](../VALIDATION.md), Parser/ParameterNode/MAKE_parameter/GEN_port
и существующие Extended/InputCodec.

Уже работают PG dialect 4, SELECT без FROM, Simple scripts, Extended Query,
native Bind `?`, codecs, PG BEGIN без опций с реальными транзакциями, строковая
настройка и её история. Весь исходный startup batch принимается: по явному
решению пользователя enable_mergejoin/cpu_operator_cost/lock_timeout игнорируются.
Их реальные эффекты в этот этап не входят. Native dialects 1/2/3 сохраняются.

В выбранных запросах 1С есть `FileName = $1` (06-config-bind) и INSERT с
последовательными `$1` … `$92` (19-bind-92-parameters). Источники находятся в
`/home/aleksey/dev/1c/captures/20261109T105635Z/profile/selected`.
Записанный DETAIL параметров не считать полным описанием wire Bind.

## Ограниченный контракт

В PG dialect 4 поддержать **уникальные последовательные номера в порядке
появления**: $1, затем $2 и так далее. Тип должен выводиться штатным DSQL из
контекста колонки, DML либо обычного CAST. Например:

```sql
SELECT CAST($1 AS INTEGER), CAST($2 AS VARCHAR(40));
SELECT ID FROM PARAM_ROWS WHERE NAME = $1;
INSERT INTO PARAM_ROWS(ID, NAME) VALUES($1, $2);
UPDATE PARAM_ROWS SET NAME = $1 WHERE ID = $2;
```

Первый срез разрешён только в корневых SELECT/INSERT/UPDATE/DELETE/MERGE,
включая их обычные CTE/subqueries. Для `$n` в native CALL, EXECUTE PROCEDURE,
EXECUTE BLOCK, USING, DDL/stored PSQL и UPDATE OR INSERT — явный отказ 0A000
для синтаксически распознанного параметризованного statement. Их прежние формы
с `?` или без параметров не менять. Недопустимая грамматика остаётся parser error.
Это временное ограничение контекстов; не добавлять общий анализатор SQL ради него.

| Случай | Поведение первого среза |
| --- | --- |
| $1, $2, …, каждый ровно один раз | Подготовка и Bind по прежним metadata/codecs |
| Повтор $1, пропуск ($1,$3), начало с $2, перестановка ($2,$1) | Явный 0A000 до успешного prepare |
| Смесь ? и $n в любом порядке | Явный 0A000; отдельно native ? сохраняется |
| $0 | Ошибка недопустимого номера, SQLSTATE 42P02 |
| Номер выше 32767, в том числе переполнение при чтении цифр | Ограничение реализации, SQLSTATE 54000 до narrowing/allocation |
| $01 | Номер 1; ведущие нули не создают отдельный параметр |
| $1foo, $1_foo, $1$foo и подобный identifier suffix | Syntax error 42601, не parameter плюс неявный alias |

32767 — верхняя граница номера, а не обещание исполнения любого сообщения
такого размера: прежние ограничения engine/message/codecs сохраняются.
Bare `$`, `$-1`, `$ 1` не являются numbered parameters. Dollar-quoted strings
не добавляются. `$n` внутри строк/comments/quoted identifiers/Q-строк не является
параметром; native идентификаторы с `$`, например A$1 и RDB$DATABASE, сохраняются.

Simple Query с типизированным `$1` по-прежнему получает 0A000: значения надо
передавать через Extended Query. Не исполнять такой SQL с неявным NULL.

Это не полная семантика PG positional parameters. В PostgreSQL номер обозначает
позицию независимо от порядка/повторов; ограничения первого среза должны быть
явно записаны. Источники: [positional parameters](https://www.postgresql.org/docs/18/sql-expressions.html#SQL-EXPRESSIONS-PARAMETERS),
[PREPARE](https://www.postgresql.org/docs/18/sql-prepare.html).

## Почему первый срез ограничен

`Parser::make_parameter()` уже создаёт ParameterNode с очередным индексом,
`MAKE_parameter()` умеет переиспользовать индекс. Но этого недостаточно для
произвольного `$n`: `GEN_port()` уплотняет номера с пропусками, а повторные
ParameterNode и CastNode выводят/перезаписывают descriptors разными путями.
Native CALL также может переносить аргументы в output message и уплотнять
оставшиеся input positions. Незаметное изменение связи `$n` → Bind недопустимо.

В этом этапе не менять GEN_port, MAKE_parameter и общий вывод типов ради
произвольной нумерации. Сначала поддержать случаи, эквивалентные уже работающим
последовательным native input parameters. Повторы/пропуски/перестановки и другие
контексты будут отдельным решением, с отдельной приёмкой.

## Минимальная архитектура и порядок реализации

1. Зафиксировать исходный отказ типизированного `$1` на отдельном runtime.
2. В штатном lexer Parser.cpp распознавать отдельный `$` + ASCII digits только
   при pgDialect. Проверять номер и trailing identifier junk, сохранять исходные
   SQL offsets. Не преобразовывать текст запроса, не менять chars.h так, чтобы
   сломать native identifiers. Токен несёт проверенный номер в parse.y.
3. Добавить альтернативу существующего `parameter` и переиспользовать
   `make_parameter()`/ParameterNode. Проверку формы параметров и последовательности
   выполнять в **подтверждённой semantic action**, а не при lexer lookahead/trial.
   Проверить generated parser: отвергнутые ветви не сдвигают счётчики.
4. Хранить только минимальный приватный parser bookkeeping. Признак настоящего
   `?` ставить в production `parameter: '?'`, а не при любом вызове make_parameter:
   он используется также для синтетических параметров локальных PSQL-функций.
   Для разрешённых корневых statements каждый `$n` обязан получить тот же индекс n
   в send metadata. Нельзя молча исправлять неверную нумерацию.
5. Проверить разрешённый корневой контекст через уже разобранную grammar/AST
   до DSQL transformations. Для остальных контекстов с настоящим `$n` отказать;
   не вводить AST annotations, публичные statement types или обход CALL в плагине.
   Параметроподобный текст внутри литерала не должен включать этот guard.
   Не использовать начальный coarse statement type: до dsqlPass он может быть
   TYPE_SELECT у других DML. Здесь нужен именно корневой grammar/AST node.
6. Extended Parse/Bind, ParameterDescription и InputCodec переиспользовать.
   Плагин не разбирает `$n`, не перенумеровывает, не подставляет литералы и не
   повторяет Bind values для одного параметра. В штатном сценарии wire production
   изменений не требует. Statement cache уже разделён по клиентскому dialect.
7. Собрать, выполнить целевые проверки, обновить документацию и остановиться
   для review пользователя. `::`, LIMIT и следующий этап автоматически не начинать.

### Граница типов и Parse OID

Сейчас engine prepare вызывается **до** InputCodec(parseTypes). Parse OID hints
проверяют совместимость с уже выведенной metadata, а не передаются parser/DSQL
как описание параметра. Этот контракт сохранить без нового prepare API/flags.

Проверять SQL с определённым контекстом: колонка, INSERT либо CAST($n AS native_type).
Нулевые/отсутствующие OID используют вывод движка; совместимые заданные OID —
прежний codec; несовместимые — прежний отказ 0A000. Избыточный список — 08P01.
Не обещать подготовку `SELECT $1` благодаря supplied OID: существующую диагностику
недостаточного SQL-контекста зафиксировать, не заменять молчаливым text fallback.
Новые codecs, generic type inference, `::` и PG aliases типов не добавлять.
Форматы Parse/Bind: [protocol messages](https://www.postgresql.org/docs/18/protocol-message-formats.html).

## Приёмка

Расширить существующий 1c/Extended harness и libpq wrappers либо C libpq tests;
отдельный checks module допустим по образцу begin/strings. Новый клиент/framework
или зависимости не нужны. Использовать отдельный runtime и синтетические fixtures,
не рабочий сервер, employee, pgwire_test или данные базы 1С.

- Настоящие PQexecParams и PQprepare/PQexecPrepared: native CAST и column inference,
  один и несколько параметров, повторный Bind/Execute с другими значениями,
  независимые portals, Describe с правильным числом/порядком OID.
- Несколько поддержанных типов с различимыми значениями: integers, UTF8 text,
  NULL и пустая строка; text/binary Bind. После DML native observer проверяет
  реальные строки/колонки и commit/rollback, а не только command tag.
- Синтетический INSERT с 92 уникальными параметрами и 92 различными значениями:
  metadata count/order, Bind и native readback всех значений. Это проверка формы
  из примера 19, не исполнение его исходной схемы и типов.
- Реальный поиск по строковому параметру по образцу `FileName=$1` из примера 06.
  Не выдавать заменённые типы BinaryData/bytea или другую тестовую схему за
  приёмку оригинального SQL и открытия базы 1С.
- Повторы, пропуски, перестановки, смешение ?/$n в обе стороны, $0, большой номер
  и длинная цепочка цифр: корректная ошибка, без успешной перенумерации/side effects.
  Правильный запрос после ошибки и Sync работает. Проверить границы токена,
  leading zeros, comments/quoted/Q/обычные строки при string mode on/off.
- Native dialects 1/2/3: `$n` отвергнут, `?` и identifiers с `$` сохраняются.
  PG cache не попадает в native prepare. Проверить исходные позиции диагностики.
- CALL с input/output и named arguments, EXECUTE BLOCK headers/body, USING и DDL
  с настоящим `$n` не обходят ограничение. Соответствующий native SQL с `?`
  сохраняет прежнее поведение. `$1` как текст в DDL/PSQL не запрещается.
- CTE/subquery и MERGE: положительная последовательная нумерация не зависит
  от порядка обхода DSQL tree; все input slots соответствуют источнику. Проверить
  unused CTE с $1 и основной SELECT с $2: metadata count=2, второй Bind используется
  в результате. Проверить SELECT/WHERE EXISTS и DML RETURNING: output fields не
  сдвигают input positions.
- BEGIN → parameterized DML → COMMIT/ROLLBACK, ошибка в T/E и recovery;
  Simple Query с типизированным `$1` отказан, Extended discard-until-Sync сохранён.
- Parse OID=0/omitted/compatible/incompatible, неправильный Bind count и bad
  payload используют прежние codec/protocol ошибки. Прежние `?` tests остаются.

Актуальные отрицательные `$n` проверки найти в C/Python driver suites и обновить
только в пределах нового контракта. Не убирать вместе с ними ограничения
повторов, недостаточного контекста, `::`, BLOB/custom types или DEALLOCATE ALL.
Если доступен Psycopg 3, проверить параметризованный запрос с типами из контекста;
если отсутствует — отметить пропуск без установки новых зависимостей.

Регрессии: штатные unit tests/isql, целевые Extended, --settings, --begin,
--strings (изменяется lexer). Parser baseline — 154 shift/reduce, 11 reduce/reduce;
новые конфликты не принимать без разбора причины. Известные crash repro не запускать.
Сборка `/tmp/firebird-pgwire-1c-build`: `make -j2`, затем
`make -C gen -j2 TARGET=Debug pgwire_tests`; примеры команд — в VALIDATION.
Для каждого runtime выбирать новый work-dir в /tmp. Записать revision/diff,
SHA модулей, команды, фактические PASS/FAIL/пропуски и exit сервера.

Обновить README/COMPATIBILITY/PLAN/TEST_PLAN с точными ограничениями первого среза.
Не объявлять полную поддержку PostgreSQL parameters или запуска 1С. Реальные
эффекты трёх игнорируемых SET и прежние engine limitations остаются отложенными.
