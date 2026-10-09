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

#ifndef REMOTE_SERVER_WIRE_PROTOCOL_PLUGIN_H
#define REMOTE_SERVER_WIRE_PROTOCOL_PLUGIN_H

#include "firebird/Interface.h"
#include "../common/classes/RefCounted.h"
#include "EndpointConfig.h"

#include <atomic>
#include <memory>
#include <mutex>

namespace Firebird::WireProtocol
{

class ProtocolSession;

// Internal host ownership, never exposed through the plugin ABI. One instance
// per configured endpoint. Sessions retain it even after the endpoint is removed.
class ProtocolPlugin final : public std::enable_shared_from_this<ProtocolPlugin>
{
public:
	static std::shared_ptr<ProtocolPlugin> load(IMaster* master, const EndpointConfig& endpoint);
	~ProtocolPlugin();

	ProtocolPlugin(const ProtocolPlugin&) = delete;
	ProtocolPlugin& operator=(const ProtocolPlugin&) = delete;

	const EndpointConfig& getEndpoint() const noexcept { return endpoint; }
	std::unique_ptr<ProtocolSession> createSession(IWireServerHost* host, IWireConnection* connection);

private:
	ProtocolPlugin(IPluginManager* manager, const EndpointConfig& endpoint);

	IPluginManager* const manager;
	const EndpointConfig endpoint;
	IWireProtocol* plugin = nullptr;
	std::mutex createMutex;
};

// Do not expose the raw plugin session: every callback and its final release
// must execute while the originating plugin is pinned. The worker/registry must
// finish run() and all stop() calls before destroying this object.
class ProtocolSession final
{
public:
	ProtocolSession(const ProtocolSession&) = delete;
	ProtocolSession& operator=(const ProtocolSession&) = delete;

	void run(CheckStatusWrapper* status);
	void stop() noexcept;

private:
	friend class ProtocolPlugin;
	ProtocolSession(std::shared_ptr<ProtocolPlugin> plugin,
		IWireServerHost* host, IWireConnection* connection);

	// Destruction order matters: session first, borrowed inputs second, plugin last.
	const std::shared_ptr<ProtocolPlugin> plugin;
	const RefPtr<IWireServerHost> host;
	const RefPtr<IWireConnection> connection;
	RefPtr<IWireSession> session;
	std::atomic<bool> started{false};
};

} // namespace Firebird::WireProtocol

#endif // REMOTE_SERVER_WIRE_PROTOCOL_PLUGIN_H
