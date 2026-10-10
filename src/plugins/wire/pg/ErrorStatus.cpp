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

#include "ErrorStatus.h"

#include <cstring>
#include <utility>

namespace Firebird::PgWire
{
namespace
{

bool validSqlState(const char* state)
{
	if (!state)
		return false;
	for (unsigned i = 0; i < 5; ++i)
	{
		const unsigned char c = state[i];
		if (!((c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z')))
			return false;
	}
	return !state[5];
}

} // namespace

Diagnostic diagnose(const IStatus* status)
{
	Diagnostic result;
	unsigned priority = 0;
	// Inspect the complete structured vector, including nested causes. Calling
	// fb_sqlstate once would only examine its legacy ISC_STATUS_LENGTH prefix.
	for (const ISC_STATUS* p = status->getErrors(); *p != isc_arg_end; )
	{
		const auto tag = *p++;
		if (tag == isc_arg_sql_state)
		{
			const char* state = reinterpret_cast<const char*>(*p++);
			if (!validSqlState(state) || !std::strcmp(state, "00000") || !std::strcmp(state, "HY000"))
				continue;
			const unsigned rank = !std::strcmp(state, "42000") || !std::strcmp(state, "22000") ||
				!std::strcmp(state, "23000") ? 10 : 35;
			if (rank > priority)
			{
				result = {state, "Firebird SQL operation failed", state[0] == '0' && state[1] == '8'};
				priority = rank;
			}
			continue;
		}
		if (tag != isc_arg_gds)
		{
			p += tag == isc_arg_cstring ? 2 : 1;
			continue;
		}
		const ISC_STATUS code = *p++;
		Diagnostic candidate;
		unsigned rank = 0;
		switch (code)
		{
		case isc_att_shut_idle:
			candidate = {"57P05", "Database attachment idle timeout", true}; rank = 100; break;
		case isc_att_shutdown:
		case isc_att_shut_killed:
		case isc_att_shut_db_down:
		case isc_att_shut_engine:
		case isc_shutdown:
			candidate = {"57P01", "Database attachment was shut down", true}; rank = 90; break;
		case isc_bad_db_handle:
		case isc_bad_trans_handle:
		case isc_bad_req_handle:
			candidate = {"08006", "Database handle is no longer usable", true}; rank = 80; break;
		case isc_io_error:
			candidate = {"58030", "Database I/O error", true}; rank = 80; break;
		case isc_virmemexh:
			candidate = {"53200", "Database memory allocation failed", true}; rank = 80; break;
		case isc_cancelled:
		case isc_cfg_stmt_timeout:
		case isc_att_stmt_timeout:
		case isc_req_stmt_timeout:
			candidate = {"57014", "SQL operation cancelled or timed out", false}; rank = 70; break;
		case isc_no_priv:
		case isc_dyn_no_priv:
			candidate = {"42501", "Permission denied", false}; rank = 60; break;
		case isc_dsql_relation_err:
			candidate = {"42P01", "Table does not exist", false}; rank = 50; break;
		case isc_no_dup:
		case isc_unique_key_violation:
			candidate = {"23505", "Unique constraint violation", false}; rank = 45; break;
		case isc_foreign_key:
		case isc_foreign_key_target_doesnt_exist:
		case isc_foreign_key_references_present:
			candidate = {"23503", "Foreign key constraint violation", false}; rank = 45; break;
		case isc_check_constraint:
			candidate = {"23514", "Check constraint violation", false}; rank = 45; break;
		case isc_not_valid:
			// The engine shares this code between NOT NULL and domain validation.
			// Keep its honest class instead of inspecting a localized value string.
			candidate = {"23000", "Column or domain constraint violation", false}; rank = 20; break;
		case isc_dsql_token_unk_err:
		case isc_command_end_err:
		case isc_command_end_err2:
			candidate = {"42601", "SQL syntax error", false}; rank = 40; break;
		default:
			{
				const ISC_STATUS single[ISC_STATUS_LENGTH] = {isc_arg_gds, code, isc_arg_end};
				char state[6];
				fb_sqlstate(state, single);
				candidate.state = state;
				if (candidate.state == "00000" || candidate.state == "HY000")
					continue;
				rank = candidate.state == "42000" || candidate.state == "22000" ||
					candidate.state == "23000" ? 10 : 30;
				if (candidate.state == "42S22")
					candidate = {"42703", "Column does not exist", false};
				else if (candidate.state == "22012")
					candidate.message = "Division by zero";
				else if (candidate.state == "22003")
					candidate.message = "Numeric value out of range";
				if (candidate.state.compare(0, 2, "08") == 0)
					candidate.fatal = true, rank = 80;
			}
			break;
		}
		if (rank > priority)
			result = std::move(candidate), priority = rank;
	}
	return result;
}

} // namespace Firebird::PgWire
