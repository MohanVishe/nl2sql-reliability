"""The retry summary is the one figure in the report that is arithmetic rather than a
count, so it is the one that can be quietly wrong."""

from __future__ import annotations

import pytest

from nl2sql_reliability.runner import Attempt

report = pytest.importorskip("report")


def attempt(*, turns: int = 1, match: bool = False, executed: bool = False, **kwargs) -> Attempt:
    return Attempt(
        arm="agentic",
        question_id=kwargs.pop("question_id", 1),
        db_id="california_schools",
        run=kwargs.pop("run", 0),
        match=match,
        reason="",
        turns=turns,
        executed=executed,
        **kwargs,
    )


class TestPrintRetries:
    def test_silent_for_an_arm_that_never_retried(self, capsys):
        report.print_retries("single", [attempt(turns=1, match=True, executed=True)])
        assert capsys.readouterr().out == ""

    def test_counts_retries_executions_and_matches(self, capsys):
        attempts = [
            attempt(turns=2, match=True, executed=True, run=0),
            attempt(turns=2, match=False, executed=True, run=1),
            attempt(turns=3, match=False, executed=False, run=2),
            attempt(turns=1, match=True, executed=True, run=3),
        ]
        report.print_retries("agentic", attempts)
        out = capsys.readouterr().out
        assert "attempts that retried        3   75.0%" in out
        assert "...ended up executing        2   66.7% of those" in out
        assert "...ended up correct          1   33.3% of those" in out

    def test_turnaround_share_counts_rescued_attempts_in_its_own_denominator(self, capsys):
        # One rescued, one still wrong. Without the loop both would have missed, so the
        # denominator is 2 and the share is 50% -- not 100%, which is what dividing by the
        # single remaining miss would wrongly give.
        attempts = [
            attempt(turns=2, match=True, executed=True, run=0),
            attempt(turns=2, match=False, executed=True, run=1),
        ]
        report.print_retries("agentic", attempts)
        assert "it fixed  50.0%" in capsys.readouterr().out

    def test_unrunnable_gold_is_excluded(self, capsys):
        attempts = [
            attempt(turns=2, match=True, executed=True, run=0),
            attempt(turns=2, match=False, gold_failed=True, run=1),
        ]
        report.print_retries("agentic", attempts)
        assert "attempts that retried        1  100.0%" in capsys.readouterr().out

    def test_a_failed_generation_is_not_counted_as_a_retry_opportunity(self, capsys):
        attempts = [
            attempt(turns=2, match=True, executed=True, run=0),
            attempt(turns=2, match=False, generated=False, run=1),
        ]
        report.print_retries("agentic", attempts)
        assert "attempts that retried        1  100.0%" in capsys.readouterr().out
