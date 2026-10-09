# Проверки реализации

Дата: 9 октября 2026 года. HEAD до изменений: `d533d51ad2`, baseline master Firebird 6 `bf94598f8deefeb7cc4a8217ee43b3040d5d6650`.

Текущий этап: P0 и первый конфигурационный срез P1. PgWire endpoint пока отсутствует; M0–M3 не приняты. Исходные Python acceptance cases не запускались против Firebird и требуют миграции по TEST_PLAN.md.

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

Первая конфигурационная часть P1 проверена. P0/P1 целиком ещё не закрыты: окончательные CLOOP signatures/ownership, plugin loading, workers/listeners и lifecycle требуют следующих изменений. Source changes не затрагивают SQL parser и родной wire dispatch.

## Границы первого среза

Проверяются регистрация двух global settings и чистый parser позиционных списков. Проверки загрузки библиотек, listeners, native-port conflicts, TLS, SQL metadata и PgWire clients относятся к последующим частям P1–P3 и пока не выполнены.

Для будущего прямого C libpq harness отсутствуют development headers/linker symlink в стандартных путях этой машины; runtime libpq сам по себе их не заменяет. Это не препятствует текущей сборке Firebird и native smoke, но должно быть закрыто до клиентской приёмки M0.
