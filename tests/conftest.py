"""Harnais de tests.

Deux réglages d'environnement, posés AVANT l'import du paquet (la
configuration est lue à l'import) :

  • le plan de contrôle `/__admin` est monté — le montage est conditionnel ;
  • l'intervalle d'évolution passe à 3600 s : aucun événement ne se déclenche
    au fil de l'horloge murale pendant la suite, même sur une CI lente. Les
    tests d'évolution font défiler le temps EXPLICITEMENT via /__admin/clock —
    c'est ce qui les rend déterministes.
"""

from __future__ import annotations

import os

os.environ.setdefault("LINKEDIN_MOCK_ADMIN_ENABLED", "true")
os.environ.setdefault("LINKEDIN_MOCK_EVOLUTION_INTERVAL", "3600")

import pytest
from fastapi.testclient import TestClient

import linkedin_mock as mock

ORG_URN = "urn:li:organization:40123456"
ORG_URN_ENC = "urn%3Ali%3Aorganization%3A40123456"

#: Les trois en-têtes d'une requête bien formée — le trousseau par défaut.
H = {
    "Authorization": "Bearer mock-linkedin-token",
    "Linkedin-Version": "202506",
    "X-Restli-Protocol-Version": "2.0.0",
}
ADMIN = {"X-Mock-Admin-Token": "mock-admin-token"}


@pytest.fixture()
def client():
    """Un client sur un état REMIS À NEUF — avant ET après, pour qu'un test ne
    lègue ni règle d'injection ni événement d'évolution au suivant."""
    c = TestClient(mock.app)
    mock.state.reset()
    yield c
    mock.state.reset()


@pytest.fixture()
def linkedin_state(client):  # noqa: ARG001 — la fixture chaîne le reset
    """L'état mutable du mock, pour les tests qui inspectent le dataset."""
    return mock.state


def tous_les_posts(client) -> list[dict]:
    """Le parcours de pagination complet du finder — l'outil des journeys."""
    posts: list[dict] = []
    start = 0
    while True:
        r = client.get(
            f"/rest/posts?q=author&author={ORG_URN_ENC}&start={start}&count=25", headers=H
        )
        assert r.status_code == 200, r.text
        page = r.json()["elements"]
        posts.extend(page)
        if len(page) < 25:
            return posts
        start += 25
