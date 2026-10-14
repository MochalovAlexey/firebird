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
#include "../TemporalCodec.h"
#include "../Protocol.h"

#include <array>
#include <cstdio>
#include <cstring>
#include <limits>
#include <stdexcept>

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

template <typename F> void expectError(const char* state, F action)
{
	try
	{
		action();
	}
	catch (const ProtocolError& error)
	{
		require(std::strcmp(error.sqlState(), state) == 0, "Unexpected temporal SQLSTATE");
		return;
	}
	throw std::runtime_error("Expected temporal conversion error");
}

std::string hex(std::string_view text)
{
	std::string bytes;
	if (text.size() % 2)
		throw std::runtime_error("Invalid fixture hex length");
	auto digit = [](char c) -> unsigned
	{
		if (c >= '0' && c <= '9')
			return c - '0';
		if (c >= 'a' && c <= 'f')
			return c - 'a' + 10;
		throw std::runtime_error("Invalid fixture hex digit");
	};
	for (unsigned i = 0; i < text.size(); i += 2)
		bytes.push_back(static_cast<char>(digit(text[i]) * 16 + digit(text[i + 1])));
	return bytes;
}

template <typename T> void fixture(unsigned type, T native,
	std::string_view text, std::string_view wireHex)
{
	// Separate native and network literals, including both epochs, avoid testing
	// only round trips of the same implementation. Neither buffer is aligned.
	std::array<unsigned char, sizeof(T) + 2> source;
	source.fill(0xa5);
	std::memcpy(source.data() + 1, &native, sizeof(native));
	const auto binary = hex(wireHex);
	require(formatTemporal(source.data() + 1, type, false) == text, "Wrong ISO temporal output");
	require(formatTemporal(source.data() + 1, type, true) == binary, "Wrong binary temporal output");
	for (bool useBinary : {false, true})
	{
		std::array<unsigned char, sizeof(T) + 2> result;
		result.fill(0xa5);
		parseTemporal(useBinary ? std::string_view(binary) : text, type, useBinary, result.data() + 1);
		require(result == source, "Wrong native temporal input or overwrite beyond field");
	}
}

void dates()
{
	struct Date
	{
		const char* text;
		ISC_DATE native;
		const char* binary;
	};
	const Date values[] = {
		{"0001-01-01", -678575, "fff4dbf9"},
		{"1600-02-29", -94494, "fffdc58a"},
		{"1858-11-17", 0, "ffff36a8"},
		{"1900-02-28", 15078, "ffff718e"},
		{"1900-03-01", 15079, "ffff718f"},
		{"1970-01-01", 40587, "ffffd533"},
		{"1999-12-31", 51543, "ffffffff"},
		{"2000-01-01", 51544, "00000000"},
		{"2000-02-29", 51603, "0000003b"},
		{"2000-03-01", 51604, "0000003c"},
		{"2100-03-01", 88128, "00008ee8"},
		{"9999-12-31", 2973483, "002c95d3"}
	};
	for (const auto& value : values)
		fixture(SQL_TYPE_DATE, value.native, value.text, value.binary);
	ISC_DATE value = 0;
	parseTemporal(" \t2000-01-01\r\n", SQL_TYPE_DATE, false, &value);
	require(value == 51544, "ASCII whitespace around date was not trimmed");
}

void times()
{
	fixture(SQL_TYPE_TIME, ISC_TIME(0), "00:00:00", "0000000000000000");
	fixture(SQL_TYPE_TIME, ISC_TIME(1), "00:00:00.0001", "0000000000000064");
	fixture(SQL_TYPE_TIME, ISC_TIME(1000), "00:00:00.1", "00000000000186a0");
	fixture(SQL_TYPE_TIME, ISC_TIME(10000), "00:00:01", "00000000000f4240");
	fixture(SQL_TYPE_TIME, ISC_TIME(864000000 - 1), "23:59:59.9999", "000000141dd75f9c");
	for (const auto text : {"00:00:00.1234", "00:00:00.12340", "00:00:00.123400"})
	{
		ISC_TIME value = 0;
		parseTemporal(text, SQL_TYPE_TIME, false, &value);
		require(value == 1234, "Exact fractional time was rounded or scaled incorrectly");
	}
	ISC_TIME value = 1;
	parseTemporal("\v00:00:00.000000\f", SQL_TYPE_TIME, false, &value);
	require(value == 0, "Zero fraction or ASCII trim rejected");
}

void timestamps()
{
	fixture(SQL_TIMESTAMP, ISC_TIMESTAMP{-678575, 0},
		"0001-01-01 00:00:00", "ff1fe2ffc59c6000");
	fixture(SQL_TIMESTAMP, ISC_TIMESTAMP{40587, 0},
		"1970-01-01 00:00:00", "fffca2fec4c82000");
	fixture(SQL_TIMESTAMP, ISC_TIMESTAMP{51542, 863999999},
		"1999-12-30 23:59:59.9999", "ffffffebe2289f9c");
	fixture(SQL_TIMESTAMP, ISC_TIMESTAMP{51543, 863999999},
		"1999-12-31 23:59:59.9999", "ffffffffffffff9c");
	fixture(SQL_TIMESTAMP, ISC_TIMESTAMP{51544, 0},
		"2000-01-01 00:00:00", "0000000000000000");
	fixture(SQL_TIMESTAMP, ISC_TIMESTAMP{51544, 1},
		"2000-01-01 00:00:00.0001", "0000000000000064");
	fixture(SQL_TIMESTAMP, ISC_TIMESTAMP{51545, 452961234},
		"2000-01-02 12:34:56.1234", "0000001ea9b35e08");
	fixture(SQL_TIMESTAMP, ISC_TIMESTAMP{2973483, 863999999},
		"9999-12-31 23:59:59.9999", "0380e70b913b7f9c");
	ISC_TIMESTAMP value{};
	parseTemporal(" 2000-01-02T12:34:56.123400\n", SQL_TIMESTAMP, false, &value);
	require(value.timestamp_date == 51545 && value.timestamp_time == 452961234,
		"ISO T separator or fractional timestamp rejected");
}

void reject(std::string_view value, unsigned type, bool binary, const char* state)
{
	std::array<unsigned char, sizeof(ISC_TIMESTAMP) + 2> target;
	target.fill(0xa5);
	const auto original = target;
	expectError(state, [&] { parseTemporal(value, type, binary, target.data() + 1); });
	require(target == original, "Failed parse modified native buffer");
}

void invalidText()
{
	for (const auto value : {"", "2000-1-01", "2000-01-1", "01/01/2000", "2000/01/01",
		"2000-01-01 BC", "2000-01-01Z", "+2000-01-01", "10000-01-01", "200x-01-01"})
	{
		reject(value, SQL_TYPE_DATE, false, "22007");
	}
	for (const auto value : {"0000-01-01", "2000-00-01", "2000-13-01", "2000-01-00",
		"2000-04-31", "1900-02-29", "2100-02-29", "2001-02-29"})
	{
		reject(value, SQL_TYPE_DATE, false, "22008");
	}
	for (const auto value : {"00:00", "0:00:00", "00:00:00.", "00:00:00.0000000",
		"00:00:00Z", "00:00:00+00:00", "00:00:00 UTC", "00:00:00.1234x"})
	{
		reject(value, SQL_TYPE_TIME, false, "22007");
	}
	for (const auto value : {"24:00:00", "23:60:00", "23:59:60", "00:00:00.000001",
		"00:00:00.00001", "23:59:59.999999", "00:00:00.123450"})
	{
		reject(value, SQL_TYPE_TIME, false, "22008");
	}
	for (const auto value : {"2000-01-01", "2000-01-01t00:00:00", "2000-01-01  00:00:00",
		"2000-01-01\t00:00:00", "2000-01-01 00:00:00-03:00", "2000-01-01 00:00:00 BC"})
	{
		reject(value, SQL_TIMESTAMP, false, "22007");
	}
	reject("2000-01-01 00:00:00.000001", SQL_TIMESTAMP, false, "22008");
	for (auto type : {SQL_TYPE_DATE, SQL_TYPE_TIME, SQL_TIMESTAMP})
	{
		for (const auto value : {"infinity", "-infinity", "+INFINITY"})
			reject(value, type, false, "22008");
	}
}

void invalidBinary()
{
	for (auto type : {SQL_TYPE_DATE, SQL_TYPE_TIME, SQL_TIMESTAMP})
	{
		const unsigned width = type == SQL_TYPE_DATE ? 4 : 8;
		for (unsigned size = 0; size <= 9; ++size)
		{
			if (size != width)
				reject(std::string(size, '\0'), type, true, "22P03");
		}
	}
	for (const auto value : {"80000000", "7fffffff", "fff4dbf8", "002c95d4"})
		reject(hex(value), SQL_TYPE_DATE, true, "22008");
	for (const auto value : {"0000000000000001", "0000000000000063", "ffffffffffffff9c",
		"000000141dd76000", "7fffffffffffffff", "8000000000000000"})
	{
		reject(hex(value), SQL_TYPE_TIME, true, "22008");
	}
	for (const auto value : {"ffffffffffffffff", "0000000000000001", "ffffffffffffff9b",
		"ff1fe2ffc59c5f9c", "0380e70b913b8000", "7fffffffffffffff", "8000000000000000"})
	{
		reject(hex(value), SQL_TIMESTAMP, true, "22008");
	}
}

void invalidNative()
{
	for (bool binary : {false, true})
	{
		for (ISC_DATE value : {-678576, 2973484, std::numeric_limits<ISC_DATE>::min(),
			std::numeric_limits<ISC_DATE>::max()})
		{
			expectError("22008", [&] { formatTemporal(&value, SQL_TYPE_DATE, binary); });
		}
		for (ISC_TIME value : {ISC_TIME(864000000), std::numeric_limits<ISC_TIME>::max()})
		{
			expectError("22008", [&] { formatTemporal(&value, SQL_TYPE_TIME, binary); });
		}
		const ISC_TIMESTAMP invalid{-678576, 0}, invalidTime{51544, 864000000};
		expectError("22008", [&] { formatTemporal(&invalid, SQL_TIMESTAMP, binary); });
		expectError("22008", [&] { formatTemporal(&invalidTime, SQL_TIMESTAMP, binary); });
	}
	ISC_DATE native = 0;
	expectError("0A000", [&] { formatTemporal(&native, SQL_LONG, false); });
	reject("", SQL_LONG, false, "0A000");
	expectError("XX000", [&] { formatTemporal(nullptr, SQL_TYPE_DATE, false); });
	expectError("XX000", [&] { parseTemporal("2000-01-01", SQL_TYPE_DATE, false, nullptr); });
}

} // namespace

int main()
{
	try
	{
		dates();
		times();
		timestamps();
		invalidText();
		invalidBinary();
		invalidNative();
		std::printf("PgWire temporal codec tests passed (%u checks)\n", checks);
		return 0;
	}
	catch (const std::exception& error)
	{
		std::fprintf(stderr, "PgWire temporal codec tests failed: %s\n", error.what());
	}
	catch (...)
	{
		std::fputs("PgWire temporal codec tests failed: unexpected exception\n", stderr);
	}
	return 1;
}
