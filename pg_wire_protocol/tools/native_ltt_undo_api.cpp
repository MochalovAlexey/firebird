// NAT-06: native TPB, force rollback and multiple transactions on one attachment.
// Run only against a new private database; no server or compatibility bootstrap.
#include <firebird/Interface.h>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>
using namespace Firebird;

static unsigned checks;
static void require(bool condition, const char* what)
{
	if (!condition) throw std::runtime_error(what);
	++checks;
}

int main(int argc, char** argv)
{
	if (argc < 2 || std::filesystem::exists(argv[1])) return 2;
	auto master = fb_get_master_interface();
	auto provider = master->getDispatcher();
	ThrowStatusWrapper status(master->getStatus());
	IAttachment* attachment = nullptr;
	IAttachment* observer = nullptr;
	ITransaction* first = nullptr;
	ITransaction* second = nullptr;
	int result = 0;
	try
	{
		auto dpb = master->getUtilInterface()->getXpbBuilder(&status, IXpbBuilder::DPB, nullptr, 0);
		dpb->insertString(&status, isc_dpb_user_name, "SYSDBA");
		dpb->insertString(&status, isc_dpb_lc_ctype, "UTF8");
		dpb->insertString(&status, isc_dpb_config, "Providers=Engine14\n");
		attachment = provider->createDatabase(&status, argv[1], dpb->getBufferLength(&status), dpb->getBuffer(&status));
		observer = provider->attachDatabase(&status, argv[1], dpb->getBufferLength(&status), dpb->getBuffer(&status));
		dpb->dispose();
		const auto start = [&](bool noAuto = false)
		{
			const unsigned char tpb[] = {isc_tpb_version3, isc_tpb_write, isc_tpb_concurrency,
				isc_tpb_wait, isc_tpb_no_auto_undo};
			return attachment->startTransaction(&status, sizeof(tpb) - (noAuto ? 0 : 1), tpb);
		};
		const auto exec = [&](ITransaction* t, const char* sql)
		{
			attachment->execute(&status, t, 0, sql, 3, nullptr, nullptr, nullptr, nullptr);
		};
		const auto scalar = [&](IAttachment* a, ITransaction* t, const char* sql) -> ISC_INT64
		{
			auto statement = a->prepare(&status, t, 0, sql, 3, 0);
			auto metadata = statement->getOutputMetadata(&status);
			auto builder = metadata->getBuilder(&status);
			builder->setType(&status, 0, SQL_INT64);
			metadata->release(); metadata = builder->getMetadata(&status); builder->release();
			IResultSet* cursor = nullptr;
			try
			{
				cursor = statement->openCursor(&status, t, nullptr, nullptr, metadata, 0);
				std::vector<unsigned char> buffer(metadata->getMessageLength(&status));
				require(cursor->fetchNext(&status, buffer.data()) == IStatus::RESULT_OK, "No scalar row");
				short null; ISC_INT64 value;
				std::memcpy(&null, buffer.data() + metadata->getNullOffset(&status, 0), sizeof(null));
				std::memcpy(&value, buffer.data() + metadata->getOffset(&status, 0), sizeof(value));
				require(!null && cursor->fetchNext(&status, buffer.data()) == IStatus::RESULT_NO_DATA, "Bad scalar result");
				cursor->close(&status); metadata->release(); statement->free(&status);
				return value;
			}
			catch (...) { if (cursor) cursor->release(); metadata->release(); statement->release(); throw; }
		};
		const auto observe = [&]()
		{
			auto t = observer->startTransaction(&status, 0, nullptr);
			try
			{
				auto n = scalar(observer, t, "SELECT COUNT(*) FROM FILLER");
				t->rollback(&status); return n;
			}
			catch (...) { t->release(); throw; }
		};
		const auto error = [&](ITransaction* t, const char* sql, const char* expected)
		{
			try { exec(t, sql); }
			catch (const FbException& ex)
			{
				char state[6]; fb_sqlstate(state, ex.getStatus()->getErrors());
				if (std::strcmp(state, expected))
					std::fprintf(stderr, "Expected %s, got %s for %s\n", expected, state, sql);
				require(!std::strcmp(state, expected), "Wrong error SQLSTATE"); return;
			}
			throw std::runtime_error(std::string("Expected error: ") + sql);
		};
		first = start();
		exec(first, "CREATE TABLE FILLER(V INTEGER)");
		first->commit(&status); first = nullptr;

		if (argc == 3)
		{
			// Only for the separately built /tmp fault-injection engine. Production
			// sources have no test hook; both failures must reach the caller.
			const bool mapFailure = !std::strcmp(argv[2], "map");
			first = start();
			exec(first, "CREATE LOCAL TEMPORARY TABLE A(V INTEGER) ON COMMIT PRESERVE ROWS");
			if (mapFailure)
			{
				exec(first, "INSERT INTO A VALUES(7)");
				first->commit(&status); first = start();
				exec(first, "DROP TABLE A");
			}
			bool failed = false;
			try { first->rollback(&status); }
			catch (const FbException& ex)
			{
				char state[6]; fb_sqlstate(state, ex.getStatus()->getErrors());
				require(!std::strcmp(state, "HY001"), "Metadata backout error was hidden"); failed = true;
			}
			require(failed, "Fault engine did not inject the backout error");
			if (mapFailure)
			{
				first->rollback(&status); first = start();
				require(scalar(attachment, first, "SELECT V FROM A") == 7, "Pending DROP snapshot was lost");
				first->rollback(&status); first = nullptr;
			}
			else
			{
				bool shut = false;
				try { exec(first, "INSERT INTO FILLER VALUES(99)"); }
				catch (const FbException& ex)
				{
					char state[6]; fb_sqlstate(state, ex.getStatus()->getErrors());
					require(!std::strcmp(state, "08003"), "Wrong shutdown SQLSTATE"); shut = true;
				}
				require(shut, "Attachment accepted SQL after partial cache backout");
				first->release(); first = nullptr;
				attachment->detach(&status); attachment = nullptr;
				require(observe() == 0, "Metadata failure committed persistent rows");
				observer->dropDatabase(&status); observer = nullptr;
			}
		}
		else
		{
			for (bool noAuto : {false, true})
			{
				first = start(noAuto);
				exec(first, "CREATE LOCAL TEMPORARY TABLE A(V INTEGER) ON COMMIT PRESERVE ROWS");
				exec(first, "INSERT INTO A VALUES(7)");
				exec(first, "INSERT INTO FILLER VALUES(1)");
				// release is a force rollback, and the attachment remains live.
				first->release(); first = nullptr;
				require(observe() == 0, "Force rollback committed persistent data");
				first = start();
				require(scalar(attachment, first, "SELECT COUNT(*) FROM SYSTEM.MON$LOCAL_TEMPORARY_TABLES") == 0,
					"Force rollback left stale LTT metadata");
				error(first, "INSERT INTO A VALUES(9)", "42S02");
				exec(first, "CREATE LOCAL TEMPORARY TABLE A(V INTEGER) ON COMMIT PRESERVE ROWS");
				exec(first, "CREATE UNIQUE INDEX UQ_A ON A(V)");
				exec(first, "INSERT INTO A VALUES(7)");
				first->commit(&status); first = start(noAuto);
				exec(first, "ALTER TABLE A ADD Q INTEGER");
				exec(first, "DROP TABLE A");
				first->release(); first = start();
				require(scalar(attachment, first, "SELECT V FROM A") == 7, "Force rollback lost committed row");
				error(first, "INSERT INTO A(Q) VALUES(1)", "42S22");
				error(first, "INSERT INTO A VALUES(7)", "23000");
				exec(first, "DROP TABLE A");
				first->commit(&status); first = nullptr;
			}

			first = start(true); second = start(true);
			exec(first, "CREATE LOCAL TEMPORARY TABLE A(V INTEGER) ON COMMIT PRESERVE ROWS");
			exec(second, "CREATE LOCAL TEMPORARY TABLE B(X INTEGER, Y INTEGER) ON COMMIT PRESERVE ROWS");
			exec(first, "INSERT INTO A VALUES(1)"); exec(second, "INSERT INTO B VALUES(2,3)");
			first->rollback(&status); first = nullptr;
			require(scalar(attachment, second, "SELECT X+Y FROM B") == 5, "Other transaction's LTT was changed");
			error(second, "INSERT INTO A VALUES(1)", "42S02");
			second->commit(&status); second = nullptr;
			first = start(); second = start();
			require(scalar(attachment, first, "SELECT X FROM B") == 2, "Committed second LTT missing");
			error(second, "ALTER TABLE B ADD Z INTEGER", "42000");
			second->rollback(&status); second = nullptr;
			first->rollback(&status); first = start();
			exec(first, "DROP TABLE B"); first->commit(&status); first = nullptr;

			// A deterministic native pre-commit error, followed by metadata/data rollback.
			first = start();
			exec(first, "CREATE EXCEPTION COMMIT_FAIL 'NAT-06 commit rejection'");
			exec(first, "CREATE TRIGGER REJECT_COMMIT ON TRANSACTION COMMIT AS BEGIN "
				"IF (RDB$GET_CONTEXT('USER_SESSION','REJECT_COMMIT') = 'yes') THEN EXCEPTION COMMIT_FAIL; END");
			first->commit(&status); first = start(true);
			exec(first, "CREATE LOCAL TEMPORARY TABLE C(V INTEGER)");
			exec(first, "INSERT INTO FILLER VALUES(2)");
			exec(first, "EXECUTE BLOCK AS BEGIN RDB$SET_CONTEXT('USER_SESSION','REJECT_COMMIT','yes'); END");
			bool rejected = false;
			try { first->commit(&status); }
			catch (const FbException& ex)
			{
				char state[6]; fb_sqlstate(state, ex.getStatus()->getErrors());
				require(!std::strcmp(state, "HY000"), "Wrong commit rejection"); rejected = true;
			}
			require(rejected, "Commit unexpectedly succeeded");
			first->rollback(&status); first = start();
			require(observe() == 0, "Rejected commit exposed data");
			error(first, "INSERT INTO C VALUES(1)", "42S02");
			first->rollback(&status); first = nullptr;
		}
		if (observer) { observer->detach(&status); observer = nullptr; }
		if (attachment) { attachment->dropDatabase(&status); attachment = nullptr; }
		std::printf("{\"passed\":true,\"assertions\":%u}\n", checks);
	}
	catch (const FbException& ex)
	{
		char buffer[2048]; master->getUtilInterface()->formatStatus(buffer,sizeof(buffer),ex.getStatus());
		std::fprintf(stderr,"%s\n",buffer); result=1;
	}
	catch (const std::exception& ex) { std::fprintf(stderr,"%s\n",ex.what()); result=1; }
	if (first) first->release();
	if (second) second->release();
	try
	{
		if (observer) { observer->detach(&status); observer=nullptr; }
		if (attachment) { attachment->dropDatabase(&status); attachment=nullptr; }
	}
	catch (...) { result=1; }
	if (observer) observer->release();
	if (attachment) attachment->release();
	provider->release(); status.dispose();
	return result;
}
