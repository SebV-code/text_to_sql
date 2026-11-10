# Architecture — Text-to-SQL sans exposition des données

## Objectif

Permettre à un utilisateur de questionner une base SQL en français, tout en garantissant que le LLM ne reçoit jamais les lignes de la base ni les résultats des requêtes.

## Flux

```text
Question utilisateur
        |
        v
Application Streamlit
        |
        |  question + schéma uniquement
        v
OpenRouter / petit LLM
        |
        |  une requête SQL uniquement
        v
Validation SQL locale
        |
        |  SELECT validé + LIMIT
        v
Base locale de démonstration ou PostgreSQL/NeonDB
        |
        |  résultats uniquement vers Streamlit
        v
Affichage SQL + tableau de résultats
```

## Frontière de confidentialité

OpenRouter reçoit uniquement :

- la question saisie par l’utilisateur ;
- les noms des tables et colonnes ;
- les types SQL ;
- les clés primaires et étrangères nécessaires aux jointures.

OpenRouter ne reçoit jamais :

- une ligne ou un échantillon de données ;
- le résultat d’une requête ;
- une erreur contenant des valeurs de la base ;
- l’URL ou les identifiants de connexion.

L’API OpenRouter est sans état : le schéma doit donc accompagner chaque demande de génération SQL. Le schéma peut être mis en cache localement, mais il n’est pas remplacé par des données d’exemple.

## Fichiers prévus

- `app.py` : interface Streamlit et orchestration lisible du flux.
- `llm.py` : construction du prompt et appel HTTP minimal à OpenRouter.
- `database.py` : lecture du schéma, exécution en lecture seule et limite de temps.
- `sql_security.py` : validation locale et ajout d’une limite de lignes.
- `scripts/create_demo_db.py` : création des tables `clients`, `produits`, `commandes` et génération de données Faker.
- `tests/` : tests unitaires et intégration sur une base SQLite locale de démonstration.
- `.streamlit/config.toml` : thème AntIqscan.

## Défense en profondeur

Le prompt verrouillé améliore la forme de la réponse, mais ne constitue pas une barrière de sécurité. Avant toute exécution, le code local doit :

1. retirer uniquement un éventuel bloc Markdown autour du SQL ;
2. refuser les réponses vides ou contenant plusieurs instructions ;
3. analyser le SQL avec `sqlglot` ;
4. n’accepter qu’une requête de lecture (`SELECT`, y compris avec `WITH`) ;
5. refuser DDL, DML, `COPY`, commandes d’administration et fonctions PostgreSQL dangereuses ;
6. limiter les tables aux tables métier découvertes dans le schéma ;
7. appliquer une limite maximale de lignes ;
8. exécuter PostgreSQL dans une transaction `READ ONLY` avec un délai maximal ;
9. utiliser en production un utilisateur NeonDB disposant uniquement de `CONNECT`, `USAGE` et `SELECT`.

## Dépendances minimales

- `streamlit` : interface ;
- `psycopg[binary]` : connexion PostgreSQL/NeonDB ;
- `sqlglot` : analyse fiable du SQL ;
- `faker` : données synthétiques de démonstration ;
- `pytest` : tests.

L’appel OpenRouter utilise la bibliothèque standard `urllib.request`, afin d’éviter une dépendance HTTP supplémentaire.

## Hors périmètre de cette première version

- authentification des utilisateurs ;
- historique partagé des questions ;
- graphiques automatiques ;
- reformulation des résultats par un second LLM ;
- déploiement serveur et service permanent.
