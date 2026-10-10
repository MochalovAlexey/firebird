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
#include "../SocketConnection.h"
#include "../common/classes/RefCounted.h"
#include "../common/status.h"

#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <string>
#include <thread>
#include <arpa/inet.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <sys/socket.h>
#include <unistd.h>

using namespace Firebird;
using namespace Firebird::WireProtocol;
using namespace std::chrono_literals;

namespace
{

using Clock = std::chrono::steady_clock;

void require(bool condition, const char* message)
{
	if (!condition)
		throw std::runtime_error(message);
}

class Descriptor
{
public:
	explicit Descriptor(int value = -1) : value(value) {}
	~Descriptor() { if (value >= 0) ::close(value); }
	Descriptor(const Descriptor&) = delete;
	Descriptor& operator=(const Descriptor&) = delete;
	int get() const { return value; }
	int release()
	{
		const int result = value;
		value = -1;
		return result;
	}
private:
	int value;
};

struct SocketPair
{
	SocketPair()
	{
		int sockets[2];
		require(::socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0, "socketpair failed");
		local = sockets[0];
		peer = sockets[1];
	}
	~SocketPair()
	{
		if (local >= 0)
			::close(local);
		::close(peer);
	}
	int takeLocal()
	{
		const int result = local;
		local = -1;
		return result;
	}
	int local;
	int peer;
};

bool hasError(const CheckStatusWrapper* status)
{
	return status->getState() & IStatus::STATE_ERRORS;
}

void requireError(const CheckStatusWrapper* status, int error)
{
	require(hasError(status), "transport failure lost its status");
	const ISC_STATUS* vector = status->getErrors();
	require(vector[0] == isc_arg_gds && vector[1] == isc_network_error &&
		vector[2] == isc_arg_string && vector[4] == isc_arg_gds &&
		(vector[5] == isc_net_read_err || vector[5] == isc_net_write_err) &&
		vector[6] == isc_arg_unix && vector[7] == error && vector[8] == isc_arg_end,
		"transport returned an unexpected or malformed error vector");
}

void verifyTransferAndLifetime()
{
	SocketPair sockets;
	const int owned = sockets.local;
	RefPtr<IWireConnection> connection(REF_NO_INCR,
		createSocketConnection(sockets.takeLocal(), 100, "127.0.0.1", 43210));
	require((::fcntl(owned, F_GETFL) & O_NONBLOCK) != 0, "socket remains blocking");
	require((::fcntl(owned, F_GETFD) & FD_CLOEXEC) != 0, "socket is inherited across exec");
	require(std::strcmp(connection->getPeerAddress(), "127.0.0.1") == 0 &&
		connection->getPeerPort() == 43210 && !connection->isEncrypted(), "peer metadata changed");
	FbLocalStatus status;
	require(connection->read(&status, 0, nullptr, 0) == 0 && !hasError(&status),
		"open zero-size read failed");
	require(connection->write(&status, 0, nullptr, 0) == 0 && !hasError(&status),
		"open zero-size write failed");

	require(::send(sockets.peer, "abc", 3, 0) == 3, "cannot seed stream");
	char buffer[32]{};
	const unsigned count = connection->read(&status, sizeof(buffer), buffer, 0);
	require(count == 3 && !hasError(&status) && std::memcmp(buffer, "abc", 3) == 0,
		"read did not return immediate partial progress");
	require(connection->write(&status, count, buffer, 0) == count && !hasError(&status),
		"write failed");
	require(::recv(sockets.peer, buffer, sizeof(buffer), 0) == 3 && std::memcmp(buffer, "abc", 3) == 0,
		"write corrupted bytes");

	require(::shutdown(sockets.peer, SHUT_WR) == 0, "peer half-close failed");
	require(connection->read(&status, sizeof(buffer), buffer, 0) == 0 && !hasError(&status),
		"orderly EOF was reported as an error");

	RefPtr<IWireConnection> retained(connection);
	connection->shutdown();
	connection->shutdown();
	require(connection->read(&status, 0, nullptr, 0) == 0, "closed read produced bytes");
	requireError(&status, ECANCELED);
	require(connection->write(&status, 0, nullptr, 0) == 0, "closed write produced bytes");
	requireError(&status, ECANCELED);
	connection = nullptr;
	require(::fcntl(owned, F_GETFD) >= 0, "shutdown or non-final release closed descriptor");
	retained = nullptr;
	require(::fcntl(owned, F_GETFD) == -1 && errno == EBADF, "final release leaked descriptor");
}

void verifyCreationFailure()
{
	SocketPair sockets;
	const int owned = sockets.local;
	bool failed = false;
	try
	{
		RefPtr<IWireConnection> connection(REF_NO_INCR,
			createSocketConnection(sockets.takeLocal(), 0, "127.0.0.1", 43210));
	}
	catch (const Firebird::Exception&)
	{
		failed = true;
	}
	require(failed, "unbounded host default was accepted");
	require(::fcntl(owned, F_GETFD) == -1 && errno == EBADF, "failed creation leaked descriptor");
}

void verifyDeadlines()
{
	SocketPair sockets;
	RefPtr<IWireConnection> connection(REF_NO_INCR,
		createSocketConnection(sockets.takeLocal(), 120, "127.0.0.1", 43210));
	for (const unsigned timeout : {0u, 30u, 5000u})
	{
		FbLocalStatus status;
		char byte;
		const auto before = Clock::now();
		require(connection->read(&status, 1, &byte, timeout) == 0, "idle read produced bytes");
		const auto elapsed = Clock::now() - before;
		requireError(&status, ETIMEDOUT);
		require(elapsed >= (timeout == 30 ? 15ms : 60ms) && elapsed < 1500ms,
			"default, shorter or clamped timeout was not respected");
	}
	// A timeout is not EOF or a permanent shutdown: later traffic remains valid.
	require(::send(sockets.peer, "x", 1, 0) == 1, "cannot send after timeout");
	FbLocalStatus status;
	char byte;
	require(connection->read(&status, 1, &byte, 0) == 1 && byte == 'x' && !hasError(&status),
		"timeout permanently disabled the connection");
}

void signalHandler(int) {}

void verifyInterruptedDeadline()
{
	SocketPair sockets;
	RefPtr<IWireConnection> connection(REF_NO_INCR,
		createSocketConnection(sockets.takeLocal(), 120, "127.0.0.1", 43210));
	struct sigaction action{}, previous{};
	action.sa_handler = signalHandler;
	::sigemptyset(&action.sa_mask);
	require(::sigaction(SIGUSR1, &action, &previous) == 0, "cannot install test signal handler");
	const pthread_t reader = ::pthread_self();
	std::atomic<bool> finished{false};
	std::thread interrupter([&]
	{
		const auto limit = Clock::now() + 600ms;
		while (!finished && Clock::now() < limit)
		{
			::pthread_kill(reader, SIGUSR1);
			std::this_thread::sleep_for(5ms);
		}
	});
	FbLocalStatus status;
	char byte;
	const auto before = Clock::now();
	const unsigned count = connection->read(&status, 1, &byte, 0);
	const auto elapsed = Clock::now() - before;
	finished = true;
	interrupter.join();
	::sigaction(SIGUSR1, &previous, nullptr);
	require(count == 0, "interrupted idle read produced bytes");
	requireError(&status, ETIMEDOUT);
	require(elapsed >= 60ms && elapsed < 450ms, "EINTR restarted the read deadline");
}

void verifyReadShutdown()
{
	SocketPair sockets;
	RefPtr<IWireConnection> connection(REF_NO_INCR,
		createSocketConnection(sockets.takeLocal(), 3000, "127.0.0.1", 43210));
	FbLocalStatus status;
	unsigned count = 1;
	std::atomic<bool> started{false};
	std::thread worker([&]
	{
		started = true;
		char byte;
		count = connection->read(&status, 1, &byte, 0);
	});
	while (!started)
		std::this_thread::yield();
	std::this_thread::sleep_for(20ms);
	const auto before = Clock::now();
	connection->shutdown();
	worker.join();
	require(Clock::now() - before < 1s && count == 0, "shutdown did not wake socket read");
	requireError(&status, ECANCELED);
}

void verifyBrokenPeer()
{
	SocketPair sockets;
	RefPtr<IWireConnection> connection(REF_NO_INCR,
		createSocketConnection(sockets.takeLocal(), 100, "127.0.0.1", 43210));
	require(::shutdown(sockets.peer, SHUT_RDWR) == 0, "peer shutdown failed");
	struct sigaction action{}, previous{};
	action.sa_handler = SIG_DFL;
	::sigemptyset(&action.sa_mask);
	require(::sigaction(SIGPIPE, &action, &previous) == 0, "cannot restore default SIGPIPE action");
	FbLocalStatus status;
	const unsigned count = connection->write(&status, 1, "x", 0);
	::sigaction(SIGPIPE, &previous, nullptr);
	require(count == 0 && hasError(&status),
		"broken peer write neither failed nor suppressed SIGPIPE");
}

void verifyTcpBackpressure()
{
	Descriptor listener(::socket(AF_INET, SOCK_STREAM, 0));
	Descriptor peer(::socket(AF_INET, SOCK_STREAM, 0));
	require(listener.get() >= 0 && peer.get() >= 0, "cannot create TCP sockets");
	const int bufferSize = 4096;
	require(::setsockopt(peer.get(), SOL_SOCKET, SO_RCVBUF, &bufferSize, sizeof(bufferSize)) == 0,
		"cannot limit TCP receive buffer");
	sockaddr_in address{};
	address.sin_family = AF_INET;
	address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
	require(::bind(listener.get(), reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0 &&
		::listen(listener.get(), 1) == 0, "cannot bind loopback TCP listener");
	socklen_t length = sizeof(address);
	require(::getsockname(listener.get(), reinterpret_cast<sockaddr*>(&address), &length) == 0 &&
		::connect(peer.get(), reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0,
		"cannot connect loopback TCP peer");
	Descriptor accepted(::accept(listener.get(), nullptr, nullptr));
	require(accepted.get() >= 0, "cannot accept TCP peer");
	require(::setsockopt(accepted.get(), SOL_SOCKET, SO_SNDBUF, &bufferSize, sizeof(bufferSize)) == 0,
		"cannot limit TCP send buffer");
	RefPtr<IWireConnection> connection(REF_NO_INCR,
		createSocketConnection(accepted.release(), 3000, "127.0.0.1", 43210));
	const std::string payload(1024 * 1024, 'x');
	FbLocalStatus status;
	const unsigned initial = connection->write(&status, payload.size(), payload.data(), 60);
	require(initial > 0 && initial < payload.size() && !hasError(&status),
		"TCP write did not return partial progress under backpressure");
	const auto limit = Clock::now() + 2s;
	while (!hasError(&status) && Clock::now() < limit)
		connection->write(&status, payload.size(), payload.data(), 60);
	requireError(&status, ETIMEDOUT);

	std::atomic<bool> started{false};
	unsigned count = 1;
	std::thread writer([&]
	{
		started = true;
		// Delayed TCP acknowledgements may permit another partial write after
		// the first timeout. Keep filling until shutdown interrupts the wait.
		do
			count = connection->write(&status, payload.size(), payload.data(), 0);
		while (!hasError(&status));
	});
	while (!started)
		std::this_thread::yield();
	std::this_thread::sleep_for(20ms);
	const auto before = Clock::now();
	connection->shutdown();
	writer.join();
	require(Clock::now() - before < 1s && count == 0, "shutdown did not wake blocked TCP write");
	requireError(&status, ECANCELED);
}

} // namespace

int main()
{
	// A broken transport must fail this executable rather than hang the test job.
	::alarm(15);
	try
	{
		verifyTransferAndLifetime();
		verifyCreationFailure();
		verifyDeadlines();
		verifyInterruptedDeadline();
		verifyReadShutdown();
		verifyBrokenPeer();
		verifyTcpBackpressure();
		std::puts("wire socket transport: deadlines, shutdown, ownership and TCP backpressure passed");
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
