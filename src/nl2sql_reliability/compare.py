"""Execution-based comparison of SQL result sets.

Two queries are equivalent if executing them produces the same result set. This module
decides "the same" carefully, because the obvious definitions are all wrong in one
direction or another:

- String comparison of the SQL itself fails every semantically identical rewrite.
- Naive row-list equality fails on row order, which is unspecified without ORDER BY.
- Naive tuple equality fails on column order, which is unspecified in the question.
- Set comparison silently loses duplicate rows, which are meaningful in SQL.

So: rows are compared as a *multiset* (duplicates preserved, order ignored) unless the
gold query pins an order with ORDER BY, and column order is resolved by searching for a
column alignment that makes the two agree.
"""

from __future__ import annotations

import itertools
import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

Row = tuple[Any, ...]
ResultSet = Sequence[Sequence[Any]]

# Ceiling on the column-alignment search. Result sets are narrow in practice, but a wide
# one must not be able to hang the harness: P(n, k) grows factorially.
MAX_ALIGNMENTS = 5040

FLOAT_PRECISION = 6

_ORDER_BY = re.compile(r"\border\s+by\b", re.IGNORECASE)


@dataclass(frozen=True)
class Comparison:
    """The verdict, plus why — the reason is what makes a failure analysable later."""

    match: bool
    reason: str

    def __bool__(self) -> bool:
        return self.match


def normalize_cell(value: Any) -> Any:
    """Collapse representational differences that are not semantic differences.

    SQLite is loosely typed and the same value arrives as int, float, str or bytes
    depending on how it was computed. Without this, `1` and `1.0` compare unequal and the
    study measures its own harness rather than the model.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, (int, float)):
        as_float = float(value)
        if math.isnan(as_float):
            return "NaN"
        if math.isinf(as_float):
            return "Infinity" if as_float > 0 else "-Infinity"
        return round(as_float, FLOAT_PRECISION)
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return value
    if isinstance(value, str):
        return value.strip()
    return value


def normalize_rows(rows: ResultSet) -> list[Row]:
    return [tuple(normalize_cell(cell) for cell in row) for row in rows]


def order_matters(gold_sql: str) -> bool:
    """Whether row order is part of the answer.

    Without ORDER BY, SQL guarantees no particular row order, so comparing sequences would
    fail correct queries at random. With it, order is part of what was asked for.

    Known limitation: an ORDER BY inside a subquery trips this heuristic, making the
    comparison stricter than it needs to be. That direction is safe — it can only report a
    correct query as incorrect, never the reverse — and it is rare enough to accept rather
    than pull in a SQL parser. Worth revisiting if the failure taxonomy shows it biting.
    """
    return bool(_ORDER_BY.search(gold_sql or ""))


def _alignments(n_pred: int, k_gold: int) -> Iterable[tuple[int, ...]]:
    """Candidate mappings from gold columns onto predicted columns.

    Ordered selections, not combinations: column order is itself unknown, so
    (0, 1) and (1, 0) are genuinely different alignments to test.
    """
    return itertools.permutations(range(n_pred), k_gold)


def _count_alignments(n_pred: int, k_gold: int) -> int:
    if k_gold > n_pred:
        return 0
    return math.perm(n_pred, k_gold)


def _project(rows: list[Row], columns: tuple[int, ...]) -> list[Row]:
    return [tuple(row[i] for i in columns) for row in rows]


def _rows_agree(gold: list[Row], pred: list[Row], ordered: bool) -> bool:
    if ordered:
        return gold == pred
    return Counter(gold) == Counter(pred)


def compare_result_sets(
    gold_rows: ResultSet,
    pred_rows: ResultSet,
    *,
    ordered: bool = False,
    allow_extra_columns: bool = True,
) -> Comparison:
    """Compare two result sets.

    Args:
        gold_rows: rows from the reference query.
        pred_rows: rows from the generated query.
        ordered: require row order to match. Derive this from the gold query with
            `order_matters`, rather than guessing.
        allow_extra_columns: accept a prediction that selects the gold columns plus
            others. A query answering the question and also returning the id it grouped by
            has answered the question. Set False for strict projection matching.
    """
    gold = normalize_rows(gold_rows)
    pred = normalize_rows(pred_rows)

    if not gold and not pred:
        return Comparison(True, "both result sets are empty")
    if len(gold) != len(pred):
        return Comparison(False, f"row count differs: gold {len(gold)}, predicted {len(pred)}")

    k_gold = len(gold[0])
    n_pred = len(pred[0])

    if any(len(row) != k_gold for row in gold):
        return Comparison(False, "gold result set is ragged")
    if any(len(row) != n_pred for row in pred):
        return Comparison(False, "predicted result set is ragged")

    if n_pred == k_gold and _rows_agree(gold, pred, ordered):
        return Comparison(True, "exact column order matches")

    if n_pred < k_gold:
        return Comparison(False, f"predicted has {n_pred} columns, gold needs {k_gold}")
    if n_pred > k_gold and not allow_extra_columns:
        return Comparison(False, f"predicted has {n_pred} columns, gold has {k_gold}")

    total = _count_alignments(n_pred, k_gold)
    if total > MAX_ALIGNMENTS:
        return Comparison(
            False,
            f"column alignment search skipped: {total} permutations exceeds cap "
            f"{MAX_ALIGNMENTS}; compared in declared column order only",
        )

    for columns in _alignments(n_pred, k_gold):
        if _rows_agree(gold, _project(pred, columns), ordered):
            extra = n_pred - k_gold
            detail = f" ignoring {extra} extra column(s)" if extra else ""
            return Comparison(True, f"matched under column alignment {columns}{detail}")

    return Comparison(False, "no column alignment reproduces the gold result set")
