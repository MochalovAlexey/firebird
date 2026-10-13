/* Native Firebird OO API regression. Credentials arrive through ISC_* env. */
#include <firebird/Interface.h>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <vector>
using namespace Firebird;

int main(int argc, char** argv)
{
    if (argc != 3 || (std::strcmp(argv[2], "prepare") && std::strcmp(argv[2], "cursor")))
        return 2;
    const bool fetch = !std::strcmp(argv[2], "cursor");
    const char* sql = fetch ? "SELECT V FROM P7_NATIVE_STREAM" :
        "SELECT CAST(42 AS INTEGER) FROM RDB$DATABASE";
    IMaster* master = fb_get_master_interface();
    IProvider* provider = master->getDispatcher();
    ThrowStatusWrapper status(master->getStatus());
    IAttachment* attachment = nullptr;
    ITransaction* transaction = nullptr;
    IStatement* first = nullptr;
    IStatement* second = nullptr;
    IResultSet* cursor = nullptr;
    IMessageMetadata* metadata = nullptr;
    int result = 0;
    try
    {
        attachment = provider->attachDatabase(&status, argv[1], 0, nullptr);
        first = attachment->prepare(&status, nullptr, 0, sql, SQL_DIALECT_V6, 0);
        second = attachment->prepare(&status, nullptr, 0, sql, SQL_DIALECT_V6, 0);
        if (fetch)
        {
            metadata = second->getOutputMetadata(&status);
            const unsigned char tpb[] = {isc_tpb_version3, isc_tpb_write, isc_tpb_read_committed,
                isc_tpb_read_consistency, isc_tpb_wait};
            transaction = attachment->startTransaction(&status, sizeof(tpb), tpb);
            cursor = second->openCursor(&status, transaction, nullptr, nullptr, metadata, 0);
            std::vector<unsigned char> buffer(metadata->getMessageLength(&status));
            if (cursor->fetchNext(&status, buffer.data()) != IStatus::RESULT_OK)
                throw std::runtime_error("native cursor returned no first row");
            ISC_LONG value;
            std::memcpy(&value, buffer.data() + metadata->getOffset(&status, 0), sizeof(value));
            if (value != 1) throw std::runtime_error("native first row differs");
            std::puts("FETCHED 1");
        }
        else std::puts("PREPARED 2");
        std::fflush(stdout);
        // The parent stops its own server before releasing these native handles.
        if (std::getchar() != 'q') throw std::runtime_error("parent did not complete shutdown");
    }
    catch (const FbException& exception)
    {
        char message[4096];
        master->getUtilInterface()->formatStatus(message, sizeof(message), exception.getStatus());
        std::fprintf(stderr, "ERROR %s\n", message);
        result = 1;
    }
    catch (const std::exception& exception)
    {
        std::fprintf(stderr, "ERROR %s\n", exception.what());
        result = 1;
    }
    CheckStatusWrapper cleanup(master->getStatus());
    if (cursor) { cursor->close(&cleanup); if (cleanup.getState() & IStatus::STATE_ERRORS) cursor->release(); }
    if (second) { second->free(&cleanup); if (cleanup.getState() & IStatus::STATE_ERRORS) second->release(); }
    if (first) { first->free(&cleanup); if (cleanup.getState() & IStatus::STATE_ERRORS) first->release(); }
    if (metadata) metadata->release();
    if (transaction) { transaction->rollback(&cleanup); if (cleanup.getState() & IStatus::STATE_ERRORS) transaction->release(); }
    if (attachment) { attachment->detach(&cleanup); if (cleanup.getState() & IStatus::STATE_ERRORS) attachment->release(); }
    provider->release();
    cleanup.dispose(); status.dispose();
    return result;
}
