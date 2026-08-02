"""Le dialecte : en-têtes, Rest.li, enveloppes, erreurs.

Chaque assertion de forme correspond à un relevé de la doc officielle
(learn.microsoft.com, monikers li-lms-2026-06/07) ou à une entrée du registre
docs/UNVERIFIED-FIELDS.md.
"""

from __future__ import annotations

from conftest import ADMIN, ORG_URN, ORG_URN_ENC, H

URL_POSTS = f"/rest/posts?q=author&author={ORG_URN_ENC}"
URL_PARTAGE = (
    "/rest/organizationalEntityShareStatistics"
    f"?q=organizationalEntity&organizationalEntity={ORG_URN_ENC}"
)
URL_ABONNES = (
    "/rest/organizationalEntityFollowerStatistics"
    f"?q=organizationalEntity&organizationalEntity={ORG_URN_ENC}"
)
URL_PAGE = f"/rest/organizationPageStatistics?q=organization&organization={ORG_URN_ENC}"

#: Une fenêtre de 7 jours DANS la fenêtre glissante de 12 mois (juin 2026).
FENETRE = "timeRange:(start:1780617600000,end:1781222400000)"


# ── Authentification ─────────────────────────────────────────────────────────


def test_401_sans_jeton_corps_atteste(client):
    """La SEULE forme d'erreur d'auth attestée mot pour mot : pas de clé
    `code`, et serviceErrorCode vaut 401 — pas un code 65xxx."""
    r = client.get(URL_POSTS)
    assert r.status_code == 401
    assert r.json() == {
        "message": "Empty oauth2_access_token",
        "serviceErrorCode": 401,
        "status": 401,
    }


def test_401_jeton_inconnu(client):
    r = client.get(URL_POSTS, headers={**H, "Authorization": "Bearer nimporte-quoi"})
    assert r.status_code == 401
    assert r.json()["code"] == "INVALID_ACCESS_TOKEN"


def test_401_jeton_expire_et_revoque(client):
    expire = client.get(
        URL_POSTS, headers={**H, "Authorization": "Bearer mock-linkedin-token-expired"}
    )
    assert (expire.status_code, expire.json()["code"]) == (401, "EXPIRED_ACCESS_TOKEN")
    revoque = client.get(
        URL_POSTS, headers={**H, "Authorization": "Bearer mock-linkedin-token-revoked"}
    )
    assert (revoque.status_code, revoque.json()["code"]) == (401, "REVOKED_ACCESS_TOKEN")


def test_schema_basic_traite_comme_jeton_vide(client):
    r = client.get(URL_POSTS, headers={**H, "Authorization": "Basic abc"})
    assert r.status_code == 401
    assert r.json()["message"] == "Empty oauth2_access_token"


# ── Versionnement ────────────────────────────────────────────────────────────


def test_400_version_absente_corps_atteste(client):
    r = client.get(URL_POSTS, headers={"Authorization": H["Authorization"]})
    assert r.status_code == 400
    assert r.json()["code"] == "VERSION_MISSING"
    assert "Linkedin-Version header" in r.json()["message"]


def test_426_version_hors_fenetre(client):
    """En deçà ET au-delà de la fenêtre active : NONEXISTENT_VERSION."""
    for version in ("202301", "202612"):
        r = client.get(URL_POSTS, headers={**H, "Linkedin-Version": version})
        assert r.status_code == 426, version
        assert r.json() == {
            "message": f"Requested version {version} is not active",
            "code": "NONEXISTENT_VERSION",
            "status": 426,
        }


def test_400_version_malformee(client):
    for version in ("foo", "2024-08", "202413", "20240"):
        r = client.get(URL_POSTS, headers={**H, "Linkedin-Version": version})
        assert r.status_code == 400, version
        assert r.json()["code"] == "INVALID_VERSION"


def test_toute_la_fenetre_active_est_acceptee(client):
    """Les deux bornes incluses — et le lookup d'en-tête est insensible à la casse."""
    for version in ("202408", "202506", "202607"):
        r = client.get(URL_POSTS, headers={**H, "Linkedin-Version": version})
        assert r.status_code == 200, version
    r = client.get(URL_POSTS, headers={**H} | {"LINKEDIN-VERSION": "202506"})
    assert r.status_code == 200


# ── Rest.li 2.0 / 1.0 ───────────────────────────────────────────────────────


def test_time_intervals_2_0_brut(client):
    r = client.get(f"{URL_PARTAGE}&timeIntervals=({FENETRE},timeGranularityType:DAY)", headers=H)
    assert r.status_code == 200
    assert len(r.json()["elements"]) == 7


def test_time_intervals_2_0_integralement_encode(client):
    """La doc montre LES DEUX écritures — Starlette ne décode qu'une fois,
    elles doivent converger."""
    encode = (
        "%28timeRange%3A%28start%3A1780617600000%2Cend%3A1781222400000%29"
        "%2CtimeGranularityType%3ADAY%29"
    )
    r = client.get(f"{URL_PARTAGE}&timeIntervals={encode}", headers=H)
    assert r.status_code == 200
    assert len(r.json()["elements"]) == 7


def test_time_intervals_2_0_ordre_des_cles_indifferent(client):
    r = client.get(f"{URL_PARTAGE}&timeIntervals=(timeGranularityType:DAY,{FENETRE})", headers=H)
    assert r.status_code == 200
    assert len(r.json()["elements"]) == 7


def test_time_intervals_1_0_pointe(client):
    """La forme protocole 1.0 — paramètres pointés, sans en-tête 2.0."""
    r = client.get(
        f"{URL_PARTAGE}&timeIntervals.timeRange.start=1780617600000"
        "&timeIntervals.timeRange.end=1781222400000&timeIntervals.timeGranularityType=DAY",
        headers={k: v for k, v in H.items() if k != "X-Restli-Protocol-Version"},
    )
    assert r.status_code == 200
    assert len(r.json()["elements"]) == 7


def test_syntaxe_2_0_sans_en_tete_protocole(client):
    """List()/parenthèses sans X-Restli-Protocol-Version: 2.0.0 → 400
    (garde-fou du mock, comportement réel non attesté — cf. registre)."""
    sans_protocole = {k: v for k, v in H.items() if k != "X-Restli-Protocol-Version"}
    r = client.get(
        f"{URL_PARTAGE}&timeIntervals=({FENETRE},timeGranularityType:DAY)",
        headers=sans_protocole,
    )
    assert r.status_code == 400
    assert "X-Restli-Protocol-Version" in r.json()["message"]


def test_liste_1_0_indexee(client, linkedin_state):
    """`shares[0]=…&shares[1]=…` — la forme tableau du protocole 1.0."""
    posts = [p["id"] for p in linkedin_state.dataset["posts"] if p["id"].startswith("urn:li:share")]
    r = client.get(
        f"{URL_PARTAGE}&shares[0]={posts[0]}&shares[1]={posts[1]}",
        headers={k: v for k, v in H.items() if k != "X-Restli-Protocol-Version"},
    )
    assert r.status_code == 200
    assert len(r.json()["elements"]) == 2


def test_urn_auteur_encode_ou_brut(client):
    for auteur in (ORG_URN_ENC, ORG_URN):
        r = client.get(f"/rest/posts?q=author&author={auteur}", headers=H)
        assert r.status_code == 200, auteur


# ── Finders : q, paramètres, pagination ─────────────────────────────────────


def test_q_manquant_puis_inconnu(client):
    assert client.get("/rest/posts", headers=H).status_code == 400
    r = client.get("/rest/posts?q=paruneautre", headers=H)
    assert r.status_code == 400
    assert "paruneautre" in r.json()["message"]


def test_piege_page_statistics_mauvais_finder(client):
    """LE piège du dialecte : pageStatistics veut `q=organization` — le
    `q=organizationalEntity` des deux autres finders est refusé."""
    r = client.get(
        "/rest/organizationPageStatistics"
        f"?q=organizationalEntity&organizationalEntity={ORG_URN_ENC}",
        headers=H,
    )
    assert r.status_code == 400
    r2 = client.get(URL_PAGE, headers=H)
    assert r2.status_code == 200


def test_auteur_urn_malforme_puis_etranger(client):
    r = client.get("/rest/posts?q=author&author=urn%3Ali%3Aperson%3A123", headers=H)
    assert r.status_code == 400
    assert r.json()["code"] == "INVALID_URN_TYPE"
    r2 = client.get("/rest/posts?q=author&author=urn%3Ali%3Aorganization%3A999", headers=H)
    assert r2.status_code == 403
    assert r2.json()["code"] == "ACCESS_DENIED"


def test_pagination_defauts_et_plafond(client):
    r = client.get(URL_POSTS, headers=H)
    assert r.json()["paging"] == {"start": 0, "count": 10, "links": []}
    assert len(r.json()["elements"]) == 10
    assert client.get(f"{URL_POSTS}&count=100", headers=H).status_code == 200
    assert client.get(f"{URL_POSTS}&count=101", headers=H).status_code == 400
    assert client.get(f"{URL_POSTS}&start=-1", headers=H).status_code == 400
    assert client.get(f"{URL_POSTS}&count=abc", headers=H).status_code == 400


def test_fin_de_donnees_page_courte(client):
    """Le signal de fin est la page COURTE — pas de paging.total sur posts."""
    r = client.get(f"{URL_POSTS}&start=70&count=25", headers=H)
    assert r.status_code == 200
    assert 0 < len(r.json()["elements"]) < 25
    assert "total" not in r.json()["paging"]
    vide = client.get(f"{URL_POSTS}&start=500&count=25", headers=H)
    assert vide.json()["elements"] == []


def test_tri_last_modified_puis_created(client):
    defaut = [
        p["lastModifiedAt"]
        for p in client.get(f"{URL_POSTS}&count=100", headers=H).json()["elements"]
    ]
    assert defaut == sorted(defaut, reverse=True)
    crees = [
        p["createdAt"]
        for p in client.get(f"{URL_POSTS}&count=100&sortBy=CREATED", headers=H).json()["elements"]
    ]
    assert crees == sorted(crees, reverse=True)
    assert client.get(f"{URL_POSTS}&sortBy=NIMPORTE", headers=H).status_code == 400


def test_view_context_accepte_et_ignore(client):
    """Un stub ne doit pas casser un client réel qui envoie viewContext."""
    r = client.get(f"{URL_POSTS}&viewContext=AUTHOR", headers=H)
    assert r.status_code == 200


# ── Lots (batch) ─────────────────────────────────────────────────────────────


def test_batch_posts_results_statuses_errors(client, linkedin_state):
    connu = linkedin_state.dataset["posts"][0]["id"]
    inconnu = "urn:li:share:1111111111111111111"
    ids = f"List({connu.replace(':', '%3A')},{inconnu.replace(':', '%3A')})"
    r = client.get(f"/rest/posts?ids={ids}", headers=H)
    assert r.status_code == 200
    corps = r.json()
    assert connu in corps["results"]
    assert corps["statuses"][inconnu] == 404
    assert corps["errors"][inconnu]["status"] == 404


def test_batch_organisations_statuts_mixtes(client):
    r = client.get("/rest/organizations?ids=List(40123456,27056405)", headers=H)
    corps = r.json()
    assert corps["statuses"] == {"40123456": 200, "27056405": 403}
    assert corps["results"]["40123456"]["localizedName"] == "Boréal Conseil"
    assert corps["errors"]["27056405"]["code"] == "ACCESS_DENIED"


def test_finder_vanity_name_avec_total(client):
    r = client.get("/rest/organizations?q=vanityName&vanityName=boreal-conseil", headers=H)
    assert r.json()["paging"]["total"] == 1
    vide = client.get("/rest/organizations?q=vanityName&vanityName=autre", headers=H)
    assert (vide.json()["paging"]["total"], vide.json()["elements"]) == (0, [])


# ── Statistiques : formes servies ────────────────────────────────────────────


def test_engagement_recalcule_sur_element_servi(client):
    stats = client.get(URL_PARTAGE, headers=H).json()["elements"][0]["totalShareStatistics"]
    attendu = (
        stats["clickCount"] + stats["likeCount"] + stats["commentCount"] + stats["shareCount"]
    ) / stats["impressionCount"]
    assert stats["engagement"] == attendu
    assert stats["impressionCount"] >= stats["uniqueImpressionsCount"]


def test_buckets_quotidiens_sans_uniques(client):
    """L'exemple officiel est incohérent sur ce champ — le mock l'OMET des
    buckets et le garde en vie entière (cf. registre)."""
    r = client.get(f"{URL_PARTAGE}&timeIntervals=({FENETRE},timeGranularityType:DAY)", headers=H)
    for element in r.json()["elements"]:
        assert "uniqueImpressionsCount" not in element["totalShareStatistics"]
        assert "timeRange" in element


def test_combinaison_shares_time_intervals_refusee(client, linkedin_state):
    """« Time-bound statistics is not supported for specific share queries » —
    le mock est STRICT par défaut ; la sonde compare_real tranchera."""
    partage = next(
        p["id"] for p in linkedin_state.dataset["posts"] if p["id"].startswith("urn:li:share")
    )
    r = client.get(
        f"{URL_PARTAGE}&shares=List({partage.replace(':', '%3A')})"
        f"&timeIntervals=({FENETRE},timeGranularityType:DAY)",
        headers=H,
    )
    assert r.status_code == 400
    assert "not supported" in r.json()["message"]


def test_granularite_invalide_et_start_obligatoire(client):
    r = client.get(f"{URL_PARTAGE}&timeIntervals=({FENETRE},timeGranularityType:WEEK)", headers=H)
    assert r.status_code == 400  # WEEK n'existe que sur les followers
    r2 = client.get(f"{URL_ABONNES}&timeIntervals=(timeGranularityType:DAY)", headers=H)
    assert r2.status_code == 400
    assert "start" in r2.json()["message"]


# ── Divers ───────────────────────────────────────────────────────────────────


def test_route_inconnue_enveloppe_linkedin(client):
    r = client.get("/rest/nimporte", headers=H)
    assert r.status_code == 404
    assert r.json()["message"] == "No root resource defined for path '/nimporte'"
    assert r.json()["status"] == 404


def test_network_sizes_edge_type_obligatoire(client):
    sans = client.get(f"/rest/networkSizes/{ORG_URN_ENC}", headers=H)
    assert sans.status_code == 400
    mauvais = client.get(f"/rest/networkSizes/{ORG_URN_ENC}?edgeType=AUTRE", headers=H)
    assert mauvais.status_code == 400


def test_quota_journalier_sans_retry_after(client):
    """LE régime LinkedIn : 429 SANS Retry-After — le client doit attendre
    minuit UTC, pas un délai annoncé (différence clé avec BoondManager)."""
    client.post(
        "/__admin/inject",
        json={"kind": "rate_limit", "scope": "/rest/posts", "after_requests": 2},
        headers=ADMIN,
    )
    assert client.get(URL_POSTS, headers=H).status_code == 200
    assert client.get(URL_POSTS, headers=H).status_code == 200
    refus = client.get(URL_POSTS, headers=H)
    assert refus.status_code == 429
    assert "Retry-After" not in refus.headers
    assert refus.json()["message"].startswith("Resource level throttle limit")
