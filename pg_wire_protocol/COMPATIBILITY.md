# Матрица совместимости PostgreSQL wire plugin

Матрица M0–M3 задаёт границы этапов. База — master Firebird 6, SQL dialect 3, UTF8, wire 3.0. Архитектура — [ARCHITECTURE.md](ARCHITECTURE.md), execution contract — [CODEX_CONTEXT.md](CODEX_CONTEXT.md). P4 с ограниченным lexer внутри плагина и существующими engine interfaces принят в целевом Linux/Super loopback профиле. Сборка, unit tests и повторные SQL/lifecycle/startup/native проверки прошли; evidence и исторические результаты находятся в [VALIDATION.md](VALIDATION.md). Следующий этап — P5: explicit I/T/E и session controls. TLS, Windows и другие server modes требуют отдельной реализации и приёмки.

## Startup/authentication: Linux/Super P2 PASS

Отдельный PgWire module добавлен в production POSIX/CMake build definitions. На Linux/Super IPv4 loopback он реализует protocol 3.0 Startup, SSL/GSS refusal N, cleartext Password exchange с настоящим Firebird Srp256 и локальный engine attachment. AuthClient/AuthServer policy, RemoteAccess и точный DatabaseAliases allowlist сохраняются; локальная security DB требует Engine14 в Providers; пустой allowlist запрещает attachments. Host attachment borrowed, plugin закрывает его через host detach. Identity и peer проверены native observer: MON$REMOTE_PROTOCOL=TCPv4, MON$REMOTE_VERSION=configured endpoint alias.

User обязателен, database по умолчанию равен user; явно пустой database отвергается. Допустимы application_name и отсутствующий/точный UTF8 client_encoding; иные startup keys/options/replication явно отклоняются. Startup ограничен 16 KiB, typed frame 1 MiB, Password body 4096 bytes с NUL. IOTimeout ограничивает whole frame и общий startup network budget; синхронный auth/attach callback до handle не получает hard preemption этим timeout.

AuthenticationOk отправляется после attach, затем ParameterStatus и ReadyForQuery I. Version token — `16.0 (Firebird pgwire prototype)`, DateStyle — `ISO, MDY`. Исторический P2 отвечал одинаковым 0A000 на все корректные Query; P3 заменяет это поведение engine execution, описанным ниже. Extended, TLS и BackendKeyData/cancel ещё отсутствуют. P2 PASS относится к записанным startup/authentication checks, не к полной матрице M0–M3.

## Текущий P4: Linux/Super loopback PASS

Совместимые SELECT/DML/DDL, RETURNING и EXECUTE BLOCK/PROCEDURE выполняются движком. Используются существующие getType/getFlags, input/output metadata, affected records и plugin-local lexical descriptor. Дополнительные public statement info items и EMPTY diagnostics удалены из движка. Повторная приёмка этой ревизии прошла: 12 Simple Query групп с trace, четыре unit executable, 7 lifecycle, 52 startup и 9 native checks. C libpq 14.24 и psycopg2 2.9.11/libpq 17.6 проверены отдельно; backend connects — 0. Результаты и историческая P3/P4 приёмка находятся в [VALIDATION.md](VALIDATION.md).

Text output: SMALLINT/INTEGER/BIGINT (OID 21/23/20), BOOLEAN (16), CHAR/VARCHAR (1042/1043), exact NUMERIC/DECIMAL в SHORT/LONG/INT64 (1700), NULL. Формат 0, typmod-1, table OID/attribute 0; logical CHAR padding сохраняется через varying fetch metadata. Unsupported types/charsets/input parameters получают 0A000 по metadata до execution.

Каждый Query использует одну implicit READ COMMITTED/write/nowait transaction. CommandComplete отправляется после успешного commit. DML tag отражает affected records, включая RETURNING; EXECUTE BLOCK/PROCEDURE не добавляют count. Ошибка вызывает rollback, recoverable error возвращает ReadyForQuery I; uncertain commit/cleanup закрывает connection. P5 добавит explicit I/T/E.

Parser error —42601, unknown relation —42P01, permission —42501, unique —23505, FK —23503, CHECK —23514; общий native NOT NULL/domain status остаётся 23000 без угадывания subclass по message.

## Главная граница

SQL передаётся в engine prepare целиком, с исходной byte length и без изменения текста. Ограниченный lexer внутри PgWire определяет вид команды, command tag, профиль transaction/session controls и признак tokenless EMPTY. Он учитывает comments, строки и quoted identifiers, но не проверяет grammar, имена объектов, privileges или типы. Общий wire host SQL не анализирует.

После успешного engine prepare плагин сочетает lexical descriptor с существующими `getType()`, `getFlags()`, input/output metadata и affected records. `FLAG_HAS_CURSOR` выбирает cursor/fetch; output metadata определяет singleton output и codecs. RETURNING не превращает DML в SELECT. Неподдержанный control либо UNKNOWN, включая USING wrapper, получает 0A000 до execution; ошибка самого prepare сохраняет приоритет. Новые публичные DSQL getInfo items, AST annotations и parser diagnostics для PgWire не добавляются.

Запрещены SQL rewriting, удаление comments из передаваемого текста, разбиение batches, замена `$n` на `?`, подстановка параметров SQL-литералами и эмуляция успешного SET/BEGIN. Совместимость синтаксиса и реальное исполнение определяет только Firebird. Неподдержанный batch передаётся parser целиком и отвергается без частичных эффектов.

EMPTY определяется ограниченным lexer только для нулевой строки, whitespace и полностью закрытых comments без SQL tokens. Даже такой текст передаётся в обычный engine prepare неизменённым. Только сочетание этого признака с engine syntax error 42601 после успешного cleanup преобразуется в EmptyQueryResponse; permission, cancellation, resource и другие ошибки не маскируются. Незаконченные строка/comment/statement и отдельный `;` остаются parser errors. Native parser diagnostics и `isql -autoterm` не меняются.

BEGIN/$n и другие несовместимые формы остаются native parser errors. Новые grammar/functions/catalogs требуют отдельной будущей работы в движке; разрешённая классификация не добавляет SQL-совместимость.

## Возможности по этапам

| Возможность | M0 | M1 | M2 | M3 |
|---|---|---|---|---|
| Wire host и PG plugin в процессе `firebird` | Загрузка и первый endpoint | Lifecycle/config tests | Сохранить | Сохранить |
| Native listener при пустом списке и при включённых plugins | Да | Да | Да | Да |
| Firebird credentials, local engine, реальные privileges | Да | Да | Да | Да |
| Simple Query с неизменённым Firebird SQL | SELECT и error recovery | CRUD, совместимый DDL/session SQL, RETURNING | Сохранить, общие codecs | Сохранить |
| Command/control descriptor | Минимальный SELECT-профиль | Ограниченный lexer в plugin + штатные engine interfaces | Общий для обоих paths | Общий |
| EMPTY / whitespace/comment-only SQL | Не критерий первого SELECT | Plugin tokenless признак + engine prepare syntax error; native diagnostics неизменны | Общий для обоих paths | Общий |
| Прямой C libpq | Connect/PQexec | PQexec, metadata, state/error checks | PQexecParams, PQprepare/PQexecPrepared, Describe, async send/get | Прикладные сценарии |
| psycopg2 2.9.11 | Autocommit smoke без connection context | Autocommit и явный native transaction SQL, без автоматического BEGIN | Регрессия допустимого профиля | Если соответствует выбранной трассе |
| Parse/Bind/Describe/Execute/Close/Flush/Sync | Явный отказ | Явный отказ | Да | Да |
| Named/unnamed statements и portals | Нет | Нет | Да, с лимитами и проверенным lifetime | Да |
| Bind с native `?` либо zero parameters | Нет | Нет | Да, по engine metadata | Да |
| SQL `$n`, standalone BEGIN, PostgreSQL casts `::` | Parser error | Parser error | Parser error | Parser error без отдельного engine расширения |
| I/T/E, native SET TRANSACTION/COMMIT/ROLLBACK | Implicit Query transaction | Да, plugin-local controls после engine prepare | Да, включая implicit cycle до Sync | Да |
| Native SAVEPOINT/ROLLBACK TO/RELEASE и RETAIN | Не критерий smoke | Реальный effect и state policy | Тот же контракт | Тот же контракт |
| SMALLINT/INTEGER/BIGINT, BOOLEAN, CHAR/VARCHAR, NUMERIC до INT64, NULL | Три integer-типа, scale/subtype 0, NULL; text results | Text results | Text/binary input/output | Да |
| FLOAT/DOUBLE, DATE/TIME/TIMESTAMP без TZ, INT128/NUMERIC до 38 цифр | Не критерий smoke | Явное ограничение codec | Text/binary input/output | Да |
| DML RETURNING | Не критерий smoke | Да для поддержанных типов, правильная исходная операция | Общие расширенные codecs | Да |
| Native session commands/transaction characteristics | Не критерий smoke | Проверяет и выполняет движок; plugin descriptor и handles отражают эффект | Тот же контракт | Нужные приложению формы проверены |
| Подключение с другого сетевого узла | Loopback | Loopback | Loopback | Да |
| PostgreSQL SSLRequest / TLS | Ответ N | Ответ N | Ответ N | Upgrade в том же endpoint, проверка сертификата клиентом |
| BackendKeyData / CancelRequest | Нет | Нет | Общий lifecycle подготовлен | Да |

Общий host использует позиционные `WireProtocolPlugins` / `WireProtocolPorts`; пустая конфигурация отключает дополнительные endpoints. PG plugin загружается штатным PluginManager из `.so`/`.dll` по правилам ARCHITECTURE.md. Ошибка дополнительного plugin не должна делать native protocol зависимым от него. Один PG plugin — первая реализация общего extension point; реализация MySQL/MSSQL plugins в этот план не входит.

Одна библиотека/factory допускает несколько configured aliases с отдельными endpoint instances и секциями `Config`. Bind/TLS/protocol settings относятся к alias; единственными позиционными списками остаются plugins и ports. Независимость instances проверяется одним тестовым модулем с двумя aliases, а не требует двух СУБД-протоколов или двух библиотек.

## Синтаксис и клиентские режимы

| Клиентский сценарий | Результат без расширения grammar |
|---|---|
| `PQexec` с совместимым SQL | Simple Query M0/M1 |
| psycopg2 `autocommit=True`, обычный cursor без `with connection` | Совместимый SQL M0/M1 |
| psycopg2 transactional mode либо connection context, генерирующий BEGIN | BEGIN отклоняется parser; сценарий не заявляется поддержанным |
| Прямой libpq, native SET TRANSACTION затем совместимый SQL/COMMIT/ROLLBACK | Explicit transaction tests M1 |
| `PQexecParams` с `CAST(? AS INTEGER)` в совместимом Firebird statement | Native binding M2 |
| `PQprepare`/`PQexecPrepared` с native `?` либо без parameters | Statement/portal lifecycle M2 |
| `PQexecParams`/обычный драйвер с SQL `$1` | Parser error до будущего engine parameter syntax |
| Драйвер/ORM, отправляющий PostgreSQL SET, catalog queries или функции | Успех только если конкретный SQL уже совместим; иначе настоящая ошибка |

В Simple Query `%s` — placeholder клиентской библиотеки, сервер его не заменяет. Адаптер может добавить PostgreSQL cast, BEGIN или catalog query. Поэтому даже обычный SELECT приложения не доказывает совместимость всего поведения драйвера.

В M2 количество, порядок и типы входных native slots берутся из engine metadata. OID hints из Parse и значения Bind проверяются и преобразуются без изменений SQL. Одинаковое значение для нескольких native `?` передаётся несколькими protocol parameters. Будущие повторные `$1` и перестановка индексов должны описываться engine binding metadata после расширения parser; plugin не ищет их в SQL.

Наличие DATE codec не включает cast `::date`, а поддержка Bind не означает поддержку `$n`. Если Firebird не может вывести тип параметра, например в нетипизированном выражении, возвращается ошибка; SQL не оборачивается в CAST автоматически. Тесты отдельно различают syntax, inference и codec limitations.

Psycopg 3, pgjdbc, Npgsql, pgx и другие клиенты не объявляются поддержанными по факту Extended Query. Их startup, автоматически генерируемый SQL и metadata discovery проверяются на выбранных версиях. Для минимального M2 acceptance достаточно прямого libpq с native SQL и обязательных raw-wire последовательностей.

## Семантика результатов и транзакций

Совместимые CREATE/ALTER/DROP, index и privilege операции покрываются descriptor и тестами. Неописанная команда получает явный 0A000 после prepare до исполнения. Command tag дополняет coarse getType сведениями ограниченного lexer: MERGE, UPDATE OR INSERT, RETURNING, DDL modifiers и control/session forms не смешиваются.

Прежний M0 SELECT-only фильтр не является текущим execution gate. P4 поддерживает обычные DML/DDL и ограничивает unsupported capabilities; подробный plugin-local control descriptor и state policy остаются P5. Новые getInfo items или parser observers не требуются.

M1 state policy сохраняет native transaction при ошибке explicit block и ставит E. Обычный prepare выполняется на этом handle; plugin-local control descriptor после prepare определяет разрешённый recovery, остальные успешно подготовленные команды блокируются с 25P02 без execution. Lexer/parser error остаётся 42601. Только известные обычные semantic prepare errors переводятся в 25P02; fatal/shutdown, cancel/timeout, invalid handle, resource/I/O и неизвестные ошибки не маскируются. Отдельный pre-prepare analysis API не требуется. Полный COMMIT из E откатывает текущую работу; ROLLBACK завершает её; успешный ROLLBACK TO восстанавливает T и сохраняет изменения до savepoint. Retaining actions учитывают сохранение handle: ROLLBACK RETAIN может очистить failed block, COMMIT RETAIN в E не может сохранить его изменения. Полная таблица и edge cases заданы в CODEX_CONTEXT.md. Факт I/T/E не обещает совпадения isolation, locks или DDL semantics с PostgreSQL.

PgWire session управляет максимум одной текущей application transaction на своём attachment. Parse/Describe/statements/portals используют её либо prepare с NULL, без отдельных скрытых transactions. Внутренние engine system/security/autonomous transactions исключены из этого счёта. Это ограничение PgWire, не ограничение native protocol или общего host для других plugins. RESET проверяется после нескольких Parse/Describe/portals, включая смену underlying transaction при неизменном interface pointer.

Явные native transaction characteristics сохраняют семантику Firebird. В частности, `SET TRANSACTION AUTO COMMIT` может зафиксировать изменения до конца логического блока: последующий rollback не отменяет прежние commits. AUTO COMMIT / NO AUTO UNDO учитываются локальным descriptor и реальным engine execution и имеют отдельные проверки; I/T/E не подменяет их поведение обещанием PostgreSQL atomicity.

EMPTY определяется ограниченным lexer только для нулевой строки, whitespace и полностью закрытых comments без SQL tokens. Даже такой текст передаётся в обычный engine prepare неизменённым. Только сочетание этого признака с engine syntax error 42601 после успешного cleanup преобразуется в EmptyQueryResponse; permission, cancellation, resource и другие ошибки не маскируются. Незаконченные строка/comment/statement и отдельный `;` остаются parser errors. Native parser diagnostics и `isql -autoterm` не меняются. CHAR/UTF8 padding и full status vector имеют отдельные tests и не меняют эту границу.

## Отдельные расширения и ограничения

- PostgreSQL grammar/functions/catalog compatibility — будущий engine этап. Если он станет обязательным для целевого приложения, M3 этого приложения зависит от него явно.
- Protocol 3.2, direct TLS negotiation, GSS encryption, PostgreSQL SCRAM/MD5 и channel binding — отдельные возможности. M3 начинает с password exchange внутри TLS и существующих Firebird credentials.
- Simple Query batches, COPY, libpq pipeline mode, SQL named cursors, large-object API, LISTEN/NOTIFY и replication — отдельные wire/session contracts.
- BLOB/bytea, ARRAY, OCTETS, DECFLOAT, типы с часовыми поясами и PostgreSQL custom types — отдельные codecs. Firebird BLOB handle не выдаётся за bytea.
- Remote security database и базы, требующие ключ шифрования от native клиента, не входят в начальный authentication profile.
- Текущая платформа — Linux/Super. SuperClassic/Classic пока диагностируются с сохранением native; для них требуется отдельная работа и приёмка. Windows host wiring/runtime отсутствуют; `.dll` входит в архитектуру plugin ABI, но не доказывает готовность Windows build.

Неподдержанный тип или интерфейсный режим возвращает явную диагностическую ошибку до side effects, когда metadata позволяет это определить. Ограничения не скрываются фиктивными SET, успешными пустыми ответами или coercion всех результатов в text.

## Прикладной профиль и доказательства

Приложение и трасса выбираются до завершения M1. В `IMPLEMENTATION_NOTES.md` фиксируются версии приложения/драйвера/libpq, ОС, connection settings без секретов, wire operations, автоматически генерируемый SQL, типы, isolation/autocommit, reset/reuse и cancellation. Трасса определяет зависимости от будущих grammar/catalog изменений заранее.

Критерий M3 — воспроизводимый сценарий конкретного приложения через PostgreSQL-драйвер по TCP с другого узла, с TLS и заявленным cancellation/session поведением. Допустимы endpoint/database/credentials и совместимый набор SQL. Изменения кода приложения или специальный режим драйвера записываются как ограничения. Если неизменённый клиент требует BEGIN/`$n`, M3 такого профиля не принимается до engine расширения; нельзя подменить его тестом autocommit/native `?` и объявить прозрачную совместимость.

`server_version` в ParameterStatus — проверяемое обещание для ветвления клиента, не версия движка и не общее обещание SQL совместимости. Значение из исторического probe проверяется на каждом заявленном клиентском профиле.

Исходный `tests/test_pgwire_acceptance.py` сохраняется как материал аудита и миграции. Его 20 функций/56 случаев требуют распределения по новым этапам и изменения предположений о BEGIN, RETURNING и SQL whitelist. Collection и старый pass count не являются приёмкой текущего контракта. Отчёт `VALIDATION.md` указывает реально исполненные checks, версии и принятый milestone; driver-only probe доказательством Firebird integration не служит.
