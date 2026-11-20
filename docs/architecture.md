# Architecture — Text-to-SQL sur NeonDB

## Objectif

Permettre à un utilisateur de questionner en français une base NeonDB existante,
sans transmettre au LLM les lignes de la base ni les résultats des requêtes.

## Flux

```text
Question utilisateur
        |
        v
Application Streamlit -- exige DATABASE_URL
        |
        |  lecture des métadonnées dans information_schema
        v
Schéma : tables, colonnes, types, PK/FK
        |
        |  question + schéma uniquement
        v
OpenRouter / LLM
        |
        |  une requête SQL uniquement
        v
Validation SQL locale
        |
        |  SELECT validé + LIMIT
        v
NeonDB -- transaction READ ONLY + statement_timeout
        |
        |  résultats uniquement vers Streamlit
        v
Affichage SQL + tableau de résultats
```

`get_schema()` est appelé avant le LLM. Son introspection interroge uniquement
`information_schema` pour les tables de base du schéma `public`, leurs colonnes,
types, clés primaires et clés étrangères. Elle ne lit aucune table métier.

## Frontière de confidentialité

OpenRouter reçoit uniquement :

- la question saisie par l'utilisateur ;
- les noms des tables et colonnes ;
- les types SQL ;
- les clés primaires et étrangères nécessaires aux jointures.

OpenRouter ne reçoit jamais :

- une ligne ou un échantillon de données ;
- le résultat d'une requête ;
- une erreur contenant des valeurs de la base ;
- l'URL ou les identifiants de connexion.

L'API OpenRouter est sans état : le schéma accompagne chaque demande de
génération SQL. Aucun résultat ni aucune erreur n'entraîne un nouvel appel.

## Modules

- `app.py` : interface Streamlit, configuration et orchestration du flux ;
- `llm.py` : construction du prompt et appel HTTP minimal à OpenRouter ;
- `database.py` : introspection `information_schema` et lecture PostgreSQL ;
- `sql_security.py` : validation locale et limite de lignes ;
- `tests/` : tests unitaires sans accès réseau ni base réelle ;
- `.streamlit/config.toml` : thème de l'interface.

## Défense en profondeur

Le prompt verrouillé améliore la forme de la réponse, mais ne constitue pas une
barrière de sécurité. Avant toute exécution, le code local :

1. retire uniquement un éventuel bloc Markdown autour du SQL ;
2. refuse les réponses vides ou contenant plusieurs instructions ;
3. analyse le SQL avec `sqlglot` ;
4. n'accepte qu'une requête de lecture (`SELECT`, y compris avec `WITH`) ;
5. refuse DDL, DML, `COPY`, commandes d'administration et fonctions dangereuses ;
6. limite les tables aux tables métier découvertes dans le schéma ;
7. applique une limite maximale de lignes ;
8. exécute PostgreSQL dans une transaction `READ ONLY` avec un délai maximal.

En production, le rôle NeonDB doit lui-même être limité à `CONNECT`, `USAGE` et
`SELECT`.

## Dépendances minimales

- `streamlit` : interface ;
- `psycopg[binary]` : connexion PostgreSQL/NeonDB ;
- `sqlglot` : analyse du SQL ;
- `pytest` : tests.

L'appel OpenRouter utilise la bibliothèque standard `urllib.request`.
