# Следующий этап: LIMIT с целочисленным литералом

Архитектурное решение от 11 ноября 2026 года. Статус: задание на реализацию.
Пункт [BACKLOG](../BACKLOG.md): **SQL-01**. SQL-02 остаётся открытым после этого этапа.
Рабочий каталог: `/home/aleksey/dev/my_firebird/firebird`.
База — master Firebird 6, ветка pg_wire_protocol; проверенная ревизия `9f94454527`.
Не переключать ветку и не сбрасывать изменения ради совпадения с этой ревизией.

## Задание и контекст

Реализуй один небольшой этап: `LIMIT n` в клиентском PG dialect 4, где n —
целочисленный литерал. Используй существующие RowsClause и исполнение Firebird.
Собери, проверь и оставь результат пользователю на review.
**Не выполнять git add, commit, amend, reset или checkout.** Пользователь
проверяет и коммитит сам; следующий этап автоматически не начинать.

**Приоритет: минимальные необходимые изменения FirebirdSQL, максимальное
переиспользование существующих механизмов, без новых зависимостей.**
Широкое задание `/home/aleksey/dev/1c/CODEX_PGWIRE_1C_TASK.md` целиком не выполнять.
Прочитай HEAD/status, применимые инструкции репозитория, [README](README.md),
начало [VALIDATION](../VALIDATION.md), SELECT grammar и PASS1_limit.

Уже работают PG4 SELECT без FROM, Simple scripts, Extended/Bind/codecs,
PG BEGIN, строковая настройка, последовательные уникальные `$1` … `$n` и
postfix casts через native CastNode. API, native dialects 1/2/3 и текущие
ограничения этих возможностей сохраняются. Три startup SET
enable_mergejoin/cpu_operator_cost/lock_timeout по решению пользователя
принимаются без эффекта; возвращаться к их реализации здесь не нужно.

В `/home/aleksey/dev/1c/captures/20261109T105635Z/profile` найдены пять семейств
LIMIT; их примеры используют числа 1 или 100001. В selected это
`04-catalog-visibility.sql` и `17-data-limit.sql`. Среди остальных семейств есть
поиск `FileName=$1 LIMIT 1`. Записанный DETAIL не считать готовым wire Bind.
Такой срез закрывает наблюдаемую форму без реализации общего PG LIMIT/OFFSET.

## Ограниченный контракт

Поддержать `LIMIT n` после необязательного ORDER BY у SELECT expression.
Число n — от **0 до 2147483647**, через прежний `long_integer`/NUMBER32BIT.
Не писать отдельный scanner чисел: существующие корректные integer spellings
(включая 0x/0o/0b и `_`) сохраняются. Размер определяется значением токена;
большие числа не обрезаются и не преобразуются через переполнение.

```sql
SELECT 42 LIMIT 0;                       -- metadata, ноль строк, SELECT 0
SELECT ID FROM LIMIT_ROWS ORDER BY ID LIMIT 2;
SELECT FILENAME FROM LIMIT_FILES WHERE FILENAME=$1 LIMIT 1;
INSERT INTO LIMIT_COPY(ID)
    SELECT ID FROM LIMIT_ROWS ORDER BY ID LIMIT 2;
```

Ограничение применяется к готовому результату соответствующего SELECT уровня:
после его DISTINCT/GROUP BY/ORDER BY и ко всему UNION, если LIMIT стоит снаружи.
Ограничение внутри скобок действует только на этот operand/subquery.
Число возвращённых строк — не больше n; при n=0 сохраняются описание результата
и корректный CommandComplete. Без ORDER BY конкретный набор/порядок строк
не обещается. Все остальные native SQL semantics сохраняются.

В этом этапе не добавлять LIMIT ALL/NULL, count-параметры `$n`/`?`, выражения,
скобки вокруг count, знаки +/- и дробные/exponent count literals. Не добавлять
PG OFFSET, `LIMIT offset,count` и смешение с ROWS/OFFSET…FETCH на одном уровне.
Неподдержанная форма получает обычную parser error; крупное число/overflow —
штатную диагностику без narrowing или успешной подмены предела.
Прежние native FIRST/SKIP, ROWS и OFFSET…FETCH отдельно продолжают работать.
Параметры в WHERE/SELECT/DML вне позиции count работают по прежнему контракту.

Почему пока без count-параметров: FirstRowsStream превращает NULL count в ноль
строк, а PostgreSQL трактует NULL как отсутствие ограничения. Нельзя просто
подставить `parameter` или `value` в новое правило и заявить PG semantics.
Обработка NULL/ALL и произвольных count expressions — отдельное решение;
не менять native stream ради неё в этом этапе.

Настоящий LIMIT допускается только в корневых SELECT/INSERT/UPDATE/DELETE/MERGE,
включая их обычные SELECT subqueries/CTE. Это **не** добавление suffix LIMIT
к UPDATE/DELETE: только их вложенный SELECT может иметь LIMIT.
DDL/CTAS/VIEW/stored PSQL/EXECUTE BLOCK/CALL/EXECUTE PROCEDURE/USING и остальные
исключённые roots с распознанным LIMIT получают 0A000 до DSQL/эффектов.
Неверная грамматика остаётся syntax error. Строка `'LIMIT 1'` guard не включает.

Причина ограничения roots та же, что у `::`: сохранённый SQL может повторно
разбираться native parser, который новой формы не знает. Не расширять
transformString, internal prepare или публичные API ради сериализации LIMIT.

## Архитектура и порядок реализации

1. На отдельном runtime подтвердить исходный отказ `SELECT 42 LIMIT 1`
   и успех эквивалентной native формы FIRST/ROWS. Зафиксировать baseline.
2. Распознавать LIMIT только в PG4 штатным lexer, сохраняя offsets. В PG4
   LIMIT — keyword, а не неявный alias: `SELECT 1 LIMIT` обязан быть ошибкой.
   Native 1/2/3 по-прежнему видят обычный идентификатор LIMIT; quoted `"LIMIT"`
   остаётся идентификатором в dialects с quoted identifiers. Достаточен узкий
   PG gate по образцу нынешних токенов; общий keyword/framework не нужен.
3. Новое SELECT-only правило `pg_limit_clause: LIMIT long_integer` создаёт
   существующий RowsClause с `length = MAKE_const_slong(n)`, `skip = nullptr`.
   Присоединить его к SELECT tail после order_clause_opt. Можно минимально
   переиспользовать создание SelectExprNode через общий nonterminal, сохранив
   остальные ветви grammar без сопутствующего рефакторинга.
4. Учесть оба пути: `select_expr` и parenthesized `query_primary`.
   Последний уже использует SelectExprNode/DFLAG_BODY_WRAPPER для ORDER/OFFSET/FETCH.
   Переиспользовать этот механизм для `(SELECT ... LIMIT n) UNION ...`,
   сохраняя границу каждого уровня. Не встраивать LIMIT в общий rows_clause:
   он также используется непосредственно в UPDATE/DELETE.
5. Признак настоящего LIMIT ставить только в подтверждённой semantic action.
   Переиспользовать parsedDmlNode и существующий root guard `$n`/postfix casts;
   не добавлять публичные metadata, типы операций, AST annotations или SQL parser
   в плагине. Проверить generated parser и защиту `!yytrial`.
6. Существующий путь RowsClause → PASS1_limit → rse_first → FirstRowsStream
   должен выполнять реальное ограничение. Сохраняются optimizer, BLR, streams,
   metadata, Bind и wire execution. Не обрезать готовые результаты в плагине
   и не переписывать LIMIT в FIRST текстом SQL.
7. Для FIRST/SKIP и LIMIT на одном уровне использовать прежнюю проверку
   isc_dsql_firstskip_rows. Не вводить глобальный запрет FIRST по parser flag:
   FIRST во внутреннем SELECT и наружный LIMIT принадлежат разным уровням.
   Собрать, проверить, обновить документы и остановиться для review.

Основные места: `src/dsql/Parser.cpp`, Parser.h, parse.y (select_expr,
query_primary, long_integer); существующие `RowsClause` в Nodes.h,
`PASS1_limit`/PASS1_rse в pass1.cpp и FirstRowsStream — для чтения/переиспользования.
Новых runtime dependencies, API/config, codecs или plugin SQL обработчиков
не требуется. Если обнаружится необходимость более широкого изменения,
сначала описать конкретный блокер и минимальную альтернативу.

## Проверки

Расширить существующий 1c/Extended harness и native OO helper, без нового
framework. Использовать свежие /tmp runtime с собственными базой/security DB
и свободными портами. Не трогать сервер пользователя, employee, pgwire_test
или рабочую базу 1С.

- Simple: LIMIT 0/1/2/100001/2147483647 на небольшой таблице, пустой результат,
  SELECT без FROM; строки/OID/tag как у эквивалентного native FIRST/ROWS.
  Для 100001 и MAX не создавать огромную fixture: проверить, что возвращены
  все имеющиеся строки. Проверить ведущие нули и штатные non-decimal literals.
- ORDER BY уникального не-NULL ключа, DISTINCT, агрегат, CTE, derived/scalar
  subquery. UNION/UNION ALL: общий наружный LIMIT и отдельный LIMIT в скобках
  должны давать различимые результаты, без ограничения только первого operand.
- INSERT SELECT LIMIT: native observer подтверждает число и значения записей,
  commit/rollback. SELECT LIMIT в UPDATE/DELETE/MERGE subquery использует те же
  правила; непосредственные UPDATE/DELETE LIMIT suffix остаются отвергнутыми.
- Реальные PQexecParams/PQprepare/PQexecPrepared с WHERE `$1` и literal LIMIT,
  repeated execution, NULL/text/binary values по прежним codecs, Describe.
  Raw Execute с maxRows меньше SQL LIMIT: suspension/resume соблюдает общий
  предел курсора; отдельные Bind/portals не делят счётчик. LIMIT 0 не посылает
  DataRow и завершает результат корректно.
- `$1`/`?` в count, ALL/NULL, отрицательные/дробные/выражения, 2147483648,
  очень длинное число, неполный/повторный LIMIT и unsupported OFFSET отвергнуты.
  FIRST/SKIP+LIMIT на одном уровне отказан; разные уровни работают.
  ROWS/OFFSET…FETCH отдельно сохраняются, смешение с LIMIT на одном уровне отказано.
  После ошибки и Sync правильный запрос работает; Simple прекращает остаток
  скрипта с прежним rollback текущего implicit сегмента.
- Native dialects 1/2/3: новая форма отказана, имя LIMIT и native row limiting
  сохранены; AUTOTERM не поглощает следующий SQL. PG→native→PG/cache на одном
  attachment; strings/Q/comments/quoted identifiers при on/off не включают
  clause/guard. Проверить source offsets без преобразования текста.
- CTAS, VIEW, PROCEDURE/PSQL, EXECUTE BLOCK и прочие исключённые roots с LIMIT
  в SELECT отказаны до эффектов, включая вложенный CTE/subquery. Проверить
  отсутствие созданных объектов native observer. Прежние формы с FIRST/ROWS
  и текстом `'LIMIT 1'` работают; не менять stored source serialization.

Сборка `/tmp/firebird-pgwire-1c-build`: `make -j2`, затем
`make -C gen -j2 TARGET=Debug pgwire_tests`. После casts parser baseline —
**158 shift/reduce, 11 reduce/reduce**. Разобрать каждый новый конфликт;
не менять ожидаемое количество без объяснения и проверки обеих трактовок.
Регрессии: SQL/session/isql units, --parameters, --casts, --strings (native/cache),
Simple и Extended. Остальные suites повторять при изменении соответствующего
общего пути или выявленной проблеме. Known native crash repro не запускать.

Записать в VALIDATION revision/diff, SHA модулей, команды, PASS/FAIL/пропуски
и exit серверов. Обновить README/COMPATIBILITY/PLAN/TEST_PLAN с точным контрактом
literal-only count и статус SQL-01 в BACKLOG; SQL-02 не закрывать.
Исторический P3 VARCHAR FAIL не скрывать и не модернизировать
весь P3 в рамках LIMIT. Полные selected 04/17 ещё требуют каталогов/custom types:
синтетическая проверка LIMIT не является их полной приёмкой или запуском 1С.

Основания: [PG LIMIT/OFFSET и NULL count](https://www.postgresql.org/docs/18/queries-limit.html),
[SELECT и область действия LIMIT](https://www.postgresql.org/docs/18/sql-select.html#SQL-LIMIT).
