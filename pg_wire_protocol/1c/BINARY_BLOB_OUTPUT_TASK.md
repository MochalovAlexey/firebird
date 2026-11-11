# Следующий этап: чтение binary BLOB как bytea

Архитектурное решение от 11 ноября 2026 года. Статус: задание на реализацию.
Рабочий каталог: `/home/aleksey/dev/my_firebird/firebird`.
База — master Firebird 6, ветка pg_wire_protocol; проверенная ревизия `ccd81f6473`.
Пункт [BACKLOG](../BACKLOG.md): **APP-07**, ограниченная часть APP-02.
Не переключать ветку и не сбрасывать изменения ради совпадения с этой ревизией.

## Задание и контекст

Реализуй только выдачу native binary BLOB результата через PgWire как PostgreSQL
bytea. Собери, проверь через настоящий libpq и оставь результат на review.
**Не выполнять git add, commit, amend, reset или checkout.** Пользователь
проверяет изменения и коммитит сам. Следующий этап автоматически не начинать.

**Приоритет: минимальные необходимые изменения FirebirdSQL, максимальное
переиспользование существующих механизмов, без новых зависимостей.**
Это этап плагина/codec поверх существующего IBlob API; изменения engine/parser,
публичных интерфейсов Firebird и SQL rewriting не требуются.
Широкое задание `/home/aleksey/dev/1c/CODEX_PGWIRE_1C_TASK.md` целиком не выполнять.
Прочитай HEAD/status, применимые инструкции, [README](README.md), начало
[VALIDATION](../VALIDATION.md), ResultCodec, Protocol::dataRow и оба executor paths.

Уже работают PG4 SELECT без FROM, Simple scripts, Extended/Bind, PG BEGIN,
строковый режим, последовательные типизированные `$n`, native postfix casts
и literal LIMIT. Ограничения этих срезов сохраняются. Три игнорируемых startup
SET остаются без эффекта по решению пользователя.

Следующее необходимое поведение видно в
`/home/aleksey/dev/1c/captures/20261109T105635Z/profile/selected/06-config-bind.sql`:

```sql
SELECT Creation,Modified,Attributes,DataSize,BinaryData
FROM public.params WHERE FileName = $1 ORDER BY PartNo
```

Поиск по FileName и Bind уже работают на синтетической схеме, но binary payload
нельзя получить через нынешний ResultCodec. Чтение содержимого BLOB даёт
самостоятельный полезный результат; каталоги и запись данных не включать.

## Ограниченный контракт

Поддержать только output `SQL_BLOB` с **subtype 0 (BINARY)** и корректным native
descriptor/размером ISC_QUAD. Не превращать BLOB SUB_TYPE TEXT или другие subtype
в bytea. Оставить их прежний отказ 0A000 на metadata до исполнения.

| Wire свойство | Значение |
|---|---|
| RowDescription OID | 17 (bytea) |
| typeSize / typmod | -1 / -1 |
| Text format 0 | ASCII `\x` и две lowercase hex-цифры на каждый байт |
| Binary format 1 | Исходные байты без prefix, terminator или дополнительной длины внутри поля |
| NULL | Прежний null indicator / wire length -1; BLOB не открывать |
| Пустой ненулевой BLOB | Text `\x`, binary длина 0; не NULL |

Нулевые байты и невалидные UTF8-последовательности внутри binary BLOB допустимы.
Не применять validateText/transliteration к его содержимому. Не передавать
ISC_QUAD/engine handle как значение и не считать нулевой ID признаком SQL NULL.
SQL NULL определяется только metadata null indicator.

Поддержка общая для Simple и Extended: SELECT cursor, singleton output,
RETURNING и EXECUTE BLOCK/PROCEDURE с совместимым native SQL. Prepared metadata
может жить через commit; открытый BLOB живёт только внутри декодирования одной
строки текущего исполнения. Ничего не открывать при Describe/Parse/Bind.

Входные BLOB/bytea, SQL type name/cast `bytea`, bytea literals, сравнения/индексы,
OCTETS, другие BLOB subtypes и large-object API сюда не входят. `bytea_output`
не добавлять как SET: text output имеет фиксированный hex-профиль.
APP-02, SQL-06 и исходная схема/открытие базы 1С этим не закрываются.

Почему input отдельно: сейчас InputCodec::encode вызывается при Bind, до
startImplicit на Execute. Создание BLOB в этот момент потребовало бы ранней
application transaction и нарушило бы текущий контракт. Отложенная materialization
input будет отдельным этапом; InputCodec в этом этапе не расширять.

## Архитектура и порядок реализации

1. На собственном runtime подтвердить нынешний metadata refusal binary BLOB.
   Подготовить native fixture с известными байтами, не использовать рабочую БД.
2. В ResultCodec добавить только допустимый BLOB descriptor и OID 17. Прежние
   checks offsets/null offsets/message length сохраняются; физический input
   decode — ISC_QUAD, wire output — содержимое BLOB.
3. Передавать в decode borrowed IAttachment и текущий ITransaction на время
   вызова. Не сохранять их в codec/metadata/prepared objects, не создавать
   отдельную транзакцию и не менять её lifetime. Проверить все вызовы decode
   в Execute.cpp/Extended.cpp: cursor и singleton paths в обоих протоколах.
   Non-BLOB unit cases не должны требовать живого attachment; non-NULL BLOB
   без необходимого контекста — явная ошибка, без разыменования NULL.
4. Использовать IAttachment::openBlob и IBlob::getSegment/close. Образцы:
   `examples/interfaces/04.print_table.cpp`, `07.blob.cpp`. RESULT_OK и
   RESULT_SEGMENT дают очередные байты, RESULT_NO_DATA — конец; проверить status,
   прочие результаты/ошибки не считать успешным EOF. Не добавлять разделители
   между сегментами, не обрывать чтение на нулевом байте/пустом сегменте.
5. Читать небольшими фиксированными порциями и соблюдать общий DataRow budget
   по правилам ниже. Не сохранять BLOB contents в portal/prepared cache.
6. Закрыть BLOB до передачи строки в Protocol::dataRow и до commit/закрытия
   cursor. Соблюсти consuming close/release: успешный close не сопровождается
   повторным release, а исключение/превышение размера не оставляет handle.
   Переиспользовать RefPtr<IBlob> с REF_NO_INCR; после успешного close и проверки
   status вызвать clear(), который обнуляет указатель без release. Присваивание
   nullptr здесь вызовет лишний release; при ошибке handle освобождает destructor.
   Ошибки open/read/close проходят существующую engine error mapping и I/T/E.
   Cleanup не подменяет первичную ошибку; не добавлять fallback в пустые байты.
7. Собрать, проверить, обновить документы и APP-07 в BACKLOG. Engine и parser
   оставляются прежними; новые runtime/test dependencies не устанавливать.

### Размер строки и память

Использовать **фактический limits.frameBytes** текущего Protocol (сейчас максимум
1 MiB, в тестах может быть меньше). Допустим небольшой getter/передача лимита
внутри плагина; это не новый публичный Firebird API. Не добавлять config setting
и не повышать предел. Не ориентироваться только на отдельный MAX_MESSAGE codec.

Размер DataRow, учитываемый frameBytes:
`4 (длина) + 2 (число полей) + 4 * число полей + сумма ненулевых wire values`.
Typed tag `D` в этот предел не входит. NULL занимает своё поле длины, без payload.
Text bytea занимает `2 + 2 * число байтов`, binary — число байтов.

Считать единый оставшийся бюджет с учётом **всех** колонок и форматов. Проверять
арифметику до addition/multiplication/narrowing и размер **до накопления** очередной
порции/hex output. Небольшой фиксированный scratch buffer допустим; чтение всего
большого BLOB, а затем проверка Protocol — недопустимо. Не делать reserve по
непроверенной общей длине BLOB. Одного лимита на каждый BLOB недостаточно:
несколько BLOB и соседние scalar fields должны укладываться в одну строку.

При точном заполнении бюджета проверить EOF следующим чтением. Следующая
непустая порция означает превышение, а не успешный усечённый результат.

Сохранить preflight в Protocol::dataRow. При превышении — прежний 0A000,
полный cleanup, без усечения и частично отправленного DataRow. Ранее отправленные
строки не отзываются; прежняя обработка ошибок определяет rollback/E. Предел
всего результата не добавляется, строки обрабатываются последовательно.

## Проверки

Использовать существующие 1c/libpq/raw/native harness и ResultCodec/Protocol
unit tests. Для настоящих multi-segment fixtures допустим небольшой native OO
helper по существующим образцам; не создавать новый framework. Все runtime,
security DB и fixtures — в новых /tmp каталогах; рабочий сервер, employee,
pgwire_test и исходную базу 1С не трогать.

- Native fixture содержит SQL NULL; IBlob API создаёт и независимо читает empty,
  все 256 значений байта, NUL/backslash/quote/invalid UTF8, stream и segmented BLOB,
  несколько сегментов и содержимое больше read buffer. Сегментация не должна
  менять итоговые байты.
- Настоящие PQexec/PQexecParams/PQprepare/Describe/execPrepared: OID 17,
  typeSize/typmod, text hex и raw binary, длины и значения. Для text проверить
  обратное преобразование настоящим PQunescapeBytea; его память освобождать.
  Raw проверить смешанные форматы в строке и NULL против пустого значения.
- Форма selected06 на синтетической PUBLIC.PARAMS с native binary BLOB,
  несколькими PartNo и разными payload. Запрос/WHERE `$1`/ORDER BY остаются
  неизменными; параметры синтетические. Не объявлять приёмку оригинальной
  схемы/данных 1С или записанного Bind по этому примеру.
- Повторные Execute, два независимых portals и suspension/resume; смена
  текущей транзакции после COMMIT/ROLLBACK не оставляет старого handle в codec.
  Simple/Extended cursor и singleton output, включая RETURNING/native PSQL.
- Точная граница frameBytes и превышение на один байт для text/binary;
  несколько BLOB + scalar/NULL columns, в том числе смешанные formats.
  Отдельно большой BLOB сверх бюджета: отказ при ограниченном накоплении,
  без частичного DataRow; следующий запрос/Sync работает. Уменьшенный Protocol
  limit в unit должен реально участвовать в decode, а не только в serializer.
- Cleanup на успехе/превышении/ошибке открытия; чтение не оставляет handles
  через fetch, close portal и завершение transaction. Для ошибок read/close
  использовать существующие тестовые возможности; если fault injection нет,
  явно сохранить этот пробел в VAL-04, не строить отдельную подсистему ради него.
- Ошибка результата в I откатывает implicit сегмент, в T даёт E/25P02 с прежним
  recovery; DML RETURNING с ошибкой размера проверить native observer после
  rollback. Ошибка после engine execution не равна отказу до side effects.
- TEXT/другие BLOB subtypes, входные BLOB и `::bytea` остаются отвергнутыми;
  scalar/text codecs и native чтение тех же объектов сохраняются.

Обновить только непосредственно изменившиеся отрицательные ожидания binary
BLOB в текущих suites. Например, ResultCodecTest и `SELECT 1::BLOB` в
pgwire_cast_checks сейчас ожидают отказ любого BLOB: после этапа subtype 0
поддерживается, а для прежнего metadata-negative нужен реально неподдержанный
subtype/type. Не отменять вместе с ним input/grammar/TEXT BLOB ограничения.
Исторический P3 VARCHAR FAIL остаётся отдельным VAL-02.

Сборка `/tmp/firebird-pgwire-1c-build`: `make -j2`, затем
`make -C gen -j2 TARGET=Debug pgwire_tests`. Запустить wire unit executables,
целевой BLOB harness, `pgwire_codec_smoke.py`, Simple/Extended и --casts.
Остальные suites повторять при затронутом общем пути или выявленной регрессии.
Известные native crash repro не запускать. Зависимости PgWire проверить прежним
способом readelf; parser/core production diff должен остаться пустым.

Обновить VALIDATION (revision/diff/SHA, команды, PASS/FAIL/SKIP, exit серверов),
README/COMPATIBILITY/PLAN/TEST_PLAN и APP-07 в BACKLOG. APP-02 остаётся открытым:
input, SQL bytea type/casts/storage/index semantics и остальные типы ещё впереди.

Основания: [PG bytea hex format](https://www.postgresql.org/docs/18/datatype-binary.html),
[DataRow и RowDescription](https://www.postgresql.org/docs/18/protocol-message-formats.html),
штатные Firebird IBlob examples и текущий Protocol::dataRow.
