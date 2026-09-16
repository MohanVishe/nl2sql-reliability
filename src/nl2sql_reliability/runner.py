"""Run each question k times, score every attempt, and write the lot to disk.

The harness exists to make one number trustworthy: the distance between pass@k and pass^k.
Three decisions follow from that.

**Every attempt is recorded, not just the verdict.** The raw reply, the extracted SQL, the
execution error and the token counts all go to disk. A reliability claim that cannot be
re-derived from the artifacts is a claim nobody should believe, including its author.

**Runs are resumable.** A full configuration is thousands of generations over several hours
on a desktop GPU. Results are appended as JSONL and completed `(arm, question_id, run)` keys
are skipped on restart, so a crash or a reboot costs minutes rather than the whole run.

**Work is ordered by `db_id`.** Consecutive questions on one database share a byte-identical
prompt prefix, which is what a provider's prompt cache keys on -- worth about two-thirds of
the token spend on a rate-limited free tier. It costs nothing locally and keeps the local and
hosted arms on the same code path.

Ordering does not bias the sample: attempts are independent draws at temperature > 0, so the
sequence they are made in cannot affect the distribution they come from.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .compare import compare_result_sets, order_matters
from .dataset import Question
from .db import DEFAULT_ROOT, find_database
from .execute import DEFAULT_TIMEOUT_SECONDS, Execution, run_query
from .generate import Generator
from .metrics import Outcome
from .prompt import build_for, extract_sql, repair

DEFAULT_RESULTS = Path("results")


@dataclass(frozen=True)
class Attempt:
    """One question, answered once, scored."""

    arm: str
    question_id: int
    db_id: str
    run: int
    match: bool
    reason: str
    sql: str = ""
    raw: str = ""
    turns: int = 1
    generated: bool = True
    gen_error: str | None = None
    truncated: bool = False
    executed: bool = False
    exec_error: str | None = None
    timed_out: bool = False
    gold_failed: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    gen_seconds: float = 0.0
    exec_seconds: float = 0.0
    at: str = ""

    @property
    def key(self) -> tuple[str, int, int]:
        return (self.arm, self.question_id, self.run)


@dataclass
class Runner:
    """Answers and scores a single question at a time."""

    generator: Generator
    arm: str
    root: Path | str = DEFAULT_ROOT
    max_turns: int = 1
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    keep_raw: bool = True
    _gold: dict[int, Execution] = field(default_factory=dict, repr=False)

    def gold(self, question: Question) -> Execution:
        """The reference result set, executed once and reused across all k attempts."""
        if question.question_id not in self._gold:
            self._gold[question.question_id] = run_query(
                find_database(question.db_id, self.root),
                question.gold_sql,
                timeout_seconds=self.timeout_seconds,
            )
        return self._gold[question.question_id]

    def attempt(self, question: Question, run: int = 0) -> Attempt:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        base = {
            "arm": self.arm,
            "question_id": question.question_id,
            "db_id": question.db_id,
            "run": run,
            "at": now,
        }

        gold = self.gold(question)
        if not gold.ok:
            # The answer key itself does not execute. Scoring this would invent a result;
            # it is flagged and excluded from the metrics instead.
            return Attempt(
                **base, match=False, reason=f"gold query failed: {gold.error}", gold_failed=True
            )

        prompt = build_for(question, self.root)
        path = find_database(question.db_id, self.root)
        ordered = order_matters(question.gold_sql)

        messages = [{"role": "user", "content": prompt.text}]
        sql = ""
        raw = ""
        execution: Execution | None = None
        prompt_tokens = completion_tokens = 0
        gen_seconds = exec_seconds = 0.0
        turns = 0

        for turn in range(1, self.max_turns + 1):
            turns = turn
            reply = self.generator.chat(messages)
            raw = reply.text
            prompt_tokens += reply.prompt_tokens
            completion_tokens += reply.completion_tokens
            gen_seconds += reply.elapsed_seconds

            if not reply.ok:
                reason = (
                    "prompt exceeded the context window" if reply.truncated else "generation failed"
                )
                return Attempt(
                    **base,
                    match=False,
                    reason=reason,
                    raw=raw if self.keep_raw else "",
                    turns=turns,
                    generated=False,
                    gen_error=reply.error,
                    truncated=reply.truncated,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    gen_seconds=gen_seconds,
                )

            sql = extract_sql(reply.text)
            execution = run_query(path, sql, timeout_seconds=self.timeout_seconds)
            exec_seconds += execution.elapsed_seconds

            if execution.ok or turn == self.max_turns:
                break

            # Only execution errors earn a retry. Feeding back "that answer was wrong" would
            # require the gold rows, and a model allowed to converge on the answer key is
            # measuring the harness rather than itself.
            messages.append({"role": "assistant", "content": reply.text})
            messages.append({"role": "user", "content": repair(sql, execution.error or "")})

        assert execution is not None
        if not execution.ok:
            reason = "query timed out" if execution.timed_out else "query failed to execute"
            comparison_match, comparison_reason = False, reason
        else:
            comparison = compare_result_sets(gold.rows, execution.rows, ordered=ordered)
            comparison_match, comparison_reason = comparison.match, comparison.reason

        return Attempt(
            **base,
            match=comparison_match,
            reason=comparison_reason,
            sql=sql,
            raw=raw if self.keep_raw else "",
            turns=turns,
            executed=execution.ok,
            exec_error=execution.error,
            timed_out=execution.timed_out,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            gen_seconds=gen_seconds,
            exec_seconds=exec_seconds,
        )


def work_order(questions: Iterable[Question], k: int) -> list[tuple[Question, int]]:
    """Every (question, run) pair, grouped so one schema is used for a long stretch."""
    ordered = sorted(questions, key=lambda q: (q.db_id, q.question_id))
    return [(question, run) for question in ordered for run in range(k)]


def completed_keys(path: Path | str) -> set[tuple[str, int, int]]:
    """Which attempts a results file already holds.

    Malformed trailing lines are ignored rather than fatal: a run killed mid-write leaves a
    partial last line, and refusing to resume over it would be the wrong lesson to take from
    a half-written byte.
    """
    path = Path(path)
    if not path.exists():
        return set()

    keys: set[tuple[str, int, int]] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                keys.add((record["arm"], int(record["question_id"]), int(record["run"])))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
    return keys


def read_attempts(path: Path | str, arm: str | None = None) -> list[Attempt]:
    """Load recorded attempts back, optionally for one arm."""
    path = Path(path)
    if not path.exists():
        return []

    fields = set(Attempt.__dataclass_fields__)
    attempts: list[Attempt] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if arm is not None and record.get("arm") != arm:
                continue
            try:
                attempts.append(Attempt(**{k: v for k, v in record.items() if k in fields}))
            except TypeError:
                continue
    return attempts


def outcomes(attempts: Iterable[Attempt]) -> list[Outcome]:
    """Collapse attempts into per-question (attempts, correct) pairs for the estimators.

    Questions whose gold query failed are dropped: an unscoreable question would otherwise
    count as a model failure and depress both figures by the same amount, which is precisely
    the annotation problem this dataset was chosen to avoid.
    """
    tally: dict[int, list[int]] = {}
    for attempt in attempts:
        if attempt.gold_failed:
            continue
        counts = tally.setdefault(attempt.question_id, [0, 0])
        counts[0] += 1
        counts[1] += int(attempt.match)
    return [
        Outcome(
            question_id=question_id, attempts=tally[question_id][0], correct=tally[question_id][1]
        )
        for question_id in sorted(tally)
    ]


def run_suite(
    questions: Iterable[Question],
    generator: Generator,
    *,
    k: int,
    arm: str | None = None,
    out_path: Path | str | None = None,
    root: Path | str = DEFAULT_ROOT,
    max_turns: int = 1,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    resume: bool = True,
    on_attempt: Callable[[Attempt, int, int], None] | None = None,
) -> Path:
    """Run every question k times, appending each scored attempt to `out_path`.

    Returns the results path. Safe to call again after a crash: completed attempts are
    skipped.
    """
    questions = list(questions)
    arm = arm or getattr(generator, "name", "unknown")
    out_path = Path(out_path or DEFAULT_RESULTS / f"{_slug(arm)}.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    done = completed_keys(out_path) if resume else set()
    pending = [
        (question, run)
        for question, run in work_order(questions, k)
        if (arm, question.question_id, run) not in done
    ]

    runner = Runner(
        generator=generator,
        arm=arm,
        root=root,
        max_turns=max_turns,
        timeout_seconds=timeout_seconds,
    )

    total = len(pending)
    # Line-buffered and flushed per attempt: the file is the only record of hours of
    # compute, and a buffer lost to a crash is that compute lost with it.
    with out_path.open("a", encoding="utf-8") as handle:
        for index, (question, run) in enumerate(pending, start=1):
            attempt = runner.attempt(question, run)
            handle.write(json.dumps(asdict(attempt), ensure_ascii=False) + "\n")
            handle.flush()
            if on_attempt is not None:
                on_attempt(attempt, index, total)

    return out_path


def progress_printer(every: int = 1) -> Callable[[Attempt, int, int], None]:
    """A simple console reporter for `on_attempt`."""
    started = time.perf_counter()
    correct = [0]

    def report(attempt: Attempt, index: int, total: int) -> None:
        correct[0] += int(attempt.match)
        if index % every and index != total:
            return
        elapsed = time.perf_counter() - started
        rate = index / elapsed if elapsed else 0.0
        remaining = (total - index) / rate if rate else 0.0
        print(
            f"[{index:>5}/{total}] {attempt.db_id:<24} q{attempt.question_id:<5} "
            f"run {attempt.run}  {'ok ' if attempt.match else '   '}"
            f"acc {correct[0] / index:>5.1%}  {rate:>4.2f}/s  eta {remaining / 60:>5.1f}m",
            flush=True,
        )

    return report


def _slug(text: str) -> str:
    return "".join(character if character.isalnum() else "-" for character in text).strip("-")


def stream_attempts(path: Path | str) -> Iterator[dict]:
    """Yield raw records from a results file, for analysis that does not need the dataclass."""
    path = Path(path)
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
