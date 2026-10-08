# База реализации Firebird 6

Состояние исходников зафиксировано 8 октября 2026 года.

| Параметр | Значение |
|---|---|
| Рабочая ветка | `pg_wire_protocol` |
| Базовая ветка | `master` Firebird 6 |
| Исходный SHA | `bf94598f8deefeb7cc4a8217ee43b3040d5d6650` |
| Версия исходников | `6.0.0.2199 Initial`, `BuildType=T` |
| Локальный provider | `CURRENT_ENGINE="Engine14"` |
| Engine-only настройка | `EMBEDDED_PROVIDERS="Providers=" CURRENT_ENGINE` |
| Стандарт C++ | C++20 в `configure.ac` |
| Начальная платформа | Linux, `ServerMode=Super` |

## Проверенные точки интеграции

- `src/remote/server/os/posix/inet_server.cpp`: `registerAuthPlugins()`, SuperServer initialization и основной вызов `SRVR_multi_thread()`; запуск PG service должен происходить в соответствующей ветви lifecycle. Наличие дополнительных путей запуска, включая Unix socket/Classic, требует явной проверки режима.
- `src/remote/server/server.cpp`: `DatabaseAuth::accept`, remote metadata, внутренние DSQL calls, native cleanup/shutdown.
- `src/jrd/extds/ValidatePassword.cpp`: обмен client/server authentication plugins в памяти; EDS guards требуют существующий attachment.
- `src/jrd/constants.h`, `src/common/classes/ParsedList.cpp`: engine-only route; удаление только `Loopback` оставляет `Remote`.
- `src/yvalve/why.cpp`: `preproviders`, остановка providers/interfaces, `postproviders`.
- `builds/posix/make.shared.variables`: раздельные `Remote_Common` и `Remote_Server`; новые server sources должны входить в последний.
- `builds/posix/Makefile.in`: `FB_DAEMON` собирается из server objects и существующих runtime libraries.

## Что установить в P0

Сборку, доступность зависимостей, конфигурацию работающего сервера, AuthServer/AuthClient и security database ещё предстоит проверить. Наличие defaults в исходниках не доказывает действующую конфигурацию установленного экземпляра.

Перед реализацией записать фактический HEAD рабочей ветки и проверить его соответствие выбранной базе `master` Firebird 6.
