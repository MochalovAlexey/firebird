# Записанный профиль 1С: этапы совместимости

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
portal. Этот режим включает PG-дополнения грамматики; типы, арифметика и кавычки
пока сохраняют семантику dialect 3. В текущем срезе единственное дополнение —
`SELECT` без `FROM`. Это ещё не полный PostgreSQL SQL dialect: `$n`, `::`,
BEGIN и LIMIT пока отвергаются.

Ключ штатного statement cache уже содержит исходный клиентский dialect: PG и
native планы не смешиваются. Обычные native dialects 1/2/3 по-прежнему требуют
FROM. Базу не нужно создавать или переводить в dialect 4; DPB и isql его не
принимают. Режим выбирается явно на границе API подготовки SQL, а не по имени
пользователя или глобальной настройке.

Сохранённые PSQL-объекты исполняют обычный BLR. Наследование PG-режима для
динамического `EXECUTE STATEMENT`, внутреннего SQL DDL и SQL-репликации в этот
срез не входит. Установочная функция написана на native PSQL. Имена колонок и
идентификаторы пока следуют Firebird: результат исходного запроса называется
`PG_BACKEND_PID`, тогда как эталон возвращает `pg_backend_pid`.

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

| Группа исходного задания | Состояние после первого среза / что требуется |
|---|---|
| 1. Начало сессии | SELECT без FROM и ограниченная identity-функция; lc_messages и пакет семи SET ещё не реализованы |
| 2. SQL и параметры | `$n`, `::`, BEGIN TRANSACTION, LIMIT и пакеты ещё не реализованы; использовать ParameterNode, CastNode, SetTransactionNode и RowsClause |
| 3. Открытие базы | pg_class/pg_database/visibility и служебные таблицы 1С ещё не реализованы; нужны настоящие metadata и данные |
| 4. Типы и функции | bytea, mchar/mvarchar и BINROWVER ещё не реализованы; сначала измерить форматы/семантику, затем использовать BLOB API, intl/collations и SQL/PSQL |
| 5. Транзакции/конкуренция | Существующие native controls сохраняются; PG BEGIN, advisory locks и PG timeout semantics ещё не реализованы |
| 6. Временные объекты/нагрузка | PG temporary DDL, serial4, ANALYZE и fasttrun_ext ещё не реализованы; нужен эталон их эффектов |

Следующий связный срез — engine contract пакетов и поддерживаемые session
settings. Simple Query требует синтаксического разбора **всего** пакета до
эффектов, затем последовательного разрешения объектов/prepare/execute и одного
конечного ReadyForQuery. Extended Parse остаётся однокомандным. Существующий
IBatch выполняет один statement с разными параметрами и этот контракт не покрывает.
Разрезание SQL в PgWire или набор заранее заготовленных ответов не допускаются.

Эталонный Postgres Pro подтвердил: execution error откатывает предшествующие SET
и данные implicit transaction; syntax error предотвращает исполнение всего
пакета. Native session setters Firebird сами по себе не имеют такого undo.
Нужны согласованные ownership/rollback/savepoint правила, а не только новые
ключевые слова. Lexer mode фиксируется при разборе всего пакета; последующий SET
не должен задним числом менять литералы уже разобранного Query.

`standard_conforming_strings=off` действительно декодирует backslash escapes.
`lock_timeout` нельзя заменить statement timeout: область действия различна.
`SET OPTIMIZE` не эквивалентен `enable_mergejoin` или `cpu_operator_cost`.
Для этих параметров контракт ещё не реализован; фиктивный SET success запрещён.
Успешный SHOW на эталоне не доказывает эффект настройки планировщика/блокировок.

Открытие существующей базы 1С и тест Гилёва на Firebird ещё не выполнены.
Выдача строк несколькими SQL или отсутствие parser errors не заменяет такую
приёмку. TLS/COPY/pipeline и другие независимые этапы roadmap сюда не включаются.
