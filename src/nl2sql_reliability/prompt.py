"""Turn a question and its schema into a prompt, and a model's reply back into SQL.

Two things happen here, and both are load-bearing for the study.

**The prefix/suffix split.** A prompt is built as a `prefix` (instructions + schema) and a
`suffix` (the question). The prefix is byte-identical for every question sharing a `db_id`,
so a provider's prompt cache can hit on it. There are 11 schemas across 498 questions and
the schema is ~618 tokens against ~280 for everything else, so caching the prefix removes
roughly two-thirds of the counted tokens on a free tier. The runner orders work by `db_id`
to keep that cache warm; see `scripts/schema_report.py` for the measurement.

**Extraction.** Models wrap SQL in prose, in fences, or in nothing at all, and the same
model does different things on different attempts. Extraction has to be forgiving, because
a parsing failure would be scored as a model failure -- which would quietly inflate exactly
the unreliability this study is trying to measure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .db import DEFAULT_ROOT, schema_for

# Kept deliberately short. Long instructions crowd out the schema, and the reliability
# question here is about the model, not about how much scaffolding can be piled around it.
INSTRUCTIONS = """\
You are an expert SQLite analyst. Given a database schema and a question, write one SQLite
query that answers it.

Rules:
- Output only the SQL, inside a ```sql code block. No explanation.
- Use only tables and columns that appear in the schema.
- The query must be read-only: a single SELECT (a leading WITH is fine).
- Do not add ORDER BY or LIMIT unless the question asks for ordering or a fixed number of rows.\
"""

# Reasoning models emit these; the SQL is never inside one.
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```(?:sql|sqlite)?[ \t]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE)
# Inside a fence, a leading SELECT or WITH is unambiguous.
_FENCED_STATEMENT = re.compile(r"\s*(SELECT|WITH)\b", re.IGNORECASE)

# In open prose it is not. "with" is an ordinary English word, so anchoring on it bare turns
# "I cannot help with that." into a SQL statement; a CTE has to look like one.
_SELECT = re.compile(r"\bSELECT\b", re.IGNORECASE)
_CTE = re.compile(r"\bWITH\s+(?:RECURSIVE\s+)?[\w\"'`\[\]]+\s+AS\s*\(", re.IGNORECASE)
_TRAILING_PROSE = re.compile(r";\s*\n\s*\S")


@dataclass(frozen=True)
class Prompt:
    """A prompt split at the point where caching stops being possible.

    `prefix` depends only on the database; `suffix` only on the question.
    """

    prefix: str
    suffix: str
    db_id: str

    @property
    def text(self) -> str:
        return f"{self.prefix}\n\n{self.suffix}"

    def __str__(self) -> str:
        return self.text


def build(schema: str, question: str, evidence: str = "", *, db_id: str = "") -> Prompt:
    """Assemble a prompt from raw parts."""
    prefix = f"{INSTRUCTIONS}\n\nSchema:\n\n{schema}"

    lines = [f"Question: {question.strip()}"]
    if evidence and evidence.strip():
        # BIRD's `evidence` is a human-written hint that disambiguates domain terms -- which
        # column encodes "eligible for free meals", what unit a figure is in. It is part of
        # the benchmark, not a prompting trick: withholding it would measure guesswork about
        # the schema rather than the model's SQL.
        lines.append(f"Hint: {evidence.strip()}")
    lines.append("SQL:")

    return Prompt(prefix=prefix, suffix="\n".join(lines), db_id=db_id)


def build_for(question, root: Path | str = DEFAULT_ROOT) -> Prompt:
    """Build the prompt for a dataset `Question`, reading its schema from disk."""
    return build(
        schema_for(question.db_id, root),
        question.question,
        question.evidence,
        db_id=question.db_id,
    )


def repair(sql: str, error: str) -> str:
    """The follow-up turn of the agentic arm: the query failed, here is why.

    Only the error is fed back, never the expected rows -- returning gold data would let the
    model converge on the answer without solving the question, and the number this study
    reports would mean nothing.
    """
    return (
        f"That query failed.\n\n"
        f"```sql\n{sql.strip()}\n```\n\n"
        f"Error: {error.strip()}\n\n"
        f"Write a corrected query. Output only the SQL, inside a ```sql code block."
    )


def extract_sql(text: str) -> str:
    """Pull the SQL statement out of a model's reply.

    Returns "" when nothing SQL-shaped is present -- an empty string executes as an error and
    is scored wrong, which is the honest outcome for a reply that contained no query.
    """
    if not text:
        return ""

    text = _THINK.sub("", text)

    blocks = [block.strip() for block in _FENCE.findall(text) if block.strip()]
    if blocks:
        # Last fence that looks like a statement: models commonly restate the schema or an
        # earlier attempt first and put the final answer last.
        for block in reversed(blocks):
            if _FENCED_STATEMENT.match(block):
                return _clean(block)
        return _clean(blocks[-1])

    # An unterminated fence is common when generation hits a token limit. A fence that closed
    # but held nothing lands here too, which is why the tail is cut at the closing marker.
    opening = re.search(r"```(?:sql|sqlite)?[ \t]*\r?\n", text, re.IGNORECASE)
    if opening:
        return _clean(text[opening.end() :].split("```")[0])

    start = _statement_start(text)
    if start is not None:
        return _clean(text[start:])

    return ""


def _statement_start(text: str) -> int | None:
    """Where a SQL statement begins in open prose, if one does."""
    starts = [match.start() for match in (_SELECT.search(text), _CTE.search(text)) if match]
    return min(starts) if starts else None


def _clean(sql: str) -> str:
    """Trim a candidate statement to just the statement."""
    sql = sql.strip()
    if not sql:
        return ""

    # Prose after the terminating semicolon ("This returns the three oldest schools.").
    end = _TRAILING_PROSE.search(sql)
    if end:
        sql = sql[: end.start() + 1]

    sql = sql.strip().rstrip(";").strip()
    return sql
