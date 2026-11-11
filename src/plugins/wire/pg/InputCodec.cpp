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
#include "InputCodec.h"
#include "FloatCodec.h"
#include "NumericCodec.h"
#include "TemporalCodec.h"
#include "../common/status.h"
#include "../common/unicode_util.h"
#include "../intl/charsets.h"

#include <algorithm>
#include <cstring>
#include <limits>

namespace Firebird::PgWire
{
namespace
{

constexpr unsigned MAX_MESSAGE = 1024 * 1024;
constexpr unsigned MAX_PARAMETERS = 32767;
constexpr unsigned MAX_VARYING = std::numeric_limits<ISC_USHORT>::max() - sizeof(ISC_USHORT);

[[noreturn]] void invalidMetadata()
{
	throw ProtocolError("XX000", "Invalid engine parameter metadata");
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
		throw ProtocolError("22021", "Invalid UTF8 or NUL in parameter text");
	}
}

std::string byteaText(std::string_view text, std::size_t remaining)
{
	validateText(text);
	std::string bytes;
	const auto append = [&](unsigned char byte)
	{
		if (bytes.size() >= MAX_VARYING || bytes.size() >= remaining)
			throw ProtocolError("0A000", "Binary BLOB parameter exceeds supported limits");
		bytes.push_back(byte);
	};
	if (text.size() >= 2 && text[0] == '\\' && text[1] == 'x')
	{
		const auto hex = [](char c) -> int
		{
			if (c >= '0' && c <= '9') return c - '0';
			if (c >= 'a' && c <= 'f') return c - 'a' + 10;
			if (c >= 'A' && c <= 'F') return c - 'A' + 10;
			return -1;
		};
		for (std::size_t i = 2; i < text.size();)
		{
			const char c = text[i];
			if (c == ' ' || c == '\t' || c == '\n' || c == '\r')
			{
				++i;
				continue;
			}
			if (i + 1 == text.size() || hex(c) < 0 || hex(text[i + 1]) < 0)
				throw ProtocolError("22023", "Invalid hexadecimal bytea parameter");
			append((hex(c) << 4) | hex(text[i + 1]));
			i += 2;
		}
	}
	else
	{
		for (std::size_t i = 0; i < text.size(); ++i)
		{
			if (text[i] != '\\')
				append(text[i]);
			else if (i + 1 < text.size() && text[i + 1] == '\\')
			{
				append('\\');
				++i;
			}
			else if (i + 3 < text.size() && text[i + 1] >= '0' && text[i + 1] <= '3' &&
				text[i + 2] >= '0' && text[i + 2] <= '7' && text[i + 3] >= '0' && text[i + 3] <= '7')
			{
				append((text[i + 1] - '0') * 64 + (text[i + 2] - '0') * 8 + text[i + 3] - '0');
				i += 3;
			}
			else
				throw ProtocolError("22P02", "Invalid escape in bytea parameter");
		}
	}
	return bytes;
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

bool digit(char c)
{
	return c >= '0' && c <= '9';
}

[[noreturn]] void invalidNumber()
{
	throw ProtocolError("22P02", "Invalid numeric parameter");
}

[[noreturn]] void numericRange()
{
	throw ProtocolError("22003", "Numeric parameter is out of range or cannot be represented exactly");
}

std::int64_t signedValue(std::uint64_t magnitude, bool negative)
{
	if (!negative)
		return static_cast<std::int64_t>(magnitude);
	if (magnitude == (std::uint64_t(1) << 63))
		return std::numeric_limits<std::int64_t>::min();
	return -static_cast<std::int64_t>(magnitude);
}

std::int64_t integer(std::string_view text, unsigned width)
{
	text = trim(text);
	bool negative = false;
	if (!text.empty() && (text.front() == '+' || text.front() == '-'))
	{
		negative = text.front() == '-';
		text.remove_prefix(1);
	}
	if (text.empty() || !std::all_of(text.begin(), text.end(), digit))
		invalidNumber();
	const auto limit = (std::uint64_t(1) << (width * 8 - 1)) - (negative ? 0 : 1);
	std::uint64_t magnitude = 0;
	for (const auto c : text)
	{
		const auto value = c - '0';
		if (magnitude > (limit - value) / 10)
			numericRange();
		magnitude = magnitude * 10 + value;
	}
	return signedValue(magnitude, negative);
}

std::int64_t binaryInteger(std::string_view value, unsigned width)
{
	if (value.size() != width)
		throw ProtocolError("22P03", "Invalid binary integer parameter length");
	std::uint64_t bits = 0;
	for (unsigned char byte : value)
		bits = (bits << 8) | byte;
	const bool negative = bits & (std::uint64_t(1) << (width * 8 - 1));
	const auto mask = width == 8 ? std::numeric_limits<std::uint64_t>::max() :
		(std::uint64_t(1) << (width * 8)) - 1;
	return signedValue(negative ? ((~bits) & mask) + 1 : bits, negative);
}

bool boolean(std::string_view text)
{
	text = trim(text);
	if (text == "1")
		return true;
	if (text == "0")
		return false;
	bool matched = false, result = false;
	for (const auto word : {"true", "false", "yes", "no", "on", "off"})
	{
		const std::string_view candidate(word);
		if (text.empty() || text.size() > candidate.size())
			continue;
		bool same = true;
		for (std::size_t i = 0; i < text.size(); ++i)
		{
			const char c = text[i] >= 'A' && text[i] <= 'Z' ? text[i] + ('a' - 'A') : text[i];
			same &= c == candidate[i];
		}
		if (same)
		{
			if (matched)
				throw ProtocolError("22P02", "Ambiguous boolean parameter");
			matched = true;
			result = candidate == "true" || candidate == "yes" || candidate == "on";
		}
	}
	if (!matched)
		throw ProtocolError("22P02", "Invalid boolean parameter");
	return result;
}

template <typename T> void store(unsigned char* address, T value)
{
	std::memcpy(address, &value, sizeof(value));
}

} // namespace

InputCodec::InputCodec(IMessageMetadata* logicalMetadata, const std::vector<std::uint32_t>& parseTypes)
{
	if (!logicalMetadata)
		invalidMetadata();
	FbLocalStatus status;
	const auto count = logicalMetadata->getCount(&status);
	check(&status);
	const auto logicalLength = logicalMetadata->getMessageLength(&status);
	check(&status);
	if (count > MAX_PARAMETERS || logicalLength > MAX_MESSAGE)
		throw ProtocolError("0A000", "Parameter metadata exceeds supported limits");
	if (!count && logicalLength)
		invalidMetadata();
	if (parseTypes.size() > count)
		throw ProtocolError("08P01", "Parse parameter type count exceeds engine parameter count");
	types.reserve(count);
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
		unsigned physicalSize = capacity, charset = 0;
		std::uint32_t oid;
		switch (type)
		{
		case SQL_SHORT:
		case SQL_LONG:
		case SQL_INT64:
		case SQL_INT128:
			if (subtype < 0 || subtype > 2 || scale < (type == SQL_INT128 ? -38 : -18) || scale > 0)
				throw ProtocolError("0A000", "Exact numeric parameter metadata is not supported");
			physicalSize = type == SQL_SHORT ? sizeof(ISC_SHORT) :
				type == SQL_LONG ? sizeof(ISC_LONG) : type == SQL_INT64 ? sizeof(ISC_INT64) : sizeof(FB_I128);
			if (capacity != physicalSize)
				invalidMetadata();
			oid = (subtype || scale || type == SQL_INT128) ? 1700 : type == SQL_SHORT ? 21 : type == SQL_LONG ? 23 : 20;
			break;
		case SQL_FLOAT:
		case SQL_DOUBLE:
		case SQL_TYPE_DATE:
		case SQL_TYPE_TIME:
		case SQL_TIMESTAMP:
			if (subtype || scale)
				throw ProtocolError("0A000", "Parameter type modifiers are not supported");
			physicalSize = type == SQL_FLOAT ? sizeof(float) : type == SQL_DOUBLE ? sizeof(double) :
				type == SQL_TYPE_DATE ? sizeof(ISC_DATE) : type == SQL_TYPE_TIME ? sizeof(ISC_TIME) :
				sizeof(ISC_TIMESTAMP);
			if (capacity != physicalSize)
				invalidMetadata();
			oid = type == SQL_FLOAT ? 700 : type == SQL_DOUBLE ? 701 :
				type == SQL_TYPE_DATE ? 1082 : type == SQL_TYPE_TIME ? 1083 : 1114;
			break;
		case SQL_BOOLEAN:
			if (subtype || scale)
				throw ProtocolError("0A000", "Boolean parameter metadata is not supported");
			if (capacity != sizeof(FB_BOOLEAN))
				invalidMetadata();
			oid = 16;
			break;
		case SQL_BLOB:
			if (subtype || scale)
				throw ProtocolError("0A000", "BLOB parameter subtype or scale is not supported");
			if (capacity != sizeof(ISC_QUAD))
				invalidMetadata();
			oid = 17;
			charset = CS_BINARY;
			hasBlobs = true;
			break;
		case SQL_TEXT:
		case SQL_VARYING:
		{
			if (subtype || scale)
				throw ProtocolError("0A000", "Text parameter metadata is not supported");
			const auto textType = logicalMetadata->getCharSet(&status, i);
			check(&status);
			charset = CSetId(textType);
			if (textType > std::numeric_limits<ISC_USHORT>::max() ||
				(charset != CS_UTF8 && charset != CS_NONE && charset != CS_ASCII))
			{
				throw ProtocolError("0A000", "Parameter character set is not supported");
			}
			if (capacity > MAX_VARYING)
				throw ProtocolError("0A000", "Text parameter exceeds supported limits");
			oid = type == SQL_TEXT ? 1042 : 1043;
			if (type == SQL_TEXT)
			{
				// Let the engine apply CHAR padding/conversion to the actual input length.
				builder->setType(&status, i, SQL_VARYING);
				check(&status);
			}
			else
				physicalSize += sizeof(ISC_USHORT);
			break;
		}
		default:
			throw ProtocolError("0A000", "Parameter data type is not supported yet");
		}
		if (i < parseTypes.size() && parseTypes[i])
		{
			const auto supplied = parseTypes[i];
			const bool compatibleText = (type == SQL_TEXT || type == SQL_VARYING) &&
				(supplied == 25 || supplied == 1042 || supplied == 1043);
			if (supplied != oid && !compatibleText)
				throw ProtocolError("0A000", "Parse parameter type does not match engine metadata");
			oid = supplied;
		}
		const auto offset = logicalMetadata->getOffset(&status, i);
		check(&status);
		const auto nullOffset = logicalMetadata->getNullOffset(&status, i);
		check(&status);
		checkRange(offset, physicalSize, logicalLength);
		checkRange(nullOffset, sizeof(ISC_SHORT), logicalLength);
		if (type == SQL_BLOB)
		{
			// The engine converts OCTETS to a BLOB at Execute, in the current transaction.
			// Bind sizes this physical slot; Parse never reserves the maximum payload.
			builder->setType(&status, i, SQL_VARYING);
			check(&status);
			builder->setSubType(&status, i, 0);
			check(&status);
			builder->setScale(&status, i, 0);
			check(&status);
			builder->setCharSet(&status, i, CS_BINARY);
			check(&status);
			builder->setLength(&status, i, 0);
			check(&status);
		}
		types.push_back(oid);
		fields.push_back({type == SQL_TEXT ? SQL_VARYING : type,
			type == SQL_BLOB ? 0 : capacity, 0, 0, charset, scale});
	}
	bindMetadata.assignRefNoIncr(builder->getMetadata(&status));
	check(&status);
	length = layout(bindMetadata, fields);
}

unsigned InputCodec::layout(IMessageMetadata* metadata, std::vector<Field>& fields)
{
	if (!metadata)
		invalidMetadata();
	FbLocalStatus status;
	const auto length = metadata->getMessageLength(&status);
	check(&status);
	if (length > MAX_MESSAGE)
		throw ProtocolError("0A000", "Parameter message exceeds supported limits");
	for (unsigned i = 0; i < fields.size(); ++i)
	{
		auto& field = fields[i];
		field.offset = metadata->getOffset(&status, i);
		check(&status);
		field.nullOffset = metadata->getNullOffset(&status, i);
		check(&status);
		checkRange(field.offset, field.length +
			(field.type == SQL_VARYING || field.type == SQL_BLOB ? sizeof(ISC_USHORT) : 0), length);
		checkRange(field.nullOffset, sizeof(ISC_SHORT), length);
	}
	return length;
}

std::size_t InputCodec::retainedBytes() const noexcept
{
	return sizeof(*this) + fields.capacity() * sizeof(Field) + types.capacity() * sizeof(std::uint32_t);
}

std::vector<unsigned char> InputCodec::encode(const std::vector<std::optional<std::string>>& values,
	const std::vector<std::int16_t>& formats)
{
	if (values.size() != fields.size())
		throw ProtocolError("08P01", "Bind parameter count does not match the prepared statement");
	if (!formats.empty() && formats.size() != 1 && formats.size() != fields.size())
		throw ProtocolError("08P01", "Bind parameter format count is invalid");
	for (auto format : formats)
	{
		if (format != 0 && format != 1)
			throw ProtocolError("08P01", "Bind parameter format code is invalid");
	}
	// Stage the new descriptor locally so an unsuccessful encode preserves the old layout.
	RefPtr<IMessageMetadata> nextMetadata;
	std::vector<Field> nextFields;
	std::vector<std::string> decoded;
	unsigned nextLength = length;
	if (hasBlobs)
	{
		FbLocalStatus status;
		RefPtr<IMetadataBuilder> builder(REF_NO_INCR, bindMetadata->getBuilder(&status));
		check(&status);
		nextFields = fields;
		decoded.resize(fields.size());
		std::size_t remaining = MAX_MESSAGE;
		for (unsigned i = 0; i < fields.size(); ++i)
		{
			if (fields[i].type != SQL_BLOB)
				continue;
			std::size_t size = 0;
			if (values[i])
			{
				const auto format = formats.empty() ? 0 : formats[formats.size() == 1 ? 0 : i];
				if (values[i]->size() > MAX_MESSAGE)
					throw ProtocolError("0A000", "Parameter value exceeds supported limits");
				if (!format)
					decoded[i] = byteaText(*values[i], remaining);
				size = format ? values[i]->size() : decoded[i].size();
				if (size > MAX_VARYING || size > remaining)
					throw ProtocolError("0A000", "Binary BLOB parameter exceeds supported limits");
			}
			remaining -= size;
			nextFields[i].length = size;
			builder->setLength(&status, i, size);
			check(&status);
		}
		nextMetadata.assignRefNoIncr(builder->getMetadata(&status));
		check(&status);
		nextLength = layout(nextMetadata, nextFields);
	}
	std::vector<unsigned char> buffer(nextLength);
	for (unsigned i = 0; i < fields.size(); ++i)
	{
		const auto& field = hasBlobs ? nextFields[i] : fields[i];
		if (!values[i])
		{
			store<ISC_SHORT>(buffer.data() + field.nullOffset, -1);
			continue;
		}
		const std::string_view value(*values[i]);
		if (value.size() > MAX_MESSAGE)
			throw ProtocolError("0A000", "Parameter value exceeds supported limits");
		const auto format = formats.empty() ? 0 : formats[formats.size() == 1 ? 0 : i];
		if (!format && field.type != SQL_BLOB)
			validateText(value);
		auto* address = buffer.data() + field.offset;
		switch (field.type)
		{
		case SQL_SHORT:
		case SQL_LONG:
		case SQL_INT64:
		case SQL_INT128:
		{
			if (types[i] == 1700)
			{
				parseNumeric(value, field.type, field.scale, format != 0, address);
				break;
			}
			const auto numeric = format ? binaryInteger(value, field.length) : integer(value, field.length);
			if (field.type == SQL_SHORT)
				store<ISC_SHORT>(address, numeric);
			else if (field.type == SQL_LONG)
				store<ISC_LONG>(address, numeric);
			else
				store<ISC_INT64>(address, numeric);
			break;
		}
		case SQL_FLOAT:
		case SQL_DOUBLE:
			parseFloat(value, field.type, format != 0, address);
			break;
		case SQL_TYPE_DATE:
		case SQL_TYPE_TIME:
		case SQL_TIMESTAMP:
			parseTemporal(value, field.type, format != 0, address);
			break;
		case SQL_BOOLEAN:
			if (format && (value.size() != 1 || (value[0] != 0 && value[0] != 1)))
				throw ProtocolError("22P03", "Invalid binary boolean parameter");
			store<FB_BOOLEAN>(address, (format ? value[0] != 0 : boolean(value)) ? FB_TRUE : FB_FALSE);
			break;
		case SQL_VARYING:
			validateText(value);
			if (field.charset == CS_ASCII && std::any_of(value.begin(), value.end(),
				[](unsigned char c) { return c > 127; }))
			{
				throw ProtocolError("22021", "Parameter is not representable in ASCII");
			}
			if (value.size() > field.length)
				throw ProtocolError("22001", "Text parameter is too long");
			[[fallthrough]];
		case SQL_BLOB:
		{
			const std::string_view bytes = field.type == SQL_BLOB && !format ? decoded[i] : value;
			store<ISC_USHORT>(address, bytes.size());
			std::memcpy(address + sizeof(ISC_USHORT), bytes.data(), bytes.size());
			break;
		}
		default:
			invalidMetadata();
		}
	}
	if (hasBlobs)
	{
		bindMetadata = nextMetadata;
		fields.swap(nextFields);
		length = nextLength;
	}
	return buffer;
}

} // namespace Firebird::PgWire
