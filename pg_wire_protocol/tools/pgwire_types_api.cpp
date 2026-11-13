/*
 *  The contents of this file are subject to the Initial Developer's Public
 *  License Version 1.0 (the "License"); you may not use this file except in
 *  compliance with the License. You may obtain a copy of the License at
 *  http://www.ibphoenix.com/main.nfs?a=ibphoenix&page=ibp_idpl.
 *
 *  Software distributed under the License is distributed AS IS, WITHOUT
 *  WARRANTY OF ANY KIND, either express or implied.
 *
 *  Copyright (c) 2026 Firebird Project and all contributors signed below.
 *  All Rights Reserved.
 */

// APP-16 native OO/remote metadata and retained-index lifecycle acceptance.
// All relations and values are synthetic; run only against the private harness.
#include <firebird/Interface.h>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <vector>

using namespace Firebird;

int main(int argc, char** argv)
{
	if (argc != 2)
		return 2;
	auto master = fb_get_master_interface();
	auto provider = master->getDispatcher();
	ThrowStatusWrapper status(master->getStatus());
	IAttachment* a = nullptr;
	IAttachment* b = nullptr;
	ITransaction* transaction = nullptr;
	std::vector<IStatement*> held;
	unsigned assertions = 0;
	auto require = [&](bool value, const char* message) {
		if (!value) throw std::runtime_error(message);
		++assertions;
	};
	int exit = 1;
	try
	{
		auto dpb = master->getUtilInterface()->getXpbBuilder(&status, IXpbBuilder::DPB, nullptr, 0);
		dpb->insertString(&status, isc_dpb_user_name, std::getenv("ISC_USER"));
		dpb->insertString(&status, isc_dpb_password, std::getenv("ISC_PASSWORD"));
		dpb->insertString(&status, isc_dpb_lc_ctype, "UTF8");
		a = provider->attachDatabase(&status, argv[1], dpb->getBufferLength(&status), dpb->getBuffer(&status));
		b = provider->attachDatabase(&status, argv[1], dpb->getBufferLength(&status), dpb->getBuffer(&status));
		dpb->dispose();
		auto execute = [&](IAttachment* attachment, const char* sql, unsigned dialect = 4) {
			auto tr = attachment->startTransaction(&status, 0, nullptr);
			try
			{
				attachment->execute(&status, tr, 0, sql, dialect, nullptr, nullptr, nullptr, nullptr);
				tr->commit(&status);
			}
			catch (...) { tr->rollback(&status); throw; }
		};
		execute(a, "CREATE TABLE public.api_typed(id integer,b bytea,c mchar(3),v mvarchar(3),n regnamespace,q mchar)");
		execute(a, "INSERT INTO public.api_typed VALUES(1,CAST('a' AS bytea),'abc','abc','public',CAST('abc' AS mchar(3)))");
		transaction = a->startTransaction(&status, 0, nullptr);
		for (unsigned dialect : {3u, 4u})
		{
			auto statement = a->prepare(&status, transaction, 0,
				"SELECT \"b\",\"c\",\"v\",\"n\",\"q\" FROM \"public\".\"api_typed\"", dialect, 0);
			auto metadata = statement->getOutputMetadata(&status);
			require(metadata->getCount(&status) == 5, "Native column count");
			const unsigned types[] = {SQL_BLOB, SQL_TEXT, SQL_VARYING, SQL_INT64, SQL_BLOB};
			const int subtypes[] = {10, 0, 0, 3, 11};
			const unsigned lengths[] = {8, 12, 12, 8, 8};
			for (unsigned i = 0; i < 5; ++i)
			{
				require((metadata->getType(&status, i) & ~1u) == types[i], "Native physical type");
				require(metadata->getSubType(&status, i) == subtypes[i], "Native semantic subtype");
				require(metadata->getLength(&status, i) == lengths[i], "Native physical length");
				require(metadata->getScale(&status, i) == 0, "Native scale");
			}
			require(metadata->getCharSet(&status, 1) == 70 && metadata->getCharSet(&status, 2) == 71,
				"Native profile character sets");
			auto cursor = statement->openCursor(&status, transaction, nullptr, nullptr, metadata, 0);
			std::vector<unsigned char> row(metadata->getMessageLength(&status));
			require(cursor->fetchNext(&status, row.data()) == IStatus::RESULT_OK, "Native typed fetch");
			ISC_INT64 ns = 0;
			std::memcpy(&ns, row.data() + metadata->getOffset(&status, 3), sizeof(ns));
			require(ns > 0 && ns <= 0xffffffffLL, "Regnamespace remote BLR uint32 value");
			cursor->close(&status);
			metadata->release();
			statement->free(&status);
		}
		transaction->commit(&status);
		transaction = nullptr;
		execute(a, "CREATE INDEX public.api_typed_i ON public.api_typed(b)");
		transaction = a->startTransaction(&status, 0, nullptr);
		for (const char* sql : {
			"SELECT id FROM public.api_typed WHERE b>=CAST('' AS bytea) PLAN (api_typed INDEX (api_typed_i))",
			"SELECT id FROM public.api_typed PLAN (api_typed ORDER api_typed_i) ORDER BY b"})
		{
			held.push_back(a->prepare(&status, transaction, 0, sql, 4, 0));
		}
		transaction->commit(&status);
		transaction = nullptr;
		execute(b, "DROP INDEX public.api_typed_i");
		execute(b, "INSERT INTO public.api_typed(id,b) VALUES(2,CAST(LPAD('',10000,ASCII_CHAR(1)) AS BLOB SUB_TYPE 10))");
		for (auto statement : held)
		{
			transaction = a->startTransaction(&status, 0, nullptr);
			auto metadata = statement->getOutputMetadata(&status);
			IResultSet* cursor = nullptr;
			bool refused = false;
			try
			{
				cursor = statement->openCursor(&status, transaction, nullptr, nullptr, metadata, 0);
				std::vector<unsigned char> row(metadata->getMessageLength(&status));
				cursor->fetchNext(&status, row.data());
			}
			catch (const FbException& ex)
			{
				const auto errors = ex.getStatus()->getErrors();
				refused = errors[0] == isc_arg_gds && errors[1] == isc_old_format;
			}
			if (cursor) cursor->release();
			metadata->release();
			require(refused, "Retained dropped bytea index must request reprepare, not lose rows");
			transaction->rollback(&status);
			transaction = nullptr;
		}
		for (auto statement : held) statement->free(&status);
		held.clear();
		transaction = a->startTransaction(&status, 0, nullptr);
		auto statement = a->prepare(&status, transaction, 0,
			"SELECT b FROM public.api_typed WHERE id=2", 4, 0);
		auto metadata = statement->getOutputMetadata(&status);
		auto cursor = statement->openCursor(&status, transaction, nullptr, nullptr, metadata, 0);
		std::vector<unsigned char> row(metadata->getMessageLength(&status));
		require(cursor->fetchNext(&status, row.data()) == IStatus::RESULT_OK, "Fresh prepare sees unindexed payload");
		ISC_QUAD id;
		std::memcpy(&id, row.data() + metadata->getOffset(&status, 0), sizeof(id));
		auto blob = a->openBlob(&status, transaction, &id, 0, nullptr);
		unsigned total = 0, got = 0;
		unsigned char bytes[251];
		while (blob->getSegment(&status, sizeof(bytes), bytes, &got) != IStatus::RESULT_NO_DATA)
		{
			for (unsigned i = 0; i < got; ++i) require(bytes[i] == 1, "Native BLOB readback content");
			total += got;
		}
		require(total == 10000, "Native BLOB readback length");
		blob->close(&status);
		cursor->close(&status);
		metadata->release();
		statement->free(&status);
		transaction->rollback(&status);
		transaction = nullptr;
		// Native LTT BLOB/0 copy/read under a non-owner attachment. No persistent
		// security class exists for these attachment-owned relations.
		execute(a, "CREATE LOCAL TEMPORARY TABLE \"public\".BLOB_FROM(V BLOB SUB_TYPE 0) ON COMMIT PRESERVE ROWS", 3);
		execute(a, "CREATE LOCAL TEMPORARY TABLE \"public\".BLOB_TO(V BLOB SUB_TYPE 0) ON COMMIT PRESERVE ROWS", 3);
		execute(a, "INSERT INTO \"public\".BLOB_FROM VALUES(x'0100')", 3);
		execute(a, "INSERT INTO \"public\".BLOB_TO SELECT V FROM \"public\".BLOB_FROM", 3);
		transaction = a->startTransaction(&status, 0, nullptr);
		statement = a->prepare(&status, transaction, 0, "SELECT V FROM \"public\".BLOB_TO", 3, 0);
		metadata = statement->getOutputMetadata(&status);
		cursor = statement->openCursor(&status, transaction, nullptr, nullptr, metadata, 0);
		row.resize(metadata->getMessageLength(&status));
		require(cursor->fetchNext(&status, row.data()) == IStatus::RESULT_OK, "Native LTT BLOB copy fetch");
		std::memcpy(&id, row.data() + metadata->getOffset(&status, 0), sizeof(id));
		blob = a->openBlob(&status, transaction, &id, 0, nullptr);
		blob->getSegment(&status, sizeof(bytes), bytes, &got);
		require(got == 2 && bytes[0] == 1 && bytes[1] == 0, "Native LTT BLOB copy bytes");
		blob->close(&status);
		cursor->close(&status);
		metadata->release();
		statement->free(&status);
		transaction->rollback(&status);
		transaction = nullptr;
		a->detach(&status);
		a = nullptr;
		b->detach(&status);
		b = nullptr;
		exit = 0;
	}
	catch (const FbException& ex)
	{
		char text[8192];
		master->getUtilInterface()->formatStatus(text, sizeof(text), ex.getStatus());
		std::cerr << text << '\n';
	}
	catch (const std::exception& ex) { std::cerr << ex.what() << '\n'; }
	for (auto statement : held) statement->release();
	if (transaction) transaction->release();
	if (a) a->release();
	if (b) b->release();
	provider->release();
	status.dispose();
	std::cout << "{\"passed\":" << (exit ? "false" : "true") << ",\"assertions\":" << assertions << "}\n";
	return exit;
}
