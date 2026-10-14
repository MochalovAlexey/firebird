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
#include "../NumericCodec.h"
#include "../common/Int128.h"

#include <array>
#include <cstdio>
#include <cstring>
#include <limits>

using namespace Firebird;
using namespace Firebird::PgWire;

namespace
{
unsigned checks = 0;

void require(bool condition, const char* message)
{
	++checks;
	if (!condition) throw std::runtime_error(message);
}

template <typename F> void expect(F action, const char* state)
{
	try { action(); }
	catch (const ProtocolError& error)
	{
		require(std::strcmp(error.sqlState(), state) == 0, "Incorrect numeric SQLSTATE");
		return;
	}
	throw std::runtime_error("Expected numeric error");
}

// Literal network words below are independent fixtures, not codec output.
std::string words(std::initializer_list<unsigned> values)
{
	std::string result;
	for (const auto value : values)
	{
		result += static_cast<char>(value >> 8);
		result += static_cast<char>(value);
	}
	return result;
}

template <typename T> void fixture(T native, unsigned type, int scale,
	std::string_view text, const std::string& binary)
{
	std::array<unsigned char, sizeof(T) + 2> output{};
	require(formatNumeric(&native, type, scale, false) == text, "Text output differs from fixture");
	require(formatNumeric(&native, type, scale, true) == binary, "Binary output differs from fixture");
	for (const bool format : {false, true})
	{
		output.fill(0xA5);
		parseNumeric(format ? std::string_view(binary) : text, type, scale, format, output.data() + 1);
		require(std::memcmp(output.data() + 1, &native, sizeof(T)) == 0, "Parsed native value differs");
		require(output.front() == 0xA5 && output.back() == 0xA5, "Conversion exceeded native buffer");
		require(formatNumeric(output.data() + 1, type, scale, format) == (format ? binary : text),
			"Unaligned native read differs");
	}
}

void goldenFixtures()
{
	fixture<ISC_SHORT>(0, SQL_SHORT, -2, "0.00", words({0, 0, 0, 2}));
	fixture<ISC_LONG>(1, SQL_LONG, -4, "0.0001", words({1, 0xFFFF, 0, 4, 1}));
	fixture<ISC_LONG>(1, SQL_LONG, -5, "0.00001", words({1, 0xFFFE, 0, 5, 1000}));
	fixture<ISC_LONG>(1234500, SQL_LONG, -4, "123.4500", words({2, 0, 0, 4, 123, 4500}));
	fixture<ISC_LONG>(-1234500, SQL_LONG, -4, "-123.4500", words({2, 0, 0x4000, 4, 123, 4500}));
	fixture<ISC_LONG>(100000000, SQL_LONG, 0, "100000000", words({1, 2, 0, 0, 1}));
	fixture<ISC_SHORT>(-32768, SQL_SHORT, 0, "-32768", words({2, 1, 0x4000, 0, 3, 2768}));
	fixture<ISC_LONG>(std::numeric_limits<ISC_LONG>::max(), SQL_LONG, 0, "2147483647",
		words({3, 2, 0, 0, 21, 4748, 3647}));
	fixture<ISC_INT64>(std::numeric_limits<ISC_INT64>::min(), SQL_INT64, -2, "-92233720368547758.08",
		words({6, 4, 0x4000, 2, 9, 2233, 7203, 6854, 7758, 800}));

	CInt128 minimum(CInt128::MkMin), maximum(CInt128::MkMax);
	FB_I128 minBuffer, maxBuffer;
	std::memcpy(&minBuffer, minimum.getBytes(), sizeof(minBuffer));
	std::memcpy(&maxBuffer, maximum.getBytes(), sizeof(maxBuffer));
	fixture(minBuffer, SQL_INT128, 0, "-170141183460469231731687303715884105728",
		words({10, 9, 0x4000, 0, 170, 1411, 8346, 469, 2317, 3168, 7303, 7158, 8410, 5728}));
	fixture(maxBuffer, SQL_INT128, 0, "170141183460469231731687303715884105727",
		words({10, 9, 0, 0, 170, 1411, 8346, 469, 2317, 3168, 7303, 7158, 8410, 5727}));
	fixture(minBuffer, SQL_INT128, -38, "-1.70141183460469231731687303715884105728",
		words({11, 0, 0x4000, 38, 1, 7014, 1183, 4604, 6923, 1731, 6873, 371, 5884, 1057, 2800}));
	CInt128 one(1);
	FB_I128 oneBuffer;
	std::memcpy(&oneBuffer, one.getBytes(), sizeof(oneBuffer));
	fixture(oneBuffer, SQL_INT128, -38, "0.00000000000000000000000000000000000001",
		words({1, 0xFFF6, 0, 38, 100}));
}

void exactInputs()
{
	ISC_INT64 native = 0;
	for (const auto value : {" +1.2300e2\t", "123", "123.000", "000012300e-2", ".123e3", "12300E-2"})
	{
		parseNumeric(value, SQL_INT64, -2, false, &native);
		require(native == 12300, "Exact decimal/exponent scaling differs");
	}
	for (const auto value : {"-0", "-0.000", "0e999999999999999", "-0E-999999999999999999"})
	{
		parseNumeric(value, SQL_INT64, -18, false, &native);
		require(native == 0, "Noncanonical text zero differs");
	}
	parseNumeric(words({4, 1, 0x4000, 2, 0, 123, 4500, 0}), SQL_INT64, -2, true, &native);
	require(native == -12345, "Noncanonical binary leading/trailing zero groups differ");
	parseNumeric(words({0, 0x8000, 0x4000, 16383}), SQL_INT64, 0, true, &native);
	require(native == 0, "Noncanonical binary zero differs");
	parseNumeric(words({1, 0, 0, 16383, 123}), SQL_INT64, -2, true, &native);
	require(native == 12300, "Display scale must not force native scale");
	parseNumeric("9223372036854775807", SQL_INT64, 0, false, &native);
	require(native == std::numeric_limits<ISC_INT64>::max(), "INT64 max differs");
	parseNumeric("-9223372036854775808", SQL_INT64, 0, false, &native);
	require(native == std::numeric_limits<ISC_INT64>::min(), "INT64 min differs");
}

void rejectedInputs()
{
	std::array<unsigned char, 16> output;
	output.fill(0xA5);
	for (const auto value : {"", " ", "+", ".", "1e", "1e+", "1..0", "1,2", "1 2", "0x10", "1e2x", "NaNx"})
		expect([&] { parseNumeric(value, SQL_INT128, 0, false, output.data()); }, "22P02");
	expect([&] { parseNumeric(std::string("1\0", 2), SQL_INT128, 0, false, output.data()); }, "22P02");
	for (const auto value : {"NaN", "-NaN", "+Infinity", "-Inf", "infinity"})
		expect([&] { parseNumeric(value, SQL_INT128, 0, false, output.data()); }, "22003");
	for (const auto value : {"1.001", "1e-3", "1e999999999999", "1e-999999999999",
		"1701411834604692317316873037158841057.28", "-1701411834604692317316873037158841057.29"})
	{
		expect([&] { parseNumeric(value, SQL_INT128, -2, false, output.data()); }, "22003");
	}
	for (const auto& item : std::vector<std::pair<unsigned, std::string>>{{SQL_SHORT, "32768"},
		{SQL_SHORT, "-32769"}, {SQL_LONG, "2147483648"}, {SQL_LONG, "-2147483649"},
		{SQL_INT64, "9223372036854775808"}, {SQL_INT64, "-9223372036854775809"},
		{SQL_INT128, "170141183460469231731687303715884105728"},
		{SQL_INT128, "-170141183460469231731687303715884105729"}})
	{
		expect([&] { parseNumeric(item.second, item.first, 0, false, output.data()); }, "22003");
	}
	for (const auto& value : {std::string(), words({0, 0, 0}), words({1, 0, 0, 0}),
		words({0, 0, 0, 0, 1}), words({1, 0, 0, 0, 10000}), words({1, 0, 0, 0, 65535}),
		words({0, 0, 0x8000, 0}), words({0, 0, 0xE000, 0}), words({0, 0, 0, 0x4000}),
		words({1, 0xFFFF, 0, 0, 1}), words({1, 0xFFFF, 0, 2, 1234}),
		words({65535, 0, 0, 0}), words({0, 0, 0, 0}) + "x"})
	{
		expect([&] { parseNumeric(value, SQL_INT128, 0, true, output.data()); }, "22P03");
	}
	for (const auto sign : {0xC000, 0xD000, 0xF000})
		expect([&] { parseNumeric(words({0, 0, unsigned(sign), 0}), SQL_INT128, 0, true, output.data()); }, "22003");
	expect([&] { parseNumeric(words({1, 32767, 0, 0, 1}), SQL_INT128, 0, true, output.data()); }, "22003");
	expect([&] { parseNumeric(words({1, 0xFFFF, 0, 4, 1}), SQL_INT128, -3, true, output.data()); }, "22003");
	expect([&] { parseNumeric(std::string(1024 * 1024 + 1, '0'), SQL_INT128, 0, false, output.data()); }, "54000");
	expect([&] { parseNumeric("1", SQL_INT128, -39, false, output.data()); }, "0A000");
	expect([&] { parseNumeric("1", SQL_INT64, -19, false, output.data()); }, "0A000");
	expect([&] { parseNumeric("1", SQL_SHORT, 1, false, output.data()); }, "0A000");
	expect([&] { parseNumeric("1", SQL_DOUBLE, 0, false, output.data()); }, "0A000");
	for (auto byte : output) require(byte == 0xA5, "Failed conversion modified native buffer");
}

void scaleRange()
{
	for (int scale = 0; scale >= -38; --scale)
	{
		for (const auto text : {"0", "1", "-1", "12345678901234567890123456789012345678"})
		{
			FB_I128 native, copy;
			parseNumeric(text, SQL_INT128, 0, false, &native);
			const auto binary = formatNumeric(&native, SQL_INT128, scale, true);
			parseNumeric(binary, SQL_INT128, scale, true, &copy);
			require(std::memcmp(&native, &copy, sizeof(native)) == 0, "Scale boundary binary conversion differs");
			parseNumeric(formatNumeric(&native, SQL_INT128, scale, false), SQL_INT128, scale, false, &copy);
			require(std::memcmp(&native, &copy, sizeof(native)) == 0, "Scale boundary text conversion differs");
		}
	}
}
} // namespace

int main()
{
	try
	{
		goldenFixtures();
		exactInputs();
		rejectedInputs();
		scaleRange();
		std::printf("Numeric codec: %u checks passed\n", checks);
		return 0;
	}
	catch (const std::exception& error)
	{
		std::fprintf(stderr, "Numeric codec failure: %s\n", error.what());
		return 1;
	}
}
