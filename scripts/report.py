"""Turn recorded attempts into the tables the write-up quotes.

    uv run python scripts/report.py
    uv run python scripts/report.py results/local-7b.jsonl --k 5
    uv run python scripts/report.py results/*.jsonl --by-database

Reads only what is on disk. Every figure printed here can be re-derived from the JSONL by
anyone who doubts it, which is the point of recording every attempt rather than a verdict.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from nl2sql_reliability.metrics import summarize, sweep  # noqa: E402
from nl2sql_reliability.runner import Attempt, outcomes, read_attempts  # noqa: E402

DEFAULT_RESULTS = Path("results")


def arms(attempts: list[Attempt]) -> dict[str, list[Attempt]]:
    grouped: dict[str, list[Attempt]] = defaultdict(list)
    for attempt in attempts:
        grouped[attempt.arm].append(attempt)
    return dict(grouped)


def print_sweep(name: str, attempts: list[Attempt], max_k: int | None) -> None:
    scored = outcomes(attempts)
    if not scored:
        print(f"{name}: nothing scoreable")
        return

    complete = min(outcome.attempts for outcome in scored)
    ragged = complete != max(outcome.attempts for outcome in scored)

    print(f"== {name}")
    print(f"   {len(scored)} questions, {complete} complete repetitions each", end="")
    if ragged:
        # An interrupted run leaves some questions with more attempts than others. The
        # estimators handle it, but a table whose rows rest on different n is worth flagging.
        print("  (ragged: some questions have more)", end="")
    print()

    print(f"   {'k':>3} {'pass@k':>8} {'pass^k':>8} {'gap':>8}")
    for suite in sweep(scored, max_k=max_k or complete):
        print(
            f"   {suite.k:>3} {suite.pass_at_k:>7.1%} {suite.pass_hat_k:>7.1%} "
            f"{suite.reliability_gap:>7.1%}"
        )
    print()


def print_failures(name: str, attempts: list[Attempt]) -> None:
    """Where the misses come from -- wrong SQL is not the same failure as no SQL."""
    scoreable = [a for a in attempts if not a.gold_failed]
    if not scoreable:
        return

    buckets: Counter[str] = Counter()
    for attempt in scoreable:
        if attempt.match:
            buckets["correct"] += 1
        elif not attempt.generated:
            buckets["generation failed" if not attempt.truncated else "prompt truncated"] += 1
        elif attempt.timed_out:
            buckets["query timed out"] += 1
        elif not attempt.executed:
            buckets["SQL did not execute"] += 1
        elif not attempt.sql:
            buckets["no SQL in reply"] += 1
        else:
            buckets["ran, wrong rows"] += 1

    total = sum(buckets.values())
    print(f"== {name}: outcome of {total} attempts")
    for label, count in buckets.most_common():
        print(f"   {label:<24} {count:>6} {count / total:>7.1%}")

    excluded = len(attempts) - len(scoreable)
    if excluded:
        print(f"   {'(gold unrunnable)':<24} {excluded:>6}  excluded from all figures")
    print()


def print_strictness(name: str, attempts: list[Attempt]) -> None:
    """How much of the score rests on accepting extra projected columns.

    Execution accuracy has to decide whether a query that answers the question and also
    returns the column it grouped by counts as correct. This prints the size of that
    decision instead of leaving a reader to wonder which way it went.
    """
    scoreable = [a for a in attempts if not a.gold_failed]
    if not scoreable:
        return
    lenient = sum(a.match for a in scoreable)
    strict = sum(a.match_strict for a in scoreable)
    if not lenient:
        return

    print(f"== {name}: effect of accepting extra columns")
    print(f"   lenient matches         {lenient:>6} {lenient / len(scoreable):>7.1%}")
    print(f"   strict matches          {strict:>6} {strict / len(scoreable):>7.1%}")
    share = (lenient - strict) / lenient
    print(f"   accepted on leniency    {lenient - strict:>6} {share:>7.1%} of matches")
    print()


def print_flakiest(name: str, attempts: list[Attempt], limit: int) -> None:
    """Questions the model got right sometimes and wrong other times.

    These are the whole finding. A question always right or always wrong contributes nothing
    to the gap between pass@k and pass^k; the split ones are where reliability is lost.
    """
    scored = outcomes([a for a in attempts if not a.gold_failed])
    flaky = [o for o in scored if 0 < o.correct < o.attempts]
    if not flaky:
        return

    flaky.sort(key=lambda o: abs(o.correct / o.attempts - 0.5))
    print(
        f"== {name}: {len(flaky)} of {len(scored)} questions are inconsistent "
        f"({len(flaky) / len(scored):.1%})"
    )
    for outcome in flaky[:limit]:
        print(f"   q{outcome.question_id:<6} {outcome.correct}/{outcome.attempts} correct")
    if len(flaky) > limit:
        print(f"   ... and {len(flaky) - limit} more")
    print()


def print_by_database(name: str, attempts: list[Attempt], k: int) -> None:
    grouped: dict[str, list[Attempt]] = defaultdict(list)
    for attempt in attempts:
        grouped[attempt.db_id].append(attempt)

    print(f"== {name}: by database, k={k}")
    print(f"   {'database':<26} {'qs':>4} {'pass@k':>8} {'pass^k':>8} {'gap':>8}")
    rows = []
    for db_id, group in grouped.items():
        scored = outcomes(group)
        if not scored or min(o.attempts for o in scored) < k:
            continue
        suite = summarize(scored, k)
        rows.append((suite.reliability_gap, db_id, len(scored), suite))
    for gap, db_id, count, suite in sorted(rows, reverse=True):
        print(
            f"   {db_id:<26} {count:>4} {suite.pass_at_k:>7.1%} "
            f"{suite.pass_hat_k:>7.1%} {gap:>7.1%}"
        )
    print()


def print_cost(name: str, attempts: list[Attempt]) -> None:
    generated = [a for a in attempts if a.generated and not a.gold_failed]
    if not generated:
        return
    gen_seconds = sum(a.gen_seconds for a in generated)
    prompt_tokens = sum(a.prompt_tokens for a in generated)
    completion_tokens = sum(a.completion_tokens for a in generated)
    turns = sum(a.turns for a in generated)

    print(f"== {name}: cost")
    print(f"   attempts            {len(generated):>10,}")
    print(f"   turns               {turns:>10,}  ({turns / len(generated):.2f} per attempt)")
    print(f"   prompt tokens       {prompt_tokens:>10,}")
    print(f"   completion tokens   {completion_tokens:>10,}")
    print(f"   generation time     {gen_seconds / 3600:>10.2f} h")
    if gen_seconds:
        print(f"   throughput          {completion_tokens / gen_seconds:>10.1f} tok/s")
        print(f"   per attempt         {gen_seconds / len(generated):>10.1f} s")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="results files (default: everything in results/)")
    parser.add_argument(
        "--k", type=int, default=None, help="cap the sweep, and set k for --by-database"
    )
    parser.add_argument("--by-database", action="store_true")
    parser.add_argument(
        "--flaky", type=int, default=10, help="how many inconsistent questions to list"
    )
    parser.add_argument("--arm", default=None, help="restrict to one arm")
    args = parser.parse_args()

    paths = [Path(p) for p in args.paths] or sorted(DEFAULT_RESULTS.glob("*.jsonl"))
    if not paths:
        print("no results found; run scripts/run_arm.py first", file=sys.stderr)
        return 1

    attempts: list[Attempt] = []
    for path in paths:
        attempts.extend(read_attempts(path, arm=args.arm))
    if not attempts:
        print("results files held no attempts", file=sys.stderr)
        return 1

    for name, group in sorted(arms(attempts).items()):
        print_sweep(name, group, args.k)
        print_failures(name, group)
        print_strictness(name, group)
        print_flakiest(name, group, args.flaky)
        print_cost(name, group)
        if args.by_database:
            scored = outcomes(group)
            k = args.k or (min(o.attempts for o in scored) if scored else 1)
            print_by_database(name, group, k)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
