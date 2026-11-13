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

/* regnamespace conversions use the installed namespace projection and the caller's transaction. */
#include "firebird.h"
#include "../jrd/PgNamespace.h"
#include "../jrd/jrd.h"
#include "../jrd/Attachment.h"
#include "../jrd/PreparedStatement.h"
#include "../jrd/ResultSet.h"
#include "../jrd/mov_proto.h"
#include "../jrd/err_proto.h"
#include "../common/classes/auto.h"
#include <cstring>
#include <algorithm>

using namespace Firebird;

namespace Jrd {
namespace {
// PostgreSQL 18 pg_get_keywords(), categories C/R/T. Identifier output quotes
// these words; unreserved keywords (including public) remain unquoted.
const char quotedKeywords[] =
	"all analyse analyze and any array as asc "
	"asymmetric authorization between bigint binary bit boolean both "
	"case cast char character check coalesce collate collation "
	"column concurrently constraint create cross current_catalog current_date current_role "
	"current_schema current_time current_timestamp current_user dec decimal default deferrable "
	"desc distinct do else end except exists extract "
	"false fetch float for foreign freeze from full "
	"grant greatest group grouping having ilike in initially "
	"inner inout int integer intersect interval into is "
	"isnull join json json_array json_arrayagg json_exists json_object json_objectagg "
	"json_query json_scalar json_serialize json_table json_value lateral leading least "
	"left like limit localtime localtimestamp merge_action national natural "
	"nchar none normalize not notnull null nullif numeric "
	"offset on only or order out outer overlaps "
	"overlay placing position precision primary real references returning "
	"right row select session_user setof similar smallint some "
	"substring symmetric system_user table tablesample then time timestamp "
	"to trailing treat trim true union unique user "
	"using values varchar variadic verbose when where window "
	"with xmlattributes xmlconcat xmlelement xmlexists xmlforest xmlnamespaces xmlparse "
	"xmlpi xmlroot xmlserialize xmltable ";

bool keyword(const string& name)
{
	const string needle = " " + name + " ";
	return (" " + string(quotedKeywords)).find(needle) != string::npos;
}

bool space(char c)
{
	return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v';
}

SINT64 input(thread_db* tdbb, dsc* from)
{
	if (!from->isText() && !from->isBlob())
	{
		const SINT64 value = MOV_get_int64(tdbb, from, 0);
		if (value < 0 && (from->dsc_dtype == dtype_long || from->dsc_dtype == dtype_short))
			return static_cast<ULONG>(value);
		if (value < 0 || value > MAX_ULONG)
			ERR_post(Arg::Gds(isc_arith_except) << Arg::Gds(isc_numeric_out_of_range));
		return value;
	}
	auto text = MOV_make_string2(tdbb, from, ttype_utf8);
	while (text.hasData() && space(text[0])) text.erase(0, 1);
	while (text.hasData() && space(text[text.length() - 1])) text.resize(text.length() - 1);
	if (text == "-") return 0;
	if (text.hasData() && std::all_of(text.begin(), text.end(),
		[](char c) { return c >= '0' && c <= '9'; }))
	{
		FB_UINT64 number = 0;
		for (const auto c : text)
		{
			if (c < '0' || c > '9')
				ERR_post(Arg::Gds(isc_pg_namespace_syntax));
			number = number * 10 + c - '0';
			if (number > MAX_ULONG)
				ERR_post(Arg::Gds(isc_arith_except) << Arg::Gds(isc_numeric_out_of_range));
		}
		return number;
	}
	string name;
	if (text.hasData() && text[0] == '"')
	{
		unsigned i = 1;
		bool closed = false;
		for (; i < text.length(); ++i)
		{
			if (text[i] != '"') name += text[i];
			else if (i + 1 < text.length() && text[i+1] == '"') name += text[++i];
			else { closed = true; ++i; break; }
		}
		if (!closed || i != text.length()) ERR_post(Arg::Gds(isc_pg_namespace_syntax));
	}
	else
	{
		for (const auto c : text)
		{
			if (space(c) || c == '.' || c == '"' || !c)
				ERR_post(Arg::Gds(isc_pg_namespace_syntax));
			name += c >= 'A' && c <= 'Z' ? c + ('a' - 'A') : c;
		}
	}
	if (name.isEmpty()) ERR_post(Arg::Gds(isc_pg_namespace_syntax));
	AutoPtr<PreparedStatement> statement(tdbb->getAttachment()->prepareUserStatement(tdbb,
		tdbb->getTransaction(), "SELECT \"oid\" FROM \"pg_catalog\".\"pg_namespace\" WHERE \"nspname\" = ?"));
	statement->setString(tdbb, 1, name);
	const auto result = statement->executeQuery(tdbb, tdbb->getTransaction());
	if (!result->fetch(tdbb))
		ERR_post(Arg::Gds(isc_pg_namespace_missing) << name);
	return result->getBigInt(tdbb, 1);
}

string output(thread_db* tdbb, SINT64 value)
{
	if (!value) return "-";
	AutoPtr<PreparedStatement> statement(tdbb->getAttachment()->prepareUserStatement(tdbb,
		tdbb->getTransaction(), "SELECT \"nspname\" FROM \"pg_catalog\".\"pg_namespace\" WHERE \"oid\" = ?"));
	statement->setBigInt(tdbb, 1, value);
	const auto result = statement->executeQuery(tdbb, tdbb->getTransaction());
	if (!result->fetch(tdbb))
	{
		string number;
		number.printf("%" SQUADFORMAT, value);
		return number;
	}
	const auto name = result->getString(tdbb, 1);
	bool quoted = keyword(name) || name.isEmpty() || (name[0] >= '0' && name[0] <= '9');
	for (const auto c : name)
		quoted |= !((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' ||
			static_cast<UCHAR>(c) >= 128);
	if (!quoted) return name;
	return "\"" + PreparedStatement::escapeName(MetaName(name)) + "\"";
}
}

bool movePgNamespace(thread_db* tdbb, dsc* from, dsc* to)
{
	if (to->isRegNamespace())
	{
		const SINT64 value = input(tdbb, from);
		memcpy(to->dsc_address, &value, sizeof(value));
		return true;
	}
	if (from->isRegNamespace() && to->isText())
	{
		SINT64 value;
		memcpy(&value, from->dsc_address, sizeof(value));
		auto text = output(tdbb, value);
		dsc source;
		source.makeText(text.length(), ttype_utf8, reinterpret_cast<UCHAR*>(text.begin()));
		MOV_move(tdbb, &source, to);
		return true;
	}
	return false;
}
}
