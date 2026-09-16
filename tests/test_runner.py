"""Tests for the k-repetition harness.

The harness is where a quiet bug does the most damage: it would not crash, it would produce
a plausible number. So these tests check the properties the headline figure rests on --
that resumption neither duplicates nor drops work, that an unscoreable question is excluded
rather than counted wrong, and that the agentic arm retries on execution errors only.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from nl2sql_reliability.dataset import Question
from nl2sql_reliability.generate import Reply, StubGenerator
from nl2sql_reliability.metrics import Outcome, summarize
from nl2sql_reliability.runner import (
    Attempt,
    Runner,
    completed_keys,
    outcomes,
    read_attempts,
    run_suite,
    work_order,
)


@pytest.fixture
def root(tmp_path):
    """A tiny BIRD-shaped data directory with one database."""
    folder = tmp_path / "dev_databases" / "shop"
    folder.mkdir(parents=True)
    connection = sqlite3.connect(folder / "shop.sqlite")
    connection.executescript(
        "CREATE TABLE customer (id INTEGER PRIMARY KEY, name TEXT, city TEXT);"
        "INSERT INTO customer VALUES (1, 'alice', 'mumbai'), (2, 'bob', 'pune');"
    )
    connection.commit()
    connection.close()

    from nl2sql_reliability.db import clear_cache

    clear_cache()
    yield tmp_path
    clear_cache()


@pytest.fixture
def question():
    return Question(
        question_id=1,
        question="How many customers are there?",
        evidence="",
        gold_sql="SELECT COUNT(*) FROM customer",
        db_id="shop",
    )


def fenced(sql: str) -> str:
    return f"```sql\n{sql}\n```"


class TestAttempt:
    def test_correct_sql_scores_as_a_match(self, root, question):
        generator = StubGenerator(replies=[fenced("SELECT COUNT(*) FROM customer")])
        attempt = Runner(generator, arm="test", root=root).attempt(question)
        assert attempt.match
        assert attempt.executed
        assert attempt.turns == 1
        assert attempt.sql == "SELECT COUNT(*) FROM customer"

    def test_wrong_sql_scores_as_a_miss(self, root, question):
        generator = StubGenerator(replies=[fenced("SELECT 99")])
        attempt = Runner(generator, arm="test", root=root).attempt(question)
        assert not attempt.match
        assert attempt.executed  # it ran; it was simply the wrong answer

    def test_invalid_sql_is_recorded_as_an_execution_failure(self, root, question):
        generator = StubGenerator(replies=[fenced("SELECT * FROM missing_table")])
        attempt = Runner(generator, arm="test", root=root).attempt(question)
        assert not attempt.match
        assert not attempt.executed
        assert "missing_table" in (attempt.exec_error or "")

    def test_reply_with_no_sql_is_a_miss_not_a_crash(self, root, question):
        generator = StubGenerator(replies=["I cannot help with that."])
        attempt = Runner(generator, arm="test", root=root).attempt(question)
        assert not attempt.match
        assert attempt.sql == ""

    def test_metadata_is_carried_through(self, root, question):
        generator = StubGenerator(replies=[fenced("SELECT COUNT(*) FROM customer")])
        attempt = Runner(generator, arm="local-7b", root=root).attempt(question, run=3)
        assert attempt.arm == "local-7b"
        assert attempt.question_id == 1
        assert attempt.db_id == "shop"
        assert attempt.run == 3
        assert attempt.at  # timestamped

    def test_raw_reply_can_be_dropped(self, root, question):
        generator = StubGenerator(replies=[fenced("SELECT COUNT(*) FROM customer")])
        attempt = Runner(generator, arm="test", root=root, keep_raw=False).attempt(question)
        assert attempt.raw == ""
        assert attempt.sql  # the SQL itself is always kept

    def test_generation_failure_is_distinguished_from_a_wrong_answer(self, root, question):
        class Broken:
            name = "broken"

            def chat(self, messages):
                return Reply(text="", error="connection refused")

            def complete(self, prompt):
                return self.chat([])

        attempt = Runner(Broken(), arm="test", root=root).attempt(question)
        assert not attempt.match
        assert not attempt.generated
        assert attempt.gen_error == "connection refused"

    def test_truncated_prompt_is_not_scored_as_a_model_error(self, root, question):
        class Truncating:
            name = "truncating"

            def chat(self, messages):
                return Reply(text=fenced("SELECT 1"), prompt_tokens=8192, truncated=True)

            def complete(self, prompt):
                return self.chat([])

        attempt = Runner(Truncating(), arm="test", root=root).attempt(question)
        assert attempt.truncated
        assert not attempt.generated
        assert "context window" in attempt.reason


class TestGoldHandling:
    def test_gold_is_executed_once_across_repetitions(self, root, question):
        generator = StubGenerator(replies=[fenced("SELECT COUNT(*) FROM customer")])
        runner = Runner(generator, arm="test", root=root)
        for run in range(5):
            runner.attempt(question, run)
        assert len(runner._gold) == 1

    def test_unrunnable_gold_is_flagged_not_scored(self, root):
        broken = Question(
            question_id=7,
            question="?",
            evidence="",
            gold_sql="SELECT * FROM does_not_exist",
            db_id="shop",
        )
        generator = StubGenerator(replies=[fenced("SELECT COUNT(*) FROM customer")])
        attempt = Runner(generator, arm="test", root=root).attempt(broken)
        assert attempt.gold_failed
        assert not attempt.match
        assert not generator.calls  # no point spending a generation on it


class TestAgenticLoop:
    def test_single_turn_by_default(self, root, question):
        generator = StubGenerator(replies=[fenced("SELECT * FROM missing_table")])
        attempt = Runner(generator, arm="test", root=root).attempt(question)
        assert attempt.turns == 1
        assert len(generator.calls) == 1

    def test_execution_error_triggers_a_repair_turn(self, root, question):
        generator = StubGenerator(
            replies=[fenced("SELECT * FROM missing_table"), fenced("SELECT COUNT(*) FROM customer")]
        )
        attempt = Runner(generator, arm="test", root=root, max_turns=3).attempt(question)
        assert attempt.match
        assert attempt.turns == 2

    def test_repair_turn_is_told_the_error(self, root, question):
        generator = StubGenerator(
            replies=[fenced("SELECT * FROM missing_table"), fenced("SELECT COUNT(*) FROM customer")]
        )
        Runner(generator, arm="test", root=root, max_turns=3).attempt(question)
        follow_up = generator.calls[1][-1]
        assert follow_up["role"] == "user"
        assert "missing_table" in follow_up["content"]

    def test_a_wrong_but_valid_query_is_not_retried(self, root, question):
        # Retrying on a wrong result would require the gold rows. A model steered by the
        # answer key is not being measured.
        generator = StubGenerator(replies=[fenced("SELECT 99")])
        attempt = Runner(generator, arm="test", root=root, max_turns=3).attempt(question)
        assert attempt.turns == 1
        assert not attempt.match

    def test_turns_are_capped(self, root, question):
        generator = StubGenerator(replies=[fenced("SELECT * FROM missing_table")])
        attempt = Runner(generator, arm="test", root=root, max_turns=3).attempt(question)
        assert attempt.turns == 3
        assert len(generator.calls) == 3

    def test_tokens_accumulate_across_turns(self, root, question):
        generator = StubGenerator(replies=[fenced("SELECT * FROM missing_table")])
        one = Runner(generator, arm="test", root=root, max_turns=1).attempt(question)
        many = Runner(
            StubGenerator(replies=[fenced("SELECT * FROM missing_table")]),
            arm="test",
            root=root,
            max_turns=3,
        ).attempt(question)
        assert many.completion_tokens > one.completion_tokens


class TestWorkOrder:
    def _questions(self):
        return [
            Question(3, "c", "", "SELECT 1", "b_db"),
            Question(1, "a", "", "SELECT 1", "a_db"),
            Question(2, "b", "", "SELECT 1", "b_db"),
        ]

    def test_every_pair_appears_once(self):
        pairs = work_order(self._questions(), k=4)
        assert len(pairs) == 12
        assert len({(q.question_id, run) for q, run in pairs}) == 12

    def test_grouped_by_database(self):
        # Consecutive work on one schema is what makes a prompt cache hit.
        ids = [q.db_id for q, _ in work_order(self._questions(), k=2)]
        assert ids == sorted(ids)

    def test_repetitions_of_a_question_are_adjacent(self):
        pairs = work_order(self._questions(), k=3)
        runs = [run for q, run in pairs if q.question_id == 2]
        assert runs == [0, 1, 2]

    def test_zero_k_is_no_work(self):
        assert work_order(self._questions(), k=0) == []


class TestPersistenceAndResume:
    def test_writes_one_line_per_attempt(self, root, question, tmp_path):
        out = tmp_path / "r.jsonl"
        generator = StubGenerator(replies=[fenced("SELECT COUNT(*) FROM customer")])
        run_suite([question], generator, k=3, arm="test", out_path=out, root=root)
        assert len(out.read_text(encoding="utf-8").strip().splitlines()) == 3

    def test_records_are_valid_json_with_the_expected_fields(self, root, question, tmp_path):
        out = tmp_path / "r.jsonl"
        generator = StubGenerator(replies=[fenced("SELECT COUNT(*) FROM customer")])
        run_suite([question], generator, k=1, arm="test", out_path=out, root=root)
        record = json.loads(out.read_text(encoding="utf-8").strip())
        for field_name in ("arm", "question_id", "db_id", "run", "match", "sql", "at"):
            assert field_name in record

    def test_resume_skips_completed_work(self, root, question, tmp_path):
        out = tmp_path / "r.jsonl"
        first = StubGenerator(replies=[fenced("SELECT COUNT(*) FROM customer")])
        run_suite([question], first, k=3, arm="test", out_path=out, root=root)

        second = StubGenerator(replies=[fenced("SELECT COUNT(*) FROM customer")])
        run_suite([question], second, k=3, arm="test", out_path=out, root=root)
        assert not second.calls  # nothing left to do
        assert len(out.read_text(encoding="utf-8").strip().splitlines()) == 3

    def test_resume_completes_a_partial_run(self, root, question, tmp_path):
        out = tmp_path / "r.jsonl"
        good = fenced("SELECT COUNT(*) FROM customer")
        run_suite(
            [question], StubGenerator(replies=[good]), k=2, arm="test", out_path=out, root=root
        )
        run_suite(
            [question], StubGenerator(replies=[good]), k=5, arm="test", out_path=out, root=root
        )
        attempts = read_attempts(out)
        assert len(attempts) == 5
        assert sorted(a.run for a in attempts) == [0, 1, 2, 3, 4]

    def test_resume_can_be_disabled(self, root, question, tmp_path):
        out = tmp_path / "r.jsonl"
        good = fenced("SELECT COUNT(*) FROM customer")
        run_suite(
            [question], StubGenerator(replies=[good]), k=2, arm="test", out_path=out, root=root
        )
        run_suite(
            [question],
            StubGenerator(replies=[good]),
            k=2,
            arm="test",
            out_path=out,
            root=root,
            resume=False,
        )
        assert len(read_attempts(out)) == 4

    def test_arms_do_not_collide_in_one_file(self, root, question, tmp_path):
        out = tmp_path / "r.jsonl"
        good = fenced("SELECT COUNT(*) FROM customer")
        run_suite([question], StubGenerator(replies=[good]), k=2, arm="a", out_path=out, root=root)
        run_suite([question], StubGenerator(replies=[good]), k=2, arm="b", out_path=out, root=root)
        assert len(read_attempts(out)) == 4
        assert len(read_attempts(out, arm="a")) == 2

    def test_a_truncated_final_line_does_not_block_resume(self, tmp_path):
        out = tmp_path / "r.jsonl"
        out.write_text(
            json.dumps({"arm": "test", "question_id": 1, "run": 0}) + '\n{"arm": "tes',
            encoding="utf-8",
        )
        assert completed_keys(out) == {("test", 1, 0)}

    def test_missing_file_has_no_completed_work(self, tmp_path):
        assert completed_keys(tmp_path / "absent.jsonl") == set()
        assert read_attempts(tmp_path / "absent.jsonl") == []

    def test_default_path_is_derived_from_the_arm(self, root, question, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        generator = StubGenerator(replies=[fenced("SELECT COUNT(*) FROM customer")])
        path = run_suite([question], generator, k=1, arm="ollama/qwen2.5-coder:7b", root=root)
        assert path.exists()
        assert path.parent.name == "results"
        assert "/" not in path.name and ":" not in path.name


class TestOutcomes:
    def test_counts_attempts_and_successes_per_question(self):
        attempts = [
            Attempt(arm="a", question_id=1, db_id="d", run=r, match=r < 2, reason="")
            for r in range(4)
        ]
        assert outcomes(attempts) == [Outcome(question_id=1, attempts=4, correct=2)]

    def test_unscoreable_questions_are_excluded(self):
        attempts = [
            Attempt(arm="a", question_id=1, db_id="d", run=0, match=True, reason=""),
            Attempt(
                arm="a", question_id=2, db_id="d", run=0, match=False, reason="", gold_failed=True
            ),
        ]
        result = outcomes(attempts)
        assert [o.question_id for o in result] == [1]

    def test_feeds_the_estimators(self, root, question, tmp_path):
        out = tmp_path / "r.jsonl"
        generator = StubGenerator(replies=[fenced("SELECT COUNT(*) FROM customer")])
        run_suite([question], generator, k=4, arm="test", out_path=out, root=root)
        suite = summarize(outcomes(read_attempts(out)), k=2)
        # A generator that is right every time has no gap between capability and reliability.
        assert suite.pass_at_k == pytest.approx(1.0)
        assert suite.pass_hat_k == pytest.approx(1.0)
        assert suite.reliability_gap == pytest.approx(0.0)

    def test_a_flaky_generator_shows_a_gap(self):
        # Right half the time: it can answer, but it does not answer every time.
        attempts = [
            Attempt(arm="a", question_id=q, db_id="d", run=r, match=(r % 2 == 0), reason="")
            for q in range(10)
            for r in range(4)
        ]
        suite = summarize(outcomes(attempts), k=2)
        assert suite.pass_at_k > suite.pass_hat_k
        assert suite.reliability_gap > 0


class TestProgress:
    def test_reporter_is_called_once_per_attempt(self, root, question, tmp_path):
        seen = []
        generator = StubGenerator(replies=[fenced("SELECT COUNT(*) FROM customer")])
        run_suite(
            [question],
            generator,
            k=3,
            arm="test",
            out_path=tmp_path / "r.jsonl",
            root=root,
            on_attempt=lambda attempt, index, total: seen.append((index, total)),
        )
        assert seen == [(1, 3), (2, 3), (3, 3)]
