# Text-to-SQL — plan d'implémentation NeonDB

**Objectif :** transformer une question française en SQL PostgreSQL, valider la
requête localement, puis interroger en lecture seule une base NeonDB existante
sans transmettre de données métier au LLM.

**Architecture :** quatre modules séparent interface, appel LLM, sécurité SQL et
accès PostgreSQL. `DATABASE_URL` est obligatoire. L'accès aux métadonnées est
limité à `information_schema` et précède chaque appel LLM.

**Stack :** Python 3.11, Streamlit, urllib, sqlglot, psycopg et pytest.

## Étape 1 — Configuration

1. Tester l'échec explicite lorsque `DATABASE_URL` est absent ou vide.
2. Construire uniquement `PostgresDatabase` avec l'URL fournie.
3. Documenter les variables sans inclure de secret.

## Étape 2 — Introspection sûre

1. Tester l'extraction des tables, colonnes, types, clés primaires et étrangères.
2. Vérifier que les requêtes d'introspection ciblent uniquement
   `information_schema` et jamais une table métier.
3. Sérialiser uniquement ces métadonnées dans le schéma destiné au prompt.

## Étape 3 — Orchestration

1. Tester l'ordre `get_schema()` → appel LLM → validation → exécution.
2. Envoyer uniquement la question et le schéma au LLM.
3. Ne jamais rappeler le LLM avec un résultat ou une erreur.

## Étape 4 — Lecture protégée

1. Conserver la validation AST et l'allowlist des tables découvertes.
2. Imposer une transaction `READ ONLY`, un `statement_timeout` et une limite de
   lignes.
3. Recommander un rôle NeonDB disposant uniquement des droits de lecture requis.

## Étape 5 — Vérification

1. Exécuter les tests ciblés après chaque cycle rouge/vert.
2. Exécuter toute la suite avec `pytest`.
3. Compiler les modules Python.
4. Examiner le diff et rechercher les anciens chemins de configuration.
