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

// Query resources never own the session transaction. In a failed explicit
// block they must be closed without discarding the transaction or savepoints.
struct Request
{
	IStatement* statement = nullptr;
	IResultSet* cursor = nullptr;

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
		return ok;
	}

	void close()
	{
		FbLocalStatus status;
		if (cursor)
		{
			cursor->close(&status);
			check(&status);
			cursor = nullptr;
		}
		if (statement)
		{
			statement->free(&status);
			check(&status);
			statement = nullptr;
		}
	}
};

bool recovery(ControlKind control)
{
	return control == ControlKind::Commit || control == ControlKind::Rollback ||
		control == ControlKind::RollbackRetaining || control == ControlKind::RollbackTo;
}

// Transaction descriptors must agree with the existing coarse native type.
// Session commands use legacy DDL type and remain an explicit lexer whitelist.
void checkStatement(IStatement* statement, const SqlInfo& info)
{
	if (!info.supported)
		throw ProtocolError("0A000", "Statement command is not supported yet");
	FbLocalStatus status;
	const auto type = statement->getType(&status);
	check(&status);
	unsigned expected = 0;
	switch (info.control)
	{
	case ControlKind::Start:
		expected = isc_info_sql_stmt_start_trans; break;
	case ControlKind::Commit:
	case ControlKind::CommitRetaining:
		expected = isc_info_sql_stmt_commit; break;
	case ControlKind::Rollback:
	case ControlKind::RollbackRetaining:
		expected = isc_info_sql_stmt_rollback; break;
	case ControlKind::Savepoint:
	case ControlKind::ReleaseSavepoint:
	case ControlKind::RollbackTo:
		expected = isc_info_sql_stmt_savepoint; break;
	case ControlKind::Session:
	case ControlKind::ResetSession:
		expected = isc_info_sql_stmt_ddl; break;
	case ControlKind::None:
		break;
	}
	if ((expected && type != expected) || (!expected &&
		(type == isc_info_sql_stmt_start_trans || type == isc_info_sql_stmt_commit ||
		 type == isc_info_sql_stmt_rollback || type == isc_info_sql_stmt_savepoint)))
	{
		throw ProtocolError("0A000", "Statement control metadata is not supported");
	}
}

} // namespace

QueryExecutor::~QueryExecutor()
{
	rollback();
}

bool QueryExecutor::rollback() noexcept
{
	if (!transaction)
		return true;
	FbLocalStatus status;
	transaction->rollback(&status);
	const bool ok = !(status->getState() & IStatus::STATE_ERRORS);
	if (!ok)
		transaction->release();
	transaction = nullptr;
	return ok;
}

bool QueryExecutor::transactionIsValid() noexcept
{
	if (!transaction)
		return false;
	FbLocalStatus status;
	const unsigned char items[] = {isc_info_tra_id};
	unsigned char buffer[32];
	transaction->getInfo(&status, sizeof(items), items, sizeof(buffer), buffer);
	return !(status->getState() & IStatus::STATE_ERRORS);
}

bool QueryExecutor::execute(Protocol& protocol, std::string_view sql)
{
	Request request;
	bool preparing = true;
	// A failing commit (including retaining) may have already changed durable
	// state/savepoints. Do not continue with an unproven transaction boundary.
	bool ending = false;
	const bool failedAtEntry = state == 'E';
	auto fail = [&](const Diagnostic& diagnostic)
	{
		bool fatal = diagnostic.fatal || ending;
		if (!request.cleanup())
			fatal = true;
		if (state != 'I' && !fatal)
		{
			if (transactionIsValid())
				state = 'E';
			else
				fatal = true;
		}
		if (state == 'I' || fatal)
		{
			if (!rollback())
				fatal = true;
			state = 'I';
		}
		protocol.error(diagnostic.state, diagnostic.message, fatal);
		return !fatal;
	};
	try
	{
		FbLocalStatus status;
		// In I, prepare uses NULL (the engine's internal system transaction),
		// avoiding a hidden application transaction before SET TRANSACTION/reset.
		// In T/E, prepare sees the existing transaction and its uncommitted DDL.
		request.statement = attachment->prepare(&status, transaction, sql.size(), sql.data(),
			SQL_DIALECT_V6, IStatement::PREPARE_PREFETCH_METADATA);
		if ((status->getState() & IStatus::STATE_ERRORS) &&
			diagnose(&status).state == "42601" && inspectSql(sql).empty)
		{
			request.close();
			protocol.emptyQuery();
			return true; // EMPTY preserves I, T or E.
		}
		check(&status);
		preparing = false;
		const auto info = inspectSql(sql);
		if (state == 'E' && !recovery(info.control))
			throw ProtocolError("25P02", "Current transaction is aborted; rollback is required");
		checkStatement(request.statement, info);

		if (info.control != ControlKind::None)
		{
			const auto control = info.control;
			std::string tag = info.tag;
			if (control == ControlKind::Start && state != 'I')
				throw ProtocolError("25001", "A transaction is already active");
			if (state == 'I' && (control == ControlKind::Commit || control == ControlKind::Rollback))
			{
				// Successfully prepared full control with no active transaction.
				// Native execute requires a handle; the wire operation is a no-op.
			}
			else if (state == 'I' && control != ControlKind::Start &&
				control != ControlKind::Session && control != ControlKind::ResetSession)
			{
				throw ProtocolError("25000", "No active transaction");
			}
			else if (state == 'E' && control == ControlKind::Commit)
			{
				// Prepare the original COMMIT, but a failed block can only roll back.
				// This invokes the native API; no replacement SQL is constructed.
				ending = true;
				request.close();
				transaction->rollback(&status);
				check(&status);
				transaction = nullptr;
				state = 'I';
				tag = "ROLLBACK";
			}
			else
			{
				const auto previous = transaction;
				ending = control == ControlKind::Commit || control == ControlKind::CommitRetaining ||
					control == ControlKind::Rollback;
				// Execute may consume/replace the old interface even on error.
				transaction = request.statement->execute(&status, transaction,
					nullptr, nullptr, nullptr, nullptr);
				check(&status);
				if (control == ControlKind::Commit || control == ControlKind::Rollback)
				{
					if (transaction)
						throw ProtocolError("XX000", "Transaction was not completed");
					state = 'I';
				}
				else if (control == ControlKind::Start)
				{
					if (!transaction)
						throw ProtocolError("XX000", "Transaction was not started");
					state = 'T';
				}
				else
				{
					if (bool(transaction) != bool(previous) ||
						(control != ControlKind::ResetSession && transaction != previous))
					{
						throw ProtocolError("XX000", "Statement unexpectedly changed the transaction");
					}
					// RESET can replace the native transaction behind the SAME interface.
					// No cursors, statements or savepoint names are cached across Queries.
					// Current reported ParameterStatus values do not describe reset options.
					state = transaction ? 'T' : 'I';
				}
			}
			request.close();
			protocol.commandComplete(tag);
			return true;
		}

		const bool hasCursor = request.statement->getFlags(&status) & IStatement::FLAG_HAS_CURSOR;
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
		if (!transaction)
		{
			// The engine controls statement and lock waits. Explicit SET TRANSACTION
			// characteristics (including AUTO COMMIT) are passed through unchanged.
			const unsigned char tpb[] = {isc_tpb_version3, isc_tpb_write,
				isc_tpb_read_committed, isc_tpb_read_consistency, isc_tpb_wait};
			transaction = attachment->startTransaction(&status, sizeof(tpb), tpb);
			check(&status);
		}
		if (hasOutput)
			protocol.rowDescription(output.columns());
		std::uint64_t rows = 0;
		if (hasCursor)
		{
			request.cursor = request.statement->openCursor(&status, transaction,
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
			const auto previous = transaction;
			transaction = request.statement->execute(&status, transaction,
				nullptr, nullptr, hasOutput ? output.fetchMetadata() : nullptr,
				hasOutput ? buffer.data() : nullptr);
			check(&status);
			if (!transaction || transaction != previous)
				throw ProtocolError("XX000", "Statement unexpectedly changed the transaction");
			if (hasOutput)
			{
				protocol.dataRow(output.decode(buffer.data(), buffer.size()));
				rows = 1;
			}
		}
		const auto count = info.count == CountKind::Affected ?
			request.statement->getAffectedRecords(&status) : rows;
		check(&status);
		const auto tag = info.count == CountKind::None ? info.tag :
			info.tag + " " + std::to_string(count);
		request.close();
		if (state == 'I')
		{
			ending = true;
			transaction->commit(&status);
			check(&status);
			transaction = nullptr;
		}
		protocol.commandComplete(tag);
		return true;
	}
	catch (const status_exception& ex)
	{
		FbLocalStatus status;
		ex.stuffException(&status);
		return fail(diagnose(&status, preparing && failedAtEntry));
	}
	catch (const ProtocolError& ex)
	{
		if (std::strncmp(ex.sqlState(), "08", 2) == 0)
			throw;
		return fail({ex.sqlState(), ex.what(), std::strcmp(ex.sqlState(), "XX000") == 0});
	}
}

} // namespace Firebird::PgWire
