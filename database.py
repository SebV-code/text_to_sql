"""Accès PostgreSQL/NeonDB limité au schéma et aux lectures métier."""

from __future__ import annotations

from dataclasses import dataclass
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


class _ConnectionFactory(Protocol):
    def __call__(self, dsn: str, /) -> Any: ...


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
        # Cette valeur a été convertie en entier par la configuration appelante.
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
        """Lit les tables, colonnes, types et clés depuis information_schema."""

        columns_sql = """
            SELECT c.table_name, c.column_name, c.data_type, c.is_nullable,
                   EXISTS (
                       SELECT 1
                       FROM information_schema.table_constraints tc
                       JOIN information_schema.key_column_usage kcu
                         ON kcu.constraint_catalog = tc.constraint_catalog
                        AND kcu.constraint_schema = tc.constraint_schema
                        AND kcu.constraint_name = tc.constraint_name
                        AND kcu.table_name = tc.table_name
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
              ON tc.constraint_catalog = kcu.constraint_catalog
             AND tc.constraint_schema = kcu.constraint_schema
             AND tc.constraint_name = kcu.constraint_name
            JOIN information_schema.referential_constraints rc
              ON tc.constraint_catalog = rc.constraint_catalog
             AND tc.constraint_schema = rc.constraint_schema
             AND tc.constraint_name = rc.constraint_name
            JOIN information_schema.key_column_usage ccu
              ON rc.unique_constraint_catalog = ccu.constraint_catalog
             AND rc.unique_constraint_schema = ccu.constraint_schema
             AND rc.unique_constraint_name = ccu.constraint_name
             AND kcu.position_in_unique_constraint = ccu.ordinal_position
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
    statement_timeout_ms: int = 5000,
) -> PostgresDatabase:
    """Construit la connexion NeonDB et refuse toute configuration absente."""

    if not database_url or not database_url.strip():
        raise ValueError("DATABASE_URL est requis.")
    return PostgresDatabase(
        database_url.strip(), statement_timeout_ms=statement_timeout_ms
    )
