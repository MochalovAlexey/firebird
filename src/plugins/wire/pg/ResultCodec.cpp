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
#include "ResultCodec.h"
#include "../common/unicode_util.h"
#include "../common/status.h"
#include "../intl/charsets.h"

#include <charconv>
#include <cstring>
#include <limits>

namespace Firebird::PgWire
{
namespace
{

constexpr unsigned MAX_MESSAGE = 1024 * 1024;
constexpr unsigned MAX_COLUMNS = 32767;
constexpr unsigned MAX_VARYING = std::numeric_limits<ISC_USHORT>::max() - sizeof(ISC_USHORT);

[[noreturn]] void invalidMetadata()
{
	throw ProtocolError("XX000", "Invalid engine result metadata");
}

void checkRange(unsigned offset, unsigned size, unsigned length)
{
	if (offset > length || size > length - offset)
		invalidMetadata();
}

void validateText(std::string_view text)
{
	if (text.find('\0') != std::string_view::npos ||
		!UnicodeUtil::utf8WellFormed(text.size(), reinterpret_cast<const UCHAR*>(text.data()), nullptr))
	{
		throw ProtocolError("22021", "Invalid UTF8 or NUL in result text");
	}
}

template <typename T> T readValue(const unsigned char* address)
{
	T value;
	std::memcpy(&value, address, sizeof(value));
	return value;
}

std::string decimal(ISC_INT64 value, int scale)
{
	// Unsigned subtraction also represents abs(INT64_MIN), without signed overflow.
	const auto magnitude = value < 0 ? std::uint64_t(0) - static_cast<std::uint64_t>(value) :
		static_cast<std::uint64_t>(value);
	char buffer[32];
	const auto converted = std::to_chars(buffer, buffer + sizeof(buffer), magnitude);
	std::string text(buffer, converted.ptr);
	const auto fractional = static_cast<unsigned>(-scale);
	if (fractional)
	{
		if (text.size() <= fractional)
			text.insert(0, fractional + 1 - text.size(), '0');
		text.insert(text.size() - fractional, 1, '.');
	}
	if (value < 0)
		text.insert(0, 1, '-');
	return text;
}

} // namespace

ResultCodec::ResultCodec(IMessageMetadata* logicalMetadata)
{
	if (!logicalMetadata)
		invalidMetadata();
	FbLocalStatus status;
	const auto count = logicalMetadata->getCount(&status);
	check(&status);
	const auto logicalLength = logicalMetadata->getMessageLength(&status);
	check(&status);
	if (count > MAX_COLUMNS || logicalLength > MAX_MESSAGE)
		throw ProtocolError("0A000", "Result metadata exceeds supported limits");
	if (!count && logicalLength)
		invalidMetadata();
	descriptions.reserve(count);
	fields.reserve(count);
	RefPtr<IMetadataBuilder> builder(REF_NO_INCR, logicalMetadata->getBuilder(&status));
	check(&status);
	if (!builder)
		invalidMetadata();

	for (unsigned i = 0; i < count; ++i)
	{
		const auto type = logicalMetadata->getType(&status, i) & ~1u;
		check(&status);
		const auto scale = logicalMetadata->getScale(&status, i);
		check(&status);
		const auto subtype = logicalMetadata->getSubType(&status, i);
		check(&status);
		const auto capacity = logicalMetadata->getLength(&status, i);
		check(&status);
		unsigned physicalSize = capacity;
		Column column;
		switch (type)
		{
		case SQL_SHORT:
		case SQL_LONG:
		case SQL_INT64:
			if (subtype < 0 || subtype > 2 || scale < -18 || scale > 0)
				throw ProtocolError("0A000", "Exact numeric result metadata is not supported");
			physicalSize = type == SQL_SHORT ? sizeof(ISC_SHORT) :
				type == SQL_LONG ? sizeof(ISC_LONG) : sizeof(ISC_INT64);
			if (capacity != physicalSize)
				invalidMetadata();
			column.oid = (subtype || scale) ? 1700 : type == SQL_SHORT ? 21 : type == SQL_LONG ? 23 : 20;
			column.typeSize = column.oid == 1700 ? -1 : physicalSize;
			break;
		case SQL_BOOLEAN:
			if (subtype || scale)
				throw ProtocolError("0A000", "Boolean result metadata is not supported");
			if (capacity != sizeof(FB_BOOLEAN))
				invalidMetadata();
			column.oid = 16;
			column.typeSize = sizeof(FB_BOOLEAN);
			break;
		case SQL_TEXT:
		case SQL_VARYING:
		{
			if (subtype || scale)
				throw ProtocolError("0A000", "Text result metadata is not supported");
			const auto textType = logicalMetadata->getCharSet(&status, i);
			check(&status);
			// Some metadata implementations retain the collation in the high byte.
			const auto charset = CSetId(textType);
			if (textType > std::numeric_limits<ISC_USHORT>::max() ||
				(charset != CS_UTF8 && charset != CS_NONE && charset != CS_ASCII))
			{
				throw ProtocolError("0A000", "Result character set is not supported");
			}
			if (capacity > MAX_VARYING)
				throw ProtocolError("0A000", "Text result exceeds supported limits");
			column.oid = type == SQL_TEXT ? 1042 : 1043;
			column.typeSize = -1;
			if (type == SQL_TEXT)
			{
				// DSQL retains the logical CHAR type, but internally provides varying
				// text (CORE-2606). Request that length rather than UTF8 byte padding.
				// Fetch types use the base SQL code. Every native message field has
				// a separate null indicator, including one requested with a base type.
				builder->setType(&status, i, SQL_VARYING);
				check(&status);
			}
			else
				physicalSize += sizeof(ISC_USHORT);
			break;
		}
		default:
			throw ProtocolError("0A000", "Result data type is not supported yet");
		}
		const auto offset = logicalMetadata->getOffset(&status, i);
		check(&status);
		const auto nullOffset = logicalMetadata->getNullOffset(&status, i);
		check(&status);
		checkRange(offset, physicalSize, logicalLength);
		checkRange(nullOffset, sizeof(ISC_SHORT), logicalLength);
		const auto alias = logicalMetadata->getAlias(&status, i);
		check(&status);
		column.name = alias ? alias : "";
		validateText(column.name);
		descriptions.push_back(std::move(column));
		fields.push_back({type == SQL_TEXT ? SQL_VARYING : type, capacity, 0, 0, scale});
	}

	metadata.assignRefNoIncr(builder->getMetadata(&status));
	check(&status);
	if (!metadata)
		invalidMetadata();
	length = metadata->getMessageLength(&status);
	check(&status);
	if (length > MAX_MESSAGE)
		throw ProtocolError("0A000", "Result message exceeds supported limits");
	for (unsigned i = 0; i < count; ++i)
	{
		auto& field = fields[i];
		field.offset = metadata->getOffset(&status, i);
		check(&status);
		field.nullOffset = metadata->getNullOffset(&status, i);
		check(&status);
		checkRange(field.offset, field.length + (field.type == SQL_VARYING ? sizeof(ISC_USHORT) : 0), length);
		checkRange(field.nullOffset, sizeof(ISC_SHORT), length);
	}
}

std::vector<std::optional<std::string>> ResultCodec::decode(const void* buffer, unsigned bufferLength) const
{
	if (bufferLength != length || (length && !buffer))
		throw ProtocolError("XX000", "Invalid engine result buffer");
	const auto* bytes = static_cast<const unsigned char*>(buffer);
	std::vector<std::optional<std::string>> result;
	result.reserve(fields.size());
	for (const auto& field : fields)
	{
		if (readValue<ISC_SHORT>(bytes + field.nullOffset))
		{
			result.emplace_back(std::nullopt);
			continue;
		}
		const auto* address = bytes + field.offset;
		switch (field.type)
		{
		case SQL_SHORT:
			result.emplace_back(decimal(readValue<ISC_SHORT>(address), field.scale));
			break;
		case SQL_LONG:
			result.emplace_back(decimal(readValue<ISC_LONG>(address), field.scale));
			break;
		case SQL_INT64:
			result.emplace_back(decimal(readValue<ISC_INT64>(address), field.scale));
			break;
		case SQL_BOOLEAN:
		{
			const auto value = readValue<FB_BOOLEAN>(address);
			if (value != FB_FALSE && value != FB_TRUE)
				throw ProtocolError("XX000", "Invalid engine boolean result");
			result.emplace_back(value == FB_TRUE ? "t" : "f");
			break;
		}
		case SQL_VARYING:
		{
			const auto size = readValue<ISC_USHORT>(address);
			if (size > field.length)
				throw ProtocolError("XX000", "Invalid engine varying result length");
			const std::string_view text(reinterpret_cast<const char*>(address + sizeof(ISC_USHORT)), size);
			validateText(text);
			result.emplace_back(text);
			break;
		}
		default:
			invalidMetadata();
		}
	}
	return result;
}

} // namespace Firebird::PgWire
