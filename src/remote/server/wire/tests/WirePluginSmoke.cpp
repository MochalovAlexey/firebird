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

// Real PluginManager/module integration. Transport below is a mock byte stream,
// so this executable makes no claims about TCP listeners or PostgreSQL support.
#include "firebird.h"
#include "../ProtocolPlugin.h"
#include "../common/status.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using namespace Firebird;
using namespace Firebird::WireProtocol;
using namespace std::chrono_literals;

namespace
{

void require(bool condition, const char* message)
{
	if (!condition)
		throw std::runtime_error(message);
}

void fail(CheckStatusWrapper* status)
{
	const ISC_STATUS errors[] = {isc_arg_gds, isc_network_error, isc_arg_string,
		reinterpret_cast<ISC_STATUS>("test stream"), isc_arg_end};
	status->setErrors(errors);
}

struct Observations
{
	std::vector<std::string> logs;
	std::string output;
	bool hostDestroyed = false;
	bool connectionDestroyed = false;
	unsigned shutdowns = 0;
	bool readTimedOut = false;
	std::atomic<bool> enteredRead{false};
};

class Host final : public IWireServerHostImpl<Host, CheckStatusWrapper>
{
public:
	explicit Host(Observations& observations) : observations(observations) {}
	void addRef() override { ++refs; }
	int release() override
	{
		if (--refs)
			return 1;
		delete this;
		return 0;
	}
	FB_BOOLEAN isStopping() override { return FB_FALSE; }
	void log(unsigned, unsigned length, const char* text) override
	{
		observations.logs.emplace_back(text, length);
	}
	unsigned references() const { return refs; }

private:
	~Host() { observations.hostDestroyed = true; }
	std::atomic<unsigned> refs{1};
	Observations& observations;
};

class Connection final : public IWireConnectionImpl<Connection, CheckStatusWrapper>
{
public:
	Connection(Observations& observations, bool blocked = false)
		: observations(observations), blocked(blocked)
	{}
	void addRef() override { ++refs; }
	int release() override
	{
		if (--refs)
			return 1;
		delete this;
		return 0;
	}
	unsigned read(CheckStatusWrapper* status, unsigned size, void* buffer, unsigned) override
	{
		std::unique_lock<std::mutex> guard(mutex);
		observations.enteredRead = true;
		if (blocked && !ready.wait_for(guard, 3s, [&] { return closed; }))
			observations.readTimedOut = true;
		if (closed || blocked)
		{
			fail(status);
			return 0;
		}
		const unsigned count = std::min({size, 3u, unsigned(input.size() - position)});
		std::memcpy(buffer, input.data() + position, count);
		position += count;
		return count;
	}
	unsigned write(CheckStatusWrapper* status, unsigned size, const void* buffer, unsigned) override
	{
		std::lock_guard<std::mutex> guard(mutex);
		if (closed)
		{
			fail(status);
			return 0;
		}
		const unsigned count = std::min(size, 2u);
		observations.output.append(static_cast<const char*>(buffer), count);
		return count;
	}
	void shutdown() override
	{
		std::lock_guard<std::mutex> guard(mutex);
		if (!closed)
		{
			closed = true;
			++observations.shutdowns;
		}
		ready.notify_all();
	}
	const char* getPeerAddress() override { return "127.0.0.1"; }
	unsigned getPeerPort() override { return 43210; }
	FB_BOOLEAN isEncrypted() override { return FB_FALSE; }
	unsigned references() const { return refs; }
	static constexpr const char* PAYLOAD = "partial stream payload";

private:
	~Connection() { observations.connectionDestroyed = true; }
	std::atomic<unsigned> refs{1};
	Observations& observations;
	const bool blocked;
	const std::string input = PAYLOAD;
	unsigned position = 0;
	std::mutex mutex;
	std::condition_variable ready;
	bool closed = false;
};

template <typename F> void expectFailure(F action, const char* message, bool injected = false,
	ISC_STATUS expectedCode = 0)
{
	bool failed = false;
	try { action(); }
	catch (const Firebird::Exception& ex)
	{
		failed = true;
		if (injected || expectedCode)
		{
			FbLocalStatus status;
			ex.stuffException(&status);
			if (expectedCode)
				require(status->getErrors()[1] == expectedCode, "unexpected plugin failure status");
			if (injected)
			{
				char text[1024];
				fb_get_master_interface()->getUtilInterface()->formatStatus(text, sizeof(text), &status);
				require(std::strstr(text, "wire test injected error"), "original plugin error was lost");
			}
		}
	}
	require(failed, message);
}

void verifyAliases(IMaster* master)
{
	std::vector<EndpointConfig> endpoints;
	std::string error;
	require(parseEndpointConfig("First,Missing,Second", "31001,31002,31003", endpoints, error),
		"endpoint config rejected");
	auto first = ProtocolPlugin::load(master, endpoints[0]);
	expectFailure([&] { ProtocolPlugin::load(master, endpoints[1]); }, "missing module was accepted");
	auto second = ProtocolPlugin::load(master, endpoints[2]);
	require(first->getEndpoint().port == 31001 && second->getEndpoint().port == 31003,
		"missing middle alias changed positional mapping");

	Observations firstObs, secondObs;
	auto* firstHost = new Host(firstObs);
	auto* firstConnection = new Connection(firstObs);
	auto* secondHost = new Host(secondObs);
	auto* secondConnection = new Connection(secondObs, true);
	auto firstSession = first->createSession(firstHost, firstConnection);
	auto secondSession = second->createSession(secondHost, secondConnection);
	require(firstHost->references() == 2 && firstConnection->references() == 2,
		"session wrapper did not retain borrowed interfaces exactly once");
	firstHost->release();
	firstConnection->release();
	secondHost->release();
	secondConnection->release();
	first.reset();
	second.reset();

	FbLocalStatus status;
	firstSession->run(&status);
	require(!(status->getState() & IStatus::STATE_ERRORS), "first session run failed");
	require(firstObs.output == Connection::PAYLOAD,
		"partial stream transfer corrupted bytes");
	firstSession->run(&status);
	require(status->getState() & IStatus::STATE_ERRORS, "second run was accepted");

	FbLocalStatus secondStatus;
	std::thread worker([&] { secondSession->run(&secondStatus); });
	const auto deadline = std::chrono::steady_clock::now() + 2s;
	while (!secondObs.enteredRead && std::chrono::steady_clock::now() < deadline)
		std::this_thread::yield();
	const bool entered = secondObs.enteredRead;
	// Release the first alias while code from the shared module is still running
	// for the second. Its final callbacks and the later stop must remain valid.
	firstSession.reset();
	const bool firstReleased = firstObs.hostDestroyed && firstObs.connectionDestroyed;
	const bool secondRetained = !secondObs.hostDestroyed && !secondObs.connectionDestroyed;
	secondSession->stop();
	worker.join();
	require(entered && firstReleased && secondRetained, "aliases did not retain independent sessions");
	require(secondStatus->getState() & IStatus::STATE_ERRORS, "second alias shutdown was lost");
	secondSession.reset();
	require(firstObs.hostDestroyed && firstObs.connectionDestroyed &&
		secondObs.hostDestroyed && secondObs.connectionDestroyed, "borrowed interfaces leaked");
	require(firstObs.logs == std::vector<std::string>{"first", "first:session.destroy"},
		"first alias config or destructor callback lost");
	require(secondObs.logs == std::vector<std::string>{"second", "second:session.destroy"},
		"second alias config or destructor callback lost");
}

void verifyErrors(IMaster* master)
{
	for (const char* name : {"FactoryError", "FactoryNull", "WrongRegistration", "RequiredMissing"})
		expectFailure([&] { ProtocolPlugin::load(master, {name, 32001}); }, "bad factory accepted",
			std::strcmp(name, "FactoryError") == 0);
	expectFailure([&] { ProtocolPlugin::load(master, {"OldProtocol", 32001}); },
		"old protocol interface accepted", false, isc_interface_version_too_old);

	for (const char* name : {"CreateError", "CreateNull", "CreateErrorObject", "RunError", "OldSession"})
	{
		auto plugin = ProtocolPlugin::load(master, {name, 32002});
		Observations observations;
		auto* host = new Host(observations);
		auto* connection = new Connection(observations);
		if (std::strcmp(name, "RunError") == 0)
		{
			auto session = plugin->createSession(host, connection);
			FbLocalStatus status;
			session->run(&status);
			require(status->getState() & IStatus::STATE_ERRORS, "run error disappeared");
		}
		else if (std::strcmp(name, "OldSession") == 0)
			expectFailure([&] { plugin->createSession(host, connection); },
				"old session interface accepted", false, isc_interface_version_too_old);
		else
			expectFailure([&] { plugin->createSession(host, connection); }, "bad session accepted",
				std::strcmp(name, "CreateNull") != 0);
		require(host->references() == 1 && connection->references() == 1,
			"session failure leaked borrowed interfaces");
		host->release();
		connection->release();
	}
}

void verifyStop(IMaster* master, const char* alias, bool beforeRun = false)
{
	auto plugin = ProtocolPlugin::load(master, {alias, 33001});
	Observations observations;
	auto* host = new Host(observations);
	auto* connection = new Connection(observations, true);
	auto session = plugin->createSession(host, connection);
	host->release();
	connection->release();
	plugin.reset();
	FbLocalStatus status;
	if (beforeRun)
	{
		session->stop();
		session->stop();
		session->run(&status);
		require(!observations.enteredRead, "pre-stopped session entered transport");
	}
	else
	{
		std::thread worker([&] { session->run(&status); });
		const auto deadline = std::chrono::steady_clock::now() + 2s;
		while (!observations.enteredRead && std::chrono::steady_clock::now() < deadline)
			std::this_thread::yield();
		const bool entered = observations.enteredRead;
		const auto stoppedAt = std::chrono::steady_clock::now();
		// StopMutex holds a plugin mutex across read(). Calling its stop before
		// transport shutdown blocks until the mock's 3s guard expires, failing the
		// 1s check below without hanging this executable indefinitely.
		session->stop();
		session->stop();
		worker.join();
		require(entered, "worker did not enter read");
		require(std::chrono::steady_clock::now() - stoppedAt < 1s, "stop failed to wake blocked I/O");
		require(!observations.readTimedOut, "blocked read reached timeout instead of shutdown");
		require(status->getState() & IStatus::STATE_ERRORS, "shutdown read did not report failure");
	}
	session.reset();
	require(observations.shutdowns == 1 && observations.hostDestroyed && observations.connectionDestroyed,
		"stop was not idempotent or leaked interfaces");
}

void mark(const char* message)
{
	const char* path = std::getenv("FB_WIRE_TEST_EVENTS");
	require(path, "event output path not configured");
	FILE* file = std::fopen(path, "a");
	require(file, "cannot write event output");
	std::fprintf(file, "%s\n", message);
	std::fclose(file);
}

} // namespace

int main()
{
	try
	{
		IMaster* master = fb_get_master_interface();
		expectFailure([&] { ProtocolPlugin::load(master, {"First,Second", 31001}); },
			"loader accepted multiple configured names as one endpoint");
		mark("host.valid_load_begin");
		verifyAliases(master);
		verifyErrors(master);
		verifyStop(master, "Blocked");
		verifyStop(master, "PreStopped", true);
		verifyStop(master, "StopMutex");
		mark("host.before_shutdown");
		require(fb_shutdown(5000, fb_shutrsn_app_stopped) == 0, "fb_shutdown failed");
		mark("host.after_shutdown");
		std::puts("wire plugin smoke: dynamic module and mock stream checks passed");
		return 0;
	}
	catch (const Firebird::Exception& ex)
	{
		FbLocalStatus status;
		ex.stuffException(&status);
		char text[1024];
		fb_get_master_interface()->getUtilInterface()->formatStatus(text, sizeof(text), &status);
		std::fprintf(stderr, "%s\n", text);
	}
	catch (const std::exception& ex)
	{
		std::fprintf(stderr, "%s\n", ex.what());
	}
	return 1;
}
