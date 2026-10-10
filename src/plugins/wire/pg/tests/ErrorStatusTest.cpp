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

#include "../ErrorStatus.h"

#include <cstdio>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <vector>

using namespace Firebird;
using namespace Firebird::PgWire;

namespace
{

using Vector = std::vector<ISC_STATUS>;
struct DisposeStatus { void operator()(IStatus* value) const { value->dispose(); } };
using Status = std::unique_ptr<IStatus, DisposeStatus>;

void require(bool condition, const char* message)
{
	if (!condition)
		throw std::runtime_error(message);
}

ISC_STATUS text(const char* value)
{
	return reinterpret_cast<ISC_STATUS>(value);
}

void expect(IMaster* master, const Vector& vector, const char* state, bool fatal)
{
	Status status(master->getStatus());
	status->setErrors(vector.data());
	const auto diagnostic = diagnose(status.get());
	require(diagnostic.state == state, "Unexpected diagnostic SQLSTATE");
	require(diagnostic.fatal == fatal, "Unexpected diagnostic severity");
	require(std::strstr(diagnostic.message, "secret diagnostic") == nullptr,
		"Raw engine text leaked into client diagnostic");
}

void verifyNestedStatus(IMaster* master)
{
	Vector longStatus{isc_arg_gds, isc_dsql_error};
	for (unsigned i = 0; i < 16; ++i)
	{
		longStatus.push_back(isc_arg_number);
		longStatus.push_back(i);
	}
	require(longStatus.size() > ISC_STATUS_LENGTH, "Nested cause is not beyond legacy prefix");
	longStatus.insert(longStatus.end(), {isc_arg_gds, isc_no_priv,
		isc_arg_string, text("secret diagnostic"), isc_arg_end});
	expect(master, longStatus, "42501", false);
	longStatus[longStatus.size() - 4] = isc_att_shut_idle;
	expect(master, longStatus, "57P05", true);

	// Numeric/string arguments containing code values or SQLSTATE-like text
	// must never be interpreted as causes.
	expect(master, {isc_arg_gds, isc_random, isc_arg_number, isc_att_shutdown,
		isc_arg_string, text("57P01 secret diagnostic"), isc_arg_end}, "XX000", false);
	expect(master, {isc_arg_gds, isc_dsql_error, isc_arg_gds, isc_sqlerr,
		isc_arg_number, -104, isc_arg_gds, isc_dsql_token_unk_err, isc_arg_end}, "42601", false);
}

// The real IStatus setter normalizes cstring to a copied string. Keep its real
// status storage, but expose the original valid vector to exercise the parser's
// cstring branch as another IStatus implementation is allowed to do.
class RawStatus final : public BaseStatusWrapper<RawStatus>
{
public:
	RawStatus(IStatus* actual, const ISC_STATUS* vector)
		: BaseStatusWrapper<RawStatus>(actual), vector(vector)
	{
		setErrors(vector);
	}
	const ISC_STATUS* getErrors() const override { return vector; }
	static void checkException(RawStatus*) noexcept {}
private:
	const ISC_STATUS* vector;
};

void verifyCString(IMaster* master)
{
	const char bytes[] = {'x', '\0', 'y'};
	const Vector vector{isc_arg_gds, isc_random, isc_arg_cstring, sizeof(bytes), text(bytes),
		isc_arg_gds, isc_cancelled, isc_arg_end};
	Status actual(master->getStatus());
	RawStatus raw(actual.get(), vector.data());
	require(actual->getErrors()[2] == isc_arg_string && raw.getErrors()[2] == isc_arg_cstring,
		"CString fixture did not preserve the raw argument shape");
	const auto diagnostic = diagnose(&raw);
	require(diagnostic.state == "57014" && !diagnostic.fatal,
		"CString arguments shifted the following cause");
}

void verifyExplicitState(IMaster* master)
{
	expect(master, {isc_arg_gds, isc_random, isc_arg_sql_state, text("22012"), isc_arg_end},
		"22012", false);
	expect(master, {isc_arg_gds, isc_random, isc_arg_sql_state, text("08006"), isc_arg_end},
		"08006", true);
	for (const char* invalid : {"00000", "HY000", "", "2201", "220120", "2200z", "22!12"})
	{
		expect(master, {isc_arg_gds, isc_random, isc_arg_sql_state, text(invalid), isc_arg_end},
			"XX000", false);
	}
	for (const char* generic : {"42000", "22000"})
	{
		expect(master, {isc_arg_gds, isc_random, isc_arg_sql_state, text(generic),
			isc_arg_gds, isc_dsql_field_err, isc_arg_end}, "42703", false);
	}
	// The explicit fallback outranks generic ISC mapping, regardless of order.
	expect(master, {isc_arg_gds, isc_dsql_error, isc_arg_sql_state, text("22012"), isc_arg_end},
		"22012", false);
	expect(master, {isc_arg_gds, isc_random, isc_arg_sql_state, text("22012"),
		isc_arg_gds, isc_dsql_error, isc_arg_end}, "22012", false);

	Vector longState{isc_arg_gds, isc_random};
	for (unsigned i = 0; i < 16; ++i)
		longState.insert(longState.end(), {isc_arg_number, i});
	longState.insert(longState.end(), {isc_arg_sql_state, text("22003"), isc_arg_end});
	expect(master, longState, "22003", false);
}

void verifyPriorities(IMaster* master)
{
	struct Test { ISC_STATUS code; const char* state; bool fatal; };
	const Test cases[] = {
		{isc_no_priv, "42501", false},
		{isc_cancelled, "57014", false},
		{isc_att_stmt_timeout, "57014", false},
		{isc_att_shutdown, "57P01", true},
		{isc_att_shut_idle, "57P05", true},
		{isc_bad_trans_handle, "08006", true},
		{isc_io_error, "58030", true},
		{isc_virmemexh, "53200", true}
	};
	for (const auto& test : cases)
	{
		expect(master, {isc_arg_gds, isc_random, isc_arg_sql_state, text("22012"),
			isc_arg_gds, test.code, isc_arg_end}, test.state, test.fatal);
		expect(master, {isc_arg_gds, test.code, isc_arg_sql_state, text("22012"), isc_arg_end},
			test.state, test.fatal);
	}
	expect(master, {isc_arg_gds, isc_no_priv, isc_arg_gds, isc_cancelled,
		isc_arg_gds, isc_att_shutdown, isc_arg_end}, "57P01", true);
	expect(master, {isc_arg_gds, isc_att_shutdown, isc_arg_gds, isc_cancelled,
		isc_arg_gds, isc_no_priv, isc_arg_end}, "57P01", true);
	expect(master, {isc_arg_gds, isc_no_priv, isc_arg_gds, isc_cancelled, isc_arg_end}, "57014", false);
}

void verifyConstraints(IMaster* master)
{
	struct Test { ISC_STATUS code; const char* state; };
	const Test cases[] = {
		{isc_no_dup, "23505"}, {isc_unique_key_violation, "23505"},
		{isc_foreign_key, "23503"}, {isc_foreign_key_target_doesnt_exist, "23503"},
		{isc_foreign_key_references_present, "23503"}, {isc_check_constraint, "23514"},
		{isc_not_valid, "23000"}
	};
	for (const auto& test : cases)
	{
		Vector wrapped{isc_arg_gds, isc_dsql_error, isc_arg_gds, isc_sqlerr, isc_arg_number, -625};
		for (unsigned i = 0; i < 16; ++i)
			wrapped.insert(wrapped.end(), {isc_arg_number, i});
		wrapped.insert(wrapped.end(), {isc_arg_gds, test.code,
			isc_arg_string, text("secret diagnostic"), isc_arg_end});
		expect(master, wrapped, test.state, false);
		expect(master, {isc_arg_gds, test.code, isc_arg_gds, isc_att_shutdown, isc_arg_end},
			"57P01", true);
	}
	expect(master, {isc_arg_gds, isc_random, isc_arg_number, isc_unique_key_violation,
		isc_arg_string, text("23505 secret diagnostic"), isc_arg_end}, "XX000", false);
}

} // namespace

int main()
{
	try
	{
		IMaster* master = fb_get_master_interface();
		verifyNestedStatus(master);
		verifyCString(master);
		verifyExplicitState(master);
		verifyPriorities(master);
		verifyConstraints(master);
		std::puts("PgWire error status tests passed");
		return 0;
	}
	catch (const std::exception& error)
	{
		std::fprintf(stderr, "PgWire error status tests failed: %s\n", error.what());
	}
	catch (...)
	{
		std::fputs("PgWire error status tests failed: unexpected exception\n", stderr);
	}
	return 1;
}
