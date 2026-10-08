# Контекст реализации PostgreSQL frontend в Firebird 6

Этот файл фиксирует архитектуру и контракты M0–M3. Подробные правила разделов 3 и 6–10 относятся к M1 (прежнему v0); расширения M2/M3 заданы в разделах 13–15 и COMPATIBILITY.md. Прямые требования пользователя имеют приоритет над документами; правила репозитория проверяются перед реализацией.

## 1. Обязательная цель пользователя

Реальный PostgreSQL-драйвер, включая libpq, передаёт совместимый Firebird SQL по PostgreSQL wire protocol. Запрос обслуживает сам Firebird server и выполняет его движок. Родной протокол работает параллельно. MS SQL Server/MySQL и новый PostgreSQL SQL parser не входят в scope. Extended Query и параметризованное выполнение входят в дорожную карту, а Simple Query является первым этапом.

**PostgreSQL frontend находится в штатном процессе firebird.** Запрещены отдельный gateway/executable, вторичный клиентский attach по TCP/Unix socket, Remote/Loopback provider для нового application attachment, PostgreSQL server и трансляция PG messages в synthetic native PACKET. Доступ к движку выполняется внутренним серверным путём, как у существующего native frontend.

Штатный firebird уже связан с внутренними runtime libraries, включая libfbclient. Сама эта линковка не является gateway. Проверяется фактический путь выполнения и отсутствие второго DB connection.

## 2. Проверенная база

- Firebird upstream master: bf94598f8deefeb7cc4a8217ee43b3040d5d6650, 6.0.0.2199 Initial, 2026-10-06.
- psycopg2 2.9.11, source fd9ae8cad2bcfc3e7e9410e7b6f07cda8f4f05ec.
- Наблюдавшийся build/runtime libpq: 170006.
- Целевая база — master Firebird 6, рабочая ветка — pg_wire_protocol. Engine14 и C++20 подтверждены исходниками. Подробности и состояние сборки — BASELINE.md.
- Research статический плюс driver-only experiment. Нативная реализация, сборка и end-to-end ещё не выполнены.

## 3. Контракт M1 и промежуточный результат M0

M0 заканчивается настоящими connect → вычисляемый SELECT → SQL error/recovery → disconnect. M1 завершает следующий ограниченный контракт. Завершение M1 не завершает всю пользовательскую цель: далее следуют M2 и M3 из PLAN.md.

1. Linux, ServerMode=Super. Дополнительный TCP listener в том же процессе; loopback; предлагаемый порт 15432; feature выключена по умолчанию.
2. PostgreSQL protocol 3.0; Simple Query; text output; один полный statement в Query.
3. psycopg2 2.9.11, обычный синхронный неназванный cursor; autocommit=True и обычный transactional mode. Независимая проверка прямым C libpq через PQexec обязательна.
4. UTF8 frontend/attachment; Firebird SQL dialect 3.
5. SELECT, простые INSERT/UPDATE/DELETE без RETURNING, CREATE TABLE и DROP TABLE.
6. SMALLINT/INTEGER/BIGINT, BOOLEAN, CHAR/VARCHAR, NUMERIC/DECIMAL с native SHORT/LONG/INT64 storage, NULL.
7. Полные BEGIN/COMMIT/ROLLBACK через узкий lexical recognizer; I/T/E state machine.
8. Настоящие Firebird credentials с Srp256 в проверенном plugin profile; проверенный auth block, реальная identity и privileges.
9. Серверный mapping database names в разрешённые local aliases; engine-only route и реальный peer metadata.
10. Wire/driver/native regression/lifecycle tests, включая доказательство отсутствия вторичного подключения.

В M1 не включать: Extended Query, SQL named cursors, server parameters/portals, COPY, replication, LISTEN/NOTIFY/cancel; SQL batches; процедуры/EXECUTE BLOCK/RETURNING; SET/SHOW; SAVEPOINT/transaction characteristics/RETAINING; INT128/DECFLOAT/FLOAT/DOUBLE/temporal/TZ/BLOB/ARRAY/OCTETS output; pooling/benchmark/ORM compatibility. PostgreSQL grammar, pg_catalog/information_schema emulation и другие DBMS остаются вне общей задачи. Extended Query, дополнительные scalar codecs, RETURNING, TLS/cancel и прикладные session controls планируются после M1; остальные возможности — по матрице COMPATIBILITY.md.

Исключения M1, например RETURNING или temporal codecs, не являются ограничениями движка Firebird и не отменяют последующие этапы. Начальная платформа Linux/Super сохраняется до отдельного расширения; удалённый TCP-доступ входит в M3.

## 4. Структура нативного frontend

Предпочтительное размещение — src/remote/server/pgwire:

- pg_protocol.h/.cpp: message definitions, framing, serialization;
- pg_server.h/.cpp: service, listener, session registry и bounded worker group;
- pg_session.h/.cpp: startup/auth/query/transaction state и server interface calls;
- pg_types.h/.cpp, pg_errors.h/.cpp: types и diagnostics.

Названия предварительные. Компилировать в существующий FB_DAEMON через Remote_Server object set. Не создавать standalone target. Проверить glob в make.shared.variables: Remote_Common используется и server, и client.

Новый listener стартует после auth/server initialization, перед native blocking loop в POSIX inet_server.cpp. Для v0 собственные PG socket readiness и workers внутри firebird; один последовательный исполнитель на сессию. Переиспользовать проверенные byte I/O/thread helpers, не весь native XDR path.

Не вызывать второй SRVR_multi_thread: текущие INET_select, native queues, request PACKET и shutdown имеют общие зависимости. Не использовать alloc_port/INET_connect как готовый независимый PG server. Общий multiplexer потребует отдельного обоснованного рефакторинга, с развилкой до asyncReceive и изменениями main/worker decode, queues, sends/errors/disconnect.

PG wire не является PROTOCOL_VERSION21. Native P_OP/PACKET/XDR и USHORT port_protocol сохраняют свои значения и смысл. PG 3.0 = 196608 хранить в PG state, не в native version field.

## 5. Аутентификация и local attach

1. Из Startup database выбрать запись server allowlist и доверенную конфигурацию локальной базы; клиентский path/URI/config запрещён.
2. В M0–M2 запросить PostgreSQL cleartext password только на loopback. В M3 тот же password exchange допускается для удалённого клиента после успешного TLS handshake по разделу 15.
3. Проверить credentials через внутренний обмен Firebird client/server auth plugins в памяти, без socket.
4. Основу взять из src/jrd/extds/ValidatePassword.cpp, выделив общую core с явными inputs. Сохранить preconditions существующего EDS wrapper; напрямую вызывать его pre-attach нельзя.
5. В v0 подтвердить Srp256 client/server pair и ограниченный auth loop, соблюдая per-database AuthServer/security DB policy; не форсировать plugin вопреки ей; неизвестные статусы/конфигурации завершаются отказом, без trust fallback.
6. Успешный server auth writer создаёт isc_dpb_auth_block. Не конструировать trusted identity из Startup username и не передавать plaintext password как данные SrpServer.
7. По образцу DatabaseAuth::accept удалить raw credentials, trusted/internal/client-config fields; добавить проверенный auth block, UTF8 и настоящие peer/address/protocol metadata.
8. Создать application attachment только через local engine provider. Проверить CURRENT_ENGINE/engine-only setting в целевой ветке. getNonLoopbackProviders недостаточен: он сохраняет Remote.
9. AuthenticationOk отправить после успешного attach. Пароль не хранить дольше необходимого; не журналировать password/DPB/secret DSN.

В engine getUserInfo raw username/trusted fields могут иметь приоритет над auth block. RemoteAccess зависит от remote address. Sanitization и stamping адреса обязательны; пропуск этих деталей меняет существующую политику доступа.

Один PG connection соответствует одному application attachment. Внутренние security DB attachments auth subsystem допустимы и не означают gateway. В профиле v0 security database локальная; remote security DB configuration отклоняется как неподдержанная для PG frontend, не меняя поведение native auth. Native клиент в тестах — независимый проверяющий клиент, не часть выполнения PG SQL.

## 6. Startup и framing в M1

- StartupMessage без tag; остальные обычные сообщения имеют tag + Int32 length. Length включает собственные 4 bytes, но не tag.
- Startup protocol 3.0; другие версии завершаются понятным FATAL. 3.2 negotiation отложен.
- SSLRequest/GSSENCRequest получают один byte N, затем продолжается startup. TLS не рекламировать.
- Проверять size bounds до allocation, overflow, NUL terminators, UTF8 и допустимые keys.
- user/database обязательны. application_name можно сохранить; client_encoding отсутствует либо UTF8. Неизвестные settings и непустые options отвергать, не подтверждать фиктивно.
- ParameterStatus: client_encoding=UTF8; server_encoding=UTF8; DateStyle=ISO, MDY; standard_conforming_strings=on; server_version=16.0 (Firebird pgwire prototype).
- server_version — задокументированный client compatibility token. Это не версия Firebird и не PostgreSQL SQL compatibility claim.
- BackendKeyData не отправлять: cancellation отсутствует. Startup завершить ReadyForQuery I.
- Собрать partial/coalesced frames; правильно обрабатывать short writes, EOF, EINTR и backpressure в принятой инфраструктуре проекта.
- Query содержит ровно одну NUL-terminated SQL string; остаток payload после terminator — protocol error, не игнорируемый хвост.
- Native op_dummy/crypt/compression не должны попасть PG-клиенту.
- Limits и deadlines задать централизованно. Пример стартовых bounds: 16 sessions, 16 KiB startup, 1 MiB frontend frame. Зафиксировать фактические значения в tests/config.

## 7. Simple Query и DSQL в M1

- Пользовательский SQL целиком передавать IAttachment::prepare с явной длиной в bytes и dialect 3. SQL_DIALECT_V6 исторически равен 3, а не версии сервера.
- Не split(';'), не rewrite %s/$1 в Simple Query, не форматировать параметры самостоятельно, не возвращать hardcoded SELECT results. Привязка $n для Extended Query описана отдельно в разделе 13.
- Проверить поддержанные statement form и output metadata до side effects. Unbound Firebird input placeholders явно отвергать.
- Prepare options и statement flags не смешивать: после prepare проверить getFlags() & FLAG_HAS_CURSOR. При cursor использовать openCursor/fetchNext, иначе execute.
- fetchNext: различать RESULT_OK, RESULT_NO_DATA и error status. Fetch error не допускает CommandComplete.
- getType недостаточно для распознавания DML RETURNING. Узкая lexical classification должна учитывать whole statement, comments/strings/quoted identifiers и отказать до execution в неподдержанных формах.
- Recognizer control-команд проверяет всю команду BEGIN/COMMIT/ROLLBACK, а не префикс; допустимы whitespace/comments и одна конечная точка с запятой.
- Optional WORK/TRANSACTION добавлять только с отдельными тестами. SET TRANSACTION, RETAINING, characteristics и незаявленные формы отвергать.
- Исследованный Firebird parser принимает один statement и требует EOF. Сверить целевую ветку и доказать SQL batch rejection тестом no side effects.
- SELECT n — число действительно отправленных строк. INSERT 0 n / UPDATE n / DELETE n — affected records API. DDL tags CREATE TABLE / DROP TABLE.
- Empty Query: EmptyQueryResponse и ReadyForQuery; comment-only поведение определить по тому же lexical scanner и протестировать, если заявлено.
- Fixture CREATE TABLE коммитить до DML. Не обещать PostgreSQL DDL semantics.

## 8. Состояния транзакции в M1

| State/command | Результат |
|---|---|
| I + ordinary SQL | Native transaction на запрос; настоящий commit/rollback; I |
| I + BEGIN | Native begin; BEGIN; T |
| T + successful SQL | Та же transaction; T |
| T + SQL error | Native rollback в v0; сохранить logical E |
| E + ordinary SQL/BEGIN | 25P02; E |
| T + COMMIT | Native commit; COMMIT; I после успеха |
| E + COMMIT | ROLLBACK tag; I; изменений не сохранить |
| T/E + ROLLBACK | Rollback при необходимости; ROLLBACK; I |
| I + COMMIT/ROLLBACK | No-op, соответствующий tag, I |
| T + BEGIN | Не создавать nested transaction; BEGIN, optional notice, T |

Этот контракт описывает работу без SAVEPOINT. Состояние E сохраняется до control command, поэтому ошибка не открывает новую transaction незаметно для клиента. Перед добавлением SAVEPOINT в M3 требуется отдельная модель восстановления: откат всей native transaction при любой ошибке несовместим с продолжением через ROLLBACK TO. Полное совпадение isolation/lock semantics нельзя выводить только из I/T/E.

TPB: READ WRITE, READ COMMITTED READ CONSISTENCY, WAIT с конечным lock timeout. Не использовать isc_tpb_autocommit/commitRetaining/rollbackRetaining для PG autocommit. CommandComplete implicit query отправлять после успешного commit. При ошибке commit/cleanup и неопределённом native состоянии завершить connection, не выдавать ложный I.

Disconnect/EOF: закрыть cursor/statement, откатить активную transaction, detach. Исходную ошибку сохранить; cleanup использует независимый status.

## 9. Metadata и codecs в M1

| Firebird logical type | PG OID | PG typlen |
|---|---:|---:|
| SMALLINT | 21 | 2 |
| INTEGER | 23 | 4 |
| BIGINT | 20 | 8 |
| BOOLEAN | 16 | 1 |
| CHAR | 1042 | -1 |
| VARCHAR | 1043 | -1 |
| NUMERIC/DECIMAL в SHORT/LONG/INT64 | 1700 | -1 |

Всегда text format=0; table OID/attribute number=0; typmod=-1. Alias брать из getAlias с правильным fallback, сохраняя quoted names.

BOOLEAN — t/f. Numbers — locale-independent decimal с точкой. Native type/subtype/scale рассматривать совместно; scale=0 не превращает numeric в integer. Exact decimal без floating point, INT64_MIN без signed overflow. NUMERIC output должен стать Python Decimal.

Native data читать по getMessageLength/getOffset/getNullOffset, с корректными VARCHAR prefix, NULL indicator, alignment и metadata lifetime. При изменении metadata использовать IMetadataBuilder, а не несуществующий IMessageBuilder.

UTF8 lengths в bytes; NULL=-1, empty string=0; CHAR padding сохранить. U+0000 в text output → 22021, без усечения. LF/TAB допустимы. Unsupported type → 0A000 до execute/openCursor, если metadata уже доступна. Не подменять unknown types строками и не выдавать всем колонкам OID text.

## 10. Ошибки в M1

ErrorResponse: S, V, C, M, завершающий NUL. Optional diagnostic fields только при корректных данных. Position P — 1-based characters, не UTF8 byte offset; v0 может её не выдавать.

Targeted mapping по ISC status codes: syntax42601, unknown relation42P01, unique23505, permission42501, credentials28P01. Alias3D000 формирует frontend. Дополнительные frontend codes: unsupported0A000, framing08P01, failed transaction25P02, encoding/NUL22021.

Fallback описать на основе fb_sqlstate; не классифицировать по локализованному error text. Simple Query error → ErrorResponse + ReadyForQuery I/E. Startup/framing fatal → close. Unsupported Extended Query → FATAL0A000 + close; не имитировать ParseComplete/BindComplete и ReadyForQuery без Sync.

Psycopg2 connect-time exception может не содержать pgcode. Точные startup SQLSTATE проверять на wire; Python negative connection test проверяет сам отказ. SQL-time ошибки тестируются через pgcode.

## 11. Ownership и shutdown

- Один последовательный исполнитель на сессию; IStatus между потоками не разделять.
- IMaster singleton не release. IStatus/IXpbBuilder dispose; остальные interface lifetimes — по контракту API.
- Выбрать ownership convention. Adopt wrapper для API-returned ref после успешного consuming close/free/commit/rollback/detach снимает владение без повторного release. RefPtr(T*) добавляет собственный ref: после consuming call этот дополнительный ref нужно освободить, как делают native Serv* wrappers.
- Не смешивать RefPtr clear и assignment-to-null без понимания convention. При ошибке terminal operation не считать handle автоматически освобождённым.
- Cleanup не бросает, исходный error status не затирает. Cancellation требует отдельного надёжного владения attachment и синхронизации с detach.
- PG service останавливается через server lifecycle: stop accept, wake socket waits, запрет новых операций, cancellation/cleanup, join в согласованных provider phases.
- Не делать безусловный бесконечный join SQL workers в preproviders: он вызывается до остановки providers. Не допускать engine calls после provider shutdown.
- Ограничить frames/connections/output buffers, startup/write/lock waits. Строки передавать постепенно; общий result set в памяти не копить.

## 12. Обратная совместимость и приёмка

Native protocol opcodes, XDR, RemoteServicePort, ODS, grammar и существующие dialects не изменять. Общие auth/attach helpers можно выделять узко с сохранением прежнего поведения и targeted regression tests. Feature по умолчанию отключена.

Исследованный Linux baseline: autogen.sh + make, C++20, make tests/run_tests; следовать фактическому pipeline checkout. Не обещать CMake/CTest без проверки.

Приёмка соответствующего этапа из TEST_PLAN.md обязательна: реальный psycopg2 и прямой libpq, type/transaction/error checks, native visibility/privileges, wire tests, тот же PID, отсутствие secondary DB connection, feature disabled, malformed client isolation, shutdown и auth/EDS regressions. MON$SERVER_PID и driver-only probe не являются доказательством native integration. Итоговый VALIDATION.md содержит точные версии, команды, результаты и ограничения.

## 13. Extended Query и параметры в M2

M2 добавляет Parse, Bind, Describe (statement/portal), Execute, Close, Flush и Sync. Оба query paths используют общие auth, attachment, типы, status mapping и cleanup. Последовательности сверять с PostgreSQL 3.0; версия сервера PostgreSQL и wire version — разные величины.

- Реестр statements хранит SQL, mapping PG parameters → native slots, metadata и prepared handle. Реестр portals хранит bound values, result formats, cursor и состояние исполнения. У объектов разные lifetimes; лимиты задаются на сессию.
- Native prepare может требовать transaction раньше Execute. Владение такой transaction и завершение на Sync определить до реализации; нельзя освобождать её после Parse, оставляя связанные объекты в неопределённом состоянии.
- `$n` распознаётся лексически вне строк, комментариев и quoted identifiers с учётом форм строк Firebird. Нумерация one-based, overflow/диапазон/количество проверяются до исполнения. Простая regex-замена запрещена.
- Повторный `$1` может соответствовать нескольким native slots. В них передаётся одно значение с проверенными преобразованиями. Явные OID, OID=0/inference и противоречивые контексты требуют тестов; ParameterDescription отражает фактический PG contract.
- Bind поддерживает NULL и PostgreSQL format-count rules для text/binary input и output. Все значения, длины, типы и форматы проверяются до исполнения. Значения передаются через native metadata/message buffers; SQL из литералов не собирается.
- Describe statement возвращает ParameterDescription и RowDescription/NoData; Describe portal учитывает result formats.
- Execute с maxRows>0 может вернуть PortalSuspended; следующий Execute продолжает тот же cursor. maxRows=0 означает все оставшиеся строки. Execute не выдаёт произвольный ReadyForQuery или повторный RowDescription.
- Flush отправляет ответы без закрытия transaction cycle. Sync завершает implicit transaction и выдаёт ровно один ReadyForQuery; explicit BEGIN block остаётся открытым. CommandComplete отдельного Execute ещё не означает commit всего implicit cycle.
- Ошибка Extended Query переводит session в discard-until-Sync. Уже поступившие Bind/Execute не выполняются; после Sync восстанавливается корректное I/E. Повреждённый frame остаётся основанием fatal close.
- Уничтожение unnamed объектов, Close и конец transaction проверяются по протоколу. Named prepared statement должен переживать commit и исполняться в новой native transaction. Portals не оставляют cursors после окончания своего lifetime.

Несколько сообщений могут приходить без ожидания ответа. Однако libpq pipeline API остаётся отдельным профилем: его поддержка не выводится из обработки одной последовательности Parse/Bind/Execute/Sync.

## 14. Общие типы и SQL results в M2

Дополнительно к M1: FLOAT/DOUBLE, DATE/TIME/TIMESTAMP без TZ, INT128/NUMERIC/DECIMAL до 38 цифр и DML RETURNING. Для перечисленного scalar subset обязательны text и binary input/output в Extended Query; Simple Query использует общие text codecs. OID, typlen, границы, округление и недопустимые значения закрепляются тестами до реализации codec.

PG binary format отличается от native Firebird message layout. Проверять byte order, numeric representation, временные epoch/units и диапазоны, NaN/Infinity и отсутствие потери точности. Непредставимые значения дают документированную ошибку. Для несовпадающих диапазонов поддержку описывать как пересечение представимых значений.

Тесты включают выражения/агрегаты с metadata шире типа исходной колонки. Decimal/INT128 не проходят через double. BLOB/ARRAY/OCTETS/DECFLOAT/TZ не получают случайный OID уже поддержанного scalar type.

RETURNING проверяется для INSERT VALUES, INSERT SELECT, UPDATE и DELETE, включая пустой результат и несколько строк. Возвращённые строки и affected rows учитываются по смыслу команды. Наличие cursor у DML не превращает его command tag в SELECT.

## 15. Сетевой и прикладной профиль M3

Listener принимает соединения на явно настроенном адресе; приёмка включает другой сетевой узел. TLS реализуется в том же frontend через SSLRequest negotiation. Для non-loopback password authentication требуется TLS; пароль проверяет тот же in-memory Firebird core. Native WireCrypt не шифрует PG socket. PostgreSQL SCRAM нельзя объявлять поддержанным за счёт наличия Srp256.

BackendKeyData и CancelRequest связываются с конкретной PG session; ключи непредсказуемы, инвалидируются при disconnect. Cancellation синхронизирована с query completion/detach. Cancel connection не открывает application attachment. Ошибка отмены в основном соединении и transaction state согласованы с протоколом. Эти primitives также используются для bounded shutdown.

По трассе приложения определить служебные команды и connection options: encoding, DateStyle/TimeZone по поддержанным типам, application_name, read-only/isolation, timeout, reset/reuse и SAVEPOINT при необходимости. Поддержанное значение должно реально менять session/TPB/codecs; фиктивный успешный SET запрещён. Catalog emulation и произвольный SQL rewrite не включаются автоматически.

Приложение и его версии пока не выбраны. M3 требует заполненного профиля COMPATIBILITY.md, TLS/cancel/session tests и end-to-end сценария. При потребности в исключённой возможности план расширяется; успешный M1/M2 не выдаётся за готовность M3.
