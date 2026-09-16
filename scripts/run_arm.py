"""Run one arm of the study: one model, one configuration, k attempts per question.

    # a pilot, to check the wiring before committing a night to it
    uv run python scripts/run_arm.py --arm pilot --questions 20 --k 5

    # the single-shot local arm
    uv run python scripts/run_arm.py --arm local-7b --questions 150 --k 10

    # the same model given its own error message and a second chance
    uv run python scripts/run_arm.py --arm local-7b-agentic --questions 150 --k 10 --max-turns 3

Re-running the same command after a crash resumes where it stopped. Results append to
`results/<arm>.jsonl`; `scripts/report.py` turns them into the pass@k / pass^k table.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from nl2sql_reliability.dataset import databases, load, stratified_subset  # noqa: E402
from nl2sql_reliability.generate import (  # noqa: E402
    DEFAULT_HOST,
    DEFAULT_NUM_CTX,
    DEFAULT_TEMPERATURE,
    OllamaGenerator,
)
from nl2sql_reliability.metrics import summarize  # noqa: E402
from nl2sql_reliability.runner import (  # noqa: E402
    outcomes,
    progress_printer,
    read_attempts,
    run_suite,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", required=True, help="name for this configuration")
    parser.add_argument("--model", default="qwen2.5-coder:7b")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--k", type=int, default=10, help="attempts per question")
    parser.add_argument(
        "--questions",
        type=int,
        default=0,
        help="stratified subset size; 0 uses all 498",
    )
    parser.add_argument("--seed", type=int, default=0, help="subset seed, not a decode seed")
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX)
    parser.add_argument(
        "--max-turns",
        type=int,
        default=1,
        help="1 is single-shot; higher lets the model see its own execution error",
    )
    parser.add_argument("--out", default=None, help="results file (default results/<arm>.jsonl)")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--every", type=int, default=10, help="progress line interval")
    args = parser.parse_args()

    questions = load()
    if args.questions:
        questions = stratified_subset(questions, args.questions, seed=args.seed)

    generator = OllamaGenerator(
        model=args.model,
        host=args.host,
        temperature=args.temperature,
        num_ctx=args.num_ctx,
    )
    if not generator.available():
        print(
            f"error: {args.model} is not available at {args.host}.\n"
            f"       start Ollama and run: ollama pull {args.model}",
            file=sys.stderr,
        )
        return 1

    if args.temperature <= 0:
        # Every attempt would be near-identical, pass@k would equal pass^k, and the study
        # would report a gap of zero that says nothing about the model.
        print(
            "error: --temperature 0 makes repetitions identical; pass^k would be meaningless.",
            file=sys.stderr,
        )
        return 1

    out_path = Path(args.out) if args.out else None
    total = len(questions) * args.k

    print(f"arm        {args.arm}")
    print(f"model      {generator.name}")
    print(f"questions  {len(questions)} across {len(databases(questions))} databases")
    print(f"k          {args.k}   max turns {args.max_turns}   temperature {args.temperature}")
    print(f"attempts   {total}")
    print()

    started = time.perf_counter()
    path = run_suite(
        questions,
        generator,
        k=args.k,
        arm=args.arm,
        out_path=out_path,
        max_turns=args.max_turns,
        resume=not args.no_resume,
        on_attempt=progress_printer(every=args.every),
    )
    elapsed = time.perf_counter() - started

    attempts = read_attempts(path, arm=args.arm)
    scored = outcomes(attempts)
    print()
    print(f"wrote {len(attempts)} attempts to {path} in {elapsed / 60:.1f} min")

    if not scored:
        print("no scoreable attempts")
        return 0

    complete = min(outcome.attempts for outcome in scored)
    print(f"{len(scored)} questions scored, {complete} complete repetitions each")
    print()
    print(f"{'k':>3} {'pass@k':>8} {'pass^k':>8} {'gap':>8}")
    for k in range(1, complete + 1):
        suite = summarize(scored, k)
        print(
            f"{k:>3} {suite.pass_at_k:>7.1%} {suite.pass_hat_k:>7.1%} {suite.reliability_gap:>7.1%}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
