# База реализации Firebird 6

Исходная база зафиксирована 8 октября 2026 года. Это сведения об исходниках, а не отчёт об успешной сборке или работающей конфигурации.

| Параметр | Значение |
|---|---|
| Рабочая ветка | `pg_wire_protocol` |
| Базовая ветка | `master` Firebird 6 |
| Исходный SHA | `bf94598f8deefeb7cc4a8217ee43b3040d5d6650` |
| Версия исходников | `6.0.0.2199 Initial`, `BuildType=T` |
| Локальный provider | `CURRENT_ENGINE="Engine14"` |
| Engine-only настройка | `EMBEDDED_PROVIDERS="Providers=" CURRENT_ENGINE` |
| Стандарт C++ | C++20 в `configure.ac` |
| Первая платформа проверки | Linux, `ServerMode=Super`; SuperClassic проверяется отдельно |

## Проверенные точки интеграции

- `src/include/firebird/FirebirdInterface.idl`, `src/yvalve/PluginManager.cpp`, `src/common/classes/GetPlugins.h`: plugin types, CLOOP, factory/module ownership. Новый тип wire plugin и интерфейсы пока отсутствуют; их проект описан в [ARCHITECTURE.md](ARCHITECTURE.md).
- `src/common/config/config.h`, `src/common/config/config.cpp`, `builds/install/misc/firebird.conf`: регистрация и документирование двух новых global configuration lists.
- `src/remote/server/os/posix/inet_server.cpp`: auth registration, initialization и native loop. Дополнительный host подключается к server lifecycle; родной frontend остаётся на существующем пути. SuperClassic использует общую многоклиентскую ветвь, но engine/cache lifecycle отличается от Super.
- `src/remote/server/os/win32/srvr_w32.cpp`: дальнейшее Windows lifecycle wiring.
- `src/remote/server/server.cpp`: native authentication, DPB sanitization, attachment и shutdown; DispatcherPtr native frontend сам по себе не означает engine-only route.
- `src/jrd/extds/ValidatePassword.cpp`: in-memory client/server authentication; входит в Engine14, EDS guards требуют существующий attachment. Нельзя прямо вызывать этот helper из нового listener до attach.
- `src/jrd/constants.h`, `src/yvalve/why.cpp`: локальный provider, config merge и shutdown phases. Дополнительные wire plugins принудительно выбирают local Providers после sanitization, сохраняя остальные database policies.
- `src/dsql/parse.y`, `src/dsql/dsql.cpp`, `src/dsql/StmtNodes.cpp`, `src/jrd/jrd.cpp`: native grammar, metadata, execution и возвращаемый transaction interface. `BEGIN`/`$n` не поддержаны, public getType теряет подробности исходных operations/controls.
- `builds/posix/make.shared.variables`: `dirObjects` не рекурсивен. Новый host subdirectory явно добавляется в server objects, PG subdirectory — в отдельную plugin object group.
- `builds/posix/Makefile.in`: daemon target, shared plugin targets и `updateCloopInterfaces`. Нужны отдельные build/install rules для PgWire.
- `configure.ac`: `TLS_OPTIONS` — thread-local storage; сетевую TLS-зависимость для PgWire ещё предстоит выбрать и подключить.

Новые исходники планируются в `src/remote/server/wire` (общий host) и `src/plugins/wire/pg` (PG shared library). Привязанные к SHA ссылки и подробности — [SOURCES.md](SOURCES.md).

## Что установить в P0

Проверить фактический HEAD и его происхождение от выбранной базы, build dependencies, штатную сборку и native baseline. Зафиксировать реальные AuthServer/AuthClient, security database и конфигурацию тестового экземпляра. Defaults исходников не доказывают действующую конфигурацию.

Выбрать TLS dependency/feature flag/packaging, определить ABI/lifecycle и способ публикации parser-derived metadata без нового SQL-синтаксиса. Записать решения в IMPLEMENTATION_NOTES.md, фактические проверки — в VALIDATION.md после их выполнения.
