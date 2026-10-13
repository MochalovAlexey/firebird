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

// Wire metadata supplied by the executor. Table identity is not advertised.
// Format 0 is text, format 1 is binary; no engine types enter this codec.
struct Column
{
	std::string name;
	std::uint32_t oid;
	std::int16_t typeSize;
	std::int32_t typeModifier = -1;
	std::int16_t format = 0;
};

struct ParseMessage
{
	std::string name;
	std::string sql;
	std::vector<std::uint32_t> parameterTypes;
};

struct BindMessage
{
	std::string portal;
	std::string statement;
	std::vector<std::int16_t> parameterFormats;
	// Length-delimited bytes, including binary NULs; text validation belongs to
	// the input codec, after the executor resolves each parameter's format.
	std::vector<std::optional<std::string>> values;
	std::vector<std::int16_t> resultFormats;
};

struct ObjectMessage
{
	char kind; // S: statement, P: portal.
	std::string name;
};

struct ExecuteMessage
{
	std::string portal;
	std::uint32_t maxRows;
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
	// Decode only structure, bounded counts and UTF8 strings. The executor
	// validates OIDs, format codes/counts and maxRows against its capabilities.
	// Returned objects own their strings; they do not retain views into body.
	ParseMessage parse() const;
	BindMessage bind() const;
	ObjectMessage object() const; // Describe or Close.
	ExecuteMessage execution() const;
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
	// During Extended recovery read complete frames, ignoring command bodies
	// except Sync/Terminate. Framing limits still apply.
	bool readMessage(Message& message, bool discard = false);

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
	// Format-count rules are 0 (all text), 1 (all alike), or one per field.
	// Binary fields are copied as bytes; text fields retain UTF8/NUL validation.
	void dataRow(const std::vector<std::optional<std::string>>& values,
		const std::vector<std::int16_t>& formats = {});
	void commandComplete(std::string_view tag);
	void emptyQuery();
	void parseComplete();
	void bindComplete();
	void closeComplete();
	void parameterDescription(const std::vector<std::uint32_t>& types);
	void noData();
	void portalSuspended();

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
