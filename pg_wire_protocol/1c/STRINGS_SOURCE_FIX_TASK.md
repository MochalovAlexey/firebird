# Следующий этап: корректное сохранение строк в исходниках SQL

Архитектурное решение по результатам review от 10 ноября 2026 года.
Рабочий каталог: /home/aleksey/dev/my_firebird/firebird.
База — master Firebird 6, ветка pg_wire_protocol.
Проверенная ревизия: 9e5b7bd07a (feat(pgwire): support standard_conforming_strings).
Это ориентир, не команда переключать ветку или сбрасывать изменения.

## Задание и контекст

Исправь найденную регрессию сериализации литералов, собери сервер, выполни
целевые проверки и оставь изменения пользователю на review.
Этот диалог — исполнитель; архитектурные решения ведутся отдельно.

**Приоритет: минимальные необходимые изменения FirebirdSQL и максимальное
переиспользование существующих механизмов.** Не добавляй зависимости,
новые методы/metadata API, новый parser или общий framework сериализации.
Не выполняй git add, commit, amend, reset или переключение ветки.
Пользователь сам проверяет результат и коммитит.

Сначала прочитай актуальные git status/HEAD и инструкции репозитория,
[контекст 1С](README.md), начало [VALIDATION.md](../VALIDATION.md) и архитектуру ниже.
Предыдущее [задание](STANDARD_CONFORMING_STRINGS_TASK.md) — контекст уже выполненного
этапа; заново выполнять его целиком не нужно.
Широкое задание /home/aleksey/dev/1c/CODEX_PGWIRE_1C_TASK.md также не задаёт
объём этого исправления. Следующие SET/BEGIN/$n сейчас не реализовывать.

Уже реализованную архитектуру сохранить:

- PG dialect 4 выбирается только клиентским prepare; native dialects 1/2/3
  и диалект БД 3 сохраняют поведение.
- Движок хранит текущий standard_conforming_strings и декодирует строки.
  История отката настройки принадлежит PgWire; восстановление — служебный SET.
- Simple Query и prepared statements сохраняют свой режим разбора;
  эффективный режим согласован с DSQL cache.
- Плагин передаёт исходный SQL без rewriting. Внутренняя сериализация исходников
  движком допустима и уже используется для введённых через _charset литералов.
- getInfo, prepare flags, session history и обработка транзакций в этом
  исправлении расширения не требуют. Whole-query preparse остаётся отложенным.

## Подтверждённая проблема

При off Parser::lexPgString отмечает обычные строковые токены для сохранения
декодированных байтов. Parser::transformString сейчас всегда добавляет им
_UTF8 перед X'hex'. Для обычного строкового выражения это допустимо.
Но существующая грамматика также содержит потребителей голого STRING:
DATE STRING, TIME STRING, TIMESTAMP STRING и SQLSTATE STRING.
Introducer перед таким STRING не допускается.

В отдельном runtime через libpq проверены следующие результаты:

| Проверка | on | off |
| --- | --- | --- |
| SELECT DATE '2026-01-01' | 2026-01-01 | 2026-01-01 |
| CREATE TABLE AS SELECT того же DATE WITH DATA | Успех | 42601 |
| CREATE PROCEDURE с присваиванием DATE, затем исполнение | Успех | Успех |
| Повторный ALTER из сохранённого source этой процедуры | Успех | 42601 |

Для процедуры с WHEN SQLSTATE '22012' подтверждён такой же дефект:
первое исполнение работает, повторная подготовка сохранённого source — 42601.

Прямой repro: отправить две команды отдельными Query:

    SET standard_conforming_strings = off;

    CREATE TABLE DATE_CTAS_REPRO AS
    SELECT DATE '2026-01-01' AS D FROM RDB$DATABASE WITH DATA;

CTAS выполняет внутреннюю подготовку сохранённого querySource, поэтому ошибка
возникает сразу. В случае процедуры некорректный source виден в metadata:

    BEGIN D=DATE _UTF8 X'323032362D30312D3031'; SUSPEND; END

Вариант с обработчиком исключения содержит:

    WHEN SQLSTATE _UTF8 X'3232303132' DO V=42

Evidence: /tmp/firebird-pgwire-strings-literals-review-01/review.json.
Reproducer: /tmp/pgwire_strings_arch_review_v2.py, режим literals.
Временные файлы могут отсутствовать позднее; SQL и причина приведены здесь.
Сервер этого прогона завершился exit 0.

## Архитектура исправления

Исправить выбор формы сериализации в существующих Parser/StrMark и semantic
action грамматики. Native grammar расширять не требуется.

1. Добавить в приватный StrMark один признак контекста: допускает ли данный
   литерал introducer при сериализации. Значение по умолчанию — false.
   Имя поля выбрать по стилю существующего кода.
2. В существующей редукции sql_string: STRING отмечать соответствующий StrMark,
   если он существует. Это место уже знает грамматический контекст.
   Для hex strings mark может отсутствовать; это нормальный случай.
3. В transformString добавлять неявный _UTF8 только для PG-декодированного
   обычного литерала, который прошёл через sql_string и не имеет явного introducer.
4. Остальные PG-декодированные STRING сохранять как существующий bare X'hex',
   без добавленного _UTF8. Явные _charset сохранять прежним механизмом.

Почему это использует существующую семантику:

- Штатный hex lexer уже возвращает TOK_STRING с нужными декодированными байтами.
  Потребители DATE/TIME/TIMESTAMP/SQLSTATE используют getString(), не charset.
  Например, DATE X'323032362D30312D3031' уже является допустимой native формой.
- Для обычного sql_string нужен _UTF8 X'...': удаление introducer везде
  превратит часть выражений в BINARY и может изменить charset, тип и сравнения.
- Контекст определяется грамматической редукцией. Не угадывать его по предыдущему
  слову lexer, не добавлять особый scanner для DATE или SQLSTATE.

Пример требуемого разделения:

| Исходный PG SQL при off | Сериализованный native source |
| --- | --- |
| DATE '2026-\x30\x31-01' | DATE X'323032362D30312D3031' |
| WHEN SQLSTATE '\x32\x32\x30\x31\x32' | WHEN SQLSTATE X'3232303132' |
| Обычное строковое выражение '\n' | _UTF8 X'0A' |

Нужно проверить фактическое прохождение grammar actions до makeParseStr,
включая btyacc/repeated parsing. Убедиться, что mark не получает неверный
контекст от невыбранной ветки разбора. Добавление semantic action должно
сохранять значение $$ и существующие parser позиции.
Использовать текущие introduced/escaped marks, не вводить публичные виды команд.

Ожидаемые production changes ограничены Parser.h, Parser.cpp и semantic action
в parse.y. Это ориентир, а не запрет обоснованного более простого решения.
Не лечить проблему пропуском канонизации: реально декодированные escapes
тоже должны переживать сохранение и native prepare.

## Проверки

Расширить существующие pg_wire_protocol/tools/pgwire_strings_checks.py
и при необходимости pgwire_strings_api.cpp. Новый harness не нужен.
Положительные wire-сценарии — через настоящий libpq.

1. CTAS WITH DATA для DATE, TIME, TIMESTAMP при on/off с обычными литералами:
   проверить созданные строки и типы. Добавить off-случай с реально
   декодируемым escape внутри даты; сравнить с уже декодированным native литералом.
2. Создать процедуру с temporal literals и процедуру с WHEN SQLSTATE через PG4/off.
   Получить RDB$PROCEDURE_SOURCE, повторно выполнить ALTER из этого source
   через native prepare и проверить результаты, в том числе после reconnect.
   Обязательно проверять исполнение, а не только текст source.
3. Смешать в одном сохраняемом объекте обычную UTF8-строку, Unicode,
   экранированную кавычку/backslash и typed/SQLSTATE literal.
   Обычные текстовые выражения должны сохранить UTF8 и metadata, а не стать BINARY.
4. Сохранить явный _UTF8, Q-strings, X'hex', native dialect isolation и
   проверки ошибок/исходных offsets. Не снимать existing guards для других charset.
5. Повторить целевой --strings, существующий isql suite и относящиеся к parser/
   классификации unit tests. Новых parser conflicts не ожидается:
   baseline 154 shift/reduce и 11 reduce/reduce.

Сборка: /tmp/firebird-pgwire-1c-build, runtime: gen/Debug/firebird внутри него.
Сверь наличие и актуальность. Команды сборки — в VALIDATION.md.
Пример целевого прогона из корня репозитория:

    python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --strings \
      --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
      --work-dir /tmp/firebird-pgwire-strings-source-fix-01 \
      --libpq /lib/x86_64-linux-gnu/libpq.so.5

Выбирай новый work-dir. Рабочий сервер, employee/pgwire_test и базу 1С
не изменять; проверять только отдельные test runtime/database.
После успешных целевых и затронутых проверок не расширять прогон без причины.
Старые ошибочно сохранённые исходники автоматически не переписывать:
исправление относится к новой сериализации. Если для проверки нужно пересоздать
объект, использовать исходный SQL и только свою временную базу.

## Отдельный исходный дефект: не включать в исправление

Дополнительная review-проверка обнаружила падение на:

    SET TRANSACTION;
    SAVEPOINT outer_point;
    EXECUTE BLOCK AS BEGIN EXECUTE STATEMENT 'SAVEPOINT inner_point'; END;

Debug assertion в exe.cpp:1351 воспроизводится на текущей сборке и на точных
бинарниках предыдущего этапа из firebird-pgwire-1c-settings-04, без нового SET.
Baseline-свидетельство: /tmp/firebird-pgwire-eds-baseline-review-02/review.json
и runtime/firebird.log; exit -6. Текущий repro:
/tmp/firebird-pgwire-strings-arch-review-01/runtime/firebird.log.
Хеши старых Engine14/PgWire в baseline runtime совпали с предыдущей сборкой.

Это не регрессия нового decoder. Существующий положительный EDS-тест исполнял
вложенный SELECT, поэтому он не доказывал внутренние SAVEPOINT.
Не исправлять этот engine defect в данном этапе и не добавлять падающий repro
в обычный acceptance suite. Задокументировать отдельную границу поддержки.
Также не трогать ранее отложенные nested RESET, ON CONNECT shutdown,
READ CONSISTENCY + AUTO COMMIT и исключённые сочетания истории с AUTO COMMIT.

## Документация и результат

Обновить соответствующие разделы 1c/README.md, TEST_PLAN.md, VALIDATION.md
и статус ближайшего шага в PLAN.md. Старые успешные результаты сохранить;
они проверяли меньший набор, а найденная проблема описана отдельным review.

В конце сообщить: что изменено и почему, git diff --stat, команды и результаты,
подтверждение отсутствия новых API/зависимостей, оставшиеся ограничения.
Оставить diff пользователю без коммита.

После проверки и принятия этого исправления следующий кандидат для 1С —
ограниченный фиксированный профиль escape_string_warning=off и
client_min_messages=error с использованием существующего SetPgSessionNode.
Это отдельный последующий этап; в текущем исправлении его не реализовывать.
