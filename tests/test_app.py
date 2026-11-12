"""Tests de l'orchestration et de l'interface Streamlit."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from database import ColumnSchema, DatabaseSchema, QueryResult, TableSchema
from sql_security import SQLSecurityError

from app import process_question


class FakeDatabase:
    dialect = "postgres"

    def __init__(self) -> None:
        self.events: list[str] = []
        self.executed: list[tuple[str, int]] = []

    def get_schema(self) -> DatabaseSchema:
        self.events.append("schema")
        return DatabaseSchema(
            "postgres",
            (
                TableSchema(
                    "clients",
                    (
                        ColumnSchema("id", "integer", False, True),
                        ColumnSchema("nom", "text", False),
                    ),
                ),
            ),
        )

    def execute(self, sql: str, *, max_rows: int) -> QueryResult:
        self.events.append("execute")
        self.executed.append((sql, max_rows))
        return QueryResult(("nom",), [("Alice locale",)])


def test_process_question_lit_schema_avant_appel_llm() -> None:
    database = FakeDatabase()
    received: dict[str, Any] = {}

    def generator(question: str, schema: str, **kwargs: Any) -> str:
        database.events.append("llm")
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

    assert database.events == ["schema", "llm", "execute"]
    assert received["question"] == "Liste les clients"
    assert "clients(id integer PRIMARY KEY, nom text NOT NULL)" in received["schema"]
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

    assert database.events == ["schema"]
    assert database.executed == []


def test_interface_exige_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(app_path).run(timeout=15)

    assert not app.exception
    assert app.error
    assert "DATABASE_URL" in app.error[0].value
    assert not app.text_input
    assert not app.button


def test_interface_affiche_question_bouton_sql_et_zone_resultats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://user:password@example.test/database"
    )

    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(app_path).run(timeout=15)

    assert not app.exception
    assert app.title[0].value == "Text-to-SQL"
    assert app.text_input[0].label == "Votre question en français"
    assert app.button[0].label == "Générer et exécuter"
    assert any("SQL" in markdown.value for markdown in app.markdown)
    assert any("Résultats" in markdown.value for markdown in app.markdown)
