"""Run SQL against a local SQLite database, safely and with a deadline.

Two things here are not incidental:

Read-only. Generated SQL is untrusted output from a language model. A hallucinated DROP or
UPDATE against a shared benchmark database would silently corrupt every subsequent run in
the study, and the corruption would look like model failure. Connections are opened
read-only so that cannot happen.

Bounded. A generated query can be accidentally quadratic. Without a deadline one bad
cross join stalls a run that is already measured in hours, so execution is interrupted
rather than waited on.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 30.0

# How often SQLite checks in with the progress handler, in virtual machine instructions.
# Small enough that the deadline is honoured promptly, large enough not to dominate runtime.
_PROGRESS_INSTRUCTIONS = 1000


@dataclass(frozen=True)
class Execution:
    """What happened when a query ran.

    A failure to execute is a normal, expected outcome here — models emit invalid SQL, and
    that is part of what the study measures. It is recorded, not raised.
    """

    ok: bool
    rows: list[tuple[Any, ...]]
    columns: list[str]
    error: str | None
    elapsed_seconds: float
    timed_out: bool = False

    @property
    def row_count(self) -> int:
        return len(self.rows)


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=1.0)


def run_query(
    db_path: str | Path,
    sql: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_rows: int | None = None,
) -> Execution:
    """Execute one query read-only and return its result set or its error.

    Args:
        db_path: SQLite file. Must exist; a missing database is an error, not an empty result.
        sql: the query to run.
        timeout_seconds: wall-clock ceiling before the query is interrupted.
        max_rows: stop fetching beyond this many rows. Guards against a generated query
            that is valid but returns the entire database.
    """
    db_path = Path(db_path)
    started = time.perf_counter()

    if not db_path.exists():
        return Execution(False, [], [], f"database not found: {db_path}", 0.0)

    connection: sqlite3.Connection | None = None
    deadline = started + timeout_seconds
    timed_out = False

    def _interrupt_if_overdue() -> int:
        nonlocal timed_out
        if time.perf_counter() > deadline:
            timed_out = True
            return 1  # non-zero aborts the running statement
        return 0

    try:
        connection = _connect_readonly(db_path)
        connection.set_progress_handler(_interrupt_if_overdue, _PROGRESS_INSTRUCTIONS)
        cursor = connection.execute(sql)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(max_rows) if max_rows is not None else cursor.fetchall()
        elapsed = time.perf_counter() - started
        return Execution(True, [tuple(r) for r in rows], columns, None, elapsed)
    except sqlite3.Error as exc:
        elapsed = time.perf_counter() - started
        message = f"query interrupted after {timeout_seconds}s" if timed_out else str(exc)
        return Execution(False, [], [], message, elapsed, timed_out=timed_out)
    finally:
        if connection is not None:
            connection.set_progress_handler(None, 0)
            connection.close()
