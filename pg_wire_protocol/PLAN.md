# План реализации wire-плагинов и PgWire

Единый список незавершённых работ — [BACKLOG.md](BACKLOG.md). Перед каждым этапом
сверять его с текущим кодом; в задании указывать затрагиваемые ID, после review
обновлять статус и evidence. Частичная реализация не закрывает оставшиеся части.

База — `master` Firebird 6, ветка `pg_wire_protocol`: [BASELINE.md](BASELINE.md). Обязательные границы задают [ARCHITECTURE.md](ARCHITECTURE.md) и [CODEX_CONTEXT.md](CODEX_CONTEXT.md); возможности и проверки — [COMPATIBILITY.md](COMPATIBILITY.md), [TEST_PLAN.md](TEST_PLAN.md).

Отдельная работа по [профилю 1С](1c/README.md) уже добавила SELECT без FROM в клиентском PG dialect и PSQL-функцию идентификации подключения. Второй согласованный срез — последовательные скрипты в Simple Query с остановкой на первой окончательной ошибке — реализован и проверен 10 ноября 2026 года, см. [VALIDATION.md](VALIDATION.md). Предварительного разбора всего пакета и нового публичного API нет. Ограниченный lexer выделяет кандидаты команд и задаёт tags/control profile; их грамматику, metadata и реальное исполнение определяет движок. Для стартовых настроек добавлен отдельный фиксированный English/UTF8 профиль `lc_messages` и `client_encoding`. Срез `standard_conforming_strings` реализован по редакции 2 задания: attachment state/parser/cache, узкая история PgWire и восстановление служебным SET. Проверки и ограничения — в VALIDATION; результат оставляется на проверку пользователя. Исходный пакет семи SET принимается с описанным ниже исключением для трёх настроек; следующие этапы автоматически не начинаются.

Архитектурный review `9e5b7bd07a` выявил ошибку сериализации typed/SQLSTATE literals при `off`. [Локальное исправление сохранённого SQL](1c/STRINGS_SOURCE_FIX_TASK.md) в `a31c890e1b` прошло повторный review и целевую приёмку. [Фиксированный профиль диагностики](1c/FIXED_DIAGNOSTICS_TASK.md) в `4abc5af919` также проверен без замечаний: только `escape_string_warning=off` и `client_min_messages=error`, через существующий SetPgSessionNode, без нового API или состояния. Результаты — в VALIDATION.

По решению пользователя реализован [синтаксический приём enable_mergejoin, cpu_operator_cost и lock_timeout](1c/IGNORED_STARTUP_SETTINGS_TASK.md): успешный SET без изменения или сохранения значения. Один внутренний Type существующего SetPgSessionNode, штатные scalar/numeric rules и классификатор SET; API, состояние, история и зависимости не расширены. Это исключение только для трёх имён: реальные эффекты optimizer/lock settings отложены, lock_timeout=20000 не задаёт 20 секунд. Полный исходный startup batch принимается с семью SET tags, I и standard_conforming_strings=off; это не реализация всех настроек и не запуск 1С. Ревизия `1f9af20db4` проверена без замечаний; результаты — в VALIDATION.

[PG BEGIN](1c/BEGIN_TASK.md) без опций реализован через штатный SetTransactionNode и общий executor: READ CONSISTENCY / READ WRITE / WAIT, promotion implicit handle, сохранение текущего блока при повторе, 25P02 в E. Native SET TRANSACTION остаётся прежним. Ревизия `97e44d0188` проверена без замечаний; повторная приёмка — в VALIDATION.

Реализован срез [последовательных $1 … $n](1c/NUMBERED_PARAMETERS_TASK.md) через Parser/ParameterNode и существующий Bind. Сначала только уникальные номера по порядку в SELECT/INSERT/UPDATE/DELETE/MERGE, с типами из SQL-контекста; остальные формы отклоняются явно. Это позволяет проверить форму 92 параметров без нового API, rewriting или изменения общего вывода типов. Ревизия `df37bb11fa` проверена без замечаний; повторная libpq/raw/native приёмка и границы проверки — в VALIDATION. Повторы/пропуски/перестановки и Parse OID inference остаются отдельными этапами.

Реализован срез [postfix cast expression::type](1c/POSTFIX_CAST_TASK.md): PG-only grammar создаёт существующий CastNode, типы и преобразования остаются native, из aliases добавляется только INT8 → BIGINT в postfix target. Первый срез ограничен корневыми SELECT/INSERT/UPDATE/DELETE/MERGE; сохранённый SQL/DDL/PSQL с настоящим `::` отклоняется до эффектов, без нового serializer/API. Production ограничен parser; существующие CastNode/Bind/codecs/API сохранены. Ревизия `9f94454527` проверена без замечаний; повторная целевая приёмка — в VALIDATION.

Реализован [LIMIT с целочисленным литералом](1c/LIMIT_TASK.md): PG-only SELECT clause создаёт существующий RowsClause и использует штатное исполнение ограничения строк. Первый срез — count 0…2147483647 без OFFSET/ALL/NULL/count-параметров, с сохранением областей ORDER/UNION/subqueries и прежнего root guard. Production ограничен Parser.cpp/h и parse.y; конфликтов осталось 158/11. Ревизия `ccd81f6473` проверена без замечаний, повторная приёмка — в VALIDATION; SQL-01 закрыт, SQL-02 остаётся открытым.

Реализован [binary BLOB output → bytea](1c/BINARY_BLOB_OUTPUT_TASK.md), APP-07 как часть APP-02: ResultCodec читает содержимое subtype 0 через штатный IBlob и передаёт text hex или raw binary с OID 17. Используется текущая transaction и существующий предел DataRow; engine/parser/API/зависимости не расширяются. Проверена выдача BinaryData в неизменной форме запроса selected06 на синтетической PUBLIC.PARAMS. Ревизия `5478a589d8` прошла review и повторную целевую приёмку; APP-07 закрыт. SQL bytea type/casts и каталоги остаются отдельными задачами; APP-02/SQL-06 не закрыты. Для APP-01 сначала нужны решения namespace IDs/имён/locale, записанные в BACKLOG.

Реализован [binary BLOB input](1c/BINARY_BLOB_INPUT_TASK.md), APP-08: InputCodec декодирует text hex/escape или binary bytea и формирует physical VARCHAR OCTETS по фактическим длинам Bind. Штатный MOVE создаёт BLOB при Execute в текущей transaction; новый lifecycle в плагине и изменения engine/API не требуются. Native OO probe подтвердил точные bytes до 65533 и mixed message с 68 BLOB. Этот предел относится к каждому входному значению, большие входные BLOB остаются APP-09. Приёмка — исходная форма selected23 на синтетической схеме, libpq/raw и native readback. Проверены реальные boundaries/cache/transaction semantics; APP-08 закрыт по реализации и оставлен на review, без git add/commit. Следующий этап автоматически не начинается.

Предыдущая P5-ревизия прошла целевую Linux/Super loopback приёмку: I/T/E, native transaction/session controls и recovery. В P6 сборка, unit tests и host/startup/Simple Query/transaction/lifecycle/native регрессии прошли. **Известное ограничение native baseline: READ COMMITTED READ CONSISTENCY + AUTO COMMIT не входит в подтверждённую совместимость; SNAPSHOT AUTO COMMIT проверен.** Целевой P6/M1 Linux/Super принят с известными native ограничениями. Shutdown во время ON CONNECT остаётся отдельным исключением: native Debug assertion воспроизводится также без wire endpoints. P7 Extended Query прошёл целевую Linux/Super приёмку: 16 групп. P8 codecs прошли целевую Linux/Super приёмку: восемь групп. Целевая клиентская приёмка P9/M2 пройдена; следующий этап — P10, TLS и cancellation. Результаты и границы покрытия — в [VALIDATION.md](VALIDATION.md). TLS, Windows и другие server modes требуют отдельной реализации и приёмки.

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

M0 проверил вычисляемый SELECT, настоящие C libpq/Psycopg2, authentication, recovery, native coexistence и lifecycle. Результаты сохранены в VALIDATION. Прежний SELECT-only фильтр и дополнительные engine metadata не являются текущим контрактом; они удалены при переносе классификации в плагин. SQL всегда передаётся engine целиком. Native dialects не принимают PG BEGIN/$n; их ограниченные PG4 формы добавлены отдельными срезами выше.

## P4. Simple Query, базовые codecs и ограниченная классификация

**Статус P4: целевая Linux/Super loopback приёмка — PASS.** Full make, четыре unit executable, 12 Simple Query групп с trace и lifecycle/startup/native regressions прошли. Evidence и ограничения — в [VALIDATION.md](VALIDATION.md). P5 ниже прошёл отдельную целевую приёмку с повторением regression; результаты разных ревизий сохранены отдельно.

Исходные байты каждой SQL-команды передаются в engine prepare с явной длиной и без изменения текста. Для Simple Query существующий lexer isql выделяет кандидаты границ, а engine prepare подтверждает завершённость команды по модели AUTOTERM. Ограниченный SqlInfo внутри PgWire определяет вид команды, command tag, профиль transaction/session controls и признак tokenless EMPTY; grammar, имена объектов, privileges и типы проверяет движок. Общий wire host SQL не анализирует.

После успешного engine prepare плагин сочетает lexical descriptor с существующими `getType()`, `getFlags()`, input/output metadata и affected records. `FLAG_HAS_CURSOR` выбирает cursor/fetch; output metadata определяет singleton output и codecs. RETURNING не превращает DML в SELECT. В I/T неподдержанный control либо UNKNOWN, включая USING wrapper, получает 0A000 до execution; в E успешно подготовленные команды, кроме разрешённого recovery, получают 25P02. Ошибка самого prepare имеет отдельный приоритет по CODEX_CONTEXT. Для классификации SQL новые публичные DSQL metadata, AST annotations и parser diagnostics не добавляются. Узкое исключение для standard_conforming_strings — attachment getInfo item и два prepare flags, описанные в 1c/README.md; методов и vtables оно не меняет.

Запрещены SQL rewriting, удаление comments из передаваемого текста, замена `$n` на `?`, подстановка параметров SQL-литералами и эмуляция успешного SET/BEGIN. Исключение пользователя — синтаксический приём только enable_mergejoin/cpu_operator_cost/lock_timeout без эффекта, описанный в начале плана. Совместимость синтаксиса и исполнение определяет Firebird.

Simple Query поддерживает [последовательные скрипты](1c/README.md): исходные диапазоны выделяются по модели isql/AUTOTERM, каждая команда проходит обычный engine prepare и исполнение, первая окончательная ошибка прекращает остаток сообщения. Предварительная проверка синтаксиса всего Query отложена; ранний COMMIT сохраняется и при поздней syntax error. В I текущий implicit сегмент откатывается при ошибке, в T остаётся E. Extended Parse по-прежнему принимает одну команду. Приёмка от 10 ноября 2026 года — в [VALIDATION.md](VALIDATION.md).

EMPTY определяется ограниченным lexer только для нулевой строки, whitespace и полностью закрытых comments без SQL tokens. Даже такой текст передаётся в обычный engine prepare неизменённым. Только сочетание этого признака с engine syntax error 42601 после успешного cleanup преобразуется в EmptyQueryResponse; permission, cancellation, resource и другие ошибки не маскируются. Незаконченные строка/comment/statement и отдельный `;` остаются parser errors. Native parser diagnostics и `isql -autoterm` не меняются.

Сохранить SELECT/DML/DDL/RETURNING, EXECUTE BLOCK/PROCEDURE, affected-record counts, singleton/cursor output и реальные implicit commit/rollback. Поддержанные text codecs: integer, BOOLEAN, CHAR/VARCHAR, exact numeric в SHORT/LONG/INT64 и NULL. CHAR fetch использует varying metadata, logical CHAR OID и значимые пробелы сохраняются.

Исторические проверки P4: ограниченный lexer unit tests; raw TCP comments/mixed-case/CTE/quoted identifiers/literals/DDL modifiers/RETURNING; неизвестные wrappers и прежний control guard; malformed SQL и тогда ещё неподдержанные batches получали parser error; текущие последовательные скрипты приняты отдельно 10 ноября 2026 года. В P5 negative expectations для поддержанных controls заменяются проверками фактического исполнения и I/T/E. Штатные native isql AUTOTERM и diagnostics не меняются. C libpq и psycopg2 autocommit, cross-visibility, real commit failure, lifecycle/startup/native regressions прошли на P4. Зависимость от отдельного native metadata executable удалена.

**Критерий завершения:** сборка и соответствующая текущему SQL boundary integration suite прошли, реальные команды/версии/ограничения записаны в VALIDATION. Close/free fault injection и engine execution timeout отдельно отмечаются как выполненные либо not-run.

## P5. Транзакции и состояние сессии

**Исторический статус P5: целевая Linux/Super loopback приёмка — PASS с native ограничением READ CONSISTENCY + AUTO COMMIT.** Все 14 transaction groups прошли без skips; SNAPSHOT AUTO COMMIT и NO AUTO UNDO проверены. Повторены full build, четыре unit executable (487 classifier checks), Simple Query 12, lifecycle 7, startup 52 и native 9. Целевой M1 принят отдельно в P6 с указанными ограничениями. Один `QueryExecutor` на authenticated session владеет transaction и I/T/E. Prepare получает current native handle, включая implicit сегмент, либо NULL при его отсутствии. Plugin-local `ControlKind` применяется после successful prepare и сверяется со штатным coarse type. Возвращённый `ITransaction*` сохраняется до проверки execute status, включая замену или NULL.

Реализованы `SET TRANSACTION` → tag BEGIN, полный COMMIT/ROLLBACK, `COMMIT/ROLLBACK [WORK] RETAIN [SNAPSHOT]`, SAVEPOINT, RELEASE SAVEPOINT и ROLLBACK TO. В I полные controls завершают существующий implicit сегмент либо являются no-op без handle; retaining и savepoint actions дают 25000/I. Повторный SET TRANSACTION в T даёт 25001/E с сохранением существующего handle. BEGIN, START TRANSACTION и RETAINING не входят в native grammar и остаются parser errors.

Только PgWire-сессия управляет не более чем одной текущей application transaction; последовательных транзакций за жизнь соединения может быть сколько угодно. Это не ограничение generic host, родного API или других plugins. Parse/Describe/statements/portals используют текущую transaction либо prepare с NULL, без отдельного handle; внутренние system/security/autonomous transactions не входят в инвариант.

В E выполняется обычный prepare с сохранённой native transaction. Успешно подготовленные команды, кроме recovery, освобождаются с 25P02 без execution. Failed-prepare whitelist ограничен фактическим `isc_dsql_relation_err`, когда итоговая диагностика — nonfatal 42P01: только этот случай заменяется на 25P02. Одного explicit SQLSTATE 42P01 недостаточно. Syntax, permission, shutdown, cancellation/timeout, invalid handle, resource/I/O и unknown errors сохраняют собственную диагностику и cleanup/close. Prepare может компилировать, загружать metadata/external routines и брать locks; отсутствие SQL execution не делает его отдельным чистым parser API.

После statement error в explicit block сохраняется native transaction и E. Полный COMMIT из E делает настоящий rollback с tag ROLLBACK/I; допустимый ROLLBACK TO и ROLLBACK RETAIN восстанавливают T. Implicit TPB — READ WRITE / READ COMMITTED READ CONSISTENCY / WAIT без заданного плагином lock timeout. В Simple Query implicit сегмент продолжается до полного COMMIT/ROLLBACK или конца Query. Промежуточные CommandComplete не означают commit; последний отправляется после успешного implicit commit. Опции explicit SET TRANSACTION, включая AUTO COMMIT / NO AUTO UNDO, передаются движку неизменёнными и не извлекаются lexer; прежние AUTO COMMIT commits не отменяются последующим rollback.

Дополнительные transport/frame/startup/auth/output timers удалены. SQL и ожидание lock используют native policy: statement-level timeout остаётся 0, implicit WAIT не получает plugin lock timeout. StatementTimeout в конфигурации задаётся в секундах, ConnectionIdleTimeout — в минутах; 0 отключает соответствующий engine cap. Native idle shutdown откатывает transaction, следующий Query получает 57P05/close.

AUTO COMMIT DML проверяется положительно в native SNAPSHOT profile, NO AUTO UNDO — отдельно. Ограничение native Debug baseline: AUTO COMMIT с READ COMMITTED READ CONSISTENCY вызывает assertion `Savepoint.cpp:833` в restart path, независимо воспроизведённый embedded isql без PgWire/server/triggers. Этот профиль не входит в принятую совместимость. В P5 core/SQL options не менялись; исправление native дефекта остаётся отдельной работой. Repro и точный объём покрытия записаны в VALIDATION; P6 принимает целевой M1 с этим native ограничением.

Native session SET исполняются по whitelist из CODEX_CONTEXT; ALTER SESSION RESET имеет настоящий effect. RESET может заменить native transaction за тем же interface pointer: при успехе I остаётся I, T — T, в E команда получает 25P02. Исторический P5 ещё не хранил statements/portals или savepoint names. Теперь RESET очищает P7 objects и узкую историю standard_conforming_strings. Session settings не эмулируются успешными ответами.

**Критерий завершения:** live checks подтверждают native transaction SQL, visibility, I/T/E и `PQtransactionStatus`, error precedence, unique violation/recovery, savepoints/retaining, disconnect rollback, session SET и ALTER SESSION RESET; результаты записаны в VALIDATION. Unit tests не заменяют эту приёмку. Отдельный этап PG BEGIN перевёл автоматический BEGIN psycopg2 в положительные проверки.

## P6. Серверная приёмка — M1

**Целевой P6/M1 Linux/Super принят с известными native ограничениями.** Прошли семь групп server harness, 58 pytest cases, сборка и перечисленные в VALIDATION регрессии. Shutdown во время ON CONNECT исключён из этой приёмки: [attach/shutdown harness](tools/pgwire_attach_shutdown_smoke.py) сохраняет FAIL/SIGABRT, assertion alloc.cpp:2075 (!child), через PgWire и native-only с пустыми wire lists. В P6 core не изменялся. P7 Extended Query прошёл целевую Linux/Super приёмку: 16 групп. P8 codecs прошли целевую Linux/Super приёмку: восемь групп. Целевая клиентская приёмка P9/M2 пройдена; следующий этап — P10, TLS и cancellation. Новых сценариев пауз или срабатывания таймаутов P6 не добавляет.

[pgwire_server_smoke.py](tools/pgwire_server_smoke.py) объединяет мигрированный pytest-набор (58 live cases — PASS), 40 connect/query/error/disconnect sessions (16 для прогрева, 24 для учёта attachments/statements/transactions/FDs; общее число threads записывается как диагностика), отмену через MON$STATEMENTS в I/E, удаление attachment в I/T, gfix shutdown базы и остановку сервера с lock wait, password exchange и partial startup. SuperClassic проверяется только на диагностируемый отказ PgWire с сохранением native.

При terminal host stop использовать `IAttachment::cancelOperation(fb_cancel_abort)`, чтобы запрет оставался действующим между engine calls; PgWire повторно проверяет stop после чтения frame. Module references сохраняются до завершения callbacks, бесконечного join в preproviders нет. Обрыв клиентского socket во время engine call не обнаруживается немедленно: cleanup начинается после возврата из движка и обнаружения I/O failure. Явный server shutdown отменяет engine operation независимо от чтения socket.

**Критерий целевого M1 выполнен:** перечисленные P6-01–07 и регрессии прошли; shutdown во время ON CONNECT остаётся явно исключённым native дефектом с отдельным FAIL. Команды и ограничения записаны в VALIDATION. Windows, работа PgWire в SuperClassic/Classic, auth-plugin и close/free fault injection этим результатом не принимаются. Native READ CONSISTENCY + AUTO COMMIT остаётся известным ограничением. Выбранное приложение и его SQL-зависимости относятся к M3.

## P7. Extended Query и prepared objects

**Целевая Linux/Super приёмка — PASS: 16 групп; evidence в [VALIDATION.md](VALIDATION.md).** Parse/Bind/Describe/Execute/Close/Flush/Sync, named/unnamed statements и portals используют существующие engine interfaces. SQL Parse передаётся неизменённым; Bind получает порядок slots из native metadata. В исходной P7-приёмке `$n` и BEGIN были parser limits; BEGIN без опций и последовательные типизированные `$n` добавлены отдельными этапами выше.

ParameterDescription, RowDescription/NoData, PortalSuspended/maxRows и повторное исполнение prepared statements реализованы. Named statement переживает commit. Bind готовит отдельный native statement с тем же SQL для каждого portal, используя текущую transaction либо NULL; скрытые application transactions не создаются. Close statement и замена unnamed statement сохраняют уже связанные portals. Simple Query инвалидирует unnamed statement и portal. Завершение transaction удаляет portals; Sync внутри T сохраняет их. Ошибка инвалидирует все portals и включает discard-until-Sync; explicit transaction сохраняется в E. RESET инвалидирует все statements/portals, даже если native transaction interface pointer не изменился.

В принятом P7 были text input/output базовых integer/BOOLEAN/CHAR/VARCHAR/NUMERIC до INT64 и NULL, binary integer/BOOLEAN/строки. P8 расширяет этот набор, как описано ниже. Parse OID hints проверяются консервативно: точный inferred numeric/integer/boolean OID, для строк совместимы 25/1042/1043; 0 или отсутствующий hint означает inference. Значения не превращаются в SQL literals.

На session ограничены 128 statements, 128 portals и 8 MiB учитываемых сохранённых SQL/metadata/buffers. Остальные engine allocations ограничиваются числом объектов. Flush не завершает transaction; Sync фиксирует implicit cycle и выдаёт ReadyForQuery, сохраняя explicit T/E. CommandComplete Execute не означает commit cycle. Ошибка implicit cycle откатывает его уже до Sync; Sync завершает protocol recovery. SET TRANSACTION при уже открытом implicit cycle получает 25001: его TPB не заменяет текущую native transaction.

**Приёмка:** прямой C libpq `PQexecParams`, `PQprepare`/`PQexecPrepared` с нулём параметров и native `?`, например `SELECT CAST(? AS INTEGER) FROM RDB$DATABASE`; raw-wire проверяет formats, lifecycle, limits и recovery. C/raw проверки `$1` получают настоящую parser error. Прошли 16 групп P7, пять codec unit executables (149 input и 487 SQL-classifier checks), семь server groups/58 pytest cases и девять native baseline checks; полные результаты и регрессии — в VALIDATION. Psycopg 3 фактически не проверен в P7, другие driver/application profiles остаются P9. P8 codecs и клиентская приёмка P9/M2 приняты отдельно.

В P7 включён узкий native cleanup fix: release_attachment в jrd.cpp снимает used со всех requests attachment перед первым CMP_release, который может освободить несколько clones одного Statement. Падение до исправления воспроизведено через native TCP без PgWire. Grammar, public statement metadata и CLOOP не меняются. Отдельный ON CONNECT shutdown assertion !child не исправлялся и повторно не проверялся.

## P8. Расширенные codecs

**Целевая Linux/Super приёмка — PASS: восемь групп и восемь unit executables; evidence в [VALIDATION.md](VALIDATION.md).** Добавлены text/binary FLOAT/DOUBLE (OID 700/701), DATE/TIME/TIMESTAMP без TZ (1082/1083/1114), INT128/NUMERIC до 38 цифр и binary NUMERIC (1700). Pure INT128 сохраняет весь signed native диапазон, включая 39-значные границы. InputCodec и ResultCodec используют общие FloatCodec/NumericCodec/TemporalCodec внутри плагина, без изменений ядра, parser или public API.

Numeric кодируется точно без float; binary — base 10000 с weight/sign/dscale. Overflow и непредставимая native scale дают 22003. FLOAT/DOUBLE используют network IEEE32/64 и locale-independent text, включая NaN/Infinity. Temporal text — ISO, binary — дни/микросекунды относительно PostgreSQL epoch 2000-01-01. Поддержаны конечные годы 0001..9999, время менее 24:00 и точность 100 мкс; меньшая точность не округляется, а отклоняется с 22008. BC, temporal infinity, TZ и DECFLOAT остаются за пределами codecs. Binary BLOB output добавлен позднее в APP-07, bounded input — в APP-08; большие input/прочие subtype остаются за границей.

Startup публикует `integer_datetimes=on`, согласованный с binary temporal representation. TimeZone не объявляется. SQL bytes и grammar не меняются; BEGIN, ограниченные `$n` и postfix `::` с native builtin targets добавлены позднее отдельными этапами; custom cast targets остаются за границей.

**Проверено:** реальный C libpq text/binary input/output и prepared reuse; независимые binary fixtures, float special/subnormal/negative-zero, INT128 limits и scale38, temporal epochs/leap years/negative timestamps/range, NULL, Simple Query и DML RETURNING, native cross-visibility и отказы до исполнения. Negative tests реализованных типов заменены positive; unsupported guards используют DECFLOAT/non-binary BLOB. Повторены P7 Extended, Simple Query, transaction/session, startup и P6 server/58 pytest cases. P9/M2 принят для совместимого SQL; следующий этап — P10.

## P9. Клиентская приёмка — M2

**Целевой M2 для Linux/Super IPv4 loopback принят:** 17 групп C libpq/raw/Psycopg 3, повторные P8 codecs и M1 регрессии. Фактические команды, версии и результаты — в [VALIDATION.md](VALIDATION.md).

C libpq 14.24 проверяет sync/async send/get без pipeline, named/unnamed preparation и reuse, formats, Parse/Bind/Execute errors, E/Sync recovery и rollback. Async path использует poll/PQflush/PQconsumeInput/PQisBusy перед каждым PQgetResult и дочитывает до NULL: 20 команд/20 результатов. Raw-wire покрывает lifecycle и cleanup portals, а дополнительный E06 case подтверждает 25001 и rollback до Sync при SET TRANSACTION внутри открытого implicit cycle.

Psycopg 3.3.6 binary/libpq 18.6 проверен через настоящие Cursor/AsyncCursor: autocommit=True, SQL без параметров, Simple/forced prepared/binary results, P8 type decoding, reuse после COMMIT и Close при вытеснении named statement. Native SET TRANSACTION/savepoints/COMMIT/ROLLBACK проверены в отдельном профиле с prepare_threshold=None, включая видимость результатов через native TCP. Libpq traces подтверждают реальные Query/Parse/Bind/Execute/Sync/Close.

В историческом P9 обычные `%s` → `$1`, автоматический BEGIN и sql.Literal(date) → `::date` дали 42601. Engine теперь поддерживает BEGIN без опций, последовательные typed `$n` и postfix casts. Psycopg 3 сценарии обновлены; live-повтор недоступен в текущем окружении. Custom targets и остальные parser limits сохраняются. При непустом prepared cache после успешного ROLLBACK Psycopg отправляет неподдержанный DEALLOCATE ALL и тоже получает 42601; сам rollback уже выполнен. Это ограничение драйверного SQL, а не повод менять parser или переписывать SQL в wire. Full cache cleanup после DROP/ALTER и native RESET требует отдельного учёта в прикладном профиле, см. COMPATIBILITY.

**M2:** Extended wire работает с поддержанным SQL и codecs; это не обещает работу произвольного PostgreSQL-приложения. Grammar/bootstrap ограничения записаны отдельно. В P9 менялись только harnesses и документация. Следующий этап — P10: удалённый TCP, TLS и CancelRequest.

## P10. Удалённый TCP, TLS и cancellation

Реализовать выбранный в P0 TLS transport, PostgreSQL SSLRequest negotiation, конфигурируемый bind/certificates и libpq `sslmode=verify-full`. Вне loopback password authentication требует TLS; native WireCrypt не заменяет TLS.

Добавить BackendKeyData/CancelRequest, session-safe lookup и engine cancellation. Проверить неверные/устаревшие keys, completion/disconnect/detach races, активный SQL и lock wait. Cancel connection не создаёт application attachment.

**Готово:** подключение с другого узла или описанного network namespace, certificate/hostname verification, cancellation и shutdown при TLS handshake. Native endpoint сохраняет собственную конфигурацию.

## P11. Проверка требований приложения

Завершить профиль, начатый в P0: обязательные wire operations, типы, startup fields, session SQL, isolation, pooling/reset и права. Поддерживаемые настройки реально применяются, кроме временно игнорируемых enable_mergejoin/cpu_operator_cost/lock_timeout по решению пользователя; их семантику реализовать отдельными этапами. Для других SET и catalog queries пустой успешный ответ не допускается. SQL session commands выполняет parser/engine.

Потребность в COPY, pipeline, иных types/auth/platforms оформляется дополнительными wire этапами. PostgreSQL grammar/functions/catalogs фиксируются как зависимость будущей работы движка. Не переносить её в plugin ради прохождения сценария.

**Готово:** известно, проходит ли приложение с существующим совместимым SQL. Изменение autocommit/SQL/options ради теста явно обозначается и не выдаётся за неизменённый профиль приложения.

## P12. Сквозная приёмка — M3

Выполнить согласованный сценарий приложения по удалённому TCP с PostgreSQL-драйвером, TLS и локальным исполнением Firebird. Повторить native coexistence, permissions, cross-visibility, concurrency, cancellation и shutdown.

Обновить configuration/install docs, COMPATIBILITY и VALIDATION: SHA, build/platform/library versions, команды, результаты и незакрытые зависимости. M3 не объявляется завершённым, если обязательный SQL приложения ещё требует будущего parser.

**M3:** заявленный прикладной профиль воспроизводимо работает и все его обязательные требования закрыты. Это не обобщается на любые PostgreSQL-приложения.

## Порядок

P0 → P1 → P2 → P3 (M0) → P4 → P5 → P6 (M1) → P7 → P8 → P9 (M2) → P10 → P11 → P12 (M3). Исследование приложения и TLS начинается в P0; lifecycle проверяется с P1. Будущий этап PostgreSQL grammar/functions лежит за пределами текущего wire scope и не заменяется эвристиками frontend.
