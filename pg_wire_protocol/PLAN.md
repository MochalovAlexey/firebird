# План реализации wire-плагинов и PgWire

База — `master` Firebird 6, ветка `pg_wire_protocol`: [BASELINE.md](BASELINE.md). Обязательные границы задают [ARCHITECTURE.md](ARCHITECTURE.md) и [CODEX_CONTEXT.md](CODEX_CONTEXT.md); возможности и проверки — [COMPATIBILITY.md](COMPATIBILITY.md), [TEST_PLAN.md](TEST_PLAN.md).

PostgreSQL grammar/functions отложены. Ограниченная plugin-local classification задаёт command tags, control profile и EMPTY; SQL не переписывается и не делится. Полную проверку и реальное исполнение выполняет неизменённый parser/DSQL; новые публичные statement metadata не добавляются.

Предыдущая P5-ревизия прошла целевую Linux/Super loopback приёмку: I/T/E, native transaction/session controls и recovery. В P6 сборка, unit tests и host/startup/Simple Query/transaction/lifecycle/native регрессии прошли. **Известное ограничение native baseline: READ COMMITTED READ CONSISTENCY + AUTO COMMIT не входит в подтверждённую совместимость; SNAPSHOT AUTO COMMIT проверен.** Целевой P6/M1 Linux/Super принят с известными native ограничениями. Shutdown во время ON CONNECT остаётся отдельным исключением: native Debug assertion воспроизводится также без wire endpoints. Следующий этап — P7 Extended Query. Результаты и границы покрытия — в [VALIDATION.md](VALIDATION.md). TLS, Windows и другие server modes требуют отдельной реализации и приёмки.

## P0. База, зависимости и проектирование

Проверить HEAD, правила репозитория и штатный Linux build workflow. Собрать сервер, подготовить отдельную UTF8/dialect 3 базу с alias и пользователями. Проверить native connect → SELECT → commit/rollback. Зафиксировать реальные AuthServer/AuthClient, локальную security database и Srp256 profile.

Записать `IMPLEMENTATION_NOTES.md`: versioned plugin ABI через CLOOP, ownership interfaces, ограниченные workers/queues, lifecycle и источники command tags. Зафиксировать границы plugin-local SQL descriptor и использование существующих statement interfaces. Новый публичный API анализа/metadata не вводится. Сразу выбрать TLS-библиотеку, optional build flag и упаковку Linux/Windows; текущий `TLS_OPTIONS` относится к thread-local storage. TLS реализуется в P10, но зависимость не оставлять неизвестной до этого этапа.

Начать профиль целевого приложения: driver/version, startup, служебные SQL, placeholders, каталоги и транзакции. Если приложение пока не выбрано, оставить это открытым условием M3; host и протокол от выбора не зависят. Не добавлять SQL compatibility в wire-слой по результатам трассы.

**Готово:** native baseline воспроизводим; решения о ABI/SQL boundary/TLS и команды сборки записаны. Недоступная зависимость обозначена конкретно, успех сборки не предполагается.

## P1. Общий host, конфигурация и plugin ABI

Добавить новый тип plugin, сохранив существующие номера, и интерфейсы в `FirebirdInterface.idl`; обновить generated bindings штатным способом. Использовать существующий PluginManager, factory registration и module ownership. Не вводить параллельный loader через прямой `dlopen`/`LoadLibrary`.

Реализовать `WireProtocolPlugins` / `WireProtocolPorts` по ARCHITECTURE: позиционные comma-separated списки, пустой default, проверка до загрузки, отсутствие сдвига портов при недостающем модуле. Native не включается в список. Общая ошибка списков отключает дополнительную конфигурацию с диагностикой; ошибка модуля или bind — его endpoint. Проверить одну тестовую библиотеку с двумя aliases/instances, включая отсутствующее имя между ними.

Endpoint settings получать через существующий `IConfigManager::getPluginConfig(name)` до bind/load, сохраняя стандартный named Config / module `.conf` fallback. NULL — ошибка endpoint. Конфигурацию подготавливать до запуска; для разных aliases рекомендуется named Config в `plugins.conf`, без дополнительных позиционных списков. Проверять значения host settings в возвращённом IConfig. Порт имеет один источник — `WireProtocolPorts`; любой Port в host settings отвергается.

Начальный host принимает только BindAddress=127.0.0.1 и отвергает любой TLS entry. Workers: default 4, диапазон 1–16; QueueSize: default 16, диапазон 1–64. Повтор host setting — ошибка endpoint. Общие пределы: 16 configured endpoints и 64 workers. Очередь ограничена размером, без истечения по времени. Сетевые read/write ожидают progress, EOF или shutdown; queue-full close и lifecycle сохраняются.

Общий host разместить в `src/remote/server/wire`, plugin — в `src/plugins/wire/pg`. `dirObjects` не рекурсивен: явно включить host objects в server target, а PG objects — в отдельную shared library и install target. PG codec не линкуется в daemon как встроенный frontend.

Host владеет listeners, transport, workers, registry и shutdown. Реализованный startup получает native endpoint через getsockname после bind: `-p` и service-name resolution уже учтены; IPv4 wildcard/loopback и IPv6 dual-stack/mapped overlap проверяются до загрузки wire модуля. Unix-only или IPv6-only native не резервирует IPv4 loopback port. Успешный bind также предшествует load. ABI не передаёт `rem_port`, platform socket types или STL. Начальное включение — Linux/Super; SuperClassic/Classic диагностируются с сохранением native. Windows wiring и приёмка других режимов остаются незакрытой частью scope.

В preproviders остановить accept и разбудить transport перед plugin stop без worker join. В postproviders все acceptors/workers разделяют один deadline 5 секунд; при его превышении сообщить ошибку и сохранить owners вместо выгрузки исполняемого модуля. Проверить stop/cleanup idle, queued и partial sessions реального server. SQL/auth cancellation относится к следующим этапам.

**Критерий готовности начального Linux/Super среза:** loading/config/transport tests, disabled/default, ошибки endpoint, bounded workers, stop/wakeup и удержание модуля до завершения callbacks. Native продолжает работать в том же процессе. Проверки выполняются на test plugin ещё до SQL, результаты подтверждаются отдельными PASS в VALIDATION. Полное покрытие Windows и других server modes этим критерием не закрывается.

## P2. PgWire framing, authentication и attachment

**Статус: целевые Linux/Super проверки P2 — PASS; команды и границы покрытия в [VALIDATION.md](VALIDATION.md).** В отдельном `src/plugins/wire/pg` реализованы protocol 3.0 StartupMessage, отказ SSLRequest/GSSENCRequest одним N, PasswordMessage, Query, Terminate и serializers. Production module добавлен в POSIX/CMake build definitions. Зафиксированный P2 отвечал всем корректным Query одинаковым 0A000 + ReadyForQuery I без engine prepare; начиная с P3 эта временная заглушка заменена execution через parser.

`IWireServerHost` расширен версионированными attachDatabase/detachDatabase. Attachment принадлежит host и выдаётся plugin как borrowed до detach/end of run; после detach он недействителен даже при ошибке. Child interfaces закрываются до detach. Общий helper `Authentication.cpp` выполняет in-memory Srp256 exchange без изменения EDS. Прямой вызов `EDS::validatePassword` до attach неприменим: он требует текущий attachment.

Host проверяет comma-separated DatabaseAliases (default empty запрещает attachments), локальные alias/security DB с Engine14 в её Providers, AuthClient/AuthServer Srp256 и RemoteAccess. Helper ограничен 16 rounds и 64 KiB на auth payload, строит новый доверенный DPB с engine-only Providers, реальным peer и endpoint alias в MON$REMOTE_VERSION; MON$REMOTE_PROTOCOL остаётся TCPv4. AuthenticationOk отправляется после успешного attach. Пароли затираются, клиент не задаёт DPB/provider route.

Codec tests и startup harness проверяют: partial/coalesced frames, literal response bytes, UTF8/NUL/length errors, пределы 16 KiB startup / 1 MiB frame / 4096 bytes password body с NUL. Transport/frame/startup/auth timers отсутствуют; shutdown должен оставлять provider возможность завершить такие вызовы.

**Критерий приёмки:** правильный/неправильный пароль, неизвестные user/alias, default database=user с allowlist, plugin policy, RemoteAccess, раздельные identities через native monitoring, ON CONNECT failure до AuthenticationOk, cleanup и отсутствие вторичного сетевого attach. Результаты фиксируются отдельно; вычисляемый SELECT остаётся условием P3/M0. Ключ шифрования от native клиента не поддерживается; серверный key holder проверяется отдельно.

## P3. Первый запрос — исторический M0

M0 проверил вычисляемый SELECT, настоящие C libpq/Psycopg2, authentication, recovery, native coexistence и lifecycle. Результаты сохранены в VALIDATION. Прежний SELECT-only фильтр и дополнительные engine metadata не являются текущим контрактом; они удалены при переносе классификации в плагин. SQL всегда передаётся engine целиком. BEGIN/$n остаются native parser errors.

## P4. Simple Query, базовые codecs и ограниченная классификация

**Статус P4: целевая Linux/Super loopback приёмка — PASS.** Full make, четыре unit executable, 12 Simple Query групп с trace и lifecycle/startup/native regressions прошли. Evidence и ограничения — в [VALIDATION.md](VALIDATION.md). P5 ниже прошёл отдельную целевую приёмку с повторением regression; результаты разных ревизий сохранены отдельно.

SQL передаётся в engine prepare целиком, с исходной byte length и без изменения текста. Ограниченный lexer внутри PgWire определяет вид команды, command tag, профиль transaction/session controls и признак tokenless EMPTY. Он учитывает comments, строки и quoted identifiers, но не проверяет grammar, имена объектов, privileges или типы. Общий wire host SQL не анализирует.

После успешного engine prepare плагин сочетает lexical descriptor с существующими `getType()`, `getFlags()`, input/output metadata и affected records. `FLAG_HAS_CURSOR` выбирает cursor/fetch; output metadata определяет singleton output и codecs. RETURNING не превращает DML в SELECT. В I/T неподдержанный control либо UNKNOWN, включая USING wrapper, получает 0A000 до execution; в E успешно подготовленные команды, кроме разрешённого recovery, получают 25P02. Ошибка самого prepare имеет отдельный приоритет по CODEX_CONTEXT. Новые публичные DSQL getInfo items, AST annotations и parser diagnostics для PgWire не добавляются.

Запрещены SQL rewriting, удаление comments из передаваемого текста, разбиение batches, замена `$n` на `?`, подстановка параметров SQL-литералами и эмуляция успешного SET/BEGIN. Совместимость синтаксиса и реальное исполнение определяет только Firebird. Неподдержанный batch передаётся parser целиком и отвергается без частичных эффектов.

EMPTY определяется ограниченным lexer только для нулевой строки, whitespace и полностью закрытых comments без SQL tokens. Даже такой текст передаётся в обычный engine prepare неизменённым. Только сочетание этого признака с engine syntax error 42601 после успешного cleanup преобразуется в EmptyQueryResponse; permission, cancellation, resource и другие ошибки не маскируются. Незаконченные строка/comment/statement и отдельный `;` остаются parser errors. Native parser diagnostics и `isql -autoterm` не меняются.

Сохранить SELECT/DML/DDL/RETURNING, EXECUTE BLOCK/PROCEDURE, affected-record counts, singleton/cursor output и реальные implicit commit/rollback. Поддержанные text codecs: integer, BOOLEAN, CHAR/VARCHAR, exact numeric в SHORT/LONG/INT64 и NULL. CHAR fetch использует varying metadata, logical CHAR OID и значимые пробелы сохраняются.

Проверки P4: ограниченный lexer unit tests; raw TCP comments/mixed-case/CTE/quoted identifiers/literals/DDL modifiers/RETURNING; неизвестные wrappers и прежний control guard; malformed SQL/batches сохраняют parser error. В P5 negative expectations для поддержанных controls заменяются проверками фактического исполнения и I/T/E. Штатные native isql AUTOTERM и diagnostics не меняются. C libpq и psycopg2 autocommit, cross-visibility, real commit failure, lifecycle/startup/native regressions прошли на P4. Зависимость от отдельного native metadata executable удалена.

**Критерий завершения:** сборка и соответствующая текущему SQL boundary integration suite прошли, реальные команды/версии/ограничения записаны в VALIDATION. Close/free fault injection и engine execution timeout отдельно отмечаются как выполненные либо not-run.

## P5. Транзакции и состояние сессии

**Исторический статус P5: целевая Linux/Super loopback приёмка — PASS с native ограничением READ CONSISTENCY + AUTO COMMIT.** Все 14 transaction groups прошли без skips; SNAPSHOT AUTO COMMIT и NO AUTO UNDO проверены. Повторены full build, четыре unit executable (487 classifier checks), Simple Query 12, lifecycle 7, startup 52 и native 9. Целевой M1 принят отдельно в P6 с указанными ограничениями. Один `QueryExecutor` на authenticated session владеет transaction и I/T/E. В I prepare получает NULL, в T/E — current native handle. Plugin-local `ControlKind` применяется после successful prepare и сверяется со штатным coarse type. Возвращённый `ITransaction*` сохраняется до проверки execute status, включая замену или NULL.

Реализованы `SET TRANSACTION` → tag BEGIN, полный COMMIT/ROLLBACK, `COMMIT/ROLLBACK [WORK] RETAIN [SNAPSHOT]`, SAVEPOINT, RELEASE SAVEPOINT и ROLLBACK TO. В I полные controls являются no-op; retaining и savepoint actions дают 25000/I. Повторный SET TRANSACTION в T даёт 25001/E с сохранением существующего handle. BEGIN, START TRANSACTION и RETAINING не входят в native grammar и остаются parser errors.

Только PgWire-сессия управляет не более чем одной текущей application transaction; последовательных транзакций за жизнь соединения может быть сколько угодно. Это не ограничение generic host, родного API или других plugins. Parse/Describe/statements/portals используют текущую transaction либо prepare с NULL, без отдельного handle; внутренние system/security/autonomous transactions не входят в инвариант.

В E выполняется обычный prepare с сохранённой native transaction. Успешно подготовленные команды, кроме recovery, освобождаются с 25P02 без execution. Failed-prepare whitelist ограничен фактическим `isc_dsql_relation_err`, когда итоговая диагностика — nonfatal 42P01: только этот случай заменяется на 25P02. Одного explicit SQLSTATE 42P01 недостаточно. Syntax, permission, shutdown, cancellation/timeout, invalid handle, resource/I/O и unknown errors сохраняют собственную диагностику и cleanup/close. Prepare может компилировать, загружать metadata/external routines и брать locks; отсутствие SQL execution не делает его отдельным чистым parser API.

После statement error в explicit block сохраняется native transaction и E. Полный COMMIT из E делает настоящий rollback с tag ROLLBACK/I; допустимый ROLLBACK TO и ROLLBACK RETAIN восстанавливают T. Implicit TPB — READ WRITE / READ COMMITTED READ CONSISTENCY / WAIT без заданного плагином lock timeout. Implicit commit выполняется до CommandComplete. Опции explicit SET TRANSACTION, включая AUTO COMMIT / NO AUTO UNDO, передаются движку неизменёнными и не извлекаются lexer; прежние AUTO COMMIT commits не отменяются последующим rollback.

Дополнительные transport/frame/startup/auth/output timers удалены. SQL и ожидание lock используют native policy: statement-level timeout остаётся 0, implicit WAIT не получает plugin lock timeout. StatementTimeout в конфигурации задаётся в секундах, ConnectionIdleTimeout — в минутах; 0 отключает соответствующий engine cap. Native idle shutdown откатывает transaction, следующий Query получает 57P05/close.

AUTO COMMIT DML проверяется положительно в native SNAPSHOT profile, NO AUTO UNDO — отдельно. Ограничение native Debug baseline: AUTO COMMIT с READ COMMITTED READ CONSISTENCY вызывает assertion `Savepoint.cpp:833` в restart path, независимо воспроизведённый embedded isql без PgWire/server/triggers. Этот профиль не входит в принятую совместимость. В P5 core/SQL options не менялись; исправление native дефекта остаётся отдельной работой. Repro и точный объём покрытия записаны в VALIDATION; P6 принимает целевой M1 с этим native ограничением.

Native session SET исполняются по whitelist из CODEX_CONTEXT; ALTER SESSION RESET имеет настоящий effect. RESET может заменить native transaction за тем же interface pointer: при успехе I остаётся I, T — T, в E команда получает 25P02. В P5 нет cached statements/portals или клиентского списка savepoint names, которые могли бы пережить reset. Session settings не эмулируются успешными ответами.

**Критерий завершения:** live checks подтверждают native transaction SQL, visibility, I/T/E и `PQtransactionStatus`, error precedence, unique violation/recovery, savepoints/retaining, disconnect rollback, session SET и ALTER SESSION RESET; результаты записаны в VALIDATION. Unit tests не заменяют эту приёмку. Автоматический BEGIN psycopg2 остаётся отрицательной проверкой синтаксиса до отдельного расширения grammar.

## P6. Серверная приёмка — M1

**Целевой P6/M1 Linux/Super принят с известными native ограничениями.** Прошли семь групп server harness, 58 pytest cases, сборка и перечисленные в VALIDATION регрессии. Shutdown во время ON CONNECT исключён из этой приёмки: [attach/shutdown harness](tools/pgwire_attach_shutdown_smoke.py) сохраняет FAIL/SIGABRT, assertion alloc.cpp:2075 (!child), через PgWire и native-only с пустыми wire lists. Core не изменялся. Следующий этап — P7 Extended Query. Новых сценариев пауз или срабатывания таймаутов P6 не добавляет.

[pgwire_server_smoke.py](tools/pgwire_server_smoke.py) объединяет мигрированный pytest-набор (58 live cases — PASS), 40 connect/query/error/disconnect sessions (16 для прогрева, 24 для учёта attachments/statements/transactions/FDs; общее число threads записывается как диагностика), отмену через MON$STATEMENTS в I/E, удаление attachment в I/T, gfix shutdown базы и остановку сервера с lock wait, password exchange и partial startup. SuperClassic проверяется только на диагностируемый отказ PgWire с сохранением native.

При terminal host stop использовать `IAttachment::cancelOperation(fb_cancel_abort)`, чтобы запрет оставался действующим между engine calls; PgWire повторно проверяет stop после чтения frame. Module references сохраняются до завершения callbacks, бесконечного join в preproviders нет. Обрыв клиентского socket во время engine call не обнаруживается немедленно: cleanup начинается после возврата из движка и обнаружения I/O failure. Явный server shutdown отменяет engine operation независимо от чтения socket.

**Критерий целевого M1 выполнен:** перечисленные P6-01–07 и регрессии прошли; shutdown во время ON CONNECT остаётся явно исключённым native дефектом с отдельным FAIL. Команды и ограничения записаны в VALIDATION. Windows, работа PgWire в SuperClassic/Classic, auth-plugin и close/free fault injection этим результатом не принимаются. Native READ CONSISTENCY + AUTO COMMIT остаётся известным ограничением. Выбранное приложение и его SQL-зависимости относятся к M3.

## P7. Extended Query и prepared objects

Реализовать Parse/Bind/Describe/Execute/Close/Flush/Sync, named/unnamed statements/portals, handle/buffer/cursor ownership и лимиты. SQL из Parse передавать неизменённым. Bind связывает значения с input slots, описанными движком, без поиска `$n` в тексте.

Поддержать ParameterDescription, RowDescription/NoData, PortalSuspended/maxRows, Close и повторное выполнение prepared statement в новой transaction. Ошибка приводит к discard-until-Sync; Flush не завершает transaction и не отправляет ReadyForQuery. Согласовать Simple/Extended transaction boundaries.

Проверить несколько prepared statements/portals и ALTER SESSION RESET: скрытая transaction под Parse не появляется, смена native transaction учитывается даже при прежнем interface pointer. Нужный engine system transaction при prepare с NULL не считается дополнительной application transaction.

**Готово:** прямой libpq `PQexecParams`, `PQprepare`/`PQexecPrepared` с нулём параметров и native `?`, например `SELECT CAST(? AS INTEGER) FROM RDB$DATABASE`. Raw-wire проверяет порядок сообщений, formats, lifetime и recovery. `$n` не переписывается и пока отвергается parser.

## P8. Расширенные codecs

Добавить FLOAT/DOUBLE, DATE/TIME/TIMESTAMP без TZ, INT128/NUMERIC до 38 цифр и text/binary input/output согласованного набора. Для каждого типа задать OID, format, range и точное преобразование. Native layout не является PG binary format.

ParameterStatus отражает реальные свойства codec/session: `integer_datetimes` согласован с binary temporal representation; TimeZone — только с поддержанным поведением. Не обещать interval/settings, которых реализация не поддерживает.

**Готово:** граничные значения, независимые binary fixtures, decimal precision, temporal epochs, NULL/empty и RETURNING с расширенными типами. Negative tests типов заменяются positive после реализации codec; `::date` остаётся синтаксической границей.

## P9. Клиентская приёмка — M2

Проверить libpq sync и async send/get без pipeline, named/unnamed preparation, повторное исполнение, formats, E/Sync recovery и cleanup portals. Повторить актуальные M1 проверки; закрепить фактические версии клиентов и libpq.

Обычный Psycopg 3 с `$n` не объявлять успешным по результатам C libpq с `?`: это разные SQL профили. Его полноценная параметризация ожидает расширения parser; нулевые параметры проверяются по реальному startup/query обмену.

**M2:** Extended wire работает с поддержанным SQL и codecs; ограничения grammar/driver bootstrap перечислены отдельно от реализованных сообщений протокола.

## P10. Удалённый TCP, TLS и cancellation

Реализовать выбранный в P0 TLS transport, PostgreSQL SSLRequest negotiation, конфигурируемый bind/certificates и libpq `sslmode=verify-full`. Вне loopback password authentication требует TLS; native WireCrypt не заменяет TLS.

Добавить BackendKeyData/CancelRequest, session-safe lookup и engine cancellation. Проверить неверные/устаревшие keys, completion/disconnect/detach races, активный SQL и lock wait. Cancel connection не создаёт application attachment.

**Готово:** подключение с другого узла или описанного network namespace, certificate/hostname verification, cancellation и shutdown при TLS handshake. Native endpoint сохраняет собственную конфигурацию.

## P11. Проверка требований приложения

Завершить профиль, начатый в P0: обязательные wire operations, типы, startup fields, session SQL, isolation, pooling/reset и права. Поддерживаемые настройки реально применяются; фиктивного успеха для SET или catalog queries нет. SQL session commands выполняет parser/engine.

Потребность в COPY, pipeline, иных types/auth/platforms оформляется дополнительными wire этапами. PostgreSQL grammar/functions/catalogs фиксируются как зависимость будущей работы движка. Не переносить её в plugin ради прохождения сценария.

**Готово:** известно, проходит ли приложение с существующим совместимым SQL. Изменение autocommit/SQL/options ради теста явно обозначается и не выдаётся за неизменённый профиль приложения.

## P12. Сквозная приёмка — M3

Выполнить согласованный сценарий приложения по удалённому TCP с PostgreSQL-драйвером, TLS и локальным исполнением Firebird. Повторить native coexistence, permissions, cross-visibility, concurrency, cancellation и shutdown.

Обновить configuration/install docs, COMPATIBILITY и VALIDATION: SHA, build/platform/library versions, команды, результаты и незакрытые зависимости. M3 не объявляется завершённым, если обязательный SQL приложения ещё требует будущего parser.

**M3:** заявленный прикладной профиль воспроизводимо работает и все его обязательные требования закрыты. Это не обобщается на любые PostgreSQL-приложения.

## Порядок

P0 → P1 → P2 → P3 (M0) → P4 → P5 → P6 (M1) → P7 → P8 → P9 (M2) → P10 → P11 → P12 (M3). Исследование приложения и TLS начинается в P0; lifecycle проверяется с P1. Будущий этап PostgreSQL grammar/functions лежит за пределами текущего wire scope и не заменяется эвристиками frontend.
