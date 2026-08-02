"""Les invariants de cohérence — ce qui rend le mock comparable à une vraie page.

L'équivalent des test_coherence_* de boondmanager-mock : chaque invariant est
une propriété que le monde réel a par construction, et que le générateur doit
donc avoir par construction AUSSI — pas par chance de seed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from conftest import ORG_URN_ENC, H

import linkedin_mock as mock
from linkedin_mock.dataset.realiste import DERNIERE_MAJ, DERNIERE_STAT, build_realiste_dataset

URL_PARTAGE = (
    "/rest/organizationalEntityShareStatistics"
    f"?q=organizationalEntity&organizationalEntity={ORG_URN_ENC}"
)
URL_ABONNES = (
    "/rest/organizationalEntityFollowerStatistics"
    f"?q=organizationalEntity&organizationalEntity={ORG_URN_ENC}"
)
URL_PAGE = f"/rest/organizationPageStatistics?q=organization&organization={ORG_URN_ENC}"

JOUR_MS = 86_400_000


def _enc(urn: str) -> str:
    return urn.replace(":", "%3A")


def test_ordres_de_grandeur_par_bucket(linkedin_state):
    """impressions ≥ clics, et likes+commentaires+partages ≤ impressions —
    sur CHAQUE bucket de CHAQUE série."""
    for serie in linkedin_state.dataset["series_posts"].values():
        for jour, bucket in serie.items():
            assert bucket["impressionCount"] >= bucket["clickCount"] >= 0, jour
            interactions_sociales = (
                bucket["likeCount"] + bucket["commentCount"] + bucket["shareCount"]
            )
            assert interactions_sociales <= bucket["impressionCount"], jour
            assert all(v >= 0 for v in bucket.values()), jour


def test_vie_entiere_est_la_somme_des_buckets(client, linkedin_state):
    """L'invariant central : sum(daily) == lifetime, par poste et org-wide.

    Les compteurs vie-entière sont DÉRIVÉS par sommation à la sérialisation —
    ce test verrouille le contrat pour qu'une future « optimisation » ne
    dénormalise pas en douce."""
    org = client.get(URL_PARTAGE, headers=H).json()["elements"][0]["totalShareStatistics"]
    attendu = sum(
        bucket["impressionCount"]
        for serie in linkedin_state.dataset["series_posts"].values()
        for bucket in serie.values()
    )
    assert org["impressionCount"] == attendu

    # Par post : un post RÉCENT (série entièrement dans la fenêtre glissante),
    # la somme de ses buckets servis == son agrégat per-share.
    posts = linkedin_state.dataset["posts"]
    recent = posts[-3]
    vie = client.get(f"{URL_PARTAGE}&shares=List({_enc(recent['id'])})", headers=H)
    if not vie.json()["elements"]:  # ugcPost selon le seed : requêter l'autre famille
        vie = client.get(f"{URL_PARTAGE}&ugcPosts=List({_enc(recent['id'])})", headers=H)
    compteurs = vie.json()["elements"][0]["totalShareStatistics"]
    serie = linkedin_state.dataset["series_posts"][recent["id"]]
    assert compteurs["impressionCount"] == sum(b["impressionCount"] for b in serie.values())
    assert compteurs["clickCount"] == sum(b["clickCount"] for b in serie.values())


def test_engagement_formule_exacte_partout(client, linkedin_state):
    posts = linkedin_state.dataset["posts"][:8]
    urns = ",".join(_enc(p["id"]) for p in posts if p["id"].startswith("urn:li:share"))
    r = client.get(f"{URL_PARTAGE}&shares=List({urns})", headers=H)
    for element in r.json()["elements"]:
        stats = element["totalShareStatistics"]
        attendu = (
            stats["clickCount"] + stats["likeCount"] + stats["commentCount"] + stats["shareCount"]
        ) / stats["impressionCount"]
        assert stats["engagement"] == attendu


def test_series_demarrent_a_la_publication(linkedin_state):
    for post in linkedin_state.dataset["posts"]:
        serie = linkedin_state.dataset["series_posts"][post["id"]]
        jour_publication = datetime.fromtimestamp(post["publishedAt"] / 1000, tz=UTC).date()
        jours = sorted(serie)
        assert jours[0] == jour_publication.isoformat(), post["id"]
        assert jours[-1] <= DERNIERE_STAT.isoformat(), post["id"]


def test_plafonds_temporels_du_jeu_de_base(linkedin_state):
    """createdAt ≤ publishedAt ≤ lastModifiedAt, et le plafond lastModifiedAt :
    un curseur posé après DERNIERE_MAJ doit voir zéro post du jeu de base."""
    plafond_ms = (DERNIERE_MAJ.toordinal() + 1 - datetime(1970, 1, 1).toordinal()) * JOUR_MS
    for post in linkedin_state.dataset["posts"]:
        assert post["createdAt"] <= post["publishedAt"] <= post["lastModifiedAt"]
        assert post["lastModifiedAt"] < plafond_ms


def test_reseau_et_facettes(client):
    reseau = client.get(
        f"/rest/networkSizes/{ORG_URN_ENC}?edgeType=COMPANY_FOLLOWED_BY_MEMBER", headers=H
    ).json()["firstDegreeSize"]
    etat = mock.state.dataset
    gains = sum(
        g["organicFollowerGain"] + g["paidFollowerGain"] for g in etat["serie_abonnes"].values()
    )
    assert reseau == etat["abonnes_base"] + gains

    element = client.get(URL_ABONNES, headers=H).json()["elements"][0]
    for famille, _cle, _segments, couverture in etat["parts_demographie"]:
        total_facette = sum(e["followerCounts"]["organicFollowerCount"] for e in element[famille])
        # Chaque facette somme EXACTEMENT à round(total x couverture) — la
        # répartition au plus fort reste ne perd ni n'invente personne.
        assert total_facette == round(reseau * couverture), famille


def test_arithmetique_des_vues(client):
    vues = client.get(URL_PAGE, headers=H).json()["elements"][0]["totalPageStatistics"]["views"]

    def n(cle: str) -> int:
        return vues[cle]["pageViews"]

    assert n("allPageViews") == n("allDesktopPageViews") + n("allMobilePageViews")
    assert n("allPageViews") == n("overviewPageViews") + n("careersPageViews")
    assert n("careersPageViews") == n("jobsPageViews") + n("lifeAtPageViews")
    assert n("desktopCareersPageViews") == n("desktopJobsPageViews") + n("desktopLifeAtPageViews")


def test_vues_correlees_aux_jours_de_post(linkedin_state):
    """Les jours de publication font plus de vues que les jours calmes."""
    jours_posts = {
        datetime.fromtimestamp(p["publishedAt"] / 1000, tz=UTC).date().isoformat()
        for p in linkedin_state.dataset["posts"]
    }
    serie = linkedin_state.dataset["serie_vues"]
    vues_post = [rec["overview"] for jour, rec in serie.items() if jour in jours_posts]
    vues_calmes = [rec["overview"] for jour, rec in serie.items() if jour not in jours_posts]
    assert sum(vues_post) / len(vues_post) > sum(vues_calmes) / len(vues_calmes)


def test_post_viral_present(linkedin_state):
    """Un pic déterministe — l'anomalie que l'aval doit savoir encaisser."""
    impressions_par_post = [
        sum(b["impressionCount"] for b in serie.values())
        for serie in linkedin_state.dataset["series_posts"].values()
    ]
    tri = sorted(impressions_par_post)
    assert tri[-1] > 5 * tri[-2] or tri[-1] > 30_000


def test_determinisme_octet_pres():
    """Deux builds à la même graine → le même monde, à l'octet près."""
    a = json.dumps(build_realiste_dataset(42), sort_keys=True, default=str)
    b = json.dumps(build_realiste_dataset(42), sort_keys=True, default=str)
    assert a == b
    autre = json.dumps(build_realiste_dataset(7), sort_keys=True, default=str)
    assert a != autre
