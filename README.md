# nl2sql-reliability

**Leaderboards report what a model *can* do. This measures how often it *actually* does it.**

Published text-to-SQL scores come from single runs. Language models are stochastic. So every
headline accuracy number describes a best case that may not reproduce — and the number a
production engineer needs is a different one: *if I ask the same question ten times, how often
do I get a correct answer every time?*

This repository measures that gap.

**New to the problem?** [docs/EXPLAINED.md](docs/EXPLAINED.md) explains the whole study in
plain language, including a real case where the model contradicted itself across ten
identical prompts by a single character.

---

## The question

For each benchmark question, each model is run **k times on an identical prompt**. Two numbers
come out:

| Metric | Meaning |
|---|---|
| **pass@k** | *Capability.* At least one of k attempts is correct. What leaderboards approximate. |
| **pass^k** | *Reliability.* **All** k attempts are correct. What an automated pipeline actually depends on. |

A model can score highly on the first and poorly on the second. That distance is the finding.

Two supporting questions:

- Does an **agentic loop** — execute, read the error, retry — improve reliability, or mostly
  spend tokens rewriting queries that were already right?
- Is **temperature 0** actually deterministic in practice?

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

## How answers are scored

Execution-based, never string matching. Both queries run against the local SQLite database and
their **result sets** are compared — because there are many correct ways to write the same
query, and a scorer that punishes rewrites would report model variance that is really scorer
variance.

The comparison rules, each of which exists because the obvious alternative is wrong:

| Rule | Why |
|---|---|
| Rows compared as a **multiset** | Set comparison silently loses duplicate rows, which are meaningful in SQL |
| Row order ignored **unless the gold query has ORDER BY** | Without ORDER BY, SQL guarantees no particular order |
| Column order resolved by **searching alignments** | Column order is not specified by the question |
| Extra columns tolerated by default | A query that answers the question *and* returns the id it grouped by has answered the question |
| `1` and `1.0` are equal; strings trimmed | SQLite is loosely typed; representation is not semantics |
| Queries run **read-only**, with a deadline | Generated SQL is untrusted: one hallucinated `DROP` would corrupt every later run |

Scoring is reported under **both** readings of the "extra columns" rule. The headline is the
lenient one, and the report prints how many matches depend on that allowance — it is the first
thing a reader should challenge, so the size of the choice is published rather than defended.

A harness problem must never be recorded as a model failure, so the outcomes are kept distinct:
generation that failed, a prompt that overran the context window, SQL that did not parse, a
query that timed out, and a query that ran and returned the wrong rows are five different
things in the results file, not one. Every attempt stores its raw reply and extracted SQL, so
the whole study can be re-scored from disk without generating anything again.

## How a run works

Each question is asked **k times on a byte-identical prompt**, and every attempt is appended to
JSONL as it completes. Three properties matter:

- **Resumable.** A full arm is thousands of generations over several hours. Completed
  `(arm, question_id, run)` keys are skipped on restart, so a crash costs minutes, not the run.
- **Ordered by database.** Consecutive questions on one database share a byte-identical prompt
  prefix, which is what a provider's prompt cache keys on — measured at a 69% token saving.
  Attempts are independent draws at temperature > 0, so ordering cannot bias the sample.
- **Gold executed once** per question, not once per repetition. A question whose reference
  query does not run is flagged and excluded, never counted against the model.

Temperature 0 is refused by the runner: every repetition would be near-identical, and the
reported gap would describe the decoder rather than the model.

```bash
uv run python scripts/fetch_databases.py            # 330 MB, one time
uv run python scripts/run_arm.py --arm local-7b --k 10
uv run python scripts/report.py
```

## Status

**Two arms complete: 9,960 attempts over 498 questions, k=10 each.** Qwen2.5-Coder-7B at Q4,
temperature 0.2, 9.4 hours of local generation.

| arm | k | pass@k | pass^k | gap |
|---|---|---|---|---|
| A — single-shot | 1 | 42.9% | 42.9% | 0.0 |
| A — single-shot | 5 | 48.2% | 37.7% | 10.6 |
| **A — single-shot** | **10** | **49.8%** | **36.1%** | **13.7** |
| B — retry on error | 1 | 46.4% | 46.4% | 0.0 |
| B — retry on error | 5 | 52.2% | 40.9% | 11.3 |
| **B — retry on error** | **10** | **54.2%** | **39.5%** | **14.7** |

More than a quarter of the model's apparent capability does not survive repetition. The failure
breakdown matters more than the headline: in arm A, 15.9% of attempts produced SQL that would
not run, and **40.9% produced SQL that ran cleanly and returned the wrong rows** — no error,
nothing for a pipeline to catch.

**Letting the model see its own error and retry moved both numbers up — and the gap up with
them.** Arm B gains +4.4 points of pass@k and +3.4 of pass^k (95% CIs [+2.4, +6.7] and
[+1.0, +5.8], paired bootstrap over questions), at 1.28× the time. But the distance between the
two metrics widens from 13.7 to 14.7 points, and the share of inconsistent questions rises from
13.7% to 14.7%. Self-correction converts hard failures into answers that are *sometimes* right:
a gain on the leaderboard metric, a loss on the one that decides whether you can leave it
running.

The loop is also narrower than it looks. Retries fire on execution errors only, so an attempt
that runs cleanly and returns wrong rows offers nothing to react to. Of the misses it was in a
position to fix, it fixed **5.0%**.

Leniency does not explain any of it: 2.5% (A) and 3.2% (B) of matches depend on accepting an
extra projected column. Per-database the gap ranges from 22.7 points to zero, so reliability is
not a fixed property of the model.

Two questions are excluded throughout because their reference SQL does not execute; 496 are
scored, not 498.

Raw per-attempt output: [`results/final/`](results/final/). Full reasoning and results in
[docs/EXPLAINED.md](docs/EXPLAINED.md); exact configuration for every arm in
[docs/RUN-LOG.md](docs/RUN-LOG.md).

The 3B arm (C) is running.

Measured on the reference machine (Ryzen 5 3600, 16 GB, RTX 3070 8 GB): 3.0 s per attempt
single-shot and 3.8 s with retries, ~21 tok/s, under 6 GB of VRAM.

## Install

```bash
uv venv
uv pip install -e ".[dev]"
uv run pytest
```

Requires Python 3.11+. Tests that hit the network are marked:

```bash
uv run pytest -m "not network"
```

## Layout

```
src/nl2sql_reliability/
  dataset.py   # fetch and load Arcwise-Plat-SQL; stratified subsets
  db.py        # locate BIRD databases; render schemas as DDL
  prompt.py    # cacheable prefix + question suffix; SQL extraction from replies
  generate.py  # model access (Ollama today); truncation reported, not swallowed
  execute.py   # read-only, deadline-bounded SQLite execution
  compare.py   # result-set equivalence
  metrics.py   # unbiased pass@k and pass^k estimators
  runner.py    # k repetitions, scoring, resumable JSONL output
scripts/
  fetch_databases.py  # download the BIRD dev databases
  schema_report.py    # measure the token budget a run will cost
  run_arm.py          # run one configuration
  report.py           # turn recorded attempts into the tables
  compare_arms.py     # arms against each other, on the questions they share
tests/         # known-equivalent and known-different cases for each
```

## Reproducibility

Every published number will ship with the prompts, model versions and quantisation, the seed,
the dates the calls were made, and the raw per-run outputs. Model endpoints change silently;
an undated benchmark number is not a measurement.

The whole study is designed to run at **zero cost** — local inference plus free API tiers — so
that a reader with no budget can reproduce it.

## Licence

MIT for the code. Arcwise-Plat-SQL is CC BY-SA 4.0 and belongs to its authors; it is downloaded
at runtime rather than redistributed here.
