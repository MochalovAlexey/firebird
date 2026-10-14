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
#include "TemporalCodec.h"
#include "Protocol.h"
#include "../common/classes/NoThrowTimeStamp.h"

#include <cstddef>
#include <cstring>
#include <limits>

namespace Firebird::PgWire
{
namespace
{

constexpr std::int64_t MIN_DATE = NoThrowTimeStamp::MIN_DATE;
constexpr std::int64_t MAX_DATE = NoThrowTimeStamp::MAX_DATE;
constexpr std::int64_t PG_EPOCH = 51544; // Firebird 1858-11-17 -> PostgreSQL 2000-01-01.
constexpr std::int64_t MICROS_PER_DAY = 86400000000;
constexpr std::int64_t MICROS_PER_TICK = 100;
constexpr std::int64_t TICKS_PER_DAY = MICROS_PER_DAY / MICROS_PER_TICK;
static_assert(ISC_TIME_SECONDS_PRECISION == 10000);
static_assert(sizeof(ISC_DATE) == 4 && sizeof(ISC_TIME) == 4);

[[noreturn]] void syntax()
{
	throw ProtocolError("22007", "Invalid ISO date or time parameter");
}

[[noreturn]] void range()
{
	throw ProtocolError("22008", "Date or time is outside the supported range or precision");
}

void checkType(unsigned type)
{
	if (type != SQL_TYPE_DATE && type != SQL_TYPE_TIME && type != SQL_TIMESTAMP)
		throw ProtocolError("0A000", "Temporal data type is not supported");
}

void checkDate(std::int64_t date)
{
	if (date < MIN_DATE || date > MAX_DATE)
		range();
}

void checkTime(std::int64_t micros)
{
	if (micros < 0 || micros >= MICROS_PER_DAY || micros % MICROS_PER_TICK)
		range();
}

constexpr unsigned daysBeforeYear(unsigned year)
{
	--year;
	return 365 * year + year / 4 - year / 100 + year / 400;
}
static_assert(MIN_DATE + daysBeforeYear(2000) == PG_EPOCH);
static_assert(MIN_DATE + daysBeforeYear(10000) - 1 == MAX_DATE);

unsigned monthDays(unsigned year, unsigned month)
{
	constexpr unsigned days[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
	const bool leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
	return days[month - 1] + (month == 2 && leap);
}

bool space(char c)
{
	return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v';
}

std::string_view trim(std::string_view text)
{
	while (!text.empty() && space(text.front()))
		text.remove_prefix(1);
	while (!text.empty() && space(text.back()))
		text.remove_suffix(1);
	return text;
}

bool infinity(std::string_view text)
{
	if (!text.empty() && (text.front() == '-' || text.front() == '+'))
		text.remove_prefix(1);
	constexpr std::string_view word = "infinity";
	if (text.size() != word.size())
		return false;
	for (unsigned i = 0; i < word.size(); ++i)
	{
		const char c = text[i] >= 'A' && text[i] <= 'Z' ? text[i] + ('a' - 'A') : text[i];
		if (c != word[i])
			return false;
	}
	return true;
}

unsigned digits(std::string_view text)
{
	unsigned value = 0;
	for (char c : text)
	{
		if (c < '0' || c > '9')
			syntax();
		value = value * 10 + c - '0';
	}
	return value;
}

ISC_DATE dateFromText(std::string_view text)
{
	if (text.size() != 10 || text[4] != '-' || text[7] != '-')
		syntax();
	const auto year = digits(text.substr(0, 4));
	const auto month = digits(text.substr(5, 2));
	const auto day = digits(text.substr(8, 2));
	if (!year || !month || month > 12 || !day || day > monthDays(year, month))
		range();
	unsigned days = daysBeforeYear(year) + day - 1;
	for (unsigned i = 1; i < month; ++i)
		days += monthDays(year, i);
	return static_cast<ISC_DATE>(MIN_DATE + days);
}

ISC_TIME timeFromText(std::string_view text)
{
	if (text.size() < 8 || text[2] != ':' || text[5] != ':')
		syntax();
	const auto hour = digits(text.substr(0, 2));
	const auto minute = digits(text.substr(3, 2));
	const auto second = digits(text.substr(6, 2));
	unsigned micros = 0;
	if (text.size() != 8)
	{
		if (text[8] != '.' || text.size() < 10 || text.size() > 15)
			syntax();
		micros = digits(text.substr(9));
		for (auto i = text.size() - 9; i < 6; ++i)
			micros *= 10;
	}
	if (hour > 23 || minute > 59 || second > 59 || micros % MICROS_PER_TICK)
		range();
	return ((hour * 60 + minute) * 60 + second) * ISC_TIME_SECONDS_PRECISION +
		micros / MICROS_PER_TICK;
}

void putDigits(std::string& text, unsigned offset, unsigned width, unsigned value)
{
	while (width)
	{
		text[offset + --width] = '0' + value % 10;
		value /= 10;
	}
}

std::string dateText(ISC_DATE value)
{
	checkDate(value);
	unsigned days = value - MIN_DATE;
	unsigned low = 1, high = 10000;
	while (high - low > 1)
	{
		const auto middle = (low + high) / 2;
		if (daysBeforeYear(middle) <= days)
			low = middle;
		else
			high = middle;
	}
	days -= daysBeforeYear(low);
	unsigned month = 1;
	while (days >= monthDays(low, month))
		days -= monthDays(low, month++);
	std::string text = "0000-00-00";
	putDigits(text, 0, 4, low);
	putDigits(text, 5, 2, month);
	putDigits(text, 8, 2, days + 1);
	return text;
}

std::string timeText(ISC_TIME ticks)
{
	checkTime(std::int64_t(ticks) * MICROS_PER_TICK);
	const auto seconds = ticks / ISC_TIME_SECONDS_PRECISION;
	std::string text = "00:00:00";
	putDigits(text, 0, 2, seconds / 3600);
	putDigits(text, 3, 2, seconds / 60 % 60);
	putDigits(text, 6, 2, seconds % 60);
	if (const auto fraction = ticks % ISC_TIME_SECONDS_PRECISION)
	{
		text += ".0000";
		putDigits(text, 9, 4, fraction);
		while (text.back() == '0')
			text.pop_back();
	}
	return text;
}

std::string binaryValue(std::int64_t value, unsigned width)
{
	auto bits = static_cast<std::uint64_t>(value);
	std::string result(width, '\0');
	for (unsigned i = 0; i < width; ++i)
	{
		result[width - i - 1] = static_cast<char>(bits & 0xff);
		bits >>= 8;
	}
	return result;
}

std::int64_t fromBinary(std::string_view value, unsigned width)
{
	if (value.size() != width)
		throw ProtocolError("22P03", "Invalid binary date or time parameter length");
	std::uint64_t bits = 0;
	for (unsigned char c : value)
		bits = (bits << 8) | c;
	if (width == 4)
		return bits <= 0x7fffffff ? std::int64_t(bits) : std::int64_t(bits) - 0x100000000LL;
	if (bits <= std::uint64_t(std::numeric_limits<std::int64_t>::max()))
		return bits;
	// Avoid out-of-range unsigned -> signed conversion, including INT64_MIN.
	return -1 - static_cast<std::int64_t>(~bits);
}

template <typename T> T load(const void* native, unsigned offset = 0)
{
	T value;
	std::memcpy(&value, static_cast<const unsigned char*>(native) + offset, sizeof(value));
	return value;
}

template <typename T> void store(void* native, T value, unsigned offset = 0)
{
	std::memcpy(static_cast<unsigned char*>(native) + offset, &value, sizeof(value));
}

} // namespace

std::string formatTemporal(const void* native, unsigned type, bool binary)
{
	checkType(type);
	if (!native)
		throw ProtocolError("XX000", "Missing native temporal buffer");
	ISC_DATE date = 0;
	ISC_TIME time = 0;
	if (type == SQL_TYPE_DATE || type == SQL_TIMESTAMP)
	{
		date = load<ISC_DATE>(native);
		checkDate(date);
	}
	if (type == SQL_TYPE_TIME || type == SQL_TIMESTAMP)
	{
		time = load<ISC_TIME>(native, type == SQL_TIMESTAMP ? offsetof(ISC_TIMESTAMP, timestamp_time) : 0);
		checkTime(std::int64_t(time) * MICROS_PER_TICK);
	}
	if (binary)
	{
		if (type == SQL_TYPE_DATE)
			return binaryValue(date - PG_EPOCH, 4);
		return binaryValue(std::int64_t(time) * MICROS_PER_TICK +
			(type == SQL_TIMESTAMP ? (date - PG_EPOCH) * MICROS_PER_DAY : 0), 8);
	}
	if (type == SQL_TYPE_DATE)
		return dateText(date);
	if (type == SQL_TYPE_TIME)
		return timeText(time);
	return dateText(date) + " " + timeText(time);
}

void parseTemporal(std::string_view value, unsigned type, bool binary, void* native)
{
	checkType(type);
	if (!native)
		throw ProtocolError("XX000", "Missing native temporal buffer");
	ISC_DATE date = 0;
	ISC_TIME time = 0;
	if (binary)
	{
		const auto number = fromBinary(value, type == SQL_TYPE_DATE ? 4 : 8);
		if (type == SQL_TYPE_DATE)
		{
			checkDate(number + PG_EPOCH);
			date = number + PG_EPOCH;
		}
		else if (type == SQL_TYPE_TIME)
		{
			checkTime(number);
			time = number / MICROS_PER_TICK;
		}
		else
		{
			// Floor division preserves the positive time of day before the PG epoch.
			auto days = number / MICROS_PER_DAY;
			auto micros = number % MICROS_PER_DAY;
			if (micros < 0)
				--days, micros += MICROS_PER_DAY;
			checkDate(days + PG_EPOCH);
			checkTime(micros);
			date = days + PG_EPOCH;
			time = micros / MICROS_PER_TICK;
		}
	}
	else
	{
		value = trim(value);
		if (infinity(value))
			range();
		if (type == SQL_TYPE_DATE)
			date = dateFromText(value);
		else if (type == SQL_TYPE_TIME)
			time = timeFromText(value);
		else
		{
			if (value.size() < 19 || (value[10] != ' ' && value[10] != 'T'))
				syntax();
			date = dateFromText(value.substr(0, 10));
			time = timeFromText(value.substr(11));
		}
	}
	// Publish only after all components have been validated.
	if (type == SQL_TYPE_DATE || type == SQL_TIMESTAMP)
		store(native, date);
	if (type == SQL_TYPE_TIME || type == SQL_TIMESTAMP)
		store(native, time, type == SQL_TIMESTAMP ? offsetof(ISC_TIMESTAMP, timestamp_time) : 0);
}

} // namespace Firebird::PgWire
