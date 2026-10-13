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
	for (const auto text : {".", "1e", "1e+", "1.2.3", "NaN", "Infinity", "1,25"})
		expectError([&] { codec.encode({text, "0", "0", "0"}); }, "22P02");
	expectError([&] { codec.encode({"0", std::nullopt, std::nullopt, std::nullopt}, {1}); }, "0A000");
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
}

void metadataValidation()
{
	const Spec unsupported[] = {{SQL_DOUBLE, 8}, {SQL_BLOB, 8}, {SQL_INT128, 16},
		{SQL_INT64, 8, -19, 1}, {SQL_INT64, 8, 1, 1}, {SQL_LONG, 4, 0, 3},
		{SQL_BOOLEAN, 1, -1}, {SQL_BOOLEAN, 1, 0, 1}, {SQL_VARYING, 8, 0, 0, 1},
		{SQL_TEXT, 8, 0, 0, 21}, {SQL_VARYING, 8, -1, 0, 4}, {SQL_VARYING, 65534, 0, 0, 4}};
	for (const auto& spec : unsupported)
	{
		auto metadata = describe({spec});
		expectError([&] { InputCodec codec(metadata); }, "0A000");
	}
	for (const auto& spec : {Spec{SQL_LONG, 2}, Spec{SQL_BOOLEAN, 2}})
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
