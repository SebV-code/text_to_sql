"""Tests de l'orchestration et de l'interface Streamlit."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from database import (
    ColumnSchema,
    DatabaseSchema,
    QueryResult,
    SQLiteDatabase,
    TableSchema,
)
from sql_security import SQLSecurityError

from app import ensure_demo_database, process_question


class FakeDatabase:
    dialect = "sqlite"

    def __init__(self) -> None:
        self.executed: list[tuple[str, int]] = []

    def get_schema(self) -> DatabaseSchema:
        return DatabaseSchema(
            "sqlite",
            (
                TableSchema(
                    "clients",
                    (
                        ColumnSchema("id", "INTEGER", False, True),
                        ColumnSchema("nom", "TEXT", False),
                    ),
                ),
            ),
        )

    def execute(self, sql: str, *, max_rows: int) -> QueryResult:
        self.executed.append((sql, max_rows))
        return QueryResult(("nom",), [("Alice locale",)])


def test_process_question_envoie_uniquement_question_et_schema_au_llm() -> None:
    database = FakeDatabase()
    received: dict[str, Any] = {}

    def generator(question: str, schema: str, **kwargs: Any) -> str:
        received.update(question=question, schema=schema, kwargs=kwargs)
        return "SELECT nom FROM clients"

    execution = process_question(
        "Liste les clients",
        database,
        api_key="cle",
        model="modele",
        max_rows=20,
        generator=generator,
    )

    assert received["question"] == "Liste les clients"
    assert "clients(id INTEGER PRIMARY KEY, nom TEXT NOT NULL)" in received["schema"]
    assert "Alice locale" not in received["schema"]
    assert set(received["kwargs"]) == {"api_key", "model", "app_url", "app_name"}
    assert database.executed == [(execution.sql, 20)]
    assert execution.result.rows == [("Alice locale",)]


def test_process_question_ne_execute_pas_un_sql_refuse() -> None:
    database = FakeDatabase()

    with pytest.raises(SQLSecurityError):
        process_question(
            "Supprime les clients",
            database,
            api_key="cle",
            model="modele",
            generator=lambda *_args, **_kwargs: "DELETE FROM clients",
        )

    assert database.executed == []


def test_ensure_demo_database_cree_sqlite_si_absente(tmp_path: Path) -> None:
    path = tmp_path / "demo.db"
    ensure_demo_database(path)
    schema = SQLiteDatabase(path).get_schema()
    assert schema.allowed_tables == {"clients", "produits", "commandes"}


def test_interface_affiche_question_bouton_sql_et_zone_resultats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "streamlit-demo.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(app_path).run(timeout=15)

    assert not app.exception
    assert app.title[0].value == "Text-to-SQL"
    assert app.text_input[0].label == "Votre question en français"
    assert app.button[0].label == "Générer et exécuter"
    assert any("SQL" in markdown.value for markdown in app.markdown)
    assert any("Résultats" in markdown.value for markdown in app.markdown)
