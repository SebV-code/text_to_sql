"""Validation locale des requêtes produites par le modèle.

Ce module est la barrière entre le texte non fiable du LLM et la base. Le
prompt aide le modèle, mais aucune confiance ne lui est accordée ici.
"""

from __future__ import annotations

import re
from collections.abc import Collection

from sqlglot import exp, parse
from sqlglot.errors import ParseError


class SQLSecurityError(ValueError):
    """Erreur volontairement générique pour une requête SQL non sûre."""


# Ces fonctions peuvent lire des fichiers, temporiser une connexion, ouvrir une
# autre connexion ou changer la configuration PostgreSQL. La comparaison est
# insensible à la casse.
DANGEROUS_FUNCTIONS = {
    "dblink",
    "dblink_exec",
    "lo_export",
    "lo_import",
    "pg_ls_dir",
    "pg_read_binary_file",
    "pg_read_file",
    "pg_sleep",
    "set_config",
}

# Les catalogues ne font pas partie du schéma métier, même si une configuration
# les ajoutait par erreur à l'allowlist.
SYSTEM_SCHEMAS = {"information_schema", "pg_catalog"}
SYSTEM_TABLES = {"sqlite_master", "sqlite_schema", "pg_user", "pg_shadow"}


def _strip_markdown_fence(text: str) -> str:
    """Retire uniquement un bloc Markdown qui entoure toute la réponse."""

    stripped = text.strip()
    match = re.fullmatch(
        r"```(?:sql|postgresql|sqlite)?\s*\n?(.*?)\n?```",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1).strip() if match else stripped


def _function_name(node: exp.Func) -> str:
    """Retourne le nom réel, y compris pour une fonction SQL inconnue."""

    if isinstance(node, exp.Anonymous):
        return node.name.lower()
    return node.sql_name().lower()


def _check_forbidden_nodes(expression: exp.Expression) -> None:
    """Parcourt tout l'AST et refuse les nœuds d'écriture/administration.

    Contrôler seulement la racine ne suffit pas : PostgreSQL autorise notamment
    un DELETE/INSERT/UPDATE avec RETURNING dans un CTE dont la racine est SELECT.
    """

    forbidden_types = tuple(
        node_type
        for name in (
            "Alter",
            "Analyze",
            "Attach",
            "Command",
            "Copy",
            "Create",
            "Delete",
            "Detach",
            "Drop",
            "Execute",
            "Grant",
            "Insert",
            "LoadData",
            "Merge",
            "Pragma",
            "Revoke",
            "Transaction",
            "TruncateTable",
            "Update",
            "Use",
        )
        if isinstance((node_type := getattr(exp, name, None)), type)
    )
    for node in expression.walk():
        # DML couvre les variantes connues par sqlglot ; la liste explicite
        # couvre aussi les DDL qui n'héritent pas tous d'une base commune.
        if isinstance(node, (exp.DML, *forbidden_types)):
            raise SQLSecurityError("Un nœud DML ou DDL imbriqué est interdit.")


def _check_tables(expression: exp.Expression, allowed_tables: set[str]) -> None:
    """Vérifie les tables physiques en ignorant les alias de CTE."""

    cte_names = {
        cte.alias_or_name.lower()
        for cte in expression.find_all(exp.CTE)
        if cte.alias_or_name
    }

    for table in expression.find_all(exp.Table):
        name = table.name.lower()
        schema = table.db.lower() if table.db else ""
        catalog = table.catalog.lower() if table.catalog else ""

        if (
            name in SYSTEM_TABLES
            or name.startswith("pg_")
            or schema in SYSTEM_SCHEMAS
            or catalog in SYSTEM_SCHEMAS
        ):
            raise SQLSecurityError("L'accès aux catalogues système est interdit.")

        # Un CTE est une relation éphémère définie dans la requête, pas une
        # table de la base. Les qualifications sont interdites pour éviter les
        # contournements par une autre base ou un autre schéma.
        if name in cte_names and not schema and not catalog:
            continue
        if schema or catalog or name not in allowed_tables:
            raise SQLSecurityError(f"Table interdite : {table.sql()}")


def _check_functions(expression: exp.Expression) -> None:
    """Refuse les fonctions connues pour avoir des effets ou exfiltrer."""

    for function in expression.find_all(exp.Func):
        name = _function_name(function)
        if name in DANGEROUS_FUNCTIONS:
            raise SQLSecurityError(f"Fonction interdite : {name}")


def _apply_limit(expression: exp.Expression, max_rows: int) -> None:
    """Ajoute ou resserre la limite sur la requête racine."""

    limit = expression.args.get("limit")
    if limit is None:
        expression.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
        return

    value = limit.expression
    if not isinstance(value, exp.Literal) or not value.is_int:
        raise SQLSecurityError("La clause LIMIT doit être un entier littéral.")
    if int(value.this) > max_rows:
        limit.set("expression", exp.Literal.number(max_rows))


def validate_and_limit_sql(
    sql: str,
    allowed_tables: Collection[str],
    *,
    max_rows: int = 100,
    dialect: str = "postgres",
) -> str:
    """Valide une unique lecture SQL puis impose une limite maximale.

    Le SQL retourné est régénéré par sqlglot : seul cet SQL validé doit être
    transmis à la base, jamais le texte brut du modèle.
    """

    if max_rows <= 0:
        raise ValueError("max_rows doit être une valeur positive.")

    cleaned = _strip_markdown_fence(sql)
    if not cleaned:
        raise SQLSecurityError("La réponse SQL est vide.")

    try:
        statements = parse(cleaned, read=dialect)
    except ParseError as exc:
        raise SQLSecurityError("Le SQL est invalide.") from exc

    if len(statements) != 1:
        raise SQLSecurityError("Le modèle doit produire une seule requête.")
    expression = statements[0]
    if expression is None:
        raise SQLSecurityError("La réponse SQL est vide.")

    # SELECT et les opérations ensemblistes de SELECT sont les seules racines
    # admises. Dans sqlglot, WITH est attaché à la requête SELECT qu'il précède.
    if not isinstance(expression, (exp.Select, exp.SetOperation)):
        raise SQLSecurityError("Seules les requêtes SELECT ou WITH sont admises.")

    # SELECT ... INTO crée une table : sa racine reste SELECT, il faut donc le
    # bloquer explicitement. Les verrous sont également inutiles en lecture.
    if expression.find(exp.Into) or expression.find(exp.Lock):
        raise SQLSecurityError("Cette forme de SELECT est interdite.")

    normalized_tables = {name.lower() for name in allowed_tables}
    _check_forbidden_nodes(expression)
    _check_tables(expression, normalized_tables)
    _check_functions(expression)
    _apply_limit(expression, max_rows)
    return expression.sql(dialect=dialect)
