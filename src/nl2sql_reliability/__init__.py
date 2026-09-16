"""Measuring run-to-run reliability of NL2SQL agents, not just peak accuracy.

Leaderboards report pass@k — whether a model *can* produce correct SQL. Production depends
on pass^k — whether it does so *every* time. This package measures the distance between them
on a dataset whose annotations have been expert-corrected, because on the uncorrected
benchmarks roughly half the answer key is wrong and the gap would be unreadable.
"""

from nl2sql_reliability.compare import Comparison, compare_result_sets, order_matters
from nl2sql_reliability.dataset import Question, databases, load, stratified_subset
from nl2sql_reliability.execute import Execution, run_query
from nl2sql_reliability.metrics import (
    Outcome,
    SuiteResult,
    pass_at_k,
    pass_hat_k,
    summarize,
    sweep,
)
from nl2sql_reliability.prompt import Prompt, build, build_for, extract_sql, repair

__version__ = "0.1.0"

__all__ = [
    "Comparison",
    "Execution",
    "Outcome",
    "Prompt",
    "Question",
    "SuiteResult",
    "build",
    "build_for",
    "compare_result_sets",
    "databases",
    "extract_sql",
    "load",
    "order_matters",
    "pass_at_k",
    "pass_hat_k",
    "repair",
    "run_query",
    "stratified_subset",
    "summarize",
    "sweep",
]
