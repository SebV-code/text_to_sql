# Text-to-SQL sur NeonDB

Application Streamlit pédagogique : une question en français est transformée en
SQL PostgreSQL, validée localement, puis exécutée en lecture seule sur une base
NeonDB existante.

La création et le peuplement d'une base de démonstration sont volontairement
séparés dans [`Faker-BDD-Neon`](https://github.com/SebV-code/Faker-BDD-Neon).

## Frontière de confidentialité

OpenRouter reçoit **uniquement** :

- la question saisie ;
- les noms de tables et colonnes du schéma `public` ;
- leurs types ;
- les clés primaires et étrangères utiles aux jointures.

`PostgresDatabase.get_schema()` obtient ces métadonnées depuis
`information_schema`. Les lignes, résultats, erreurs de la base, URL de connexion
et identifiants ne sont jamais placés dans le prompt. Après l'appel unique au
modèle, la validation et l'exécution ont lieu dans l'application. Une erreur SQL
ne déclenche pas de second appel LLM.

## Prérequis et installation

- Python 3.11 ;
- [`uv`](https://docs.astral.sh/uv/) ;
- une base NeonDB existante et son URL de connexion ;
- une clé OpenRouter.

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

L'application ne charge volontairement pas de fichier `.env`. Exporter les
variables dans le processus :

```bash
export DATABASE_URL='postgresql://utilisateur:mot-de-passe@hote/base?sslmode=require'
export OPENROUTER_API_KEY='...'
export OPENROUTER_MODEL='openai/gpt-4o-mini'  # facultatif
export DB_STATEMENT_TIMEOUT_MS=5000          # facultatif
export SQL_MAX_ROWS=100                      # facultatif
```

`DATABASE_URL` est obligatoire : l'application s'arrête avec un message de
configuration lorsqu'elle est absente. Une autre option est un fichier local
`.streamlit/secrets.toml`, ignoré par Git :

```toml
DATABASE_URL = "postgresql://utilisateur:mot-de-passe@hote/base?sslmode=require"
OPENROUTER_API_KEY = "..."
OPENROUTER_MODEL = "openai/gpt-4o-mini"
```

Voir `.env.example` pour toutes les valeurs disponibles. Ne jamais committer un
vrai secret.

## Lancement

```bash
.venv/bin/streamlit run app.py
```

L'écran contient volontairement quatre éléments principaux : question, bouton,
SQL validé et tableau de résultats.

## Droits NeonDB recommandés

Utiliser un rôle dédié disposant seulement de `CONNECT`, `USAGE` sur le schéma
`public` et `SELECT` sur les tables autorisées. L'application ajoute en défense
en profondeur :

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
7. les identifiants cités et les CTE récursifs sont refusés ;
8. un `LIMIT` est ajouté ou abaissé localement ;
9. PostgreSQL est interrogé dans une transaction en lecture seule avec délai.

Le prompt n'est pas considéré comme une barrière de sécurité.

## Tests et contrôles locaux

```bash
.venv/bin/pytest tests/ -q
.venv/bin/python -m compileall -q app.py llm.py database.py sql_security.py tests
```

Les appels OpenRouter et les connexions PostgreSQL sont remplacés par des doubles
de test. La suite locale ne valide donc ni un compte OpenRouter ni une instance
NeonDB réels en l'absence d'identifiants.

## Structure

- `app.py` — interface et orchestration sans reboucle LLM ;
- `llm.py` — prompt et appel OpenRouter via `urllib.request` ;
- `database.py` — introspection sûre et exécution PostgreSQL en lecture seule ;
- `sql_security.py` — analyse AST, allowlist et limite ;
- `tests/` — tests unitaires avec réseau et base simulés ;
- `.streamlit/config.toml` — thème de l'interface.
