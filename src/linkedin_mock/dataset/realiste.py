"""Le jeu de données : la page LinkedIn de « Boréal Conseil ».

MÊME entreprise fictive que boondmanager-mock (ESN française de 34 personnes,
data & IA) — la cohérence inter-mocks est délibérée : les deux mocks racontent
la même société, l'un côté ERP, l'autre côté communication.

Ce que le générateur produit — et les endpoints ne font que DÉRIVER :

  posts           72 publications sur ~24 mois, ~85 % `urn:li:share:` et ~15 %
                  `urn:li:ugcPost:` (vidéos/documents), identifiants 19
                  chiffres STRICTEMENT croissants avec le temps de publication
                  (plausible, non attesté — cf. registre), commentaires
                  français d'ESN : recrutement, fins de mission client avec
                  mentions, événements, partenariats, articles, vie d'agence ;
  séries par post impressions/clics/réactions/commentaires/partages PAR JOUR
                  UTC, décroissance réaliste (pic à J0-J2, queue
                  exponentielle, un post « viral » déterministe) — les
                  compteurs vie-entière sont des SOMMES de ces buckets ;
  abonnés         gains quotidiens organiques (pics les jours de post,
                  quelques journées NÉGATIVES — désabonnements nets) + deux
                  fenêtres de campagne payante ; le total /networkSizes vaut
                  base + Σ gains ;
  vues de page    baseline quotidienne, pics après chaque post, pics
                  careers/jobs après les posts de recrutement ; les splits
                  desktop/mobile et overview/careers/jobs/lifeAt tiennent
                  l'arithmétique vérifiée de l'exemple officiel ;
  démographies    des PARTS fixes par facette (matérialisées à la requête sur
                  le total courant) — chaque facette couvre MOINS que le
                  total, comme en réel (membres sans l'attribut absents), et
                  `associationType` ne liste que les 34 salariés.

Déterminisme : `random.Random(seed)` et une ancre temporelle FIXE. Jamais
`datetime.now()` — deux exécutions produisent le même monde à l'octet près.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from typing import Any

# ── Ancre temporelle ─────────────────────────────────────────────────────────

AUJOURDHUI = date(2026, 7, 15)
#: Premier jour des séries (abonnés, vues) — la « création » de la page.
DEBUT_SERIE = date(2024, 8, 1)
#: Dernier jour de statistiques du jeu de base : J-2, la règle de
#: disponibilité documentée des follower statistics, appliquée à tout le jeu.
DERNIERE_STAT = date(2026, 7, 13)
#: Plafond des `lastModifiedAt` du jeu de base — un curseur incrémental posé
#: après doit rendre zéro post ; les événements d'évolution sont STRICTEMENT
#: postérieurs (cf. evolution.EPOQUE).
DERNIERE_MAJ = date(2026, 7, 12)

#: Les salariés de Boréal Conseil — le même effectif que boondmanager-mock.
NOMBRE_SALARIES = 34

ABONNES_BASE = 2612


def _ms(jour: date, heure: int = 9, minute: int = 0, seconde: int = 0) -> int:
    """Epoch millisecondes, UTC — l'unité de tous les horodatages LinkedIn."""
    return int(
        datetime(jour.year, jour.month, jour.day, heure, minute, seconde, tzinfo=UTC).timestamp()
        * 1000
    )


# ── Catalogues ───────────────────────────────────────────────────────────────

#: Sociétés clientes FICTIVES mentionnées dans les posts (mentions `@[…](urn)`).
_CLIENTS: tuple[tuple[str, str], ...] = (
    ("Nexalis", "urn:li:organization:71054001"),
    ("Groupe Ardentis", "urn:li:organization:71054002"),
    ("Banque Hesperia", "urn:li:organization:71054003"),
)

_POSTS_RECRUTEMENT = (
    "Boréal Conseil recrute ! Nous cherchons un·e Data Engineer senior pour accompagner nos "
    "clients sur leurs plateformes data. {hashtag|\\#|WeAreHiring} {hashtag|\\#|DataEngineering}",
    "Notre équipe grandit : deux postes de Consultant·e BI ouverts à Paris. Venez construire "
    "des tableaux de bord qui servent vraiment les métiers. {hashtag|\\#|recrutement} "
    "{hashtag|\\#|PowerBI}",
    "Envie de rejoindre une ESN à taille humaine ? Nous ouvrons un poste de MLOps Engineer. "
    "{hashtag|\\#|WeAreHiring} {hashtag|\\#|MLOps}",
    "Stage de fin d'études : industrialisation d'un pipeline dbt chez un grand compte. "
    "Encadrement rapproché, vrai sujet, vraie prod. {hashtag|\\#|stage} {hashtag|\\#|dbt}",
)

_POSTS_CAS_CLIENT = (
    "Fin de mission chez @[{client}]({urn}) : 18 mois pour refondre la plateforme data, "
    "diviser par trois les temps de traitement et former les équipes. Merci pour la "
    "confiance ! {hashtag|\\#|data}",
    "Retour d'expérience : comment @[{client}]({urn}) a fiabilisé son reporting réglementaire "
    "avec une approche contract-first. L'étude de cas complète est en ligne. "
    "{hashtag|\\#|DataGovernance}",
    "Nouveau projet signé avec @[{client}]({urn}) : mise en place d'un socle analytics "
    "self-service. On a hâte de commencer. {hashtag|\\#|analytics}",
)

_POSTS_EVENEMENT = (
    "Nous serons au Salon Big Data & IA Paris cette semaine — venez parler pipelines, "
    "gouvernance et vraie vie de la data au stand B12. {hashtag|\\#|BigDataParis}",
    "Meetup ce jeudi dans nos locaux : « dbt en production, deux ans après ». Places "
    "limitées, inscription en commentaire. {hashtag|\\#|dbt} {hashtag|\\#|meetup}",
    "Retour en images sur notre atelier DuckDB : 40 participants, trois cas d'usage, zéro "
    "slide marketing. Merci à tous ! {hashtag|\\#|DuckDB}",
    "Devoxx France, jour 1 : notre équipe est sur place. Si vous voulez échanger sur "
    "l'ingénierie data, c'est le moment. {hashtag|\\#|DevoxxFR}",
)

_POSTS_PARTENARIAT = (
    "Boréal Conseil est désormais partenaire dbt Labs. Une certification de plus au service "
    "de nos clients. {hashtag|\\#|dbt} {hashtag|\\#|partenariat}",
    "Notre équipe compte trois nouveaux certifiés Databricks Data Engineer Professional. "
    "Bravo à eux ! {hashtag|\\#|Databricks}",
    "Nous rejoignons le programme partenaires Microsoft Fabric — nos premiers retours "
    "terrain arrivent bientôt sur le blog. {hashtag|\\#|MicrosoftFabric}",
)

_POSTS_ARTICLE = (
    "Nouvel article sur le blog : « Pourquoi vos tests dbt ne testent rien » — ou comment "
    "passer de tests décoratifs à de vrais contrats de données.",
    "Sur le blog cette semaine : retour d'expérience sur l'extraction incrémentale sans "
    "curseur fiable. Spoiler : la pagination ment.",
    "Article : « RLS PostgreSQL en production, ce qu'on aurait aimé savoir avant ». Nos "
    "erreurs, pour que vous n'ayez pas à les refaire.",
    "Le guide Boréal de l'observabilité des pipelines data est en ligne — 12 pages, zéro "
    "buzzword, que du vécu.",
)

_POSTS_VIE_AGENCE = (
    "Séminaire d'été : deux jours à Étretat pour souffler, célébrer les projets livrés et "
    "préparer la rentrée. {hashtag|\\#|VieDAgence}",
    "Bienvenue aux quatre consultant·e·s qui rejoignent Boréal Conseil ce mois-ci ! "
    "{hashtag|\\#|onboarding}",
    "Portrait d'équipe : Camille, Data Engineer, raconte son quotidien entre ingestion "
    "temps réel et mentorat des juniors. {hashtag|\\#|PortraitDEquipe}",
    "10 ans de Boréal Conseil ! Merci à nos clients, partenaires et surtout à toute "
    "l'équipe. La suite s'annonce belle. {hashtag|\\#|anniversaire}",
)

_POSTS_VOEUX = (
    "Toute l'équipe de Boréal Conseil vous souhaite une excellente année ! Rétrospective : "
    "14 projets livrés, 6 recrutements, un meetup lancé. {hashtag|\\#|BonneAnnee}",
    "Belle trêve à toutes et à tous — on se retrouve en janvier, reposés et pleins "
    "d'idées. {hashtag|\\#|fetes}",
)

#: (catégorie, gabarits, poids) — le tirage est déterministe via rng.
_CATEGORIES: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("recrutement", _POSTS_RECRUTEMENT, 0.20),
    ("cas_client", _POSTS_CAS_CLIENT, 0.15),
    ("evenement", _POSTS_EVENEMENT, 0.15),
    ("partenariat", _POSTS_PARTENARIAT, 0.10),
    ("article", _POSTS_ARTICLE, 0.20),
    ("vie_agence", _POSTS_VIE_AGENCE, 0.15),
    ("voeux", _POSTS_VOEUX, 0.05),
)

_TITRES_MEDIA = (
    "Atelier data en 3 minutes",
    "Nos consultants sur le terrain",
    "Démo : un pipeline de bout en bout",
    "Rencontre avec l'équipe",
)

#: Facettes démographiques : (famille, clé d'entrée, ((segment, poids)…), couverture).
#: Les poids somment à 1 par famille ; la couverture < 1 reproduit les membres
#: sans l'attribut, absents des facettes réelles. Sémantique des entiers d'URN
#: PLAUSIBLE, non attestée (cf. docs/UNVERIFIED-FIELDS.md).
PARTS_DEMOGRAPHIE: tuple[tuple[str, str, tuple[tuple[str, float], ...], float], ...] = (
    (
        "followerCountsByGeoCountry",
        "geo",
        (
            ("urn:li:geo:105015875", 0.66),  # France
            ("urn:li:geo:100565514", 0.09),
            ("urn:li:geo:106693272", 0.07),
            ("urn:li:geo:101282230", 0.05),
            ("urn:li:geo:102787409", 0.13),
        ),
        0.94,
    ),
    (
        "followerCountsByFunction",
        "function",
        (
            ("urn:li:function:8", 0.34),
            ("urn:li:function:13", 0.22),
            ("urn:li:function:25", 0.12),
            ("urn:li:function:4", 0.09),
            ("urn:li:function:15", 0.08),
            ("urn:li:function:1", 0.15),
        ),
        0.90,
    ),
    (
        "followerCountsByIndustry",
        "industry",
        (
            ("urn:li:industry:96", 0.38),
            ("urn:li:industry:4", 0.18),
            ("urn:li:industry:43", 0.12),
            ("urn:li:industry:6", 0.11),
            ("urn:li:industry:14", 0.21),
        ),
        0.88,
    ),
    (
        "followerCountsByGeo",
        "geo",
        (
            ("urn:li:geo:90009717", 0.52),  # région parisienne
            ("urn:li:geo:90009716", 0.11),
            ("urn:li:geo:90009710", 0.09),
            ("urn:li:geo:90009734", 0.28),
        ),
        0.86,
    ),
    (
        "followerCountsBySeniority",
        "seniority",
        (
            ("urn:li:seniority:3", 0.35),
            ("urn:li:seniority:2", 0.22),
            ("urn:li:seniority:4", 0.18),
            ("urn:li:seniority:5", 0.13),
            ("urn:li:seniority:6", 0.12),
        ),
        0.91,
    ),
    (
        "followerCountsByStaffCountRange",
        "staffCountRange",
        (
            ("SIZE_1", 0.06),
            ("SIZE_2_TO_10", 0.13),
            ("SIZE_11_TO_50", 0.22),
            ("SIZE_51_TO_200", 0.19),
            ("SIZE_201_TO_500", 0.12),
            ("SIZE_1001_TO_5000", 0.15),
            ("SIZE_10001_OR_MORE", 0.13),
        ),
        0.89,
    ),
)

#: Les facettes de pageStatistics — mêmes segments, familles `pageStatisticsBy*`
#: (l'industrie y est `industryV2`, particularité du dialecte réel).
PARTS_PAGES: tuple[tuple[str, str, tuple[tuple[str, float], ...], float], ...] = tuple(
    (
        famille.replace("followerCountsBy", "pageStatisticsBy").replace(
            "ByIndustry", "ByIndustryV2"
        ),
        "industryV2" if cle == "industry" else cle,
        segments,
        round(couverture - 0.04, 2),
    )
    for famille, cle, segments, couverture in PARTS_DEMOGRAPHIE
    if famille != "followerCountsByAssociationType"
)


# ── Posts ────────────────────────────────────────────────────────────────────


def _calendrier(rng: random.Random, nombre: int) -> list[date]:
    """`nombre` jours de publication, étalés de DEBUT_SERIE à DERNIERE_MAJ-3.

    Espacement régulier + gigue déterministe : la cadence d'une page qui
    publie ~3 fois par mois, sans deux posts le même jour.
    """
    portee = (DERNIERE_MAJ - timedelta(days=3) - DEBUT_SERIE).days
    jours: list[date] = []
    occupe: set[date] = set()
    for k in range(nombre):
        base = DEBUT_SERIE + timedelta(days=round(k * portee / (nombre - 1)))
        jour = base + timedelta(days=rng.randint(-3, 3))
        jour = max(DEBUT_SERIE, min(jour, DERNIERE_MAJ - timedelta(days=3)))
        while jour in occupe:
            jour += timedelta(days=1)
        occupe.add(jour)
        jours.append(jour)
    return sorted(jours)


def _commentaire(rng: random.Random, categorie: str, gabarits: tuple[str, ...]) -> str:
    texte = rng.choice(gabarits)
    if categorie == "cas_client":
        # PAS str.format() : les gabarits little-format `{hashtag|\#|…}` sont
        # des accolades LITTÉRALES du dialecte LinkedIn, pas des placeholders.
        client, urn = rng.choice(_CLIENTS)
        return texte.replace("{client}", client).replace("{urn}", urn)
    return texte


def _posts(rng: random.Random, org_urn: str) -> list[dict[str, Any]]:
    jours = _calendrier(rng, 72)
    categories = [c for c, _, _ in _CATEGORIES]
    poids = [p for _, _, p in _CATEGORIES]
    gabarits = {c: g for c, g, _ in _CATEGORIES}

    posts: list[dict[str, Any]] = []
    identifiant = 7_180_000_000_000_000_000
    for index, jour in enumerate(jours):
        identifiant += rng.randint(30, 120) * 10**14
        categorie = rng.choices(categories, weights=poids, k=1)[0]
        # Les vœux n'ont de sens qu'en fin/début d'année.
        if categorie == "voeux" and jour.month not in (1, 12):
            categorie = "vie_agence"
        est_ugc = rng.random() < 0.15
        urn = f"urn:li:ugcPost:{identifiant}" if est_ugc else f"urn:li:share:{identifiant}"
        publie = _ms(jour, rng.randint(7, 10), rng.choice((0, 15, 30, 45)))

        post: dict[str, Any] = {
            "id": urn,
            "author": org_urn,
            "commentary": _commentaire(rng, categorie, gabarits[categorie]),
            "createdAt": publie,
            "publishedAt": publie,
            "lastModifiedAt": publie,
            "lifecycleState": "PUBLISHED",
            "lifecycleStateInfo": {"isEditedByAuthor": False},
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "thirdPartyDistributionChannels": [],
            },
            "isReshareDisabledByAuthor": False,
            # Clé INTERNE au générateur, retirée avant exposition.
            "_categorie": categorie,
        }
        if est_ugc:
            genre = "video" if rng.random() < 0.6 else "document"
            post["content"] = {
                "media": {
                    "id": f"urn:li:{genre}:C56{identifiant % 10**10:010d}",
                    "title": rng.choice(_TITRES_MEDIA),
                }
            }
        elif categorie == "article":
            post["content"] = {
                "article": {
                    "source": f"https://blog.boreal-conseil.example/{jour:%Y/%m}/article-{index}",
                    "title": post["commentary"].split(" : ")[-1].split(".")[0][:80],
                    "description": "Le blog data & IA de Boréal Conseil.",
                }
            }
        elif index in (10, 40):
            # L'exemple officiel du finder montre `content: {}` sur un post
            # texte — le mock sert les deux variantes (émission non attestée).
            post["content"] = {}
        posts.append(post)

    # Trois repartages : un post relaie un post plus ancien.
    for index in (24, 47, 63):
        parent = posts[index - rng.randint(4, 10)]["id"]
        posts[index]["reshareContext"] = {"parent": parent, "root": parent}

    # Trois posts édités après publication — la matière du curseur lastModifiedAt.
    for index in (18, 39, 61):
        publie_ms = int(posts[index]["publishedAt"])
        edite = min(
            publie_ms + rng.randint(1, 5) * 86_400_000 + rng.randint(0, 3600) * 1000,
            _ms(DERNIERE_MAJ, 18),
        )
        posts[index]["lastModifiedAt"] = edite
        posts[index]["lifecycleStateInfo"] = {"isEditedByAuthor": True}

    return posts


# ── Séries quotidiennes par post ─────────────────────────────────────────────

#: Poids de décroissance des 25 premiers jours (pic J0-J2, queue exponentielle).
_DECROISSANCE: tuple[float, ...] = (
    0.35,
    0.25,
    0.12,
    *(0.28 * (0.7794**i) * (1 - 0.7794) / (1 - 0.7794**22) for i in range(22)),
)


def _serie_post(
    rng: random.Random, jour_publication: date, *, viral: bool
) -> dict[str, dict[str, int]]:
    vie = int(rng.lognormvariate(7.74, 0.9))  # médiane ≈ 2 300, P95 ≈ 10 000
    if viral:
        vie *= 15
    taux_clic = rng.uniform(0.015, 0.04)
    taux_like = rng.uniform(0.010, 0.030)
    taux_commentaire = rng.uniform(0.001, 0.006)
    taux_partage = rng.uniform(0.001, 0.008)

    serie: dict[str, dict[str, int]] = {}
    for decalage, poids in enumerate(_DECROISSANCE):
        jour = jour_publication + timedelta(days=decalage)
        if jour > DERNIERE_STAT:
            break
        impressions = round(vie * poids * rng.uniform(0.85, 1.15))
        if impressions <= 0:
            continue
        serie[jour.isoformat()] = {
            "impressionCount": impressions,
            "clickCount": round(impressions * taux_clic),
            "likeCount": round(impressions * taux_like),
            "commentCount": round(impressions * taux_commentaire),
            "shareCount": round(impressions * taux_partage),
        }
    # La traîne : quelques impressions résiduelles jusqu'à J+60.
    for decalage in range(len(_DECROISSANCE), 60):
        jour = jour_publication + timedelta(days=decalage)
        if jour > DERNIERE_STAT:
            break
        impressions = rng.randint(0, 2)
        if impressions == 0:
            continue
        serie[jour.isoformat()] = {
            "impressionCount": impressions,
            "clickCount": 1 if rng.random() < 0.1 else 0,
            "likeCount": 0,
            "commentCount": 0,
            "shareCount": 0,
        }
    return serie


# ── Abonnés ──────────────────────────────────────────────────────────────────

#: Deux campagnes payantes de deux semaines — les seuls gains `paid` du jeu.
_CAMPAGNES: tuple[tuple[date, date], ...] = (
    (date(2025, 10, 6), date(2025, 10, 19)),
    (date(2026, 2, 2), date(2026, 2, 15)),
)


def _serie_abonnes(rng: random.Random, jours_posts: set[date]) -> dict[str, dict[str, int]]:
    serie: dict[str, dict[str, int]] = {}
    jour = DEBUT_SERIE
    while jour <= DERNIERE_STAT:
        lendemain_de_post = (jour in jours_posts) or ((jour - timedelta(days=1)) in jours_posts)
        organique = rng.randint(4, 14) if lendemain_de_post else rng.randint(0, 5)
        if rng.random() < 0.04:
            # Un solde NET peut être négatif — des désabonnements, ça existe.
            organique = -rng.randint(1, 3)
        paye = rng.randint(4, 18) if any(a <= jour <= b for a, b in _CAMPAGNES) else 0
        if organique or paye:
            serie[jour.isoformat()] = {
                "organicFollowerGain": organique,
                "paidFollowerGain": paye,
            }
        jour += timedelta(days=1)
    return serie


# ── Vues de page ─────────────────────────────────────────────────────────────


def _serie_vues(
    rng: random.Random, jours_posts: set[date], jours_recrutement: set[date]
) -> dict[str, dict[str, Any]]:
    serie: dict[str, dict[str, Any]] = {}
    jour = DEBUT_SERIE
    while jour <= DERNIERE_STAT:
        facteur = 1.0
        for recul in (0, 1, 2):
            if (jour - timedelta(days=recul)) in jours_posts:
                facteur = max(facteur, rng.uniform(1.5, 3.0) / (1 + recul * 0.4))
        accueil = round(rng.randint(12, 45) * facteur)
        emplois = rng.randint(0, 3)
        vie = rng.randint(0, 2)
        for recul in (0, 1, 2, 3):
            if (jour - timedelta(days=recul)) in jours_recrutement:
                emplois = rng.randint(8, 25)
                vie = rng.randint(3, 10)
                break
        serie[jour.isoformat()] = {
            "overview": accueil,
            "jobs": emplois,
            "lifeAt": vie,
            "part_bureau": round(rng.uniform(0.55, 0.75), 3),
            "part_uniques": round(rng.uniform(0.72, 0.85), 3),
        }
        jour += timedelta(days=1)
    return serie


# ── Organisation ─────────────────────────────────────────────────────────────


def _organisation(org_id: str) -> dict[str, Any]:
    """`GET /rest/organizations/{id}` — `id` est un NOMBRE, `$URN` est présent."""
    return {
        "vanityName": "boreal-conseil",
        "localizedName": "Boréal Conseil",
        "name": {
            "localized": {"fr_FR": "Boréal Conseil"},
            "preferredLocale": {"country": "FR", "language": "fr"},
        },
        "defaultLocale": {"country": "FR", "language": "fr"},
        "organizationType": "PRIVATELY_HELD",
        "staffCountRange": "SIZE_11_TO_50",
        "industries": ["urn:li:industry:96"],
        "specialties": [],
        "localizedSpecialties": [],
        "alternativeNames": [],
        "groups": [],
        "locations": [
            {
                "locationType": "HEADQUARTERS",
                "address": {
                    "country": "FR",
                    "city": "Paris",
                    "postalCode": "75009",
                    "line1": "14 rue des Martyrs",
                },
            }
        ],
        "versionTag": "2140766938",
        "foundedOn": {"year": 2016},
        "localizedWebsite": "https://www.boreal-conseil.example",
        "logoV2": {
            "original": "urn:li:digitalmediaAsset:C4E0BAQBoreal0000001",
            "cropped": "urn:li:digitalmediaAsset:C4E0BAQBoreal0000001",
            "cropInfo": {"x": 0, "y": 0, "width": 400, "height": 400},
        },
        "coverPhotoV2": {
            "original": "urn:li:digitalmediaAsset:C4E1BAQBoreal0000002",
            "cropped": "urn:li:digitalmediaAsset:C4E1BAQBoreal0000002",
            "cropInfo": {"x": 0, "y": 87, "width": 1128, "height": 191},
        },
        "primaryOrganizationType": "NONE",
        "id": int(org_id),
        "$URN": f"urn:li:organization:{org_id}",
    }


# ── Assemblage ───────────────────────────────────────────────────────────────


def build_realiste_dataset(seed: int = 42, org_id: str = "40123456") -> dict[str, Any]:
    rng = random.Random(seed)
    org_urn = f"urn:li:organization:{org_id}"

    posts = _posts(rng, org_urn)
    index_viral = len(posts) - 10  # un post récent, déterministe

    series_posts: dict[str, dict[str, dict[str, int]]] = {}
    uniques_vie: dict[str, int] = {}
    jours_posts: set[date] = set()
    jours_recrutement: set[date] = set()
    for index, post in enumerate(posts):
        jour_publication = datetime.fromtimestamp(post["publishedAt"] / 1000, tz=UTC).date()
        jours_posts.add(jour_publication)
        if post["_categorie"] == "recrutement":
            jours_recrutement.add(jour_publication)
        serie = _serie_post(rng, jour_publication, viral=index == index_viral)
        series_posts[post["id"]] = serie
        impressions_vie = sum(b["impressionCount"] for b in serie.values())
        uniques_vie[post["id"]] = round(impressions_vie * rng.uniform(0.62, 0.80))
        del post["_categorie"]

    return {
        "organisation": _organisation(org_id),
        "posts": posts,
        "series_posts": series_posts,
        "uniques_vie": uniques_vie,
        "abonnes_base": ABONNES_BASE,
        "serie_abonnes": _serie_abonnes(rng, jours_posts),
        "nombre_salaries": NOMBRE_SALARIES,
        "parts_demographie": PARTS_DEMOGRAPHIE,
        "parts_pages": PARTS_PAGES,
        "serie_vues": _serie_vues(rng, jours_posts, jours_recrutement),
        "debut_serie": DEBUT_SERIE,
    }
