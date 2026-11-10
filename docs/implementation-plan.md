# Text-to-SQL Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Construire une application Streamlit pédagogique qui transforme une question française en SQL, valide la requête localement, puis interroge une base locale de démonstration ou PostgreSQL/NeonDB sans transmettre de données au LLM.

**Architecture:** Quatre modules Python simples séparent interface, appel LLM, sécurité SQL et accès aux données. Une base SQLite locale permet un vrai test d’intégration sans identifiants NeonDB ; le chemin PostgreSQL utilise `psycopg` et une transaction en lecture seule.

**Tech Stack:** Python 3.11, Streamlit, urllib, sqlglot, sqlite3, psycopg, Faker, pytest.

---

### Task 1: Configuration et tests du prompt

**Files:**
- Create: `tests/test_llm.py`
- Create: `llm.py`
- Create: `.env.example`
- Create: `requirements.txt`

**Steps:**
1. Écrire des tests qui imposent la présence du schéma, de la question et des contraintes de sortie, et l’absence de données/résultats.
2. Exécuter les tests et constater l’échec attendu.
3. Implémenter la construction des messages et l’appel OpenRouter via `urllib.request`.
4. Rejouer les tests.

### Task 2: Validation SQL locale

**Files:**
- Create: `tests/test_sql_security.py`
- Create: `sql_security.py`

**Steps:**
1. Tester un `SELECT`, un `WITH`, les blocs Markdown, la limite automatique, les instructions multiples, DML/DDL, tables inconnues, catalogues système et fonctions dangereuses.
2. Vérifier l’échec initial.
3. Implémenter la validation minimale avec `sqlglot`.
4. Vérifier tous les cas et refactoriser uniquement après passage au vert.

### Task 3: Base de données et schéma sûr

**Files:**
- Create: `tests/test_database.py`
- Create: `database.py`

**Steps:**
1. Tester l’extraction du schéma SQLite sans valeurs, l’exécution d’un `SELECT`, et la limite de résultats.
2. Vérifier l’échec initial.
3. Implémenter SQLite pour la démonstration et PostgreSQL via `psycopg` pour NeonDB.
4. Sur PostgreSQL, appliquer transaction en lecture seule et `statement_timeout`.
5. Rejouer les tests.

### Task 4: Base de démonstration

**Files:**
- Create: `tests/test_demo_db.py`
- Create: `scripts/create_demo_db.py`

**Steps:**
1. Tester la création des trois tables, leurs relations et leur peuplement.
2. Vérifier l’échec initial.
3. Créer `clients`, `produits`, `commandes` avec Faker et une graine reproductible.
4. Rejouer les tests.

### Task 5: Interface Streamlit

**Files:**
- Create: `app.py`
- Create: `.streamlit/config.toml`

**Steps:**
1. Garder l’interface dans un seul fichier lisible : titre, champ question, bouton, SQL, tableau.
2. Ne jamais rappeler le LLM avec les résultats ou une erreur de base.
3. Reprendre le thème AntIqscan fourni.
4. Vérifier le démarrage headless de Streamlit.

### Task 6: Documentation et vérification finale

**Files:**
- Create: `README.md`
- Create: `.gitignore`
- Modify: `docs/architecture.md`

**Steps:**
1. Documenter installation, création de la base de démonstration, variables, lancement et garde-fous.
2. Exécuter la suite complète `pytest`.
3. Compiler les modules Python.
4. Démarrer Streamlit en mode headless et vérifier son endpoint de santé.
5. Examiner `git diff`, puis créer un commit local sans pousser.
