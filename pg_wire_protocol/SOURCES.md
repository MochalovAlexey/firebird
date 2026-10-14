# Первичные источники и карта исходников

Пакет обновлён 9 октября 2026 года. База — `master` Firebird 6, рабочая ветка `pg_wire_protocol`; SHA и состояние checkout описаны в [BASELINE.md](BASELINE.md). Исследованный upstream [bf94598f8deefeb7cc4a8217ee43b3040d5d6650](https://github.com/FirebirdSQL/firebird/tree/bf94598f8deefeb7cc4a8217ee43b3040d5d6650) задаёт development version 6.0.0.2199. Локальные ссылки ниже ведут в этот workspace; номера строк — навигация по исходному snapshot, symbols остаются основным ориентиром.

Документ перечисляет доказательства и точки расширения, а не задаёт конкурирующий контракт: архитектура находится в [ARCHITECTURE.md](ARCHITECTURE.md), выводы — в [RESEARCH.md](RESEARCH.md). Новый общий wire host использует PluginManager/CLOOP, PG implementation собирается отдельной shared library. SQL разбирает Firebird parser; wire SQL classification/rewriting исключены требованием пользователя.

Граница доказательств: статическое чтение исходников и документации. Работающий host/plugin ещё не подтверждён build/runtime tests. Исторический driver_probe описывает синтетический сервер без Firebird; перенесённый Python suite требует миграции и не является универсальным acceptance gate. Движущиеся URL current/master служат навигацией; исполняемые проверки фиксируют версии и SHA.

## PostgreSQL и драйвер

### S1 — поток протокольных сообщений

[PostgreSQL 18 Message Flow](https://www.postgresql.org/docs/18/protocol-flow.html)

Startup, Simple/Extended Query, транзакционные циклы, допустимое отсутствие BackendKeyData у сторонних реализаций без cancellation.

### S2 — формат сообщений

[PostgreSQL 18 Message Formats](https://www.postgresql.org/docs/18/protocol-message-formats.html)

[PostgreSQL 17 Message Formats](https://www.postgresql.org/docs/17/protocol-message-formats.html)

Для layout protocol 3.0 полезна документация версии 17. PostgreSQL 18 описывает также изменения 3.2; нельзя случайно скопировать новый cancellation layout в реализацию 3.0.

### S3 — версии wire и параметры соединения

[PostgreSQL 18 Protocol Overview](https://www.postgresql.org/docs/18/protocol-overview.html)

[PostgreSQL 18 libpq Connection Parameters](https://www.postgresql.org/docs/18/libpq-connect.html)

Различие Simple/Extended, protocol 3.0/3.2 и поведение max_protocol_version. libpq 18 по умолчанию остаётся на 3.0 при отсутствии требующих нового протокола опций.

### S4 — выполнение через libpq

[libpq Command Execution](https://www.postgresql.org/docs/18/libpq-exec.html)

[libpq Connection Status](https://www.postgresql.org/docs/18/libpq-status.html)

PQexec, PQexecParams, result metadata и влияние standard_conforming_strings.

### S5 — документация Psycopg 2

[Psycopg 2 usage](https://www.psycopg.org/docs/usage.html)

[Psycopg 2 connection](https://www.psycopg.org/docs/connection.html)

[Psycopg 2 installation](https://www.psycopg.org/docs/install.html)

[Psycopg 2 extensions](https://www.psycopg.org/docs/extensions.html)

Работа транзакций и cursor, особенности binary distribution, build/runtime libpq. Сайт уже содержит документацию более новой версии; воспроизводимый профиль исследования закреплён на 2.9.11.

### S6 — закреплённый исходник Psycopg 2.9.11

Repository: [psycopg/psycopg2](https://github.com/psycopg/psycopg2).

Commit tag 2_9_11: fd9ae8cad2bcfc3e7e9410e7b6f07cda8f4f05ec.

- [pqpath.c](https://github.com/psycopg/psycopg2/blob/fd9ae8cad2bcfc3e7e9410e7b6f07cda8f4f05ec/psycopg/pqpath.c): PQexec и команды транзакций.
- [connection_int.c](https://github.com/psycopg/psycopg2/blob/fd9ae8cad2bcfc3e7e9410e7b6f07cda8f4f05ec/psycopg/connection_int.c): encoding, DateStyle, server version, escaping.
- [adapter_datetime.c](https://github.com/psycopg/psycopg2/blob/fd9ae8cad2bcfc3e7e9410e7b6f07cda8f4f05ec/psycopg/adapter_datetime.c): PostgreSQL casts при адаптации temporal types.

### S7 — Psycopg 3 cursor types

[Psycopg 3 Cursor Types](https://www.psycopg.org/psycopg3/docs/advanced/cursors.html)

ClientCursor, client-side binding и Simple Query, включая оговорку COMMIT/ROLLBACK начиная с 3.1.20.

### S8 — диагностические поля PostgreSQL

[Error and Notice Fields](https://www.postgresql.org/docs/18/protocol-error-fields.html)

ErrorResponse, SQLSTATE, severity и character-based position.

### S9 — PostgreSQL SQLSTATE

[PostgreSQL Error Codes](https://www.postgresql.org/docs/18/errcodes-appendix.html)

Справочник целевых diagnostic classes; не заменяет mapping исходных Firebird status codes.

## Firebird semantics и типы

### S10 — транзакции Firebird

[Firebird 6 Transaction Control](https://www.firebirdsql.org/file/documentation/chunk/en/refdocs/fblangref60/langref-transacs.html)

Native transaction model, TPB semantics, отличие AUTO COMMIT / COMMIT RETAIN. Применимость к продуктовой ветке проверяется по её исходникам.

### S11 — аутентификация и providers

[Firebird 6 Security](https://www.firebirdsql.org/file/documentation/chunk/en/refdocs/fblangref60/langref-security.html)

[Firebird Configuration Reference](https://www.firebirdsql.org/docs/html/en/refdocs/fbconf/firebird-configuration-reference.html)

[Writing Custom Providers](https://www.firebirdsql.org/writing-custom-providers-for-firebird)

Граница локальной и сетевой аутентификации и назначение providers. Для встроенного PG frontend недостаточно локального attach с username/password: сначала нужна проверка credentials и серверный AuthBlock; существующий внутренний обмен auth plugins описан в F8. Имена engine provider меняются между версиями; текущие defaults сверены по исходнику Firebird 6.

### S12 — строковые значения PostgreSQL

[PostgreSQL Character Types](https://www.postgresql.org/docs/18/datatype-character.html)

NUL недопустим в PostgreSQL character types. Строковый codec должен выдавать явную ошибку вместо тихого усечения Firebird строки.

### S13 — расширение результатов агрегатов Firebird

[Firebird 6 General-purpose Aggregates](https://firebirdsql.org/file/documentation/chunk/en/refdocs/fblangref60/langref-aggfuncs-general.html)

SUM(BIGINT) и exact numeric могут вернуть более широкий тип, чем исходная колонка.

### S14 — BOOLEAN и OID

[PostgreSQL Boolean Type](https://www.postgresql.org/docs/18/datatype-boolean.html)

[PostgreSQL pg_type.dat](https://github.com/postgres/postgres/blob/REL_17_STABLE/src/include/catalog/pg_type.dat)

BOOLEAN output t/f и встроенные идентификаторы типов. OID выбираются явно; PostgreSQL catalog storage не создаётся.

### S15 — дополнительные особенности Firebird SQL

[Firebird 6 SELECT](https://www.firebirdsql.org/file/documentation/chunk/en/refdocs/fblangref60/langref-dml.html)

[Firebird 6 INSERT](https://www.firebirdsql.org/file/documentation/chunk/en/refdocs/fblangref60/langref-dml-insert.html)

[Firebird 6 EXECUTE BLOCK](https://www.firebirdsql.org/file/documentation/chunk/en/refdocs/fblangref60/langref-dml-execblock.html)

[Jaybird metadata API](https://www.firebirdsql.org/docs/drivers/java/latest/docs/org.firebirdsql.jaybird/org/firebirdsql/jdbc/FBDatabaseMetaData.html)

SQL fixtures учитывают требования parser и native DDL semantics; RETURNING и PSQL используют штатные output metadata/cursor flags и соответствующий способ получения результата. Поведение сверяется с выбранным SHA Firebird 6.

## Закреплённая карта исходников Firebird

### F1 — публичные интерфейсы и ownership

- [FirebirdInterface.idl](../src/include/firebird/FirebirdInterface.idl): `IAttachment`, `ITransaction`, `IStatement`, `IResultSet`, `IMessageMetadata`, `IMetadataBuilder`; `cancelOperation` около строки 696.
- [Interface.h](../src/include/firebird/Interface.h) и [Using OO API](../doc/Using_OO_API.md): вызовы, wrappers и status contract.
- [RefCounted.h](../src/common/classes/RefCounted.h), [ImplementHelper.h](../src/common/classes/ImplementHelper.h): `RefPtr`, `REF_NO_INCR`, implementation helpers.

Обычный `RefPtr` добавляет ссылку, adopt-вариант принимает уже выданную; successful commit/rollback/detach/free/close могут потреблять interface ownership. Эти interfaces используются штатным native server, но сами по себе не гарантируют local engine route.

Ограничение одной активной application transaction относится к PgWire state machine. Native API передаёт конкретный `ITransaction` в prepare/execute и допускает несколько transaction handles у attachment; общий host не должен превращать ограничение одного протокола в правило движка.

### F2 — официальные примеры OO API

- [02.update.cpp](../examples/interfaces/02.update.cpp), [03.select.cpp](../examples/interfaces/03.select.cpp), [04.print_table.cpp](../examples/interfaces/04.print_table.cpp), [05.user_metadata.cpp](../examples/interfaces/05.user_metadata.cpp).

Примеры показывают message buffers и metadata. Их клиентский attach, retaining и упрощённые error loops не задают серверный auth/lifecycle contract.

### F3 — dispatcher и native server

- [why.cpp](../src/yvalve/why.cpp): `Dispatcher::attachOrCreateDatabase`, merge `isc_dpb_config` перед provider iteration, около строки 6550.
- [server.cpp](../src/remote/server/server.cpp): `DatabaseAuth::accept`, около строки 2554; `prepare_statement`, `execute_statement`, `fetch`, `start_transaction`, `disconnect`.
- [inet_server.cpp](../src/remote/server/os/posix/inet_server.cpp): POSIX `main`, `registerAuthPlugins` и native server loop.

Native server обращается к dispatcher с configured providers. Host для дополнительных протоколов должен явно ограничить selection локальным engine после sanitization DPB. Это переопределяет `Providers`, но не отменяет остальные database policies. Наличие `IAttachment` или совпадение PID не доказывают отсутствие сетевого перехода.

### F4 — parser, statement semantics и engine metadata

- [jrd.cpp](../src/jrd/jrd.cpp): `JAttachment::prepare` → DSQL.
- [parse.y](../src/dsql/parse.y): `top: statement | statement ';'`, около строки 913; native transaction/session grammar и параметры.
- [btyacc_fb.ske](../src/dsql/btyacc_fb.ske): generated parser skeleton и EOF.
- [dsql.cpp](../src/dsql/dsql.cpp): `isc_info_sql_stmt_flags`/`isc_info_sql_stmt_type`, около строк 848–925.
- [StmtNodes.cpp](../src/dsql/StmtNodes.cpp), [DsqlStatements.h](../src/dsql/DsqlStatements.h): semantic statement types, RETURNING, MERGE и UPDATE OR INSERT.
- [dsql.cpp](../src/dsql/dsql.cpp): `prepareStatement`, около строк 532–561, legacy empty input error и удаление завершающего `;` при отсутствии `PREPARE_REQUIRE_SEMICOLON`.
- [Parser.cpp](../src/dsql/Parser.cpp): `yyerror_detailed`/`yyerrorIncompleteCmd`, около строк 1243–1277; `isc_command_end_err2` не различает EMPTY и незавершённую непустую команду.
- [jrd.cpp](../src/jrd/jrd.cpp): `JAttachment::prepare`, около строки 5759; отдельное использование nested `isc_command_end_err2` при `PREPARE_REQUIRE_SEMICOLON` подтверждает необходимость сохранять этот legacy contract.

Parser получает целый SQL без модификаций. Структура native API сводит RETURNING к SELECT/EXEC_PROCEDURE, MERGE/UPDATE OR INSERT к INSERT, session management к DDL, retaining и non-retaining commit к одному публичному kind. Эти факты остаются ограничениями coarse getType; текущее проектное решение дополняет его ограниченным lexer в PgWire для tags/control profile. getFlags/input/output metadata и affected records сохраняют роль в execution и codecs. SQL rewriting, splitting по `;`, wire-замена `$n` и эмуляция успешных commands запрещены. Будущие BEGIN/$n относятся к parser/parameter metadata.

Новые публичные info items, engine origin/operation metadata, AST annotations или parser observer не требуются и удаляются из текущего дизайна. P4 использует plugin-local command descriptor; P5 добавит локальный control descriptor, сохраняя реальные engine effects/handles. В E используется обычный prepare и последующий guard перед execute; только известные semantic errors преобразуются в 25P02, syntax остаётся 42601, приоритетные fatal/cancel/timeout/invalid handle/resource/I/O и неизвестные diagnostics сохраняются по CODEX_CONTEXT. Исторические результаты предыдущих реализаций записаны отдельно в VALIDATION.

EMPTY не требует изменения parser diagnostics или prepare flags. Сам `isc_command_end_err2` не доказывает пустоту: нужен tokenless-признак ограниченного plugin lexer и syntax error 42601 от реального engine prepare исходного SQL. Только это сочетание после cleanup даёт EmptyQueryResponse; другие ошибки не маскируются. Незаконченные tokens/comments/strings, vertical tab и отдельный `;` остаются errors. Штатные auto-terminator/continuation paths DSQL сохраняются.

Текущая реализация ограниченного descriptor расположена в [SqlInfo.h](../src/plugins/wire/pg/SqlInfo.h) и [SqlInfo.cpp](../src/plugins/wire/pg/SqlInfo.cpp); её применение после prepare — в [Execute.cpp](../src/plugins/wire/pg/Execute.cpp). Это код plugin, а не новое публичное API движка. Наличие кода само по себе не является результатом приёмки; команды и результаты повторного прогона записываются в VALIDATION.

### F5 — типы, CHAR и полный status vector

- [sqlda_pub.h](../src/include/firebird/impl/sqlda_pub.h), [inf_pub.h](../src/include/firebird/impl/inf_pub.h), [consts_pub.h](../src/include/firebird/impl/consts_pub.h): dialect 3, statement kinds, SQL types, flags.
- [MsgMetadata.h](../src/common/MsgMetadata.h), [MsgMetadata.cpp](../src/common/MsgMetadata.cpp): lengths, charsets, offsets, metadata builder.
- [gen.cpp](../src/dsql/gen.cpp): `GEN_port`, около строки 228, превращает внутренний CHAR в varying, сохраняя описание CHAR через `par_is_text`; комментарий ссылается на CORE-2606.
- [intl.cpp](../src/jrd/intl.cpp): `INTL_adjust_text_descriptor`, около строки 288; `pad_spaces` при выдаче SQL_TEXT, около строки 660.
- [ExprNodes.cpp](../src/dsql/ExprNodes.cpp): LiteralNode корректирует metadata до максимального количества bytes, около строки 8060.
- [errd.cpp](../src/dsql/errd.cpp): `internal_post`, около строки 185, добавляет общий `isc_dsql_error` перед конкретным status.
- [gds.cpp](../src/yvalve/gds.cpp): `fb_sqlstate`, около строки 2464, пример обхода tagged status vector.
- [jrd messages](../src/include/firebird/impl/msg/jrd.h): конкретные codes и их Firebird SQLSTATE.

CHAR UTF8 SQL_TEXT buffer может быть N×4 bytes с padding. Запросить его как SQL_VARYING через builder позволяет получить engine-correct length, сохранив исходный CHAR OID; это требует проверки с trailing spaces и многобайтным текстом. Error mapping обходит весь status vector и учитывает дополнительные причины. Firebird `42S02`/`HY008` нельзя безусловно выдать вместо PostgreSQL `42P01`/`57014`.

### F6 — конфигурация, сборка и TLS dependency

- [firebird.conf](../builds/install/misc/firebird.conf), [databases.conf](../builds/install/misc/databases.conf), [config.h](../src/common/config/config.h), [config.cpp](../src/common/config/config.cpp): global/per-database keys.
- [make.shared.variables](../builds/posix/make.shared.variables): `dirFiles`/`dirObjects` около строк 4–18 не рекурсивны; `Remote_Server` около строки 70; `Engine_Objects` около строки 98.
- [Makefile.in](../builds/posix/Makefile.in): штатный `FB_DAEMON` target около строки 658, пример отдельного profiler plugin около строки 772; IDL/CLOOP generation.
- [make.defaults](../builds/posix/make.defaults), [configure.ac](../configure.ac), [CI](../.github/workflows/main.yml): toolchain, dependencies и штатный POSIX build.
- [src/CMakeLists.txt](../src/CMakeLists.txt): CLOOP header generation присутствует; готовый native server target в просмотренной карте не подтверждён.

Объекты host и отдельной PG library должны явно попасть в соответствующие targets/install rules. В просмотренной server build нет готовой сетевой TLS library. `TLS_OPTIONS` в configure (около строки 1275) — thread-local storage. TLS dependency/опциональность/упаковка определяются в P0; наличие libtomcrypt не решает PostgreSQL SSLRequest → TLS.

### F7 — native wire и транспорт

- [protocol.h](../src/remote/protocol.h), [protocol.cpp](../src/remote/protocol.cpp): `P_OP`, `PACKET`, `xdr_protocol`.
- [remote.h](../src/remote/remote.h): `rem_port`, native XDR streams/callbacks и `ServAttachment/ServTransaction/ServStatement/ServCursor`.
- [inet.cpp](../src/remote/inet.cpp): `alloc_port`, receive/send, `select_accept/select_multi/select_wait`.
- [remote.cpp](../src/remote/remote.cpp), [server.cpp](../src/remote/server/server.cpp): `REMOTE_get_timeout_params`, `server_req_t`, `SRVR_multi_thread`, `process_packet`, `loopThread`, `set_server`.

Native queues и callbacks передают PACKET; `port_type` отражает transport, а не независимый wire codec. Общий host дополнительных протоколов и PG codec не должны отправлять PG bytes в этот dispatcher. Native listener и его конфигурация остаются самостоятельными.

### F8 — проверка credentials и защищённый attach

- [ValidatePassword.cpp](../src/jrd/extds/ValidatePassword.cpp): `EDS::validatePassword`, `CBlock`, `SBlock`, `DummyCryptKey`; guards текущего attachment/auth block около строки 175. Сборка этого файла в Engine14 — F6.
- [SrpClient.cpp](../src/auth/SecureRemotePassword/client/SrpClient.cpp), [SrpServer.cpp](../src/auth/SecureRemotePassword/server/SrpServer.cpp): plugin exchange, security database, `setDbCryptCallback`.
- [interface.cpp](../src/remote/client/interface.cpp): `registerRedirector` регистрирует client auth plugins, около строки 1110.
- [inet_server.cpp](../src/remote/server/os/posix/inet_server.cpp): `registerAuthPlugins`, около строки 163.
- [Auth.h](../src/common/Auth.h), [Auth.cpp](../src/common/Auth.cpp): verified AuthBlock writer.
- [server.cpp](../src/remote/server/server.cpp): `DatabaseAuth::accept`, sanitization DPB и `addClumplets` с достоверным peer.
- [jrd.cpp](../src/jrd/jrd.cpp): `JProvider::internalAttach`, `getUserInfo`, `RemoteAccess` и приоритет username над auth block.
- [constants.h](../src/jrd/constants.h): `CURRENT_ENGINE="Engine14"`, `EMBEDDED_PROVIDERS="Providers=" CURRENT_ENGINE`, около строки 511.
- [ParsedList.cpp](../src/common/classes/ParsedList.cpp): `getNonLoopbackProviders` удаляет только Loopback; Remote остаётся и может участвовать в security database attach.

EDS wrapper нельзя вызывать до attachment. Общий host auth service требует явных credentials и доверенной конфигурации, ограниченного exchange, cleanup и policy tests. Копия механики в каждом wire-плагине не нужна. Если реализация затронет EDS, регрессии сохраняют прежние guards; неизменённый EDS не требует обязательного рефакторинга ради M0. Security database начального профиля локальная; отсутствие сетевого fallback проверяется отдельно.

### F9 — server lifecycle и режимы

- [inet_server.cpp](../src/remote/server/os/posix/inet_server.cpp): около строки 378 все non-Classic modes получают `SRVR_multi_client`; Classic имеет fork/accept lifecycle.
- [jrd.cpp](../src/jrd/jrd.cpp): общий Database instance только для Super, около строки 7509; отличия cleanup около строки 7840.
- [why.cpp](../src/yvalve/why.cpp): `Dispatcher::shutdown` и последовательность preproviders/providers/postproviders.
- [server.cpp](../src/remote/server/server.cpp): native `pre_shutdown`, `shut_server`, `Worker::shutdown`, disconnect.

Общая ветка listener делает SuperClassic возможным, но не доказывает его корректность без lifecycle/coexistence tests. Host останавливает accept/readers и workers согласованно с provider shutdown. Module unload выполняется после последнего callback/interface/thread; бесконечный join до provider shutdown запрещён.

### F10 — PluginManager и CLOOP

- [FirebirdInterface.idl](../src/include/firebird/FirebirdInterface.idl): PluginBase/PluginFactory/PluginModule/PluginManager, около строк 150–280; существующие TYPE_* и TYPE_COUNT. Wire protocol type пока отсутствует; TYPE_WIRE_CRYPT решает другую задачу.
- [PluginManager.cpp](../src/yvalve/PluginManager.cpp): `PluginLoadInfo`, около строки 775, разрешает logical name через plugins.conf `Module`/`RegisterName`; `loadModule` около строки 958 исправляет platform extension и вызывает entry point; `releasePlugin` и `waitForType` определяют lifetime.
- [PluginManager.cpp](../src/yvalve/PluginManager.cpp): `findPluginConfig`, около строки 251, читает `Config` из alias section и находит соответствующую секцию; [plugins.conf](../builds/install/misc/plugins.conf) содержит штатный пример такой связи.
- [GetPlugins.h](../src/common/classes/GetPlugins.h): existing typed access to plugin sets.
- [Makefile.in](../builds/posix/Makefile.in): `FirebirdInterface.idl`/`IdlFbInterfaces.h`, CLOOP build/generation.

`WireProtocolPlugins`/`WireProtocolPorts` и wire interfaces — проектируемые расширения, а не готовые upstream возможности. Добавляются новый plugin type, versioned interfaces, config validation и host lifecycle. Логические имена сохраняют штатное сопоставление с `.so`/`.dll`; не требуется второй самостоятельный loader. Детали контракта определены в ARCHITECTURE.

Два aliases с одинаковыми `Module`/`RegisterName` и разными `Config` используют один модуль и разные endpoint settings. Bind/TLS конфигурация относится к alias, не к ещё одному позиционному списку. Проверки создают два экземпляра одного минимального тестового модуля и подтверждают раздельные settings, ошибку одного endpoint без смещения другого, удержание модуля до завершения обеих sessions.

### F11 — ключи шифрования базы

- [server.cpp](../src/remote/server/server.cpp): `DatabaseAuth::accept` передаёт native crypt callback, около строки 2604.
- [CryptoManager.cpp](../src/jrd/CryptoManager.cpp): `loadPlugin`, около строки 456; key holder callback около строки 489 и holder-less `setKey` около строки 520.

Обычный PG-клиент не предоставляет native client key callback. Initial profile исключает необходимость ключа от клиента; серверные источники ключей могут работать и требуют отдельной проверки. Это ограничение не тождественно запрету зашифрованных баз.

### F12 — внешняя отмена, shutdown и таймауты

- [VirtualTable.cpp](../src/jrd/VirtualTable.cpp): DELETE MON$ATTACHMENTS → LCK_attachment, DELETE MON$STATEMENTS → LCK_cancel, около строки 62.
- [Attachment.cpp](../src/jrd/Attachment.cpp): `blockingAstShutdown`/`blockingAstCancel`, около строк 671/695; idle timer и `isc_att_shut_idle`, около строки 798.
- [jrd.cpp](../src/jrd/jrd.cpp): `JAttachment::cancelOperation`, около строки 2457, async/nonblocking lock; shutdown reasons около строки 6829; `thread_db::getCancelState` с secondary timeout reason около строки 9330.
- [DsqlRequests.cpp](../src/dsql/DsqlRequests.cpp): `setupTimer`, около строки 179; config/attachment/request statement timeout.
- [jrd messages](../src/include/firebird/impl/msg/jrd.h): `isc_cancelled`, `isc_att_shutdown`, `isc_att_shut_killed/idle/db_down/engine`, `isc_cfg/att/req_stmt_timeout`.

Тесты включают native admin operations и idle sockets. PG mapping учитывает конкретную причину: admin shutdown 57P01, cancellation/statement timeout 57014, idle-session timeout 57P05 — S9. Обнаружение остановки attachment, доставка FATAL и закрытие idle socket требуют явного механизма; ошибка на следующем engine API call сама по себе не гарантирует немедленного уведомления.

## Дополнительные источники для M2 и M3

### S16 — libpq и Extended Query

- [PostgreSQL 17 libpq execution API](https://www.postgresql.org/docs/17/libpq-exec.html): PQexecParams, PQprepare/PQexecPrepared, parameter OID, text/binary formats и result metadata.
- [PostgreSQL 17 protocol flow](https://www.postgresql.org/docs/17/protocol-flow.html): Parse/Bind/Describe/Execute/Close/Flush/Sync, portals, implicit transactions и discard-until-Sync.
- [libpq fe-exec.c](https://github.com/postgres/postgres/blob/REL_17_STABLE/src/interfaces/libpq/fe-exec.c): PQexecParams/PQsendQueryParams и PQexecPrepared/PQsendQueryPrepared используют Extended Query; PQprepare отправляет Parse.

Эти источники обосновывают отдельную приёмку C libpq и обязательный M2. Поддержка PQexec сама по себе не покрывает остальные API. REL_17_STABLE — движущаяся ссылка; SHA реально использованной libpq фиксируется при подготовке harness/fixtures.

### S17 — подключение, TLS и cancellation

- [PostgreSQL 17 connection parameters](https://www.postgresql.org/docs/17/libpq-connect.html): настройки libpq, sslmode и connection profile.
- [PostgreSQL 17 message flow](https://www.postgresql.org/docs/17/protocol-flow.html): SSLRequest, TLS upgrade, BackendKeyData и CancelRequest.
- [PostgreSQL 17 message formats](https://www.postgresql.org/docs/17/protocol-message-formats.html): layout protocol 3.0, включая cancellation.

Для M3 нужен собственный проверенный TLS/cancel path внутри PG frontend. Native Firebird WireCrypt и наличие Srp256 не доказывают PostgreSQL TLS/SCRAM compatibility. Форматы 3.2 не переносятся в контракт 3.0 автоматически.


### S18 — P8 scalar text/binary representations

- [PostgreSQL numeric send/recv](https://github.com/postgres/postgres/blob/REL_17_STABLE/src/backend/utils/adt/numeric.c): base-10000 digits, signed weight, sign, display scale.
- [PostgreSQL numeric types](https://www.postgresql.org/docs/current/datatype-numeric.html) и [float source](https://doxygen.postgresql.org/float_8c_source.html): IEEE floating point и text special values.
- [PostgreSQL date/time send](https://doxygen.postgresql.org/date_8c.html): date_send передаёт int32 дней, time_send — int64 микросекунд.
- [PostgreSQL timestamp send](https://doxygen.postgresql.org/backend_2utils_2adt_2timestamp_8c.html) и [timestamp constants](https://doxygen.postgresql.org/datatype_2timestamp_8h_source.html): int64 микросекунд, epoch 2000-01-01, 86400000000 микросекунд в сутках.
- [Firebird native temporal constants](../src/common/classes/NoThrowTimeStamp.h), [native timestamp implementation](../src/common/classes/NoThrowTimeStamp.cpp) и [Int128](../src/common/Int128.h): native epoch/range/100 µs и переносимый доступ к native integer representation.

P8 ограничивает input точной представимостью native типа; NUMERIC/temporal не округляются внутри codecs. Это явно более узкий профиль, чем все значения PostgreSQL. Независимые binary fixtures находятся в unit tests и [codec harness](tools/pgwire_codec_smoke.py).
