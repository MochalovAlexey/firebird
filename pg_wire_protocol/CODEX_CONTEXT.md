# Контекст реализации wire protocol plugins в Firebird 6

Контракт реализации M0–M3. Архитектура host/plugin определена в [ARCHITECTURE.md](ARCHITECTURE.md), границы приёмки — в [COMPATIBILITY.md](COMPATIBILITY.md), [PLAN.md](PLAN.md) и [TEST_PLAN.md](TEST_PLAN.md). Прямые требования пользователя имеют приоритет. Предыдущая P5-ревизия прошла целевую Linux/Super loopback приёмку: I/T/E, native transaction/session controls и recovery. В P6 сборка, unit tests и host/startup/Simple Query/transaction/lifecycle/native регрессии прошли. **Известное ограничение native baseline: READ COMMITTED READ CONSISTENCY + AUTO COMMIT не входит в подтверждённую совместимость; SNAPSHOT AUTO COMMIT проверен.** Целевой P6/M1 Linux/Super принят с известными native ограничениями. Shutdown во время ON CONNECT остаётся отдельным исключением: native Debug assertion воспроизводится также без wire endpoints. P7 Extended Query прошёл целевую Linux/Super приёмку: 16 групп. P8 codecs прошли целевую Linux/Super приёмку: восемь групп. P9 прошёл 17 групп с реальными C libpq, raw-wire и ограниченным профилем Psycopg 3; целевой M2 Linux/Super для совместимого SQL и реализованных codecs принят. Следующий этап — P10. Результаты и границы покрытия — в [VALIDATION.md](VALIDATION.md). TLS, Windows и другие server modes требуют отдельной реализации и приёмки.

## 1. Цель и границы

Обычный PostgreSQL-драйвер, включая libpq, подключается по TCP к процессу `firebird`, а запросы исполняет движок Firebird. Дополнительные wire protocols подключаются библиотеками plugins. Родной Firebird wire protocol остаётся встроенным, работает параллельно и доступен независимо от списка дополнительных plugins.

SQL передаётся движку неизменённым. Плагину разрешён ограниченный lexer для command tags, профиля controls и EMPTY; полная проверка SQL и исполнение остаются в engine. Точный контракт и запреты rewrite/splitting/эмуляции заданы в разделе 5.

PostgreSQL-синтаксис, который parser Firebird пока не принимает, возвращает настоящую ошибку. Расширения grammar, функций и системных объектов выполняются позднее в движке отдельным этапом. Они не заменяются сетевыми обходами. Значения Bind передаются через metadata/message buffers; SQL из литералов не собирается.

Plugin вызывает серверный host API и внутренние attachment/transaction/DSQL interfaces. Host выбирает только локальный engine provider. Отдельный gateway, вторичный attach через TCP/Unix socket, Remote/Loopback provider для application attachment, PostgreSQL server и synthetic native PACKET не входят в архитектуру. Штатная линковка Firebird с libfbclient допустима; проверяется фактический execution path.

## 2. База и результаты этапов

- База: `master` Firebird 6, SHA `bf94598f8deefeb7cc4a8217ee43b3040d5d6650`, `6.0.0.2199 Initial`, `Engine14`, C++20; рабочая ветка `pg_wire_protocol`.
- Зафиксированный driver-only probe: psycopg2 2.9.11, runtime libpq 17.6. Он выполнен на синтетическом сервере и не доказывает интеграцию с Firebird.
- Текущий host — Linux, `ServerMode=Super`; SuperClassic/Classic диагностируются с сохранением native. Их интеграция и Windows wiring/runtime требуют отдельной работы. P1 POSIX build/runtime принят по VALIDATION, production PgWire добавлен в POSIX/CMake build definitions и прошёл целевые POSIX/Linux/Super проверки P2; полная CMake/Windows acceptance этим не заявляется.
- M0: загрузка PG plugin, настоящее TCP-подключение, authentication, вычисляемый SELECT, SQL error/recovery, disconnect, работа native listener. Полный descriptor и EMPTY не являются условием первого SELECT.
- M1: Simple Query на совместимом Firebird SQL, metadata, codecs, CRUD, совместимый DDL, DML RETURNING, реальные транзакции, privileges и lifecycle.
- M2: Extended Query lifecycle, prepared statements/portals, Bind с native `?` либо без параметров, text/binary codecs. Синтаксис `$n` сюда не входит.
- M3: TLS, другой сетевой узел, cancellation и конкретный профиль приложения, совместимый с текущим parser. Это не заявление о работе любого PostgreSQL-приложения.

M0/M1 проверяются прямым C libpq `PQexec` и psycopg2 с `autocommit=True`, без `with connection`: context manager может отправить BEGIN даже при autocommit. Обычный transactional mode psycopg2 автоматически посылает BEGIN; пока он отклоняется parser. В P5 для проверки явных транзакций прямой libpq отправляет совместимый `SET TRANSACTION`, COMMIT и ROLLBACK. Включение будущей поддержки BEGIN требует отдельного изменения parser и тестов.

## 3. Host, plugin и конфигурация

Общий server host располагается в `src/remote/server/wire`, PostgreSQL plugin — в `src/plugins/wire/pg`. Используются Firebird PluginManager и версионируемые CLOOP interfaces, а не отдельный самодельный загрузчик библиотек. Правила сопоставления логических plugin names с `.so`/`.dll` заданы в ARCHITECTURE.md.

`WireProtocolPlugins` задаёт дополнительные plugins, `WireProtocolPorts` — соответствующие им порты позиционно. Пустые списки отключают дополнительные listeners. Число элементов, порты, дубликаты, доступность plugin и коллизии проверяются до запуска endpoint; детали диагностики и изоляции отказов определены архитектурным контрактом. Пример одного PG endpoint: `WireProtocolPlugins = PgWire`, `WireProtocolPorts = 15432`. До TLS endpoint работает только на loopback.

Одна библиотека/factory может обслуживать несколько configured aliases. Каждый alias создаёт отдельный endpoint instance и получает собственную секцию `Config` через штатную конфигурацию plugins. Bind address, TLS policy/certificates и protocol settings относятся к этой секции; третий позиционный список не добавляется. Host владеет transport и проверяет разрешённость настроек для текущего этапа. Используется существующий getPluginConfig со штатным named Config / module-file fallback, без нового API ConfigManager. Два aliases одной библиотеки не должны разделять mutable session/config state.

Host управляет listeners, ограниченными ресурсами соединений, загрузкой/владением plugins, authentication/local attach и shutdown. Plugin реализует PostgreSQL codec и session state. Конкретный интерфейс transport/connection не должен передавать plugin внутренние native PACKET или XDR структуры.

Server host явно добавляется в `Remote_Server`: `dirObjects` не рекурсивен. PG plugin собирается отдельной библиотекой и устанавливается штатным plugin packaging. Его код не включается в клиентский Remote_Common. Native `SRVR_multi_thread`, opcodes, XDR, `RemoteServicePort`, ODS и существующие dialects сохраняют поведение. PG protocol 3.0 имеет значение 196608 и хранится отдельно от native protocol version.

## 4. Authentication и local attachment

1. Host проверяет точное вхождение Startup database в comma-separated DatabaseAliases endpoint Config (default empty запрещает все attachments), затем разрешает локальный alias из databases.conf. Произвольный клиентский path/URI/config не становится provider route.
2. Plugin получает пароль PostgreSQL password exchange; M0–M2 допускают это только на loopback, M3 — по проверенному TLS для удалённого клиента.
3. Host проверяет credentials через обмен Firebird client/server auth plugins в памяти. P2 требует Srp256 в обоих AuthClient/AuthServer базы, локальную security DB с Engine14 в её Providers; не более 16 rounds и 64 KiB на auth payload. Policy и реальные privileges сохраняются.
4. `src/jrd/extds/ValidatePassword.cpp` служит образцом. Его EDS wrapper требует уже существующий attachment и напрямую до attach неприменим. Отдельный helper реализован в wire/Authentication.cpp с явными inputs, без рефакторинга EDS.
5. Auth block создаётся успешным server auth plugin. Startup username сам по себе не становится trusted identity. Не передавать plaintext password как данные SrpServer и не включать trust fallback.
6. Host строит новый DPB только из доверенных полей: auth block, UTF8/dialect 3, engine-only provider route и настоящий peer. Клиентские raw credentials, trusted/internal/client-config DPB не передаются. RemoteAccess и database policy сохраняются. MON$REMOTE_PROTOCOL показывает TCPv4, MON$REMOTE_VERSION — configured endpoint alias, обычно PgWire; application_name передаётся как process name, не как identity.
7. Host принудительно задаёт `isc_dpb_config = EMBEDDED_PROVIDERS` для application attachment. Удаления только Loopback недостаточно: Remote остаётся. Это ограничение строже native dispatcher route; database `Providers` не может направить wire attachment в Remote.
8. AuthenticationOk отправляется только после успешного attach. Пароль удаляется после использования, не попадает в логи, DPB dumps или secret DSN.

PgWire session использует один application attachment, принадлежащий host. IWireServerHost версии 4 возвращает его borrowed до detachDatabase или конца run; plugin использует его только на session worker и не вызывает detach/drop/release напрямую. Child interfaces закрываются до host detach; borrowed handle недействителен после detach даже при ошибке. Host закрывает оставленные attachments после run. Generic ABI не навязывает другим plugins одно attachment или одну transaction.

Host проверяет UTF8 и byte limits: database/user/application_name до 255 bytes, нормализованный Srp256 login до 63 bytes, protocol endpoint alias до 63 bytes. Host password input имеет явную длину до 4096 bytes без NUL; PgWire Password body ограничен 4096 bytes вместе с framing NUL. Пароль затирается после использования. Внутренние локальные security DB attachments разрешены. Remote security database и шифрованная база, требующая ключ от native клиента, не входят в начальный профиль. Наличие native db-crypt callback не означает, что PostgreSQL-драйвер умеет передать этот ключ.

## 5. Plugin-local descriptor и существующие engine interfaces

SQL передаётся в engine prepare целиком, с исходной byte length и без изменения текста. Ограниченный lexer внутри PgWire определяет вид команды, command tag, профиль transaction/session controls и признак tokenless EMPTY. Он учитывает comments, строки и quoted identifiers, но не проверяет grammar, имена объектов, privileges или типы. Общий wire host SQL не анализирует.

После успешного engine prepare плагин сочетает lexical descriptor с существующими `getType()`, `getFlags()`, input/output metadata и affected records. `FLAG_HAS_CURSOR` выбирает cursor/fetch; output metadata определяет singleton output и codecs. RETURNING не превращает DML в SELECT. В I/T неподдержанный control либо UNKNOWN, включая USING wrapper, получает 0A000 до execution; в E успешно подготовленные команды, кроме разрешённого recovery, получают 25P02. Ошибка самого prepare имеет отдельный приоритет по CODEX_CONTEXT. Новые публичные DSQL getInfo items, AST annotations и parser diagnostics для PgWire не добавляются.

Запрещены SQL rewriting, удаление comments из передаваемого текста, разбиение batches, замена `$n` на `?`, подстановка параметров SQL-литералами и эмуляция успешного SET/BEGIN. Совместимость синтаксиса и реальное исполнение определяет только Firebird. Неподдержанный batch передаётся parser целиком и отвергается без частичных эффектов.

EMPTY определяется ограниченным lexer только для нулевой строки, whitespace и полностью закрытых comments без SQL tokens. Даже такой текст передаётся в обычный engine prepare неизменённым. Только сочетание этого признака с engine syntax error 42601 после успешного cleanup преобразуется в EmptyQueryResponse; permission, cancellation, resource и другие ошибки не маскируются. Незаконченные строка/comment/statement и отдельный `;` остаются parser errors. Native parser diagnostics и `isql -autoterm` не меняются.

Coarse getType не различает все MERGE/UPDATE OR INSERT/RETURNING/DDL forms, поэтому исходный tag дополняется ограниченной lexical classification после успешного prepare. Наличие output columns не служит классификацией операции; getFlags и output metadata выбирают API исполнения. Плагин не расширяет AST, публичный getInfo или diagnostics движка ради этой задачи.

В P5 `ControlKind` остаётся локальным для PgWire: начало/полное завершение/retaining, savepoint action, session SET и reset. Transaction characteristics не извлекаются из текста: весь SET TRANSACTION обрабатывает движок. После исполнения учитываются фактические изменения native handle и session state. Descriptor не обещает PostgreSQL isolation/DDL semantics и не подменяет native execution.

## 6. Startup и framing

- StartupMessage идёт без tag; обычные сообщения содержат tag и Int32 length. Length включает собственные четыре bytes, но не tag.
- Исходный wire version — 3.0. Другие версии получают понятный FATAL; negotiation 3.2 отложен.
- До M3 SSLRequest/GSSENCRequest получают один byte `N`, затем продолжается Startup. С M3 SSLRequest может перейти в TLS в том же endpoint.
- Проверяются bounds до allocation, overflow, NUL terminators, UTF8 и допустимые startup keys. Query содержит ровно одну NUL-terminated SQL string; лишние bytes после terminator — protocol error.
- Непустой user обязателен; при отсутствии database используется user и всё равно проверяется alias allowlist. Явно пустой database отвергается. client_encoding отсутствует либо равен точному UTF8; application_name поддержан. Неизвестные keys/options/replication получают 0A000; иная encoding — 22023, invalid UTF8 — 22021. Duplicate keys, неверные lengths/terminators и хвост после NUL — 08P01.
- Начальные ParameterStatus: `client_encoding=UTF8`, `server_encoding=UTF8`, `standard_conforming_strings=on`, `DateStyle=ISO, MDY`. P8 публикует `integer_datetimes=on` для integer-based binary temporal codecs; TimeZone не публикуется, типы с TZ не поддержаны. IntervalStyle не заявлять без interval codec/session contract.
- `server_version` — документированный compatibility token. Исходное значение из probe: `16.0 (Firebird pgwire prototype)`; его влияние на целевого клиента проверяется отдельно. Оно не обещает PostgreSQL SQL compatibility.
- До реализации cancellation BackendKeyData не выдаётся. После startup отправляется ReadyForQuery `I`.
- Поддерживаются partial/coalesced frames, short writes, EOF, EINTR и backpressure. Native crypt/compression/op_dummy не попадают в PG stream.
- Пределы P2 фиксированы: Startup 16 KiB, typed frame 1 MiB, Password body 4096 bytes с NUL. Length включает собственные 4 bytes, typed tag не включается. Активные sessions ограничены host Workers (default 4, максимум 16 на endpoint, 64 суммарно), queued sockets — QueueSize (default 16, максимум 64).
- Transport/frame/startup/auth/output timers отсутствуют. Read/write ждут данные или возможность записи, EOF, ошибку либо shutdown; partial progress не теряется. Queued sockets ограничены числом, без истечения по времени.

## 7. Simple Query и результаты

Полный неизменённый SQL проходит обычный engine prepare с явной byte length и dialect 3. `SQL_DIALECT_V6` исторически обозначает dialect 3, а не версию сервера. Текущая grammar принимает один statement с необязательной конечной точкой с запятой; batches отвергает parser до выполнения любой их части.

Текущий `Execute.cpp` создаёт один `QueryExecutor` на authenticated session. Prepare получает current native transaction, если она есть, иначе NULL/system prepare. В I это может быть handle ещё не завершённого Extended cycle. После successful prepare проверяются plugin-local descriptor и input/output metadata. Обычный Query из I создаёт implicit transaction только перед execution; в T использует существующую. Cursor flag выбирает openCursor/fetch, иначе execute выдаёт singleton output при наличии output columns. RowDescription/DataRow передаются потоком; отсутствие output не создаёт RowDescription. Request handles закрываются до CommandComplete; implicit transaction сначала коммитится, explicit transaction сохраняется. ReadyForQuery отражает I/T/E. Ошибка implicit Query вызывает rollback, recoverable explicit error сохраняет transaction и E. Ошибка cleanup, неопределённый commit или fatal engine error завершают session. Целевая Linux/Super приёмка P5 прошла с известным native ограничением READ CONSISTENCY + AUTO COMMIT; evidence и исторические P4 результаты в VALIDATION.

Текущие text results соответствуют набору P8 в разделе 9: integer/BOOLEAN/CHAR/VARCHAR, exact numeric через INT128, FLOAT/DOUBLE и конечные DATE/TIME/TIMESTAMP без TZ, NULL. Table OID/attribute number 0, typmod=-1, alias из engine metadata. CHAR читается через varying fetch metadata с сохранением logical CHAR OID и пробелов. Поддержаны UTF8/NONE/ASCII с проверкой результата на valid UTF8; OCTETS и другие неподдержанные charsets/types получают 0A000 по metadata до исполнения. Входные parameters в Simple Query получают 0A000 по input metadata.

Statement-level timeout остаётся 0: действуют штатные engine configuration/session settings, включая native SET STATEMENT TIMEOUT. Плагин не задаёт собственный SQL timeout. В firebird.conf StatementTimeout измеряется в секундах, ConnectionIdleTimeout — в минутах; 0 означает отсутствие соответствующего engine cap. Native DML timer не обещает hard deadline для prepare, DDL и controls. Native idle timeout закрывает attachment и откатывает transaction; следующий Query получает FATAL 57P05/close. Плагин не выполняет периодических engine calls, обновляющих idle timer, и не подключает session повторно.

Принимаются совместимые команды, для которых plugin-local descriptor, штатные engine interfaces и codecs обеспечивают правильный tag, реальное исполнение и корректное состояние. Совместимый DDL не ограничивается CREATE/DROP TABLE. Неописанный command/control получает явный отказ после prepare, без исполнения. DML RETURNING использует те же codecs, что SELECT; cursor не меняет семантику DML.

После prepare `getFlags() & FLAG_HAS_CURSOR` определяет `openCursor/fetchNext`, иначе используется execute и output metadata. Prepare flags и statement flags не смешиваются. `RESULT_OK`, `RESULT_NO_DATA` и error различаются. Fetch error исключает успешный CommandComplete. Unbound input parameters в Simple Query отвергаются по metadata, без поиска `?` в тексте.

CommandComplete использует plugin-local command descriptor и фактический результат: SELECT — отправленные строки, DML — affected records, DDL/session/control — определённый tag. RETURNING, MERGE, UPDATE OR INSERT и DDL modifiers имеют отдельные tests. Coarse INSERT type сам по себе не определяет tag. Команды без PostgreSQL аналога сохраняют tags UPDATE OR INSERT n, EXECUTE BLOCK/PROCEDURE без count, RECREATE/CREATE OR ALTER/DECLARE с объектом, SET GENERATOR/SET STATISTICS без count. Control tags P5: native start → BEGIN; commit/commit retaining → COMMIT; rollback/rollback retaining/rollback-to → ROLLBACK; SAVEPOINT, RELEASE, SET, RESET. Полный COMMIT в E выдаёт ROLLBACK после настоящего rollback. UNKNOWN остаётся capability refusal по текущему состоянию.

EMPTY определяется ограниченным lexer только для нулевой строки, whitespace и полностью закрытых comments без SQL tokens. Даже такой текст передаётся в обычный engine prepare неизменённым. Только сочетание этого признака с engine syntax error 42601 после успешного cleanup request resources преобразуется в EmptyQueryResponse; permission, cancellation, resource и другие ошибки не маскируются. Незаконченные строка/comment/statement и отдельный `;` остаются parser errors. Native parser diagnostics и `isql -autoterm` не меняются. EmptyQueryResponse сохраняет прежнее I/T/E и текущую transaction. Fixtures DDL коммитятся до DML; PostgreSQL DDL semantics не обещаются.

## 8. Транзакции

P5 реализует PostgreSQL `I/T/E` через plugin-local control descriptor после engine prepare и реальный transaction handle; целевая Linux/Super приёмка прошла с native ограничением READ CONSISTENCY + AUTO COMMIT; целевой M1 принят в P6 с указанными исключениями. Explicit transaction начинается native `SET TRANSACTION` и получает tag BEGIN. SQL `BEGIN`, `START TRANSACTION` и `RETAINING` native parser не принимает. Допустимы `COMMIT/ROLLBACK [WORK] RETAIN [SNAPSHOT]`, `ROLLBACK [WORK] TO [SAVEPOINT] name` и `RELEASE SAVEPOINT name [ONLY]`. Результат `IStatement::execute` может создать, заменить или освободить transaction handle: возвращённый interface сохраняется до проверки status, в том числе при ошибке.

**Одна PgWire session управляет не более чем одной текущей application transaction на своём attachment.** В неё входит либо implicit cycle, либо explicit transaction. Parse, Describe, prepared statements и portals не создают собственных application transactions. Prepare использует текущий handle либо NULL, при котором движок может использовать внутреннюю system transaction. Внутренние system/security/autonomous transactions не входят в этот счёт. Инвариант относится к PgWire, а не к native protocol или любому будущему plugin общего host.

В E обычный prepare выполняется на сохранённом действительном handle. После successful prepare descriptor разрешает только recovery из таблицы ниже; остальные statements освобождаются без execute/openCursor и получают 25P02, в том числе unsupported commands/session SET/RESET. При failed prepare сначала определяется полная engine диагностика. Только реальный `isc_dsql_relation_err`, давший итоговый nonfatal 42P01, входит в whitelist преобразования в 25P02; одного explicit SQLSTATE 42P01 недостаточно. Syntax остаётся 42601/E, permission — 42501/E; cancellation/timeout, shutdown/fatal, invalid handles, resource/I/O и неизвестные ошибки сохраняются с соответствующим cleanup/close. Неизвестные semantic errors не подменяются 25P02. EMPTY обрабатывается отдельно до этого gate и сохраняет состояние. Prepare не исполняет подготовленный DML/DDL, но может компилировать, загружать metadata/external routines и брать locks.

| Состояние и операция по plugin-local descriptor после prepare | Результат |
|---|---|
| I + обычный исполнимый SQL | Implicit native transaction на Query; настоящий commit/rollback; I |
| I + начало explicit transaction | Выполнить engine control; сохранить возвращённый handle; T |
| T + успешный SQL без reset/transaction effect | Та же transaction; T |
| T + ошибка SQL | Сохранить native transaction, заблокировать обычное исполнение; E |
| E + подготовленный обычный SQL | 25P02; не выполнять; E; ошибки prepare имеют приоритет, описанный выше |
| T + полный COMMIT | Реальный commit; I после успеха |
| E + полный COMMIT | Откат; tag ROLLBACK; I |
| T/E + полный ROLLBACK | Откат; I |
| T + COMMIT/ROLLBACK RETAIN | Реальный retaining effect; живой handle, T |
| T + SAVEPOINT/RELEASE/ROLLBACK TO | Реальный engine effect; T |
| E + успешный ROLLBACK TO | Откат до savepoint, сохранить предыдущие изменения; T |
| E + ROLLBACK RETAIN | Реальный rollback retaining; T |
| E + COMMIT RETAIN/SAVEPOINT/RELEASE | 25P02; E, никаких изменений |
| I + полный COMMIT/ROLLBACK | Подготовленный native control: no-op, соответствующий tag; I, возможен notice |
| I + retaining control или SAVEPOINT/RELEASE/ROLLBACK TO | 25000: активной transaction нет; I |
| T + повторный SET TRANSACTION | 25001; сохранить существующий handle, E |

Native transaction сохраняется в E, пока она остаётся действительной. Неверный savepoint или другая ошибка recovery сохраняет E; обычный SQL не возобновляется автоматически. Автоматический откат всей transaction на любой SQL error несовместим с ROLLBACK TO и не используется. Отдельно проверить ошибки prepare/fetch/commit и случаи, когда движок уже уничтожил handle: такой сбой требует явного завершения session либо доказанной recovery policy, а не ложного T. Ни один путь COMMIT в E не должен зафиксировать ещё незафиксированные изменения failed transaction; успешный recovery подтверждается реальным эффектом движка.

Явный native `SET TRANSACTION AUTO COMMIT` сохраняет семантику Firebird: движок может уже зафиксировать предыдущие изменения через retaining, сохранив живой handle. Последующий rollback не отменяет уже зафиксированные изменения. Wire-слой не отключает флаг и не обещает атомарность такого блока. AUTO COMMIT и NO AUTO UNDO не сканируются и не хранятся в lexical descriptor; их применяет native engine. Visibility/error/savepoint tests сравнивают фактическое поведение с native API, включая очистку savepoints после retaining. Таблица I/T/E описывает границы завершённых циклов, а не возможность отмены ранее совершённых engine commits. До Sync в implicit Extended cycle current native handle может существовать при внутреннем состоянии I; полный COMMIT/ROLLBACK тогда действительно завершает его и удаляет portals.

**Ограничение native baseline:** AUTO COMMIT в сочетании с READ COMMITTED READ CONSISTENCY вызывает Debug assertion в `Savepoint.cpp:833` на restart path. Дефект воспроизводится embedded isql без PgWire, серверного процесса и triggers; полный repro — в VALIDATION. SNAPSHOT AUTO COMMIT имеет независимый positive native baseline и входит в целевой transaction harness вместе с отдельной проверкой NO AUTO UNDO. Проблемный READ CONSISTENCY profile не заявляется совместимым; core и обработка опций SQL для обхода дефекта не меняются.

Implicit TPB: READ WRITE, READ COMMITTED READ CONSISTENCY, WAIT без заданного плагином lock timeout: ожидание native по умолчанию не ограничено, но SQL может быть отменён native StatementTimeout. До создания application transaction prepare использует NULL; при её наличии, включая незавершённый Extended cycle, prepare видит текущий handle и uncommitted DDL. Explicit SET TRANSACTION применяет поддержанные parser характеристики реально. `isc_tpb_autocommit` и retaining не заменяют завершение implicit transaction. Simple Query CommandComplete отправляется после successful execution/cleanup и, для implicit Query, успешного commit. При неопределённом состоянии после ошибки commit/cleanup connection закрывается без ложного ReadyForQuery I.

Native session SET whitelist: ROLE / TRUSTED ROLE; DEBUG OPTION; DECFLOAT ROUND / TRAPS TO; SESSION IDLE TIMEOUT; STATEMENT TIMEOUT; TIME ZONE; BIND OF; OPTIMIZE; SEARCH_PATH TO. Подготовленные команды исполняются движком и выдают SET, сохраняя I либо T; в E они блокируются с 25P02. Их аргументы проверяет engine, и PostgreSQL-only SET не получает фиктивного успеха.

`ALTER SESSION RESET` имеет отдельный descriptor и tag RESET. Он может откатить transaction и создать новую за тем же `ITransaction` interface; равенства указателей недостаточно для определения lifetime. После successful reset в T сохраняется T для новой transaction, в I — I; в E reset получает 25P02. P7 хранит statements/portals по разделу 11; RESET инвалидирует весь этот кэш. Имена savepoints плагин не хранит. Текущие ParameterStatus не описывают изменяемые reset options. Если reset закрыл attachment/сделал handle недействительным, session завершается через FATAL. P7 runtime-приёмка отдельно проверяет invalidation prepared objects/portals, зависящих от сброшенных settings/role/temp objects.

После обнаружения disconnect/EOF закрываются cursor/statement, откатывается transaction и выполняется detach. Отдельного watcher socket во время engine call нет: обрыв обнаруживается после возврата из движка при следующем I/O, поэтому немедленная отмена по disconnect не обещается. Server shutdown отменяет engine operation явно. Cleanup имеет независимый status и сохраняет первичную ошибку.

## 9. Типы и codecs

P8 реализует приведённые ниже text/binary input/output codecs; целевая runtime-приёмка Linux/Super прошла. P4 text output и P7 basic Extended codecs ранее прошли целевую приёмку, evidence в VALIDATION. Новый набор не расширяет grammar или public engine API.

| Firebird logical type в P8 | PG OID | PG typlen |
|---|---:|---:|
| SMALLINT | 21 | 2 |
| INTEGER | 23 | 4 |
| BIGINT | 20 | 8 |
| BOOLEAN | 16 | 1 |
| CHAR | 1042 | -1 |
| VARCHAR | 1043 | -1 |
| NUMERIC/DECIMAL в SHORT/LONG/INT64/INT128; pure INT128 | 1700 | -1 |
| FLOAT | 700 | 4 |
| DOUBLE PRECISION | 701 | 8 |
| DATE | 1082 | 4 |
| TIME без TZ | 1083 | 8 |
| TIMESTAMP без TZ | 1114 | 8 |

Simple Query выдаёт text format 0; Extended Bind выбирает text 0 или binary 1. Для всех колонок table OID/attribute number 0, typmod -1. Alias берётся из metadata с сохранением quoted names. NULL передаётся длиной -1, пустая строка — 0; UTF8 lengths — bytes.

BOOLEAN кодируется `t/f`; числа — locale-independent decimal. Type/subtype/scale рассматриваются вместе: scale 0 не превращает NUMERIC в INTEGER. Exact decimal не проходит через floating point; INT64_MIN и INT128_MIN обрабатываются без signed overflow. Numeric scale допускается 0..−18 для SHORT/LONG/INT64 и 0..−38 для INT128. Pure INT128 сохраняет весь signed диапазон, включая 39-значные крайние значения; precision NUMERIC(38) остаётся ограничением native SQL типа.

Native buffers читаются по getMessageLength/getOffset/getNullOffset с учётом alignment, VARCHAR prefix, NULL indicator и lifetime. Для изменения fetch metadata используется IMetadataBuilder. Для CHAR сохраняется исходный logical type/OID 1042, а transport fetch metadata переводится в varying, чтобы движок выполнил преобразование фиксированного буфера. Проверить, что полученная длина сохраняет логическую CHAR-добивку и не выдаёт лишние пробелы от UTF8 byte capacity. Не применять безусловный `rstrip`. Преобразование проверяется через существующий metadata builder; тесты включают ASCII, multibyte, пустую строку и значимые конечные пробелы.

U+0000 в text output даёт 22021 без усечения; LF/TAB разрешены. Неподдержанный тип даёт 0A000 до execute/openCursor, если metadata уже известна. Не подменять unknown types строками и не выдавать всем колонкам OID text.

`NumericCodec` выдаёт canonical binary base-10000 groups с signed weight, sign и dscale; zero имеет ndigits=0/weight=0/positive sign, display scale сохраняется. Input допускает неканонические нулевые groups, но не скрытые ненулевые digits за dscale. Malformed binary — 22P03, malformed text — 22P02; numeric NaN/Infinity, overflow и непредставимая дробная часть — 22003 без округления. Максимальный numeric payload — 1 MiB.

`FloatCodec` передаёт FLOAT/DOUBLE как IEEE float32/float64 в network order; text conversion не зависит от locale. NaN и ±Infinity поддержаны; конечное decimal overflow/underflow вне native диапазона — 22003, malformed text — 22P02, неверная binary length — 22P03.

`TemporalCodec` поддерживает конечные DATE/TIME/TIMESTAMP без TZ, годы 0001..9999 и точные native ticks по 100 µs. ISO text: `YYYY-MM-DD`, `HH:MM:SS[.ffffff]`, timestamp с пробелом либо `T`. До шести fractional digits; лишняя относительно native точности пятая/шестая допускается только нулевой. Binary DATE — int32 дней от 2000-01-01, TIME — int64 µs от полуночи, TIMESTAMP — int64 µs от 2000-01-01; все multibyte поля в network order. Nonmultiple-100 µs, invalid calendar/range, 24:00, leap second, binary даты до 0001 года и infinity не округляются и дают 22008; malformed ISO, включая текстовые BC/TZ suffix, — 22007, неверная binary length — 22P03. `integer_datetimes=on`, TimeZone не публикуется.

Все эти codecs общие для Simple/Extended Query и DML RETURNING. Независимые binary fixtures проверяют layout, диапазоны и точность; native message не выдаётся за PG binary. BLOB/ARRAY/OCTETS/DECFLOAT/TZ имеют отдельные будущие контракты. Целевой M2 для этих codecs и совместимого SQL принят в P9.

## 10. Ошибки и внешнее завершение

ErrorResponse содержит S, V, C, M и конечный NUL. Поле P, если есть, — 1-based character position, не UTF8 byte offset. Targeted SQLSTATE mapping: syntax 42601, unknown relation 42P01, unique 23505, permission 42501, credentials 28P01; alias 3D000, unsupported feature 0A000, framing 08P01, failed transaction 25P02, encoding/NUL 22021.

Внутренний `ErrorStatus.cpp` сопоставляет parser errors с 42601, unknown relation с 42P01 и permission с 42501 по полному структурированному status vector. Валидный explicit SQLSTATE учитывается как fallback; native shutdown/cancellation/permission причины имеют приоритет. Diagnostics не включают SQL/credentials или сырой server status text. Error mapping unit tests и реальная commit failure повторно проверены на текущем execution path; результаты записаны в VALIDATION. Native 100 ms DML execution timeout в I/T проверен в P5 с error/recovery; hard deadline prepare/DDL/controls не заявляется. Инъекции close/free failures остаются отдельным незакрытым пунктом.

Обрабатывать **весь структурированный status vector**, включая вложенные коды после `isc_dsql_error`/`isc_sqlerr`, типы аргументов и приоритет конкретной причины. Использовать проверенные helpers и fallback `fb_sqlstate`; локализованный error text не классифицируется. В E не маскировать реальные fatal/cancel/invalid-handle/resource/I/O ошибки правилом 25P02; precedence определён в разделе 8. Startup SQLSTATE проверяется raw wire, поскольку psycopg2 connect exception может не иметь pgcode.

Simple Query error завершается ErrorResponse и корректным ReadyForQuery. Fatal startup/framing error закрывает соединение. P7 реализует Extended Query с discard-until-Sync после recoverable error; ParseComplete/BindComplete отправляются после успешной соответствующей операции.

P6 проверяет отмену через MON$STATEMENTS, завершение attachment через MON$ATTACHMENTS и shutdown базы; новых тестов пауз или срабатывания ConnectionIdleTimeout/StatementTimeout не добавляется. По реальной причине выбирается query cancel/statement timeout 57014, admin/database termination FATAL 57P01, idle-session timeout FATAL 57P05. Закрытая движком attachment не должна оставаться якобы рабочей wire session. Обнаружение shutdown при idle socket и срок закрытия определяются явно: перевод ошибки следующего execute сам по себе не обеспечивает немедленного уведомления.

## 11. Extended Query: P7 принят для Linux/Super

Parse, Bind, Describe statement/portal, Execute, Close, Flush и Sync прошли целевую Linux/Super приёмку: 16 групп, см. VALIDATION. SQL Parse передаётся engine без изменений. Прямой libpq использует zero parameters либо Firebird `?`; порядок slots задаёт native metadata. PostgreSQL BEGIN и `$n` остаются parser errors до отдельного расширения движка. Grammar, public statement metadata и CLOOP для P7 не меняются. Узкий native fix в release_attachment (jrd.cpp) снимает used со всех requests attachment перед CMP_release общего Statement; исходное падение воспроизведено также через native TCP без PgWire. Отдельный ON CONNECT shutdown assertion !child не исправлялся и повторно не проверялся.

- Statement хранит SQL, prepared handle и engine metadata. Bind готовит собственный native handle с тем же SQL для portal, чтобы разные cursors и bound buffers не разделяли execution state. Parse/Bind prepare используют current application transaction либо NULL/system prepare; отдельные application transactions для объектов не создаются.
- Named statements переживают commit. Close statement и замена unnamed statement не уничтожают уже связанные portals; portal удерживает нужный statement owner. Simple Query удаляет unnamed statement и portal. Завершение transaction удаляет все portals; Sync в explicit T сохраняет portals, включая suspended cursors.
- ParameterDescription использует проверенные OID. Отсутствующий/нулевой Parse hint означает inference; hints могут описывать начальную часть параметров. Все нестроковые типы требуют точного inferred OID; строковые 25/1042/1043 взаимно совместимы, принятый explicit OID сохраняется. Невозможный native inference не исправляется CAST/rewrite; несовместимый OID получает 0A000.
- InputCodec поддерживает text/binary набор P8 из раздела 9, включая numeric через INT128, FLOAT/DOUBLE и temporal без TZ. Binary NULL не требует декодирования payload. UTF8/NONE проверяются как valid UTF8, ASCII дополнительно ограничивается ASCII; NUL запрещён. CHAR передаётся через varying message metadata с исходным charset.
- Bind проверяет количество значений, formats 0/1/N и коды 0/1, длины и range до execution. Неверные semantic counts/formats дают ERROR 08P01 и discard-until-Sync; повреждённый frame остаётся fatal. ResultCodec использует общие text/binary codecs P8; выбор output formats не меняет native execution.
- Describe statement выдаёт ParameterDescription и RowDescription/NoData; Describe portal учитывает выбранные result formats. Execute maxRows > 0 может вернуть PortalSuspended; следующий Execute продолжает cursor. maxRows 0 выбирает остаток.
- Flush отправляет ответы без завершения transaction и ReadyForQuery. Sync завершает implicit cycle и выдаёт один ReadyForQuery; explicit transaction остаётся открытой. CommandComplete отдельного Execute не означает commit всего cycle. SET TRANSACTION при уже открытом implicit cycle получает 25001: новый TPB не заменяет текущую native transaction.
- Любая recoverable Extended error инвалидирует все portals и включает discard-until-Sync: последующие execution messages не выполняются. Implicit cycle откатывается сразу при ошибке, до Sync; explicit transaction сохраняется в E. Sync завершает protocol recovery. Syntax/permission/fatal/cancel precedence остаётся общим с Simple Query. RESET инвалидирует все prepared statements и portals, в том числе при прежнем native transaction interface pointer.
- При discard проверяются внешняя длина и полнота frame, но body известных игнорируемых command messages не разбирается. Sync/Terminate обрабатываются нормально; неизвестный tag, повреждённая длина и EOF внутри frame остаются fatal.
- Лимиты session: 128 statements, 128 portals и 8 MiB учитываемых сохранённых SQL, metadata и buffers. Дополнительные engine allocations ограничиваются числом объектов; лимит не является измерением RSS процесса. EOF/shutdown закрывает cursors/handles и откатывает незавершённую transaction.

P7 принят по прямому C libpq и raw-wire, включая отрицательные `$1`/BEGIN случаи. Фактическая совместимость Psycopg 3 в P7 не проверена; P8 расширил codecs, P9 принял M2 по конкретному клиентскому профилю ниже. Pipeline mode и будущая поддержка repeated/reordered `$n` остаются отдельными задачами движка и протокола.

### P9: целевой M2 Linux/Super принят

Версии профиля: C libpq 14.24 headers/runtime; Psycopg 3.3.6 binary implementation с libpq 18.6, Python 3.10.12. P9 прошёл 17 групп; Psycopg 3 внутри этого набора прошёл пять live TCP групп с четырьмя ожидаемыми ограничениями. Проверены настоящие Cursor/AsyncCursor, Simple/forced Extended, text/binary P8 values, named reuse через COMMIT и Close eviction. Полный контракт режимов и source-аудит driver cache заданы в COMPATIBILITY.

C harness прошёл sync и настоящий async/nonblocking send/get без pipeline: 20 commands/20 results, 40 poll calls; PQflush обслуживается через poll READ/WRITE, PQconsumeInput предшествует проверке PQisBusy, PQgetResult вызывается только при готовности и дренируется до NULL даже после ошибки. Named/unnamed reuse, Parse/Bind/Execute errors, E/Sync и native ROLLBACK проверяются вместе с реальным откатом записи. Внешний subprocess watchdog ограничивает тест, новых wire timers или сценариев пауз нет.

Для Psycopg 3 отдельно подтверждены zero-argument SQL с autocommit=True в Simple Query и forced prepared/binary Extended Query. Native controls прошли на свежей connection с `prepare_threshold=None`: SET TRANSACTION/COMMIT/ROLLBACK, savepoint, I/T/E и failed COMMIT. Forced preparation использует отдельную connection с включённым cache. Подтверждён 42601 на стандартных `%s` → `$n`, автоматическом BEGIN и `sql.Literal(date)` → `::date`. После cached ROLLBACK драйвер послал DEALLOCATE ALL и получил 42601; настоящий rollback завершился, состояние I и следующий запрос работоспособны. Возможный DEALLOCATE ALL после DROP/ALTER установлен по исходникам, отдельно не проверен. RESET инвалидирует server objects, но не запускает cache invalidation драйвера; stale driver cache после RESET — source boundary без live проверки этого режима.

Целевой M2 Linux/Super принят по live PASS прямого libpq и обязательных raw-wire случаев с совместимым native SQL, без rewriting. Регрессии Simple Query, transactions, startup/server, P8 codecs и native baseline также прошли; результаты — в VALIDATION. Успех ограниченного Psycopg режима не расширяет parser и не является общей гарантией driver/application compatibility. Следующий этап — P10.

Native inference должен завершиться уже при engine prepare, до проверки Parse OID hints. Поэтому неоднозначным expressions в fixtures нужен явный CAST самого `?`, например `CAST(? AS INTEGER) + ID`; внешний CAST результата не всегда определяет тип операнда. Это исходный совместимый SQL клиента: плагин не внедряет CAST и не передаёт OID hints в parser вместо native metadata.

## 12. Lifecycle и приёмка

Один последовательный исполнитель обслуживает session; IStatus между потоками не разделяется. IMaster singleton не release; IStatus/IXpbBuilder dispose; остальные interfaces освобождаются по API contract. Выбрать единое ownership convention и учитывать consuming close/free/commit/rollback/detach: нельзя одновременно считать возвращённый ref поглощённым и повторно release его. Ошибка terminal operation не доказывает освобождение handle.

Host останавливает accept, пробуждает transport waits, запрещает новые операции, вызывает terminal `IAttachment::cancelOperation(fb_cancel_abort)` при остановке, освобождает sessions и joins workers в согласованных provider phases. Бесконечный join перед provider shutdown недопустим. После остановки providers engine calls не происходят. Plugin library остаётся загруженной, пока существуют её objects/callbacks/workers; точный lifecycle указан в ARCHITECTURE.md.

Ограничиваются размеры frames/buffers и число connections/workers/queued sockets; собственных startup/write/lock timers нет. Строки результата отправляются постепенно. После чтения frame PgWire повторно проверяет stop перед новой обработкой. P6 проверяет явный shutdown при native lock wait без ожидания срабатывания lock timeout.

Python suite мигрирован под текущий контракт: 58 live cases прошли. P6 server harness дополнительно проверяет resource accounting после прогрева, административную отмену/завершение, database shutdown и отказ PgWire в SuperClassic с сохранением native. Реальная приёмка включает прямой libpq, корректный psycopg2 profile, native visibility/privileges, wire errors, отключённые plugins, malformed client isolation, lifecycle и отсутствие secondary DB connection. MON$SERVER_PID и синтетический probe недостаточны.

## 13. M3 и будущая SQL совместимость

Приложение и версии драйвера выбираются для M3; серверная приёмка M1 не зависит от этого выбора. Трасса фиксирует startup options, query paths, типы/форматы, автоматически генерируемый SQL, transaction controls, catalogs и cancellation. Если профиль требует BEGIN, `$n` или другую PostgreSQL-конструкцию, это зависимость отдельного будущего engine этапа; её нельзя скрыть специальным режимом клиента.

M3 проверяет явно настроенный удалённый endpoint и TLS upgrade через SSLRequest в том же plugin. Выбор TLS-библиотеки и build/packaging dependency принимается в P0. Native WireCrypt не шифрует PG stream; Srp256 не означает PostgreSQL SCRAM support.

BackendKeyData/CancelRequest привязаны к session, используют непредсказуемые ключи, инвалидируются при disconnect и синхронизируются с completion/detach. Cancel connection не создаёт application attachment. Session settings применяются только при реальном эффекте; фиктивный успешный SET запрещён.

Отчёт VALIDATION.md называет этап, версии, команды, результаты и ограничения. Будущие grammar/functions/catalog расширения описываются отдельно и получают engine regression tests; wire plugin продолжает передавать SQL неизменённым.
