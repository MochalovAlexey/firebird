# APP-16: рабочие типы и выражения 1С

Инструкция для отдельного диалога разработки, 12 ноября 2026 года.
Работать в `/home/aleksey/dev/my_firebird/firebird`, master Firebird 6,
ветка `pg_wire_protocol`; проверенная основа `7799cd3a54` (APP-15).
Не переключать ветку ради ревизии. Прочитать применимые инструкции, status/diff,
[PROGRESS](PROGRESS.md), [BACKLOG](../BACKLOG.md), начало [VALIDATION](../VALIDATION.md)
и [реестр будущей изоляции](../EXTENSION_POINTS.md).

**Выполнить реализацию, сборку и приёмку всего этапа ниже. Исследование и выбор
представления — внутренние части работы, не отдельный сдаваемый результат.**
Эта инструкция заменяет прежнее исследовательское задание APP-16; его запрет
production-изменений и остановка перед реализацией отменены пользователем.

Приоритет: минимальные необходимые изменения FirebirdSQL и зависимостей при
корректной семантике, максимальный reuse parser/DSQL, storage, BLOB, B-tree,
intl/collations, transactions и codecs. Текущая концепция сохраняется: SQL
разбирает/исполняет движок, PgWire обеспечивает протокол и handles/codecs,
каталоги/прикладные функции устанавливаются SQL/PSQL, где его достаточно.
Не добавлять внешний parser, новый runtime backend, generic plugin framework,
SQL rewriting или рефакторинг будущей изоляции.

Пользователь проверяет изменения и коммитит сам. Не выполнять git add, commit,
amend, reset или checkout. Не начинать следующие крупные этапы автоматически.

## 1. Рабочий результат и критерий завершения

После установки совместимости libpq-клиент выполняет через PgWire:

- CREATE постоянных и PG temporary tables с настоящими bytea и mchar/mvarchar;
  INSERT/UPDATE/DELETE/SELECT, literals, casts и typed Bind без SQL-подмены.
- Сравнения и обычные/UNIQUE-индексы binary IDs: bytes `01` и `0100` различаются;
  выборка с индексом и без него даёт одинаковый результат.
- Передачу и чтение bytea payload больше 65533 bytes в пределах существующих
  message budgets, без потери байтов и утечки BLOB/transaction handles.
- Нужные 1С строковые casts/сравнения/сортировку/индексы, SUBSTR и LIKE над
  mchar/mvarchar с проверенной семантикой установленного расширения Postgres Pro.
- BINROWVER и настоящий предикат версии в DELETE/UPDATE, с правильными bytes,
  числом изменённых строк и rollback.
- Преобразование имени в regnamespace с корректной type identity, выводом и
  wire-форматами, согласованное с namespace IDs APP-10/15.

**Обязательная прикладная приёмка:** неизменённые selected04,07,08,12,17,18,24
на явно синтетической схеме с необходимыми настоящими типами/данными. Для
selected24 подготовить pg_temp.tt9 с bytea и проверить исходный CREATE UNIQUE
INDEX, не заменяя колонку на VARCHAR/VARBINARY. selected06/23 должны сохранять
чтение/запись binary payload. Отдельно выполнить общий сценарий indexed DML,
version predicate, commit/rollback, reopen и двух подключений.

Выбранные примеры не исчерпывают этап типов. Внутри работы составить конечный
перечень реально используемых операций/агрегатов над этими типами по families.jsonl,
включая встречающиеся BOOL_OR/MIN/MAX. Переиспользовать имеющиеся, реализовать
недостающие и включить этот перечень в приёмку. Форму выражения из записи сохранять;
при независимом блокере полного запроса проверять её на честном fixture и не
объявлять весь запрос принятым. Функции, только объявленные в schema.sql и не
используемые в профиле, автоматически не добавляют объём. Прохождение семи
selected-запросов без обязательных операций не закрывает весь типовой этап.

Итог — production diff, установленный профиль, сборка и PASS этой приёмки.
Alias, standalone prototype, исследовательский отчёт или только native equivalent
не закрывают этап. Непройденную обязательную проверку не объявлять ограничением
завершённого этапа. При реальном препятствии сохранить evidence и указать,
что этап не завершён; сложность выбора типа сама по себе не является основанием
останавливать работу после исследования.

## 2. Контекст и конечные границы объёма

APP-15 принят: 268 libpq results / 8408 OO assertions, raw и installer/upgrade PASS.
PG4 names/TEMP/resolver/namespace/undo и NAT-06 переиспользовать. База/DPB остаётся
native dialect 3, PG4 — режим конкретного Prepare. Native1/2/3 и прежние BLOB,
OCTETS/CHAR/VARCHAR не должны незаметно получить другие правила сравнения.

Этап закрывает типовую часть APP-02, APP-09 и необходимые части SQL-06/SQL-05.
Расширить вывод типов/параметры из SQL-03/04 только там, где это необходимо для
принятых форм этого этапа; не реализовывать все PostgreSQL coercions/arrays.
Не считать весь APP-02 закрытым без проверки требуемых операций из записи.

За пределами этапа: serial/default lifecycle и selected13; ANALYZE/fasttrun и
selected14–16; advisory locks; полный pg_database/current_database/collation
catalog; ON COMMIT DROP; реальная загрузка информационной базы и запуск Гилёва.
Все каталоги не требуется одновременно переводить с native BIGINT/VARCHAR в
PG oid/name; regnamespace должен корректно работать с текущими реальными IDs.
Полный pg_type не добавлять только ради демонстрации одного type OID.

Исходные материалы:

- `/home/aleksey/dev/1c/PG_WIRE_FINDINGS.md`, `PG_WIRE_PROFILE.md`;
- `/home/aleksey/dev/1c/captures/20261109T105635Z/schema.sql`,
  `profile/selected` и `profile/families.jsonl`;
- Postgres Pro 18.6 `/opt/pgpro/1c-18`, установленное расширение
  `share/extension/mchar--2.3.sql` и его control;
- [bytea](https://www.postgresql.org/docs/18/datatype-binary.html),
  [binary functions](https://www.postgresql.org/docs/18/functions-binarystring.html),
  [mchar](https://postgrespro.ru/docs/postgrespro/18/mchar),
  [OID alias types](https://www.postgresql.org/docs/18/datatype-oid.html).

Только отдельные `/tmp` runtime/locks/security/databases/endpoints. Не менять
рабочие employee/pgwire_test/gilev_demo_ru или пользовательские сервисы.
Capture/payload/credentials не копировать в Git; fixtures обозначать синтетическими.

## 3. Архитектура реализации

### Исследование непосредственно перед соответствующим кодом

На private Postgres Pro через настоящий libpq зафиксировать спорную семантику,
версии, metadata и text/binary bytes. Переиспользовать существующие probes;
не повторять уже доказанные observations без нового основания. После выбора
минимального корректного представления **сразу продолжать реализацию**.
Не сдавать выбор типа отдельным этапом и не требовать подтверждения каждого
внутреннего subtype/helper/AST решения.

Для реализуемого типа связать SQL spelling/typmod → persisted representation →
runtime descriptor → casts/comparison/index keys → public metadata → wire OID/formats.
Проверять не только поле, но литерал/параметр/функцию, CASE/COALESCE/UNION и
поддержанные stored-source/native reprepare пути. Сохранять semantic identity
через существующие native механизмы; не угадывать её по имени колонки/alias/SQL.

Отсутствующий готовый native механизм требуется минимально расширить в рамках
этого этапа. Это относится к type identity, conversions, comparison, index keys
и передаче metadata. Новое внутреннее поле/признак не является автоматически
новым публичным API. В первую очередь использовать существующие descriptor,
BLR/metadata поля и IMessageMetadata, например getSubType, когда их контракт подходит.
Если расширение всё-таки необходимо, обосновать конкретный пробел и сделать самое
узкое совместимое изменение, включая сохранение/чтение метаданных и проверки.
Не заменять реализацию запиской «нужен новый контракт».

ODS/public ABI не менять ради удобства и не обещать их неизменность без проверки
выбранного представления. Существующие native объекты должны оставаться читаемыми;
новые capabilities не должны молча интерпретироваться прежним runtime иначе.
Не создавать универсальную систему пользовательских SQL-типов на будущее.
Необходимые точки изменения фиксировать в EXTENSION_POINTS по мере реализации.

### Bytea: единая семантика коротких IDs и больших payload

Предпочтительная опора — native BLOB storage и отличимый semantic marker для
binary string, с native B-tree и существующим IBlob обменом. Проверить подходящий
способ хранения marker, его propagation/конверсии и metadata; не назначать новый
subtype без проверки существующих значений и blob-filter/MOVE контракта.
Выбрать более простой native путь, если он действительно обеспечивает тот же
полный профиль, а не только текущие короткие fixtures.

Доказанные исходные расхождения: `/tmp/app16-native-types-9b_8gttz/{probe,probe2,probe3}.{sql,log}`.
VARBINARY и BLOB SUB_TYPE 0 считают `01` и `0100` равными; UNIQUE VARBINARY
отвергает второе значение. OCTETS NO PAD создать не удалось; PAD SPACE контроль
тоже не прошёл, а UTF8 FROM SYSTEM.UNICODE NO PAD прошёл. Не объявлять это
невозможностью любого collation решения, но простой alias не подходит.

Reuse points: CVT2_compare/CVT2_blob_compare, CSetId(TTypeId), INTL_string_to_key,
BTR_key_length/BTR_make_key, persistent/LTT index DDL в DdlNodes.epp, native
BLOB storage/IBlob, InputCodec/ResultCodec. Бинарная семантика должна учитывать
реальную длину и все bytes, включая конечные нули. Сравнение, сортировка,
DISTINCT/GROUP и ключи индекса не должны расходиться в поддержанном профиле.

Реализовать обычные и UNIQUE indexes, включая составные и descending, поверх
штатных B-tree/undo. Размер индексируемого **фактического** значения ограничивается
проверенным native key budget с явной ошибкой и atomic rollback; это не предел
всего bytea типа или неиндексируемого payload. Проверить build на заполненной
таблице, DML/rollback/rebuild, lookup/range и совпадение natural/index plans.
Нельзя только снять запрет BLOB index, молча отбросить хвост ключа или заменить
UNIQUE prefix/hash без точного разрешения коллизий.

Не выбирать storage/длину по `_IDRRef`, имени таблицы, текущим данным либо наличию
CREATE INDEX. Bounded binary плюс BLOB допустимы только если единый SQL-контракт
сохраняется для всех требуемых значений и исходного runtime CREATE TEMP bytea;
не вводить произвольный лимит 16/256/8192 bytes вместо реализации.

Грамматика PG4 должна поддержать bytea в нужном DDL и casts, включая исходные
hex/escape literals. Bytea input decoding следует за SQL string parsing;
Bind text не проходит SQL lexer. Согласовать оба escape layers при strings on/off,
NULL/empty, invalid hex/octal и binary format. Повторно использовать decoder там,
где это не связывает engine с wire plugin; не добавлять второй SQL parser.

APP-09 реализовать через native IBlob в текущей application transaction.
Bind сохраняет проверенные bytes/план native input layout; материализация крупных
BLOB выполняется при Execute. Parse/Bind без Execute не создаёт hidden transaction
или оставленные BLOB. Проверить повторные Execute/portals, смешанные параметры,
ошибку после частичной материализации, rollback/EOF и освобождение ресурсов.
Существующие frame/message/DataRow budgets не повышать для обхода задачи.
APP-07/08, обычные native binary BLOB и их text/binary wire профиль сохранить.

### Mchar/mvarchar через native intl и правильные type metadata

Переиспользовать native character storage и существующие intl/ICU/collations.
Реализовать нужное отличие, если готовой collation недостаточно; в PgWire не
добавлять отдельную ICU/реализацию сортировки. UTF-16 storage эталона не доказывает
его wire encoding: измерить libpq metadata и настоящие send/receive bytes.

Поддержать DDL mchar(n)/mvarchar(n), используемые bare/schema-qualified cast targets,
assignment и explicit cast с правильными length/typmod правилами. Сравнить регистр,
кириллицу, accents, trailing spaces, empty/NULL, supplementary Unicode, SUBSTR,
LIKE и конкатенацию в требуемых формах записи. Equality/order/UNIQUE должны
согласоваться и при mixed operands/Bind, а не только двух колонках одной collation.

Custom type OIDs не копировать из рабочей базы Postgres Pro: обеспечить стабильную
идентичность установленных типов нашего профиля, согласованную между SQL metadata,
ParameterDescription и RowDescription. Стандартные OIDs и динамические OIDs
расширения различать. Не переименовывать обычный VARCHAR в wire в зависимости
от имени поля и не строить полный каталог чужой СУБД ради нескольких типов.

### Regnamespace и BINROWVER

Реализовать общий type/cast regnamespace, переиспользуя namespace registry APP-10
и живые temp IDs APP-15. Проверить text/numeric input, вывод именем/OID и uint32
binary, quoted names, missing namespace, pg_temp, DROP/recreate, overflow и
Prepare/Execute binding по oracle. Current BIGINT catalog fields могут сохранить
metadata; обеспечить правильное сравнение с regnamespace в исходном selected04.
DOMAIN BIGINT без type/input/output semantics и AST только для одного IN запрещены.

BINROWVER сначала реализовать PSQL/native bit operations через SQL installer;
C++ builtin нужен только при доказанной недостаточности native SQL. Эталон —
точное определение из schema.sql. Функция возвращает bytes и не увеличивает версию.
Проверить 0, 1, 255/256, границы байтов, int32 max, NULL и отрицательные аргументы;
не заменять MOD/SET_BYTE поведение reinterpret signed bytes без сверки.
Исходный selected18 обязан корректно удалить строку при совпадении версии,
оставить её при несовпадении и поддержать rollback.

## 4. Установка и проверка законченным этапом

Добавить необходимые SQL-объекты совместимости в общий install_compat.py.
Проверить fresh install и отдельный atomic upgrade принятой APP-15 базы, collision/
повтор/rollback, grants и сохранение прежних IDs/данных. Не устанавливать объекты
при attach и не выдавать всем административные права ради функций/типов.
Постоянные typed columns/indexes должны переживать disconnect и restart.

Расширить существующие pgwire_1c_smoke/libpq/raw/native helpers. Один итоговый
прогон должен проверять согласованный runtime и installer; unit/probes отдельных
частей не заменяют его. Синтетические fixtures используют исходные SQL-формы
и типы, но не объявляются настоящей информационной базой 1С.

| Группа | Обязательная приёмка |
|---|---|
| Исходный SQL | selected04/07/08/12/17/18/24 без rewriting; selected06/23 regression; реальные rows, tags, SQLSTATE, affected rows |
| Binary values | NULL/empty, 00/01/0100/010000/ff, все 256 bytes, нулевые префиксы/суффиксы, разные BLOB с одинаковым содержимым, границы сегментов и 65533 |
| Индексы | Persistent и LTT, обычный/UNIQUE/composite/descending, natural vs index, range, build/update/delete/rollback/rebuild, фактический key overflow без truncate |
| Большие параметры | >65533 bytes в допустимом frame budget, text/binary и mixed Bind, несколько portals/repeated Execute, неисполненный Close, ошибки/cleanup |
| Строки/функции | Oracle comparisons/typmods/casts/SUBSTR/LIKE/ordering/unique, Unicode и пробелы; BINROWVER/version predicate |
| Операции профиля | Полный установленный перечень реально используемых операций/агрегатов типов из families, включая нужные BOOL_OR/MIN/MAX; результаты и NULL semantics по oracle |
| Type identity | Column/literal/parameter/function/CASE/COALESCE/UNION, required persisted source/reprepare, native metadata и PG OIDs/formats, namespace lifecycle |
| Транзакции/сессии | Implicit/explicit, T→E→ROLLBACK TO/ROLLBACK, concurrent independent TEMP, commit/reopen/restart, cache включён |
| Установка/native | Fresh/APP-15 upgrade/отказы/grants; native1/2/3 прежние BLOB/OCTETS/text/index semantics и параллельный native endpoint |

Собрать сервер, выполнить релевантные existing APP-15/names/strings/codec/
transaction regressions на итоговом коде; не запускать бесконечные полные повторы
без новых оснований. Известные NAT-01…07 не объявлять исправленными по новым
fixtures. Если дефект затронут изменением, локализовать и исправить необходимую
часть; независимый дефект не скрывать перестановкой SQL или выключением cache.

## 5. Сдача

Оставить для review implementation diff и отчёт: что начало работать, source/runtime
hashes, команды, expected/actual, oracle metadata/bytes, PASS/FAIL/SKIP, shutdown/
cleanup и точные оставшиеся границы. Обновить README/COMPATIBILITY/PLAN/BACKLOG/
PROGRESS/TEST_PLAN/VALIDATION и EXTENSION_POINTS. Решения по представлению записать
как объяснение реализованного кода; отдельная обязательная исследовательская сдача
не нужна. Никакого финального коммита без пользователя.

После успешной приёмки всего типового этапа останется пять крупных результатов:
каталоги открытия, блокировки/служебные операции, подготовка реальной базы,
открытие клиентом 1С, Гилёв. Пока есть только инструкция — остаётся шесть.
