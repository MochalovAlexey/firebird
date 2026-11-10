# Следующий этап: фиксированный профиль клиентской диагностики

Архитектурное решение от 10 ноября 2026 года. Статус: задание на реализацию.
Рабочий каталог: `/home/aleksey/dev/my_firebird/firebird`.
База — master Firebird 6, ветка `pg_wire_protocol`.
Проверенная ревизия: `a31c890e1b` — исправлена сериализация PG-строк в сохранённом SQL.
Ревизия дана для ориентира; не переключай ветку и не сбрасывай изменения.

## Задание и контекст

Реализуй только две команды из записанного стартового пакета 1С:

```sql
SET escape_string_warning = off;
SET client_min_messages=error;
```

Собери сервер, выполни целевые проверки, обнови документацию и оставь изменения
пользователю на review. **Не выполняй git add, commit, amend, reset или checkout.**
Пользователь проверяет и коммитит самостоятельно. Этот диалог — исполнитель;
следующие архитектурные этапы обсуждаются отдельно.

**Приоритет: минимальные необходимые изменения FirebirdSQL, максимальное
переиспользование существующих механизмов, без новых зависимостей.**
Не выполняй целиком широкое задание `/home/aleksey/dev/1c/CODEX_PGWIRE_1C_TASK.md`.

Сначала прочитай актуальные HEAD/status, инструкции репозитория, [README](README.md),
начало [VALIDATION](../VALIDATION.md) и существующий `SetPgSessionNode`.
Ранее уже реализованы:

- клиентский PG dialect 4; native dialects 1/2/3 сохраняют своё поведение;
- SELECT без FROM и ограниченная функция `pg_catalog.pg_backend_pid()`;
- последовательные Simple Query scripts с остановкой после первой ошибки;
- фиксированные `lc_messages='en_US.UTF-8'` и `client_encoding=UTF8`;
- изменяемый `standard_conforming_strings`: текущее значение в attachment,
  история восстановления в PgWire, prepare flags и согласованный DSQL cache;
- корректное сохранение декодированных строк в CTAS/PSQL, включая typed literals
  и SQLSTATE. Этот код и исправление `StrMark::introducerAllowed` сохранить.

## Контракт этапа

Это **фиксированный ограниченный профиль**, а не полная поддержка двух GUC.
Успешный SET подтверждает уже действующее поведение; переключения настройки нет.

| Параметр | Единственное разрешённое значение | Наблюдаемое поведение |
| --- | --- | --- |
| `escape_string_warning` | `off`, без учёта регистра | PG backslash escapes при `standard_conforming_strings=off` не создают предупреждений |
| `client_min_messages` | `error`, без учёта регистра | Текущий PgWire передаёт ошибки, без канала NoticeResponse; ошибки не подавляются |

Разрешить существующие формы `=` и `TO`, значение как строку либо идентификатор.
Проверять значение в Execute через существующий `SetPgSessionNode::execute()`.
Другие синтаксически допустимые значения (`on`, `notice`, `warning`, `'bad'`, `''`)
должны давать **22023**, а не успешный ответ. Синтаксис вне существующего
`pg_setting_value` остаётся ошибкой parser; расширять его под все варианты PG SET
не нужно. Parse/Bind сами значение не применяют и не отклоняют его как unsupported.
CommandComplete — `SET`. Обычного пользователя достаточно; новых прав не вводить.

Основание: `Parser::lexPgString()` сейчас не создаёт escape warnings;
`PgWire::diagnose()` читает `IStatus::getErrors()`, а Protocol не имеет
NoticeResponse. Перед реализацией убедись, что эти условия сохранились.
Если появился новый источник клиентских предупреждений, сначала зафиксируй
расхождение: обещать подавление без проверки такого пути нельзя.

В PostgreSQL defaults другие: `escape_string_warning=on`,
`client_min_messages=NOTICE`. Кроме того, **INFO передаётся клиенту независимо
от client_min_messages**, в том числе при `error`. Наш этап не реализует источники
и доставку INFO/NOTICE/WARNING и не заявляет полного совпадения диагностики PG.
Настройки относятся к сообщениям клиенту; серверный журнал и native IStatus
warnings не перенастраиваются. Документировать это явно:
[escape_string_warning](https://www.postgresql.org/docs/18/runtime-config-compatible.html#GUC-ESCAPE-STRING-WARNING),
[client_min_messages](https://www.postgresql.org/docs/18/runtime-config-client.html#GUC-CLIENT-MIN-MESSAGES).

## Архитектура и минимальные изменения

1. В `src/common/ParserTokens.h` и `src/dsql/parse.y` добавить два имени настроек
   по образцу LC_MESSAGES/CLIENT_ENCODING. Имена — non-reserved. Расширить
   существующий PG-only keyword gate в `Parser.cpp`: в native dialects это
   обычные identifiers. Не заменять gate поздним YYERROR: он ранее давал ложный
   command_end_err2 и мешал AUTOTERM определять окончательную ошибку.
2. В `set_pg_session` добавить две альтернативы, используя неизменённые
   `pg_setting_assignment` и `pg_setting_value`. OFF уже допустим как значение;
   ERROR уже есть среди токенов и non_reserved_word. Новый parser не нужен.
3. Расширить внутренний `SetPgSessionNode::Type` двумя вариантами и добавить
   две проверки допустимого значения в `execute()`. Использовать существующие
   сравнение строк и построение ошибки 22023. Проверять всю строку, включая её
   длину, как для прежних параметров; не принимать значение по префиксу.
   Обновить комментарий о фиксированном профиле. Общий registry GUC не создавать.
4. В `src/plugins/wire/pg/SqlInfo.cpp::sessionCommand()` добавить только имена
   настроек в существующий список. Плагин определяет Session/SET; грамматику,
   значение и исполнение определяет движок. Исходный SQL передаётся без rewriting.
5. Переиспользовать существующие error path, I/T/E, prepared execution и
   SessionManagementNode. Защитный отказ PG SET внутри PSQL остаётся как есть.

Нового изменяемого состояния здесь нет: не добавлять attachment fields,
историю в плагин, savepoint snapshots, undo или сброс cache. Не расширять
IAttachment/getInfo, prepare flags, ABI, ParameterStatus и конфигурацию.
Не добавлять warning transport/filter/framework ради этого фиксированного профиля.

COMMIT/ROLLBACK/ALTER SESSION RESET не меняют эти два фиксированных значения.
Ошибка нового SET в явной транзакции должна по существующим правилам переводить
соединение в E и восстанавливать прежний standard_conforming_strings. Это откат
ранее выполненного изменяемого SET, а не новое состояние диагностики.

## Приёмка

Расширить существующие `pg_wire_protocol/tools/pgwire_1c_smoke.py --settings`
(`settings_client`, `settings_protocols`) и `SqlInfoTest.cpp`. Не создавать новый
harness, тестовый режим или общую инфраструктуру. Использовать настоящий libpq,
имеющиеся raw-wire helpers и отдельную тестовую базу/сервер.

Проверить поведение, а не только успешный parse:

1. Обе исходные команды отдельными Query и вместе; варианты TO/`=`, quoted/bare,
   регистр и повторное исполнение обычным пользователем. Правильный tag SET.
2. Неподдержанные значения дают 22023. В Simple Query первая ошибка останавливает
   последующие команды; незакоммиченные изменения текущего сегмента откатываются.
   Переиспользовать существующий сценарий проверки скриптов.
3. Named Extended Parse/Bind проходят для синтаксически корректного плохого
   значения, Execute даёт 22023. Поддержанные значения проходят повторные
   Bind/Execute одного statement; восстановление через Sync сохраняется.
4. При standard_conforming_strings=off обычная строка с `\n` возвращает перевод
   строки без NoticeResponse. Некорректный Unicode escape (`'\u12'`) сохраняет
   ошибку 22025; SELECT 1/0 — ErrorResponse/22012. Неизвестный `'\q'` допустим,
   поэтому не использовать его как отрицательный пример. Прочитать raw frames до ReadyForQuery и явно
   проверить отсутствие `N`: helper, который молча отбрасывает notices, этого
   не доказывает. В частности, `StatusClient.events` сейчас пропускает `N`.
   Достаточно минимально расширить существующий helper либо использовать его
   низкоуровневое чтение, не добавляя новый protocol client.
5. В явной транзакции: SET standard_conforming_strings=off, затем неверный новый
   SET — E и восстановление исходного строкового режима согласно прежней истории;
   ROLLBACK восстанавливает работоспособность. Успешные фиксированные SET не
   меняют строковый режим. Проверить COMMIT/ROLLBACK/reset без новой истории.
6. Native dialects 1/2/3 отвергают новые формы SET, корректно выходят из ошибки
   при AUTOTERM; имена настроек остаются допустимыми unquoted identifiers.
   Прежние native session controls и ограничение PSQL сохраняются.

Регрессии: прежние проверки English/UTF8 в --settings, целевой --strings,
classifier, SessionTest и isql tests. Parser baseline: 154 shift/reduce и
11 reduce/reduce конфликтов; новых конфликтов быть не должно.
Не повторять широкие suites и известные crash repro без связи с новым изменением.

Использовать штатную сборку; текущий отдельный build-dir, если ещё существует:

```sh
cd /tmp/firebird-pgwire-1c-build
make -j2
make -C gen -j2 TARGET=Debug pgwire_tests
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_sql_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/pgwire_session_test
LD_LIBRARY_PATH=gen/Debug/firebird/lib gen/Debug/firebird/tests/isql_test --log_level=message

cd /home/aleksey/dev/my_firebird/firebird
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --settings \
  --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-fixed-diagnostics-01 \
  --libpq /lib/x86_64-linux-gnu/libpq.so.5
python3 -B pg_wire_protocol/tools/pgwire_1c_smoke.py --strings \
  --firebird-dir /tmp/firebird-pgwire-1c-build/gen/Debug/firebird \
  --work-dir /tmp/firebird-pgwire-fixed-diagnostics-strings-01 \
  --libpq /lib/x86_64-linux-gnu/libpq.so.5
```

При повторении выбирать новые work-dir. Рабочий сервер, employee, pgwire_test
и базы 1С не менять. Сборку из другого checkout не принимать за проверку diff.
Сохранить фактические результаты, revision/diff, SHA модулей и штатный exit сервера
в VALIDATION. Если проверка недоступна, указать это, не записывать предполагаемый PASS.

## Граница результата

Обновить README, PLAN и ограничения совместимости: поддержаны только off/error,
два SET из исходного пакета — фрагменты трассы, не приёмка всего startup.
Исходник пакета:
`/home/aleksey/dev/1c/captures/20261109T105635Z/profile/selected/03-session-batch.sql`.
После этапа остаются `enable_mergejoin`, `cpu_operator_cost`, `lock_timeout`;
первый SET пакета по-прежнему отклоняется. Не заменять их приблизительными
SET OPTIMIZE или StatementTimeout и не принимать как пустые успешные команды.

Не реализовывать другие значения, SET LOCAL/SESSION/DEFAULT, SHOW, startup GUC,
BEGIN, $n, каталоги либо следующий этап 1С. Предварительный parse всего Simple
Query по-прежнему отложен; явный ранний COMMIT сохраняется при поздней ошибке.
Известные ограничения native engine из VALIDATION не исправлять попутно.
После реализации и целевых проверок остановиться и передать diff пользователю.
