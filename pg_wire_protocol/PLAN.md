# План реализации PostgreSQL frontend в Firebird 6

Рабочая ветка — `pg_wire_protocol`, база — `master` Firebird 6 по [BASELINE.md](BASELINE.md). Архитектура и контракты — [CODEX_CONTEXT.md](CODEX_CONTEXT.md), границы поддержки — [COMPATIBILITY.md](COMPATIBILITY.md), проверки — [TEST_PLAN.md](TEST_PLAN.md).

Все этапы относятся к frontend внутри штатного процесса `firebird`. Запросы выполняются его локальным движком. M0 и M1 — промежуточные результаты; M2 и M3 необходимы для движения к обычному приложению с libpq. PostgreSQL SQL grammar и catalog emulation в эту работу не входят.

## P0. База, сборка и проектирование lifecycle

Проверить HEAD рабочей ветки, наличие базового SHA Firebird 6, правила репозитория, build dependencies и штатный Linux workflow. Подтвердить Engine14 по текущему `CURRENT_ENGINE`, C++20 и место добавления server objects.

Собрать штатный сервер, подготовить отдельную UTF8/dialect 3 базу и aliases, проверить native connect → SELECT → commit/rollback. Зафиксировать фактические AuthServer/AuthClient, локальную security database и разрешённый Srp256 profile. Исходные defaults не заменяют эту проверку.

Записать `IMPLEMENTATION_NOTES.md`: карту изменяемых подсистем, ownership interfaces, модель ограниченных workers, session registry, лимиты и порядок остановки. Сразу предусмотреть два execution paths (Simple/Extended), будущие statement/portal registries, TLS transport и cancellation без реализации их функций раньше соответствующего этапа.

**Приёмка:** native baseline воспроизводим; команды и результат сборки записаны. При недоступной зависимости фиксируется конкретное ограничение, успех не объявляется.

## P1. Service, listener и codec

Добавить `src/remote/server/pgwire` в штатный server target. Ввести конфигурацию с default disabled, loopback bind и портом 15432. Запустить один PG service в POSIX Super lifecycle после необходимой auth initialization и перед native loop. Другие server modes при включённой feature должны давать документированную диагностику.

Реализовать StartupMessage, SSLRequest/GSSENCRequest, Query, Terminate и serializers startup/error/simple responses. Собственные socket readiness/dispatch и bounded workers; native XDR, `asyncReceive`, crypt/compression и `op_dummy` не участвуют в PG stream. Не запускать вторую независимую `SRVR_multi_thread()`.

Проверить partial/coalesced frames, short writes, EOF, bounds, UTF8, timeouts и backpressure. Stop accept/wake readers и освобождение незавершённого startup работают уже здесь. До P2 authentication завершается явным отказом.

**Приёмка:** listener принадлежит `firebird`; native endpoint работает; выключенная feature не открывает PG port; bind failure и shutdown освобождают ресурсы.

## P2. Authentication и внутренний attachment

Выделить transport-neutral core из механики `ValidatePassword.cpp`, сохранив EDS preconditions. Core принимает явные credentials и доверенную конфигурацию базы; его размещение не должно создавать зависимость общего runtime от PG listener или от активного `thread_db` attachment.

Проверить регистрацию client/server Srp256 plugins, ограниченный обмен в памяти, alias allowlist и per-database policy. После проверки сформировать auth block, удалить raw/trusted/client-config поля, записать настоящий peer metadata. Attachment открывается по engine-only route; `Remote`/`Loopback` fallback исключён. Security DB в начальном профиле локальная.

**Приёмка:** правильный/неправильный пароль, неизвестный пользователь/alias, запрещённый plugin, RemoteAccess, параллельные identities, отсутствие вторичного DB connection. Native auth и затронутые EDS paths сохраняют поведение. AuthenticationOk отправляется только после успешного attach.

## P3. Первый вычисляемый запрос и M0

Провести полный SQL через native prepare dialect 3 → INTEGER metadata → cursor/fetch → PG result. Использовать autocommit с настоящим commit/rollback. Проверить:

```sql
SELECT CAST(40 + 2 AS INTEGER) AS "answer" FROM RDB$DATABASE
```

Клиенты получают 42, OID 23 и alias `answer`. Дополнительное выражение или таблица исключает константный ответ. После ошибочного SQL тот же connection выполняет успешный запрос. EOF/disconnect закрывает statement/cursor, transaction и attachment.

**Приёмка M0:** psycopg2 и небольшой независимый C libpq client с PQexec проходят этот сценарий против встроенного endpoint. Native listener работает одновременно. Call path и наблюдение sockets подтверждают выполнение внутри Firebird без backend connection.

## P4. Simple Query types, CRUD и diagnostics

Реализовать scalar subset M1: integers, boolean, char/varchar, exact numeric до INT64, NULL. Проверять реальные type/subtype/scale, quoted aliases, UTF8 byte lengths, NULL/empty, padding и locale-independent formatting. Ошибки преобразуются по ISC codes, а не по тексту.

Добавить INSERT/UPDATE/DELETE без RETURNING, CREATE/DROP TABLE, affected rows и правильные tags. До исполнения отвергать неподдержанные формы и metadata. Полный SQL проходит native parser; `split(';')` не используется. Scalar codecs и metadata сделать общими для будущего Extended Query.

**Приёмка:** обязательные M1 type/CRUD/error cases, batch/RETURNING rejection без side effects, ошибки fetch/commit без ложного CommandComplete.

## P5. Transaction state и обычный режим psycopg2

Реализовать whole-command BEGIN/COMMIT/ROLLBACK, I/T/E и явный TPB по CODEX_CONTEXT. Ошибка внутри explicit block откатывает native transaction и сохраняет E до завершения блока. COMMIT из E возвращает ROLLBACK. Retaining не заменяет завершение implicit transaction.

**Приёмка:** обычный psycopg2 autocommit=False, `with connection` при autocommit=True, visibility из другой сессии, unique violation → E → 25P02 → recovery, disconnect rollback, повторный BEGIN и no-op controls. Прямой libpq проверяет PQtransactionStatus и command tags независимо от поведения Python wrapper.

## P6. Серверная приёмка M1

Завершить архитектурные, authentication, wire и lifecycle tests M1. Проверить PG write/native read и обратное направление, feature disabled, ограничения ресурсов, malformed client isolation, shutdown с idle/incomplete/auth/active/blocked-query sessions.

Lifecycle проверяется с P1, а не впервые на P6. Нельзя бессрочно join SQL workers в preproviders или допускать engine calls после разрушения providers. Управление отменой engine operations здесь нужно для остановки сервера, хотя PG CancelRequest появится позднее.

**Приёмка M1:** все обязательные группы M1 из TEST_PLAN пройдены; результаты записаны в VALIDATION. Python suite — часть этой приёмки. M1 подтверждает ограниченный Simple Query profile, не общий libpq endpoint.

## P7. Extended Query, `$n` и prepared objects

Реализовать Parse/Bind/Describe/Execute/Close/Flush/Sync и отдельные registries statements/portals. Определить lifetime native prepared handles, bound buffers, cursors и implicit transaction от первого требующего её действия до Sync. Named statement может повторно исполняться после commit в новой transaction.

Добавить token-aware mapping `$n` в native slots с повторными/переставленными параметрами, явными OID и inference. Значения Bind декодируются в native buffers. Проверить NULL, format-count rules, unsupported OID/format, несоответствие параметров и отказ до side effects.

Реализовать Describe variants, NoData, ParameterDescription, PortalSuspended, Execute maxRows, Close и лимиты объектов. Error recovery discards messages до Sync. Flush выдаёт ответы без ReadyForQuery и без commit. Simple/Extended paths используют одну transaction model с корректными различиями границ.

**Приёмка:** C libpq PQexecParams, PQprepare/PQexecPrepared и Describe работают с базовыми типами. Raw-wire tests подтверждают lifetime, порядок ответов и recovery, которые не видны через один высокоуровневый execute.

## P8. Дополнительные codecs и RETURNING

Расширить общий codec layer: FLOAT/DOUBLE, DATE/TIME/TIMESTAMP без TZ, INT128/NUMERIC до 38 цифр. Реализовать text/binary input/output scalar subset M2. Для каждого типа зафиксировать OID, format, диапазоны и правила преобразования. Не копировать native layout в wire; не терять точность numeric.

Поддержать DML RETURNING с корректной выдачей строк, affected rows и command tags, включая INSERT SELECT. Прежние отрицательные проверки M1 преобразовать в положительные M2; исключение PostgreSQL casts `::date` остаётся самостоятельным syntax test.

**Приёмка:** все scalar types M2 в Simple text output и Extended text/binary input/output; независимые binary fixtures, граничные значения и aggregate metadata; RETURNING без частичных результатов успеха при ошибке.

## P9. Клиентская приёмка M2

Проверить прямой libpq: sync API, asynchronous send/get без pipeline, named/unnamed prepared statements, повторное исполнение с другими параметрами и formats. Добавить обычный Psycopg 3 Cursor с закреплённой версией, включая переход к server preparation; ClientCursor не подменяет эту проверку.

Повторить сохраняющие смысл M1 tests и архитектурную/lifecycle приёмку для ошибок Parse/Bind/Execute, открытых portals и prepared statements. Версии libpq фиксировать фактически, отдельно от версии Python package.

**Приёмка M2:** parameterized C libpq и обычный Psycopg 3 работают в согласованном профиле; Extended recovery, transaction boundaries и resource limits проверены. Не заявлять pipeline/COPY/batches автоматически.

## P10. TCP с другого узла, TLS и cancellation

Добавить конфигурируемый non-loopback bind и PostgreSQL SSLRequest → TLS в том же процессе. Проверять сертификат через libpq verify-full. Password authentication вне loopback требует TLS; native WireCrypt не используется как его замена. GSS и PG SCRAM пока остаются вне профиля.

Добавить BackendKeyData/CancelRequest и session-safe engine cancellation. Проверить invalid/stale keys, disconnect/detach races и активный SQL/lock wait. После отмены connection и transaction state должны соответствовать контракту. Повторить shutdown с TLS handshake, медленным TLS client и cancel connection.

**Приёмка:** удалённый клиент в другом сетевом узле или явно документированном изолированном network namespace, TLS verification, действительная отмена SQL; frontend и движок в одном `firebird`.

## P11. Служебные команды и профиль приложения

Выбрать приложение и записать его driver/libpq versions, параметры подключения и сценарии. Снять startup/query trace без секретов. Эту работу можно начать после M0; она не должна блокировать общий протокольный M2.

Определить обязательные session commands/settings, transaction characteristics, reset/reuse и SAVEPOINT. Для поддержанных команд реализовать реальный эффект и mapping в Firebird; неизвестные требования не подтверждать фиктивным успехом. При SAVEPOINT пересмотреть rollback strategy M1 и проверить ROLLBACK TO после ошибки.

Если приложение требует COPY, batches, pipeline, дополнительные types/auth methods или другую платформу, добавить отдельные шаги и tests до приёмки этого профиля. SQL grammar/catalog dependencies фиксировать как ограничения текущей постановки, отдельно от wire failures.

**Приёмка:** матрица приложения заполнена, его обязательные служебные операции проверены. Драйвер не заменяется и не переводится скрыто в специальный режим ради прохождения теста.

## P12. Сквозная приёмка M3 и передача

Выполнить сценарии приложения по TCP с другого узла к Firebird, с совместимым SQL, тем же PostgreSQL-драйвером и документированной конфигурацией. Повторить native cross-visibility, auth/permissions, concurrency, отмену, ошибки и shutdown для окончательного набора возможностей.

Обновить configuration docs, COMPATIBILITY и VALIDATION. Указать SHA, build/driver versions, команды, результаты, coverage и оставшиеся ограничения. Не называть все PostgreSQL-приложения совместимыми по результату одного профиля.

**Приёмка M3:** целевой прикладной профиль воспроизводимо работает; backend network hop отсутствует; родной frontend сохранён; обязательных незакрытых требований этого профиля нет.

## Порядок и приоритет

Основная последовательность: P0 → P1 → P2 → P3 (M0) → P4 → P5 → P6 (M1) → P7 → P8 → P9 (M2) → P10 → P11 → P12 (M3).

Исследование клиентского профиля P11 можно начать раньше, чтобы скорректировать приоритеты. Auth extraction, engine-only routing и lifecycle проверять маленькими срезами с начала работы. Календарные оценки давать после штатной сборки и P2, не подменяя неизвестные интеграционные риски точными сроками.
