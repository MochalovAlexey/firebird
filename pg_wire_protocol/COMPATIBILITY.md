# Матрица совместимости PostgreSQL wire plugin

Матрица задаёт планируемые границы этапов, а не реализованную поддержку. База — `master` Firebird 6, SQL dialect 3, UTF8, PostgreSQL wire 3.0. Архитектура plugins определена в [ARCHITECTURE.md](ARCHITECTURE.md), execution contract — в [CODEX_CONTEXT.md](CODEX_CONTEXT.md).

## Главная граница

Wire plugin разбирает сообщения протокола и передаёт SQL целиком в существующий engine parser. Он не содержит SQL lexer, классификатора по словам, whitelist SQL-команд, splitter или rewriter. Это правило распространяется на служебный SQL драйвера, transaction control, command tags, EMPTY и `$n`. Результаты анализа предоставляет parser/DSQL; bindings и tags строятся по его metadata.

Совместимый SQL выполняется движком с реальными эффектами, в пределах заявленных interfaces и codecs. Несовместимый синтаксис получает parser error. Расширения PostgreSQL grammar, функций и системных объектов отложены в отдельный engine этап; отсутствие такого расширения не компенсируется сетевым перехватом команды.

## Возможности по этапам

| Возможность | M0 | M1 | M2 | M3 |
|---|---|---|---|---|
| Wire host и PG plugin в процессе `firebird` | Загрузка и первый endpoint | Lifecycle/config tests | Сохранить | Сохранить |
| Native listener при пустом списке и при включённых plugins | Да | Да | Да | Да |
| Firebird credentials, local engine, реальные privileges | Да | Да | Да | Да |
| Simple Query с неизменённым Firebird SQL | SELECT и error recovery | CRUD, совместимый DDL/session SQL, RETURNING | Сохранить, общие codecs | Сохранить |
| Engine-derived operation/control metadata | Один getInfo item: SELECT отличается от RETURNING/SELECT_BLOCK | Полный descriptor P4/P5, без frontend lexer | Общий для обоих paths | Общий |
| EMPTY / whitespace/comment-only SQL | Поддержка ещё не заявляется | Чистый EOF: отдельный engine diagnostic с legacy-compatible status | Общий для обоих paths | Общий |
| Прямой C libpq | Connect/PQexec | PQexec, metadata, state/error checks | PQexecParams, PQprepare/PQexecPrepared, Describe, async send/get | Прикладные сценарии |
| psycopg2 2.9.11 | Autocommit smoke без connection context | Autocommit и явный native transaction SQL, без автоматического BEGIN | Регрессия допустимого профиля | Если соответствует выбранной трассе |
| Parse/Bind/Describe/Execute/Close/Flush/Sync | Явный отказ | Явный отказ | Да | Да |
| Named/unnamed statements и portals | Нет | Нет | Да, с лимитами и проверенным lifetime | Да |
| Bind с native `?` либо zero parameters | Нет | Нет | Да, по engine metadata | Да |
| SQL `$n`, standalone BEGIN, PostgreSQL casts `::` | Parser error | Parser error | Parser error | Parser error без отдельного engine расширения |
| I/T/E, native SET TRANSACTION/COMMIT/ROLLBACK | Implicit Query transaction | Да, parser-derived controls | Да, включая implicit cycle до Sync | Да |
| Native SAVEPOINT/ROLLBACK TO/RELEASE и RETAIN | Не критерий smoke | Реальный effect и state policy | Тот же контракт | Тот же контракт |
| SMALLINT/INTEGER/BIGINT, BOOLEAN, CHAR/VARCHAR, NUMERIC до INT64, NULL | INTEGER | Text results | Text/binary input/output | Да |
| FLOAT/DOUBLE, DATE/TIME/TIMESTAMP без TZ, INT128/NUMERIC до 38 цифр | Не критерий smoke | Явное ограничение codec | Text/binary input/output | Да |
| DML RETURNING | Не критерий smoke | Да для поддержанных типов, правильная исходная операция | Общие расширенные codecs | Да |
| Native session commands/transaction characteristics | Не критерий smoke | Разбирает и выполняет движок, metadata отражает эффект | Тот же контракт | Нужные приложению формы проверены |
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

Команда не отклоняется только из-за отсутствия её первого слова в списке. Совместимый DDL включает проверяемые CREATE/ALTER/DROP, index и privilege операции. Session statements выполняются движком реально. Корректный command tag требует metadata исходной операции: coarse `getType` не различает MERGE, UPDATE OR INSERT, DML RETURNING, session management и некоторые controls.

M0 использует минимальный дополнительный engine kind для SELECT-профиля: существующих `getType` и cursor flag недостаточно, чтобы исключить DML RETURNING/SELECT_BLOCK. Не реализованные в M0 категории получают metadata-based 0A000 до исполнения. Полные operation/control metadata и выполнение совместимых форм добавляются в P4/P5; весь descriptor не блокирует первый SELECT.

M1 state policy сохраняет native transaction при ошибке explicit block и ставит E. Обычный prepare выполняется на этом handle; metadata определяет разрешённый recovery, остальные успешно подготовленные команды блокируются с 25P02 без execution. Lexer/parser error остаётся 42601. Только известные обычные semantic prepare errors переводятся в 25P02; fatal/shutdown, cancel/timeout, invalid handle, resource/I/O и неизвестные ошибки не маскируются. Отдельный pre-prepare analysis API не требуется. Полный COMMIT из E откатывает текущую работу; ROLLBACK завершает её; успешный ROLLBACK TO восстанавливает T и сохраняет изменения до savepoint. Retaining actions учитывают сохранение handle: ROLLBACK RETAIN может очистить failed block, COMMIT RETAIN в E не может сохранить его изменения. Полная таблица и edge cases заданы в CODEX_CONTEXT.md. Факт I/T/E не обещает совпадения isolation, locks или DDL semantics с PostgreSQL.

PgWire session управляет максимум одной текущей application transaction на своём attachment. Parse/Describe/statements/portals используют её либо prepare с NULL, без отдельных скрытых transactions. Внутренние engine system/security/autonomous transactions исключены из этого счёта. Это ограничение PgWire, не ограничение native protocol или общего host для других plugins. RESET проверяется после нескольких Parse/Describe/portals, включая смену underlying transaction при неизменном interface pointer.

Явные native transaction characteristics сохраняют семантику Firebird. В частности, `SET TRANSACTION AUTO COMMIT` может зафиксировать изменения до конца логического блока: последующий rollback не отменяет прежние commits. AUTO COMMIT / NO AUTO UNDO отражаются в metadata и имеют отдельные проверки; I/T/E не подменяет их поведение обещанием PostgreSQL atomicity.

EMPTY определяется при чистом EOF без SQL tokens существующим engine lexer/parser. Незаконченный SELECT, строка и block comment остаются ошибками. Дополнительный diagnostic marker или opt-in prepare flag сохраняет legacy `isc_command_end_err2` и `isql -autoterm`; общий код incomplete command не означает EMPTY. CHAR сохраняет logical OID и характерную добивку, а transport conversion не выдаёт лишнюю UTF8 byte padding. Ошибки сопоставляются по полному status vector. Эти детали являются условиями корректности M1, а не поводом для SQL recognition в plugin.

## Отдельные расширения и ограничения

- PostgreSQL grammar/functions/catalog compatibility — будущий engine этап. Если он станет обязательным для целевого приложения, M3 этого приложения зависит от него явно.
- Protocol 3.2, direct TLS negotiation, GSS encryption, PostgreSQL SCRAM/MD5 и channel binding — отдельные возможности. M3 начинает с password exchange внутри TLS и существующих Firebird credentials.
- Simple Query batches, COPY, libpq pipeline mode, SQL named cursors, large-object API, LISTEN/NOTIFY и replication — отдельные wire/session contracts.
- BLOB/bytea, ARRAY, OCTETS, DECFLOAT, типы с часовыми поясами и PostgreSQL custom types — отдельные codecs. Firebird BLOB handle не выдаётся за bytea.
- Remote security database и базы, требующие ключ шифрования от native клиента, не входят в начальный authentication profile.
- Начальная платформа — Linux/Super. SuperClassic проверяется на общем process lifecycle и не отклоняется только по названию режима. Windows и Classic получают отдельную интеграционную приёмку; `.dll` входит в архитектуру plugin ABI, но не доказывает готовность Windows build.

Неподдержанный тип или интерфейсный режим возвращает явную диагностическую ошибку до side effects, когда metadata позволяет это определить. Ограничения не скрываются фиктивными SET, успешными пустыми ответами или coercion всех результатов в text.

## Прикладной профиль и доказательства

Приложение и трасса выбираются до завершения M1. В `IMPLEMENTATION_NOTES.md` фиксируются версии приложения/драйвера/libpq, ОС, connection settings без секретов, wire operations, автоматически генерируемый SQL, типы, isolation/autocommit, reset/reuse и cancellation. Трасса определяет зависимости от будущих grammar/catalog изменений заранее.

Критерий M3 — воспроизводимый сценарий конкретного приложения через PostgreSQL-драйвер по TCP с другого узла, с TLS и заявленным cancellation/session поведением. Допустимы endpoint/database/credentials и совместимый набор SQL. Изменения кода приложения или специальный режим драйвера записываются как ограничения. Если неизменённый клиент требует BEGIN/`$n`, M3 такого профиля не принимается до engine расширения; нельзя подменить его тестом autocommit/native `?` и объявить прозрачную совместимость.

`server_version` в ParameterStatus — проверяемое обещание для ветвления клиента, не версия движка и не общее обещание SQL совместимости. Значение из исторического probe проверяется на каждом заявленном клиентском профиле.

Исходный `tests/test_pgwire_acceptance.py` сохраняется как материал аудита и миграции. Его 20 функций/56 случаев требуют распределения по новым этапам и изменения предположений о BEGIN, RETURNING и SQL whitelist. Collection и старый pass count не являются приёмкой текущего контракта. Отчёт `VALIDATION.md` указывает реально исполненные checks, версии и принятый milestone; driver-only probe доказательством Firebird integration не служит.
