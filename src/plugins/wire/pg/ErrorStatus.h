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

#ifndef PLUGINS_WIRE_PG_ERROR_STATUS_H
#define PLUGINS_WIRE_PG_ERROR_STATUS_H

#include "firebird/Interface.h"

#include <string>

namespace Firebird::PgWire
{

struct Diagnostic
{
	std::string state = "XX000";
	const char* message = "Firebird SQL operation failed";
	bool fatal = false;
};

// Classify structured status arguments only. Message strings never influence
// classification or appear in the client diagnostic. Explicit SQLSTATE is a
// fallback; native shutdown, cancellation and privilege causes take priority.
Diagnostic diagnose(const IStatus* status);

} // namespace Firebird::PgWire

#endif // PLUGINS_WIRE_PG_ERROR_STATUS_H
