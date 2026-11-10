"""Validation locale des requêtes produites par le modèle.

Ce module est la barrière entre le texte non fiable du LLM et la base. Le
prompt aide le modèle, mais aucune confiance ne lui est accordée ici.
"""

from __future__ import annotations

import re
from collections.abc import Collection

from sqlglot import exp, parse
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import traverse_scope


class SQLSecurityError(ValueError):
    """Erreur volontairement générique pour une requête SQL non sûre."""


# Politique fail-closed : seules les fonctions de lecture nécessaires aux
# questions analytiques courantes sont admises. Une blacklist laisserait passer
# les nombreuses variantes dblink/XML/extensions propres à PostgreSQL.
SAFE_FUNCTIONS = {
    "abs",
    "avg",
    "cast",
    "ceil",
    "ceiling",
    "coalesce",
    "concat",
    "concat_ws",
    "count",
    "current_date",
    "current_time",
    "current_timestamp",
    "date",
    "date_trunc",
    "datetime",
    "extract",
    "floor",
    "greatest",
    "julianday",
    "least",
    "length",
    "lower",
    "ltrim",
    "max",
    "min",
    "nullif",
    "replace",
    "round",
    "rtrim",
    "strftime",
    "substring",
    "sum",
    "trim",
    "upper",
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
    """Vérifie chaque table dans la portée SQL où elle est référencée.

    Une liste globale de noms de CTE est insuffisante : un CTE imbriqué ne doit
    jamais masquer une table homonyme utilisée par une requête parente.
    ``traverse_scope`` résout chaque source selon les règles de visibilité SQL.
    """

    # Les identifiants cités sont sensibles à la casse dans PostgreSQL, alors
    # que sqlglot peut associer une référence non citée à un CTE cité homonyme.
    # Le schéma pédagogique n'en a pas besoin : les refuser évite toute
    # divergence entre la résolution du parseur et celle de la base.
    if any(identifier.args.get("quoted") for identifier in expression.find_all(exp.Identifier)):
        raise SQLSecurityError("Les identifiants SQL cités sont interdits.")

    for scope in traverse_scope(expression):
        visible_ctes = {name.lower() for name in scope.cte_sources}
        for table in scope.tables:
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

            # sqlglot associe l'alias visible à un Scope pour les CTE et les
            # sous-requêtes, et à une Table pour une vraie table de la base.
            # Une relation qualifiée reste toujours physique et est refusée.
            # Les identifiants non cités PostgreSQL sont insensibles à la casse.
            # cte_sources est propre à la portée : contrairement à une collecte
            # globale, un CTE imbriqué ne masque pas une table de la requête mère.
            if name in visible_ctes and not schema and not catalog:
                continue
            if schema or catalog or name not in allowed_tables:
                raise SQLSecurityError(f"Table interdite : {table.sql()}")


def _check_functions(expression: exp.Expression) -> None:
    """N'autorise que des fonctions analytiques explicitement sans effet."""

    for function in expression.find_all(exp.Func):
        name = _function_name(function)
        if name not in SAFE_FUNCTIONS:
            raise SQLSecurityError(f"Fonction interdite : {name}")


def _check_complexity(expression: exp.Expression) -> None:
    """Refuse les constructions sans borne incompatibles avec une petite app."""

    if any(with_clause.args.get("recursive") for with_clause in expression.find_all(exp.With)):
        raise SQLSecurityError("Les CTE récursifs sont interdits.")


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
    except SqlglotError as exc:
        # Le parseur peut échouer pendant la tokenisation ou l'analyse. Aucun
        # détail interne de sqlglot ne doit traverser la frontière applicative.
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
    _check_complexity(expression)
    _check_tables(expression, normalized_tables)
    _check_functions(expression)
    _apply_limit(expression, max_rows)
    return expression.sql(dialect=dialect)
