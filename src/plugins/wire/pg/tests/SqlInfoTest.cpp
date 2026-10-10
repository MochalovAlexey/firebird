/*
 *  The contents of this file are subject to the Initial Developer's Public
 *  License Version 1.0 (the "License"); you may not use this file except in
 *  compliance with the License. You may obtain a copy of the License at
 *  http://www.ibphoenix.com/main.nfs?a=ibphoenix&page=ibp_idpl.
 *
 *  Software distributed under the License is distributed AS IS, WITHOUT
 *  WARRANTY OF ANY KIND, either express or implied.
 *
 *  Copyright (c) 2026 Firebird Project and all contributors signed below.
 *  All Rights Reserved.
 */

#include "../SqlInfo.h"

#include <cstdio>
#include <stdexcept>
#include <string>

using namespace Firebird::PgWire;

namespace
{

unsigned checks = 0;

void expect(std::string_view sql, std::string_view tag, CountKind count = CountKind::None)
{
	const auto original = std::string(sql);
	const auto info = inspectSql(sql);
	if (!info.supported || info.empty || info.tag != tag || info.count != count || sql != original)
		throw std::runtime_error("Wrong command classification: " + original);
	++checks;
}

void unknown(std::string_view sql, bool empty = false)
{
	const auto info = inspectSql(sql);
	if (info.supported || info.empty != empty || !info.tag.empty() || info.count != CountKind::None)
		throw std::runtime_error("Unexpected command classification: " + std::string(sql));
	++checks;
}

void testDml()
{
	expect("SELECT 1 FROM RDB$DATABASE", "SELECT", CountKind::Rows);
	expect("/* START TRANSACTION */ sElEcT 'DELETE' FROM RDB$DATABASE", "SELECT", CountKind::Rows);
	expect("SELECT q'{/* DELETE */;}' FROM RDB$DATABASE", "SELECT", CountKind::Rows);
	expect("WITH t AS (SELECT 1 AS n FROM RDB$DATABASE) SELECT n FROM t", "SELECT", CountKind::Rows);
	expect("WITH RECURSIVE t AS (SELECT 1 n FROM RDB$DATABASE) SELECT n FROM t", "SELECT", CountKind::Rows);
	expect("(SELECT 1 FROM RDB$DATABASE)", "SELECT", CountKind::Rows);
	expect("/* preceding */ ((SELECT 1 FROM RDB$DATABASE))", "SELECT", CountKind::Rows);
	expect("INSERT INTO t (n) VALUES (1) RETURNING n", "INSERT 0", CountKind::Affected);
	expect("INSERT INTO t (n) WITH x AS (SELECT 1 n FROM RDB$DATABASE) SELECT n FROM x", "INSERT 0", CountKind::Affected);
	expect("UPDATE t SET n = 2 RETURNING n", "UPDATE", CountKind::Affected);
	expect("UPDATE \"OR\" SET n = 2", "UPDATE", CountKind::Affected);
	expect("UPDATE \"OR\"\"INSERT\" SET n = 2", "UPDATE", CountKind::Affected);
	expect("UPDATE ORBIT SET n = 2", "UPDATE", CountKind::Affected);
	expect("UPDATE OR$X SET n = 2", "UPDATE", CountKind::Affected);
	expect("UPDATE/*x*/OR--x\rINSERT INTO t(n) VALUES(1)", "UPDATE OR INSERT", CountKind::Affected);
	expect("DELETE FROM t WHERE n = 1 RETURNING n", "DELETE", CountKind::Affected);
	expect("MERGE INTO t USING s ON t.n = s.n WHEN MATCHED THEN DELETE", "MERGE", CountKind::Affected);
	expect("EXECUTE PROCEDURE p(1)", "EXECUTE PROCEDURE");
	expect("CALL p(1)", "EXECUTE PROCEDURE");
	expect("EXECUTE BLOCK RETURNS(n INTEGER) AS BEGIN n = 1; SUSPEND; END", "EXECUTE BLOCK");
	unknown("EXECUTE STATEMENT 'SELECT 1 FROM RDB$DATABASE'");
	unknown("USING AUTONOMOUS TRANSACTION DO INSERT INTO t(n) VALUES(1)");
}

void testDdl()
{
	struct Case { const char* sql; const char* tag; };
	const Case cases[] = {
		{"CREATE TABLE t(n INTEGER)", "CREATE TABLE"},
		{"CREATE TABLE IF NOT EXISTS t(n INTEGER)", "CREATE TABLE"},
		{"CREATE GLOBAL TEMPORARY TABLE t(n INTEGER)", "CREATE TABLE"},
		{"CREATE LOCAL TEMPORARY TABLE t(n INTEGER)", "CREATE TABLE"},
		{"RECREATE GLOBAL TEMPORARY TABLE t(n INTEGER)", "RECREATE TABLE"},
		{"RECREATE LOCAL TEMPORARY TABLE t(n INTEGER)", "RECREATE TABLE"},
		{"CREATE VIEW v AS SELECT n FROM t", "CREATE VIEW"},
		{"ALTER VIEW v AS SELECT n FROM t", "ALTER VIEW"},
		{"DROP VIEW v", "DROP VIEW"},
		{"CREATE INDEX idx ON t(n)", "CREATE INDEX"},
		{"CREATE UNIQUE INDEX idx ON t(n)", "CREATE INDEX"},
		{"CREATE DESC INDEX idx ON t(n)", "CREATE INDEX"},
		{"CREATE ASCENDING INDEX idx ON t(n)", "CREATE INDEX"},
		{"CREATE UNIQUE ASC INDEX idx ON t(n)", "CREATE INDEX"},
		{"CREATE UNIQUE DESCENDING INDEX idx ON t(n)", "CREATE INDEX"},
		{"CREATE /* modifier */ UNIQUE /* direction */ DESCENDING /* object */ INDEX idx ON t(n)", "CREATE INDEX"},
		{"ALTER INDEX idx INACTIVE", "ALTER INDEX"},
		{"DROP INDEX IF EXISTS idx", "DROP INDEX"},
		{"CREATE GENERATOR g", "CREATE SEQUENCE"},
		{"CREATE SEQUENCE s", "CREATE SEQUENCE"},
		{"ALTER GENERATOR g RESTART WITH 3", "ALTER SEQUENCE"},
		{"DROP GENERATOR g", "DROP SEQUENCE"},
		{"RECREATE GENERATOR g", "RECREATE SEQUENCE"},
		{"CREATE OR ALTER SEQUENCE s", "CREATE OR ALTER SEQUENCE"},
		{"SET GENERATOR g TO 9", "SET GENERATOR"},
		{"SET STATISTICS INDEX idx", "SET STATISTICS"},
		{"CREATE PROCEDURE p AS BEGIN END", "CREATE PROCEDURE"},
		{"CREATE OR ALTER PROCEDURE p AS BEGIN END", "CREATE OR ALTER PROCEDURE"},
		{"RECREATE PROCEDURE p AS BEGIN END", "RECREATE PROCEDURE"},
		{"CREATE FUNCTION f RETURNS INTEGER AS BEGIN RETURN 1; END", "CREATE FUNCTION"},
		{"CREATE AGGREGATE FUNCTION f", "CREATE FUNCTION"},
		{"ALTER EXTERNAL FUNCTION f", "ALTER FUNCTION"},
		{"DROP EXTERNAL FUNCTION f", "DROP FUNCTION"},
		{"DECLARE EXTERNAL FUNCTION f", "DECLARE FUNCTION"},
		{"CREATE OR ALTER AGGREGATE FUNCTION f", "CREATE OR ALTER FUNCTION"},
		{"RECREATE AGGREGATE FUNCTION f", "RECREATE FUNCTION"},
		{"CREATE TRIGGER tr FOR t BEFORE INSERT AS BEGIN END", "CREATE TRIGGER"},
		{"CREATE DOMAIN d AS INTEGER", "CREATE DOMAIN"},
		{"ALTER EXCEPTION e 'message'", "ALTER EXCEPTION"},
		{"CREATE OR ALTER EXCEPTION e 'message'", "CREATE OR ALTER EXCEPTION"},
		{"CREATE ROLE r", "CREATE ROLE"},
		{"ALTER CURRENT USER SET TAGS (k = 'v')", "ALTER USER"},
		{"CREATE USER u PASSWORD 'secret'", "CREATE USER"},
		{"CREATE COLLATION c FOR UTF8 FROM UNICODE", "CREATE COLLATION"},
		{"ALTER CHARACTER SET UTF8 SET DEFAULT COLLATION UNICODE", "ALTER CHARACTER SET"},
		{"DECLARE FILTER f", "DECLARE FILTER"},
		{"DROP FILTER f", "DROP FILTER"},
		{"CREATE SHADOW 1 'shadow.fdb'", "CREATE SHADOW"},
		{"DROP SHADOW 1", "DROP SHADOW"},
		{"ALTER DATABASE SET DEFAULT CHARACTER SET UTF8", "ALTER DATABASE"},
		{"CREATE DATABASE 'db.fdb'", "CREATE DATABASE"},
		{"CREATE PACKAGE p AS BEGIN END", "CREATE PACKAGE"},
		{"CREATE PACKAGE BODY p AS BEGIN END", "CREATE PACKAGE BODY"},
		{"CREATE OR ALTER PACKAGE BODY p AS BEGIN END", "CREATE OR ALTER PACKAGE BODY"},
		{"ALTER PACKAGE BODY p AS BEGIN END", "ALTER PACKAGE BODY"},
		{"RECREATE PACKAGE BODY p AS BEGIN END", "RECREATE PACKAGE BODY"},
		{"DROP PACKAGE BODY p", "DROP PACKAGE BODY"},
		{"CREATE PACKAGE \"BODY\" AS BEGIN END", "CREATE PACKAGE"},
		{"CREATE PACKAGE BODY_X AS BEGIN END", "CREATE PACKAGE"},
		{"CREATE PACKAGE BODY$X AS BEGIN END", "CREATE PACKAGE"},
		{"CREATE PACKAGE BODY AS BEGIN END", "CREATE PACKAGE"},
		{"RECREATE PACKAGE BODY AS BEGIN END", "RECREATE PACKAGE"},
		{"CREATE OR ALTER PACKAGE BODY AS BEGIN END", "CREATE OR ALTER PACKAGE"},
		{"CREATE PACKAGE BODY SQL SECURITY INVOKER AS BEGIN END", "CREATE PACKAGE"},
		{"ALTER PACKAGE BODY SQL SECURITY DEFINER", "ALTER PACKAGE"},
		{"ALTER PACKAGE BODY DROP SQL SECURITY", "ALTER PACKAGE"},
		{"CREATE PACKAGE BODY.p AS BEGIN END", "CREATE PACKAGE"},
		{"CREATE PACKAGE BODY%SCHEMA.p AS BEGIN END", "CREATE PACKAGE"},
		{"DROP PACKAGE BODY", "DROP PACKAGE"},
		{"DROP PACKAGE BODY;", "DROP PACKAGE"},
		{"DROP PACKAGE BODY -- end", "DROP PACKAGE"},
		{"DROP PACKAGE BODY /* end */ ;", "DROP PACKAGE"},
		{"DROP PACKAGE IF EXISTS BODY", "DROP PACKAGE"},
		{"CREATE PACKAGE IF NOT EXISTS BODY AS BEGIN END", "CREATE PACKAGE"},
		{"CREATE PACKAGE BODY IF NOT EXISTS BODY AS BEGIN END", "CREATE PACKAGE BODY"},
		{"CREATE PACKAGE BODY BODY AS BEGIN END", "CREATE PACKAGE BODY"},
		{"CREATE PACKAGE BODY SQL AS BEGIN END", "CREATE PACKAGE BODY"},
		{"CREATE PACKAGE BODY SQL.p AS BEGIN END", "CREATE PACKAGE BODY"},
		{"CREATE PACKAGE BODY \"AS\" AS BEGIN END", "CREATE PACKAGE BODY"},
		{"DROP PACKAGE BODY SQL", "DROP PACKAGE BODY"},
		{"DROP PACKAGE BODY IF EXISTS BODY", "DROP PACKAGE BODY"},
		{"CREATE MAPPING m", "CREATE MAPPING"},
		{"CREATE GLOBAL MAPPING m", "CREATE MAPPING"},
		{"ALTER GLOBAL MAPPING m", "ALTER MAPPING"},
		{"CREATE OR ALTER GLOBAL MAPPING m", "CREATE OR ALTER MAPPING"},
		{"DROP GLOBAL MAPPING m", "DROP MAPPING"},
		{"CREATE SCHEMA s", "CREATE SCHEMA"},
		{"CREATE OR ALTER SCHEMA s", "CREATE OR ALTER SCHEMA"},
		{"ALTER EXTERNAL CONNECTIONS POOL CLEAR ALL", "ALTER EXTERNAL CONNECTIONS POOL"},
		{"GRANT SELECT ON t TO u", "GRANT"},
		{"REVOKE SELECT ON t FROM u", "REVOKE"},
		{"COMMENT ON TABLE t IS 'CREATE TABLE'", "COMMENT"}
	};
	for (const auto& item : cases)
		expect(item.sql, item.tag);
}

void testEmptyAndMalformed()
{
	const std::string_view empty[] = {
		"", " \t\n\r\f ", "-- no newline", "-- SELECT;", "/**/", "/* 'SELECT'; */",
		" \t/* a */ -- b\r\n /* c */\f", "-- CR\r-- LF\n/* last */", "/* no /* nesting */"
	};
	for (const auto sql : empty)
		unknown(sql, true);
	const std::string_view malformed[] = {
		";", " /* ok */ ;", "-- comment\n;", "/*", " /* closed */ /* open",
		"/* a /* b */ */", "'", "\"", "'SELECT'", "\"SELECT\"", "\"SELE\"\"CT\"",
		"q'{SELECT}'", "q'{/*}'", "q'[-- comment]';", "q'{unterminated", "'-- SELECT'",
		"'/* SELECT */'", "\v", "\vSELECT 1 FROM RDB$DATABASE", "-- LF\n/* never closed",
		"CREATE OR", "CREATE OR /* open", "CREATE OR \"ALTER\" TABLE t(n INTEGER)",
		"CREATE \"TABLE\"", "CREATE GLOBAL", "CREATE LOCAL MAPPING m", "CREATE UNIQUE TABLE t",
		"CREATE DESC VIEW v", "CREATE EXTERNAL CONNECTIONS", "CREATE PACKAGE /* open",
		"CREATE PACKAGE \"unterminated", "ALTER CURRENT \"USER\"", "ALTER CHARACTER",
		"CREATE PACKAGE BODY /* open", "CREATE PACKAGE BODY SQL /* open",
		"UPDATE OR", "UPDATE OR \"INSERT\"", "UPDATE /* open", "UPDATE \"unterminated",
		"EXECUTE /* open", "EXECUTE \"BLOCK\"", "SET STATISTICS \"INDEX\""
	};
	for (const auto sql : malformed)
		unknown(sql);
	unknown(std::string_view("\0", 1));
	unknown(std::string(1024 * 1024, ' '), true);
	unknown(std::string(1024 * 1024, 'x'));
	unknown("/*" + std::string(1024 * 1024, 'x'));
	unknown("/*" + std::string(1024 * 1024, 'x') + "*/", true);
	expect("/* no /* nesting */ SELECT 1 FROM RDB$DATABASE", "SELECT", CountKind::Rows);
	// Classification deliberately stops at the needed prefix. These inputs are
	// not valid SQL: a caller must preserve the engine's prepare error instead
	// of treating the command tag as syntax validation or splitting the batch.
	expect("SELECT '", "SELECT", CountKind::Rows);
	expect("SELECT 1 FROM RDB$DATABASE; DELETE FROM t", "SELECT", CountKind::Rows);
	expect("CREATE TABLE", "CREATE TABLE");
}

void testBoundariesAndControls()
{
	const std::string_view commands[] = {"SELECT", "WITH", "INSERT", "UPDATE", "DELETE", "MERGE",
		"CALL", "EXECUTE", "CREATE", "ALTER", "DROP", "RECREATE", "DECLARE", "GRANT", "REVOKE", "COMMENT", "SET"};
	const std::string_view suffixes[] = {"1", "_x", "$x", "x", "\xD0\xAF"};
	for (const auto word : commands)
	{
		for (const auto suffix : suffixes)
			unknown(std::string(word) + std::string(suffix));
	}
	const std::string_view controls[] = {
		"BEGIN", "START TRANSACTION", "SET TRANSACTION", "COMMIT", "COMMIT RETAIN SNAPSHOT",
		"ROLLBACK", "ROLLBACK RETAIN", "ROLLBACK TO SAVEPOINT s", "SAVEPOINT s", "RELEASE SAVEPOINT s",
		"SET ROLE r", "SET TRUSTED ROLE", "SET SESSION IDLE TIMEOUT 1 SECOND", "SET STATEMENT TIMEOUT 1",
		"SET TIME ZONE 'UTC'", "SET BIND OF INT128 TO BIGINT", "SET DECFLOAT ROUND HALF_UP",
		"SET DECFLOAT TRAPS TO OVERFLOW", "SET OPTIMIZE FOR FIRST ROWS", "SET SEARCH_PATH TO PUBLIC",
		"SET DEBUG OPTION x = 1", "ALTER SESSION RESET", "ALTER /* CREATE TABLE */ SESSION RESET",
		"RESET ALL", "USING (n INTEGER = 1) DO SELECT n FROM RDB$DATABASE"
	};
	for (const auto sql : controls)
		unknown(sql);
}

} // namespace

int main()
{
	try
	{
		testDml();
		testDdl();
		testEmptyAndMalformed();
		testBoundariesAndControls();
		std::printf("SQL classification: %u checks passed\n", checks);
		return 0;
	}
	catch (const std::exception& error)
	{
		std::fprintf(stderr, "%s\n", error.what());
		return 1;
	}
}
