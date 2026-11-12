"""Tests de l'accès PostgreSQL sans fuite de données."""

from __future__ import annotations

from typing import Any

import pytest

from database import PostgresDatabase, create_database


class FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.description = [("nom",)]
        self._rows: list[tuple[Any, ...]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.calls.append((sql, params))
        if "FROM information_schema.columns" in sql:
            self._rows = [
                ("clients", "id", "integer", "NO", True),
                ("clients", "nom", "text", "NO", False),
                ("commandes", "id", "integer", "NO", True),
                ("commandes", "client_id", "integer", "NO", False),
            ]
        elif "FROM information_schema.table_constraints" in sql:
            self._rows = [("commandes", "client_id", "clients", "id")]
        else:
            self._rows = [("Alice",)]

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        return self._rows[:size]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = FakeCursor()
        self.committed = False

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True


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


@pytest.mark.parametrize("max_rows", [0, -1])
def test_postgres_execute_exige_une_limite_positive(max_rows: int) -> None:
    database = PostgresDatabase(
        "postgresql://secret@host/db", connector=lambda _: FakeConnection()
    )

    with pytest.raises(ValueError, match="positif"):
        database.execute("SELECT 1", max_rows=max_rows)


def test_get_schema_lit_uniquement_information_schema() -> None:
    connection = FakeConnection()
    database = PostgresDatabase(
        "postgresql://secret@host/db", connector=lambda _: connection
    )

    schema = database.get_schema()
    prompt = schema.to_prompt()

    catalog_queries = [sql for sql, _ in connection.cursor_instance.calls[2:]]
    assert len(catalog_queries) == 2
    assert all("information_schema" in sql for sql in catalog_queries)
    columns_query = catalog_queries[0]
    normalized_columns_query = " ".join(columns_query.split())
    assert "kcu.constraint_catalog = tc.constraint_catalog" in normalized_columns_query
    assert "kcu.constraint_schema = tc.constraint_schema" in normalized_columns_query
    assert "kcu.constraint_name = tc.constraint_name" in normalized_columns_query
    assert "kcu.table_name = tc.table_name" in normalized_columns_query
    assert all(
        "FROM clients" not in sql and "FROM commandes" not in sql
        for sql in catalog_queries
    )
    assert schema.dialect == "postgres"
    assert schema.allowed_tables == {"clients", "commandes"}
    assert "clients(id integer PRIMARY KEY, nom text NOT NULL)" in prompt
    assert "commandes.client_id -> clients.id" in prompt
    assert connection.committed is True


def test_create_database_construit_uniquement_postgres() -> None:
    database = create_database(
        database_url="  postgresql://user:***@host/db  ",
        statement_timeout_ms=4321,
    )

    assert isinstance(database, PostgresDatabase)
    assert database.statement_timeout_ms == 4321


@pytest.mark.parametrize("database_url", [None, "", "   "])
def test_create_database_exige_database_url(database_url: str | None) -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        create_database(database_url=database_url)


@pytest.mark.parametrize("timeout_ms", [0, -1])
def test_delai_postgres_doit_etre_positif(timeout_ms: int) -> None:
    with pytest.raises(ValueError, match="positif"):
        PostgresDatabase("postgresql://secret@host/db", statement_timeout_ms=timeout_ms)
