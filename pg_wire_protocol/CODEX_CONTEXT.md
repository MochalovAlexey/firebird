# Контекст реализации wire protocol plugins в Firebird 6

Контракт реализации M0–M3. Архитектура host/plugin, конфигурация и lifecycle определены в [ARCHITECTURE.md](ARCHITECTURE.md); границы приёмки — в [COMPATIBILITY.md](COMPATIBILITY.md), [PLAN.md](PLAN.md) и [TEST_PLAN.md](TEST_PLAN.md). Прямые требования пользователя имеют приоритет. Документ задаёт контракт этапов. Linux/Super P1 принят в пределах VALIDATION; целевые Linux/Super проверки реализованного P2 Startup/authentication прошли, см. [VALIDATION.md](VALIDATION.md). SQL execution отсутствует: каждый корректный Query пока получает 0A000 + ReadyForQuery I; engine parser подключается в P3. Планируемые M0–M3 не являются заявлением о текущей поддержке.

## 1. Цель и границы

Обычный PostgreSQL-драйвер, включая libpq, подключается по TCP к процессу `firebird`, а запросы исполняет движок Firebird. Дополнительные wire protocols подключаются библиотеками plugins. Родной Firebird wire protocol остаётся встроенным, работает параллельно и доступен независимо от списка дополнительных plugins.

Plugin разбирает только сетевой протокол: framing, startup, authentication exchange, Query/Parse/Bind и другие сообщения, сериализацию типов и ответов. **Весь SQL передаётся существующему parser/DSQL Firebird без изменения.** В plugin и общем wire host запрещены SQL lexer, recognizer, поиск первого значимого слова, keyword allowlist, splitting batches и переписывание SQL. Это относится также к BEGIN, COMMIT, ROLLBACK, пустым запросам, command tags и `$n`. Операция определяется по результату parser/DSQL, а не по исходному тексту.

PostgreSQL-синтаксис, который parser Firebird пока не принимает, возвращает настоящую ошибку. Расширения grammar, функций и системных объектов выполняются позднее в движке отдельным этапом. Они не заменяются сетевыми обходами. Значения Bind передаются через metadata/message buffers; SQL из литералов не собирается.

Plugin вызывает серверный host API и внутренние attachment/transaction/DSQL interfaces. Host выбирает только локальный engine provider. Отдельный gateway, вторичный attach через TCP/Unix socket, Remote/Loopback provider для application attachment, PostgreSQL server и synthetic native PACKET не входят в архитектуру. Штатная линковка Firebird с libfbclient допустима; проверяется фактический execution path.

## 2. База и результаты этапов

- База: `master` Firebird 6, SHA `bf94598f8deefeb7cc4a8217ee43b3040d5d6650`, `6.0.0.2199 Initial`, `Engine14`, C++20; рабочая ветка `pg_wire_protocol`.
- Зафиксированный driver-only probe: psycopg2 2.9.11, runtime libpq 17.6. Он выполнен на синтетическом сервере и не доказывает интеграцию с Firebird.
- Текущий host — Linux, `ServerMode=Super`; SuperClassic/Classic диагностируются с сохранением native. Их интеграция и Windows wiring/runtime требуют отдельной работы. P1 POSIX build/runtime принят по VALIDATION, production PgWire добавлен в POSIX/CMake build definitions и прошёл целевые POSIX/Linux/Super проверки P2; полная CMake/Windows acceptance этим не заявляется.
- M0: загрузка PG plugin, настоящее TCP-подключение, authentication, вычисляемый SELECT, минимальный engine metadata item для его отличия от RETURNING/SELECT_BLOCK, SQL error/recovery, disconnect, работа native listener. Полный descriptor и EMPTY не являются условием первого SELECT.
- M1: Simple Query на совместимом Firebird SQL, metadata, codecs, CRUD, совместимый DDL, DML RETURNING, реальные транзакции, privileges и lifecycle.
- M2: Extended Query lifecycle, prepared statements/portals, Bind с native `?` либо без параметров, text/binary codecs. Синтаксис `$n` сюда не входит.
- M3: TLS, другой сетевой узел, cancellation и конкретный профиль приложения, совместимый с текущим parser. Это не заявление о работе любого PostgreSQL-приложения.

M0/M1 проверяются прямым C libpq `PQexec` и psycopg2 с `autocommit=True`, без `with connection`: context manager может отправить BEGIN даже при autocommit. Обычный transactional mode psycopg2 автоматически посылает BEGIN; пока он отклоняется parser. Для проверки явных транзакций прямой libpq отправляет совместимый `SET TRANSACTION`, COMMIT и ROLLBACK. Включение будущей поддержки BEGIN требует отдельного изменения parser и тестов.

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

## 5. Engine metadata: минимум M0 и расширение M1

Существующих `IStatement::getType/getFlags` недостаточно. Публичный SELECT включает DML RETURNING; MERGE и UPDATE OR INSERT могут выглядеть как INSERT; RETAIN неотличим от полного COMMIT/ROLLBACK; session commands выглядят как DDL. Число колонок не восстанавливает исходную операцию.

Для M0 достаточно одного дополнительного нейтрального info item через `IStatement::getInfo`, различающего SELECT, DML RETURNING и SELECT_BLOCK по данным движка. Coarse `getType == SELECT` вместе с `FLAG_HAS_CURSOR` этого не обеспечивает: все три формы могут иметь одинаковые публичные признаки. M0 выполняет заявленный SELECT-профиль, остальные пока не реализованные категории получают 0A000 до исполнения по engine metadata. Это временная граница этапа, не постоянный SQL allowlist M1. Происхождение SELECT не означает отсутствие эффектов у вызываемой им функции.

Полный descriptor добавляется в P4/P5 до CRUD/transaction приёмки M1. Он содержит нейтральный результат от **существующего engine parser/DSQL**:

- исходная операция и разновидность DDL/session command, сохранённые до DSQL transformations;
- transaction action, retaining, разновидность savepoint command и native transaction characteristics, включая AUTO COMMIT / NO AUTO UNDO;
- способ получения результата и metadata input/output.

Использовать дополнительные info items через `IStatement::getInfo`; старые statement types и значения не переопределяются. PostgreSQL tags формирует plugin по metadata успешно подготовленного statement. Для E используется обычный prepare с существующей native transaction и приоритет ошибок из раздела 8. Отдельный API анализа до prepare не является обязательной частью плана. Prepare не выполняет подготовленный DML/DDL, но может читать metadata, брать locks, менять caches, вызывать trace и external routine initialization; ошибочно обещать полное отсутствие каких-либо эффектов подготовки.

В P4 EMPTY различается небольшим расширением diagnostics существующего engine lexer/parser: отдельный marker выдаётся только при чистом EOF без SQL tokens, включая настоящий пустой текст и whitespace/comment-only. Незаконченный SELECT, строка или block comment остаются syntax error; отсутствие tokens перед ошибкой комментария не является EMPTY. Учесть нулевую длину, которая сейчас отсекается до parser. Сохранить legacy `isc_command_end_err2` и поведение `isql -autoterm`: marker можно добавить в status vector, сохранив прежний wrapper, либо включить opt-in prepare flag; выбранный вариант проверяется native regression. Один общий `isc_command_end_err2` никогда не переводится в EmptyQueryResponse. Новый публичный analysis API, второй scanner и новые PostgreSQL SQL конструкции для этого не нужны.

## 6. Startup и framing

- StartupMessage идёт без tag; обычные сообщения содержат tag и Int32 length. Length включает собственные четыре bytes, но не tag.
- Исходный wire version — 3.0. Другие версии получают понятный FATAL; negotiation 3.2 отложен.
- До M3 SSLRequest/GSSENCRequest получают один byte `N`, затем продолжается Startup. С M3 SSLRequest может перейти в TLS в том же endpoint.
- Проверяются bounds до allocation, overflow, NUL terminators, UTF8 и допустимые startup keys. Query содержит ровно одну NUL-terminated SQL string; лишние bytes после terminator — protocol error.
- Непустой user обязателен; при отсутствии database используется user и всё равно проверяется alias allowlist. Явно пустой database отвергается. client_encoding отсутствует либо равен точному UTF8; application_name поддержан. Неизвестные keys/options/replication получают 0A000; иная encoding — 22023, invalid UTF8 — 22021. Duplicate keys, неверные lengths/terminators и хвост после NUL — 08P01.
- Начальные ParameterStatus: `client_encoding=UTF8`, `server_encoding=UTF8`, `standard_conforming_strings=on`, `DateStyle=ISO, MDY`. Для integer-based binary temporal codecs закрепить `integer_datetimes=on`; TimeZone публиковать только с поддержанным поведением. IntervalStyle не заявлять без interval codec/session contract.
- `server_version` — документированный compatibility token. Исходное значение из probe: `16.0 (Firebird pgwire prototype)`; его влияние на целевого клиента проверяется отдельно. Оно не обещает PostgreSQL SQL compatibility.
- До реализации cancellation BackendKeyData не выдаётся. После startup отправляется ReadyForQuery `I`.
- Поддерживаются partial/coalesced frames, short writes, EOF, EINTR и backpressure. Native crypt/compression/op_dummy не попадают в PG stream.
- Пределы P2 фиксированы: Startup 16 KiB, typed frame 1 MiB, Password body 4096 bytes с NUL. Length включает собственные 4 bytes, typed tag не включается. Активные sessions ограничены host Workers (default 4, максимум 16 на endpoint, 64 суммарно), queued sockets — QueueSize (default 16, максимум 64).
- IOTimeout задаёт whole-frame deadline и один общий startup network deadline для SSL/GSS, Password и всех ответов. Short reads/writes не сбрасывают budget. Синхронный auth/attach callback, особенно до появления handle, не имеет hard preemption этим deadline; stop checks и provider shutdown обеспечивают отдельную координацию. Истечение сетевого budget не является доказательством своевременного завершения engine callback.

## 7. Simple Query и результаты

Полный неизменённый SQL проходит обычный engine prepare с явной byte length и dialect 3. `SQL_DIALECT_V6` исторически обозначает dialect 3, а не версию сервера. Текущая grammar принимает один statement с необязательной конечной точкой с запятой; batches отвергает parser до выполнения любой их части.

M1 не вводит список разрешённых SQL keywords. Принимаются совместимые команды, для которых engine interfaces и codecs обеспечивают реальное выполнение и корректное состояние; parser/backend ошибки и ограничения API возвращаются явно. Совместимый DDL не ограничивается CREATE/DROP TABLE. DML RETURNING выполняется с теми же codecs, что SELECT; presence cursor не меняет семантику DML.

После prepare `getFlags() & FLAG_HAS_CURSOR` определяет `openCursor/fetchNext`, иначе используется execute и output metadata. Prepare flags и statement flags не смешиваются. `RESULT_OK`, `RESULT_NO_DATA` и error различаются. Fetch error исключает успешный CommandComplete. Unbound input parameters в Simple Query отвергаются по metadata, без поиска `?` в тексте.

CommandComplete строится по исходной операции из engine metadata и фактическому результату: SELECT — отправленные строки, DML — affected records, DDL/session/control — соответствующая операция. RETURNING, MERGE, UPDATE OR INSERT и DDL подвиды имеют собственные тесты. Не выводить INSERT tag из coarse statement type. Команды без прямого PostgreSQL аналога получают документированный tag, а не ложную семантику другой команды.

С P4 отдельный engine EMPTY diagnostic даёт EmptyQueryResponse и ReadyForQuery с прежним I/T/E. Он распознаётся до общего преобразования syntax errors, в том числе в E; один старый incomplete-command code не является таким diagnostic. Whitespace/comments определяет engine parser; в M0 эта поддержка ещё не заявляется. Fixtures DDL коммитятся до DML; PostgreSQL DDL semantics не обещаются.

## 8. Транзакции

Plugin ведёт PostgreSQL `I/T/E`, используя engine-derived category и реальный transaction handle. Explicit transaction начинается совместимым `SET TRANSACTION`; BEGIN до расширения grammar возвращает parser error. Результат `IStatement::execute` может создать, заменить или освободить transaction handle и должен учитываться.

**Одна PgWire session управляет не более чем одной текущей application transaction на своём attachment.** В неё входит либо implicit cycle, либо explicit transaction. Parse, Describe, prepared statements и portals не создают собственных application transactions. Prepare использует текущий handle либо NULL, при котором движок может использовать внутреннюю system transaction. Внутренние system/security/autonomous transactions не входят в этот счёт. Инвариант относится к PgWire, а не к native protocol или любому будущему plugin общего host.

В E выполнять обычный prepare на сохранённом действительном handle. После успешной подготовки metadata разрешает только recovery из таблицы ниже; остальные statements освобождаются без execute/openCursor и получают 25P02. Lexer/parser errors возвращаются как 42601 с сохранением E. Известные обычные semantic prepare errors, например отсутствующий объект, можно переводить в 25P02 только по явно заданной таблице кодов. Shutdown/fatal, cancellation/timeout, недействительный transaction/attachment, resource exhaustion и I/O errors сохраняют реальную диагностику и соответствующий cleanup/close; неизвестную ошибку также не скрывать за 25P02. Поэтому правило 25P02 ниже не означает замену любой ошибки prepare. Отдельно проверить отсутствие пользовательского DML/DDL execution при отвергнутом statement и освобождение его compile resources.

| Состояние и операция по engine metadata | Результат |
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
| I + полный COMMIT/ROLLBACK | Parser-derived no-op, соответствующий tag; I, возможен notice |
| I + retaining control | 25000: активной transaction нет; I |
| T + повторный SET TRANSACTION | 25001; сохранить существующий handle, E |

Native transaction сохраняется в E, пока она остаётся действительной. Неверный savepoint или другая ошибка recovery сохраняет E; обычный SQL не возобновляется автоматически. Автоматический откат всей transaction на любой SQL error несовместим с ROLLBACK TO и не используется. Отдельно проверить ошибки prepare/fetch/commit и случаи, когда движок уже уничтожил handle: такой сбой требует явного завершения session либо доказанной recovery policy, а не ложного T. Ни один путь COMMIT в E не должен зафиксировать ещё незафиксированные изменения failed transaction; успешный recovery подтверждается реальным эффектом движка.

Явный native `SET TRANSACTION AUTO COMMIT` сохраняет семантику Firebird: движок может уже зафиксировать предыдущие изменения через retaining, сохранив живой handle. Последующий rollback не отменяет уже зафиксированные изменения. Это документированное отличие native SQL от PostgreSQL transaction block; wire-слой не отключает флаг и не обещает атомарность такого блока. AUTO COMMIT и NO AUTO UNDO отражаются в engine-derived metadata и проверяются отдельными visibility/error/savepoint tests против native API. Для NO AUTO UNDO не предполагать гарантии cleanup/savepoints без проверки соответствующего engine path. Таблица I/T/E описывает состояние текущего handle, а не возможность отмены ранее совершённых engine commits.

Implicit TPB: READ WRITE, READ COMMITTED READ CONSISTENCY, WAIT с конечным lock timeout. Explicit `SET TRANSACTION` применяет поддержанные parser характеристики реально. Не использовать `isc_tpb_autocommit` или retaining как замену завершению implicit transaction. Simple Query CommandComplete отправляется после успешного implicit commit. При неопределённом состоянии после ошибки commit/cleanup connection закрывается без ложного ReadyForQuery I.

`ALTER SESSION RESET` — отдельный session effect от engine metadata. Он может откатить transaction и создать новую, переиспользовав тот же `ITransaction` interface; равенства указателей недостаточно для определения её lifetime. После успешного reset в T сохранить T для новой transaction, инвалидировать portals/savepoint bookkeeping и обновить session metadata/ParameterStatus согласно реальным сброшенным настройкам. В I reset без создания explicit transaction остаётся I; в E он блокируется как обычный SQL до разрешённого recovery. Если ошибка reset закрыла attachment, завершить connection через FATAL. Отдельно проверить prepared statements, зависящие от сброшенных role/settings/temp objects: старые metadata нельзя использовать без подтверждения их действительности.

Disconnect/EOF закрывает cursor/statement, откатывает transaction и detach. Cleanup имеет независимый status и сохраняет первичную ошибку.

## 9. Типы и codecs

| Firebird logical type в M1 | PG OID | PG typlen |
|---|---:|---:|
| SMALLINT | 21 | 2 |
| INTEGER | 23 | 4 |
| BIGINT | 20 | 8 |
| BOOLEAN | 16 | 1 |
| CHAR | 1042 | -1 |
| VARCHAR | 1043 | -1 |
| NUMERIC/DECIMAL в SHORT/LONG/INT64 | 1700 | -1 |

M1 выдаёт text format 0, table OID/attribute number 0, typmod -1. Alias берётся из metadata с сохранением quoted names. NULL передаётся длиной -1, пустая строка — 0; UTF8 lengths — bytes.

BOOLEAN кодируется `t/f`; числа — locale-independent decimal. Type/subtype/scale рассматриваются вместе: scale 0 не превращает NUMERIC в INTEGER. Exact decimal не проходит через floating point; INT64_MIN обрабатывается без signed overflow.

Native buffers читаются по getMessageLength/getOffset/getNullOffset с учётом alignment, VARCHAR prefix, NULL indicator и lifetime. Для изменения fetch metadata используется IMetadataBuilder. Для CHAR сохраняется исходный logical type/OID 1042, а transport fetch metadata переводится в varying, чтобы движок выполнил преобразование фиксированного буфера. Проверить, что полученная длина сохраняет логическую CHAR-добивку и не выдаёт лишние пробелы от UTF8 byte capacity. Не применять безусловный `rstrip`. Если metadata недостаточно, до приёмки дополнить engine-derived character length; тесты включают ASCII, multibyte, пустую строку и значимые конечные пробелы.

U+0000 в text output даёт 22021 без усечения; LF/TAB разрешены. Неподдержанный тип даёт 0A000 до execute/openCursor, если metadata уже известна. Не подменять unknown types строками и не выдавать всем колонкам OID text.

M2 добавляет FLOAT/DOUBLE, DATE/TIME/TIMESTAMP без TZ и INT128/NUMERIC до 38 цифр; общие codecs используются и Simple, и Extended Query. Для заявленных scalar types обязательны text/binary input/output. PG binary layout не совпадает с native message layout: отдельно проверяются byte order, numeric representation, epoch/units, диапазоны, NaN/Infinity и точность. BLOB/ARRAY/OCTETS/DECFLOAT/TZ имеют отдельные будущие контракты.

## 10. Ошибки и внешнее завершение

ErrorResponse содержит S, V, C, M и конечный NUL. Поле P, если есть, — 1-based character position, не UTF8 byte offset. Targeted SQLSTATE mapping: syntax 42601, unknown relation 42P01, unique 23505, permission 42501, credentials 28P01; alias 3D000, unsupported feature 0A000, framing 08P01, failed transaction 25P02, encoding/NUL 22021.

Обрабатывать **весь структурированный status vector**, включая вложенные коды после `isc_dsql_error`/`isc_sqlerr`, типы аргументов и приоритет конкретной причины. Использовать проверенные helpers и fallback `fb_sqlstate`; локализованный error text не классифицируется. В E не маскировать реальные fatal/cancel/invalid-handle/resource/I/O ошибки правилом 25P02; precedence определён в разделе 8. Startup SQLSTATE проверяется raw wire, поскольку psycopg2 connect exception может не иметь pgcode.

Simple Query error завершается ErrorResponse и корректным ReadyForQuery. Fatal startup/framing error закрывает соединение. До M2 неподдержанный Extended Query получает явный ограниченный отказ; нельзя имитировать ParseComplete/BindComplete.

Проверить отмену statement, удаление через MON$STATEMENTS, принудительное завершение attachment через MON$ATTACHMENTS, shutdown базы, ConnectionIdleTimeout и StatementTimeout. По реальной причине выбирается query cancel/statement timeout 57014, admin/database termination FATAL 57P01, idle-session timeout FATAL 57P05. Закрытая движком attachment не должна оставаться якобы рабочей wire session. Обнаружение shutdown при idle socket и срок закрытия определяются явно: перевод ошибки следующего execute сам по себе не обеспечивает немедленного уведомления.

## 11. Extended Query в M2

Поддерживаются Parse, Bind, Describe statement/portal, Execute, Close, Flush и Sync. SQL Parse передаётся engine без изменений. Тесты прямого libpq используют zero parameters либо Firebird `?`; native input metadata определяет порядок slots. `$n` остаётся parser error до отдельного расширения grammar.

- Statements хранят prepared handle и engine metadata; portals — bound values, result formats, cursor и execution state. Их lifetimes и лимиты различаются.
- Parse/prepare использует текущую единственную application transaction либо NULL для engine prepare; отдельная transaction на statement/portal запрещена. При необходимости начать implicit cycle его handle становится текущим общим handle session. Sync завершает его согласно protocol state; named statements переживают commit и выполняются в новой текущей transaction без сохранения старой скрытой transaction. Describe и создание дополнительных portals не меняют этот инвариант.
- ParameterDescription формируется из native metadata и проверенных OID преобразований. Parse OID hints не обеспечивают автоматически engine inference; неизвестный или противоречивый тип даёт явный отказ. В частности, невозможный для Firebird inference не исправляется SQL rewrite.
- Bind проверяет число значений и format-count rules, NULL, длины, text/binary formats и range до execution. Значения записываются в native buffers.
- Describe statement выдаёт ParameterDescription и RowDescription/NoData; Describe portal учитывает result formats.
- Execute с maxRows больше нуля может вернуть PortalSuspended; следующий Execute продолжает cursor. maxRows 0 выбирает остаток. Execute не создаёт произвольный ReadyForQuery.
- Flush отправляет ответы без завершения transaction. Sync завершает implicit cycle и выдаёт один ReadyForQuery; native explicit transaction остаётся открытой. CommandComplete отдельного Execute не означает commit всего cycle.
- После ошибки обрабатывается discard-until-Sync; поступившие execution messages не выполняются. Sync возвращает корректное I/E. Повреждённый frame остаётся fatal.
- Unnamed replacement, Close, transaction end и session close освобождают соответствующие ресурсы; portals не оставляют открытые cursors.

Pipeline mode — отдельный профиль. Будущая поддержка `$n`, повторных индексов и параметрического inference реализуется в engine parser/metadata; plugin получает готовые binding descriptors и не выделяет SQL tokens.

## 12. Lifecycle и приёмка

Один последовательный исполнитель обслуживает session; IStatus между потоками не разделяется. IMaster singleton не release; IStatus/IXpbBuilder dispose; остальные interfaces освобождаются по API contract. Выбрать единое ownership convention и учитывать consuming close/free/commit/rollback/detach: нельзя одновременно считать возвращённый ref поглощённым и повторно release его. Ошибка terminal operation не доказывает освобождение handle.

Host останавливает accept, пробуждает transport waits, запрещает новые операции, вызывает `IAttachment::cancelOperation` при необходимости, освобождает sessions и joins workers в согласованных provider phases. Бесконечный join перед provider shutdown недопустим. После остановки providers engine calls не происходят. Plugin library остаётся загруженной, пока существуют её objects/callbacks/workers; точный lifecycle указан в ARCHITECTURE.md.

Ограничиваются frames, connections, buffers и startup/write/lock waits. Строки результата отправляются постепенно. Lock timeout test fixtures должен быть больше предусмотренного времени disconnect cleanup, чтобы проверка освобождения блокировки не гонялась с Terminate.

Исходный Python suite — материал для аудита и миграции. Его 20 функций/56 случаев отражают старые предположения о BEGIN, whitelist и RETURNING; это не текущий обязательный pass count. В начале реализации перенести применимые cases и переписать остальные по этапам. Реальная приёмка включает прямой libpq, корректный psycopg2 profile, native visibility/privileges, wire errors, отключённые plugins, malformed client isolation, lifecycle и отсутствие secondary DB connection. MON$SERVER_PID и синтетический probe недостаточны.

## 13. M3 и будущая SQL совместимость

Приложение и версии драйвера выбираются до завершения M1. Трасса фиксирует startup options, query paths, типы/форматы, автоматически генерируемый SQL, transaction controls, catalogs и cancellation. Если профиль требует BEGIN, `$n` или другую PostgreSQL-конструкцию, это зависимость отдельного будущего engine этапа; её нельзя скрыть специальным режимом клиента.

M3 проверяет явно настроенный удалённый endpoint и TLS upgrade через SSLRequest в том же plugin. Выбор TLS-библиотеки и build/packaging dependency принимается в P0. Native WireCrypt не шифрует PG stream; Srp256 не означает PostgreSQL SCRAM support.

BackendKeyData/CancelRequest привязаны к session, используют непредсказуемые ключи, инвалидируются при disconnect и синхронизируются с completion/detach. Cancel connection не создаёт application attachment. Session settings применяются только при реальном эффекте; фиктивный успешный SET запрещён.

Отчёт VALIDATION.md называет этап, версии, команды, результаты и ограничения. Будущие grammar/functions/catalog расширения описываются отдельно и получают engine regression tests; wire plugin продолжает передавать SQL неизменённым.
