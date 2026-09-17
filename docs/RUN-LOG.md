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
| Started | 2026-09-17 09:12 UTC |
| Finished | 2026-09-17 18:31 UTC |
| Generation time | 5.28 h |
| Wall clock | spread over ~9 h; paused and resumed several times |
| Throughput | 21.2 tok/s, 3.8 s per attempt |
| VRAM | 5.5 GB |
| Prompt tokens | 5,286,792 |
| Completion tokens | 403,620 |
| Turns | 6,225 (1.26 per attempt) |
| Raw output | [`results/final/local-7b-agentic.jsonl`](../results/final/local-7b-agentic.jsonl) (4.4 MB) |

### Result

| k | pass@k | pass^k | gap |
|---|---|---|---|
| 1 | 46.4% | 46.4% | 0.0 |
| 2 | 49.2% | 43.6% | 5.5 |
| 5 | 52.2% | 40.9% | 11.3 |
| 10 | 54.2% | 39.5% | 14.7 |

Outcome of 4,960 scoreable attempts:

| | count | share |
|---|---|---|
| correct | 2,301 | 46.4% |
| ran, wrong rows | 2,243 | 45.2% |
| SQL did not execute | 398 | 8.0% |
| query timed out | 18 | 0.4% |

Strict scoring: 2,228 matches (44.9%). 3.2% of matches depend on accepting extra columns.

73 of 496 questions (14.7%) were answered correctly some of the time but not always.

### What the retry loop recovered

| | |
|---|---|
| attempts that retried | 793 (16.0%) |
| …ended up executing | 377 (47.5% of those) |
| …ended up correct | 140 (17.7% of those) |
| of the misses it could have fixed, it fixed | 5.0% |

Retries fire on execution failure only, so the loop can never reach an attempt that runs
cleanly and returns the wrong rows. That is the ceiling, and against arm A it was already low:
15.9% of single-shot attempts errored out, against 40.9% that failed silently.

### Against arm A

Both arms scored on the same 496 questions, k=10.

| | arm A | arm B | difference | 95% CI |
|---|---|---|---|---|
| pass@k | 49.8% | 54.2% | +4.4 | [+2.4, +6.7] |
| pass^k | 36.1% | 39.5% | +3.4 | [+1.0, +5.8] |
| gap | 13.7 | 14.7 | **+1.0** | |
| inconsistent questions | 13.7% | 14.7% | +1.0 | |

Reliability improved on 27 questions and worsened on 10. Cost: 1.28× the time, 1.26 turns
per attempt, 32% more prompt tokens.

### Notes

Both estimators rose and the distance between them rose too. The early measurement quoted
here before the arm finished — 1.47 turns per attempt at 343 attempts — did not hold: the
final figure is 1.26, because the retry rate fell as the run moved through databases. The
early number was taken from the first database alphabetically and was not representative.
That is the reason this file records whole runs rather than progress snapshots.

---

## C — `local-3b-single`

Smaller model from the same family, so the comparison isolates parameter count rather than
confounding it with different training data or tokenizer.

| | |
|---|---|
| Model | `qwen2.5-coder:3b` (Q4_K_M, 1.9 GB) |
| Max turns | 1 |
| Attempts | 4,980 (498 × 10) |
| Started | 2026-09-17 18:33 UTC |
| Finished | 2026-09-18 03:14 UTC |
| Generation time | 3.95 h |
| Wall clock | spread over ~8.7 h; interrupted once and resumed |
| Throughput | 21.8 tok/s, 2.9 s per attempt |
| VRAM | 4.5 GB |
| Prompt tokens | 3,994,960 |
| Completion tokens | 310,581 |
| Raw output | [`results/final/local-3b-single.jsonl`](../results/final/local-3b-single.jsonl) (4.1 MB) |

### Result

| k | pass@k | pass^k | gap |
|---|---|---|---|
| 1 | 29.7% | 29.7% | 0.0 |
| 2 | 33.8% | 25.6% | 8.1 |
| 5 | 38.7% | 21.6% | 17.1 |
| 10 | 42.3% | 18.8% | 23.6 |

Outcome of 4,960 scoreable attempts:

| | count | share |
|---|---|---|
| SQL did not execute | 1,849 | 37.3% |
| ran, wrong rows | 1,628 | 32.8% |
| correct | 1,473 | 29.7% |
| query timed out | 10 | 0.2% |

Strict scoring: 1,429 matches (28.8%). 3.0% of matches depend on accepting extra columns.

117 of 496 questions (23.6%) were answered correctly some of the time but not always.

### Against arm A

Same family, same questions, half the parameters. k=10, 496 questions.

| | arm A (7B) | arm C (3B) | difference | 95% CI |
|---|---|---|---|---|
| pass@k | 49.8% | 42.3% | −7.5 | [−11.7, −3.2] |
| pass^k | 36.1% | 18.8% | **−17.3** | [−21.4, −13.3] |
| gap | 13.7 | 23.6 | +9.9 | |
| inconsistent questions | 13.7% | 23.6% | +9.9 | |

Reliability worsened on 102 questions and improved on 16.

**Reliability falls more than twice as fast as capability.** A single-run benchmark, which
measures something close to pass@1, would have reported the two models 13 points apart; on
all-of-ten agreement they are 17 points apart, and the smaller model keeps barely half the
reliability of the larger.

### By database

| Database | pass@10 | pass^10 | gap |
|---|---|---|---|
| card_games | 49.0% | 17.6% | 31.4 |
| student_club | 72.9% | 41.7% | 31.2 |
| european_football_2 | 49.0% | 19.6% | 29.4 |
| codebase_community | 50.0% | 22.9% | 27.1 |
| superhero | 61.5% | 34.6% | 26.9 |
| formula_1 | 37.9% | 12.1% | 25.8 |
| financial | 23.3% | 3.3% | 20.0 |
| debit_card_specializing | 40.0% | 23.3% | 16.7 |
| toxicology | 27.5% | 12.5% | 15.0 |
| thrombosis_prediction | 20.0% | 6.0% | 14.0 |
| california_schools | 13.3% | 3.3% | 10.0 |

Every database is worse than its 7B counterpart, and `debit_card_specializing` — the one
database where the 7B was perfectly consistent, gap 0.0 — opens to 16.7 points.

### Notes

The 3B is **not faster** on this hardware: 2.9 s per attempt against the 7B's 3.0, and 21.8
tok/s against 20.3. Per-request overhead dominates at this size, so the smaller model buys
about 1.3 GB of VRAM and nothing else. Anyone choosing it for latency on a single-stream
desktop workload would be paying 17 points of reliability for a 3% speedup.

---

## Reproducing any of these

```bash
uv venv && uv pip install -e ".[dev]"
uv run python scripts/fetch_databases.py          # 330 MB, one time
ollama pull qwen2.5-coder:7b
ollama pull qwen2.5-coder:3b

uv run python scripts/run_arm.py --arm local-7b-single  --k 10
uv run python scripts/run_arm.py --arm local-7b-agentic --k 10 --max-turns 3
uv run python scripts/run_arm.py --arm local-3b-single  --k 10 --model qwen2.5-coder:3b

uv run python scripts/report.py                    # each arm on its own
uv run python scripts/compare_arms.py              # arms against each other
```

Any run can be interrupted and restarted with the same command; completed attempts are skipped.

Exact numbers will not reproduce — temperature is above zero by design, so repetitions differ.
The aggregate figures should reproduce closely. If they do not, that is itself worth reporting.
