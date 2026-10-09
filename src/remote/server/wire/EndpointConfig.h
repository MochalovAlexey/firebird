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

#ifndef REMOTE_SERVER_WIRE_ENDPOINT_CONFIG_H
#define REMOTE_SERVER_WIRE_ENDPOINT_CONFIG_H

#include <string>
#include <string_view>
#include <vector>

namespace Firebird::WireProtocol
{

struct EndpointConfig
{
	std::string plugin;
	unsigned port;
};

namespace Detail
{

inline std::string_view trimEndpointWhitespace(std::string_view text)
{
	constexpr std::string_view whitespace = " \t\r\n\v\f";
	const auto begin = text.find_first_not_of(whitespace);
	if (begin == std::string_view::npos)
		return {};

	const auto end = text.find_last_not_of(whitespace);
	return text.substr(begin, end - begin + 1);
}

inline bool splitEndpointList(std::string_view text, const char* setting,
	std::vector<std::string_view>& values, std::string& error)
{
	text = trimEndpointWhitespace(text);
	if (text.empty())
		return true;

	for (;;)
	{
		const auto comma = text.find(',');
		const auto value = trimEndpointWhitespace(text.substr(0, comma));
		if (value.empty())
		{
			error = std::string(setting) + " contains an empty element at position " +
				std::to_string(values.size() + 1);
			return false;
		}

		values.push_back(value);
		if (comma == std::string_view::npos)
			return true;

		text.remove_prefix(comma + 1);
	}
}

inline bool validConfiguredName(std::string_view name)
{
	for (const unsigned char c : name)
	{
		// A configured name must remain one PluginManager token on every platform.
		if (c <= ' ' || c == 127 || c == ';' || c == '/' || c == '\\' || c == ':')
			return false;
	}

	return true;
}

} // namespace Detail

// No plugin loading or socket operations occur here. Missing configured names
// retain their position for the later loader. Failure leaves endpoints unchanged.
inline bool parseEndpointConfig(std::string_view plugins, std::string_view ports,
	std::vector<EndpointConfig>& endpoints, std::string& error)
{
	error.clear();
	std::vector<std::string_view> names;
	std::vector<std::string_view> portValues;
	if (!Detail::splitEndpointList(plugins, "WireProtocolPlugins", names, error) ||
		!Detail::splitEndpointList(ports, "WireProtocolPorts", portValues, error))
	{
		return false;
	}

	if (names.size() != portValues.size())
	{
		error = "WireProtocolPlugins and WireProtocolPorts must have the same number of elements";
		return false;
	}

	std::vector<EndpointConfig> parsed;
	parsed.reserve(names.size());
	for (std::size_t i = 0; i < names.size(); ++i)
	{
		if (!Detail::validConfiguredName(names[i]))
		{
			error = "Invalid WireProtocolPlugins configured name at position " + std::to_string(i + 1);
			return false;
		}

		unsigned port = 0;
		for (const unsigned char c : portValues[i])
		{
			if (c < '0' || c > '9' || port > (65535u - (c - '0')) / 10u)
			{
				error = "Invalid WireProtocolPorts value at position " + std::to_string(i + 1) +
					": expected a decimal port in 1..65535";
				return false;
			}
			port = port * 10u + (c - '0');
		}

		if (port == 0)
		{
			error = "Invalid WireProtocolPorts value at position " + std::to_string(i + 1) +
				": expected a decimal port in 1..65535";
			return false;
		}

		for (std::size_t j = 0; j < parsed.size(); ++j)
		{
			const char* duplicate = nullptr;
			if (parsed[j].plugin == names[i])
				duplicate = "WireProtocolPlugins";
			else if (parsed[j].port == port)
				duplicate = "WireProtocolPorts";

			if (duplicate)
			{
				error = std::string("Duplicate ") + duplicate + " value at position " +
					std::to_string(i + 1) + " (first used at position " + std::to_string(j + 1) + ")";
				return false;
			}
		}

		parsed.push_back({std::string(names[i]), port});
	}

	endpoints.swap(parsed);
	return true;
}

} // namespace Firebird::WireProtocol

#endif // REMOTE_SERVER_WIRE_ENDPOINT_CONFIG_H
