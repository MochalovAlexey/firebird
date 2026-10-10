# Решения первого этапа реализации

Начало реализации: 9 октября 2026 года. База исходников — master Firebird 6 из BASELINE.md; HEAD до изменений — `d533d51ad2`. Фактические результаты сборки и проверок записываются отдельно в [VALIDATION.md](VALIDATION.md).

Linux/Super host P1 со штатным getPluginConfig прошёл повторную проверку, записанную в разделе VALIDATION о ConfigManager. Текущий P2 добавляет authentication/attachment service и отдельный PgWire module; целевые Linux/Super проверки P2 прошли по [VALIDATION.md](VALIDATION.md). SQL execution, TLS, Windows wiring/runtime и другие server modes этим срезом не закрыты.

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

M0 требует лишь минимального engine-origin kind через существующий getInfo: SELECT должен отличаться от RETURNING и selectable EXECUTE BLOCK до execution. Полные command/control/session metadata и EMPTY добавляются в P4/P5. SQL передаётся без изменения, frontend lexer отсутствует.

Отдельный публичный analysis API не закладывается. В E используется обычный prepare с текущей живой transaction, подробные metadata и приоритетная таблица ошибок. EMPTY выявляет штатный parser по чистому EOF без tokens; legacy diagnostics, включая isql AUTOTERM, сохраняются.

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

AuthenticationOk отправляется только после успешного attach, затем реальные ParameterStatus и ReadyForQuery I. Version token — `16.0 (Firebird pgwire prototype)`, DateStyle — `ISO, MDY`. Каждый структурно корректный Query пока получает 0A000 + ReadyForQuery I; пустой текст, BEGIN, совместимый SELECT и любой иной SQL не разбираются и не исполняются. Engine parser и M0 SELECT добавляются в P3.

IOTimeout задаёт whole-frame deadline и единый startup network budget, включая SSL/GSS, Password и startup replies; partial progress не обновляет budget. Это не hard preemption синхронного auth plugin/provider attach callback до появления attachment handle. Между callbacks проверяется stop; shutdown даёт providers возможность завершить операции, затем действует общий worker deadline. Пределы сетевого ожидания не выдаются за гарантию завершения произвольного кода plugin за тот же срок.

Добавлены codec tests с независимыми literal wire bytes и `pgwire_startup_smoke.py` для реального PgWire, authentication, monitoring, ошибок и cleanup рядом с native. Целевой runtime `/tmp/firebird-pgwire-p2-startup-04/summary.json` зафиксировал PASS всех 52 startup checks с libpq 14.24. Server syscall trace содержит 0 backend connect attempts и 51 явно учтённое OS/NSS Unix обращение; завершение без forced kill заняло около 0.165 s. Команды, сборка и границы покрытия записаны в [VALIDATION.md](VALIDATION.md). P2 Startup не закрывает M0; SQL execution требует P3.
