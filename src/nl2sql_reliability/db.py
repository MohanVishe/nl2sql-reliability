"""Locate BIRD databases and render their schemas.

The 498 questions run against 11 SQLite databases shipped in the BIRD dev set. Two jobs
here: find the file for a `db_id`, and turn that file into the schema text a model is shown.

Databases are located by **searching** for `<db_id>.sqlite` rather than by assuming a
directory layout. BIRD's dev archive has been restructured between releases, and a hard-coded
path silently resolving to nothing is far worse than a search that either finds the file or
says it could not: the first looks like model failure, the second looks like what it is.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_ROOT = Path("data")

# SQLite's own bookkeeping tables. Showing them to a model wastes context and invites
# queries against tables that are not part of the question.
_INTERNAL_PREFIXES = ("sqlite_",)


# sha256 of each BIRD dev database the published results were scored against, taken from the
# local copy on 2026-09-26 (unmodified since download: every connection is read-only). That
# copy came from the Hugging Face mirror nlile/BIRD-bench at revision 3b94f13f89dc, whose
# dev_databases.zip has LFS sha256 8132aacfe61ecec7...3f317d8f. BIRD's databases are CC BY-SA 4.0.
DATABASE_SHA256 = {
    "california_schools": "986817d793479801ed55133e55aa27e335422c0cd3866b54a3d6317b7c5f09c1",
    "card_games": "c98bdb57fe7474da798b407785544b9af0daaad5d61fd21e2a73309493bc1227",
    "codebase_community": "92101be6d2a9f6adceea59d38f6d1c556087f9eea1432432a21268cb1348036d",
    "debit_card_specializing": "b3d149ad05746dbbe5116e229e17e18f09c39db43cf117d9ef3441753608b691",
    "european_football_2": "e4d361dbeec6591a4b315877c0c481da05c8ff5a3d389b34d6e17b6f15bee4e0",
    "financial": "d15d89cdb068a202b6f2b99342af44dffc1d52545b39ceaf62efdc0ba570101e",
    "formula_1": "17185981cd747f6cdc374cb02a6096db3130e6ec2ddc582fe1686a28fb4c4c8a",
    "student_club": "eb89bcfe97eefa386a27904ec5aa15159811a7eac894ec659a36e48fa9f76b77",
    "superhero": "75e94a2c3236ee3bb2c01fb97a1c4b4c1c269bcefd4eab1d04be323d2d0825b1",
    "thrombosis_prediction": "e7e16d74b4731b4b8d33fdbe8c29cd5622620788ce8d1f335631f65fc7cf1db9",
    "toxicology": "f5fa7f21af1ad878ff8fef1b0582b8cb2d7ed63dbac65ff16d2ba05667650c5b",
}


def verify_databases(root: Path | str = DEFAULT_ROOT) -> dict[str, str]:
    """Check every expected database against its pinned sha256.

    Returns {db_id: problem} for each one that is missing or differs; empty means all match.
    A database that changed under a fixed answer key changes the gold rows, and with them
    every score.
    """
    index = _index(str(root))
    problems: dict[str, str] = {}
    for db_id, expected in DATABASE_SHA256.items():
        path = index.get(db_id)
        if path is None:
            problems[db_id] = "missing"
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            problems[db_id] = f"sha256 {digest.hexdigest()[:12]}..., expected {expected[:12]}..."
    return problems


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
