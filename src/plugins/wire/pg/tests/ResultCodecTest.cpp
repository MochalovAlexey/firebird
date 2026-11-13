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

#include "firebird.h"
#include "../ResultCodec.h"
#include "../common/status.h"

#include <cstdio>
#include <algorithm>
#include <cstring>
#include <limits>
#include <stdexcept>

using namespace Firebird;
using namespace Firebird::PgWire;

namespace
{

void require(bool condition, const char* message)
{
	if (!condition)
		throw std::runtime_error(message);
}

template <typename F> void expectError(F action, const char* expected)
{
	try
	{
		action();
	}
	catch (const ProtocolError& error)
	{
		require(std::strcmp(error.sqlState(), expected) == 0, "Unexpected result codec SQLSTATE");
		return;
	}
	throw std::runtime_error("Expected a result codec error");
}

struct Spec
{
	unsigned type, length;
	int scale = 0, subtype = 0;
	unsigned charset = 0;
	const char* name = "value";
};

RefPtr<IMessageMetadata> describe(const std::vector<Spec>& specs)
{
	FbLocalStatus status;
	RefPtr<IMetadataBuilder> builder(REF_NO_INCR,
		fb_get_master_interface()->getMetadataBuilder(&status, specs.size()));
	check(&status);
	for (unsigned i = 0; i < specs.size(); ++i)
	{
		const auto& spec = specs[i];
		builder->setType(&status, i, spec.type);
		check(&status);
		builder->setLength(&status, i, spec.length);
		check(&status);
		builder->setScale(&status, i, spec.scale);
		check(&status);
		builder->setSubType(&status, i, spec.subtype);
		check(&status);
		builder->setCharSet(&status, i, spec.charset);
		check(&status);
		builder->setAlias(&status, i, spec.name);
		check(&status);
	}
	RefPtr<IMessageMetadata> metadata(REF_NO_INCR, builder->getMetadata(&status));
	check(&status);
	return metadata;
}

unsigned offset(const ResultCodec& codec, unsigned field, bool nullIndicator = false)
{
	FbLocalStatus status;
	const auto result = nullIndicator ? codec.fetchMetadata()->getNullOffset(&status, field) :
		codec.fetchMetadata()->getOffset(&status, field);
	check(&status);
	return result;
}

template <typename T> void store(std::vector<unsigned char>& buffer, unsigned position, T value)
{
	require(position <= buffer.size() && sizeof(value) <= buffer.size() - position,
		"Invalid test buffer position");
	std::memcpy(buffer.data() + position, &value, sizeof(value));
}

void storeText(const ResultCodec& codec, std::vector<unsigned char>& buffer,
	unsigned field, std::string_view text)
{
	const auto position = offset(codec, field);
	store<ISC_USHORT>(buffer, position, text.size());
	require(position + sizeof(ISC_USHORT) + text.size() <= buffer.size(), "Invalid test text position");
	std::memcpy(buffer.data() + position + sizeof(ISC_USHORT), text.data(), text.size());
}

void verifyIntegerBoundaries()
{
	auto metadata = describe({{SQL_SHORT, 2}, {SQL_LONG, 4}, {SQL_INT64, 8}});
	ResultCodec codec(metadata);
	require(codec.columns().size() == 3 && codec.columns()[0].oid == 21 &&
		codec.columns()[1].oid == 23 && codec.columns()[2].oid == 20, "Incorrect integer OIDs");
	require(codec.columns()[0].typeSize == 2 && codec.columns()[1].typeSize == 4 &&
		codec.columns()[2].typeSize == 8, "Incorrect integer type sizes");
	std::vector<unsigned char> buffer(codec.messageLength());
	store<ISC_SHORT>(buffer, offset(codec, 0), std::numeric_limits<ISC_SHORT>::min());
	store<ISC_LONG>(buffer, offset(codec, 1), std::numeric_limits<ISC_LONG>::min());
	store<ISC_INT64>(buffer, offset(codec, 2), std::numeric_limits<ISC_INT64>::min());
	auto values = codec.decode(buffer.data(), buffer.size());
	require(values[0] == "-32768" && values[1] == "-2147483648" &&
		values[2] == "-9223372036854775808", "Incorrect minimum integer values");
	store<ISC_SHORT>(buffer, offset(codec, 0), std::numeric_limits<ISC_SHORT>::max());
	store<ISC_LONG>(buffer, offset(codec, 1), std::numeric_limits<ISC_LONG>::max());
	store<ISC_INT64>(buffer, offset(codec, 2), std::numeric_limits<ISC_INT64>::max());
	values = codec.decode(buffer.data(), buffer.size());
	require(values[0] == "32767" && values[1] == "2147483647" &&
		values[2] == "9223372036854775807", "Incorrect maximum integer values");
	for (unsigned i = 0; i < values.size(); ++i)
		store<ISC_SHORT>(buffer, offset(codec, i, true), -1);
	values = codec.decode(buffer.data(), buffer.size());
	require(!values[0] && !values[1] && !values[2], "NULL integer was decoded as a value");
}

void verifyExactDecimals()
{
	auto metadata = describe({{SQL_SHORT, 2, -3, 1}, {SQL_LONG, 4, 0, 2},
		{SQL_INT64, 8, -2, 1}, {SQL_INT64, 8, -18, 2}, {SQL_INT64, 8, -3, 0}});
	ResultCodec codec(metadata);
	for (const auto& column : codec.columns())
		require(column.oid == 1700 && column.typeSize == -1 && column.typeModifier == -1,
			"Exact numeric metadata was confused with integer metadata");
	std::vector<unsigned char> buffer(codec.messageLength());
	store<ISC_SHORT>(buffer, offset(codec, 0), -1);
	store<ISC_LONG>(buffer, offset(codec, 1), -2147483647);
	store<ISC_INT64>(buffer, offset(codec, 2), std::numeric_limits<ISC_INT64>::min());
	store<ISC_INT64>(buffer, offset(codec, 3), 0);
	store<ISC_INT64>(buffer, offset(codec, 4), 12000);
	auto values = codec.decode(buffer.data(), buffer.size());
	require(values[0] == "-0.001" && values[1] == "-2147483647" &&
		values[2] == "-92233720368547758.08" && values[3] == "0.000000000000000000" &&
		values[4] == "12.000", "Exact numeric text lost sign, scale or precision");
	store<ISC_INT64>(buffer, offset(codec, 3), std::numeric_limits<ISC_INT64>::max());
	values = codec.decode(buffer.data(), buffer.size());
	require(values[3] == "9.223372036854775807", "Maximum scaled integer lost precision");
	store<ISC_SHORT>(buffer, offset(codec, 2, true), -1);
	require(!codec.decode(buffer.data(), buffer.size())[2], "NULL decimal became a number");
}

void verifyBoolean()
{
	auto metadata = describe({{SQL_BOOLEAN, 1}});
	ResultCodec codec(metadata);
	require(codec.columns()[0].oid == 16 && codec.columns()[0].typeSize == 1,
		"Incorrect boolean metadata");
	std::vector<unsigned char> buffer(codec.messageLength());
	store<FB_BOOLEAN>(buffer, offset(codec, 0), FB_TRUE);
	require(codec.decode(buffer.data(), buffer.size())[0] == "t", "Incorrect true text");
	store<FB_BOOLEAN>(buffer, offset(codec, 0), FB_FALSE);
	require(codec.decode(buffer.data(), buffer.size())[0] == "f", "Incorrect false text");
	store<FB_BOOLEAN>(buffer, offset(codec, 0), 2);
	expectError([&] { codec.decode(buffer.data(), buffer.size()); }, "XX000");
	store<ISC_SHORT>(buffer, offset(codec, 0, true), -1);
	require(!codec.decode(buffer.data(), buffer.size())[0], "NULL boolean read invalid payload");
}

void verifyText()
{
	auto metadata = describe({{SQL_TEXT, 16, 0, 0, 4, "Mixed Case Я"},
		{SQL_VARYING, 16, 0, 0, 4}, {SQL_TEXT, 4}, {SQL_VARYING, 8, 0, 0, 2}});
	ResultCodec codec(metadata);
	// Codec owns its fetch metadata independently of the supplied descriptor.
	metadata = nullptr;
	FbLocalStatus status;
	require(codec.fetchMetadata()->getType(&status, 0) == SQL_VARYING,
		"CHAR fetch did not request a varying buffer");
	check(&status);
	require(codec.columns()[0].oid == 1042 && codec.columns()[1].oid == 1043 &&
		codec.columns()[0].typeSize == -1 && codec.columns()[0].typeModifier == -1 &&
		codec.columns()[0].name == "Mixed Case Я", "Logical CHAR or alias metadata was changed");
	std::vector<unsigned char> buffer(codec.messageLength());
	storeText(codec, buffer, 0, "Я   ");
	storeText(codec, buffer, 1, "A ");
	storeText(codec, buffer, 2, "    ");
	storeText(codec, buffer, 3, "\t\n");
	auto values = codec.decode(buffer.data(), buffer.size());
	require(values[0] == "Я   " && values[1] == "A " && values[2] == "    " && values[3] == "\t\n",
		"Text length, trailing spaces or control characters were changed");
	storeText(codec, buffer, 1, "");
	store<ISC_SHORT>(buffer, offset(codec, 2, true), -1);
	values = codec.decode(buffer.data(), buffer.size());
	require(values[1] && values[1]->empty() && !values[2], "Empty text was confused with NULL");
	for (const auto& invalid : {std::string("a\0b", 3), std::string("\xC0\x80", 2),
		std::string("\xED\xA0\x80", 3), std::string("\xF4\x90\x80\x80", 4)})
	{
		storeText(codec, buffer, 1, invalid);
		expectError([&] { codec.decode(buffer.data(), buffer.size()); }, "22021");
	}
	store<ISC_USHORT>(buffer, offset(codec, 1), 17);
	expectError([&] { codec.decode(buffer.data(), buffer.size()); }, "XX000");
	store<ISC_SHORT>(buffer, offset(codec, 1, true), -1);
	require(!codec.decode(buffer.data(), buffer.size())[1], "NULL varying read invalid length");
	expectError([&] { codec.decode(buffer.data(), buffer.size() - 1); }, "XX000");
	expectError([&] { codec.decode(nullptr, buffer.size()); }, "XX000");
}

void verifyMetadataRejection()
{
	const Spec unsupported[] = {
		{SQL_BLOB, 8, 0, 1}, {SQL_BLOB, 8, 0, 2}, {SQL_BLOB, 8, 1},
		{SQL_DEC16, 8}, {SQL_INT128, 16, -39, 1},
		{SQL_DOUBLE, 8, -2, 1}, {SQL_TYPE_DATE, 4, -1},
		{SQL_INT64, 8, -19, 1}, {SQL_INT64, 8, 1, 1}, {SQL_LONG, 4, 0, 3},
		{SQL_BOOLEAN, 1, -1}, {SQL_BOOLEAN, 1, 0, 1},
		{SQL_TEXT, 16, 0, 0, 1}, {SQL_TEXT, 16, 0, 0, 21},
		{SQL_VARYING, 16, -1, 0, 4}, {SQL_VARYING, 65534, 0, 0, 4}
	};
	for (const auto& spec : unsupported)
	{
		auto metadata = describe({spec});
		expectError([&] { ResultCodec codec(metadata); }, "0A000");
	}
	for (const auto& spec : {Spec{SQL_LONG, 2}, Spec{SQL_BOOLEAN, 2}, Spec{SQL_BLOB, 4}})
	{
		auto metadata = describe({spec});
		expectError([&] { ResultCodec codec(metadata); }, "XX000");
	}
	std::vector<Spec> many(32768, {SQL_SHORT, 2});
	auto metadata = describe(many);
	expectError([&] { ResultCodec codec(metadata); }, "0A000");
	metadata = describe(std::vector<Spec>(17, {SQL_VARYING, 65532, 0, 0, 4}));
	expectError([&] { ResultCodec codec(metadata); }, "0A000");
	expectError([] { ResultCodec codec(nullptr); }, "XX000");
	metadata = describe({});
	ResultCodec empty(metadata);
	require(empty.messageLength() == 0 && empty.columns().empty() && empty.decode(nullptr, 0).empty(),
		"Empty output metadata was not supported");
}

void verifyBinaryFormats()
{
	auto metadata = describe({{SQL_SHORT, 2}, {SQL_LONG, 4}, {SQL_INT64, 8},
		{SQL_BOOLEAN, 1}, {SQL_VARYING, 16, 0, 0, 4}});
	ResultCodec codec(metadata, {1});
	std::vector<unsigned char> buffer(codec.messageLength());
	store<ISC_SHORT>(buffer, offset(codec, 0), -32768);
	store<ISC_LONG>(buffer, offset(codec, 1), -1);
	store<ISC_INT64>(buffer, offset(codec, 2), std::numeric_limits<ISC_INT64>::min());
	store<FB_BOOLEAN>(buffer, offset(codec, 3), FB_FALSE);
	storeText(codec, buffer, 4, "Я ");
	const auto values = codec.decode(buffer.data(), buffer.size());
	require(values[0] == std::string("\x80\0", 2) && values[1] == std::string(4, '\xff') &&
		values[2] == std::string("\x80\0\0\0\0\0\0\0", 8) &&
		values[3] == std::string(1, '\0') && values[4] == "Я ", "Incorrect binary result bytes");
	for (const auto& column : codec.columns())
		require(column.format == 1, "Incorrect binary column description");
	ResultCodec mixed(metadata, {0, 1, 0, 1, 0});
	const auto mixedValues = mixed.decode(buffer.data(), buffer.size());
	require(mixedValues[0] == "-32768" && mixedValues[1] == values[1] &&
		mixedValues[2] == "-9223372036854775808" && mixedValues[3] == values[3] &&
		mixedValues[4] == values[4], "Mixed result formats were ignored");
	store<ISC_SHORT>(buffer, offset(codec, 3, true), -1);
	require(!codec.decode(buffer.data(), buffer.size())[3], "Binary NULL became false");
	expectError([&] { ResultCodec invalid(metadata, {0, 1}); }, "08P01");
	expectError([&] { ResultCodec invalid(metadata, {2}); }, "0A000");
	metadata = describe({{SQL_INT64, 8, -2, 1}});
	ResultCodec numeric(metadata, {1});
	buffer.assign(numeric.messageLength(), 0);
	store<ISC_INT64>(buffer, offset(numeric, 0), -12345);
	require(numeric.decode(buffer.data(), buffer.size())[0] == std::string("\0\2\0\0\x40\0\0\2\0\x7b\x11\x94", 12),
		"Binary numeric result did not use base 10000 groups");
}

// Minimal ABI endpoints, as in SessionTest. Unused calls remain unset.
// They exercise consuming close and error cleanup without a live attachment.
struct Blob : IBlob
{
	Blob() : IBlob(DoNotInherit())
	{
		table.version = VERSION;
		table.release = [](IReferenceCounted* self) noexcept
		{
			auto& b = *static_cast<Blob*>(self);
			++b.releases; b.open = false;
			return 0;
		};
		table.getSegment = [](IBlob* self, IStatus* status, unsigned capacity, void* buffer,
			unsigned* length) noexcept
		{
			auto& b = *static_cast<Blob*>(self);
			++b.reads; *length = 0;
			if (b.failRead)
			{
				const ISC_STATUS error[] = {isc_arg_gds, isc_io_error, isc_arg_end};
				status->setErrors(error);
				return IStatus::RESULT_ERROR;
			}
			if (b.badResult) return 999;
			if (b.badLength) { *length = capacity + 1; return IStatus::RESULT_OK; }
			if (b.segment == b.segments.size()) return IStatus::RESULT_NO_DATA;
			const auto& data = b.segments[b.segment];
			*length = std::min<std::size_t>(capacity, data.size() - b.position);
			std::memcpy(buffer, data.data() + b.position, *length);
			b.position += *length; b.bytesRead += *length;
			if (b.position < data.size()) return IStatus::RESULT_SEGMENT;
			++b.segment; b.position = 0;
			return IStatus::RESULT_OK;
		};
		table.close = [](IBlob* self, IStatus* status) noexcept
		{
			auto& b = *static_cast<Blob*>(self);
			++b.closes;
			if (b.failClose)
			{
				const ISC_STATUS error[] = {isc_arg_gds, isc_bad_segstr_handle, isc_arg_end};
				status->setErrors(error);
			}
			else b.open = false;
		};
		cloopVTable = &table;
	}
	VTable table{};
	std::vector<std::string> segments;
	std::size_t segment = 0, position = 0, bytesRead = 0;
	unsigned reads = 0, closes = 0, releases = 0;
	bool open = false, failRead = false, failClose = false, badResult = false, badLength = false;
};

struct Transaction : ITransaction
{
	Transaction() : ITransaction(DoNotInherit()) {}
};

struct Attachment : IAttachment
{
	Attachment() : IAttachment(DoNotInherit())
	{
		table.version = VERSION;
		table.openBlob = [](IAttachment* self, IStatus* status, ITransaction* transaction,
			ISC_QUAD* id, unsigned bpbLength, const unsigned char*) noexcept -> IBlob*
		{
			auto& a = *static_cast<Attachment*>(self);
			++a.opens;
			a.wrongContext |= transaction != a.expected || bpbLength || id->gds_quad_high || id->gds_quad_low > 1;
			if (a.failOpen)
			{
				const ISC_STATUS error[] = {isc_arg_gds, isc_bad_segstr_id, isc_arg_end};
				status->setErrors(error);
				return nullptr;
			}
			auto& b = a.blobs[id->gds_quad_low % 2];
			b.segment = b.position = 0; b.open = true;
			return &b;
		};
		cloopVTable = &table;
	}
	VTable table{};
	Blob blobs[2];
	ITransaction* expected = nullptr;
	unsigned opens = 0;
	bool wrongContext = false, failOpen = false;
};

struct Connection : IWireConnection
{
	Connection() : IWireConnection(DoNotInherit())
	{
		table.version = VERSION;
		table.write = [](IWireConnection* self, IStatus*, unsigned size, const void* bytes) noexcept
		{
			auto& c = *static_cast<Connection*>(self);
			c.output.append(static_cast<const char*>(bytes), size);
			return size;
		};
		cloopVTable = &table;
	}
	VTable table{};
	std::string output;
};

void verifyBlobs()
{
	auto metadata = describe({{SQL_BLOB, sizeof(ISC_QUAD)}});
	Transaction transaction;
	for (const std::int16_t format : {0, 1})
	{
		ResultCodec codec(metadata, {format});
		require(codec.columns()[0].oid == 17 && codec.columns()[0].typeSize == -1 &&
			codec.columns()[0].typeModifier == -1, "Incorrect bytea metadata");
		std::vector<unsigned char> buffer(codec.messageLength()); // ID zero is not NULL.
		Attachment attachment; attachment.expected = &transaction;
		auto& blob = attachment.blobs[0];
		blob.segments = {"", std::string("\0\x5c\x27\xff\x80", 5), ""};
		Limits limits; limits.frameBytes = format ? 15 : 22;
		Connection connection;
		Protocol protocol(fb_get_master_interface(), &connection, limits);
		auto values = codec.decode(buffer.data(), buffer.size(), protocol.frameLimit(), &attachment, &transaction);
		require(values[0] == (format ? blob.segments[1] : "\\x005c27ff80"), "BLOB bytes or segment boundaries changed");
		require(blob.closes == 1 && !blob.releases && !blob.open && blob.reads == 4 &&
			!attachment.wrongContext, "BLOB close/EOF/context contract differs");
		protocol.dataRow(values, {format});
		require(connection.output.size() == limits.frameBytes + 1 && connection.output[0] == 'D',
			"Exact DataRow boundary differs");
		limits.frameBytes -= 1;
		Connection tooSmall;
		Protocol smallProtocol(fb_get_master_interface(), &tooSmall, limits);
		expectError([&] { smallProtocol.dataRow(codec.decode(buffer.data(), buffer.size(),
			smallProtocol.frameLimit(), &attachment, &transaction), {format}); }, "0A000");
		require(tooSmall.output.empty() && blob.releases == 1 && !blob.open, "Overflow leaked a BLOB or partial DataRow");
		// Preserve the serializer's independent preflight as well.
		expectError([&] { smallProtocol.dataRow(values, {format}); }, "0A000");
		require(tooSmall.output.empty(), "Serializer overflow wrote a partial row");
		blob.segments = {blob.segments[1], "x"};
		const auto reads = blob.reads;
		expectError([&] { codec.decode(buffer.data(), buffer.size(), protocol.frameLimit(),
			&attachment, &transaction); }, "0A000");
		require(blob.reads == reads + 2 && blob.releases == 2 && !blob.open,
			"Full budget was mistaken for EOF before a later nonempty segment");
		blob.segments.clear();
		values = codec.decode(buffer.data(), buffer.size(), 12, &attachment, &transaction);
		require(values[0] && *values[0] == (format ? "" : "\\x"), "Empty BLOB became NULL");
		expectError([&] { codec.decode(buffer.data(), buffer.size()); }, "XX000");
		expectError([&] { codec.decode(buffer.data(), buffer.size(), 20, &attachment); }, "XX000");
		const auto opened = attachment.opens;
		store<ISC_SHORT>(buffer, offset(codec, 0, true), -1);
		require(!codec.decode(buffer.data(), buffer.size(), 10)[0] && attachment.opens == opened,
			"NULL BLOB required context or opened a handle");
		expectError([&] { codec.decode(buffer.data(), buffer.size(), 9); }, "0A000");
	}
	ResultCodec binary(metadata, {1});
	std::vector<unsigned char> buffer(binary.messageLength());
	Attachment attachment; attachment.expected = &transaction;
	auto& blob = attachment.blobs[0];
	blob.segments = {std::string(20000, 'x')};
	expectError([&] { binary.decode(buffer.data(), buffer.size(), 12, &attachment, &transaction); }, "0A000");
	require(blob.reads == 1 && blob.bytesRead <= 4096 && blob.releases == 1 && !blob.open,
		"Oversize BLOB was read unboundedly or leaked");
	const auto values = binary.decode(buffer.data(), buffer.size(), 20010, &attachment, &transaction);
	require(values[0] == blob.segments[0] && blob.closes == 1 && !blob.open, "RESULT_SEGMENT truncated BLOB");
	Transaction second;
	attachment.expected = &second;
	binary.decode(buffer.data(), buffer.size(), 20010, &attachment, &second);
	require(!attachment.wrongContext, "Codec retained a prior transaction");

	for (unsigned failure = 0; failure < 5; ++failure)
	{
		Attachment failing; failing.expected = &transaction;
		failing.failOpen = failure == 0;
		failing.blobs[0].failRead = failure == 1;
		failing.blobs[0].failClose = failure == 2;
		failing.blobs[0].badResult = failure == 3;
		failing.blobs[0].badLength = failure == 4;
		if (failure >= 3)
			expectError([&] { binary.decode(buffer.data(), buffer.size(), 20, &failing, &transaction); }, "XX000");
		else
		{
			bool caught = false;
			try { binary.decode(buffer.data(), buffer.size(), 20, &failing, &transaction); }
			catch (const status_exception& ex)
			{
				FbLocalStatus status; ex.stuffException(&status);
				const auto code = failure == 0 ? isc_bad_segstr_id : failure == 1 ? isc_io_error : isc_bad_segstr_handle;
				require(status->getErrors()[1] == code, "BLOB cleanup replaced the original engine error");
				caught = true;
			}
			require(caught, "Engine BLOB error became success");
		}
		require(!failing.blobs[0].open && failing.blobs[0].releases == (failure ? 1u : 0u),
			"Failed BLOB operation leaked or released twice");
	}
	// One shared budget, with overhead for NULLs and payload for later scalar fields.
	metadata = describe({{SQL_BLOB, 8}, {SQL_LONG, 4}, {SQL_BLOB, 8}, {SQL_BLOB, 8}});
	ResultCodec mixed(metadata, {0, 0, 1, 1});
	buffer.assign(mixed.messageLength(), 0);
	store<ISC_LONG>(buffer, offset(mixed, 1), 7);
	store<ISC_QUAD>(buffer, offset(mixed, 2), ISC_QUAD{0, 1});
	store<ISC_SHORT>(buffer, offset(mixed, 3, true), -1);
	Attachment multi; multi.expected = &transaction;
	multi.blobs[0].segments = {std::string("\0ab", 3)};
	multi.blobs[1].segments = {std::string("\0\xff", 2)};
	const auto row = mixed.decode(buffer.data(), buffer.size(), 33, &multi, &transaction);
	require(row[0] == "\\x006162" && row[1] == "7" && row[2] == multi.blobs[1].segments[0] && !row[3],
		"Mixed BLOB/scalar/NULL row differs");
	expectError([&] { mixed.decode(buffer.data(), buffer.size(), 32, &multi, &transaction); }, "0A000");
	require(multi.blobs[0].closes == 2 && multi.blobs[1].closes == 1 && multi.blobs[1].releases == 1,
		"Combined row budget did not clean up both BLOBs");
	metadata = describe({{SQL_BLOB, 8}, {SQL_LONG, 4}});
	ResultCodec scalarLast(metadata);
	buffer.assign(scalarLast.messageLength(), 0);
	store<ISC_LONG>(buffer, offset(scalarLast, 1), 42);
	expectError([&] { scalarLast.decode(buffer.data(), buffer.size(), 23, &multi, &transaction); }, "0A000");
	require(multi.blobs[0].closes == 3 && !multi.blobs[0].open,
		"A scalar after the BLOB escaped the shared row budget");
}

} // namespace

int main()
{
	try
	{
		verifyIntegerBoundaries();
		verifyExactDecimals();
		verifyBoolean();
		verifyText();
		verifyMetadataRejection();
		verifyBinaryFormats();
		verifyBlobs();
		std::puts("PgWire result codec tests passed");
		return 0;
	}
	catch (const std::exception& error)
	{
		std::fprintf(stderr, "PgWire result codec tests failed: %s\n", error.what());
	}
	catch (...)
	{
		std::fputs("PgWire result codec tests failed: unexpected exception\n", stderr);
	}
	return 1;
}
