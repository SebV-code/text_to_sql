# Text-to-SQL local et confidentiel

Application Streamlit pédagogique : une question en français est transformée en
une requête SQL, validée localement, puis exécutée sur SQLite ou
PostgreSQL/NeonDB.

## Frontière de confidentialité

OpenRouter reçoit **uniquement** :

- la question saisie ;
- les noms de tables et colonnes ;
- leurs types ;
- les clés primaires et étrangères utiles aux jointures.

Les lignes, résultats, erreurs de la base, URL de connexion et identifiants ne
sont jamais placés dans le prompt. Après l'appel unique au modèle, la validation
et l'exécution ont lieu localement. Une erreur SQL ne déclenche pas de second
appel LLM.

## Prérequis et installation

- Python 3.11 ;
- [`uv`](https://docs.astral.sh/uv/) ;
- une clé OpenRouter pour générer du SQL ;
- facultativement une URL PostgreSQL/NeonDB. Sans URL de base, SQLite est utilisé.

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

L'application ne charge volontairement pas de fichier `.env`. Exporter les
variables dans le processus :

```bash
export OPENROUTER_API_KEY='...'
export OPENROUTER_MODEL='openai/gpt-4o-mini'  # facultatif
```

Une autre option est un fichier local `.streamlit/secrets.toml`, ignoré par Git :

```toml
OPENROUTER_API_KEY = "..."
OPENROUTER_MODEL = "openai/gpt-4o-mini"
# DATABASE_URL = "postgresql://..."  # facultatif
```

Voir `.env.example` pour toutes les valeurs disponibles. Ne jamais committer un
vrai secret.

## Démonstration SQLite

Au premier démarrage sans `DATABASE_URL`, `demo.db` est créé automatiquement avec
les tables françaises `clients`, `produits` et `commandes`. Les lignes sont
synthétiques, générées avec Faker et une graine reproductible. La base peut aussi
être créée explicitement :

```bash
.venv/bin/python scripts/create_demo_db.py --path demo.db --seed 42
```

Puis lancer l'interface :

```bash
.venv/bin/streamlit run app.py
```

L'écran contient volontairement quatre éléments principaux : question, bouton,
SQL validé et tableau de résultats.

## PostgreSQL / NeonDB

Définir `DATABASE_URL` dans l'environnement ou les secrets Streamlit :

```bash
export DATABASE_URL='postgresql://utilisateur:mot-de-passe@hote/base?sslmode=require'
export DB_STATEMENT_TIMEOUT_MS=5000
```

Utiliser un rôle dédié disposant seulement de `CONNECT`, `USAGE` sur le schéma
métier et `SELECT` sur les tables autorisées. L'application ajoute en défense en
profondeur :

- `SET TRANSACTION READ ONLY` ;
- un `statement_timeout` local à la transaction ;
- une limite locale de lignes (`SQL_MAX_ROWS`, 100 par défaut).

## Garde-fous SQL

`sqlglot` analyse le SQL avant toute exécution :

1. une seule instruction est admise ;
2. sa racine doit être une lecture `SELECT`/`WITH` ;
3. tous les nœuds de l'AST sont parcourus afin de bloquer aussi un DML/DDL
   imbriqué dans un CTE ;
4. DML, DDL, `COPY`, commandes d'administration, `SELECT INTO` et verrous sont
   refusés ;
5. les catalogues système sont interdits et seules des fonctions analytiques
   explicitement sûres sont autorisées ;
6. chaque table physique doit appartenir au schéma métier découvert, avec une
   résolution des CTE limitée à leur portée ;
7. les identifiants cités et les CTE récursifs sont refusés pour éviter les
   divergences de résolution et les calculs sans borne ;
8. un `LIMIT` est ajouté ou abaissé localement ;
9. SQLite est ouvert en lecture seule avec `PRAGMA query_only` et chaque requête
   est exécutée dans un processus isolé, supprimé si le délai est dépassé.

Le prompt n'est pas considéré comme une barrière de sécurité.

## Tests et contrôles locaux

```bash
.venv/bin/pytest tests/ -q
.venv/bin/python -m compileall -q app.py llm.py database.py sql_security.py scripts tests
```

Les appels OpenRouter sont mockés et le chemin PostgreSQL est testé avec une
fausse connexion. La suite locale ne prétend donc pas valider un compte
OpenRouter ou une instance NeonDB réels en l'absence de clés.

Pour un contrôle manuel du démarrage :

```bash
.venv/bin/streamlit run app.py --server.headless true
# puis ouvrir http://localhost:8501/_stcore/health
```

## Structure

- `app.py` — interface et orchestration sans reboucle LLM ;
- `llm.py` — prompt et appel OpenRouter via `urllib.request` ;
- `database.py` — schéma sûr, SQLite et PostgreSQL en lecture seule ;
- `sql_security.py` — analyse AST, allowlist et limite ;
- `scripts/create_demo_db.py` — données françaises Faker ;
- `tests/` — tests unitaires, HTTP mocké et intégration SQLite locale ;
- `.streamlit/config.toml` — thème AntIqscan fourni.
