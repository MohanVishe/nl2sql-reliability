# Method — the technical detail

The [README](../README.md) is the short version and [EXPLAINED.md](EXPLAINED.md) is the long
plain-language one. This page is for readers who want the design decisions behind the numbers:
what is measured, how it is scored, and why each rule is the way it is.

---

## What is measured

For each benchmark question, each model is run **k times on an identical prompt**. Two numbers
come out:

| Metric | Meaning |
|---|---|
| **pass@k** | *Capability.* At least one of k attempts is correct. What leaderboards approximate. |
| **pass^k** | *Reliability.* **All** k attempts are correct. What an automated pipeline actually depends on. |

Both are computed with **unbiased estimators** over the n samples drawn, not the plug-in forms
`1-(1-p)^k` and `p^k`. The plug-in forms assume sampling with replacement, which understates
pass@k and overstates pass^k — flattering in exactly the direction this study measures. See
[`metrics.py`](../src/nl2sql_reliability/metrics.py) for the derivation.

- pass@k — Chen et al., *Evaluating Large Language Models Trained on Code* (2021), [arXiv:2107.03374](https://arxiv.org/abs/2107.03374)
- pass^k — Yao et al., *τ-bench* (2024), [arXiv:2406.12045](https://arxiv.org/abs/2406.12045)

The three configurations ("arms"):

| Arm | Model | Setup |
|---|---|---|
| A — `local-7b-single` | Qwen2.5-Coder-7B, Q4_K_M | one generation per attempt |
| B — `local-7b-agentic` | same | up to 3 turns; the model sees its own execution error, never the expected rows |
| C — `local-3b-single` | Qwen2.5-Coder-3B, Q4_K_M | one generation per attempt |

### Settings, and why

| Setting | Value | Why |
|---|---|---|
| Model | Qwen2.5-Coder-7B-Instruct | A strong open code model that fits the 8 GB GPU this ran on. 14B does not fit. |
| Weights | Q4_K_M (4-bit) | BF16 7B needs ~14 GB. Q4 is ~4.7 GB and leaves room for the KV cache. |
| Small model | Qwen2.5-Coder-3B, Q4_K_M | Same family, so the size comparison is not confounded by different training data or tokenizer. |
| Temperature | 0.2 | Must be above zero: pass^k measures run-to-run variation, and a greedy decode makes every attempt near-identical. 0.2 is a low, conservative setting for SQL generation, so the gap is not produced by an unusually random configuration. |
| Decode seed | none | A fixed seed would make the ten repetitions identical, and pass^k would measure nothing. |
| k | 10 | The gap keeps widening with k (5.1 at k=2, 13.7 at k=10 on arm A; 8.1 vs 23.6 on arm C), so small k understates it. 10 was affordable: 4–5 h per arm locally, with no rate limit. |
| Context window | 8,192 tokens | The largest single-turn prompt was 1,965 tokens and the largest three-turn conversation 6,678. 8,192 fits both inside 8 GB of VRAM. Ollama silently cuts over-long prompts from the front, so truncation is detected: 0 of 14,940 attempts were truncated. |
| Max new tokens | 512 | One SQL query is the whole answer; gold queries average ~50 tokens. The cap cut off 10 of 4,980 replies on arm A and 11 on arm C (0.2%) — mostly the model repeating itself. They are scored as failures, so the cap moves no figure by more than 0.2 points. |
| Max turns (arm B) | 3 | The first try plus two repairs. The third turn recovered 12 correct answers, against 128 from the second, so a longer loop would add little. |
| Retry feedback | the database's error message only | Showing expected rows would let the model converge on the answer key and measure the harness rather than the model. |
| Query deadline | 30 s | Generated SQL is untrusted; a runaway query must not stall the run. 10–18 attempts per arm hit it and are recorded as timeouts. |
| Database access | read-only | One generated `DROP` or `UPDATE` would corrupt every later attempt. |

**Not addressed:** whether temperature 0 is deterministic in practice. It would need its own
arm. The runner refuses temperature 0 outright, because near-identical repetitions would make
pass^k describe the decoder rather than the model. Results at higher temperatures are not
measured either; the gap there may differ.

## Related work

The all-attempts metric is not new. τ-bench defines pass^k for tool-using agents;
[DySQL-Bench](https://arxiv.org/abs/2510.26495) reports an all-trials score for multi-turn
text-to-SQL; [Knowing When to Stop](https://arxiv.org/abs/2607.03991) uses execution agreement
across repeated samples as a stopping rule for text-to-SQL inference. This study's contribution
is narrower: single-turn pass@k vs pass^k on an expert-corrected answer key, with failures split
by whether they raise an error, the reach of an error-driven retry loop measured directly, and
the effect of model size on the gap — each difference with a paired interval.

## Why the dataset is not plain BIRD

Jin et al. ([arXiv:2601.08778](https://arxiv.org/abs/2601.08778), VLDB 2026) audited the
benchmarks the field cites and found annotation error rates of **52.8% on BIRD Mini-Dev** and
**62.8% on Spider 2.0-Snow**. Re-scoring 16 published agents against corrected labels moved
results by −7% to +31% relative, and shifted leaderboard ranks by up to nine places.

That is fatal for a study like this one specifically. Measuring disagreement between repeated
runs on a dataset where half the answer key is wrong measures how *creatively* a model goes
wrong, not how *reliably* it goes right.

So this uses **[Arcwise-Plat-SQL](https://github.com/uiuc-kang-lab/text_to_sql_benchmarks)** —
498 BIRD Mini-Dev questions across 11 databases with SQL annotations corrected by database
experts (CC BY-SA 4.0). The data is fetched on demand, never vendored here.

Two questions are excluded throughout because their reference SQL does not execute against the
shipped database; 496 are scored.

## How answers are scored

Execution-based, never string matching. Both queries run against the local SQLite database and
their **result sets** are compared — there are many correct ways to write the same query, and a
scorer that punishes rewrites would report model variance that is really scorer variance.

No model grades another model anywhere in the pipeline.

| Rule | Why |
|---|---|
| Rows compared as a **multiset** | Set comparison silently loses duplicate rows, which are meaningful in SQL |
| Row order ignored **unless the gold query has ORDER BY** | Without ORDER BY, SQL guarantees no particular order |
| Column order resolved by **searching alignments** | Column order is not specified by the question |
| Extra columns tolerated by default | A query that answers the question *and* returns the id it grouped by has answered the question |
| `1` and `1.0` are equal; strings trimmed | SQLite is loosely typed; representation is not semantics |
| Queries run **read-only**, with a deadline | Generated SQL is untrusted: one hallucinated `DROP` would corrupt every later run |

Scoring is recorded under **both** readings of the extra-columns rule. The headline is lenient;
the strict reading changes 2.5–3.2% of matches depending on the arm.

A harness problem must never be recorded as a model failure, so outcomes are kept distinct:
generation failed, prompt overran the context window, SQL did not execute, query timed out, and
query ran and returned the wrong rows are five different things in the results file.

## How a run works

Each question is asked **k times on a byte-identical prompt**, and every attempt is appended to
JSONL as it completes.

- **Resumable.** Completed `(arm, question_id, run)` keys are skipped on restart, so a crash
  costs minutes, not the run.
- **Ordered by database.** Consecutive questions on one database share a byte-identical prompt
  prefix — what a provider's prompt cache keys on, measured at a 69% token saving. Attempts are
  independent draws at temperature > 0, so ordering cannot bias the sample.
- **Gold executed once** per question, not once per repetition.
- **Truncation is detected.** Ollama silently drops the front of an over-long prompt — which is
  where the schema lives. A prompt that fills the context window is recorded as an error, not
  scored.

## How the numbers are checked

**Two identities that must hold.** At k=1, "at least one correct" and "all correct" are the same
statement, so the two metrics must be equal. At k=n, the gap is mathematically identical to the
fraction of questions answered correctly some-but-not-all of the time, which is counted by a
separate code path. Both hold for all three arms.

**Paired bootstrap for every difference.** Arm-vs-arm differences come with a 95% interval from
10,000 resamples of the per-question differences. Questions are resampled, not attempts —
questions are the independent unit, and resampling attempts would give an interval far too
narrow. Pairing cancels out the fact that some questions are simply harder than others.

**Comparisons use the intersection.** [`compare_arms.py`](../scripts/compare_arms.py) aligns
arms on the questions they all answered, and holds k to the shallowest arm.

## Full results

| arm | pass@1 | pass@10 | pass^10 | gap | inconsistent | silent failures | errors |
|---|---|---|---|---|---|---|---|
| A — 7B single | 42.9% | 49.8% | 36.1% | 13.7 | 13.7% | 40.9% | 15.9% |
| B — 7B retry | 46.4% | 54.2% | 39.5% | 14.7 | 14.7% | 45.2% | 8.0% |
| C — 3B single | 29.7% | 42.3% | 18.8% | 23.6 | 23.6% | 32.8% | 37.3% |

| Difference vs A, k=10 | pass@k | 95% CI | pass^k | 95% CI | gap | 95% CI | better / worse |
|---|---|---|---|---|---|---|---|
| B — retry | +4.4 | [+2.4, +6.7] | +3.4 | [+1.0, +5.8] | +1.0 | [−2.0, +4.0] | 27 / 10 |
| C — 3B | −7.5 | [−11.7, −3.2] | −17.3 | [−21.4, −13.3] | +9.9 | [+5.0, +14.7] | 16 / 102 |

Arm B's gap interval includes zero, so it is reported as leaving the gap unchanged. Arm C's
widening is clear of zero.

Arm B's retry loop fired on 16.0% of attempts; 47.5% of those ended up executing and 17.7% ended
up correct — that is its success rate on the failures it can reach. Counted against every miss
the arm would otherwise have had, silent ones included, it fixed 5.0%.

Per-arm configuration, timings and token counts: [RUN-LOG.md](RUN-LOG.md).

## Install and test

```bash
uv venv
uv pip install -e ".[dev]"
uv run pytest                      # 251 tests
uv run pytest -m "not network"     # skip tests that need the internet
```

Requires Python 3.11+. CI runs on 3.11, 3.12 and 3.13.

## Reproduce

```bash
uv run python scripts/fetch_databases.py          # 330 MB, one time
ollama pull qwen2.5-coder:7b
ollama pull qwen2.5-coder:3b

uv run python scripts/run_arm.py --arm local-7b-single  --k 10
uv run python scripts/run_arm.py --arm local-7b-agentic --k 10 --max-turns 3
uv run python scripts/run_arm.py --arm local-3b-single  --k 10 --model qwen2.5-coder:3b

uv run python scripts/report.py            # each arm on its own
uv run python scripts/compare_arms.py      # arms against each other
```

Any run can be interrupted and restarted with the same command. Exact numbers will not
reproduce — temperature is above zero by design — but the aggregates should reproduce closely.

## Layout

```
src/nl2sql_reliability/
  dataset.py   # fetch and load Arcwise-Plat-SQL
  db.py        # locate BIRD databases; render schemas as DDL
  prompt.py    # cacheable prefix + question suffix; SQL extraction from replies
  generate.py  # model access (Ollama); truncation reported, not swallowed
  execute.py   # read-only, deadline-bounded SQLite execution
  compare.py   # result-set equivalence
  metrics.py   # unbiased pass@k and pass^k estimators
  runner.py    # k repetitions, scoring, resumable JSONL output
scripts/
  fetch_databases.py  # download the BIRD dev databases
  schema_report.py    # measure the token budget a run will cost
  run_arm.py          # run one configuration
  report.py           # turn recorded attempts into tables
  compare_arms.py     # arms against each other, on the questions they share
results/final/        # every attempt from every arm, one JSON line each
tests/                # known-equivalent and known-different cases for each module
```
