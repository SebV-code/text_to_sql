"""Crée une base SQLite de démonstration avec des données synthétiques.

Ces données Faker restent locales. Elles ne sont jamais intégrées au prompt
OpenRouter ; seul le schéma produit par :mod:`database` peut quitter la machine.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from faker import Faker


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
CREATE TABLE clients (
    id INTEGER PRIMARY KEY,
    nom TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    ville TEXT NOT NULL,
    date_inscription DATE NOT NULL
);
CREATE TABLE produits (
    id INTEGER PRIMARY KEY,
    nom TEXT NOT NULL,
    categorie TEXT NOT NULL,
    prix NUMERIC NOT NULL CHECK (prix >= 0),
    stock INTEGER NOT NULL CHECK (stock >= 0)
);
CREATE TABLE commandes (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    produit_id INTEGER NOT NULL REFERENCES produits(id),
    quantite INTEGER NOT NULL CHECK (quantite > 0),
    date_commande DATE NOT NULL
);
CREATE INDEX idx_commandes_client ON commandes(client_id);
CREATE INDEX idx_commandes_produit ON commandes(produit_id);
"""

CATEGORIES = ("Livres", "Papeterie", "Informatique", "Maison", "Loisirs")


def create_demo_database(
    path: str | Path,
    *,
    client_count: int = 50,
    product_count: int = 30,
    order_count: int = 200,
    seed: int = 42,
) -> Path:
    """Crée une base neuve, peuplée et reproductible à l'emplacement demandé."""

    if min(client_count, product_count) <= 0 or order_count < 0:
        raise ValueError("Les nombres de clients/produits doivent être positifs.")

    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    # Le script est idempotent : une régénération ne conserve aucun ancien
    # schéma ou enregistrement susceptible de fausser la démonstration.
    database_path.unlink(missing_ok=True)

    fake = Faker("fr_FR")
    fake.seed_instance(seed)
    random_source = random.Random(seed)

    with sqlite3.connect(database_path) as connection:
        connection.executescript(SCHEMA_SQL)

        clients = []
        for identifier in range(1, client_count + 1):
            clients.append(
                (
                    identifier,
                    fake.name(),
                    f"client{identifier}.{fake.user_name()}@example.test",
                    fake.city(),
                    fake.date_between(start_date="-5y", end_date="today").isoformat(),
                )
            )
        connection.executemany(
            "INSERT INTO clients "
            "(id, nom, email, ville, date_inscription) VALUES (?, ?, ?, ?, ?)",
            clients,
        )

        products = []
        for identifier in range(1, product_count + 1):
            # Decimal évite les artefacts binaires lors de la génération des prix.
            price = Decimal(random_source.randint(299, 49_999)) / Decimal(100)
            products.append(
                (
                    identifier,
                    fake.catch_phrase(),
                    random_source.choice(CATEGORIES),
                    str(price),
                    random_source.randint(0, 250),
                )
            )
        connection.executemany(
            "INSERT INTO produits "
            "(id, nom, categorie, prix, stock) VALUES (?, ?, ?, ?, ?)",
            products,
        )

        orders = [
            (
                identifier,
                random_source.randint(1, client_count),
                random_source.randint(1, product_count),
                random_source.randint(1, 8),
                fake.date_between(start_date="-2y", end_date=date.today()).isoformat(),
            )
            for identifier in range(1, order_count + 1)
        ]
        connection.executemany(
            "INSERT INTO commandes "
            "(id, client_id, produit_id, quantite, date_commande) "
            "VALUES (?, ?, ?, ?, ?)",
            orders,
        )
        connection.commit()

    return database_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Créer la base SQLite de démonstration")
    parser.add_argument("--path", default="demo.db", help="Chemin du fichier SQLite")
    parser.add_argument("--seed", type=int, default=42, help="Graine Faker")
    args = parser.parse_args()
    path = create_demo_database(args.path, seed=args.seed)
    print(f"Base de démonstration créée : {path}")


if __name__ == "__main__":
    main()
