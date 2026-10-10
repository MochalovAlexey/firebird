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

#ifndef PLUGINS_WIRE_PG_EXECUTE_H
#define PLUGINS_WIRE_PG_EXECUTE_H

#include "Protocol.h"

namespace Firebird::PgWire
{

// The attachment is borrowed from the host. SQL bytes go unchanged to prepare.
// Each Query owns one implicit transaction and closes all child interfaces.
// Sends result/error, but not ReadyForQuery. false means the session must close.
// Transport failures propagate to the session; they must never resume framing.
bool executeQuery(Protocol& protocol, IAttachment* attachment, std::string_view sql,
	unsigned timeoutMs);

} // namespace Firebird::PgWire

#endif // PLUGINS_WIRE_PG_EXECUTE_H
