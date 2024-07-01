/*
 * The contents of this file are subject to the Interbase Public
 * License Version 1.0 (the "License"); you may not use this file
 * except in compliance with the License. You may obtain a copy
 * of the License at http://www.Inprise.com/IPL.html
 *
 * Software distributed under the License is distributed on an
 * "AS IS" basis, WITHOUT WARRANTY OF ANY KIND, either express
 * or implied. See the License for the specific language governing
 * rights and limitations under the License.
 *
 * The Original Code was created by Inprise Corporation
 * and its predecessors. Portions created by Inprise Corporation are
 * Copyright (C) Inprise Corporation.
 *
 * All Rights Reserved.
 * Contributor(s): ______________________________________.
 */

#ifndef JRD_FUNCTION_H
#define JRD_FUNCTION_H

#include "../jrd/Routine.h"
#include "../common/classes/array.h"
#include "../common/dsc.h"
#include "../common/classes/NestConst.h"
#include "../jrd/val.h"
#include "../dsql/Nodes.h"
#include "../jrd/HazardPtr.h"
#include "../jrd/lck.h"

namespace Jrd
{
	class ValueListNode;
	class QualifiedName;
	class Function;

	class Function : public Routine
	{
		static const char* const EXCEPTION_MESSAGE;

	public:
		static Lock* makeLock(thread_db* tdbb, MemoryPool& p);
		static int blockingAst(void* ast_object);

		static Function* lookup(thread_db* tdbb, MetaId id, ObjectBase::Flag flags);
		static Function* lookup(thread_db* tdbb, const QualifiedName& name, ObjectBase::Flag flags);

	private:
		explicit Function(Cached::Function* perm)
			: Routine(perm->getPool()),
			  cachedFunction(perm),
			  fun_entrypoint(NULL),
			  fun_inputs(0),
			  fun_return_arg(0),
			  fun_temp_length(0),
			  fun_exception_message(perm->getPool()),
			  fun_deterministic(false),
			  fun_external(NULL)
		{
		}

	public:
		Function(MemoryPool& p)
			: Routine(p),
			  cachedFunction(FB_NEW_POOL(p) Cached::Function(p)),
			  fun_entrypoint(NULL),
			  fun_inputs(0),
			  fun_return_arg(0),
			  fun_temp_length(0),
			  fun_exception_message(p),
			  fun_deterministic(false),
			  fun_external(NULL)
		{
		}

		static Function* create(thread_db* tdbb, MemoryPool& pool, Cached::Function* perm);
		bool scan(thread_db* tdbb, ObjectBase::Flag flags);

		static const char* objectFamily(void*)
		{
			return "function";
		}


	public:
		int getObjectType() const override
		{
			return objectType();
		}

		SLONG getSclType() const override
		{
			return obj_functions;
		}

		static int objectType();

	private:
		virtual ~Function()
		{
			delete fun_external;
		}

	public:
		void releaseExternal() override
		{
			delete fun_external;
			fun_external = NULL;
		}

	public:
		Cached::Function* cachedFunction;		// entry in the cache
		int (*fun_entrypoint)();				// function entrypoint
		USHORT fun_inputs;						// input arguments
		USHORT fun_return_arg;					// return argument
		ULONG fun_temp_length;					// temporary space required

		Firebird::string fun_exception_message;	// message containing the exception error message

		bool fun_deterministic;
		const ExtEngineManager::Function* fun_external;

		Cached::Function* getPermanent() const override
		{
			return cachedFunction;
		}

		bool reload(thread_db* tdbb) override;
	};
}

#endif // JRD_FUNCTION_H
