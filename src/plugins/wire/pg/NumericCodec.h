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
#ifndef PLUGINS_WIRE_PG_NUMERIC_CODEC_H
#define PLUGINS_WIRE_PG_NUMERIC_CODEC_H

#include "Protocol.h"

namespace Firebird::PgWire
{

// SQL_SHORT/LONG/INT64 with scale 0..-18, or SQL_INT128 with scale 0..-38.
// The caller supplies sufficient native storage; unaligned addresses are valid.
// Conversion is exact: unsupported fractional precision is never rounded away.
std::string formatNumeric(const void* native, unsigned type, int scale, bool binary);
void parseNumeric(std::string_view value, unsigned type, int scale, bool binary, void* native);

} // namespace Firebird::PgWire

#endif // PLUGINS_WIRE_PG_NUMERIC_CODEC_H
