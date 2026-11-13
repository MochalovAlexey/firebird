/*
 *  The contents of this file are subject to the Initial
 *  Developer's Public License Version 1.0 (the "License");
 *  you may not use this file except in compliance with the License.
 *  See http://www.ibphoenix.com/main.nfs?a=ibphoenix&page=ibp_idpl.
 *  Software distributed under the License is distributed "AS IS",
 *  WITHOUT WARRANTY OF ANY KIND, either express or implied.
 *  All Rights Reserved.
 */
#include "firebird.h"
#include <exception>
#include "../jrd/PgTempOperations.h"
#include "../jrd/jrd.h"
#include "../jrd/Attachment.h"
#include "../jrd/LocalTemporaryTable.h"
#include "../jrd/met.h"
#include "../jrd/Relation.h"
#include "../jrd/req.h"
#include "../jrd/tra.h"
#include "../jrd/Statement.h"
#include "../jrd/Resources.h"
#include "../jrd/idx_proto.h"
#include "../dsql/dsql.h"
#include "../dsql/DsqlStatementCache.h"

using namespace Firebird;

namespace Jrd
{
LocalTemporaryTable* getPgTempTable(thread_db* tdbb, const QualifiedName& name)
{
	auto resolved = name;
	const auto attachment = tdbb->getAttachment();
	attachment->qualifyPgRelationName(tdbb, resolved, {obj_relation});
	const auto found = attachment->att_local_temporary_tables.get(resolved);
	if (!found)
	{
		if (MetadataCache::getVersioned<Cached::Relation>(tdbb, resolved, CacheFlag::AUTOCREATE))
			(Arg::Gds(isc_random) << "Operation requires a PG temporary table" << Arg::SqlState("42809")).raise();
		(Arg::Gds(isc_relnotdef) << name.toQuotedString() << Arg::SqlState("42P01")).raise();
	}
	if (!(*found)->pgTemporary)
		(Arg::Gds(isc_random) << "Operation requires a PG-owned temporary table" << Arg::SqlState("42809")).raise();
	return *found;
}

void analyzePgTempTable(thread_db* tdbb, jrd_tra*, const QualifiedName& name)
{
	const auto table = getPgTempTable(tdbb, name);
	if (table->relation)
	{
		const auto relation = table->relation->getPermanent();
		// The base temporary tree is empty; statistics must describe the current
		// attachment's data instance. Native IDX_statistics updates its root page.
		AutoSetRestoreFlag<ULONG> pageSpace(&tdbb->tdbb_flags, TDBB_use_db_page_space, false);
		if (relation->getPages(tdbb, 0, false))
		{
			for (const auto& index : table->indexes)
			{
				if (!index.inactive)
				{
					SelectivityList selectivity(*tdbb->getDefaultPool());
					IDX_statistics(tdbb, relation, index.id, selectivity);
				}
			}
		}
	}
	// New prepares must use the refreshed native selectivity/cardinality.
	tdbb->getAttachment()->att_dsql_instance->dbb_statement_cache->purge(tdbb, false);
}

void truncatePgTempTable(thread_db* tdbb, jrd_tra* transaction, const QualifiedName& name)
{
	const auto table = getPgTempTable(tdbb, name);
	if (!table->relation)
		return;
	const auto relation = table->relation->getPermanent();
	const auto attachment = tdbb->getAttachment();
	const auto inUse = [&]()
	{
		(Arg::Gds(isc_obj_in_use) << name.toQuotedString() << Arg::SqlState("55006")).raise();
	};
	for (auto other = attachment->att_transactions; other; other = other->tra_next)
	{
		if (other != transaction && other->traLttRel.exist(relation))
			inUse();
	}
	for (const auto request : attachment->att_requests)
	{
		if (!(request->req_flags & req_active))
			continue;
		for (const auto& resource : request->getStatement()->getResources()->relations)
		{
			if (resource() == relation)
				inUse();
		}
	}
	AutoSetRestoreFlag<ULONG> pageSpace(&tdbb->tdbb_flags, TDBB_use_db_page_space, false);
	const auto pages = relation->getPages(tdbb, 0, false);
	if (!pages)
		return;
	const auto discardUndo = [&]()
	{
		std::exception_ptr error;
		for (auto savepoint = transaction->tra_save_point; savepoint; savepoint = savepoint->getNext())
		{
			try
			{
				savepoint->discardTempFrameActions(table->relation, 0, true);
			}
			catch (...)
			{
				if (!error)
					error = std::current_exception();
			}
		}
		if (error)
			std::rethrow_exception(error);
	};
	// Atomically refuse shared instances before changing either data or undo.
	bool deleted;
	try
	{
		deleted = relation->delPages(tdbb, 0, pages, true);
	}
	catch (...)
	{
		// Deletion may already have unlinked/freed pages. Remove all old row
		// actions before AutoSavePoint or ROLLBACK TO can attempt to replay them.
		try { discardUndo(); } catch (...) { /* Preserve the page cleanup error. */ }
		transaction->tra_flags |= TRA_invalidated;
		attachment->signalShutdown(isc_att_shutdown);
		throw;
	}
	if (!deleted)
		inUse();
	// Postgres Pro fasttruncate deliberately survives rollback. Old undo must
	// never address record numbers reused by the new empty native instance.
	try
	{
		discardUndo();
	}
	catch (...)
	{
		// Undo-space allocation failure must not replay old rows into new pages.
		transaction->tra_flags |= TRA_invalidated;
		attachment->signalShutdown(isc_att_shutdown);
		throw;
	}
	try
	{
		relation->getPages(tdbb); // Recreate native indexes; retain metadata/identity.
	}
	catch (...)
	{
		// getPages can have inserted an instance before an index allocation fails.
		// Keep the table empty and allow a later access to create a complete one.
		try
		{
			if (const auto partial = relation->getPages(tdbb, 0, false))
			{
				if (!relation->delPages(tdbb, 0, partial, true))
					attachment->signalShutdown(isc_att_shutdown);
			}
		}
		catch (...)
		{
			attachment->signalShutdown(isc_att_shutdown);
			// Preserve the original instance creation error.
		}
		throw;
	}
}
}
