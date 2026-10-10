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

#ifndef REMOTE_SERVER_WIRE_SOCKET_CONNECTION_H
#define REMOTE_SERVER_WIRE_SOCKET_CONNECTION_H

#include "firebird/Interface.h"

namespace Firebird::WireProtocol
{

// POSIX host transport. Takes ownership of socket immediately, including on
// failure, and returns one owned reference. The caller must supply a positive
// finite default timeout and the accepted connection's numeric peer address.
// Setup errors throw Firebird::Exception. I/O errors use the interface status.
// The descriptor is closed only when the final connection reference is released.
IWireConnection* createSocketConnection(int socket, unsigned defaultTimeoutMs,
	const char* peerAddress, unsigned peerPort);

} // namespace Firebird::WireProtocol

#endif // REMOTE_SERVER_WIRE_SOCKET_CONNECTION_H
