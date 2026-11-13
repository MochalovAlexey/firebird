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

/* Profile string wire formats. Storage and comparison remain in native intl. */
#ifndef PGWIRE_STRING_CODEC_H
#define PGWIRE_STRING_CODEC_H
#include "Protocol.h"
#include "../common/unicode_util.h"
#include <vector>

namespace Firebird::PgWire {
// Stable OIDs owned by this installed profile; not Postgres Pro extension OIDs.
inline constexpr std::uint32_t MCHAR_OID = 0x00fb0001;
inline constexpr std::uint32_t MVARCHAR_OID = 0x00fb0002;
inline bool profileString(std::uint32_t oid)
{
	return oid == MCHAR_OID || oid == MVARCHAR_OID;
}
inline std::string stringFromBinary(std::string_view bytes)
{
	// mchar 2.3 receives complete UTF16 code units and ignores a final odd byte.
	bytes = bytes.substr(0, bytes.size() & ~std::size_t(1));
	if (bytes.empty())
		return {};
	std::vector<USHORT> units(bytes.size() / 2);
	for (std::size_t i = 0; i < units.size(); ++i)
		units[i] = static_cast<unsigned char>(bytes[2*i]) |
			(static_cast<unsigned char>(bytes[2*i+1]) << 8);
	std::string result(units.size() * 3, '\0');
	USHORT error = 0;
	ULONG position = 0;
	const auto size = UnicodeUtil::utf16ToUtf8(bytes.size(), units.data(), result.size(),
		reinterpret_cast<UCHAR*>(result.data()), &error, &position);
	if (error)
		throw ProtocolError("22021", "Invalid UTF16 string parameter");
	result.resize(size);
	return result;
}
inline std::string stringToBinary(std::string_view text)
{
	if (text.empty())
		return {};
	std::vector<USHORT> units(text.size());
	USHORT error = 0;
	ULONG position = 0;
	const auto size = UnicodeUtil::utf8ToUtf16(text.size(), reinterpret_cast<const UCHAR*>(text.data()),
		units.size() * sizeof(USHORT), units.data(), &error, &position);
	if (error)
		throw ProtocolError("22021", "Invalid UTF8 engine string result");
	std::string result(size, '\0');
	for (unsigned i = 0; i < size / 2; ++i)
	{
		result[2*i] = units[i] & 255;
		result[2*i+1] = units[i] >> 8;
	}
	return result;
}
}
#endif
