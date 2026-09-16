# nl2sql-reliability

**Leaderboards report what a model *can* do. This measures how often it *actually* does it.**

Published text-to-SQL scores come from single runs. Language models are stochastic. So every
headline accuracy number describes a best case that may not reproduce — and the number a
production engineer needs is a different one: *if I ask the same question ten times, how often
do I get a correct answer every time?*

This repository measures that gap.

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

Generation and execution are **decoupled** — all SQL is written to disk first and scored
afterwards — so that a database lock or timeout is never recorded as a model failure. The
study measures the model, not its own harness.

## Status

Under active development. The dataset loader, execution layer and result comparator are
complete and tested; model arms and the run harness are next.

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
  execute.py   # read-only, deadline-bounded SQLite execution
  compare.py   # result-set equivalence
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
