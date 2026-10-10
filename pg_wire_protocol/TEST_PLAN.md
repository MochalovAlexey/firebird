# План приёмки wire plugins и PostgreSQL endpoint

База — `master` Firebird 6, SHA из [BASELINE.md](BASELINE.md). Архитектура и SQL boundary определены в [ARCHITECTURE.md](ARCHITECTURE.md), transaction contract — в [CODEX_CONTEXT.md](CODEX_CONTEXT.md), заявляемые возможности — в [COMPATIBILITY.md](COMPATIBILITY.md).

Исходный Python scaffold содержит 20 тестовых функций / 56 параметризованных случаев и требует миграции. Начальные проверки находятся в `src/common/tests/WireProtocolConfigTest.cpp`, `src/remote/server/wire/tests` и изолированных harness в `tools`. Plugin runner проверяет loading/ownership с mock transport; отдельные socket tests используют POSIX sockets. `wire_host_smoke.py` запускает Linux/Super с native SQL и двумя TCP echo endpoints из настоящей тестовой библиотеки: проверяются конфигурация, workers/queues, timeout и server shutdown. PostgreSQL raw-wire/engine suites ещё предстоит реализовать; перечисление проверки в этом документе не означает, что она выполнена. Результаты в VALIDATION.md относятся к указанным там ревизиям; после перехода host на штатный getPluginConfig требуется повторный прогон. Driver-only probe на синтетическом сервере и pytest collection не являются приёмкой Firebird.

## Этапы и обязательный минимум M0

| Этап | Принимаемый результат |
|---|---|
| M0 | Динамический PgWire внутри `firebird`, реальная authentication, вычисляемый SELECT и минимальный дополнительный engine kind, ошибка/recovery, native coexistence и завершение ресурсов |
| M1 | Simple Query с совместимым SQL, metadata-driven execution, базовые codecs, native transaction controls, server/auth/lifecycle приёмка |
| M2 | Extended Query transport, prepared statements/portals и Bind с совместимыми native placeholders; расширенные codecs |
| M3 | TLS/удалённый TCP/cancellation и подтверждённый профиль конкретного приложения с явно указанными SQL dependencies |

Поддержка обычных параметризованных PostgreSQL-клиентов с `$n` зависит от будущего расширения parser. Ни M2 transport, ни специальный совместимый SQL fixture не доказывают прозрачную работу такого приложения.

Малый обязательный набор M0 выполняется раньше полной матрицы:

| ID | Проверка и критерий |
|---|---|
| M0-01 | Native baseline собирается и принимает TCP connect/SELECT/disconnect при пустом `WireProtocolPlugins`; дополнительных sockets нет |
| M0-02 | PgWire загружен как `.so`; generic host и оба listeners принадлежат одному `firebird`; review и наблюдение `connect()` исключают backend hop |
| M0-03 | Правильные credentials открывают разрешённую БД с проверенным `CURRENT_USER`; неправильный пароль отклонён, следующий правильный connect работает |
| M0-04 | C libpq `PQexec` и psycopg2 с явно заданным `autocommit=True`, без `with conn`, получают 42 и OID 23 для вычисляемого SELECT |
| M0-05 | Ошибочный SQL получает engine-derived error; следующий SELECT на том же connection работает; `BEGIN` не получает фиктивного успеха |
| M0-06 | Отсутствующий PgWire/занятый дополнительный порт дают диагностику, native connect продолжает работать |
| M0-07 | EOF, незавершённый startup и остановка сервера освобождают sessions/workers; код библиотеки не выгружается до завершения её вызовов |
| M0-08 | Минимальный дополнительный getInfo item отличает обычный SELECT от cursor-producing DML RETURNING и SELECT_BLOCK. Последние пока получают 0A000 до execution, native observer подтверждает отсутствие изменений. Legacy getType/flags сохраняются; весь descriptor P4/P5 для M0 не требуется |

Пример M0 SQL: `SELECT CAST(6 * 7 AS INTEGER) AS "answer" FROM RDB$DATABASE`. Второе выражение с другим результатом либо запрос к тестовой таблице исключает hardcoded answer.

Полная DML/DDL/control metadata, E-state и EMPTY относятся к P4/P5 и M1. Фильтр только `getType == SELECT` плюс `FLAG_HAS_CURSOR` не проходит M0-08: RETURNING/SELECT_BLOCK могут иметь те же признаки. Ограниченный M0 не превращается в постоянный SQL allowlist M1.

## Fixtures и свидетельства

- Disposable БД dialect 3/UTF8, alias `pgwire_test`, локальная security database и проверенный Srp256 profile. Начальный режим — Linux/Super, PgWire `127.0.0.1:15432`.
- Native observer — отдельный настоящий TCP-клиент к той же БД, например `inet://127.0.0.1:3050/pgwire_test`. Его исходящие sockets отделяются от sockets сервера.
- Пользователь `PGWIRE_TEST` создаёт собственные объекты; `PGWIRE_LIMITED` не имеет доступа к ним. Отдельный административный fixture нужен для monitoring shutdown/timeout cases.
- Alias allowlist, `RemoteAccess=false`, неподходящий auth profile и missing plugins проверяются отдельными server configs. Тесты не меняют рабочий сервер пользователя.
- Для cross-visibility observer начинает новую транзакцию; DDL fixtures коммитятся до дальнейшего DML. Cleanup удаляет только объекты текущего теста.
- Лимиты connections/workers/frames/queues/buffers, startup/write/statement/lock deadlines и общий shutdown deadline записываются в отчёте.
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

Для A04 дополнительно проверяется отказ implicit Config::getPlugins(TYPE_WIRE_PROTOCOL) и direct loader name с запятой до module entrypoint. Для native-conflict части A06 нужны startup cases с явным RemoteServicePort, override `-p`, RemoteServiceName, отключённым TCP и пересекающимися bind/dual-stack адресами; конфликтный endpoint не должен вызывать module entrypoint, native продолжает работать. Результаты прежнего IPv4 loopback smoke с override `-p` записаны в VALIDATION для соответствующей ревизии; текущий вариант требует повторной проверки, остальные варианты — отдельных запусков.

Конфигурация подготавливается до запуска через стандартные средства Firebird. Для разных aliases рекомендуется named Config. Приёмка host не вводит отдельную проверку структуры Plugin/Config blocks или новых правил разрешения конфигурации; она проверяет настройки, возвращённые существующим getter, их defaults и совместную работу endpoint/native.

## S. Authentication и identity

| ID | Сценарий | Критерий |
|---|---|---|
| S01 | Правильный/неправильный пароль, неизвестный пользователь | Правильный вход либо startup `28P01`; AuthenticationOk только после attach; нет рабочего user attachment после отказа |
| S02 | Auth rounds и ошибочные plugin statuses | Ограниченный обмен в памяти, cleanup, без trusted/OS/service-account fallback |
| S03 | Srp256 запрещён per-database policy или security DB недоступна | Диагностируемый отказ; native auth configuration не подменяется |
| S04 | CURRENT_USER, concurrent users, denied SELECT | Реальные разные identities; `42501` без GRANT; credentials/auth blocks не переходят между sessions |
| S05 | Alias allowlist, неизвестный alias, paths/URIs/options | Только server-authorized local database; неизвестный alias `3D000`; клиент не внедряет DPB/config/trusted identity |
| S06 | RemoteAccess=false и peer metadata | Отказ удалённому клиенту; внутренний attachment не маскирует TCP origin как embedded |
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
| W08 | Пустой текст, whitespace/comment-only, незавершённые SELECT/строка/block comment | С P4 отдельный engine diagnostic означает только чистый EOF без SQL tokens; EmptyQueryResponse сохраняет прежнее I/T/E. Незаконченная строка/комментарий не становится EMPTY даже без предшествующих tokens. Общий isc_command_end_err2 не означает EmptyQueryResponse; parser/lexer errors сохраняются |
| W09 | `BEGIN`, `$1`, `::date` и похожие tokens внутри literals/comments | Пока parser их не поддерживает в соответствующих позициях — engine syntax errors; внутри literals/comments смысл определяет parser; frontend replacement отсутствует |
| W10 | Несколько statements в одном Query/Parse | Parser отвергает неподдержанный batch без частичного исполнения; `;` внутри совместимого SQL не разделяется frontend |
| W11 | EMPTY diagnostic и legacy native callers, включая isql -autoterm | Прежний isc_command_end_err2/continuation/trace contract сохраняется через совместимый marker или opt-in flag. Незаконченный SQL просит продолжение как прежде; пустые/комментированные input не меняют legacy behavior; PG получает EMPTY только по новому явному признаку |

W07 требует и code review, и наблюдаемой границы передачи текста. Одни удачные строки со словом BEGIN не доказывают отсутствие второго lexer. Unsupported wire messages получают реальный protocol/capability error; точные ожидания меняются после реализации Extended/TLS/cancel.

## Q/T. DSQL metadata, выполнение и транзакции

P3/M0 добавляет только минимальный дополнительный kind из M0-08. Полный semantic descriptor реализуется в P4/P5 перед приёмкой M1; `getType()`/cursor flag недостаточно для разных DML/DDL/control forms. Для E используется обычный prepare с текущей native transaction; отдельный pre-prepare analysis API не является gate.

| ID | Сценарий | Критерий |
|---|---|---|
| Q01 | Вычисляемые SELECT, 0/1/many rows, aliases | Реальные значения, правильная description и SELECT row count |
| Q02 | CREATE/DROP/ALTER и другие доступные DDL | Tag выводится из parser/DSQL metadata, без keywords scanner; изменение видно native observer после commit |
| Q03 | INSERT VALUES/SELECT, UPDATE, DELETE, MERGE, UPDATE OR INSERT с/без RETURNING | Реальные rows и affected counts, tag исходной операции; cursor не превращает DML в SELECT, coarse INSERT type не маскирует MERGE |
| Q04 | Supported SQL с unsupported input/output capability | Отказ по metadata до side effects; не blanket rejection синтаксической формы RETURNING |
| Q05 | SET TRANSACTION, COMMIT/ROLLBACK, RETAINING, SAVEPOINT и session-origin operations | Descriptor различает семантику, transaction characteristics/effect; explicit/implicit origin известен session state. Frontend не классифицирует SQL по тексту |
| Q06 | Native/PG cross-visibility в обе стороны | Одна база, штатные права и commit/rollback; native клиент не участвует в PG execution path |
| Q07 | Prepare/fetch/execute/commit errors | Нет ложного CommandComplete; исходная ошибка не затёрта cleanup; uncertain transaction state завершает connection |
| T01 | Обычный implicit Query | Настоящий commit после успешного исполнения, rollback после ошибки, state I; retaining не подменяет завершение |
| T02 | Native SET TRANSACTION из I, native COMMIT/ROLLBACK | Реальная transaction, T/I, isolation/read-only и cross-session visibility. Повторный SET TRANSACTION при active handle — 25001/E с сохранением transaction |
| T03 | Ошибка в explicit transaction | Native handle сохраняется, state E; успешно подготовленные обычный SQL/SAVEPOINT/RELEASE/COMMIT RETAIN получают 25P02 без execution и открытия новой transaction. Ошибки самого prepare имеют precedence из T11 |
| T04 | Полный COMMIT/ROLLBACK из E | COMMIT выполняет rollback и возвращает ROLLBACK/I; ROLLBACK также завершает transaction; ещё незафиксированные изменения не сохранены; native AUTO COMMIT проверяется отдельно T09 |
| T05 | Native RETAINING в T/E/I | В T реальная retaining operation остаётся T; E + ROLLBACK RETAIN возвращает T после rollback retaining; E + COMMIT RETAIN даёт 25P02/E; в I RETAIN — 25000/I |
| T06 | SAVEPOINT → изменение → ошибка → ROLLBACK TO → продолжение | Успешный rollback-to возвращает T и сохраняет изменения до savepoint; неверный savepoint даёт error/E; нельзя незаметно откатить весь transaction |
| T07 | Полный COMMIT/ROLLBACK в I | Parser-derived no-op/I, optional notice; frontend не распознаёт команду самостоятельно |
| T08 | Disconnect с незавершённой transaction | Rollback и освобождение lock; native observer подтверждает отсутствие uncommitted changes |
| T09 | Native AUTO COMMIT / NO AUTO UNDO | Metadata сохраняет выбранные характеристики; visibility/error/savepoints соответствуют native API. Уже совершённые AUTO COMMIT commits сохраняются после последующего rollback; ложной атомарности PG block нет |
| T10 | ALTER SESSION RESET в I/T/E, включая ошибку reset | Engine-derived effect учитывает смену transaction при том же interface pointer; portals/savepoints/session metadata обновлены. В T новая transaction остаётся T, в I остаётся I, в E отказ 25P02. Если reset закрыл attachment — FATAL/close, без ложного recovery |
| T11 | Обычный prepare в E: syntax, semantic, cancel/timeout, shutdown, invalid handle, resource/I/O | Lexer/parser error возвращает 42601/E; известный ordinary semantic failure переводится в 25P02/E по таблице. Отмена/timeout сохраняет свою причину; fatal/invalid-handle/shutdown и resource/I/O не маскируются 25P02, выполняется необходимый cleanup/close. Неизвестный error сохраняет диагностику; отвергнутый statement не выполняет DML/DDL |
| T12 | Число current application transactions в PgWire session | В I/T/E и после control errors session управляет максимум одним handle; нет скрытой второй application transaction. Engine system/security/autonomous transactions отделяются при подсчёте; native protocol и другие host plugins этим правилом не ограничиваются |

Transaction characteristics и session operations, которые объявлены поддержанными, меняют реальное состояние. Unsupported capabilities получают metadata-based refusal до side effects. Native controls не отклоняются целиком только из-за отсутствия frontend recognizer. Проверки statement classification выполняются также для legacy `getType()` и existing callers: новая metadata не должна менять их семантику.

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
| D11 | Engine idle timeout | FATAL 57P05 и закрытие, без повторного скрытого attach |

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
| R03 | Non-loopback без TLS; partial/slow TLS | Password не передаётся в открытом канале; bounded handshake и shutdown, без вмешательства native WireCrypt |
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
| Batch/error/disconnect | Сохранить соответствующий parser/transaction/cleanup смысл, исключить прежний frontend lexical contract |

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
