"""The cross-arm comparison is where a reader looks for the answer, so its alignment rules
need to hold even when the arms are ragged -- which, after an interrupted run, they are."""

from __future__ import annotations

import pytest

from nl2sql_reliability.runner import Attempt

compare_arms = pytest.importorskip("compare_arms")


def attempt(arm: str, question_id: int, run: int, *, match: bool = True, **kwargs) -> Attempt:
    return Attempt(
        arm=arm,
        question_id=question_id,
        db_id=kwargs.pop("db_id", "california_schools"),
        run=run,
        match=match,
        match_strict=kwargs.pop("match_strict", match),
        reason="",
        **kwargs,
    )


def spread(arm: str, question_id: int, runs: int, correct: int, **kwargs) -> list[Attempt]:
    """`runs` attempts on one question, the first `correct` of them right."""
    return [attempt(arm, question_id, r, match=r < correct, **kwargs) for r in range(runs)]


class TestOutcomesByQuestion:
    def test_counts_matches(self):
        by_q = compare_arms.outcomes_by_question(spread("a", 1, 10, 4), strict=False)
        assert by_q[1].attempts == 10
        assert by_q[1].correct == 4

    def test_strict_uses_the_strict_column(self):
        attempts = [
            attempt("a", 1, 0, match=True, match_strict=True),
            attempt("a", 1, 1, match=True, match_strict=False),
        ]
        assert compare_arms.outcomes_by_question(attempts, strict=False)[1].correct == 2
        assert compare_arms.outcomes_by_question(attempts, strict=True)[1].correct == 1

    def test_unrunnable_gold_is_dropped_entirely(self):
        # Not "counted as wrong" -- a question whose answer key does not execute would
        # depress both arms equally and look like agreement rather than an absent datum.
        attempts = spread("a", 1, 3, 1) + [attempt("a", 2, 0, match=False, gold_failed=True)]
        by_q = compare_arms.outcomes_by_question(attempts, strict=False)
        assert set(by_q) == {1}


class TestAlign:
    def test_keeps_only_questions_every_arm_answered(self):
        grouped = {
            "a": spread("a", 1, 4, 2) + spread("a", 2, 4, 2),
            "b": spread("b", 1, 4, 3),
        }
        views, _ = compare_arms.align(grouped, k=None, strict=False)
        for view in views:
            assert [o.question_id for o in view.outcomes] == [1]

    def test_depth_is_the_shallowest_arm(self):
        # Arm b was stopped after 3 repetitions. Comparing at k=4 would ask arm b to
        # estimate four-sample behaviour from three samples, which is not defined.
        grouped = {"a": spread("a", 1, 10, 5), "b": spread("b", 1, 3, 2)}
        _, depth = compare_arms.align(grouped, k=None, strict=False)
        assert depth == 3

    def test_k_can_lower_the_depth_but_not_raise_it(self):
        grouped = {"a": spread("a", 1, 10, 5), "b": spread("b", 1, 4, 2)}
        assert compare_arms.align(grouped, k=2, strict=False)[1] == 2
        assert compare_arms.align(grouped, k=99, strict=False)[1] == 4

    def test_each_arm_keeps_its_own_repetition_count(self):
        grouped = {"a": spread("a", 1, 10, 5), "b": spread("b", 1, 4, 2)}
        views, _ = compare_arms.align(grouped, k=None, strict=False)
        assert sorted(v.outcomes[0].attempts for v in views) == [4, 10]

    def test_no_shared_questions_is_reported_not_crashed(self):
        grouped = {"a": spread("a", 1, 3, 1), "b": spread("b", 2, 3, 1)}
        views, depth = compare_arms.align(grouped, k=None, strict=False)
        assert views == [] and depth == 0


class TestBootstrap:
    def test_a_constant_difference_has_no_spread(self):
        low, high = compare_arms.bootstrap_interval([0.25] * 50, samples=200)
        assert low == high == pytest.approx(0.25)

    def test_interval_brackets_the_mean(self):
        paired = [0.1 * i for i in range(-10, 11)]
        mean = sum(paired) / len(paired)
        low, high = compare_arms.bootstrap_interval(paired, samples=2000)
        assert low <= mean <= high

    def test_is_seeded_so_the_number_is_reproducible(self):
        paired = [0.3, -0.1, 0.0, 0.7, -0.4]
        assert compare_arms.bootstrap_interval(paired, samples=500) == (
            compare_arms.bootstrap_interval(paired, samples=500)
        )

    def test_empty_input_does_not_divide_by_zero(self):
        assert compare_arms.bootstrap_interval([], samples=10) == (0.0, 0.0)


class TestEndToEndPrinting:
    def test_two_arms_print_without_error(self, capsys):
        grouped = {
            "single": spread("single", 1, 5, 2) + spread("single", 2, 5, 5),
            "agentic": spread("agentic", 1, 5, 4) + spread("agentic", 2, 5, 5),
        }
        # Compared at k=2: at k=5 neither 2/5 nor 4/5 clears the bar, both score zero
        # reliability, and the improvement this test is about would be invisible.
        views, depth = compare_arms.align(grouped, k=2, strict=False)
        compare_arms.print_table(views, depth)
        compare_arms.print_differences(views, "single", depth)
        compare_arms.print_cost(views, "single")
        out = capsys.readouterr().out
        assert "agentic against single" in out
        # q1 went 2/5 -> 4/5 and q2 was already perfect, so exactly one question improves.
        assert "improved on 1 questions, worsened on 0" in out

    def test_a_missing_baseline_says_so_rather_than_raising(self, capsys):
        grouped = {"a": spread("a", 1, 3, 1), "b": spread("b", 1, 3, 2)}
        views, depth = compare_arms.align(grouped, k=None, strict=False)
        compare_arms.print_differences(views, "nonexistent", depth)
        assert "not among the arms" in capsys.readouterr().out
