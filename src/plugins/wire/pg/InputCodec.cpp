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
#include "StringCodec.h"
#include "../common/Bytea.h"
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
		if (bytes.size() >= remaining)
			throw ProtocolError("0A000", "Binary BLOB parameter exceeds supported limits");
		bytes.push_back(byte);
	};
	decodeBytea(text, append, [](bool hex)
	{
		throw ProtocolError(hex ? "22023" : "22P02", hex ?
			"Invalid hexadecimal bytea parameter" : "Invalid escape in bytea parameter");
	});
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
			if (type == SQL_INT64 && subtype == 3 && !scale && capacity == sizeof(ISC_INT64))
			{
				oid = 4089;
				break;
			}
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
			charset = CSetId(logicalMetadata->getCharSet(&status, i));
			check(&status);
			if (((subtype && subtype != isc_blob_bytea && subtype != isc_blob_pg_mchar) && !(subtype == isc_blob_text &&
				(charset == CS_PG_MCHAR || charset == CS_PG_MVARCHAR))) || scale)
				throw ProtocolError("0A000", "BLOB parameter subtype or scale is not supported");
			if (capacity != sizeof(ISC_QUAD))
				invalidMetadata();
			if (subtype == isc_blob_pg_mchar)
			{
				charset = CS_PG_MCHAR;
				builder->setSubType(&status, i, isc_blob_text);
				check(&status);
				builder->setCharSet(&status, i, CS_UTF8);
				check(&status);
			}
			oid = charset == CS_PG_MCHAR ? MCHAR_OID : charset == CS_PG_MVARCHAR ? MVARCHAR_OID : 17;
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
				(charset != CS_UTF8 && charset != CS_NONE && charset != CS_ASCII &&
				charset != CS_PG_MCHAR && charset != CS_PG_MVARCHAR))
			{
				throw ProtocolError("0A000", "Parameter character set is not supported");
			}
			if (capacity > MAX_VARYING)
				throw ProtocolError("0A000", "Text parameter exceeds supported limits");
			oid = charset == CS_PG_MCHAR ? MCHAR_OID : charset == CS_PG_MVARCHAR ? MVARCHAR_OID :
				type == SQL_TEXT ? 1042 : 1043;
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
			const bool compatibleText = (profileString(oid) || type == SQL_TEXT || type == SQL_VARYING) &&
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

		if (oid == 4089)
		{
			builder->setType(&status, i, SQL_VARYING);
			check(&status);
			builder->setSubType(&status, i, 0);
			check(&status);
			builder->setLength(&status, i, 0);
			check(&status);
			builder->setCharSet(&status, i, CS_UTF8);
			check(&status);
		}

		types.push_back(oid);
		fields.push_back({type == SQL_TEXT || oid == 4089 ? SQL_VARYING : type,
			oid == 4089 ? 0 : capacity, 0, 0, charset, scale});
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
			(field.type == SQL_VARYING ? sizeof(ISC_USHORT) : 0), length);
		checkRange(field.nullOffset, sizeof(ISC_SHORT), length);
	}
	return length;
}

std::size_t InputCodec::retainedBytes() const noexcept
{
	std::size_t size = sizeof(*this) + fields.capacity() * sizeof(Field) + types.capacity() * sizeof(std::uint32_t) +
		blobs.capacity() * sizeof(blobs[0]);
	for (const auto& blob : blobs)
		if (blob) size += blob->capacity();
	return size;
}

std::vector<unsigned char> InputCodec::encode(const std::vector<std::optional<std::string>>& inputValues,
	const std::vector<std::int16_t>& formats)
{
	auto values = inputValues;
	if (values.size() != fields.size())
		throw ProtocolError("08P01", "Bind parameter count does not match the prepared statement");
	if (!formats.empty() && formats.size() != 1 && formats.size() != fields.size())
		throw ProtocolError("08P01", "Bind parameter format count is invalid");
	for (auto format : formats)
	{
		if (format != 0 && format != 1)
			throw ProtocolError("08P01", "Bind parameter format code is invalid");
	}
	FbLocalStatus status;
	RefPtr<IMetadataBuilder> builder;
	auto nextFields = fields;
	for (unsigned i = 0; i < fields.size(); ++i)
	{
		const bool pgString = fields[i].charset == CS_PG_MCHAR || fields[i].charset == CS_PG_MVARCHAR;
		if ((!pgString && types[i] != 4089) || !values[i]) continue;
		const auto format = formats.empty() ? 0 : formats[formats.size() == 1 ? 0 : i];
		if (format && types[i] == 4089)
		{
			if (values[i]->size() != 4)
				throw ProtocolError("22P03", "Invalid binary regnamespace parameter length");
			std::uint32_t number = 0;
			for (unsigned char c : *values[i]) number = (number << 8) | c;
			values[i] = std::to_string(number);
		}
		else if (format && profileString(types[i])) values[i] = stringFromBinary(*values[i]);
		validateText(*values[i]);
		if (fields[i].type == SQL_BLOB)
		{
			if (fields[i].charset == CS_PG_MCHAR)
				while (!values[i]->empty() && values[i]->back() == ' ') values[i]->pop_back();
			continue;
		}
		if (values[i]->size() > MAX_VARYING)
			throw ProtocolError("0A000", "Native text message field exceeds supported limits");
		if (!builder)
		{
			builder.assignRefNoIncr(bindMetadata->getBuilder(&status));
			check(&status);
		}
		nextFields[i].length = values[i]->size();
		builder->setLength(&status, i, values[i]->size());
		check(&status);
		builder->setCharSet(&status, i, CS_UTF8);
		check(&status);
	}
	RefPtr<IMessageMetadata> nextMetadata(bindMetadata);
	if (builder)
	{
		nextMetadata.assignRefNoIncr(builder->getMetadata(&status));
		check(&status);
	}
	const auto nextLength = layout(nextMetadata, nextFields);
	// Decode into temporary ownership: a failed Bind cannot alter the old portal.
	std::vector<std::optional<std::string>> nextBlobs(fields.size());
	std::size_t remaining = MAX_MESSAGE - nextLength;
	for (unsigned i = 0; i < fields.size(); ++i)
	{
		if (fields[i].type != SQL_BLOB || !values[i])
			continue;
		const auto format = formats.empty() ? 0 : formats[formats.size() == 1 ? 0 : i];
		const bool pgString = fields[i].charset == CS_PG_MCHAR || fields[i].charset == CS_PG_MVARCHAR;
		nextBlobs[i] = format || pgString ? *values[i] : byteaText(*values[i], remaining);
		if (nextBlobs[i]->size() > remaining)
			throw ProtocolError("0A000", "Binary BLOB parameters exceed supported limits");
		remaining -= nextBlobs[i]->size();
	}

	std::vector<unsigned char> buffer(nextLength);
	for (unsigned i = 0; i < fields.size(); ++i)
	{
		const auto& field = nextFields[i];
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
		{
			store<ISC_USHORT>(address, value.size());
			std::memcpy(address + sizeof(ISC_USHORT), value.data(), value.size());
			break;
		}
		case SQL_BLOB:
			break; // Filled by materialize only after the application transaction starts.
		default:
			invalidMetadata();
		}
	}
	bindMetadata = nextMetadata;
	fields.swap(nextFields);
	length = nextLength;
	blobs.swap(nextBlobs);
	return buffer;
}

void InputCodec::materialize(IAttachment* attachment, ITransaction* transaction,
	std::vector<unsigned char>& buffer)
{
	if (!attachment || !transaction || buffer.size() != length)
		invalidMetadata();
	FbLocalStatus status;
	std::vector<RefPtr<IBlob>> opened(blobs.size());
	std::vector<ISC_QUAD> ids(blobs.size());
	try
	{
		for (unsigned i = 0; i < blobs.size(); ++i)
		{
			if (!blobs[i]) continue;
			opened[i].assignRefNoIncr(attachment->createBlob(&status, transaction, &ids[i], 0, nullptr));
			check(&status);
			const auto& bytes = *blobs[i];
			for (std::size_t offset = 0; offset < bytes.size();)
			{
				const unsigned size = std::min<std::size_t>(65535, bytes.size() - offset);
				opened[i]->putSegment(&status, size, bytes.data() + offset);
				check(&status);
				offset += size;
			}
		}
		// Keep all handles cancellable until every parameter has been written.
		for (auto& blob : opened)
		{
			if (!blob) continue;
			blob->close(&status);
			check(&status);
			blob.clear();
		}
		for (unsigned i = 0; i < blobs.size(); ++i)
			if (blobs[i]) store<ISC_QUAD>(buffer.data() + fields[i].offset, ids[i]);
	}
	catch (...)
	{
		for (auto& blob : opened)
		{
			if (!blob) continue;
			FbLocalStatus cleanup;
			blob->cancel(&cleanup);
			if (!(cleanup->getState() & IStatus::STATE_ERRORS)) blob.clear();
		}
		throw;
	}

}

} // namespace Firebird::PgWire
