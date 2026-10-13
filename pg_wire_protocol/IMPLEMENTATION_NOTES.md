# Решения первого этапа реализации

Начало реализации: 9 октября 2026 года. База исходников — master Firebird 6 из BASELINE.md; HEAD до изменений — `d533d51ad2`. Фактические результаты сборки и проверок записываются отдельно в [VALIDATION.md](VALIDATION.md).

Linux/Super host P1 со штатным getPluginConfig и целевые P2 startup/authentication checks прошли по [VALIDATION.md](VALIDATION.md). P3 SELECT execution и минимальная engine metadata прошли целевую Linux/Super приёмку M0. P4 Simple Query DML/DDL, базовые codecs и EMPTY прошли целевую Linux/Super приёмку. По уточнённому требованию пользователя выполнен refactor классификации в плагин (описан ниже, Linux/Super проверки прошли). P5 explicit transactions и session controls прошёл целевую Linux/Super приёмку с отдельным native AUTO COMMIT ограничением; результаты записаны в VALIDATION. TLS, Windows wiring/runtime и другие server modes остаются дальнейшей работой.

Текущее уточнение от 13 октября 2026 года: добавленные wire-таймауты удалены. Ниже сохранена история этапов; прежние описания IOTimeout относятся к тем ревизиям, а не к текущему контракту.

## Первый срез P1

Первый срез зарегистрировал глобальные `WireProtocolPlugins` / `WireProtocolPorts` и проверку позиционных пар; сам parser не загружает plugins и не открывает listeners. Оба параметра имеют пустое значение по умолчанию; существующие ConfigKey сохраняют номера, новые добавлены в конец.

Парсер конфигурации располагается в `src/remote/server/wire/EndpointConfig.h`. Это внутренний header-only компонент generic host; стандартные C++ types здесь не пересекают будущий plugin ABI. Unit tests входят в существующий `common_test` через `src/common/tests`, без создания отдельного test framework.

Список имеет строгий comma-separated формат с ASCII whitespace по краям. Пустые позиции, несовпадающие длины, нечисловые/нулевые/переполненные порты, дубли портов и configured names отклоняются. Для двух endpoints одной factory используются разные aliases в plugins.conf. Configured name не должен содержать разделители, позволяющие превратить один элемент в список при вызове PluginManager.

Пары формируются до загрузки модулей, без фильтрации неизвестных имён. Ошибка проверки не выдаёт частичный список. Host при такой ошибке отключает дополнительную конфигурацию с диагностикой, сохраняя native startup. Проверку фактического native endpoint и bind выполняет Server, а не parser строк.

## Конфигурация endpoint

Порт задаётся только в WireProtocolPorts. Named Config соответствующего alias в plugins.conf — штатный способ разделить limits и protocol settings между instances. Host получает IConfig через существующий `IConfigManager::getPluginConfig(configuredName)` до bind/load; factory получает собственный IPluginConfig штатным путём.

Применяются существующие правила разрешения конфигурации и module-file fallback. Конфигурация подготавливается до запуска; для согласованных host/plugin settings разных aliases рекомендуется named Config. NULL от getPluginConfig означает отказ endpoint. Неуказанные host settings в возвращённом IConfig получают defaults; заданные значения проходят host validation.

Начальные settings: BindAddress=127.0.0.1 без альтернативных адресов; Workers default 4, диапазон 1–16; QueueSize default 16, диапазон 1–64; IOTimeout default 30000 ms, диапазон 1–300000 ms. Повтор host setting запрещён. Любой Port или TLS в Config отклоняется. Максимум 16 configured endpoints и суммарный budget 64 workers; ошибка одного endpoint сохраняет остальные позиции и native frontend.

Расширяемость проверяется одной тестовой библиотекой с двумя aliases и отдельными instances/configs. MissingPlugin между ними сохраняет третьему элементу третий порт. Проверка должна наблюдать и удержание общего module, когда один instance завершается, а второй ещё выполняет callback.

## SQL и транзакции

Текущий PgWire передаёт полный SQL в штатный prepare, без переписывания и разбиения. `SqlInfo` в библиотеке плагина ограниченно читает начальные лексемы для command tag и определения поддержанного профиля. Классификация используется только после успешного prepare; существующие getType/getFlags/input/output metadata и getAffectedRecords определяют native execution. Пустой ввод определяется лексически в плагине только совместно с реальной синтаксической ошибкой prepare. Незавершённый SQL и batch сохраняют ошибку движка.

Добавленные ранее origin/command/result/DDL items и `isc_dsql_empty_statement` удалены вместе с полями AST, изменениями grammar/lexer и native metadata test. Публичный analysis API и compiler observer не вводятся. В P5 ограниченная классификация transaction/session actions остаётся в плагине, а native prepare/execute определяют синтаксическую и семантическую допустимость. Fatal/cancel/permission errors нельзя маскировать классификацией.

Одна текущая application transaction — инвариант только PgWire-сессии. Native attachments, generic host и другие protocol plugins сохраняют возможности Firebird. Parse/Describe/statements/portals не создают отдельной application transaction; внутренние system/security/autonomous transactions не считаются нарушением.

## TLS и дальнейший ABI

Для P10 выбран OpenSSL 3 API с необязательной сборочной поддержкой TLS; штатная сборка без дополнительных endpoints не должна получать обязательную TLS-зависимость. Предлагаемый POSIX option — `--with-wire-tls=openssl` с default `no`; он ещё не реализован. При подключении зависимости нужны feature detection, POSIX link/install и отдельная Windows packaging проверка. Конкретная поддерживаемая версия поставки определяется при реализации TLS; наличие development headers на машине не подтверждает production profile.

TLS transport и сертификаты принадлежат host, решение об upgrade после SSLRequest — PgWire. Начальный IWireConnection задаёт partial I/O, timeout и shutdown/wakeup без передачи raw sockets/OpenSSL объектов. TLS upgrade будет добавлен через новую версию интерфейса. Этот документ не объявляет P0/P1 завершёнными.

## Второй срез P1: ABI и владение плагинами

HEAD до изменений — `e912b12560`. В CLOOP добавлены TYPE_WIRE_PROTOCOL=13 и четыре интерфейса: IWireProtocol, IWireSession, IWireConnection, IWireServerHost. Существующие номера типов и версии интерфейсов сохраняются; C++, Pascal и C bindings обновляются штатным `updateCloopInterfaces`. Точный контракт — [UsingWireProtocolPlugins.md](../doc/UsingWireProtocolPlugins.md).

ProtocolPlugin в `src/remote/server/wire` загружает один configured name за вызов PluginManager. Отсутствующий optional module становится ошибкой данного endpoint, без перехода к следующему имени и без изменения port mapping. Код не открывает sockets. После review Config::getPlugins(TYPE_WIRE_PROTOCOL) явно запрещает implicit list lookup; host читает raw settings парой и передаёт проверенные имена по одному. Direct loader call также отвергает запятую до обращения к PluginManager.

ProtocolSession удерживает plugin, host context и connection. Сначала освобождается возвращённый библиотекой session object, затем host-side references, затем plugin через releasePlugin. Это удерживает библиотеку при удалении endpoint, пока его session ещё работает. Raw session наружу не выдаётся. Создание sessions сериализовано на instance; run допускается один раз, stop может вызываться параллельно и будит transport через shutdown. Владелец wrapper обязан дождаться возврата run и всех stop перед его уничтожением.

Первый ABI срез предоставлял host diagnostics/stop; P2 добавляет authentication/attachment отдельной версией IWireServerHost, сохраняя область одного соединения. TLS upgrade остаётся будущим расширением. Ни количества attachments, ни числа транзакций generic ABI не ограничивает.

Тестовый модуль и отдельный runner используют настоящий PluginManager/libfbclient; stream в runner — управляемая имитация partial I/O и блокирующего ожидания. Тестовая библиотека собирается только отдельной целью и не устанавливается в production plugins. Эта проверка не подтверждает TCP transport, server shutdown или PgWire.

## Текущий срез P1: Linux/Super TCP host

`wire/os/posix/Server.cpp` реализует bind/listen/accept, фиксированные worker slots, ограниченную очередь и registry активных sessions. Запуск подключён к POSIX startup после native bind. Дополнительные listeners включаются только в Super; SuperClassic/Classic получают диагностику с сохранением native. P1 для остальных платформ/режимов остаётся незавершённым.

Host проверяет значения transport settings и конфликт с фактическим native listener, затем выполняет bind и только после его успеха загружает plugin. Полная очередь закрывает новое принятое соединение. Queued socket ещё не имеет plugin session; его ожидание ограничено IOTimeout. Session создаётся в свободном worker; factory callback сериализован, sessions одного instance могут работать параллельно.

`SocketConnection` владеет accepted descriptor с O_NONBLOCK/FD_CLOEXEC. recv/send возвращают partial progress, poll использует steady-clock deadline; EINTR не начинает новый timeout. Нулевой timeout выбирает host default, положительный только сокращает его. SIGPIPE подавляется для записи. Конкурентный shutdown вызывает SHUT_RDWR без close; descriptor закрывается при последнем release, поэтому ожидающий syscall не получает повторно использованный fd. Transport пока plaintext; whole-frame/startup deadlines реализует codec PgWire в P2.

В preproviders host прекращает accept, будит active transports и очищает очередь, запрашивает plugin stop без join. В postproviders задан один общий deadline 5 секунд для acceptors/workers всех endpoints. После завершения callbacks освобождаются session/endpoint/plugin owners. При превышении deadline callback сообщает ошибку и удерживает owners вместо выгрузки работающего кода. Это не средство остановки произвольно зависшего доверенного plugin callback.

Diagnostics содержат alias и port; plugin log ограничивается 512 input bytes, непечатные bytes заменяются, пользовательский текст не становится printf format. Пароли и SQL parameters plugin передавать не должен.

Отдельные проверки реального POSIX transport и `wire_host_smoke.py` используют изолированный runtime, один сервер с native TCP/SQL и двумя test aliases, queue/timeout и shutdown. Конфигурационная часть должна проверять штатный getPluginConfig и fallback вместе с host setting validation. Test echo выдаёт configured label и возвращает bytes; PostgreSQL framing/authentication/SQL он не реализует. Результаты каждого запуска фиксируются только в VALIDATION после выполнения. P2 добавляет PgWire, общий auth service и локальный attachment; BEGIN/$n остаются будущей работой parser/DSQL, SQL execution начинается в P3.

## Уточнения после review второго среза

ProtocolSession::stop сначала вызывает transport shutdown, затем plugin stop. Это устраняет зависимость пробуждения read от callback плагина, в том числе цикл с ошибочно удерживаемым mutex. Требование неблокирующего plugin stop сохраняется; fixture с mutex намеренно нарушает его, чтобы проверить защитный порядок. Произвольное зависание кода внутри доверенного модуля этим не изолируется.

Native conflict проверяется Server через getsockname borrowed listener после native bind: `-p` и service-name resolution уже разрешены. IPv4 wildcard/127.0.0.1, IPv6 wildcard с IPv4 support и соответствующий mapped address пересекаются с дополнительным loopback listener. Unix и IPv6-only native не резервируют этот IPv4 port. При ошибке определения metadata дополнительный host не запускается; native socket не изменяется. Сравнение с сырым RemoteServicePort не добавляется в библиотечный loader, не имеющий startup context.

## P2: PgWire startup и host authentication — Linux/Super PASS

База среза — `69712ca686`. `src/plugins/wire/pg/Protocol.{h,cpp}` реализует codec protocol 3.0, `PgWire.cpp` — plugin/session. Production build definitions POSIX/CMake создают отдельный PgWire module; его объекты не входят в daemon или Remote_Common. Windows host wiring/runtime пока отсутствуют.

IWireServerHost версии 4 добавляет attachDatabase/detachDatabase. Возвращённый attachment borrowed, принадлежит host до detach или конца run; plugin не вызывает его detach/drop/release напрямую. Child interfaces сохраняют штатное владение и закрываются до host detach. Detach инвалидирует borrowed handle даже при ошибке; host убирает оставленные resources после run. Cancellation использует host ownership, а plugin stop остаётся неблокирующим.

`DatabaseAliases` в endpoint Config — точный comma-separated allowlist; пустое значение запрещает attachments. Host использует существующий getPluginConfig, без нового метода ConfigManager или собственного resolver. `Authentication.cpp` выполняет in-memory Srp256 exchange при разрешении обоих AuthClient/AuthServer базы, максимум 16 rounds и 64 KiB на auth payload; EDS не изменяется. Alias и security DB должны разрешаться локально; Providers security DB должен включать Engine14, иначе helper отказывает до Srp256. RemoteAccess сохраняется. Новый DPB содержит проверенный auth block, UTF8/dialect 3, engine-only Providers и реальный peer. MON$REMOTE_PROTOCOL показывает TCPv4, MON$REMOTE_VERSION — configured endpoint alias, обычно PgWire.

Host проверяет UTF8: alias/user/application_name до 255 bytes, нормализованный Srp256 login до 63 bytes, protocol alias metadata до 63 bytes. Пароль host получает с явной длиной до 4096 bytes без NUL; codec ограничивает Password body 4096 bytes вместе с завершающим NUL, поэтому фактический wire password короче. Буферы пароля затираются после использования. Исключения и клиентские diagnostics не раскрывают raw credentials/status text.

Startup ограничен 16 KiB, typed frame — 1 MiB; limits включают поле length, но не typed tag. User обязателен; отсутствующий database равен user и всё равно проверяется allowlist, явно пустой database отклоняется. Допустимы application_name и отсутствующий либо точный UTF8 client_encoding. Неизвестные keys, options и replication явно отклоняются. SSL/GSS получают по одному N, повторные requests отвергаются. Другие версии получают FATAL 0A000; minor-version negotiation и BackendKeyData/CancelRequest ещё не реализованы.

AuthenticationOk отправляется только после успешного attach, затем реальные ParameterStatus и ReadyForQuery I. Version token — `16.0 (Firebird pgwire prototype)`, DateStyle — `ISO, MDY`. В зафиксированном P2 каждый структурно корректный Query получал 0A000 + ReadyForQuery I без engine prepare. Текущий P3 заменяет эту временную заглушку executor ниже; исторический startup PASS не подтверждает его выполнение.

IOTimeout задаёт whole-frame deadline и единый startup network budget, включая SSL/GSS, Password и startup replies; partial progress не обновляет budget. Это не hard preemption синхронного auth plugin/provider attach callback до появления attachment handle. Между callbacks проверяется stop; shutdown даёт providers возможность завершить операции, затем действует общий worker deadline. Пределы сетевого ожидания не выдаются за гарантию завершения произвольного кода plugin за тот же срок.

Добавлены codec tests с независимыми literal wire bytes и `pgwire_startup_smoke.py` для реального PgWire, authentication, monitoring, ошибок и cleanup рядом с native. Целевой runtime `/tmp/firebird-pgwire-p2-startup-04/summary.json` зафиксировал PASS всех 52 startup checks с libpq 14.24. Server syscall trace содержит 0 backend connect attempts и 51 явно учтённое OS/NSS Unix обращение; завершение без forced kill заняло около 0.165 s. Команды, сборка и границы покрытия записаны в [VALIDATION.md](VALIDATION.md). P2 Startup не закрывает M0; SQL execution требует P3.

## Исторический P3: engine SELECT и origin metadata — Linux/Super M0 PASS

База среза — `d072686153`. `Execute.cpp` вызывает public attachment/transaction/statement interfaces на borrowed host attachment. SQL из Query передаётся целиком, с явной byte length и dialect 3; исключается лишь wire terminator. DsqlDmlStatement сохраняет исходный AST kind до doDsqlPass, `dsql.cpp` выдаёт новый origin item через существующий getInfo. `inf_pub.h` и generated Pascal constants согласованы; новый plugin/config ABI для metadata не требуется.

До openCursor проверяются origin/type/flags, отсутствие input parameters, result count/length/offsets и codecs. Первые codecs — SMALLINT/INTEGER/BIGINT при scale=0/subtype=0, NULL, locale-independent text; OID 21/23/20 и typlen 2/4/8. Имена берутся из alias metadata, table OID/attribute number равны 0, typmod=-1: column-origin mapping не вводится. RowDescription и DataRow передаются потоком; полного materialized result set нет.

Каждый Query создаёт одну application transaction READ COMMITTED/write/record-version/nowait; native ReadConsistency configuration сохраняется. Prepare, openCursor и fetch используют один handle. После EOF закрываются cursor/statement и выполняется commit; только затем отправляется CommandComplete `SELECT n`. Ошибка приводит к rollback; cleanup использует отдельный status и не подменяет первичную диагностику. Неопределённый commit или failed cleanup завершают session. IOTimeout задаёт также engine statement timeout; синхронный prepare не имеет обещанного hard deadline.

Внутренний `ErrorStatus.cpp` определяет SQLSTATE по полному структурированному status vector, без анализа SQL или локализованного сообщения. Валидный explicit SQLSTATE используется как fallback, с приоритетом native shutdown/cancellation/permission причин; `src/plugins/wire/pg/tests/ErrorStatusTest.cpp` проверяет длинные vectors и этот приоритет. Parser errors дают 42601, неизвестная relation — 42P01, permission — 42501; shutdown/cancel/resource/invalid-handle причины имеют приоритет. Ошибки transport и сетевой timeout закрывают поток без дополнительного ответа, который мог бы попасть в частично записанный frame. EMPTY marker отсутствует, пустой ввод остаётся parser error.

`src/dsql/tests/StatementOriginTest.cpp` — отдельный native API executable: создаёт disposable БД, проверяет 21 statement kind дважды, getType/flags, combined/single/truncated getInfo и отсутствие исполнения DML при prepare. Покрыты SELECT/WITH/UNION/FOR UPDATE, INSERT/UPDATE/DELETE/MERGE/UPDATE OR INSERT RETURNING и selectable EXECUTE BLOCK. Independent attachment наблюдает committed fixture после проверок. CLI: `statement_origin_test <new-database-name>`, optional credentials через ISC_USER/ISC_PASSWORD; существующая БД не перезаписывается.

Для реальных client runs подготовлен `/tmp/firebird-pgwire-p3-deps`: extracted Ubuntu libpq-dev 14.24 без установки в систему, системный runtime libpq 14.24 и venv с psycopg2-binary 2.9.11, compiled/runtime libpq 17.6 (170006). C libpq и psycopg2 проверены отдельно с этими версиями. Query harness прошёл 13 групп, включая независимый metadata executable (21 случай в двух проходах), actual C/Python drivers, guards/transactions/errors/rights/disconnect, shutdown активного SQL и missing/occupied endpoint. Startup regression прошёл 52 проверки, native baseline — 9; full make и codec/error unit tests прошли. Trace показал 0 backend connect и 51 OS/NSS обращение, shutdown занял 0.114–0.164 s без forced kill. Детали и команды находятся в VALIDATION. Инъекции commit/close/free failures и отдельное истечение engine execution timeout ещё не выполнялись; полная CMake-сборка и Windows не проверены.

## Исторический P4 до refactor: Simple Query, result codecs и EMPTY

База среза — `03be167a24`. Исполнитель принимает SELECT, INSERT/UPDATE/DELETE/MERGE/UPDATE OR INSERT, DDL и EXECUTE BLOCK/PROCEDURE по metadata движка. SQL передаётся в prepare целиком. Новые getInfo items 35–38 описывают исходную команду, форму результата NONE/SINGLETON/CURSOR и DDL operation/object; origin 34 и legacy type/flags сохраняются. Команда сохраняется до transformations RETURNING, DDL subtype задаётся grammar. Эти дополнительные public values удалены последующим refactor, описанным ниже. Неописанный wrapper USING остаётся UNKNOWN/0A000; CALL нормализован движком как EXECUTE PROCEDURE. Transaction/session metadata пока задаёт широкую категорию, а её исполнение и детализация остаются P5.

ResultCodec отделён от lifecycle executor. Он проверяет input/output metadata до выполнения: неподдержанные типы и параметры дают 0A000 без побочных эффектов. SMALLINT/INTEGER/BIGINT передаются как OID 21/23/20; BOOLEAN — 16; CHAR/VARCHAR — 1042/1043; NUMERIC/DECIMAL на SHORT/LONG/INT64 — 1700. Точные числа форматируются целочисленно, включая INT64_MIN, без floating-point conversion. SQL NULL отличается от пустой строки. Для CHAR запрашивается varying fetch layout движка: сохраняются logical CHAR OID и пробелы по character length, без удаления trailing spaces и без UTF8 byte padding. Разрешены UTF8/NONE/ASCII, результирующие bytes проверяются на valid UTF8 и отсутствие NUL. Для всех колонок table OID/attribute number 0 и typmod=-1; extended codecs и column-origin mapping не добавляются.

Каждый Query использует одну implicit READ COMMITTED transaction для prepare и исполнения. Cursor results читаются до EOF, singleton outputs — через execute. RETURNING сохраняет исходный DML command tag; число изменённых записей берётся из getAffectedRecords, независимо от числа возвращённых строк. SELECT использует число DataRow. INSERT выдаёт `INSERT 0 n`, UPDATE/DELETE/MERGE — соответствующее имя и count, согласно [PostgreSQL message formats](https://www.postgresql.org/docs/current/protocol-message-formats.html). Firebird-specific tags остаются явными: `UPDATE OR INSERT n`, `EXECUTE BLOCK`, `EXECUTE PROCEDURE`, `RECREATE ...`, `CREATE OR ALTER ...`; libpq PQcmdTuples не обязан понимать нестандартные tags. DDL action/object не извлекаются из SQL-текста.

Успешный CommandComplete отправляется после закрытия cursor/statement и реального commit. Ошибка fetch/execute приводит к rollback; ошибка commit закрывает соединение с FATAL без CommandComplete/ReadyForQuery, поскольку исход commit нельзя представлять как успешный idle cycle. При execute новый transaction handle сохраняется до проверки status: API может потребить прежний handle даже при ошибке. Ошибки transport закрывают поток без записи нового frame поверх частично отправленного ответа.

Только чистый tokenless EOF получает дополнительный `isc_dsql_empty_statement` в прежней цепочке `isc_sqlerr/-104` и `isc_command_end_err2`. Существующий lexer различает пустой текст/comments, незавершённую команду и отдельный token `;`; frontend scanner отсутствует. Плагин проверяет marker в структурированном status vector и выдаёт EmptyQueryResponse, затем ReadyForQuery I. Legacy continuation и isql AUTOTERM продолжают видеть прежний код незавершённой команды. UNIQUE/FK/CHECK errors получают соответственно 23505/23503/23514 по native codes. Общий `isc_not_valid` обозначает как NOT NULL, так и domain validation, поэтому возвращается честный общий 23000 без анализа локализованного сообщения.

P4 unit/native/runtime checks и фактические результаты фиксируются в [VALIDATION.md](VALIDATION.md). Явные транзакции I/T/E, session controls, Extended Query, PostgreSQL grammar, TLS и другие server platforms остаются последующими этапами.

## Refactor P4: классификация только в PgWire

База среза — `26d4cc3704`. Прямое уточнение пользователя разрешило ограниченный лексический анализ в плагине и потребовало удалить изменения ядра для дополнительных metadata, включая origin. DSQL, Parser, grammar, inf_pub.h и JRD message definitions возвращены к состоянию до P3/P4 metadata; generated Pascal constants регенерируются штатно. Native getType/flags и parser diagnostics сохраняют исходное поведение.

`SqlInfo.{h,cpp}` возвращает только данные для PgWire: completion tag, источник count (returned rows/affected records/none), поддержанность команды и признак пустого ввода. Scanner читает нужный начальный префикс с учётом границ слов и штатных whitespace/comments. Он не ищет RETURNING внутри SQL, не обходит тела PSQL/CTE, не разбивает batch и не заменяет syntax/parameters. WITH является SELECT по текущей Firebird grammar; ведущая скобка допустима для parenthesized SELECT. Эти правила классификации относятся к текущему native dialect, а не обещают поддержку будущего SQL-синтаксиса.

Executor сначала вызывает prepare оригинального текста. При успехе lexical tag применяется к подготовленному statement; при unsupported command возвращается 0A000 до execute. Native transaction types дополнительно проверяются старым getType; session commands с общим legacy DDL type отсекаются по префиксу. HAS_CURSOR выбирает openCursor/fetch; отсутствие cursor вместе с output columns выбирает execute с singleton output. Поэтому исходная команда больше не выводится из результата DSQL transformations.

EMPTY также проходит prepare. Только сочетание engine syntax error 42601 и полного ввода из whitespace/закрытых comments даёт EmptyQueryResponse после cleanup. Отдельный `;`, незакрытые comments/quotes и другие tokens не EMPTY. Block comments следуют текущему Firebird lexer без вложенности. Остальные ошибки сохраняют full-status mapping; запрос с синтаксической ошибкой не подменяется успешной классификацией префикса.

Имплицитные transactions, codecs, actual commit перед CommandComplete, rollback и FATAL при commit failure сохранены. Публичных metadata/getInfo/AST/observer API для протокола больше нет. Исторический metadata executable и его build targets удалены. Повторная Linux/Super приёмка прошла: полная сборка, четыре unit executables (284 SqlInfo checks), 12 Simple Query, 7 lifecycle, 52 startup и 9 native checks. Команды, свидетельства и ограничения — в [VALIDATION.md](VALIDATION.md).


## P5: транзакции и состояние сессии внутри плагина

База среза — `3657d6a5c1`. `QueryExecutor` живёт вместе с PgWire-соединением и владеет одной текущей application transaction. Statement/cursor принадлежат отдельному Query и закрываются до ответа; executor уничтожается до detach borrowed attachment. В I prepare получает NULL и использует внутреннюю system transaction движка. В T/E prepare получает текущую транзакцию: видит её изменения и не создаёт дополнительный application handle. Исходный SQL не переписывается.

`SqlInfo` добавляет внутренний `ControlKind`. После native prepare descriptor сверяется с существующим coarse getType. Поддержаны `SET TRANSACTION`, full `COMMIT`/`ROLLBACK`, native `RETAIN [SNAPSHOT]`, savepoints и явный whitelist native SET, `ALTER SESSION RESET`. `BEGIN` и PostgreSQL параметры `$n` не добавлены в grammar; `RETAINING` также не является native SQL spelling. Ни DSQL/parser, ни public metadata/API для этого этапа не расширялись.

Состояния ReadyForQuery отражают I/T/E. Ошибка в explicit block сохраняет реальную транзакцию и savepoints, переводя сессию в E. После native prepare обычная команда в E получает 25P02 без исполнения. Успешный `ROLLBACK TO` или `ROLLBACK RETAIN` возвращает T; full rollback возвращает I. COMMIT в E вызывает native rollback API и отвечает ROLLBACK/I. Повторный start отклоняется с 25001/E до execute. Full controls в I после успешного prepare являются no-op; retaining/savepoint controls без транзакции получают 25000.

Диагностика failed prepare в E использует узкий whitelist: только реальный `isc_dsql_relation_err` может стать 25P02. Missing column, syntax, permission, cancellation, resource и fatal errors сохраняют диагностику. Syntax имеет приоритет над missing relation, явный SQLSTATE класса 08 — fatal и также не маскируется. При ошибке executor проверяет валидность текущего handle; ошибка COMMIT/COMMIT RETAIN либо cleanup не выдаёт ложный ReadyForQuery и завершает соединение.

Implicit Query использует READ WRITE / READ COMMITTED READ CONSISTENCY / WAIT с lock timeout `ceil(IOTimeout/1000)` секунд. Commit завершается до CommandComplete. Statement timeout ограничен меньшим из IOTimeout и ненулевого attachment statement timeout: native request timeout иначе перекрывает `SET STATEMENT TIMEOUT`. Native timer охватывает DML; hard deadline для prepare, DDL, COMMIT/RESET и других control operations не обещается. Explicit SET TRANSACTION передаёт движку все параметры, включая AUTO COMMIT и NO AUTO UNDO, без отдельного разбора или изменения флагов.

Session controls исполняются штатно с текущим либо NULL handle. RESET в T откатывает и перезапускает реальную транзакцию, причём pointer интерфейса может остаться прежним; в I не создаёт application transaction, в E отклоняется с 25P02. Плагин не хранит между Query cursors, prepared statements или имена savepoints. Текущие ParameterStatus не описывают сбрасываемые настройки. При disconnect текущая транзакция откатывается перед detach.

При проверках обнаружен отдельный native Debug defect: INSERT в READ COMMITTED READ CONSISTENCY AUTO COMMIT вызывает assertion Savepoint.cpp:833 и через embedded isql без PgWire. SNAPSHOT AUTO COMMIT работает; его реальный durable effect проверяется отдельно. Ядро и параметры SQL не подменяются; воспроизведение и границы — в VALIDATION.

Добавлен `pgwire_transaction_smoke.py` с настоящими TCP clients, C libpq и независимым native observer; он проверяет состояния, видимость, savepoints, native transaction/session effects и реальные ошибки commit/reset через database triggers. Прошли сборка, четыре unit executables (487 classifier checks), 14 P5 групп и Simple Query/lifecycle/startup/native regressions. Результаты и ограничения покрытия зафиксированы в [VALIDATION.md](VALIDATION.md). Следующий этап P6 закрывает M1 отдельной полной приёмкой; реализация P5 сама по себе не означает готовность M1–M3.


## Удаление добавленных таймаутов

13 октября 2026 года, база `5f873c9eba`. По указанию пользователя полностью удалены IOTimeout, deadlines startup/auth/messages/output и истечение ожидания queued sockets. Из нового IWireConnection удалён аргумент timeout; C++/Pascal bindings перегенерированы. Сетевые операции ждут данные, EOF/ошибку или shutdown. Общий предел join при остановке сервера сохранён.

QueryExecutor больше не устанавливает statement timeout и не задаёт lock timeout в implicit TPB. ConnectionIdleTimeout, StatementTimeout и native session/transaction settings применяет движок. Сервер и protocol plugins требуется пересобирать вместе. Новые тестовые сценарии не добавлены; из существующих удалены проверки устранённых ограничений. Результаты сборки и применимых проверок записываются в [VALIDATION.md](VALIDATION.md).
