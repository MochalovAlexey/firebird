#include "firebird.h"
#include "boost/test/unit_test.hpp"
#include "../common/config/config.h"
#include "../common/config/config_file.h"
#include "../remote/server/wire/EndpointConfig.h"

using namespace Firebird;
using namespace Firebird::WireProtocol;

BOOST_AUTO_TEST_SUITE(WireProtocolConfigTests)

BOOST_AUTO_TEST_CASE(RegisteredAsGlobalDisabledSettings)
{
	static_assert(KEY_WIRE_PROTOCOL_PLUGINS == KEY_ALLOW_UPDATE_OVERWRITE + 1);
	static_assert(KEY_WIRE_PROTOCOL_PORTS == KEY_WIRE_PROTOCOL_PLUGINS + 1);

	BOOST_CHECK_EQUAL(Config::getKeyByName("WireProtocolPlugins"), KEY_WIRE_PROTOCOL_PLUGINS);
	BOOST_CHECK_EQUAL(Config::getKeyByName("WireProtocolPorts"), KEY_WIRE_PROTOCOL_PORTS);
	BOOST_CHECK(entries[KEY_WIRE_PROTOCOL_PLUGINS].is_global);
	BOOST_CHECK(entries[KEY_WIRE_PROTOCOL_PORTS].is_global);

	ConfigFile file(ConfigFile::USE_TEXT, "");
	Config config(file);
	BOOST_CHECK_EQUAL(config.getString(KEY_WIRE_PROTOCOL_PLUGINS), "");
	BOOST_CHECK_EQUAL(config.getString(KEY_WIRE_PROTOCOL_PORTS), "");
}

BOOST_AUTO_TEST_CASE(DatabaseCannotOverrideEndpoints)
{
	ConfigFile serverFile(ConfigFile::USE_TEXT,
		"WireProtocolPlugins = PgLocal, PgRemote\nWireProtocolPorts = 15432, 15433\n");
	Config server(serverFile);
	ConfigFile databaseFile(ConfigFile::USE_TEXT,
		"WireProtocolPlugins = Other\nWireProtocolPorts = 15434\n");
	Config database(databaseFile, "databases.conf", server);

	BOOST_CHECK_EQUAL(database.getString(KEY_WIRE_PROTOCOL_PLUGINS), "PgLocal, PgRemote");
	BOOST_CHECK_EQUAL(database.getString(KEY_WIRE_PROTOCOL_PORTS), "15432, 15433");
}

BOOST_AUTO_TEST_CASE(BlankListsDisableEndpoints)
{
	std::vector<EndpointConfig> endpoints = {{"Previous", 15432}};
	std::string error = "previous error";
	BOOST_REQUIRE(parseEndpointConfig(" \t\r\n\v\f", "\r\n ", endpoints, error));
	BOOST_CHECK(endpoints.empty());
	BOOST_CHECK(error.empty());
	BOOST_REQUIRE(parseEndpointConfig({}, {}, endpoints, error));
	BOOST_CHECK(endpoints.empty());
}

BOOST_AUTO_TEST_CASE(PositionsArePreservedBeforeLoading)
{
	std::vector<EndpointConfig> endpoints;
	std::string error;
	BOOST_REQUIRE_MESSAGE(parseEndpointConfig(" StubA , MissingPlugin , StubB ",
		" 15432 , 15433 , 15434 ", endpoints, error), error);
	BOOST_REQUIRE_EQUAL(endpoints.size(), 3u);
	BOOST_CHECK_EQUAL(endpoints[0].plugin, "StubA");
	BOOST_CHECK_EQUAL(endpoints[0].port, 15432u);
	BOOST_CHECK_EQUAL(endpoints[1].plugin, "MissingPlugin");
	BOOST_CHECK_EQUAL(endpoints[1].port, 15433u);
	BOOST_CHECK_EQUAL(endpoints[2].plugin, "StubB");
	BOOST_CHECK_EQUAL(endpoints[2].port, 15434u);
}

BOOST_AUTO_TEST_CASE(PortBoundsAndAsciiWhitespace)
{
	std::vector<EndpointConfig> endpoints;
	std::string error;
	BOOST_REQUIRE_MESSAGE(parseEndpointConfig("\tPgLocal\r,\nPgRemote\v,\fPgOther ",
		" 1\t,\r65535\n, 0015432 ", endpoints, error), error);
	BOOST_REQUIRE_EQUAL(endpoints.size(), 3u);
	BOOST_CHECK_EQUAL(endpoints[0].port, 1u);
	BOOST_CHECK_EQUAL(endpoints[1].port, 65535u);
	BOOST_CHECK_EQUAL(endpoints[2].port, 15432u);
}

BOOST_AUTO_TEST_CASE(EmptyElementsAreRejectedWithoutCompaction)
{
	struct TestCase
	{
		const char* plugins;
		const char* ports;
		const char* setting;
		const char* position;
	};
	const TestCase cases[] = {
		{",PgWire", "15432,15433", "WireProtocolPlugins", "position 1"},
		{"PgWire,,Other", "15432,15433,15434", "WireProtocolPlugins", "position 2"},
		{"PgWire, \t,Other", "15432,15433,15434", "WireProtocolPlugins", "position 2"},
		{"PgWire,", "15432,15433", "WireProtocolPlugins", "position 2"},
		{"PgWire,Other", ",15433", "WireProtocolPorts", "position 1"},
		{"PgWire,Other,Third", "15432,,15434", "WireProtocolPorts", "position 2"},
		{"PgWire,Other", "15432, \t", "WireProtocolPorts", "position 2"}
	};
	for (const auto& test : cases)
	{
		BOOST_TEST_CONTEXT(test.plugins << " / " << test.ports)
		{
			std::vector<EndpointConfig> endpoints;
			std::string error;
			BOOST_CHECK(!parseEndpointConfig(test.plugins, test.ports, endpoints, error));
			BOOST_CHECK(endpoints.empty());
			BOOST_CHECK(error.find(test.setting) != std::string::npos);
			BOOST_CHECK(error.find(test.position) != std::string::npos);
		}
	}
}

BOOST_AUTO_TEST_CASE(ListLengthsMustMatch)
{
	struct TestCase
	{
		const char* plugins;
		const char* ports;
	};
	const TestCase cases[] = {
		{"", "15432"}, {"PgWire", ""}, {" \t", "15432"},
		{"PgWire,Other", "15432"}, {"PgWire", "15432,15433"}
	};
	for (const auto& test : cases)
	{
		std::vector<EndpointConfig> endpoints;
		std::string error;
		BOOST_CHECK(!parseEndpointConfig(test.plugins, test.ports, endpoints, error));
		BOOST_CHECK(endpoints.empty());
		BOOST_CHECK(error.find("same number of elements") != std::string::npos);
	}
}

BOOST_AUTO_TEST_CASE(InvalidPortsAreRejected)
{
	const char* values[] = {
		"0", "000", "-1", "+15432", "65536", "4294967296",
		"18446744073709551616", "999999999999999999999999999999999999",
		"15432x", "1.0", "1e3", "0x3c48", "15 432", "15\t432"
	};
	for (const auto* value : values)
	{
		BOOST_TEST_CONTEXT(value)
		{
			std::vector<EndpointConfig> endpoints;
			std::string error;
			BOOST_CHECK(!parseEndpointConfig("PgWire", value, endpoints, error));
			BOOST_CHECK(endpoints.empty());
			BOOST_CHECK(error.find("WireProtocolPorts") != std::string::npos);
			BOOST_CHECK(error.find("position 1") != std::string::npos);
		}
	}
}

BOOST_AUTO_TEST_CASE(ConfiguredNamesCannotBecomePathsOrLists)
{
	const char* values[] = {
		"Pg Wire", "Pg\tWire", "PgWire;Other", "/tmp/PgWire", "plugins/PgWire",
		"plugins\\PgWire", "C:PgWire", "Pg\nWire"
	};
	for (const auto* value : values)
	{
		std::vector<EndpointConfig> endpoints;
		std::string error;
		BOOST_CHECK(!parseEndpointConfig(value, "15432", endpoints, error));
		BOOST_CHECK(endpoints.empty());
		BOOST_CHECK(error.find("WireProtocolPlugins") != std::string::npos);
	}

	std::vector<EndpointConfig> endpoints;
	std::string error;
	BOOST_CHECK(!parseEndpointConfig(std::string_view("Pg\0Wire", 7), "15432", endpoints, error));
	BOOST_CHECK(!parseEndpointConfig("PgWire", std::string_view("15\0", 3), endpoints, error));
}

BOOST_AUTO_TEST_CASE(DuplicateNamesAndPortsAreRejected)
{
	std::vector<EndpointConfig> endpoints;
	std::string error;
	BOOST_CHECK(!parseEndpointConfig("PgWire,PgWire", "15432,15433", endpoints, error));
	BOOST_CHECK(error.find("Duplicate WireProtocolPlugins") != std::string::npos);
	BOOST_CHECK(error.find("position 2") != std::string::npos);
	BOOST_CHECK(!parseEndpointConfig("PgLocal,PgRemote", "15432,015432", endpoints, error));
	BOOST_CHECK(error.find("Duplicate WireProtocolPorts") != std::string::npos);
	BOOST_CHECK(error.find("position 2") != std::string::npos);
	BOOST_CHECK(endpoints.empty());
}

BOOST_AUTO_TEST_CASE(FailedParseDoesNotPublishPartialEndpoints)
{
	std::vector<EndpointConfig> endpoints = {{"Previous", 15431}};
	std::string error;
	BOOST_CHECK(!parseEndpointConfig("PgLocal,PgRemote", "15432,invalid", endpoints, error));
	BOOST_REQUIRE_EQUAL(endpoints.size(), 1u);
	BOOST_CHECK_EQUAL(endpoints[0].plugin, "Previous");
	BOOST_CHECK_EQUAL(endpoints[0].port, 15431u);
	BOOST_CHECK(error.find("position 2") != std::string::npos);

	BOOST_REQUIRE_MESSAGE(parseEndpointConfig("PgLocal,PgRemote", "15432,15433", endpoints, error), error);
	BOOST_REQUIRE_EQUAL(endpoints.size(), 2u);
	BOOST_CHECK_EQUAL(endpoints[0].plugin, "PgLocal");
	BOOST_CHECK_EQUAL(endpoints[1].plugin, "PgRemote");
	BOOST_CHECK(error.empty());
}

BOOST_AUTO_TEST_SUITE_END()
