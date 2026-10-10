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

#ifndef REMOTE_SERVER_WIRE_AUTHENTICATION_H
#define REMOTE_SERVER_WIRE_AUTHENTICATION_H

#include "firebird/Interface.h"

#include <atomic>

namespace Firebird::WireProtocol
{

// Internal host service, not a plugin ABI. The caller has already checked its
// endpoint's database allowlist and transport policy. Peer/protocol information
// comes from the host, never from a client-supplied DPB or provider configuration.
// The helper accepts configured local aliases only, authenticates with the
// database's permitted Srp256 client/server pair and attaches using Engine14.
// The security database must also be local and permit the Engine14 provider.
//
// Returns one owned attachment reference, or nullptr with a status error.
// No attachment registry or implicit transaction is created here. The caller
// must register/clean up a result even if stopping became true during attach;
// it owns cancellation, detach and provider-shutdown synchronization.
// Password input is borrowed for this call. Its bounded internal copy is wiped
// before attach; the caller must also erase its own protocol input buffer.
IAttachment* authenticateAndAttach(CheckStatusWrapper* status,
	const char* databaseAlias, const char* user, unsigned passwordLength, const char* password,
	const char* peerAddress, unsigned peerPort, const char* protocolName,
	const char* applicationName, const std::atomic<bool>& stopping);

} // namespace Firebird::WireProtocol

#endif // REMOTE_SERVER_WIRE_AUTHENTICATION_H
