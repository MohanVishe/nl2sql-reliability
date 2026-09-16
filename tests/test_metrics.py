"""Tests for the capability and reliability estimators.

These decide the study's headline numbers, so they are checked against hand-computable
cases rather than against themselves.
"""

from __future__ import annotations

import pytest

from nl2sql_reliability.metrics import (
    Outcome,
    pass_at_k,
    pass_hat_k,
    summarize,
    sweep,
)


class TestPassAtK:
    def test_all_correct_is_certain(self):
        assert pass_at_k(10, 10, 5) == 1.0

    def test_none_correct_is_impossible(self):
        assert pass_at_k(10, 0, 5) == 0.0

    def test_k_equals_one_is_the_success_rate(self):
        assert pass_at_k(10, 3, 1) == pytest.approx(0.3)

    def test_more_failures_than_k_is_not_certain(self):
        # 5 of 10 correct, drawing 2: 1 - C(5,2)/C(10,2) = 1 - 10/45
        assert pass_at_k(10, 5, 2) == pytest.approx(1 - 10 / 45)

    def test_fewer_failures_than_k_is_certain(self):
        # Only 2 failures in 10; any 3 drawn must include a success.
        assert pass_at_k(10, 8, 3) == 1.0

    def test_increases_with_k(self):
        values = [pass_at_k(10, 3, k) for k in range(1, 8)]
        assert values == sorted(values)


class TestPassHatK:
    def test_all_correct_is_certain(self):
        assert pass_hat_k(10, 10, 4) == 1.0

    def test_none_correct_is_impossible(self):
        assert pass_hat_k(10, 0, 4) == 0.0

    def test_k_equals_one_is_the_success_rate(self):
        assert pass_hat_k(10, 3, 1) == pytest.approx(0.3)

    def test_fewer_successes_than_k_is_impossible(self):
        assert pass_hat_k(10, 3, 4) == 0.0

    def test_hand_computed(self):
        # 6 of 10 correct, drawing 3: C(6,3)/C(10,3) = 20/120
        assert pass_hat_k(10, 6, 3) == pytest.approx(20 / 120)

    def test_decreases_with_k(self):
        values = [pass_hat_k(10, 8, k) for k in range(1, 9)]
        assert values == sorted(values, reverse=True)

    def test_one_failure_destroys_reliability_at_full_k(self):
        # The point of the metric: 9/10 looks strong, but is never all-of-10.
        assert pass_hat_k(10, 9, 10) == 0.0
        assert pass_at_k(10, 9, 10) == 1.0


class TestUnbiasedVersusNaive:
    """The plug-in estimators are biased in opposite directions, for one shared reason.

    Drawing k of n samples without replacement makes extreme runs rarer than independent
    draws would: once a failure is drawn, fewer failures remain. So all-fail is rarer than
    (1-p)^k and all-succeed is rarer than p^k.
    """

    def test_naive_plug_in_overstates_reliability(self):
        naive = (6 / 10) ** 3
        assert pass_hat_k(10, 6, 3) < naive

    def test_naive_plug_in_understates_capability(self):
        naive = 1 - (1 - 3 / 10) ** 3
        assert pass_at_k(10, 3, 3) > naive

    def test_both_biases_shrink_as_n_grows(self):
        # With replacement is the large-n limit, so the gap closes from both sides.
        small = abs(pass_hat_k(10, 5, 2) - 0.5**2)
        large = abs(pass_hat_k(1000, 500, 2) - 0.5**2)
        assert large < small


class TestValidation:
    def test_k_greater_than_attempts_is_rejected(self):
        with pytest.raises(ValueError, match="exceeds attempts"):
            pass_at_k(5, 3, 6)

    def test_zero_k_is_rejected(self):
        with pytest.raises(ValueError, match="k must be positive"):
            pass_hat_k(5, 3, 0)

    def test_correct_above_attempts_is_rejected(self):
        with pytest.raises(ValueError):
            pass_at_k(5, 6, 2)

    def test_outcome_rejects_impossible_counts(self):
        with pytest.raises(ValueError, match="outside"):
            Outcome(question_id=1, attempts=5, correct=7)

    def test_outcome_rejects_zero_attempts(self):
        with pytest.raises(ValueError, match="attempts must be positive"):
            Outcome(question_id=1, attempts=0, correct=0)


class TestOutcome:
    def test_from_flags(self):
        outcome = Outcome.from_flags(3, [True, False, True, True])
        assert outcome.attempts == 4
        assert outcome.correct == 3


class TestSummarize:
    def test_perfect_suite(self):
        outcomes = [Outcome(i, 10, 10) for i in range(5)]
        result = summarize(outcomes, k=5)
        assert result.pass_at_k == 1.0
        assert result.pass_hat_k == 1.0
        assert result.reliability_gap == 0.0

    def test_gap_appears_with_flaky_questions(self):
        # Every question solvable, none solved dependably.
        outcomes = [Outcome(i, 10, 5) for i in range(20)]
        result = summarize(outcomes, k=5)
        assert result.pass_at_k > 0.9
        assert result.pass_hat_k < 0.01
        assert result.reliability_gap > 0.9

    def test_is_an_unweighted_mean_of_per_question_values(self):
        # A question run more times must contribute no more to the suite score than one
        # run fewer times. Checked against the mean computed directly, rather than by
        # comparing two suites — the estimators legitimately differ with n, so equal
        # weighting cannot be inferred from two suite scores being close.
        outcomes = [Outcome(1, 100, 50), Outcome(2, 10, 10), Outcome(3, 12, 4)]
        result = summarize(outcomes, k=2)
        expected = sum(pass_hat_k(o.attempts, o.correct, 2) for o in outcomes) / len(outcomes)
        assert result.pass_hat_k == pytest.approx(expected)

    def test_duplicating_a_question_does_shift_the_mean(self):
        # Guards the interpretation above: weighting is per question, so the suite score is
        # sensitive to which questions are in it, not to how many times each was run.
        one = summarize([Outcome(1, 10, 10), Outcome(2, 10, 0)], k=2)
        two = summarize([Outcome(1, 10, 10), Outcome(2, 10, 0), Outcome(3, 10, 0)], k=2)
        assert two.pass_hat_k < one.pass_hat_k

    def test_empty_is_rejected(self):
        with pytest.raises(ValueError, match="no outcomes"):
            summarize([], k=1)


class TestSweep:
    def test_covers_every_k_up_to_the_smallest_run(self):
        outcomes = [Outcome(1, 10, 7), Outcome(2, 6, 4)]
        results = sweep(outcomes)
        assert [r.k for r in results] == list(range(1, 7))

    def test_respects_max_k(self):
        outcomes = [Outcome(1, 10, 7)]
        assert [r.k for r in sweep(outcomes, max_k=3)] == [1, 2, 3]

    def test_gap_is_monotonic_in_k(self):
        outcomes = [Outcome(i, 10, 7) for i in range(10)]
        gaps = [r.reliability_gap for r in sweep(outcomes)]
        assert gaps == sorted(gaps)

    def test_k_of_one_has_no_gap(self):
        outcomes = [Outcome(i, 10, 7) for i in range(10)]
        first = sweep(outcomes)[0]
        assert first.k == 1
        assert first.reliability_gap == pytest.approx(0.0)
