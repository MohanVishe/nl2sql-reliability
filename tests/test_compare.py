"""Tests for result-set comparison.

The comparator is the one component that can silently invalidate the entire study: too
strict and correct SQL is scored as failure, inflating the measured variance; too lax and
wrong SQL passes, deflating it. Both directions are tested.
"""

from __future__ import annotations

import pytest

from nl2sql_reliability.compare import (
    Comparison,
    compare_result_sets,
    normalize_cell,
    order_matters,
)


class TestEquivalent:
    """Cases that must be accepted. Rejecting these would inflate measured variance."""

    def test_identical(self):
        rows = [(1, "alice"), (2, "bob")]
        assert compare_result_sets(rows, rows)

    def test_row_order_differs_when_unordered(self):
        gold = [(1, "alice"), (2, "bob")]
        pred = [(2, "bob"), (1, "alice")]
        assert compare_result_sets(gold, pred)

    def test_column_order_differs(self):
        gold = [(1, "alice"), (2, "bob")]
        pred = [("alice", 1), ("bob", 2)]
        assert compare_result_sets(gold, pred)

    def test_extra_column_is_tolerated_by_default(self):
        gold = [("alice",), ("bob",)]
        pred = [("alice", 30), ("bob", 25)]
        assert compare_result_sets(gold, pred)

    def test_int_and_float_are_the_same_value(self):
        assert compare_result_sets([(1,)], [(1.0,)])

    def test_float_noise_below_precision(self):
        assert compare_result_sets([(1.00000001,)], [(1.00000002,)])

    def test_whitespace_around_strings(self):
        assert compare_result_sets([("alice",)], [("  alice  ",)])

    def test_nulls_match(self):
        assert compare_result_sets([(None, 1)], [(None, 1)])

    def test_both_empty(self):
        assert compare_result_sets([], [])

    def test_duplicate_rows_preserved_on_both_sides(self):
        gold = [(1,), (1,), (2,)]
        pred = [(2,), (1,), (1,)]
        assert compare_result_sets(gold, pred)


class TestDifferent:
    """Cases that must be rejected. Accepting these would deflate measured variance."""

    def test_different_values(self):
        assert not compare_result_sets([(1,)], [(2,)])

    def test_row_count_differs(self):
        assert not compare_result_sets([(1,), (2,)], [(1,)])

    def test_duplicates_are_not_collapsed_to_a_set(self):
        # The classic set-comparison bug: same distinct values, different multiplicities.
        gold = [(1,), (1,), (2,)]
        pred = [(1,), (2,), (2,)]
        assert not compare_result_sets(gold, pred)

    def test_row_order_matters_when_gold_is_ordered(self):
        gold = [(1,), (2,)]
        pred = [(2,), (1,)]
        assert not compare_result_sets(gold, pred, ordered=True)

    def test_missing_column(self):
        gold = [(1, "alice")]
        pred = [(1,)]
        assert not compare_result_sets(gold, pred)

    def test_extra_column_rejected_when_strict(self):
        gold = [("alice",)]
        pred = [("alice", 30)]
        assert not compare_result_sets(gold, pred, allow_extra_columns=False)

    def test_null_is_not_zero(self):
        assert not compare_result_sets([(None,)], [(0,)])

    def test_null_is_not_empty_string(self):
        assert not compare_result_sets([(None,)], [("",)])

    def test_empty_against_non_empty(self):
        assert not compare_result_sets([], [(1,)])

    def test_extra_column_cannot_rescue_wrong_values(self):
        gold = [("alice",)]
        pred = [("bob", "alice_id")]
        assert not compare_result_sets(gold, pred)


class TestOrderedMatching:
    def test_same_order_passes_when_ordered(self):
        rows = [(3,), (2,), (1,)]
        assert compare_result_sets(rows, rows, ordered=True)

    def test_column_alignment_still_works_when_ordered(self):
        gold = [(1, "a"), (2, "b")]
        pred = [("a", 1), ("b", 2)]
        assert compare_result_sets(gold, pred, ordered=True)


class TestOrderMattersHeuristic:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT name FROM t ORDER BY age",
            "select x from y order by z desc",
            "SELECT a FROM b\nORDER  BY\n c",
        ],
    )
    def test_detects_order_by(self, sql):
        assert order_matters(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT name FROM t",
            "SELECT COUNT(*) FROM t GROUP BY x",
            "",
        ],
    )
    def test_absent_order_by(self, sql):
        assert not order_matters(sql)

    def test_ordered_word_in_identifier_is_not_a_false_positive(self):
        assert not order_matters("SELECT order_by_column FROM orders")


class TestNormalization:
    def test_booleans_become_numbers(self):
        assert normalize_cell(True) == normalize_cell(1)

    def test_bytes_decoded(self):
        assert normalize_cell(b"alice") == "alice"

    def test_undecodable_bytes_survive(self):
        assert normalize_cell(b"\xff\xfe") == b"\xff\xfe"

    def test_nan_is_stable(self):
        assert normalize_cell(float("nan")) == normalize_cell(float("nan"))


class TestReasons:
    """Failures must say why — the reason strings become the failure taxonomy."""

    def test_reason_is_populated_on_failure(self):
        result = compare_result_sets([(1,), (2,)], [(1,)])
        assert isinstance(result, Comparison)
        assert "row count" in result.reason

    def test_reason_is_populated_on_success(self):
        result = compare_result_sets([(1, 2)], [(2, 1)])
        assert result.match
        assert "alignment" in result.reason

    def test_wide_result_sets_do_not_hang(self):
        # 12 predicted columns against 10 gold is ~2.4e9 permutations; must bail, not spin.
        gold = [tuple(range(10))]
        pred = [tuple(range(12))]
        result = compare_result_sets(gold, pred)
        assert not result.match
        assert "cap" in result.reason
