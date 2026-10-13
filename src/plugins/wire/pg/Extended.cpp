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
#include "InputCodec.h"
#include "ResultCodec.h"
#include "../common/status.h"

#include <cstring>
#include <limits>
#include <map>
#include <set>

namespace Firebird::PgWire
{
namespace
{

constexpr unsigned MAX_STATEMENTS = 128;
constexpr unsigned MAX_PORTALS = 128;
constexpr std::size_t MAX_RETAINED_BYTES = 8 * 1024 * 1024;

struct NativeRequest
{
	IStatement* statement = nullptr;
	IResultSet* cursor = nullptr;

	~NativeRequest() { close(); }

	bool close() noexcept
	{
		bool ok = closeCursor();
		if (statement)
		{
			FbLocalStatus status;
			statement->free(&status);
			if (status->getState() & IStatus::STATE_ERRORS)
				statement->release(), ok = false;
			statement = nullptr;
		}
		return ok;
	}

	bool closeCursor() noexcept
	{
		if (!cursor)
			return true;
		FbLocalStatus status;
		cursor->close(&status);
		const bool ok = !(status->getState() & IStatus::STATE_ERRORS);
		if (!ok)
			cursor->release();
		cursor = nullptr;
		return ok;
	}
};

struct Prepared
{
	NativeRequest native;
	std::string sql;
	SqlInfo info;
	std::unique_ptr<InputCodec> input;
	std::unique_ptr<ResultCodec> output;

	std::size_t bytes() const
	{
		std::size_t size = sizeof(*this) + sql.capacity() + info.tag.capacity();
		if (input)
			size += input->messageLength() + input->retainedBytes();
		if (output)
			size += output->messageLength() + output->retainedBytes();
		return size;
	}
};

struct Portal
{
	NativeRequest native;
	std::shared_ptr<Prepared> source;
	std::unique_ptr<InputCodec> input;
	std::unique_ptr<ResultCodec> output;
	std::vector<unsigned char> parameters, buffer;
	std::vector<std::int16_t> formats;
	std::uint64_t rows = 0;
	std::string tag;
	bool started = false, done = false;

	std::size_t bytes() const
	{
		std::size_t size = sizeof(*this) + parameters.capacity() + buffer.capacity() +
			formats.capacity() * sizeof(std::int16_t) + tag.capacity();
		if (input)
			size += input->retainedBytes();
		if (output)
			size += output->retainedBytes();
		return size;
	}
};

void sameOutput(const ResultCodec& expected, const ResultCodec& actual)
{
	const auto& first = expected.columns();
	const auto& second = actual.columns();
	if (first.size() != second.size())
		throw ProtocolError("0A000", "Prepared result metadata changed; prepare again");
	for (unsigned i = 0; i < first.size(); ++i)
	{
		if (first[i].oid != second[i].oid || first[i].typeSize != second[i].typeSize ||
			first[i].typeModifier != second[i].typeModifier || first[i].name != second[i].name)
		{
			throw ProtocolError("0A000", "Prepared result metadata changed; prepare again");
		}
	}
}

} // namespace

struct QueryExecutor::Objects
{
	std::map<std::string, std::shared_ptr<Prepared>> statements;
	std::map<std::string, std::shared_ptr<Portal>> portals;
	bool discard = false;

	void removeStatement(const std::string& name)
	{
		const auto found = statements.find(name);
		if (found != statements.end())
		{
			if (!found->second->native.close())
				throw ProtocolError("XX000", "Could not close prepared statement");
			statements.erase(found);
		}
	}

	std::size_t bytes() const
	{
		std::size_t size = 0;
		std::set<const Prepared*> counted;
		for (const auto& entry : statements)
		{
			size += entry.first.size();
			if (counted.insert(entry.second.get()).second)
				size += entry.second->bytes();
		}
		for (const auto& entry : portals)
		{
			size += entry.first.size() + entry.second->bytes();
			if (counted.insert(entry.second->source.get()).second)
				size += entry.second->source->bytes();
		}
		return size;
	}

	void reserve(std::size_t amount) const
	{
		if (amount > MAX_RETAINED_BYTES || bytes() > MAX_RETAINED_BYTES - amount)
			throw ProtocolError("54000", "Prepared objects exceed the session memory limit");
	}
};

QueryExecutor::QueryExecutor(IAttachment* attachment)
	: objects(std::make_unique<Objects>()), attachment(attachment)
{}

QueryExecutor::~QueryExecutor()
{
	clearPortals();
	clearStatements();
	rollback();
}

bool QueryExecutor::discarding() const noexcept
{
	return objects->discard;
}

bool QueryExecutor::clearPortals(IStatement* executing) noexcept
{
	bool ok = true;
	for (const auto& entry : objects->portals)
	{
		auto& native = entry.second->native;
		if (!(executing && native.statement == executing ? native.closeCursor() : native.close()))
			ok = false;
	}
	objects->portals.clear();
	return ok;
}

bool QueryExecutor::clearStatements() noexcept
{
	bool ok = true;
	for (const auto& entry : objects->statements)
		if (!entry.second->native.close())
			ok = false;
	objects->statements.clear();
	return ok;
}

void QueryExecutor::clearUnnamed()
{
	const auto found = objects->portals.find("");
	if (found != objects->portals.end())
	{
		if (!found->second->native.close())
			throw ProtocolError("XX000", "Could not close query resources");
		objects->portals.erase(found);
	}
	objects->removeStatement("");
}

bool QueryExecutor::process(Protocol& protocol, const Message& message)
{
	if (objects->discard && message.type != 'S')
		return true;
	if (message.type == 'Q')
	{
		if (!execute(protocol, message.text()))
			return false;
		protocol.ready(state);
		return true;
	}

	// Decode outside the recoverable-error handler: a malformed body is fatal.
	// Semantic errors in a well-framed Bind/Parse instead recover through Sync.
	std::optional<ParseMessage> parse;
	std::optional<BindMessage> bind;
	std::optional<ObjectMessage> object;
	std::optional<ExecuteMessage> execution;
	switch (message.type)
	{
	case 'P': parse = message.parse(); break;
	case 'B': bind = message.bind(); break;
	case 'D': case 'C': object = message.object(); break;
	case 'E':
		execution = message.execution();
		break;
	case 'H': return true; // Writes are synchronous; Flush has no transaction effect.
	case 'S': break;
	default: throw ProtocolError("0A000", "Frontend message is not supported yet");
	}

	bool preparing = false, ending = false;
	const bool failedAtEntry = state == 'E';
	// Retain in-flight owners until the error handler has checked native cleanup,
	// including objects not yet registered and controls removed by their own reset.
	std::shared_ptr<Prepared> pendingPrepared;
	std::shared_ptr<Portal> pendingPortal;
	auto fail = [&](const Diagnostic& diagnostic)
	{
		bool fatal = diagnostic.fatal || ending;
		if (pendingPortal && !pendingPortal->native.close())
			fatal = true;
		if (pendingPrepared && !pendingPrepared->native.close())
			fatal = true;
		pendingPortal.reset();
		pendingPrepared.reset();
		if (!clearPortals())
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
		objects->discard = message.type != 'S';
		protocol.error(diagnostic.state, diagnostic.message, fatal);
		if (!fatal && message.type == 'S')
			protocol.ready(state);
		return !fatal;
	};

	try
	{
		FbLocalStatus status;
		if (parse)
		{
			if (parse->name.empty())
				objects->removeStatement("");
			else if (objects->statements.count(parse->name))
				throw ProtocolError("42P05", "Prepared statement already exists");
			if (objects->statements.size() == MAX_STATEMENTS)
				throw ProtocolError("54000", "Too many prepared statements");
			objects->reserve(parse->name.size() + parse->sql.size());
			const auto prepared = pendingPrepared = std::make_shared<Prepared>();
			prepared->sql = std::move(parse->sql);
			preparing = true;
			prepared->native.statement = attachment->prepare(&status, transaction,
				prepared->sql.size(), prepared->sql.data(), SQL_DIALECT_V6,
				IStatement::PREPARE_PREFETCH_METADATA);
			prepared->info = inspectSql(prepared->sql);
			if ((status->getState() & IStatus::STATE_ERRORS) &&
				diagnose(&status).state == "42601" && prepared->info.empty)
			{
				if (!parse->parameterTypes.empty())
					throw ProtocolError("08P01", "Empty query has no parameters");
			}
			else
			{
				check(&status);
				preparing = false;
				if (state == 'E' && !recovery(prepared->info.control))
					throw ProtocolError("25P02", "Current transaction is aborted; rollback is required");
				checkStatement(prepared->native.statement, prepared->info);
				RefPtr<IMessageMetadata> input(REF_NO_INCR,
					prepared->native.statement->getInputMetadata(&status));
				check(&status);
				prepared->input = std::make_unique<InputCodec>(input, parse->parameterTypes);
				RefPtr<IMessageMetadata> output(REF_NO_INCR,
					prepared->native.statement->getOutputMetadata(&status));
				check(&status);
				prepared->output = std::make_unique<ResultCodec>(output);
			}
			objects->reserve(parse->name.size() + prepared->bytes());
			objects->statements.emplace(std::move(parse->name), prepared);
			pendingPrepared.reset();
			protocol.parseComplete();
		}
		else if (bind)
		{
			if (bind->portal.empty())
			{
				const auto old = objects->portals.find("");
				if (old != objects->portals.end())
				{
					if (!old->second->native.close())
						throw ProtocolError("XX000", "Could not close query resources");
					objects->portals.erase(old);
				}
			}
			else if (objects->portals.count(bind->portal))
				throw ProtocolError("42P03", "Portal already exists");
			if (objects->portals.size() == MAX_PORTALS)
				throw ProtocolError("54000", "Too many portals");
			const auto found = objects->statements.find(bind->statement);
			if (found == objects->statements.end())
				throw ProtocolError("26000", "Prepared statement does not exist");
			const auto portal = pendingPortal = std::make_shared<Portal>();
			portal->source = found->second;
			const auto& source = *portal->source;
			if (!source.info.empty)
			{
				if (state == 'E' && !recovery(source.info.control))
					throw ProtocolError("25P02", "Current transaction is aborted; rollback is required");
				// Each portal has its own native statement/cursor. Firebird permits
				// only one open cursor on a statement. No per-portal transaction.
				preparing = true;
				portal->native.statement = attachment->prepare(&status, transaction,
					source.sql.size(), source.sql.data(), SQL_DIALECT_V6,
					IStatement::PREPARE_PREFETCH_METADATA);
				check(&status);
				preparing = false;
				checkStatement(portal->native.statement, source.info);
				RefPtr<IMessageMetadata> input(REF_NO_INCR,
					portal->native.statement->getInputMetadata(&status));
				check(&status);
				portal->input = std::make_unique<InputCodec>(input, source.input->parameterTypes());
				if (portal->input->parameterTypes() != source.input->parameterTypes())
					throw ProtocolError("0A000", "Prepared parameter metadata changed; prepare again");
				portal->parameters = portal->input->encode(bind->values, bind->parameterFormats);
				RefPtr<IMessageMetadata> output(REF_NO_INCR,
					portal->native.statement->getOutputMetadata(&status));
				check(&status);
				portal->output = std::make_unique<ResultCodec>(output, bind->resultFormats);
				sameOutput(*source.output, *portal->output);
				portal->buffer.resize(portal->output->messageLength());
			}
			else
			{
				if (!bind->values.empty() || bind->parameterFormats.size() > 1 ||
					bind->resultFormats.size() > 1)
				{
					throw ProtocolError("08P01", "Empty query has no parameters or columns");
				}
				for (const auto format : bind->parameterFormats)
					if (format != 0 && format != 1)
						throw ProtocolError("0A000", "Parameter format is not supported");
				for (const auto format : bind->resultFormats)
					if (format != 0 && format != 1)
						throw ProtocolError("0A000", "Result format is not supported");
			}
			portal->formats = std::move(bind->resultFormats);
			objects->reserve(bind->portal.size() + portal->bytes());
			objects->portals.emplace(std::move(bind->portal), portal);
			pendingPortal.reset();
			protocol.bindComplete();
		}
		else if (object)
		{
			if (message.type == 'C')
			{
				// Closing a missing object is idempotent, as in the wire protocol.
				if (object->kind == 'S')
					objects->removeStatement(object->name);
				else
				{
					const auto found = objects->portals.find(object->name);
					if (found != objects->portals.end())
					{
						if (!found->second->native.close())
							throw ProtocolError("XX000", "Could not close query resources");
						objects->portals.erase(found);
					}
				}
				protocol.closeComplete();
			}
			else if (object->kind == 'S')
			{
				const auto found = objects->statements.find(object->name);
				if (found == objects->statements.end())
					throw ProtocolError("26000", "Prepared statement does not exist");
				const auto& prepared = *found->second;
				protocol.parameterDescription(prepared.input ? prepared.input->parameterTypes() :
					std::vector<std::uint32_t>{});
				if (prepared.output && !prepared.output->columns().empty())
					protocol.rowDescription(prepared.output->columns());
				else
					protocol.noData();
			}
			else
			{
				const auto found = objects->portals.find(object->name);
				if (found == objects->portals.end())
					throw ProtocolError("34000", "Portal does not exist");
				const auto& portal = *found->second;
				if (portal.output && !portal.output->columns().empty())
					protocol.rowDescription(portal.output->columns());
				else
					protocol.noData();
			}
		}
		else if (execution)
		{
			if (execution->maxRows > std::numeric_limits<std::int32_t>::max())
				throw ProtocolError("08P01", "Invalid Execute row limit");
			const auto found = objects->portals.find(execution->portal);
			if (found == objects->portals.end())
				throw ProtocolError("34000", "Portal does not exist");
			// Keep this object alive if executing a transaction boundary/reset
			// removes it from the portal registry.
			const auto owned = pendingPortal = found->second;
			auto& portal = *owned;
			const auto& info = portal.source->info;
			if (info.empty)
			{
				protocol.emptyQuery();
				return true;
			}
			if (state == 'E' && !recovery(info.control))
				throw ProtocolError("25P02", "Current transaction is aborted; rollback is required");
			if (portal.done)
			{
				protocol.commandComplete(portal.tag);
				return true;
			}
			if (info.control != ControlKind::None)
			{
				portal.tag = control(portal.native.statement, info, ending);
				if (!portal.native.close())
					throw ProtocolError("XX000", "Could not close query resources");
				portal.done = true;
				protocol.commandComplete(portal.tag);
				return true;
			}
			const bool hasOutput = !portal.output->columns().empty();
			if (!portal.started)
			{
				const bool hasCursor = portal.native.statement->getFlags(&status) & IStatement::FLAG_HAS_CURSOR;
				check(&status);
				if (hasCursor && !hasOutput)
					throw ProtocolError("XX000", "Inconsistent engine output metadata");
				startImplicit();
				portal.started = true;
				if (hasCursor)
				{
					portal.native.cursor = portal.native.statement->openCursor(&status, transaction,
						portal.input->metadata(), portal.parameters.data(), portal.output->fetchMetadata(), 0);
					check(&status);
				}
				else
				{
					const auto previous = transaction;
					transaction = portal.native.statement->execute(&status, transaction,
						portal.input->metadata(), portal.parameters.data(),
						hasOutput ? portal.output->fetchMetadata() : nullptr,
						hasOutput ? portal.buffer.data() : nullptr);
					check(&status);
					if (!transaction || transaction != previous)
						throw ProtocolError("XX000", "Statement unexpectedly changed the transaction");
					if (hasOutput)
					{
						protocol.dataRow(portal.output->decode(portal.buffer.data(), portal.buffer.size()), portal.formats);
						portal.rows = 1;
					}
				}
			}
			std::uint32_t fetched = 0;
			while (portal.native.cursor)
			{
				if (execution->maxRows && fetched == execution->maxRows)
				{
					protocol.portalSuspended();
					return true;
				}
				const auto result = portal.native.cursor->fetchNext(&status, portal.buffer.data());
				check(&status);
				if (result == IStatus::RESULT_NO_DATA)
				{
					if (!portal.native.closeCursor())
						throw ProtocolError("XX000", "Could not close query resources");
					break;
				}
				if (result != IStatus::RESULT_OK || portal.rows == std::numeric_limits<std::uint64_t>::max())
					throw ProtocolError("XX000", "Invalid engine fetch result");
				protocol.dataRow(portal.output->decode(portal.buffer.data(), portal.buffer.size()), portal.formats);
				++portal.rows;
				++fetched;
			}
			const auto count = info.count == CountKind::Affected ?
				portal.native.statement->getAffectedRecords(&status) : portal.rows;
			check(&status);
			portal.tag = info.count == CountKind::None ? info.tag : info.tag + " " + std::to_string(count);
			if (!portal.native.close())
				throw ProtocolError("XX000", "Could not close query resources");
			portal.done = true;
			protocol.commandComplete(portal.tag);
		}
		else // Sync, including recovery after a prior error.
		{
			objects->discard = false;
			finishImplicit(ending);
			protocol.ready(state);
		}
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
		if (std::strcmp(ex.sqlState(), "08006") == 0)
			throw;
		return fail({ex.sqlState(), ex.what(), std::strcmp(ex.sqlState(), "XX000") == 0});
	}
}

} // namespace Firebird::PgWire
