// Native API boundary: multiple transactions in one attachment and retaining.
#include <firebird/Interface.h>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <stdexcept>
using namespace Firebird;
int main(int argc, char** argv)
{
    if (argc != 2 || std::filesystem::exists(argv[1])) return 2;
    auto master = fb_get_master_interface();
    auto provider = master->getDispatcher();
    ThrowStatusWrapper status(master->getStatus());
    IAttachment* attachment = nullptr;
    ITransaction* a = nullptr;
    ITransaction* b = nullptr;
    try
    {
        auto dpb = master->getUtilInterface()->getXpbBuilder(&status, IXpbBuilder::DPB, nullptr, 0);
        dpb->insertString(&status, isc_dpb_user_name, "SYSDBA");
        dpb->insertString(&status, isc_dpb_config, "Providers=Engine14\n");
        attachment = provider->createDatabase(&status, argv[1], dpb->getBufferLength(&status), dpb->getBuffer(&status));
        dpb->dispose();
        const unsigned char tpb[] = {isc_tpb_version3, isc_tpb_write, isc_tpb_concurrency, isc_tpb_nowait};
        const auto start = [&]() { return attachment->startTransaction(&status, sizeof(tpb), tpb); };
        const auto exec = [&](ITransaction* transaction, const char* sql, unsigned dialect = 4,
                              const char* expected = nullptr)
        {
            try { attachment->execute(&status, transaction, 0, sql, dialect, nullptr, nullptr, nullptr, nullptr); }
            catch (const FbException& ex)
            {
                char state[6]; fb_sqlstate(state, ex.getStatus()->getErrors());
                if (expected && !std::strcmp(expected, state)) return;
                std::fprintf(stderr, "SQL: %s\n", sql); throw;
            }
            if (expected) throw std::runtime_error("Expected timeout syntax rejection");
        };
        const auto timeout = [&](unsigned expected)
        {
            if (attachment->getStatementTimeout(&status) != expected)
                throw std::runtime_error("Attachment statement timeout differs");
        };
        exec(nullptr, "SET STATEMENT_TIMEOUT TO 123"); timeout(123);
        exec(nullptr, "SET STATEMENT_TIMEOUT = 0"); timeout(0);
        exec(nullptr, "SET STATEMENT_TIMEOUT = 456"); timeout(456);
        exec(nullptr, "SET STATEMENT_TIMEOUT TO DEFAULT"); timeout(0);
        a = start(); exec(a, "SET STATEMENT_TIMEOUT TO 123");
        a->rollback(&status); a = nullptr; timeout(123);
        a = start(); exec(a, "SET STATEMENT_TIMEOUT = 456");
        a->commit(&status); a = nullptr; timeout(456);
        for (const auto sql : {"SET STATEMENT_TIMEOUT TO -1", "SET STATEMENT_TIMEOUT TO '1s'",
                              "SET LOCAL STATEMENT_TIMEOUT TO 1", "SET STATEMENT_TIMEOUT TO ON"})
        {
            exec(nullptr, sql, 4, "42000"); timeout(456);
        }
        a = start();
        for (unsigned dialect : {1u, 2u, 3u})
        {
            auto statement = attachment->prepare(&status, a, 0,
                "SELECT 1 AS STATEMENT_TIMEOUT FROM SYSTEM.RDB$DATABASE", dialect, 0);
            statement->free(&status);
            exec(nullptr, "SET STATEMENT_TIMEOUT TO 1", dialect, "42000"); timeout(456);
            exec(nullptr, "SET STATEMENT TIMEOUT 456 MILLISECOND", dialect); timeout(456);
        }
        a->rollback(&status); a = nullptr;
        exec(nullptr, "SET STATEMENT_TIMEOUT TO DEFAULT"); timeout(0);
        const auto lock = [&](ITransaction* transaction, bool shared, bool conflict = false)
        {
            const auto sql = shared ? "EXECUTE BLOCK AS DECLARE n INTEGER; BEGIN n = RDB$PG_ADVISORY_LOCK(1, 1); END" :
                                     "EXECUTE BLOCK AS DECLARE n INTEGER; BEGIN n = RDB$PG_ADVISORY_LOCK(1, 2); END";
            try
            {
                attachment->execute(&status, transaction, 0, sql, 3, nullptr, nullptr, nullptr, nullptr);
            }
            catch (const FbException& ex)
            {
                if (conflict && (ex.getStatus()->getErrors()[1] == isc_lock_conflict ||
                                 ex.getStatus()->getErrors()[1] == isc_deadlock)) return;
                throw;
            }
            if (conflict) throw std::runtime_error("Different native transactions did not conflict");
        };
        a = start(); b = start();
        lock(a, true); lock(b, true); lock(b, false, true);
        a->commitRetaining(&status); lock(b, false); lock(a, true, true);
        b->rollbackRetaining(&status); lock(a, false); lock(b, true, true);
        a->commit(&status); a = nullptr; lock(b, true);
        b->rollback(&status); b = nullptr;
        a = start(); b = start(); lock(a, false); a->release(); a = nullptr; lock(b, false);
        b->rollback(&status); b = nullptr;
        attachment->dropDatabase(&status); attachment = nullptr;
        std::puts("{\"passed\":true,\"same_attachment_transactions\":true,\"retaining_and_release\":true,"
                  "\"statement_timeout_values_and_native_guards\":true,\"statement_timeout_attachment_lifetime\":true}");
    }
    catch (const FbException& ex)
    {
        char buffer[4096]; master->getUtilInterface()->formatStatus(buffer, sizeof(buffer), ex.getStatus());
        std::fprintf(stderr, "%s\n", buffer);
        if (a) a->release(); if (b) b->release(); if (attachment) attachment->release();
        provider->release(); return 1;
    }
    catch (const std::exception& ex)
    {
        std::fprintf(stderr, "%s\n", ex.what());
        if (a) a->release(); if (b) b->release(); if (attachment) attachment->release();
        provider->release(); return 1;
    }
    provider->release();
    return 0;
}
