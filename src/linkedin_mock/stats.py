"""Statistiques — bucketing UTC, fenêtres glissantes, engagement recalculé.

Les séries INTERNES du jeu de données (impressions/clics/réactions par post et
par jour UTC, gains d'abonnés, vues de page) sont la seule source ; tout ce que
l'API sert en est DÉRIVÉ à la sérialisation :

  • les compteurs vie-entière sont des SOMMES des buckets — l'invariant
    sum(daily) == lifetime tient par construction, pas par chance ;
  • `engagement` est recalculé sur chaque élément servi :
    (clicks + likes + comments + shares) / impressions — formule VÉRIFIÉE
    numériquement sur les trois exemples officiels, 0 quand il n'y a pas
    d'impression ;
  • les buckets quotidiens OMETTENT `uniqueImpressionsCount` (l'exemple
    officiel ne le porte que sur un bucket, sous un nom typographié — émission
    réelle non attestée, cf. registre) ; l'agrégat vie-entière le porte.

Fenêtres reproduites (doc officielle) :

  partage   « rolling 12-month window » — les buckets antérieurs à J-365 sont
            écrêtés ;
  abonnés   données disponibles de J-365 à J-2 (UTC), `timeRange.start`
            obligatoire ;
  page      aucune fenêtre documentée — servie depuis la création de la page.

« Maintenant » est VIRTUEL : époque du jeu de données + temps écoulé depuis le
reset, horloge /__admin/clock comprise. Deux serveurs au même âge servent les
mêmes fenêtres — la propriété qui rend les tests d'extraction rejouables.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from .injection import engine
from .settings import settings

#: Granularités acceptées par endpoint (doc officielle).
GRANULARITES_PARTAGE = ("DAY", "MONTH")
GRANULARITES_ABONNES = ("DAY", "WEEK", "MONTH")
GRANULARITES_PAGE = ("DAY", "MONTH")

_COMPTEURS = ("impressionCount", "clickCount", "likeCount", "commentCount", "shareCount")


# ── Horloge virtuelle ────────────────────────────────────────────────────────


def maintenant_virtuel() -> datetime:
    """EPOQUE + temps écoulé depuis le reset (avances /__admin/clock comprises)."""
    from .evolution import EPOQUE
    from .state import state

    ecoule = engine.now() - state.evolution.demarrage
    return (EPOQUE + timedelta(seconds=ecoule)).astimezone(UTC)


def jour_virtuel() -> date:
    return maintenant_virtuel().date()


def _ms(jour: date) -> int:
    return int(datetime(jour.year, jour.month, jour.day, tzinfo=UTC).timestamp() * 1000)


def _jour_de_ms(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date()


# ── Découpage en buckets ─────────────────────────────────────────────────────


def _debut_bucket(jour: date, granularite: str) -> date:
    if granularite == "DAY":
        return jour
    if granularite == "WEEK":
        # Alignement lundi ISO — non attesté par la doc, cf. registre.
        return jour - timedelta(days=jour.weekday())
    return jour.replace(day=1)


def _bucket_suivant(debut: date, granularite: str) -> date:
    if granularite == "DAY":
        return debut + timedelta(days=1)
    if granularite == "WEEK":
        return debut + timedelta(days=7)
    if debut.month == 12:
        return date(debut.year + 1, 1, 1)
    return date(debut.year, debut.month + 1, 1)


def decouper_buckets(
    start_ms: int,
    end_ms: int,
    granularite: str,
    *,
    jour_min: date,
    jour_fin_ex: date,
) -> list[tuple[date, date]]:
    """Les buckets [début, fin) écrêtés à la fenêtre de l'endpoint.

    `start` demandé est inclusif, `end` exclusif, normalisés au jour UTC — le
    régime documenté des share statistics, appliqué partout (la table de
    paramètres de pageStatistics dit l'inverse, très probablement une coquille
    de doc ; cf. registre).
    """
    debut_demande = max(_jour_de_ms(start_ms), jour_min)
    fin_demandee = min(_jour_de_ms(end_ms - 1) + timedelta(days=1), jour_fin_ex)
    if fin_demandee <= debut_demande:
        return []
    buckets: list[tuple[date, date]] = []
    curseur = _debut_bucket(debut_demande, granularite)
    while curseur < fin_demandee:
        fin_bucket = _bucket_suivant(curseur, granularite)
        buckets.append((curseur, min(fin_bucket, fin_demandee)))
        curseur = fin_bucket
    return buckets


def fenetre_partage() -> tuple[date, date]:
    """[J-365, J) — la fenêtre glissante de 12 mois des share statistics."""
    aujourd_hui = jour_virtuel()
    return aujourd_hui - timedelta(days=365), aujourd_hui + timedelta(days=1)


def fenetre_abonnes() -> tuple[date, date]:
    """[J-365, J-2] inclus, soit une borne exclusive à J-1."""
    aujourd_hui = jour_virtuel()
    return aujourd_hui - timedelta(days=365), aujourd_hui - timedelta(days=1)


def fenetre_page() -> tuple[date, date]:
    """Depuis la création de la page — aucune fenêtre documentée."""
    from .state import state

    return state.dataset["debut_serie"], jour_virtuel() + timedelta(days=1)


def time_range(debut: date, fin_ex: date) -> dict[str, int]:
    return {"start": _ms(debut), "end": _ms(fin_ex)}


# ── Statistiques de partage ──────────────────────────────────────────────────


def _zero() -> dict[str, int]:
    return dict.fromkeys(_COMPTEURS, 0)


def _sommer(series: dict[str, dict[str, int]], debut: date, fin_ex: date) -> dict[str, int]:
    """Somme des compteurs d'UNE série {jour ISO → compteurs} sur [début, fin)."""
    total = _zero()
    jour = debut
    while jour < fin_ex:
        if (bucket := series.get(jour.isoformat())) is not None:
            for compteur in _COMPTEURS:
                total[compteur] += bucket.get(compteur, 0)
        jour += timedelta(days=1)
    return total


def _serie_bornes(series: dict[str, dict[str, int]]) -> tuple[date, date] | None:
    if not series:
        return None
    jours = sorted(series)
    return date.fromisoformat(jours[0]), date.fromisoformat(jours[-1]) + timedelta(days=1)


def _stats_partage(compteurs: dict[str, int], uniques: int | None = None) -> dict[str, Any]:
    """Le bloc `totalShareStatistics`, engagement recalculé à la sérialisation.

    Ordre des clés calqué sur l'exemple officiel vie-entière ; les buckets
    temporels omettent `uniqueImpressionsCount`.
    """
    impressions = compteurs["impressionCount"]
    interactions = (
        compteurs["clickCount"]
        + compteurs["likeCount"]
        + compteurs["commentCount"]
        + compteurs["shareCount"]
    )
    bloc: dict[str, Any] = {}
    if uniques is not None:
        bloc["uniqueImpressionsCount"] = uniques
    bloc["clickCount"] = compteurs["clickCount"]
    bloc["engagement"] = interactions / impressions if impressions else 0
    bloc["likeCount"] = compteurs["likeCount"]
    bloc["commentCount"] = compteurs["commentCount"]
    bloc["shareCount"] = compteurs["shareCount"]
    bloc["impressionCount"] = impressions
    return bloc


def _cle_urn(urn: str) -> str:
    """La clé de l'élément per-share : `share` ou `ugcPost` selon le type d'URN."""
    return "ugcPost" if urn.startswith("urn:li:ugcPost:") else "share"


def element_partage_vie() -> dict[str, Any]:
    """L'agrégat organisation vie-entière — un seul élément."""
    from .state import state

    total = _zero()
    for series in state.dataset["series_posts"].values():
        bornes = _serie_bornes(series)
        if bornes is None:
            continue
        partiel = _sommer(series, *bornes)
        for compteur in _COMPTEURS:
            total[compteur] += partiel[compteur]
    uniques = sum(state.dataset["uniques_vie"].values())
    return {
        "totalShareStatistics": _stats_partage(total, uniques=uniques),
        "organizationalEntity": settings.organization_urn,
    }


def elements_partage_par_post(urns: list[str]) -> list[dict[str, Any]]:
    """Un élément par URN ACTIF — les posts sans activité (ou inconnus, ou
    supprimés) sont OMIS : « can be assumed to have counts of 0 » (doc)."""
    from .state import state

    existants = {p["id"] for p in state.dataset["posts"]}
    elements: list[dict[str, Any]] = []
    for urn in urns:
        series = state.dataset["series_posts"].get(urn)
        if urn not in existants or not series:
            continue
        bornes = _serie_bornes(series)
        if bornes is None:
            continue
        compteurs = _sommer(series, *bornes)
        if all(v == 0 for v in compteurs.values()):
            continue
        elements.append(
            {
                "totalShareStatistics": _stats_partage(
                    compteurs, uniques=state.dataset["uniques_vie"].get(urn, 0)
                ),
                _cle_urn(urn): urn,
                "organizationalEntity": settings.organization_urn,
            }
        )
    return elements


def elements_partage_buckets(
    start_ms: int, end_ms: int, granularite: str, urns: list[str] | None = None
) -> list[dict[str, Any]]:
    """Les buckets temporels — org entière, ou par post (mode non strict)."""
    from .state import state

    jour_min, jour_fin_ex = fenetre_partage()
    buckets = decouper_buckets(
        start_ms, end_ms, granularite, jour_min=jour_min, jour_fin_ex=jour_fin_ex
    )
    elements: list[dict[str, Any]] = []
    if urns is None:
        toutes_series = list(state.dataset["series_posts"].values())
        for debut, fin_ex in buckets:
            total = _zero()
            for series in toutes_series:
                partiel = _sommer(series, debut, fin_ex)
                for compteur in _COMPTEURS:
                    total[compteur] += partiel[compteur]
            elements.append(
                {
                    "timeRange": time_range(debut, fin_ex),
                    "totalShareStatistics": _stats_partage(total),
                    "organizationalEntity": settings.organization_urn,
                }
            )
        return elements
    for urn in urns:
        series = state.dataset["series_posts"].get(urn)
        if not series:
            continue
        for debut, fin_ex in buckets:
            elements.append(
                {
                    "timeRange": time_range(debut, fin_ex),
                    "totalShareStatistics": _stats_partage(_sommer(series, debut, fin_ex)),
                    _cle_urn(urn): urn,
                    "organizationalEntity": settings.organization_urn,
                }
            )
    return elements


# ── Abonnés ──────────────────────────────────────────────────────────────────


def total_abonnes() -> int:
    """`networkSizes.firstDegreeSize` = base + Σ gains (nets, négatifs compris)."""
    from .state import state

    gains = sum(
        g["organicFollowerGain"] + g["paidFollowerGain"]
        for g in state.dataset["serie_abonnes"].values()
    )
    return int(state.dataset["abonnes_base"] + gains)


def _compteurs_facette(organique: int) -> dict[str, int]:
    """Les démographies roulent le payant dans l'organique (note officielle :
    « Do not refer to the paidFollowerCount field »)."""
    return {"organicFollowerCount": organique, "paidFollowerCount": 0}


def _repartir(total: int, poids: list[float]) -> list[int]:
    """Répartition au plus fort reste — les parts somment EXACTEMENT à total."""
    bruts = [total * p for p in poids]
    bases = [int(b) for b in bruts]
    restes = sorted(range(len(bruts)), key=lambda i: bruts[i] - bases[i], reverse=True)
    manque = total - sum(bases)
    for i in restes[:manque]:
        bases[i] += 1
    return bases


def element_abonnes_vie() -> dict[str, Any]:
    """L'élément vie-entière : les 7 familles de facettes démographiques.

    Chaque facette couvre MOINS que le total (les membres sans l'attribut sont
    absents, comme en réel) — sauf `associationType`, qui ne liste que les
    salariés. Le total, lui, vit sur /networkSizes.
    """
    from .state import state

    total = total_abonnes()
    element: dict[str, Any] = {}
    element["followerCountsByAssociationType"] = [
        {
            "followerCounts": _compteurs_facette(state.dataset["nombre_salaries"]),
            "associationType": "EMPLOYEE",
        }
    ]
    for facette, cle, segments, couverture in state.dataset["parts_demographie"]:
        couverts = round(total * couverture)
        parts = _repartir(couverts, [poids for _, poids in segments])
        element[facette] = [
            {"followerCounts": _compteurs_facette(n), cle: valeur}
            for (valeur, _), n in zip(segments, parts, strict=True)
        ]
    element["organizationalEntity"] = settings.organization_urn
    return element


def elements_abonnes_buckets(start_ms: int, end_ms: int, granularite: str) -> list[dict[str, Any]]:
    from .state import state

    jour_min, jour_fin_ex = fenetre_abonnes()
    serie = state.dataset["serie_abonnes"]
    elements: list[dict[str, Any]] = []
    for debut, fin_ex in decouper_buckets(
        start_ms, end_ms, granularite, jour_min=jour_min, jour_fin_ex=jour_fin_ex
    ):
        organique = 0
        paye = 0
        jour = debut
        while jour < fin_ex:
            if (gains := serie.get(jour.isoformat())) is not None:
                organique += gains["organicFollowerGain"]
                paye += gains["paidFollowerGain"]
            jour += timedelta(days=1)
        elements.append(
            {
                "timeRange": time_range(debut, fin_ex),
                "followerGains": {
                    "organicFollowerGain": organique,
                    "paidFollowerGain": paye,
                },
                "organizationalEntity": settings.organization_urn,
            }
        )
    return elements


# ── Vues de page ─────────────────────────────────────────────────────────────


def _vues_jour(rec: dict[str, Any]) -> dict[str, int]:
    """Les 15 compteurs d'un jour, dérivés du stockage compact.

    Arithmétique VÉRIFIÉE sur l'exemple officiel : all = allDesktop + allMobile
    = overview + careers, et careers = jobs + lifeAt.
    """
    accueil, emplois, vie = rec["overview"], rec["jobs"], rec["lifeAt"]
    part = rec["part_bureau"]
    accueil_bureau = round(accueil * part)
    emplois_bureau = round(emplois * part)
    vie_bureau = round(vie * part)
    carrieres = emplois + vie
    carrieres_bureau = emplois_bureau + vie_bureau
    total = accueil + carrieres
    total_bureau = accueil_bureau + carrieres_bureau
    return {
        "allDesktopPageViews": total_bureau,
        "allMobilePageViews": total - total_bureau,
        "allPageViews": total,
        "careersPageViews": carrieres,
        "desktopCareersPageViews": carrieres_bureau,
        "desktopJobsPageViews": emplois_bureau,
        "desktopLifeAtPageViews": vie_bureau,
        "desktopOverviewPageViews": accueil_bureau,
        "jobsPageViews": emplois,
        "lifeAtPageViews": vie,
        "mobileCareersPageViews": carrieres - carrieres_bureau,
        "mobileJobsPageViews": emplois - emplois_bureau,
        "mobileLifeAtPageViews": vie - vie_bureau,
        "mobileOverviewPageViews": accueil - accueil_bureau,
        "overviewPageViews": accueil,
    }


def _vues_cumulees(debut: date, fin_ex: date) -> tuple[dict[str, int], float]:
    """Somme des 15 compteurs sur [début, fin) + part d'uniques moyenne."""
    from .state import state

    serie = state.dataset["serie_vues"]
    total = dict.fromkeys(_vues_jour({"overview": 0, "jobs": 0, "lifeAt": 0, "part_bureau": 0}), 0)
    parts_uniques: list[float] = []
    jour = debut
    while jour < fin_ex:
        if (rec := serie.get(jour.isoformat())) is not None:
            for cle, valeur in _vues_jour(rec).items():
                total[cle] += valeur
            parts_uniques.append(rec["part_uniques"])
        jour += timedelta(days=1)
    return total, (sum(parts_uniques) / len(parts_uniques) if parts_uniques else 0.78)


def element_page_vie() -> dict[str, Any]:
    """L'élément vie-entière : totalPageStatistics complet + 6 facettes."""
    from .state import state

    debut, fin_ex = fenetre_page()
    vues, _ = _vues_cumulees(debut, fin_ex)
    element: dict[str, Any] = {}
    for facette, cle, segments, couverture in state.dataset["parts_pages"]:
        couverts = round(vues["allPageViews"] * couverture)
        parts = _repartir(couverts, [poids for _, poids in segments])
        element[facette] = [
            {"pageStatistics": {"views": {"allPageViews": {"pageViews": n}}}, cle: valeur}
            for (valeur, _), n in zip(segments, parts, strict=True)
        ]
    element["totalPageStatistics"] = {
        "clicks": {"desktopCustomButtonClickCounts": [], "mobileCustomButtonClickCounts": []},
        "views": {cle: {"pageViews": valeur} for cle, valeur in sorted(vues.items())},
    }
    element["organization"] = settings.organization_urn
    return element


def elements_page_buckets(start_ms: int, end_ms: int, granularite: str) -> list[dict[str, Any]]:
    """Buckets temporels — jeu de familles RÉDUIT, avec `uniquePageViews`
    (sous-ensemble exact non attesté, cf. registre)."""
    jour_min, jour_fin_ex = fenetre_page()
    elements: list[dict[str, Any]] = []
    for debut, fin_ex in decouper_buckets(
        start_ms, end_ms, granularite, jour_min=jour_min, jour_fin_ex=jour_fin_ex
    ):
        vues, part_uniques = _vues_cumulees(debut, fin_ex)
        familles = {
            "allPageViews": vues["allPageViews"],
            "overviewPageViews": vues["overviewPageViews"],
            "careersPageViews": vues["careersPageViews"],
            "jobsPageViews": vues["jobsPageViews"],
            "lifeAtPageViews": vues["lifeAtPageViews"],
        }
        elements.append(
            {
                "timeRange": time_range(debut, fin_ex),
                "totalPageStatistics": {
                    "views": {
                        cle: {
                            "pageViews": valeur,
                            "uniquePageViews": round(valeur * part_uniques),
                        }
                        for cle, valeur in familles.items()
                    }
                },
                "organization": settings.organization_urn,
            }
        )
    return elements
