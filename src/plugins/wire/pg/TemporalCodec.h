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

#ifndef PLUGINS_WIRE_PG_TEMPORAL_CODEC_H
#define PLUGINS_WIRE_PG_TEMPORAL_CODEC_H

#include <string>
#include <string_view>

namespace Firebird::PgWire
{

// SQL_TYPE_DATE/TIME and SQL_TIMESTAMP without timezone; native may be unaligned.
// Text uses ISO dates and HH:MM:SS with optional fractional seconds, up to six
// input digits. Values must fit years 0001..9999 and exact native 100us ticks.
// Binary uses PostgreSQL network-order days/microseconds from 2000-01-01.
// NULL is handled by the caller. Failed parsing leaves native unchanged.
std::string formatTemporal(const void* native, unsigned type, bool binary);
void parseTemporal(std::string_view value, unsigned type, bool binary, void* native);

} // namespace Firebird::PgWire

#endif // PLUGINS_WIRE_PG_TEMPORAL_CODEC_H
