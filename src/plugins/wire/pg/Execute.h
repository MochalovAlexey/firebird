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

namespace Firebird::PgWire
{

// One executor per authenticated session, destroyed before the borrowed
// attachment is detached. Only this object owns an application transaction.
class QueryExecutor final
{
public:
	QueryExecutor(IAttachment* attachment, unsigned timeoutMs)
		: attachment(attachment), timeoutMs(timeoutMs) {}
	~QueryExecutor();
	QueryExecutor(const QueryExecutor&) = delete;
	QueryExecutor& operator=(const QueryExecutor&) = delete;

	// SQL goes unchanged to native prepare. Sends result/error, not ReadyForQuery.
	// false means close the session; transport failures propagate to its owner.
	bool execute(Protocol& protocol, std::string_view sql);
	char transactionState() const noexcept { return state; }

private:
	bool rollback() noexcept;
	bool transactionIsValid() noexcept;

	IAttachment* const attachment;
	const unsigned timeoutMs;
	ITransaction* transaction = nullptr;
	char state = 'I';
};

} // namespace Firebird::PgWire

#endif // PLUGINS_WIRE_PG_EXECUTE_H
