# Method — the technical detail

The [README](../README.md) is the short version and [EXPLAINED.md](EXPLAINED.md) is the long
plain-language one. This page is for readers who want the design decisions behind the numbers:
what is measured, how it is scored, and why each rule is the way it is.

---

## What is measured

For each benchmark question, each model is run **k times on an identical prompt**, sampled at
temperature 0.2. Two numbers come out:

| Metric | Meaning |
|---|---|
| **pass@k** | *Capability.* At least one of k attempts is correct. What leaderboards approximate. |
| **pass^k** | *Reliability.* **All** k attempts are correct. What an automated pipeline actually depends on. |

Both are computed with **unbiased estimators** over the n samples drawn, not the plug-in forms
`1-(1-p)^k` and `p^k`. The plug-in forms assume sampling with replacement, which understates
pass@k and overstates pass^k — flattering in exactly the direction this study measures. See
[`metrics.py`](../src/nl2sql_reliability/metrics.py) for the derivation. At k = n = 10 both
estimators reduce to per-question indicators (all ten right; at least one right).

- pass@k — Chen et al., *Evaluating Large Language Models Trained on Code* (2021), [arXiv:2107.03374](https://arxiv.org/abs/2107.03374)
- pass^k — Yao et al., *τ-bench* (2024), [arXiv:2406.12045](https://arxiv.org/abs/2406.12045)

The three configurations ("arms"):

| Arm | Model | Setup |
|---|---|---|
| A — `local-7b-single` | Qwen2.5-Coder-7B-Instruct, Q4_K_M | one generation per attempt |
| B — `local-7b-agentic` | same | up to 3 turns; the model sees its own execution error, never the expected rows |
| C — `local-3b-single` | Qwen2.5-Coder-3B-Instruct, Q4_K_M | one generation per attempt |

Exact model digests, runtime version and sampler settings are in [RUN-LOG.md](RUN-LOG.md).

### Settings, and why

| Setting | Value | Why |
|---|---|---|
| Model | Qwen2.5-Coder-7B-Instruct | A strong open code model that fits the 8 GB GPU this ran on. 14B does not fit. Apache 2.0. |
| Weights | Q4_K_M (4-bit) | BF16 7B needs ~14 GB. Q4 is ~4.7 GB and leaves room for the KV cache. |
| Small model | Qwen2.5-Coder-3B-Instruct, Q4_K_M | Same family and the same 4-bit quantisation, so training data and tokenizer are held fixed. Quantisation is not separated from size: 4-bit may cost a 3B more than a 7B. Qwen Research License. |
| Temperature | 0.2 | A design choice. pass^k here measures the run-to-run spread of a *sampled* model, and 0.2 is a common low setting for SQL generation, so the gap is not produced by an unusually random configuration. Whether greedy decoding (temperature 0) gives identical attempts in practice was not measured; see [Next](#next). |
| Decode seed | none | Not set, so single attempts cannot be replayed. One shared seed would make the ten repetitions identical; a per-attempt seed (seed = run index) keeps them different *and* replayable. Next runs use that. |
| k | 10 | The gap keeps widening with k (5.3 at k=2, 13.9 at k=10 on arm A; 8.2 vs 23.8 on arm C), so small k understates it, and k=10 is itself a lower bound for more repetitions. 10 was affordable: 4–5 h per arm locally, with no rate limit. |
| Context window | 8,192 tokens | The largest single-turn prompt was 1,965 tokens and the largest three-turn conversation 6,678. 8,192 fits both inside 8 GB of VRAM. Ollama silently cuts over-long prompts from the front, so truncation is detected: 0 of 14,880 generated attempts were truncated. |
| Max new tokens | 512 | One SQL query is the whole answer; gold queries average ~50 tokens. The cap cut off 10 of 4,960 replies on arm A and 11 on arm C (0.2%) — mostly the model repeating itself. They are scored as failures, so the cap moves no figure by more than 0.2 points. |
| Max turns (arm B) | 3 | The first try plus two repairs. The third turn recovered 12 correct answers, against 133 from the second, so a longer loop would add little. |
| Retry feedback | the database's error message only | Showing expected rows would let the model converge on the answer key and measure the harness rather than the model. |
| Query deadline | 30 s | Generated SQL is untrusted; a runaway query must not stall the run. 10–18 attempts per arm hit it and are recorded as timeouts. The same deadline applied to reference queries, which is why two questions are excluded ([below](#why-the-dataset-is-not-plain-bird)). |
| Database access | read-only | One generated `DROP` or `UPDATE` would corrupt every later attempt. |

**Not addressed:** results at temperature 0 and at higher temperatures. The runner refuses
temperature 0 unless `--allow-greedy` is passed, because a greedy arm answers a different
question (is decoding deterministic?) from the one the published arms answer. Accuracy varying
across runs under settings meant to be deterministic has been reported for five LLMs (Atil et
al., [arXiv:2408.04667](https://arxiv.org/abs/2408.04667)); it has not been measured here.

## Related work

The all-attempts metric is not new. τ-bench defines pass^k for tool-using agents;
[DySQL-Bench](https://arxiv.org/abs/2510.26495) reports a repeated-trial Pass@5 for multi-turn
text-to-SQL that sits far below its single-trial accuracy (23.81% vs 58.34% for GPT-4o), i.e. an
all-trials score; [Knowing When to Stop](https://arxiv.org/abs/2607.03991) uses execution
agreement across repeated samples as a stopping rule for text-to-SQL inference.
[Dr.Spider](https://arxiv.org/abs/2301.08881) measures robustness to perturbed questions and
schemas, the input-side counterpart of the output-side variation measured here. On reporting,
Miller ([arXiv:2411.00640](https://arxiv.org/abs/2411.00640)) treats evaluation questions as a
sample from a larger population and gives formulas for error bars and for comparing two models on
the same questions, which is the view the paired intervals below take.

This study's contribution is narrower: single-turn pass@k vs pass^k on an expert-corrected answer
key, with failures split by whether they raise an error, the reach of an error-driven retry loop
measured directly, and the effect of model size on the gap — each difference with a paired
interval.

## Why the dataset is not plain BIRD

BIRD (Li et al., NeurIPS 2023, [arXiv:2305.03111](https://arxiv.org/abs/2305.03111)) is the
standard benchmark. Jin et al. ([arXiv:2601.08778](https://arxiv.org/abs/2601.08778), VLDB 2026)
audited the benchmarks the field cites and found annotation error rates of **52.8% on BIRD
Mini-Dev** and **62.8% on Spider 2.0-Snow**. Re-scoring 16 published agents against corrected
labels moved results by −7% to +31% relative, and shifted leaderboard ranks by up to nine places.

That is fatal for a study like this one specifically. Measuring disagreement between repeated
runs on a dataset where half the answer key is wrong measures how *creatively* a model goes
wrong, not how *reliably* it goes right.

So this uses **[Arcwise-Plat-SQL](https://github.com/uiuc-kang-lab/text_to_sql_benchmarks)** —
498 of BIRD Mini-Dev's 500 questions across 11 databases, with SQL annotations corrected by
database experts (CC BY-SA 4.0). The data is fetched on demand, never vendored here, and pinned:
upstream commit `b83d8a899c3e` ("more corrections", 2026-08-27), sha256 `e7bf7640…8395`, checked
on every load.

**What this version does not fix.** Arcwise-Plat-SQL corrects the SQL only and, by the upstream
authors' design, keeps the original ambiguities in the questions and evidence. For a consistency
study that matters: a model that switches between two defensible readings of an ambiguous
question is scored as flaky. For example, q242's evidence says `SUBSTR(atom_id, 7, 2)` while the
reference uses `SUBSTR(atom_id, -2)`. The share of the gap this explains is not measured. Upstream
also publishes Arcwise-Plat, which resolves the ambiguities and schema issues for the same 498
question ids; rerunning on it is the first item under [Next](#next).

Each prompt includes BIRD's evidence hint, which is BIRD's standard (oracle-knowledge) setting.

**Two questions are excluded throughout** (q518 and q701): their reference SQL runs, but takes
longer than the 30 s deadline, so no reference rows exist to score against. Given time they
complete (147–160 s and 742–823 s across two timing runs on the reference machine). 496 are scored.

## How answers are scored

Execution-based, never string matching. Both queries run against the local SQLite database and
their **result sets** are compared — there are many correct ways to write the same query, and a
scorer that punishes rewrites would report model variance that is really scorer variance.

No model grades another model anywhere in the pipeline.

| Rule | Why |
|---|---|
| Rows compared as a **multiset** | Set comparison silently loses duplicate rows, which are meaningful in SQL |
| Row order ignored **unless the gold query has a top-level ORDER BY** | Without ORDER BY, SQL guarantees no particular order. An ORDER BY inside a subquery, CTE or window `OVER (...)` picks rows or computes a value; it does not order the result |
| Column order resolved by **searching alignments** | Column order is not specified by the question |
| Extra columns tolerated by default | A query that answers the question *and* returns the id it grouped by has answered the question |
| `1` and `1.0` are equal; strings trimmed | SQLite is loosely typed; representation is not semantics |
| An empty or comment-only query is an **execution error** | SQLite runs it without complaint and returns no rows; counted as executed it could match an empty reference and would never trigger a retry |
| Queries run **read-only**, with a deadline | Generated SQL is untrusted: one hallucinated `DROP` would corrupt every later run |

Scoring is recorded under **both** readings of the extra-columns rule. The headline is lenient;
the strict reading changes 2.5–3.2% of matches depending on the arm.

A harness problem must never be recorded as a model failure, so outcomes are kept distinct:
generation failed, prompt overran the context window, SQL did not execute, query timed out, and
query ran and returned rows that did not match the reference are five different things in the
results file.

### Robustness: BIRD official scoring

This scorer is stricter than BIRD's official execution accuracy, which compares the two results
as **sets** (`set(pred) == set(gold)`: duplicates and row order ignored, no normalisation).
Re-executing every attempt and scoring it BIRD's way:

| | pass@1 | pass@10 | pass^10 | gap | silent : crash |
|---|---|---|---|---|---|
| A — 7B, this scorer | 43.0% | 50.0% | 36.1% | 13.9 | 2.53 |
| A — 7B, BIRD EX | 46.5% | 53.0% | 38.7% | 14.3 | 2.31 |
| B — 7B retry, this scorer | 46.6% | 54.4% | 39.7% | 14.7 | 5.37 |
| B — 7B retry, BIRD EX | 49.6% | 56.7% | 43.3% | 13.3 | 5.01 |
| C — 3B, this scorer | 29.8% | 42.5% | 18.8% | 23.8 | 0.87 |
| C — 3B, BIRD EX | 32.6% | 45.4% | 21.6% | 23.8 | 0.80 |

Every conclusion survives. Under BIRD EX the retry loop's gap change is −1.0 (95% CI −4.0 to
+2.0: still no detectable change) and the 3B's is +9.5 (+4.6 to +13.9). The absolute scores are
2.8–3.5 points higher, so they are not comparable with numbers produced by BIRD's official script.
It also means the "ran, rows didn't match" share includes some attempts whose rows differ from
the reference only in duplicates or order. Recomputed by an independent script, not the repo's
scorer.

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
10,000 resamples (with replacement) of the per-question differences. Questions are resampled, not
attempts — questions are the independent unit, and resampling attempts would give an interval
far too narrow. Pairing cancels out the fact that some questions are simply harder than others.
Resampling whole databases instead (11 clusters) widens the intervals but changes no
conclusion: gap change B − A [−3.1, +4.7], C − A [+4.0, +15.7].

**Comparisons use the intersection.** [`compare_arms.py`](../scripts/compare_arms.py) aligns
arms on the questions they all answered, and holds k to the shallowest arm.

## Full results

| arm | pass@1 | pass@10 | pass^10 | gap | inconsistent | ran, rows didn't match | errors |
|---|---|---|---|---|---|---|---|
| A — 7B single | 43.0% | 50.0% | 36.1% | 13.9 | 13.9% | 40.8% | 15.9% |
| B — 7B retry | 46.6% | 54.4% | 39.7% | 14.7 | 14.7% | 45.0% | 8.0% |
| C — 3B single | 29.8% | 42.5% | 18.8% | 23.8 | 23.8% | 32.8% | 37.3% |

Timeouts (0.2%, 0.4%, 0.2%) are the remainder of each row.

| Difference vs A, k=10 | pass@k | 95% CI | pass^k | 95% CI | gap | 95% CI | better / worse |
|---|---|---|---|---|---|---|---|
| B — retry | +4.4 | [+2.4, +6.7] | +3.6 | [+1.2, +6.0] | +0.8 | [−2.2, +3.8] | 28 / 10 |
| C — 3B | −7.5 | [−11.7, −3.2] | −17.3 | [−21.4, −13.3] | +9.9 | [+5.0, +14.7] | 16 / 102 |

Arm B's gap interval includes zero, so it is reported as no detectable change, not as "no
change": the interval cannot rule out a shift of a couple of points either way. Arm C's
widening is clear of zero. As a ratio, the 3B's gap is 1.71× the 7B's (95% CI 1.33–2.26).

The gap at k = n is also the share of partly-solved questions, so it rises and falls with
capability. A capability-free view: of the questions a model answers correctly at least once,
the share it answers correctly every time (pass^10 / pass@10) is **72.2%** for the 7B, 73.0% for
the 7B with retries, and **44.1%** for the 3B.

Arm B's retry loop fired on 16.0% of attempts; 47.5% of those ended up executing and 18.3% ended
up correct — that is its success rate on the failures it can reach. Counted against every miss
the arm would otherwise have had, silent ones included, it fixed 5.2%.

Per-arm configuration, timings and token counts: [RUN-LOG.md](RUN-LOG.md).

### Scorer changes after publication

On 2026-09-26 two scorer bugs found in an external audit were fixed, and every published attempt
was re-scored with [`scripts/rescore.py`](../scripts/rescore.py), which re-executes each attempt's
stored SQL against the same databases and rewrites only the verdict fields:

1. **ORDER BY below the top level forced row order.** The order check matched ORDER BY anywhere
   in the reference query, including inside subqueries and window clauses (12 scored questions).
   Only a top-level ORDER BY counts now. This flipped 17 verdicts from miss to match, all on one
   question (q728: 4 in A, 10 in B, 3 in C), whose predictions returned the right rows in an order
   the reference never specified.
2. **An empty query counted as executed.** It is an execution error now. No published attempt
   had an empty query, so this changed nothing.

Every execution status came out the same as recorded, which is what makes a re-score equivalent
to a re-run here. The headline figures that moved (arm A): pass@1 42.9 → 43.0, pass@10 49.8 →
50.0, gap 13.7 → 13.9; pass^10 is unchanged at 36.1. The full old → new list is in the commit
that made the change, and the previous verdicts are in git history.

## Next

1. **Arcwise-Plat (full)** on arm A: same 498 ids, ambiguities resolved. Measures how much of the
   gap is question ambiguity rather than model variance.
2. **Temperature 0** on the 7B (`--allow-greedy`): how deterministic greedy decoding is on a
   local GPU, and what gap is left.
3. **The 3B at 8-bit**, to separate size from quantisation, and a current-generation open model
   that fits 8 GB.
4. **Per-attempt seeds** (seed = run index) and the model digest recorded in every attempt.
5. A longer deadline for reference queries only (they run once per question), so q518 and q701
   can be scored.

## Install and test

```bash
uv sync --locked --extra dev
uv run pytest                                       # 285 tests; 16 need the databases or internet
uv run pytest -m "not network and not databases"    # the 269 that CI runs
```

Requires Python 3.11+. CI runs on 3.11, 3.12 and 3.13.

## Reproduce

```bash
uv run python scripts/fetch_databases.py          # 346 MB, one time; sha256-checked
ollama pull qwen2.5-coder:7b
ollama pull qwen2.5-coder:3b

uv run python scripts/run_arm.py --arm local-7b-single  --k 10
uv run python scripts/run_arm.py --arm local-7b-agentic --k 10 --max-turns 3
uv run python scripts/run_arm.py --arm local-3b-single  --k 10 --model qwen2.5-coder:3b

uv run python scripts/report.py results/*.jsonl                        # each arm on its own
uv run python scripts/compare_arms.py results/*.jsonl --baseline local-7b-single

uv run python scripts/report.py                   # the published results/final/, for comparison
uv run python scripts/rescore.py                  # re-score results/final/ with the current scorer
```

Any run can be interrupted and restarted with the same command. Exact numbers will not
reproduce — temperature is above zero by design — but the aggregates should reproduce closely.

## Layout

```
src/nl2sql_reliability/
  dataset.py   # fetch and load Arcwise-Plat-SQL, pinned by commit and sha256
  db.py        # locate BIRD databases; render schemas as DDL; pinned checksums
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
  rescore.py          # re-score recorded attempts after a scorer change, no model needed
results/final/        # every attempt from every arm, one JSON line each
tests/                # known-equivalent and known-different cases for each module
```
