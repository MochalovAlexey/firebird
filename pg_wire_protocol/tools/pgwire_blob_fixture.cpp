// Native binary BLOB fixture and independent readback for the private 1c harness.
#include <firebird/Interface.h>
#include <firebird/Message.h>
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace Firebird;

static void require(bool ok, const char* reason)
{
	if (!ok) throw std::runtime_error(reason);
}

static std::string payload(unsigned id)
{
	if (id <= 1) return {};
	if (id == 3) return std::string("\0\x5c\x27\xff\xc0\x80\xed\xa0\x80\xf4\x90\x80\x80", 13);
	const unsigned sizes[] = {0,0,256,13,10000,10000,65537,1048566,1048567,524282,524283,2097169,262140,524270};
	std::string value(sizes[id], '\0');
	for (unsigned i = 0; i < value.size(); ++i)
		value[i] = static_cast<unsigned char>(id == 2 ? i : i * 37 + 11);
	return value;
}

// Each manifest line is a SELECT returning one BLOB, a tab, and expected hex or NULL.
// This path reads input written by libpq using a new native attachment/transaction.
static unsigned readExpected(IMaster* master, ThrowStatusWrapper& status, IAttachment* attachment,
	ITransaction* transaction, IBlob*& blob, const char* path)
{
	std::ifstream manifest(path);
	require(bool(manifest), "Cannot read BLOB manifest");
	FB_MESSAGE(Value, ThrowStatusWrapper, (FB_BLOB, b)) value(&status, master);
	unsigned count = 0;
	std::string line;
	while (std::getline(manifest, line))
	{
		const auto tab = line.find('\t');
		require(tab != std::string::npos, "Invalid BLOB manifest");
		const auto sql = line.substr(0, tab), expected = line.substr(tab + 1);
		value.clear();
		attachment->execute(&status, transaction, sql.size(), sql.data(), 3,
			nullptr, nullptr, value.getMetadata(), value.getData());
		require(bool(value->bNull) == (expected == "NULL"), "Persisted BLOB NULL differs");
		if (!value->bNull)
		{
			blob = attachment->openBlob(&status, transaction, &value->b, 0, nullptr);
			std::string actual;
			unsigned char bytes[701];
			for (;;)
			{
				unsigned length = 0;
				const auto code = blob->getSegment(&status, sizeof(bytes), bytes, &length);
				if (code == IStatus::RESULT_NO_DATA) break;
				require(code == IStatus::RESULT_OK || code == IStatus::RESULT_SEGMENT, "Native BLOB read failed");
				for (unsigned i = 0; i < length; ++i)
				{
					actual += "0123456789abcdef"[bytes[i] >> 4];
					actual += "0123456789abcdef"[bytes[i] & 15];
				}
			}
			blob->close(&status); blob = nullptr;
			require(actual == expected, "Persisted BLOB bytes differ");
		}
		++count;
	}
	return count;
}

int main(int argc, char** argv)
{
	const bool readOnly = argc == 4 && std::strcmp(argv[2], "--read") == 0;
	if (argc != 2 && !readOnly) return 2;
	auto master = fb_get_master_interface();
	auto provider = master->getDispatcher();
	ThrowStatusWrapper status(master->getStatus());
	CheckStatusWrapper cleanup(master->getStatus());
	IAttachment* attachment = nullptr;
	ITransaction* transaction = nullptr;
	IBlob* blob = nullptr;
	int result = 0;
	try
	{
		const auto attach = [&]()
		{
			auto dpb = master->getUtilInterface()->getXpbBuilder(&status, IXpbBuilder::DPB, nullptr, 0);
			dpb->insertString(&status, isc_dpb_user_name, readOnly ? std::getenv("ISC_USER") : "SYSDBA");
			dpb->insertString(&status, isc_dpb_lc_ctype, "UTF8");
			if (readOnly)
				dpb->insertString(&status, isc_dpb_password, std::getenv("ISC_PASSWORD"));
			else
				dpb->insertString(&status, isc_dpb_config, "Providers=Engine14\n");
			attachment = provider->attachDatabase(&status, argv[1], dpb->getBufferLength(&status), dpb->getBuffer(&status));
			dpb->dispose();
		};
		attach();
		transaction = attachment->startTransaction(&status, 0, nullptr);
		if (readOnly)
		{
			const auto count = readExpected(master, status, attachment, transaction, blob, argv[3]);
			transaction->rollback(&status); transaction = nullptr;
			attachment->detach(&status); attachment = nullptr;
			std::printf("{\"passed\":true,\"readback_rows\":%u}\n", count);
		}
		else
		{
			FB_MESSAGE(Message, ThrowStatusWrapper, (FB_INTEGER, id) (FB_BLOB, b)) message(&status, master);
			for (unsigned id = 0; id < 14; ++id)
			{
				message.clear(); message->id = id;
				if (!id) message->bNull = -1;
				else
				{
					const bool stream = id == 5;
					const unsigned char bpb[] = {isc_bpb_version1, isc_bpb_type, 1,
						static_cast<unsigned char>(stream ? isc_bpb_type_stream : isc_bpb_type_segmented)};
					blob = attachment->createBlob(&status, transaction, &message->b, sizeof(bpb), bpb);
					const auto value = payload(id);
					if (id != 1)
					{
						blob->putSegment(&status, 0, value.data());
						std::size_t offset = 0;
						for (unsigned size : {13u, 7000u, 0u})
						{
							const unsigned length = std::min<std::size_t>(size, value.size() - offset);
							blob->putSegment(&status, length, value.data() + offset);
							offset += length;
						}
						while (offset < value.size())
						{
							const unsigned length = std::min<std::size_t>(32000, value.size() - offset);
							blob->putSegment(&status, length, value.data() + offset);
							offset += length;
						}
						blob->putSegment(&status, 0, value.data());
					}
					blob->close(&status); blob = nullptr;
				}
				attachment->execute(&status, transaction, 0, "INSERT INTO BLOB_ROWS(ID,B) VALUES(?,?)", 3,
					message.getMetadata(), message.getData(), nullptr, nullptr);
			}
			transaction->commit(&status); transaction = nullptr;
			attachment->detach(&status); attachment = nullptr;
			// A new attachment/transaction reads persisted IDs, not the creation handles.
			attach(); transaction = attachment->startTransaction(&status, 0, nullptr);
			unsigned reads = 0, partialSegments = 0;
			for (unsigned id = 0; id < 14; ++id)
			{
				message.clear();
				const auto sql = "SELECT ID,B FROM BLOB_ROWS WHERE ID=" + std::to_string(id);
				attachment->execute(&status, transaction, sql.size(), sql.data(), 3,
					nullptr, nullptr, message.getMetadata(), message.getData());
				require(message->id == static_cast<int>(id), "Fixture ID mismatch");
				require(bool(message->bNull) == (id == 0), "Native NULL/empty BLOB mismatch");
				if (!id) continue;
				blob = attachment->openBlob(&status, transaction, &message->b, 0, nullptr);
				const unsigned char item[] = {isc_info_blob_type};
				unsigned char info[16]{};
				blob->getInfo(&status, sizeof(item), item, sizeof(info), info);
				require(info[0] == isc_info_blob_type && info[1] == 1 && !info[2] && info[3] == (id == 5),
					"Native stream/segmented type mismatch");
				std::string actual;
				char buffer[701];
				for (;;)
				{
					unsigned length = 0;
					const auto code = blob->getSegment(&status, sizeof(buffer), buffer, &length);
					++reads;
					if (code == IStatus::RESULT_NO_DATA) break;
					require(code == IStatus::RESULT_OK || code == IStatus::RESULT_SEGMENT, "Native BLOB read failed");
					partialSegments += code == IStatus::RESULT_SEGMENT;
					actual.append(buffer, length);
				}
				require(actual == payload(id), "Persisted native BLOB bytes differ");
				blob->close(&status); blob = nullptr;
			}
			require(partialSegments > 0, "Fixture did not exercise partial segments");
			transaction->rollback(&status); transaction = nullptr;
			attachment->detach(&status); attachment = nullptr;
			std::printf("{\"passed\":true,\"rows\":14,\"reads\":%u,\"partial_segments\":%u,\"stream_id\":5}\n", reads, partialSegments);
		}
	}
	catch (const FbException& ex)
	{
		char buffer[1024]; master->getUtilInterface()->formatStatus(buffer, sizeof(buffer), ex.getStatus());
		std::fprintf(stderr, "%s\n", buffer); result = 1;
	}
	catch (const std::exception& ex)
	{
		std::fprintf(stderr, "%s\n", ex.what()); result = 1;
	}
	if (blob) blob->release();
	if (transaction)
	{
		transaction->rollback(&cleanup);
		if (cleanup.getState() & IStatus::STATE_ERRORS) transaction->release();
	}
	if (attachment)
	{
		attachment->detach(&cleanup);
		if (cleanup.getState() & IStatus::STATE_ERRORS) attachment->release();
	}
	provider->release(); status.dispose(); cleanup.dispose();
	return result;
}
