"""Tests des barrières SQL locales."""

import pytest
from sqlglot import parse_one
from sqlglot.errors import SqlglotError, TokenError

import sql_security
from sql_security import SQLSecurityError, validate_and_limit_sql


TABLES = {"clients", "produits", "commandes"}


def secure(sql: str, max_rows: int = 100) -> str:
    return validate_and_limit_sql(sql, TABLES, max_rows=max_rows, dialect="sqlite")


def secure_postgres(sql: str, max_rows: int = 100) -> str:
    return validate_and_limit_sql(sql, TABLES, max_rows=max_rows, dialect="postgres")


def test_select_simple_recoit_une_limite_locale() -> None:
    expression = parse_one(secure("SELECT nom FROM clients"), dialect="sqlite")
    assert int(expression.args["limit"].expression.this) == 100


def test_with_est_accepte_et_les_alias_cte_ne_sont_pas_des_tables_metier() -> None:
    sql = secure(
        "WITH ventes AS (SELECT produit_id FROM commandes) "
        "SELECT produit_id FROM ventes"
    )
    assert "WITH ventes AS" in sql


def test_un_bloc_markdown_sql_unique_est_nettoye() -> None:
    sql = secure("```sql\nSELECT * FROM produits\n```")
    assert sql.startswith("SELECT * FROM produits")
    assert "```" not in sql


@pytest.mark.parametrize("sql", ["", "   ", "```sql\n\n```"])
def test_une_reponse_vide_est_refusee(sql: str) -> None:
    with pytest.raises(SQLSecurityError, match="vide"):
        secure(sql)


def test_une_erreur_de_tokenisation_devient_une_erreur_applicative() -> None:
    with pytest.raises(SQLSecurityError, match="invalide") as caught:
        secure("SELECT '")

    assert isinstance(caught.value.__cause__, TokenError)


def test_toute_erreur_sqlglot_devient_une_erreur_applicative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_parse(*args: object, **kwargs: object) -> None:
        raise SqlglotError("échec interne du parseur")

    monkeypatch.setattr(sql_security, "parse", fail_parse)

    with pytest.raises(SQLSecurityError, match="invalide") as caught:
        secure("SELECT 1")

    assert isinstance(caught.value.__cause__, SqlglotError)


def test_plusieurs_instructions_sont_refusees() -> None:
    with pytest.raises(SQLSecurityError, match="une seule"):
        secure("SELECT * FROM clients; SELECT * FROM produits")


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO clients (nom) VALUES ('x')",
        "UPDATE clients SET nom = 'x'",
        "DELETE FROM clients",
        "DROP TABLE clients",
        "CREATE TABLE pirate (id INTEGER)",
        "COPY clients TO '/tmp/clients.csv'",
        "PRAGMA table_info(clients)",
    ],
)
def test_les_requetes_non_select_sont_refusees(sql: str) -> None:
    with pytest.raises(SQLSecurityError):
        secure(sql)


def test_une_table_hors_allowlist_est_refusee() -> None:
    with pytest.raises(SQLSecurityError, match="interdite.*secrets"):
        secure("SELECT * FROM secrets")


def test_un_cte_imbrique_ne_masque_pas_une_table_externe_interdite() -> None:
    sql = (
        "SELECT * FROM secrets_view "
        "WHERE EXISTS ("
        "WITH secrets_view AS (SELECT 1) "
        "SELECT 1 FROM secrets_view"
        ")"
    )

    with pytest.raises(SQLSecurityError, match="interdite.*secrets_view"):
        secure(sql)


@pytest.mark.parametrize(
    "sql",
    [
        'WITH "Secrets" AS (SELECT 1) SELECT * FROM Secrets',
        'WITH secrets AS (SELECT 1) SELECT * FROM "secrets"',
    ],
)
def test_les_identifiants_de_relation_cites_sont_refuses(sql: str) -> None:
    with pytest.raises(SQLSecurityError, match="cités"):
        secure_postgres(sql)


def test_un_cte_non_cite_est_resolu_sans_sensibilite_a_la_casse() -> None:
    sql = secure_postgres(
        "WITH Ventes AS (SELECT produit_id FROM commandes) "
        "SELECT produit_id FROM VENTES"
    )

    assert "WITH Ventes AS" in sql


@pytest.mark.parametrize(
    "sql",
    [
        "WITH x AS (DELETE FROM clients RETURNING *) SELECT * FROM x",
        "WITH x AS (INSERT INTO clients (nom) VALUES ('x') RETURNING *) SELECT * FROM x",
        "WITH x AS (UPDATE clients SET nom = 'x' RETURNING *) SELECT * FROM x",
    ],
)
def test_les_noeuds_dml_imbriques_dans_un_cte_sont_refuses(sql: str) -> None:
    with pytest.raises(SQLSecurityError, match="DML ou DDL imbriqué"):
        secure(sql)


def test_un_noeud_ddl_imbrique_est_refuse_meme_si_la_racine_est_select() -> None:
    # sqlglot autorise la construction programmatique d'arbres : cette forme
    # garantit que le contrôle parcourt tout l'AST et pas seulement sa racine.
    from sqlglot import exp
    import sql_security

    expression = exp.select("*").from_(
        exp.Subquery(this=exp.Drop(this=exp.Table(this="clients")), alias="x")
    )
    with pytest.raises(SQLSecurityError, match="DML ou DDL imbriqué"):
        sql_security._check_forbidden_nodes(expression)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM sqlite_master",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM pg_user",
    ],
)
def test_les_catalogues_systeme_sont_refuses(sql: str) -> None:
    with pytest.raises(SQLSecurityError, match="système"):
        secure(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_read_file('/etc/passwd') FROM clients",
        "SELECT pg_sleep(10) FROM clients",
        "SELECT dblink('connexion', 'SELECT 1') FROM clients",
        "SELECT dblink_connect('connexion') FROM clients",
        "SELECT pg_terminate_backend(123) FROM clients",
        "SELECT query_to_xml('SELECT 1', true, false, '') FROM clients",
        "SELECT set_config('x', 'y', false) FROM clients",
    ],
)
def test_les_fonctions_dangereuses_sont_refusees(sql: str) -> None:
    with pytest.raises(SQLSecurityError, match="Fonction interdite"):
        secure(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT query_to_xml_and_xmlschema('SELECT 1', true, false, '') FROM clients",
        "SELECT table_to_xml('clients', true, false, '') FROM clients",
        "SELECT database_to_xml(true, false, '') FROM clients",
        "SELECT dblink_connect_u('connexion') FROM clients",
        "SELECT randomblob(100000000) FROM clients",
        "SELECT fonction_inconnue(id) FROM clients",
    ],
)
def test_seules_les_fonctions_explicitement_sures_sont_acceptees(sql: str) -> None:
    with pytest.raises(SQLSecurityError, match="Fonction interdite"):
        secure_postgres(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM clients",
        "SELECT SUM(id), AVG(id), MIN(id), MAX(id) FROM clients",
        "SELECT LOWER(nom), UPPER(nom), LENGTH(nom) FROM clients",
        "SELECT COALESCE(nom, 'inconnu'), ROUND(id, 0) FROM clients",
    ],
)
def test_les_fonctions_de_lecture_usuelles_restent_acceptees(sql: str) -> None:
    secure_postgres(sql)


def test_les_cte_recursifs_sont_refuses() -> None:
    with pytest.raises(SQLSecurityError, match="récursifs"):
        secure(
            "WITH RECURSIVE compteur(n) AS ("
            "SELECT 1 UNION ALL SELECT n + 1 FROM compteur"
            ") SELECT * FROM compteur"
        )


def test_une_limite_existante_trop_haute_est_abaissee() -> None:
    expression = parse_one(secure("SELECT * FROM clients LIMIT 500", 25))
    assert int(expression.args["limit"].expression.this) == 25


def test_une_limite_existante_plus_basse_est_conservee() -> None:
    expression = parse_one(secure("SELECT * FROM clients LIMIT 10", 25))
    assert int(expression.args["limit"].expression.this) == 10


@pytest.mark.parametrize("max_rows", [0, -1])
def test_la_limite_locale_doit_etre_positive(max_rows: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        secure("SELECT * FROM clients", max_rows)
