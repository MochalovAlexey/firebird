# Решения первого этапа реализации

Начало реализации: 9 октября 2026 года. База исходников — master Firebird 6 из BASELINE.md; HEAD до изменений — `d533d51ad2`. Фактические результаты сборки и проверок записываются отдельно в [VALIDATION.md](VALIDATION.md).

Текущий срез P1 добавляет Linux/Super TCP listeners, socket transport, ограниченные workers/queues и startup/shutdown, используя штатный getPluginConfig. После изменения конфигурационного пути нужна повторная приёмка; прежние результаты в VALIDATION относятся к записанным там ревизиям. Authentication/attachment service, production PgWire и TLS ещё отсутствуют. Windows и другие server modes не закрыты этим срезом.

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

Authentication и TLS upgrade пока отсутствуют в ABI и будут добавлены версионированно после реализации соответствующих host services. Host context уже имеет область одного соединения; будущая проверка credentials получит peer/TLS state от host. Ни количества attachments, ни числа транзакций этот ABI не ограничивает.

Тестовый модуль и отдельный runner используют настоящий PluginManager/libfbclient; stream в runner — управляемая имитация partial I/O и блокирующего ожидания. Тестовая библиотека собирается только отдельной целью и не устанавливается в production plugins. Эта проверка не подтверждает TCP transport, server shutdown или PgWire.

## Текущий срез P1: Linux/Super TCP host

`wire/os/posix/Server.cpp` реализует bind/listen/accept, фиксированные worker slots, ограниченную очередь и registry активных sessions. Запуск подключён к POSIX startup после native bind. Дополнительные listeners включаются только в Super; SuperClassic/Classic получают диагностику с сохранением native. P1 для остальных платформ/режимов остаётся незавершённым.

Host проверяет значения transport settings и конфликт с фактическим native listener, затем выполняет bind и только после его успеха загружает plugin. Полная очередь закрывает новое принятое соединение. Queued socket ещё не имеет plugin session; его ожидание ограничено IOTimeout. Session создаётся в свободном worker; factory callback сериализован, sessions одного instance могут работать параллельно.

`SocketConnection` владеет accepted descriptor с O_NONBLOCK/FD_CLOEXEC. recv/send возвращают partial progress, poll использует steady-clock deadline; EINTR не начинает новый timeout. Нулевой timeout выбирает host default, положительный только сокращает его. SIGPIPE подавляется для записи. Конкурентный shutdown вызывает SHUT_RDWR без close; descriptor закрывается при последнем release, поэтому ожидающий syscall не получает повторно использованный fd. Transport пока plaintext; frame/startup deadlines добавит protocol plugin.

В preproviders host прекращает accept, будит active transports и очищает очередь, запрашивает plugin stop без join. В postproviders задан один общий deadline 5 секунд для acceptors/workers всех endpoints. После завершения callbacks освобождаются session/endpoint/plugin owners. При превышении deadline callback сообщает ошибку и удерживает owners вместо выгрузки работающего кода. Это не средство остановки произвольно зависшего доверенного plugin callback.

Diagnostics содержат alias и port; plugin log ограничивается 512 input bytes, непечатные bytes заменяются, пользовательский текст не становится printf format. Пароли и SQL parameters plugin передавать не должен.

Отдельные проверки реального POSIX transport и `wire_host_smoke.py` используют изолированный runtime, один сервер с native TCP/SQL и двумя test aliases, queue/timeout и shutdown. Конфигурационная часть должна проверять штатный getPluginConfig и fallback вместе с host setting validation. Test echo выдаёт configured label и возвращает bytes; PostgreSQL framing/authentication/SQL он не реализует. Результаты каждого запуска фиксируются только в VALIDATION после выполнения. Следующий функциональный этап P2 добавит PgWire, общий auth service и локальный attachment; BEGIN/$n остаются будущей работой parser/DSQL.

## Уточнения после review второго среза

ProtocolSession::stop сначала вызывает transport shutdown, затем plugin stop. Это устраняет зависимость пробуждения read от callback плагина, в том числе цикл с ошибочно удерживаемым mutex. Требование неблокирующего plugin stop сохраняется; fixture с mutex намеренно нарушает его, чтобы проверить защитный порядок. Произвольное зависание кода внутри доверенного модуля этим не изолируется.

Native conflict проверяется Server через getsockname borrowed listener после native bind: `-p` и service-name resolution уже разрешены. IPv4 wildcard/127.0.0.1, IPv6 wildcard с IPv4 support и соответствующий mapped address пересекаются с дополнительным loopback listener. Unix и IPv6-only native не резервируют этот IPv4 port. При ошибке определения metadata дополнительный host не запускается; native socket не изменяется. Сравнение с сырым RemoteServicePort не добавляется в библиотечный loader, не имеющий startup context.
