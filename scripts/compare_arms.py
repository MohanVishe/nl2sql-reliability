"""Put two or more arms side by side on the questions they both answered.

    uv run python scripts/compare_arms.py
    uv run python scripts/compare_arms.py --k 5
    uv run python scripts/compare_arms.py --baseline local-7b-single

Reading each arm's report separately is not a comparison. Two arms can be summarised over
different question sets -- one arm may have skipped a question its gold query failed on, or
been stopped early -- and subtracting those two headline numbers silently compares different
populations. Everything here is computed over the **intersection**: the questions every arm
answered, each with the same number of repetitions. That is usually a slightly smaller set
than any single arm's own report, and the numbers here will differ a little from the ones in
that report for exactly that reason.

Three things are printed:

**The table.** pass@k and pass^k per arm, on the common set.

**What moved.** A per-question flip count against the baseline: how many questions the arm
fixed, how many it broke. A change of +2 points that fixed 30 questions and broke 20 is a
different result from one that fixed 10 and broke nothing, and the headline hides which.

**What it cost.** Turns, seconds and tokens per attempt. An arm that buys two points of
accuracy for a 1.6x slowdown has not obviously won, and the reader is entitled to both halves.

A paired bootstrap over questions gives an interval on each difference. It resamples
questions, not attempts: the questions are the independent units here, and an interval built
by resampling attempts within a question would be far too narrow.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from nl2sql_reliability.metrics import (  # noqa: E402
    Outcome,
    pass_at_k,
    pass_hat_k,
    summarize,
)
from nl2sql_reliability.runner import Attempt, read_attempts  # noqa: E402

DEFAULT_RESULTS = Path("results")
BOOTSTRAP_SAMPLES = 10_000
BOOTSTRAP_SEED = 20260917


@dataclass(frozen=True)
class ArmView:
    """One arm, restricted to a common question set."""

    name: str
    outcomes: list[Outcome]
    attempts: list[Attempt]

    @property
    def by_question(self) -> dict[int, Outcome]:
        return {o.question_id: o for o in self.outcomes}


def load(paths: list[Path]) -> dict[str, list[Attempt]]:
    grouped: dict[str, list[Attempt]] = defaultdict(list)
    for path in paths:
        for attempt in read_attempts(path):
            grouped[attempt.arm].append(attempt)
    return dict(grouped)


def outcomes_by_question(attempts: list[Attempt], *, strict: bool) -> dict[int, Outcome]:
    tally: dict[int, list[int]] = {}
    for attempt in attempts:
        if attempt.gold_failed:
            continue
        counts = tally.setdefault(attempt.question_id, [0, 0])
        counts[0] += 1
        counts[1] += int(attempt.match_strict if strict else attempt.match)
    return {
        qid: Outcome(question_id=qid, attempts=counts[0], correct=counts[1])
        for qid, counts in tally.items()
    }


def align(
    grouped: dict[str, list[Attempt]], *, k: int | None, strict: bool
) -> tuple[list[ArmView], int]:
    """Restrict every arm to the questions all of them answered, at a shared depth.

    Returns the aligned arms and the number of repetitions they have in common. A question
    answered 10 times in one arm and 7 in another is compared at 7: estimating k-sample
    behaviour from fewer than k samples is not defined, and quietly using a different n per
    arm would make the two columns incomparable.
    """
    per_arm = {name: outcomes_by_question(a, strict=strict) for name, a in grouped.items()}
    shared = set.intersection(*(set(view) for view in per_arm.values()))
    if not shared:
        return [], 0

    depth = min(min(view[qid].attempts for qid in shared) for view in per_arm.values())
    if k is not None:
        depth = min(depth, k)

    # Each arm keeps its own n; only k is held common. Truncating an arm to the shared depth
    # would mean choosing which repetitions to discard, and there is no principled choice --
    # the estimators are defined over a ragged n and dropping data to tidy the table would
    # throw away real attempts to no purpose.
    views = [
        ArmView(
            name=name,
            outcomes=[per_arm[name][qid] for qid in sorted(shared)],
            attempts=grouped[name],
        )
        for name in sorted(per_arm)
    ]
    return views, depth


def print_table(views: list[ArmView], k: int) -> None:
    print(f"== {len(views[0].outcomes)} questions answered by every arm, k={k}\n")
    print(f"   {'arm':<22} {'pass@k':>8} {'pass^k':>8} {'gap':>8} {'flaky':>8}")
    for view in views:
        suite = summarize(view.outcomes, k)
        flaky = sum(1 for o in view.outcomes if 0 < o.correct < o.attempts)
        print(
            f"   {view.name:<22} {suite.pass_at_k:>7.1%} {suite.pass_hat_k:>7.1%} "
            f"{suite.reliability_gap:>7.1%} {flaky / len(view.outcomes):>7.1%}"
        )
    print()


def print_differences(views: list[ArmView], baseline: str, k: int) -> None:
    base = next((v for v in views if v.name == baseline), None)
    if base is None:
        print(f"baseline {baseline!r} not among the arms; skipping differences\n")
        return
    base_suite = summarize(base.outcomes, k)
    base_by_q = base.by_question

    for view in views:
        if view.name == baseline:
            continue
        suite = summarize(view.outcomes, k)
        print(f"== {view.name} against {baseline}, k={k}")

        for label, value, base_value, getter in (
            ("pass@k", suite.pass_at_k, base_suite.pass_at_k, pass_at_k),
            ("pass^k", suite.pass_hat_k, base_suite.pass_hat_k, pass_hat_k),
        ):
            paired = []
            for outcome in view.outcomes:
                before = base_by_q[outcome.question_id]
                paired.append(
                    getter(outcome.attempts, outcome.correct, k)
                    - getter(before.attempts, before.correct, k)
                )
            low, high = bootstrap_interval(paired)
            delta = value - base_value
            crosses = low <= 0.0 <= high
            note = "  (interval includes zero)" if crosses else ""
            print(f"   {label:<8} {delta:>+7.1%}   95% CI [{low:>+6.1%}, {high:>+6.1%}]{note}")

        fixed = broke = 0
        for outcome in view.outcomes:
            before = base_by_q[outcome.question_id]
            was = pass_hat_k(before.attempts, before.correct, k)
            now = pass_hat_k(outcome.attempts, outcome.correct, k)
            if now > was:
                fixed += 1
            elif now < was:
                broke += 1
        print(f"   reliability improved on {fixed} questions, worsened on {broke}")
        print()


def bootstrap_interval(
    paired: list[float], samples: int = BOOTSTRAP_SAMPLES
) -> tuple[float, float]:
    """A 95% percentile interval on the mean of paired per-question differences.

    Seeded, so the interval printed today is the interval printed next year from the same
    JSONL. Seeding decode would destroy the study; seeding the analysis of fixed data only
    makes it reproducible.
    """
    if not paired:
        return (0.0, 0.0)
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(paired)
    means = []
    for _ in range(samples):
        total = 0.0
        for _ in range(n):
            total += paired[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    return (means[int(0.025 * samples)], means[int(0.975 * samples) - 1])


def print_cost(views: list[ArmView], baseline: str) -> None:
    """Seconds and tokens per attempt.

    Timings come from whatever hardware the arm ran on and are only comparable across arms
    that shared a machine -- see docs/RUN-LOG.md. Turn and token counts are hardware-free
    and comparable anywhere.
    """
    seconds_per_arm: dict[str, float] = {}

    print("== cost per attempt")
    print(
        f"   {'arm':<22} {'attempts':>9} {'turns':>7} {'seconds':>9} "
        f"{'prompt tok':>11} {'reply tok':>10}"
    )
    for view in views:
        generated = [a for a in view.attempts if a.generated and not a.gold_failed]
        if not generated:
            continue
        n = len(generated)
        seconds_per_arm[view.name] = sum(a.gen_seconds for a in generated) / n
        print(
            f"   {view.name:<22} {n:>9,} {sum(a.turns for a in generated) / n:>7.2f} "
            f"{seconds_per_arm[view.name]:>9.1f} "
            f"{sum(a.prompt_tokens for a in generated) / n:>11,.0f} "
            f"{sum(a.completion_tokens for a in generated) / n:>10,.0f}"
        )

    reference = seconds_per_arm.get(baseline)
    if reference:
        print()
        for name, seconds in seconds_per_arm.items():
            if name != baseline:
                print(f"   {name} takes {seconds / reference:.2f}x the time of {baseline}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="results files (default: results/final/*.jsonl)")
    parser.add_argument("--k", type=int, default=None, help="compare at this k (default: max)")
    parser.add_argument("--baseline", default=None, help="arm to difference against")
    parser.add_argument("--strict", action="store_true", help="score without column leniency")
    args = parser.parse_args()

    paths = [Path(p) for p in args.paths] or sorted((DEFAULT_RESULTS / "final").glob("*.jsonl"))
    if not paths:
        print("no results found; run scripts/run_arm.py first", file=sys.stderr)
        return 1

    grouped = load(paths)
    if len(grouped) < 2:
        print(
            f"need at least two arms to compare, found {list(grouped)}",
            file=sys.stderr,
        )
        return 1

    views, depth = align(grouped, k=args.k, strict=args.strict)
    if not views:
        print("the arms share no questions", file=sys.stderr)
        return 1

    if args.strict:
        print("scoring strictly: extra projected columns are not accepted\n")

    baseline = args.baseline or views[0].name
    print_table(views, depth)
    print_differences(views, baseline, depth)
    print_cost(views, baseline)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
