# Проверки реализации

Текущая правка от 14 октября 2026 года — P8 расширенные codecs, база `598d487095`. Целевой P8 для Linux/Super прошёл приёмку: восемь групп codec harness, восемь unit executables и регрессии ниже. Следующий этап — P9, клиентская приёмка M2; полный M2 ещё не заявляется. В P8 ядро и parser не менялись. Известные ON CONNECT shutdown и READ CONSISTENCY + AUTO COMMIT ограничения сохраняются. Предыдущие разделы, включая удалённые IOTimeout и timeout cases, сохранены как история.

Дата: 12 октября 2026 года. HEAD до текущего P5: `3657d6a5c1`, baseline master Firebird 6 `bf94598f8deefeb7cc4a8217ee43b3040d5d6650`. Предыдущие разделы сохраняют историю проверок, включая впоследствии удалённые engine metadata и native metadata executable.

Текущий этап: **P5 прошёл целевую Linux/Super IPv4 loopback приёмку** с указанным ниже native AUTO COMMIT ограничением. Production changes ограничены PgWire; parser/DSQL/public metadata не менялись. Полный SQL передаётся штатному prepare без изменений. Прямые драйверы — C libpq 14.24 и psycopg2 2.9.11/libpq 17.6. Следующий этап — P6, серверная приёмка M1; M1–M3 не приняты. При проверках найден отдельный native Debug defect сочетания AUTO COMMIT с READ COMMITTED READ CONSISTENCY, воспроизводимый без PgWire.

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

## P3/M0: настоящий SELECT через движок — исторический Linux/Super PASS

9 октября 2026 года, HEAD до изменений `d072686153`. `Execute.cpp` передаёт исходные SQL bytes и длину в `IAttachment::prepare`, затем использует `IStatement::openCursor` и `IResultSet::fetchNext`. Одну implicit READ COMMITTED/write/nowait transaction разделяют prepare/open/fetch. После EOF закрываются cursor/statement и выполняется commit; только затем отправляется CommandComplete. Ошибка приводит к rollback, ошибка cleanup или commit — к завершению сессии. SQL lexer/splitter/rewriter в plugin не добавлен.

Новый `isc_info_sql_stmt_origin=34` сохраняет исходный AST category до DSQL transformations: SELECT=1, OTHER=0. В сочетании с прежними type/flags это позволяет отклонять DML RETURNING и selectable EXECUTE BLOCK до исполнения. Сам origin не означает read-only: SELECT может вызывать routines с эффектами. Legacy type/flags и ABI интерфейсов не меняются; Pascal constants перегенерированы.

Начальный result profile: SMALLINT/INTEGER/BIGINT при scale=0/subtype=0, NULL, text format, OID 21/23/20, engine aliases. Прочие output types и input parameters отвергаются по metadata до openCursor. RowDescription/DataRow/CommandComplete проверяют sizes/UTF8 до allocation и записи. Ошибка частичной записи завершает transport без дописывания нового frame. `ErrorStatus.cpp` рассматривает полный структурированный status vector, включая explicit SQLSTATE; строки и числовые аргументы не считаются кодами ошибок.

### Сборка и самостоятельные unit tests

```sh
cd /tmp/firebird-pgwire-build
./config.status --file=gen/Makefile:builds/posix/Makefile.in --file=gen/make.shared.variables:builds/posix/make.shared.variables
make -j2 > p3-final-build.log 2>&1
make -C gen -j2 TARGET=Debug pgwire_tests statement_origin_tests > p3-tests-build.log 2>&1
LD_LIBRARY_PATH=/tmp/firebird-pgwire-build/gen/Debug/firebird/lib \
  gen/Debug/firebird/tests/pgwire_protocol_test > p3-codec-tests.log 2>&1
LD_LIBRARY_PATH=/tmp/firebird-pgwire-build/gen/Debug/firebird/lib \
  gen/Debug/firebird/tests/pgwire_error_test > p3-error-tests.log 2>&1
```

Все команды PASS, exit 0. Полная пересборка после public header change записана в `p3-build-retry.log`; финальный проход и тестовые targets — в логах выше. Первый `make -j4` остановился из-за внутреннего compiler error GCC 13.4.0 в неизменённом `TimeZoneUtil.cpp`/`<atomic>`. Повторный `make -j2` прошёл без изменения этого кода.

Codec unit tests включают независимые literal bytes metadata/rows/tags, NULL/empty/UTF8, integer text, partial writes, signed metadata, exact bounds/field counts и обрыв DataRow по deadline. Error-status unit tests проверяют nested cause и explicit SQLSTATE за пределами первых 20 элементов, `cstring`, ложные code values внутри аргументов и приоритеты shutdown/cancel/permission. Оба executable сообщили PASS.

Production PgWire содержит отдельные Execute/ErrorStatus objects. POSIX build проверен; CMake definitions обновлены, полная CMake сборка не выполнялась. Pascal constants сгенерированы штатным `updateCloopInterfaces`; Pascal compiler отсутствует.

### Клиенты и изолированный runtime

Для прямого C test использованы настоящие headers из Ubuntu `libpq-dev 14.24-0ubuntu0.22.04.1`, извлечённого в `/tmp/firebird-pgwire-p3-deps/libpq-dev` без системной установки. Системный libpq — 14.24. В отдельном venv `/tmp/firebird-pgwire-p3-deps/venv` установлен psycopg2-binary 2.9.11, который использует свой libpq 17.6. Версии, SHA headers/source и build command фиксирует harness; ctypes-only smoke не подменяет C gate.

```sh
cd /home/aleksey/dev/my_firebird/firebird
python3 -B pg_wire_protocol/tools/pgwire_query_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p3-query-02 \
  --libpq-include-dir /tmp/firebird-pgwire-p3-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5 \
  --driver-python /tmp/firebird-pgwire-p3-deps/venv/bin/python \
  --statement-origin-test /tmp/firebird-pgwire-build/gen/Debug/firebird/tests/statement_origin_test
```

PASS: **13 групп проверок**, все четыре server phases завершились exit 0 без forced kill. Shutdown — 0.114–0.164 секунды, включая остановку с выполняющимся SELECT. Результаты и sanitized logs: `/tmp/firebird-pgwire-p3-query-02/summary.json` и соседние файлы. Использованы отдельные runtime/security DB/application databases/locks и временные TCP ports. Сетевые тесты и strace запускались с разрешением вне sandbox; рабочие экземпляры пользователя не менялись.

| Проверка | Свидетельство |
|---|---|
| Независимая engine metadata | `statement-origin.log`: 21 SQL case × 2 прохода prepare/free, SELECT/WITH/UNION, RETURNING, BLOCK, DDL/controls; legacy type/flags, cache, combined/single/truncated getInfo и отсутствие execution — PASS |
| Прямой C libpq | `libpq-direct-c.log`: compile headers/runtime 140024, PQexec, 42/OID23/alias answer, 49, integer limits/NULL, 0/many rows, command tags, errors/recovery — PASS |
| psycopg2 | `psycopg2.log`: 2.9.11, compile/runtime libpq 170006, explicit autocommit=True без connection context, значения/типы/NULL/rows/errors/recovery — PASS |
| Raw results и SQL boundary | 0/1/257 rows, UTF8/quoted aliases, integer metadata, partial/coalesced Query, comments/literals с BEGIN/$1/semicolon, batch parser refusal — PASS |
| Identity и права | CURRENT_USER подтверждён integer predicate; второй пользователь получает 42501 на таблицу и затем успешно выполняет свой SELECT — PASS |
| Refusal до execution | INSERT SELECT/UPDATE RETURNING с legacy SELECT cursor, INSERT VALUES RETURNING, selectable BLOCK и unsupported output — 0A000 без RowDescription; sequences не изменились, поэтому rollback не скрывает исполнения |
| Implicit commit/rollback | SELECT над процедурой сохраняет insert после успеха; division error после уже отправленной DataRow откатывает insert, CommandComplete отсутствует, следующий SELECT работает |
| Disconnect/active shutdown | Streaming SELECT действительно исполнился по sequence marker; disconnect откатывает insert и удаляет attachment. Долгий SELECT виден в MON$STATEMENTS и оставляет sequence marker; server shutdown отменяет работу и откатывает insert |
| Ошибки endpoint | Отсутствующий production PgWire module и занятый дополнительный порт диагностируются; native SQL продолжает работать; при bind failure модуль ещё не загружен |
| Локальный engine route | Trace успешного auth/SELECT, неуспешного auth/policy и native observer: 0 backend connects; 51 OS/NSS Unix вызов по тем же двум точным разрешённым путям |

После query/error native monitoring подтверждает отсутствие оставшейся application transaction; после disconnect — отсутствие session attachment. Подготовленный SQL выполняется только после проверки origin/type/flags/input/output metadata. Batch с GEN_ID отвергнут parser до изменения sequence.

Первый запуск `p3-query-01` остановился при C connect: harness ожидал только готовности native listener. Перед driver suite добавлено ожидание успешного PgWire login/Terminate/cleanup и информативная диагностика libpq с удалением пароля. Полный повторный `p3-query-02` прошёл; production binaries между этими попытками не менялись. Первый неуспешный запуск не входит в PASS.

### Регрессия и итог M0

```sh
python3 -B pg_wire_protocol/tools/pgwire_startup_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p3-startup-01
python3 -B pg_wire_protocol/tools/native_baseline.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p3-native-01
```

PASS: startup regression — 52 checks, теперь с реальным SELECT/recovery; native baseline при отключённых wire plugins — 9 checks, прежние SQL/auth/commit/rollback. Все серверы остановлены с exit 0. Native также работал параллельно с PgWire во время query/startup suites. Python AST, локальные Markdown links и `git diff --check` прошли.

M0-01…08 закрыты для указанного Linux/Super loopback profile. Это не приёмка M1: DML/DDL/control statements, дополнительные codecs, EMPTY marker, Extended Query, PostgreSQL grammar, TLS и удалённый bind остаются последующим этапам.

Границы проверки: runtime Windows/Classic/SuperClassic и полная CMake сборка отсутствуют; ошибки close/free/commit не инъецировались, их порядок и consuming ownership проверены review. Истечение engine statement timeout отдельно не измерялось; проверены network deadlines и cancellation при active SQL shutdown. IOTimeout передаётся в IStatement::setTimeout после prepare и не является hard deadline для startTransaction/prepare/auth/cleanup/commit. C++/C tests и actual clients подтверждают текущий профиль, а не прозрачную работу произвольного PostgreSQL-приложения.

## Консольный SQL-клиент для ручной проверки — Linux PASS

9 октября 2026 года, HEAD `0d9546336f`. Добавлен `tools/pgwire_client.py`: Python stdlib/ctypes и настоящая libpq, Simple Query по TCP, повторные `-c`/`-f` на одном соединении и интерактивный ввод с отдельной командой `\g`. Ввод UTF8 передаётся целиком, включая CRLF внутри SQL literals. Клиент показывает результаты, OID, NULL, SQLSTATE и transaction state. Инструкция настройки и совместной проверки с native isql находится в [MANUAL_TESTING.md](MANUAL_TESTING.md). Production-код и серверные возможности этим изменением не расширяются.

Временный integration harness использует существующий `StartupHarness`, отдельную копию runtime/security DB, тестовую application database и свободные TCP ports:

```sh
python3 -B /tmp/pgwire_manual_client_smoke.py \
  --repository /home/aleksey/dev/my_firebird/firebird \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-manual-client-02
```

PASS: **15 проверок**, результат — `/tmp/firebird-pgwire-manual-client-02/summary.json`. Проверены результат 42/OID23/NULL, сохранение attachment между запросами, восстановление после SQL-ошибки, порядок смешанных `-c`/`-f`, UTF8 и сохранение CRLF в file/stdin, отсутствие разбиения SQL, `--stop-on-error`, ошибки authentication/file, интерактивные `\g`/`\p`/`\r`/`\q`. Унаследованные `PGHOSTADDR`/`PGSERVICE` не меняют выбранный endpoint; `PGTARGETSESSIONATTRS` не вызывает дополнительных SQL-проверок.

Через отдельное native TCP-подключение подтверждены тот же server PID и двусторонняя видимость committed changes: native INSERT читается PgWire-клиентом; INSERT внутри selectable procedure, вызванной через PgWire SELECT, виден native observer. Это проверяет implicit commit, а не поддержку прямого INSERT через PgWire. Сервер завершился exit 0 за 0.164 секунды без forced kill. Рабочая БД пользователя не изменялась.

Первый запуск `manual-client-01` прошёл 12 проверок; финальный `manual-client-02` повторил их после исправлений и добавления CRLF/environment cases. Python syntax, `--help`, локальные Markdown links и `git diff --check` также прошли. Новая проверка относится к утилите на Linux/Super loopback; серверная приёмка M0 и её ограничения приведены выше.

По запросу пользователя скрытый ввод пароля заменён на `-P PASSWORD` / `--password PASSWORD` (`-W PASSWORD` — дополнительное короткое имя). Аргумент имеет приоритет перед `PGPASSWORD`; отсутствие обоих даёт ошибку аргументов до подключения. `-d` / `--database` передаёт имя или путь буквально, однако production PgWire по-прежнему принимает только разрешённые alias. Прямое подключение по пути этим изменением не реализуется.

Повторный запуск той же команды с `--work-dir /tmp/firebird-pgwire-manual-client-03` — **18 проверок PASS**. Добавлены успешный вход с аргументом пароля при неверном `PGPASSWORD`, отказ неверному аргументу при правильном `PGPASSWORD` и отказ при отсутствии пароля без интерактивного prompt или загрузки libpq. Пароли не записываются в отчёт; тестовые argv credentials используются согласно выбранному интерфейсу клиента. Результат — `/tmp/firebird-pgwire-manual-client-03/summary.json`. Все прежние CLI/native checks повторены; рабочий экземпляр пользователя не менялся.

## P4 — Simple Query DML/DDL, codecs и EMPTY: исторический Linux/Super PASS

12 октября 2026 года, HEAD до изменений `03be167a24`. Полная POSIX Debug сборка выполнена в `/tmp/firebird-pgwire-build`; рабочий сервер пользователя и его базы не изменялись. Проверенный профиль — Linux x86_64, Super, IPv4 loopback, UTF8/dialect 3, отдельный production PgWire module и локальный Engine14. Публичные metadata items 35–38 и EMPTY diagnostic реализованы движком; SQL lexer/splitter/rewrite во frontend отсутствуют. Generated Pascal constants обновлены штатной сборкой.

### Сборка и unit tests

```sh
cd /tmp/firebird-pgwire-build
./config.status --file=gen/Makefile:builds/posix/Makefile.in \
  --file=gen/make.shared.variables:builds/posix/make.shared.variables
make -j2 > p4-build.log 2>&1
make -C gen -j2 TARGET=Debug pgwire_tests statement_origin_tests > p4-tests-build.log 2>&1
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_protocol_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_error_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_result_test
```

PASS: full build и все три executable unit tests. Protocol test сверяет EmptyQueryResponse с независимыми literal bytes; ErrorStatus test проверяет UNIQUE/FK/CHECK, полные status vectors и приоритет fatal/cancel/permission; ResultCodec test проверяет native buffer layout/bounds, integer limits, точное scaled numeric/INT64_MIN, boolean, NULL, CHAR/VARCHAR, UTF8/NUL и отказ неподдержанным metadata. Новая test link rule включает COMMON_LIB, поскольку internal UTF8/status helpers не экспортируются libfbclient.

Первый build выявил два использования запрещённого copy constructor StatusVector в новых engine diagnostics. Исправлено отдельным созданием и наполнением vector; полный повторный build прошёл. После успешной полной сборки production-код не менялся. Последующие изменения затрагивали ожидания тестов и документацию; финальный native test пересобран через `statement_origin_tests`, лог `p4-metadata-build.log`.

### Полная P4 приёмка

```sh
cd /home/aleksey/dev/my_firebird/firebird
python3 -B pg_wire_protocol/tools/pgwire_simple_query_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p4-simple-02 \
  --libpq-include-dir /tmp/firebird-pgwire-p3-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5 \
  --driver-python /tmp/firebird-pgwire-p3-deps/venv/bin/python \
  --statement-origin-test /tmp/firebird-pgwire-build/gen/Debug/firebird/tests/statement_origin_test
```

PASS: **12 групп**, результат `/tmp/firebird-pgwire-p4-simple-02/summary.json`. Harness создаёт копию runtime, отдельные security/application databases, пользователей, locks и свободные TCP ports. Использованы настоящие headers/runtime C libpq 14.24 и psycopg2 2.9.11 с libpq 17.6; Python connection работает с `autocommit=True`, без connection context. Сетевые проверки/strace выполнялись с разрешением вне sandbox.

| Проверка | Фактический результат |
|---|---|
| Native metadata | 31 DML/control/session + 46 DDL statements, два прохода: origin/command/result/DDL, legacy type/flags, cache reuse, single/combined/truncated getInfo, отсутствие execution при prepare — PASS |
| Native EMPTY | Пустой текст, whitespace/comments, explicit length; сохранение isc_dsql_error/isc_sqlerr/isc_command_end_err2, отсутствие marker у неполного SQL и `;`; PREPARE_REQUIRE_SEMICOLON и отдельный native isql AUTOTERM — PASS |
| Scalar codecs | Integer limits, BOOLEAN, CHAR с ASCII/кириллицей/пробелами, VARCHAR UTF8/emoji/пустая строка, NULL, exact scaled NUMERIC/DECIMAL, OID/size/typmod — PASS |
| DML и RETURNING | INSERT/UPDATE/DELETE, MERGE, UPDATE OR INSERT, 0/1/many affected records, singleton/cursor RETURNING, исходные tags; trigger effects не увеличивают внешний affected count — PASS |
| DDL и PSQL | CREATE/ALTER/DROP, RECREATE, CREATE OR ALTER, таблицы/индексы/views/domains/sequences/procedures/exceptions, EXECUTE BLOCK с/без результата и EXECUTE PROCEDURE — PASS |
| Native cross-visibility | Committed DML/DDL из PgWire видны отдельному native observer; после обычной ошибки записи откатываются, transaction не остаётся — PASS |
| Отказ до execution | Unsupported DOUBLE/BLOB/INT128, input parameters и controls, batch refusal; GEN_ID не меняется, поэтому rollback не скрывает исполнения — PASS |
| Ошибки и recovery | UNIQUE 23505, CHECK 23514, общий NOT NULL/domain 23000, parser 42601, продолжение того же соединения; division error после DataRow без CommandComplete и с rollback — PASS |
| Реальная ошибка commit | ON TRANSACTION COMMIT trigger вызывает exception после выдачи DataRow: FATAL XX000 → EOF, без CommandComplete/ReadyForQuery; native observer подтверждает rollback, новое PgWire соединение успешно — PASS |
| EMPTY по wire | EmptyQueryResponse/Ready I и следующий SELECT; незавершённый SQL и вложенный block comment получают parser error — PASS |
| Настоящие драйверы | C PQexec и psycopg2: DDL/DML/RETURNING, преобразование новых типов, tags, ошибки/recovery, EMPTY в C — PASS |
| Локальное исполнение | Syscall trace всех server threads от startup до shutdown: 0 backend connect attempts; 49 OS/NSS Unix calls по двум прежним явно разрешённым путям — PASS |

Обе server phases завершились exit 0 без forced kill; shutdown около 0.164 секунды. Логи metadata/C/Python/native AUTOTERM и trace находятся рядом с summary. Ранний `p4-simple-01` остановился на неверном ожидании native CALL: без output targets он имеет result NONE. В тестах также исправлены native syntax `CREATE OR ALTER SEQUENCE ... START WITH` и ожидание внешнего status prefix isc_dsql_error. SQL debug attempts обнаружили только неверное положительное ожидание вложенного block comment: текущий Firebird parser его не поддерживает. После исправлений отдельный debug run прошёл 10 SQL-групп; он не заменяет приведённый полный `simple-02` PASS. Production binaries для этих повторов не менялись.

### Lifecycle, startup и native regression

```sh
python3 -B pg_wire_protocol/tools/pgwire_lifecycle_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p4-lifecycle-02
python3 -B pg_wire_protocol/tools/pgwire_startup_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p4-startup-01
python3 -B pg_wire_protocol/tools/native_baseline.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p4-native-01
```

PASS: **7 lifecycle groups**, **52 startup checks**, **9 native checks**. Lifecycle runner повторно использует прежние P3 fixtures/methods: 0/1/257 rows, права второго пользователя, implicit commit/rollback, реальные streaming disconnect и active-query shutdown с rollback, cleanup attachments, missing module/occupied port при работающем native. Исторический полный `pgwire_query_smoke.py` содержит SELECT-only отказы P3; его запуск не выдаётся за регрессию текущего P4. Эти устаревшие отказы заменены успешными P4 cases в новом Simple Query harness.

Все server phases завершились exit 0 без forced kill, shutdown 0.114–0.164 секунды. Startup trace: 0 backend connects, 51 учтённый OS/NSS Unix call. Python syntax, локальные Markdown links и `git diff --check` прошли. MANUAL_TESTING обновлён двусторонним PgWire/native CRUD сценарием и примерами новых типов.

P4 принят для описанного профиля; **следующий этап P5 — explicit transactions I/T/E и session controls**. Сейчас каждый Query автоматически commit/rollback, а явные SET TRANSACTION/COMMIT/ROLLBACK/savepoints получают 0A000. BEGIN и `$n` остаются ограничениями grammar. Это ещё не M1: P5/P6 не завершены. Extended Query, расширенные codecs, TLS, Windows/другие server modes и full CMake build не проверены. Ошибки close/free не инъецировались, отдельное истечение engine statement timeout не измерялось. FK SQLSTATE проверен unit status vectors, отдельный TCP FK fixture не запускался. IOTimeout не является hard deadline для prepare/auth/cleanup/commit. Для рабочего экземпляра пользователя изменения вступят в силу после его пересборки и перезапуска.

## Рефакторинг P4: классификация внутри PgWire — Linux/Super PASS

12 октября 2026 года, HEAD до изменений `26d4cc3704`. Удалены дополнительные origin/command/result/DDL info items, поля и методы DSQL/AST, изменения grammar/lexer и `isc_dsql_empty_statement`. Pascal constants перегенерированы штатным CLOOP. Удалены `StatementOriginTest.cpp`, его build targets и зависимость Python harness от этого executable. Проверка ниже завершилась exit 0: затронутые файлы движка, public constants и их документация совпадают с состоянием до этих дополнений.

```sh
git diff --exit-code d072686153 -- src/dsql \
  src/include/firebird/impl/inf_pub.h src/include/firebird/impl/msg/jrd.h \
  src/include/gen/Firebird.pas doc/README.isc_info_xxx
```

Plugin-only `SqlInfo` определяет command tag, способ подсчёта строк и поддержанный профиль по ограниченному начальному префиксу. Полный неизменённый SQL всегда проходит штатный prepare первым. Выбор cursor/singleton execution использует существующие `getFlags()` и output metadata; affected records берутся из существующего API. EMPTY получается только при реальной синтаксической ошибке prepare и полностью tokenless тексте. Остальные ошибки движка сохраняют приоритет перед capability checks.

### Сборка и unit tests

```sh
cd /tmp/firebird-pgwire-build
./config.status --file=gen/Makefile:builds/posix/Makefile.in \
  --file=gen/make.shared.variables:builds/posix/make.shared.variables
make -C gen TARGET=Debug updateCloopInterfaces > p4-lex-cloop.log 2>&1
make -j2 > p4-lex-build.log 2>&1
make -C gen -j2 TARGET=Debug pgwire_tests > p4-lex-tests-final-build.log 2>&1
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_protocol_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_error_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_result_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_sql_test
```

PASS: полная POSIX Debug сборка и все четыре executable. Новый SqlInfo test — **284 проверки**: comments/whitespace, quotes, границы keywords/identifiers, CTE и скобочный SELECT, DML/DDL modifiers, PACKAGE BODY против package с именем BODY, EMPTY и malformed input, отказ controls/unknown wrappers. Отдельная сборка этого чистого C++ теста с `-Wall -Wextra -Werror` также прошла. Первоначальная test link rule ошибочно включала общие LINK_LIBS с decFloat без его search path; ненужная для этого теста зависимость удалена, финальный build прошёл. Production binaries после успешной полной сборки не менялись.

### Повторная сетевая приёмка

```sh
cd /home/aleksey/dev/my_firebird/firebird
python3 -B pg_wire_protocol/tools/pgwire_simple_query_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p4-lex-simple-02 \
  --libpq-include-dir /tmp/firebird-pgwire-p3-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5 \
  --driver-python /tmp/firebird-pgwire-p3-deps/venv/bin/python
python3 -B pg_wire_protocol/tools/pgwire_lifecycle_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p4-lex-lifecycle-01
python3 -B pg_wire_protocol/tools/pgwire_startup_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p4-lex-startup-01
python3 -B pg_wire_protocol/tools/native_baseline.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p4-lex-native-01
```

PASS: **12 Simple Query groups**, **7 lifecycle groups**, **52 startup checks**, **9 native checks**. Для каждого запуска сохранён `summary.json` и sanitized logs в соответствующем work directory. Настоящие C headers/runtime — libpq 14.24; psycopg2 2.9.11 использует libpq 17.6 и explicit autocommit. Все запуски выполнялись на отдельных runtime/security/application databases/locks и свободных TCP ports в `/tmp`, с разрешённым TCP/strace вне sandbox.

| Проверка | Результат |
|---|---|
| SELECT, CRUD, MERGE/upsert, RETURNING, DDL, PSQL и scalar codecs | PASS: прежние значения, tags, row counts, native cross-visibility и implicit commit/rollback сохранены |
| Ограниченная классификация | PASS: mixed case/comments, keywords в quoted names/literals, CTE, скобочный SELECT, GTT/index modifiers; PACKAGE с именем BODY и настоящий PACKAGE BODY различаются |
| Приоритет native prepare | PASS: malformed controls/batches дают 42601, семантически неверный USING сохраняет 42000; SQL не делится и не исполняется частично |
| Отказ до execution | PASS: valid unknown autonomous USING и session controls дают 0A000; sequence guard остаётся 0, ALTER SESSION RESET не сбрасывает USER_SESSION context |
| EMPTY и native parser | PASS: whitespace/comments дают EmptyQueryResponse, `;`, vertical tab, незакрытые quotes/comments и неполный SQL дают 42601; native isql AUTOTERM continuation сохранён |
| Commit/fetch failures, disconnect, shutdown | PASS: настоящий rollback, отсутствие ложного CommandComplete, cleanup transactions/attachments; real commit failure закрывает session через FATAL |
| Драйверы и native protocol | PASS: C PQexec и psycopg2, concurrent native observer, native protocol с отключёнными wire plugins |
| Локальное исполнение | PASS: оба syscall trace содержат 0 backend connect attempts; Simple Query — 49, startup — 51 OS/NSS Unix calls по ранее разрешённым двум путям |

Все server phases завершились exit 0 без forced kill; shutdown в Simple Query/lifecycle/startup — около 0.114–0.164 секунды. Python AST, локальные Markdown links и `git diff --check` прошли.

Первый `simple-01` остановился на неверной fixture `USING DO INSERT ...`: native USING требует parameters, local declarations или autonomous clause и правильно выдал 42000 при prepare. Положительный unsupported case заменён на `USING IN AUTONOMOUS TRANSACTION DO INSERT ...`, исходный оставлен отрицательной проверкой native semantic error. Отдельный SQL-only debug подтвердил исправление; приведённый полный `simple-02` повторил все группы и trace. Production-код для исправления fixture не менялся.

Текущая приёмка относится к Linux/Super loopback и Simple Query. Следующий этап — P5: explicit transactions/session controls; явные COMMIT/ROLLBACK пока получают 0A000, а implicit commit/rollback работают. Extended Query, TLS, Windows/другие server modes и полная CMake сборка не проверены. Close/free fault injection и отдельное истечение engine statement timeout не выполнялись. Рабочий сервер пользователя и его базы не изменялись; для использования текущих исходников в нём нужна пересборка и перезапуск.


## P5: explicit transactions и session controls

12 октября 2026 года, HEAD до изменений `3657d6a5c1`. Production-код изменён только в `src/plugins/wire/pg`; diff по `src/dsql`, `src/jrd`, `src/include` пустой. Сборка и все runtime checks используют отдельные `/tmp` runtime/database/security/locks. Рабочий сервер и employee не менялись.

```sh
cd /tmp/firebird-pgwire-build
make -j2 > p5-build.log 2>&1
make -j2 > p5-final-build.log 2>&1
make -C gen -j2 TARGET=Debug pgwire_tests > p5-tests-build.log 2>&1
```

Оба полных build passes и сборка tests завершились exit 0. Четыре executables `gen/Debug/firebird/tests/pgwire_{protocol,error,result,sql}_test`, запущенные с `LD_LIBRARY_PATH=/tmp/firebird-pgwire-build/gen/Debug/firebird/lib`, прошли; SQL classifier — 487 assertions. Проверяются native control prefixes, quoted identifiers/comments, отсутствие замены SQL, failed-prepare whitelist и приоритет syntax/privilege/cancel/fatal diagnostics над 25P02.

Runtime запускается из корня workspace. Для повторения нужны **новые** work directories:

```sh
python3 -B pg_wire_protocol/tools/pgwire_transaction_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p5-transactions-03 \
  --libpq-include-dir /tmp/firebird-pgwire-p3-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5

python3 -B pg_wire_protocol/tools/pgwire_simple_query_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p5-simple-01 \
  --libpq-include-dir /tmp/firebird-pgwire-p3-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5 \
  --driver-python /tmp/firebird-pgwire-p3-deps/venv/bin/python

python3 -B pg_wire_protocol/tools/pgwire_lifecycle_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p5-lifecycle-01
python3 -B pg_wire_protocol/tools/pgwire_startup_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p5-startup-01
python3 -B pg_wire_protocol/tools/native_baseline.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p5-native-01
```

P5 checks наблюдают фактические wire I/T/E и `PQtransactionStatus`, сравнивают видимость с независимым native TCP attachment и считают application transactions через MON$TRANSACTIONS после каждого запроса. В I их 0, в T/E — 1; обычный Query сохраняет transaction ID, RESET в T меняет underlying ID. Поддержаны full controls в I, native RETAIN, повторный start, commit failed block как rollback, recovery через savepoints с сохранением ранних изменений, EMPTY в I/T/E, READ ONLY/SNAPSHOT, READ COMMITTED visibility, NO AUTO UNDO и disconnect rollback. Failed prepare проверяет missing relation, missing column, syntax и permission отдельно; sequence guard доказывает отсутствие execution в E.

Session checks проверяют реальные ROLE, TIME ZONE, USER_SESSION context, timeouts, SEARCH_PATH и BIND. Длинный native procedure SELECT после `SET STATEMENT TIMEOUT 100 MILLISECOND` прерывается с 57014 как в implicit, так и explicit transaction, откатывает побочные эффекты и сохраняет правильное I/E. Statement timeout выбирается как минимум IOTimeout и ненулевого native attachment timeout; раньше request timeout перекрывал attachment setting.

Реальные database triggers вызывают ошибки explicit COMMIT и ALTER SESSION RESET. Клиент получает FATAL без CommandComplete/ReadyForQuery, затем EOF; native observer подтверждает rollback, следующий login работает. Fault triggers активны только на время этой группы. P4 regression отдельно повторяет реальную ошибку implicit commit. Synthetic failures cursor close/statement free не инъецировались.

### Найденный native AUTO COMMIT defect

Первый запуск `/tmp/firebird-pgwire-p5-transactions-01/summary.json` прошёл первые восемь групп, затем native isql оборвал Debug server с SIGABRT до соответствующего PgWire AUTO COMMIT test. Assertion: `m_transaction->tra_save_point->getNumber() == m_number`, `src/jrd/Savepoint.cpp:833`.

Независимое воспроизведение в `/tmp/firebird-native-autocommit-xolgpdwd` использует только embedded isql, `Providers=Engine14`, отдельную БД, без сервера и PgWire. Явный `SET AUTODDL OFF` исключает автокоммит DDL клиентом:

```sql
SET AUTODDL OFF;
CREATE TABLE T (ID INTEGER);
COMMIT;
SET TRANSACTION READ WRITE READ COMMITTED READ CONSISTENCY AUTO COMMIT;
INSERT INTO T VALUES (1);
```

INSERT вызывает тот же SIGABRT. Backtrace (`gdb_autoddl_off.log`): `JStatement::execute` → `DsqlDmlRequest::execute` → `executeReceiveWithRestarts` → `AutoSavePoint::release`. По исходникам, wrapper read-consistency restart держит AutoSavePoint, тогда как AUTO COMMIT внутри execution завершает retaining и меняет savepoint. Падение воспроизводится без triggers; изначальная гипотеза о commit-trigger interaction не подтвердилась.

Native `SET TRANSACTION ... AUTO COMMIT; ROLLBACK` без DML завершился успешно. `SNAPSHOT AUTO COMMIT` с INSERT, затем ROLLBACK также завершился успешно, после чего COUNT(*)=1 подтверждает сохранённый auto-commit effect. Поэтому итоговый P5 fixture проверяет AUTO COMMIT в SNAPSHOT; сочетание с READ COMMITTED READ CONSISTENCY остаётся известным ограничением текущего native Debug baseline. В плагин не добавлены разбор этих опций, их подмена или обход assertion; engine не менялся. Поведение Release-сборки не проверялось.

Промежуточный `/tmp/firebird-pgwire-p5-transactions-02/summary.json` прошёл 13 групп с явным пропуском AUTO COMMIT DML; это не итоговая проверка AUTO COMMIT. Окончательный результат полного fixture приведён ниже.


### Итог P5

| Проверка | Результат / свидетельство |
|---|---|
| Полная Debug сборка и 4 unit executables | PASS; `p5-final-build.log`, `p5-tests-build.log`; 487 SqlInfo assertions |
| P5 native transactions/session controls | PASS, 14 групп, без пропусков; `/tmp/firebird-pgwire-p5-transactions-03/summary.json` |
| Simple Query regression | PASS, 12 групп; `/tmp/firebird-pgwire-p5-simple-01/summary.json` |
| Lifecycle regression | PASS, 7 групп; `/tmp/firebird-pgwire-p5-lifecycle-01/summary.json` |
| Startup/authentication regression | PASS, 52 checks; `/tmp/firebird-pgwire-p5-startup-01/summary.json` |
| Native baseline | PASS, 9 checks; `/tmp/firebird-pgwire-p5-native-01/summary.json` |

P5 C libpq использует headers/runtime 14.24, реально наблюдает IDLE/INTRANS/INERROR и savepoint recovery. Simple Query regression дополнительно проверяет psycopg2 2.9.11 с libpq 17.6 в autocommit mode. Финальный AUTO COMMIT case — native/PgWire SNAPSHOT с проверкой MON$AUTO_COMMIT, MON$ISOLATION_MODE, durable visibility и невозможности отменить предыдущий commit поздним rollback.

Отдельный P5 trace phase (startup/authentication/контрольный SELECT/shutdown) показал 0 backend connect attempts; 49 Unix OS/NSS attempts учтены отдельно. Это не трасса каждого transactional statement. Simple Query и startup trace также показали 0 backend connects. В итоговых runtime groups все servers завершились exit 0 без forced kill; SIGTERM shutdown занял 0.114–0.166 s.

Область результата — P5 на Linux/Super, IPv4 loopback, native compatible SQL. READ COMMITTED READ CONSISTENCY AUTO COMMIT остаётся известным native Debug defect. Остальные transaction options не обещают семантику PostgreSQL. Полная M1 приёмка P6, SuperClassic/Windows/TLS, Extended Query, PostgreSQL BEGIN/$n, close/free failure injection и отдельные live effects DECFLOAT/OPTIMIZE/TRUSTED ROLE не объявляются выполненными. Native timers ограничивают DML execution/fetch, без hard deadline для prepare, DDL или transaction/session controls. Планы и ручной двухклиентский сценарий обновлены.

## Удаление дополнительных wire-таймаутов — 13 октября 2026

База правки — `5f873c9eba`. Удалены `IOTimeout`, сетевые/frame/startup/auth/output deadlines, истечение очереди по времени и задаваемые плагином statement/lock timeout. Сохраняются штатные настройки Firebird. `IWireConnection::read/write` больше не принимают timeout; C++/Pascal bindings пересозданы. Сервер и protocol plugins необходимо пересобрать вместе и перезапустить.

| Проверка | Результат / свидетельство |
|---|---|
| Полная Debug сборка, `make -j2` | PASS; `/tmp/firebird-pgwire-build/remove-wire-timeouts-build.log` |
| `make -C gen -j2 TARGET=Debug wire_tests pgwire_tests` | PASS; `/tmp/firebird-pgwire-build/remove-wire-timeouts-tests-build.log` |
| Четыре PgWire unit executables: protocol, error, result, sql | PASS; включая 487 SqlInfo checks |
| `wire_socket_test`: partial I/O, EOF, ownership, shutdown, backpressure | PASS; запуск с разрешёнными локальными sockets |
| Существующий wire host smoke | PASS, 9 checks; `/tmp/firebird-wire-no-timeouts-host-01/summary.json` |
| Существующий Simple Query smoke | PASS, 12 групп; `/tmp/firebird-pgwire-no-timeouts-simple-01/summary.json` |
| Python syntax, Markdown links, `git diff --check` | PASS |

Сборка и тестовые runtime находятся в `/tmp`; запущенный сервер и базы пользователя не изменялись. Wire host проверяет совместную работу native listener и двух plugin endpoints, ограничение размера очереди и остановку blocked I/O. Simple Query проверяет DML/DDL/RETURNING и видимость через native connection с C libpq 14.24 и psycopg2 2.9.11/libpq 17.6. Все четыре тестовых server phases завершились с exit 0 без forced kill; trace показал 0 backend network connects.

Новых тестов с паузами или срабатываниями таймаутов не добавлено; прежние проверки удалённых таймаутов убраны. Полный P5 transaction/session, startup и native baseline suites в этой правке повторно не запускались; их результаты выше относятся к предыдущей ревизии.

## P6: серверная приёмка — 13 октября 2026

База — `228966d5c6`, Linux x86_64, Debug, Engine14, Super, IPv4 loopback. Изменены только wire shutdown и проверочные материалы: terminal stop использует `fb_cancel_abort`, потому что однократный `fb_cancel_raise` может попасть между engine calls; PgWire повторно проверяет остановку после чтения сообщения. Parser, DSQL, native transaction semantics и ABI в этой правке не менялись. Все runtime и БД изолированы в `/tmp`; рабочий сервер пользователя не изменялся.

**P6 завершён; целевой M1 для Linux/Super принят с известными native ограничениями.** По согласованному объёму исправление падения при shutdown во время ON CONNECT исключено из этого этапа. Сценарий остаётся FAIL и воспроизводится также при полностью отключённом PgWire; его результат не входит в PASS. Ранее выявленный READ CONSISTENCY + AUTO COMMIT также остаётся вне подтверждённой совместимости. Следующий этап — P7, Extended Query.

| Проверка | Результат / свидетельство |
|---|---|
| Полная сборка `make -j2` и `make -C gen -j2 TARGET=Debug wire_tests pgwire_tests` | PASS; `/tmp/firebird-pgwire-build/p6-build.log`, `p6-tests-build.log` |
| PgWire protocol/error/result/sql unit executables | PASS, включая 487 SqlInfo checks |
| P6 server harness | PASS, 7 групп; `/tmp/firebird-pgwire-p6-server-03/summary.json` |
| Мигрированный psycopg2 pytest | PASS, 58 cases без skips; `/tmp/firebird-pgwire-p6-server-03/pytest.log` |
| P5 transactions/session regression | PASS, 14 групп; `/tmp/firebird-pgwire-p6-transactions-01/summary.json` |
| Simple Query regression | PASS, 12 групп; `/tmp/firebird-pgwire-p6-simple-01/summary.json` |
| Lifecycle regression | PASS, 7 групп; `/tmp/firebird-pgwire-p6-lifecycle-01/summary.json` |
| Startup/authentication regression | PASS, 49 checks; `/tmp/firebird-pgwire-p6-startup-01/summary.json` |
| Native baseline | PASS, 9 checks; `/tmp/firebird-pgwire-p6-native-01/summary.json` |
| Wire host и plugin lifecycle | PASS, 9 host checks; `/tmp/firebird-wire-p6-host-01/summary.json`; `wire_plugin_smoke.py` подтвердил aliases/stop/cleanup/unload через реальный PluginManager |
| Shutdown во время ON CONNECT: PgWire | FAIL, SIGABRT; `/tmp/firebird-pgwire-p6-attach-shutdown-01/summary.json` и `backtrace.log` |
| Тот же ON CONNECT: только native TCP, wire endpoints выключены | FAIL, тот же SIGABRT; `/tmp/firebird-pgwire-p6-native-attach-shutdown-01/summary.json` и `backtrace.log` |

Новый server harness выполняет 58 driver cases, затем 16 warmup и 24 измеряемых connect/query/error/explicit transaction/disconnect цикла. Attachments/transactions/statements освобождаются; FD baseline — 15, все три измеренных batch — 15. Потоков было 11 во всех финальных snapshots, но это диагностическое число: native server сохраняет workers в пуле, поэтому его точное совпадение не является gate. Возврат RSS к исходному значению не проверяется.

DELETE MON$STATEMENTS действительно отменяет работу с 57014 и правильным I/E/recovery. DELETE MON$ATTACHMENTS и `gfix -shut full -force 0` дают FATAL 57P01 на следующем Query, с rollback и последующим успешным подключением после online. Для gfix используется случайный пароль SYSDBA только клонированной security DB. Остановка при record lock wait синхронизируется с MON$RECORD_WAITS > 0; одновременно существуют idle transaction, password wait и неполный Startup. Оба server phases завершаются exit 0 без SIGKILL. SuperClassic проверен только на явный отказ дополнительного endpoint при работоспособном native listener.

Реальные драйверы: C libpq headers/runtime 14.24 и psycopg2 2.9.11/libpq 17.6, pytest 8.4.2. Traced startup/Simple Query/transactions показали 0 backend network connects. Новых проверок пауз или срабатывания timeout нет; существующие P5 session tests повторены как regression.

Основная команда из корня workspace (нужен новый work directory):

```sh
python3 -B pg_wire_protocol/tools/pgwire_server_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p6-server-03 \
  --driver-python /tmp/firebird-pgwire-p3-deps/venv/bin/python
```

Промежуточные `server-01` и `server-02` не входят в PASS: первый выявил некорректное требование точного native thread count, второй — отсутствовавшее право DELETE на MON$STATEMENTS у тестового observer. В debug fixture также заменён role-based gfix на SYSDBA клонированной security DB, чтобы вернуть полностью остановленную БД online. Эти исправления относятся к harness, production binaries не менялись после `p6-build.log`.

### Известное исключение приёмки: ON CONNECT и shutdown

Trigger для WIRE_PG_OTHER вставляет marker, увеличивает sequence и выполняет длительный COUNT. Native observer подтверждает вход по sequence, после чего harness посылает SIGTERM. Оба варианта падают на `Assertion (!child)`, `src/common/classes/alloc.cpp:2075`, server exit -6 без принудительного kill. Проверки EOF/rollback после штатной остановки не достигнуты.

Стек: `JProvider::internalAttach` → `unwindAttach` → `JRD_shutdown_database` → `Database::~Database` → `MemPool::~MemPool`. В native-only варианте нижняя ветка — `DatabaseAuth::accept`, список wire endpoints пуст. Это отделяет дефект native attach cleanup от lifecycle PgWire/plugin unload. Полные stacks, fixture SQL и логи сохранены в двух каталогах, указанных в таблице. Ошибка не маскируется успешным skip/xfail: скрипт возвращает ненулевой код, а падение явно исключено из объёма текущей приёмки M1.

В PgWire core оставшийся child pool принадлежит Request для TRIGGER_CONNECT, созданному в Statement::getRequest. Request уже не используется и отвязан от attachment/transaction/caller; statement и его request generation остаются живы. Причина пропуска освобождения ещё не установлена. Принудительное удаление child pools или обход в плагине не добавлены; исправление native cleanup остаётся отдельной задачей.

Сохранённый в репозитории reproducer повторил оба FAIL с `trigger_entered=1`, server exit -6 и тем же assertion. Его summary находятся в `/tmp/firebird-pgwire-p6-attach-repro-02/` и `/tmp/firebird-native-p6-attach-repro-02/`. Команды (work directories должны быть новыми):

```sh
python3 -B pg_wire_protocol/tools/pgwire_attach_shutdown_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p6-attach-repro-02
python3 -B pg_wire_protocol/tools/pgwire_attach_shutdown_smoke.py --native-only \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-native-p6-attach-repro-02
```

## P7: Extended Query — 13 октября 2026

База — `c94f8e84ff`, Linux x86_64, Debug, Engine14, Super, IPv4 loopback. Реализованы Parse/Bind/Describe/Execute/Close/Flush/Sync, prepared statements/portals, native input metadata и text/binary basic codecs. Все SQL bytes передаются движку без изменения. Parser, публичные statement metadata и CLOOP не расширялись. Отдельно исправлено освобождение нескольких native requests общего Statement при shutdown attachment; причина и native regression описаны ниже. Рабочий сервер пользователя и его базы не изменялись.

**P7 прошёл целевую приёмку.** Следующий этап — P8, расширенные codecs; P9 должен завершить M2. Параметры проверены через native `?`, PostgreSQL `$1` и BEGIN по-прежнему дают ошибку парсера. Фактический Psycopg 3 profile в P7 не проверен. Binary NUMERIC, floating point/temporal/INT128 codecs, TLS и CancelRequest остаются последующими этапами.

| Проверка | Результат / свидетельство |
|---|---|
| Полная сборка `make -j2` | PASS; `/tmp/firebird-pgwire-build/p7-build.log` |
| Сборка opt-in `wire_tests pgwire_tests`, пять PgWire unit executables | PASS; `/tmp/firebird-pgwire-build/p7-tests-build.log`; InputCodec 149 checks, SqlInfo 487 checks; Protocol/ErrorStatus/ResultCodec PASS |
| Extended Query | PASS, 16 групп; `/tmp/firebird-pgwire-p7-extended-04/summary.json` |
| Simple Query regression | PASS, 12 групп; `/tmp/firebird-pgwire-p7-simple-01/summary.json` |
| Transaction/session regression | PASS, 14 групп; `/tmp/firebird-pgwire-p7-transactions-01/summary.json` |
| Startup/authentication regression | PASS, 49 checks; `/tmp/firebird-pgwire-p7-startup-01/summary.json` |
| Server regression после engine cleanup fix | PASS, 7 групп и 58 pytest cases (23.85 s); `/tmp/firebird-pgwire-p7-server-02/summary.json` |
| Native TCP baseline после fix | PASS, 9 checks; `/tmp/firebird-pgwire-p7-native-01/summary.json` |
| Native two-prepared-handle shutdown | PASS, 3 checks, обе остановки exit 0; `/tmp/firebird-pgwire-p7-native-prepared-shutdown-02/summary.json` |

Прямой C libpq 14.24 (headers/runtime) проверил PQexecParams, PQprepare/PQexecPrepared, PQsendQueryParams/PQsendQueryPrepared/PQgetResult в nonblocking режиме, ноль параметров, native `?`, NULL, text/binary и настоящий parser failure `$1`. Raw TCP проверил порядок сообщений, Describe/OIDs/formats, named/unnamed replacement/Close и сохранение bound portals после закрытия source statement. Несколько portals одного SQL имеют независимые cursors и значения; maxRows/Suspended/resume сохраняют строки и итоговый command count.

Native observer подтвердил отсутствие commit до Sync, rollback всего implicit cycle при ошибке, сохранение explicit T/E через Sync, восстановление savepoints и prepared reuse после commit. COMMIT/ROLLBACK/RETAIN и переход COMMIT из E в ROLLBACK проверены непосредственно через Extended Execute. Ошибки Bind отклоняют неподдержанные значения/форматы до необратимого GEN_ID effect. Recovery игнорирует тела полных командных frames до Sync; повреждённая внешняя framing остаётся fatal. RESET из I/T очищает objects без дополнительных prepare-transactions, даже при замене underlying transaction за тем же интерфейсом.

Проверены 128 statements/128 portals и суммарный byte limit: 51 SQL по 163889 bytes помещается, следующий получает 54000 до достижения count limit. Это лимит учитываемых SQL/metadata/buffers, а не измеренный RSS cap. EOF/Terminate и shutdown освобождают suspended cursors и откатывают незавершённые изменения. Traced startup/Simple Query повторно показал 0 backend network connect attempts; scope trace указан в summary, он не выдаётся за отдельную трассу всех Extended сообщений.

Команда основной приёмки (work directory должен быть новым):

```sh
python3 -B pg_wire_protocol/tools/pgwire_extended_query_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p7-extended-04 \
  --libpq-include-dir /tmp/firebird-pgwire-p3-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5
```

### Native cleanup нескольких prepared requests

P7 выявил assertion `Statement.cpp:781`, `!((*instance)->isUsed())`, при остановке с двумя native prepares одного SQL. Независимый native TCP запуск с пустым списком wire endpoints воспроизвёл SIGABRT: `/tmp/firebird-pgwire-p7-native-cursor-01/summary.json`. Это отдельный дефект; ранее отложенный ON CONNECT assertion `alloc.cpp:2075 (!child)` не исправлялся и не проверялся повторно в P7.

Стек нового падения: `Statement::release → CMP_release → release_attachment → purge_attachment → shutdownAttachments`. `release_attachment` снимал used только с очередного request, но CMP_release освобождал весь Statement вместе с другими занятыми clones. Исправление в `src/jrd/jrd.cpp` сначала снимает used у всех requests уничтожаемого attachment, затем освобождает statements прежним циклом. Коллекция меняется только во втором проходе; parser, execution semantics и новые metadata/API этим не затрагиваются.

Сохранён воспроизводимый regression с родным OO API и TCP, отключённым PgWire, двумя prepared handles и отдельным сценарием cursor/rollback:

```sh
python3 -B pg_wire_protocol/tools/pgwire_native_prepared_shutdown_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p7-native-prepared-shutdown-02
```

Промежуточные Extended `-01/-02/-03` не входят в PASS. Они выявили требование native inference (`ID + CAST(? AS INTEGER)`; исходный `CAST(ID + ? AS INTEGER)` даёт HY004 также в native isql SET PLANONLY), неверную fatal обработку отрицательного Execute maxRows и описанный cleanup assertion. Эти причины исправлены и покрыты финальным `-04`. Native inference evidence сохранён в `extended-01/native-parameter-inference.json`; SQL rewriting ради прохождения fixture не добавлялся. Первый запуск native regression остановился до старта сервера из-за отсутствовавшего linker search path для libtommath; harness теперь задаёт runtime library path через `-rpath-link`.


## P8: расширенные codecs — 14 октября 2026

База — `598d487095`, Linux x86_64, Debug, Engine14, Super, IPv4 loopback. Production changes находятся в PgWire: общие FloatCodec/NumericCodec/TemporalCodec подключены к InputCodec и ResultCodec; startup добавляет `integer_datetimes=on`. Ядро, parser, public statement metadata, host ABI и таймауты не менялись. Рабочий сервер пользователя и его базы не изменялись.

**P8 прошёл целевую приёмку.** Добавлены text/binary FLOAT/DOUBLE, конечные DATE/TIME/TIMESTAMP без TZ, INT128 и точные NUMERIC/DECIMAL до precision 38, включая binary NUMERIC. Pure INT128 сохраняет полный signed диапазон. Temporal input должен точно представляться native 100 µs ticks, поддерживаются годы 0001..9999; меньшая точность не округляется. Профиль и SQLSTATE описаны в COMPATIBILITY. Следующий этап — P9/M2; `$n`, PostgreSQL BEGIN и `::date` по-прежнему не входят в grammar.

| Проверка | Результат / свидетельство |
|---|---|
| Полная сборка `make -j2` | PASS; `/tmp/firebird-pgwire-build/p8-build.log` |
| Сборка `wire_tests pgwire_tests` | PASS; `/tmp/firebird-pgwire-build/p8-tests-build.log` |
| Восемь PgWire unit executables | PASS: Float 112, Numeric 502, Temporal 313, Input 154, SqlInfo 487 checks; Result/Protocol/ErrorStatus PASS |
| Numeric standalone UBSan | PASS, 502 checks, без diagnostics |
| Новые codecs через TCP/libpq | PASS, 8 групп; `/tmp/firebird-pgwire-p8-codecs-02/summary.json` |
| Extended Query regression | PASS, 16 групп; `/tmp/firebird-pgwire-p8-extended-01/summary.json` |
| Simple Query regression | PASS, 12 групп; `/tmp/firebird-pgwire-p8-simple-01/summary.json` |
| Transaction/session regression | PASS, 14 групп; `/tmp/firebird-pgwire-p8-transactions-01/summary.json` |
| Startup/authentication regression | PASS, 49 checks; `/tmp/firebird-pgwire-p8-startup-01/summary.json` |
| Server regression и live psycopg2 | PASS, 7 групп и 58 pytest cases (23.98 s); `/tmp/firebird-pgwire-p8-server-01/summary.json` |
| Python AST и `git diff --check` | PASS |

Реальные клиенты: C libpq headers/runtime 14.24 и psycopg2 2.9.11/libpq 17.6. Новый C client использует PQexecParams, PQprepare, PQdescribePrepared и PQexecPrepared с native `?`, восемью раздельными параметрами, text/binary input/output и NULL. Эталонные wire bytes заданы независимо от серверных codecs. Проверены числовые OID и ParameterDescription, typmod=-1, повторное исполнение после ошибки.

Новый raw-wire harness проверяет IEEE signed zero, subnormal/min/max, NaN/±Infinity; full INT128 min/max, 38 fractional digits, exact exponent и base-10000 numeric; epochs 1858/2000, отрицательный timestamp, даты 0001/9999, temporal100 µs. Unit fixtures дополнительно проверяют leap years, unaligned native buffers и отсутствие частичной записи при ошибке. Simple SELECT и Extended INSERT/Simple UPDATE RETURNING сохраняют значения; отдельный native TCP observer подтверждает обе стороны cross-visibility. В 26 negative Bind cases ошибки формата/диапазона/точности возникают до необратимого GEN_ID effect.

Старые negative FLOAT/DOUBLE/DATE/TIME/TIMESTAMP/INT128 tests заменены положительными; guards неподдержанного output используют DECFLOAT/BLOB. SET BIND regression теперь проверяет исходный DOUBLE, coercion к VARCHAR и восстановление DOUBLE после RESET. Existing server resource checks: после 16 warmup и 24 измеряемых sessions FD baseline 15, все три batch 15; native monitoring не оставляет attachments/transactions/statements. Существующие P5 timeout cases повторены без добавления новых pause/timeout scenarios.

Trace harness зафиксировал 0 backend network connects в своём startup/Simple Query scope; полный Extended codec обмен этим syscall trace не заявляется покрытым. Неисправленный native ON CONNECT shutdown defect и READ CONSISTENCY + AUTO COMMIT остаются ранее записанными исключениями и в P8 не исправлялись. Windows, другие server modes, TLS, CancelRequest и обычная параметризация Psycopg 3 требуют последующих этапов.

В промежуточном `/tmp/firebird-pgwire-p8-codecs-01/` прямой C libpq и float checks прошли, затем сам Python runner получил ValueError: Python 3.10 `fromisoformat` не принимает четыре fractional digits. В test-only helper добавлено дополнение дроби нулями до шести цифр; production codecs не менялись. Полный повтор `codecs-02` прошёл.

Воспроизводимый запуск (из корня workspace, work directory должен быть новым):

```sh
python3 -B pg_wire_protocol/tools/pgwire_codec_smoke.py \
  --firebird-dir /tmp/firebird-pgwire-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-p8-codecs-new \
  --libpq-include-dir /tmp/firebird-pgwire-p3-deps/libpq-dev/usr/include/postgresql \
  --libpq-library /lib/x86_64-linux-gnu/libpq.so.5
```

Unit targets собираются через `make -C gen -j2 TARGET=Debug wire_tests pgwire_tests` в build directory. Выполняемые файлы — `gen/Debug/firebird/tests/pgwire_*_test`, с `LD_LIBRARY_PATH` на `gen/Debug/firebird/lib` этой же сборки. Для обновления make templates при существующем configure: `MAKE_SRC_DIR=builds/posix ./config.status gen/Makefile gen/make.shared.variables`.
