# Обоснование PostgreSQL frontend для Firebird 6

Целевая архитектура — два сетевых frontend в одном процессе `firebird`, с общим движком и базами Firebird. Выбранная база `master` совпадает с SHA исходного исследования: `bf94598f8deefeb7cc4a8217ee43b3040d5d6650`, Firebird `6.0.0.2199 Initial`. Ветка разработки — `pg_wire_protocol`; подробности — [BASELINE.md](BASELINE.md).

Ниже собраны решения, влияющие на реализацию, и вопросы, которые нужно закрыть сборкой/экспериментом. Это архитектурное исследование, не результат работы нового endpoint. Контракт задают CODEX_CONTEXT и COMPATIBILITY; последовательность — PLAN. Обозначения S/F относятся к [SOURCES.md](SOURCES.md).

## Почему нужен самостоятельный frontend

Native Firebird protocol основан на P_OP/PACKET/XDR. PostgreSQL startup и обычные tagged messages имеют иное кадрирование. Добавление native PROTOCOL_VERSION не меняет codec. `rem_port`, queues, `asyncReceive`, send/receive, crypt/compression и keepalive связаны с native packets [F7].

Отдельный listener внутри процесса позволяет определить codec до чтения сообщения и сохранить native путь. Для первого Linux/SuperServer варианта предлагаются свои socket readiness, session registry и bounded workers. Повторный запуск SRVR_multi_thread поверх глобальной INET/queue infrastructure требует рефакторинга и не является готовым способом добавить второй протокол.

Новые server sources размещаются в `src/remote/server/pgwire` и входят в Remote_Server/FB_DAEMON. Remote_Common используется также клиентом, поэтому listener туда не переносится. Текущая линковка штатного сервера с libfbclient допустима: нативность определяется выбранным provider/call path, а не именем библиотеки [F3, F6, F9].

## Вызовы движка

Native server уже использует IAttachment/ITransaction/IStatement/IResultSet. Новый frontend может работать на том же уровне interfaces без native packet handles и вторичного соединения. Prepare получает совместимый Firebird SQL и dialect 3; metadata/flags определяют execute либо openCursor/fetch [F1–F4].

Состояние session отделяется от transport, а код выполнения и codecs разделяются между Simple и Extended Query. Это позволяет начать с PQexec и добавить Bind/portals без второй независимой модели транзакций.

Полный Simple Query statement проверяется native parser до исполнения. Нельзя делить текст через split(';'). Для BEGIN/COMMIT/ROLLBACK и классификации поддержанных форм нужен ограниченный lexical recognizer с учётом строк/комментариев. SQL batches пока отклоняются; их будущая поддержка потребует отдельной работы с границами команд и implicit transaction, а не исполнения первого распознанного фрагмента [F4, S1].

## Аутентификация и engine-only route

`ValidatePassword.cpp` содержит обмен Firebird auth client/server plugins в памяти. EDS wrapper требует уже существующий attachment/auth block, поэтому pre-attach вызов новой PG session требует выделения общей части, сохраняющей guards EDS [F8].

Начальный профиль — Srp256, разрешённый действующей per-database policy, и локальная security database. Наличие core в исходниках ещё не подтверждает его корректную работу из PG startup: нужно проверить регистрацию обеих plugin factories, config resolution, результаты plugin loop и cleanup.

После authentication применяются правила DatabaseAuth::accept: проверенный auth block, удаление raw/trusted/client-controlled DPB fields и настоящий remote address. Последний влияет на RemoteAccess; внутренний вызов не должен превращать сетевого клиента в локальную identity. Выбирается только CURRENT_ENGINE/EMBEDDED_PROVIDERS. getNonLoopbackProviders удаляет Loopback, но сохраняет Remote [F8].

M1 использует PostgreSQL password exchange на loopback. M3 переносит тот же обмен внутрь TLS для удалённых подключений. PostgreSQL SCRAM и Firebird SRP — разные протокольные механизмы; поддержка Srp256 не означает, что клиент с требованием PG SCRAM сможет подключиться. Такой профиль потребует отдельного решения [S1, S17, F8].

## Почему одного psycopg2 недостаточно

Закреплённый psycopg2 2.9.11 в обычном synchronous execute вызывает PQexec; параметры адаптируются в SQL на клиенте. Это делает его подходящим первым клиентом Simple Query [S6].

В приложенном driver_probe.json зафиксированы startup 3.0, Query, BEGIN/COMMIT/ROLLBACK, ParameterStatus и работа без BackendKeyData для этого клиента. Сервер опыта был синтетическим; Firebird не участвовал. Скрипта стенда среди приложенных файлов нет. JSON подтверждает только записанное наблюдение, а не интеграцию.

Libpq включает PQexecParams, PQprepare/PQexecPrepared и asynchronous counterparts, использующие Extended Query. Поэтому M2 является самостоятельным обязательным этапом, а прямой C libpq harness добавляется уже в M0/M1. Обычный Psycopg 3 Cursor используется в M2 как второй клиент; перевод в ClientCursor не проверяет тот же контракт [S4, S7, S16].

Параметры `$n` требуют token-aware mapping к native slots и корректного input metadata. Это отдельный integration contract. PostgreSQL casts, добавленные адаптером, остаются SQL-несовместимостью: появление DATE codec не включает поддержку `::date` [S6].

## Сложности Extended Query

Statement и portal различаются по lifetime: подготовленный запрос может использоваться после commit, а portal содержит привязанные значения и cursor в transaction context. Разработка должна определить, как Firebird prepare получает transaction и как native handles переживают границы PG cycle.

Главная граница implicit transaction — Sync, а не отдельный Execute. Flush позволяет клиенту получить ответы до Sync. Ошибка требует discard-until-Sync; следующая уже поступившая команда не должна исполняться случайно. RowDescription/ReadyForQuery нельзя рассылать по схеме Simple Query после каждого сообщения [S1, S16].

Параметры с OID=0, повторное использование `$n`, разные native contexts и binary format потребуют самостоятельных fixtures. Их нельзя проверить только запросом с одним INTEGER параметром. Поддержка Extended Query сама по себе не доказывает libpq pipeline API.

## Типы и результаты

OID выбирает клиентский decoder; выдача всех значений как text OID скрывает отсутствие типизированной совместимости. Native type/subtype/scale, NULL offsets, CHAR padding и exact decimal formatting нужны уже в M1 [F1, F5, S14].

Узкий scalar subset M1 — ограничение первого этапа. M2 включает floating point, temporal без TZ, INT128/numeric до 38 цифр и RETURNING. Metadata результата может быть шире исходной колонки, например у агрегата; выбор codec делается после prepare. Binary protocol требует собственных преобразований вместо копирования native buffers [S2, S4, S13].

RETURNING уже поддерживается движком, но требует корректной классификации command tag и affected rows при наличии cursor. Его реализация не требует общего PostgreSQL parser. Аналогично DATE output codec — самостоятельная задача frontend.

## Транзакции и служебные команды

Обычный psycopg2 сам посылает BEGIN, включая connection context при autocommit=True. Поэтому M1 включает I/T/E и настоящий commit/rollback, без retaining вместо autocommit. При E дальнейший SQL отвергается до завершения блока [S5, S6, S10].

Модель M1 без savepoints допускает rollback native transaction с сохранением logical E. Для SAVEPOINT потребуется возможность rollback-to после ошибки, а значит изменение обработки ошибок. Поведение locks/isolation нужно тестировать отдельно; одно совпадение ReadyForQuery state этого не доказывает.

Connection settings и служебные команды драйвера/пула могут появляться до прикладного SQL. Перед M3 нужен trace целевого приложения. Поддержанные read-only/isolation/encoding/timeout/reset команды должны менять реальное состояние; успешные заглушки SET/SHOW создают скрытую несовместимость. PostgreSQL catalog emulation остаётся за пределами текущей задачи.

## Lifecycle и доказательство выполнения внутри Firebird

Сессия владеет socket, auth state, attachment, transaction, statements, portals и cursors. Ownership должен учитывать consuming API operations и refcount wrappers. Cleanup использует отдельный status и не затирает первичную ошибку [F1, F9].

Shutdown проходит preproviders → остановку providers/interfaces → postproviders. PG service прекращает accept, будит readers, останавливает новые operations и согласованно завершает workers. Бесконечный join в preproviders может препятствовать самой остановке движка. Этот риск проверяется с P1, а не после всех codecs [F9].

Совпадение MON$SERVER_PID и успешный SELECT недостаточны для доказательства нативности. Нужны владелец listener, review provider route, наблюдение исходящих connect() из сервера и cross-visibility через независимый native клиент. Security DB attachments в памяти допустимы; backend network hop в execution path — нет.

## Что ещё предстоит установить

- Работоспособность штатной сборки выбранного master и наличие нужных dependencies.
- Размещение общего auth core и сохранение EDS/native поведения после его выделения.
- Runtime provider route, remote policy и порядок остановки PG workers.
- Точные native/PG type conversions, prepared-object lifetime и errors до/после Sync.
- Выбранный TLS механизм в build, cancellation ownership и remote fixture.
- Приложение, его driver/libpq versions и обязательные session operations для M3.

Эти вопросы имеют отдельные этапы и критерии в PLAN/TEST_PLAN. Календарную оценку следует уточнить после P0/P2; исследование не подменяет executable integration evidence.
