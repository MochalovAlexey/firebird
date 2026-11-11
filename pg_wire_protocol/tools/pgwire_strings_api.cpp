// Direct OO contract checks. Runs only against a new private database.
#include <firebird/Interface.h>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>

using namespace Firebird;

static unsigned checks = 0;
static void require(bool ok, const char* reason)
{
	if (!ok) throw std::runtime_error(reason);
	++checks;
}

// The old native test templates use the system table in dialects 1/2/3.
// PG4 must reference that same uppercase native object explicitly. This changes
// only the test template, not user SQL or the production execution path.
static std::string systemTable(std::string sql, unsigned dialect)
{
	if (dialect == SQL_DIALECT_PG)
	{
		const std::string from = "FROM RDB$DATABASE";
		for (auto pos = sql.find(from); pos != std::string::npos; pos = sql.find(from, pos))
		{
			const std::string quoted = "FROM \"SYSTEM\".\"RDB$DATABASE\"";
			sql.replace(pos, from.size(), quoted);
			pos += quoted.size();
		}
	}
	return sql;
}

int main(int argc, char** argv)
{
	if (argc != 2 || std::filesystem::exists(argv[1]) || std::filesystem::is_symlink(argv[1]))
		return 2;
	auto master = fb_get_master_interface();
	auto provider = master->getDispatcher();
	ThrowStatusWrapper status(master->getStatus());
	CheckStatusWrapper expected(master->getStatus());
	IAttachment* attachment = nullptr;
	ITransaction* transaction = nullptr;
	IStatement* statement = nullptr;
	IResultSet* cursor = nullptr;
	IMessageMetadata* metadata = nullptr;
	int result = 0;
	try
	{
		const auto attach = [&](const char* charset, bool create)
		{
			auto dpb = master->getUtilInterface()->getXpbBuilder(&status, IXpbBuilder::DPB, nullptr, 0);
			dpb->insertString(&status, isc_dpb_user_name, "SYSDBA");
			dpb->insertString(&status, isc_dpb_lc_ctype, charset);
			dpb->insertString(&status, isc_dpb_config, "Providers=Engine14\nMaxStatementCacheSize=2097152\n");
			attachment = create ? provider->createDatabase(&status, argv[1],
				dpb->getBufferLength(&status), dpb->getBuffer(&status)) : provider->attachDatabase(&status,
				argv[1], dpb->getBufferLength(&status), dpb->getBuffer(&status));
			dpb->dispose();
		};
		const auto mode = [&]()
		{
			const unsigned char item[] = {fb_info_standard_conforming_strings};
			unsigned char buffer[16]{};
			attachment->getInfo(&status, sizeof(item), item, sizeof(buffer), buffer);
			require(buffer[0] == item[0] && buffer[1] == 4 && !buffer[2] &&
				buffer[7] == isc_info_end, "Invalid mode info item");
			const auto value = isc_vax_integer(reinterpret_cast<const char*>(buffer + 3), 4);
			require(value == 0 || value == 1, "Invalid mode value");
			return bool(value);
		};
		const auto setting = [&](bool on)
		{
			const char* sql = on ? "SET standard_conforming_strings=on" : "SET standard_conforming_strings=off";
			statement = attachment->prepare(&status, nullptr, 0, sql, SQL_DIALECT_PG, 0);
			require(!statement->execute(&status, nullptr, nullptr, nullptr, nullptr, nullptr), "SET opened transaction");
			statement->free(&status); statement = nullptr;
			require(mode() == on, "SET did not change the actual mode");
		};
		const auto query = [&](unsigned dialect, unsigned flags, const std::string& sql, const std::string& value)
		{
			const auto before = mode();
			const auto text = systemTable(sql, dialect);
			statement = attachment->prepare(&status, nullptr, text.size(), text.data(), dialect, flags);
			require(mode() == before, "Prepare override changed the attachment");
			metadata = statement->getOutputMetadata(&status);
			const auto length = metadata->getLength(&status, 0);
			auto builder = metadata->getBuilder(&status);
			builder->setType(&status, 0, SQL_VARYING);
			metadata->release(); metadata = builder->getMetadata(&status); builder->release();
			transaction = attachment->startTransaction(&status, 0, nullptr);
			cursor = statement->openCursor(&status, transaction, nullptr, nullptr, metadata, 0);
			std::vector<unsigned char> buffer(metadata->getMessageLength(&status));
			require(cursor->fetchNext(&status, buffer.data()) == IStatus::RESULT_OK, "No row");
			unsigned short bytes;
			const auto offset = metadata->getOffset(&status, 0);
			std::memcpy(&bytes, buffer.data() + offset, sizeof(bytes));
			require(std::string(reinterpret_cast<char*>(buffer.data()+offset+2), bytes) == value, "Wrong string bytes");
			require(cursor->fetchNext(&status, buffer.data()) == IStatus::RESULT_NO_DATA, "Extra row");
			cursor->close(&status); cursor = nullptr;
			metadata->release(); metadata = nullptr;
			statement->free(&status); statement = nullptr;
			transaction->rollback(&status); transaction = nullptr;
			return length;
		};
		const auto reject = [&](const std::string& sql, unsigned dialect, unsigned flags, const char* code,
			unsigned line = 0, unsigned column = 0)
		{
			expected.init();
			const auto text = systemTable(sql, dialect);
			statement = attachment->prepare(&expected, nullptr, text.size(), text.data(), dialect, flags);
			if (statement || !(expected.getState() & IStatus::STATE_ERRORS))
				std::fprintf(stderr, "Unexpected prepare success dialect=%u flags=%u: %s\n", dialect, flags, sql.c_str());
			require(!statement && (expected.getState() & IStatus::STATE_ERRORS), "Prepare unexpectedly accepted SQL");
			char actual[6]; fb_sqlstate(actual, expected.getErrors());
			require(!std::strcmp(actual, code), "Unexpected prepare SQLSTATE");
			bool positionFound = false;
			for (const ISC_STATUS* p = expected.getErrors(); *p != isc_arg_end; )
			{
				const auto tag = *p++;
				require(tag != isc_arg_gds || *p != isc_command_end_err2, "Definitive error became incomplete SQL");
				if (line && tag == isc_arg_gds && (*p == isc_dsql_token_unk_err || *p == isc_dsql_line_col_error))
				{
					require(p[1] == isc_arg_number && p[2] == line &&
						p[3] == isc_arg_number && p[4] == column, "Error position is not the original SQL position");
					positionFound = true;
				}
				p += tag == isc_arg_cstring ? 2 : 1;
			}
			require(!line || positionFound, "Missing error position");
		};
		constexpr auto ON = IStatement::PREPARE_STANDARD_CONFORMING_STRINGS_ON;
		constexpr auto OFF = IStatement::PREPARE_STANDARD_CONFORMING_STRINGS_OFF;
		const std::string sql = "SELECT '\\n' FROM RDB$DATABASE";
		const std::string cast = "SELECT CAST('\\n' AS VARCHAR(8)) FROM RDB$DATABASE";
		attach("UTF8", true);
		reject(std::string("SELECT 1 AS \"a") + char(0) + "b\"", 4, 0, "42000");
		require(mode(), "Initial mode is not on");
		// Same attachment/cache: PG numbered inputs must never enter native prepares.
		const auto input = [&](const char* text, unsigned dialect)
		{
			const auto sql = systemTable(text, dialect);
			statement = attachment->prepare(&status, nullptr, sql.size(), sql.data(), dialect, 0);
			metadata = statement->getInputMetadata(&status);
			require(metadata->getCount(&status) == 1 &&
				(metadata->getType(&status, 0) & ~1u) == SQL_LONG, "Input metadata changed");
			metadata->release(); metadata = nullptr;
			statement->free(&status); statement = nullptr;
		};
		// Same SQL text, attachment and DSQL cache; native dialects retain uppercase
		// lookup while PG4 uses lowercase. Both rows are real, distinct objects.
		for (const char* ddl : {"CREATE TABLE PUBLIC.NAMES_CACHE(VAL VARCHAR(10))",
			"CREATE TABLE PUBLIC.\"names_cache\"(\"val\" VARCHAR(10))",
			"INSERT INTO PUBLIC.NAMES_CACHE VALUES('native')",
			"INSERT INTO PUBLIC.\"names_cache\" VALUES('pg')"})
		{
			transaction = attachment->startTransaction(&status, 0, nullptr);
			attachment->execute(&status, transaction, 0, ddl, 3, nullptr, nullptr, nullptr, nullptr);
			transaction->commit(&status); transaction = nullptr;
		}
		for (unsigned dialect : {1u, 2u, 3u})
		{
			query(4, 0, "SELECT VAL FROM NAMES_CACHE", "pg");
			query(dialect, 0, "SELECT VAL FROM NAMES_CACHE", "native");
			query(4, 0, "SELECT VAL FROM NAMES_CACHE", "pg");
		}
		const char* numbered = "SELECT CAST($1 AS INTEGER) FROM RDB$DATABASE";
		for (unsigned dialect : {1u, 2u, 3u})
		{
			input(numbered, SQL_DIALECT_PG);
			reject(numbered, dialect, 0, "42000", 1, 13);
			input("SELECT CAST(? AS INTEGER) FROM RDB$DATABASE", dialect);
			query(dialect, 0, "SELECT A$1 FROM (SELECT 42 AS A$1 FROM RDB$DATABASE)", "42");
			input(numbered, SQL_DIALECT_PG);
		}
		input("USING (A INTEGER=?) DO SELECT :A FROM RDB$DATABASE", SQL_DIALECT_PG);
		input("USING (A INTEGER=?) DO SELECT CAST(:A AS BIGINT) FROM RDB$DATABASE", SQL_DIALECT_PG);
		const char* postfix = "SELECT 42::INTEGER FROM RDB$DATABASE";
		for (unsigned dialect : {1u, 2u, 3u})
		{
			query(4, 0, postfix, "42");
			reject(postfix, dialect, 0, "42000", 1, 10);
			query(dialect, 0, "SELECT CAST(42 AS INTEGER) AS INT8 FROM RDB$DATABASE", "42");
			query(dialect, 0, "EXECUTE BLOCK RETURNS(V INTEGER) AS BEGIN "
				"SELECT 42 FROM RDB$DATABASE INTO :V; SUSPEND; END", "42");
			query(4, 0, postfix, "42");
		}
		const char* limited = "SELECT 42 FROM RDB$DATABASE LIMIT 1";
		for (unsigned dialect : {1u, 2u, 3u})
		{
			query(4, 0, limited, "42");
			reject(limited, dialect, 0, "42000", 1, std::strlen(limited));
			query(dialect, 0, "SELECT FIRST 1 42 AS LIMIT FROM RDB$DATABASE", "42");
			query(dialect, 0, "SELECT LIMIT FROM (SELECT 42 AS LIMIT FROM RDB$DATABASE) ROWS 1", "42");
			query(4, 0, limited, "42");
		}
		for (unsigned dialect : {3u, 4u})
			query(dialect, 0, "SELECT \"LIMIT\" FROM (SELECT 42 AS \"LIMIT\" FROM RDB$DATABASE)", "42");
		input("USING (A INTEGER=?) DO SELECT FIRST 1 :A FROM RDB$DATABASE", SQL_DIALECT_PG);
		// The alias applies only after ::; ordinary CAST still resolves a domain.
		transaction = attachment->startTransaction(&status, 0, nullptr);
		attachment->execute(&status, transaction, 0, "CREATE DOMAIN INT8 AS VARCHAR(8)", 3,
			nullptr, nullptr, nullptr, nullptr);
		transaction->commit(&status); transaction = nullptr;
		for (unsigned dialect : {1u, 2u, 3u, 4u})
		{
			query(dialect, 0, dialect == SQL_DIALECT_PG ?
				"SELECT CAST('domain' AS \"INT8\") FROM RDB$DATABASE" :
				"SELECT CAST('domain' AS INT8) FROM RDB$DATABASE", "domain");
			if (dialect >= SQL_DIALECT_V6)
				query(dialect, 0, "SELECT CAST('quoted' AS \"INT8\") FROM RDB$DATABASE", "quoted");
			query(dialect, 0, "SELECT INT8 FROM (SELECT 42 AS INT8 FROM RDB$DATABASE)", "42");
		}
		query(4, 0, "SELECT 42::INT8 FROM RDB$DATABASE", "42");
		query(4, 0, "SELECT 42::iNt8 FROM RDB$DATABASE", "42");
		reject("SELECT CAST(1 AS BIGINT) FROM RDB$DATABASE", 1, 0, "HY000");
		require(query(4, 0, "SELECT 'abc'::VARCHAR FROM RDB$DATABASE", "abc") ==
			query(4, 0, "SELECT CAST('abc' AS VARCHAR) FROM RDB$DATABASE", "abc"),
			"Postfix changed the native VARCHAR default length");
		for (unsigned flags : {ON, OFF})
		{
			reject("SELECT 42 FROM RDB$DATABASE LIMIT\n  -1", 4, flags, "42000", 2, 3);
			reject("SELECT 42 FROM RDB$DATABASE LIMIT 1\n  @", 4, flags, "42000", 2, 3);
			reject("SELECT 'LIMIT 1', q'{LIMIT 1}' FROM RDB$DATABASE LIMIT 1\n  @", 4, flags, "42000", 2, 3);
			query(4, flags, "SELECT q'{LIMIT 1}' AS \"LIMIT\" /* LIMIT 1 */ FROM RDB$DATABASE", "LIMIT 1");
			query(4, flags, "EXECUTE BLOCK RETURNS(V VARCHAR(8)) AS BEGIN "
				"V='LIMIT 1'; /* LIMIT 1 */ SUSPEND; END", "LIMIT 1");
			reject("SELECT\n CAST($1foo AS INTEGER) FROM RDB$DATABASE", 4, flags, "42000", 2, 7);
			reject("SELECT\n CAST($0 AS INTEGER) FROM RDB$DATABASE", 4, flags, "42P02", 2, 7);
			reject("SELECT\n CAST($32768 AS INTEGER) FROM RDB$DATABASE", 4, flags, "54000", 2, 7);
			reject("SELECT\n CAST($2 AS INTEGER) FROM RDB$DATABASE", 4, flags, "0A000", 2, 7);
			reject("SELECT CAST($01 AS INTEGER),\n  @ FROM RDB$DATABASE", 4, flags, "42000", 2, 3);
			reject("SELECT 1::INTEGER,\n  @ FROM RDB$DATABASE", 4, flags, "42000", 2, 3);
			reject("SELECT\n 1::\"INT8\" FROM RDB$DATABASE", 4, flags, "42000", 2, 5);
			reject("SELECT\n 1::INT4 FROM RDB$DATABASE", 4, flags, "42000", 2, 5);
		}
		const auto onLength = query(4, 0, sql, "\\n");
		setting(false);
		const auto offLength = query(4, 0, sql, "\n");
		require(onLength == 8 && offLength == 4, "Literal metadata did not reflect decoded length");
		query(4, ON, sql, "\\n");
		query(4, 0, sql, "\n");
		query(4, ON, cast, "\\n");
		setting(true);
		query(4, OFF, sql, "\n");
		query(4, 0, sql, "\\n");
		query(4, OFF, cast, "\n");
		for (unsigned dialect : {1u, 2u, 3u})
		{
			setting(false);
			query(dialect, 0, sql, "\\n");
			query(dialect, OFF, sql, "\\n");
			reject("SET standard_conforming_strings=off;", dialect, IStatement::PREPARE_REQUIRE_SEMICOLON, "42000");
			setting(true);
			query(4, 0, sql, "\\n");
		}
		reject(sql, 4, ON|OFF, "22023");
		reject(sql, 3, ON|OFF, "22023");
		reject(std::string("SELECT '")+char(0x80)+"'", 4, OFF, "22021");
		reject(std::string("SELECT '")+char(0)+"'", 4, OFF, "22021");
		query(4, OFF, "SELECT _UTF8 '\\n' FROM RDB$DATABASE", "\n");
		query(4, OFF, "SELECT _WIN1251 q'{\\n}' FROM RDB$DATABASE", "\\n");
		reject("SELECT _WIN1251 '\\n'", 4, OFF, "0A000");
		reject("EXECUTE BLOCK AS BEGIN SET standard_conforming_strings=off; END", 4, 0, "0A000");
		for (const auto flags : {ON, OFF})
		{
			const std::string invalid = "SELECT '\\u0041', @ FROM RDB$DATABASE";
			reject(invalid, 4, flags, "42000", 1, invalid.find('@') + 1);
			reject("SELECT '\\n',\n  @ FROM RDB$DATABASE", 4, flags, "42000", 2, 3);
		}
		// Preparing a SET does nothing; reuse of the same handle applies it each time.
		statement = attachment->prepare(&status, nullptr, 0, "SET standard_conforming_strings=off", 4, 0);
		require(mode(), "SET applied at prepare");
		for (unsigned i=0; i<2; ++i)
		{
			require(!statement->execute(&status, nullptr, nullptr, nullptr, nullptr, nullptr), "SET returned transaction");
			require(!mode(), "Repeated SET did not execute");
			require(!attachment->execute(&status, nullptr, 0, "SET standard_conforming_strings=on", 4,
				nullptr, nullptr, nullptr, nullptr), "Session SET returned transaction");
		}
		statement->free(&status); statement = nullptr;
		transaction = attachment->startTransaction(&status, 0, nullptr);
		require(attachment->execute(&status, transaction, 0, "SET standard_conforming_strings=off", 4,
			nullptr, nullptr, nullptr, nullptr) == transaction, "SET replaced transaction");
		transaction->rollback(&status); transaction = nullptr;
		require(!mode(), "OO native rollback must preserve the session setting");
		attachment->execute(&status, nullptr, 0, "ALTER SESSION RESET", 3, nullptr, nullptr, nullptr, nullptr);
		require(mode(), "RESET did not restore on");
		attachment->detach(&status); attachment = nullptr;
		attach("WIN1251", false);
		reject(sql, 4, OFF, "0A000");
		statement = attachment->prepare(&status, nullptr, 0, "SET standard_conforming_strings=off", 4, 0);
		expected.init();
		require(!statement->execute(&expected, nullptr, nullptr, nullptr, nullptr, nullptr), "SET opened transaction");
		char code[6]; fb_sqlstate(code, expected.getErrors());
		require(!std::strcmp(code,"0A000") && mode(), "Non-UTF8 setting was silently accepted");
		statement->free(&status); statement = nullptr;
		query(4, ON, sql, "\\n");
		attachment->dropDatabase(&status); attachment = nullptr;
		std::printf("{\"passed\":true,\"checks\":%u}\n", checks);
	}
	catch (const FbException& ex)
	{
		char buffer[1024]; master->getUtilInterface()->formatStatus(buffer, sizeof(buffer), ex.getStatus());
		std::fprintf(stderr, "%s (after %u checks)\n", buffer, checks);
		result = 1;
	}
	catch (const std::exception& ex)
	{
		std::fprintf(stderr, "%s (after %u checks)\n", ex.what(), checks);
		result = 1;
	}
	// Failure paths must also close requests before rolling back/detaching.
	if (cursor)
	{
		cursor->close(&expected);
		if (expected.getState() & IStatus::STATE_ERRORS) cursor->release();
	}
	if (metadata) metadata->release();
	if (statement)
	{
		statement->free(&expected);
		if (expected.getState() & IStatus::STATE_ERRORS) statement->release();
	}
	if (transaction)
	{
		transaction->rollback(&expected);
		if (expected.getState() & IStatus::STATE_ERRORS) transaction->release();
	}
	if (attachment)
	{
		attachment->detach(&expected);
		if (expected.getState() & IStatus::STATE_ERRORS) attachment->release();
	}
	provider->release(); status.dispose(); expected.dispose();
	return result;
}
