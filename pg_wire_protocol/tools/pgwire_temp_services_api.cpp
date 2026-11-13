// Native API guards for irreversible LTT cleanup; uses only a harness-owned DB.
#include <firebird/Interface.h>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <vector>
using namespace Firebird;
int main(int argc, char** argv)
{
    if (argc != 2) return 2;
    auto master = fb_get_master_interface(); auto provider = master->getDispatcher();
    ThrowStatusWrapper status(master->getStatus());
    IAttachment* attachment = nullptr;
    ITransaction* a = nullptr; ITransaction* b = nullptr;
    IStatement* statement = nullptr; IResultSet* cursor = nullptr; IMessageMetadata* metadata = nullptr;
    try
    {
        auto dpb = master->getUtilInterface()->getXpbBuilder(&status, IXpbBuilder::DPB, nullptr, 0);
        dpb->insertString(&status, isc_dpb_user_name, "SYSDBA");
        dpb->insertString(&status, isc_dpb_lc_ctype, "UTF8");
        dpb->insertString(&status, isc_dpb_config, "Providers=Engine14\n");
        attachment = provider->attachDatabase(&status, argv[1], dpb->getBufferLength(&status), dpb->getBuffer(&status));
        dpb->dispose();
        const unsigned char tpb[] = {isc_tpb_version3, isc_tpb_write, isc_tpb_concurrency, isc_tpb_wait};
        const auto start = [&]() { return attachment->startTransaction(&status, sizeof(tpb), tpb); };
        const auto exec = [&](ITransaction* transaction, const char* sql, unsigned dialect=4, const char* expected=nullptr)
        {
            try { attachment->execute(&status, transaction, 0, sql, dialect, nullptr, nullptr, nullptr, nullptr); }
            catch (const FbException& ex)
            {
                char state[6]; fb_sqlstate(state, ex.getStatus()->getErrors());
                if (expected && !std::strcmp(expected, state)) return;
                std::fprintf(stderr, "SQL: %s\n", sql); throw;
            }
            if (expected) throw std::runtime_error("Expected native guard rejection");
        };
        const char* truncate = "EXECUTE BLOCK AS DECLARE n INTEGER; BEGIN n=RDB$PG_FASTTRUNCATE('pg_temp.guard_table'); END";
        exec(nullptr, "SET SEARCH_PATH TO \"public\", \"pg_catalog\", \"SYSTEM\"", 3);
        a = start(); exec(a, "CREATE TEMP TABLE guard_table(v INTEGER)"); a->commit(&status); a=nullptr;
        a = start(); exec(a,"INSERT INTO guard_table VALUES(1)"); exec(a,"INSERT INTO guard_table VALUES(2)"); a->commit(&status); a=nullptr;
        a = start();
        statement = attachment->prepare(&status,a,0,"SELECT * FROM guard_table ORDER BY v",4,0);
        metadata = statement->getOutputMetadata(&status);
        cursor = statement->openCursor(&status,a,nullptr,nullptr,metadata,0);
        std::vector<unsigned char> row(metadata->getMessageLength(&status));
        if (cursor->fetchNext(&status,row.data()) != IStatus::RESULT_OK) throw std::runtime_error("No first row");
        exec(a,truncate,3,"55006");
        if (cursor->fetchNext(&status,row.data()) != IStatus::RESULT_OK) throw std::runtime_error("Rejected truncate changed rows");
        cursor->close(&status); cursor=nullptr; statement->free(&status); statement=nullptr;
        metadata->release(); metadata=nullptr;
        b = start();
        statement = attachment->prepare(&status,b,0,"SELECT * FROM guard_table",4,0);
        metadata = statement->getOutputMetadata(&status);
        cursor = statement->openCursor(&status,b,nullptr,nullptr,metadata,0);
        cursor->fetchNext(&status,row.data()); cursor->close(&status); cursor=nullptr;
        statement->free(&status); statement=nullptr; metadata->release(); metadata=nullptr;
        exec(a,truncate,3,"55006");
        b->rollback(&status); b=nullptr;
        exec(a,truncate,3); exec(a,"INSERT INTO guard_table VALUES(3)"); a->rollback(&status); a=nullptr;
        a=start();
        for (unsigned dialect : {1u,2u,3u})
        {
            statement=attachment->prepare(&status,a,0,"SELECT 1 AS ANALYZE FROM SYSTEM.RDB$DATABASE",dialect,0);
            statement->free(&status);statement=nullptr;
            exec(a,"ANALYZE PGWIRE_TEMP.\"guard_table\"",dialect,"42000");
            exec(a,"SELECT 1 == 1 FROM SYSTEM.RDB$DATABASE",dialect,"42000");
        }
        a->rollback(&status);a=nullptr;
        attachment->detach(&status); attachment=nullptr;
        std::puts("{\"passed\":true,\"active_cursor_guard\":true,\"native_transaction_guard\":true,"
                  "\"native_analyze_rejected\":true,\"native_fulleq_rejected\":true}");
    }
    catch (const FbException& ex)
    {
        char message[4096]; master->getUtilInterface()->formatStatus(message,sizeof(message),ex.getStatus());
        std::fprintf(stderr,"%s\n",message);
        if(cursor)cursor->release(); if(statement)statement->release();if(metadata)metadata->release();
        if(a)a->release();if(b)b->release();if(attachment)attachment->release();provider->release();return 1;
    }
    catch (const std::exception& ex)
    {
        std::fprintf(stderr,"%s\n",ex.what());
        if(cursor)cursor->release();if(statement)statement->release();if(metadata)metadata->release();
        if(a)a->release();if(b)b->release();if(attachment)attachment->release();provider->release();return 1;
    }
    provider->release();return 0;
}
