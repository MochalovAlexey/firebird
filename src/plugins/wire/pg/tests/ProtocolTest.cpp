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

#include "../Protocol.h"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <type_traits>
#include <utility>

using namespace Firebird;
using namespace Firebird::PgWire;

namespace
{

using Bytes = std::vector<std::uint8_t>;

void require(bool condition, const char* message)
{
	if (!condition)
		throw std::runtime_error(message);
}

template <typename Function>
void expectError(const char* state, Function function)
{
	try
	{
		function();
	}
	catch (const ProtocolError& error)
	{
		require(std::strcmp(error.sqlState(), state) == 0, "Unexpected SQLSTATE");
		return;
	}
	throw std::runtime_error("Expected protocol error was not raised");
}

void integer(Bytes& bytes, std::uint32_t value)
{
	for (int shift = 24; shift >= 0; shift -= 8)
		bytes.push_back(static_cast<std::uint8_t>(value >> shift));
}

void append(Bytes& bytes, const Bytes& other)
{
	bytes.insert(bytes.end(), other.begin(), other.end());
}

Bytes text(std::string_view value)
{
	Bytes bytes(value.begin(), value.end());
	bytes.push_back(0);
	return bytes;
}

Bytes frame(char tag, const Bytes& body)
{
	Bytes bytes{static_cast<std::uint8_t>(tag)};
	integer(bytes, body.size() + 4);
	append(bytes, body);
	return bytes;
}

Bytes startup(std::initializer_list<std::pair<std::string_view, std::string_view>> values,
	std::uint32_t version = 196608)
{
	Bytes body;
	integer(body, version);
	for (const auto& value : values)
	{
		append(body, text(value.first));
		append(body, text(value.second));
	}
	if (version == 196608)
		body.push_back(0);
	Bytes result;
	integer(result, body.size() + 4);
	append(result, body);
	return result;
}

// A borrowed connection, owned by each test. Partial progress and errors are
// exercised through the public ABI, just as they are by a loaded plugin.
class Connection final : public IWireConnectionImpl<Connection, CheckStatusWrapper>
{
public:
	explicit Connection(Bytes input = {}) : input(std::move(input)) {}
	void addRef() override { ++refs; }
	int release() override { return --refs; }
	unsigned read(CheckStatusWrapper* status, unsigned size, void* buffer) override
	{
		if (failRead)
		{
			const ISC_STATUS errors[] = {isc_arg_gds, isc_net_read_err, isc_arg_end};
			status->setErrors(errors);
			return 0;
		}
		const unsigned count = std::min<std::size_t>({size, readChunk, input.size() - position});
		if (count)
			std::memcpy(buffer, input.data() + position, count);
		position += count;
		return excessiveRead ? size + 1 : count;
	}
	unsigned write(CheckStatusWrapper*, unsigned size, const void* buffer) override
	{
		const unsigned count = std::min(size, writeChunk);
		const auto* bytes = static_cast<const std::uint8_t*>(buffer);
		output.insert(output.end(), bytes, bytes + count);
		return excessiveWrite ? size + 1 : count;
	}
	void shutdown() override {}
	const char* getPeerAddress() override { return "127.0.0.1"; }
	unsigned getPeerPort() override { return 54321; }
	FB_BOOLEAN isEncrypted() override { return FB_FALSE; }

	Bytes input, output;
	std::size_t position = 0;
	unsigned readChunk = 3, writeChunk = 2;
	bool failRead = false, excessiveRead = false, excessiveWrite = false;
	int refs = 1;
};

void verifyStartup(IMaster* master)
{
	Bytes input = startup({}, 80877104);
	append(input, startup({}, 80877103));
	append(input, startup({{"user", "alice"}, {"database", "db"},
		{"application_name", "codec-test"}, {"client_encoding", "UTF8"}}));
	Connection connection(std::move(input));
	connection.readChunk = 1;
	Protocol protocol(master, &connection);
	Startup result;
	require(protocol.readStartup(result), "Startup was mistaken for EOF");
	require(result.user == "alice" && result.database == "db" &&
		result.applicationName == "codec-test" && result.clientEncoding == "UTF8",
		"Startup values changed");
	require(connection.output == Bytes({'N', 'N'}), "SSL/GSS refusal is not one byte each");
	require(connection.position == connection.input.size(), "Startup framing lost alignment");
	Message message;
	require(!protocol.readMessage(message), "Clean EOF was rejected");

	Connection plain(startup({{"user", "alice"}, {"database", "db"}}));
	Protocol plainProtocol(master, &plain);
	require(plainProtocol.readStartup(result) && result.clientEncoding == "UTF8" &&
		result.applicationName.empty(), "Absent optional startup values did not default");
	Connection defaultDatabase(startup({{"user", "alice"}}));
	Protocol defaultProtocol(master, &defaultDatabase);
	require(defaultProtocol.readStartup(result) && result.database == "alice",
		"Absent startup database did not default to user");
}

void verifyStartupErrors(IMaster* master)
{
	const std::pair<const char*, Bytes> cases[] = {
		{"0A000", startup({}, 196609)},
		{"0A000", startup({{"user", "a"}, {"database", "d"}, {"options", "-c x=y"}})},
		{"0A000", startup({{"user", "a"}, {"database", "d"}, {"replication", "true"}})},
		{"0A000", startup({{"user", "a"}, {"database", "d"}, {"unknown", "x"}})},
		{"22023", startup({{"database", "d"}})},
		{"22023", startup({{"user", ""}, {"database", "d"}})},
		{"22023", startup({{"user", "a"}, {"database", ""}})},
		{"22023", startup({{"user", "a"}, {"database", "d"}, {"client_encoding", "LATIN1"}})},
		{"08P01", startup({{"user", "a"}, {"user", "b"}, {"database", "d"}})},
		{"08P01", {0, 0, 0, 7}},
		{"08P01", {0xFF, 0xFF, 0xFF, 0xFF}},
		{"08P01", {0, 0}},
		{"08P01", {0, 0, 0, 8, 0, 3, 0, 0}},
		{"22021", startup({{"user", "a"}, {"database", "d"}, {"application_name", "\xED\xA0\x80"}})}
	};
	for (const auto& item : cases)
	{
		Connection connection(item.second);
		Protocol protocol(master, &connection);
		Startup result;
		result.user = "unchanged";
		expectError(item.first, [&] { protocol.readStartup(result); });
		require(result.user == "unchanged", "Invalid startup partially changed result");
	}
	Bytes repeated = startup({}, 80877103);
	append(repeated, startup({}, 80877103));
	Connection connection(repeated);
	Protocol protocol(master, &connection);
	Startup result;
	expectError("08P01", [&] { protocol.readStartup(result); });
	require(connection.output == Bytes({'N'}), "Repeated SSL request received another response");

	Bytes trailing = startup({{"user", "a"}, {"database", "d"}});
	++trailing[3];
	trailing.push_back('x');
	Connection extra(trailing);
	Protocol extraProtocol(master, &extra);
	expectError("08P01", [&] { extraProtocol.readStartup(result); });

	Connection empty;
	Protocol emptyProtocol(master, &empty);
	require(!emptyProtocol.readStartup(result), "Startup clean EOF was rejected");
}

void verifyFrontend(IMaster* master)
{
	// Lexical-looking text is deliberately opaque to the codec.
	const std::string sql = " /* ; BEGIN $1 ? */ SELECT '\xD0\xAF' FROM RDB$DATABASE;\r\n";
	Bytes input = frame('Q', text(sql));
	append(input, frame('p', text("s\xC3\xA9" "cret")));
	append(input, frame('Q', text("")));
	append(input, frame('X', {}));
	Connection connection(input);
	connection.readChunk = 1024;
	Protocol protocol(master, &connection);
	Message message;
	require(protocol.readMessage(message) && message.type == 'Q' && message.text() == sql,
		"Query bytes were interpreted or changed");
	require(protocol.readMessage(message) && message.type == 'p' && message.text() == "s\xC3\xA9" "cret",
		"Password bytes changed");
	Message moved(std::move(message));
	require(moved.type == 'p' && moved.text() == "s\xC3\xA9" "cret" && !message.type,
		"Message move lost ownership");
	moved.clear();
	require(moved.body.empty() && !moved.type, "Message clear retained content");
	require(protocol.readMessage(message) && message.type == 'Q' && message.text().empty(),
		"Empty query was rejected by codec");
	require(protocol.readMessage(message) && message.type == 'X' && message.body.empty(),
		"Terminate was not parsed");
	require(!protocol.readMessage(message), "Coalesced frames lost their boundary");
	static_assert(!std::is_copy_constructible_v<Message> && !std::is_copy_assignable_v<Message>);
}

void verifyFrontendErrors(IMaster* master)
{
	const std::pair<const char*, Bytes> cases[] = {
		{"08P01", {'Q', 0, 0, 0, 3}},
		{"08P01", {'Q', 0xFF, 0xFF, 0xFF, 0xFF}},
		{"08P01", {'Q', 0}},
		{"08P01", {'Q', 0, 0, 0, 6, 'x'}},
		{"08P01", frame('Q', {})},
		{"08P01", frame('p', {'x'})},
		{"08P01", frame('Q', {'x', 0, 'y', 0})},
		{"08P01", frame('X', {0})},
		{"22021", frame('Q', {0xC0, 0x80, 0})},
		{"22021", frame('Q', {0xE2, 0x82, 0})},
		{"22021", frame('p', {0xF4, 0x90, 0x80, 0x80, 0})},
		{"22021", frame('p', {0x80, 0})},
		{"22021", frame('Q', {0xED, 0xA0, 0x80, 0})}
	};
	for (const auto& item : cases)
	{
		Connection connection(item.second);
		Protocol protocol(master, &connection);
		Message message;
		expectError(item.first, [&] { protocol.readMessage(message); });
	}
	Limits limits;
	limits.passwordBytes = 4;
	Connection connection(frame('p', text("1234")));
	Protocol protocol(master, &connection, limits);
	Message message;
	expectError("08P01", [&] { protocol.readMessage(message); });
	require(connection.position == 5 && message.body.empty(),
		"Oversized password was read or allocated before checking its length");
	Connection boundary(frame('p', text("123")));
	Protocol boundaryProtocol(master, &boundary, limits);
	require(boundaryProtocol.readMessage(message) && message.text() == "123",
		"Password length at the configured boundary was rejected");

	// Unsupported tags still have bounded framing; the session decides policy.
	Connection unsupported(frame('Y', {1, 2, 3}));
	Protocol unsupportedProtocol(master, &unsupported);
	require(unsupportedProtocol.readMessage(message) && message.type == 'Y' &&
		message.body == Bytes({1, 2, 3}), "Unsupported frame lost its boundary");
}

void verifyBackend(IMaster* master)
{
	Connection connection;
	connection.writeChunk = 1;
	Protocol protocol(master, &connection);
	protocol.authenticationCleartext();
	protocol.authenticationOk();
	protocol.parameterStatus("client_encoding", "UTF8");
	protocol.ready();
	protocol.error("0A000", "Unsupported", true);
	const Bytes expected = {
		'R', 0, 0, 0, 8, 0, 0, 0, 3,
		'R', 0, 0, 0, 8, 0, 0, 0, 0,
		'S', 0, 0, 0, 25, 'c', 'l', 'i', 'e', 'n', 't', '_', 'e', 'n', 'c', 'o', 'd', 'i', 'n', 'g', 0,
		'U', 'T', 'F', '8', 0,
		'Z', 0, 0, 0, 5, 'I',
		'E', 0, 0, 0, 39, 'S', 'F', 'A', 'T', 'A', 'L', 0,
		'V', 'F', 'A', 'T', 'A', 'L', 0, 'C', '0', 'A', '0', '0', '0', 0,
		'M', 'U', 'n', 's', 'u', 'p', 'p', 'o', 'r', 't', 'e', 'd', 0, 0
	};
	require(connection.output == expected, "Backend wire bytes differ from protocol layout");
	const auto before = connection.output.size();
	expectError("22021", [&] { protocol.parameterStatus("n", std::string_view("x\0y", 3)); });
	require(connection.output.size() == before, "Invalid backend value was partially written");

	Limits limits;
	limits.frameBytes = 5;
	Connection tiny;
	Protocol tinyProtocol(master, &tiny, limits);
	expectError("08P01", [&] { tinyProtocol.parameterStatus("", ""); });
	expectError("08P01", [&] { tinyProtocol.error("0A000", "", false); });
	tinyProtocol.ready('T');
	require(tiny.output == Bytes({'Z', 0, 0, 0, 5, 'T'}), "Minimum Ready frame was rejected");

	Connection stalled;
	stalled.writeChunk = 0;
	Protocol stalledProtocol(master, &stalled);
	expectError("08006", [&] { stalledProtocol.ready(); });
	require(stalled.output.empty(), "Failed write emitted a second error frame");
	Connection excessive;
	excessive.excessiveWrite = true;
	Protocol excessiveProtocol(master, &excessive);
	expectError("08006", [&] { excessiveProtocol.ready(); });
	require(excessive.output == Bytes({'Z', 0}), "Invalid write progress appended an error frame");
}

void verifyResults(IMaster* master)
{
	Connection connection;
	connection.writeChunk = 1;
	Protocol protocol(master, &connection);
	protocol.rowDescription({{"answer", 23, 4}});
	protocol.dataRow({std::nullopt, std::string(), std::string("\xD0\xAF"),
		std::string("-2147483648")});
	protocol.commandComplete("SELECT 1");
	const Bytes expected = {
		'T', 0, 0, 0, 31, 0, 1, 'a', 'n', 's', 'w', 'e', 'r', 0,
		0, 0, 0, 0, 0, 0, 0, 0, 0, 23, 0, 4, 0xFF, 0xFF, 0xFF, 0xFF, 0, 0,
		'D', 0, 0, 0, 35, 0, 4,
		0xFF, 0xFF, 0xFF, 0xFF,
		0, 0, 0, 0,
		0, 0, 0, 2, 0xD0, 0xAF,
		0, 0, 0, 11, '-', '2', '1', '4', '7', '4', '8', '3', '6', '4', '8',
		'C', 0, 0, 0, 13, 'S', 'E', 'L', 'E', 'C', 'T', ' ', '1', 0
	};
	require(connection.output == expected,
		"RowDescription/DataRow/CommandComplete differ from literal wire layout");
	connection.output.clear();
	protocol.rowDescription({{"\xD0\xAF", 1043, -1, 7}});
	const Bytes varying = {
		'T', 0, 0, 0, 27, 0, 1, 0xD0, 0xAF, 0,
		0, 0, 0, 0, 0, 0, 0, 0, 4, 19, 0xFF, 0xFF, 0, 0, 0, 7, 0, 0
	};
	require(connection.output == varying,
		"UTF8 alias, signed type size or type modifier changed");
	connection.output.clear();
	protocol.rowDescription({});
	protocol.dataRow({});
	require(connection.output == Bytes({'T', 0, 0, 0, 6, 0, 0, 'D', 0, 0, 0, 6, 0, 0}),
		"Zero-column rows lost their count or boundary");
	connection.output.clear();
	protocol.emptyQuery();
	protocol.ready();
	require(connection.output == Bytes({'I', 0, 0, 0, 4, 'Z', 0, 0, 0, 5, 'I'}),
		"EmptyQueryResponse must contain no CommandComplete or result payload");
}

void verifyResultErrors(IMaster* master)
{
	Connection connection;
	Protocol protocol(master, &connection);
	expectError("22021", [&] {
		protocol.rowDescription({{"valid", 23, 4}, {std::string("x\0y", 3), 23, 4}});
	});
	expectError("22021", [&] { protocol.rowDescription({{"\xED\xA0\x80", 23, 4}}); });
	expectError("22021", [&] { protocol.dataRow({std::string("valid"), std::string("x\0y", 3)}); });
	expectError("22021", [&] { protocol.dataRow({std::string("\xF4\x90\x80\x80")}); });
	expectError("22021", [&] { protocol.commandComplete(std::string_view("SELECT 1\0x", 10)); });
	expectError("22021", [&] { protocol.commandComplete("\xC0\x80"); });
	require(connection.output.empty(), "Invalid result text emitted a partial frame");

	Limits limits;
	limits.frameBytes = 31;
	Connection description;
	Protocol descriptionProtocol(master, &description, limits);
	expectError("0A000", [&] { descriptionProtocol.rowDescription({{"answer!", 23, 4}}); });
	expectError("0A000", [&] { descriptionProtocol.rowDescription({{"", 23, 4}, {"", 23, 4}}); });
	require(description.output.empty(), "Oversized RowDescription emitted a partial frame");
	descriptionProtocol.rowDescription({{"answer", 23, 4}});
	require(description.output.size() == 32 && description.output[4] == 31,
		"RowDescription at its exact byte limit was rejected");

	limits.frameBytes = 14;
	Connection row;
	Protocol rowProtocol(master, &row, limits);
	expectError("0A000", [&] { rowProtocol.dataRow({std::string("12345")}); });
	expectError("0A000", [&] { rowProtocol.dataRow({std::string("ab"), std::string("cd")}); });
	require(row.output.empty(), "Oversized DataRow emitted a partial frame");
	rowProtocol.dataRow({std::string("1234")});
	require(row.output.size() == 15 && row.output[4] == 14,
		"DataRow at its exact byte limit was rejected");

	limits.frameBytes = 13;
	Connection command;
	Protocol commandProtocol(master, &command, limits);
	expectError("0A000", [&] { commandProtocol.commandComplete("SELECT 10"); });
	require(command.output.empty(), "Oversized CommandComplete emitted a partial frame");
	commandProtocol.commandComplete("SELECT 1");
	require(command.output.size() == 14 && command.output[4] == 13,
		"CommandComplete at its exact byte limit was rejected");

	limits.frameBytes = 5;
	Connection tiny;
	Protocol tinyProtocol(master, &tiny, limits);
	expectError("0A000", [&] { tinyProtocol.rowDescription({}); });
	expectError("0A000", [&] { tinyProtocol.dataRow({}); });
	require(tiny.output.empty(), "Short result frame limit underflowed");

	// The initial result profile supports at most 32767 fields. Do not truncate
	// a larger count, even when the message fits the independent byte limit.
	std::vector<Column> columns(32768, {"", 23, 4});
	std::vector<std::optional<std::string>> values(32768);
	expectError("0A000", [&] { protocol.rowDescription(columns); });
	expectError("0A000", [&] { protocol.dataRow(values); });
	require(connection.output.empty(), "Excessive result field count wrote a frame");
	values.pop_back();
	connection.writeChunk = 4096;
	protocol.dataRow(values);
	require(connection.output.size() == 131075 && connection.output[5] == 0x7F &&
		connection.output[6] == 0xFF, "Maximum supported result count changed");
}

void verifyTransport(IMaster* master)
{
	Message message;
	Connection broken;
	broken.failRead = true;
	Protocol brokenProtocol(master, &broken);
	bool failed = false;
	try
	{
		brokenProtocol.readMessage(message);
	}
	catch (const FbException& error)
	{
		failed = true;
		require(error.getStatus()->getErrors()[1] == isc_net_read_err,
			"Transport ISC status was lost");
	}
	require(failed, "Transport failure was reported as EOF");
	Connection excessive({'Q'});
	excessive.excessiveRead = true;
	Protocol excessiveProtocol(master, &excessive);
	expectError("08P01", [&] { excessiveProtocol.readMessage(message); });
}

} // namespace

int main()
{
	try
	{
		IMaster* master = fb_get_master_interface();
		verifyStartup(master);
		verifyStartupErrors(master);
		verifyFrontend(master);
		verifyFrontendErrors(master);
		verifyBackend(master);
		verifyResults(master);
		verifyResultErrors(master);
		verifyTransport(master);
		std::puts("PgWire protocol codec tests passed");
		return 0;
	}
	catch (const std::exception& error)
	{
		std::fprintf(stderr, "PgWire protocol codec tests failed: %s\n", error.what());
	}
	catch (...)
	{
		std::fputs("PgWire protocol codec tests failed: unexpected exception\n", stderr);
	}
	return 1;
}
