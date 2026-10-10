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

#ifndef PLUGINS_WIRE_PG_PROTOCOL_H
#define PLUGINS_WIRE_PG_PROTOCOL_H

#include "firebird/Interface.h"

#include <chrono>
#include <cstdint>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace Firebird::PgWire
{

struct Limits
{
	// Length-field limits include the four length bytes, but not a typed tag.
	unsigned startupBytes = 16 * 1024;
	unsigned frameBytes = 1024 * 1024;
	unsigned passwordBytes = 4096; // Password body, including its trailing NUL.
	unsigned timeoutMs = 30000; // Whole frame; the host may impose a shorter wait.
};

class ProtocolError final : public std::runtime_error
{
public:
	ProtocolError(const char* sqlState, const char* message);
	const char* sqlState() const noexcept { return state.c_str(); }
private:
	const std::string state;
};

struct Startup
{
	std::string user;
	std::string database; // Defaults to user when the startup field is absent.
	std::string applicationName;
	std::string clientEncoding = "UTF8";
};

// No copies of password frames. Wipe storage before clearing/reusing/freeing it.
struct Message
{
	Message() = default;
	~Message();
	Message(const Message&) = delete;
	Message& operator=(const Message&) = delete;
	Message(Message&& other) noexcept;
	Message& operator=(Message&& other) noexcept;
	void clear() noexcept;
	// For validated Query/Password messages only. Excludes the framing NUL;
	// SQL bytes are otherwise unchanged, and the view expires with the message.
	std::string_view text() const;
	char type = 0;
	std::vector<std::uint8_t> body;
};

class Protocol final
{
public:
	using Clock = std::chrono::steady_clock;
	using Deadline = Clock::time_point;

	// The connection is borrowed for this object's lifetime; master is the
	// interface passed to the plugin entrypoint. Byte limits may only be reduced.
	Protocol(IMaster* master, IWireConnection* connection, Limits limits = {});
	Protocol(const Protocol&) = delete;
	Protocol& operator=(const Protocol&) = delete;

	Deadline startupDeadline() const noexcept { return startupEnd; }
	// false means EOF before the next frame. Truncation inside a frame is an error.
	// Declines SSL/GSS once each with N, using one deadline for the whole exchange.
	bool readStartup(Startup& startup);
	bool readMessage(Message& message, Deadline deadline = Deadline::max());

	// Pass startupDeadline() during authentication (including Password reads and
	// startup replies) to share its original budget. A default deadline starts
	// one finite budget for this complete outgoing frame, including short writes.
	void authenticationCleartext(Deadline deadline = Deadline::max());
	void authenticationOk(Deadline deadline = Deadline::max());
	void parameterStatus(std::string_view name, std::string_view value,
		Deadline deadline = Deadline::max());
	void ready(char transaction = 'I', Deadline deadline = Deadline::max());
	void error(std::string_view sqlState, std::string_view message, bool fatal,
		Deadline deadline = Deadline::max());

private:
	struct DisposeStatus { void operator()(IStatus* status) const { status->dispose(); } };
	Deadline normalize(Deadline deadline) const;
	unsigned remaining(Deadline deadline) const;
	bool readExact(void* buffer, unsigned size, Deadline deadline, bool allowEof = false);
	void writeExact(const void* buffer, unsigned size, Deadline deadline);
	void send(char type, const std::vector<std::uint8_t>& body, Deadline deadline);

	IWireConnection* const connection;
	const Limits limits;
	const Deadline startupEnd;
	std::unique_ptr<IStatus, DisposeStatus> status;
};

} // namespace Firebird::PgWire

#endif // PLUGINS_WIRE_PG_PROTOCOL_H
