# Задание для начала реализации PostgreSQL frontend

Этот текст предназначен для следующего поручения на реализацию. Подготовка и сохранение текущего пакета не запускают это задание автоматически.

---

Реализуй PostgreSQL wire frontend внутри штатного сервера Firebird 6 по пакету `pg_wire_protocol` в корне workspace. Прочитай применимые AGENTS.md, затем README.md, BASELINE.md, CODEX_CONTEXT.md, COMPATIBILITY.md, PLAN.md и TEST_PLAN.md из этого пакета. RESEARCH.md и SOURCES.md содержат обоснование и карту исходников.

Работай в ветке `pg_wire_protocol`, основанной на `master` Firebird 6. Исходная база — `bf94598f8deefeb7cc4a8217ee43b3040d5d6650`, `6.0.0.2199 Initial`, Engine14, C++20. Проверь фактический HEAD и сохрани существующие изменения. Сначала установи штатный build/native baseline.

PostgreSQL TCP connection принимает сам процесс `firebird`. Новый listener, codec и session dispatcher вызывают внутренние attachment/transaction/DSQL interfaces и локальный engine provider. Родной Firebird listener работает параллельно. Отдельный gateway, вторичный attach по сети/Remote/Loopback, synthetic native PACKET и исполнение SQL в PostgreSQL не соответствуют архитектуре. Существующая внутренняя линковка с libfbclient допустима; проверяется фактический путь выполнения.

Выполняй PLAN последовательно и отмечай достигнутый этап:

- M0: реальная authentication, вычисляемый SELECT, error/recovery и disconnect; psycopg2 и прямой C libpq PQexec.
- M1: законченный ограниченный Simple Query profile — scalar types, CRUD, I/T/E, privileges, wire/resource/lifecycle и native regression tests.
- M2: PQexecParams/PQprepare/PQexecPrepared, Extended Query, `$n`, statements/portals, text/binary codecs, дополнительные types и RETURNING.
- M3: удалённый TCP, TLS, cancellation и согласованный профиль приложения с обычным PostgreSQL-драйвером.

M0 и M1 являются промежуточными результатами. Успешный Simple Query не означает готовность общего libpq endpoint. Следуй границам порученного этапа, если пользователь явно ограничил текущую реализацию. Имя приложения пока не задано; это не блокирует M0–M2. Перед прикладной приёмкой M3 установи его driver/version/settings и используемые операции.

Для начала: Linux, ServerMode=Super, feature default disabled, loopback port 15432, protocol 3.0, UTF8, Firebird SQL dialect 3. PostgreSQL grammar и catalog emulation остаются вне задачи. В M1 не переписывай SQL; в M2 реализуй только предусмотренное token-aware сопоставление `$n` с native parameter slots, без интерполяции значений.

Приоритетные места исходников: `src/remote/server/os/posix/inet_server.cpp`, `src/remote/server/server.cpp`, `src/jrd/extds/ValidatePassword.cpp`, `src/jrd/constants.h`, `src/common/classes/ParsedList.cpp`, `src/yvalve/why.cpp`, `builds/posix/make.shared.variables`. Новый модуль размещай в `src/remote/server/pgwire` и включай в существующий server target. Не запускай второй независимый SRVR_multi_thread поверх native глобальных очередей.

Auth core выделяй из существующего plugin exchange с сохранением EDS guards. Используй настоящий разрешённый Srp256 profile, verified auth block, sanitised DPB и реальные peer metadata. Не доверяй Startup user без проверки и не обходи RemoteAccess/per-database policy. Engine-only route требуется и при ошибках; local security database входит в начальный профиль.

Первый контрольный SQL:

```sql
SELECT CAST(40 + 2 AS INTEGER) AS "answer" FROM RDB$DATABASE
```

Результат 42/OID 23 вычисляет Firebird. Затем проверь другое выражение, ошибочный SQL и успешный SQL на том же connection. Одновременно работает native клиент. Начни lifecycle/ownership проверки сразу с listener, включая bounded shutdown при активном SQL.

Подготовленный Python-файл предназначен для M1. При добавлении M2 перенеси временные negative tests по правилам TEST_PLAN; не оставляй прежний отказ от новых поддержанных types/RETURNING и не скрывай failures через blanket skip/xfail. Добавь независимые C libpq и raw-wire tests; они не заменяются клиентской подстановкой параметров в psycopg2.

Все интеграционные результаты получай против настоящего встроенного endpoint. `evidence/driver_probe.json` — исторический синтетический опыт. Collection, единичный SELECT и MON$SERVER_PID не доказывают приёмку. Проверяй владельца listener, отсутствие backend connect(), права, native cross-visibility и освобождение ресурсов.

В `pg_wire_protocol/IMPLEMENTATION_NOTES.md` записывай выбранные решения, в `pg_wire_protocol/VALIDATION.md` — SHA/версии, commands, реальные results, доказательство native execution, принятый этап и оставшиеся ограничения. При ограничении среды укажи точный непрошедший шаг; не подменяй runtime checks mock-результатами. Обычные обратимые работы в рамках порученной реализации выполняй без повторного запроса разрешения.
