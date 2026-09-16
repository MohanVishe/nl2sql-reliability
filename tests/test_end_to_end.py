"""End-to-end checks against the real BIRD databases.

Everything else in the suite runs against synthetic tables. This file is what proves the
scoring path works on the actual data the study will use: real schemas, real gold SQL, real
result sets.

Skipped automatically when the databases are not present, so the suite still runs on a
clean checkout and in CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nl2sql_reliability import (
    compare_result_sets,
    load,
    order_matters,
    run_query,
)
from nl2sql_reliability.db import available, find_database, schema_for

DATA = Path("data")
EXPECTED_DATABASES = 11

pytestmark = pytest.mark.databases


def _databases_present() -> bool:
    try:
        return len(available(DATA)) >= EXPECTED_DATABASES
    except Exception:
        return False


skip_without_databases = pytest.mark.skipif(
    not _databases_present(),
    reason="BIRD databases not downloaded; run scripts/fetch_databases.py",
)


@pytest.fixture(scope="module")
def questions():
    return load()


@skip_without_databases
class TestDatabasesPresent:
    def test_all_eleven_databases_available(self):
        assert len(available(DATA)) >= EXPECTED_DATABASES

    def test_every_question_has_its_database(self, questions):
        missing = sorted({q.db_id for q in questions} - set(available(DATA)))
        assert not missing, f"no database file for: {missing}"


@skip_without_databases
class TestSchemas:
    def test_every_database_renders_a_schema(self, questions):
        for db_id in sorted({q.db_id for q in questions}):
            schema = schema_for(db_id, DATA)
            assert "CREATE TABLE" in schema, f"{db_id} produced no schema"

    def test_schema_sizes_are_recorded(self, questions):
        # Schema length sets the per-question token cost, which sets how much of a free-tier
        # quota each run consumes. Asserting an upper bound would be arbitrary; asserting
        # they are non-trivial and finite catches a broken renderer.
        for db_id in sorted({q.db_id for q in questions}):
            size = len(schema_for(db_id, DATA))
            assert 100 < size < 200_000, f"{db_id} schema is {size} chars"


@skip_without_databases
class TestGoldQueriesExecute:
    """The gold SQL must run. A gold query that errors cannot score anything."""

    def test_sample_of_gold_queries_execute(self, questions):
        from nl2sql_reliability.dataset import stratified_subset

        failures = []
        for question in stratified_subset(questions, 60, seed=0):
            result = run_query(find_database(question.db_id, DATA), question.gold_sql)
            if not result.ok:
                failures.append((question.question_id, question.db_id, result.error))
        assert not failures, f"{len(failures)} gold queries failed: {failures[:5]}"

    def test_gold_query_matches_itself(self, questions):
        question = questions[0]
        path = find_database(question.db_id, DATA)
        first = run_query(path, question.gold_sql)
        second = run_query(path, question.gold_sql)
        assert first.ok and second.ok
        assert compare_result_sets(
            first.rows, second.rows, ordered=order_matters(question.gold_sql)
        )


@pytest.fixture(scope="module")
def scored(questions):
    """A question whose gold query returns several rows, with its result set.

    Several rows, not merely one: a single-row result cannot demonstrate that row order is
    handled or that a truncated answer is rejected, which are two of the cases most likely
    to be scored wrongly.
    """
    for question in questions:
        path = find_database(question.db_id, DATA)
        result = run_query(path, question.gold_sql)
        if result.ok and len(result.rows) >= 2:
            return question, path, result
    pytest.skip("no question produced a multi-row gold result set")


@skip_without_databases
class TestScoringOnRealData:
    """The path a real run takes: execute gold, execute candidate, compare."""

    def test_gold_scores_as_correct(self, scored):
        question, _, gold = scored
        assert compare_result_sets(gold.rows, gold.rows, ordered=order_matters(question.gold_sql))

    def test_deliberately_wrong_query_scores_as_wrong(self, scored):
        question, path, gold = scored
        # Valid SQL against the same database, answering a different question. This is the
        # case that matters: invalid SQL is trivially caught, whereas plausible-but-wrong
        # SQL is what a model actually produces.
        wrong = run_query(path, "SELECT 1")
        assert wrong.ok
        assert not compare_result_sets(
            gold.rows, wrong.rows, ordered=order_matters(question.gold_sql)
        )

    def test_invalid_sql_does_not_score_as_correct(self, scored):
        question, path, gold = scored
        broken = run_query(path, "SELECT * FROM table_that_does_not_exist")
        assert not broken.ok
        assert not compare_result_sets(
            gold.rows, broken.rows, ordered=order_matters(question.gold_sql)
        )

    def test_truncated_result_scores_as_wrong(self, scored):
        question, _, gold = scored
        assert not compare_result_sets(
            gold.rows, gold.rows[:-1], ordered=order_matters(question.gold_sql)
        )

    def test_duplicated_row_scores_as_wrong(self, scored):
        question, _, gold = scored
        assert not compare_result_sets(
            gold.rows, gold.rows + gold.rows[:1], ordered=order_matters(question.gold_sql)
        )

    def test_reordered_rows_score_as_correct_when_gold_is_unordered(self, scored):
        question, _, gold = scored
        if order_matters(question.gold_sql):
            pytest.skip("gold query pins row order")
        assert compare_result_sets(gold.rows, list(reversed(gold.rows)))
