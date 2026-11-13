# Открытие настоящей базы 1С через Firebird

13 ноября 2026 года этап 4 выполнен: настоящий тонкий клиент **1С 8.5.4.1306**
открыл подготовленную информационную базу, прочитал конфигурацию и отрисовал
начальную страницу **«Тест Гилёва : 2.1.1.48»**. SQL выполнялся Firebird 6.0.0
через PgWire. Сервер `ibsrv` и клиент остались работающими; одновременное native
подключение прочитало ту же `.fdb`.

Это приёмка открытия подготовленной базы. Сам тест Гилёва, его результат,
производительность, создание базы средствами 1С и загрузка `.dt` не проверены.

## Проверенный стенд

| Компонент | Значение |
|---|---|
| Runtime | `/tmp/pgwire-1c-launch/runtime`, Debug Linux/Super |
| База | `/tmp/pgwire-1c-launch/data/gilev_32k.fdb`, UTF8, ODS 14.1, page size 32768 |
| Alias / пользователь БД | `gilev_firebird` / SYSDBA |
| Родной endpoint | `127.0.0.1:23050` |
| PgWire endpoint | `127.0.0.1:25432` |
| Сервер 1С | `/opt/1cv8/x86_64/8.5.4.1306/ibsrv` |
| Клиент | `/opt/1cv8/x86_64/8.5.4.1306/1cv8c` |
| Публикация | `http://127.0.0.1:18314/gilev` |
| Загруженная сервером 1С libpq | `/opt/1cv8/x86_64/8.5.4.1306/libpq.so.5.14`, `PQlibVersion=140002` |

Стенд отдельный: исходный `gilev_demo_ru` в PostgreSQL использовался только для
чтения, рабочие службы пользователя не перенастраивались. Пароль тестового
Firebird хранится вне репозитория в `/tmp/pgwire-1c-launch/connection.json`
с правами 0600. `/tmp` — временное место; это не установка для эксплуатации.

Перенесены **194 таблицы / 135146 строк** с проверкой binary fingerprints до
COMMIT. Созданы 337 поддержанных исходных индексов и 14 вспомогательных,
сохранены все 46 PRIMARY KEY и NULL-семантика UNIQUE. Подготовка, требования к
размеру страницы и явное разрешение неполного schema profile описаны в
[PREPARE_INFOBASE.md](PREPARE_INFOBASE.md). Накатаны все 18 SQL-скриптов
[общего установщика](install_compat.py).

## Запуск

Оставленный работающий стенд можно открыть командой:

```sh
/opt/1cv8/x86_64/8.5.4.1306/1cv8c ENTERPRISE \
  /WS http://127.0.0.1:18314/gilev /L ru /AppAutoCheckVersion-
```

Для повторения на другом отдельном стенде сначала подготовить базу и конфиги
PgWire по [quickstart](../quickstart.md), установить SQL-профиль и выполнить
[перенос](PREPARE_INFOBASE.md). В проверенном plugin config использованы
`DatabaseAliases = gilev_firebird`, `Workers = 16`, `QueueSize = 32`,
`BindAddress = 127.0.0.1`; в firebird.conf — native порт 23050,
`WireProtocolPlugins = PgWire`, `WireProtocolPorts = 25432`.

Сервер 1С запускался так (пароль передать своим способом в `PGWIRE_DB_PASSWORD`):

```sh
/opt/1cv8/x86_64/8.5.4.1306/ibsrv \
  --data=/tmp/pgwire-1c-launch/ibsrv \
  --http-address=127.0.0.1 --http-port=18314 --http-base=/gilev \
  --direct-regport=11541 --direct-range=11560:11569 \
  --disable-ssh-gate --name=GilevFirebird --dbms=PostgreSQL \
  --db-server='127.0.0.1 port=25432' \
  --db-name=gilev_firebird --db-user=SYSDBA \
  --db-pwd="$PGWIRE_DB_PASSWORD" --schedule-jobs=deny
```

В `--db-server` нет завершающей точки с запятой. Для текущего стенда сохранены
локальные `start_firebird.py`, `start_1c.py`, `start_client.py` и PID-файлы в
`/tmp/pgwire-1c-launch`; второй экземпляр сервера на этих портах запускать не нужно.

## Что подтверждено

- В `/proc` процесса `ibsrv` подтверждены настоящая libpq и TCP-соединения с
  PgWire 25432; клиент соединён с HTTP-публикацией этого `ibsrv`.
- Наблюдались Startup protocol 3 / UTF8 и настоящий Extended Query обмен 1С:
  inferred parameter types, text inputs, binary bytea input и binary results.
- Реальные запросы прочитали Config/version, конфигурацию, v8users и каталоги
  индексов, выполнили служебные UPDATE params/files. Последний блокер устранён
  в `db0ffc4dd3`: timestamp Bind `20261113 120430` обрабатывается в PgWire
  без нового кода в движке и без потери native точности 100 µs.
- Получен снимок именно окна процесса `1cv8c`, без захвата чужих окон.
  Native isql одновременно подтвердил Firebird 6.0.0, путь этой базы,
  ODS 14.1 / 32768 и 230 строк таблицы config.

[Сводка и hashes](../evidence/1c-opening-20261113.json) сохранены в репозитории.
Полные локальные артефакты: `/tmp/pgwire-1c-launch/opening-summary.json`,
`actual-libpq.json`, `actual-wire.jsonl`, `firebird-trace.log`,
`native-after-client.log`, `client-ui-confirmed-open/report.json` и
`client-ui-confirmed-open/0x160015a.png`. Пароли и значения BLOB в wire evidence
не записывались. SQL/runtime проверки перечислены в [VALIDATION](../VALIDATION.md).

## Следующий этап и ограничения

Следующий результат — реальный прогон Гилёва с проверкой данных и устойчивости.
Начальная страница конфигурации требует толстый клиент в **обычном режиме**.
Текущий `ibsrv` обслуживает управляемый режим; обычный режим исключён из его
возможностей в [руководстве 1С, §7.2](https://1c-dn.com/library/tutorials/1c_enterprise_administrator_guide_file_mode_8_3_27/).
Для нагрузки нужен отдельный кластер 1С с `ragent` и толстым клиентом.
При переходе завершить клиент и `ibsrv`; одновременно обслуживать эту же базу
standalone-сервером и кластером нельзя. Текущая приёмка не подменяет этот прогон.

Остаются 16 expression indexes и три циклических sequence defaults, различия
ICU/libc collation, ограниченные PG catalogs/types/functions и поддержанный
профиль транзакций. Перед измерением проверить влияние повторного native
prepare при Bind. Фиксированные no-op SET остаются no-op по решению пользователя.
Полный список, включая native дефекты и точки будущей изоляции, ведётся в
[BACKLOG](../BACKLOG.md) и [EXTENSION_POINTS](../EXTENSION_POINTS.md).
