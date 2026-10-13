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
#include "../../SocketConnection.h"
#include "../common/classes/ImplementHelper.h"

#include <atomic>
#include <cerrno>
#include <string>
#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

namespace Firebird::WireProtocol
{

namespace
{

class SocketHandle
{
public:
	explicit SocketHandle(int value) noexcept : value(value) {}
	~SocketHandle()
	{
		// Retrying close after EINTR can close an unrelated, reused descriptor.
		if (value >= 0)
			::close(value);
	}
	SocketHandle(const SocketHandle&) = delete;
	SocketHandle& operator=(const SocketHandle&) = delete;
	int get() const noexcept { return value; }
	int release() noexcept
	{
		const int result = value;
		value = -1;
		return result;
	}

private:
	int value;
};

[[noreturn]] void setupError(const char* operation, int error)
{
	(Arg::Gds(isc_network_error) << "wire connection" <<
		Arg::Gds(isc_random) << operation << Arg::Unix(error)).raise();
}

void setDescriptorFlags(int socket, int readCommand, int writeCommand, int bits)
{
	int flags;
	do
		flags = ::fcntl(socket, readCommand);
	while (flags < 0 && errno == EINTR);
	if (flags < 0)
		setupError("Cannot read wire socket flags", errno);

	int result;
	do
		result = ::fcntl(socket, writeCommand, flags | bits);
	while (result < 0 && errno == EINTR);
	if (result < 0)
		setupError("Cannot set wire socket flags", errno);
}

class SocketConnection final :
	public RefCntIface<IWireConnectionImpl<SocketConnection, CheckStatusWrapper>>
{
public:
	SocketConnection(SocketHandle& owner, const char* peerAddress, unsigned peerPort)
		: peerAddress(peerAddress), peerPort(peerPort),
		  socket(owner.release())
	{}

	unsigned read(CheckStatusWrapper* status, unsigned size, void* buffer) override
	{
		return transfer(status, size, buffer, nullptr);
	}

	unsigned write(CheckStatusWrapper* status, unsigned size, const void* buffer) override
	{
		return transfer(status, size, nullptr, buffer, true);
	}

	void shutdown() override
	{
		if (!stopped.exchange(true))
		{
			// No close here: a concurrent poll/recv/send must never see a reused fd.
			// Connected sockets wake poll and both directions of pending I/O.
			::shutdown(socket.get(), SHUT_RDWR);
		}
	}

	const char* getPeerAddress() override { return peerAddress.c_str(); }
	unsigned getPeerPort() override { return peerPort; }
	FB_BOOLEAN isEncrypted() override { return FB_FALSE; }

private:
	~SocketConnection() override = default;

	unsigned fail(CheckStatusWrapper* status, bool writing, int error) const
	{
		(Arg::Gds(isc_network_error) << peerAddress.c_str() <<
			Arg::Gds(writing ? isc_net_write_err : isc_net_read_err) << Arg::Unix(error)).copyTo(status);
		return 0;
	}

	unsigned transfer(CheckStatusWrapper* status, unsigned size, void* input, const void* output,
		bool writing = false)
	{
		if (stopped.load())
			return fail(status, writing, ECANCELED);
		if (!size)
			return 0;
		if (writing ? !output : !input)
			return fail(status, writing, EINVAL);

		for (;;)
		{
			if (stopped.load())
				return fail(status, writing, ECANCELED);

			ssize_t count;
			if (writing)
			{
#ifdef MSG_NOSIGNAL
				constexpr int sendFlags = MSG_NOSIGNAL;
#else
				constexpr int sendFlags = 0; // SO_NOSIGPIPE is configured at creation.
#endif
				count = ::send(socket.get(), output, size, sendFlags);
			}
			else
				count = ::recv(socket.get(), input, size, 0);

			const int ioError = errno;
			if (stopped.load())
				return fail(status, writing, ECANCELED);
			if (count >= 0)
			{
				if (writing && !count)
					return fail(status, true, EPIPE);
				return static_cast<unsigned>(count);
			}
			if (ioError == EINTR)
				continue;
			if (ioError != EAGAIN && ioError != EWOULDBLOCK)
				return fail(status, writing, ioError);

			for (;;)
			{
				if (stopped.load())
					return fail(status, writing, ECANCELED);
				pollfd descriptor{socket.get(), static_cast<short>(writing ? POLLOUT : POLLIN), 0};
				const int ready = ::poll(&descriptor, 1, -1);
				const int pollError = errno;
				if (stopped.load())
					return fail(status, writing, ECANCELED);
				if (ready < 0)
				{
					if (pollError == EINTR)
						continue;
					return fail(status, writing, pollError);
				}
				if (!ready)
					continue;
				if (descriptor.revents & POLLNVAL)
					return fail(status, writing, EBADF);
				// recv/send consumes readiness, EOF or the socket's actual error.
				break;
			}
		}
	}

	const std::string peerAddress;
	const unsigned peerPort;
	std::atomic<bool> stopped{false};
	// Last: no throwing initialization may follow taking ownership from the guard.
	SocketHandle socket;
};

} // namespace

IWireConnection* createSocketConnection(int socket, const char* peerAddress, unsigned peerPort)
{
	SocketHandle owner(socket);
	if (socket < 0 || !peerAddress || !peerAddress[0] || peerPort > 65535)
		setupError("Invalid wire socket configuration", EINVAL);

	setDescriptorFlags(socket, F_GETFL, F_SETFL, O_NONBLOCK);
	setDescriptorFlags(socket, F_GETFD, F_SETFD, FD_CLOEXEC);
#if !defined(MSG_NOSIGNAL) && defined(SO_NOSIGPIPE)
	const int enabled = 1;
	if (::setsockopt(socket, SOL_SOCKET, SO_NOSIGPIPE, &enabled, sizeof(enabled)) < 0)
		setupError("Cannot suppress wire socket SIGPIPE", errno);
#elif !defined(MSG_NOSIGNAL)
#error "Wire socket transport requires MSG_NOSIGNAL or SO_NOSIGPIPE"
#endif

	// The guard owns the fd if allocation or copying the peer string fails.
	auto* connection = FB_NEW SocketConnection(owner, peerAddress, peerPort);
	connection->addRef();
	return connection;
}

} // namespace Firebird::WireProtocol
