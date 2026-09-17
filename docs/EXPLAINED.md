# What this project is, explained from scratch

No background assumed. If you already know what pass@k is, the [README](../README.md) is the
shorter read.

**Contents**

1. [The short version](#1-the-short-version)
2. [The problem, from the beginning](#2-the-problem-from-the-beginning)
3. [A real example](#3-a-real-example-one-character)
4. [The two numbers](#4-the-two-numbers-we-measure)
5. [Why not the famous benchmark](#5-why-we-dont-use-the-famous-benchmark-as-is)
6. [What we are testing](#6-what-we-are-testing)
7. [How it works](#7-how-it-actually-works)
8. [What we found](#8-what-we-found)
9. [How we know the numbers are right](#9-how-we-know-the-numbers-are-right)
10. [What it means in practice](#10-what-it-means-in-practice)
11. [How the study avoids fooling itself](#11-how-the-study-avoids-fooling-itself)
12. [Cost](#12-cost-and-why-that-shaped-the-design)
13. [Status](#13-status)
14. [Glossary](#14-glossary)
15. [Sources](#15-sources)

---

## 1. The short version

**What we are doing.** Asking an AI the same database question ten times and checking whether it
gives the same correct answer every time.

**Why.** Public scoreboards ask each question *once*. AI models are random — the same question
can produce different answers. So the published scores describe a best case, not what you get in
production.

**What we found.** The model looks ~50% capable, but it is only ~36% *reliable*. Over a
quarter of its apparent ability does not survive repetition. And the failures are mostly silent
— queries that run perfectly and return the wrong data.

**And the fix everyone reaches for doesn't fix it.** Letting the model read its own error and
retry — an "AI agent" — raised both numbers but widened the gap between them, because it mostly
converts always-wrong questions into sometimes-right ones.

**The goal.** Produce a number the field does not currently publish, with the raw evidence
attached so anyone can check it.

---

## 2. The problem, from the beginning

### What "text-to-SQL" means

Databases store information in tables. To get anything out, you write a query in a language
called SQL:

```sql
SELECT COUNT(*) FROM customer WHERE city = 'Mumbai'
```

That means "count the customers in Mumbai." Writing SQL requires knowing the language *and* the
particular database's structure, so there's an obvious idea: let an AI write it. You ask
"how many customers are in Mumbai?" in English, the AI produces the SQL, the database answers.

This works well enough that companies are building products on it.

### How the field measures whether it works

Researchers use shared test sets — a few hundred questions with known-correct answers. You point
your AI at them, count how many it gets right, and publish the percentage. Good systems report
60–70%.

### The hole in that measurement

**Those scores come from asking each question once.**

AI language models are not deterministic. They pick each word using probabilities, so the same
question asked twice can produce two different answers. That's not a defect — it's how they
work, and it's what lets them be creative.

But it means a published score of 65% tells you: *when we asked these questions one time each,
65% of the answers were right.* It does not tell you what happens when you ask again.

And "ask again" is the normal case. A dashboard refreshing hourly asks the same question 24
times a day. What you need to know is not "can it do this?" but **"does it do this every
time?"**

Nobody publishes that number. This project measures it.

---

## 3. A real example: one character

This is an actual case from the completed run. Same model, same question, same prompt, ten
times.

**The question:** *"What is the foreign name of the card in French of type Creature, normal
layout and black border color, by artist Matthew D. Wilson?"*

The database has two relevant tables: `cards` (English card data) and `foreign_data`
(translations). The question asks for the **French** name, so the answer must come from
`foreign_data`.

On some runs the model wrote this:

```sql
SELECT T2.name
FROM cards AS T1 JOIN foreign_data AS T2 ON T1.uuid = T2.uuid
WHERE T1.artist = 'Matthew D. Wilson' AND T1.borderColor = 'black'
  AND T1.types = 'Creature' AND T1.layout = 'normal' AND T2.language = 'French'
```

On other runs, this:

```sql
SELECT T1.name
FROM cards AS T1 JOIN foreign_data AS T2 ON T1.uuid = T2.uuid
WHERE T1.artist = 'Matthew D. Wilson' AND T1.borderColor = 'black'
  AND T1.types = 'Creature' AND T1.layout = 'normal' AND T2.language = 'French'
```

**The difference is `T2.name` versus `T1.name`. One character.**

`T2` is the translations table, `T1` is the English table. The first query returns the French
name. The second returns the English name. The question asked for French.

Across ten identical prompts the model produced: `Y Y Y n n Y n Y n n` — right five times, wrong
five times.

Three things make this the entire point of the project:

1. **The wrong query is not broken.** Valid syntax. Runs without error. Returns exactly one row
   that looks like a perfectly reasonable answer. Nothing, anywhere, reports a problem.
2. **A scoreboard hides it.** Asked once, this is a coin flip that might land on "success."
   Measured as "did any of ten attempts work?", it scores a clean 100%.
3. **Production breaks.** Put this query in a reporting pipeline and half your reports show the
   wrong language, silently, forever.

That is what "unreliable" means here. Not crashes. **Silent, plausible, intermittent
wrongness** — the hardest kind to catch.

---

## 4. The two numbers we measure

Suppose you ask the same question 10 times and get 5 right.

| The question you're asking | Its name | This example |
|---|---|---|
| Could it do it *at all*? | **pass@k** | High — it managed it repeatedly |
| Does it do it *every single time*? | **pass^k** | Low — it failed half the attempts |

- **pass@k** — "at least one of k attempts was correct." This is **capability**. It's roughly
  what public leaderboards approximate.
- **pass^k** — "**all** k attempts were correct." This is **reliability**. It's what you depend
  on when a query runs unattended at 3am with nobody watching.

**An analogy.** You're hiring, and you give a candidate the same task ten times. If they get it
right at least once, they are *capable* of the work. If they get it right all ten times, they
are *reliable*. You would not put someone in the first group in charge of production.

The industry publishes the first number and lets readers assume the second.

**The distance between them is what this project measures.**

### A note on the maths

The naive way to compute these is to take the success rate `p` and say pass@k = `1 - (1-p)^k`.
That is wrong, and wrong in a direction that flatters the result: it *understates* capability and
*overstates* reliability. This project uses the standard unbiased estimators from Chen et al.
(2021), which account for having drawn a limited number of samples.

---

## 5. Why we don't use the famous benchmark as-is

The standard test set is called **BIRD**. We use a corrected version, and the reason matters.

In 2026, researchers audited the benchmarks everyone cites and found that **52.8% of the answers
in BIRD Mini-Dev were wrong** — not the AI's answers, *the answer key itself*. When they
re-scored 16 published systems against corrected labels, rankings moved by up to nine places.

For most studies that's a serious problem. For this one it would be fatal.

We are measuring whether a model **disagrees with itself**. If half the answer key is wrong,
we'd be measuring how *consistently* the model gets things wrong — which is a completely
different and useless number. A model that reliably produces the "wrong" answer that happens to
match a bad label would score as highly reliable.

So this study uses **Arcwise-Plat-SQL**: 498 of those questions across 11 databases, with the
correct answers rewritten by database experts. It's downloaded when you run the code, not copied
into this repository, because its licence belongs to its authors.

---

## 6. What we are testing

Three runs of the same 498 questions, each repeated 10 times — about 15,000 generated queries.

| # | Setup | The question it answers |
|---|---|---|
| **A** | 7-billion-parameter model, one attempt per question | How big is the reliability gap at all? |
| **B** | Same model, shown its own error message and allowed to retry | Does letting an AI fix itself actually help? |
| **C** | Smaller 3-billion-parameter model, one attempt | Does a bigger model buy reliability, or only capability? |

**Why B is interesting.** "Agentic" AI — let the model run its query, read the error, try again
— is widely assumed to be better. But it can only fix queries that *visibly fail*. Look back at
the example in §3: the wrong query ran perfectly. An agentic loop would see no error and move
on. So we expect self-correction to help far less than people assume — and we'll have the number
either way. We also count the extra tokens, because "marginally better at triple the cost" is a
real finding.

> That prediction is left here as written, before the run. [§8 has the answer](#setup-b-does-letting-the-ai-fix-itself-help):
> it was right about the mechanism and wrong about the headline — retrying helped more than
> expected on both metrics, while making the thing we actually measure slightly worse.

**Why C is interesting.** If the 3B model shows a *similar* gap to the 7B, then reliability isn't
something you buy by scaling up, and that changes how you'd build a product. Both are from the
same model family, so the comparison isolates size rather than confounding it with different
training data.

---

## 7. How it actually works

For one question, one attempt:

1. **Read the database structure** and write it out as `CREATE TABLE` statements.
2. **Build a prompt** — the structure, the question, and the human-written hint the benchmark
   supplies.
3. **Ask the model** for SQL.
4. **Extract the SQL** from the reply — models wrap it in code fences, in prose, or in nothing.
5. **Run both queries** — the model's and the known-correct one — against the real database.
6. **Compare the results**, not the query text.
7. **Write everything to disk** — raw reply, extracted SQL, verdict, timings, token counts.

Repeat ten times per question, for 498 questions, per setup.

### Why step 6 is the one that decides whether any of this is valid

There are many correct ways to write the same query. If you compared the SQL *text*, you'd mark
a model wrong for using a `JOIN` where the answer key used a subquery — and you'd publish scorer
noise as model unreliability. So we run both queries and compare what comes back:

| Rule | Why it has to be this way |
|---|---|
| Rows compared as a multiset | Set comparison silently drops duplicate rows, which mean something in SQL |
| Row order ignored unless the correct query sorts | Without `ORDER BY`, SQL promises no particular order |
| Column order resolved by searching alignments | The question never specified column order |
| `1` and `1.0` treated as equal | SQLite is loosely typed; how a number is stored isn't what it means |
| Queries run **read-only**, with a time limit | Generated SQL is untrusted. One hallucinated `DROP TABLE` would corrupt the benchmark for every later run |

---

## 8. What we found

**Setups A and B are complete: 9,960 attempts, 498 questions, 10 repetitions each, 9.4 hours of
computation.** Model: Qwen2.5-Coder-7B, compressed to 4-bit, running locally.

### The headline

| k (attempts) | pass@k — *can* it? | pass^k — *does it always*? | gap |
|---|---|---|---|
| 1 | 42.9% | 42.9% | 0.0 |
| 2 | 45.5% | 40.4% | 5.1 |
| 5 | 48.2% | 37.7% | 10.6 |
| **10** | **49.8%** | **36.1%** | **13.7** |

Read the bottom row like this: across 498 questions, asking each one ten times —

- **49.8%** had at least one correct answer among the ten. That's what capability looks like.
- **36.1%** were correct on all ten. That's what you can actually depend on.
- The **13.7 point** difference is questions the model got right *sometimes*.

**More than a quarter of its apparent capability doesn't survive being asked twice.**

Notice the gap *grows* with k — 5.1 at two attempts, 13.7 at ten. That is the expected shape:
every extra repetition is another chance to catch an inconsistency. It also means a study that
stops at k=2 or k=3 (as a metered API budget would force) substantially understates the problem.

### Where the failures come from

This is the table that changes how you'd build something.

| What happened | Count | Share |
|---|---|---|
| Correct | 2,130 | 42.9% |
| **Ran fine, returned wrong rows** | **2,030** | **40.9%** |
| SQL wouldn't execute | 790 | 15.9% |
| Query timed out | 10 | 0.2% |

Only **16%** of attempts failed loudly — a syntax error, a missing column, something a program
can detect and handle.

**41% of all attempts produced SQL that executed perfectly and returned the wrong answer.** No
error. No warning. Nothing for a pipeline to catch. Those are the dangerous ones, and they
outnumber the catchable failures roughly 2.6 to 1.

### Does the scoring choice matter? No.

There's a judgement call in scoring: if a query answers the question *and* returns an extra
column, does it count? Reasonable people disagree, so we recorded both verdicts for every
attempt rather than picking one quietly.

| Scoring | Matches | Share |
|---|---|---|
| Lenient (extra columns allowed) | 2,130 | 42.9% |
| Strict (exact columns only) | 2,076 | 41.9% |

Only **2.5%** of matches depend on the lenient reading. The headline is essentially unchanged
either way, so the most obvious objection to this kind of study doesn't apply here.

### Reliability depends on the database

| Database | pass@10 | pass^10 | gap |
|---|---|---|---|
| formula_1 | 57.6% | 34.8% | **22.7** |
| codebase_community | 54.2% | 33.3% | 20.8 |
| toxicology | 32.5% | 12.5% | 20.0 |
| thrombosis_prediction | 46.0% | 26.0% | 20.0 |
| card_games | 47.1% | 33.3% | 13.7 |
| superhero | 71.2% | 57.7% | 13.5 |
| european_football_2 | 54.9% | 45.1% | 9.8 |
| financial | 33.3% | 26.7% | 6.7 |
| student_club | 62.5% | 56.2% | 6.2 |
| california_schools | 16.7% | 13.3% | 3.3 |
| debit_card_specializing | 43.3% | 43.3% | **0.0** |

The gap runs from 22.7 points to zero. **Reliability is not a fixed property of the model** —
it depends heavily on the database it's pointed at. One schema produced perfect consistency;
another lost 23 points.

That's useful if you're building something: you cannot read a model's benchmark score and assume
it transfers to *your* database. You have to measure on yours.

### Setup B: does letting the AI fix itself help?

This is the "agentic" setup. Same model, same questions, same everything — but now, when the
model's query fails to run, we hand it the error message and let it try again, up to three
times. It never sees the right answer; it only sees its own mistake.

This is what most people mean by an AI agent, and it is widely assumed to be better. Here is
what it actually did.

| k | pass@k — *can* it? | pass^k — *does it always*? | gap |
|---|---|---|---|
| 1 | 46.4% | 46.4% | 0.0 |
| 5 | 52.2% | 40.9% | 11.3 |
| **10** | **54.2%** | **39.5%** | **14.7** |

Side by side with setup A:

| | A: one shot | B: retry on error | change |
|---|---|---|---|
| pass@10 (capability) | 49.8% | 54.2% | **+4.4** |
| pass^10 (reliability) | 36.1% | 39.5% | **+3.4** |
| **the gap** | **13.7** | **14.7** | **+1.0** |
| questions it's inconsistent on | 13.7% | 14.7% | +1.0 |
| time per attempt | 3.0 s | 3.8 s | 1.28× |

**Both numbers went up. So did the gap between them.** That is the result, and it is not the
one you'd guess.

Retrying genuinely helps — both improvements are real, not noise. (We checked: resampling the
questions 10,000 times, the improvement stays positive every time. See §9.) But it helps
*capability* more than it helps *reliability*. What retrying mostly does is take a question the
model used to fail on every time and turn it into a question the model now gets right
*sometimes*. On a leaderboard that's a clear win. If you're running this unattended, you have
traded a predictable failure for an unpredictable one, which is arguably worse — a query that
always breaks gets noticed and fixed, and one that breaks one time in six does not.

### Why the retry loop can't do much

The loop only fires when a query **fails to run**. Look back at §3: that wrong query executed
perfectly and returned a clean, wrong table. There is no error message to hand back. The loop
never even wakes up.

So its reach is capped in advance, and we can measure exactly how capped:

| | count | |
|---|---|---|
| Attempts that triggered a retry | 793 | 16% of all attempts |
| …that ended up running at all | 377 | 48% of those |
| …that ended up **correct** | 140 | 18% of those |
| **Of the failures it could have fixed, it fixed** | | **5.0%** |

One in twenty. And note the middle row — more than half the time, the model reads its own error
message and still produces something that won't run.

There's a cost on the other side too. Compared question by question, setup B's reliability
improved on 27 questions and **got worse on 10**. A second turn sometimes talks the model out
of a query that was already right.

**The practical takeaway:** an agent loop built on error messages can only address the failures
that announce themselves. In this study those were the minority — and the silent failures,
which are the ones that actually hurt, went *up* (40.9% → 45.2% of attempts), because queries
that used to crash now run and return something wrong instead.

---

## 9. How we know the numbers are right

A study like this fails quietly. It doesn't crash — it produces a plausible number that happens
to be wrong. So here are the checks that make the result believable, and you can verify all of
them yourself from the published data.

**Check 1 — the k=1 identity.** When you only make one attempt, "at least one was correct" and
"all were correct" are the same statement. The two numbers *must* be identical at k=1. They are:
42.9% and 42.9%. If the estimators were implemented wrongly, this is the first place it would
show.

**Check 2 — the k=n identity.** When k equals the number of attempts you actually made, the gap
between the two numbers is *mathematically identical* to the fraction of questions answered
correctly some-but-not-all of the time. These are computed by completely separate code paths:

- Gap from the estimators: 49.8 − 36.1 = **13.7 points**
- Inconsistent questions, counted directly: 68 out of 496 = **13.71%**

They agree. Two independent routes to the same quantity.

Both identities hold for setup B as well: 46.4% = 46.4% at k=1, and 54.2 − 39.5 = 14.7 points
against 73 of 496 = 14.72% inconsistent questions.

**Check 3 — no AI grades the AI.** There is no "LLM as judge" anywhere in this project. A query
is correct if running it against the real database produces the correct rows. That's a fact
about a database, not an opinion that could drift.

**Check 4 — is the A-vs-B difference real, or luck?** Setup B scored higher, but on 496
questions a few points could be chance. To check, we use a **paired bootstrap**: take the
per-question difference between the two setups, draw 496 of those differences at random (with
repeats), average them, and do that 10,000 times. That builds a picture of how much the answer
would wobble if we'd happened to pick a different set of questions.

The middle 95% of those 10,000 averages is the interval quoted earlier: **[+2.4, +6.7] for
capability and [+1.0, +5.8] for reliability**. Neither interval contains zero, so the
improvement isn't an artefact of which questions we happened to ask.

Two details matter. We resample **questions**, not attempts — questions are the independent
unit here, and resampling attempts within a question would produce an interval far too narrow
and a confidence nobody has earned. And the comparison is **paired**: each question is compared
against itself across the two setups, which cancels out the fact that some questions are simply
harder than others.

**Check 5 — 248 automated tests**, run against Python 3.11, 3.12 and 3.13 on every change,
including tests against the real databases.

**Check 6 — everything is published.** All 9,960 raw attempts are in the repository: every reply,
every extracted query, both verdicts, timings, token counts. Every number above can be
recomputed without running a model.

### Three honest caveats

**496 questions, not 498.** Two questions have reference SQL that doesn't execute against the
shipped database. They are flagged and excluded everywhere rather than being counted as model
failures — which would have been the convenient choice, since it would have made the model look
worse and the finding look bigger.

**These results describe one model, at one setting.** Qwen2.5-Coder-7B at 4-bit, temperature
0.2, measured 2026-09-16 to 2026-09-17. They do not automatically transfer to larger hosted
models, and this study doesn't claim they do.

**Setup B's retry loop is one design, not the only one.** It retries on execution errors, up to
three turns, and never sees the expected rows. A loop that also checked the result for
plausibility, or ran more turns, might do better — this study measures the common design, not
the best possible one.

---

## 10. What it means in practice

If you were building a product on this model, here's the practical translation. Out of every 100
questions:

- About **36** get a correct answer every single time. These are safe.
- About **14** get a correct answer *sometimes*. These are the dangerous ones — they'll pass your
  testing and fail in production, intermittently, with no error.
- About **50** essentially never work.

The middle group is the discovery. They don't show up as a known limitation, because on the day
you tested, they worked.

**What follows from that:**

1. **A single benchmark run tells you the wrong thing.** Test your questions repeatedly or you're
   measuring luck.
2. **Error handling is not enough.** Most failures here never raise an error. Catching exceptions
   catches 16% of the problem.
3. **Measure on your own database.** The gap varied from 0 to 22.7 points across schemas.
4. **The safe questions are genuinely safe.** 36% consistently correct is a real, usable
   capability — it just needs to be identified by repetition rather than assumed from a headline
   number.

---

## 11. How the study avoids fooling itself

The easiest way to produce an impressive reliability number is to accidentally measure your own
code. Specific defences, each guarding against a specific way this could have gone wrong:

- **A harness failure is never recorded as a model failure.** Five outcomes stay separate:
  generation failed, the prompt overflowed, the SQL wouldn't parse, the query timed out, the
  query ran and was wrong. Collapsing them into "the model was wrong" would inflate exactly the
  number being reported.
- **Truncated prompts are flagged, not scored.** The model host silently drops the front of an
  over-long prompt — and the front is where the database structure lives. The model would then
  answer from a partial schema and look unreliable through no fault of its own.
- **Both strict and lenient scoring are published** (§8), so the judgement call is visible
  rather than defended.
- **Self-correction never sees the answer.** Setup B gets the database's error message and
  nothing else. A model allowed to see the answer key would converge on it and the number would
  be meaningless.
- **Temperature 0 is refused outright.** At temperature 0 every repetition is nearly identical,
  the gap collapses to zero, and the "result" would describe a decoding setting rather than the
  model. The runner errors out rather than produce that number.
- **Every raw reply is kept**, so nobody has to trust the analysis code — including its author.

### Three bugs found while building it

Worth stating, because "it worked first time" is rarely true and usually means nobody looked.

1. **The resume bug.** Question IDs are stored as text (`"1471"`) but the code compared them as
   numbers. Nothing crashed. The effect was that resuming an interrupted run would silently redo
   work it had already done, and count the duplicates as extra repetitions — shifting both
   headline numbers. It was caught by inspecting real output, *not* by the tests, because the
   test fixtures used tidy integer IDs the real data never produces. The lesson: a test fixture
   cleaner than your real data tests nothing.
2. **The "with" bug.** Code that pulled SQL out of replies looked for the word `WITH` (a real SQL
   keyword). So the refusal *"I cannot help with that."* was parsed as a SQL statement. A parser
   bug like this inflates precisely the unreliability the project reports.
3. **The swallowed-error bug.** A network error class was caught by the wrong handler, so server
   rejections would have been silently retried instead of reported.

---

## 12. Cost, and why that shaped the design

**This study costs nothing to run or reproduce.** No paid API, no cloud, no hosting.

Everything runs on one desktop (Ryzen 5 3600, 16 GB RAM, RTX 3070 with 8 GB of video memory).
Measured at about 21 tokens per second and under 6 GB of video memory:

| | Setup A | Setup B |
|---|---|---|
| Seconds per attempt | 3.0 | 3.8 |
| Total generation time | 4.12 h | 5.28 h |
| Input tokens | 4.0 M | 5.3 M |
| Output tokens | 0.30 M | 0.40 M |

Setup B costs 28% more time and 32% more input tokens for its 4.4 points of capability — a
tradeoff worth knowing about before building a retry loop into a product.

That token count is the point. **On a typical free API tier, Setup A alone would take about
eight days** — and that's why nobody publishes this number. Reliability only becomes visible
through repetition, and repetition is exactly what a metered API makes expensive. Running
locally removes the quota, which is why k can be 10 here instead of the 2 or 3 a free tier
allows. Look again at §8: at k=2 the gap reads 5.1 points; at k=10 it reads 13.7. A budget-
constrained study would have found a third of the real effect.

It also means anyone can reproduce this, which is the difference between a result and a claim.

---

## 13. Status

| Setup | State |
|---|---|
| **A** — 7B, single attempt | ✅ Complete. 4,980 attempts published. |
| **B** — 7B, self-correcting | ✅ Complete. 4,980 attempts published. |
| **C** — 3B, single attempt | 🔄 Running |

248 tests passing. Continuous integration green on Python 3.11, 3.12 and 3.13.

Raw results: [`results/final/`](../results/final/) — one file per setup, one line per attempt.

---

## 14. Glossary

| Term | Plain meaning |
|---|---|
| **SQL** | The language used to ask databases questions |
| **Text-to-SQL / NL2SQL** | Turning an English question into a database query |
| **pass@k** | Did at least one of k attempts work? **Capability.** |
| **pass^k** | Did *all* k attempts work? **Reliability.** |
| **Execution accuracy** | Judging a query by the rows it returns, not the text it's written in |
| **Benchmark** | A shared set of test questions with known answers |
| **BIRD** | The standard text-to-SQL benchmark, whose answer key has documented errors |
| **Arcwise-Plat-SQL** | The expert-corrected subset of BIRD this study uses |
| **Agentic loop** | Letting the model run its query, read the error, and try again |
| **Temperature** | How random the model's output is. Above zero the same prompt can give different answers — the entire reason this study exists. |
| **Quantisation (Q4)** | Compressing a model to fit in less memory, at a small cost in quality |
| **Token** | Roughly a word-piece; how model usage is measured and billed |
| **Gold query / gold SQL** | The known-correct answer to a benchmark question |
| **Schema** | The structure of a database — its tables and columns |
| **Ollama** | Software for running AI models on your own computer |
| **JSONL** | A text file with one JSON record per line — how results are stored here |

---

## 15. Sources

- Annotation errors in text-to-SQL benchmarks — Jin et al., VLDB 2026:
  [arXiv:2601.08778](https://arxiv.org/abs/2601.08778)
- Corrected dataset:
  [uiuc-kang-lab/text_to_sql_benchmarks](https://github.com/uiuc-kang-lab/text_to_sql_benchmarks)
  (CC BY-SA 4.0)
- pass@k estimator — Chen et al., 2021: [arXiv:2107.03374](https://arxiv.org/abs/2107.03374)
- pass^k as a reliability measure — τ-bench, Sierra:
  [arXiv:2406.12045](https://arxiv.org/abs/2406.12045)
- BIRD benchmark: [bird-bench.github.io](https://bird-bench.github.io/)
