# What this project is, in plain language

This document assumes no background. If you know what pass@k is and why BIRD's labels are
disputed, the [README](../README.md) is the shorter read.

---

## 1. The one-paragraph version

You can ask an AI to turn a plain English question into a database query. "How many customers
are in Mumbai?" becomes `SELECT COUNT(*) FROM customer WHERE city = 'Mumbai'`. Published
scoreboards say the good models get this right 60–70% of the time. **But those scores come from
asking each question once.** AI models are not deterministic — ask the same question twice and
you can get two different answers. So this project asks each question **ten times** and measures
something the scoreboards do not: not "can the model get this right?" but "**does it get this
right every single time?**"

Those two numbers are not the same, and the distance between them is the finding.

---

## 2. A real example from this study

Here is an actual case, taken from the run in progress. Same model, same question, same prompt,
ten times.

**The question:** *"What is the foreign name of the card in French of type Creature, normal
layout and black border color, by artist Matthew D. Wilson?"*

The database has two tables: `cards` (the English card data) and `foreign_data` (translations).
The question asks for the **French** name, so the answer must come from `foreign_data`.

On some runs the model wrote this:

```sql
SELECT T2.name
FROM cards AS T1 JOIN foreign_data AS T2 ON T1.uuid = T2.uuid
WHERE T1.artist = 'Matthew D. Wilson' AND T1.borderColor = 'black'
  AND T1.types = 'Creature' AND T1.layout = 'normal' AND T2.language = 'French'
```

On other runs it wrote this:

```sql
SELECT T1.name
FROM cards AS T1 JOIN foreign_data AS T2 ON T1.uuid = T2.uuid
WHERE T1.artist = 'Matthew D. Wilson' AND T1.borderColor = 'black'
  AND T1.types = 'Creature' AND T1.layout = 'normal' AND T2.language = 'French'
```

**Spot the difference: `T2.name` versus `T1.name`. One character.**

The first returns the French name. The second returns the English name. The question asked for
the French one.

Across ten identical prompts, the model produced: `Y Y Y n n Y n Y n n` — right five times,
wrong five times.

Three things make this the whole point of the project:

1. **The wrong query is not broken.** It has correct syntax, it runs without error, it returns
   exactly one row that looks like a perfectly good answer. Nothing anywhere reports a problem.
2. **A scoreboard would hide it.** Asked once, this question is a coin flip — it might be
   recorded as a success. Measured as "did any of ten attempts work?", it scores 100%.
3. **Production would break.** If this query sat inside a reporting pipeline, half your reports
   would quietly show the wrong language, and nobody would get an alert.

That is what "unreliable" means here. Not crashes. **Silent, plausible, intermittent wrongness.**

---

## 3. The two numbers we measure

Say you ask the same question 10 times and the model gets it right 5 times.

| Question you're asking | Name | What this example scores |
|---|---|---|
| Could it do it at all? | **pass@k** | High — it managed it repeatedly |
| Does it do it *every* time? | **pass^k** | Low — it failed half the attempts |

- **pass@k** — "at least one of k attempts was correct." This is **capability**. It is roughly
  what public leaderboards report.
- **pass^k** — "**all** k attempts were correct." This is **reliability**. It is what you
  actually depend on when the query runs unattended at 3am.

An analogy. You're hiring someone and you give them the same task ten times. If they get it
right at least once, they are *capable* of the work. If they get it right all ten times, they
are *reliable*. You would not put a person in the first group in charge of production. The
industry currently reports the first number and quietly implies the second.

**The gap between the two is what this project measures.**

Both numbers are computed with the standard unbiased estimators — the ones that account for
having sampled a limited number of attempts rather than naively multiplying probabilities.
Getting this wrong in the obvious way understates capability and overstates reliability, so the
implementation follows Chen et al. (2021) rather than the intuitive shortcut.

---

## 4. Why we do not use the famous benchmark as-is

The standard test set for this task is called **BIRD**. We use a corrected version of it, and
the reason matters.

In 2026, researchers audited these benchmarks and found that **52.8% of the answers in BIRD
Mini-Dev were wrong** — not the models' answers, the *answer key* itself. When they re-scored 16
published AI systems against corrected labels, rankings moved by up to nine places.

For most studies that is bad. For this one it would be fatal. We are measuring whether a model
**disagrees with itself**. If half the answer key is wrong, we would not be measuring how
reliably a model gets things right — we would be measuring how *consistently* it gets them
wrong, which is a different and useless number.

So this study uses **Arcwise-Plat-SQL**: 498 of those questions across 11 databases, with the
correct answers rewritten by database experts. The data is downloaded when you run the code, not
copied into this repository, because it carries a share-alike licence that belongs to its
authors.

---

## 5. What, specifically, we are testing

Three runs of the same 498 questions, each repeated 10 times. That is ~15,000 generated queries.

| # | Setup | The question it answers |
|---|---|---|
| **A** | 7-billion-parameter model, one attempt per question | What is the reliability gap at all? |
| **B** | Same model, but shown its own error and allowed to retry | Does letting an agent fix itself actually help? |
| **C** | Smaller 3-billion-parameter model, one attempt | Does a bigger model buy reliability, or just capability? |

**Why B is interesting.** "Agentic" AI — let the model run its query, read the error, and try
again — is currently assumed to be better. But it can only fix queries that *fail*. In the
example above, the wrong query ran perfectly. An agentic loop would have looked at it, seen no
error, and moved on. So we expect self-correction to help less than people assume, and we will
have the numbers either way. We also count the extra tokens it spends, because "slightly better
and three times the cost" is a real result.

**Why C is interesting.** If the 3B model has a *similar* gap to the 7B, then reliability is not
something you buy by scaling up, and that changes how you'd build a product. B and C use the
same model family, so the comparison isolates size rather than confounding it with different
training data.

---

## 6. How it works, step by step

For one question, one attempt:

1. **Read the database structure** and write it out as `CREATE TABLE` statements.
2. **Build a prompt** — the structure, the question, and BIRD's human-written hint.
3. **Ask the model** for SQL.
4. **Pull the SQL out of the reply** — models wrap it in code fences, in prose, or in nothing.
5. **Run both queries** — the model's and the known-correct one — against the real database.
6. **Compare the results**, not the query text.
7. **Write the whole attempt to disk** — the raw reply, the SQL, the verdict, the timings.

Then repeat that 10 times per question, for 498 questions, for each setup.

**Step 6 is where studies like this usually go wrong.** There are many correct ways to write the
same query. If you compared the SQL text, you would score a model wrong for using a `JOIN` where
the answer key used a subquery — and you would report scorer noise as model unreliability. So we
run both queries and compare what comes back:

| Rule | Why it has to be this way |
|---|---|
| Rows compared as a multiset | Set comparison silently loses duplicate rows, which mean something in SQL |
| Row order ignored unless the correct query sorts | Without `ORDER BY`, SQL promises no particular order |
| Column order resolved by searching alignments | The question never specified column order |
| `1` and `1.0` treated as equal | SQLite is loosely typed; how a number is stored is not what it means |
| Queries run **read-only**, with a time limit | Generated SQL is untrusted output. One hallucinated `DROP TABLE` would corrupt the benchmark for every later run |

---

## 7. What this will let us say — and what it won't

**What we will be able to state, with evidence anyone can re-check:**

- The size of the gap between capability and reliability for these models on this task.
- Whether agentic self-correction closes that gap, and what it costs in tokens to try.
- Whether model size buys reliability or only capability.
- Which questions are unstable, with the actual queries showing how the model contradicted
  itself.

**What we will *not* claim:**

- That these results transfer to models we did not test, especially large hosted ones.
- That the absolute accuracy numbers are comparable to BIRD's public leaderboard. They are not —
  different answer key, different scoring rules, deliberately so.
- Anything about a model version other than the exact one recorded, on the date recorded. Hosted
  model endpoints change silently; an undated benchmark number is not a measurement.

If the gap turns out to be **small**, that gets published too. It would disconfirm something the
field widely assumes, which is a real result. The study is not designed to produce a dramatic
number.

---

## 8. How this study tries not to fool itself

The easiest way to produce an impressive reliability number is to accidentally measure your own
code. Specific defences:

- **A harness failure is never recorded as a model failure.** Five outcomes stay separate in the
  results: generation failed, the prompt overflowed the context window, the SQL wouldn't parse,
  the query timed out, and the query ran but returned the wrong rows. Collapsing those into "the
  model was wrong" would inflate exactly the number the project is reporting.
- **A prompt that gets silently truncated is flagged, not scored.** Ollama drops the front of an
  over-long prompt without telling you — and the front is where the database structure lives. The
  model would then answer from a partial schema and look unreliable through no fault of its own.
- **Both strict and lenient scoring are published.** Does a query that answers the question *and*
  returns one extra column count as correct? Reasonable people differ. Rather than pick one
  quietly, every attempt records both verdicts and the report prints how many results depend on
  the lenient reading.
- **Self-correction is never shown the correct answer.** The agentic setup gets the database's
  error message and nothing else. A model allowed to see the answer key would converge on it, and
  the resulting number would mean nothing.
- **The model that grades is not the model being graded.** There is no AI judge here at all. A
  query is correct if running it produces the correct rows. That is a fact about a database, not
  an opinion.
- **Every raw reply is kept.** Any number in the write-up can be recomputed from the results
  files, by anyone, without re-running a single model call.
- **Temperature 0 is refused by the runner.** At temperature 0 every repetition is near-identical,
  the gap collapses to zero, and the result would describe the decoding setting rather than the
  model.

---

## 9. Cost, and why that was a design constraint

**This study costs nothing to run or reproduce.** No paid API, no cloud, no hosting.

Everything runs locally on one desktop computer (Ryzen 5 3600, 16 GB RAM, RTX 3070 with 8 GB of
video memory). Measured: about 3 seconds per query, 22–32 tokens per second, using 5.8 GB of
video memory. One complete setup — 498 questions, 10 repetitions — takes roughly 4.5 hours.

This is deliberate, not a compromise. Reliability only becomes visible with repetition, and
repetition is exactly what a metered API makes expensive. Running locally removes the quota,
which is why `k` can be 10 here instead of the 2 or 3 a free tier would allow. A reliability
figure at k=3 is barely a figure.

It also means a reader with no budget can reproduce the whole thing, which is the difference
between a result and a claim.

---

## 10. Current status

**The full pipeline is built and tested. No results are published yet.**

- 229 automated tests passing; continuous integration green on Python 3.11, 3.12 and 3.13.
- Verified end to end against the real databases.
- Setup A is running now. B and C follow.

The example in §2 is a real observation from the run in progress. It is an illustration of the
phenomenon, **not a result** — single examples never are. The aggregate numbers will be published
only when a complete run supports them.

---

## 11. Glossary

| Term | Plain meaning |
|---|---|
| **NL2SQL / text-to-SQL** | Turning an English question into a database query |
| **pass@k** | Did at least one of k attempts work? Capability. |
| **pass^k** | Did *all* k attempts work? Reliability. |
| **Execution accuracy** | Judging a query by the rows it returns, not the text it's written in |
| **BIRD** | The standard text-to-SQL benchmark, whose answer key has documented errors |
| **Arcwise-Plat-SQL** | The expert-corrected subset of BIRD this study uses |
| **Agentic loop** | Let the model run its query, read the error, and try again |
| **Temperature** | How random the model's output is. Above zero, the same prompt can give different answers — which is the entire reason this study exists. |
| **Quantisation (Q4)** | Compressing a model so it fits in less memory, at a small cost in quality |
| **Prompt cache** | Providers charge less for a prompt whose opening is unchanged, which is why questions are grouped by database |
| **Ollama** | Software for running AI models on your own computer |

---

## 12. Sources

- Annotation errors in text-to-SQL benchmarks — Jin et al., VLDB 2026:
  [arXiv:2601.08778](https://arxiv.org/abs/2601.08778)
- Corrected dataset: [uiuc-kang-lab/text_to_sql_benchmarks](https://github.com/uiuc-kang-lab/text_to_sql_benchmarks)
  (CC BY-SA 4.0)
- pass@k estimator — Chen et al., 2021: [arXiv:2107.03374](https://arxiv.org/abs/2107.03374)
- pass^k as a reliability measure — τ-bench, Sierra:
  [arXiv:2406.12045](https://arxiv.org/abs/2406.12045)
- BIRD benchmark: [bird-bench.github.io](https://bird-bench.github.io/)
