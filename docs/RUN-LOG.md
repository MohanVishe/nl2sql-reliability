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
| Runtime | Ollama 0.34.1 as recorded at run time (0.34.2 installed when this file was last checked, 2026-09-26; the server logs from the run dates have rotated away) |
| Python | 3.14.7 (package requires ≥3.11) |
| uv | 0.12.10 |

## Models

Digests from Ollama's `/api/tags` on 2026-09-26. Both model files were last modified before the
first arm that used them started (7B: 2026-09-16 18:16 UTC, arm A began 18:31 UTC; 3B:
2026-09-16 18:31 UTC, arm C began 2026-09-17 18:33 UTC), so these are the weights the runs used.
The results files do not record a digest per attempt; future runs will.

| Tag | Digest | Quantisation | Licence |
|---|---|---|---|
| `qwen2.5-coder:7b` | `dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364` | Q4_K_M, 4.7 GB | Apache 2.0 |
| `qwen2.5-coder:3b` | `f72c60cabf6237b07f6e632b2c48d533cef25eda2efbd34bed21c5e9c01e6225` | Q4_K_M, 1.9 GB | Qwen Research License (research use) |

Request options sent on every call: `temperature` 0.2, `num_ctx` 8192, `num_predict` 512, no
`seed`. Neither model's Modelfile sets any sampler parameter (`ollama show --parameters` is
empty), so Ollama's built-in defaults apply to everything else (top-k, top-p, repetition
penalty). The chat template adds the system prompt "You are Qwen, created by Alibaba Cloud. You
are a helpful assistant." The user turn is the prompt built by `prompt.py`.

## Fixed across all arms

| | |
|---|---|
| Dataset | Arcwise-Plat-SQL, 498 questions, 11 databases; upstream commit `b83d8a899c3e0a771feccfa9ae0ece89885f2898`, sha256 `e7bf76408f99266506ea558d84982c69422e5db3dfecab5f08a3a8d3900e8395` |
| Databases | BIRD dev set, SQLite (CC BY-SA 4.0), from the Hugging Face mirror `nlile/BIRD-bench` at revision `3b94f13f89dc` (`dev_databases.zip`, LFS sha256 `8132aacfe61ecec7198a62e93cb8512dac89b1d6b9922fe3c7bb94b13f317d8f`). Each `.sqlite` is sha256-checked against `db.DATABASE_SHA256` |
| Repetitions (k) | 10 |
| Temperature | 0.2 |
| Context window | 8192 |
| Max new tokens | 512 |
| Decode seed | none, so single attempts cannot be replayed. (One shared seed would make the repetitions identical; a per-attempt seed would not. Next runs use seed = run index.) |
| Scoring | execution-based; both lenient and strict recorded |

The reason for each setting is in [METHOD.md](METHOD.md#settings-and-why).

Two questions (q518, q701) are excluded from every figure: their reference SQL runs but takes
longer than the 30 s query deadline (147–160 s and 742–823 s across two timing runs when allowed to finish), so there are no
reference rows to score against. Their 60 records (2 × 10 × 3 arms) carry `gold_failed: true`
and no model call was made for them. 496 questions are scored, not 498: 14,880 scored
generations out of 14,940 records.

---

## A — `local-7b-single`

Single-shot. One generation per attempt, no retry.

| | |
|---|---|
| Model | `qwen2.5-coder:7b` (Q4_K_M, 4.7 GB) |
| Max turns | 1 |
| Attempts | 4,980 records (498 × 10); 4,960 generated and scored |
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
| 1 | 43.0% | 43.0% | 0.0 |
| 2 | 45.6% | 40.4% | 5.3 |
| 5 | 48.4% | 37.7% | 10.8 |
| 10 | 50.0% | 36.1% | 13.9 |

Outcome of 4,960 scoreable attempts:

| | count | share |
|---|---|---|
| correct | 2,134 | 43.0% |
| ran, rows didn't match the reference | 2,026 | 40.8% |
| SQL did not execute | 790 | 15.9% |
| query timed out | 10 | 0.2% |

Strict scoring: 2,080 matches (41.9%). 2.5% of matches depend on accepting extra columns.

69 of 496 questions (13.9%) were answered correctly some of the time but not always.

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
| Attempts | 4,980 records (498 × 10); 4,960 generated and scored |
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
| 1 | 46.6% | 46.6% | 0.0 |
| 2 | 49.4% | 43.8% | 5.5 |
| 5 | 52.4% | 41.1% | 11.3 |
| 10 | 54.4% | 39.7% | 14.7 |

Outcome of 4,960 scoreable attempts:

| | count | share |
|---|---|---|
| correct | 2,311 | 46.6% |
| ran, rows didn't match the reference | 2,233 | 45.0% |
| SQL did not execute | 398 | 8.0% |
| query timed out | 18 | 0.4% |

Strict scoring: 2,238 matches (45.1%). 3.2% of matches depend on accepting extra columns.

73 of 496 questions (14.7%) were answered correctly some of the time but not always.

### What the retry loop recovered

| | |
|---|---|
| attempts that retried | 793 (16.0%) |
| …ended up executing | 377 (47.5% of those) |
| …ended up correct | 145 (18.3% of those): 133 on turn 2, 12 on turn 3 |
| share of all misses the loop fixed (silent ones included) | 5.2% (145 of 2,794) |

Retries fire on execution failure only, so the loop can never reach an attempt that runs
cleanly and returns the wrong rows. That is the ceiling, and against arm A it was already low:
15.9% of single-shot attempts errored out, against 40.8% that failed silently.

### Against arm A

Both arms scored on the same 496 questions, k=10.

| | arm A | arm B | difference | 95% CI |
|---|---|---|---|---|
| pass@k | 50.0% | 54.4% | +4.4 | [+2.4, +6.7] |
| pass^k | 36.1% | 39.7% | +3.6 | [+1.2, +6.0] |
| gap | 13.9 | 14.7 | +0.8 | [−2.2, +3.8] — includes zero |
| inconsistent questions | 13.9% | 14.7% | +0.8 | |

Reliability improved on 28 questions and worsened on 10. Cost: 1.28× the time, 1.26 turns
per attempt, 32% more prompt tokens.

### Notes

Both estimators rose by amounts clear of zero. The gap between them moved by +0.8, but its
interval includes zero, so this arm is recorded as no detectable change in the gap: neither a
widening nor evidence that it stayed exactly the same. The early measurement quoted
here before the arm finished — 1.47 turns per attempt at 343 attempts — did not hold: the
final figure is 1.26, because the retry rate fell as the run moved through databases. The
early number was taken from the first database alphabetically and was not representative.
That is the reason this file records whole runs rather than progress snapshots.

---

## C — `local-3b-single`

Smaller model from the same family and at the same 4-bit quantisation, so training data and
tokenizer are held fixed. Quantisation is not separated from size: 4-bit may cost a 3B more than
a 7B.

| | |
|---|---|
| Model | `qwen2.5-coder:3b` (Q4_K_M, 1.9 GB) |
| Max turns | 1 |
| Attempts | 4,980 records (498 × 10); 4,960 generated and scored |
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
| 1 | 29.8% | 29.8% | 0.0 |
| 2 | 33.9% | 25.6% | 8.2 |
| 5 | 38.9% | 21.6% | 17.3 |
| 10 | 42.5% | 18.8% | 23.8 |

Outcome of 4,960 scoreable attempts:

| | count | share |
|---|---|---|
| SQL did not execute | 1,849 | 37.3% |
| ran, rows didn't match the reference | 1,625 | 32.8% |
| correct | 1,476 | 29.8% |
| query timed out | 10 | 0.2% |

Strict scoring: 1,432 matches (28.9%). 3.0% of matches depend on accepting extra columns.

118 of 496 questions (23.8%) were answered correctly some of the time but not always.

### Against arm A

Same family, same questions, half the parameters. k=10, 496 questions.

| | arm A (7B) | arm C (3B) | difference | 95% CI |
|---|---|---|---|---|
| pass@k | 50.0% | 42.5% | −7.5 | [−11.7, −3.2] |
| pass^k | 36.1% | 18.8% | **−17.3** | [−21.4, −13.3] |
| gap | 13.9 | 23.8 | +9.9 | [+5.0, +14.7] |
| inconsistent questions | 13.9% | 23.8% | +9.9 | |

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
| superhero | 63.5% | 34.6% | 28.8 |
| codebase_community | 50.0% | 22.9% | 27.1 |
| formula_1 | 37.9% | 12.1% | 25.8 |
| financial | 23.3% | 3.3% | 20.0 |
| debit_card_specializing | 40.0% | 23.3% | 16.7 |
| toxicology | 27.5% | 12.5% | 15.0 |
| thrombosis_prediction | 20.0% | 6.0% | 14.0 |
| california_schools | 13.3% | 3.3% | 10.0 |

pass^10 is lower than the 7B's on 10 of 11 databases (toxicology ties at 12.5%), and
`debit_card_specializing` — the one database where the 7B was perfectly consistent, gap 0.0 —
opens to 16.7 points.

### Notes

The 3B is **not faster** on this hardware: 2.9 s per attempt against the 7B's 3.0, and 21.8
tok/s against 20.3. Per-request overhead dominates at this size, so the smaller model buys
about 1.3 GB of VRAM and nothing else. Anyone choosing it for latency on a single-stream
desktop workload would be paying 17 points of reliability for a 3% speedup.

---

## Re-score — 2026-09-26

Not a new run. Two scorer bugs were fixed (an ORDER BY below the top level of the reference
query no longer forces row order; an empty query is an execution error, not an empty result),
and `scripts/rescore.py --write` re-executed the stored SQL of all 14,880 scored attempts against
the same databases (sha256-verified) and rewrote only `match`, `match_strict` and `reason`.

- Execution status: identical to the recorded one for all 14,880 attempts.
- Verdicts changed: 17, all on q728, all miss → match (arm A 4, B 10, C 3). The window
  function's `OVER (ORDER BY …)` had been read as an ordering requirement.
- Empty queries in the published data: 0, so the second fix changed nothing.
- Every other record is byte-identical. The figures above are the re-scored ones; the previous
  verdicts are in git history (results as of commit `a0ea7fb`).

| figure | before | after |
|---|---|---|
| A pass@1 / pass@10 / pass^10 / gap | 42.9 / 49.8 / 36.1 / 13.7 | 43.0 / 50.0 / 36.1 / 13.9 |
| B pass@1 / pass@10 / pass^10 / gap | 46.4 / 54.2 / 39.5 / 14.7 | 46.6 / 54.4 / 39.7 / 14.7 |
| C pass@1 / pass@10 / pass^10 / gap | 29.7 / 42.3 / 18.8 / 23.6 | 29.8 / 42.5 / 18.8 / 23.8 |
| B − A: pass^10 / gap | +3.4 [+1.0, +5.8] / +1.0 [−2.0, +4.0] | +3.6 [+1.2, +6.0] / +0.8 [−2.2, +3.8] |
| C − A: pass@10 / pass^10 / gap | −7.5 / −17.3 / +9.9 | unchanged |

---

## Reproducing any of these

```bash
uv sync --locked --extra dev
uv run python scripts/fetch_databases.py          # 346 MB, one time; sha256-checked
ollama pull qwen2.5-coder:7b
ollama pull qwen2.5-coder:3b

uv run python scripts/run_arm.py --arm local-7b-single  --k 10
uv run python scripts/run_arm.py --arm local-7b-agentic --k 10 --max-turns 3
uv run python scripts/run_arm.py --arm local-3b-single  --k 10 --model qwen2.5-coder:3b

uv run python scripts/report.py results/*.jsonl    # each arm on its own
uv run python scripts/compare_arms.py results/*.jsonl --baseline local-7b-single
```

Any run can be interrupted and restarted with the same command; completed attempts are skipped.

Exact numbers will not reproduce — temperature is above zero by design, so repetitions differ.
The aggregate figures should reproduce closely. If they do not, that is itself worth reporting.
