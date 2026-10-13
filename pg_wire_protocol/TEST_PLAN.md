# План приёмки wire plugins и PostgreSQL endpoint

База — `master` Firebird 6, SHA из [BASELINE.md](BASELINE.md). Архитектура и SQL boundary определены в [ARCHITECTURE.md](ARCHITECTURE.md), transaction contract — в [CODEX_CONTEXT.md](CODEX_CONTEXT.md), заявляемые возможности — в [COMPATIBILITY.md](COMPATIBILITY.md).

Исходный Python scaffold содержит 20 функций/56 параметризованных случаев и требует миграции. Начальные host tests расположены в src/common/tests/WireProtocolConfigTest.cpp, src/remote/server/wire/tests и isolated tools harness. P2/P3/P4 приёмка сохранена в VALIDATION. Предыдущая P5-ревизия прошла целевую Linux/Super приёмку с native ограничением READ CONSISTENCY + AUTO COMMIT; выбранные проверки после удаления wire-таймаутов прошли и записаны в [VALIDATION.md](VALIDATION.md), без повторения полной P5-приёмки. P6/M1 остаётся следующим этапом. PASS относится к зафиксированным cases/fixtures, а наличие строки в полной матрице не означает её прохождения.

## Этапы и обязательный минимум

| Этап | Принимаемый результат |
|---|---|
| M0 | Динамический PgWire внутри firebird, authentication, вычисляемый SELECT, error/recovery, native coexistence и cleanup |
| M1 | Simple Query с совместимым SQL, ограниченный plugin descriptor + существующие metadata, basic codecs, native transaction controls и lifecycle |
| M2 | Extended Query, prepared statements/portals, native placeholders и расширенные codecs |
| M3 | TLS/удалённый TCP/cancellation и проверенный профиль приложения с явными SQL dependencies |

BEGIN/START TRANSACTION/RETAINING/$n и SQL, который parser пока не поддерживает, не входят в успешный profile. Native retaining проверяется через `COMMIT/ROLLBACK [WORK] RETAIN [SNAPSHOT]`. Прежние M0/P3 результаты и SELECT-only guards являются историей в VALIDATION; текущие RETURNING/blocks проверяются положительно при поддержанных metadata. Удалённый native metadata executable не является зависимостью harness.

Минимальные серверные gates сохраняются независимо от внутреннего способа classification:

| ID | Проверка и критерий |
|---|---|
| M0-01 | Native baseline собирается и обслуживает TCP при пустых WireProtocolPlugins/Ports |
| M0-02 | PgWire загружен отдельной библиотекой в firebird; review и connect trace исключают backend network hop |
| M0-03 | Authentication сохраняет CURRENT_USER и privileges; bad password не даёт рабочего attachment |
| M0-04 | C libpq PQexec и psycopg2 autocommit без connection context возвращают вычисляемый 42/OID 23 |
| M0-05 | Настоящий parser error и следующий успешный query на том же connection; BEGIN не эмулируется |
| M0-06 | Отсутствующий module/занятый PG port не отключает native listener |
| M0-07 | EOF/incomplete startup/shutdown освобождают attachments/sessions/workers до unload |
| M0-08 | Prepare использует исходный SQL; descriptor и штатные input/output/flags проверяются до execution. Unsupported capabilities не дают частичных эффектов, supported RETURNING работает |

## Исторический P5 — целевая Linux/Super приёмка PASS с native ограничением

Финальный transaction harness прошёл 14 групп без skips: I/T/E, raw ReadyForQuery/PQtransactionStatus, visibility через native observer, savepoint/retaining recovery, session SET/RESET, 100 ms DML timeout в I/T, error precedence, SNAPSHOT AUTO COMMIT, NO AUTO UNDO, disconnect и реальные commit/reset errors, C libpq и connect trace. Backend connects — 0; серверы завершились с exit 0. Full build и четыре unit executable (487 classifier checks), Simple Query 12, lifecycle 7, startup 52 и native 9 прошли отдельно. Evidence — в VALIDATION. Это целевой profile, не автоматический PASS всех вариантов T01–T14 и остальной матрицы; P6/M1 ещё не завершён.

Текущий [pgwire_transaction_smoke.py](tools/pgwire_transaction_smoke.py) проверяет AUTO COMMIT DML с SNAPSHOT и отдельно NO AUTO UNDO. Native SNAPSHOT baseline положительный. Дефект AUTO COMMIT с READ COMMITTED READ CONSISTENCY воспроизводит assertion `Savepoint.cpp:833` в restart path через embedded isql без PgWire/server/triggers; этот profile не входит в подтверждённую совместимость и требует повторного теста после native fix. Evidence независимых native repro и точная команда прогона записываются в VALIDATION; core/SQL options для обхода не меняются.

## Исторический P4 после переноса классификации — Linux/Super loopback PASS

P4-ревизия [pgwire_simple_query_smoke.py](tools/pgwire_simple_query_smoke.py) прошла 12 групп, включая raw TCP, C libpq, psycopg2 autocommit, lexical boundary, native AUTOTERM и trace. [pgwire_lifecycle_smoke.py](tools/pgwire_lifecycle_smoke.py) прошёл 7 групп, startup/native regressions — 52/9 checks. Full make и четыре unit executable прошли; SQL classifier — 284 assertions. Версии: C headers/runtime libpq 14.24, psycopg2 2.9.11/libpq 17.6. Backend network connects — 0, все серверы завершились с exit 0. Evidence и ограничения — в [VALIDATION.md](VALIDATION.md). Прежние negative expectations controls в P4-08/10 заменяются P5 positive/state checks; остальная regression сохраняется.

| ID | Проверка и критерий |
|---|---|
| P4-01 | Ограниченный lexer: comments, mixed case, CTE, quoted keyword identifiers, literals с semicolon/BEGIN/$n; исходные SQL bytes передаются prepare неизменёнными |
| P4-02 | SELECT/DML/MERGE/upsert/RETURNING/EXECUTE BLOCK/PROCEDURE: command tag от plugin descriptor, cursor flag и output metadata выбирают API; actual affected records не заменяются rowcount |
| P4-03 | CREATE/ALTER/DROP/RECREATE/CREATE OR ALTER, GLOBAL TEMPORARY TABLE, UNIQUE DESCENDING INDEX; PACKAGE с именем BODY отличается от PACKAGE BODY; правильные tags и реальные engine effects |
| P4-04 | EMPTY/whitespace/closed comments отправляются в prepare; только engine syntax error плюс tokenless признак даёт EmptyQueryResponse. Незаконченный SQL/comment/string, vertical tab и отдельный `;` сохраняют 42601 |
| P4-05 | Native diagnostics и isql AUTOTERM работают без новых marker/getInfo/AST extensions; malformed SQL и batch errors имеют приоритет над unsupported classification |
| P4-06 | Integer/boolean/CHAR/VARCHAR/exact numeric/NULL: OID/alias/UTF8/trailing spaces/INT64_MIN/scale/subtype; table OID/attribute 0, typmod-1 |
| P4-07 | DML 0/1/many, singleton/cursor RETURNING, procedures/blocks; native observer подтверждает commit и rollback |
| P4-08 | Unsupported input/output metadata, controls и UNKNOWN USING получают 0A000 после prepare до execution; sequence guard не меняется, ALTER SESSION RESET не сбрасывает USER_SESSION context |
| P4-09 | Parser 42601, relation 42P01, permission 42501, unique 23505, FK 23503, CHECK 23514, coarse NOT NULL/domain 23000. Реальный commit trigger даёт FATAL/EOF без CommandComplete/Ready, native observer видит rollback |
| P4-10 | C libpq/psycopg2 autocommit, native coexistence, startup/lifecycle/trace; P4 остаётся I-only, explicit controls принимаются отдельно в P5 |

Неподдержанный USING wrapper проверяется после engine prepare: валидный `USING IN AUTONOMOUS TRANSACTION DO ...` получает 0A000. USING без обязательной clause сохраняет native semantic 42000; malformed/batch SQL сохраняет syntax 42601. Эти ошибки не маскируются capability refusal; GEN_ID fixture исключает скрытое частичное execution. Отдельно проверяются следующие успешные queries после recoverable ошибок. Ошибки close/free и engine timeout отмечаются по фактическому evidence.

## P2 — историческая startup приёмка и regression

Целевой Linux/Super P2 startup harness прошёл 52 проверки с libpq 14.24, см. [VALIDATION.md](VALIDATION.md). В том срезе все корректные Query получали 0A000 без engine prepare. P3 заменяет эту заглушку SELECT execution; startup/authentication/cleanup остаются regression checks, query expectations должны соответствовать текущему этапу.

| ID | Проверка и критерий |
|---|---|
| P2-01 | Production PgWire загружен отдельной библиотекой в Linux/Super; native продолжает выполнять SQL. POSIX/CMake definitions не включают PG objects в daemon/Remote_Common; Windows runtime не заявляется |
| P2-02 | Raw startup и реальный libpq/psycopg2 connect: AuthenticationOk только после attach, ожидаемые ParameterStatus/Ready I, без фиктивного BackendKeyData. Исторический P2 давал всем Query 0A000/I; текущие query/recovery expectations проверяются в P4-01–10 |
| P2-03 | Правильные credentials, неверный пароль/unknown user 28P01, unknown/disallowed alias 3D000, RemoteAccess=false и запрещённый Srp256 policy; native observer подтверждает реальные раздельные identities и peer |
| P2-04 | ON CONNECT trigger отклоняет вход даже с правильным паролем: ErrorResponse до AuthenticationOk, attachment освобождён; следующий разрешённый connect работает |
| P2-05 | Неполные/coalesced frames, SSL/GSS N, duplicate/unknown startup keys, неправильная version, lengths/NUL/UTF8, границы 16 KiB startup / 1 MiB typed frame / 4096 bytes Password с NUL; bounds проверены до payload allocation |
| P2-06 | User обязателен; absent database=user проходит лишь при разрешённом alias, explicit empty database отвергается. Empty DatabaseAliases запрещает attachment; клиентский path/URI/config не меняет provider route |
| P2-07 | Unit golden bytes, short writes/reads и сохранение partial progress; прежние transport/startup timeout expectations удалены |
| P2-08 | EOF, Terminate, failed auth, idle и incomplete startup при shutdown: attachments/FDs освобождены, module жив до возврата callbacks. MON$REMOTE_PROTOCOL=TCPv4, MON$REMOTE_VERSION=endpoint alias; password отсутствует в diagnostics |

Auth helper отдельно ограничен 16 rounds и 64 KiB на payload. Проверяются UTF8 и host bounds alias/user/application_name 255 bytes, нормализованный Srp256 login 63 bytes; Password codec limit включает NUL. Дополнительных сетевых/auth timers нет. Такой вызов проверяется с provider shutdown и fault-injection отдельно; PASS idle transport не доказывает preemption auth plugin. Наряду с live harness выполнить code review engine-only provider route и наблюдение server connect(), чтобы исключить дополнительный backend hop.

## Fixtures и свидетельства

- Disposable БД dialect 3/UTF8, alias `pgwire_test`, локальная security database и проверенный Srp256 profile. Начальный режим — Linux/Super, PgWire `127.0.0.1:15432`.
- Native observer — отдельный настоящий TCP-клиент к той же БД, например `inet://127.0.0.1:3050/pgwire_test`. Его исходящие sockets отделяются от sockets сервера.
- Пользователь `PGWIRE_TEST` создаёт собственные объекты; `PGWIRE_LIMITED` не имеет доступа к ним. Отдельный административный fixture нужен для monitoring shutdown/timeout cases.
- Alias allowlist, `RemoteAccess=false`, неподходящий auth profile и missing plugins проверяются отдельными server configs. Тесты не меняют рабочий сервер пользователя.
- Для cross-visibility observer начинает новую транзакцию; DDL fixtures коммитятся до дальнейшего DML. Cleanup удаляет только объекты текущего теста.
- Лимиты connections/workers/frames/queues/buffers, независимые native statement/lock settings и общий shutdown deadline записываются в отчёте.
- Credentials передаются через отдельное окружение; DSN, command output, status vectors и трассы не должны раскрывать пароль.

Для каждого этапа нужны разные уровни evidence: unit tests codec/state/metadata; прямой libpq и Python client; native observer; process/build/provider/lifecycle checks. Совпадение `MON$SERVER_PID` само по себе не исключает gateway.

## A. Plugin host, конфигурация и сборка

| ID | Сценарий | Критерий |
|---|---|---|
| A01 | Сборка/установка/загрузка | Generic host входит в server target; PostgreSQL-specific objects находятся в отдельной plugin library, а не в daemon или Remote_Common; экспортирован штатный entrypoint |
| A02 | Пустые default lists | Ни additional listener, ни его workers/attachments не появляются; native regression проходит |
| A03 | Один тестовый module/factory, два configured aliases `StubA`, `StubB` с разными Config/ответами | Созданы отдельные instances; alias точно соответствует своему positional port/config, mutable state изолирован. Две библиотеки и второй СУБД-протокол не требуются |
| A04 | `StubA, MissingPlugin, StubB` и три порта; StubA/StubB используют один module/factory | Средний endpoint диагностирован как недоступный; StubB остаётся на третьем порту со своей Config. Первая и родная службы работают; manager не сдвигает позиции и не переиспользует ошибочную instance |
| A05 | Разная длина/один пустой список/пустая позиция/trailing comma | Вся некорректная дополнительная конфигурация отклонена до загрузки модулей; native работает |
| A06 | Порт 0, отрицательный, нечисловой, overflow, >65535, дубликат/конфликт native | Предсказуемый отказ без автоматической подмены порта; native listener не закрывается |
| A07 | Missing/bad module, wrong factory/type, exception в factory, bind failure | Ошибка содержит plugin/port/reason без секретов; частичные resources освобождены; другие корректные endpoints доступны |
| A08 | `plugins.conf` Module/RegisterName | Файл с отличающимся именем загружается по логическому имени; неверный RegisterName даёт явный отказ |
| A09 | Startup integration | Один listener для каждой настроенной пары alias/port в server process, без повторного bind на других startup paths; нет второго `SRVR_multi_thread()`, native PACKET/XDR или startup в client library |
| A10 | Provider/network route | Review actual call path и наблюдение исходящих server `connect()` при auth/SQL: engine-only, без Remote/Loopback/native TCP/Unix backend |
| A11 | Super, SuperClassic, Classic | Super тестируется явно; SuperClassic не считается автоматически покрытым Super. Неподдержанные modes диагностируются и сохраняют native |
| A12 | Другие build paths | CLOOP bindings согласованы; CMake/Windows имеют отдельные build results. Проектный переносимый ABI не выдаётся за проверенную DLL |
| A13 | Разные bind/TLS/protocol settings через Config двух aliases | Каждый instance получает свою секцию Config. Сохраняются только два positional списка plugins/ports; host проверяет bind/TLS policy. До M3 remote/TLS настройки не включают необъявленную capability; с M3 проверяются разные сертификаты/policies без переноса настроек между endpoints |
| A14 | Штатный getPluginConfig: module `.conf` fallback для первого alias, named Config для второго | Оба endpoint получают ожидаемые host settings и различимые ответы plugin. Отсутствие named Config само по себе не является ошибкой; NULL getter отклоняет endpoint. Заданные host settings проверяются на тип, диапазон и повтор независимо от источника |

Strict positional validation проверяется отдельно от PluginManager iterator: optional module skipping не должен менять mapping. Конфигурационные ошибки не проверяют изоляцию от segfault в доверенном плагине: весь код работает в одном процессе.

Для A04 дополнительно проверяется отказ implicit Config::getPlugins(TYPE_WIRE_PROTOCOL) и direct loader name с запятой до module entrypoint. Для native-conflict части A06 нужны startup cases с явным RemoteServicePort, override `-p`, RemoteServiceName, отключённым TCP и пересекающимися bind/dual-stack адресами; конфликтный endpoint не должен вызывать module entrypoint, native продолжает работать. Повторный IPv4 loopback smoke с override `-p` и штатным getPluginConfig записан в VALIDATION; остальные варианты требуют отдельных запусков.

Конфигурация подготавливается до запуска через стандартные средства Firebird. Для разных aliases рекомендуется named Config. Приёмка host не вводит отдельную проверку структуры Plugin/Config blocks или новых правил разрешения конфигурации; она проверяет настройки, возвращённые существующим getter, их defaults и совместную работу endpoint/native.

## S. Authentication и identity

| ID | Сценарий | Критерий |
|---|---|---|
| S01 | Правильный/неправильный пароль, неизвестный пользователь | Правильный вход либо startup `28P01`; AuthenticationOk только после attach; нет рабочего user attachment после отказа |
| S02 | Auth rounds и ошибочные plugin statuses | Не более 16 rounds и 64 KiB на payload, cleanup, без trusted/OS/service-account fallback |
| S03 | Srp256 запрещён per-database policy, security DB недоступна или не имеет Engine14 в Providers | Диагностируемый отказ; native auth configuration не подменяется |
| S04 | CURRENT_USER, concurrent users, denied SELECT | Реальные разные identities; `42501` без GRANT; credentials/auth blocks не переходят между sessions |
| S05 | Alias allowlist, неизвестный alias, paths/URIs/options | Только server-authorized local database; неизвестный alias `3D000`; клиент не внедряет DPB/config/trusted identity |
| S06 | RemoteAccess=false и peer metadata | Отказ удалённому клиенту; реальный peer, MON$REMOTE_PROTOCOL=TCPv4 и MON$REMOTE_VERSION=endpoint alias не маскируют TCP origin как embedded |
| S07 | Native auth и затронутые EDS paths | Native regression обязательна; при выделении helper из EDS сохранить его positive/negative/precondition cases |

Startup SQLSTATE проверять raw ErrorResponse field `C`: `psycopg2.connect()` может бросить OperationalError без `pgcode`. Внутренние security attachments/caches учитывать отдельно от session-owned application attachments.

## W. Framing и SQL boundary

| ID | Сценарий | Критерий |
|---|---|---|
| W01 | Startup без tag, typed messages, fragmentation по каждой границе, coalesced frames | Точные lengths/network order, каждый frame обработан один раз, transport read size не ограничивает размер frame |
| W02 | Partial write, slow reader, EOF в header/payload/password | Ограниченная память, отсутствие ложного успеха, cleanup только нужной session |
| W03 | Invalid/overflow/oversized length, NUL/хвост, UTF8 | Отказ до опасной allocation или исполнения; SQL не усекается |
| W04 | Startup 3.0/неподдержанные version/options, SSLRequest/GSSENCRequest | Реальный профиль: до TLS ровно `N`; неизвестные обещания не подтверждены успешным startup |
| W05 | Независимые golden bytes результатов/errors | OID, lengths, NULL=-1, empty=0, required ErrorResponse fields, ReadyForQuery и tags; не только собственный encode→decode |
| W06 | Native keepalive/crypt/compression и parallel sessions | PG stream не получает native bytes; framing/auth/SQL state разных sessions независимы |
| W07 | Байты SQL на входе и в parser | Instrumented boundary сверяет bytes/length для comments, quotes, semicolons, whitespace и UTF8; wire frontend не нормализует и не переписывает SQL |
| W08 | EMPTY и malformed SQL | Исходный SQL проходит engine prepare; только tokenless closed-comments/whitespace + syntax 42601 даёт EMPTY. Незаконченные tokens, vertical tab и отдельный `;` остаются errors; non-syntax errors сохраняются |
| W09 | `BEGIN`, `START TRANSACTION`, `COMMIT RETAINING`, `$1`, `::date` и похожие tokens внутри literals/comments | Engine syntax errors в неподдержанных позициях; внутри literals/comments смысл определяет parser. Ошибка в T переводит в E; в E syntax сохраняет 42601. Native RETAIN [SNAPSHOT] проверяется положительно, frontend replacement отсутствует |
| W10 | Несколько statements в одном Query/Parse | Parser отвергает неподдержанный batch без частичного исполнения; `;` внутри совместимого SQL не разделяется frontend |
| W11 | Native callers и isql -autoterm | Штатные parser diagnostics, continuation и trace не меняются; PgWire не добавляет public EMPTY marker или metadata API |

W07 требует code review неизменной передачи SQL и TCP fixtures с comments/quotes/CTE/semicolons. Ограниченный plugin lexer проверяется отдельно: он задаёт descriptor/EMPTY, а engine по-прежнему валидирует каждый SQL. Unsupported wire messages получают protocol/capability error; ожидания уточняются при Extended/TLS/cancel.

## Q/T. DSQL metadata, выполнение и транзакции

Используются штатные getType/getFlags/input/output/affected records и plugin-local command/control descriptor. Новые public engine metadata, parser observer и pre-prepare analysis API не требуются. P5 E-state использует обычный prepare и реальный transaction handle; следующие проверки обязательны для его acceptance и дальнейшего M1.

| ID | Сценарий | Критерий |
|---|---|---|
| Q01 | Вычисляемые SELECT, 0/1/many rows, aliases | Реальные значения, правильная description и SELECT row count |
| Q02 | CREATE/DROP/ALTER и другие доступные DDL | Tag задаётся ограниченным plugin descriptor после engine prepare; изменение видно native observer после commit |
| Q03 | INSERT VALUES/SELECT, UPDATE, DELETE, MERGE, UPDATE OR INSERT с/без RETURNING | Реальные rows и affected counts, tag исходной операции; cursor не превращает DML в SELECT, coarse INSERT type не маскирует MERGE |
| Q04 | Supported SQL с unsupported input/output capability | Отказ по metadata до side effects; не blanket rejection синтаксической формы RETURNING |
| Q05 | SET TRANSACTION, COMMIT/ROLLBACK RETAIN [SNAPSHOT], SAVEPOINT и session operations | Plugin-local descriptor различает actions после engine prepare и сверяется с legacy type. Tags: BEGIN/COMMIT/ROLLBACK/SAVEPOINT/RELEASE/SET/RESET; фактический effect и transaction handle контролируются |
| Q06 | Native/PG cross-visibility в обе стороны | Одна база, штатные права и commit/rollback; native клиент не участвует в PG execution path |
| Q07 | Prepare/fetch/execute/commit errors | Нет ложного CommandComplete; исходная ошибка не затёрта cleanup; uncertain transaction state завершает connection |
| T01 | Обычный implicit Query | Prepare с NULL, затем READ WRITE / READ COMMITTED READ CONSISTENCY / WAIT TPB без заданного плагином lock timeout. Commit до CommandComplete, rollback после ошибки, state I; retaining не подменяет завершение |
| T02 | Native SET TRANSACTION из I, native COMMIT/ROLLBACK | Реальная transaction, T/I, isolation/read-only и cross-session visibility. Повторный SET TRANSACTION при active handle — 25001/E с сохранением transaction |
| T03 | Ошибка в explicit transaction | Native handle сохраняется, state E; успешно подготовленные обычный SQL/SAVEPOINT/RELEASE/COMMIT RETAIN получают 25P02 без execution и открытия новой transaction. Ошибки самого prepare имеют precedence из T11 |
| T04 | Полный COMMIT/ROLLBACK из E | COMMIT выполняет rollback и возвращает ROLLBACK/I; ROLLBACK также завершает transaction; ещё незафиксированные изменения не сохранены; native AUTO COMMIT проверяется отдельно T09 |
| T05 | Native COMMIT/ROLLBACK [WORK] RETAIN [SNAPSHOT] в T/E/I | В T retaining остаётся T с реальным commit/rollback; E + ROLLBACK RETAIN восстанавливает T; E + COMMIT RETAIN даёт 25P02/E; в I RETAIN — 25000/I. Lifecycle savepoints сверяется с native API |
| T06 | SAVEPOINT → изменение → ошибка → ROLLBACK TO → продолжение; RELEASE [ONLY] | Rollback-to возвращает T и сохраняет прежние изменения; неверный savepoint даёт error/E. Имена в quotes и имена RETAIN/WORK не путаются с controls. RELEASE SAVEPOINT name [ONLY] имеет native effect; все savepoint actions в I дают 25000/I |
| T07 | Полный COMMIT/ROLLBACK в I | Подготовленный native control и локальный descriptor дают no-op/I, optional notice |
| T08 | Disconnect с незавершённой transaction | Rollback и освобождение lock; native observer подтверждает отсутствие uncommitted changes |
| T09 | Native SNAPSHOT AUTO COMMIT / NO AUTO UNDO | Lexer не сканирует опции; SNAPSHOT AUTO COMMIT проверяется native/PG visibility и сохранением прежних commits после rollback. NO AUTO UNDO/savepoint recovery проверяется независимо. AUTO COMMIT + READ COMMITTED READ CONSISTENCY имеет отдельный native Savepoint assertion и не входит в заявленное покрытие |
| T10 | ALTER SESSION RESET в I/T/E, включая ошибку reset | Проверяются сброс USER_SESSION/settings, rollback и новая native transaction при возможном прежнем interface pointer. В T сохраняется T, в I — I, в E — 25P02 без reset. P5 не кэширует statements/portals/savepoint names; M2 caches проверяются отдельно. Invalid attachment/handle после reset требует FATAL/close |
| T11 | Prepare в E: actual missing relation, syntax, permission, cancel/timeout, fatal и unknown error | Только native isc_dsql_relation_err с итоговым nonfatal 42P01 даёт 25P02; explicit SQLSTATE 42P01 без причины не входит в whitelist. Syntax 42601 и permission 42501 сохраняются. Отмена/timeout, shutdown/invalid handle, resource/I/O и unknown errors не маскируются. Проверяются full-vector priority и отсутствие execution |
| T12 | Число current application transactions и phase boundaries | QueryExecutor владеет максимум одним handle; prepare в I получает NULL, в T/E — current и видит uncommitted DDL. Нет cached statement transaction. Engine system/security/autonomous transactions отделяются при подсчёте; native и другие plugins этим правилом не ограничиваются |
| T13 | Native session SET whitelist в I/T/E | ROLE/TRUSTED ROLE, DEBUG OPTION, DECFLOAT ROUND/TRAPS, idle/statement timeout, TIME ZONE, BIND, OPTIMIZE, SEARCH_PATH: settings реально меняются и выдаётся SET, I/T сохраняются; в E после successful prepare — 25P02 без effect. Native configuration/session timeout ограничивает DML без дополнительного лимита плагина; проверить 100 ms timeout в I/T, реальный interrupt и recovery. Не заявлять hard deadline prepare/DDL/controls |
| T14 | EMPTY в I/T/E | EmptyQueryResponse сохраняет прежнее состояние и transaction; изменения/savepoints не теряются. Отдельный semicolon и unfinished SQL/comment остаются ошибками, не EMPTY |

Заявленные transaction characteristics и session operations меняют реальное engine state. Неподдержанные descriptor/API/codecs получают отказ после prepare до execution. Legacy getType/getFlags и native diagnostics не изменяются; control classification покрывается отдельными tests вместе с фактическими handles/effects.

T11 не утверждает, что prepare лишён compile/cache/lock/trace/external-initialization effects: проверяется отсутствие исполнения подготовленного SQL и освобождение ресурсов. Редкие resource/I/O failures воспроизводятся контролируемой инъекцией ошибок, без фактического исчерпания ресурсов рабочего сервера.

## D. Типы, строки и диагностика

| ID | Сценарий | Критерий |
|---|---|---|
| D01 | M1 integers, BOOLEAN, CHAR/VARCHAR, exact numeric, NULL | Верные OID/Python types, bounds, INT64_MIN, scale=0 и fractional numeric без float intermediate |
| D02 | UTF8 CHAR, varying fetch metadata, trailing spaces | Значения сверяются с native fetch через SQL_VARYING; нет byte-padding повреждения или rtrim логических пробелов; проверяются ASCII/многобайтные значения, пустое и полностью пробельное CHAR |
| D03 | Quoted aliases, NULL/empty, апостроф/backslash/LF/TAB/semicolon | Metadata и значения не теряются; U+0000 даёт 22021 вместо truncation |
| D04 | M2 FLOAT/DOUBLE, temporal без TZ, INT128/NUMERIC(38), aggregates | OID/диапазоны/precision определяются result metadata; NaN/Infinity policy явна; нет потери exact numeric |
| D05 | Binary input/output | Независимые wire fixtures, endian/epoch/units/lengths; native buffer не копируется напрямую в PG binary |
| D06 | Unsupported type/format или malformed Bind | Capability error до исполнения; действительно неподдержанные types не превращаются в text/NULL |
| D07 | Full ISC status vector с wrapper и nested causes | SQLSTATE выбирается по полной структуре, включая update-conflict/deadlock/constraint causes; не по локализованному тексту и не только первому коду |
| D08 | Syntax, unknown table, unique, permission, login | Проверены 42601/42P01/23505/42501/28P01 и fallback; messages сохраняют смысл и не раскрывают credentials |
| D09 | Native observer удаляет MON$ATTACHMENTS для PG session; отдельно shutdown базы | FATAL 57P01 и закрытие PG connection; target не выполняет последующий SQL |
| D10 | Native observer отменяет MON$STATEMENTS или срабатывает statement timeout | 57014 в запросе, корректные I/E, освобождение statement/cursor и возможность предусмотренного recovery |
| D11 | Engine idle timeout | Attachment закрыт с rollback; следующий Query получает FATAL 57P05/close, без повторного attach и периодических engine calls, продлевающих idle timer |

Для D09/D11 delivery contract: active operation получает terminal error при возврате из engine; для idle socket обязательна доставка на следующем запросе, если writable channel сохраняется. Harness после administrative action/timeout посылает запрос и проверяет raw ErrorResponse и close; поддержанная eager notification может прийти раньше с тем же кодом. Транспортный обрыв при уже закрытом socket фиксируется отдельно и не подменяет проверку mapping на рабочем канале. Если engine callback позволяет иной контракт, его сначала зафиксировать в CONTEXT и согласовать тест, а не обещать unsolicited сообщения без реализации.

## E. Extended Query с совместимым SQL

Прямой C libpq harness фиксирует `PQlibVersion`, compile headers, OS и SHA. Он использует `PQexecParams`/`PQprepare` с нулём параметров и с native SQL `SELECT CAST(? AS INTEGER) FROM RDB$DATABASE`; libpq передаёт текст серверу. Это проверяет wire Bind, но не поддержку PostgreSQL `$n`.

| ID | Сценарий | Критерий |
|---|---|---|
| E01 | PQexecParams без параметров и с native `?` | Реальное execution через Parse/Bind/Execute/Sync; input metadata и значения согласованы |
| E02 | PQprepare/PQexecPrepared, OID=0/явные OID, NULL/text/binary | Повторное исполнение с новыми значениями, корректные inference/ParameterDescription, format counts 0/1/N и mismatch errors |
| E03 | Named/unnamed objects, replacement, Close, Describe | Правильные lifetimes, NoData/RowDescription и parameter metadata; statement способен пережить commit по контракту |
| E04 | Несколько portals, Execute maxRows, PortalSuspended | Нет потерь/дубликатов; buffers/cursors/formats изолированы; CommandComplete только по завершении |
| E05 | Parse/Bind/Execute error, последующие сообщения до Sync | Discard-until-Sync, отсутствие side effects отброшенных команд, затем recovery |
| E06 | Flush без Sync, несколько Execute до Sync | Ответы доступны, implicit commit и ReadyForQuery появляются на правильной границе; ошибка откатывает implicit cycle |
| E07 | Sync внутри native explicit transaction, Simple/Extended switching | Состояние T/E сохраняется; unnamed invalidation и resources соответствуют контракту |
| E08 | PQsendQueryParams/PQsendQueryPrepared/PQgetResult | Async/nonblocking API без pipeline, правильный конец результатов и errors |
| E09 | Limits, EOF/shutdown с prepared objects и suspended portal | Ограниченная память, освобождение всех objects, module lifetime сохраняется |
| E10 | Обычный Psycopg 3 с параметрами `%s` → `$n` | Пока parser не расширен, зафиксированный syntax failure; не объявлять такую библиотеку параметризованно совместимой |
| E11 | Несколько Parse/Describe/statements/portals перед ALTER SESSION RESET | Prepare использует текущую application transaction либо NULL/system prepare; собственных transactions у prepared objects нет. RESET не получает ошибку из-за скрытой prepare-transaction, корректно заменяет текущую transaction, инвалидирует portals/savepoints; следующий запрос работает |

После отдельной поддержки `$n` в parser добавить repeated/reordered indices, gaps/overflow, references в literals/comments и обычный Psycopg 3 Cursor с переходом к named preparation. Эти проверки обязательны для будущего заявления о таком клиенте. ClientCursor и клиентская подстановка не заменяют Extended Query. Pipeline и COPY требуют самостоятельных матриц, если входят в профиль приложения.

В E11 проверить оба входа: несколько Parse/Describe из I без собственной transaction на statement и те же операции при уже открытой explicit transaction. Создание implicit cycle допускает только один общий current handle. Сравнить ownership counters и native monitoring, учитывая внутренние engine transactions отдельно; одинаковый interface pointer после RESET не доказывает прежнюю underlying transaction.

## L. Resource ownership и остановка

| ID | Сценарий | Критерий |
|---|---|---|
| L01 | Серии connect/SQL/error/disconnect после warmup | Session attachments, transactions, cursors, plugin sessions и FDs возвращаются к baseline; documented caches выделены отдельно |
| L02 | Малформированный/медленный клиент и исчерпание лимита | Другие PG sessions и native обслуживаются; очереди/память ограничены |
| L03 | Shutdown в accept, idle read, password exchange, incomplete frame/write | Readers/writers разбужены, workers завершены, registry освобождён |
| L04 | Shutdown active SQL/lock wait | Lock timeout намеренно больше cleanup deadline; shutdown всё равно ограничен и не ждёт естественного lock timeout |
| L05 | Provider phases | Нет бесконечного join в preproviders; cancellation позволяет providers завершиться; после shutdown не запускаются engine operations |
| L06 | Library lifetime | Инструментированный plugin подтверждает, что callbacks/sessions завершены до releasePlugin/module cleanup; нет direct dlclose и use-after-unload |
| L07 | Частичная ошибка создания session/plugin и repeated stop | Идемпотентный cleanup, нет double release; initial status не затёрт вторичной ошибкой |

Подходящее свидетельство выбирается под риск: ownership counters, monitoring, FDs, инструментированный тестовый plugin, при необходимости sanitizer. Только отсутствие lock не доказывает отсутствие утечек. Проверять cleanup и worker completion независимо от завершения клиентского процесса.

Исходный тестовый профиль T08/L04: cleanup deadline 5 секунд, lock timeout конкурирующей transaction 15 секунд, общий client deadline 30 секунд. Значения фиксируются в fixture и отчёте; при адаптации к CI сохраняется строгий порядок cleanup deadline < lock timeout < client deadline. Это устраняет гонку между Terminate cleanup и проверкой освобождённого ключа.

## R/C/U. Удалённый профиль, TLS, отмена и приложение

| ID | Сценарий | Критерий |
|---|---|---|
| R01 | Отдельный узел/network namespace | Реальная TCP-топология зафиксирована; listener и engine в одном firebird |
| R02 | SSLRequest → TLS, sslmode=verify-full | Проверены trust/hostname, неверный/истёкший сертификат и отказ handshake; выбранная в P0 TLS dependency отражена в build |
| R03 | Non-loopback без TLS; partial/slow TLS | Password не передаётся в открытом канале; shutdown прерывает handshake, без вмешательства native WireCrypt |
| C01 | BackendKeyData/CancelRequest при SQL и lock wait | 57014 в основном connection, безопасная cancellation ownership, correct I/E |
| C02 | Invalid/stale/чужой key; races с complete/detach | Чужая session не отменяется; cancel socket не создаёт application attachment; нет UAF/hang |
| U01 | Startup/session commands выбранного приложения | Фиксированы driver/libpq/version/options и trace без секретов; SQL понимает engine parser, settings имеют реальный эффект |
| U02 | Неизменённый драйвер, сценарии приложения, pooling если нужен | Все обязательные операции работают либо их parser dependencies явно блокируют приёмку; специальный клиентский режим не скрывается |
| U03 | Итоговые native/PG concurrency и regression | Cross-visibility, privileges, transaction/resource/lifecycle gates повторены для итогового профиля |

Приложение пока не выбрано. Если его driver автоматически посылает `BEGIN`, `$n`, `SET` или catalog queries, эти реальные dependencies нельзя исключить из acceptance. Их неподдержка пока ограничивает применимость endpoint, хотя совместимый SQL уже работает. Исходный Python helper принудительно использует loopback и `sslmode=disable`; R/C/U требуют отдельного harness.

## Миграция Python scaffold

Исходное число 56 не является обязательной целью новой приёмки. Перед execution пересмотреть конкретные node IDs и зафиксировать migration diff:

| Исходная группа | Изменение |
|---|---|
| `_pg_connection(autocommit=False)` и тесты implicit BEGIN | Перевести compatible-wire fixtures в `autocommit=True`; explicit transactions проверять передачей native SQL. Отдельный negative test фиксирует реальный отказ driver-generated BEGIN |
| `test_connection_context_with_autocommit` | `with conn` способен послать BEGIN даже при autocommit=True; прежний positive expectation откладывается до parser support, вместо него проверить/документировать отказ без утраты данных |
| Scalar/CRUD/error/permissions/native roundtrip | Сохранить смысл, обновить fixtures и сопоставить Q/D/S gates; compatible SQL должен проверяться положительно |
| `test_returning_rejected_before_changes` | Удалить blanket rejection RETURNING; заменить позитивными rows/tags/counts для поддержанных codecs и отдельным capability refusal без side effects |
| `test_unsupported_output_types` | Negative только для реально отсутствующего codec; реализованные temporal/floating/INT128 cases становятся positive |
| `test_python_date_adapter_remains_unsupported_sql` | Syntax boundary `::date` сохраняется независимо от появления DATE codec |
| Batch/error/disconnect | Сохранить соответствующий parser/transaction/cleanup смысл, сохранить ограниченную lexical classification и engine validation; запретить rewrite/splitting |

Не делать blanket skip/xfail legacy suite и не требовать одновременно взаимоисключающие positive/negative outcomes. Будущие parser-dependent client tests имеют отдельную отмеченную dependency и не считаются выполненными. Недостающий сервер/fixture в выбранной integration job является ошибкой, а не успешным skip.

Пример окружения для будущего запуска, без настоящих секретов:

```sh
export PGWIRE_TEST_DSN='host=127.0.0.1 port=15432 dbname=pgwire_test user=PGWIRE_TEST'
export PGPASSWORD='<пароль тестового пользователя>'
export PGWIRE_LIMITED_DSN='host=127.0.0.1 port=15432 dbname=pgwire_test user=PGWIRE_LIMITED'
export PGWIRE_LIMITED_PASSWORD='<пароль ограниченного пользователя>'
export FB_NATIVE_DSN='inet://127.0.0.1:3050/pgwire_test'
export FB_NATIVE_USER='PGWIRE_TEST'
export FB_NATIVE_PASSWORD='<пароль native observer>'
export FB_ISQL='/absolute/path/to/firebird/isql'
```

Python dependencies закреплены в `requirements-tests.txt`; фактический runtime libpq записывать отдельно. Из каталога пакета подготовительная проверка выполняет `python -m py_compile tests/test_pgwire_acceptance.py` и `python -m pytest --collect-only -q`. После миграции запуск выбранного integration набора оборачивается во внешний deadline, например `timeout 180s python -m pytest -q -m integration`. `connect_timeout` не ограничивает зависший SQL; native subprocess и серверные ожидания также имеют собственные deadlines.

## Отчёт VALIDATION

Отдельно записать SHA/build/OS/ServerMode, module filename и configured name/port, limits, driver/header/runtime versions, реальные commands и результаты. Для каждой строки выбранного этапа указать test/node ID, evidence и pass/fail/not-run; collection, unit и live integration отделяются.

Обязательно приложить A01/A10 process/provider evidence, auth/identity/policy, parser byte/metadata checks, native visibility, shutdown/module lifetime и capability matrix. Указать точный принятый этап и оставшиеся parser/client dependencies. Работающий SELECT, положительный probe, собранные tests или несколько вручную выбранных driver calls не заменяют обязательные gates.
