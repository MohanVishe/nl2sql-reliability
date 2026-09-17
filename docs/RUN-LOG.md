# Run log

Every arm that has been run, with the exact configuration it ran under.

Model endpoints and weights change, sometimes silently. A benchmark number without a date, a
model version and a quantisation is not a measurement — it is an anecdote. This file is what
makes the results in [EXPLAINED.md](EXPLAINED.md) checkable a year from now.

Newest last.

---

## Reference machine

Identical for every arm unless an entry says otherwise.

| | |
|---|---|
| CPU | AMD Ryzen 5 3600 (6 cores / 12 threads) |
| RAM | 16 GB |
| GPU | NVIDIA RTX 3070, 8 GB VRAM |
| OS | Windows 11 Pro 26200 |
| Runtime | Ollama 0.34.1 |
| Python | 3.14.7 (package requires ≥3.11) |
| uv | 0.12.10 |

## Fixed across all arms

| | |
|---|---|
| Dataset | Arcwise-Plat-SQL, 498 questions, 11 databases |
| Databases | BIRD dev set, SQLite |
| Repetitions (k) | 10 |
| Temperature | 0.2 |
| Context window | 8192 |
| Max new tokens | 512 |
| Decode seed | none — repetitions must differ, or pass^k measures nothing |
| Scoring | execution-based; both lenient and strict recorded |

Two questions are excluded from every figure because their reference SQL does not execute
against the shipped database. 496 are scored, not 498.

---

## A — `local-7b-single`

Single-shot. One generation per attempt, no retry.

| | |
|---|---|
| Model | `qwen2.5-coder:7b` (Q4_K_M, 4.7 GB) |
| Max turns | 1 |
| Attempts | 4,980 (498 × 10) |
| Started | 2026-09-16 18:31 UTC |
| Finished | 2026-09-17 08:37 UTC |
| Generation time | 4.12 h |
| Wall clock | spread over ~14 h; the run was paused and resumed several times |
| Throughput | 20.3 tok/s, 3.0 s per attempt |
| VRAM | 5.8 GB |
| Prompt tokens | 3,994,960 |
| Completion tokens | 300,754 |
| Raw output | [`results/final/local-7b-single.jsonl`](../results/final/local-7b-single.jsonl) (4.2 MB) |

### Result

| k | pass@k | pass^k | gap |
|---|---|---|---|
| 1 | 42.9% | 42.9% | 0.0 |
| 2 | 45.5% | 40.4% | 5.1 |
| 5 | 48.2% | 37.7% | 10.6 |
| 10 | 49.8% | 36.1% | 13.7 |

Outcome of 4,960 scoreable attempts:

| | count | share |
|---|---|---|
| correct | 2,130 | 42.9% |
| ran, wrong rows | 2,030 | 40.9% |
| SQL did not execute | 790 | 15.9% |
| query timed out | 10 | 0.2% |

Strict scoring: 2,076 matches (41.9%). 2.5% of matches depend on accepting extra columns.

68 of 496 questions (13.7%) were answered correctly some of the time but not always.

### Notes

The run was paused and resumed several times around other use of the machine. Resumption is by
`(arm, question_id, run)` key, so this has no effect on the results — but it is why generation
time (4.12 h) is much less than elapsed wall clock.

---

## B — `local-7b-agentic`

Same model, shown its own execution error and allowed to retry. Retries fire on execution
failure only; the model never sees the expected rows.

| | |
|---|---|
| Model | `qwen2.5-coder:7b` (Q4_K_M, 4.7 GB) |
| Max turns | 3 |
| Attempts | 4,980 (498 × 10) |
| Started | 2026-09-17 |
| Status | **running** |

Early measurement at 343 attempts: 1.47 turns per attempt, 4.9 s per attempt — a 1.65× slowdown
against the single-shot arm. That multiplier is itself a result: it is what self-correction
costs, against whatever it buys.

Results will be added here when the arm completes.

---

## C — `local-3b-single`

Smaller model from the same family, so the comparison isolates parameter count rather than
confounding it with different training data or tokenizer.

| | |
|---|---|
| Model | `qwen2.5-coder:3b` (Q4_K_M, 1.9 GB) |
| Max turns | 1 |
| Attempts | 4,980 (498 × 10) |
| Status | **queued** |

---

## Reproducing any of these

```bash
uv venv && uv pip install -e ".[dev]"
uv run python scripts/fetch_databases.py          # 330 MB, one time
ollama pull qwen2.5-coder:7b

uv run python scripts/run_arm.py --arm local-7b-single  --k 10
uv run python scripts/run_arm.py --arm local-7b-agentic --k 10 --max-turns 3
uv run python scripts/run_arm.py --arm local-3b-single  --k 10 --model qwen2.5-coder:3b

uv run python scripts/report.py
```

Any run can be interrupted and restarted with the same command; completed attempts are skipped.

Exact numbers will not reproduce — temperature is above zero by design, so repetitions differ.
The aggregate figures should reproduce closely. If they do not, that is itself worth reporting.
