"""Tests de l'accès aux bases sans fuite de données."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from database import PostgresDatabase, SQLiteDatabase, create_database


def make_sqlite(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE clients (
            id INTEGER PRIMARY KEY,
            nom TEXT NOT NULL,
            email TEXT NOT NULL
        );
        CREATE TABLE commandes (
            id INTEGER PRIMARY KEY,
            client_id INTEGER NOT NULL REFERENCES clients(id),
            total NUMERIC NOT NULL
        );
        INSERT INTO clients VALUES (1, 'Alice Secret', 'alice@example.test');
        INSERT INTO commandes VALUES (1, 1, 42.50);
        """
    )
    connection.commit()
    connection.close()


def test_schema_sqlite_ne_contient_que_metadonnees_et_relations(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    make_sqlite(path)

    schema = SQLiteDatabase(path).get_schema()
    prompt = schema.to_prompt()

    assert schema.dialect == "sqlite"
    assert schema.allowed_tables == {"clients", "commandes"}
    assert "clients(id INTEGER PRIMARY KEY, nom TEXT NOT NULL, email TEXT NOT NULL)" in prompt
    assert "commandes.client_id -> clients.id" in prompt
    assert "Alice Secret" not in prompt
    assert "alice@example.test" not in prompt
    assert "42.50" not in prompt


def test_execution_sqlite_retourne_colonnes_et_lignes(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    make_sqlite(path)

    result = SQLiteDatabase(path).execute(
        "SELECT nom, email FROM clients ORDER BY id LIMIT 10", max_rows=10
    )

    assert result.columns == ("nom", "email")
    assert result.rows == [("Alice Secret", "alice@example.test")]


def test_execution_sqlite_applique_une_limite_defensive_au_fetch(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    make_sqlite(path)
    connection = sqlite3.connect(path)
    connection.executemany(
        "INSERT INTO clients VALUES (?, ?, ?)",
        [(index, f"Client {index}", f"c{index}@test") for index in range(2, 8)],
    )
    connection.commit()
    connection.close()

    result = SQLiteDatabase(path).execute("SELECT * FROM clients", max_rows=3)

    assert len(result.rows) == 3


def test_delai_sqlite_est_configurable(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "test.db", statement_timeout_ms=321)

    assert database.statement_timeout_ms == 321


@pytest.mark.parametrize("timeout_ms", [0, -1])
def test_delai_sqlite_doit_etre_positif(tmp_path: Path, timeout_ms: int) -> None:
    with pytest.raises(ValueError, match="positif"):
        SQLiteDatabase(tmp_path / "test.db", statement_timeout_ms=timeout_ms)


def test_execution_sqlite_interrompt_meme_une_fonction_native_couteuse(
    tmp_path: Path,
) -> None:
    path = tmp_path / "test.db"
    make_sqlite(path)
    database = SQLiteDatabase(path, statement_timeout_ms=1)

    with pytest.raises(RuntimeError, match="délai") as caught:
        database.execute("SELECT length(randomblob(100000000))", max_rows=1)

    assert type(caught.value).__name__ == "DatabaseQueryTimeoutError"


class FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.description = [("nom",)]

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.calls.append((sql, params))

    def fetchmany(self, size: int) -> list[tuple[str]]:
        return [("Alice",)][:size]


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = FakeCursor()
        self.committed = False
        self.rolled_back = False

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


def test_postgres_execute_en_transaction_read_only_avec_timeout() -> None:
    connection = FakeConnection()
    database = PostgresDatabase(
        "postgresql://secret@host/db",
        statement_timeout_ms=3210,
        connector=lambda _: connection,
    )

    result = database.execute("SELECT nom FROM clients LIMIT 5", max_rows=5)

    calls = connection.cursor_instance.calls
    assert calls[0] == ("SET TRANSACTION READ ONLY", None)
    assert calls[1] == ("SET LOCAL statement_timeout = 3210", None)
    assert calls[2] == ("SELECT nom FROM clients LIMIT 5", None)
    assert result.rows == [("Alice",)]
    assert connection.committed is True


def test_create_database_choisit_postgres_quand_url_presente(tmp_path: Path) -> None:
    database = create_database(
        database_url="postgresql://user:password@host/db",
        sqlite_path=tmp_path / "ignored.db",
    )
    assert isinstance(database, PostgresDatabase)


def test_create_database_choisit_sqlite_sans_url(tmp_path: Path) -> None:
    path = tmp_path / "demo.db"
    database = create_database(
        database_url=None,
        sqlite_path=path,
        statement_timeout_ms=4321,
    )
    assert isinstance(database, SQLiteDatabase)
    assert database.path == path
    assert database.statement_timeout_ms == 4321
