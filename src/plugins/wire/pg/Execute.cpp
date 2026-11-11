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
#include "../common/classes/ClumpletReader.h"
#include <algorithm>
#include "../common/utils_proto.h"
#include "../isql/FrontendLexer.h"

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

} // namespace

bool QueryExecutor::readStrings()
{
	FbLocalStatus status;
	const unsigned char items[] = {fb_info_standard_conforming_strings};
	unsigned char buffer[16] = {};
	attachment->getInfo(&status, sizeof(items), items, sizeof(buffer), buffer);
	check(&status);
	ClumpletReader info(ClumpletReader::InfoResponse, buffer, sizeof(buffer));
	if (info.isEof() || info.getClumpTag() != fb_info_standard_conforming_strings ||
		info.getClumpLength() != 4)
	{
		throw ProtocolError("XX000", "Engine does not report standard_conforming_strings");
	}
	const auto value = info.getInt();
	info.moveNext();
	if ((value != 0 && value != 1) || info.isEof() || info.getClumpTag() != isc_info_end)
		throw ProtocolError("XX000", "Invalid standard_conforming_strings info response");
	return value != 0;
}

unsigned QueryExecutor::stringPrepareFlags(bool value)
{
	return value ? IStatement::PREPARE_STANDARD_CONFORMING_STRINGS_ON :
		IStatement::PREPARE_STANDARD_CONFORMING_STRINGS_OFF;
}

void QueryExecutor::beginStrings()
{
	if (!stringsAtStart)
		stringsAtStart = readStrings();
}

void QueryExecutor::clearStringHistory()
{
	stringsAtStart.reset();
	stringSavepoints.clear();
}

void QueryExecutor::restoreStrings(bool value)
{
	if (readStrings() == value)
		return;
	// Fixed maintenance SQL: no user text, dispatcher, application transaction,
	// journal entry or protocol result. A failure makes the connection unusable.
	const char* sql = value ? "SET standard_conforming_strings = on" :
		"SET standard_conforming_strings = off";
	Request request;
	FbLocalStatus status;
	request.statement = attachment->prepare(&status, nullptr, 0, sql, SQL_DIALECT_PG,
		stringPrepareFlags(true));
	check(&status);
	const auto unexpected = request.statement->execute(&status, nullptr, nullptr, nullptr, nullptr, nullptr);
	check(&status);
	if (unexpected)
	{
		unexpected->rollback(&status);
		if (status->getState() & IStatus::STATE_ERRORS)
			unexpected->release();
		throw ProtocolError("XX000", "Session restoration unexpectedly started a transaction");
	}
	request.close();
	if (readStrings() != value)
		throw ProtocolError("XX000", "Could not restore standard_conforming_strings");
}

void QueryExecutor::sendStrings(Protocol& protocol, bool value)
{
	if (!stringsSent || *stringsSent != value)
	{
		protocol.parameterStatus("standard_conforming_strings", value ? "on" : "off");
		stringsSent = value;
	}
}

void QueryExecutor::reportStrings(Protocol& protocol)
{
	sendStrings(protocol, readStrings());
}

// Shared Simple/Extended abort path. Native data undo remains owned by the engine;
// the single setting follows the logical block, even before a native handle exists.
bool QueryExecutor::fail(Protocol& protocol, const Diagnostic& diagnostic, bool fatal)
{
	if (state != 'I' && !fatal && !transactionIsValid())
		fatal = true;
	if (!fatal)
	{
		try
		{
			if (state != 'E' && stringsAtStart)
			{
				restoreStrings(stringSavepoints.empty() ? *stringsAtStart : stringSavepoints.back().value);
			}
			readStrings();
		}
		catch (...)
		{
			fatal = true;
		}
	}
	if (state == 'I' || fatal)
	{
		if (!rollback())
			fatal = true;
		state = 'I';
		clearStringHistory();
	}
	else
		state = 'E';
	protocol.error(diagnostic.state, diagnostic.message, fatal);
	return !fatal;
}

bool QueryExecutor::recovery(ControlKind control)
{
	return control == ControlKind::Commit || control == ControlKind::Rollback ||
		control == ControlKind::RollbackRetaining || control == ControlKind::RollbackTo;
}

// Transaction descriptors must agree with the existing coarse native type.
// Session commands use legacy DDL type and remain an explicit lexer whitelist.
void QueryExecutor::checkStatement(IStatement* statement, const SqlInfo& info)
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
	case ControlKind::PgStart:
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

void QueryExecutor::startImplicit()
{
	if (transaction)
		return;
	FbLocalStatus status;
	const unsigned char tpb[] = {isc_tpb_version3, isc_tpb_write,
		isc_tpb_read_committed, isc_tpb_read_consistency, isc_tpb_wait};
	transaction = attachment->startTransaction(&status, sizeof(tpb), tpb);
	check(&status);
}

void QueryExecutor::finishImplicit(bool& ending)
{
	if (state != 'I')
		return;
	if (!clearPortals())
		throw ProtocolError("XX000", "Could not close query resources");
	if (transaction)
	{
		ending = true;
		FbLocalStatus status;
		transaction->commit(&status);
		check(&status);
		transaction = nullptr;
	}
	clearStringHistory();
}

std::string QueryExecutor::control(IStatement* statement, const SqlInfo& info, bool& ending)
{
	const auto kind = info.control;
	if (kind == ControlKind::PgStart && transaction)
	{
		// Promote an implicit segment or retain an explicit block, including its
		// cursors, savepoints and setting history. Prepare has validated the SQL.
		state = 'T';
		return info.tag;
	}
	if (kind == ControlKind::Start && (state != 'I' || transaction))
		throw ProtocolError("25001", "A transaction is already active");
	if (state == 'I' && kind != ControlKind::Start && kind != ControlKind::PgStart && kind != ControlKind::Session &&
		kind != ControlKind::ResetSession && kind != ControlKind::Commit && kind != ControlKind::Rollback)
	{
		throw ProtocolError("25000", "No active transaction");
	}
	// Cursors cannot outlive a transaction boundary, including a reset which
	// can replace the underlying transaction without changing its interface.
	if (kind == ControlKind::Commit || kind == ControlKind::Rollback ||
		kind == ControlKind::CommitRetaining || kind == ControlKind::RollbackRetaining ||
		kind == ControlKind::RollbackTo || kind == ControlKind::ResetSession)
	{
		if (!clearPortals(statement))
			throw ProtocolError("XX000", "Could not close query resources");
	}
	if (kind == ControlKind::ResetSession && !clearStatements())
		throw ProtocolError("XX000", "Could not close prepared statements");
	if (!transaction && (kind == ControlKind::Commit || kind == ControlKind::Rollback))
	{
		ending = true;
		if (kind == ControlKind::Rollback && stringsAtStart)
			restoreStrings(*stringsAtStart);
		clearStringHistory();
		ending = false;
		return info.tag;
	}
	ending = kind == ControlKind::Commit || kind == ControlKind::CommitRetaining ||
		kind == ControlKind::Rollback;
	FbLocalStatus status;
	if (state == 'E' && kind == ControlKind::Commit)
	{
		transaction->rollback(&status);
		check(&status);
		transaction = nullptr;
		state = 'I';
		if (stringsAtStart)
			restoreStrings(*stringsAtStart);
		clearStringHistory();
		return "ROLLBACK";
	}
	// Allocate the prospective entry before executing the native control. History
	// mutation afterwards must not fail halfway through a successful SAVEPOINT.
	StringSavepoint pending;
	if (kind == ControlKind::Savepoint)
	{
		if (stringSavepoints.size() >= 1024 &&
			std::none_of(stringSavepoints.begin(), stringSavepoints.end(),
				[&](const auto& item) { return item.name == info.savepoint; }))
			throw ProtocolError("54000", "Too many tracked savepoints");
		pending = {info.savepoint, readStrings()};
		stringSavepoints.reserve(stringSavepoints.size() + 1);
	}
	const auto previous = transaction;
	transaction = statement->execute(&status, transaction, nullptr, nullptr, nullptr, nullptr);
	check(&status);
	if (kind == ControlKind::Commit || kind == ControlKind::Rollback)
	{
		if (transaction)
			throw ProtocolError("XX000", "Transaction was not completed");
		state = 'I';
	}
	else if (kind == ControlKind::Start || kind == ControlKind::PgStart)
	{
		if (!transaction)
			throw ProtocolError("XX000", "Transaction was not started");
		state = 'T';
	}
	else
	{
		if (bool(transaction) != bool(previous) ||
			(kind != ControlKind::ResetSession && transaction != previous))
		{
			throw ProtocolError("XX000", "Statement unexpectedly changed the transaction");
		}
		if (state != 'I')
			state = transaction ? 'T' : 'I';
	}
	// A native control has succeeded. Failure to reconcile the setting/history is
	// fatal, including a failed maintenance SET after a successful data rollback.
	const bool wasEnding = ending;
	ending = true;
	switch (kind)
	{
	case ControlKind::Session:
		readStrings(); // validate actual post-Execute state, without interpreting SET text
		break;
	case ControlKind::Savepoint:
	{
		const auto old = std::find_if(stringSavepoints.begin(), stringSavepoints.end(),
			[&](const auto& item) { return item.name == info.savepoint; });
		if (old != stringSavepoints.end())
			stringSavepoints.erase(old); // native repeated name replaces only that point
		stringSavepoints.push_back(std::move(pending));
		break;
	}
	case ControlKind::RollbackTo:
	case ControlKind::ReleaseSavepoint:
	{
		const auto found = std::find_if(stringSavepoints.begin(), stringSavepoints.end(),
			[&](const auto& item) { return item.name == info.savepoint; });
		if (found == stringSavepoints.end())
			throw ProtocolError("XX000", "Native savepoint is missing from session history");
		if (kind == ControlKind::RollbackTo)
		{
			restoreStrings(found->value);
			stringSavepoints.erase(found + 1, stringSavepoints.end());
		}
		else if (info.only)
			stringSavepoints.erase(found);
		else
			stringSavepoints.erase(found, stringSavepoints.end());
		break;
	}
	case ControlKind::Rollback:
	case ControlKind::RollbackRetaining:
		if (stringsAtStart)
			restoreStrings(*stringsAtStart);
		[[fallthrough]];
	case ControlKind::Commit:
	case ControlKind::CommitRetaining:
	case ControlKind::ResetSession:
		clearStringHistory();
		if (state == 'T')
			beginStrings(); // retaining and reset begin a new logical block
		break;
	default:
		break;
	}
	ending = wasEnding;
	return info.tag;
}

bool QueryExecutor::execute(Protocol& protocol, std::string_view sql)
{
	Request request;
	bool preparing = true;
	// A failing commit (including retaining) may have already changed durable
	// state/savepoints. Do not continue with an unproven transaction boundary.
	bool ending = false;
	bool failedAtEntry = state == 'E';
	auto fail = [&](const Diagnostic& diagnostic)
	{
		bool fatal = diagnostic.fatal || ending;
		if (!request.cleanup())
			fatal = true;
		if (!clearPortals())
			fatal = true;
		return this->fail(protocol, diagnostic, fatal);
	};
	try
	{
		clearUnnamed();
		const bool standardStrings = readStrings();
		FrontendLexer lexer(sql, !standardStrings);
		std::size_t statementStart = 0;
		bool completed = false;
		// Only the last completion waits for the implicit commit. Rows are streamed.
		std::string tag;
		for (;;)
		{
			beginStrings();
			preparing = true;
			ending = false;
			failedAtEntry = state == 'E';
			const auto statementEnd = lexer.getStatementEnd();
			const auto text = sql.substr(statementStart,
				statementEnd.value_or(sql.size()) - statementStart);
			const auto info = inspectSql(text, standardStrings);
			if (completed && !statementEnd && info.empty)
				break; // No extra EmptyQueryResponse for trailing whitespace/comments.

			if (!tag.empty())
			{
				protocol.commandComplete(tag);
				tag.clear();
			}

			FbLocalStatus status;
			// Probing a PSQL prefix must not start an application transaction.
			// In T/E or an implicit script segment, prepare sees the current handle.
			request.statement = attachment->prepare(&status, transaction, text.size(), text.data(),
				SQL_DIALECT_PG, IStatement::PREPARE_PREFETCH_METADATA | stringPrepareFlags(standardStrings) |
					(statementEnd ? IStatement::PREPARE_REQUIRE_SEMICOLON : 0));
			if (status->getState() & IStatus::STATE_ERRORS)
			{
				const auto diagnostic = diagnose(&status);
				if (statementEnd && diagnostic.state == "42601" && !diagnostic.fatal &&
					fb_utils::containsErrorCode(status->getErrors(), isc_command_end_err2))
				{
					// AUTOTERM: the delimiter may be inside a PSQL body. Keep the
					// original prefix and try its next boundary, without executing it.
					request.close();
					continue;
				}
				if (!completed && !statementEnd && diagnostic.state == "42601" && info.empty)
				{
					request.close();
					finishImplicit(ending);
					protocol.emptyQuery();
					return true; // EMPTY preserves I, T or E.
				}
			}
			check(&status);
			preparing = false;
			if (state == 'E' && !recovery(info.control))
				throw ProtocolError("25P02", "Current transaction is aborted; rollback is required");
			checkStatement(request.statement, info);

			if (info.control != ControlKind::None)
				tag = control(request.statement, info, ending);
			else
			{
				const bool hasCursor = request.statement->getFlags(&status) & IStatement::FLAG_HAS_CURSOR;
				check(&status);
				RefPtr<IMessageMetadata> input(REF_NO_INCR, request.statement->getInputMetadata(&status));
				check(&status);
				const auto inputCount = input->getCount(&status);
				check(&status);
				if (inputCount)
					throw ProtocolError("0A000", "Query parameters require the Extended Query protocol");
				RefPtr<IMessageMetadata> logicalOutput(REF_NO_INCR, request.statement->getOutputMetadata(&status));
				check(&status);
				const ResultCodec output(logicalOutput);
				const bool hasOutput = !output.columns().empty();
				if (hasCursor && !hasOutput)
					throw ProtocolError("XX000", "Inconsistent engine output metadata");
				std::vector<unsigned char> buffer(output.messageLength());
				startImplicit();
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
						protocol.dataRow(output.decode(buffer.data(), buffer.size(),
							protocol.frameLimit(), attachment, transaction));
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
						protocol.dataRow(output.decode(buffer.data(), buffer.size(),
							protocol.frameLimit(), attachment, transaction));
						rows = 1;
					}
				}
				const auto count = info.count == CountKind::Affected ?
					request.statement->getAffectedRecords(&status) : rows;
				check(&status);
				tag = info.count == CountKind::None ? info.tag :
					info.tag + " " + std::to_string(count);
			}
			request.close();
			completed = true;
			statementStart = statementEnd.value_or(sql.size());
			if (!statementEnd)
				break;
		}
		finishImplicit(ending);
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
