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

#include "firebird.h"
#include "ProtocolPlugin.h"
#include "../common/StatusArg.h"
#include "../common/status.h"

#include <cstring>
#include <utility>

namespace Firebird::WireProtocol
{

namespace
{

void requireVersion(IVersioned* iface, unsigned version, const char* name)
{
	if (iface->cloopVTable->version < version)
	{
		(Arg::Gds(isc_interface_version_too_old) << Arg::Num(version) <<
			Arg::Num(iface->cloopVTable->version) << name).raise();
	}
}

} // namespace

ProtocolPlugin::ProtocolPlugin(IPluginManager* manager, const EndpointConfig& endpoint)
	: manager(manager), endpoint(endpoint)
{
}

ProtocolPlugin::~ProtocolPlugin()
{
	// The manager releases the module owner after returning from plugin code.
	if (plugin)
		manager->releasePlugin(plugin);
}

std::shared_ptr<ProtocolPlugin> ProtocolPlugin::load(IMaster* master, const EndpointConfig& endpoint)
{
	if (endpoint.plugin.empty() || !Detail::validConfiguredName(endpoint.plugin) ||
		endpoint.port == 0 || endpoint.port > 65535)
	{
		(Arg::Gds(isc_random) << "Invalid wire protocol endpoint").raise();
	}

	auto* manager = master->getPluginManager();
	auto loaded = std::shared_ptr<ProtocolPlugin>(new ProtocolPlugin(manager, endpoint));
	FbLocalStatus status;

	// Always request exactly one configured name. PluginSet skips missing optional
	// modules in a list; doing that here would break the positional port mapping.
	RefPtr<IPluginSet> plugins(REF_NO_INCR, manager->getPlugins(&status,
		IPluginManager::TYPE_WIRE_PROTOCOL, endpoint.plugin.c_str(), nullptr));
	check(&status);
	if (!plugins || !plugins->getName())
	{
		(Arg::Gds(isc_random) <<
			("Wire protocol plugin is unavailable: " + endpoint.plugin).c_str()).raise();
	}

	if (std::strcmp(plugins->getName(), endpoint.plugin.c_str()) != 0)
		(Arg::Gds(isc_random) << "Wire protocol configured name mismatch").raise();

	loaded->plugin = static_cast<IWireProtocol*>(plugins->getPlugin(&status));
	check(&status);
	if (!loaded->plugin)
		(Arg::Gds(isc_random) << "Wire protocol factory returned no plugin").raise();

	requireVersion(loaded->plugin, IWireProtocol::VERSION, "IWireProtocol");
	return loaded;
}

std::unique_ptr<ProtocolSession> ProtocolPlugin::createSession(
	IWireServerHost* host, IWireConnection* connection)
{
	if (!host || !connection)
		(Arg::Gds(isc_random) << "Wire protocol session requires host and connection").raise();

	requireVersion(host, IWireServerHost::VERSION, "IWireServerHost");
	requireVersion(connection, IWireConnection::VERSION, "IWireConnection");
	auto result = std::unique_ptr<ProtocolSession>(new ProtocolSession(shared_from_this(), host, connection));
	FbLocalStatus status;
	{
		std::lock_guard<std::mutex> guard(createMutex);
		result->session.assignRefNoIncr(plugin->createSession(&status, host, connection));
	}
	check(&status);
	if (!result->session)
		(Arg::Gds(isc_random) << "Wire protocol plugin returned no session").raise();

	requireVersion(result->session, IWireSession::VERSION, "IWireSession");
	return result;
}

ProtocolSession::ProtocolSession(std::shared_ptr<ProtocolPlugin> plugin,
	IWireServerHost* host, IWireConnection* connection)
	: plugin(std::move(plugin)), host(host), connection(connection)
{
}

void ProtocolSession::run(CheckStatusWrapper* status)
{
	if (started.exchange(true))
	{
		(Arg::Gds(isc_random) << "Wire protocol session has already run").copyTo(status);
		return;
	}

	session->run(status);
}

void ProtocolSession::stop() noexcept
{
	// Wake transport waits before entering plugin code, even if a faulty plugin
	// tries to acquire a lock held by its blocked I/O callback in stop().
	connection->shutdown();
	session->stop();
}

} // namespace Firebird::WireProtocol
