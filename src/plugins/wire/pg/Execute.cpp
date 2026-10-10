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

#include "firebird.h"
#include "Execute.h"
#include "ErrorStatus.h"
#include "ResultCodec.h"
#include "SqlInfo.h"
#include "../common/classes/ImplementHelper.h"
#include "../common/status.h"

#include <cstring>
#include <limits>

namespace Firebird::PgWire
{
namespace
{

// close/free/commit consume an interface only on success. Failed calls retain
// it for rollback/release. Cleanup does not overwrite the original diagnostic.
struct Request
{
	ITransaction* transaction = nullptr;
	IStatement* statement = nullptr;
	IResultSet* cursor = nullptr;
	bool committing = false;

	~Request() { cleanup(); }

	bool cleanup() noexcept
	{
		FbLocalStatus status;
		bool ok = true;
		if (cursor)
		{
			cursor->close(&status);
			if (status->getState() & IStatus::STATE_ERRORS)
				cursor->release(), ok = false;
			cursor = nullptr;
		}
		if (statement)
		{
			statement->free(&status);
			if (status->getState() & IStatus::STATE_ERRORS)
				statement->release(), ok = false;
			statement = nullptr;
		}
		if (transaction)
		{
			transaction->rollback(&status);
			if (status->getState() & IStatus::STATE_ERRORS)
				transaction->release(), ok = false;
			transaction = nullptr;
		}
		return ok;
	}

	void finish()
	{
		FbLocalStatus status;
		if (cursor)
		{
			cursor->close(&status);
			check(&status);
			cursor = nullptr;
		}
		statement->free(&status);
		check(&status);
		statement = nullptr;
		committing = true;
		transaction->commit(&status);
		check(&status);
		transaction = nullptr;
		committing = false;
	}
};

// Existing statement types remain authoritative for transaction operations.
// Session commands that share legacy DDL type are excluded by the plugin's
// leading-command classification, after successful engine prepare.
void checkStatement(IStatement* statement, const SqlInfo& info)
{
	FbLocalStatus status;
	const auto type = statement->getType(&status);
	check(&status);
	switch (type)
	{
	case isc_info_sql_stmt_start_trans:
	case isc_info_sql_stmt_commit:
	case isc_info_sql_stmt_rollback:
	case isc_info_sql_stmt_savepoint:
		throw ProtocolError("0A000", "Transaction commands are not supported yet");
	}
	if (!info.supported)
		throw ProtocolError("0A000", "Statement command is not supported yet");
}

} // namespace

bool executeQuery(Protocol& protocol, IAttachment* attachment, std::string_view sql,
	unsigned timeoutMs)
{
	Request request;
	try
	{
		FbLocalStatus status;
		// One real application transaction, shared by prepare, open and fetch.
		// Native ReadConsistency configuration still applies to READ COMMITTED.
		const unsigned char tpb[] = {isc_tpb_version3, isc_tpb_write,
			isc_tpb_read_committed, isc_tpb_rec_version, isc_tpb_nowait};
		request.transaction = attachment->startTransaction(&status, sizeof(tpb), tpb);
		check(&status);
		// The explicit length excludes only the wire terminator. The engine
		// receives the original SQL, including comments and the full statement.
		// Limited plugin classification never rewrites or splits that SQL.
		request.statement = attachment->prepare(&status, request.transaction, sql.size(), sql.data(),
			SQL_DIALECT_V6, IStatement::PREPARE_PREFETCH_METADATA);
		if ((status->getState() & IStatus::STATE_ERRORS) &&
			diagnose(&status).state == "42601" && inspectSql(sql).empty)
		{
			if (!request.cleanup())
				throw ProtocolError("XX000", "Empty query cleanup failed");
			protocol.emptyQuery();
			return true;
		}
		check(&status);
		// The lexer provides completion metadata only after the engine has
		// accepted the statement. Invalid or incomplete SQL keeps its real error.
		const auto info = inspectSql(sql);
		checkStatement(request.statement, info);
		const bool hasCursor = request.statement->getFlags(&status) & IStatement::FLAG_HAS_CURSOR;
		check(&status);
		request.statement->setTimeout(&status, timeoutMs);
		check(&status);
		RefPtr<IMessageMetadata> input(REF_NO_INCR, request.statement->getInputMetadata(&status));
		check(&status);
		const auto inputCount = input->getCount(&status);
		check(&status);
		if (inputCount)
			throw ProtocolError("0A000", "Query parameters require Extended Query support");
		RefPtr<IMessageMetadata> logicalOutput(REF_NO_INCR, request.statement->getOutputMetadata(&status));
		check(&status);
		const ResultCodec output(logicalOutput);
		const bool hasOutput = !output.columns().empty();
		if (hasCursor && !hasOutput)
			throw ProtocolError("XX000", "Inconsistent engine output metadata");
		std::vector<unsigned char> buffer(output.messageLength());
		// Validate/emit the complete description before any execution side effects.
		if (hasOutput)
			protocol.rowDescription(output.columns());
		std::uint64_t rows = 0;
		if (hasCursor)
		{
			request.cursor = request.statement->openCursor(&status, request.transaction,
				nullptr, nullptr, output.fetchMetadata(), 0);
			check(&status);
			for (;;)
			{
				const auto result = request.cursor->fetchNext(&status, buffer.data());
				check(&status);
				if (result == IStatus::RESULT_NO_DATA)
					break;
				if (result != IStatus::RESULT_OK || rows == std::numeric_limits<std::uint64_t>::max())
					throw ProtocolError("XX000", "Invalid engine fetch result");
				protocol.dataRow(output.decode(buffer.data(), buffer.size()));
				++rows;
			}
		}
		else
		{
			const auto previous = request.transaction;
			// execute can consume/change the old handle even on an error. Store the
			// returned handle before checking status; never release the old one again.
			request.transaction = request.statement->execute(&status, request.transaction,
				nullptr, nullptr, hasOutput ? output.fetchMetadata() : nullptr,
				hasOutput ? buffer.data() : nullptr);
			check(&status);
			if (!request.transaction || request.transaction != previous)
				throw ProtocolError("XX000", "Statement unexpectedly changed the transaction");
			if (hasOutput)
			{
				protocol.dataRow(output.decode(buffer.data(), buffer.size()));
				rows = 1;
			}
		}
		// RETURNING rows and affected records are independent. For MERGE the
		// engine sums inserts, updates and deletes, irrespective of its cursor type.
		const auto count = info.count == CountKind::Affected ?
			request.statement->getAffectedRecords(&status) : rows;
		check(&status);
		const auto tag = info.count == CountKind::None ? info.tag :
			info.tag + " " + std::to_string(count);
		request.finish();
		// A fetch/close/free/commit error cannot be followed by CommandComplete.
		protocol.commandComplete(tag);
		return true;
	}
	catch (const status_exception& ex)
	{
		FbLocalStatus status;
		ex.stuffException(&status);
		const auto diagnostic = diagnose(&status);
		const bool clean = request.cleanup();
		const bool fatal = diagnostic.fatal || !clean || request.committing;
		protocol.error(diagnostic.state, diagnostic.message, fatal);
		return !fatal;
	}
	catch (const ProtocolError& ex)
	{
		// A frame might have been only partially written. Never attempt to
		// recover the stream after any transport/framing failure.
		if (std::strncmp(ex.sqlState(), "08", 2) == 0)
			throw;
		const bool clean = request.cleanup();
		const bool fatal = !clean || std::strcmp(ex.sqlState(), "XX000") == 0;
		protocol.error(ex.sqlState(), ex.what(), fatal);
		return !fatal;
	}
}

} // namespace Firebird::PgWire
