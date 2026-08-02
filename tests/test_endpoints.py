"""Les quatre parcours consommateur, de bout en bout.

Ce sont les gestes EXACTS du connecteur insights360 : smoke test credentials,
listage paginé des posts, stats vie-entière par lots per-share, buckets
quotidiens org / abonnés / vues. Si un de ces tests casse, le pipeline aval
casse pareil.
"""

from __future__ import annotations

from datetime import UTC, datetime

from conftest import ORG_URN, ORG_URN_ENC, H, tous_les_posts

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


# ── Parcours 1 : smoke test credentials ──────────────────────────────────────


def test_smoke_credentials(client):
    org = client.get("/rest/organizations/40123456", headers=H)
    assert org.status_code == 200
    assert org.json()["id"] == 40123456  # un NOMBRE, pas une chaîne
    assert org.json()["$URN"] == ORG_URN
    assert org.json()["vanityName"] == "boreal-conseil"

    reseau = client.get(
        f"/rest/networkSizes/{ORG_URN_ENC}?edgeType=COMPANY_FOLLOWED_BY_MEMBER", headers=H
    )
    assert reseau.status_code == 200
    assert reseau.json()["firstDegreeSize"] > 2612  # base + gains

    inconnue = client.get("/rest/organizations/999999", headers=H)
    assert inconnue.status_code == 404
    assert inconnue.json()["message"] == "Organization 999999 is inactive"


# ── Parcours 2 : listage paginé des posts ────────────────────────────────────


def test_walk_complet_du_finder(client):
    posts = tous_les_posts(client)
    assert len(posts) == 72
    assert len({p["id"] for p in posts}) == 72
    assert all(p["lifecycleState"] == "PUBLISHED" for p in posts)
    assert all(p["author"] == ORG_URN for p in posts)
    types = {p["id"].split(":")[2] for p in posts}
    assert types == {"share", "ugcPost"}  # les deux familles d'URN


def test_matiere_editoriale_du_jeu(client):
    posts = tous_les_posts(client)
    edites = [p for p in posts if p["lifecycleStateInfo"]["isEditedByAuthor"]]
    assert len(edites) == 3
    assert all(p["lastModifiedAt"] > p["publishedAt"] for p in edites)
    repartages = [p for p in posts if "reshareContext" in p]
    assert len(repartages) == 3
    ids = {p["id"] for p in posts}
    assert all(r["reshareContext"]["parent"] in ids for r in repartages)
    avec_hashtag = [p for p in posts if "{hashtag|\\#|" in p["commentary"]]
    assert avec_hashtag, "les gabarits little-format doivent être servis"
    avec_mention = [p for p in posts if "@[" in p["commentary"]]
    assert all("](urn:li:organization:" in p["commentary"] for p in avec_mention)


def test_lecture_unitaire_et_erreurs(client, linkedin_state):
    urn = linkedin_state.dataset["posts"][5]["id"]
    r = client.get(f"/rest/posts/{_enc(urn)}", headers=H)
    assert r.status_code == 200
    assert r.json()["id"] == urn
    assert client.get("/rest/posts/urn%3Ali%3Ashare%3A42", headers=H).status_code == 404
    assert client.get("/rest/posts/pasunurn", headers=H).status_code == 400


# ── Parcours 3 : stats par publication, par lots ─────────────────────────────


def test_stats_vie_entiere_par_lots(client):
    """Le geste du connecteur : lots de 20 URN, répartis shares/ugcPosts."""
    posts = tous_les_posts(client)
    elements: list[dict] = []
    for debut in range(0, len(posts), 20):
        lot = posts[debut : debut + 20]
        partages = [p["id"] for p in lot if p["id"].startswith("urn:li:share:")]
        ugc = [p["id"] for p in lot if p["id"].startswith("urn:li:ugcPost:")]
        for nom, urns in (("shares", partages), ("ugcPosts", ugc)):
            if not urns:
                continue
            r = client.get(
                f"{URL_PARTAGE}&{nom}=List({','.join(_enc(u) for u in urns)})", headers=H
            )
            assert r.status_code == 200
            elements.extend(r.json()["elements"])

    # Tous les posts du jeu de base ont de l'activité : un élément chacun.
    assert len(elements) == len(posts)
    for element in elements:
        cle = "ugcPost" if "ugcPost" in element else "share"
        assert element[cle].startswith("urn:li:")
        assert element["organizationalEntity"] == ORG_URN
        stats = element["totalShareStatistics"]
        assert stats["impressionCount"] > 0
        assert stats["uniqueImpressionsCount"] <= stats["impressionCount"]


def test_share_inconnu_ou_sans_activite_omis(client, linkedin_state):
    connu = next(
        p["id"] for p in linkedin_state.dataset["posts"] if p["id"].startswith("urn:li:share")
    )
    fantome = "urn:li:share:1111111111111111111"
    r = client.get(f"{URL_PARTAGE}&shares=List({_enc(connu)},{_enc(fantome)})", headers=H)
    assert r.status_code == 200
    assert len(r.json()["elements"]) == 1  # le fantôme est OMIS, pas à zéro


# ── Parcours 4 : buckets quotidiens ──────────────────────────────────────────


def test_buckets_org_contigus_et_alignes(client):
    debut = 1780617600000  # 2026-06-05 minuit UTC
    fin = debut + 10 * JOUR_MS
    r = client.get(
        f"{URL_PARTAGE}&timeIntervals=(timeRange:(start:{debut},end:{fin}),"
        "timeGranularityType:DAY)",
        headers=H,
    )
    elements = r.json()["elements"]
    assert len(elements) == 10
    for rang, element in enumerate(elements):
        plage = element["timeRange"]
        assert plage["end"] - plage["start"] == JOUR_MS
        assert plage["start"] == debut + rang * JOUR_MS
        assert datetime.fromtimestamp(plage["start"] / 1000, tz=UTC).hour == 0


def test_fenetre_glissante_12_mois_ecretee(client):
    """Demander depuis 2024 : les buckets antérieurs à J-365 n'existent pas."""
    debut_2024 = 1725148800000  # 2024-09-01
    fin = 1780617600000  # 2026-06-05
    r = client.get(
        f"{URL_PARTAGE}&timeIntervals=(timeRange:(start:{debut_2024},end:{fin}),"
        "timeGranularityType:MONTH)",
        headers=H,
    )
    elements = r.json()["elements"]
    assert elements, "la partie dans la fenêtre doit être servie"
    premier = datetime.fromtimestamp(elements[0]["timeRange"]["start"] / 1000, tz=UTC).date()
    assert premier.isoformat() >= "2025-07-01"  # écrêté à ~J-365, aligné au mois


def test_gains_abonnes_bornes_j_moins_2(client):
    """Fenêtre demandée jusqu'à « maintenant » : le dernier bucket servi doit
    s'arrêter à J-2 — la règle de disponibilité documentée."""
    debut = 1780272000000  # 2026-06-01
    fin = 1784678400000  # 2026-07-22 — au-delà de l'ancre du jeu
    r = client.get(
        f"{URL_ABONNES}&timeIntervals=(timeRange:(start:{debut},end:{fin}),"
        "timeGranularityType:DAY)",
        headers=H,
    )
    elements = r.json()["elements"]
    assert elements
    dernier = datetime.fromtimestamp(elements[-1]["timeRange"]["end"] / 1000, tz=UTC).date()
    assert dernier.isoformat() <= "2026-07-14"  # end exclusif → données ≤ 07-13 = J-2
    assert any(
        e["followerGains"]["organicFollowerGain"] != 0
        or e["followerGains"]["paidFollowerGain"] != 0
        for e in elements
    )


def test_gains_hebdomadaires_et_campagne_payante(client):
    debut = 1759104000000  # 2025-09-29 (lundi) — couvre la campagne d'octobre 2025
    fin = debut + 35 * JOUR_MS
    r = client.get(
        f"{URL_ABONNES}&timeIntervals=(timeRange:(start:{debut},end:{fin}),"
        "timeGranularityType:WEEK)",
        headers=H,
    )
    elements = r.json()["elements"]
    assert elements
    assert sum(e["followerGains"]["paidFollowerGain"] for e in elements) > 0


def test_demographie_vie_entiere(client):
    r = client.get(URL_ABONNES, headers=H)
    element = r.json()["elements"][0]
    familles = [
        "followerCountsByAssociationType",
        "followerCountsByGeoCountry",
        "followerCountsByFunction",
        "followerCountsByIndustry",
        "followerCountsByGeo",
        "followerCountsBySeniority",
        "followerCountsByStaffCountRange",
    ]
    for famille in familles:
        assert element[famille], famille
    salaries = element["followerCountsByAssociationType"][0]
    assert salaries["associationType"] == "EMPLOYEE"
    assert salaries["followerCounts"] == {"organicFollowerCount": 34, "paidFollowerCount": 0}
    # Les démographies roulent le payant dans l'organique.
    for entree in element["followerCountsByGeoCountry"]:
        assert entree["followerCounts"]["paidFollowerCount"] == 0


def test_vues_de_page_quotidiennes(client):
    debut = 1780617600000  # 2026-06-05
    fin = debut + 5 * JOUR_MS
    r = client.get(
        f"{URL_PAGE}&timeIntervals=(timeRange:(start:{debut},end:{fin}),timeGranularityType:DAY)",
        headers=H,
    )
    elements = r.json()["elements"]
    assert len(elements) == 5
    for element in elements:
        vues = element["totalPageStatistics"]["views"]
        assert element["organization"] == ORG_URN
        for famille in ("allPageViews", "overviewPageViews", "careersPageViews"):
            assert vues[famille]["uniquePageViews"] <= vues[famille]["pageViews"]
