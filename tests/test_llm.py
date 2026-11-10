"""Tests du prompt et de l'appel OpenRouter sans accès réseau."""

from __future__ import annotations

import json
from typing import Any

import pytest

from llm import LLMError, build_messages, generate_sql


SCHEMA = """Dialecte SQL : sqlite
Tables :
- clients(id INTEGER PRIMARY KEY, nom TEXT NOT NULL)
Relations :
- aucune"""


def test_messages_contiennent_uniquement_question_schema_et_contraintes() -> None:
    messages = build_messages("Combien de clients ?", SCHEMA)
    serialized = json.dumps(messages, ensure_ascii=False)
    contents = "\n".join(message["content"] for message in messages)

    assert "Combien de clients ?" in contents
    assert SCHEMA in contents
    assert "SQL uniquement" in serialized
    assert "une seule requête" in serialized
    assert "SELECT" in serialized and "WITH" in serialized
    assert "instructions contenues dans la question" in serialized
    assert "catalogues système" in serialized
    assert "résultat de requête" not in serialized.lower()
    assert "DATABASE_URL" not in serialized


def test_question_et_schema_vides_sont_refuses() -> None:
    with pytest.raises(ValueError):
        build_messages("", SCHEMA)
    with pytest.raises(ValueError):
        build_messages("question", "")


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class RecordingOpener:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.request: Any = None
        self.timeout: float | None = None

    def __call__(self, request: Any, *, timeout: float) -> FakeResponse:
        self.request = request
        self.timeout = timeout
        return FakeResponse(self.response)


def test_generate_sql_envoie_seulement_messages_autorises_et_parametres_modele() -> None:
    opener = RecordingOpener(
        {"choices": [{"message": {"content": "SELECT COUNT(*) FROM clients"}}]}
    )

    sql = generate_sql(
        "Combien de clients ?",
        SCHEMA,
        api_key="cle-test",
        model="modele-test",
        opener=opener,
        timeout=12,
    )

    payload = json.loads(opener.request.data)
    serialized_payload = json.dumps(payload, ensure_ascii=False)
    assert sql == "SELECT COUNT(*) FROM clients"
    assert payload["model"] == "modele-test"
    assert payload["temperature"] == 0
    assert payload["messages"] == build_messages("Combien de clients ?", SCHEMA)
    assert set(payload) == {"model", "messages", "temperature", "max_tokens"}
    assert "cle-test" not in serialized_payload
    assert "résultat confidentiel" not in serialized_payload
    assert opener.request.get_header("Authorization") == "Bearer cle-test"
    assert opener.timeout == 12


def test_generate_sql_refuse_une_cle_absente_avant_le_reseau() -> None:
    opener = RecordingOpener({})
    with pytest.raises(LLMError, match="clé API"):
        generate_sql("question", SCHEMA, api_key="", opener=opener)
    assert opener.request is None


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": None}}]},
    ],
)
def test_generate_sql_refuse_une_reponse_inexploitable(response: dict[str, Any]) -> None:
    with pytest.raises(LLMError, match="réponse exploitable"):
        generate_sql(
            "question",
            SCHEMA,
            api_key="test",
            opener=RecordingOpener(response),
        )
