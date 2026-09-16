"""Report schema sizes and the token budget they imply.

Schema text dominates every prompt in this study, so its size decides how long a run takes
against a rate-limited free tier. This script measures it rather than estimating it.

    uv run python scripts/schema_report.py
    uv run python scripts/schema_report.py --runs 10 --tpd 500000

Token counts are characters/4, the usual rough conversion. That is good enough to choose
between "days" and "weeks"; it is not a substitute for the provider's own accounting once a
run is under way.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from nl2sql_reliability.dataset import databases, load  # noqa: E402
from nl2sql_reliability.db import find_database, read_schema, schema_for  # noqa: E402

CHARS_PER_TOKEN = 4

# Measured from the dataset on 2026-09-16: question ~23 tokens, evidence ~31. Instruction
# text and the generated SQL are estimated; they are small next to the schema.
NON_SCHEMA_TOKENS_PER_QUESTION = 23 + 31 + 150 + 75


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10, help="repetitions per question")
    parser.add_argument("--tpd", type=int, default=500_000, help="tokens per day allowed")
    args = parser.parse_args()

    questions = load()
    counts = databases(questions)

    header = f"{'database':<26} {'qs':>4} {'chars':>9} {'~tokens':>8} {'tables':>7}"
    print(header)
    print("-" * len(header))

    schema_tokens: dict[str, int] = {}
    for db_id, n in counts.most_common():
        text = schema_for(db_id)
        tables = len(read_schema(find_database(db_id)))
        tokens = len(text) // CHARS_PER_TOKEN
        schema_tokens[db_id] = tokens
        print(f"{db_id:<26} {n:>4} {len(text):>9,} {tokens:>8,} {tables:>7}")

    print("-" * len(header))

    per_question_schema = sum(schema_tokens[db] * n for db, n in counts.items())
    mean_schema = per_question_schema // len(questions)
    print(f"mean schema tokens per question: {mean_schema:,}")

    runs = args.runs
    uncached = (per_question_schema + NON_SCHEMA_TOKENS_PER_QUESTION * len(questions)) * runs
    # With prompt caching, each schema is charged once rather than once per question — there
    # are only 11 of them across 498 questions. Requests must be ordered by db_id for the
    # cache to hit, which is why the runner groups them that way.
    cached = sum(schema_tokens.values()) + NON_SCHEMA_TOKENS_PER_QUESTION * len(questions) * runs

    print()
    print(f"One configuration = {len(questions)} questions x {runs} runs")
    print(f"Quota assumed: {args.tpd:,} tokens/day")
    for label, tokens in (("uncached", uncached), ("cached", cached)):
        days = tokens / args.tpd
        millions = tokens / 1e6
        print(f"  {label:<9} {millions:>6.1f}M tokens  ->  {days:>5.1f} days")
    print("  local     no quota; wall-clock only")
    print()
    print(f"Caching saves {(uncached - cached) / uncached * 100:.0f}% of counted tokens.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
