"""Tests for query execution against a real (temporary) SQLite database."""

from __future__ import annotations

import sqlite3

import pytest

from nl2sql_reliability.compare import compare_result_sets, order_matters
from nl2sql_reliability.execute import run_query


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE person (id INTEGER PRIMARY KEY, name TEXT, age INTEGER, city TEXT);
        INSERT INTO person VALUES
            (1, 'alice', 30, 'mumbai'),
            (2, 'bob',   25, 'pune'),
            (3, 'carol', 35, 'mumbai'),
            (4, 'dave',  25, 'delhi');
        """
    )
    connection.commit()
    connection.close()
    return path


class TestExecution:
    def test_simple_select(self, db):
        result = run_query(db, "SELECT name FROM person WHERE age > 28")
        assert result.ok
        assert result.row_count == 2
        assert result.columns == ["name"]

    def test_invalid_sql_is_reported_not_raised(self, db):
        result = run_query(db, "SELECT nonexistent FROM person")
        assert not result.ok
        assert result.error
        assert result.rows == []

    def test_syntax_error_is_reported(self, db):
        result = run_query(db, "SELECT FROM WHERE")
        assert not result.ok
        assert result.error

    def test_missing_database_is_an_error(self, tmp_path):
        result = run_query(tmp_path / "absent.sqlite", "SELECT 1")
        assert not result.ok
        assert "not found" in result.error

    def test_empty_result_is_success_not_failure(self, db):
        result = run_query(db, "SELECT name FROM person WHERE age > 100")
        assert result.ok
        assert result.row_count == 0

    def test_max_rows_caps_output(self, db):
        result = run_query(db, "SELECT name FROM person", max_rows=2)
        assert result.ok
        assert result.row_count == 2

    def test_elapsed_is_recorded(self, db):
        result = run_query(db, "SELECT 1")
        assert result.elapsed_seconds >= 0


class TestReadOnly:
    """Generated SQL is untrusted. A mutation must fail rather than corrupt the benchmark."""

    def test_insert_is_refused(self, db):
        result = run_query(db, "INSERT INTO person VALUES (5, 'eve', 20, 'goa')")
        assert not result.ok

    def test_update_is_refused(self, db):
        result = run_query(db, "UPDATE person SET age = 0")
        assert not result.ok

    def test_drop_is_refused(self, db):
        result = run_query(db, "DROP TABLE person")
        assert not result.ok

    def test_database_survives_attempted_mutation(self, db):
        run_query(db, "DELETE FROM person")
        after = run_query(db, "SELECT COUNT(*) FROM person")
        assert after.ok
        assert after.rows == [(4,)]


class TestEndToEnd:
    """Execution and comparison together — the actual scoring path."""

    def test_semantically_equivalent_rewrite_scores_as_correct(self, db):
        gold_sql = "SELECT name FROM person WHERE city = 'mumbai'"
        pred_sql = "SELECT p.name FROM person AS p WHERE p.city LIKE 'mumbai'"
        gold = run_query(db, gold_sql)
        pred = run_query(db, pred_sql)
        assert gold.ok and pred.ok
        assert compare_result_sets(gold.rows, pred.rows, ordered=order_matters(gold_sql))

    def test_extra_column_still_answers_the_question(self, db):
        gold = run_query(db, "SELECT name FROM person WHERE age = 25")
        pred = run_query(db, "SELECT name, age FROM person WHERE age = 25")
        assert compare_result_sets(gold.rows, pred.rows)

    def test_wrong_aggregate_is_caught(self, db):
        gold = run_query(db, "SELECT COUNT(*) FROM person WHERE city = 'mumbai'")
        pred = run_query(db, "SELECT COUNT(*) FROM person")
        assert not compare_result_sets(gold.rows, pred.rows)

    def test_ordering_is_enforced_when_gold_orders(self, db):
        gold_sql = "SELECT name FROM person ORDER BY age ASC"
        pred_sql = "SELECT name FROM person ORDER BY age DESC"
        gold = run_query(db, gold_sql)
        pred = run_query(db, pred_sql)
        assert not compare_result_sets(gold.rows, pred.rows, ordered=order_matters(gold_sql))

    def test_missing_join_condition_is_caught(self, db):
        gold = run_query(db, "SELECT COUNT(*) FROM person WHERE age = 25")
        pred = run_query(db, "SELECT COUNT(*) FROM person a, person b WHERE a.age = 25")
        assert not compare_result_sets(gold.rows, pred.rows)
