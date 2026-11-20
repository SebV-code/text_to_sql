"""Interface Streamlit sobre pour interroger une base en français."""

from __future__ import annotations

import hmac
import html
import json
import os
from collections.abc import Callable

import streamlit as st

from database import Database, DatabaseSchema, PostgresDatabase, QueryResult
from llm import DEFAULT_MODEL, LLMError, generate_sql
from sql_security import SQLSecurityError, validate_and_limit_sql


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
) -> tuple[str, QueryResult]:
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
    return safe_sql, database.execute(safe_sql, max_rows=max_rows)


def schema_diagram_dot(schema: DatabaseSchema) -> str:
    """Dessine uniquement les tables et contraintes déclarées, sans lignes."""
    lines = [
        "digraph schema {",
        'graph [rankdir=LR, bgcolor="transparent", nodesep=0.7, ranksep=1.1];',
        'node [shape=plain, fontname="Helvetica"];',
        'edge [color="#64748b", arrowsize=0.8, fontname="Helvetica"];',
    ]
    names = {table.name for table in schema.tables}
    for table in schema.tables:
        # L'identifiant DOT et le texte HTML du label ont des échappements distincts.
        node = json.dumps(table.name, ensure_ascii=False)
        lines.append(f'{node} [label=<')
        lines.append('<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="7" COLOR="#94a3b8">')
        lines.append(f'<TR><TD BGCOLOR="#1e293b" COLSPAN="2"><FONT COLOR="white"><B>{html.escape(table.name)}</B></FONT></TD></TR>')
        foreign_columns = {fk.column for fk in table.foreign_keys}
        for index, column in enumerate(table.columns):
            badge = " ".join(tag for tag, present in (("PK", column.primary_key), ("FK", column.name in foreign_columns)) if present)
            port = f"c{index}"
            lines.append(f'<TR><TD PORT="{port}" ALIGN="LEFT">{html.escape(column.name)}</TD><TD ALIGN="LEFT"><FONT COLOR="#64748b">{html.escape(column.data_type)} {badge}</FONT></TD></TR>')
        lines.append('</TABLE>>];')
    for table in schema.tables:
        for fk in table.foreign_keys:
            if fk.reference_table not in names:
                continue
            source = table.name
            target = fk.reference_table
            # Les ports pointent sur les colonnes, pas seulement les tables.
            source_column = next((i for i, col in enumerate(table.columns) if col.name == fk.column), None)
            target_table = next(t for t in schema.tables if t.name == target)
            target_column = next((i for i, col in enumerate(target_table.columns) if col.name == fk.reference_column), None)
            if source_column is not None and target_column is not None:
                lines.append(f'{json.dumps(source)}:c{source_column} -> {json.dumps(target)}:c{target_column} [label={json.dumps(fk.column + " → " + fk.reference_column, ensure_ascii=False)}];')
    return "\n".join([*lines, "}"])


def run_app() -> None:
    """Construit l'unique écran Streamlit de la V1."""

    st.set_page_config(page_title="Text-to-SQL", page_icon="🔎", layout="wide")
    st.title("Text-to-SQL")
    st.caption(
        "Posez une question en français. Seuls la question et le schéma "
        "(tables, colonnes, types et relations) sont transmis à OpenRouter."
    )

    demo_password = _setting("DEMO_PASSWORD")
    if _setting("DEMO_REQUIRE_AUTH") == "1" and not demo_password:
        st.error("La démonstration nécessite un mot de passe configuré.")
        return
    if demo_password and not st.session_state.get("demo_authenticated", False):
        attempt = st.text_input("Mot de passe de démonstration", type="password")
        if hmac.compare_digest(attempt.encode(), demo_password.encode()):
            st.session_state.demo_authenticated = True
            st.rerun()
        return

    database_url = _setting("DATABASE_URL").strip()
    if not database_url:
        st.error(
            "DATABASE_URL est absent. Configurez l'URL de la base NeonDB dans "
            "l'environnement ou dans .streamlit/secrets.toml."
        )
        return

    database = PostgresDatabase(
        database_url,
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
    if st.button("Voir le schéma des données"):
        st.session_state.show_schema = True
    if st.session_state.get("show_schema", False):
        st.subheader("Schéma des données interrogeables")
        st.caption("PK : clé primaire · FK : clé étrangère · flèche : lien entre tables. Données fictives.")
        if st.button("Revenir aux questions"):
            st.session_state.show_schema = False
            st.rerun()
        try:
            st.graphviz_chart(schema_diagram_dot(database.get_schema()), use_container_width=True)
        except Exception:
            st.error("Le schéma n'est pas disponible pour le moment.")
        return

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
            safe_sql, result = process_question(
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

    sql_placeholder.code(safe_sql, language="sql")
    if result.rows:
        records = [
            dict(zip(result.columns, row, strict=True))
            for row in result.rows
        ]
        result_placeholder.dataframe(records, use_container_width=True)
    else:
        result_placeholder.info("La requête ne retourne aucune ligne.")


if __name__ == "__main__":
    run_app()
