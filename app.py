"""Interface Streamlit sobre pour interroger une base en français."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import streamlit as st

from database import Database, QueryResult, create_database
from llm import DEFAULT_MODEL, LLMError, generate_sql
from scripts.create_demo_db import create_demo_database
from sql_security import SQLSecurityError, validate_and_limit_sql


@dataclass(frozen=True)
class QueryExecution:
    """Réunit le SQL validé et son résultat, tous deux conservés localement."""

    sql: str
    result: QueryResult


def _setting(name: str, default: str = "") -> str:
    """Lit d'abord l'environnement, puis les secrets privés de Streamlit."""

    environment_value = os.getenv(name)
    if environment_value is not None:
        return environment_value
    try:
        value = st.secrets.get(name, default)
    except Exception:
        # Streamlit lève une exception spécifique quand aucun fichier de secrets
        # n'existe. L'application locale doit tout de même démarrer sans lui.
        return default
    return str(value) if value is not None else default


def _positive_integer_setting(name: str, default: int) -> int:
    """Valide localement une valeur numérique de configuration."""

    try:
        value = int(_setting(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def ensure_demo_database(path: str | Path) -> Path:
    """Crée la démonstration synthétique seulement si elle est absente."""

    database_path = Path(path)
    if not database_path.exists():
        create_demo_database(database_path)
    return database_path


def process_question(
    question: str,
    database: Database,
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    max_rows: int = 100,
    generator: Callable[..., str] = generate_sql,
    app_url: str = "http://localhost:8501",
    app_name: str = "Text-to-SQL local",
) -> QueryExecution:
    """Exécute le flux complet sans jamais reboucler vers le modèle.

    L'appel LLM a lieu une seule fois, avant tout résultat ou erreur SQL. Sa
    signature ne reçoit que la question et ``schema.to_prompt()`` ; les valeurs
    de configuration restantes concernent exclusivement OpenRouter.
    """

    schema = database.get_schema()
    raw_sql = generator(
        question,
        schema.to_prompt(),
        api_key=api_key,
        model=model,
        app_url=app_url,
        app_name=app_name,
    )
    safe_sql = validate_and_limit_sql(
        raw_sql,
        schema.allowed_tables,
        max_rows=max_rows,
        dialect=schema.dialect,
    )
    result = database.execute(safe_sql, max_rows=max_rows)
    return QueryExecution(safe_sql, result)


def run_app() -> None:
    """Construit l'unique écran Streamlit de la V1."""

    st.set_page_config(page_title="Text-to-SQL", page_icon="🔎", layout="wide")
    st.title("Text-to-SQL")
    st.caption(
        "Posez une question en français. Seuls la question et le schéma "
        "(tables, colonnes, types et relations) sont transmis à OpenRouter."
    )

    database_url = _setting("DATABASE_URL").strip()
    sqlite_path = Path(_setting("SQLITE_PATH", "demo.db"))
    if not database_url:
        ensure_demo_database(sqlite_path)

    database = create_database(
        database_url=database_url or None,
        sqlite_path=sqlite_path,
        statement_timeout_ms=_positive_integer_setting(
            "DB_STATEMENT_TIMEOUT_MS", 5000
        ),
    )
    max_rows = _positive_integer_setting("SQL_MAX_ROWS", 100)
    api_key = _setting("OPENROUTER_API_KEY")
    model = _setting("OPENROUTER_MODEL", DEFAULT_MODEL)

    question = st.text_input(
        "Votre question en français",
        placeholder="Ex. Quels sont les cinq produits les plus commandés ?",
    )
    submitted = st.button("Générer et exécuter", type="primary")

    st.markdown("### SQL généré")
    sql_placeholder = st.empty()
    st.markdown("### Résultats")
    result_placeholder = st.empty()

    if not submitted:
        sql_placeholder.caption("La requête validée apparaîtra ici.")
        result_placeholder.caption("Les lignes restent dans cette application.")
        return
    if not question.strip():
        st.warning("Saisissez une question avant de lancer la requête.")
        return
    if not api_key.strip():
        st.error(
            "OPENROUTER_API_KEY est absent. Configurez-le dans l'environnement "
            "ou dans .streamlit/secrets.toml."
        )
        return

    try:
        with st.spinner("Génération et validation de la requête…"):
            execution = process_question(
                question,
                database,
                api_key=api_key,
                model=model,
                max_rows=max_rows,
                app_url=_setting("OPENROUTER_APP_URL", "http://localhost:8501"),
                app_name=_setting("OPENROUTER_APP_NAME", "Text-to-SQL local"),
            )
    except (LLMError, SQLSecurityError) as exc:
        # Ces messages ne contiennent ni ligne métier ni secret. Surtout, ils
        # sont affichés localement et ne déclenchent jamais un second appel LLM.
        st.error(str(exc))
        return
    except Exception:
        # Une erreur du pilote peut inclure des valeurs : ne pas l'afficher et
        # ne jamais la transmettre à OpenRouter.
        st.error("La base n'a pas pu exécuter la requête validée.")
        return

    sql_placeholder.code(execution.sql, language="sql")
    if execution.result.rows:
        records = [
            dict(zip(execution.result.columns, row, strict=True))
            for row in execution.result.rows
        ]
        result_placeholder.dataframe(records, use_container_width=True)
    else:
        result_placeholder.info("La requête ne retourne aucune ligne.")


if __name__ == "__main__":
    run_app()
