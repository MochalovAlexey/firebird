# Первичные источники и карта исходников

Исходная карта подготовлена 6 октября 2026 года; пакет обновлён 8 октября 2026 года для M0–M3. Пользователь выбрал master Firebird 6 и рабочую ветку pg_wire_protocol. Локальная база совпадает с закреплённым SHA ниже; ключевые build/provider/entry-point symbols повторно сверены, см. BASELINE.md. Движущиеся URL current/master удобны для навигации; воспроизводимые результаты привязываются к конкретным версиям и SHA.

Архитектура исследования: новый PostgreSQL wire frontend **внутри процесса штатного Firebird server**, со своим listener и codec, проверкой credentials через внутренние auth plugins и прямым вызовом локального engine provider. Внешний gateway, повторное подключение к native TCP endpoint и клиентский attachment bridge исключены. Интерфейсы `IAttachment`/`IStatement` сами по себе не определяют сетевой путь: ими уже пользуется native server, а выбор provider требует отдельного контроля.

Граница доказательств: проведено статическое чтение документации и исходников. Изменённый native server не реализован, не собран и не запущен; работоспособность интеграции пока не проверена. F7–F9 фиксируют точки интеграции. Упоминания v0 в исходной карте относятся к M1; актуальные расширения M2/M3 определены в CODEX_CONTEXT/COMPATIBILITY, дополнительные источники — S16/S17 в конце файла.

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

NUL недопустим в PostgreSQL character types. Отсюда выбран явный отказ v0, а не тихое усечение Firebird строки.

### S13 — расширение результатов агрегатов Firebird

[Firebird 6 General-purpose Aggregates](https://firebirdsql.org/file/documentation/chunk/en/refdocs/fblangref60/langref-aggfuncs-general.html)

SUM(BIGINT) и exact numeric могут вернуть более широкий тип, чем исходная колонка.

### S14 — BOOLEAN и OID

[PostgreSQL Boolean Type](https://www.postgresql.org/docs/18/datatype-boolean.html)

[PostgreSQL pg_type.dat](https://github.com/postgres/postgres/blob/REL_17_STABLE/src/include/catalog/pg_type.dat)

BOOLEAN output t/f и встроенные идентификаторы типов. OID в v0 выбираются явно; PostgreSQL catalog storage не создаётся.

### S15 — дополнительные особенности Firebird SQL

[Firebird 6 SELECT](https://www.firebirdsql.org/file/documentation/chunk/en/refdocs/fblangref60/langref-dml.html)

[Firebird 6 INSERT](https://www.firebirdsql.org/file/documentation/chunk/en/refdocs/fblangref60/langref-dml-insert.html)

[Firebird 6 EXECUTE BLOCK](https://www.firebirdsql.org/file/documentation/chunk/en/refdocs/fblangref60/langref-dml-execblock.html)

[Jaybird metadata API](https://www.firebirdsql.org/docs/drivers/java/latest/docs/org.firebirdsql.jaybird/org/firebirdsql/jdbc/FBDatabaseMetaData.html)

FROM в проверенном синтаксисе обязателен; RETURNING и PSQL требуют отдельного consideration, а fixture должна учитывать native DDL semantics. Поведение сверяется с выбранным SHA Firebird 6.

## Закреплённая карта исходников Firebird

Repository: [FirebirdSQL/firebird](https://github.com/FirebirdSQL/firebird).

Исследованный [commit bf94598f8deefeb7cc4a8217ee43b3040d5d6650](https://github.com/FirebirdSQL/firebird/tree/bf94598f8deefeb7cc4a8217ee43b3040d5d6650).

[writeBuildNum.sh](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/misc/writeBuildNum.sh) в этом commit задаёт `BuildType=T`, `MajorVer=6`, `MinorVer=0`, `RevNo=0`, `BuildNum=2199`. Это development snapshot официального upstream; он не доказывает наличие готовой версии 6 в целевом продукте. Перед работой Codex должен зафиксировать точный repository/branch/SHA продуктовой базы и повторно сверить указанные symbols.

### F1 — публичные интерфейсы

- [FirebirdInterface.idl](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/include/firebird/FirebirdInterface.idl)
- [Interface.h](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/include/firebird/Interface.h)
- [Using OO API](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/doc/Using_OO_API.md)

IMetadataBuilder, message offsets, flags, execute/openCursor, ownership. Эти же interfaces применяются внутри native server; внешний клиентский слой для их использования не нужен. Примечание: текст документации о размещении statement types устарел относительно source; публичные isc_info_sql_stmt_* находятся в inf_pub.h. Различие между принимающим возвращённую ссылку wrapper и штатным `RefPtr`, который добавляет свою ссылку, см. F9.

### F2 — официальные примеры

- [02.update.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/examples/interfaces/02.update.cpp)
- [03.select.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/examples/interfaces/03.select.cpp)
- [04.print_table.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/examples/interfaces/04.print_table.cpp)
- [05.user_metadata.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/examples/interfaces/05.user_metadata.cpp)

Примеры полезны для metadata и выполнения statements. Их локальный client attach, retaining или упрощённые циклы ошибок не задают архитектуру PG frontend. Образец серверной аутентификации и создания attachment — F3/F8, lifecycle — F9.

### F3 — dispatcher и существующий native server

- [yvalve/why.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/yvalve/why.cpp)
- [remote/client/interface.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/remote/client/interface.cpp)
- [remote/server/server.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/remote/server/server.cpp)
- [POSIX server entry point](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/remote/server/os/posix/inet_server.cpp)

Проверены `Dispatcher::attachOrCreateDatabase`, перебор configured providers и DPB config; `DatabaseAuth::accept`, `rem_port::prepare_statement`, `execute_statement`, `fetch`, `start_transaction`, `disconnect`; `main()` POSIX сервера и `registerAuthPlugins`. Это исходные точки для реализации PG listener и прямого выполнения запросов внутри того же процесса.

`remote/client/interface.cpp` используется как источник о регистрации auth client plugins и поведении `Remote`/`Loopback`, которое требуется исключить из PG execution route. Его клиентский native connection path не является backend предлагаемого решения. Подробности codec/dispatch — F7, проверка credentials/attach — F8, запуск и остановка — F9.

### F4 — engine boundary и полный разбор SQL

- [jrd/jrd.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/jrd/jrd.cpp)
- [dsql/dsql.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/dsql/dsql.cpp)
- [dsql/parse.y](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/dsql/parse.y)
- [dsql/btyacc_fb.ske](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/dsql/btyacc_fb.ske)

JAttachment::prepare вызывает DSQL_prepare. Начальное правило grammar принимает один statement; generated parser skeleton требует EOF. Это статическое основание отказа multi-statement без partial execution при корректном length/NUL handling.

### F5 — типы и ошибки

- [sqlda_pub.h](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/include/firebird/impl/sqlda_pub.h)
- [inf_pub.h](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/include/firebird/impl/inf_pub.h)
- [consts_pub.h](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/include/firebird/impl/consts_pub.h)
- [jrd diagnostic messages](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/include/firebird/impl/msg/jrd.h)
- [yvalve/gds.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/yvalve/gds.cpp)

SQL_DIALECT_V6=3, statement kinds, SQL type/subtype/scale, fb_sqlstate и исходные диагностические коды.

### F6 — конфигурация и build

- [firebird.conf](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/builds/install/misc/firebird.conf)
- [databases.conf](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/builds/install/misc/databases.conf)
- [configure.ac](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/configure.ac)
- [Linux and cross-platform CI](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/.github/workflows/main.yml)
- [POSIX Makefile.in](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/builds/posix/Makefile.in)

Прочитаны реальные build commands; сборка в рамках исследования не выполнялась. Базовый путь для Linux — существующие autotools/make и команды CI. В просмотренном `src/CMakeLists.txt` готовый native server target не обнаружен; интеграция PG frontend через CMake не подтверждена. Подключение объектов к штатному `FB_DAEMON`, конфигурация и lifecycle подробно сопоставлены в F9.

### F7 — wire protocol, транспорт и native dispatch

- [remote/protocol.h](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/remote/protocol.h)
- [remote/protocol.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/remote/protocol.cpp)
- [remote/remote.h](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/remote/remote.h)
- [remote/inet.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/remote/inet.cpp)
- [remote/remote.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/remote/remote.cpp)
- [remote/server/server.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/remote/server/server.cpp)

| Проверенный файл и symbol | Что подтверждено | Следствие для встроенного PG frontend |
|---|---|---|
| `protocol.h`: `P_OP`, `PACKET`; `protocol.cpp`: `xdr_protocol` | Native opcodes и XDR layout; `PACKET` — структура с полями native сообщений | PG tags и framing требуют отдельного codec; не менять значения native opcodes |
| `remote.h`: `rem_port` | Transport kind `INET`/`XNET`, native `port_protocol`, callbacks с `PACKET*`, XDR streams, locks и object registry | Тип транспорта не заменяет идентификатор wire protocol; PG 3.0 нельзя присвоить native `port_protocol` |
| `remote.h`: `ServAttachment`, `ServTransaction`, `ServStatement`, `ServCursor`; `Rdb`, `Rtr`, `Rsr` | Штатный сервер уже держит engine interfaces; registries дополнительно обслуживают native handles и состояние протокола | PG session может владеть теми же видами interfaces, не создавая native packets/object IDs |
| `inet.cpp`: `alloc_port`, `receive`, `send_full`, `send_partial` | `alloc_port` устанавливает native packet callbacks и создаёт XDR streams; receive/send вызывают `xdr_protocol` | Подмена содержимого native INET port PostgreSQL bytes не создаёт PG listener |
| `inet.cpp`: `select_accept`, `select_multi`, `select_wait` | Socket accept и ожидание связаны с `rem_port` и глобальной native инфраструктурой | При выделении общих socket helpers сохранять native поведение; для v0 выбран собственный listener того же процесса |
| `remote.cpp`: `rem_port::getPortConfig`, `REMOTE_get_timeout_params` | Получение per-port config и native timeout parameters | Сверить, какие лимиты переносимы; PG startup/read/write deadlines задавать осмысленно |
| `server.cpp`: `server_req_t`, `SRVR_multi_thread`, `process_packet`, `loopThread` | Очередь содержит `PACKET req_send/req_receive`; dispatcher и worker loop работают с native opcodes | Не запускать PG bytes через эту очередь без отдельного существенного refactor; v0 использует свою ограниченную группу PG workers в том же server |
| `server.cpp`: `set_server` | Реестр `srvr` сопоставляет серверы по `rem_port::port_type`, а не codec | Второй вызов `SRVR_multi_thread` с INET port не является независимой PG service |

Имена новых PG файлов, классов и config keys в плане являются предложением. В проверенном upstream PG listener/codec отсутствуют; соответствие схемы реальному серверу доказывается будущим build и endpoint tests.

### F8 — внутренняя проверка credentials, auth plugins и защищённый attach

- [jrd/extds/ValidatePassword.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/jrd/extds/ValidatePassword.cpp)
- [auth/SecureRemotePassword/client/SrpClient.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/auth/SecureRemotePassword/client/SrpClient.cpp)
- [auth/SecureRemotePassword/server/SrpServer.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/auth/SecureRemotePassword/server/SrpServer.cpp)
- [common/Auth.h](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/common/Auth.h)
- [common/Auth.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/common/Auth.cpp)
- [remote/server/server.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/remote/server/server.cpp)
- [remote/client/interface.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/remote/client/interface.cpp)
- [POSIX server registration](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/remote/server/os/posix/inet_server.cpp)
- [jrd/jrd.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/jrd/jrd.cpp)
- [jrd/constants.h](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/jrd/constants.h)
- [common/classes/ParsedList.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/common/classes/ParsedList.cpp)

Контракты `IClientBlock`, `IServerBlock`, `IClient`, `IServer`, `IAuth` — F1. `IServerBlock` не предоставляет raw password; `IClientBlock` предоставляет `getPassword`.

| Проверенный symbol | Что подтверждено | Необходимая работа / ограничение |
|---|---|---|
| `EDS::validatePassword`, `CBlock`, `SBlock`, `DummyCryptKey` | Существующий обмен auth client/server plugins в памяти; configured plugin lists, ограниченный цикл challenge/response, запись `isc_dpb_auth_block` | Выделить общий credential-validation core; готовая EDS функция требует уже существующий `tdbb` attachment/auth block и не вызывается напрямую из PG pre-attach |
| `registerSrpClient`, `registerSrpServer` | Регистрация семейств SRP plugins; client registration вызывается из `registerRedirector`, server registration — из server startup | Для общего внутреннего auth обмена обеспечить штатную регистрацию обеих сторон без вызова client connection path |
| `ServerAuth::authenticate`, `SrvAuthBlock::createPluginsItr` | Native auth учитывает серверный/per-database config, plugin selection и native continuation messages | Не объявлять EDS extraction полной заменой всех native auth policies; поддерживаемый профиль v0 должен быть определён и проверен отдельно |
| `DatabaseAuth::accept` | Записывает проверенный auth block; удаляет user/password, trusted/internal attach tags и клиентский `isc_dpb_config`; вызывает dispatcher | Выделить или повторить узкий серверный attach helper без native response serialization; PG Startup не должен задавать привилегированные DPB fields |
| `addClumplets` | Подставляет настоящий remote address первым, исключает дубликаты и задаёт native remote protocol `P<n>` | Для PG сохранить достоверную remote metadata, но передавать правильное имя протокола; не выдавать PG за native `P<n>` |
| `JProvider::internalAttach`, `getUserInfo` | Local engine отвергает remote-shaped DB name; `RemoteAccess` проверяется по remote address; username в DPB имеет приоритет над auth block | После проверки credentials убрать username/password из attach DPB, сохранить verified auth block и действительную remote address metadata |
| `Dispatcher::attachOrCreateDatabase` — F3 | Dispatcher перебирает configured providers и применяет DPB config | PG attach route должен быть server-controlled и ограничен локальным engine provider; запрещён fallback в `Remote`/`Loopback` |
| `CURRENT_ENGINE`, `EMBEDDED_PROVIDERS` в `jrd/constants.h` | В этом upstream: `CURRENT_ENGINE="Engine14"`, `EMBEDDED_PROVIDERS="Providers=" CURRENT_ENGINE` | Проверить эти definitions в целевой ветке; название macro не отменяет необходимость auth block и remote access checks |
| `ParsedList::getNonLoopbackProviders` | Удаляет только `Loopback`, оставляя `Remote`; вызывается также при открытии security DB в `SrpServer.cpp` | Этот helper сам по себе не гарантирует отсутствие повторного сетевого подключения |

Наличие внутреннего plugin exchange подтверждено чтением кода. Извлечение общего helper, его доступность до attach, поддержка выбранного security database/auth profile, корректность ошибок и отсутствие регрессии EDS ещё требуют реализации и проверок. PostgreSQL TLS и Firebird native WireCrypt — разные механизмы; native crypto state не защищает автоматически новый PG socket.

### F9 — lifecycle, конфигурация, ownership и сборка штатного сервера

- [remote/server/os/posix/inet_server.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/remote/server/os/posix/inet_server.cpp)
- [remote/server/server.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/remote/server/server.cpp)
- [yvalve/why.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/yvalve/why.cpp)
- [common/config/config.h](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/common/config/config.h)
- [common/config/config.cpp](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/common/config/config.cpp)
- [common/classes/RefCounted.h](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/common/classes/RefCounted.h)
- [common/classes/ImplementHelper.h](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/common/classes/ImplementHelper.h)
- [builds/posix/make.shared.variables](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/builds/posix/make.shared.variables)
- [builds/posix/make.defaults](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/builds/posix/make.defaults)
- [builds/posix/Makefile.in](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/builds/posix/Makefile.in)
- [src/CMakeLists.txt](https://github.com/FirebirdSQL/firebird/blob/bf94598f8deefeb7cc4a8217ee43b3040d5d6650/src/CMakeLists.txt)

| Проверенная область | Что подтверждено | Следствие для плана |
|---|---|---|
| POSIX `main`, `registerAuthPlugins`, `SRVR_multi_thread` | Auth registration и Super initialization предшествуют блокирующему native loop; Classic содержит отдельный fork/accept lifecycle | Запуск PG service внутри этого `main`; v0 ограничить Linux `ServerMode=Super`, иные режимы не считать проверенными |
| `Dispatcher::shutdown` | `fb_shut_preproviders` → shutdown providers/interfaces → `fb_shut_postproviders` → finish/exit | Остановить PG accept, разбудить socket waits и корректно завершить workers; нельзя бессрочно join активный SQL до provider shutdown |
| Native `pre_shutdown`, `shut_server`, `Worker::shutdown`, `rem_port::disconnect` | Собственные флаги остановки и освобождение cursor/statement/transaction/attachment | Новые PG workers участвуют в shutdown того же процесса; active query и idle socket должны иметь отдельные проверки остановки |
| `RefPtr(T*)`, `RefPtr(REF_NO_INCR, T*)`, `clear`, `done` в Y-valve | Обычный `RefPtr` добавляет ссылку; вариант `REF_NO_INCR` принимает существующую; successful close/free/commit/rollback/detach потребляет интерфейсную ссылку | Выбрать явную модель владения. Native `Serv*` после consuming close освобождает дополнительную ссылку; adopt wrapper должен разоружаться. Не смешивать эти схемы |
| `DispatcherPtr` | Штатный internal helper получает dispatcher и управляет его ссылкой | Это образец серверного доступа к engine interfaces; отсутствие сети обеспечивается выбранным local provider route |
| `ConfigKey`, `entries`, config getters | В `config.h` перечислена процедура добавления конфигурации; global и per-database getters различаются | Новые PG keys и documented defaults — изменения продукта, их ещё нет upstream |
| `Remote_Common`, `Remote_Server`, `Remote_Client` | `Remote_Common` входит и в server, и в client; `Remote_Server_Objects` образуют server object set | Добавлять PG server sources только в штатный server target; не собирать внешний gateway и не включать listener случайно в client library |
| `FB_DAEMON`, `FIREBIRD_LIBRARY_LINK` | Штатный `firebird` уже линкуется с `-lfbclient` как частью существующего runtime | Требование — отсутствие client connection/bridge, а не обещание удалить существующую runtime dependency из Firebird |
| Make targets `tests`, `run_tests` и CI — F6 | Существуют common/engine/isql test executables и Linux autotools/make workflow | Добавить PG codec/session/endpoint tests и native regression checks; готовый PG test target не существует |
| Просмотренный `src/CMakeLists.txt` | Native server target не обнаружен | Не утверждать готовую CMake сборку встроенного PG frontend; выбрать подтверждённый Linux build path |

Ни успешная сборка, ни запуск изменённого native server, ни отсутствие сетевого backend соединения в runtime в рамках исследования не проверялись. Это обязательные результаты этапа реализации, включая native PostgreSQL driver → PG port → local engine в одном процессе сервера.

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
