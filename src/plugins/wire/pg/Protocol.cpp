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

#include "Protocol.h"

#include <algorithm>
#include <cstring>
#include <utility>

// PostgreSQL protocol 3.0 message layouts:
// https://www.postgresql.org/docs/17/protocol-message-formats.html
namespace Firebird::PgWire
{
namespace
{
constexpr std::uint32_t VERSION_3 = 196608;
constexpr std::uint32_t SSL_REQUEST = 80877103;
constexpr std::uint32_t GSS_REQUEST = 80877104;

[[noreturn]] void malformed(const char* message)
{
	throw ProtocolError("08P01", message);
}

std::uint32_t integer(const std::uint8_t* bytes)
{
	return (std::uint32_t(bytes[0]) << 24) | (std::uint32_t(bytes[1]) << 16) |
		(std::uint32_t(bytes[2]) << 8) | bytes[3];
}

void appendInteger(std::vector<std::uint8_t>& bytes, std::uint32_t value)
{
	for (int shift = 24; shift >= 0; shift -= 8)
		bytes.push_back(static_cast<std::uint8_t>(value >> shift));
}

void validateUtf8(std::string_view text)
{
	const auto* bytes = reinterpret_cast<const unsigned char*>(text.data());
	for (std::size_t i = 0; i < text.size();)
	{
		const unsigned first = bytes[i++];
		if (first < 0x80)
			continue;
		unsigned trailing, value, minimum;
		if (first >= 0xC2 && first <= 0xDF)
			trailing = 1, value = first & 0x1F, minimum = 0x80;
		else if (first >= 0xE0 && first <= 0xEF)
			trailing = 2, value = first & 0x0F, minimum = 0x800;
		else if (first >= 0xF0 && first <= 0xF4)
			trailing = 3, value = first & 7, minimum = 0x10000;
		else
			throw ProtocolError("22021", "Invalid UTF8 in wire string");
		if (text.size() - i < trailing)
			throw ProtocolError("22021", "Incomplete UTF8 in wire string");
		while (trailing--)
		{
			const unsigned next = bytes[i++];
			if ((next & 0xC0) != 0x80)
				throw ProtocolError("22021", "Invalid UTF8 in wire string");
			value = (value << 6) | (next & 0x3F);
		}
		if (value < minimum || value > 0x10FFFF || (value >= 0xD800 && value <= 0xDFFF))
			throw ProtocolError("22021", "Invalid UTF8 code point in wire string");
	}
}

std::string_view consumeString(const std::vector<std::uint8_t>& bytes, std::size_t& offset)
{
	const auto begin = offset;
	while (offset < bytes.size() && bytes[offset])
		++offset;
	if (offset == bytes.size())
		malformed("Unterminated wire string");
	std::string_view value(reinterpret_cast<const char*>(bytes.data() + begin), offset - begin);
	++offset;
	validateUtf8(value);
	return value;
}

void appendString(std::vector<std::uint8_t>& bytes, std::string_view text)
{
	if (text.find('\0') != std::string_view::npos)
		throw ProtocolError("22021", "NUL in backend wire string");
	validateUtf8(text);
	bytes.insert(bytes.end(), text.begin(), text.end());
	bytes.push_back(0);
}

Startup parseStartup(const std::vector<std::uint8_t>& body)
{
	Startup result;
	std::size_t offset = 0;
	unsigned seen = 0;
	for (;;)
	{
		const auto name = consumeString(body, offset);
		if (name.empty())
		{
			if (offset != body.size())
				malformed("Trailing bytes after startup terminator");
			break;
		}
		const auto value = consumeString(body, offset);
		unsigned field;
		if (name == "user")
			field = 1;
		else if (name == "database")
			field = 2;
		else if (name == "application_name")
			field = 4;
		else if (name == "client_encoding")
			field = 8;
		else
			throw ProtocolError("0A000", "Unsupported startup parameter");
		if (seen & field)
			malformed("Duplicate startup parameter");
		seen |= field;
		switch (field)
		{
		case 1: result.user = value; break;
		case 2: result.database = value; break;
		case 4: result.applicationName = value; break;
		case 8:
			if (value != "UTF8")
				throw ProtocolError("22023", "Only UTF8 client_encoding is supported");
			break;
		}
	}
	if (result.user.empty())
		throw ProtocolError("22023", "Startup user is required");
	if (!(seen & 2))
		result.database = result.user;
	else if (result.database.empty())
		throw ProtocolError("22023", "Startup database must not be empty");
	return result;
}

} // namespace

ProtocolError::ProtocolError(const char* sqlState, const char* message)
	: std::runtime_error(message), state(sqlState)
{}

Message::~Message() { clear(); }
Message::Message(Message&& other) noexcept
	: type(other.type), body(std::move(other.body))
{
	other.type = 0;
}
Message& Message::operator=(Message&& other) noexcept
{
	if (this != &other)
	{
		clear();
		type = other.type;
		body = std::move(other.body);
		other.type = 0;
	}
	return *this;
}
void Message::clear() noexcept
{
	volatile std::uint8_t* bytes = body.data();
	for (std::size_t i = 0; i < body.size(); ++i)
		bytes[i] = 0;
	body.clear();
	type = 0;
}
std::string_view Message::text() const
{
	if (type != 'p' && type != 'Q')
		malformed("Message does not contain a password or query string");
	std::size_t offset = 0;
	const auto result = consumeString(body, offset);
	if (offset != body.size())
		malformed("Trailing bytes after message string");
	return result;
}

Protocol::Protocol(IMaster* master, IWireConnection* connection, Limits limits)
	: connection(connection), limits(limits), startupEnd(Clock::now() + std::chrono::milliseconds(limits.timeoutMs)),
	  status(master ? master->getStatus() : nullptr)
{
	const Limits maximum;
	if (!connection || !status || limits.startupBytes < 9 || limits.startupBytes > maximum.startupBytes ||
		limits.frameBytes < 5 || limits.frameBytes > maximum.frameBytes ||
		!limits.passwordBytes || limits.passwordBytes > maximum.passwordBytes ||
		!limits.timeoutMs || limits.timeoutMs > 300000)
	{
		throw std::invalid_argument("Invalid PgWire protocol limits or interfaces");
	}
}

Protocol::Deadline Protocol::normalize(Deadline deadline) const
{
	return std::min(deadline, Clock::now() + std::chrono::milliseconds(limits.timeoutMs));
}

unsigned Protocol::remaining(Deadline deadline) const
{
	const auto duration = deadline - Clock::now();
	if (duration <= Clock::duration::zero())
		throw ProtocolError("08006", "Wire frame deadline exceeded");
	return static_cast<unsigned>(std::chrono::ceil<std::chrono::milliseconds>(duration).count());
}

bool Protocol::readExact(void* buffer, unsigned size, Deadline deadline, bool allowEof)
{
	auto* bytes = static_cast<std::uint8_t*>(buffer);
	unsigned done = 0;
	while (done < size)
	{
		ThrowStatusWrapper wrapped(status.get());
		const unsigned count = connection->read(&wrapped, size - done, bytes + done, remaining(deadline));
		if (!count)
		{
			if (!done && allowEof)
				return false;
			malformed("Unexpected EOF inside wire frame");
		}
		if (count > size - done)
			malformed("Transport returned excessive read length");
		done += count;
		remaining(deadline);
	}
	return true;
}

void Protocol::writeExact(const void* buffer, unsigned size, Deadline deadline)
{
	const auto* bytes = static_cast<const std::uint8_t*>(buffer);
	unsigned done = 0;
	while (done < size)
	{
		ThrowStatusWrapper wrapped(status.get());
		const unsigned count = connection->write(&wrapped, size - done, bytes + done, remaining(deadline));
		if (!count || count > size - done)
			malformed("Transport returned invalid write length");
		done += count;
		remaining(deadline);
	}
}

bool Protocol::readStartup(Startup& startup)
{
	bool ssl = false, gss = false;
	for (;;)
	{
		std::uint8_t header[8];
		if (!readExact(header, 4, startupEnd, true))
			return false;
		const auto length = integer(header);
		if (length < 8 || length > limits.startupBytes)
			malformed("Invalid startup frame length");
		readExact(header + 4, 4, startupEnd);
		const auto version = integer(header + 4);
		if (version == SSL_REQUEST || version == GSS_REQUEST)
		{
			if (length != 8)
				malformed("Invalid encryption request length");
			bool& seen = version == SSL_REQUEST ? ssl : gss;
			if (seen)
				malformed("Repeated encryption request");
			seen = true;
			writeExact("N", 1, startupEnd);
			continue;
		}
		if (version != VERSION_3)
			throw ProtocolError("0A000", "Only wire protocol version 3.0 is supported");
		std::vector<std::uint8_t> body(length - 8);
		readExact(body.data(), body.size(), startupEnd);
		startup = parseStartup(body);
		return true;
	}
}

bool Protocol::readMessage(Message& message, Deadline deadline)
{
	deadline = normalize(deadline);
	message.clear();
	std::uint8_t header[5];
	if (!readExact(header, 1, deadline, true))
		return false;
	readExact(header + 1, 4, deadline);
	const auto length = integer(header + 1);
	if (length < 4 || length > limits.frameBytes)
		malformed("Invalid frontend frame length");
	if (header[0] == 'p' && length - 4 > limits.passwordBytes)
		malformed("Password frame exceeds its limit");
	message.type = header[0];
	message.body.resize(length - 4);
	readExact(message.body.data(), message.body.size(), deadline);
	if (message.type == 'p' || message.type == 'Q')
		message.text();
	else if (message.type == 'X' && !message.body.empty())
		malformed("Terminate frame must have an empty body");
	return true;
}

void Protocol::send(char type, const std::vector<std::uint8_t>& body, Deadline deadline)
{
	deadline = normalize(deadline);
	if (body.size() > limits.frameBytes - 4)
		malformed("Backend frame exceeds its limit");
	std::vector<std::uint8_t> header{static_cast<std::uint8_t>(type)};
	appendInteger(header, body.size() + 4);
	writeExact(header.data(), header.size(), deadline);
	writeExact(body.data(), body.size(), deadline);
}

void Protocol::authenticationCleartext(Deadline deadline)
{
	send('R', {0, 0, 0, 3}, deadline);
}
void Protocol::authenticationOk(Deadline deadline)
{
	send('R', {0, 0, 0, 0}, deadline);
}
void Protocol::parameterStatus(std::string_view name, std::string_view value, Deadline deadline)
{
	if (limits.frameBytes < 6 || name.size() > limits.frameBytes - 6 ||
		value.size() > limits.frameBytes - 6 - name.size())
		malformed("ParameterStatus exceeds its limit");
	std::vector<std::uint8_t> body;
	appendString(body, name);
	appendString(body, value);
	send('S', body, deadline);
}
void Protocol::ready(char transaction, Deadline deadline)
{
	if (transaction != 'I' && transaction != 'T' && transaction != 'E')
		throw std::invalid_argument("Invalid transaction status for ReadyForQuery");
	send('Z', {static_cast<std::uint8_t>(transaction)}, deadline);
}
void Protocol::error(std::string_view sqlState, std::string_view message, bool fatal, Deadline deadline)
{
	if (sqlState.size() != 5 || !std::all_of(sqlState.begin(), sqlState.end(), [](unsigned char c)
		{ return (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z'); }))
	{
		throw std::invalid_argument("Invalid ErrorResponse SQLSTATE");
	}
	// Fixed fields: length4, S/V(7 each), C(7), M tag/NUL(2), final NUL(1).
	if (limits.frameBytes < 28 || message.size() > limits.frameBytes - 28)
		malformed("ErrorResponse exceeds its limit");
	std::vector<std::uint8_t> body{'S'};
	appendString(body, fatal ? "FATAL" : "ERROR");
	body.push_back('V');
	appendString(body, fatal ? "FATAL" : "ERROR");
	body.push_back('C');
	appendString(body, sqlState);
	body.push_back('M');
	appendString(body, message);
	body.push_back(0);
	send('E', body, deadline);
}

} // namespace Firebird::PgWire
