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
		{SQL_BLOB, 8}, {SQL_DEC16, 8}, {SQL_INT128, 16, -39, 1},
		{SQL_DOUBLE, 8, -2, 1}, {SQL_TYPE_DATE, 4, -1},
		{SQL_INT64, 8, -19, 1}, {SQL_INT64, 8, 1, 1}, {SQL_INT64, 8, 0, 3},
		{SQL_BOOLEAN, 1, -1}, {SQL_BOOLEAN, 1, 0, 1},
		{SQL_TEXT, 16, 0, 0, 1}, {SQL_TEXT, 16, 0, 0, 21},
		{SQL_VARYING, 16, -1, 0, 4}, {SQL_VARYING, 65534, 0, 0, 4}
	};
	for (const auto& spec : unsupported)
	{
		auto metadata = describe({spec});
		expectError([&] { ResultCodec codec(metadata); }, "0A000");
	}
	for (const auto& spec : {Spec{SQL_LONG, 2}, Spec{SQL_BOOLEAN, 2}})
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
