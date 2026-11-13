/*
 *  The contents of this file are subject to the Initial
 *  Developer's Public License Version 1.0 (the "License");
 *  you may not use this file except in compliance with the License.
 *  See http://www.ibphoenix.com/main.nfs?a=ibphoenix&page=ibp_idpl.
 *  Software distributed under the License is distributed "AS IS",
 *  WITHOUT WARRANTY OF ANY KIND, either express or implied.
 *  All Rights Reserved.
 */
#ifndef JRD_PG_TEMP_OPERATIONS_H
#define JRD_PG_TEMP_OPERATIONS_H

#include "../jrd/QualifiedName.h"

namespace Jrd
{
class thread_db;
class jrd_tra;
class LocalTemporaryTable;

// The compatibility policy is limited to this attachment's PG temporary tables.
// Storage, index statistics and row undo remain native engine operations.
LocalTemporaryTable* getPgTempTable(thread_db*, const QualifiedName&);
void analyzePgTempTable(thread_db*, jrd_tra*, const QualifiedName&);
void truncatePgTempTable(thread_db*, jrd_tra*, const QualifiedName&);
}
#endif
