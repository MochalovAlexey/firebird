# Следующий этап: bytea-параметры через native VARCHAR OCTETS

Архитектурное решение от 11 ноября 2026 года. Статус: задание на реализацию.
Рабочий каталог: `/home/aleksey/dev/my_firebird/firebird`.
База — master Firebird 6, ветка pg_wire_protocol, проверенная ревизия `5478a589d8`.
[BACKLOG](../BACKLOG.md): **APP-08**, ограниченная часть APP-02; большие значения
остаются APP-09. Не переключать ветку и не сбрасывать изменения ради этой ревизии.

## Контекст и результат этапа

Реализуй приём bytea-параметров для native BLOB SUB_TYPE BINARY, собери и проверь
через настоящий libpq. Оставь изменения пользователю на review.
**Не выполнять git add, commit, amend, reset или checkout.** Пользователь
проверяет и коммитит сам; следующий этап автоматически не начинать.

Главный приоритет — минимальные изменения FirebirdSQL и максимальное
переиспользование существующих механизмов, без новых зависимостей. Задание
`/home/aleksey/dev/1c/CODEX_PGWIRE_1C_TASK.md` целиком не выполнять.
Прочитай status/HEAD, применимые инструкции, [README](README.md), [BACKLOG](../BACKLOG.md),
начало [VALIDATION](../VALIDATION.md), InputCodec и Bind/Execute в Extended.cpp.

APP-07 принят: binary BLOB результата передаётся как bytea OID 17, text hex или
raw binary. Уже работают typed последовательные `$n`, Bind, PG BEGIN, literal
LIMIT и ограниченные postfix casts. Остальные ограничения этих этапов сохраняются.
Ignored SET остаются без эффекта по решению пользователя.

Конкретный следующий сценарий — неизменённый запрос
`/home/aleksey/dev/1c/captures/20261109T105635Z/profile/selected/23-binary-config-write.sql`:

```sql
UPDATE public.files SET MODIFIED = $1, DATASIZE = $2, BINARYDATA = $3 WHERE FILENAME = $4 AND PARTNO = $5
```

В записи `$3` представлен binary payload длиной 40444 байта; исходный Bind
format code не записан. Для проверки использовать синтетический payload и
native PUBLIC.FILES, не переносить реальные данные конфигурации в Git.
Оригинальная schema/Bind 1С, все 243 формы и открытие базы этим не принимаются.

## Архитектура: преобразование выполняет Firebird

Не добавлять создание BLOB/отложенную materialization в плагин. Уже существующий
путь движка принимает physical SQL_VARYING с charset CS_BINARY (OCTETS) вместо
логического BLOB: DsqlDmlRequest::metadataToFormat → MOV_move → blb::move →
move_from_string (`src/dsql/DsqlRequests.cpp`, `src/jrd/mov.cpp`, `src/jrd/blb.cpp`).
Движок создаёт BLOB при исполнении в текущей транзакции и ведёт его lifetime.
Специальная ветка OCTETS сохраняет байты без transliteration.

Native OO probe на этой сборке уже подтвердил NULL/empty и точные данные до
65533 байт, включая NUL/invalid UTF8, после commit и нового attachment.
Артефакты: `/tmp/pgwire-input-native-probe-20261111`; результаты — в VALIDATION.
Это доказательство native механизма, а не уже реализованный input PgWire.

Production ожидается в InputCodec.cpp/h. Parse/Bind по-прежнему не создают
application transaction, BLOB или другие engine resources. Extended.cpp уже
создаёт отдельный InputCodec для каждого portal и вызывает encode до регистрации
portal. Execute использует его metadata и buffer после startImplicit().
Новые API, transaction state, handles, caches, config, SQL rewriting и зависимости
не требуются. Parser/core/ResultCodec и обычный OCTETS SQL type не расширять.

### Логический тип и физический message

- Принимать только выведенный движком SQL_BLOB subtype 0, scale 0, длина ISC_QUAD.
  Wire OID остаётся 17; Parse hint 0/отсутствующий или 17. Другие hints не
  переопределяют тип движка. `SELECT $1` без native типа остаётся SQL-04.
- Логические offsets/ranges сначала проверять по ISC_QUAD. Через существующий
  IMetadataBuilder задать physical SQL_VARYING, subtype/scale 0, charset CS_BINARY.
  Это транспортный descriptor, не изменение типа колонки/SQL-параметра в движке.
- Размер physical BLOB slot зависит от **декодированных байтов конкретного Bind**.
  Не резервировать 65533 на каждый параметр: в исходной таблице selected19
  `_accrg45` 68 bytea из 92 параметров; такой резерв превысил бы 4 MiB даже для
  коротких ID. Существующий 1 MiB native-message limit не повышать.
- Минимальный вариант: constructor строит BLOB slots с capacity 0 для metadata;
  encode на отдельном portal codec декодирует значения, получает builder,
  задаёт фактические длины BLOB, получает новый descriptor и пересчитывает
  offsets/null offsets/общую длину. Переиспользовать небольшой общий код layout
  checks между constructor и encode, без нового общего framework.
- Допустимо снять const у внутреннего encode: единственный production call —
  portal-specific InputCodec в Extended.cpp. Prepared codec не меняется;
  metadata()/messageLength() после encode описывают возвращённый buffer.
  Descriptor и buffer должны оставаться согласованными. Обычный scalar-only
  путь не требует пересборки metadata.
- Временные декодированные значения нужны только внутри encode и не остаются
  рядом с готовым portal.parameters. Не сохранять исходный Bind второй копией
  и не вводить pending-BLOB objects. Нулевые bytes в native varying допустимы.
- Физический slot = двухбайтовая длина + payload; NULL только через null indicator.
  Для NULL/empty capacity 0 допустима, но их indicators различаются. После builder
  заново проверить все ranges, включая prefix, все scalar offsets и null offsets.
  Не считать прежний capacity=0 верхним пределом значения при последующем encode.

### Форматы и ошибки

| Вход | Контракт |
|---|---|
| Binary format 1 | Исходные байты, включая NUL и любые invalid UTF8; без prefix/hex/перекодирования |
| Text format 0, hex | `\x` + hex; регистр hex-цифр любой, space/tab/LF/CR допустимы перед/между/после пар, но не внутри пары или prefix |
| Text format 0, escape | Обычные символы, `\\` → один backslash, `\ooo` → байт, где первая octal-цифра 0…3 и две следующие 0…7 |
| NULL | Native null indicator; не пустой BLOB |
| Пустое значение | Binary длина 0; text пустая строка или `\x`; ненулевой empty BLOB |

Bind text — уже значение параметра, не SQL literal. Не снимать SQL-кавычки,
не применять standard_conforming_strings и не делать второй SQL unescape.
Не trim вход: пробелы вне hex-профиля являются данными. Prefix распознаётся только
как `\x` в самом начале. Сохранить прежнюю проверку UTF8/NUL для **сырого text
parameter**, но её нельзя применять к декодированным bytea bytes или binary input.

Malformed escape → 22P02; malformed hex/нечётные цифры → 22023, как PG 18
byteain/hex_decode_safe. Invalid raw text encoding/NUL → прежний 22021.
Существующие ошибки формата/count и несоответствия Parse OID сохраняются.
Не использовать permissive PQunescapeBytea как эталон отказов сервера.

### Пределы

Максимум одного декодированного значения — **65533 байта**, существующий
MAX_VARYING: 16-битная длина native descriptor минус prefix. Это предел message
descriptor, а не SQL DDL VARCHAR(32765). 65534 и больше → явный 0A000 на Bind,
до Execute, без усечения/округления/перехода на другой способ записи.

Сохранить действующие frame/native message/session-cache limits (1 MiB / 1 MiB /
8 MiB). Считать фактическую общую длину и арифметику до allocation/narrowing,
учесть scalar neighbours и NULL slots. Проверять decoded length при накоплении;
не выделять максимальный native slot для каждого BLOB. После encode существующий
Portal::bytes/reserve должен учитывать фактический buffer/metadata, без двойной
копии payload. Отклонение по настоящему общему лимиту остаётся допустимым.

APP-09 сохраняет большие входные BLOB; способ для них сейчас не реализовывать.
SQL `bytea` type/literals/casts, сравнения/индексы, TEXT/custom BLOB subtypes,
large-object API и SET bytea_output не входят в этот этап. APP-02/SQL-06/APP-05
остаются открытыми.

## Порядок реализации и проверки

1. Подтвердить нынешний input metadata refusal на отдельном runtime. Проверить
   native bridge; при необходимости перенести небольшой OO probe в существующий
   helper, без нового framework. Рабочие серверы/БД/employee/pgwire_test/1С не трогать.
2. Расширить InputCodec описанным логическим типом, bytea decoding и physical
   layout конкретного Bind. Использовать существующий IMetadataBuilder.
3. Добавить целевые unit checks и libpq/raw/native приёмку в существующий harness.
   Обновить только отрицательные случаи, которые действительно стали поддержаны.
4. Собрать, выполнить проверки и обновить документы/APP-08. Никаких новых шагов
   автоматически и никаких git commits.

Обязательные проверки:

- OID 17 при Parse/Describe; hints 0/17, mismatch; неподдержанные subtype/scale/
  invalid descriptor. Binary и text hex/escape через настоящий PQexecParams и
  PQprepare/PQexecPrepared; mixed parameter/result formats. Native independent
  readback содержимого после commit, возврат данных через уже принятый APP-07.
- NULL/empty, все 256 байтов, quote/backslash/NUL/invalid UTF8 в decoded value;
  bytea text при standard_conforming_strings on/off даёт те же байты. Проверить
  hex whitespace/case (включая пробелы сразу после prefix и после последней пары),
  odd nibble/invalid digit, dangling/неполный/неверный escape,
  `\400`, raw text NUL/invalid UTF8, а также обычные printable/UTF8 text bytes.
  Для спорных bytea input случаев сравнить с отдельным Postgres Pro 18.6 через
  libpq, используя имеющиеся средства; не устанавливать зависимости.
- Длины 0/1/32765/32766/40444/65533 и отказ 65534 для обоих wire formats;
  общий native-message boundary и учёт actual payload в cache. 68 BLOB из 92
  mixed параметров с короткими различными значениями/NULL не должны упереться
  в резерв максимума на каждый BLOB. Это проверка формы, не original-schema replay.
- Исходный SQL selected23 дословно на синтетической PUBLIC.FILES: UPDATE 1/0,
  WHERE, все пять параметров, native readback. Payload 40444 синтетический;
  исходную конфигурацию и запись DETAIL не считать восстановленным Bind.
- Parse/Bind/Describe/Flush без Execute не создают application transaction и
  не меняют таблицу/sequence. Неверный bytea и неподдержанный output отказывают
  до исполнения, включая последующие discarded messages до Sync.
- INSERT/UPDATE RETURNING, typed SELECT/EXECUTE PROCEDURE с native `?`, cursor
  и singleton paths. Независимые portals с разными payload lengths, suspension/
  resume, close до Execute, повторное выполнение prepared после commit/rollback.
  Один portal не должен менять metadata/buffer другого или выполнять DML повторно.
  В unit повторить encode на одном codec: long → NULL/empty → short → long,
  проверяя новые offsets/lengths и согласованность metadata с каждым buffer.
- Implicit commit/rollback, explicit T/E/25P02, ROLLBACK TO savepoint, disconnect;
  native observer подтверждает изменения/откат. Engine error после преобразования
  параметра не выдаётся за отказ до исполнения; дополнительных транзакций нет.
- Scalar codecs, output BLOB, input TEXT/custom BLOB и `::bytea` negatives прежние.
  В InputCodecTest и pgwire_blob_checks заменить blanket input-BLOB refusals
  на положительные subtype-0 cases и адресные unsupported subtype negatives.

Сборка: `/tmp/firebird-pgwire-1c-build`, `make -j2`, затем
`make -C gen -j2 TARGET=Debug pgwire_tests`. Запустить wire unit executables,
целевой input harness, прежние `--blobs`, `pgwire_codec_smoke.py`, Extended и
`--parameters` (сверить актуальное имя режима через --help). Иные suites повторять
при изменении общего пути/обнаруженной регрессии, без known native crash repro.
После успешных обязательных проверок не запускать всё повторно без причины.

Записать в VALIDATION revision/diff/SHA, команды, PASS/FAIL/SKIP, реальные пределы
и exit серверов. Обновить README/COMPATIBILITY/PLAN/TEST_PLAN и BACKLOG. Проверить
прежний DT_NEEDED PgWire; production core/parser diff должен остаться пустым.
Psycopg 3 SKIP, VAL-02 и прочие открытые пункты не закрывать косвенно.

Источники формата: [PG 18 bytea](https://www.postgresql.org/docs/18/datatype-binary.html),
[byteain](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/backend/utils/adt/varlena.c),
[hex_decode_safe](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/backend/utils/adt/encode.c).
Это основания wire codec; исходный SQL по-прежнему разбирает Firebird.
