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
#include "../InputCodec.h"
#include "../common/status.h"

#include <cstdio>
#include <cstring>
#include <limits>
#include <memory>
#include <stdexcept>

using namespace Firebird;
using namespace Firebird::PgWire;

namespace
{

unsigned checks = 0;

void require(bool condition, const char* message)
{
	++checks;
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
		if (std::strcmp(error.sqlState(), expected))
			throw std::runtime_error(std::string("Expected SQLSTATE ") + expected + ", got " + error.sqlState());
		++checks;
		return;
	}
	throw std::runtime_error("Expected an input codec error");
}

struct Spec
{
	unsigned type, length;
	int scale = 0, subtype = 0;
	unsigned charset = 0;
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
	}
	RefPtr<IMessageMetadata> metadata(REF_NO_INCR, builder->getMetadata(&status));
	check(&status);
	return metadata;
}

template <typename T> T value(const InputCodec& codec, const std::vector<unsigned char>& buffer,
	unsigned index, bool nullIndicator = false)
{
	FbLocalStatus status;
	const auto offset = nullIndicator ? codec.metadata()->getNullOffset(&status, index) :
		codec.metadata()->getOffset(&status, index);
	check(&status);
	require(offset <= buffer.size() && sizeof(T) <= buffer.size() - offset, "Test read exceeded native buffer");
	T result;
	std::memcpy(&result, buffer.data() + offset, sizeof(result));
	return result;
}

std::string textValue(const InputCodec& codec, const std::vector<unsigned char>& buffer, unsigned index)
{
	FbLocalStatus status;
	const auto offset = codec.metadata()->getOffset(&status, index);
	check(&status);
	const auto size = value<ISC_USHORT>(codec, buffer, index);
	require(offset + sizeof(ISC_USHORT) + size <= buffer.size(), "Test string exceeded native buffer");
	return std::string(reinterpret_cast<const char*>(buffer.data() + offset + sizeof(ISC_USHORT)), size);
}

void integers()
{
	auto metadata = describe({{SQL_SHORT, 2}, {SQL_LONG, 4}, {SQL_INT64, 8}});
	InputCodec codec(metadata, {21, 0});
	require(codec.parameterTypes() == std::vector<std::uint32_t>({21, 23, 20}), "Incorrect inferred integer OIDs");
	auto buffer = codec.encode({"-32768", "-2147483648", "-9223372036854775808"});
	require(value<ISC_SHORT>(codec, buffer, 0) == std::numeric_limits<ISC_SHORT>::min(), "Smallint minimum differs");
	require(value<ISC_LONG>(codec, buffer, 1) == std::numeric_limits<ISC_LONG>::min(), "Integer minimum differs");
	require(value<ISC_INT64>(codec, buffer, 2) == std::numeric_limits<ISC_INT64>::min(), "Bigint minimum differs");
	buffer = codec.encode({" +32767\t", "2147483647", "9223372036854775807"}, {0});
	require(value<ISC_SHORT>(codec, buffer, 0) == 32767 && value<ISC_LONG>(codec, buffer, 1) == 2147483647 &&
		value<ISC_INT64>(codec, buffer, 2) == std::numeric_limits<ISC_INT64>::max(), "Integer maximum differs");
	buffer = codec.encode({std::string("\x80\0", 2), std::string("\x80\0\0\0", 4),
		std::string("\x80\0\0\0\0\0\0\0", 8)}, {1});
	require(value<ISC_SHORT>(codec, buffer, 0) == -32768 && value<ISC_LONG>(codec, buffer, 1) ==
		std::numeric_limits<ISC_LONG>::min() && value<ISC_INT64>(codec, buffer, 2) ==
		std::numeric_limits<ISC_INT64>::min(), "Network signed integer decoding differs");
	buffer = codec.encode({"17", std::string("\0\0\x01\x02", 4), std::nullopt}, {0, 1, 1});
	require(value<ISC_SHORT>(codec, buffer, 0) == 17 && value<ISC_LONG>(codec, buffer, 1) == 258 &&
		value<ISC_SHORT>(codec, buffer, 2, true) == -1, "Mixed Bind formats or NULL differ");
	for (const auto& test : std::vector<std::pair<unsigned, std::string>>{{0, "32768"}, {0, "-32769"},
		{1, "2147483648"}, {1, "-2147483649"}, {2, "9223372036854775808"}, {2, "-9223372036854775809"}})
	{
		std::vector<std::optional<std::string>> values{"0", "0", "0"};
		values[test.first] = test.second;
		expectError([&] { codec.encode(values); }, "22003");
	}
	for (const auto text : {"", "+", "1.0", "1e0", "1 2", "0x10", "--1"})
		expectError([&] { codec.encode({text, "0", "0"}); }, "22P02");
	expectError([&] { codec.encode({"1", "0", "0"}, {1}); }, "22P03");
	expectError([&] { codec.encode({std::string("1\0", 2), "0", "0"}); }, "22021");
	expectError([&] { InputCodec other(metadata, {23}); }, "0A000");
	expectError([&] { InputCodec other(metadata, {21, 23, 20, 0}); }, "08P01");
	expectError([&] { codec.encode({"0"}); }, "08P01");
	expectError([&] { codec.encode({"0", "0", "0"}, {0, 0}); }, "08P01");
	expectError([&] { codec.encode({std::nullopt, std::nullopt, std::nullopt}, {2}); }, "08P01");
	expectError([&] { codec.encode({"0", "0", "0"}, {-1}); }, "08P01");
}

void decimals()
{
	auto metadata = describe({{SQL_SHORT, 2, -3, 1}, {SQL_LONG, 4, 0, 2}, {SQL_INT64, 8, -2, 1},
		{SQL_INT64, 8, -18, 0}});
	InputCodec codec(metadata);
	require(codec.parameterTypes() == std::vector<std::uint32_t>({1700, 1700, 1700, 1700}), "Numeric OIDs differ");
	auto buffer = codec.encode({"-32.76800", "2147483647.000", "-92233720368547758.08", "1e-18"});
	require(value<ISC_SHORT>(codec, buffer, 0) == -32768 && value<ISC_LONG>(codec, buffer, 1) == 2147483647 &&
		value<ISC_INT64>(codec, buffer, 2) == std::numeric_limits<ISC_INT64>::min() &&
		value<ISC_INT64>(codec, buffer, 3) == 1, "Exact scaled numeric encoding differs");
	buffer = codec.encode({"+.1250", "000123000.00e-3", "-0e999999999999999999999", "0.0000000000000000010"});
	require(value<ISC_SHORT>(codec, buffer, 0) == 125 && value<ISC_LONG>(codec, buffer, 1) == 123 &&
		value<ISC_INT64>(codec, buffer, 2) == 0 && value<ISC_INT64>(codec, buffer, 3) == 1,
		"Decimal normalization lost precision");
	for (const auto text : {"32.768", "0.0001", "1e1000000000", "1e-1000000000"})
		expectError([&] { codec.encode({text, "0", "0", "0"}); }, "22003");
	for (const auto text : {".", "1e", "1e+", "1.2.3", "1,25"})
		expectError([&] { codec.encode({text, "0", "0", "0"}); }, "22P02");
	for (const auto text : {"NaN", "Infinity", "-Infinity"})
		expectError([&] { codec.encode({text, "0", "0", "0"}); }, "22003");
	expectError([&] { codec.encode({"0", std::nullopt, std::nullopt, std::nullopt}, {1}); }, "22P03");
	buffer = codec.encode({std::string("\0\1\0\0\x40\0\0\3\0\x20", 10), std::nullopt, std::nullopt, std::nullopt}, {1});
	require(value<ISC_SHORT>(codec, buffer, 0) == -32000, "Binary numeric did not preserve native scale");
	buffer = codec.encode({std::nullopt, std::nullopt, std::nullopt, std::nullopt}, {1});
	require(value<ISC_SHORT>(codec, buffer, 0, true) == -1, "Binary NULL required a numeric payload decoder");
}

void booleans()
{
	auto metadata = describe({{SQL_BOOLEAN, 1}});
	InputCodec codec(metadata, {16});
	for (const auto text : {"t", " TRUE ", "Tr", "yes", "y", "ON", "1"})
		require(value<FB_BOOLEAN>(codec, codec.encode({text}), 0) == FB_TRUE, "True parameter differs");
	for (const auto text : {"f", "FALSE", "fa", "no", "n", "off", "of", "0"})
		require(value<FB_BOOLEAN>(codec, codec.encode({text}), 0) == FB_FALSE, "False parameter differs");
	for (const auto text : {"", "o", "2", "truth"})
		expectError([&] { codec.encode({text}); }, "22P02");
	for (const char byte : {char(0), char(1)})
		require(value<FB_BOOLEAN>(codec, codec.encode({std::string(1, byte)}, {1}), 0) == byte,
			"Binary boolean differs");
	for (const auto& bytes : {std::string(), std::string(1, '\2'), std::string("\0\1", 2)})
		expectError([&] { codec.encode({bytes}, {1}); }, "22P03");
}

void text()
{
	auto metadata = describe({{SQL_TEXT, 16, 0, 0, 4}, {SQL_VARYING, 16, 0, 0, 4},
		{SQL_TEXT, 4}, {SQL_VARYING, 8, 0, 0, 2}});
	InputCodec codec(metadata, {25, 1042, 1043});
	auto* originalMetadata = codec.metadata();
	metadata = nullptr;
	require(codec.parameterTypes() == std::vector<std::uint32_t>({25, 1042, 1043, 1043}),
		"Accepted explicit text OIDs were not retained");
	FbLocalStatus status;
	require(codec.metadata()->getType(&status, 0) == SQL_VARYING, "CHAR input did not use VARYING");
	check(&status);
	auto buffer = codec.encode({"Я  ", "", std::nullopt, "\tA\n"}, {1, 0, 1, 0});
	require(textValue(codec, buffer, 0) == "Я  " && textValue(codec, buffer, 1).empty() &&
		value<ISC_SHORT>(codec, buffer, 2, true) == -1 && textValue(codec, buffer, 3) == "\tA\n",
		"Input text, trailing spaces or NULL changed");
	for (const auto& invalid : {std::string("a\0b", 3), std::string("\xC0\x80", 2),
		std::string("\xED\xA0\x80", 3), std::string("\xF4\x90\x80\x80", 4)})
	{
		for (const std::int16_t format : {0, 1})
			expectError([&] { codec.encode({invalid, "", "", ""}, {format}); }, "22021");
	}
	expectError([&] { codec.encode({"", "", "", "Я"}); }, "22021");
	expectError([&] { codec.encode({std::string(17, 'a'), "", "", ""}); }, "22001");
	expectError([&] { codec.encode({"", "", "12345", ""}); }, "22001");
	buffer = codec.encode({"", "", "Я", ""});
	require(textValue(codec, buffer, 2) == "Я", "NONE input did not preserve UTF8 bytes");
	require(codec.metadata() == originalMetadata, "Scalar Bind unnecessarily rebuilt metadata");
}

void emptyProfileStrings()
{
	auto metadata = describe({{SQL_TEXT, 16, 0, 0, 70}, {SQL_VARYING, 16, 0, 0, 71}});
	InputCodec codec(metadata);
	for (const auto& bytes : {std::string(), std::string(1, '\xff')})
	{
		const auto buffer = codec.encode({bytes, bytes}, {1});
		for (unsigned i = 0; i < 2; ++i)
			require(textValue(codec, buffer, i).empty() && value<ISC_SHORT>(codec, buffer, i, true) == 0,
				"Empty UTF16 profile input must remain a non-NULL empty string");
	}
	const auto buffer = codec.encode({std::nullopt, std::nullopt}, {1});
	for (unsigned i = 0; i < 2; ++i)
		require(value<ISC_SHORT>(codec, buffer, i, true) == -1, "NULL profile input became empty");
}

// ABI probes exercise deferred creation, consuming close and partial-write cancellation.
struct WriteBlob : IBlob
{
	WriteBlob() : IBlob(DoNotInherit())
	{
		table.version = VERSION;
		table.release = [](IReferenceCounted* self) noexcept
			{ auto& b = *static_cast<WriteBlob*>(self); ++b.releases; b.open = false; return 0; };
		table.putSegment = [](IBlob* self, IStatus* status, unsigned size, const void* data) noexcept
		{
			auto& b = *static_cast<WriteBlob*>(self);
			if (b.failWrite)
			{
				const ISC_STATUS error[] = {isc_arg_gds, isc_io_error, isc_arg_end};
				status->setErrors(error); return;
			}
			b.bytes.append(static_cast<const char*>(data), size);
		};
		table.close = [](IBlob* self, IStatus*) noexcept
			{ auto& b = *static_cast<WriteBlob*>(self); ++b.closes; b.open = false; };
		table.cancel = [](IBlob* self, IStatus*) noexcept
			{ auto& b = *static_cast<WriteBlob*>(self); ++b.cancels; b.open = false; };
		cloopVTable = &table;
	}
	VTable table{};
	std::string bytes;
	unsigned closes = 0, cancels = 0, releases = 0;
	bool open = true, failWrite = false;
};
struct BlobTransaction : ITransaction
{
	BlobTransaction() : ITransaction(DoNotInherit()) {}
};
struct BlobWrites : IAttachment
{
	BlobWrites() : IAttachment(DoNotInherit())
	{
		table.version = VERSION;
		table.createBlob = [](IAttachment* self, IStatus*, ITransaction* transaction,
			ISC_QUAD* id, unsigned bpbLength, const unsigned char*) noexcept -> IBlob*
		{
			auto& a = *static_cast<BlobWrites*>(self);
			a.wrongContext |= transaction != &a.transaction || bpbLength != 0;
			a.blobs.push_back(std::make_unique<WriteBlob>());
			a.blobs.back()->failWrite = a.blobs.size() == a.failAt;
			*id = {0, static_cast<ISC_ULONG>(a.blobs.size())};
			return a.blobs.back().get();
		};
		cloopVTable = &table;
	}
	void run(InputCodec& codec, std::vector<unsigned char>& buffer)
	{
		blobs.clear();
		codec.materialize(this, &transaction, buffer);
		require(!wrongContext, "BLOB escaped the supplied application transaction");
		for (const auto& b : blobs)
			require(!b->open && b->closes == 1 && !b->cancels && !b->releases,
				"BLOB consuming close/release differs");
	}
	std::string value(const InputCodec& codec, const std::vector<unsigned char>& buffer, unsigned i)
	{
		FbLocalStatus status;
		ISC_QUAD id;
		std::memcpy(&id, buffer.data() + codec.metadata()->getOffset(&status, i), sizeof(id));
		check(&status);
		require(id.gds_quad_low && id.gds_quad_low <= blobs.size(), "Bad materialized ID");
		return blobs[id.gds_quad_low - 1]->bytes;
	}
	VTable table{};
	BlobTransaction transaction;
	std::vector<std::unique_ptr<WriteBlob>> blobs;
	unsigned failAt = 0;
	bool wrongContext = false;
};

void blobs()
{
	auto metadata = describe({{SQL_LONG, 4}, {SQL_BLOB, sizeof(ISC_QUAD)}, {SQL_SHORT, 2},
		{SQL_BLOB, sizeof(ISC_QUAD)}});
	InputCodec codec(metadata, {0, 17});
	BlobWrites writes;
	require(codec.parameterTypes() == std::vector<std::uint32_t>({23, 17, 21, 17}), "BLOB OIDs differ");
	expectError([&] { InputCodec other(metadata, {0, 25}); }, "0A000");
	FbLocalStatus status;
	require(codec.metadata()->getType(&status, 1) == SQL_BLOB &&
		codec.metadata()->getLength(&status, 1) == sizeof(ISC_QUAD) &&
		codec.metadata()->getSubType(&status, 1) == 0 && codec.metadata()->getScale(&status, 1) == 0,
		"BLOB input must retain native ID layout");
	check(&status);
	std::string allBytes;
	for (unsigned i = 0; i < 256; ++i)
		allBytes.push_back(i);
	std::vector<unsigned char> buffer;
	for (const auto& input : std::vector<std::optional<std::string>>{std::string(150001, '\xff'),
		std::nullopt, "", allBytes, "x", std::string(150001, '\0')})
	{
		buffer = codec.encode({"123", input, "-7", "\\x00FF"}, {0, 1, 0, 0});
		writes.run(codec, buffer);
		require(buffer.size() == codec.messageLength() &&
			codec.metadata()->getLength(&status, 1) == sizeof(ISC_QUAD), "BLOB layout retained old capacity");
		check(&status);
		require(value<ISC_LONG>(codec, buffer, 0) == 123 && value<ISC_SHORT>(codec, buffer, 2) == -7,
			"Scalar neighbours changed after BLOB resize");
		require(value<ISC_SHORT>(codec, buffer, 1, true) == (input ? 0 : -1), "BLOB NULL differs from empty");
		if (input)
			require(writes.value(codec, buffer, 1) == *input, "Binary BLOB bytes changed");
		require(writes.value(codec, buffer, 3) == std::string("\0\xff", 2), "Other BLOB slot changed");
	}
	const auto oldLength = codec.messageLength();
	auto* oldMetadata = codec.metadata();
	expectError([&] { codec.encode({"invalid", "short", "0", ""}); }, "22P02");
	require(codec.metadata() == oldMetadata && codec.messageLength() == oldLength &&
		writes.value(codec, buffer, 1) == std::string(150001, '\0'), "Failed encode changed the retained layout");
	InputCodec single(describe({{SQL_BLOB, sizeof(ISC_QUAD)}}));
	for (const auto& test : std::vector<std::pair<std::string, std::string>>{
		{"", ""}, {"\\x", ""}, {"\\x \t\r\n", ""}, {"\\x 00 aB\tCd\nEf\r ", std::string("\0\xab\xcd\xef", 4)},
		{"\\000\\377\\200\\\\'", std::string("\0\xff\x80\\'", 5)}, {" 'Я' ", " 'Я' "},
		{" \\\\x41", " \\x41"}, {"\\x5c303030", "\\000"}})
	{
		buffer = single.encode({test.first});
		writes.run(single, buffer);
		require(writes.value(single, buffer, 0) == test.second, "Text bytea decoding differs");
	}
	for (const auto text : {"\\x0", "\\x 0 ", "\\xgg", "\\x0 0", "\\x00\v", "\\x\f00", "\\x00z0"})
		expectError([&] { single.encode({text}); }, "22023");
	for (const auto text : {"\\", "\\0", "\\00", "\\400", "\\378", "\\q", " \\x41", "\\X00", "\\ x00"})
		expectError([&] { single.encode({text}); }, "22P02");
	for (const auto& text : {std::string("a\0b", 3), std::string("\xc0\x80", 2)})
		expectError([&] { single.encode({text}); }, "22021");
	for (const unsigned size : {0, 1, 32765, 32766, 40444, 65533, 65534, 150001})
	{
		buffer = single.encode({std::string(size, '\xff')}, {1});
		writes.run(single, buffer);
		require(writes.value(single, buffer, 0) == std::string(size, '\xff'), "Binary boundary changed bytes");
		buffer = single.encode({"\\x" + std::string(size * 2, 'f')});
		writes.run(single, buffer);
		require(writes.value(single, buffer, 0) == std::string(size, '\xff'), "Hex boundary changed bytes");
	}

	// 68 independently sized BLOBs amongst 92 fields must not reserve 68 maximum slots.
	std::vector<Spec> specs;
	std::vector<std::optional<std::string>> inputs;
	for (unsigned i = 0; i < 92; ++i)
	{
		const bool blob = i % 4 != 3 && i != 90;
		specs.push_back(blob ? Spec{SQL_BLOB, sizeof(ISC_QUAD)} : Spec{SQL_LONG, 4});
		inputs.push_back(blob ? (i % 8 == 0 ? std::nullopt : std::optional<std::string>(std::string(i % 17, 'a' + i % 26))) : "42");
	}
	InputCodec mixed(describe(specs));
	buffer = mixed.encode(inputs);
	writes.run(mixed, buffer);
	require(buffer.size() < 2048, "Short BLOBs reserved maximum slots");
	for (unsigned i = 0; i < inputs.size(); ++i)
	{
		if (specs[i].type == SQL_LONG)
			require(value<ISC_LONG>(mixed, buffer, i) == 42, "Mixed scalar differs");
		else if (inputs[i])
			require(writes.value(mixed, buffer, i) == *inputs[i], "Mixed BLOB differs");
		else
			require(value<ISC_SHORT>(mixed, buffer, i, true) == -1, "Mixed BLOB NULL differs");
	}
	// Payload plus the native message retains the 1 MiB aggregate budget.
	specs.assign(16, {SQL_BLOB, sizeof(ISC_QUAD)});
	specs.push_back({SQL_SHORT, 2});
	InputCodec boundary(describe(specs));
	inputs.assign(16, std::string(65000, 'a'));
	inputs[15] = std::string(1024 * 1024 - boundary.messageLength() - 15 * 65000, 'b');
	inputs.push_back("1");
	buffer = boundary.encode(inputs);
	writes.run(boundary, buffer);
	require(buffer.size() < 1024 && value<ISC_SHORT>(boundary, buffer, 16) == 1,
		"Payload was copied into the native scalar message");
	*inputs[15] += 'b';
	expectError([&] { boundary.encode(inputs); }, "0A000");

	InputCodec partial(describe({{SQL_BLOB,8},{SQL_BLOB,8}}));
	buffer = partial.encode({std::string(150001,'x'), "second"}, {1});
	BlobWrites failing;
	require(failing.blobs.empty(), "Bind created a native BLOB");
	failing.failAt = 2;
	bool caught = false;
	try { partial.materialize(&failing, &failing.transaction, buffer); }
	catch (const status_exception&) { caught = true; }
	require(caught && failing.blobs.size() == 2, "Partial materialization did not fail");
	for (const auto& blob : failing.blobs)
		require(blob->cancels == 1 && !blob->open && !blob->closes && !blob->releases,
			"Partial materialization leaked a BLOB or released it twice");
	writes.run(partial, buffer);
	require(writes.value(partial,buffer,0)==std::string(150001,'x') &&
		writes.value(partial,buffer,1)=="second", "Failed materialization changed Bind bytes");
}

void metadataValidation()
{
	const Spec unsupported[] = {{SQL_BLOB, 8, 0, 1}, {SQL_BLOB, 8, 0, 2}, {SQL_BLOB, 8, 1},
		{SQL_DEC16, 8}, {SQL_INT128, 16, -39, 1},
		{SQL_DOUBLE, 8, -2, 1}, {SQL_TYPE_DATE, 4, -1},
		{SQL_INT64, 8, -19, 1}, {SQL_INT64, 8, 1, 1}, {SQL_LONG, 4, 0, 3},
		{SQL_BOOLEAN, 1, -1}, {SQL_BOOLEAN, 1, 0, 1}, {SQL_VARYING, 8, 0, 0, 1},
		{SQL_TEXT, 8, 0, 0, 21}, {SQL_VARYING, 8, -1, 0, 4}, {SQL_VARYING, 65534, 0, 0, 4}};
	for (const auto& spec : unsupported)
	{
		auto metadata = describe({spec});
		expectError([&] { InputCodec codec(metadata); }, "0A000");
	}
	for (const auto& spec : {Spec{SQL_LONG, 2}, Spec{SQL_BOOLEAN, 2}, Spec{SQL_BLOB, 4}})
	{
		auto metadata = describe({spec});
		expectError([&] { InputCodec codec(metadata); }, "XX000");
	}
	auto metadata = describe(std::vector<Spec>(32768, {SQL_SHORT, 2}));
	expectError([&] { InputCodec codec(metadata); }, "0A000");
	metadata = describe(std::vector<Spec>(17, {SQL_VARYING, 65532, 0, 0, 4}));
	expectError([&] { InputCodec codec(metadata); }, "0A000");
	expectError([] { InputCodec codec(nullptr); }, "XX000");
	metadata = describe({});
	InputCodec empty(metadata);
	require(empty.messageLength() == 0 && empty.parameterTypes().empty() && empty.encode().empty(),
		"Zero-parameter Bind was not supported");
	require(empty.encode({}, {1}).empty(), "All-parameters format rejected empty Bind");
}

} // namespace

int main()
{
	try
	{
		integers();
		decimals();
		booleans();
		text();
		emptyProfileStrings();
		blobs();
		metadataValidation();
		std::printf("PgWire input codec tests passed (%u checks)\n", checks);
		return 0;
	}
	catch (const std::exception& error)
	{
		std::fprintf(stderr, "PgWire input codec tests failed: %s\n", error.what());
	}
	catch (...)
	{
		std::fputs("PgWire input codec tests failed: unexpected exception\n", stderr);
	}
	return 1;
}
