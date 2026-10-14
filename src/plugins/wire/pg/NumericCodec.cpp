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
#include "NumericCodec.h"
#include "../common/Int128.h"

#include <algorithm>
#include <cstring>
#include <limits>

namespace Firebird::PgWire
{
namespace
{

constexpr std::size_t MAX_NUMERIC_BYTES = 1024 * 1024;
static_assert(sizeof(unsigned) == 4 && sizeof(Int128) == sizeof(FB_I128));
constexpr unsigned POSITIVE = 0x0000, NEGATIVE = 0x4000;
constexpr unsigned NAN_VALUE = 0xC000, POS_INFINITY = 0xD000, NEG_INFINITY = 0xF000;

[[noreturn]] void invalid(bool binary)
{
	throw ProtocolError(binary ? "22P03" : "22P02", "Invalid numeric parameter");
}

[[noreturn]] void range()
{
	throw ProtocolError("22003", "Numeric value is out of range or cannot be represented exactly");
}

unsigned width(unsigned type, int scale)
{
	unsigned result;
	switch (type)
	{
	case SQL_SHORT: result = 2; break;
	case SQL_LONG: result = 4; break;
	case SQL_INT64: result = 8; break;
	case SQL_INT128: result = 16; break;
	default: throw ProtocolError("0A000", "Unsupported native numeric type");
	}
	if (scale > 0 || scale < (result == 16 ? -38 : -18))
		throw ProtocolError("0A000", "Unsupported native numeric scale");
	return result;
}

struct Decimal
{
	std::string digits;
	std::int64_t power = 0;
	bool negative = false;
};

bool digit(char value) { return value >= '0' && value <= '9'; }
bool space(char value)
{
	return value == ' ' || value == '\t' || value == '\r' || value == '\n' || value == '\f' || value == '\v';
}

bool equalFold(std::string_view value, std::string_view word)
{
	if (value.size() != word.size())
		return false;
	for (std::size_t i = 0; i < value.size(); ++i)
	{
		const auto c = value[i];
		if ((c >= 'A' && c <= 'Z' ? c + ('a' - 'A') : c) != word[i])
			return false;
	}
	return true;
}

void normalize(Decimal& value)
{
	const auto first = value.digits.find_first_not_of('0');
	if (first == std::string::npos)
	{
		value.digits = "0";
		value.power = 0;
		value.negative = false;
		return;
	}
	const auto last = value.digits.find_last_not_of('0');
	value.power += static_cast<std::int64_t>(value.digits.size() - last - 1);
	value.digits = value.digits.substr(first, last - first + 1);
}

Decimal textNumber(std::string_view text)
{
	while (!text.empty() && space(text.front())) text.remove_prefix(1);
	while (!text.empty() && space(text.back())) text.remove_suffix(1);
	Decimal value;
	if (!text.empty() && (text.front() == '+' || text.front() == '-'))
	{
		value.negative = text.front() == '-';
		text.remove_prefix(1);
	}
	if (equalFold(text, "nan") || equalFold(text, "inf") || equalFold(text, "infinity"))
		range();
	value.digits.reserve(text.size());
	std::size_t position = 0;
	while (position < text.size() && digit(text[position])) value.digits += text[position++];
	if (position < text.size() && text[position] == '.')
	{
		++position;
		while (position < text.size() && digit(text[position]))
		{
			value.digits += text[position++];
			--value.power;
		}
	}
	if (value.digits.empty()) invalid(false);
	if (position < text.size() && (text[position] == 'e' || text[position] == 'E'))
	{
		++position;
		bool negative = false;
		if (position < text.size() && (text[position] == '+' || text[position] == '-'))
			negative = text[position++] == '-';
		const auto start = position;
		std::int64_t exponent = 0;
		while (position < text.size() && digit(text[position]))
		{
			// This bound exceeds the largest compensating fractional input.
			exponent = std::min<std::int64_t>(2 * MAX_NUMERIC_BYTES, exponent * 10 + text[position++] - '0');
		}
		if (position == start) invalid(false);
		value.power += negative ? -exponent : exponent;
	}
	if (position != text.size()) invalid(false);
	normalize(value);
	return value;
}

unsigned read16(std::string_view value, std::size_t offset)
{
	return (static_cast<unsigned char>(value[offset]) << 8) | static_cast<unsigned char>(value[offset + 1]);
}

void write16(std::string& value, unsigned word)
{
	value += static_cast<char>(word >> 8);
	value += static_cast<char>(word);
}

Decimal binaryNumber(std::string_view bytes)
{
	// PostgreSQL numeric_recv/numeric_send: network uint16 count, int16 weight,
	// uint16 sign and display scale, followed by uint16 base-10000 digits.
	if (bytes.size() < 8) invalid(true);
	const auto count = read16(bytes, 0), sign = read16(bytes, 4), dscale = read16(bytes, 6);
	const auto word = read16(bytes, 2);
	const int weight = word <= 0x7FFF ? static_cast<int>(word) : static_cast<int>(word) - 0x10000;
	if (bytes.size() != 8 + 2 * count || dscale > 0x3FFF ||
		(sign != POSITIVE && sign != NEGATIVE && sign != NAN_VALUE &&
		 sign != POS_INFINITY && sign != NEG_INFINITY))
	{
		invalid(true);
	}
	Decimal value;
	value.negative = sign == NEGATIVE;
	value.power = 4 * (static_cast<std::int64_t>(weight) - count + 1);
	value.digits.reserve(4 * count);
	for (unsigned i = 0; i < count; ++i)
	{
		auto group = read16(bytes, 8 + 2 * i);
		if (group >= 10000) invalid(true);
		char digits[4];
		for (int j = 3; j >= 0; --j)
		{
			digits[j] = static_cast<char>('0' + group % 10);
			group /= 10;
		}
		value.digits.append(digits, 4);
	}
	if (sign != POSITIVE && sign != NEGATIVE) range();
	normalize(value);
	// Reject hidden nonzero digits instead of silently truncating at dscale.
	if (value.digits != "0" && value.power < -static_cast<int>(dscale)) invalid(true);
	return value;
}

const char* magnitudeLimit(unsigned size, bool negative)
{
	switch (size)
	{
	case 2: return negative ? "32768" : "32767";
	case 4: return negative ? "2147483648" : "2147483647";
	case 8: return negative ? "9223372036854775808" : "9223372036854775807";
	default: return negative ? "170141183460469231731687303715884105728" :
		"170141183460469231731687303715884105727";
	}
}

std::string scaledInteger(Decimal value, unsigned size, int scale)
{
	if (value.digits == "0") return value.digits;
	const auto power = value.power - scale;
	if (power < 0 || power > 39 || value.digits.size() + power > 39) range();
	value.digits.append(static_cast<std::size_t>(power), '0');
	const std::string_view limit = magnitudeLimit(size, value.negative);
	if (value.digits.size() > limit.size() ||
		(value.digits.size() == limit.size() && value.digits.compare(limit) > 0))
	{
		range();
	}
	return value.digits;
}

template <typename T> T load(const void* address)
{
	T value;
	std::memcpy(&value, address, sizeof(value));
	return value;
}

template <typename T> void store(void* address, T value)
{
	std::memcpy(address, &value, sizeof(value));
}

Decimal nativeNumber(const void* native, unsigned size)
{
	Decimal value;
	if (size != 16)
	{
		const std::int64_t number = size == 2 ? load<ISC_SHORT>(native) :
			size == 4 ? load<ISC_LONG>(native) : load<ISC_INT64>(native);
		value.negative = number < 0;
		const std::uint64_t magnitude = value.negative ? std::uint64_t(-(number + 1)) + 1 : number;
		value.digits = std::to_string(magnitude);
		return value;
	}
	Int128 number;
	std::memcpy(number.getBytes(), native, sizeof(FB_I128));
	unsigned words[4];
	number.getTable32(words);
	value.negative = (words[3] & 0x80000000U) != 0;
	if (value.negative)
	{
		std::uint64_t carry = 1;
		for (auto& word : words)
		{
			const std::uint64_t part = std::uint64_t(~word) + carry;
			word = static_cast<unsigned>(part);
			carry = part >> 32;
		}
	}
	do
	{
		std::uint64_t remainder = 0;
		for (int i = 3; i >= 0; --i)
		{
			const auto part = (remainder << 32) | words[i];
			words[i] = static_cast<unsigned>(part / 10);
			remainder = part % 10;
		}
		value.digits += static_cast<char>('0' + remainder);
	} while (words[0] || words[1] || words[2] || words[3]);
	std::reverse(value.digits.begin(), value.digits.end());
	return value;
}

} // namespace

std::string formatNumeric(const void* native, unsigned type, int scale, bool binary)
{
	const auto size = width(type, scale);
	if (!native) throw ProtocolError("XX000", "Missing native numeric buffer");
	auto value = nativeNumber(native, size);
	std::string text = value.digits;
	const unsigned fraction = -scale;
	if (fraction)
	{
		if (text.size() <= fraction) text.insert(0, fraction + 1 - text.size(), '0');
		text.insert(text.size() - fraction, 1, '.');
	}
	if (!binary)
	{
		if (value.negative) text.insert(0, 1, '-');
		return text;
	}
	const auto dot = text.find('.');
	const auto integerLength = dot == std::string::npos ? text.size() : dot;
	if (dot != std::string::npos) text.erase(dot, 1);
	const auto padding = (4 - integerLength % 4) % 4;
	text.insert(0, padding, '0');
	text.append((4 - text.size() % 4) % 4, '0');
	int weight = static_cast<int>((integerLength + padding) / 4) - 1;
	std::vector<unsigned> groups;
	for (std::size_t i = 0; i < text.size(); i += 4)
	{
		unsigned group = 0;
		for (unsigned j = 0; j < 4; ++j) group = group * 10 + text[i + j] - '0';
		groups.push_back(group);
	}
	std::size_t first = 0, last = groups.size();
	while (first < last && !groups[first]) { ++first; --weight; }
	while (last > first && !groups[last - 1]) --last;
	const auto count = last - first;
	std::string bytes;
	bytes.reserve(8 + count * 2);
	write16(bytes, static_cast<unsigned>(count));
	write16(bytes, count ? static_cast<unsigned>(weight) & 0xFFFF : 0);
	write16(bytes, count && value.negative ? NEGATIVE : POSITIVE);
	write16(bytes, fraction);
	for (auto i = first; i < last; ++i) write16(bytes, groups[i]);
	return bytes;
}

void parseNumeric(std::string_view input, unsigned type, int scale, bool binary, void* native)
{
	const auto size = width(type, scale);
	if (!native) throw ProtocolError("XX000", "Missing native numeric buffer");
	if (input.size() > MAX_NUMERIC_BYTES) throw ProtocolError("54000", "Numeric parameter exceeds supported limits");
	const auto value = binary ? binaryNumber(input) : textNumber(input);
	const auto digits = scaledInteger(value, size, scale);
	if (size == 16)
	{
		// Build toward the final signed value: -2^127 has no positive counterpart.
		// The checked decimal bound above also protects the Int128 operations.
		CInt128 number(0);
		for (char digit : digits)
		{
			number *= 10;
			if (value.negative) number -= static_cast<unsigned>(digit - '0');
			else number += static_cast<unsigned>(digit - '0');
		}
		std::memcpy(native, number.getBytes(), sizeof(FB_I128));
		return;
	}
	std::uint64_t magnitude = 0;
	for (char digit : digits) magnitude = magnitude * 10 + digit - '0';
	const auto number = value.negative ?
		(magnitude == (std::uint64_t(1) << 63) ? std::numeric_limits<std::int64_t>::min() :
		 -static_cast<std::int64_t>(magnitude)) : static_cast<std::int64_t>(magnitude);
	if (size == 2) store(native, static_cast<ISC_SHORT>(number));
	else if (size == 4) store(native, static_cast<ISC_LONG>(number));
	else store(native, static_cast<ISC_INT64>(number));
}

} // namespace Firebird::PgWire
