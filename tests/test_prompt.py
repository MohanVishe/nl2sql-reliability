"""Tests for prompt assembly and SQL extraction.

Extraction gets the bulk of the attention. Every reply shape that slips through is scored as
a wrong answer, so a sloppy parser would show up in the results as unreliability that belongs
to this code rather than to the model.
"""

from __future__ import annotations

import sqlite3
from dataclasses import FrozenInstanceError

import pytest

from nl2sql_reliability.prompt import (
    INSTRUCTIONS,
    Prompt,
    build,
    build_for,
    extract_sql,
    repair,
)

SCHEMA = "CREATE TABLE school (\n  id INTEGER PRIMARY KEY,\n  name TEXT\n);"


class TestBuild:
    def test_contains_schema_question_and_instructions(self):
        prompt = build(SCHEMA, "How many schools?")
        assert SCHEMA in prompt.text
        assert "How many schools?" in prompt.text
        assert INSTRUCTIONS in prompt.text

    def test_evidence_is_included_as_a_hint(self):
        prompt = build(SCHEMA, "How many?", "a school is a row in school")
        assert "a school is a row in school" in prompt.text

    def test_empty_evidence_adds_no_hint_line(self):
        assert "Hint:" not in build(SCHEMA, "How many?", "").text
        assert "Hint:" not in build(SCHEMA, "How many?", "   ").text

    def test_question_is_stripped(self):
        assert "Question: How many?" in build(SCHEMA, "  How many?  ").text

    def test_ends_by_asking_for_sql(self):
        assert build(SCHEMA, "How many?").text.rstrip().endswith("SQL:")

    def test_str_is_the_full_text(self):
        prompt = build(SCHEMA, "How many?")
        assert str(prompt) == prompt.text


class TestCacheablePrefix:
    """The prefix must not vary with the question, or prompt caching cannot hit."""

    def test_prefix_is_identical_across_questions_on_one_schema(self):
        a = build(SCHEMA, "How many schools?", "some hint")
        b = build(SCHEMA, "Which school is largest?", "a different hint")
        assert a.prefix == b.prefix

    def test_prefix_differs_between_schemas(self):
        other = "CREATE TABLE pupil (id INTEGER PRIMARY KEY);"
        assert build(SCHEMA, "q").prefix != build(other, "q").prefix

    def test_prefix_holds_the_schema_and_suffix_holds_the_question(self):
        prompt = build(SCHEMA, "How many schools?")
        assert SCHEMA in prompt.prefix
        assert "How many schools?" not in prompt.prefix
        assert "How many schools?" in prompt.suffix

    def test_text_is_prefix_then_suffix(self):
        prompt = build(SCHEMA, "How many?")
        assert prompt.text.startswith(prompt.prefix)
        assert prompt.text.endswith(prompt.suffix)

    def test_prompt_is_frozen(self):
        with pytest.raises(FrozenInstanceError):
            build(SCHEMA, "q").prefix = "mutated"


class TestExtractFenced:
    def test_sql_fence(self):
        assert extract_sql("```sql\nSELECT 1\n```") == "SELECT 1"

    def test_sqlite_fence(self):
        assert extract_sql("```sqlite\nSELECT 1\n```") == "SELECT 1"

    def test_bare_fence(self):
        assert extract_sql("```\nSELECT 1\n```") == "SELECT 1"

    def test_prose_around_the_fence_is_dropped(self):
        reply = "Sure! Here is the query:\n\n```sql\nSELECT COUNT(*) FROM school\n```\n\nDone."
        assert extract_sql(reply) == "SELECT COUNT(*) FROM school"

    def test_last_statement_fence_wins(self):
        # Models often show an attempt, correct themselves, then give the final query.
        reply = "First try:\n```sql\nSELECT 1\n```\nBetter:\n```sql\nSELECT 2\n```"
        assert extract_sql(reply) == "SELECT 2"

    def test_schema_fence_before_the_query_is_ignored(self):
        reply = "```\nCREATE TABLE school (id INT);\n```\n```sql\nSELECT 1\n```"
        assert extract_sql(reply) == "SELECT 1"

    def test_multiline_sql_keeps_its_shape(self):
        sql = "SELECT name\nFROM school\nWHERE id > 3"
        assert extract_sql(f"```sql\n{sql}\n```") == sql

    def test_unterminated_fence_still_yields_sql(self):
        # Generation that hit a token limit mid-reply.
        assert extract_sql("Here:\n```sql\nSELECT 1") == "SELECT 1"

    def test_uppercase_fence_tag(self):
        assert extract_sql("```SQL\nSELECT 1\n```") == "SELECT 1"


class TestExtractUnfenced:
    def test_bare_sql(self):
        assert extract_sql("SELECT COUNT(*) FROM school") == "SELECT COUNT(*) FROM school"

    def test_leading_prose(self):
        assert extract_sql("The query is: SELECT 1") == "SELECT 1"

    def test_with_clause(self):
        reply = "WITH t AS (SELECT 1) SELECT * FROM t"
        assert extract_sql(reply) == reply

    def test_trailing_prose_after_semicolon_is_dropped(self):
        reply = "SELECT 1;\nThis returns a single row."
        assert extract_sql(reply) == "SELECT 1"

    def test_lowercase_keyword(self):
        assert extract_sql("select 1") == "select 1"


class TestExtractDegenerate:
    def test_empty_reply(self):
        assert extract_sql("") == ""

    def test_none_safe(self):
        assert extract_sql(None) == ""

    def test_refusal_yields_nothing(self):
        assert extract_sql("I cannot answer that.") == ""

    @pytest.mark.parametrize(
        "prose",
        [
            "I cannot help with that.",
            "There is no table with customer data in this schema.",
            "Start with the orders table and join it to customers.",
        ],
    )
    def test_the_english_word_with_is_not_a_cte(self, prose):
        # "with" is an ordinary word. Anchoring on it bare turned every refusal containing it
        # into a SQL statement, which would have been scored as a failed query rather than as
        # a reply with no query in it.
        assert extract_sql(prose) == ""

    def test_a_real_cte_is_still_found_in_prose(self):
        reply = "Try this: WITH totals AS (SELECT 1 AS n) SELECT n FROM totals"
        assert extract_sql(reply) == "WITH totals AS (SELECT 1 AS n) SELECT n FROM totals"

    def test_whitespace_only_fence_yields_nothing(self):
        assert extract_sql("```sql\n\n```") == ""

    def test_trailing_semicolon_removed(self):
        assert extract_sql("```sql\nSELECT 1;\n```") == "SELECT 1"

    def test_surrounding_whitespace_removed(self):
        assert extract_sql("```sql\n\n   SELECT 1   \n\n```") == "SELECT 1"

    def test_think_block_is_stripped(self):
        reply = "<think>Maybe SELECT 99 is right?</think>\n```sql\nSELECT 1\n```"
        assert extract_sql(reply) == "SELECT 1"

    def test_think_block_alone_yields_nothing(self):
        assert extract_sql("<think>I should use SELECT 1</think>") == ""

    def test_non_statement_fence_is_returned_rather_than_discarded(self):
        # Scoring it wrong is correct; silently returning "" would hide what the model did.
        reply = "```\nUPDATE school SET name = 'x'\n```"
        assert extract_sql(reply) == "UPDATE school SET name = 'x'"


class TestExtractedSqlIsExecutable:
    """The point of extraction: what comes out must run."""

    @pytest.fixture
    def connection(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            "CREATE TABLE school (id INTEGER PRIMARY KEY, name TEXT);"
            "INSERT INTO school VALUES (1, 'a'), (2, 'b');"
        )
        yield connection
        connection.close()

    @pytest.mark.parametrize(
        "reply",
        [
            "```sql\nSELECT COUNT(*) FROM school\n```",
            "```sql\nSELECT COUNT(*) FROM school;\n```",
            "Here you go:\n\n```\nSELECT COUNT(*) FROM school\n```\n\nDone.",
            "SELECT COUNT(*) FROM school",
            "SELECT COUNT(*) FROM school;\nThat is the total.",
            "<think>hmm</think>\n```sql\nSELECT COUNT(*) FROM school\n```",
        ],
    )
    def test_reply_shapes_all_execute(self, connection, reply):
        assert connection.execute(extract_sql(reply)).fetchall() == [(2,)]


class TestRepair:
    def test_includes_the_failed_sql_and_the_error(self):
        text = repair("SELECT * FROM nope", "no such table: nope")
        assert "SELECT * FROM nope" in text
        assert "no such table: nope" in text

    def test_asks_for_a_corrected_query(self):
        assert "corrected" in repair("SELECT 1", "boom").lower()

    def test_round_trips_through_extraction(self):
        # The repair turn must be parseable by the same extractor, or the agentic arm would
        # score its second attempt wrong for a formatting reason.
        echoed = extract_sql(repair("SELECT 1", "boom"))
        assert extract_sql(f"```sql\n{echoed}\n```") == "SELECT 1"


@pytest.mark.databases
class TestBuildForRealQuestion:
    def test_builds_from_a_dataset_question(self):
        from nl2sql_reliability.dataset import load
        from nl2sql_reliability.db import available

        if not available():
            pytest.skip("BIRD databases not downloaded")
        question = load()[0]
        prompt = build_for(question)
        assert isinstance(prompt, Prompt)
        assert prompt.db_id == question.db_id
        assert "CREATE TABLE" in prompt.prefix
        assert question.question in prompt.suffix
