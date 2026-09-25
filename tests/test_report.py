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
        assert "share of all misses the loop fixed    50.0%" in capsys.readouterr().out

    def test_silent_misses_count_in_all_misses_but_not_in_the_reachable_share(self, capsys):
        # The loop fires on execution errors only. A query that runs and returns wrong rows
        # never triggers it, so it belongs in "all misses" and must stay out of the
        # reachable share. The two figures differ exactly when silent misses exist -- which
        # is the case this label once got wrong.
        attempts = [
            attempt(turns=2, match=True, executed=True, run=0),  # reachable, rescued
            attempt(turns=2, match=False, executed=True, run=1),  # reachable, not rescued
            attempt(turns=1, match=False, executed=True, run=2),  # silent: unreachable
            attempt(turns=1, match=False, executed=True, run=3),  # silent: unreachable
        ]
        report.print_retries("agentic", attempts)
        out = capsys.readouterr().out
        assert "...ended up correct          1   50.0% of those" in out  # 1 of 2 reachable
        assert "share of all misses the loop fixed    25.0%" in out  # 1 of 4 misses

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


class TestExclusions:
    def test_a_timed_out_reference_is_labelled_as_a_timeout(self, capsys):
        attempts = [
            attempt(match=True, executed=True, run=0),
            Attempt(
                arm="agentic",
                question_id=2,
                db_id="x",
                run=0,
                match=False,
                reason="gold query failed: query interrupted after 30.0s",
                gold_failed=True,
            ),
        ]
        report.print_failures("agentic", attempts)
        out = capsys.readouterr().out
        assert "(reference timed out)" in out
        assert "(1 question)" in out
        assert "unrunnable" not in out

    def test_any_other_reference_failure_is_labelled_as_a_failure(self, capsys):
        attempts = [
            attempt(match=True, executed=True, run=0),
            Attempt(
                arm="agentic",
                question_id=2,
                db_id="x",
                run=0,
                match=False,
                reason="gold query failed: no such table: t",
                gold_failed=True,
            ),
        ]
        report.print_failures("agentic", attempts)
        assert "(reference query failed)" in capsys.readouterr().out


class TestDefaultPath:
    def test_the_documented_command_finds_the_published_results(self):
        # `uv run python scripts/report.py` with no arguments must work on a fresh clone,
        # where the only results are the committed ones in results/final/.
        assert report.DEFAULT_RESULTS.as_posix() == "results/final"
        assert sorted(report.DEFAULT_RESULTS.glob("*.jsonl")), "no committed results found"
