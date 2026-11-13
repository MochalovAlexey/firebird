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

// Shared bytea input layer. SQL string parsing and Bind framing happen before this function.
#ifndef COMMON_BYTEA_H
#define COMMON_BYTEA_H
#include <string_view>
namespace Firebird {
template <typename Append, typename Invalid>
void decodeBytea(std::string_view text, Append append, Invalid invalid)
{
	if (text.size() >= 2 && text[0] == '\\' && text[1] == 'x')
	{
		const auto hex = [](char c) -> int
		{
			if (c >= '0' && c <= '9') return c - '0';
			if (c >= 'a' && c <= 'f') return c - 'a' + 10;
			if (c >= 'A' && c <= 'F') return c - 'A' + 10;
			return -1;
		};
		for (std::size_t i = 2; i < text.size();)
		{
			const char c = text[i];
			if (c == ' ' || c == '\t' || c == '\n' || c == '\r')
			{
				++i;
				continue;
			}
			if (i + 1 == text.size() || hex(c) < 0 || hex(text[i + 1]) < 0)
				invalid(true);
			append((hex(c) << 4) | hex(text[i + 1]));
			i += 2;
		}
	}
	else
	{
		for (std::size_t i = 0; i < text.size(); ++i)
		{
			if (text[i] != '\\')
				append(text[i]);
			else if (i + 1 < text.size() && text[i + 1] == '\\')
			{
				append('\\');
				++i;
			}
			else if (i + 3 < text.size() && text[i + 1] >= '0' && text[i + 1] <= '3' &&
				text[i + 2] >= '0' && text[i + 2] <= '7' && text[i + 3] >= '0' && text[i + 3] <= '7')
			{
				append((text[i + 1] - '0') * 64 + (text[i + 2] - '0') * 8 + text[i + 3] - '0');
				i += 3;
			}
			else
				invalid(false);
		}
	}
}
} // namespace Firebird
#endif
