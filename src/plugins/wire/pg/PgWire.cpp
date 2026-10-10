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

#include "firebird.h"
#include "Protocol.h"
#include "Execute.h"
#include "../common/classes/ImplementHelper.h"
#include "../common/status.h"

#include <atomic>
#include <chrono>
#include <cstring>
#include <string>

using namespace Firebird;
using namespace Firebird::PgWire;

namespace
{

bool hasError(const IStatus* status, ISC_STATUS code)
{
	// Inspect structured arguments, never message text or arbitrary argument values.
	for (const ISC_STATUS* p = status->getErrors(); *p != isc_arg_end; )
	{
		const auto tag = *p++;
		if (tag == isc_arg_gds && *p == code)
			return true;
		p += tag == isc_arg_cstring ? 2 : 1;
	}
	return false;
}

const char* startupSqlState(const IStatus* status)
{
	if (hasError(status, isc_att_shutdown) || hasError(status, isc_shutdown))
		return "57P01";
	if (hasError(status, isc_login) || hasError(status, isc_login_error))
		return "28P01";
	if (hasError(status, isc_unavailable))
		return "3D000";
	if (hasError(status, isc_no_priv))
		return "42501";
	return "XX000";
}

class Session final : public RefCntIface<IWireSessionImpl<Session, CheckStatusWrapper>>
{
public:
	Session(IWireServerHost* host, IWireConnection* connection, Limits limits)
		: host(host), connection(connection), limits(limits) {}

	void run(CheckStatusWrapper* status) override
	{
		// The host owns this borrowed handle and closes it if run exits abnormally.
		struct AttachmentScope
		{
			IWireServerHost* host;
			IAttachment* attachment = nullptr;
			~AttachmentScope()
			{
				if (attachment)
				{
					FbLocalStatus cleanup;
					host->detachDatabase(&cleanup, attachment);
				}
			}
		} owned{host};
		Protocol protocol(MasterInterfacePtr(), connection, limits);
		try
		{
			if (stopping())
				return;
			Startup startup;
			if (!protocol.readStartup(startup))
				return;
			const auto deadline = protocol.startupDeadline();
			protocol.authenticationCleartext(deadline);
			Message message;
			if (!protocol.readMessage(message, deadline))
				return;
			if (message.type == 'X')
				return;
			if (message.type != 'p')
				throw ProtocolError("08P01", "Expected a password message");
			if (stopping())
				return;
			FbLocalStatus authStatus;
			const auto password = message.text();
			owned.attachment = host->attachDatabase(&authStatus, startup.database.c_str(),
				startup.user.c_str(), password.size(), password.data(), startup.applicationName.c_str());
			message.clear();
			if (!owned.attachment || (authStatus->getState() & IStatus::STATE_ERRORS))
			{
				protocol.error(startupSqlState(&authStatus), "Database connection rejected", true, deadline);
				return;
			}
			if (stopping())
				return;
			protocol.authenticationOk(deadline);
			protocol.parameterStatus("server_version", "16.0 (Firebird pgwire prototype)", deadline);
			protocol.parameterStatus("server_encoding", "UTF8", deadline);
			protocol.parameterStatus("client_encoding", "UTF8", deadline);
			protocol.parameterStatus("DateStyle", "ISO, MDY", deadline);
			protocol.parameterStatus("standard_conforming_strings", "on", deadline);
			protocol.parameterStatus("application_name", startup.applicationName, deadline);
			protocol.ready('I', deadline);

			while (!stopping() && protocol.readMessage(message))
			{
				if (message.type == 'X')
					return;
				if (message.type == 'Q')
				{
					if (!executeQuery(protocol, owned.attachment, message.text(), limits.timeoutMs))
						return;
					protocol.ready();
				}
				else
					throw ProtocolError("0A000", "Frontend message is not supported yet");
				message.clear();
			}
		}
		catch (const ProtocolError& ex)
		{
			// A failed output may have sent only a frame prefix. Appending an
			// ErrorResponse would corrupt that frame; close the transport instead.
			if (std::strcmp(ex.sqlState(), "08006") != 0)
				sendFatal(protocol, ex.sqlState(), ex.what());
		}
		catch (const FbException&)
		{
			// Transport errors close this session. Credentials and raw status text
			// must never become client diagnostics or host log messages.
		}
		catch (const Exception& ex)
		{
			ex.stuffException(status);
			sendFatal(protocol, "XX000", "Protocol session failed");
		}
		catch (...)
		{
			(Arg::Gds(isc_random) << "PgWire session failed").copyTo(status);
			sendFatal(protocol, "XX000", "Protocol session failed");
		}
	}

	void stop() override { stopped = true; }

private:
	bool stopping() const { return stopped || host->isStopping(); }
	void sendFatal(Protocol& protocol, const char* state, const char* message) noexcept
	{
		if (stopping())
			return;
		try
		{
			protocol.error(state, message, true, Protocol::Clock::now() + std::chrono::milliseconds(100));
		}
		catch (...) {}
	}
	IWireServerHost* const host;
	IWireConnection* const connection;
	const Limits limits;
	std::atomic<bool> stopped{false};
};

class Plugin final : public StdPlugin<IWireProtocolImpl<Plugin, CheckStatusWrapper>>
{
public:
	explicit Plugin(IPluginConfig* factory)
	{
		FbLocalStatus status;
		RefPtr<IConfig> config(REF_NO_INCR, factory->getDefaultConfig(&status));
		check(&status);
		if (!config)
			return;
		RefPtr<IConfigEntry> timeout(REF_NO_INCR, config->find(&status, "IOTimeout"));
		check(&status);
		if (timeout)
		{
			const auto value = timeout->getIntValue();
			if (value < 1 || value > 300000)
				(Arg::Gds(isc_random) << "PgWire IOTimeout must be between 1 and 300000 ms").raise();
			limits.timeoutMs = value;
		}
	}
	IWireSession* createSession(CheckStatusWrapper* status, IWireServerHost* host,
		IWireConnection* connection) override
	{
		try
		{
			if (!host || !connection || host->cloopVTable->version < IWireServerHost::VERSION)
				(Arg::Gds(isc_random) << "PgWire requires host attachment services").raise();
			auto* session = FB_NEW Session(host, connection, limits);
			session->addRef();
			return session;
		}
		catch (const Exception& ex) { ex.stuffException(status); }
		return nullptr;
	}
private:
	Limits limits;
};

SimpleFactory<Plugin> factory;

} // anonymous namespace

extern "C" FB_DLL_EXPORT void FB_PLUGIN_ENTRY_POINT(IMaster* master)
{
	CachedMasterInterface::set(master);
	PluginManagerInterfacePtr()->registerPluginFactory(IPluginManager::TYPE_WIRE_PROTOCOL, "PgWire", &factory);
	getUnloadDetector()->registerMe();
}
