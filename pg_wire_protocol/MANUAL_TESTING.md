# Ручная проверка PgWire через SQL-клиент

Для ручной работы есть [tools/pgwire_client.py](tools/pgwire_client.py): Python 3 клиент, который загружает настоящую `libpq` через `ctypes` и подключается по TCP. Дополнительные Python-пакеты не нужны; системная `libpq` должна быть доступна загрузчику библиотек.

Текущий Simple Query выполняет SELECT, INSERT/UPDATE/DELETE, MERGE, UPDATE OR INSERT, поддержанные DDL и вызовы процедур/EXECUTE BLOCK. Поддержаны результаты SMALLINT/INTEGER/BIGINT, BOOLEAN, CHAR/VARCHAR, точные NUMERIC/DECIMAL в SHORT/LONG/INT64/INT128, FLOAT/DOUBLE, DATE/TIME/TIMESTAMP без TZ и NULL; DML RETURNING может возвращать одну или несколько строк. Вне явного блока запрос выполняется в implicit transaction: успех фиксируется перед CommandComplete, ошибка откатывает её. После SET TRANSACTION запросы разделяют одну transaction до COMMIT/ROLLBACK. Пустой SQL и текст только из whitespace/comments дают EmptyQueryResponse.

Доступны native SET TRANSACTION, COMMIT/ROLLBACK, RETAIN, savepoints и поддержанные session SET/ALTER SESSION RESET. Клиент показывает состояния I (idle), T (transaction), E (failed transaction). Несовместимые `BEGIN` и `$1` получают ошибку парсера Firebird. Клиент отправляет любой введённый SQL целиком и показывает реальную ошибку сервера; он не добавляет BEGIN и не переписывает запросы.

PgWire использует ограниченный анализ начала команды для текста ответа, но весь SQL передаётся штатному парсеру Firebird без изменений. Имена в кавычках, литералы и содержимое PSQL не переписываются. Неизвестная плагину команда может получить 0A000 после успешного prepare; синтаксическая ошибка остаётся ошибкой движка.

Ниже приведена инструкция для вашего экземпляра сервера. Она сама не создаёт пользователей, не изменяет конфигурацию и не запускает сервер. Ожидаемые результаты примеров нужно проверить на выбранной БД.

## Параметры и Extended Query P7

P7 Extended Query прошёл целевую Linux/Super приёмку: 16 групп, см. [VALIDATION.md](VALIDATION.md). Этот консольный `pgwire_client.py` использует `PQexec`/Simple Query и не добавляет отдельные Bind values. Для P7 нужен прямой libpq `PQexecParams` либо `PQprepare`/`PQexecPrepared`, например SQL `SELECT CAST(? AS INTEGER) FROM RDB$DATABASE` с одним отдельным text parameter `42`. SQL остаётся неизменным; `$1` и автоматически добавленный BEGIN по-прежнему получают parser error.

Input/output поддерживают text/binary integer/BOOLEAN/CHAR/VARCHAR, точные NUMERIC/DECIMAL и INT128, FLOAT/DOUBLE, DATE/TIME/TIMESTAMP без TZ и NULL. Named prepared statements можно повторно исполнять после commit; portals ограничены transaction и удаляются при ошибке/завершении. RESET очищает все prepared objects. Лимиты session: 128 statements, 128 portals, 8 MiB учитываемых SQL/metadata/buffers. Профиль Psycopg 3 ещё не проверен; результаты существующих Simple Query примеров не доказывают совместимость его автоматически генерируемого SQL.

Для неоднозначного native expression задайте тип самого параметра в исходном SQL, например `CAST(? AS INTEGER) + ID`: Parse OID hints не заменяют вывод типов Firebird, плагин не вставляет CAST. В Extended Query успешный implicit cycle фиксируется на Sync; ошибка откатывает его до Sync. SET TRANSACTION при уже открытом implicit cycle получает 25001. Codecs расширены в P8; клиентская приёмка M2 — в P9.

## Подготовить экземпляр Firebird

После изменения wire-интерфейса пересоберите сервер и плагины вместе и перезапустите сервер. Добавленные wire-таймауты удалены; простой сессии и время выполнения SQL управляются штатными ConnectionIdleTimeout и StatementTimeout.

Нужна сборка текущей ветки с production-библиотекой `plugins/libPgWire.so`, локальная тестовая БД и существующий пользователь Firebird с правами на неё. Начальный host работает на Linux, только в `ServerMode = Super` и на IPv4 loopback.

В `firebird.conf` этого экземпляра:

```ini
ServerMode = Super
RemoteBindAddress = 127.0.0.1
RemoteServicePort = 3050
WireProtocolPlugins = PgWire
WireProtocolPorts = 15432

AuthServer = Srp256
AuthClient = Srp256
```

Native-порт `3050` и PostgreSQL-порт `15432` принадлежат одному серверу. Используйте свободные порты; если native уже настроен на другой порт, сохраните его и замените `3050` в примерах ниже.

В `plugins.conf`:

```ini
Plugin = PgWire {
    Module = $(dir_plugins)/PgWire
    RegisterName = PgWire
    Config = PgWireConfig
}

Config = PgWireConfig {
    BindAddress = 127.0.0.1
    DatabaseAliases = pgwire_test
    Workers = 4
    QueueSize = 16
}
```

В `databases.conf` укажите действительный путь к уже созданной локальной БД:

```ini
pgwire_test = /absolute/path/to/pgwire_test.fdb
```

`DatabaseAliases` — разрешённые имена БД для endpoint. Клиент передаёт `pgwire_test`, а не путь к файлу. У процесса Firebird должен быть доступ к файлу. Per-database settings должны разрешать `RemoteAccess` и Srp256 в AuthClient/AuthServer. Используется настроенная локальная security database; в её `Providers` должен присутствовать `Engine14`. Пароль — настоящий пароль пользователя Firebird. TLS пока отсутствует, поэтому endpoint остаётся на `127.0.0.1`.

Примените конфигурацию при запуске или штатном перезапуске этого экземпляра. Для отдельного запуска уже собранного сервера можно использовать:

```bash
FIREBIRD_BIN=/tmp/firebird-pgwire-build/gen/Debug/firebird/bin
"$FIREBIRD_BIN/firebird" -d
```

Замените значение `FIREBIRD_BIN` на каталог `bin` своей сборки. Команда запускает сервер в foreground и занимает терминал; native и PgWire клиенты запускаются в других терминалах. Если экземпляр уже работает как служба, второй процесс на тех же портах не нужен. Далее сервер считается работающим; для сравнения данных нужны два клиентских терминала.

## Подключиться по PostgreSQL wire

Команды выполняются из корня репозитория:

```bash
python3 pg_wire_protocol/tools/pgwire_client.py \
    -H 127.0.0.1 -p 15432 -d pgwire_test -U PGWIRE_TEST -P 'YOUR_PASSWORD'
```

Замените `PGWIRE_TEST` на существующего пользователя, `YOUR_PASSWORD` — на его пароль. База задаётся через `-d` / `--database`, пароль — через `-P` / `--password`. Короткий `-W` также принимает значение пароля. Скрытого запроса ввода больше нет: если пароль не указан аргументом или через `PGPASSWORD`, клиент завершится с сообщением об отсутствующем параметре. Значение из командной строки имеет приоритет перед `PGPASSWORD`.

Клиент передаёт имя или путь БД буквально, но текущий сервер PgWire принимает только разрешённые alias. Для файла `employee.fdb` можно добавить в `databases.conf` строку `pgwire_test = $(dir_sampleDb)/employee.fdb` и передать `-d pgwire_test`. Прямое подключение через `-d /path/to/employee.fdb` требует дальнейшей поддержки со стороны сервера.

Адрес, порт, alias и пользователь также задаются через `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`; аргументы командной строки имеют приоритет. `PGHOSTADDR` и `PGSERVICE` игнорируются, чтобы они не переопределяли выбранный адрес. Клиент отключает TLS/GSS encryption для текущего loopback endpoint и дополнительные SQL-проверки `target_session_attrs`.

При запуске из терминала без `-c`/`-f` включается интерактивный режим. Его можно выбрать явно через `--interactive` или `-i`.

Введите SQL, затем отдельной строкой `\g`:

```text
SELECT CAST(40 + 2 AS INTEGER) AS "answer" FROM RDB$DATABASE
\g
```

Ожидаются колонка `answer`, OID `23`, значение `42`, command tag `SELECT 1` и состояние transaction `IDLE`/`I`. Клиент показывает имена и OID колонок, строки, NULL, command tag и состояние транзакции из libpq.

| Команда на отдельной строке | Действие |
|---|---|
| `\g` | Отправить накопленный SQL одним вызовом PQexec |
| `\p` | Показать накопленный текст |
| `\r` | Очистить накопленный текст |
| `\q` | Завершить соединение |
| `\?` | Показать справку |

Точка с запятой **не запускает** запрос: выполнением управляет `\g`. Клиент не ищет SQL statements и не разделяет текст по `;`. Завершающая SQL-точка с запятой допустима, но две команды в одном буфере попадут в parser целиком и пока будут отклонены. После ошибки вне явного блока можно сразу продолжать. Ошибка внутри явного блока переводит его в E: для продолжения нужен ROLLBACK или допустимый ROLLBACK TO SAVEPOINT.

Проверка типов и NULL:

```text
SELECT CAST(-7 AS SMALLINT) AS "small",
       CAST(42 AS INTEGER) AS "integer",
       CAST(1234567890123 AS BIGINT) AS "big",
       CAST(NULL AS INTEGER) AS "nil"
FROM RDB$DATABASE
\g
```

Ожидаемые OID: `21`, `23`, `20`, `23`. Дополнительные типы P4:

```text
SELECT CAST(TRUE AS BOOLEAN) AS "flag",
       CAST('Я' AS CHAR(4) CHARACTER SET UTF8) AS "fixed",
       CAST('Я🙂 ' AS VARCHAR(20) CHARACTER SET UTF8) AS "varying",
       CAST('-123.40' AS NUMERIC(18, 2)) AS "amount",
       CAST(7 AS NUMERIC(9, 0)) AS "whole_numeric",
       CAST(NULL AS VARCHAR(20) CHARACTER SET UTF8) AS "nil_text"
FROM RDB$DATABASE
\g
```

Ожидаемые OID: `16`, `1042`, `1043`, `1700`, `1700`, `1043`. BOOLEAN выводится как `t/f`; CHAR содержит `Я` и три пробела, VARCHAR сохраняет конечный пробел. Значение amount — ровно `-123.40`, whole_numeric — `7` с OID numeric, несмотря на scale=0. NULL отличается от пустой строки. Выравнивание таблицы добавляет собственные пробелы при отображении; эти пробелы не являются данными результата.

FLOAT/DOUBLE, даты/время без TZ и INT128 теперь поддержаны. Например:

```sql
SELECT CAST(1.25 AS DOUBLE PRECISION),
       CAST('2026-10-14' AS DATE),
       CAST('12:34:56.1234' AS TIME),
       CAST('1999-12-31 23:59:59.9999' AS TIMESTAMP),
       CAST(12345678901234567890123456789012345678 AS NUMERIC(38,0))
FROM RDB$DATABASE;
\g
```

Даты ограничены годами 0001..9999, дробная часть времени — native точностью 100 мкс. Для Bind text используется ISO, а непредставимая точность отклоняется, не округляется. Numeric передаётся без потери decimal precision. BLOB, DECFLOAT и типы WITH TIME ZONE пока получают `0A000` до исполнения. Пример отказа: `SELECT CAST(1 AS DECFLOAT(16)) FROM RDB$DATABASE`. Строковые результаты, включая `CURRENT_USER`, уже поддерживаются в пределах описанных типов.

Для EmptyQueryResponse отправьте `\g` при пустом буфере либо выполните:

```text
/* только комментарий */
-- SQL-токенов здесь нет
\g
```

Ожидаются `Command: (empty query)` и состояние `I`, без строк результата. Незаконченный `SELECT`, незакрытая строка или block comment остаются ошибками, после которых можно отправить следующий запрос.

## Одноразовые запросы, файлы и pipe

Каждый `-c` отправляет один полный SQL. Повторные `-c` выполняются последовательно на одном соединении:

```bash
python3 pg_wire_protocol/tools/pgwire_client.py \
    -H 127.0.0.1 -p 15432 -d pgwire_test -U PGWIRE_TEST -P 'YOUR_PASSWORD' \
    -c 'SELECT CAST(6 * 7 AS INTEGER) AS "answer" FROM RDB$DATABASE' \
    -c 'SELECT CAST(9 AS SMALLINT) AS "next" FROM RDB$DATABASE'
```

`-f` читает UTF8-файл и отправляет всё его содержимое одним запросом:

```bash
python3 pg_wire_protocol/tools/pgwire_client.py \
    -H 127.0.0.1 -p 15432 -d pgwire_test -U PGWIRE_TEST -P 'YOUR_PASSWORD' \
    -f /absolute/path/query.sql
```

Можно чередовать и повторять `-c`/`-f`; порядок аргументов сохраняется. Это не запуск isql/psql script: в файле должен быть один SQL statement, без `\g`, `SET TERM`, `CONNECT` и других команд клиентской оболочки.

По умолчанию после SQL-ошибки клиент продолжает выполнять оставшиеся `-c`/`-f`; `--stop-on-error` завершает работу на первой ошибке. Код выхода для пакетного запуска: `0` — все запросы успешны, `1` — была SQL-ошибка, `2` — ошибка настройки, подключения или чтения файла. В интерактивном режиме обычный выход через `\q` после исправления SQL-ошибок возвращает `0`.

Без `-c`/`-f` и без интерактивного режима перенаправленный stdin также становится одним полным SQL:

```bash
printf '%s\n' 'SELECT CAST(42 AS INTEGER) FROM RDB$DATABASE' |
    python3 pg_wire_protocol/tools/pgwire_client.py \
        -H 127.0.0.1 -p 15432 -d pgwire_test -U PGWIRE_TEST -P 'YOUR_PASSWORD'
```

Вместо аргумента пароля можно использовать окружение `PGPASSWORD`. Клиент не анализирует SQL-файл или pipe как последовательность отдельных запросов.

## Два терминала: native меняет, PgWire читает

В первом клиентском терминале запустите native isql. Пример для Bash получает пароль через скрытый ввод и передаёт его только окружению дочернего процесса:

```bash
FIREBIRD_BIN=/tmp/firebird-pgwire-build/gen/Debug/firebird/bin
read -r -s -p 'Firebird password: ' PGWIRE_MANUAL_PASSWORD
printf '\n'
ISC_PASSWORD="$PGWIRE_MANUAL_PASSWORD" "$FIREBIRD_BIN/isql" \
    -user PGWIRE_TEST 'inet://127.0.0.1:3050/pgwire_test'
unset PGWIRE_MANUAL_PASSWORD
```

Здесь `unset` выполнится после выхода из isql. Выбранный пользователь должен иметь права на создание тестовой таблицы и изменение данных. Таблица ниже должна отсутствовать; используйте собственное уникальное имя, если оно уже занято.

В native isql:

```sql
CREATE TABLE PGWIRE_MANUAL (ID INTEGER NOT NULL PRIMARY KEY, VAL INTEGER);
COMMIT;
INSERT INTO PGWIRE_MANUAL VALUES (1, 10);
INSERT INTO PGWIRE_MANUAL VALUES (2, 20);
COMMIT;
```

Во втором клиентском терминале подключитесь через `pgwire_client.py` и выполните:

```text
SELECT ID, VAL FROM PGWIRE_MANUAL ORDER BY ID
\g
```

Ожидаются `(1, 10)` и `(2, 20)`. Теперь в native isql:

```sql
UPDATE PGWIRE_MANUAL SET VAL = 11 WHERE ID = 1;
COMMIT;
DELETE FROM PGWIRE_MANUAL WHERE ID = 2;
COMMIT;
```

Повторите SELECT в PgWire: ожидается только `(1, 11)`. Без SET TRANSACTION каждый PgWire Query исполняется в новой implicit READ COMMITTED READ CONSISTENCY transaction и завершает её commit после чтения всех строк.

Проверка native rollback:

```sql
UPDATE PGWIRE_MANUAL SET VAL = 999 WHERE ID = 1;
ROLLBACK;
```

Повторный PgWire SELECT должен по-прежнему вернуть `(1, 11)`. В этом сценарии PgWire работает без SET TRANSACTION, поэтому отдельный COMMIT после каждого запроса не нужен. Изменения из другого соединения native observer с snapshot transaction увидит после своего `COMMIT;` или `ROLLBACK;` и нового SELECT.

## Два терминала: PgWire меняет, native проверяет

Этот отдельный сценарий использует новую таблицу `PGWIRE_P4_MANUAL` и не меняет данные `PGWIRE_MANUAL` из предыдущего раздела. Имя таблицы должно быть свободным; PgWire пользователь должен иметь права на создание и изменение объектов.

В PgWire выполните каждый запрос отдельным `\g`:

```text
CREATE TABLE PGWIRE_P4_MANUAL (
    ID INTEGER NOT NULL PRIMARY KEY,
    VAL INTEGER,
    FLAG BOOLEAN,
    FIXED_TEXT CHAR(4) CHARACTER SET UTF8,
    NOTE VARCHAR(20) CHARACTER SET UTF8,
    AMOUNT NUMERIC(18, 2))
\g
INSERT INTO PGWIRE_P4_MANUAL VALUES (1, 10, TRUE, 'Я', 'A ', 1.20)
RETURNING ID, VAL, FLAG, FIXED_TEXT, NOTE, AMOUNT
\g
```

Ожидаются tags `CREATE TABLE` и `INSERT 0 1`. INSERT RETURNING выдаёт ID=1, VAL=10, FLAG=`t`, FIXED_TEXT с логической CHAR-добивкой, NOTE=`A ` и AMOUNT=`1.20`. После каждого запроса состояние остаётся `I`; дополнительный COMMIT через PgWire не требуется.

В native isql завершите прежнюю snapshot transaction и прочитайте изменения:

```sql
COMMIT;
SELECT ID, VAL, FLAG, FIXED_TEXT, NOTE, AMOUNT
FROM PGWIRE_P4_MANUAL ORDER BY ID;
COMMIT;
```

Должна быть видна строка, записанная через PgWire. Теперь в PgWire:

```text
UPDATE PGWIRE_P4_MANUAL SET VAL = 11, AMOUNT = -0.12
WHERE ID = 1 RETURNING ID, VAL, AMOUNT
\g
```

Ожидаются строка `(1, 11, -0.12)` и tag `UPDATE 1`. В native observer новый SELECT после `COMMIT;` должен показать те же значения. Затем в PgWire:

```text
DELETE FROM PGWIRE_P4_MANUAL WHERE ID = 1 RETURNING ID, VAL
\g
SELECT ID, VAL FROM PGWIRE_P4_MANUAL ORDER BY ID
\g
```

DELETE RETURNING выдаёт `(1, 11)` с tag `DELETE 1`, а SELECT — ноль строк и `SELECT 0`. В native isql:

```sql
COMMIT;
SELECT ID, VAL FROM PGWIRE_P4_MANUAL;
COMMIT;
```

После подтверждения пустой таблицы и завершения native transaction удалите объект через PgWire:

```text
DROP TABLE PGWIRE_P4_MANUAL
\g
```

Ожидается tag `DROP TABLE`. Native observer может проверить отсутствие объекта:

```sql
COMMIT;
SELECT COUNT(*) FROM RDB$RELATIONS
WHERE RDB$RELATION_NAME = 'PGWIRE_P4_MANUAL';
COMMIT;
```

Ожидается `0`. Этот сценарий проверяет implicit commit для CREATE, INSERT, UPDATE, DELETE и DROP через PgWire и видимость изменений через родной протокол.

## Проверить ошибки и продолжение работы

В интерактивном PgWire клиенте по очереди отправьте:

```text
UPDATE PGWIRE_MANUAL SET VAL = 999 WHERE ID = 1
RETURNING CAST(VAL AS DECFLOAT(16))
\g
BEGIN
\g
SELECT $1 FROM RDB$DATABASE
\g
SELECT ID, VAL FROM PGWIRE_MANUAL ORDER BY ID
\g
```

UPDATE должен получить `0A000` из-за неподдержанного типа RETURNING **до изменения строки**. BEGIN и `$1` дают ошибку парсера. Этот пример выполняется из I, без открытого явного блока. Последний SELECT должен работать на том же connection и вернуть прежнюю строку `(1, 11)` из native-сценария. Native observer после завершения своей snapshot transaction также должен увидеть VAL=11. Успешный UPDATE без неподдержанного RETURNING теперь выполняется и фиксируется — он больше не подходит для проверки отказа.

Клиент выводит SQLSTATE и server diagnostic. Fatal/transport error может завершить соединение; это отличается от обычной recoverable SQL-ошибки.

## Явные транзакции и savepoints

На тестовой БД создайте отдельную таблицу через PgWire (вне явного блока):

```text
CREATE TABLE PGWIRE_TX_MANUAL (ID INTEGER PRIMARY KEY, VAL INTEGER)
\g
SET TRANSACTION READ WRITE ISOLATION LEVEL READ COMMITTED RECORD_VERSION
\g
INSERT INTO PGWIRE_TX_MANUAL VALUES (1, 10)
\g
```

После SET TRANSACTION ожидаются tag `BEGIN` и состояние T; INSERT остаётся в T. Команда BEGIN в тексте SQL этим не поддерживается. В другом native-подключении выполните `COMMIT; SELECT * FROM PGWIRE_TX_MANUAL; COMMIT;`: новая строка пока не видна.

В том же PgWire-подключении:

```text
SAVEPOINT BEFORE_UPDATE
\g
UPDATE PGWIRE_TX_MANUAL SET VAL = 20 WHERE ID = 1
\g
INSERT INTO PGWIRE_TX_MANUAL VALUES (1, 30)
\g
SELECT * FROM PGWIRE_TX_MANUAL
\g
ROLLBACK TO SAVEPOINT BEFORE_UPDATE
\g
SELECT * FROM PGWIRE_TX_MANUAL
\g
COMMIT
\g
```

Повторный INSERT получает 23505 и переводит блок в E. Первый SELECT получает 25P02. ROLLBACK TO возвращает T; следующий SELECT показывает `(1, 10)`, поскольку изменение до savepoint сохранено. COMMIT переводит соединение в I. Native observer после завершения своей snapshot transaction теперь видит `(1, 10)`.

Для полной отмены откройте новый блок, измените VAL и отправьте ROLLBACK. Native observer должен по-прежнему видеть 10. Если вместо восстановления отправить COMMIT из E, сервер выполнит rollback всего незавершённого блока и вернёт tag ROLLBACK/I. Выход `\q` или обрыв соединения также откатывает незавершённую transaction.

Native retaining записывается как `COMMIT RETAIN [SNAPSHOT]` или `ROLLBACK RETAIN [SNAPSHOT]` и сохраняет T. Написание RETAINING текущий parser не принимает. Native AUTO COMMIT фиксирует промежуточные изменения; последующий ROLLBACK их не отменит. Проверенный профиль — `SET TRANSACTION READ WRITE SNAPSHOT AUTO COMMIT`. Сочетание AUTO COMMIT с READ COMMITTED READ CONSISTENCY вызывает assertion в текущем native Debug-движке даже через isql без PgWire; до исправления движка этот профиль не используйте. Подробности воспроизведения — в [VALIDATION.md](VALIDATION.md). Обычный SET TRANSACTION без характеристик сохраняет native default SNAPSHOT, поэтому для READ COMMITTED указывайте его явно.

`ALTER SESSION RESET` реально сбрасывает настройки/контекст сессии. В T он откатывает изменения и создаёт новую transaction, оставляя T; в E получает 25P02. После проверки завершите блок и удалите тестовую таблицу:

```text
ROLLBACK
\g
DROP TABLE PGWIRE_TX_MANUAL
\g
```

## Необязательно: увидеть implicit commit и rollback записи из SELECT

SELECT может вызывать процедуры с побочными эффектами. Следующий отдельный пример показывает запись внутри selectable PSQL procedure, созданной через native isql. Он дополняет прямой CRUD-сценарий проверкой implicit commit и rollback после ошибки внутри процедуры. Используйте тестовую таблицу `PGWIRE_MANUAL` из native-сценария; у строки ID=1 ожидается VAL=11.

В native isql:

```sql
SET TERM ^ ;
CREATE PROCEDURE PGWIRE_MANUAL_BUMP (P_ID INTEGER, P_FAIL SMALLINT)
RETURNS (V INTEGER)
AS
BEGIN
  UPDATE PGWIRE_MANUAL SET VAL = VAL + 1
    WHERE ID = :P_ID RETURNING VAL INTO :V;
  IF (P_FAIL = 1) THEN
    V = V / (P_FAIL - 1);
  SUSPEND;
END^
SET TERM ; ^
COMMIT;
```

Процедуру и таблицу здесь использует тот же пользователь, который их создал. Если PgWire подключается под другим пользователем, заранее выдайте необходимые object/EXECUTE privileges через native интерфейс.

В PgWire:

```text
SELECT V FROM PGWIRE_MANUAL_BUMP(1, 0)
\g
```

Ожидается `12`. После чтения до EOF сервер фиксирует implicit transaction. В native isql завершите предыдущую snapshot transaction и проверьте:

```sql
COMMIT;
SELECT ID, VAL FROM PGWIRE_MANUAL WHERE ID = 1;
```

Ожидается VAL=12. Теперь в PgWire намеренно вызовите ошибку после UPDATE внутри процедуры:

```text
SELECT V FROM PGWIRE_MANUAL_BUMP(1, 1)
\g
```

Ожидается ошибка деления на ноль `22012`, без успешного CommandComplete. Implicit transaction откатывается. После `COMMIT;` или `ROLLBACK;` в native observer новый SELECT должен снова показать VAL=12, а следующий PgWire SELECT — успешно выполниться.

Созданные для этой проверки объекты удаляются через native isql:

```sql
COMMIT;
DROP PROCEDURE PGWIRE_MANUAL_BUMP;
COMMIT;
DROP TABLE PGWIRE_MANUAL;
COMMIT;
```

Если необязательную процедуру не создавали, пропустите её DROP. Подробные границы текущего этапа — в [COMPATIBILITY.md](COMPATIBILITY.md), результаты автоматической приёмки — в [VALIDATION.md](VALIDATION.md).
