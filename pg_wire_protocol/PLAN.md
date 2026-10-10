# План реализации wire-плагинов и PgWire

База — `master` Firebird 6, ветка `pg_wire_protocol`: [BASELINE.md](BASELINE.md). Обязательные границы задают [ARCHITECTURE.md](ARCHITECTURE.md) и [CODEX_CONTEXT.md](CODEX_CONTEXT.md); возможности и проверки — [COMPATIBILITY.md](COMPATIBILITY.md), [TEST_PLAN.md](TEST_PLAN.md).

PostgreSQL grammar/functions отложены. Все этапы передают SQL существующему parser без wire-классификации и переписывания. Расширение нейтральных parser/DSQL metadata входит в реализацию протокола; принятие нового SQL-синтаксиса — отдельная будущая работа.

Состояние на 9 октября 2026: начальный Linux/Super host P1 со штатным getPluginConfig прошёл повторную проверку; результаты и границы покрытия записаны в [VALIDATION.md](VALIDATION.md). Целевые Linux/Super проверки P2 прошли: отдельный PgWire module, framing, host authentication и локальный attachment. SQL execution отсутствует, каждый корректный Query получает 0A000. Следующий функциональный этап — P3 и M0 SELECT. Windows и другие server modes этим срезом не принимаются.

## P0. База, зависимости и проектирование

Проверить HEAD, правила репозитория и штатный Linux build workflow. Собрать сервер, подготовить отдельную UTF8/dialect 3 базу с alias и пользователями. Проверить native connect → SELECT → commit/rollback. Зафиксировать реальные AuthServer/AuthClient, локальную security database и Srp256 profile.

Записать `IMPLEMENTATION_NOTES.md`: versioned plugin ABI через CLOOP, ownership interfaces, ограниченные workers/queues, deadlines, lifecycle и источники command tags. Для M0 определить только минимальный engine-origin kind через существующий `getInfo`; полный descriptor относится к P4/P5. Отдельный публичный API анализа до prepare не является обязательным. Сразу выбрать TLS-библиотеку, optional build flag и упаковку Linux/Windows; текущий `TLS_OPTIONS` относится к thread-local storage. TLS реализуется в P10, но зависимость не оставлять неизвестной до этого этапа.

Начать профиль целевого приложения: driver/version, startup, служебные SQL, placeholders, каталоги и транзакции. Если приложение пока не выбрано, оставить это открытым условием M3; host и протокол от выбора не зависят. Не добавлять SQL compatibility в wire-слой по результатам трассы.

**Готово:** native baseline воспроизводим; решения о ABI/engine metadata/TLS и команды сборки записаны. Недоступная зависимость обозначена конкретно, успех сборки не предполагается.

## P1. Общий host, конфигурация и plugin ABI

Добавить новый тип plugin, сохранив существующие номера, и интерфейсы в `FirebirdInterface.idl`; обновить generated bindings штатным способом. Использовать существующий PluginManager, factory registration и module ownership. Не вводить параллельный loader через прямой `dlopen`/`LoadLibrary`.

Реализовать `WireProtocolPlugins` / `WireProtocolPorts` по ARCHITECTURE: позиционные comma-separated списки, пустой default, проверка до загрузки, отсутствие сдвига портов при недостающем модуле. Native не включается в список. Общая ошибка списков отключает дополнительную конфигурацию с диагностикой; ошибка модуля или bind — его endpoint. Проверить одну тестовую библиотеку с двумя aliases/instances, включая отсутствующее имя между ними.

Endpoint settings получать через существующий `IConfigManager::getPluginConfig(name)` до bind/load, сохраняя стандартный named Config / module `.conf` fallback. NULL — ошибка endpoint. Конфигурацию подготавливать до запуска; для разных aliases рекомендуется named Config в `plugins.conf`, без дополнительных позиционных списков. Проверять значения host settings в возвращённом IConfig. Порт имеет один источник — `WireProtocolPorts`; любой Port в host settings отвергается.

Начальный host принимает только BindAddress=127.0.0.1 и отвергает любой TLS entry. Workers: default 4, диапазон 1–16; QueueSize: default 16, диапазон 1–64; IOTimeout: default 30000 ms, диапазон 1–300000 ms. Повтор host setting — ошибка endpoint. Общие пределы: 16 configured endpoints и 64 workers. Проверить queue-full close, expiration queued socket и timeout реальных transport operations; whole-frame deadlines появятся в protocol plugin.

Общий host разместить в `src/remote/server/wire`, plugin — в `src/plugins/wire/pg`. `dirObjects` не рекурсивен: явно включить host objects в server target, а PG objects — в отдельную shared library и install target. PG codec не линкуется в daemon как встроенный frontend.

Host владеет listeners, transport, workers, registry и shutdown. Реализованный startup получает native endpoint через getsockname после bind: `-p` и service-name resolution уже учтены; IPv4 wildcard/loopback и IPv6 dual-stack/mapped overlap проверяются до загрузки wire модуля. Unix-only или IPv6-only native не резервирует IPv4 loopback port. Успешный bind также предшествует load. ABI не передаёт `rem_port`, platform socket types или STL. Начальное включение — Linux/Super; SuperClassic/Classic диагностируются с сохранением native. Windows wiring и приёмка других режимов остаются незакрытой частью scope.

В preproviders остановить accept и разбудить transport перед plugin stop без worker join. В postproviders все acceptors/workers разделяют один deadline 5 секунд; при его превышении сообщить ошибку и сохранить owners вместо выгрузки исполняемого модуля. Проверить stop/cleanup idle, queued и partial sessions реального server. SQL/auth cancellation относится к следующим этапам.

**Критерий готовности начального Linux/Super среза:** loading/config/transport tests, disabled/default, ошибки endpoint, bounded workers, stop/wakeup и удержание модуля до завершения callbacks. Native продолжает работать в том же процессе. Проверки выполняются на test plugin ещё до SQL, результаты подтверждаются отдельными PASS в VALIDATION. Полное покрытие Windows и других server modes этим критерием не закрывается.

## P2. PgWire framing, authentication и attachment

**Статус: целевые Linux/Super проверки P2 — PASS; команды и границы покрытия в [VALIDATION.md](VALIDATION.md).** В отдельном `src/plugins/wire/pg` реализованы protocol 3.0 StartupMessage, отказ SSLRequest/GSSENCRequest одним N, PasswordMessage, Query, Terminate и serializers. Production module добавлен в POSIX/CMake build definitions. Любой корректный Query получает 0A000 + ReadyForQuery I без SQL-разбора; parser execution начинается в P3.

`IWireServerHost` расширен версионированными attachDatabase/detachDatabase. Attachment принадлежит host и выдаётся plugin как borrowed до detach/end of run; после detach он недействителен даже при ошибке. Child interfaces закрываются до detach. Общий helper `Authentication.cpp` выполняет in-memory Srp256 exchange без изменения EDS. Прямой вызов `EDS::validatePassword` до attach неприменим: он требует текущий attachment.

Host проверяет comma-separated DatabaseAliases (default empty запрещает attachments), локальные alias/security DB с Engine14 в её Providers, AuthClient/AuthServer Srp256 и RemoteAccess. Helper ограничен 16 rounds и 64 KiB на auth payload, строит новый доверенный DPB с engine-only Providers, реальным peer и endpoint alias в MON$REMOTE_VERSION; MON$REMOTE_PROTOCOL остаётся TCPv4. AuthenticationOk отправляется после успешного attach. Пароли затираются, клиент не задаёт DPB/provider route.

Codec tests и startup harness проверяют: partial/coalesced frames, literal response bytes, UTF8/NUL/length errors, пределы 16 KiB startup / 1 MiB frame / 4096 bytes password body с NUL. Общий startup network budget включает SSL/GSS, Password и ответы; short I/O не обновляет deadline. IOTimeout не прерывает принудительно синхронный auth/attach callback до handle; shutdown должен оставлять provider возможность завершить такие вызовы.

**Критерий приёмки:** правильный/неправильный пароль, неизвестные user/alias, default database=user с allowlist, plugin policy, RemoteAccess, раздельные identities через native monitoring, ON CONNECT failure до AuthenticationOk, cleanup и отсутствие вторичного сетевого attach. Результаты фиксируются отдельно; вычисляемый SELECT остаётся условием P3/M0. Ключ шифрования от native клиента не поддерживается; серверный key holder проверяется отдельно.

## P3. Первый запрос и минимальная metadata — M0

Добавить минимальный info item исходного вида операции через существующий `IStatement::getInfo`, сохранив legacy `getType()`/flags. До execution он должен различать обычный SELECT, DML RETURNING и selectable EXECUTE BLOCK: публичные SELECT type/FLAG_HAS_CURSOR для этого недостаточны. M0 выполняет поддержанный SELECT, остальные виды дают 0A000 по metadata. Это временная граница первого smoke, которая снимается в P4/P5.

Полный descriptor DML/DDL/control/session и EMPTY не входят в M0. До P4 пустой ввод может получать существующую parser error; его нельзя фиктивно считать успешно разобранным. E/recovery появляется в P5. Wire SQL lexer не вводится ни для временного M0, ни для полного контракта.

Выполнить через engine prepare/execute/fetch:

```sql
SELECT CAST(40 + 2 AS INTEGER) AS "answer" FROM RDB$DATABASE
```

Прямой C libpq `PQexec` и psycopg2 с `autocommit=True` получают 42, OID 23 и alias `answer`. Psycopg2 connection context не использовать: он может отправлять `BEGIN`. Проверить другое выражение/данные, SQL error → успешный query на той же сессии, настоящий implicit commit/rollback и освобождение attachment при disconnect.

**M0:** обязательные M0 checks из TEST_PLAN, включая SELECT/RETURNING/SELECT_BLOCK distinction; native работает одновременно, endpoint принадлежит `firebird`, backend network hop отсутствует. Минимальная metadata проверена независимо от wire. `BEGIN` и `$n` получают ошибки существующего parser.

## P4. Simple Query, базовые codecs и diagnostics

Расширить нейтральный engine descriptor исходными DML/DDL subtype, result kind и metadata для точных command tags; controls/session effects завершить в P5. Сохранить вид операции до DSQL transformations. MERGE/UPDATE OR INSERT/RETURNING не классифицируются по coarse SELECT/INSERT type.

Различать EMPTY посредством отдельного engine diagnostic marker для чистого EOF без SQL tokens. Учесть нулевую длину до parser, whitespace/comments и незаконченные строки/комментарии. Сохранить legacy `isc_command_end_err2`-совместимость, в частности isql AUTOTERM; дополнительный marker/opt-in проверяется native regression. Новый standalone analysis API не нужен.

Реализовать integers, boolean, CHAR/VARCHAR, точный numeric до INT64 и NULL. Сохранять исходные type/subtype/scale/alias; проверять диапазоны, UTF8 byte lengths, NULL/empty и locale-independent formatting. Для CHAR предпочесть engine fetch conversion в SQL_VARYING, сохраняя внешние CHAR metadata: не обрезать значимые пробелы и не передавать физическую UTF8-добивку.

Выполнять совместимые DML/DDL и RETURNING без лексического allowlist. Проверять поддержку входных/выходных metadata до side effects. Форма запроса определяется parser, количество строк — engine result, command tag — нейтральными metadata. Не ограничивать DDL только CREATE/DROP TABLE.

Сопоставлять ошибки по полному структурированному ISC status vector и приоритету конкретных причин. Не использовать текст сообщения или только `status[1]`. Ошибки fetch/commit не завершаются ложным CommandComplete.

**Готово:** базовые codec/DML/DDL/RETURNING/error cases; несовместимый синтаксис отвергается движком без исполнения части batch. Неподдержанный тип даёт ошибку, а не подмену на text/NULL.

## P5. Транзакции и состояние сессии

Реализовать I/T/E по CODEX_CONTEXT с parser-derived действиями `SET TRANSACTION`, `COMMIT`, `ROLLBACK`, retaining и savepoints. SQL управления транзакцией проходит движок; после execute учитывать возвращённый `ITransaction*`, который может измениться или стать NULL. Implicit transaction имеет настоящий commit/rollback.

Только PgWire-сессия управляет не более чем одной текущей application transaction; последовательных транзакций за жизнь соединения может быть сколько угодно. Это не ограничение generic host, родного API или других plugins. Parse/Describe/statements/portals используют текущую transaction либо prepare с NULL, без отдельного handle; внутренние system/security/autonomous transactions не входят в инвариант.

В E использовать обычный prepare с живой native transaction и подробные metadata. После успешного prepare исполнять только разрешённые recovery commands, прочие освобождать с 25P02. Синтаксические ошибки сохраняют 42601; ожидаемые semantic errors могут отображаться в 25P02 по явной таблице. Shutdown, cancellation/timeout, invalid handle, resource/I/O errors сохраняют собственную диагностику и cleanup/close. Prepare не выполняет подготовленный DML/DDL, но может компилировать, загружать metadata/external routines и брать locks; это не отдельный чистый parser API.

После statement error в explicit block сохранять native transaction и логический E; обычные команды получают 25P02. Полный COMMIT из E завершает текущую transaction rollback. Допустимый ROLLBACK TO восстанавливает T без потери изменений до savepoint. Retaining, AUTO COMMIT / NO AUTO UNDO и ошибки control commands следуют контракту CODEX_CONTEXT: native AUTO COMMIT сохраняет собственную семантику, уже совершённые commits не отменяются. Не перехватывать PostgreSQL `BEGIN`, `SET` и другие несовместимые формы.

**Готово:** native transaction SQL, visibility, I/T/E и `PQtransactionStatus`, unique violation/recovery, savepoints/retaining, disconnect rollback и `ALTER SESSION RESET`, способный заменить native transaction при том же interface pointer. Автоматический `BEGIN` psycopg2 остаётся отрицательной проверкой синтаксиса до отдельного расширения grammar.

## P6. Серверная приёмка — M1

Выполнить архитектурные, authentication, parser, wire и lifecycle группы TEST_PLAN. Переработать исходный Python scaffold по новому контракту; его прежние 56 cases не являются неизменным gate. Добавить cross-visibility PG/native, permissions, лимиты, malformed clients и resource accounting после прогрева.

Проверить shutdown с idle/incomplete/auth/active/blocked-query sessions. Использовать `IAttachment::cancelOperation`; не делать бесконечный join в preproviders. Module references удерживаются до завершения callbacks; engine calls после shutdown providers запрещены.

Добавить внешнее завершение attachment/database, отмену через MON$STATEMENTS, idle/statement timeouts. Зафиксировать обнаружение shutdown при idle socket и сроки cleanup. Super и SuperClassic получают отдельные результаты.

**M1:** обязательные проверки пройдены, ограничения и команды в VALIDATION. Это Simple Query с совместимым SQL, а не гарантия работы любого PostgreSQL-приложения.

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
