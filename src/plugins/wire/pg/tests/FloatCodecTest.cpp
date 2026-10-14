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
#include "../FloatCodec.h"
#include "../Protocol.h"

#include <cmath>
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

void error(std::string_view text, unsigned type, bool binary, const char* state)
{
	unsigned char native[16];
	try
	{
		parseFloat(text, type, binary, native + 1);
	}
	catch (const ProtocolError& exception)
	{
		require(std::strcmp(exception.sqlState(), state) == 0, "Incorrect floating point SQLSTATE");
		return;
	}
	throw std::runtime_error("Expected floating point rejection");
}

template <typename T> void verify(unsigned type, std::string_view one, std::string_view minusZero)
{
	unsigned char native[16];
	parseFloat(one, type, true, native + 1);
	T value;
	std::memcpy(&value, native + 1, sizeof(value));
	require(value == 1, "Independent IEEE 1.0 fixture was decoded incorrectly");
	require(formatFloat(native + 1, type, true) == one, "Independent IEEE 1.0 fixture was encoded incorrectly");
	parseFloat(minusZero, type, true, native + 1);
	std::memcpy(&value, native + 1, sizeof(value));
	require(value == 0 && std::signbit(value), "Binary negative zero lost sign");
	require(formatFloat(native + 1, type, false) == "-0", "Text negative zero lost sign");
	parseFloat("-0", type, false, native + 1);
	require(formatFloat(native + 1, type, true) == minusZero, "Text negative zero encoded incorrectly");
	for (const T expected : {T(0), T(-0.0), T(0.1), T(-123456.75), std::numeric_limits<T>::min(),
		std::numeric_limits<T>::max(), std::numeric_limits<T>::lowest(), std::numeric_limits<T>::denorm_min()})
	{
		const auto text = formatFloat(&expected, type, false);
		parseFloat(text, type, false, native + 1);
		require(std::memcmp(&expected, native + 1, sizeof(T)) == 0, "Text round trip lost native floating point precision");
		const auto bytes = formatFloat(&expected, type, true);
		parseFloat(bytes, type, true, native + 1);
		require(std::memcmp(&expected, native + 1, sizeof(T)) == 0, "Binary round trip lost native floating point bits");
	}
	for (const auto text : {"Infinity", "+INF", "-inf", " -INFINITY\t", "NaN", "nAn", "-nan"})
	{
		parseFloat(text, type, false, native + 1);
		std::memcpy(&value, native + 1, sizeof(value));
		require(std::isinf(value) || std::isnan(value), "Special floating point input differs");
		const auto formatted = formatFloat(native + 1, type, false);
		require(formatted == "NaN" || formatted == "Infinity" || formatted == "-Infinity", "Special floating point output differs");
	}
	for (const auto text : {" +.125e+1\n", "1.25", "125e-2", "1.2500"})
	{
		parseFloat(text, type, false, native + 1);
		std::memcpy(&value, native + 1, sizeof(value));
		require(value == T(1.25), "Decimal floating point grammar differs");
	}
	for (const auto text : {"", "+", ".", "1e", "1e+", "--1", "+-1", "1 2", "1,5", "NaN(1)", "0x1p1", "Infinityx"})
		error(text, type, false, "22P02");
	for (const auto text : {"1e99999", "-1e-99999"})
		error(text, type, false, "22003");
	error(std::string(sizeof(T) - 1, '\0'), type, true, "22P03");
	error(std::string(sizeof(T) + 1, '\0'), type, true, "22P03");
}

} // namespace

int main()
{
	try
	{
		verify<float>(SQL_FLOAT, std::string("\x3f\x80\0\0", 4), std::string("\x80\0\0\0", 4));
		verify<double>(SQL_DOUBLE, std::string("\x3f\xf0\0\0\0\0\0\0", 8), std::string("\x80\0\0\0\0\0\0\0", 8));
		error("3.4028236e38", SQL_FLOAT, false, "22003");
		error("1.7976931348623159e308", SQL_DOUBLE, false, "22003");
		std::printf("PgWire floating point codec tests passed (%u checks)\n", checks);
		return 0;
	}
	catch (const std::exception& exception)
	{
		std::fprintf(stderr, "PgWire floating point codec tests failed: %s\n", exception.what());
		return 1;
	}
}
