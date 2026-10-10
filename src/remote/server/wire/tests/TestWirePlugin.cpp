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

// This module is built only by wire_tests and is never installed as a plugin.
#include "firebird/Interface.h"

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <string>
#include <utility>

using namespace Firebird;

namespace
{

std::mutex eventMutex;
IMaster* master = nullptr;

void event(const std::string& text)
{
	std::lock_guard<std::mutex> guard(eventMutex);
	const char* path = std::getenv("FB_WIRE_TEST_EVENTS");
	if (path)
	{
		if (FILE* file = std::fopen(path, "a"))
		{
			std::fprintf(file, "%s\n", text.c_str());
			std::fclose(file);
		}
	}
}

void fail(CheckStatusWrapper* status)
{
	const ISC_STATUS errors[] = {isc_arg_gds, isc_random, isc_arg_string,
		reinterpret_cast<ISC_STATUS>("wire test injected error"), isc_arg_end};
	status->setErrors(errors);
}

std::string readConfig(CheckStatusWrapper* status, IConfig* config, const char* name)
{
	IConfigEntry* entry = config->find(status, name);
	if (!entry)
		return {};
	const std::string value(entry->getValue());
	entry->release();
	return value;
}

class Session final : public IWireSessionImpl<Session, CheckStatusWrapper>
{
public:
	Session(std::string label, std::string mode, IWireServerHost* host, IWireConnection* connection)
		: label(std::move(label)), mode(std::move(mode)), host(host), connection(connection)
	{
		if (this->mode == "session_old")
		{
			oldVTable = *static_cast<IWireSession::VTable*>(cloopVTable);
			oldVTable.version = IWireSession::VERSION - 1;
			cloopVTable = &oldVTable;
		}
		event(this->label + ":session.create");
	}

	void addRef() override { ++refs; }
	int release() override
	{
		if (--refs)
			return 1;
		delete this;
		return 0;
	}

	void run(CheckStatusWrapper* status) override
	{
		event(label + ":session.run");
		host->log(IWireServerHost::LOG_INFO, label.size(), label.c_str());
		if (mode == "run_error")
		{
			fail(status);
			return;
		}

		const bool tcp = mode == "tcp_echo";
		const unsigned peerPort = connection->getPeerPort();
		if (std::string(connection->getPeerAddress()) != "127.0.0.1" ||
			(tcp ? (peerPort == 0 || peerPort > 65535) : peerPort != 43210) ||
			connection->isEncrypted())
		{
			fail(status);
			return;
		}
		if (tcp)
		{
			const std::string greeting = label + '\n';
			unsigned position = 0;
			while (position != greeting.size())
			{
				const unsigned written = connection->write(status, greeting.size() - position,
					greeting.data() + position, 0);
				if ((status->getState() & IStatus::STATE_ERRORS) || written == 0)
					return;
				position += written;
			}
		}

		char buffer[11];
		while (!stopped.load())
		{
			unsigned length;
			{
				std::unique_lock<std::mutex> guard(readMutex, std::defer_lock);
				if (mode == "stop_mutex")
					guard.lock();
				length = connection->read(status, sizeof(buffer), buffer, 0);
			}
			if ((status->getState() & IStatus::STATE_ERRORS) || length == 0)
				return;
			unsigned position = 0;
			while (position != length && !stopped.load())
			{
				const unsigned written = connection->write(status, length - position,
					buffer + position, 0);
				if ((status->getState() & IStatus::STATE_ERRORS) || written == 0)
					return;
				position += written;
			}
		}
	}

	void stop() override
	{
		// Fault injection only: this mode deliberately violates the nonblocking
		// stop contract. The host must wake transport before entering plugin code.
		std::unique_lock<std::mutex> guard(readMutex, std::defer_lock);
		if (mode == "stop_mutex")
			guard.lock();
		stopped = true;
		// Deliberately does not wake I/O: the host wrapper must shut down transport.
	}

private:
	~Session()
	{
		if (stopped)
			event(label + ":session.stopped");
		// These callbacks must remain valid after the endpoint releases its plugin.
		const std::string message = label + ":session.destroy";
		host->log(IWireServerHost::LOG_INFO, message.size(), message.c_str());
		connection->getPeerPort();
		event(message);
	}

	std::atomic<unsigned> refs{1};
	std::atomic<bool> stopped{false};
	std::mutex readMutex;
	std::string label;
	std::string mode;
	IWireServerHost* host;
	IWireConnection* connection;
	IWireSession::VTable oldVTable{};
};

class Protocol final : public IWireProtocolImpl<Protocol, CheckStatusWrapper>
{
public:
	Protocol(std::string label, std::string mode)
		: label(std::move(label)), mode(std::move(mode))
	{
		if (this->mode == "protocol_old")
		{
			oldVTable = *static_cast<IWireProtocol::VTable*>(cloopVTable);
			oldVTable.version = IWireProtocol::VERSION - 1;
			cloopVTable = &oldVTable;
		}
		event(this->label + ":plugin.create");
	}

	void addRef() override { ++refs; }
	int release() override
	{
		if (--refs)
			return 1;
		delete this;
		return 0;
	}
	void setOwner(IReferenceCounted* value) override { owner = value; }
	IReferenceCounted* getOwner() override { return owner; }

	IWireSession* createSession(CheckStatusWrapper* status,
		IWireServerHost* host, IWireConnection* connection) override
	{
		if (mode == "create_error")
		{
			fail(status);
			return nullptr;
		}
		if (mode == "create_null")
			return nullptr;
		if (mode == "create_error_object")
			fail(status);
		return new Session(label, mode, host, connection);
	}

private:
	~Protocol() { event(label + ":plugin.destroy"); }
	std::atomic<unsigned> refs{1};
	IReferenceCounted* owner = nullptr;
	std::string label;
	std::string mode;
	IWireProtocol::VTable oldVTable{};
};

class Factory final : public IPluginFactoryImpl<Factory, CheckStatusWrapper>
{
public:
	IPluginBase* createPlugin(CheckStatusWrapper* status, IPluginConfig* config) override
	{
		IConfig* defaults = config->getDefaultConfig(status);
		if (!defaults)
			return nullptr;
		const std::string label = readConfig(status, defaults, "Label");
		const std::string mode = readConfig(status, defaults, "Mode");
		defaults->release();
		if (status->getState() & IStatus::STATE_ERRORS)
			return nullptr;
		if (mode == "factory_error")
		{
			fail(status);
			return nullptr;
		}
		if (mode == "factory_null")
			return nullptr;
		return new Protocol(label, mode);
	}
};

class Module final : public IPluginModuleImpl<Module, CheckStatusWrapper>
{
public:
	~Module()
	{
		if (manager)
		{
			manager->unregisterModule(this);
			doClean();
		}
		event("module.unload");
	}
	void doClean() override
	{
		event("module.cleanup");
		manager = nullptr;
	}
	void threadDetach() override {}
	IPluginManager* manager = nullptr;
};

Factory factory;
Module module;

} // namespace

extern "C" FB_DLL_EXPORT void FB_PLUGIN_ENTRY_POINT(IMaster* masterInterface)
{
	master = masterInterface;
	module.manager = master->getPluginManager();
	module.manager->registerModule(&module);
	module.manager->registerPluginFactory(IPluginManager::TYPE_WIRE_PROTOCOL, "WireTest", &factory);
	event("module.load");
}
