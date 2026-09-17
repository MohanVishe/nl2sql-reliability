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
  spend tokens rewriting queries that were already right? *Answered by arm B: it improves both
  metrics and still widens the gap between them.*
- Does **model size** buy reliability, or only capability? *Answered by arm C: reliability falls
  more than twice as fast as capability when parameters are halved.*

Not addressed here: whether **temperature 0** is deterministic in practice. It would need its
own arm — the three above all run at 0.2, and the runner refuses temperature 0 outright, because
near-identical repetitions would make pass^k describe the decoder rather than the model.

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

**All three arms complete: 14,940 attempts over 498 questions, k=10 each**, 13.4 hours of local
generation at temperature 0.2.

| arm | pass@10 | pass^10 | gap | inconsistent |
|---|---|---|---|---|
| **A** — 7B, single-shot | 49.8% | 36.1% | **13.7** | 13.7% |
| **B** — 7B, retry on error | 54.2% | 39.5% | **14.7** | 14.7% |
| **C** — 3B, single-shot | 42.3% | 18.8% | **23.6** | 23.6% |

Read the gap column, not the first one. It is the share of questions a model can solve but does
not solve dependably, and **neither intervention closed it — both widened it.**

**Arm A establishes the problem.** More than a quarter of the model's apparent capability does
not survive repetition. The failure breakdown matters more than the headline: 15.9% of attempts
produced SQL that would not run, and **40.9% produced SQL that ran cleanly and returned the
wrong rows** — no error, nothing for a pipeline to catch.

**Arm B: self-correction helps capability more than reliability.** Showing the model its own
execution error and letting it retry gains +4.4 points of pass@k and +3.4 of pass^k (95% CIs
[+2.4, +6.7] and [+1.0, +5.8], paired bootstrap over questions), at 1.28× the time. But the gap
widens to 14.7, and silent failures rise from 40.9% to 45.2% of attempts — queries that used to
crash now run and return something wrong instead. The loop is narrower than it looks: retries
fire on execution errors only, so of the misses it was in a position to fix, it fixed **5.0%**.
It improved 27 questions and broke 10.

**Arm C: reliability falls more than twice as fast as size.** Halving the parameters costs 7.5
points of pass@k but **17.3 points of pass^k** ([−21.4, −13.3]). The gap nearly doubles to 23.6,
reliability worsened on 102 questions against 16 improved, and the one database where the 7B was
perfectly consistent opens to 16.7 points. A single-run benchmark would have put these two
models 13 points apart; on all-of-ten agreement they are 17 apart. The 3B is also **not faster**
here — 2.9 s per attempt against 3.0 — so it buys VRAM, not latency.

Leniency explains none of it: 2.5%, 3.2% and 3.0% of matches respectively depend on accepting an
extra projected column. Per-database the gap ranges from 31.4 points to zero, so reliability is
not a fixed property of a model — you cannot read a benchmark score and assume it transfers to
your schema.

Two questions are excluded throughout because their reference SQL does not execute; 496 are
scored, not 498.

Raw per-attempt output: [`results/final/`](results/final/) — every reply, every extracted query,
both verdicts, timings and token counts, so every figure above can be recomputed without running
a model. Full reasoning in [docs/EXPLAINED.md](docs/EXPLAINED.md); exact configuration for every
arm in [docs/RUN-LOG.md](docs/RUN-LOG.md).

Measured on the reference machine (Ryzen 5 3600, 16 GB, RTX 3070 8 GB) at ~21 tok/s and under
6 GB of VRAM.

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
