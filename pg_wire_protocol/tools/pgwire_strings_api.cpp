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
			statement = attachment->prepare(&status, nullptr, sql.size(), sql.data(), dialect, flags);
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
			statement = attachment->prepare(&expected, nullptr, sql.size(), sql.data(), dialect, flags);
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
				if (line && tag == isc_arg_gds && *p == isc_dsql_token_unk_err)
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
		require(mode(), "Initial mode is not on");
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
