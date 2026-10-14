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

#ifndef PGWIRE_FLOAT_CODEC_H
#define PGWIRE_FLOAT_CODEC_H

#include <string>
#include <string_view>

namespace Firebird::PgWire
{

std::string formatFloat(const void* native, unsigned type, bool binary);
void parseFloat(std::string_view value, unsigned type, bool binary, void* native);

} // namespace Firebird::PgWire

#endif // PGWIRE_FLOAT_CODEC_H
