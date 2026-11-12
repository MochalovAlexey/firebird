// APP-14: one native prepared handle must read live attachment state at Execute.
// Uses only the existing OO API, in a new private embedded database.
#include <firebird/Interface.h>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

using namespace Firebird;

static unsigned checks = 0;
static void require(bool ok, const char* message)
{
	if (!ok) throw std::runtime_error(message);
	++checks;
}

int main(int argc, char** argv)
{
	if (argc != 2 || std::filesystem::exists(argv[1]) || std::filesystem::is_symlink(argv[1]))
		return 2;
	auto master = fb_get_master_interface();
	auto provider = master->getDispatcher();
	ThrowStatusWrapper status(master->getStatus());
	IAttachment* attachment = nullptr;
	ITransaction* transaction = nullptr;
	IStatement* persistent = nullptr;
	int result = 0;
	try
	{
		auto dpb = master->getUtilInterface()->getXpbBuilder(&status, IXpbBuilder::DPB, nullptr, 0);
		dpb->insertString(&status, isc_dpb_user_name, "SYSDBA");
		dpb->insertString(&status, isc_dpb_lc_ctype, "UTF8");
		dpb->insertString(&status, isc_dpb_config, "Providers=Engine14\n");
		attachment = provider->createDatabase(&status, argv[1], dpb->getBufferLength(&status), dpb->getBuffer(&status));
		dpb->dispose();
		const auto exec = [&](const char* sql)
		{
			attachment->execute(&status, transaction, 0, sql, 3, nullptr, nullptr, nullptr, nullptr);
		};
		const auto fetch = [&](IStatement* statement) -> std::optional<int>
		{
			auto metadata = statement->getOutputMetadata(&status);
			auto builder = metadata->getBuilder(&status);
			builder->setType(&status, 0, SQL_LONG);
			metadata->release(); metadata = builder->getMetadata(&status); builder->release();
			IResultSet* cursor = nullptr;
			try
			{
				cursor = statement->openCursor(&status, transaction, nullptr, nullptr, metadata, 0);
				std::vector<unsigned char> buffer(metadata->getMessageLength(&status));
				require(cursor->fetchNext(&status, buffer.data()) == IStatus::RESULT_OK, "No row");
				short null;
				int value;
				std::memcpy(&null, buffer.data() + metadata->getNullOffset(&status, 0), sizeof(null));
				std::memcpy(&value, buffer.data() + metadata->getOffset(&status, 0), sizeof(value));
				require(cursor->fetchNext(&status, buffer.data()) == IStatus::RESULT_NO_DATA, "Extra row");
				cursor->close(&status); cursor = nullptr;
				metadata->release();
				return null ? std::optional<int>() : value;
			}
			catch (...)
			{
				if (cursor) cursor->release();
				metadata->release();
				throw;
			}
		};
		const auto query = [&](const char* sql, unsigned dialect = 3)
		{
			auto statement = attachment->prepare(&status, transaction, 0, sql, dialect, 0);
			try
			{
				auto value = fetch(statement);
				statement->free(&status);
				return value;
			}
			catch (...) { statement->release(); throw; }
		};
		const auto reject = [&](IStatement* statement = nullptr)
		{
			try { fetch(statement ? statement : persistent); }
			catch (const FbException& error)
			{
				char state[6]; fb_sqlstate(state, error.getStatus()->getErrors());
				require(!std::strcmp(state, "0A000"), "Unexpected helper guard SQLSTATE");
				return;
			}
			throw std::runtime_error("Guard did not reject Execute");
		};
		transaction = attachment->startTransaction(&status, 0, nullptr);
		for (const char* sql : {"CREATE SCHEMA \"pg_catalog\"", "CREATE SCHEMA VA", "CREATE SCHEMA VB",
			"CREATE TABLE VA.SAME(V INTEGER)", "CREATE TABLE VB.SAME(V INTEGER)",
			"INSERT INTO VA.SAME VALUES(11)", "INSERT INTO VB.SAME VALUES(22)"}) exec(sql);
		exec("CREATE FUNCTION PUBLIC.CHANGE_PATH RETURNS VARCHAR(4) AS BEGIN "
			"EXECUTE STATEMENT 'SET SEARCH_PATH TO VB, SYSTEM'; RETURN 'SAME'; END");
		exec("CREATE FUNCTION PUBLIC.NULL_PATH RETURNS VARCHAR(4) AS BEGIN "
			"EXECUTE STATEMENT 'SET SEARCH_PATH TO VB, SYSTEM'; RETURN NULL; END");
		transaction->commit(&status);
		transaction = attachment->startTransaction(&status, 0, nullptr);
		// Prepare is independent of the current effective relation path.
		persistent = attachment->prepare(&status, transaction, 0,
			"SELECT RDB$PG_RELATION_ID('SAME') FROM SYSTEM.RDB$DATABASE", 3, 0);
		auto metadata = persistent->getOutputMetadata(&status);
		require(metadata->getType(&status, 0) == SQL_LONG && metadata->isNullable(&status, 0),
			"Helper metadata is not nullable INTEGER");
		metadata->release();
		require(!fetch(persistent), "Missing implicit-path lookup is not NULL");
		exec("SET SEARCH_PATH TO VA, VB, \"pg_catalog\", SYSTEM");
		try
		{
			query("SELECT RDB$PG_RELATION_ID('SAME' || ASCII_CHAR(0) || 'suffix') FROM SYSTEM.RDB$DATABASE");
			throw std::runtime_error("Embedded NUL was silently normalized");
		}
		catch (const FbException& error)
		{
			char state[6]; fb_sqlstate(state, error.getStatus()->getErrors());
			require(!std::strcmp(state, "22023"), "Wrong native invalid-name SQLSTATE");
		}
		auto first = fetch(persistent);
		require(first == query("SELECT RDB$RELATION_ID FROM SYSTEM.RDB$RELATIONS WHERE RDB$SCHEMA_NAME='VA' AND RDB$RELATION_NAME='SAME'"),
			"Wrong native ID for VA");
		for (unsigned dialect : {1u, 2u, 3u})
		{
			require(query("SELECT V FROM SAME", dialect) == 11, "Native SELECT changed");
			require(query("SELECT OCTET_LENGTH(MAKE_DBKEY('VA.SAME',0)) FROM SYSTEM.RDB$DATABASE", dialect) == 8,
				"Native MAKE_DBKEY changed");
		}
		exec("SET SEARCH_PATH TO VB, VA, \"pg_catalog\", SYSTEM");
		auto second = fetch(persistent);
		require(second && second != first && query("SELECT V FROM SAME") == 22, "Prepared helper cached old path");
		// The argument changes attachment state at Execute. Guards must inspect
		// that new state, including on repeated execution of the same native handle.
		auto changingArgument = attachment->prepare(&status, transaction, 0,
			"SELECT RDB$PG_RELATION_ID(PUBLIC.CHANGE_PATH()) FROM SYSTEM.RDB$DATABASE", 3, 0);
		try
		{
			for (unsigned i = 0; i < 2; ++i)
			{
				exec("SET SEARCH_PATH TO VA, \"pg_catalog\", SYSTEM");
				require(fetch(changingArgument) == second, "Helper resolved before evaluating argument");
				require(query("SELECT V FROM SAME") == 22, "Argument did not change search path");
				require(fetch(persistent) == second, "Prepared helper ignored changed path");
			}
			changingArgument->free(&status);
		}
		catch (...) { changingArgument->release(); throw; }
		exec("SET SEARCH_PATH TO VA, \"pg_catalog\", SYSTEM");
		require(!query("SELECT RDB$PG_RELATION_ID(CAST(NULL AS VARCHAR(4))) FROM SYSTEM.RDB$DATABASE"),
			"NULL argument on a supported path is not NULL");
		auto nullArgument = attachment->prepare(&status, transaction, 0,
			"SELECT RDB$PG_RELATION_ID(PUBLIC.NULL_PATH()) FROM SYSTEM.RDB$DATABASE", 3, 0);
		try
		{
			// An implicit catalog path is now supported, including after expression evaluation.
			require(!fetch(nullArgument), "NULL path result is not NULL");
			require(fetch(persistent) == second, "NULL argument did not change path");
			nullArgument->free(&status);
		}
		catch (...) { nullArgument->release(); throw; }
		exec("CREATE FUNCTION PUBLIC.NULL_LTT RETURNS VARCHAR(4) AS BEGIN "
            "EXECUTE STATEMENT 'CREATE LOCAL TEMPORARY TABLE VA.ARG_LTT(V INTEGER)'; RETURN NULL; END");
        auto guardedNull = attachment->prepare(&status, transaction, 0,
            "SELECT RDB$PG_RELATION_ID(PUBLIC.NULL_LTT()) FROM SYSTEM.RDB$DATABASE", 3, 0);
        try { reject(guardedNull); guardedNull->free(&status); }
        catch (...) { guardedNull->release(); throw; }
        // A NULL name still validates the effective path after expression
        // evaluation. Real schemas may not impersonate a virtual temp namespace.
        exec("CREATE SCHEMA \"pg_temp_999999\"");
        exec("SET SEARCH_PATH TO \"pg_temp_999999\", VA, \"pg_catalog\", SYSTEM");
        bool collisionRejected = false;
        try { query("SELECT RDB$PG_RELATION_ID(CAST(NULL AS VARCHAR(4))) FROM SYSTEM.RDB$DATABASE"); }
        catch (const FbException& error)
        {
            char message[1024]; master->getUtilInterface()->formatStatus(message, sizeof(message), error.getStatus());
            require(std::strstr(message, "real schema collides"), "Wrong NULL path guard");
            collisionRejected = true;
        }
        require(collisionRejected, "NULL skipped effective-path validation");
        exec("SET SEARCH_PATH TO VA, \"pg_catalog\", SYSTEM");
        exec("DROP SCHEMA \"pg_temp_999999\"");
		exec("SET SEARCH_PATH TO MISSING, \"pg_catalog\", SYSTEM");
		require(!fetch(persistent), "Missing lookup is not NULL");
		exec("SET SEARCH_PATH TO VA, \"$user\", \"pg_catalog\", SYSTEM"); require(fetch(persistent) == first, "Expanded path changed lookup");
		exec("SET SEARCH_PATH TO VA, \"pg_temp\", \"pg_catalog\", SYSTEM"); require(fetch(persistent) == first, "Expanded path changed lookup");
		exec("SET SEARCH_PATH TO VA, \"pg_catalog\", SYSTEM");
		require(query("SELECT COUNT(*) FROM SYSTEM.MON$LOCAL_TEMPORARY_TABLES") == 0, "Unexpected LTT");
		exec("CREATE LOCAL TEMPORARY TABLE VA.LIVE_LTT(V INTEGER) ON COMMIT PRESERVE ROWS");
		// MON still uses its first transaction snapshot. The helper must use the live map.
		require(query("SELECT COUNT(*) FROM SYSTEM.MON$LOCAL_TEMPORARY_TABLES") == 0, "MON snapshot changed");
		reject();
		exec("DROP TABLE VA.LIVE_LTT");
		require(fetch(persistent) == first, "LTT DROP did not clear live guard");
		persistent->free(&status); persistent = nullptr;
		transaction->rollback(&status); transaction = nullptr;
		attachment->dropDatabase(&status); attachment = nullptr;
		std::printf("{\"passed\":true,\"assertions\":%u,\"same_prepared_handle\":true,\"native_dialects\":[1,2,3]}\n", checks);
	}
	catch (const FbException& error)
	{
		char buffer[2048];
		master->getUtilInterface()->formatStatus(buffer, sizeof(buffer), error.getStatus());
		std::fprintf(stderr, "%s\n", buffer); result = 1;
	}
	catch (const std::exception& error) { std::fprintf(stderr, "%s\n", error.what()); result = 1; }
	// A failed assertion still owns a live native transaction/attachment.
	// Close them explicitly instead of leaving shutdown to clean up the test DB.
	try
	{
		if (persistent) { persistent->free(&status); persistent = nullptr; }
		if (transaction) { transaction->rollback(&status); transaction = nullptr; }
		if (attachment) { attachment->dropDatabase(&status); attachment = nullptr; }
	}
	catch (const FbException& error)
	{
		char buffer[2048];
		master->getUtilInterface()->formatStatus(buffer, sizeof(buffer), error.getStatus());
		std::fprintf(stderr, "Cleanup: %s\n", buffer); result = 1;
	}
	if (persistent) persistent->release();
	if (transaction) transaction->release();
	if (attachment) attachment->release();
	provider->release();
	status.dispose();
	return result;
}
