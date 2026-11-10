/*
 * Copyright (c) 2026 Firebird Project and all contributors signed below.
 * Distributed under the Initial Developer's Public License Version 1.0.
 */
#include "firebird.h"
#include "../Execute.h"
#include <cstdio>
#include <cstring>

using namespace Firebird;
using namespace Firebird::PgWire;

namespace {
void require(bool ok, const char* message)
{
	if (!ok) throw std::runtime_error(message);
}

// Minimal test ABI endpoints. Unused methods stay null, so accidental new engine
// operations cannot silently pass. These are not production interface wrappers.
struct Statement : IStatement
{
	Statement(bool& mode, unsigned& frees) : IStatement(DoNotInherit()), mode(mode), frees(frees)
	{
		table.version = VERSION;
		table.getType = [](IStatement*, IStatus*) noexcept { return unsigned(isc_info_sql_stmt_ddl); };
		table.execute = [](IStatement* self, IStatus* status, ITransaction* tra,
			IMessageMetadata*, void*, IMessageMetadata*, void*) noexcept -> ITransaction*
		{
			auto& s = *static_cast<Statement*>(self);
			if (s.fail)
			{
				const ISC_STATUS errors[] = {isc_arg_gds, isc_wish_list, isc_arg_end};
				status->setErrors(errors);
			}
			else if (!s.ignore) s.mode = s.value;
			return tra;
		};
		table.free = [](IStatement* self, IStatus*) noexcept { ++static_cast<Statement*>(self)->frees; };
		cloopVTable = &table;
	}
	VTable table{};
	bool& mode;
	unsigned& frees;
	bool value = false, fail = false, ignore = false;
};

struct Attachment : IAttachment
{
	explicit Attachment(unsigned failure) : IAttachment(DoNotInherit()), statement(mode, frees), failure(failure)
	{
		table.version = VERSION;
		table.getInfo = [](IAttachment* self, IStatus*, unsigned, const unsigned char*,
			unsigned, unsigned char* buffer) noexcept
		{
			auto& a = *static_cast<Attachment*>(self);
			const unsigned char value[] = {fb_info_standard_conforming_strings, 4, 0,
				static_cast<unsigned char>(a.mode), 0, 0, 0, isc_info_end};
			std::memcpy(buffer, value, sizeof(value));
			if (a.missingInfo) buffer[0] = isc_info_error;
		};
		table.prepare = [](IAttachment* self, IStatus* status, ITransaction*, unsigned length,
			const char* sql, unsigned, unsigned) noexcept -> IStatement*
		{
			auto& a = *static_cast<Attachment*>(self);
			const std::string_view text(sql, length ? length : std::strlen(sql));
			if (text.substr(0, 3) != "SET")
			{
				const ISC_STATUS errors[] = {isc_arg_gds, isc_dsql_token_unk_err,
					isc_arg_number, 1, isc_arg_number, 1, isc_arg_end};
				status->setErrors(errors);
				return nullptr;
			}
			a.statement.value = text.find("= on") != text.npos;
			if (a.statement.value)
			{
				++a.restores;
				if (a.failure == 1)
				{
					const ISC_STATUS errors[] = {isc_arg_gds, isc_wish_list, isc_arg_end};
					status->setErrors(errors);
					return nullptr;
				}
				a.statement.fail = a.failure == 2;
				a.statement.ignore = a.failure == 3;
			}
			return &a.statement;
		};
		table.startTransaction = [](IAttachment* self, IStatus*, unsigned, const unsigned char*) noexcept -> ITransaction*
		{
			++static_cast<Attachment*>(self)->transactions;
			return nullptr;
		};
		cloopVTable = &table;
	}
	VTable table{};
	bool mode = true, missingInfo = false;
	unsigned restores = 0, frees = 0, transactions = 0;
	Statement statement;
	unsigned failure;
};

class Connection final : public IWireConnectionImpl<Connection, CheckStatusWrapper>
{
public:
	void addRef() override {}
	int release() override { return 1; }
	unsigned read(CheckStatusWrapper*, unsigned, void*) override { return 0; }
	unsigned write(CheckStatusWrapper*, unsigned size, const void* buffer) override
	{
		const auto bytes = static_cast<const unsigned char*>(buffer);
		output.insert(output.end(), bytes, bytes + size);
		return size;
	}
	void shutdown() override {}
	const char* getPeerAddress() override { return "127.0.0.1"; }
	unsigned getPeerPort() override { return 0; }
	FB_BOOLEAN isEncrypted() override { return FB_FALSE; }
	std::vector<unsigned char> output;
};
}

int main()
{
	try
	{
		for (unsigned failure : {0u, 1u, 2u, 3u})
		{
			Attachment attachment(failure);
			Connection connection;
			Protocol protocol(fb_get_master_interface(), &connection);
			QueryExecutor executor(&attachment);
			executor.reportStrings(protocol);
			connection.output.clear();
			Message query;
			query.type = 'Q';
			const char sql[] = "SET standard_conforming_strings = off; invalid";
			query.body.assign(sql, sql + sizeof(sql));
			require(executor.process(protocol, query) == (failure == 0), "Restoration failure did not close session");
			require(attachment.mode == (failure == 0), "Reported restoration without an actual SET");
			require(attachment.restores == 1 && attachment.transactions == 0, "Recursive restore or hidden transaction");
			require(attachment.frees == (failure == 1 ? 1u : 2u), "Maintenance statement leaked");
			std::string tags;
			for (std::size_t i = 0; i < connection.output.size();)
			{
				const auto& out = connection.output;
				const auto tag = char(out[i]);
				const auto size = (unsigned(out[i+1])<<24) | (unsigned(out[i+2])<<16) |
					(unsigned(out[i+3])<<8) | out[i+4];
				tags += tag;
				if (tag == 'E')
				{
					const std::string body(out.begin()+i+5, out.begin()+i+1+size);
					require(body.find(failure ? "FATAL" : "ERROR") != body.npos, "Wrong severity");
					require(body.find("42601") != body.npos, "Original error was lost");
				}
				i += 1 + size;
			}
			require(tags == (failure ? "CE" : "CEZ"), "Extra maintenance results or false ParameterStatus");
		}
		Attachment incompatible(0);
		incompatible.missingInfo = true;
		Connection connection;
		Protocol protocol(fb_get_master_interface(), &connection);
		QueryExecutor executor(&incompatible);
		bool rejected = false;
		try { executor.reportStrings(protocol); }
		catch (const ProtocolError&) { rejected = true; }
		require(rejected && connection.output.empty(), "Missing info silently defaulted to on");
		std::puts("Session restoration: 5 scenarios passed");
		return 0;
	}
	catch (const std::exception& error)
	{
		std::fprintf(stderr, "%s\n", error.what());
		return 1;
	}
}
