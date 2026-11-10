"""Tests de la base SQLite synthétique."""

import sqlite3
from pathlib import Path

from scripts.create_demo_db import create_demo_database


def test_creation_base_demo_cree_et_peuple_les_trois_tables(tmp_path: Path) -> None:
    path = create_demo_database(
        tmp_path / "demo.db",
        client_count=12,
        product_count=7,
        order_count=25,
        seed=123,
    )

    connection = sqlite3.connect(path)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table'"
        )
    }
    counts = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("clients", "produits", "commandes")
    }
    foreign_keys = connection.execute("PRAGMA foreign_key_list(commandes)").fetchall()
    connection.close()

    assert {"clients", "produits", "commandes"} <= tables
    assert counts == {"clients": 12, "produits": 7, "commandes": 25}
    assert {(row[3], row[2], row[4]) for row in foreign_keys} == {
        ("client_id", "clients", "id"),
        ("produit_id", "produits", "id"),
    }


def test_generation_est_reproductible_avec_la_meme_graine(tmp_path: Path) -> None:
    first = create_demo_database(tmp_path / "a.db", seed=77)
    second = create_demo_database(tmp_path / "b.db", seed=77)

    def snapshot(path: Path) -> tuple[object, ...]:
        connection = sqlite3.connect(path)
        row = connection.execute(
            "SELECT nom, ville, email FROM clients ORDER BY id LIMIT 1"
        ).fetchone()
        connection.close()
        return row

    assert snapshot(first) == snapshot(second)


def test_recreation_remplace_le_fichier_existant(tmp_path: Path) -> None:
    path = tmp_path / "demo.db"
    create_demo_database(path, client_count=3, product_count=2, order_count=4)
    create_demo_database(path, client_count=5, product_count=4, order_count=6)

    connection = sqlite3.connect(path)
    assert connection.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == 5
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()
