"""Re-score recorded attempts with the current scorer, without running a model again.

    uv run python scripts/rescore.py              # report what would change
    uv run python scripts/rescore.py --write      # rewrite results/final/*.jsonl in place

Needs the BIRD databases (`scripts/fetch_databases.py`). Every attempt's final SQL and every
gold query is executed again, read-only, and the two result sets are compared with the
scorer in `compare.py`. Only the verdict fields (`match`, `match_strict`, `reason`) are ever
rewritten; the model's reply, the SQL, timings and token counts are left exactly as recorded.

Re-scoring the final SQL is equivalent to re-running the arm with the new scorer as long as the
scorer change cannot alter which turn an agentic attempt stops on. Retries fire on execution
errors only, so a change to the row comparison cannot. A change to what counts as an execution
error can, and this script refuses to write if any attempt's execution status comes out
different from the one recorded: that would need a real re-run, not a re-score.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from nl2sql_reliability.compare import compare_result_sets, order_matters  # noqa: E402
from nl2sql_reliability.dataset import load  # noqa: E402
from nl2sql_reliability.db import DEFAULT_ROOT, find_database  # noqa: E402
from nl2sql_reliability.execute import DEFAULT_TIMEOUT_SECONDS, run_query  # noqa: E402

DEFAULT_RESULTS = Path("results/final")


def _score_question(item: tuple) -> tuple[int, dict]:
    """Run one question's gold once and each distinct predicted SQL once; return verdicts."""
    question_id, db_path, gold_sql, predictions, timeout = item
    gold = run_query(db_path, gold_sql, timeout_seconds=timeout)
    verdicts: dict[str, dict] = {}
    if not gold.ok:
        return question_id, {"gold_ok": False, "gold_error": gold.error, "verdicts": verdicts}
    ordered = order_matters(gold_sql)
    for sql in predictions:
        pred = run_query(db_path, sql, timeout_seconds=timeout)
        if not pred.ok:
            verdicts[sql] = {"executed": False, "timed_out": pred.timed_out}
            continue
        lenient = compare_result_sets(gold.rows, pred.rows, ordered=ordered)
        strict = lenient.match and bool(
            compare_result_sets(gold.rows, pred.rows, ordered=ordered, allow_extra_columns=False)
        )
        verdicts[sql] = {
            "executed": True,
            "match": lenient.match,
            "match_strict": strict,
            "reason": lenient.reason,
        }
    return question_id, {"gold_ok": True, "verdicts": verdicts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="results files (default: results/final/*.jsonl)")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="data directory")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--write", action="store_true", help="rewrite the files in place")
    args = parser.parse_args()

    paths = [Path(p) for p in args.paths] or sorted(DEFAULT_RESULTS.glob("*.jsonl"))
    if not paths:
        print("no results files found", file=sys.stderr)
        return 1

    questions = {q.question_id: q for q in load()}
    files = {path: path.read_text(encoding="utf-8").splitlines() for path in paths}
    records = {path: [json.loads(line) for line in lines] for path, lines in files.items()}
    # A record must survive a load/dump round trip byte for byte, or a rewrite would touch
    # lines whose verdict did not change and the diff would hide the ones that did.
    for path, lines in files.items():
        for record, line in zip(records[path], lines, strict=True):
            if json.dumps(record, ensure_ascii=False) != line:
                print(f"{path}: a line does not round-trip; refusing", file=sys.stderr)
                return 1

    predictions: dict[int, set[str]] = defaultdict(set)
    for recs in records.values():
        for record in recs:
            if not record["gold_failed"] and record["generated"]:
                predictions[int(record["question_id"])].add(record["sql"])

    items = [
        (
            qid,
            str(find_database(questions[qid].db_id, args.root)),
            questions[qid].gold_sql,
            sorted(sqls),
            args.timeout,
        )
        for qid, sqls in predictions.items()
    ]
    items.sort(key=lambda item: -len(item[3]))  # longest first, for an even finish
    distinct = sum(len(item[3]) for item in items)
    print(f"re-executing {distinct:,} distinct queries on {len(items)} questions")
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        scored = dict(pool.map(_score_question, items, chunksize=1))

    problems = 0
    for path, recs in records.items():
        changed: Counter[str] = Counter()
        flips: list[str] = []
        for record in recs:
            if record["gold_failed"] or not record["generated"]:
                continue
            qid = int(record["question_id"])
            result = scored[qid]
            if not result["gold_ok"]:
                print(f"  q{qid}: gold no longer executes ({result['gold_error']})")
                problems += 1
                continue
            verdict = result["verdicts"][record["sql"]]
            if verdict["executed"] != record["executed"]:
                print(
                    f"  {record['arm']} q{qid} run {record['run']}: execution status "
                    f"{record['executed']} -> {verdict['executed']}"
                )
                problems += 1
                continue
            if not verdict["executed"]:
                continue  # the recorded error and timeout stand as recorded
            for key in ("match", "match_strict", "reason"):
                if record[key] != verdict[key]:
                    changed[key] += 1
                    record[key] = verdict[key]
        for record, line in zip(recs, files[path], strict=True):
            if json.dumps(record, ensure_ascii=False) != line:
                flips.append(f"q{record['question_id']}/run{record['run']}")
        print(
            f"{path}: {len(flips)} records change "
            f"(match {changed['match']}, match_strict {changed['match_strict']}, "
            f"reason {changed['reason']})"
        )
        if flips:
            print("   " + " ".join(flips))

    if problems:
        print(f"{problems} attempt(s) cannot be re-scored; nothing written", file=sys.stderr)
        return 1
    if args.write:
        for path, recs in records.items():
            text = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in recs)
            path.write_text(text, encoding="utf-8", newline="\n")
        print("written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
