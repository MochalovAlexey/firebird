/*
 *  The contents of this file are subject to the Initial
 *  Developer's Public License Version 1.0 (the "License");
 *  you may not use this file except in compliance with the
 *  License. You may obtain a copy of the License at
 *  http://www.ibphoenix.com/main.nfs?a=ibphoenix&page=ibp_idpl.
 *
 *  Software distributed under the License is distributed AS IS,
 *  WITHOUT WARRANTY OF ANY KIND, either express or implied.
 *  See the License for the specific language governing rights
 *  and limitations under the License.
 *
 *  The Original Code was created by Adriano dos Santos Fernandes
 *  for the Firebird Open Source RDBMS project.
 *
 *  Copyright (c) 2023 Adriano dos Santos Fernandes <adrianosf at gmail.com>
 *  and all contributors signed below.
 *
 *  All Rights Reserved.
 *  Contributor(s): ______________________________________.
 *
 */

#include "firebird.h"
#include "boost/test/unit_test.hpp"
#include "../FrontendLexer.h"
#include <variant>

using namespace Firebird;

BOOST_AUTO_TEST_SUITE(ISqlSuite)
BOOST_AUTO_TEST_SUITE(FrontendLexerSuite)


BOOST_AUTO_TEST_SUITE(FrontendLexerTests)

BOOST_AUTO_TEST_CASE(StripCommentsTest)
{
	BOOST_TEST(FrontendLexer::stripComments(
		"/* comment */ select 1 from rdb$database /* comment */") == "select 1 from rdb$database");

	BOOST_TEST(FrontendLexer::stripComments(
		"-- comment\nselect '123' /* comment */ from rdb$database -- comment\n") == "select '123' from rdb$database");

	BOOST_TEST(FrontendLexer::stripComments(
		" select 1 from rdb$database -- comment") == "select 1 from rdb$database");
}

BOOST_AUTO_TEST_CASE(GetSingleStatementTest)
{
	{	// scope
		const std::string s1 =
			"select /* ; */ -- ;\n"
			"  ';' || q'{;}'\n"
			"  from rdb$database;";

		BOOST_TEST(std::get<FrontendLexer::SingleStatement>(FrontendLexer(
			s1 + "\nselect ...").getSingleStatement(";")).withSemicolon == s1);
	}

	{	// scope
		const std::string s1 = "select 1 from rdb$database; -- comment";
		const std::string s2 = "select 2 from rdb$database;";
		const std::string s3 = "select 3 from rdb$database;";
		const std::string s4 = "execute block returns (o1 integer) as begin o1 = 1;";
		const std::string s5 = "end;";
		const std::string s6 = "?";
		const std::string s7 = "? set;";

		FrontendLexer lexer(s1 + "\n" + s2);

		BOOST_TEST(std::get<FrontendLexer::SingleStatement>(lexer.getSingleStatement(";")).withSemicolon == s1);

		lexer.appendBuffer(s3 + "\n" + s4 + s5);

		BOOST_TEST(std::get<FrontendLexer::SingleStatement>(lexer.getSingleStatement(";")).withSemicolon == s2);
		BOOST_TEST(std::get<FrontendLexer::SingleStatement>(lexer.getSingleStatement(";")).withSemicolon == s3);

		BOOST_TEST(std::get<FrontendLexer::SingleStatement>(lexer.getSingleStatement(";")).withSemicolon == s4);
		lexer.rewind();
		BOOST_TEST(std::get<FrontendLexer::SingleStatement>(lexer.getSingleStatement(";")).withSemicolon == s4 + s5);

		lexer.appendBuffer(s6 + "\n" + s7);
		BOOST_TEST(std::get<FrontendLexer::SingleStatement>(lexer.getSingleStatement(";")).withSemicolon == s6);
		BOOST_TEST(std::get<FrontendLexer::SingleStatement>(lexer.getSingleStatement(";")).withSemicolon == s7);
	}

	{	// scope
		const std::string s1 = "-- comment;;";
		const std::string s2 = "select 1 from rdb$database;";

		FrontendLexer lexer(s1);

		BOOST_TEST(std::get<FrontendLexer::IncompleteTokenError>(lexer.getSingleStatement(";")).insideComment);

		lexer.appendBuffer("\n" + s2);

		BOOST_TEST(std::get<FrontendLexer::SingleStatement>(lexer.getSingleStatement(";")).withSemicolon ==
			s1 + "\n" + s2);
	}

	BOOST_TEST(!std::get<FrontendLexer::IncompleteTokenError>(FrontendLexer(
		"select 1 from rdb$database").getSingleStatement(";")).insideComment);
	BOOST_TEST(std::get<FrontendLexer::IncompleteTokenError>(FrontendLexer(
		"select 1 from rdb$database; /*").getSingleStatement(";")).insideComment);
}

BOOST_AUTO_TEST_CASE(GetStatementEndPreservesSourceTest)
{
	const std::string first =
		" \t/* leading ; */ SELECT ';'';' AS \"column;\", "
		"q'{;}', q'(;)', q'[;]', q'<;>', q'!;!' FROM RDB$DATABASE;";
	const std::string second = " -- trailing ;\r\n/* another ; */ SELECT 2;";
	const std::string tail = "\nSELECT 3 -- comment at EOF";
	const std::string source = first + second + tail;
	FrontendLexer lexer(source);

	const auto firstEnd = lexer.getStatementEnd();
	BOOST_REQUIRE(firstEnd.has_value());
	BOOST_TEST(*firstEnd == first.size());
	const auto secondEnd = lexer.getStatementEnd();
	BOOST_REQUIRE(secondEnd.has_value());
	BOOST_TEST(*secondEnd == first.size() + second.size());
	BOOST_TEST(!lexer.getStatementEnd().has_value());
	BOOST_TEST(lexer.getBuffer() == source);
}

BOOST_AUTO_TEST_CASE(GetStatementEndDoesNotReadAheadTest)
{
	const std::string first = "SELECT 1;";
	for (const std::string tail : {" /* unterminated", " 'unterminated;", " -- comment at EOF"})
	{
		FrontendLexer lexer(first + tail);
		const auto statementEnd = lexer.getStatementEnd();
		BOOST_REQUIRE(statementEnd.has_value());
		BOOST_TEST(*statementEnd == first.size());
		BOOST_TEST(!lexer.getStatementEnd().has_value());
		BOOST_TEST(lexer.getBuffer() == first + tail);
	}
}

BOOST_AUTO_TEST_CASE(GetStatementEndAccumulationTest)
{
	const std::string first = "EXECUTE BLOCK RETURNS (N INTEGER) AS BEGIN N = 1;";
	const std::string second = " N = N + 1;";
	const std::string third = " SUSPEND;";
	const std::string fourth = " END;";
	const std::string next = " SELECT 42;";
	const std::string block = first + second + third + fourth;
	FrontendLexer lexer(block + next);

	// The caller keeps its command start unchanged while prepare reports an incomplete block.
	std::size_t expectedEnd = 0;
	for (const auto* part : {&first, &second, &third, &fourth, &next})
	{
		expectedEnd += part->size();
		const auto statementEnd = lexer.getStatementEnd();
		BOOST_REQUIRE(statementEnd.has_value());
		BOOST_TEST(*statementEnd == expectedEnd);
	}
	BOOST_TEST(!lexer.getStatementEnd().has_value());
	BOOST_TEST(lexer.getBuffer() == block + next);
}


BOOST_AUTO_TEST_CASE(BackslashStatementBoundariesTest)
{
	const std::string source = R"(SELECT 'a\';b'; SELECT q'{\';}'; SELECT "x\"; SELECT 2;)";
	FrontendLexer pg(source, true);
	const auto first = pg.getStatementEnd();
	BOOST_REQUIRE(first.has_value());
	BOOST_TEST(source.substr(0, *first) == R"(SELECT 'a\';b';)");
	BOOST_REQUIRE(pg.getStatementEnd().has_value());
	BOOST_REQUIRE(pg.getStatementEnd().has_value());
	BOOST_REQUIRE(pg.getStatementEnd().has_value());
	BOOST_TEST(!pg.getStatementEnd().has_value());
	BOOST_TEST(pg.getBuffer() == source);

	FrontendLexer native(source);
	const auto nativeFirst = native.getStatementEnd();
	BOOST_REQUIRE(nativeFirst.has_value());
	BOOST_TEST(source.substr(0, *nativeFirst) == R"(SELECT 'a\';)");
	FrontendLexer incomplete(R"(SELECT 'a\'; SELECT 2;)", true);
	BOOST_TEST(!incomplete.getStatementEnd().has_value());
}

BOOST_AUTO_TEST_CASE(GetStatementEndTailAndEmptyFragmentsTest)
{
	for (const std::string source : {"", " \t", "-- comment", "/* comment */", "SELECT 42",
		"SELECT 'unterminated;", "SELECT q'{unterminated;", "SELECT 42 /* unterminated;"})
	{
		FrontendLexer lexer(source);
		BOOST_TEST(!lexer.getStatementEnd().has_value());
		BOOST_TEST(lexer.getBuffer() == source);
	}

	FrontendLexer emptyStatements("; ;");
	const auto firstEnd = emptyStatements.getStatementEnd();
	const auto secondEnd = emptyStatements.getStatementEnd();
	BOOST_REQUIRE(firstEnd.has_value());
	BOOST_REQUIRE(secondEnd.has_value());
	BOOST_TEST(*firstEnd == 1u);
	BOOST_TEST(*secondEnd == 3u);
	BOOST_TEST(!emptyStatements.getStatementEnd().has_value());

	const std::string source = "?\nSELECT 42;";
	FrontendLexer question(source);
	const auto statementEnd = question.getStatementEnd();
	BOOST_REQUIRE(statementEnd.has_value());
	BOOST_TEST(*statementEnd == source.size());
}

BOOST_AUTO_TEST_CASE(SkipSingleLineCommentsTest)
{
	FrontendLexer lexer(
		"-- comment 0\r\n"
		"set -- comment 1\n"
		"stats -- comment 2\r\n"
		"- -- comment 3\n"
		"-- comment 4\n"
	);

	BOOST_TEST(lexer.getToken().processedText == "SET");
	BOOST_TEST(lexer.getToken().processedText == "STATS");
	BOOST_TEST(lexer.getToken().processedText == "-");
	BOOST_TEST(lexer.getToken().type == FrontendLexer::Token::TYPE_EOF);
}

BOOST_AUTO_TEST_CASE(SkipMultiLineCommentsTest)
{
	FrontendLexer lexer(
		"/* comment 0 */\r\n"
		"set /* comment 1\n"
		"comment 1 continuation\n"
		"*/ stats /* comment 2\r\n"
		"* */ / /* comment 3*/ /* comment 4*/"
	);

	BOOST_TEST(lexer.getToken().processedText == "SET");
	BOOST_TEST(lexer.getToken().processedText == "STATS");
	BOOST_TEST(lexer.getToken().processedText == "/");
	BOOST_TEST(lexer.getToken().type == FrontendLexer::Token::TYPE_EOF);
}

BOOST_AUTO_TEST_CASE(ParseStringsTest)
{
	FrontendLexer lexer(
		"'ab''c\"d' "
		"\"ab''c\"\"d\" "
		"q'{ab'c\"d}' "
		"q'(ab'c\"d)' "
		"q'[ab'c\"d]' "
		"q'<ab'c\"d>' "
		"q'!ab'c\"d!' "
	);

	BOOST_TEST(lexer.getToken().processedText == "ab'c\"d");
	BOOST_TEST(lexer.getToken().processedText == "ab''c\"d");
	BOOST_TEST(lexer.getToken().processedText == "ab'c\"d");
	BOOST_TEST(lexer.getToken().processedText == "ab'c\"d");
	BOOST_TEST(lexer.getToken().processedText == "ab'c\"d");
	BOOST_TEST(lexer.getToken().processedText == "ab'c\"d");
	BOOST_TEST(lexer.getToken().processedText == "ab'c\"d");
}

BOOST_AUTO_TEST_SUITE_END()	// FrontendLexerTests


BOOST_AUTO_TEST_SUITE_END()	// FrontendLexerSuite
BOOST_AUTO_TEST_SUITE_END()	// ISqlSuite
