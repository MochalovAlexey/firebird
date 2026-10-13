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

#ifndef PLUGINS_WIRE_PG_INPUT_CODEC_H
#define PLUGINS_WIRE_PG_INPUT_CODEC_H

#include "Protocol.h"
#include "../common/classes/RefCounted.h"

namespace Firebird::PgWire
{

// Converts Bind values to the native input message without changing SQL.
// Owns its bind metadata; the original descriptor is only borrowed by the ctor.
// Nonzero Parse OIDs must match the inferred type, except interchangeable text
// OIDs 25/1042/1043. Missing or zero hints use the native inferred OID.
class InputCodec final
{
public:
	explicit InputCodec(IMessageMetadata* logicalMetadata,
		const std::vector<std::uint32_t>& parseTypes = {});

	IMessageMetadata* metadata() const noexcept { return bindMetadata; }
	unsigned messageLength() const noexcept { return length; }
	std::size_t retainedBytes() const noexcept;
	const std::vector<std::uint32_t>& parameterTypes() const noexcept { return types; }
	std::vector<unsigned char> encode(const std::vector<std::optional<std::string>>& values = {},
		const std::vector<std::int16_t>& formats = {}) const;

private:
	struct Field
	{
		unsigned type, length, offset, nullOffset, charset;
		int scale;
	};

	RefPtr<IMessageMetadata> bindMetadata;
	unsigned length = 0;
	std::vector<std::uint32_t> types;
	std::vector<Field> fields;
};

} // namespace Firebird::PgWire

#endif // PLUGINS_WIRE_PG_INPUT_CODEC_H
