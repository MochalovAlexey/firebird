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

#include "SqlInfo.h"

#include <utility>

namespace Firebird::PgWire
{

namespace
{

enum class TokenKind { Word, Other, End, Invalid };

struct Token
{
	TokenKind kind;
	std::string_view text;

	bool is(std::string_view keyword) const noexcept
	{
		if (kind != TokenKind::Word || text.size() != keyword.size())
			return false;
		for (std::size_t i = 0; i < text.size(); ++i)
		{
			const auto c = static_cast<unsigned char>(text[i]);
			const auto upper = c >= 'a' && c <= 'z' ? c - ('a' - 'A') : c;
			if (upper != static_cast<unsigned char>(keyword[i]))
				return false;
		}
		return true;
	}
};

bool whitespace(unsigned char c) noexcept
{
	// CHR_WHITE in dsql/chars.h: vertical tab is not SQL whitespace.
	return c == ' ' || c == '\t' || c == '\n' || c == '\f' || c == '\r';
}

bool identifier(unsigned char c) noexcept
{
	// Include all possible identifier continuations so a keyword prefix never
	// matches SELECT1, SELECT$NAME, SELECT_name or SELECT followed by UTF-8.
	return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
		(c >= '0' && c <= '9') || c == '_' || c == '$' || c >= 0x80;
}

class Prefix final
{
public:
	explicit Prefix(std::string_view value) : sql(value) {}

	Token next()
	{
		for (;;)
		{
			while (position < sql.size() && whitespace(sql[position]))
				++position;
			if (position == sql.size())
				return {TokenKind::End, {}};
			if (sql.substr(position, 2) == "--")
			{
				position += 2;
				while (position < sql.size() && sql[position] != '\r' && sql[position] != '\n')
					++position;
				continue;
			}
			if (sql.substr(position, 2) == "/*")
			{
				// Firebird comments do not nest. Only the first closing pair matters.
				const auto end = sql.find("*/", position + 2);
				if (end == std::string_view::npos)
					return {TokenKind::Invalid, {}};
				position = end + 2;
				continue;
			}
			break;
		}

		const auto start = position++;
		if (identifier(sql[start]))
		{
			while (position < sql.size() && identifier(sql[position]))
				++position;
			return {TokenKind::Word, sql.substr(start, position - start)};
		}
		if (sql[start] == '\'' || sql[start] == '"')
		{
			const auto quote = sql[start];
			while (position < sql.size())
			{
				if (sql[position++] != quote)
					continue;
				if (position < sql.size() && sql[position] == quote)
				{
					++position;
					continue;
				}
				// Quoted strings and identifiers remain opaque, never keywords.
				return {TokenKind::Other, sql.substr(start, position - start)};
			}
			return {TokenKind::Invalid, {}};
		}
		return {TokenKind::Other, sql.substr(start, 1)};
	}

private:
	std::string_view sql;
	std::size_t position = 0;
};

SqlInfo command(std::string tag, CountKind count = CountKind::None)
{
	return {std::move(tag), count, false, true};
}

SqlInfo controlCommand(std::string tag, ControlKind control)
{
	return {std::move(tag), CountKind::None, false, true, control};
}

bool name(const Token& token)
{
	// Do not unquote, normalize or retain a savepoint name. Execution passes
	// the original SQL to the engine, including native identifier semantics.
	return token.kind == TokenKind::Word ||
		(token.kind == TokenKind::Other && token.text.front() == '"');
}

SqlInfo endTransaction(Prefix& prefix, bool commit)
{
	auto token = prefix.next();
	if (token.is("WORK"))
		token = prefix.next();
	if (!commit && token.is("TO"))
	{
		token = prefix.next();
		if (token.is("SAVEPOINT"))
			token = prefix.next();
		return name(token) ? controlCommand("ROLLBACK", ControlKind::RollbackTo) : SqlInfo{};
	}
	const bool retain = token.is("RETAIN");
	if (retain)
	{
		token = prefix.next();
		if (token.is("SNAPSHOT"))
			token = prefix.next();
	}
	// RETAINING is not a Firebird SQL keyword. Neither it nor a quoted
	// modifier must accidentally become a full COMMIT or ROLLBACK.
	if (token.kind != TokenKind::End && token.text != ";")
		return {};
	if (commit)
	{
		return controlCommand("COMMIT",
			retain ? ControlKind::CommitRetaining : ControlKind::Commit);
	}
	return controlCommand("ROLLBACK",
		retain ? ControlKind::RollbackRetaining : ControlKind::Rollback);
}

bool sessionCommand(Prefix& prefix, const Token& second)
{
	// Explicit native mng_statement prefixes from dsql/parse.y. Options and
	// values remain the engine's responsibility; this is not PostgreSQL SET.
	if (second.is("ROLE") || second.is("OPTIMIZE"))
		return true;
	if (second.is("TRUSTED"))
		return prefix.next().is("ROLE");
	if (second.is("DEBUG"))
		return prefix.next().is("OPTION");
	if (second.is("DECFLOAT"))
	{
		const auto third = prefix.next();
		return third.is("ROUND") || (third.is("TRAPS") && prefix.next().is("TO"));
	}
	if (second.is("SESSION"))
		return prefix.next().is("IDLE") && prefix.next().is("TIMEOUT");
	if (second.is("STATEMENT"))
		return prefix.next().is("TIMEOUT");
	if (second.is("TIME"))
		return prefix.next().is("ZONE");
	if (second.is("BIND"))
		return prefix.next().is("OF");
	if (second.is("SEARCH_PATH"))
		return prefix.next().is("TO");
	return false;
}

bool direction(const Token& token)
{
	return token.is("ASC") || token.is("ASCENDING") ||
		token.is("DESC") || token.is("DESCENDING");
}

std::string_view ddlObject(Prefix& prefix, Token token)
{
	if (token.is("GLOBAL") || token.is("LOCAL"))
	{
		const bool global = token.is("GLOBAL");
		token = prefix.next();
		if (global && token.is("MAPPING"))
			return "MAPPING";
		return token.is("TEMPORARY") && prefix.next().is("TABLE") ? "TABLE" : "";
	}
	if (token.is("UNIQUE") || direction(token))
	{
		if (token.is("UNIQUE"))
			token = prefix.next();
		if (direction(token))
			token = prefix.next();
		return token.is("INDEX") ? "INDEX" : "";
	}
	if (token.is("EXTERNAL"))
	{
		token = prefix.next();
		if (token.is("FUNCTION"))
			return "FUNCTION";
		return token.is("CONNECTIONS") && prefix.next().is("POOL") ?
			"EXTERNAL CONNECTIONS POOL" : "";
	}
	if (token.is("AGGREGATE"))
		return prefix.next().is("FUNCTION") ? "FUNCTION" : "";
	if (token.is("CURRENT"))
		return prefix.next().is("USER") ? "USER" : "";
	if (token.is("CHARACTER"))
		return prefix.next().is("SET") ? "CHARACTER SET" : "";
	if (token.is("GENERATOR") || token.is("SEQUENCE"))
		return "SEQUENCE";
	if (token.is("PACKAGE"))
	{
		token = prefix.next();
		if (token.kind == TokenKind::Invalid)
			return "";
		if (!token.is("BODY"))
			return "PACKAGE";
		// BODY is also a non-reserved identifier. Distinguish the modifier
		// from a package (or its schema qualifier) actually named BODY.
		token = prefix.next();
		if (token.kind == TokenKind::Invalid)
			return "";
		if (token.kind == TokenKind::End || token.text == ";" || token.text == "." ||
			token.text == "%" || token.is("AS") || token.is("DROP"))
		{
			return "PACKAGE";
		}
		if (token.is("SQL"))
		{
			// SQL can itself be the name following the BODY modifier.
			token = prefix.next();
			if (token.kind == TokenKind::Invalid)
				return "";
			if (token.is("SECURITY"))
				return "PACKAGE";
		}
		return "PACKAGE BODY";
	}
	constexpr std::string_view objects[] = {
		"DATABASE", "TABLE", "VIEW", "INDEX", "PROCEDURE", "FUNCTION", "TRIGGER",
		"DOMAIN", "EXCEPTION", "ROLE", "USER", "COLLATION", "FILTER", "SHADOW",
		"MAPPING", "SCHEMA"
	};
	for (const auto object : objects)
	{
		if (token.is(object))
			return object;
	}
	return "";
}

} // namespace

SqlInfo inspectSql(std::string_view sql)
{
	Prefix prefix(sql);
	const auto first = prefix.next();
	if (first.kind == TokenKind::End)
		return {{}, CountKind::None, true, false};
	// Native WITH introduces only select_expr. The engine has already checked
	// the CTE and its final statement; do not walk parentheses or scan its body.
	// A successfully prepared statement starting with '(' is also a SELECT:
	// query_primary is the only native top-level production with that prefix.
	if (first.is("SELECT") || first.is("WITH") ||
		(first.kind == TokenKind::Other && first.text == "("))
		return command("SELECT", CountKind::Rows);
	if (first.is("INSERT"))
		return command("INSERT 0", CountKind::Affected);
	if (first.is("UPDATE"))
	{
		const auto second = prefix.next();
		if (second.kind == TokenKind::Invalid)
			return {};
		if (second.is("OR"))
		{
			return prefix.next().is("INSERT") ?
				command("UPDATE OR INSERT", CountKind::Affected) : SqlInfo{};
		}
		return command("UPDATE", CountKind::Affected);
	}
	if (first.is("DELETE"))
		return command("DELETE", CountKind::Affected);
	if (first.is("MERGE"))
		return command("MERGE", CountKind::Affected);
	if (first.is("CALL"))
		return command("EXECUTE PROCEDURE");
	if (first.is("EXECUTE"))
	{
		const auto second = prefix.next();
		if (second.is("PROCEDURE"))
			return command("EXECUTE PROCEDURE");
		if (second.is("BLOCK"))
			return command("EXECUTE BLOCK");
		return {};
	}
	if (first.is("GRANT"))
		return command("GRANT");
	if (first.is("REVOKE"))
		return command("REVOKE");
	if (first.is("COMMENT"))
		return command("COMMENT");
	if (first.is("COMMIT"))
		return endTransaction(prefix, true);
	if (first.is("ROLLBACK"))
		return endTransaction(prefix, false);
	if (first.is("SAVEPOINT"))
	{
		return name(prefix.next()) ?
			controlCommand("SAVEPOINT", ControlKind::Savepoint) : SqlInfo{};
	}
	if (first.is("RELEASE"))
	{
		return prefix.next().is("SAVEPOINT") && name(prefix.next()) ?
			controlCommand("RELEASE", ControlKind::ReleaseSavepoint) : SqlInfo{};
	}
	if (first.is("SET"))
	{
		const auto second = prefix.next();
		if (second.is("TRANSACTION"))
			return controlCommand("BEGIN", ControlKind::Start);
		if (second.is("GENERATOR"))
			return command("SET GENERATOR");
		if (second.is("STATISTICS") && prefix.next().is("INDEX"))
			return command("SET STATISTICS");
		if (sessionCommand(prefix, second))
			return controlCommand("SET", ControlKind::Session);
		return {};
	}
	constexpr std::string_view actions[] = {"CREATE", "ALTER", "DROP", "RECREATE", "DECLARE"};
	for (const auto action : actions)
	{
		if (!first.is(action))
			continue;
		auto object = prefix.next();
		if (first.is("ALTER") && object.is("SESSION"))
		{
			return prefix.next().is("RESET") ?
				controlCommand("RESET", ControlKind::ResetSession) : SqlInfo{};
		}
		std::string tag(action);
		if (first.is("CREATE") && object.is("OR"))
		{
			if (!prefix.next().is("ALTER"))
				return {};
			tag += " OR ALTER";
			object = prefix.next();
		}
		const auto name = ddlObject(prefix, object);
		if (name.empty())
			return {};
		tag += ' ';
		tag += name;
		return command(std::move(tag));
	}
	// Includes PostgreSQL-only BEGIN/START/RESET and native USING wrappers.
	return {};
}

} // namespace Firebird::PgWire
