// Native dialect controls for PG-only shared relation/index namespace lookup.
#include <firebird/Interface.h>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <vector>
using namespace Firebird;
int main(int argc, char** argv)
{
    if (argc != 2) return 2;
    auto master=fb_get_master_interface();auto provider=master->getDispatcher();
    ThrowStatusWrapper status(master->getStatus());
    IAttachment* attachment=nullptr;ITransaction* transaction=nullptr;
    IStatement* statement=nullptr;IResultSet* cursor=nullptr;IMessageMetadata* metadata=nullptr;
    try
    {
        auto dpb=master->getUtilInterface()->getXpbBuilder(&status,IXpbBuilder::DPB,nullptr,0);
        dpb->insertString(&status,isc_dpb_user_name,"SYSDBA");
        dpb->insertString(&status,isc_dpb_config,"Providers=Engine14\n");
        attachment=provider->attachDatabase(&status,argv[1],dpb->getBufferLength(&status),dpb->getBuffer(&status));
        dpb->dispose();
        attachment->execute(&status,nullptr,0,"SET SEARCH_PATH TO \"idx_a\",\"idx_b\",SYSTEM",3,nullptr,nullptr,nullptr,nullptr);
        transaction=attachment->startTransaction(&status,0,nullptr);
        for (unsigned dialect : {1u,2u,3u})
        {
            statement=attachment->prepare(&status,transaction,0,"SELECT * FROM NATIVE_INDEX_FIRST",dialect,0);
            metadata=statement->getOutputMetadata(&status);
            cursor=statement->openCursor(&status,transaction,nullptr,nullptr,metadata,0);
            std::vector<unsigned char> row(metadata->getMessageLength(&status));
            if (cursor->fetchNext(&status,row.data())!=IStatus::RESULT_OK) throw std::runtime_error("No native row");
            int value;std::memcpy(&value,row.data()+metadata->getOffset(&status,0),sizeof(value));
            if (value!=77) throw std::runtime_error("Native relation lookup changed");
            cursor->close(&status);cursor=nullptr;statement->free(&status);statement=nullptr;metadata->release();metadata=nullptr;
        }
        bool rejected=false;
        try { statement=attachment->prepare(&status,transaction,0,"SELECT * FROM \"NATIVE_INDEX_FIRST\"",4,0); }
        catch (const FbException&) { rejected=true; }
        if (!rejected) throw std::runtime_error("PG lookup bypassed an earlier index");
        transaction->rollback(&status);transaction=nullptr;attachment->detach(&status);attachment=nullptr;
        std::puts("{\"passed\":true,\"native_dialects\":[1,2,3],\"pg_shared_namespace\":true}");
    }
    catch (const FbException& ex)
    {
        char buffer[4096];master->getUtilInterface()->formatStatus(buffer,sizeof(buffer),ex.getStatus());std::fprintf(stderr,"%s\n",buffer);
        if(cursor)cursor->release();if(statement)statement->release();if(metadata)metadata->release();
        if(transaction)transaction->release();if(attachment)attachment->release();provider->release();return 1;
    }
    catch (const std::exception& ex)
    {
        std::fprintf(stderr,"%s\n",ex.what());
        if(cursor)cursor->release();if(statement)statement->release();if(metadata)metadata->release();
        if(transaction)transaction->release();if(attachment)attachment->release();provider->release();return 1;
    }
    provider->release();return 0;
}
