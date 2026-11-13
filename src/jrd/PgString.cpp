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
#include "../jrd/PgString.h"
#include "../jrd/jrd.h"
#include "../jrd/blb.h"
#include "../jrd/blb_proto.h"
#include "../jrd/mov_proto.h"
#include "../jrd/err_proto.h"

using namespace Firebird;
namespace Jrd {

PgStringValue readPgString(thread_db* tdbb, const dsc* desc)
{
	PgStringValue result;
	if (!desc->isMcharBlob())
	{
		result.text = MOV_make_string2(tdbb, desc, ttype_utf8, false);
		if (desc->getCharSet() == CS_PG_MCHAR && desc->dsc_dtype == dtype_text)
		{
			result.typmod = 0;
			for (const auto c : result.text)
				result.typmod += (static_cast<UCHAR>(c) & 0xc0) != 0x80;
		}
		return result;
	}
	AutoBlb blob(tdbb, blb::open(tdbb, tdbb->getTransaction(), (bid*) desc->dsc_address));
	UCHAR header[4];
	if (blob->blb_length < sizeof(header) ||
		blob->BLB_get_data(tdbb, header, sizeof(header), false) != sizeof(header))
		ERR_post(Arg::Gds(isc_bad_segstr_id));
	const ULONG typmod = ULONG(header[0]) | (ULONG(header[1]) << 8) |
		(ULONG(header[2]) << 16) | (ULONG(header[3]) << 24);
	result.typmod = static_cast<SLONG>(typmod);
	if (result.typmod < -1)
		ERR_post(Arg::Gds(isc_bad_segstr_id));
	const auto size = blob->blb_length - sizeof(header);
	blob->BLB_get_data(tdbb, reinterpret_cast<UCHAR*>(result.text.getBuffer(size)), size, false);
	return result;
}

void writePgString(thread_db* tdbb, const PgStringValue& value, dsc* desc)
{
	// Only unbounded mchar needs a value header: its original typmod affects
	// SUBSTR even after a bare cast, UNION, function return or persistence.
	// The header is an explicit little-endian int32 followed by UTF8 bytes.
	auto blob = blb::create(tdbb, tdbb->getTransaction(), (bid*) desc->dsc_address);
	if (desc->isMcharBlob())
	{
		const auto typmod = static_cast<ULONG>(value.typmod);
		const UCHAR header[] = {UCHAR(typmod), UCHAR(typmod >> 8), UCHAR(typmod >> 16), UCHAR(typmod >> 24)};
		blob->BLB_put_data(tdbb, header, sizeof(header));
	}
	blob->BLB_put_data(tdbb, reinterpret_cast<const UCHAR*>(value.text.c_str()), value.text.length());
	blob->BLB_close(tdbb);
}
}
