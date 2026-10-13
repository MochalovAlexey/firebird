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

#ifndef PLUGINS_WIRE_PG_EXECUTE_H
#define PLUGINS_WIRE_PG_EXECUTE_H

#include "Protocol.h"
#include "SqlInfo.h"

namespace Firebird::PgWire
{

// One executor per authenticated session, destroyed before the borrowed
// attachment is detached. Only this object owns an application transaction.
class QueryExecutor final
{
public:
	explicit QueryExecutor(IAttachment* attachment);
	~QueryExecutor();
	QueryExecutor(const QueryExecutor&) = delete;
	QueryExecutor& operator=(const QueryExecutor&) = delete;

	// SQL goes unchanged to native prepare. Sends result/error, not ReadyForQuery.
	// false means close the session; transport failures propagate to its owner.
	bool execute(Protocol& protocol, std::string_view sql);
	// Dispatches both query protocols, including recovery through Sync and Ready.
	bool process(Protocol& protocol, const Message& message);
	bool discarding() const noexcept;
	char transactionState() const noexcept { return state; }

private:
	struct Objects;
	std::unique_ptr<Objects> objects;
	static bool recovery(ControlKind control);
	static void checkStatement(IStatement* statement, const SqlInfo& info);
	std::string control(IStatement* statement, const SqlInfo& info, bool& ending);
	void startImplicit();
	void finishImplicit(bool& ending);
	bool clearPortals(IStatement* executing = nullptr) noexcept;
	bool clearStatements() noexcept;
	void clearUnnamed();
	bool rollback() noexcept;
	bool transactionIsValid() noexcept;

	IAttachment* const attachment;
	ITransaction* transaction = nullptr;
	char state = 'I';
};

} // namespace Firebird::PgWire

#endif // PLUGINS_WIRE_PG_EXECUTE_H
