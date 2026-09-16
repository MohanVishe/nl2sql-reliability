"""Tests for dataset loading and subset selection.

Tests touching the network are marked so the suite stays runnable offline.
"""

from __future__ import annotations

import pytest

from nl2sql_reliability.dataset import (
    EXPECTED_DATABASES,
    EXPECTED_ITEMS,
    Question,
    databases,
    stratified_subset,
)


def make_questions(counts: dict[str, int]) -> list[Question]:
    questions: list[Question] = []
    qid = 0
    for db_id, n in counts.items():
        for _ in range(n):
            questions.append(
                Question(
                    question_id=qid,
                    question=f"q{qid}",
                    evidence="",
                    gold_sql="SELECT 1",
                    db_id=db_id,
                )
            )
            qid += 1
    return questions


@pytest.fixture
def sample():
    return make_questions({"alpha": 200, "beta": 150, "gamma": 100, "delta": 40, "eps": 8})


class TestQuestion:
    def test_from_raw(self):
        q = Question.from_raw(
            {
                "question_id": 7,
                "question": "How many?",
                "evidence": "hint",
                "SQL": "SELECT COUNT(*) FROM t",
                "db_id": "shop",
            }
        )
        assert q.question_id == 7
        assert q.gold_sql == "SELECT COUNT(*) FROM t"

    def test_null_evidence_becomes_empty_string(self):
        q = Question.from_raw(
            {
                "question_id": 1,
                "question": "q",
                "evidence": None,
                "SQL": "SELECT 1",
                "db_id": "d",
            }
        )
        assert q.evidence == ""

    def test_missing_field_is_rejected(self):
        with pytest.raises(ValueError, match="missing fields"):
            Question.from_raw({"question_id": 1, "question": "q"})


class TestStratifiedSubset:
    def test_exact_size(self, sample):
        assert len(stratified_subset(sample, 150)) == 150

    def test_every_database_represented(self, sample):
        subset = stratified_subset(sample, 100)
        assert set(q.db_id for q in subset) == set(q.db_id for q in sample)

    def test_smallest_database_survives(self, sample):
        # A database with 8 of 498 questions rounds to zero under naive proportional
        # sampling. Losing it would make subset results incomparable to the full run.
        subset = stratified_subset(sample, 50)
        assert any(q.db_id == "eps" for q in subset)

    def test_deterministic_for_a_seed(self, sample):
        a = [q.question_id for q in stratified_subset(sample, 80, seed=42)]
        b = [q.question_id for q in stratified_subset(sample, 80, seed=42)]
        assert a == b

    def test_different_seeds_differ(self, sample):
        a = [q.question_id for q in stratified_subset(sample, 80, seed=1)]
        b = [q.question_id for q in stratified_subset(sample, 80, seed=2)]
        assert a != b

    def test_oversized_request_returns_everything(self, sample):
        assert len(stratified_subset(sample, 10_000)) == len(sample)

    def test_zero_is_rejected(self, sample):
        with pytest.raises(ValueError):
            stratified_subset(sample, 0)

    def test_no_duplicates(self, sample):
        subset = stratified_subset(sample, 200)
        ids = [q.question_id for q in subset]
        assert len(ids) == len(set(ids))

    @pytest.mark.parametrize("size", [5, 11, 37, 50, 99, 150, 213, 333, 497])
    def test_size_is_exact_at_every_scale(self, sample, size):
        # Per-database rounding lands near the target, not on it, in either direction.
        # This caught a real off-by-one (149 returned for 150).
        assert len(stratified_subset(sample, size)) == size

    @pytest.mark.parametrize("size", [11, 50, 150, 333])
    def test_coverage_holds_at_every_scale(self, sample, size):
        subset = stratified_subset(sample, size)
        assert set(q.db_id for q in subset) == set(q.db_id for q in sample)


class TestDatabaseCounts:
    def test_counts_per_database(self, sample):
        counts = databases(sample)
        assert counts["alpha"] == 200
        assert counts["eps"] == 8


@pytest.mark.network
class TestRealDataset:
    """Guards the published numbers: if upstream changes, results stop being reproducible."""

    def test_loads_expected_shape(self, tmp_path):
        from nl2sql_reliability.dataset import load

        questions = load(tmp_path / "arcwise.json")
        assert len(questions) == EXPECTED_ITEMS
        assert len({q.db_id for q in questions}) == EXPECTED_DATABASES
        assert all(q.gold_sql.strip() for q in questions)
