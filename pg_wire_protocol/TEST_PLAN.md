# План проверки PostgreSQL frontend в Firebird 6

База — master Firebird 6, рабочая ветка pg_wire_protocol, исходный SHA из BASELINE.md. Разделы 1–8 описывают приёмку M1 (прежнего v0); общие architecture/auth/lifecycle проверки сохраняются после M1. Разделы 10–13 добавляют проверки M2/M3 и правила изменения временных отрицательных tests.

| Этап | Обязательная приёмка |
|---|---|
| M0 | Встроенный listener, реальная auth, вычисляемый SELECT/error/recovery/disconnect; psycopg2 и C libpq PQexec; native coexistence |
| M1 | A/S/W/I/N/L ниже, подготовленные 56 Python cases и отдельные C libpq PQexec/metadata/transaction/error cases |
| M2 | Применимая регрессия M1, E и D ниже, C libpq parameterized/prepared/async API, обычный Psycopg 3 Cursor |
| M3 | Применимая регрессия M2, R/C/U ниже, TLS/cancel, отдельный сетевой узел и сценарии выбранного приложения |

Текущий статус: перенесён исходный Python scaffold; новых C++/C/Extended/TLS tests пока нет. Статически подтверждены 20 Python-функций и 56 случаев. Сборка и исполнение против Firebird не выполнены. Сообщение исходного пакета о collect-only относится к подготовке того пакета, а не к новой интеграционной проверке.

## 1. Что считается результатом

Для приёмки endpoint должен работать внутри существующего бинарника `firebird`: Linux, `ServerMode = Super`, один серверный PID, собственные `PgListener`, codec, session dispatcher и ограниченная группа workers. Attachment открывается через внутренний server/engine path в памяти. Внешний gateway, второй серверный процесс, backend TCP, выбор `Remote`/`Loopback` provider и подключение через `fbclient` к собственному native listener не входят в принятую архитектуру.

В M1 PostgreSQL wire 3.0 и Simple Query доставляют совместимый Firebird dialect 3 SQL без перевода. Проверяются типы, CRUD, транзакции, identity и доступ к той же базе обычным Firebird клиентом. Extended Query добавляется в M2; PostgreSQL SQL-парсер остаётся вне задачи.

Нужны четыре независимых вида свидетельств:

1. C++ codec/session tests без базы: формат сообщений, TCP stream, состояния и границы ресурсов.
2. Реальные psycopg2 и прямой C libpq → native pgwire frontend → Firebird engine: функциональный контракт клиента; с M2 также обычный Psycopg 3 Cursor.
3. Обычный Firebird клиент: видимость изменений, privileges, сохранность native входа.
4. Архитектурные и lifecycle проверки: один серверный процесс, отсутствие backend hop, корректные authentication и shutdown.

Driver-only probe в `evidence` содержит синтетические результаты и не подтверждает выполнение SQL в Firebird. Четыре уровня приёмки обязательны независимо от успешной collection.

## 2. Fixture и границы окружения

- Отдельная disposable Firebird database: dialect 3, UTF8, серверный alias `pgwire_test` в разрешённом наборе endpoint.
- Сборка целевой ветки Firebird с pgwire feature, Linux, `ServerMode = Super`. Native и PG listeners принадлежат одному процессу `firebird`.
- PG listener на `127.0.0.1:15432`; профиль v0 без TLS. Native observer подключается по явному native TCP connection string к тому же экземпляру, например `inet://127.0.0.1:3050/pgwire_test`.
- TCP соединение **внешнего native observer** разрешено и необходимо для cross-check. Оно не должно превращаться в реализацию backend path PG frontend.
- Основной пользователь `PGWIRE_TEST`, способный создавать свои таблицы, и `PGWIRE_LIMITED` без доступа к таблицам первого пользователя. Оба проходят реальную `Srp256` authentication; это не `SYSDBA`, владелец БД/администратор или общий service account.
- Security database в профиле v0 локальная. Проверка отсутствия исходящих DB connections охватывает и PG auth; внутренние security attachments и штатные caches не считать лишними application attachments.
- Дополнительные конфигурационные fixtures для отказов: alias вне allowlist, alias с `RemoteAccess = false`, конфигурация без допустимого `Srp256`. Их создаёт harness целевой ветки, не обычный driver suite.
- Явные лимиты соединений, workers, очередей, входных/выходных данных, handshake, lock/statement ожиданий и остановки сервера. Значения фиксируются в отчёте.
- Каждый SQL test создаёт собственные имена объектов. После `CREATE TABLE` выполняется commit до DML. Cleanup затрагивает только объекты теста.
- Native observer использует новое соединение или новую транзакцию, чтобы предыдущий snapshot не скрывал commit.
- Credentials передаются через окружение; их нет в коде, аргументах `isql`, логах и итоговом отчёте.

Отсутствие сервера или обязательного параметра в выбранной integration job — ошибка, а не успешный skip.

## 3. Обязательные архитектурные gates

Эти проверки надо реализовать в целевой ветке отдельно от подготовленного Python suite.

| ID | Проверка | Достаточное свидетельство |
|---|---|---|
| A01 | Endpoint находится в существующем `firebird` | Review сборки/entry point и владелец listening socket: тот же PID, который обслуживает native listener; отдельного gateway executable/service нет |
| A02 | PG attach/SQL не создаёт backend соединения | Review фактического call path и выбранного internal provider; контролируемое наблюдение исходящих `connect()` из серверного процесса во время PG connect/SQL. Ни native TCP/Unix loopback, ни `Remote`/`Loopback` fallback для этого пути не допускаются |
| A03 | Socket и worker ownership | Собственные listener/selector/worker resources; отсутствует второй независимый запуск `SRVR_multi_thread()` поверх его глобальных native очередей и `INET_select` |
| A04 | Feature выключена в конфигурации | PG порт не открыт, PG threads/attachments не создаются, обычные native connect/SQL/disconnect проходят |
| A05 | Сборка с feature выключена | Существующий server target собирается; native smoke проходит. Если выбран runtime-only switch, решение явно зафиксировано вместо выдуманного build flag |
| A06 | Граница поддержанных режимов | Linux/Super проходит. Включение PG в неподдержанном режиме даёт явный диагностируемый отказ по конфигурационному контракту, без молчаливого запуска иной архитектуры |
| A07 | Занятый порт/ошибка bind | Предусмотренный startup failure, освобождение частично созданных ресурсов и понятная диагностика; отказ не объявляется успешным стартом PG |

`MON$SERVER_PID`, имя процесса в SQL или совпадение PID из monitoring tables **сами по себе не доказывают** отсутствие gateway: посредник может подключиться к тому же Firebird и получить тот же PID. Для A01/A02 нужны сведения о владельце sockets, кодовом пути и отсутствии вторичного backend соединения. При сетевом наблюдении отделять исходящий native observer от сервера и документировать ограничения инструмента.

## 4. Authentication, identity и доступ

PG `AuthenticationCleartextPassword` передаёт пароль выбранному **in-memory Srp256 core**, выделенному из механики `ValidatePassword`. Это не PG SCRAM и не доверенный attach только по имени пользователя. `AuthenticationOk` разрешён после успешной проверки credentials и открытия разрешённого attachment.

| ID | Проверка | Ожидание |
|---|---|---|
| S01 | Правильный/неправильный пароль, неизвестный пользователь | Успех либо `28P01`; следующий правильный connect работает. После неуспешной authentication не остаётся рабочего user attachment |
| S02 | Лимит auth rounds, ошибка/неподдержанный результат plugin | Ограниченный отказ и освобождение plugin/auth state; нет зависания и успешного trusted fallback |
| S03 | `Srp256` недоступен или запрещён применимой конфигурацией | Явный отказ; нет перехода на OS identity, `Legacy_Auth` или service account ради успешного входа |
| S04 | `SELECT CURRENT_USER FROM RDB$DATABASE` | Identity соответствует аутентифицированному пользователю и документированным правилам его имени; это не имя процесса или владельца сервера |
| S05 | Второй пользователь читает таблицу без GRANT | `42501`; отсутствие привилегий не компенсируется внутренним engine attach |
| S06 | Разрешённый/неизвестный alias | Разрешённый alias открывает ожидаемую БД; неизвестный даёт `3D000`. Client-supplied paths, URI, DPB/config/auth block не подменяют серверное сопоставление |
| S07 | Alias с `RemoteAccess = false` | PG вход отклонён по смыслу как удалённый native вход. Внутренний attach сохраняет remote peer и не обходит проверку как локальный trusted attach |
| S08 | Server-side alias и per-database security configuration | Credentials и открытие БД используют согласованную конфигурацию alias; alias не позволяет выбрать чужую security database или расширить доступ |
| S09 | Параллельные логины разных пользователей | Auth block, password и identity не переходят между сессиями; SQL исполняется с правами своего пользователя |
| S10 | Существующие EDS authentication paths после выделения core | `EDS::validatePassword`/InternalDS/IscDS scenarios сохраняют прежние условия вызова: правильные credentials работают, неправильные отвергаются, skip/precondition cases не меняют семантику |
| S11 | Обычная native authentication | Существующие native connect/SQL и negative-password tests проходят с включённым и выключенным PG frontend |

S10 требует существующих EDS fixtures/тестов целевой ветки либо узких добавленных регрессионных случаев. Успешный PG login этого не доказывает. Review проверяет, что выделенный core не требует уже существующего пользовательского `thread_db`/attachment и не снимает прежние проверки EDS wrapper.

Точные SQLSTATE startup-ошибок S01/S06 проверяются по полю `C` в raw ErrorResponse в C++/wire test. При неуспешном `psycopg2.connect()` драйвер может не получить PGresult и оставить `OperationalError.pgcode = None`; Python test не должен принимать это за неправильный SQLSTATE сервера. Проверка только исключения `OperationalError` не заменяет raw-wire assertion.

## 5. C++ wire/codec tests

| ID | Сценарий | Ожидание |
|---|---|---|
| W01 | Startup без tag; Query/Terminate с tag | Точные boundaries и length, network byte order |
| W02 | Короткий frame разбит по каждой возможной границе | Один результат decoding, без ожидания полного frame в общем accept loop |
| W03 | Несколько frames в одном чтении; frame больше transport-порции | Каждый разобран ровно один раз; остаток сохранён; размер read buffer не выдан за PG frame limit |
| W04 | Partial write, backpressure, send failure | Ответ не теряется и не помечается успешным после ошибки; память и cleanup ограничены |
| W05 | Length меньше минимального, overflow, выше лимита | Отказ до чрезмерной allocation и до исполнения SQL |
| W06 | Отсутствующий NUL, embedded NUL/хвост, некорректный UTF8 | Protocol/encoding error без усечения SQL и side effects |
| W07 | EOF в startup/header/payload/password | Cleanup только этой session; остальные listeners и sessions работают |
| W08 | SSLRequest/GSSENCRequest в M1 | Ровно один byte `N`, затем допустимый startup; TLS-поведение M3 проверяется отдельно, native crypt handshake не появляется |
| W09 | Startup 3.0; неподдержанные major/minor и options | Успех либо понятный FATAL по профилю; незаявленные настройки не игнорируются с успехом |
| W10 | Golden bytes RowDescription/DataRow | OID/typlen, UTF8 byte length, `NULL = -1`, empty = 0, text format; BOOLEAN — один byte `t`/`f` |
| W11 | ErrorResponse/ReadyForQuery/CommandComplete | Обязательные поля и terminators, states `I/T/E`, tags и row counts |
| W12 | Parse/Bind/Execute в M1 | `FATAL 0A000` и close; с M2 эту временную проверку заменяет группа E |
| W13 | Пустой и whitespace-only Query | EmptyQueryResponse и правильное состояние транзакции |
| W14 | Две sessions, interleaved bytes | Независимые framing/auth/SQL states; чужие bytes не попадают в ответ |
| W15 | Native `DummyPacketInterval` включён | На PG socket не отправляются `op_dummy`, native compression, XDR или wire-crypt bytes; native keepalive работает |
| W16 | CancelRequest в M1, недопустимые message sequences | Документированный ограниченный отказ/закрытие; с M3 CancelRequest проверяется группой C, native async opcode handling не используется |

Для W10 использовать независимо проверенные byte fixtures. Encode→decode одним кодом не служит единственным доказательством. Отдельная fuzzing infrastructure для v0 не требуется: нужны regression cases границ framing и состояний.

## 6. SQL и transaction contract

Часть проверок подготовлена в `tests/test_pgwire_acceptance.py`. Строка в таблице не означает, что проверка уже реализована или пройдена: сверять pytest node IDs и оставшиеся C++/integration cases.

| ID | Проверка | Ожидаемое поведение |
|---|---|---|
| I01 | Connect + `SELECT CAST(6*7 AS INTEGER) FROM RDB$DATABASE` | 42 вычислено Firebird; INTEGER OID 23 |
| I02 | Пустой result set | Description существует, строк 0, `SELECT 0` |
| I03 | Типы, границы и metadata | Правильные OID, Python types, значения и quoted column aliases |
| I04 | UTF8, апостроф, backslash, semicolon, LF/TAB | Нет rewriting, потерь или исполнения фрагмента параметра как SQL |
| I05 | NULL и пустая строка | `None` отличается от `""` |
| I06 | CREATE/DROP TABLE, INSERT/UPDATE/DELETE | Правильные tags, rowcount, commit и данные |
| I07 | Explicit commit и отдельная session | До commit запись не видна, после commit видна |
| I08 | Explicit rollback | Запись отсутствует, соединение пригодно дальше |
| I09 | Unique violation внутри block | `23505`, state `E`, следующий SQL получает `25P02` |
| I10 | COMMIT и ROLLBACK из E | Изменения не сохранены; state `I`; COMMIT из E возвращает tag ROLLBACK |
| I11 | Disconnect с активной transaction | Rollback; другая session вставляет тот же ключ без оставленного lock |
| I12 | Ошибка SQL вне block | ErrorResponse + `I`, затем успешный SELECT на том же connection |
| I13 | Multi-statement, включая DML первым statement | Отказ до любых изменений; никакого `split(';')` |
| I14 | INSERT/UPDATE/DELETE RETURNING вне поддержки | `0A000` до исполнения; ни одна строка не изменена |
| I15 | Unsupported output type | `0A000`, без подмены на str/NULL; отказ до исполнения, если тип известен после prepare |
| I16 | Python date, адаптированная в `::date` | Понятная syntax error; endpoint не переписывает PostgreSQL cast |
| I17 | U+0000 в выдаваемом text | `22021`; нет молчаливого усечения |
| I18 | `with connection` при `autocommit=True` | Реальные BEGIN/COMMIT; context manager не принимается за простое закрытие connection |
| I19 | Пустой Query/повторный BEGIN/COMMIT и ROLLBACK в I | Согласованные states, notices и tags без ложной вложенной транзакции |
| I20 | Ошибка fetch или commit после начала ответа | Нет ложного CommandComplete; корректный error/state либо закрытие при утрате достоверного состояния |
| N01 | PG write → native read; native write → PG read | Одна реальная база, видимость подтверждена после commit |
| N02 | Native SQL под разными пользователями | PG endpoint сохраняет engine privilege enforcement и CURRENT_USER |

I03 включает SMALLINT/INTEGER/BIGINT, `INT64_MIN`, BOOLEAN `t/f`, CHAR padding, VARCHAR с кириллицей, NUMERIC(18,0), дробный NUMERIC/DECIMAL на поддержанном физическом типе, ноль и NULL. Для Decimal не использовать double как источник ожидаемого значения. FLOAT/DOUBLE, INT128, DECFLOAT, temporal types, BLOB/ARRAY/OCTETS остаются unsupported.

Транзакционные unit tests отдельно проверяют выбранную реализацию `E`: native rollback сразу при ошибке внутри block, сохранение logical failed state и отказ от SQL до COMMIT/ROLLBACK. Для `I` нужен настоящий commit/rollback каждой команды; retaining и native autocommit не заменяют этот контракт.

## 7. Обязательные lifecycle и resource tests

| ID | Сценарий | Ожидание |
|---|---|---|
| L01 | Серия connect/auth/query/disconnect, включая ошибки | Attachments, transactions, statements, cursors, sockets и worker slots возвращаются к исходному уровню |
| L02 | Максимум sessions, очередь заполнена, медленный client | Число workers и размер очередей ограничены; явный отказ/timeout; native listener работает |
| L03 | Handshake завис, password не прислан, partial query не завершён | Deadline освобождает session; один client не занимает accept loop или worker навсегда |
| L04 | Shutdown при незавершённом auth, активной transaction и SQL/lock wait | Остановлен accept, работа прервана штатным способом в пределах deadline, выполнены rollback/detach/close, workers joined до разрушения используемой engine/plugin infrastructure |
| L05 | Ошибка на этапах startup/attachment/cleanup | Нет use-after-free, double release/dispose, забытых sockets и обращения к завершённому provider |
| L06 | PG worker fault/исключение, malformed client | Затронута только эта session; общий error path не отправляет native PACKET на PG socket |

Для L01 выбрать подходящее средство целевой ветки: lifecycle counters, monitoring и/или доступную sanitizer build. Отсутствие SQL lock не доказывает отсутствие утечек handles. Не нужно запускать все инструменты одновременно без конкретного риска.

Baseline L01 снимать после прогрева. Отдельно учитывать документированные server/plugin caches; проверять освобождение session-owned ресурсов и отсутствие роста между повторными одинаковыми сериями, а не требовать нулевого числа всех attachments сервера.

Для L04 проверить собственный PgListener/worker group именно в server init/shutdown path. Успешное завершение Python client не подтверждает порядок teardown внутри `firebird`.

## 8. Подготовленный Python suite: окружение и запуск

`tests/test_pgwire_acceptance.py` — executable acceptance cases для будущего endpoint. Он проверяет driver/SQL контракт, но не заменяет C++ framing, authentication-core, architecture и shutdown tests разделов 3–7.

При подготовке collection обнаружила 56 параметризованных cases из 20 тестовых функций; без `native`/`privileges` выбираются 54. Это число собранных, не пройденных against Firebird tests. Среди остающихся отдельных gates — CURRENT_USER, RemoteAccess, raw framing/Extended messages, точные startup SQLSTATE, EDS regression, socket/provider architecture и shutdown. Native roundtrip проверяет видимость SQL effects, а не расположение frontend в процессе.

Закреплённые зависимости: `psycopg2-binary==2.9.11`, `pytest==8.4.2`. Команды выполняются из корня пакета либо соответствующего места после переноса файлов в репозиторий:

~~~sh
python3 -m venv .venv-pgwire
. .venv-pgwire/bin/activate
python -m pip install -r requirements-tests.txt
~~~

Пример окружения без настоящих секретов:

~~~sh
export PGWIRE_TEST_DSN='host=127.0.0.1 port=15432 dbname=pgwire_test user=PGWIRE_TEST'
export PGPASSWORD='<пароль основного пользователя>'
export PGWIRE_LIMITED_DSN='host=127.0.0.1 port=15432 dbname=pgwire_test user=PGWIRE_LIMITED'
export PGWIRE_LIMITED_PASSWORD='<пароль ограниченного пользователя>'
export FB_NATIVE_DSN='inet://127.0.0.1:3050/pgwire_test'
export FB_NATIVE_USER='PGWIRE_TEST'
export FB_NATIVE_PASSWORD='<пароль native пользователя>'
export FB_ISQL='/absolute/path/to/firebird/isql'
~~~

`FB_ISQL` — Firebird isql, не одноимённая ODBC-утилита. Native subprocess получает credentials через своё окружение. DSN с паролем нельзя выводить при ошибке теста.

Python helper требует явный loopback `host=127.0.0.1` или `host=::1`, `dbname` и `user`, запрещает пароль внутри PG DSN и задаёт `hostaddr`, UTF8, `sslmode=disable`, `gssencmode=disable`, `target_session_attrs=any`, пустые `options`, `connect_timeout=5`. На runtime libpq 18+ он также задаёт `max_protocol_version=3.0`. Другие настройки из окружения/DSN не расширяют заявленный контракт.

| Выбранные tests | Обязательные параметры |
|---|---|
| Любые integration | `PGWIRE_TEST_DSN`, `PGPASSWORD` |
| Дополнительно marker `native` | `FB_ISQL`, `FB_NATIVE_DSN`, `FB_NATIVE_USER`, `FB_NATIVE_PASSWORD` |
| Дополнительно marker `privileges` | `PGWIRE_LIMITED_DSN`, `PGWIRE_LIMITED_PASSWORD` |

Module-level marker — `integration`; дополнительные markers — `native` и `privileges`. Недостающая переменная для выбранной группы вызывает fixture failure. Collection без параметров допускается: SQL при collection не исполняется.

Ограниченный запуск до настройки native/limited fixtures:

~~~sh
timeout 120s python -m pytest -q -m 'integration and not native and not privileges'
~~~

Полный подготовленный driver suite:

~~~sh
timeout 180s python -m pytest -q -m integration
~~~

Подготовительная проверка без сервера:

~~~sh
python -m py_compile tests/test_pgwire_acceptance.py
python -m pytest --collect-only -q
~~~

Внешний timeout ограничивает зависание драйвера при ошибке wire. `connect_timeout` ограничивает только подключение; он не заменяет общий deadline и server-side lock/statement limits. Native subprocess имеет собственный timeout.

В ограниченном запуске нет native roundtrip и privilege proof. Даже полный Python suite не закрывает architecture/authentication-core/C++/lifecycle gates. В отчёте называть выполненную группу, а не объявлять весь v0 принятым.

## 9. Формат итогового VALIDATION

- Repository/SHA, build flags, ОС и `ServerMode`; включение/выключение PG feature.
- Firebird server/native client, psycopg2, фактические compile/runtime libpq versions. Пин psycopg2 не заменяет запись libpq/platform profile.
- PG connection/protocol profile и server limits без секретов.
- Выполненные commands, collection и реальные execution results раздельно, точные failing/skipped node IDs.
- A01/A02 evidence: владелец sockets, внутренний attach path и способ исключения secondary backend connections.
- Srp256, CURRENT_USER, denied privilege, RemoteAccess, alias и EDS/native regression results.
- Native roundtrip, lifecycle/shutdown и feature-disabled results.
- Что осталось не реализовано/не проверено; соответствующие gate IDs.

Отчёт называет принятый этап M0/M1/M2/M3. Для M1 допустимо утверждение о Simple Query-профиле psycopg2 и прямого libpq; для M2 нужны перечисленные Extended API; для M3 — конкретный прикладной профиль и удалённый TCP-сценарий. Одного SELECT, collection pytest или `MON$SERVER_PID` недостаточно.

## 10. Прямой libpq и Extended Query в M2

C harness компилируется против явно выбранной libpq и записывает PQlibVersion, header/build version, ОС и server SHA. Уже в M1 он проверяет PQconnectdb/PQexec, PQftype/PQgetisnull/PQcmdTuples, PQtransactionStatus, SQLSTATE и освобождение PGresult/connection. Это отдельный будущий harness, его исходников в текущем пакете ещё нет.

| ID | Проверка | Ожидание |
|---|---|---|
| E01 | PQexecParams с совместимым SQL и `$n` | Bind значений через native buffers; реальные результаты Firebird |
| E02 | Явные parameter OID и OID=0 | Правильные inference/ParameterDescription; неоднозначность даёт ошибку |
| E03 | Повторный `$1`, перестановка индексов, неверный индекс/overflow | Корректный mapping либо отказ до исполнения |
| E04 | `$1` внутри строки, комментария, quoted identifier и альтернативной строки Firebird | Эти фрагменты не превращаются в параметры |
| E05 | PQprepare/PQexecPrepared и разные значения | Повторное исполнение без повторной подстановки SQL; statement переживает commit |
| E06 | Named и unnamed statements, замена и Close | Spec-defined lifetime и ошибки; handles освобождены |
| E07 | Describe statement и portal | ParameterDescription, RowDescription/NoData, типы и result formats согласованы |
| E08 | Bind NULL, empty, text/binary и format counts 0/1/N | Правильная выдача; mismatch/invalid format отвергаются |
| E09 | Execute maxRows, PortalSuspended, продолжение cursor | Без потерь/дубликатов, CommandComplete только при завершении |
| E10 | Несколько portals и interleaved sessions | Независимые bound buffers, cursors, formats и identities |
| E11 | Ошибка Parse/Bind/Execute до Sync | Уже принятые последующие команды не исполняются; recovery после Sync |
| E12 | Flush без Sync | Ответ приходит; ReadyForQuery и implicit commit не появляются |
| E13 | Несколько Execute до Sync, commit visibility | Одна implicit transaction до Sync; ошибка откатывает её изменения |
| E14 | Sync внутри explicit BEGIN block | Block остаётся открытым; I/T/E соответствуют результату |
| E15 | Simple Query между Extended cycles | Корректная инвалидизация unnamed objects и отсутствие оставшихся cursors |
| E16 | PQsendQueryParams/PQsendQueryPrepared и PQgetResult | Асинхронный API без pipeline работает, включая nonblocking I/O и ошибки |
| E17 | Лимиты statements/portals/parameters и большой Bind | Ограниченные ресурсы, изоляция клиентов, отсутствие частичного исполнения |
| E18 | EOF/shutdown с prepared statement, suspended portal и discard state | Освобождение ресурсов, bounded shutdown, native endpoint не регрессирует |
| E19 | Обычный Psycopg 3 Cursor, подготовка statement после порога | Реальный Extended Query; переход к named preparation работает |

E01–E08 проверяются прямым libpq, E09–E15 и граничные sequences — raw-wire harness. Версия Psycopg 3 закрепляется перед M2; тест включает server preparation, а не только `prepare_threshold=None`. ClientCursor или клиентская подстановка параметров не заменяют эту приёмку.

Libpq pipeline API не входит в E16. Для его будущей поддержки нужны отдельные tests multiple Sync/error segments и pipeline result statuses.

## 11. Общие codecs и RETURNING в M2

| ID | Проверка | Ожидание |
|---|---|---|
| D01 | Все scalar types M2, text и binary input/output | Правильные OID, значения, NULL и длины; Simple Query использует те же text codecs |
| D02 | Integer и exact numeric границы, INT128/NUMERIC(38), scale | Нет переполнения, float-промежуточного значения или потери точности |
| D03 | FLOAT/DOUBLE, rounding, NaN/Infinity | Поддержанные значения представлены правильно; остальные дают документированную ошибку |
| D04 | DATE/TIME/TIMESTAMP без TZ | Epoch/units, до/после опорной даты, leap day, precision и границы диапазона проверены |
| D05 | Golden binary fixtures из независимого источника | Codec сверяется с PG format, а не только с собственным decode/encode |
| D06 | SUM/AVG и другие выражения с расширенной metadata | Выбор codec по результату, не по типу исходной колонки |
| D07 | RETURNING для INSERT VALUES/SELECT, UPDATE, DELETE | Правильные строки, description, command tags и affected rows; 0/1/many rows |
| D08 | Ошибка conversion/fetch/commit при RETURNING | Нет ложного успеха и случайных частичных изменений вне transaction contract |
| D09 | Неподдержанные type/format и malformed binary payload | Отказ до side effects, recovery через Sync, без подмены на text/NULL |

Эталонный PostgreSQL может использоваться в отдельных codec/protocol tests для получения fixtures. Он не участвует в исполнении запросов Firebird endpoint. Синтаксически несовместимые SQL не сравниваются как будто обе СУБД обязаны их принимать.

## 12. Удалённый доступ, отмена и приложение в M3

| ID | Проверка | Ожидание |
|---|---|---|
| R01 | TCP с другого узла или изолированного network namespace | Задокументирована реальная топология; listener принадлежит `firebird` |
| R02 | SSLRequest и TLS, libpq sslmode=verify-full | Успех с корректным trust/hostname; отказ с неверным сертификатом/именем |
| R03 | Non-loopback без TLS | Password authentication не начинается по открытому каналу |
| R04 | Partial TLS handshake, EOF, timeout, медленный client | Ограниченные ресурсы и корректный shutdown |
| R05 | Native WireCrypt/DummyPacketInterval вместе с PG TLS | Оба endpoint работают независимо без смешивания wire bytes |
| C01 | BackendKeyData и CancelRequest | Отмена активного SQL/lock wait и SQLSTATE 57014 в основном connection |
| C02 | Неверный/старый key, чужая session | Чужая работа не отменяется; cancel socket закрывается без application attach |
| C03 | Cancel при completion/disconnect/detach | Нет use-after-free, отмены другой session или зависания |
| C04 | Запрос после cancellation, autocommit/explicit block | Соответствующие I/E и восстановление через control command |
| U01 | Startup/session settings выбранного клиента | Поддержанные значения реально применены; неизвестные не подтверждены фиктивно |
| U02 | Read-only/isolation и transaction control | Реальный TPB/поведение и корректное отражение состояния |
| U03 | SAVEPOINT/ROLLBACK TO/RELEASE, если нужны приложению | После ошибки возможен заявленный rollback-to и продолжение; изменения до savepoint сохранены |
| U04 | Reset/reuse/pooling lifecycle, если используется | Нет переноса transaction/settings/identity между логическими sessions |
| U05 | End-to-end приложения с обычным PostgreSQL-драйвером | Совместимый SQL выполняется Firebird; изменения кода/режима клиента и ограничения явно перечислены |
| U06 | Native/PG concurrency и cross-visibility в итоговом профиле | Одна реальная база, права и transaction isolation не обходятся |

Для R/U нужен отдельный fixture/harness: исходный Python helper принудительно задаёт loopback, sslmode=disable и фиксированный startup profile. Запуск его без изменений не доказывает ни TLS, ни удалённый доступ, ни поддержку обычных настроек приложения.

Целевое приложение пока не выбрано. До U01 фиксируются его версия, driver/libpq, connection options, wire operations и служебные SQL. Неиспользуемые дополнительные возможности обозначаются как не входящие в профиль; обязательная операция приложения не исключается молча ради успешной приёмки.

## 13. Как развивать исходный Python suite

`tests/test_pgwire_acceptance.py` сохранён без изменения поведения как исходный M1 scaffold. На текущем этапе нельзя выдавать его 56 cases за покрытие M2/M3. Перед расширением implementation разделить проверки на стабильные и зависящие от возможностей этапа:

- `test_returning_rejected_before_changes`: M1 negative; в M2 заменить D07/D08, включая проверку отсутствия незапланированных изменений при ошибке.
- `test_unsupported_output_types`: перечисленные DATE/TIME/TIMESTAMP/FLOAT/DOUBLE/INT128/NUMERIC(38) становятся положительными cases M2. Негативные проверки остаются для действительно неподдержанных типов.
- `test_python_date_adapter_remains_unsupported_sql`: остаётся syntax boundary, пока `::date` вне scope, даже после появления DATE codec.
- Batch rejection остаётся, пока multi-statement profile не реализован. Extended Query допускает один statement в Parse независимо от будущей поддержки Simple Query batches.
- W12 и W16 меняются согласно таблицам E/C; W08 проверяется с учётом включённой TLS policy.
- Positive M1 regression сохраняется на M2/M3. Не делать blanket skip/xfail исходного набора и не считать одновременно обязательными взаимоисключающие positive/negative expectations.

Отчёт фиксирует активную capability matrix и выбранные node IDs. Исходное число 56 не является постоянной целью после расширения протокола. В конце каждого этапа отдельно приводятся результаты C++/raw-wire, C libpq, Python, native и architecture/lifecycle suites.
