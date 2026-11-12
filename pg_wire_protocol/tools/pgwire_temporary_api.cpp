// APP-15: native API boundaries of PG temporary metadata and namespace undo.
// Invoked by the existing isolated pgwire_1c_smoke harness; creates/drops one private DB.
#include <firebird/Interface.h>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>
using namespace Firebird;
static unsigned checks;
static void require(bool value, const char* what)
{
	if (!value) throw std::runtime_error(what);
	++checks;
}
int main(int argc, char** argv)
{
	if (argc != 2 || std::filesystem::exists(argv[1])) return 2;
	auto master = fb_get_master_interface();
	auto provider = master->getDispatcher();
	ThrowStatusWrapper status(master->getStatus());
	IAttachment* attachment = nullptr;
	ITransaction* transaction = nullptr;
	int result = 0;
	try
	{
		auto dpb = master->getUtilInterface()->getXpbBuilder(&status, IXpbBuilder::DPB, nullptr, 0);
		dpb->insertString(&status, isc_dpb_user_name, "SYSDBA");
		dpb->insertString(&status, isc_dpb_lc_ctype, "UTF8");
		dpb->insertString(&status, isc_dpb_config, "Providers=Engine14\n");
		attachment = provider->createDatabase(&status, argv[1], dpb->getBufferLength(&status), dpb->getBuffer(&status));
		dpb->dispose();
		const auto start = [&](bool noAuto = false)
		{
			const unsigned char tpb[] = {isc_tpb_version3, isc_tpb_write, isc_tpb_concurrency,
				isc_tpb_wait, isc_tpb_no_auto_undo};
			transaction = attachment->startTransaction(&status, sizeof(tpb) - (noAuto ? 0 : 1), tpb);
		};
		const auto exec = [&](const char* sql, unsigned dialect = 4)
		{
			try { attachment->execute(&status, transaction, 0, sql, dialect, nullptr, nullptr, nullptr, nullptr); }
			catch (...) { std::fprintf(stderr,"SQL: %s\n",sql); throw; }
		};
		const auto read = [&](IStatement* statement)
		{
			auto metadata = statement->getOutputMetadata(&status);
			auto builder = metadata->getBuilder(&status);
			builder->setType(&status, 0, SQL_INT64);
			metadata->release(); metadata = builder->getMetadata(&status); builder->release();
			IResultSet* cursor = nullptr;
			try
			{
				cursor = statement->openCursor(&status, transaction, nullptr, nullptr, metadata, 0);
				std::vector<unsigned char> buffer(metadata->getMessageLength(&status));
				require(cursor->fetchNext(&status, buffer.data()) == IStatus::RESULT_OK, "No scalar row");
				short null; ISC_INT64 value;
				std::memcpy(&null, buffer.data()+metadata->getNullOffset(&status,0),sizeof(null));
				std::memcpy(&value, buffer.data()+metadata->getOffset(&status,0),sizeof(value));
				require(!null && cursor->fetchNext(&status,buffer.data())==IStatus::RESULT_NO_DATA,"Bad scalar result");
				cursor->close(&status); metadata->release(); return value;
			}
			catch (...) { if (cursor) cursor->release(); metadata->release(); throw; }
		};
		const auto scalar = [&](const char* sql, unsigned dialect = 3)
		{
			auto statement = attachment->prepare(&status,transaction,0,sql,dialect,0);
			try { auto value=read(statement); statement->free(&status); return value; }
			catch (...) { statement->release(); throw; }
		};
		const auto error = [&](const char* sql, const char* expected, unsigned dialect = 4)
		{
			try { attachment->execute(&status,transaction,0,sql,dialect,nullptr,nullptr,nullptr,nullptr); }
			catch (const FbException& ex)
			{
				char state[6]; fb_sqlstate(state,ex.getStatus()->getErrors());
				if (std::strcmp(state,expected)) std::fprintf(stderr,"Expected %s, got %s: %s\n",expected,state,sql);
				require(!std::strcmp(state,expected),"Wrong native SQLSTATE"); return;
			}
			throw std::runtime_error(std::string("Expected rejection: ")+sql);
		};
		start();
		for (const char* sql : {"CREATE SCHEMA PGWIRE_COMPAT", "CREATE SEQUENCE PGWIRE_COMPAT.NAMESPACE_SEQ START WITH 32769",
			"CREATE SCHEMA PGWIRE_TEMP", "CREATE SCHEMA \"public\"", "CREATE SCHEMA \"pg_catalog\"",
			"CREATE TABLE PUBLIC.FILLER(V INTEGER)",
			"CREATE PROCEDURE PGWIRE_COMPAT.TEMP_ROWS_V1 RETURNS (ROW_KIND SMALLINT, PUBLIC_ID BIGINT, NATIVE_ID INTEGER, "
			"RELATION_NAME VARCHAR(63) CHARACTER SET UTF8, NAMESPACE_ID BIGINT, NAMESPACE_NAME VARCHAR(63) CHARACTER SET UTF8) "
			"EXTERNAL NAME 'PGWIRE_TEMP_ROWS_V1' ENGINE SYSTEM"}) exec(sql,3);
		transaction->commit(&status); transaction=nullptr;
		const auto ns = [&]() { return scalar("SELECT COALESCE(MAX(PUBLIC_ID),0) FROM PGWIRE_COMPAT.TEMP_ROWS_V1 WHERE ROW_KIND=0"); };
		const auto id = [&]() { return scalar("SELECT PUBLIC_ID FROM PGWIRE_COMPAT.TEMP_ROWS_V1 WHERE ROW_KIND=1"); };
		const auto count = [&]() { return scalar("SELECT COUNT(*) FROM PGWIRE_COMPAT.TEMP_ROWS_V1 WHERE ROW_KIND=1"); };
        start();
        exec("CREATE SCHEMA \"pg_temp_32769\"",3);
        error("CREATE TEMP TABLE collision(v INTEGER)","42000");
        require(ns()==0 && count()==0,"Namespace collision left metadata");
        exec("DROP SCHEMA \"pg_temp_32769\"",3); transaction->commit(&status); transaction=nullptr;
		for (bool noAuto : {false,true})
		{
			for (bool force : {false,true})
			{
				start(noAuto); require(ns()==0,"Namespace exists before first create");
				exec("CREATE TEMP TABLE first_create(v INTEGER)");
				require(ns()>32768 && count()==1,"First create did not initialize");
				exec("INSERT INTO first_create VALUES(7)");
				if (force) transaction->release(); else transaction->rollback(&status);
				transaction=nullptr; start();
				require(ns()==0 && count()==0,"Rollback retained namespace/table");
				transaction->rollback(&status); transaction=nullptr;
			}
			start(noAuto); exec("CREATE TEMP TABLE retain(v INTEGER)");
			auto initial=ns(); auto relation=id();
			exec("INSERT INTO retain VALUES(8)");
			transaction->commitRetaining(&status);
			exec("SAVEPOINT s"); exec("DROP TABLE retain"); transaction->rollbackRetaining(&status);
			require(ns()==initial && id()==relation && scalar("SELECT v FROM retain",4)==8,"Retaining lost committed identity/data");
			exec("DROP TABLE retain"); transaction->commitRetaining(&status);
			transaction->rollback(&status); transaction=nullptr; start();
			require(ns()==initial && count()==0,"Empty committed namespace lost");
			transaction->rollback(&status); transaction=nullptr;
			// Reattach is the native attachment boundary for an initialized namespace.
			attachment->detach(&status); attachment=nullptr;
			auto b=master->getUtilInterface()->getXpbBuilder(&status,IXpbBuilder::DPB,nullptr,0);
			b->insertString(&status,isc_dpb_user_name,"SYSDBA"); b->insertString(&status,isc_dpb_lc_ctype,"UTF8");
			b->insertString(&status,isc_dpb_config,"Providers=Engine14\n");
			attachment=provider->attachDatabase(&status,argv[1],b->getBufferLength(&status),b->getBuffer(&status)); b->dispose();
		}
		start();
		exec("CREATE TEMP TABLE threshold(v INTEGER)");
		exec("EXECUTE BLOCK AS DECLARE I INTEGER=0; BEGIN WHILE (I<300000) DO BEGIN "
			"INSERT INTO PUBLIC.FILLER VALUES(:I); I=I+1; END END",3);
		require(ns()>32768 && scalar("SELECT COUNT(*) FROM PUBLIC.FILLER")==300000,"Threshold fixture incomplete");
		transaction->rollback(&status); transaction=nullptr; start();
		require(ns()==0 && count()==0 && scalar("SELECT COUNT(*) FROM PUBLIC.FILLER")==0,"Large root rollback lost undo");
		exec("SAVEPOINT outer_sp"); exec("CREATE TEMP TABLE empty(v INTEGER)"); exec("DROP TABLE empty");
		exec("RELEASE SAVEPOINT outer_sp"); transaction->rollbackRetaining(&status);
		require(ns()==0,"Released namespace undo did not reach transaction");
		// Failed CREATE never activates the namespace; validation precedes allocation.
		error("CREATE TEMP TABLE bad(v INTEGER DEFAULT 1)","42000"); require(ns()==0,"Failed create activated namespace");
		// Savepoint exemption depends on current ownership, not the prepared PG AST.
		exec("CREATE TEMP TABLE stale(v INTEGER)"); transaction->commitRetaining(&status);
		auto stale=attachment->prepare(&status,transaction,0,"DROP TABLE pg_temp.stale",4,0);
		exec("DROP TABLE stale"); exec("CREATE LOCAL TEMPORARY TABLE PGWIRE_TEMP.\"stale\"(V INTEGER)",3);
		exec("SAVEPOINT sp");
		bool rejected=false;
		try { stale->execute(&status,transaction,nullptr,nullptr,nullptr,nullptr); }
		catch (const FbException& ex) { char code[6]; fb_sqlstate(code,ex.getStatus()->getErrors()); require(!std::strcmp(code,"0A000"),"Wrong stale guard"); rejected=true; }
		stale->free(&status); require(rejected,"Stale PG DDL bypassed native savepoint guard");
		exec("RELEASE SAVEPOINT sp"); exec("DROP TABLE PGWIRE_TEMP.\"stale\"",3);
		transaction->commitRetaining(&status);
        exec("CREATE TABLE PGWIRE_TEMP.\"alien_relation\"(V INTEGER)",3);
        error("CREATE TEMP TABLE IF NOT EXISTS alien_relation(v INTEGER)","0A000");
        exec("DROP TABLE PGWIRE_TEMP.\"alien_relation\"",3); transaction->commitRetaining(&status);
		// Existing native handles keep their compiled relation; fresh PG Prepare
        // (and wire Bind/reprepare) must use the changed effective namespace.
        exec("SET SEARCH_PATH TO public, pg_catalog, \"SYSTEM\"");
        exec("CREATE TABLE public.native_cache(v INTEGER)");
        exec("INSERT INTO public.native_cache VALUES(1)"); transaction->commitRetaining(&status);
        auto oldHandle=attachment->prepare(&status,transaction,0,"SELECT v FROM native_cache",4,0);
        require(read(oldHandle)==1,"Old handle fixture failed");
        exec("CREATE TEMP TABLE native_cache(v INTEGER)"); exec("INSERT INTO native_cache VALUES(2)");
        require(read(oldHandle)==1 && scalar("SELECT v FROM native_cache",4)==2,"Fresh Prepare/old native handle contracts differ");
        oldHandle->free(&status); exec("DROP TABLE native_cache"); exec("DROP TABLE public.native_cache");
        transaction->commitRetaining(&status);
		// A paused provider cursor owns value copies across DROP/recreate.
		exec("CREATE TEMP TABLE copy_rows(v INTEGER)");
		auto old=id();
		auto snapshot=attachment->prepare(&status,transaction,0,"SELECT PUBLIC_ID FROM PGWIRE_COMPAT.TEMP_ROWS_V1",3,0);
		auto meta=snapshot->getOutputMetadata(&status);
		auto cursor=snapshot->openCursor(&status,transaction,nullptr,nullptr,meta,0);
		std::vector<unsigned char> row(meta->getMessageLength(&status));
		require(cursor->fetchNext(&status,row.data())==IStatus::RESULT_OK,"No namespace snapshot row");
		exec("DROP TABLE copy_rows"); exec("CREATE TEMP TABLE copy_rows(v INTEGER)");
		require(id()!=old,"Recreate did not allocate fresh ID");
		require(cursor->fetchNext(&status,row.data())==IStatus::RESULT_OK,"Snapshot row disappeared");
		ISC_INT64 copied; std::memcpy(&copied,row.data()+meta->getOffset(&status,0),sizeof(copied));
		require(copied==old,"Paused provider retained mutable metadata");
		cursor->close(&status); meta->release(); snapshot->free(&status);
		exec("DROP TABLE copy_rows"); transaction->commitRetaining(&status);
		std::set<ISC_INT64> nativeIds, publicIds;
		for (unsigned i=0;i<1030;++i)
		{
			exec("CREATE TEMP TABLE reuse(v INTEGER)");
			nativeIds.insert(scalar("SELECT NATIVE_ID FROM PGWIRE_COMPAT.TEMP_ROWS_V1 WHERE ROW_KIND=1"));
			require(publicIds.insert(id()).second,"Public ID reused");
			require(scalar("SELECT COUNT(*) FROM reuse",4)==0,"Cached native ID retained rows");
			exec("DROP TABLE reuse"); transaction->commitRetaining(&status);
		}
		require(nativeIds.size()<publicIds.size(),"Native relation ID reuse was not reached");
		// Native dialects retain native default DELETE and GTT lookup.
		for (unsigned dialect : {1u,2u,3u})
		{
			exec("CREATE LOCAL TEMPORARY TABLE PUBLIC.NATIVE_LTT(V INTEGER)",dialect);
			exec("INSERT INTO PUBLIC.NATIVE_LTT VALUES(1)",dialect); transaction->commit(&status); transaction=nullptr; start();
			require(scalar("SELECT COUNT(*) FROM PUBLIC.NATIVE_LTT",dialect)==0,"Native LTT default changed");
			exec("SAVEPOINT native_sp",dialect);
			error("ALTER TABLE PUBLIC.NATIVE_LTT ADD X INTEGER","0A000",dialect);
			exec("RELEASE SAVEPOINT native_sp",dialect); exec("DROP TABLE PUBLIC.NATIVE_LTT",dialect);
			exec("CREATE GLOBAL TEMPORARY TABLE PUBLIC.NATIVE_GTT(V INTEGER)",dialect);
			exec("INSERT INTO PUBLIC.NATIVE_GTT VALUES(1)",dialect);
			require(scalar("SELECT OCTET_LENGTH(MAKE_DBKEY('PUBLIC.NATIVE_GTT',0)) FROM SYSTEM.RDB$DATABASE",dialect)==8,"MAKE_DBKEY changed");
			transaction->commit(&status); transaction=nullptr; start();
			require(scalar("SELECT COUNT(*) FROM PUBLIC.NATIVE_GTT",dialect)==0,"Native GTT default changed");
			error("SELECT * FROM \"PUBLIC\".\"NATIVE_GTT\"","0A000");
			exec("DROP TABLE PUBLIC.NATIVE_GTT",dialect); transaction->commitRetaining(&status);
		}
		// Exhaustion is explicit, has no metadata side effects, and never wraps.
		exec("SET GENERATOR PGWIRE_COMPAT.NAMESPACE_SEQ TO 4294967295",3);
		error("CREATE TEMP TABLE exhausted(v INTEGER)","22003");
		require(count()==0 && scalar("SELECT GEN_ID(PGWIRE_COMPAT.NAMESPACE_SEQ,0) FROM SYSTEM.RDB$DATABASE")==4294967296LL,"Allocator wrapped or created metadata");
		transaction->rollback(&status); transaction=nullptr;
		attachment->dropDatabase(&status); attachment=nullptr;
		std::printf("{\"passed\":true,\"assertions\":%u,\"threshold_rows\":300000,\"native_ids\":%zu,\"public_ids\":%zu}\n",checks,nativeIds.size(),publicIds.size());
	}
	catch (const FbException& ex) { char text[2048]; master->getUtilInterface()->formatStatus(text,sizeof(text),ex.getStatus()); std::fprintf(stderr,"%s\n",text); result=1; }
	catch (const std::exception& ex) { std::fprintf(stderr,"%s\n",ex.what()); result=1; }
	if (transaction) transaction->release();
	try { if (attachment) { attachment->dropDatabase(&status); attachment=nullptr; } } catch (...) { result=1; }
	if (attachment) attachment->release(); provider->release(); status.dispose(); return result;
}
