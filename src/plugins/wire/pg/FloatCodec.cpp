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
#include "FloatCodec.h"
#include "Protocol.h"

#include <charconv>
#include <cmath>
#include <cstring>
#include <limits>

namespace Firebird::PgWire
{
namespace
{

bool space(char c)
{
	return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v';
}

bool equalFold(std::string_view text, std::string_view expected)
{
	if (text.size() != expected.size())
		return false;
	for (unsigned i = 0; i < text.size(); ++i)
	{
		const auto c = text[i] >= 'A' && text[i] <= 'Z' ? text[i] + ('a' - 'A') : text[i];
		if (c != expected[i])
			return false;
	}
	return true;
}

[[noreturn]] void invalidFloat()
{
	throw ProtocolError("22P02", "Invalid floating point parameter");
}

template <typename T, typename Bits> std::string format(const void* native, bool binary)
{
	static_assert(std::numeric_limits<T>::is_iec559 && sizeof(T) == sizeof(Bits));
	if (binary)
	{
		Bits bits;
		std::memcpy(&bits, native, sizeof(bits));
		std::string bytes(sizeof(bits), '\0');
		for (unsigned i = 0; i < sizeof(bits); ++i)
		{
			bytes[sizeof(bits) - i - 1] = static_cast<char>(bits & 0xff);
			bits >>= 8;
		}
		return bytes;
	}
	T value;
	std::memcpy(&value, native, sizeof(value));
	if (std::isnan(value))
		return "NaN";
	if (std::isinf(value))
		return value < 0 ? "-Infinity" : "Infinity";
	char buffer[64];
	// Locale independent, shortest representation that round trips to this type.
	const auto converted = std::to_chars(buffer, buffer + sizeof(buffer), value);
	if (converted.ec != std::errc())
		throw ProtocolError("XX000", "Cannot format floating point result");
	return std::string(buffer, converted.ptr);
}

template <typename T, typename Bits> void parse(std::string_view text, bool binary, void* native)
{
	static_assert(std::numeric_limits<T>::is_iec559 && sizeof(T) == sizeof(Bits));
	if (binary)
	{
		if (text.size() != sizeof(Bits))
			throw ProtocolError("22P03", "Invalid binary floating point parameter length");
		Bits bits = 0;
		for (unsigned char byte : text)
			bits = (bits << 8) | byte;
		std::memcpy(native, &bits, sizeof(bits));
		return;
	}
	while (!text.empty() && space(text.front()))
		text.remove_prefix(1);
	while (!text.empty() && space(text.back()))
		text.remove_suffix(1);
	bool negative = false;
	if (!text.empty() && (text.front() == '+' || text.front() == '-'))
	{
		negative = text.front() == '-';
		text.remove_prefix(1);
	}
	T value;
	if (equalFold(text, "nan"))
		value = std::numeric_limits<T>::quiet_NaN();
	else if (equalFold(text, "inf") || equalFold(text, "infinity"))
		value = std::numeric_limits<T>::infinity();
	else
	{
		// Validate decimal syntax ourselves: from_chars also recognizes NaN payloads.
		std::size_t position = 0, digits = 0;
		auto consumeDigits = [&]
		{
			while (position < text.size() && text[position] >= '0' && text[position] <= '9')
			{
				++position;
				++digits;
			}
		};
		consumeDigits();
		if (position < text.size() && text[position] == '.')
		{
			++position;
			consumeDigits();
		}
		if (!digits)
			invalidFloat();
		if (position < text.size() && (text[position] == 'e' || text[position] == 'E'))
		{
			++position;
			if (position < text.size() && (text[position] == '+' || text[position] == '-'))
				++position;
			digits = 0;
			consumeDigits();
			if (!digits)
				invalidFloat();
		}
		if (position != text.size())
			invalidFloat();
		const auto converted = std::from_chars(text.data(), text.data() + text.size(), value);
		if (converted.ec == std::errc::result_out_of_range)
			throw ProtocolError("22003", "Floating point parameter is out of range");
		if (converted.ec != std::errc() || converted.ptr != text.data() + text.size())
			invalidFloat();
	}
	if (negative)
		value = -value;
	std::memcpy(native, &value, sizeof(value));
}

} // namespace

std::string formatFloat(const void* native, unsigned type, bool binary)
{
	switch (type)
	{
	case SQL_FLOAT:
		return format<float, std::uint32_t>(native, binary);
	case SQL_DOUBLE:
		return format<double, std::uint64_t>(native, binary);
	default:
		throw ProtocolError("XX000", "Invalid floating point result type");
	}
}

void parseFloat(std::string_view value, unsigned type, bool binary, void* native)
{
	switch (type)
	{
	case SQL_FLOAT:
		parse<float, std::uint32_t>(value, binary, native);
		break;
	case SQL_DOUBLE:
		parse<double, std::uint64_t>(value, binary, native);
		break;
	default:
		throw ProtocolError("XX000", "Invalid floating point parameter type");
	}
}

} // namespace Firebird::PgWire
