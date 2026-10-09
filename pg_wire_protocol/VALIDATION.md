# Проверки реализации

Дата: 9 октября 2026 года. HEAD до изменений: `d533d51ad2`, baseline master Firebird 6 `bf94598f8deefeb7cc4a8217ee43b3040d5d6650`.

Текущий этап: P0 и два среза P1 — конфигурация, затем ABI/loader/session ownership. PgWire endpoint пока отсутствует; M0–M3 не приняты. Исходные Python acceptance cases не запускались против Firebird и требуют миграции по TEST_PLAN.md. Первый срез ниже относится к HEAD `d533d51ad2`, второй — к `e912b12560` до изменений.

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
