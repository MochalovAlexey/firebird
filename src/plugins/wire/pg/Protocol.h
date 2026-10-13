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

#include <cstdint>
#include <memory>
#include <optional>
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

// Wire metadata supplied by the executor. Table identity is not advertised;
// all columns use text format. No engine types or SQL text enter this codec.
struct Column
{
	std::string name;
	std::uint32_t oid;
	std::int16_t typeSize;
	std::int32_t typeModifier = -1;
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
	// The connection is borrowed for this object's lifetime; master is the
	// interface passed to the plugin entrypoint. Byte limits may only be reduced.
	Protocol(IMaster* master, IWireConnection* connection, Limits limits = {});
	Protocol(const Protocol&) = delete;
	Protocol& operator=(const Protocol&) = delete;

	// false means EOF before the next frame. Truncation inside a frame is an error.
	// Declines SSL/GSS once each with N.
	bool readStartup(Startup& startup);
	bool readMessage(Message& message);

	void authenticationCleartext();
	void authenticationOk();
	void parameterStatus(std::string_view name, std::string_view value);
	void ready(char transaction = 'I');
	void error(std::string_view sqlState, std::string_view message, bool fatal);
	// Preflight the complete message before allocating its buffer or writing.
	// At most 32767 result fields are supported. Limits produce 0A000; invalid
	// UTF8/NUL produces 22021. These checks
	// emit no partial message, allowing the executor to report a query error.
	void rowDescription(const std::vector<Column>& columns);
	void dataRow(const std::vector<std::optional<std::string>>& values);
	void commandComplete(std::string_view tag);
	void emptyQuery();

private:
	struct DisposeStatus { void operator()(IStatus* status) const { status->dispose(); } };
	bool readExact(void* buffer, unsigned size, bool allowEof = false);
	void writeExact(const void* buffer, unsigned size);
	void send(char type, const std::vector<std::uint8_t>& body);

	IWireConnection* const connection;
	const Limits limits;
	std::unique_ptr<IStatus, DisposeStatus> status;
};

} // namespace Firebird::PgWire

#endif // PLUGINS_WIRE_PG_PROTOCOL_H
