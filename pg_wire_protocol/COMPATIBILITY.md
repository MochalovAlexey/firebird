# Матрица совместимости PostgreSQL frontend

Матрица задаёт планируемые границы этапов. Ни одна строка пока не означает реализованную поддержку. Базовый wire — PostgreSQL 3.0; SQL исполняется Firebird 6, dialect 3, UTF8.

## Возможности по этапам

| Возможность | M0 | M1 / v0 | M2 | M3 |
|---|---|---|---|---|
| TCP listener внутри `firebird`, native listener параллельно | Да | Да | Да | Да |
| Firebird credentials, local engine, реальные privileges | Да | Да | Да | Да |
| Один Simple Query statement | SELECT и error recovery | SELECT, CRUD, CREATE/DROP TABLE | Сохранить и расширить типы | Сохранить |
| Прямой C libpq | Connect/PQexec smoke | PQexec, metadata, transaction/error tests | PQexecParams, PQprepare/PQexecPrepared, Describe, async send/get | Сценарии приложения |
| psycopg2 2.9.11, обычный cursor | Autocommit smoke | Обычный transactional mode и autocommit | Регрессия поддержанных операций | Прикладной профиль, если выбран |
| Psycopg 3, обычный Cursor | Нет | Нет | Дополнительная проверка Extended Query | Прикладной профиль, если выбран |
| Parse/Bind/Describe/Execute/Close/Flush/Sync | Отказ | Отказ | Да | Да |
| Named/unnamed statements и portals | Нет | Нет | Да, с лимитами и корректным lifetime | Да |
| `$n` и отдельные значения параметров | Нет | Нет | Да, token-aware mapping и native binding | Да |
| I/T/E и BEGIN/COMMIT/ROLLBACK | Необязателен explicit block | Да | Да, включая implicit cycle до Sync | Да |
| SMALLINT/INTEGER/BIGINT, BOOLEAN, CHAR/VARCHAR, NUMERIC до INT64, NULL | INTEGER | Text results | Text/binary input и output | Да |
| FLOAT/DOUBLE, DATE/TIME/TIMESTAMP без TZ, INT128/NUMERIC до 38 цифр | Нет | Отказ по metadata | Text/binary input и output | Да |
| DML RETURNING | Нет | Отказ до исполнения | Да, с корректным command tag и affected rows | Да |
| Ограниченные служебные команды сессии | Нет | Только transaction control | Контракт проектируется | Реализованный набор по трассе приложения |
| SAVEPOINT / ROLLBACK TO / RELEASE | Нет | Отказ | Проектирование модели ошибок | Реализация, если требует приложение; иначе явное ограничение |
| Read-only / isolation settings | Один фиксированный TPB | Один фиксированный TPB | Проектирование mapping | Поддержанные значения применяются реально |
| Подключение с другого сетевого узла | Нет | Нет | Loopback для приёмки | Да |
| PostgreSQL SSLRequest и TLS | Ответ N | Ответ N | Ответ N | Upgrade в том же listener; проверка сертификата клиентом |
| BackendKeyData / CancelRequest | Нет | Нет | Подготовлен lifecycle cancellation | Да |

Поддержка всех scalar codecs M2 применяется к обоим query paths. Добавление Extended Query не должно оставлять Simple Query с искусственно более узкими выходными типами.

Обычный Psycopg 3 Cursor проверяется без принудительного перевода в ClientCursor. Конкретные версии Psycopg 3 и runtime libpq фиксируются до запуска M2. В историческом probe есть только psycopg2 2.9.11 / libpq 17.6; для других комбинаций результаты отсутствуют.

## Что остаётся вне базового профиля

PostgreSQL grammar и catalog emulation исключены по постановке задачи. Остальные ограничения должны оставаться видимыми в отчёте:

- Protocol 3.2, direct TLS negotiation, GSS encryption, PostgreSQL SCRAM/MD5, channel binding — отдельные расширения. Для M3 выбран password exchange внутри TLS с проверкой существующих Firebird credentials.
- Simple Query batches, COPY, libpq pipeline mode, SQL named cursors, large-object API, LISTEN/NOTIFY и replication — отдельные протокольные/сессионные возможности. Они не считаются реализованными по факту поддержки Extended Query.
- BLOB/bytea, ARRAY, OCTETS, DECFLOAT, типы с часовыми поясами, PostgreSQL custom types — отдельные codec contracts. В частности, Firebird BLOB handle нельзя выдать за PostgreSQL bytea.
- Windows, Classic и SuperClassic — отдельные платформенные этапы.

Если целевое приложение использует одну из этих возможностей, M3 для него не принимается до реализации возможности либо явного согласования более узкого прикладного профиля. Отсутствие SQL parser PostgreSQL не объясняет отказ в обычном wire Bind или неправильную границу транзакции.

## Параметры и синтаксис

В Simple Query сервер получает готовый SQL-текст; `%s` — синтаксис клиентской библиотеки и на сервере не обрабатывается. Адаптер psycopg2 может добавить `::date` или `::bytea`; это уже неподдержанный PostgreSQL SQL.

В Extended Query `$1`, `$2` являются частью контракта параметризованного выполнения. Frontend выделяет их как токены в совместимом Firebird SQL и сопоставляет с native input slots. Повторное использование одного `$n`, перестановка индексов, явные/выведенные OID и NULL требуют отдельной проверки. Значения передаются через metadata/message buffers; подстановка строковых литералов в SQL не используется.

Наличие DATE codec M2 не включает автоматически PostgreSQL cast `::date`. Тесты обязаны различать ошибки синтаксиса и отсутствие codec.

## Совместимость приложения

До M3 в `IMPLEMENTATION_NOTES.md` фиксируются приложение, версии драйвера/libpq, ОС, параметры соединения без секретов, wire operations, типы/форматы, служебные команды, isolation/autocommit и cancellation. Приложение пока не выбрано; это не мешает реализации M0–M2.

Критерий M3 — воспроизводимый сценарий приложения через его обычный PostgreSQL-драйвер по TCP к Firebird. Допустимы настройка endpoint/database/credentials и тестовый набор совместимого SQL. Любые необходимые изменения кода приложения или специальный режим драйвера фиксируются как ограничения; их нельзя скрыть заявлением о прозрачной совместимости.

`server_version` в ParameterStatus — проверяемое обещание поведения клиенту. Значение `16.0 (Firebird pgwire prototype)` используется как исходный compatibility token M1, унаследованный из probe. Для M2/M3 проверить ветвление целевых клиентов по версии и документировать выбранное значение. Оно не означает реализацию PostgreSQL 16 SQL или всех его функций.
