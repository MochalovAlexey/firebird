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

#ifndef PLUGINS_WIRE_PG_SQL_INFO_H
#define PLUGINS_WIRE_PG_SQL_INFO_H

#include <string>
#include <string_view>

namespace Firebird::PgWire
{

enum class CountKind
{
	None,
	Rows,
	Affected
};

enum class ControlKind
{
	None,
	Start,
	Commit,
	Rollback,
	CommitRetaining,
	RollbackRetaining,
	Savepoint,
	ReleaseSavepoint,
	RollbackTo,
	Session,
	ResetSession
};

struct SqlInfo
{
	std::string tag;
	CountKind count = CountKind::None;
	bool empty = false;
	bool supported = false;
	ControlKind control = ControlKind::None;
};

// Classify the leading command for wire completion and capability checks. This
// does not validate SQL: use supported/tag/control only after successful engine
// prepare. ControlKind describes the command, not the current transaction state.
// The SQL is never modified, split or executed here. empty is true only for an
// entirely tokenless input, including whitespace and fully closed comments.
SqlInfo inspectSql(std::string_view sql);

} // namespace Firebird::PgWire

#endif // PLUGINS_WIRE_PG_SQL_INFO_H
