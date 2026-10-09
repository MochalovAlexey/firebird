# Решения первого этапа реализации

Начало реализации: 9 октября 2026 года. База исходников — master Firebird 6 из BASELINE.md; HEAD до изменений — `d533d51ad2`. Фактические результаты сборки и проверок записываются отдельно в [VALIDATION.md](VALIDATION.md).

## Первый срез P1

Первый срез регистрирует глобальные `WireProtocolPlugins` / `WireProtocolPorts` и реализует проверку позиционных пар. Он не загружает wire plugins и не открывает listeners. Оба параметра имеют пустое значение по умолчанию; существующие ConfigKey сохраняют номера, новые добавляются в конец.

Парсер конфигурации располагается в `src/remote/server/wire/EndpointConfig.h`. Это внутренний header-only компонент generic host; стандартные C++ types здесь не пересекают будущий plugin ABI. Unit tests входят в существующий `common_test` через `src/common/tests`, без создания отдельного test framework.

Список имеет строгий comma-separated формат с ASCII whitespace по краям. Пустые позиции, несовпадающие длины, нечисловые/нулевые/переполненные порты, дубли портов и configured names отклоняются. Для двух endpoints одной factory используются разные aliases в plugins.conf. Configured name не должен содержать разделители, позволяющие превратить один элемент в список при вызове PluginManager.

Пары формируются до загрузки модулей, без фильтрации неизвестных имён. Ошибка проверки не выдаёт частичный список. Будущий host при такой ошибке отключает дополнительную конфигурацию с диагностикой, сохраняя native startup. Проверка конфликта с фактическим native endpoint и bind относится к интеграции host, а не к чистому парсеру строк.

## Конфигурация endpoint

Порт задаётся только в WireProtocolPorts. Bind/TLS/limits размещаются в именованном Config соответствующего alias в plugins.conf. Host получает IConfig через `IMaster::getConfigManager()->getPluginConfig(configuredName)` до load/bind; factory получает собственный IPluginConfig штатным путём.

Начальная реализация будет использовать named Config blocks. Нельзя предполагать идентичность произвольного ConfigFile override в двух путях загрузчика; это требует отдельного теста/исправления. Явно отсутствующий Config и NULL не должны приводить к запуску с ослабленной default policy.

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

До подключения listeners нужно согласовать strict config resolution с кэшированным pluginsConf. Обычный getConfig перечитывает файл, а getPluginConfig и factory используют cached snapshot: отдельная свежая проверка не доказывает корректность фактически загружаемой конфигурации. Legacy fallback не меняется в этом срезе. Доступ к тому же snapshot, missing Config/NULL, ConfigFile и Port override остаются задачей следующей интеграции.

Следующие части P1: strict host settings, listeners, bounded workers/queues, registry, server startup/shutdown и native coexistence с активным дополнительным endpoint. Затем P2 добавит PgWire framing, общий auth service и локальный attachment. BEGIN/$n остаются отдельной будущей работой parser/DSQL.

## Уточнения после review второго среза

ProtocolSession::stop сначала вызывает transport shutdown, затем plugin stop. Это устраняет зависимость пробуждения read от callback плагина, в том числе цикл с ошибочно удерживаемым mutex. Требование неблокирующего plugin stop сохраняется; fixture с mutex намеренно нарушает его, чтобы проверить защитный порядок. Произвольное зависание кода внутри доверенного модуля этим не изолируется.

Native-port conflict остаётся обязательной проверкой следующего startup среза до загрузки модулей. Она должна использовать фактический native TCP endpoint с учётом `-p`, service-name resolution, отключённого TCP и bind/dual-stack. Сравнение только с сырым RemoteServicePort не добавляется в библиотечный loader, не имеющий startup context.
