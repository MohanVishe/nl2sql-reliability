"""Tests for database discovery and schema rendering."""

from __future__ import annotations

import sqlite3

import pytest

from nl2sql_reliability import db as dbmod
from nl2sql_reliability.db import (
    DatabaseNotFound,
    available,
    clear_cache,
    find_database,
    read_schema,
    render_schema,
    schema_for,
)


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    # Discovery is cached for speed across thousands of questions; tests create fresh
    # layouts per test and must not see a previous one.
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def bird_like(tmp_path):
    """A directory shaped like BIRD's dev archive: one folder per database."""
    root = tmp_path / "data" / "dev_databases"

    shop = root / "shop"
    shop.mkdir(parents=True)
    connection = sqlite3.connect(shop / "shop.sqlite")
    connection.executescript(
        """
        CREATE TABLE customer (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            city TEXT
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            total REAL,
            FOREIGN KEY (customer_id) REFERENCES customer(id)
        );
        INSERT INTO customer VALUES (1, 'alice', 'mumbai'), (2, 'bob', 'pune');
        INSERT INTO orders VALUES (1, 1, 250.0), (2, 1, 100.0), (3, 2, 75.5);
        """
    )
    connection.commit()
    connection.close()

    school = root / "school"
    school.mkdir(parents=True)
    connection = sqlite3.connect(school / "school.sqlite")
    connection.executescript("CREATE TABLE student (id INTEGER PRIMARY KEY, name TEXT);")
    connection.commit()
    connection.close()

    return tmp_path / "data"


class TestDiscovery:
    def test_finds_database_by_id(self, bird_like):
        path = find_database("shop", bird_like)
        assert path.name == "shop.sqlite"
        assert path.exists()

    def test_finds_nested_layout(self, bird_like):
        # The file sits two levels down; discovery searches rather than assuming a layout.
        assert find_database("school", bird_like).exists()

    def test_available_lists_every_database(self, bird_like):
        assert set(available(bird_like)) == {"shop", "school"}

    def test_missing_database_names_what_was_found(self, bird_like):
        with pytest.raises(DatabaseNotFound) as excinfo:
            find_database("absent", bird_like)
        message = str(excinfo.value)
        assert "absent" in message
        assert "shop" in message and "school" in message

    def test_empty_root_is_reported_clearly(self, tmp_path):
        with pytest.raises(DatabaseNotFound, match="none"):
            find_database("shop", tmp_path)

    def test_cache_is_clearable(self, bird_like, tmp_path):
        assert set(available(bird_like)) == {"shop", "school"}
        new = bird_like / "dev_databases" / "extra"
        new.mkdir(parents=True)
        sqlite3.connect(new / "extra.sqlite").close()
        clear_cache()
        assert "extra" in available(bird_like)


class TestReadSchema:
    def test_finds_all_tables(self, bird_like):
        tables = read_schema(find_database("shop", bird_like))
        assert [t.name for t in tables] == ["customer", "orders"]

    def test_columns_and_types(self, bird_like):
        tables = {t.name: t for t in read_schema(find_database("shop", bird_like))}
        columns = {c.name: c for c in tables["customer"].columns}
        assert set(columns) == {"id", "name", "city"}
        assert columns["id"].primary_key
        assert columns["name"].not_null
        assert not columns["city"].not_null
        assert columns["name"].type == "TEXT"
        assert columns["id"].type == "INTEGER"

    def test_foreign_keys(self, bird_like):
        tables = {t.name: t for t in read_schema(find_database("shop", bird_like))}
        assert tables["orders"].foreign_keys == (("customer_id", "customer", "id"),)

    def test_internal_tables_excluded(self, tmp_path):
        path = tmp_path / "x.sqlite"
        connection = sqlite3.connect(path)
        # AUTOINCREMENT forces SQLite to create its internal sqlite_sequence table.
        connection.executescript(
            "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT); INSERT INTO t DEFAULT VALUES;"
        )
        connection.commit()
        connection.close()
        assert [t.name for t in read_schema(path)] == ["t"]

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(DatabaseNotFound):
            read_schema(tmp_path / "nope.sqlite")


class TestRenderSchema:
    def test_renders_create_table(self, bird_like):
        text = schema_for("shop", bird_like)
        assert "CREATE TABLE customer (" in text
        assert "CREATE TABLE orders (" in text

    def test_marks_primary_key(self, bird_like):
        assert "id INTEGER PRIMARY KEY" in schema_for("shop", bird_like)

    def test_marks_not_null(self, bird_like):
        assert "name TEXT NOT NULL" in schema_for("shop", bird_like)

    def test_includes_foreign_key(self, bird_like):
        text = schema_for("shop", bird_like)
        assert "FOREIGN KEY (customer_id) REFERENCES customer(id)" in text

    def test_empty_database_renders_empty(self, tmp_path):
        path = tmp_path / "empty.sqlite"
        sqlite3.connect(path).close()
        assert render_schema(read_schema(path)) == ""

    def test_schema_is_valid_sql(self, bird_like, tmp_path):
        # The rendered DDL must actually parse: it is the model's only description of the
        # database, so a malformed rendering would silently degrade every prompt.
        text = schema_for("shop", bird_like)
        target = sqlite3.connect(tmp_path / "rebuilt.sqlite")
        target.executescript(text)
        rebuilt = {
            row[0] for row in target.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        target.close()
        assert rebuilt == {"customer", "orders"}


class TestSchemaSize:
    def test_schema_text_is_compact(self, bird_like):
        # Schema dominates every prompt, so its size sets the per-question quota cost.
        text = schema_for("shop", bird_like)
        assert len(text) < 1000
        assert dbmod.DEFAULT_ROOT.name == "data"
