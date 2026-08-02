"""L'évolution temporelle — le delta que l'extraction incrémentale doit voir.

Les tests font défiler le temps EXPLICITEMENT via /__admin/clock (l'intervalle
d'évolution est à 3600 s dans le harnais : rien ne bouge tout seul). Chaque
événement écrit dans le jour UTC de SON horodatage — une avance de quatre
jours remplit quatre jours de buckets.
"""

from __future__ import annotations

import os

from conftest import ADMIN, ORG_URN_ENC, H, tous_les_posts

import linkedin_mock as mock

URL_POSTS = f"/rest/posts?q=author&author={ORG_URN_ENC}"
URL_PARTAGE = (
    "/rest/organizationalEntityShareStatistics"
    f"?q=organizationalEntity&organizationalEntity={ORG_URN_ENC}"
)
URL_ABONNES = (
    "/rest/organizationalEntityFollowerStatistics"
    f"?q=organizationalEntity&organizationalEntity={ORG_URN_ENC}"
)

JOUR_MS = 86_400_000
#: 2026-07-14 minuit UTC — le lendemain du plafond de stats du jeu de base.
LENDEMAIN_PLAFOND_MS = 1783987200000
QUATRE_JOURS = 4 * 86_400


def _avancer(client, secondes: int) -> None:
    r = client.post("/__admin/clock", json={"advance_seconds": secondes}, headers=ADMIN)
    assert r.status_code == 200


def test_fige_sans_avance_d_horloge(client):
    """Intervalle 3600 s et pas d'avance : deux lectures identiques à l'octet."""
    a = client.get(URL_PARTAGE, headers=H).json()
    b = client.get(URL_PARTAGE, headers=H).json()
    assert a == b


def test_avance_remplit_les_jours_suivants(client):
    """+4 jours : des buckets quotidiens APRÈS le plafond du jeu de base."""
    _avancer(client, QUATRE_JOURS)
    fin = LENDEMAIN_PLAFOND_MS + 6 * JOUR_MS
    r = client.get(
        f"{URL_PARTAGE}&timeIntervals=(timeRange:(start:{LENDEMAIN_PLAFOND_MS},end:{fin}),"
        "timeGranularityType:DAY)",
        headers=H,
    )
    elements = r.json()["elements"]
    assert elements
    assert sum(e["totalShareStatistics"]["impressionCount"] for e in elements) > 0


def test_gains_abonnes_apres_avance(client):
    """La fenêtre J-2 s'ouvre au fil de l'avance : les gains des événements
    deviennent visibles."""
    _avancer(client, QUATRE_JOURS)
    fin = LENDEMAIN_PLAFOND_MS + 10 * JOUR_MS
    r = client.get(
        f"{URL_ABONNES}&timeIntervals=(timeRange:(start:{LENDEMAIN_PLAFOND_MS},end:{fin}),"
        "timeGranularityType:DAY)",
        headers=H,
    )
    elements = r.json()["elements"]
    assert elements
    assert sum(e["followerGains"]["organicFollowerGain"] for e in elements) > 0


def test_nouveau_post_en_tete_du_finder(client):
    avant = client.get(f"{URL_POSTS}&count=1", headers=H).json()["elements"][0]
    _avancer(client, QUATRE_JOURS)
    apres = client.get(f"{URL_POSTS}&count=1", headers=H).json()["elements"][0]
    assert apres["lastModifiedAt"] > avant["lastModifiedAt"]
    assert len(tous_les_posts(client)) > 72


def test_edition_re_surface_un_post_ancien(client):
    """Un post VIEUX re-surfacé par édition : le geste que le curseur
    lastModifiedAt doit revoir — l'analogue du _profil de boond."""
    plafond_base = max(p["lastModifiedAt"] for p in mock.state.dataset["posts"])
    _avancer(client, QUATRE_JOURS)
    posts = tous_les_posts(client)
    edites_anciens = [
        p
        for p in posts
        if p["lastModifiedAt"] > plafond_base
        and p["publishedAt"] < plafond_base - 30 * JOUR_MS
        and p["lifecycleStateInfo"]["isEditedByAuthor"]
    ]
    assert edites_anciens, "au moins un post ancien doit avoir été édité"


def test_somme_egale_vie_entiere_apres_evolution(client):
    """L'invariant central SURVIT à l'évolution : lifetime dérivé des buckets."""
    _avancer(client, QUATRE_JOURS)
    posts = tous_les_posts(client)
    nouveau = max(posts, key=lambda p: p["publishedAt"])
    serie = mock.state.dataset["series_posts"][nouveau["id"]]
    if not serie:
        return  # publié dans la dernière heure virtuelle : pas encore de stats
    r = client.get(f"{URL_PARTAGE}&shares=List({nouveau['id'].replace(':', '%3A')})", headers=H)
    element = r.json()["elements"][0]["totalShareStatistics"]
    assert element["impressionCount"] == sum(b["impressionCount"] for b in serie.values())


def test_chronologie_deterministe(client):
    """reset → +1 jour → capture, deux fois : les mêmes octets."""

    def capture() -> tuple:
        mock.state.reset()
        _avancer(client, 86_400)
        page = client.get(f"{URL_POSTS}&count=5", headers=H).json()
        fin = LENDEMAIN_PLAFOND_MS + 3 * JOUR_MS
        jour = client.get(
            f"{URL_PARTAGE}&timeIntervals=(timeRange:(start:{LENDEMAIN_PLAFOND_MS},end:{fin}),"
            "timeGranularityType:DAY)",
            headers=H,
        ).json()
        return page, jour

    assert capture() == capture()


def test_journal_expose_le_delta(client):
    _avancer(client, 6 * 3600)
    etat = client.get("/__admin/state", headers=ADMIN).json()
    assert etat["evolution"]["applied"] >= 6
    genres = {entree["kind"] for entree in etat["evolution"]["journal"]}
    assert genres <= {"stats_jour", "nouveau_post", "gains_abonnes", "edition_post", "pic_viral"}


def test_desactivable_par_env(client):
    os.environ["LINKEDIN_MOCK_EVOLUTION"] = "false"
    try:
        mock.settings.reload()
        mock.state.reset()
        _avancer(client, QUATRE_JOURS)
        etat = client.get("/__admin/state", headers=ADMIN).json()
        assert etat["evolution"]["applied"] == 0
        assert len(tous_les_posts(client)) == 72
    finally:
        del os.environ["LINKEDIN_MOCK_EVOLUTION"]
        mock.settings.reload()
        mock.state.reset()


def test_reset_rearme_la_chronologie(client):
    _avancer(client, QUATRE_JOURS)
    assert client.get("/__admin/state", headers=ADMIN).json()["evolution"]["applied"] > 0
    client.post("/__admin/reset", headers=ADMIN)
    etat = client.get("/__admin/state", headers=ADMIN).json()
    assert etat["evolution"]["applied"] == 0
    assert etat["totals"]["posts"] == 72


def test_mutate_pousse_le_post_en_tete(client):
    cible = mock.state.dataset["posts"][3]["id"]
    r = client.post(
        "/__admin/mutate",
        json={"post_id": cible, "commentary": "Contenu corrigé après coup."},
        headers=ADMIN,
    )
    assert r.status_code == 200
    tete = client.get(f"{URL_POSTS}&count=1", headers=H).json()["elements"][0]
    assert tete["id"] == cible
    assert tete["commentary"] == "Contenu corrigé après coup."
    assert tete["lifecycleStateInfo"]["isEditedByAuthor"] is True


def test_delete_sort_du_finder_mais_pas_de_l_histoire(client):
    """Le régime réel : le post supprimé disparaît du listage et du per-share,
    mais les agrégats organisation gardent son passé."""
    avant = client.get(URL_PARTAGE, headers=H).json()["elements"][0]["totalShareStatistics"]
    cible = mock.state.dataset["posts"][0]["id"]
    client.post("/__admin/delete", json={"post_id": cible}, headers=ADMIN)

    assert len(tous_les_posts(client)) == 71
    per_share = client.get(f"{URL_PARTAGE}&shares=List({cible.replace(':', '%3A')})", headers=H)
    if not per_share.json()["elements"]:
        pass  # share : omis, comme attendu
    else:  # la cible était un ugcPost : la famille shares ne l'aurait jamais servi
        raise AssertionError("le post supprimé ne doit plus être servi en per-share")
    apres = client.get(URL_PARTAGE, headers=H).json()["elements"][0]["totalShareStatistics"]
    assert apres["impressionCount"] == avant["impressionCount"]
