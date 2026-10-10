# Проверки реализации

Дата: 9 октября 2026 года. HEAD до текущего среза P2: `69712ca686`, baseline master Firebird 6 `bf94598f8deefeb7cc4a8217ee43b3040d5d6650`. Предыдущие разделы сохраняют историю проверок.

Текущий этап: P2 — production PgWire plugin, framing, Startup, проверка credentials через Srp256 и локальный attachment. Целевые проверки Linux/Super прошли: codec, 52 проверки настоящего PostgreSQL TCP endpoint, libpq 14.24, регрессия host/plugin и native coexistence. Host использует существующий `IConfigManager::getPluginConfig`, интерфейс ConfigManager не расширяется. Query пока возвращает `0A000` и ReadyForQuery `I`; SQL execution начинается с P3, M0–M3 не приняты. Исходные Python acceptance cases не запускались против Firebird и требуют миграции по TEST_PLAN.md. Результаты P2 приведены в последнем разделе.

## Окружение и сборка

Linux x86-64, GCC 13.4.0, GNU Make 4.3, autoconf 2.71. Используется штатная POSIX developer/Debug сборка вне дерева исходников. ICU/zlib доступны; tommath/tomcrypt выбраны bundled. Отсутствие pkg-config не мешает этому configure workflow.

```sh
mkdir -p /tmp/firebird-pgwire-build
cd /tmp/firebird-pgwire-build
/home/aleksey/dev/my_firebird/firebird/autogen.sh --enable-developer --with-builtin-tommath --with-builtin-tomcrypt > configure-output.log 2>&1
make -j2 > build-output.log 2>&1
```

Configure и штатная baseline-сборка завершились с кодом 0. Финальный проход `make -j4 > baseline-final-build.log 2>&1` подтвердил сборку исходного baseline; конфигурационные изменения P1 применялись после baseline smoke. Логи расположены в указанной build directory и не входят в git.

## Native baseline

```sh
python3 pg_wire_protocol/tools/native_baseline.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-native-baseline-tcp-20261009
```

PASS. Отдельная копия runtime, собственные UTF8 database/security DB/locks, `ServerMode=Super`, `AuthServer=AuthClient=Srp256`, `WireCrypt=Required`. Версия сервера/isql — `LI-T6.0.0.2199-dev Firebird 6.0 Initial`. Проверено настоящее native TCPv4 соединение с созданным пользователем PGWIRE_TEST: SELECT 42, CURRENT_USER, MON$SERVER_PID, commit, rollback, отказ неверному паролю и успешный reconnect после отказа. Сервер остановлен штатно, exit 0.

Свидетельство: `/tmp/firebird-pgwire-native-baseline-tcp-20261009/summary.json` и соседние sanitized logs. Временный TCP port — 52813; для повторного запуска скрипт выбирает свободный порт и требует новый work directory. Пароль генерируется в памяти и не передаётся в command arguments или отчёт.

Первый запуск внутри sandbox был остановлен запретом создания socket (`EPERM`), до bootstrap. После разрешённого запуска вне sandbox проверка прошла; это ограничение среды, а не ошибка Firebird. Используемые рабочие экземпляры пользователя не изменялись.

## Проверка изменений P1

После добавления двух global parameters полная пересборка `make -j4 > p1-build-output.log 2>&1` завершилась успешно, exit 0.

Команды сборки/запуска конфигурационных tests:

```sh
cd /tmp/firebird-pgwire-build/gen
make -j4 TARGET=Debug /tmp/firebird-pgwire-build/gen/Debug/firebird/tests/common_test > /tmp/firebird-pgwire-build/p1-tests-build.log 2>&1
/tmp/firebird-pgwire-build/gen/Debug/firebird/tests/common_test --run_test=WireProtocolConfigTests --report_level=detailed --log_level=test_suite > /tmp/firebird-pgwire-build/p1-wire-config-tests.log 2>&1
/tmp/firebird-pgwire-build/gen/Debug/firebird/tests/common_test --run_test=CommonClassesSuite/ConfigFileTests --report_level=detailed --log_level=error > /tmp/firebird-pgwire-build/p1-config-regression.log 2>&1
```

| Проверка | Результат |
|---|---|
| Сборка common_test | PASS, exit 0 |
| WireProtocolConfigTests | PASS: 11 cases, 168 assertions |
| Существующие ConfigFileTests | PASS: 3 cases, 4 assertions |
| Native TCP smoke на изменённой сборке | PASS: все проверки baseline, shutdown exit 0 |
| Markdown links, Python syntax, git diff --check | PASS |

Новые tests проверяют global/default registration и невозможность per-database override; blank lists; сохранение позиций до загрузки; диапазон/overflow/формат портов; пустые элементы; несовпадающие длины; дубли; недопустимые разделители в configured name; отсутствие частичного результата при ошибке. Остальные suites common_test не входят в этот целевой запуск.

Повторный runtime запуск из корня workspace:

```sh
python3 pg_wire_protocol/tools/native_baseline.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-native-p1-20261009
```

Свидетельство: `/tmp/firebird-pgwire-native-p1-20261009/summary.json`; TCPv4 port 37677, Super/Srp256, версия `LI-T6.0.0.2199-dev`. Запуск использовал разрешённый локальный TCP вне sandbox; runtime и security database изолированы. Успех относится к native protocol, а не к пока отсутствующему PostgreSQL endpoint.

Первая конфигурационная часть P1 проверена. Результаты следующего среза ABI/loading приведены ниже; workers/listeners и интеграция lifecycle сервера остаются будущей работой. Source changes не затрагивают SQL parser и родной wire dispatch.

## Границы первого среза

Проверяются регистрация двух global settings и чистый parser позиционных списков. Проверки загрузки библиотек, listeners, native-port conflicts, TLS, SQL metadata и PgWire clients относятся к последующим частям P1–P3 и пока не выполнены.

Для будущего прямого C libpq harness отсутствуют development headers/linker symlink в стандартных путях этой машины; runtime libpq сам по себе их не заменяет. Это не препятствует текущей сборке Firebird и native smoke, но должно быть закрыто до клиентской приёмки M0.

## Второй срез P1: ABI, loading и session ownership

9 октября 2026 года, HEAD до изменений `e912b12560`. В том же Linux/Debug build добавлены TYPE_WIRE_PROTOCOL и четыре CLOOP интерфейса. Существующие номера plugin types и версии старых интерфейсов сохранены. C++/Pascal исходники сгенерированы штатным CLOOP; C header создаётся в build runtime, а не как новый tracked source.

```sh
cd /tmp/firebird-pgwire-build/gen
make TARGET=Debug updateCloopInterfaces > /tmp/firebird-pgwire-build/p1-abi-cloop.log 2>&1
cd /tmp/firebird-pgwire-build
make -j4 > p1-abi-build.log 2>&1
./config.status --file=gen/Makefile:builds/posix/Makefile.in --file=gen/make.shared.variables:builds/posix/make.shared.variables > p1-abi-make-config.log 2>&1
make -C gen -j4 TARGET=Debug wire_tests > p1-abi-tests-build.log 2>&1
make -j4 > p1-abi-final-build.log 2>&1
```

Все команды PASS, exit 0. Финальная сборка включает ProtocolPlugin.o в daemon. Для частичного config.status нужны явные `--file=output:input`: сокращённый вызов с именами targets в этом build не восстанавливает MAKE_SRC_DIR и завершается до генерации Makefile.

Новый C API проверен GCC с `-std=c11 -Wall -Wextra -Werror -fsyntax-only`: создание/запуск/остановка/release session, read/write/shutdown connection, значения TYPE_WIRE_PROTOCOL/TYPE_COUNT. Проверочный source — `/tmp/firebird-pgwire-build/wire_abi_c_check.c`. Pascal binding сгенерирован; Pascal compiler отсутствует, отдельная компиляция им не проводилась. Windows и другие server modes в этом срезе не проверялись.

### Настоящий PluginManager и тестовая библиотека

```sh
cd /home/aleksey/dev/my_firebird/firebird
python3 pg_wire_protocol/tools/wire_plugin_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-wire-abi-final-20261009
```

PASS, exit 0. Свидетельства — `summary.json`, `events.log`, `stdout.log`, `stderr.log` в этом work directory. Отдельные firebird.conf/plugins.conf/locks; runner использует пересобранный libfbclient и динамически загруженный `tests/libWireTestPlugin.so`. Он не поднимает сервер и не открывает TCP; byte stream реализован mock-классом в runner.

| Проверка | Результат |
|---|---|
| First и Second: одна библиотека/factory, разные named Config и instances | PASS; один module.load |
| Missing между ними | PASS; отклонён, Second сохранил порт 31003 |
| Required missing module и неверный RegisterName | PASS; отказ загрузки |
| Factory NULL/error, createSession NULL/error и object+error, run error | PASS; исходная injected error сохранена, session references освобождены |
| Недостаточная версия IWireProtocol/IWireSession | PASS; isc_interface_version_too_old до первого вызова нового метода, объекты освобождены |
| Partial read/write, EOF и повторный run | PASS; bytes сохранены, второй run отклонён |
| Удаление endpoint до завершения session | PASS; host/connection/plugin удерживаются wrapper |
| First освобождается при активном run второго alias | PASS; final callbacks Second остаются валидными |
| Stop до run, repeated stop, stop при blocked read | PASS; ожидание прервано менее чем за 1 секунду, shutdown идемпотентен |
| Session release → plugin release → module cleanup/unload | PASS; библиотека выгружена при явном fb_shutdown после освобождения sessions |

Отдельно прошёл `make TARGET=Debug run_wire_tests` из build `gen`; лог — `p1-abi-make-smoke.log`. После добавления version cases тестовые binaries пересобраны (`p1-abi-tests-final-build.log`), итоговый ручной запуск приведён выше. Targets имеют явную зависимость от актуального libfbclient; dry-run с изменённым PluginManager.cpp подтвердил его пересборку (`p1-abi-library-dependency.log`). Тестовая библиотека не включена в production plugin/install targets. Проверены callback lifetime и loader shutdown в runner; это не server shutdown с работающими TCP/SQL sessions. Transport timeout/clamping и OS I/O failures mock-тест не проверяет.

### Регрессия и границы

После финальной сборки пересобран common_test и повторены прежние два фильтра: WireProtocolConfigTests — 11 cases / 168 assertions PASS; ConfigFileTests — 3 cases / 4 assertions PASS. Логи — `p1-abi-common-tests-build.log`, `p1-abi-config-tests.log`, `p1-abi-config-regression.log`.

```sh
python3 pg_wire_protocol/tools/native_baseline.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-native-abi-20261009
```

PASS: все 9 checks, Srp256/Super/TCPv4, SELECT, identity, commit/rollback, wrong password, reconnect и штатный shutdown exit 0. Временный port 41291; сервер/isql `LI-T6.0.0.2199-dev`. Запуск выполнен с разрешённым локальным TCP вне sandbox на отдельной копии runtime/security DB; сервер после проверки остановлен. Результат — `/tmp/firebird-pgwire-native-abi-20261009/summary.json`.

ABI/loader/session ownership проверены для этого среза. Python AST, локальные ссылки документации и git diff --check — PASS. До завершения P1 остаются strict configuration на общем cached snapshot PluginManager, bind/listeners, resource limits, registry/workers и интеграция startup/shutdown. Auth/TLS/engine services пока отсутствуют; M0 не принят. Native smoke подтверждает прежний native путь при отключённых дополнительных endpoints, а не сосуществование с ещё отсутствующим TCP listener.

## Исправления по review второго среза

9 октября 2026 года, HEAD до изменений `0fd7261a53`. Transport shutdown перенесён перед plugin stop. Implicit `Config::getPlugins(TYPE_WIRE_PROTOCOL)` теперь отказывает: positional settings должны проверяться вместе, затем loader получает одно имя. Direct loader name с запятой отвергается до обращения к PluginManager.

- Полная `make -j4` — PASS (`p1-review-build.log`).
- Пересборка wire_tests/common_test — PASS (`p1-review-tests-build.log`, финальное уточнение fixture — `p1-review-tests-final-build.log`).
- WireProtocolConfigTests — 12 cases / 171 assertions PASS; ConfigFileTests — 3 cases / 4 assertions PASS (`p1-review-config-tests.log`, `p1-review-config-regression.log`).
- Real-plugin smoke — PASS; итоговые `summary.json` и `events.log` в `/tmp/firebird-pgwire-review-final-20261009`. Команда прежняя, с новым `--work-dir`.
- StopMutex fixture намеренно берёт в stop mutex, удерживаемый вокруг read. Stop завершается менее чем за 1 секунду; отдельно проверяется, что mock read не вышел по своему страховочному timeout. Это защитная проверка порядка, а не разрешение блокирующего plugin stop в ABI.
- Для имени `First,Second` отказ получен до module entrypoint. Marker `host.valid_load_begin` предшествует единственному module.load.
- Проверка чувствительности тестов: отдельные копии loader под `/tmp` с прежним порядком stop и разрешённой запятой отклонены harness. Первая достигает страховочного timeout, вторая преждевременно загружает модуль. Рабочие исходники и binaries при этом не заменялись; итог — `/tmp/firebird-pgwire-review-mutants-20261009/summary.json`.
- Native TCP smoke — PASS, все 9 checks, shutdown exit 0. Изолированная копия `/tmp/firebird-pgwire-native-review-20261009`, port 51649, Super/Srp256/TCPv4, версия `LI-T6.0.0.2199-dev`.

На момент этого среза native-port conflict ещё не проверялся кодом: server startup/listeners оставались следующим срезом. ARCHITECTURE/PLAN/TEST_PLAN уточнены: до загрузки модулей нужны фактические native endpoints с учётом `-p`, service-name resolution, disabled TCP и bind/dual-stack. Результат следующего среза приведён ниже.

## Третий срез P1: strict Config и Linux/Super TCP host

Этот раздел сохраняет результаты первоначального варианта host. По решению пользователя расширение ConfigManager и отдельный strict resolver затем удалены; соответствующие config tests ниже относятся только к тому варианту. Актуальное поведение и повторная проверка описаны в следующем разделе.

9 октября 2026 года, HEAD до изменений `3e9fa366e2`. В той же Linux/Debug сборке реализованы строгий lookup named Config на cached snapshot PluginManager, TCP host, POSIX transport и startup/shutdown integration. `IConfigManager` расширен до версии 4 с сохранением legacy lookup; C++, Pascal и C bindings обновлены штатным `updateCloopInterfaces`.

```sh
cd /tmp/firebird-pgwire-build
./config.status --file=gen/Makefile:builds/posix/Makefile.in --file=gen/make.shared.variables:builds/posix/make.shared.variables
make -j4 > p1-host-checked-build.log 2>&1
make -C gen -j4 TARGET=Debug wire_tests /tmp/firebird-pgwire-build/gen/Debug/firebird/tests/common_test > p1-host-checked-tests-build.log 2>&1
gen/Debug/firebird/tests/common_test --run_test=WireProtocolConfigTests --report_level=detailed --log_level=error > p1-host-config-tests.log 2>&1
gen/Debug/firebird/tests/common_test --run_test=CommonClassesSuite/ConfigFileTests --report_level=detailed --log_level=error > p1-host-config-regression.log 2>&1
```

Все команды PASS. Полная штатная сборка включает host/transport в daemon. WireProtocolConfigTests: 12 cases / 171 assertions; существующие ConfigFileTests: 3 cases / 4 assertions. C binding проверен `gcc -std=c11 -Wall -Wextra -Werror -fsyntax-only` для нового lookup, его версии/flag и прежних wire interfaces; sources — `wire_config_c_check.c` и `wire_abi_c_check.c` в build directory. Pascal compiler отсутствует.

Из корня workspace выполнены:

```sh
python3 pg_wire_protocol/tools/plugin_config_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-plugin-config-20261009
python3 pg_wire_protocol/tools/wire_plugin_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-plugin-host-20261009
LD_LIBRARY_PATH=/tmp/firebird-pgwire-build/gen/Debug/firebird/lib \
  /tmp/firebird-pgwire-build/gen/Debug/firebird/tests/wire_socket_test
python3 pg_wire_protocol/tools/wire_host_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-host-tcp-20261009
python3 pg_wire_protocol/tools/native_baseline.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-native-host-disabled-20261009
```

| Проверка | Результат |
|---|---|
| Strict plugin config | PASS: 26 изолированных cases; missing/duplicate Plugin/Config, ConfigFile, nested reference, loader keys, includes/case, legacy fallback и bad arguments |
| Общий config snapshot | PASS: после инициализации cache и замены файла strict lookup и factory видят исходный named Config |
| ABI/plugin lifecycle regression | PASS: настоящая shared library, aliases/failure paths/ownership/stop, mock transport |
| POSIX socket transport | PASS: partial I/O, EOF, deadline/clamping, EINTR, sticky shutdown, пробуждение read, fd ownership/setup failure, TCP backpressure и broken peer без SIGPIPE |
| Native + два wire aliases | PASS: native SQL и два дополнительных TCP listener в одном процессе; одна test library, разные Config/instances; Missing между aliases не сдвигает ports |
| Ошибки endpoint | PASS: отсутствующий Config, Port override, remote bind, TLS, duplicates и недопустимые Workers/QueueSize/IOTimeout; валидные endpoints и native работают |
| Workers/queue/deadline | PASS: переполнение закрывает лишний socket, queued client получает worker после освобождения, queued socket истекает при занятом worker, idle read завершается по timeout |
| Server shutdown | PASS: SIGTERM пробуждает idle/partial sessions, session release предшествует plugin/module cleanup, exit 0 без forced kill |
| Native-port conflict | PASS: `-p` отличается от RemoteServicePort; конфликт с фактическим портом отклонён до module entrypoint, native SQL работает |
| Пустые списки по умолчанию | PASS: 9 native baseline checks — Srp256/TCPv4, вычисляемый SELECT, identity, commit/rollback, неверный пароль, reconnect и shutdown exit 0 |

Сетевые проверки запускались с разрешённым локальным TCP вне sandbox. Каждый server harness использует отдельные runtime/security DB/data/locks под `/tmp`; пароли генерируются в памяти и не попадают в argv или отчёт. Все тестовые серверы остановлены.

Свидетельства: `summary.json` и соседние logs/events в указанных work directories. Transport log — `/tmp/firebird-pgwire-build/p1-host-socket-tests.log`. В coexist run native port 50237, First 55369, Second 33753; shutdown 0.114 секунды. Native conflict run: фактический port 57697 при configured 45271, shutdown 0.114 секунды. Default-disabled native run: port 35041. Это временные порты конкретного запуска; harness выбирает свободные заново.

В CMake UNIX server source list добавлены три host objects, чтобы POSIX startup hook имел реализацию. `cmake -P /tmp/firebird-wire-cmake-check.cmake` — PASS: declarations включают каждый существующий source ровно один раз в UNIX fbserver, без добавления в client library/Windows target. Это проверка source lists; отдельная полная CMake-сборка, Windows, SuperClassic/Classic runtime, service-name/Unix-only/IPv6 native cases пока не проверялись. Проверка конфликта использует `getsockname`, но runtime PASS выше относится только к IPv4 loopback с `-p`. Общие максимумы 16 endpoints / 64 workers заданы кодом; их граничная нагрузочная приёмка остаётся отдельной проверкой.

После обновления комментариев firebird.conf финальный `make -j4 > p1-host-packaging-build.log 2>&1` — PASS, exit 0. Python AST четырёх tools, локальные ссылки 13 документов и `git diff --check` — PASS.

Начальный Linux/Super host проверен с тестовым byte echo. Он не реализует PostgreSQL framing, authentication или SQL. Следующий функциональный этап — P2; полное межплатформенное покрытие P1 и M0 этим результатом не закрываются.

## Упрощение конфигурации: существующий ConfigManager

9 октября 2026 года, тот же HEAD `3e9fa366e2`. По решению пользователя host переведён на существующий `getPluginConfig(name)` со штатными правилами разрешения конфигурации. Конфигурация подготавливается до запуска сервера. Новый метод, flag и strict resolver удалены, отдельные PluginConfigSmoke/tool и build target также удалены. Проверки transport settings и native-port conflict сохранены.

После штатного `updateCloopInterfaces` IDL, C++/Pascal bindings и `PluginManager.cpp` побайтно совпадают с HEAD. Generated C binding имеет прежнюю `IConfigManager_VERSION == 3`; `gcc -std=c11 -Wall -Wextra -Werror -fsyntax-only` для `wire_config_existing_api_check.c` и `wire_abi_c_check.c` в build directory — PASS.

`wire_host_smoke.py` теперь проверяет первый alias через module `.conf` fallback, второй — через named Config. Greeting первого alias подтверждает чтение настроек factory, проверки его Workers/QueueSize/IOTimeout — чтение host. Отдельный отказ из-за отсутствия named Config больше не требуется. Результаты strict resolver из предыдущего раздела не являются проверкой текущего кода.

```sh
cd /tmp/firebird-pgwire-build
./config.status --file=gen/Makefile:builds/posix/Makefile.in --file=gen/make.shared.variables:builds/posix/make.shared.variables > p1-config-api-revert-make.log 2>&1
make -C gen TARGET=Debug updateCloopInterfaces > p1-config-api-revert-cloop.log 2>&1
make -j4 > p1-config-api-revert-build.log 2>&1
make -C gen -j4 TARGET=Debug wire_tests > p1-config-api-revert-wire-tests-build.log 2>&1
cd /home/aleksey/dev/my_firebird/firebird
python3 -B pg_wire_protocol/tools/wire_plugin_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-plugin-config-revert-20261009
python3 -B pg_wire_protocol/tools/wire_host_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-host-file-fallback-20261009
```

Все команды PASS, exit 0. Plugin smoke подтвердил загрузку/ошибки/ownership и порядок stop/release с настоящей библиотекой. TCP host smoke: 11 checks PASS, включая file fallback для host и factory, named Config второго alias, native SQL/auth/transactions, queue/worker limits, timeouts, конфигурационные ошибки endpoint, конфликт фактического native-порта и shutdown. Оба тестовых сервера завершились с exit 0 без forced kill; summary/logs сохранены в указанных work directories. Сетевой запуск выполнен с разрешённым локальным TCP на изолированном runtime под `/tmp`.

Python AST оставшихся трёх tools, ссылки документации и `git diff --check` — PASS. Интерфейс ConfigManager, его реализация и сгенерированные C++/Pascal bindings в текущем рабочем дереве не отличаются от HEAD до среза TCP host.

## P2: PgWire Startup, authentication и локальный attachment

9 октября 2026 года, HEAD до изменений `69712ca686`. Новый production модуль `libPgWire.so` загружается через существующий wire plugin host. PgWire разбирает сообщения протокола; SQL не классифицирует и не изменяет. Host проверяет `DatabaseAliases` и per-database policy, выполняет обмен между Srp256 auth plugins в памяти и создаёт attachment через локальный Engine14. Новые методы добавлены только в `IWireServerHost` (version 4). `IConfigManager` остаётся version 3; его IDL и `PluginManager.cpp` не изменены.

### Сборка и codec

В прежней POSIX Debug build directory выполнены штатная генерация CLOOP и полная пересборка:

```sh
cd /tmp/firebird-pgwire-build
./config.status --file=gen/Makefile:builds/posix/Makefile.in --file=gen/make.shared.variables:builds/posix/make.shared.variables
make -C gen TARGET=Debug updateCloopInterfaces
make -j4 > p2-build.log 2>&1
make -C gen -j4 TARGET=Debug wire_tests pgwire_tests > p2-tests-final-build.log 2>&1
```

PASS. Последующие исправления auth helper пересобраны полной `make -j4`, финальный production build — `p2-auth-pool-build.log`, exit 0. Исходный `wire_socket_test` target захватывал лишний `Server.o`; его зависимости ограничены `SocketConnection.o` и общими объектами. Production PgWire входит в стандартный plugins target; codec test остаётся отдельным opt-in target.

```sh
LD_LIBRARY_PATH=/tmp/firebird-pgwire-build/gen/Debug/firebird/lib \
  /tmp/firebird-pgwire-build/gen/Debug/firebird/tests/pgwire_protocol_test \
  > /tmp/firebird-pgwire-build/p2-codec-tests.log 2>&1
```

PASS: golden bytes, fragmented/coalesced input, partial writes, EOF/transport errors, limits, NUL/UTF8, SSL/GSS отказ, сохранение SQL bytes и общие deadlines. Password buffer очищается после использования. Startup и Password используют один сетевой deadline; прогресс отдельными байтами не продлевает его.

Generated C API проверен `gcc -std=c11 -Wall -Wextra -Werror -fsyntax-only`: `/tmp/firebird-pgwire-build/p2_host_abi_c_check.c` и прежний `wire_abi_c_check.c`. PASS, включая версии ConfigManager/host и новые attachment methods. Pascal binding сгенерирован, Pascal compiler отсутствует. Проверка CMake source lists (`cmake -P /tmp/firebird-p2-cmake-check.cmake`) подтверждает server-only размещение host/authentication; это не полная сборка CMake.

### Настоящий PostgreSQL TCP endpoint

```sh
cd /home/aleksey/dev/my_firebird/firebird
python3 -B pg_wire_protocol/tools/pgwire_startup_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p2-startup-04
```

PASS: **52 checks**, exit 0. Использованы изолированные runtime, security database, application databases и временные loopback ports. Пароли генерируются тестом и не включаются в command line или отчёт. Сетевой запуск разрешён вне sandbox. Свидетельство — `/tmp/firebird-pgwire-p2-startup-04/summary.json`, sanitized logs и `server-connect.trace` рядом.

| Проверка | Результат |
|---|---|
| Настоящий libpq через ctypes в отдельном процессе | PASS: libpq 14.24 (`140024`), protocol 3, connect, два Query с `0A000`, transaction status IDLE |
| Raw Startup/authentication и две identities | PASS: native observer видит разных пользователей, Srp256, тот же server PID, TCPv4/peer port, UTF8 и application name |
| AuthenticationOk только после успешного attach | PASS: ON CONNECT trigger может отклонить вход после проверки credentials; AuthenticationOk в этом случае отсутствует |
| Неверный пароль, неизвестный/inactive user | PASS: отказ до AuthenticationOk, следующий допустимый connect работает |
| Alias allowlist, unknown alias, path/URI | PASS: только разрешённые локальные aliases; отсутствие database означает user, explicit empty database отклонён |
| RemoteAccess, AuthServer policy, remote security DB | PASS: отказ; клиентские параметры не меняют выбранную конфигурацию |
| Application Providers=Remote,Loopback | PASS: host всё равно создаёт локальный engine attachment |
| SSLRequest/GSSENCRequest и malformed packets | PASS: `N`, отказ повторного negotiation, bounds до payload allocation, version/options/keys/NUL/UTF8/EOF |
| Общие startup/password/query deadlines | PASS: частичные чтения с прогрессом не обновляют timeout; проверено с IOTimeout=1000 ms |
| Query recovery | PASS: `0A000` + Ready `I`, следующий Query обработан; SQL execution не заявляется |
| Cleanup и shutdown | PASS: session attachments закрыты; idle authenticated, password wait и incomplete startup не мешают остановке |
| Native protocol вместе с PgWire | PASS: SQL, credentials, commit и rollback |

Все три запуска сервера (основной, deadlines, syscall trace) завершились exit 0 без forced kill; shutdown занял около 0.165 секунды.

`strace` наблюдал `connect()` сервера и всех threads от запуска до shutdown, включая успешный/неуспешный auth, проверку policy и native probe. Backend network connects: **0**. Обнаружен 51 вызов к службам ОС через Unix sockets: 46 к `/run/systemd/userdb/io.systemd.DynamicUser` и 5 к `/var/run/nscd/socket`. Auditor разрешает только эти два точных NSS-пути и учитывает их отдельно; TCP/IPv6, другие Unix sockets, abstract addresses и нераспознанные sockaddr дают FAIL. Сохранённый trace и семь отрицательных синтетических примеров проверили это различие.

В предварительных запусках исправлены: использование stack-only default memory pool в heap `ClientBlock`, активация роли native observer через `SET ROLE`, классификация обращений к NSS в trace auditor. Итоговый PASS относится к полному повторному запуску после исправлений, а не к частично прошедшим попыткам.

### Регрессия и границы результата

```sh
python3 -B pg_wire_protocol/tools/wire_plugin_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p2-plugin-regression-20261009
python3 -B pg_wire_protocol/tools/wire_host_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p2-host-regression-20261009
```

PASS. Real-plugin regression проверил loading/errors/ownership/stop/release. TCP host regression: 11 checks, включая module `.conf` fallback, named Config второго alias, native auth/transactions, positional ports, queues/workers/timeouts, native-port conflict до module load и shutdown. Оба сервера host regression завершились exit 0 без forced kill. Python AST, локальные Markdown links и `git diff --check` также прошли.

Результат относится к Linux/Super и IPv4 loopback без TLS. Psycopg 2/3 в окружении отсутствуют; проверен именно runtime libpq через ctypes, C development headers для будущего harness по-прежнему отсутствуют. Windows, другие server modes и полная CMake сборка не проверялись. Security database должна быть локальной и иметь Engine14 среди Providers. Синхронный auth/attach до получения handle не имеет жёсткого прерывания по сетевому IOTimeout: helper проверяет stop между callbacks, а protocol повторно проверяет deadline перед AuthenticationOk. Проверки transport shutdown не доказывают preemption произвольного auth plugin.

P2 не выполняет engine prepare/execute. SELECT, типы результатов, транзакции, Extended Query, Cancel и TLS остаются в следующих этапах; M0 ожидает P3.
