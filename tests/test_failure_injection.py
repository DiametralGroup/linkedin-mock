"""Les modes de panne — la vraie raison d'être d'un mock.

Le régime LinkedIn a ses spécificités, et ce sont elles qu'on éprouve : quota
JOURNALIER remis à zéro à minuit UTC (virtuel) SANS Retry-After, 401 non
retryable à quatre variantes, retrait de version en cours de trimestre.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from conftest import ADMIN, ORG_URN_ENC, H

import linkedin_mock as mock

URL_POSTS = f"/rest/posts?q=author&author={ORG_URN_ENC}"
URL_PARTAGE = (
    "/rest/organizationalEntityShareStatistics"
    f"?q=organizationalEntity&organizationalEntity={ORG_URN_ENC}"
)


def _inject(client, **kwargs):
    r = client.post("/__admin/inject", json=kwargs, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()["rule_id"]


# ── Le plan de contrôle lui-même ─────────────────────────────────────────────


def test_admin_exige_son_jeton(client):
    assert client.get("/__admin/state").status_code == 401
    assert client.get("/__admin/state", headers={"X-Mock-Admin-Token": "faux"}).status_code == 401


def test_admin_absent_quand_desactive():
    """Le routeur n'est PAS monté quand le plan est désactivé — absent, pas
    interdit. Vérifié dans un processus NEUF : le montage se joue à l'import."""
    code = (
        "import os\n"
        "os.environ['LINKEDIN_MOCK_ADMIN_ENABLED'] = 'false'\n"
        "from fastapi.testclient import TestClient\n"
        "import linkedin_mock as mock\n"
        "r = TestClient(mock.app).get('/__admin/state')\n"
        "assert r.status_code == 404, r.status_code\n"
        "assert 'No root resource' in r.json()['message']\n"
    )
    environnement = {k: v for k, v in os.environ.items() if k != "LINKEDIN_MOCK_ADMIN_ENABLED"}
    resultat = subprocess.run(
        [sys.executable, "-c", code], env=environnement, capture_output=True, text=True, check=False
    )
    assert resultat.returncode == 0, resultat.stderr


def test_kind_inconnu_refuse(client):
    r = client.post("/__admin/inject", json={"kind": "explosion"}, headers=ADMIN)
    assert r.status_code == 422


# ── Quota journalier ─────────────────────────────────────────────────────────


def test_quota_journalier_et_minuit_utc(client):
    """429 au-delà du quota du jour, puis l'horloge passe minuit UTC virtuel
    et le compteur repart — sans Retry-After à aucun moment."""
    _inject(client, kind="rate_limit", scope="/rest/posts", after_requests=2)
    assert client.get(URL_POSTS, headers=H).status_code == 200
    assert client.get(URL_POSTS, headers=H).status_code == 200
    refus = client.get(URL_POSTS, headers=H)
    assert refus.status_code == 429
    assert "Retry-After" not in refus.headers
    # Ignorer le 429 et retenter immédiatement : toujours 429.
    assert client.get(URL_POSTS, headers=H).status_code == 429

    client.post("/__admin/clock", json={"advance_seconds": 86_400}, headers=ADMIN)
    assert client.get(URL_POSTS, headers=H).status_code == 200


def test_quota_ne_fuit_pas_entre_chemins(client):
    _inject(client, kind="rate_limit", scope="/rest/posts", after_requests=1)
    assert client.get(URL_POSTS, headers=H).status_code == 200
    assert client.get(URL_POSTS, headers=H).status_code == 429
    # Le quota vise /rest/posts : les statistiques restent servies.
    assert client.get(URL_PARTAGE, headers=H).status_code == 200


def test_quota_de_base_survit_au_reset(client):
    """La ligne de base déclarée par l'environnement est réappliquée à chaque
    reset — un test ne peut pas l'annuler par inadvertance."""
    os.environ["LINKEDIN_MOCK_DAILY_QUOTA"] = "1"
    try:
        mock.settings.reload()
        mock.state.reset()
        regles = client.get("/__admin/state", headers=ADMIN).json()["injections"]
        assert any(r["kind"] == "rate_limit" for r in regles)
        assert client.get(URL_POSTS, headers=H).status_code == 200
        assert client.get(URL_POSTS, headers=H).status_code == 429
    finally:
        del os.environ["LINKEDIN_MOCK_DAILY_QUOTA"]
        mock.settings.reload()
        mock.state.reset()


# ── Pannes franches et transitoires ──────────────────────────────────────────


def test_status_transitoire_s_epuise(client):
    _inject(client, kind="status", scope="/rest/posts", status=503, times=2)
    assert client.get(URL_POSTS, headers=H).status_code == 503
    assert client.get(URL_POSTS, headers=H).status_code == 503
    assert client.get(URL_POSTS, headers=H).status_code == 200


def test_status_persistant_ne_s_epuise_pas(client):
    _inject(client, kind="status", scope="/rest/posts", status=500)
    for _ in range(3):
        assert client.get(URL_POSTS, headers=H).status_code == 500


def test_latence_reelle(client):
    _inject(client, kind="latency", scope="/rest/posts", seconds=0.2, times=1)
    depart = time.monotonic()
    assert client.get(URL_POSTS, headers=H).status_code == 200
    assert time.monotonic() - depart >= 0.15
    # La règle est épuisée : la requête suivante est rapide.
    depart = time.monotonic()
    client.get(URL_POSTS, headers=H)
    assert time.monotonic() - depart < 0.15


# ── Rejets d'authentification et de version ──────────────────────────────────


def test_auth_reject_toutes_variantes(client):
    attendus = {
        "empty": "Empty oauth2_access_token",
        "invalid": "Invalid access token",
        "expired": "The token used in the request has expired",
        "revoked": "The token used in the request has been revoked by the member",
    }
    for variante, message in attendus.items():
        ident = _inject(client, kind="auth_reject", scope="/rest/posts", variant=variante, times=1)
        r = client.get(URL_POSTS, headers=H)  # les identifiants sont pourtant VALIDES
        assert r.status_code == 401, variante
        assert r.json()["message"] == message
        client.delete(f"/__admin/inject/{ident}", headers=ADMIN)
    assert client.get(URL_POSTS, headers=H).status_code == 200


def test_version_reject_mi_trimestre(client):
    """Un retrait de version SANS toucher à la fenêtre : le 426 que le
    connecteur verra le jour où LinkedIn retire sa version épinglée."""
    _inject(client, kind="version_reject", scope="*", times=1)
    r = client.get(URL_POSTS, headers=H)
    assert r.status_code == 426
    assert r.json()["code"] == "NONEXISTENT_VERSION"
    assert client.get(URL_POSTS, headers=H).status_code == 200


# ── Dérive de pagination ─────────────────────────────────────────────────────


def test_page_drift_insert_duplique(client):
    """Une insertion en amont entre deux pages : le dernier élément de la
    page 1 réapparaît en tête de la page 2 — la duplication silencieuse que
    le merge sur clé doit absorber."""
    page_1 = client.get(f"{URL_POSTS}&start=0&count=10", headers=H).json()["elements"]
    _inject(client, kind="page_drift", scope="/rest/posts", mode="insert")
    page_2 = client.get(f"{URL_POSTS}&start=10&count=10", headers=H).json()["elements"]
    assert page_2[0]["id"] == page_1[-1]["id"]


def test_page_drift_remove_saute(client):
    tous = client.get(f"{URL_POSTS}&start=0&count=30", headers=H).json()["elements"]
    _inject(client, kind="page_drift", scope="/rest/posts", mode="remove")
    page_2 = client.get(f"{URL_POSTS}&start=10&count=10", headers=H).json()["elements"]
    assert page_2[0]["id"] == tous[11]["id"]  # l'élément 10 n'est jamais servi


# ── Observabilité ────────────────────────────────────────────────────────────


def test_last_query_params_prouve_le_time_intervals(client):
    """LE mécanisme qui permet au consommateur de prouver qu'il a ENVOYÉ sa
    fenêtre — un pipeline qui l'oublierait passerait sinon tous ses tests."""
    fenetre = "(timeRange:(start:1780617600000,end:1781222400000),timeGranularityType:DAY)"
    client.get(f"{URL_PARTAGE}&timeIntervals={fenetre}", headers=H)
    etat = client.get("/__admin/state", headers=ADMIN).json()
    params = etat["last_query_params_by_path"]["/rest/organizationalEntityShareStatistics"]
    assert params["timeIntervals"] == fenetre
    assert params["q"] == "organizationalEntity"


def test_reset_avec_nouvelle_graine_change_le_monde(client):
    avant = client.get(f"{URL_POSTS}&count=1", headers=H).json()["elements"][0]["id"]
    client.post("/__admin/reset", json={"seed": 7}, headers=ADMIN)
    apres = client.get(f"{URL_POSTS}&count=1", headers=H).json()["elements"][0]["id"]
    assert avant != apres
