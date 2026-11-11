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
		const auto reject = [&]()
		{
			try { fetch(persistent); }
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
		transaction->commit(&status);
		transaction = attachment->startTransaction(&status, 0, nullptr);
		// Prepare without a supported path must succeed, Execute must fail.
		persistent = attachment->prepare(&status, transaction, 0,
			"SELECT RDB$PG_RELATION_ID('SAME') FROM SYSTEM.RDB$DATABASE", 3, 0);
		auto metadata = persistent->getOutputMetadata(&status);
		require(metadata->getType(&status, 0) == SQL_LONG && metadata->isNullable(&status, 0),
			"Helper metadata is not nullable INTEGER");
		metadata->release();
		reject();
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
		exec("SET SEARCH_PATH TO MISSING, \"pg_catalog\", SYSTEM");
		require(!fetch(persistent), "Missing lookup is not NULL");
		exec("SET SEARCH_PATH TO VA, \"$user\", \"pg_catalog\", SYSTEM"); reject();
		exec("SET SEARCH_PATH TO VA, \"pg_temp\", \"pg_catalog\", SYSTEM"); reject();
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
	if (persistent) persistent->release();
	if (transaction) transaction->release();
	if (attachment) attachment->release();
	provider->release();
	status.dispose();
	return result;
}
