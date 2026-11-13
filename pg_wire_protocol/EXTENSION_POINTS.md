# Точки будущей изоляции кода совместимости

Решение пользователя от 12 ноября 2026 года: текущую архитектуру сохранить,
а места зависимости от конкретного протокола, SQL-диалекта и СУБД учитывать
отдельно для будущего выделения кода. Основа реестра — `7799cd3a54` (APP-15).
Это реестр технического долга, не задание на рефакторинг или новый plugin API.
Общий статус — ARCH-01 в [BACKLOG](BACKLOG.md).

## Правила ведения

- При каждой новой доработке обновлять соответствующую строку или добавлять ID.
  Указывать символы/файлы, причину зависимости, владельца состояния и проверки,
  которые будущий перенос обязан сохранить. Ссылаться на символы, не только номера строк.
- Различать протокол, правила SQL-диалекта и переиспользуемую native возможность.
  Диалект выбирается для Prepare; порт не определяет семантику движка.
- Статус «учтено» означает, что код ещё не изолирован. Будущий перенос отмечать
  отдельной ревизией и проверками; запись не удалять.
- Сейчас не добавлять интерфейсы/колбэки/абстракции, универсальные flags и новый
  framework только ради будущего переноса. Объём необходимого изменения оценивать
  по всем затронутым слоям, а не по размеру wire-плагина.
- Перенос в отдельный внутренний файл не создаёт независимый динамический плагин.
  Новый публичный контракт языка/типов/undo требует отдельного решения.

## Действующие точки

Все строки ниже имеют статус **учтено, выделение отложено**.

| ID | Точка и текущие символы | Что специфично и кто владеет | Возможная будущая граница / обязательный инвариант |
|---|---|---|---|
| EXT-01 | [FirebirdInterface.idl](../src/include/firebird/FirebirdInterface.idl): IWireProtocol/IWireSession/IWireServerHost; [wire host](../src/remote/server) и [PgWire](../src/plugins/wire/pg) | Host владеет transport/listener/worker; плагин — wire session, framing, startup/auth, handles и codecs | Сохранять существующую plugin-границу; generic host не должен получать PG SQL правила. Native endpoint, stop/unload и локальный engine attach остаются независимыми. |
| EXT-02 | [Parser.cpp](../src/dsql/Parser.cpp), [Parser.h](../src/dsql/Parser.h), [parse.y](../src/dsql/parse.y), [dsql.cpp](../src/dsql/dsql.cpp): pgDialect, SQL_DIALECT_PG, transformString, PG grammar | Правила PG4: folding, `_`, строки, BEGIN, `$n`, casts, LIMIT, TEMP; parser/scratch владеют режимом конкретного Prepare | Возможный внутренний слой диалекта; сохранить native1/2/3, native-safe stored source, reprepare, позиции параметров и отсутствие SQL rewriting в плагине. |
| EXT-03 | [StmtNodes.cpp](../src/dsql/StmtNodes.cpp): SetPgSessionNode; [Attachment.h](../src/jrd/Attachment.h); [Execute.cpp](../src/plugins/wire/pg/Execute.cpp), [Extended.cpp](../src/plugins/wire/pg/Extended.cpp) | Фиксированные SET и standard_conforming_strings; bool принадлежит attachment, согласованная PG история настройки — QueryExecutor | Отделять session policy от native session operations. Сохранить prepare override/cache, I/T/E и savepoint recovery; три согласованных no-op SET не считать реальными настройками. |
| EXT-04 | [Attachment.cpp](../src/jrd/Attachment.cpp): pgRelationPath, qualifyPgRelationName, qualifyPgNewName, checkPgRelationProfile; [DsqlCompilerScratch.cpp](../src/dsql/DsqlCompilerScratch.cpp); [SysFunction.cpp](../src/jrd/SysFunction.cpp): evlPgRelationId | PG search_path, `$user`, implicit pg_catalog/pg_temp, private namespace; состояние attachment, lookup через native MET/SCL | Кандидат на общий внутренний PG resolver. Компиляция и visibility должны использовать одинаковые правила; фактические права/path после вычисления аргументов, точные имена, отсутствие SELECT/USAGE подмены. |
| EXT-05 | [DdlNodes.epp](../src/dsql/DdlNodes.epp): pgLttTarget, checkPgTempName, CreateRelationNode/defineLocalTempTable; [DsqlRequests.cpp](../src/dsql/DsqlRequests.cpp): DsqlDdlRequest::execute; [Nodes.h](../src/dsql/Nodes.h): isPgTemporaryDdl | PG TEMP defaults/ownership, table/index namespace, CASCADE profile, узкое разрешение DDL под user savepoint | Правила допустимости можно выделить, вызовы до эффектов остаются в engine. Проверять текущий target на Execute; не разрешать persistent/GTT/native DDL по прежнему Prepare. |
| EXT-06 | [Attachment.h](../src/jrd/Attachment.h): att_pg_temp_namespace/initializer; [LocalTemporaryTable.h](../src/jrd/LocalTemporaryTable.h): pgTemporary/pgRelationId; [Attachment.cpp](../src/jrd/Attachment.cpp): allocatePgTempId | PG ownership, public IDs, virtual namespace; attachment/LTT metadata, sequence в PGWIRE_COMPAT | Возможное выделение состояния PG temporary objects. Сохранять clone/undo, уникальность IDs, namespace после DROP последней таблицы, изоляцию подключений; не вводить параллельный registry в wire. |
| EXT-07 | [Savepoint.cpp](../src/jrd/Savepoint.cpp), [Savepoint.h](../src/jrd/Savepoint.h), [tra.cpp](../src/jrd/tra.cpp), [tra.h](../src/jrd/tra.h): m_pgTempNamespace, collectPgTempNamespaceUndo, tra_pg_temp_namespace_undo, finishPgTempNamespace | Undo первой PG namespace activation; savepoint/transaction владеют отменой, attachment — самим namespace | Будущая граница transactional compatibility state. Сохранить statement/user savepoint/full rollback, root release, NO AUTO UNDO, retaining/force и failed commit. Обычный NAT-06 LttUndo — native механизм, не PG policy. |
| EXT-08 | [DsqlStatementCache.cpp](../src/dsql/DsqlStatementCache.cpp): buildStatementKey; [metd.epp](../src/dsql/metd.epp): METD_drop_relation | PG разрешение имён зависит от роли/CURRENT_USER; TEMP shadowing требует cache invalidation | Внутренняя зависимость плана от среды диалекта. Нельзя убрать key/purge при переносе resolver; сохранить включённый cache, SET ROLE, DROP/recreate, RESET и prepared executions. |
| EXT-09 | [ExtEngineManager.cpp](../src/jrd/ExtEngineManager.cpp): PgTempRow/PgTempRows, validatePgTempRows, openPgTempRows, SystemEngine::makeProcedure | PG live catalog projection, установленный exact entrypoint; курсор владеет копией строк LTT metadata | Кандидат на отдельную внутреннюю factory/provider реализацию. Сохранить layout validation, snapshot at Open, обычный SQL installer/старый ODS, отсутствие dangling pointers и MON-cache зависимости. |
| EXT-10 | [sql](1c/sql), [install_compat.py](1c/install_compat.py): PGWIRE_COMPAT, public/lowercase wrappers, catalog installers | Установленная SQL-часть совместимости, ID registry и grants; объекты конкретной БД | Оставлять SQL/PSQL там, где достаточно native возможностей. Сохранить atomic install/upgrade, collision refusals, права и реальные metadata; не переносить catalog ответы в wire или hardcoded fixtures. |
| EXT-11 | [InputCodec.cpp](../src/plugins/wire/pg/InputCodec.cpp), [ResultCodec.cpp](../src/plugins/wire/pg/ResultCodec.cpp), [Extended.cpp](../src/plugins/wire/pg/Extended.cpp) | Отображение native descriptor → PG OID/format, bytea input/output, portal/transaction handles | Граница logical SQL type и wire codec. Для новых типов фиксировать propagation/storage/metadata; не угадывать тип по имени колонки/SQL/alias. Сохранить bounds, NULL, raw bytes, lifetime и native metadata. |

## APP-16: добавленные точки реализации

Следующие строки описывают production diff APP-16; итоговый статус приёмки ведётся
в VALIDATION. Внутренние helpers не создают нового публичного plugin API.

| ID | Символы | Состояние и контракт / проверка при переносе |
|---|---|---|
| EXT-12 | Parser::makePgType/markPgType/markPgSyntax, GEN_descriptor, DsqlCompilerScratch::putDtype, PAR_desc, BlrFromMessage, InternalMessageBuffer, remote PARSE_format | PG spelling → native descriptor: bytea BLOB/10; bounded mchar/mvarchar CHAR/VARCHAR с charset 70/71; bare mchar BLOB/11; bare mvarchar text BLOB/charset71; regnamespace INT64/subtype3/scale0. BLR 34 сохраняет regnamespace. Канонический stored source использует native типы, включая BIGINT SUB_TYPE 3, и SUBSTRING вместо PG SUBSTR. Проверять поле, CAST, функции, CASE/COALESCE/UNION, native reprepare; root guards postfix/$n/LIMIT остаются отдельным контрактом. |
| EXT-13 | common/Bytea.h, blb::move_from_string, CVT2_blob_compare, BTR_key_length/compress, IDX_create/IDX_garbage_collect | Binary-string semantic marker принадлежит engine descriptor, storage — обычному BLOB/MVCC. Общий decoder вызывается после SQL lexer или непосредственно из Bind. Полное сравнение content+length; B-tree кодирует bytes в непустые пары nibbles и terminator, сохраняет prefix/zero tails. Native фактический key budget, compound/descending, backout отвергнутого oversized key; обычные BLOB/0 и OCTETS сохраняют padding-семантику. |
| EXT-14 | sort.cpp: SKD_bytea/SKD_pg_string, Optimizer sort descriptors | Native sorter хранит BLOB IDs как ссылки, сравнивает значения через engine; не сортирует по ID. Сохраняются native run merge, duplicate elimination, secondary keys и descending. Проверять spill/multiple runs, DISTINCT/GROUP/MIN/MAX и разные BLOB с одинаковым содержимым. |
| EXT-15 | IntlManager/intl_builtin; PgString; MOV_pg_string; SubstringNode/ConcatenateNode/ComparativeBoolNode/MaxMinAggNode | UTF8 storage, native ICU CI/PAD collation; bounded lengths в Unicode codepoints. Bare mchar сохраняет value typmod в BLOB/11 (int32 LE + UTF8), поскольку SUBSTR зависит от исходной длины даже после bare cast/хранения. Wire получает UTF8 через native conversion; UTF16LE send/receive — только формат протокола. Проверять padding/truncation, mixed operands, LIKE, metadata typmod и UNIQUE. |
| EXT-16 | PgNamespace::movePgNamespace | Regnamespace input/output обращается к установленному живому pg_namespace в текущей transaction, без wire registry. uint32 range/binary и quoting, DROP/recreate, prepared execution и temp namespace. Public catalog BIGINT/VARCHAR не переименованы в wire oid/name. |
| EXT-17 | ods.h, INI_upgrade, DdlNodes::storeGlobalField | ODS 14.1 объявляет новые descriptor/index capabilities и charset 70/71. Старые объекты читаются новым runtime; gfix обновляет системные intl metadata; прежний runtime отвергает новую minor ODS. Это необходимая граница совместимости хранения, не изменение B-tree page format или OO vtable. |
| EXT-18 | InputCodec::encode/materialize, Extended Execute, StringCodec, ResultCodec | Bind владеет проверенными bytes; IBlob создаются только на Execute в application transaction. Все открытые handles отменяются при частичной write failure. Logical metadata сохраняет тип, physical message — BLOB ID; прежние frame/message/portal/DataRow budgets не увеличены. Stable custom OIDs 0x00fb0001/2, bytea17 и regnamespace4089. Проверять Close без Execute, repeated portals, rollback/EOF, text/binary/mixed и границы бюджетов. |
| EXT-19 | QueryExecutor::finishImplicit, DFW create_ltt_index phase 0, BTR_next_index/BTR_find_page | Implicit commit с доказанной native keytoobig/no_dup причиной возвращается к I только после успешного полного rollback. Explicit/uncertain boundaries остаются fatal. LTT build backout удаляет зарезервированные base/instance slots; DROP bytea прекращает maintenance, старый план требует reprepare. Проверять failed build/retry, oversized DML/savepoint, DROP/rollback и retained handles. |

## Переиспользуемые механизмы — не долг по PG изоляции

NAT-06 LTT metadata undo исправляет native rollback. Native storage/MVCC/B-tree,
SCL и допустимый non-throwing schema-USAGE decision остаются механизмами движка.
APP-16 также исправляет native index-name lookup при наличии LTT, DFW index build
backout и checkBlob собственной LTT без persistent security class. LTT ID lookup
остаётся attachment-local; persistent SCL и принятый NAT-06 undo не меняются.
Перенос PG policy не должен дублировать их или выносить внутренние Jrd pointers
через публичный wire/UDR ABI. Если новая native возможность нужна типам или
индексам, отдельно фиксировать её общий контракт и специфичное PG отображение.

APP-16 дополняет EXT-02/11 строками EXT-12…19. Эти места не являются публичным
API расширения типов. Учёт будущей изоляции не заменяет функциональную сдачу
[этапа типов](1c/TYPES_AND_EXPRESSIONS_TASK.md).

## Каталог текущей базы

EXT-20 также включает `pg_tablespace` (реально пустое пространство имён PG
tablespaces в Firebird) и [install_type_catalog.sql](1c/sql/install_type_catalog.sql).
Каталог типов отражает пять реальных alias из `Parser::makePgType` и wire OID
кодеков; `pg_type_is_visible` сохраняет их текущее независимое от search_path
разрешение. При выделении диалекта синхронно менять resolver, каталог и OID;
не подменять ими каталог всех native domains.

В том же SQL-слое находятся [pg_tables](1c/sql/install_tables_catalog.sql) и
[pg_attribute](1c/sql/install_attribute_catalog.sql). Первая проекция использует
существующие live relation/namespace providers, вторая — native persistent
relation/field IDs (+1), а не позиции колонок. При дальнейшем расширении
temporary columns нужен live provider со стабильными field IDs; MON$ snapshots
и FIELD_POSITION не обеспечивают этот контракт. Новые core/API точки в этом
срезе не добавляются.

| ID | Символы | Состояние и контракт / проверка при переносе |
|---|---|---|
| EXT-20 | [install_database_catalog.sql](1c/sql/install_database_catalog.sql): current_database, pg_database, PGWIRE_COMPAT.DATABASE_COLLATION; [configure_ru_collation.sql](1c/sql/configure_ru_collation.sql) | SQL-слой проецирует canonical DB_NAME и реальный default UTF8 collation конкретной БД. Native metadata остаётся владельцем состояния; wire не хранит имя/locale отдельно. Сохранить atomic install, запрет перезаписи чужих объектов, обычные grants, явную ошибку для неподдержанной collation и независимость PG_MCHAR/PG_MVARCHAR. ICU ru_RU и libc ru_RU не считаются эквивалентными; границы описаны в [DATABASE_CATALOG](1c/DATABASE_CATALOG.md). |
| EXT-22 | [parse.y](../src/dsql/parse.y): column_def serial/serial4 → native identity | Специфично только отображение PG-объявления столбца в существующий identity AST. Native LTT identity (LocalTemporaryTable::Identity, GenIdNode) — переиспользуемая возможность, без PG полей/BLR/API. Сохранить default/explicit NULL, nontransactional counter, attachment isolation и metadata undo. Полная PG sequence/CYCLE семантика остаётся вне [профиля](1c/SERIAL_IDENTITY.md). |

## Транзакционные advisory locks

| ID | Символы | Состояние и контракт / проверка при переносе |
|---|---|---|
| EXT-21 | [tra.cpp](../src/jrd/tra.cpp): acquireAdvisoryLock/rollbackAdvisoryLocks/mergeAdvisoryLocks/releaseAdvisoryLocks; [SysFunction.cpp](../src/jrd/SysFunction.cpp): RDB$PG_ADVISORY_LOCK; [Execute.cpp](../src/plugins/wire/pg/Execute.cpp): fail | Native lock manager и native savepoint numbers владеют блокировками и undo; PG wrappers выбирают shared/exclusive, wire сообщает переход текущего логического блока в E. Будущая граница должна сохранить межсоединительные конфликты, native multi-transaction isolation, downgrade/rollback/release savepoint, retaining/cleanup и protocol-only error. INTEGER0 вместо PG void — явное ограничение профиля, не новый persisted datatype или ABI. |

## Служебные операции PG temporary tables

| ID | Символы | Состояние и контракт / проверка при переносе |
|---|---|---|
| EXT-23 | [PgTempOperations](../src/jrd/PgTempOperations.cpp), AnalyzeNode, RDB$PG_FASTTRUNCATE; QualifiedMetaString lowercase policy | PG policy ограничена собственными LTT attachment; native resolver, IDX_statistics, page instances и undo остаются владельцами операций. Функции возвращают INTEGER0 вместо void. Будущая изоляция должна сохранить необратимую очистку относительно rollback, непрерывность serial, обновление именно текущего index root, native defaults и guarded cursor/second-TX rejection. Опции delPages.onlyIfUnused и discardTempFrameActions.allVersions включаются только явно; обычные native callers сохраняют прежнее поведение. |


## APP-17: fulleq syntax lowering

| ID | Точка | Контракт будущего отделения |
|---|---|---|
| EXT-24 | Parser PG_FULL_EQ, ComparativeBoolNode::dsqlPgFullEq, stored syntax marks | PG-only generic operator lowers to existing blr_equiv after a DSQL type guard. Native comparator/optimizer remain shared; no new BLR/ABI. Preserve NULL, accepted integer/temporal/bytea/mchar behavior, explicit rejection of incompatible text/decimal/NaN/time-zone cases, PG precedence and parenthesized native source reprepare. SQL wrappers and partial extension inventory stay outside core. |
