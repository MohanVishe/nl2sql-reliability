"""Locate BIRD databases and render their schemas.

The 498 questions run against 11 SQLite databases shipped in the BIRD dev set. Two jobs
here: find the file for a `db_id`, and turn that file into the schema text a model is shown.

Databases are located by **searching** for `<db_id>.sqlite` rather than by assuming a
directory layout. BIRD's dev archive has been restructured between releases, and a hard-coded
path silently resolving to nothing is far worse than a search that either finds the file or
says it could not: the first looks like model failure, the second looks like what it is.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_ROOT = Path("data")

# SQLite's own bookkeeping tables. Showing them to a model wastes context and invites
# queries against tables that are not part of the question.
_INTERNAL_PREFIXES = ("sqlite_",)


class DatabaseNotFound(FileNotFoundError):
    """Raised when a db_id has no matching .sqlite file under the search root."""


@lru_cache(maxsize=1)
def _index(root: str) -> dict[str, Path]:
    """Map every db_id found under `root` to its .sqlite file.

    Cached: this walks the whole data directory, and it is called once per question during
    a run of thousands.
    """
    found: dict[str, Path] = {}
    for path in Path(root).rglob("*.sqlite"):
        found.setdefault(path.stem, path)
    return found


def available(root: Path | str = DEFAULT_ROOT) -> dict[str, Path]:
    """Every database discovered under `root`, by db_id."""
    return dict(_index(str(root)))


def find_database(db_id: str, root: Path | str = DEFAULT_ROOT) -> Path:
    """Path to the SQLite file for `db_id`."""
    index = _index(str(root))
    if db_id not in index:
        known = ", ".join(sorted(index)) or "none"
        raise DatabaseNotFound(
            f"no database named {db_id!r} under {root}. Found: {known}. "
            "Run scripts/fetch_databases.py to download the BIRD dev set."
        )
    return index[db_id]


def clear_cache() -> None:
    """Forget the discovered layout. Call after downloading or moving databases."""
    _index.cache_clear()


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    primary_key: bool
    not_null: bool


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    foreign_keys: tuple[tuple[str, str, str], ...]  # (column, referenced table, its column)


def read_schema(db_path: Path | str) -> list[Table]:
    """Introspect a SQLite database into tables, columns and foreign keys."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise DatabaseNotFound(f"database not found: {db_path}")

    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        table_names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            if not row[0].startswith(_INTERNAL_PREFIXES)
        ]

        tables: list[Table] = []
        for name in table_names:
            columns = tuple(
                Column(
                    name=row[1],
                    type=row[2] or "",
                    primary_key=bool(row[5]),
                    not_null=bool(row[3]),
                )
                # PRAGMA cannot be parameterised; the name comes from sqlite_master, not
                # from user or model input, and is quoted.
                for row in connection.execute(f'PRAGMA table_info("{name}")')
            )
            foreign_keys = tuple(
                (row[3], row[2], row[4])
                for row in connection.execute(f'PRAGMA foreign_key_list("{name}")')
            )
            tables.append(Table(name=name, columns=columns, foreign_keys=foreign_keys))
        return tables
    finally:
        connection.close()


def render_schema(tables: list[Table]) -> str:
    """Render schema as CREATE TABLE statements.

    DDL rather than prose: it is the form these models saw most of during training, it is
    unambiguous about types and keys, and it is compact. Schema text dominates every prompt
    in this study, so its size directly sets how much of the free-tier quota each question
    costs.
    """
    blocks: list[str] = []
    for table in tables:
        lines = [f"CREATE TABLE {table.name} ("]
        parts: list[str] = []
        for column in table.columns:
            piece = f"  {column.name} {column.type}".rstrip()
            if column.primary_key:
                piece += " PRIMARY KEY"
            elif column.not_null:
                piece += " NOT NULL"
            parts.append(piece)
        for column_name, referenced_table, referenced_column in table.foreign_keys:
            target = referenced_table
            if referenced_column:
                target = f"{referenced_table}({referenced_column})"
            parts.append(f"  FOREIGN KEY ({column_name}) REFERENCES {target}")
        lines.append(",\n".join(parts))
        lines.append(");")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def schema_for(db_id: str, root: Path | str = DEFAULT_ROOT) -> str:
    """Rendered schema text for a db_id — what gets put in front of the model."""
    return render_schema(read_schema(find_database(db_id, root)))
