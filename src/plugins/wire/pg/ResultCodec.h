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

#ifndef PLUGINS_WIRE_PG_RESULT_CODEC_H
#define PLUGINS_WIRE_PG_RESULT_CODEC_H

#include "Protocol.h"
#include "../common/classes/RefCounted.h"

namespace Firebird::PgWire
{

// Metadata and native message conversion shared by Simple and Extended Query.
// Construct before executing: unsupported metadata must not produce side effects.
// The input metadata is borrowed only during construction. The converted fetch
// metadata is owned here; keep this object alive while its cursor is in use.
class ResultCodec final
{
public:
	explicit ResultCodec(IMessageMetadata* logicalMetadata);

	IMessageMetadata* fetchMetadata() const noexcept { return metadata; }
	unsigned messageLength() const noexcept { return length; }
	const std::vector<Column>& columns() const noexcept { return descriptions; }
	std::vector<std::optional<std::string>> decode(const void* buffer, unsigned bufferLength) const;

private:
	struct Field
	{
		unsigned type, length, offset, nullOffset;
		int scale;
	};

	RefPtr<IMessageMetadata> metadata;
	unsigned length = 0;
	std::vector<Column> descriptions;
	std::vector<Field> fields;
};

} // namespace Firebird::PgWire

#endif // PLUGINS_WIRE_PG_RESULT_CODEC_H
