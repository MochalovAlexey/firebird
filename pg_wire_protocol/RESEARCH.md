# Обоснование wire-плагинов для Firebird 6

Исследование относится к `master` Firebird 6; исходный SHA и рабочая ветка зафиксированы в [BASELINE.md](BASELINE.md). Это статическое обоснование, не отчёт о работающем endpoint. Актуальный контракт задаёт [ARCHITECTURE.md](ARCHITECTURE.md), этапы — [PLAN.md](PLAN.md), проверки — [TEST_PLAN.md](TEST_PLAN.md). Обозначения S/F относятся к [SOURCES.md](SOURCES.md).

## Изоляция протоколов и plugin infrastructure

Native Firebird protocol использует `P_OP/PACKET/XDR`; `rem_port`, очереди и `SRVR_multi_thread` связаны с этими сообщениями. PostgreSQL startup/tagged messages требуют собственного codec. Изменение номера native protocol либо повторный запуск native dispatcher не создаёт независимый PostgreSQL frontend [F7].

Выбран общий серверный host дополнительных wire-плагинов. Он работает внутри штатного процесса `firebird`; PG codec/session logic поставляется отдельной shared library. Родной listener работает независимо от наличия дополнительных модулей. `WireProtocolPlugins` и `WireProtocolPorts` задают позиционное соответствие логических имён плагинов и портов; точные правила проверки, отказов и lifecycle описывает ARCHITECTURE.

Firebird уже имеет PluginManager, загрузку `.so`/`.dll`, factories, владение модулем и cleanup. Для wire-протоколов требуется новый тип и версионированные CLOOP interfaces; существующий `IWireCryptPlugin` предназначен для шифрования native bytes и не заменяет их. Логическое имя разрешается в модуль через штатную plugin configuration [F10].

Два позиционных списка сохраняются. Разные aliases могут указывать одинаковые `Module`/`RegisterName`, но разные `Config` sections в `plugins.conf`: это позволяет назначать endpoint-specific bind/TLS settings без новых параллельных списков. Проверка host использует один минимальный тестовый модуль с двумя экземплярами и разными конфигурациями; второй протокол СУБД для этого не нужен. Поддержка разных настроек одного модуля не должна зависеть от порядка загрузки aliases [F10].

Сборочные списки не рекурсивны: `dirObjects(remote/server)` не подхватит произвольный новый подкаталог. Host objects включаются в server target явно; PG implementation получает отдельный plugin target и install rule. Линковка штатного `firebird` с `libfbclient` уже существует; отсутствие backend network hop доказывается provider route и runtime-наблюдением, а не именем runtime-библиотеки [F6, F9].

## SQL принадлежит парсеру и движку

SQL передаётся в Firebird неизменным, с явной длиной. Wire-код проверяет framing, NUL, encoding и лимиты; он не разбирает SQL, не ищет первое ключевое слово, не делит batch по `;` и не заменяет `$n`. Предложение внешнего review классифицировать по первому слову отклонено в соответствии с требованием пользователя.

`JAttachment::prepare` передаёт запрос в DSQL. Текущее начальное правило grammar принимает один statement с необязательной завершающей `;`; несовместимый SQL получает ошибку этого parser. SQL whitelist только для CREATE/DROP TABLE не нужен. Ограничения wire codecs и API-возможностей должны быть явными, но не превращаться во второй SQL dialect [F4].

Публичные statement kind/flags недостаточны для точного command tag: RETURNING может описываться как SELECT/EXEC_PROCEDURE, MERGE и UPDATE OR INSERT как INSERT, session management как DDL. COMMIT и COMMIT RETAINING также сводятся к одному публичному kind. Требуется нейтральная metadata от parser/engine о семантике команды и её влиянии на transaction/session; она расширяет engine API, а не создаёт PG-specific SQL parser [F4, F5].

Это расширение вводится постепенно. M0 требует лишь минимальный новый info item через существующий `IStatement::getInfo`, различающий исходный SELECT, DML RETURNING и SELECT_BLOCK. Он сохраняет legacy `getType()` и позволяет ограничить первый исполняемый срез обычным SELECT без keyword checks. Полная metadata для DML/DDL/session/transaction controls появляется в P4/P5; её проектирование не означает реализации всей матрицы до первого запроса.

Отдельный публичный pre-prepare analysis API не является условием M0 или обработки E. В failed explicit block допустим обычный prepare в сохраняемой native transaction: после успеха решение о возможности исполнения принимается по metadata. Только известные semantic prepare errors при такой проверке отображаются в 25P02; lexer/parser syntax errors сохраняют 42601. Fatal/shutdown/cancellation/timeout/invalid handle/resource/I/O failures и неизвестные ошибки не маскируются. Подготовка не исполняет statement, но может компилировать, загружать metadata/external routines, брать locks и вызывать trace; проверяются cleanup и отсутствие утечек. Точные E/recovery правила задаёт CODEX_CONTEXT.

EMPTY/comment-only устанавливается штатным parser. Нынешний `isc_command_end_err2` встречается и для незавершённого `SELECT`, поэтому он не является признаком пустоты. Возможен отдельный вложенный диагностический code при сохранении legacy ошибки либо opt-in prepare flag для нового результата EMPTY. Глобальное превращение прежней ошибки в успешный prepare не допускается. Существующая обработка завершающего `;` внутри DSQL также сохраняется для legacy callers: требование неизменных bytes относится к передаче SQL через wire/host, а не к удалению штатной внутренней нормализации [F4].

`BEGIN` и `$n` нельзя незаметно поддержать в wire layer: их будущая поддержка относится к grammar/parameter metadata. Пока соответствующего синтаксиса нет, запросы завершаются штатной ошибкой Firebird. Тесты используют native SQL, включая поддерживаемое управление транзакциями. Autocommit позволяет начать проверку libpq без автоматически добавляемого драйвером `BEGIN`.

## Общий authentication service и engine route

`EDS::validatePassword` демонстрирует ограниченный обмен auth client/server plugins в памяти, но требует текущий `thread_db` attachment и существующий auth block. Он компилируется в Engine14, поэтому не является готовым pre-attach вызовом для host [F8].

Общая проверка credentials обслуживает wire-плагины через host service с явными входами и доверенной конфигурацией базы. Копирование этой проверки в каждый протокольный модуль не решает границу ответственности. Существующий EDS path может оставаться неизменным; если общая часть извлекается из него, сохраняются preconditions и добавляется целевая регрессия EDS. Проверяются обе регистрации Srp256, per-database plugin policy, ограниченный auth loop, неизвестные статусы и cleanup [F8].

Host создаёт attachment после проверки credentials: записывает проверенный auth block, удаляет raw/trusted/internal/client-config DPB fields, задаёт достоверный peer address и имя протокола. Это необходимо для identity, privileges и `RemoteAccess`. Native server использует dispatcher с configured providers; требование local-engine-only для новых wire-плагинов строже его обычного provider selection. После sanitization host выбирает `EMBEDDED_PROVIDERS` (`Engine14`); переопределяется выбор provider, а не остальные per-database policies. `getNonLoopbackProviders` недостаточен: он оставляет `Remote`, в том числе для security database. Начальный профиль требует локальную security DB и проверки отсутствия сетевого auth hop [F3, F8].

Native attach передаёт `setDbCryptCallback`; PostgreSQL-клиент не реализует этот Firebird обмен ключами. Базы, требующие получения ключа от клиента, не входят в исходный PG profile. Это не запрет всех encrypted databases: ключ может быть доступен серверному crypt/key-holder plugin. Такой сценарий требует отдельного fixture [F11].

## Драйверы и границы доказательств

Обычный синхронный execute закреплённого psycopg2 2.9.11 использует `PQexec`; параметры адаптируются клиентом в SQL. Этот клиент также отправляет собственные transaction commands. Его успешное подключение не доказывает совместимость всех libpq API [S5, S6].

`driver_probe.json` описывает синтетический PostgreSQL endpoint без Firebird. Скрипт стенда отсутствует в исходном пакете; JSON остаётся историческим наблюдением о драйвере. Перенесённый Python suite требует миграции: ожидания `BEGIN`, SQL allowlist и прежних этапных отказов не являются универсальным контрактом реализации. Количество tests не равно количеству доказанных возможностей.

`PQexecParams`, `PQprepare/PQexecPrepared` требуют Extended Query. Проверка Parse/Bind/Describe/Execute/Close/Flush/Sync отделяется от SQL syntax compatibility: сначала проверяется поддерживаемый SQL и native `?` parameters прямым libpq harness, затем отдельно — PostgreSQL `$n` после изменения parser. Клиентские `::date`, catalog queries и служебные SET также остаются зависимостями grammar/functions/catalog, даже при готовом wire codec [S4, S7, S16].

Профиль приложения и trace startup/SQL следует выбрать рано. Если драйвер или ORM автоматически посылает несовместимый SQL, это фиксируется как зависимость расширения parser/functions; wire plugin не эмулирует успешное исполнение таких команд.

## Extended Query, результаты и ошибки

Prepared statement и portal имеют разный lifetime; portal связывает параметры и cursor с transaction context. Sync задаёт границу implicit transaction, Flush позволяет получить сообщения до Sync, а ошибка требует discard-until-Sync. RowDescription/ReadyForQuery нельзя копировать из Simple Query после каждого Execute [S1, S16].

Одна активная application transaction на connection — ограничение PgWire и его PostgreSQL I/T/E модели. Host ABI и native Firebird attachments не получают такого глобального ограничения: их способность обслуживать несколько transactions сохраняется.

OID выбирает decoder клиента. Кодек использует реальные type/subtype/scale, message offsets, NULL indicator и metadata lifetime. Расширение результата агрегата учитывается после prepare; binary wire layout не совпадает с native buffers. Неподдержанный тип даёт явную ошибку до выполнения, когда metadata уже доступна [F1, F5, S13, S14].

CHAR UTF8 требует различать объявленные символы и максимальные байты буфера. При чтении как SQL_TEXT padding может занимать весь буфер N×4. Ни выдача всех этих пробелов, ни безусловный rtrim не сохраняют значение CHAR(N). Firebird уже преобразует внутренний CHAR в varying, оставляя внешнее описание CHAR; предпочтительный путь — запросить fetch metadata через `IMetadataBuilder` как SQL_VARYING, сохранив исходный тип для PG OID. Проверки включают ASCII, многобайтные символы, пустую строку, значимые конечные пробелы и `'xy  '` для CHAR(4) [F5].

DSQL status vector часто начинается с `isc_dsql_error`/`isc_sqlerr`; конкретная причина и дополнительный timeout/shutdown code расположены дальше. Mapping должен безопасно обходить весь tagged vector и выбирать наиболее конкретную причину. `fb_sqlstate` полезен как пример обхода, но Firebird `42S02`/`HY008` не равны PostgreSQL `42P01`/`57014`. Текст ошибок не является ключом классификации [F5, S8, S9].

## Lifecycle, server modes и TLS

Сессия владеет socket, auth state, attachment, transaction, statements, portals и cursors. Module lifetime охватывает все callbacks, interfaces и workers; библиотека выгружается после их завершения. Cleanup не затирает первичную ошибку и учитывает consuming операции API [F1, F9, F10].

Shutdown выполняется по фазам preproviders → providers/interfaces → postproviders. Host прекращает accept, будит socket waits и согласованно отменяет engine operations через `IAttachment::cancelOperation`. Бесконечный join перед остановкой providers недопустим. В приёмку входят server shutdown, `MON$ATTACHMENTS`, `MON$STATEMENTS`, database shutdown, `ConnectionIdleTimeout` и `StatementTimeout`. Для idle socket отдельно определяется обнаружение завершённого attachment и доставка FATAL/закрытие; наличие engine error на следующем API call не доказывает мгновенное уведомление [F9, F12].

Admin/database shutdown отображается в FATAL `57P01`, query cancellation/statement timeout — в `57014`, idle-session timeout — в FATAL `57P05`, когда причина известна. Дополнительная диагностика берётся из полного status vector [F12, S9].

Super и SuperClassic используют общий многоклиентский listener path, поэтому безусловный архитектурный запрет SuperClassic не обоснован. Но database sharing и cleanup в движке различаются. Поддержка каждого режима требует собственных lifecycle/coexistence checks; Classic имеет отдельный process/fork lifecycle [F9].

В проверенной server build нет готовой TLS-библиотеки. `TLS_OPTIONS` в configure относится к thread-local storage; libtomcrypt/native WireCrypt не обеспечивает PostgreSQL TLS. Выбор TLS dependency, сборочных flags и упаковки фиксируется в P0, даже если SSLRequest upgrade реализуется позже. PostgreSQL password exchange вне loopback требует защищённого TLS канала; Firebird Srp256 не является PostgreSQL SCRAM [F6, S17].

## Оставшиеся проверки

- Штатная сборка master и отдельный shared plugin target; загрузка, отключение, ошибка модуля и доступность native listener.
- CLOOP ABI, порядок загрузки/освобождения modules и позиционная config validation.
- Общий pre-attach auth service, per-database policy, engine-only route и crypto profile.
- Достаточная engine metadata без SQL-разбора в wire layer; корректная модель transaction/session effects.
- CHAR/numeric/binary conversions, полный status mapping, portal lifetime и recovery до/после Sync.
- Super/SuperClassic shutdown, внешняя отмена, idle detection и TLS transport.
- Миграция fixtures и trace конкретного приложения с разделением wire failures и SQL dependencies.

Совпадение `MON$SERVER_PID` и успешный SELECT не заменяют доказательство пути: проверяются владелец listener, выбранный provider, исходящие connect() сервера и видимость изменений независимым native клиентом. Все перечисленные свойства требуют исполняемых проверок; чтение исходников их не подтверждает.
