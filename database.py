"""Accès minimal aux bases SQLite de démo et PostgreSQL/NeonDB.

Le module ne construit aucun prompt LLM. Il fournit séparément les métadonnées
de schéma et les résultats, afin qu'une fuite accidentelle soit plus difficile.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import psycopg


@dataclass(frozen=True)
class ColumnSchema:
    """Description d'une colonne, sans aucune valeur issue de la table."""

    name: str
    data_type: str
    nullable: bool
    primary_key: bool = False


@dataclass(frozen=True)
class ForeignKeySchema:
    """Relation nécessaire au modèle pour construire les jointures."""

    column: str
    reference_table: str
    reference_column: str


@dataclass(frozen=True)
class TableSchema:
    name: str
    columns: tuple[ColumnSchema, ...]
    foreign_keys: tuple[ForeignKeySchema, ...] = ()


@dataclass(frozen=True)
class DatabaseSchema:
    """Schéma métier sérialisable sans lignes ni exemples de données."""

    dialect: str
    tables: tuple[TableSchema, ...]

    @property
    def allowed_tables(self) -> set[str]:
        return {table.name for table in self.tables}

    def to_prompt(self) -> str:
        """Produit un texte volontairement limité aux métadonnées autorisées."""

        lines = [f"Dialecte SQL : {self.dialect}", "Tables :"]
        for table in self.tables:
            columns = []
            for column in table.columns:
                details = f"{column.name} {column.data_type}"
                if column.primary_key:
                    details += " PRIMARY KEY"
                if not column.nullable and not column.primary_key:
                    details += " NOT NULL"
                columns.append(details)
            lines.append(f"- {table.name}({', '.join(columns)})")
        lines.append("Relations :")
        relations = [
            f"- {table.name}.{foreign_key.column} -> "
            f"{foreign_key.reference_table}.{foreign_key.reference_column}"
            for table in self.tables
            for foreign_key in table.foreign_keys
        ]
        lines.extend(relations or ["- aucune"])
        return "\n".join(lines)


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: list[tuple[Any, ...]]


class Database(Protocol):
    dialect: str

    def get_schema(self) -> DatabaseSchema: ...

    def execute(self, sql: str, *, max_rows: int) -> QueryResult: ...


class SQLiteDatabase:
    """Base locale ouverte en lecture seule pour les requêtes applicatives."""

    dialect = "sqlite"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect_read_only(self) -> sqlite3.Connection:
        # mode=ro empêche une écriture même si la validation SQL régressait.
        return sqlite3.connect(f"file:{self.path.resolve()}?mode=ro", uri=True)

    def get_schema(self) -> DatabaseSchema:
        tables: list[TableSchema] = []
        with self._connect_read_only() as connection:
            table_names = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_schema "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name"
                )
            ]
            for table_name in table_names:
                # Le nom vient du catalogue SQLite, pas d'une entrée utilisateur.
                quoted_name = table_name.replace('"', '""')
                column_rows = connection.execute(
                    f'PRAGMA table_info("{quoted_name}")'
                ).fetchall()
                foreign_rows = connection.execute(
                    f'PRAGMA foreign_key_list("{quoted_name}")'
                ).fetchall()
                columns = tuple(
                    ColumnSchema(
                        name=row[1],
                        data_type=row[2] or "TEXT",
                        nullable=not bool(row[3]) and not bool(row[5]),
                        primary_key=bool(row[5]),
                    )
                    for row in column_rows
                )
                foreign_keys = tuple(
                    ForeignKeySchema(
                        column=row[3],
                        reference_table=row[2],
                        reference_column=row[4],
                    )
                    for row in foreign_rows
                )
                tables.append(TableSchema(table_name, columns, foreign_keys))
        return DatabaseSchema(self.dialect, tuple(tables))

    def execute(self, sql: str, *, max_rows: int) -> QueryResult:
        if max_rows <= 0:
            raise ValueError("max_rows doit être positif")
        with self._connect_read_only() as connection:
            # query_only constitue une seconde barrière SQLite indépendante.
            connection.execute("PRAGMA query_only = ON")
            cursor = connection.execute(sql)
            columns = tuple(item[0] for item in (cursor.description or ()))
            rows = cursor.fetchmany(max_rows)
        return QueryResult(columns, rows)


class _ConnectionFactory(Protocol):
    def __call__(self, dsn: str) -> Any: ...


class PostgresDatabase:
    """Connexion PostgreSQL/NeonDB avec transaction strictement en lecture."""

    dialect = "postgres"

    def __init__(
        self,
        dsn: str,
        *,
        statement_timeout_ms: int = 5000,
        connector: _ConnectionFactory | None = None,
    ) -> None:
        if statement_timeout_ms <= 0:
            raise ValueError("statement_timeout_ms doit être positif")
        self._dsn = dsn
        self.statement_timeout_ms = statement_timeout_ms
        self._connector = connector or psycopg.connect

    def _prepare_read_only(self, cursor: Any) -> None:
        cursor.execute("SET TRANSACTION READ ONLY")
        # Valeur convertie en int dans le constructeur : aucune interpolation
        # provenant de l'utilisateur n'arrive dans cette commande.
        cursor.execute(f"SET LOCAL statement_timeout = {self.statement_timeout_ms}")

    def execute(self, sql: str, *, max_rows: int) -> QueryResult:
        if max_rows <= 0:
            raise ValueError("max_rows doit être positif")
        with self._connector(self._dsn) as connection:
            with connection.cursor() as cursor:
                self._prepare_read_only(cursor)
                cursor.execute(sql)
                columns = tuple(
                    getattr(item, "name", item[0]) for item in (cursor.description or ())
                )
                rows = cursor.fetchmany(max_rows)
            connection.commit()
        return QueryResult(columns, rows)

    def get_schema(self) -> DatabaseSchema:
        """Lit uniquement information_schema, jamais les tables métier."""

        columns_sql = """
            SELECT c.table_name, c.column_name, c.data_type, c.is_nullable,
                   EXISTS (
                       SELECT 1
                       FROM information_schema.table_constraints tc
                       JOIN information_schema.key_column_usage kcu
                         ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                       WHERE tc.constraint_type = 'PRIMARY KEY'
                         AND tc.table_schema = c.table_schema
                         AND tc.table_name = c.table_name
                         AND kcu.column_name = c.column_name
                   ) AS is_primary_key
            FROM information_schema.columns c
            JOIN information_schema.tables t
              ON t.table_schema = c.table_schema
             AND t.table_name = c.table_name
            WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
            ORDER BY c.table_name, c.ordinal_position
        """
        foreign_keys_sql = """
            SELECT tc.table_name, kcu.column_name,
                   ccu.table_name, ccu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.constraint_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = 'public'
            ORDER BY tc.table_name, kcu.ordinal_position
        """
        with self._connector(self._dsn) as connection:
            with connection.cursor() as cursor:
                self._prepare_read_only(cursor)
                cursor.execute(columns_sql)
                column_rows = cursor.fetchall()
                cursor.execute(foreign_keys_sql)
                foreign_rows = cursor.fetchall()
            connection.commit()

        by_table: dict[str, list[ColumnSchema]] = {}
        for table_name, column_name, data_type, nullable, primary_key in column_rows:
            by_table.setdefault(table_name, []).append(
                ColumnSchema(
                    column_name,
                    data_type,
                    nullable == "YES",
                    bool(primary_key),
                )
            )
        relations: dict[str, list[ForeignKeySchema]] = {}
        for table_name, column, reference_table, reference_column in foreign_rows:
            relations.setdefault(table_name, []).append(
                ForeignKeySchema(column, reference_table, reference_column)
            )
        tables = tuple(
            TableSchema(
                name,
                tuple(columns),
                tuple(relations.get(name, ())),
            )
            for name, columns in sorted(by_table.items())
        )
        return DatabaseSchema(self.dialect, tables)


def create_database(
    *,
    database_url: str | None,
    sqlite_path: str | Path,
    statement_timeout_ms: int = 5000,
) -> Database:
    """Choisit PostgreSQL si une URL non vide existe, sinon la démo SQLite."""

    if database_url and database_url.strip():
        return PostgresDatabase(
            database_url.strip(), statement_timeout_ms=statement_timeout_ms
        )
    return SQLiteDatabase(sqlite_path)
