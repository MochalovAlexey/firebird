/*
 *  The contents of this file are subject to the Initial
 *  Developer's Public License Version 1.0 (the "License");
 *  you may not use this file except in compliance with the
 *  License. You may obtain a copy of the License at
 *  http://www.ibphoenix.com/main.nfs?a=ibp_idpl.
 *
 *  Software distributed under the License is distributed AS IS,
 *  WITHOUT WARRANTY OF ANY KIND, either express or implied.
 *  See the License for the specific language governing rights
 *  and limitations under the License.
 *
 *  The Original Code was created for the Firebird Open Source RDBMS project.
 *
 *  Copyright (c) 2026 Firebird Project and all contributors signed below.
 *
 *  All Rights Reserved.
 *  Contributor(s): ______________________________________.
 */

#ifndef REMOTE_SERVER_WIRE_SERVER_H
#define REMOTE_SERVER_WIRE_SERVER_H

namespace Firebird::WireProtocol
{

// The native listener is already bound. Its descriptor is borrowed only during
// this call; it is never changed or closed by the additional protocol host.
// Failures are logged locally and must not abort native startup.
void startServer(int nativeSocket, bool supportedMode) noexcept;

} // namespace Firebird::WireProtocol

#endif
