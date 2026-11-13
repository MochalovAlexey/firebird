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

/* Native profile string values; bounded columns retain ordinary character storage. */
#ifndef JRD_PG_STRING_H
#define JRD_PG_STRING_H
#include "../common/classes/fb_string.h"
#include "../common/dsc.h"
namespace Jrd {
class thread_db;
struct PgStringValue
{
	Firebird::string text;
	SLONG typmod = -1;
};
PgStringValue readPgString(thread_db*, const dsc*);
void writePgString(thread_db*, const PgStringValue&, dsc*);
}
#endif
