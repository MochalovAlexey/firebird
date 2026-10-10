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
#include "../../Server.h"
#include "../../ProtocolPlugin.h"
#include "../../SocketConnection.h"
#include "../common/classes/ImplementHelper.h"
#include "../common/config/config.h"
#include "../common/StatusArg.h"
#include "../common/status.h"
#include "../common/ThreadStart.h"
#include "../yvalve/gds_proto.h"

#include <arpa/inet.h>
#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>
#include <algorithm>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <deque>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

namespace Firebird::WireProtocol
{
namespace
{
using Clock = std::chrono::steady_clock;
constexpr unsigned MAX_ENDPOINTS = 16;
constexpr unsigned MAX_WORKERS = 64;

class Socket final
{
public:
	explicit Socket(int fd = -1) : fd(fd) {}
	~Socket() { if (fd >= 0) ::close(fd); }
	Socket(const Socket&) = delete;
	Socket& operator=(const Socket&) = delete;
	int get() const { return fd; }
	int release() { const int result = fd; fd = -1; return result; }
private:
	int fd;
};

void fail(const char* message)
{
	(Arg::Gds(isc_random) << message).raise();
}

void socketFailure(const char* operation)
{
	(Arg::Gds(isc_random) << operation << Arg::Unix(errno)).raise();
}

std::string safeText(const char* text, unsigned length)
{
	std::string result;
	for (unsigned i = 0; text && i < std::min(length, 512u); ++i)
	{
		const unsigned char c = text[i];
		result += c >= 32 && c < 127 ? char(c) : '?';
	}
	return result;
}

void logEndpoint(const EndpointConfig& endpoint, const char* message)
{
	gds__log("Wire protocol %s port %u: %s",
		safeText(endpoint.plugin.c_str(), endpoint.plugin.size()).c_str(), endpoint.port, message);
}

void logException(const EndpointConfig& endpoint, const Exception& ex)
{
	FbLocalStatus status;
	ex.stuffException(&status);
	char buffer[1024];
	MasterInterfacePtr()->getUtilInterface()->formatStatus(buffer, sizeof(buffer), &status);
	logEndpoint(endpoint, buffer);
}

struct Settings
{
	EndpointConfig endpoint;
	unsigned workers = 4;
	unsigned queueSize = 16;
	unsigned timeout = 30000;
};

RefPtr<IConfigEntry> setting(IConfig* config, const char* name)
{
	FbLocalStatus status;
	RefPtr<IConfigEntry> entry(REF_NO_INCR, config->find(&status, name));
	check(&status);
	RefPtr<IConfigEntry> duplicate(REF_NO_INCR, config->findPos(&status, name, 1));
	check(&status);
	if (duplicate)
		fail(("Duplicate wire host setting: " + std::string(name)).c_str());
	if (entry)
	{
		RefPtr<IConfig> nested(REF_NO_INCR, entry->getSubConfig(&status));
		check(&status);
		if (nested)
			fail(("Wire host setting must be scalar: " + std::string(name)).c_str());
	}
	return entry;
}

unsigned number(IConfig* config, const char* name, unsigned fallback, unsigned maximum)
{
	const auto entry = setting(config, name);
	if (!entry)
		return fallback;
	const char* value = entry->getValue();
	if (!value || !*value)
		fail(("Empty wire host setting: " + std::string(name)).c_str());
	unsigned result = 0;
	for (const char* p = value; *p; ++p)
	{
		if (*p < '0' || *p > '9' || result > (maximum - unsigned(*p - '0')) / 10)
			fail(("Invalid wire host setting: " + std::string(name)).c_str());
		result = result * 10 + unsigned(*p - '0');
	}
	if (!result || result > maximum)
		fail(("Wire host setting out of range: " + std::string(name)).c_str());
	return result;
}

Settings readSettings(const EndpointConfig& endpoint)
{
	RefPtr<IConfig> config(REF_NO_INCR, MasterInterfacePtr()->getConfigManager()->getPluginConfig(
		endpoint.plugin.c_str()));
	if (!config)
		fail("Cannot read wire endpoint configuration");
	if (setting(config, "Port"))
		fail("Port in plugin Config is forbidden; use WireProtocolPorts");
	if (setting(config, "TLS"))
		fail("TLS is not available for additional wire endpoints yet");
	if (const auto bind = setting(config, "BindAddress"))
	{
		const char* address = bind->getValue();
		if (!address || std::string(address) != "127.0.0.1")
			fail("Wire BindAddress must be 127.0.0.1 in the initial transport profile");
	}
	Settings settings{endpoint};
	settings.workers = number(config, "Workers", settings.workers, 16);
	settings.queueSize = number(config, "QueueSize", settings.queueSize, 64);
	settings.timeout = number(config, "IOTimeout", settings.timeout, 300000);
	return settings;
}

// Inspect the actual bound listener: -p and service-name resolution are already
// accounted for. AF_UNIX reserves no TCP port.
struct NativeEndpoint
{
	unsigned port = 0;
	bool overlapsLoopback = false;

	explicit NativeEndpoint(int socket)
	{
		sockaddr_storage address{};
		socklen_t length = sizeof(address);
		if (::getsockname(socket, reinterpret_cast<sockaddr*>(&address), &length) < 0)
			socketFailure("Cannot inspect native listener");
		if (address.ss_family == AF_INET)
		{
			const auto& ipv4 = reinterpret_cast<const sockaddr_in&>(address);
			port = ntohs(ipv4.sin_port);
			const auto ip = ntohl(ipv4.sin_addr.s_addr);
			overlapsLoopback = ip == INADDR_ANY || ip == INADDR_LOOPBACK;
		}
		else if (address.ss_family == AF_INET6)
		{
			const auto& ipv6 = reinterpret_cast<const sockaddr_in6&>(address);
			port = ntohs(ipv6.sin6_port);
			int onlyV6 = 1;
			socklen_t size = sizeof(onlyV6);
			if (::getsockopt(socket, IPPROTO_IPV6, IPV6_V6ONLY, &onlyV6, &size) < 0)
				socketFailure("Cannot inspect native IPv6 policy");
			if (!onlyV6 && IN6_IS_ADDR_UNSPECIFIED(&ipv6.sin6_addr))
				overlapsLoopback = true;
			if (IN6_IS_ADDR_V4MAPPED(&ipv6.sin6_addr))
			{
				in_addr mapped{};
				memcpy(&mapped, &ipv6.sin6_addr.s6_addr[12], sizeof(mapped));
				const auto ip = ntohl(mapped.s_addr);
				overlapsLoopback = ip == INADDR_ANY || ip == INADDR_LOOPBACK;
			}
		}
		else if (address.ss_family != AF_UNIX)
			fail("Unsupported native listener family");
	}
};

void setNonblocking(int socket)
{
	const int flags = ::fcntl(socket, F_GETFL);
	const int fdFlags = ::fcntl(socket, F_GETFD);
	if (flags < 0 || fdFlags < 0 || ::fcntl(socket, F_SETFL, flags | O_NONBLOCK) < 0 ||
		::fcntl(socket, F_SETFD, fdFlags | FD_CLOEXEC) < 0)
	{
		socketFailure("Cannot configure wire listener");
	}
}

int listenSocket(unsigned port, unsigned backlog)
{
	Socket socket(::socket(AF_INET, SOCK_STREAM, 0));
	if (socket.get() < 0)
		socketFailure("Cannot create wire listener");
	setNonblocking(socket.get());
	const int one = 1;
	if (::setsockopt(socket.get(), SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one)) < 0)
		socketFailure("Cannot configure wire listener reuse");
	sockaddr_in address{};
	address.sin_family = AF_INET;
	address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
	address.sin_port = htons(port);
	if (::bind(socket.get(), reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0)
		socketFailure("Cannot bind wire listener");
	if (::listen(socket.get(), backlog) < 0)
		socketFailure("Cannot listen on wire endpoint");
	return socket.release();
}

class HostContext final : public RefCntIface<IWireServerHostImpl<HostContext, CheckStatusWrapper>>
{
public:
	HostContext(const EndpointConfig& endpoint, std::atomic<bool>& stopping)
		: endpoint(endpoint), stopping(stopping) {}
	FB_BOOLEAN isStopping() override { return stopping.load(); }
	void log(unsigned level, unsigned length, const char* message) override
	{
		if (level >= LOG_INFO && level <= LOG_ERROR)
			logEndpoint(endpoint, safeText(message, length).c_str());
	}
private:
	const EndpointConfig endpoint;
	std::atomic<bool>& stopping;
};

class Endpoint final
{
public:
	explicit Endpoint(Settings settings)
		: settings(std::move(settings)), listener(listenSocket(this->settings.endpoint.port,
			this->settings.queueSize)), workers(this->settings.workers), active(this->settings.workers)
	{
		// Config/native conflict checks and successful bind precede loading.
		plugin = ProtocolPlugin::load(MasterInterfacePtr(), this->settings.endpoint);
	}

	void start()
	{
		for (auto& worker : workers)
		{
			++live;
			try { Thread::start(workerEntry, this, THREAD_medium, &worker); }
			catch (...) { --live; throw; }
		}
		++live;
		try { Thread::start(acceptEntry, this, THREAD_medium, &acceptor); }
		catch (...) { --live; throw; }
		logEndpoint(settings.endpoint, "listening on 127.0.0.1");
	}

	void stop()
	{
		stopping = true;
		::shutdown(listener.get(), SHUT_RDWR);
		ready.notify_all();
		// Retain references under the registry lock, then call outside it.
		for (unsigned i = 0; i < active.size(); ++i)
		{
			std::shared_ptr<Work> work;
			std::shared_ptr<ProtocolSession> session;
			{
				std::lock_guard<std::mutex> guard(mutex);
				work = active[i];
				if (work)
					session = work->session;
			}
			if (work)
				work->connection->shutdown();
			if (session)
				session->stop();
		}
		for (;;)
		{
			std::shared_ptr<Work> work;
			{
				std::lock_guard<std::mutex> guard(mutex);
				if (queue.empty())
					break;
				work = std::move(queue.front());
				queue.pop_front();
			}
			work->connection->shutdown();
		}
	}

	bool finish(Clock::time_point deadline)
	{
		{
			std::unique_lock<std::mutex> guard(mutex);
			if (!finished.wait_until(guard, deadline, [&] { return live == 0; }))
				return false;
		}
		acceptor.waitForCompletion();
		for (auto& worker : workers)
			worker.waitForCompletion();
		return true;
	}

private:
	struct Work
	{
		explicit Work(IWireConnection* connection) : connection(REF_NO_INCR, connection) {}
		RefPtr<IWireConnection> connection;
		std::shared_ptr<ProtocolSession> session;
		const Clock::time_point accepted = Clock::now();
	};

	static THREAD_ENTRY_DECLARE acceptEntry(THREAD_ENTRY_PARAM arg)
	{
		auto* self = static_cast<Endpoint*>(arg);
		try { self->acceptLoop(); }
		catch (const Exception& ex) { logException(self->settings.endpoint, ex); self->stop(); }
		catch (...) { logEndpoint(self->settings.endpoint, "Listener failed"); self->stop(); }
		self->threadFinished();
		return 0;
	}
	static THREAD_ENTRY_DECLARE workerEntry(THREAD_ENTRY_PARAM arg)
	{
		auto* self = static_cast<Endpoint*>(arg);
		try { self->workerLoop(); }
		catch (const Exception& ex) { logException(self->settings.endpoint, ex); self->stop(); }
		catch (...) { logEndpoint(self->settings.endpoint, "Worker failed"); self->stop(); }
		self->threadFinished();
		return 0;
	}
	void threadFinished()
	{
		std::lock_guard<std::mutex> guard(mutex);
		--live;
		finished.notify_all();
	}

	void acceptLoop()
	{
		while (!stopping)
		{
			expireQueued();
			pollfd wait{listener.get(), POLLIN, 0};
			const int result = ::poll(&wait, 1, 100);
			if (stopping)
				break;
			if (result < 0)
			{
				if (errno == EINTR)
					continue;
				socketFailure("Wire listener poll failed");
			}
			if (!result)
				continue;
			if (!(wait.revents & POLLIN))
				fail("Wire listener became unavailable");
			sockaddr_in peer{};
			socklen_t size = sizeof(peer);
			Socket socket(::accept(listener.get(), reinterpret_cast<sockaddr*>(&peer), &size));
			if (socket.get() < 0)
			{
				if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR || errno == ECONNABORTED)
					continue;
				socketFailure("Wire accept failed");
			}
			char address[INET_ADDRSTRLEN];
			if (!::inet_ntop(AF_INET, &peer.sin_addr, address, sizeof(address)))
				socketFailure("Cannot format wire peer address");
			RefPtr<IWireConnection> connection(REF_NO_INCR, createSocketConnection(socket.release(),
				settings.timeout, address, ntohs(peer.sin_port)));
			auto work = std::make_shared<Work>(connection.getPtr());
			connection.clear(); // Work adopts the returned reference.
			{
				std::lock_guard<std::mutex> guard(mutex);
				if (stopping || queue.size() >= settings.queueSize)
					continue;
				queue.push_back(std::move(work));
			}
			ready.notify_one();
		}
	}

	void expireQueued()
	{
		for (;;)
		{
			std::shared_ptr<Work> expired;
			{
				std::lock_guard<std::mutex> guard(mutex);
				if (queue.empty() || Clock::now() - queue.front()->accepted <
					std::chrono::milliseconds(settings.timeout))
				{
					return;
				}
				expired = std::move(queue.front());
				queue.pop_front();
			}
			expired->connection->shutdown();
		}
	}

	void workerLoop()
	{
		const unsigned slot = nextWorker.fetch_add(1);
		for (;;)
		{
			std::shared_ptr<Work> work;
			{
				std::unique_lock<std::mutex> guard(mutex);
				ready.wait(guard, [&] { return stopping || !queue.empty(); });
				if (stopping)
					return;
				work = std::move(queue.front());
				queue.pop_front();
				active[slot] = work;
			}
			try
			{
				if (!stopping && Clock::now() - work->accepted < std::chrono::milliseconds(settings.timeout))
				{
					RefPtr<IWireServerHost> context(FB_NEW HostContext(settings.endpoint, stopping));
					std::shared_ptr<ProtocolSession> session = plugin->createSession(context, work->connection);
					{
						std::lock_guard<std::mutex> guard(mutex);
						work->session = session;
					}
					if (stopping)
						session->stop();
					else
					{
						FbLocalStatus status;
						session->run(&status);
						if (status->getState() & IStatus::STATE_ERRORS)
							logEndpoint(settings.endpoint, "Session ended with an error");
					}
				}
			}
			catch (const Exception& ex) { logException(settings.endpoint, ex); }
			catch (...) { logEndpoint(settings.endpoint, "Session creation or execution failed"); }
			work->connection->shutdown();
			{
				std::lock_guard<std::mutex> guard(mutex);
				active[slot].reset(); // Local work owns the final reference outside the lock.
			}
		}
	}

	const Settings settings;
	Socket listener;
	std::shared_ptr<ProtocolPlugin> plugin;
	std::atomic<bool> stopping{false};
	std::atomic<unsigned> nextWorker{0};
	std::atomic<unsigned> live{0};
	std::mutex mutex;
	std::condition_variable ready, finished;
	Thread acceptor;
	std::vector<Thread> workers;
	std::vector<std::shared_ptr<Work>> active;
	std::deque<std::shared_ptr<Work>> queue;
};

class Server final
{
public:
	void start(const std::vector<EndpointConfig>& endpoints, const NativeEndpoint& native)
	{
		unsigned workers = 0;
		for (const auto& endpoint : endpoints)
		{
			std::shared_ptr<Endpoint> instance;
			bool started = false;
			try
			{
				{
					std::lock_guard<std::mutex> guard(mutex);
					if (stopping)
						return;
				}
				const auto settings = readSettings(endpoint);
				if (native.overlapsLoopback && native.port == endpoint.port)
					fail("Port conflicts with native TCP listener");
				if (workers + settings.workers > MAX_WORKERS)
					fail("Total wire worker limit exceeded");
				instance = std::make_shared<Endpoint>(settings);
				{
					// Publish and start host threads atomically with respect to shutdown.
					// Module entrypoint/factory above and stop/release below run unlocked.
					std::lock_guard<std::mutex> guard(mutex);
					if (stopping)
						continue;
					endpointsOwned.push_back(instance);
					workers += settings.workers;
					instance->start();
				}
				started = true;
			}
			catch (const Exception& ex) { logException(endpoint, ex); }
			catch (const std::exception& ex) { logEndpoint(endpoint, ex.what()); }
			catch (...) { logEndpoint(endpoint, "Endpoint startup failed"); }
			if (instance && !started)
				instance->stop();
		}
	}

	static int shutdownCallback(const int, const int mask, void* arg)
	{
		auto* self = static_cast<Server*>(arg);
		try
		{
			std::vector<std::shared_ptr<Endpoint>> endpoints;
			{
				std::lock_guard<std::mutex> guard(self->mutex);
				self->stopping = true;
				endpoints = self->endpointsOwned;
			}
			if (mask == fb_shut_preproviders)
			{
				for (auto& endpoint : endpoints)
					endpoint->stop();
			}
			else if (mask == fb_shut_postproviders)
			{
				const auto deadline = Clock::now() + std::chrono::seconds(5);
				bool done = true;
				for (auto& endpoint : endpoints)
					done = endpoint->finish(deadline) && done;
				if (!done)
				{
					gds__log("Wire protocol workers did not finish within shutdown deadline");
					return 1; // Pin owners, never unload running plugin code.
				}
				{
					std::lock_guard<std::mutex> guard(self->mutex);
					self->endpointsOwned.clear(); // Snapshot retains final owners outside lock.
				}
			}
			return 0;
		}
		catch (...) { gds__log("Wire protocol shutdown failed"); return 1; }
	}

private:
	std::mutex mutex;
	bool stopping = false;
	std::vector<std::shared_ptr<Endpoint>> endpointsOwned;
};

} // namespace

void startServer(int nativeSocket, bool supportedMode) noexcept
{
	try
	{
		std::vector<EndpointConfig> endpoints;
		std::string error;
		if (!parseEndpointConfig(Config::getWireProtocolPlugins(), Config::getWireProtocolPorts(), endpoints, error))
		{
			gds__log("Wire protocol configuration: %s", error.c_str());
			return;
		}
		if (endpoints.empty())
			return;
		if (!supportedMode)
		{
			gds__log("Additional wire protocols currently require ServerMode=Super");
			return;
		}
		if (endpoints.size() > MAX_ENDPOINTS)
			fail("Too many wire protocol endpoints (maximum 16)");
		NativeEndpoint native(nativeSocket);
		// Callback storage lasts until process exit. Successful shutdown clears all
		// endpoints; an unresponsive trusted plugin retains its owners.
		auto server = std::make_unique<Server>();
		ISC_STATUS_ARRAY status;
		if (fb_shutdown_callback(status, Server::shutdownCallback,
			fb_shut_preproviders | fb_shut_postproviders, server.get()))
		{
			gds__log_status("Cannot register wire protocol shutdown", status);
			return;
		}
		Server* registered = server.release();
		registered->start(endpoints, native);
	}
	catch (const Exception& ex) { iscLogException("Wire protocol startup failed", ex); }
	catch (...) { gds__log("Wire protocol startup failed"); }
}

} // namespace Firebird::WireProtocol
