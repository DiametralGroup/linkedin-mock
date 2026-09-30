"""The dataset: the LinkedIn page of "Boréal Conseil".

SAME fictional company as boondmanager-mock (a 34-person French data & AI
consultancy) — the cross-mock consistency is deliberate: both mocks tell the
story of the same company, one on the ERP side, the other on the
communication side.

What the generator produces — and the endpoints only DERIVE from it:

  posts           72 publications over ~24 months, ~85% `urn:li:share:` and
                  ~15% `urn:li:ugcPost:` (videos/documents), 19-digit
                  identifiers STRICTLY increasing with publication time
                  (plausible, not attested — cf. registry), French ESN-style
                  comments: recruitment, client engagements wrapping up with
                  mentions, events, partnerships, articles, agency life;
  per-post series impressions/clicks/reactions/comments/shares PER UTC DAY,
                  realistic decay (peak at J0-J2, exponential tail, one
                  deterministic "viral" post) — lifetime counters are SUMS of
                  these buckets;
  followers       daily organic gains (spikes on post days, a few NEGATIVE
                  days — net unfollows) + two paid campaign windows; the
                  /networkSizes total equals base + Σ gains;
  page views      daily baseline, spikes after each post, careers/jobs spikes
                  after recruitment posts; the desktop/mobile and
                  overview/careers/jobs/lifeAt splits hold the verified
                  arithmetic of the official example;
  demographics    fixed SHARES per facet (materialized at request time
                  against the current total) — each facet covers LESS than
                  the total, as in reality (members without the attribute
                  absent), and `associationType` only lists the 34 staff.

Determinism: `random.Random(seed)` and a FIXED time anchor. Never
`datetime.now()` — two runs produce the same world byte for byte.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from typing import Any

# ── Time anchor ──────────────────────────────────────────────────────────────

TODAY = date(2026, 7, 15)
#: First day of the series (followers, views) — the page's "creation".
SERIES_START = date(2024, 8, 1)
#: Last day of stats in the base dataset: J-2, the documented availability
#: rule of follower statistics, applied across the whole dataset.
LAST_STAT_DAY = date(2026, 7, 13)
#: Ceiling of the base dataset's `lastModifiedAt` values — an incremental
#: cursor placed after this must return zero posts; evolution events are
#: STRICTLY later (cf. evolution.EPOCH).
LAST_UPDATE_DAY = date(2026, 7, 12)

#: Boréal Conseil's staff — the same headcount as boondmanager-mock.
STAFF_COUNT = 34

FOLLOWERS_BASE = 2612


def _ms(day: date, hour: int = 9, minute: int = 0, second: int = 0) -> int:
    """Epoch milliseconds, UTC — the unit of every LinkedIn timestamp."""
    return int(
        datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=UTC).timestamp() * 1000
    )


# ── Catalogs ─────────────────────────────────────────────────────────────────

#: FICTIONAL client companies mentioned in posts (`@[…](urn)` mentions).
_CLIENTS: tuple[tuple[str, str], ...] = (
    ("Nexalis", "urn:li:organization:71054001"),
    ("Groupe Ardentis", "urn:li:organization:71054002"),
    ("Banque Hesperia", "urn:li:organization:71054003"),
)

_POSTS_RECRUITMENT = (
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

_POSTS_CLIENT_CASE = (
    "Fin de mission chez @[{client}]({urn}) : 18 mois pour refondre la plateforme data, "
    "diviser par trois les temps de traitement et former les équipes. Merci pour la "
    "confiance ! {hashtag|\\#|data}",
    "Retour d'expérience : comment @[{client}]({urn}) a fiabilisé son reporting réglementaire "
    "avec une approche contract-first. L'étude de cas complète est en ligne. "
    "{hashtag|\\#|DataGovernance}",
    "Nouveau projet signé avec @[{client}]({urn}) : mise en place d'un socle analytics "
    "self-service. On a hâte de commencer. {hashtag|\\#|analytics}",
)

_POSTS_EVENT = (
    "Nous serons au Salon Big Data & IA Paris cette semaine — venez parler pipelines, "
    "gouvernance et vraie vie de la data au stand B12. {hashtag|\\#|BigDataParis}",
    "Meetup ce jeudi dans nos locaux : « dbt en production, deux ans après ». Places "
    "limitées, inscription en commentaire. {hashtag|\\#|dbt} {hashtag|\\#|meetup}",
    "Retour en images sur notre atelier DuckDB : 40 participants, trois cas d'usage, zéro "
    "slide marketing. Merci à tous ! {hashtag|\\#|DuckDB}",
    "Devoxx France, jour 1 : notre équipe est sur place. Si vous voulez échanger sur "
    "l'ingénierie data, c'est le moment. {hashtag|\\#|DevoxxFR}",
)

_POSTS_PARTNERSHIP = (
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

_POSTS_AGENCY_LIFE = (
    "Séminaire d'été : deux jours à Étretat pour souffler, célébrer les projets livrés et "
    "préparer la rentrée. {hashtag|\\#|VieDAgence}",
    "Bienvenue aux quatre consultant·e·s qui rejoignent Boréal Conseil ce mois-ci ! "
    "{hashtag|\\#|onboarding}",
    "Portrait d'équipe : Camille, Data Engineer, raconte son quotidien entre ingestion "
    "temps réel et mentorat des juniors. {hashtag|\\#|PortraitDEquipe}",
    "10 ans de Boréal Conseil ! Merci à nos clients, partenaires et surtout à toute "
    "l'équipe. La suite s'annonce belle. {hashtag|\\#|anniversaire}",
)

_POSTS_GREETINGS = (
    "Toute l'équipe de Boréal Conseil vous souhaite une excellente année ! Rétrospective : "
    "14 projets livrés, 6 recrutements, un meetup lancé. {hashtag|\\#|BonneAnnee}",
    "Belle trêve à toutes et à tous — on se retrouve en janvier, reposés et pleins "
    "d'idées. {hashtag|\\#|fetes}",
)

#: (category, templates, weight) — the draw is deterministic via rng.
_CATEGORIES: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("recruitment", _POSTS_RECRUITMENT, 0.20),
    ("client_case", _POSTS_CLIENT_CASE, 0.15),
    ("event", _POSTS_EVENT, 0.15),
    ("partnership", _POSTS_PARTNERSHIP, 0.10),
    ("article", _POSTS_ARTICLE, 0.20),
    ("agency_life", _POSTS_AGENCY_LIFE, 0.15),
    ("greetings", _POSTS_GREETINGS, 0.05),
)

_MEDIA_TITLES = (
    "Atelier data en 3 minutes",
    "Nos consultants sur le terrain",
    "Démo : un pipeline de bout en bout",
    "Rencontre avec l'équipe",
)

#: Demographic facets: (family, entry key, ((segment, weight)…), coverage).
#: Weights sum to 1 per family; coverage < 1 reproduces members without the
#: attribute, absent from the real facets. Semantics of the URN integers are
#: PLAUSIBLE, not attested (cf. docs/UNVERIFIED-FIELDS.md).
DEMOGRAPHICS_SHARES: tuple[tuple[str, str, tuple[tuple[str, float], ...], float], ...] = (
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
            ("urn:li:geo:90009717", 0.52),  # Paris region
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

#: The pageStatistics facets — same segments, `pageStatisticsBy*` families
#: (industry is `industryV2` there, a quirk of the real dialect).
PAGES_SHARES: tuple[tuple[str, str, tuple[tuple[str, float], ...], float], ...] = tuple(
    (
        family.replace("followerCountsBy", "pageStatisticsBy").replace(
            "ByIndustry", "ByIndustryV2"
        ),
        "industryV2" if key == "industry" else key,
        segments,
        round(coverage - 0.04, 2),
    )
    for family, key, segments, coverage in DEMOGRAPHICS_SHARES
    if family != "followerCountsByAssociationType"
)


# ── Posts ────────────────────────────────────────────────────────────────────


def _calendar(rng: random.Random, count: int) -> list[date]:
    """`count` publication days, spread from SERIES_START to LAST_UPDATE_DAY-3.

    Regular spacing + deterministic jitter: the cadence of a page that
    publishes ~3 times a month, never two posts on the same day.
    """
    span = (LAST_UPDATE_DAY - timedelta(days=3) - SERIES_START).days
    days: list[date] = []
    taken: set[date] = set()
    for k in range(count):
        base = SERIES_START + timedelta(days=round(k * span / (count - 1)))
        day = base + timedelta(days=rng.randint(-3, 3))
        day = max(SERIES_START, min(day, LAST_UPDATE_DAY - timedelta(days=3)))
        while day in taken:
            day += timedelta(days=1)
        taken.add(day)
        days.append(day)
    return sorted(days)


def _comment(rng: random.Random, category: str, templates: tuple[str, ...]) -> str:
    text = rng.choice(templates)
    if category == "client_case":
        # NOT str.format(): the little-format templates `{hashtag|\#|…}` are
        # LITERAL braces of the LinkedIn dialect, not placeholders.
        client, urn = rng.choice(_CLIENTS)
        return text.replace("{client}", client).replace("{urn}", urn)
    return text


def _posts(rng: random.Random, org_urn: str) -> list[dict[str, Any]]:
    days = _calendar(rng, 72)
    categories = [c for c, _, _ in _CATEGORIES]
    weights = [p for _, _, p in _CATEGORIES]
    templates = {c: g for c, g, _ in _CATEGORIES}

    posts: list[dict[str, Any]] = []
    identifier = 7_180_000_000_000_000_000
    for index, day in enumerate(days):
        identifier += rng.randint(30, 120) * 10**14
        category = rng.choices(categories, weights=weights, k=1)[0]
        # Greetings only make sense at the end/start of the year.
        if category == "greetings" and day.month not in (1, 12):
            category = "agency_life"
        is_ugc = rng.random() < 0.15
        urn = f"urn:li:ugcPost:{identifier}" if is_ugc else f"urn:li:share:{identifier}"
        published = _ms(day, rng.randint(7, 10), rng.choice((0, 15, 30, 45)))

        post: dict[str, Any] = {
            "id": urn,
            "author": org_urn,
            "commentary": _comment(rng, category, templates[category]),
            "createdAt": published,
            "publishedAt": published,
            "lastModifiedAt": published,
            "lifecycleState": "PUBLISHED",
            "lifecycleStateInfo": {"isEditedByAuthor": False},
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "thirdPartyDistributionChannels": [],
            },
            "isReshareDisabledByAuthor": False,
            # Key INTERNAL to the generator, removed before exposure.
            "_category": category,
        }
        if is_ugc:
            kind = "video" if rng.random() < 0.6 else "document"
            post["content"] = {
                "media": {
                    "id": f"urn:li:{kind}:C56{identifier % 10**10:010d}",
                    "title": rng.choice(_MEDIA_TITLES),
                }
            }
        elif category == "article":
            post["content"] = {
                "article": {
                    "source": f"https://blog.boreal-conseil.example/{day:%Y/%m}/article-{index}",
                    "title": post["commentary"].split(" : ")[-1].split(".")[0][:80],
                    "description": "Le blog data & IA de Boréal Conseil.",
                }
            }
        elif index in (10, 40):
            # The official finder example shows `content: {}` on a text post
            # — the mock serves both variants (emission not attested).
            post["content"] = {}
        posts.append(post)

    # Three reshares: a post relays an older post.
    for index in (24, 47, 63):
        parent = posts[index - rng.randint(4, 10)]["id"]
        posts[index]["reshareContext"] = {"parent": parent, "root": parent}

    # Three posts edited after publication — the material for the
    # lastModifiedAt cursor.
    for index in (18, 39, 61):
        published_ms = int(posts[index]["publishedAt"])
        edited = min(
            published_ms + rng.randint(1, 5) * 86_400_000 + rng.randint(0, 3600) * 1000,
            _ms(LAST_UPDATE_DAY, 18),
        )
        posts[index]["lastModifiedAt"] = edited
        posts[index]["lifecycleStateInfo"] = {"isEditedByAuthor": True}

    return posts


# ── Daily per-post series ────────────────────────────────────────────────────

#: Decay weights for the first 25 days (peak J0-J2, exponential tail).
_DECAY: tuple[float, ...] = (
    0.35,
    0.25,
    0.12,
    *(0.28 * (0.7794**i) * (1 - 0.7794) / (1 - 0.7794**22) for i in range(22)),
)


def _post_series(
    rng: random.Random, publish_day: date, *, viral: bool
) -> dict[str, dict[str, int]]:
    lifetime_total = int(rng.lognormvariate(7.74, 0.9))  # median ≈ 2,300, P95 ≈ 10,000
    if viral:
        lifetime_total *= 15
    click_rate = rng.uniform(0.015, 0.04)
    like_rate = rng.uniform(0.010, 0.030)
    comment_rate = rng.uniform(0.001, 0.006)
    share_rate = rng.uniform(0.001, 0.008)

    series: dict[str, dict[str, int]] = {}
    for offset, weight in enumerate(_DECAY):
        day = publish_day + timedelta(days=offset)
        if day > LAST_STAT_DAY:
            break
        impressions = round(lifetime_total * weight * rng.uniform(0.85, 1.15))
        if impressions <= 0:
            continue
        series[day.isoformat()] = {
            "impressionCount": impressions,
            "clickCount": round(impressions * click_rate),
            "likeCount": round(impressions * like_rate),
            "commentCount": round(impressions * comment_rate),
            "shareCount": round(impressions * share_rate),
        }
    # The tail: a few residual impressions up to J+60.
    for offset in range(len(_DECAY), 60):
        day = publish_day + timedelta(days=offset)
        if day > LAST_STAT_DAY:
            break
        impressions = rng.randint(0, 2)
        if impressions == 0:
            continue
        series[day.isoformat()] = {
            "impressionCount": impressions,
            "clickCount": 1 if rng.random() < 0.1 else 0,
            "likeCount": 0,
            "commentCount": 0,
            "shareCount": 0,
        }
    return series


# ── Followers ────────────────────────────────────────────────────────────────

#: Two two-week paid campaigns — the dataset's only `paid` gains.
_CAMPAIGNS: tuple[tuple[date, date], ...] = (
    (date(2025, 10, 6), date(2025, 10, 19)),
    (date(2026, 2, 2), date(2026, 2, 15)),
)


def _followers_series(rng: random.Random, post_days: set[date]) -> dict[str, dict[str, int]]:
    series: dict[str, dict[str, int]] = {}
    day = SERIES_START
    while day <= LAST_STAT_DAY:
        day_after_post = (day in post_days) or ((day - timedelta(days=1)) in post_days)
        organic = rng.randint(4, 14) if day_after_post else rng.randint(0, 5)
        if rng.random() < 0.04:
            # A NET balance can be negative — unfollows happen.
            organic = -rng.randint(1, 3)
        paid = rng.randint(4, 18) if any(a <= day <= b for a, b in _CAMPAIGNS) else 0
        if organic or paid:
            series[day.isoformat()] = {
                "organicFollowerGain": organic,
                "paidFollowerGain": paid,
            }
        day += timedelta(days=1)
    return series


# ── Page views ───────────────────────────────────────────────────────────────


def _views_series(
    rng: random.Random, post_days: set[date], recruitment_days: set[date]
) -> dict[str, dict[str, Any]]:
    series: dict[str, dict[str, Any]] = {}
    day = SERIES_START
    while day <= LAST_STAT_DAY:
        factor = 1.0
        for lookback in (0, 1, 2):
            if (day - timedelta(days=lookback)) in post_days:
                factor = max(factor, rng.uniform(1.5, 3.0) / (1 + lookback * 0.4))
        overview = round(rng.randint(12, 45) * factor)
        jobs = rng.randint(0, 3)
        life_at = rng.randint(0, 2)
        for lookback in (0, 1, 2, 3):
            if (day - timedelta(days=lookback)) in recruitment_days:
                jobs = rng.randint(8, 25)
                life_at = rng.randint(3, 10)
                break
        series[day.isoformat()] = {
            "overview": overview,
            "jobs": jobs,
            "lifeAt": life_at,
            "part_bureau": round(rng.uniform(0.55, 0.75), 3),
            "part_uniques": round(rng.uniform(0.72, 0.85), 3),
        }
        day += timedelta(days=1)
    return series


# ── Organization ─────────────────────────────────────────────────────────────


def _organization(org_id: str) -> dict[str, Any]:
    """`GET /rest/organizations/{id}` — `id` is a NUMBER, `$URN` is present."""
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


# ── Assembly ─────────────────────────────────────────────────────────────────


def build_realiste_dataset(seed: int = 42, org_id: str = "40123456") -> dict[str, Any]:
    rng = random.Random(seed)
    org_urn = f"urn:li:organization:{org_id}"

    posts = _posts(rng, org_urn)
    viral_index = len(posts) - 10  # a recent post, deterministic

    posts_series: dict[str, dict[str, dict[str, int]]] = {}
    lifetime_uniques: dict[str, int] = {}
    post_days: set[date] = set()
    recruitment_days: set[date] = set()
    for index, post in enumerate(posts):
        publish_day = datetime.fromtimestamp(post["publishedAt"] / 1000, tz=UTC).date()
        post_days.add(publish_day)
        if post["_category"] == "recruitment":
            recruitment_days.add(publish_day)
        series = _post_series(rng, publish_day, viral=index == viral_index)
        posts_series[post["id"]] = series
        lifetime_impressions = sum(b["impressionCount"] for b in series.values())
        lifetime_uniques[post["id"]] = round(lifetime_impressions * rng.uniform(0.62, 0.80))
        del post["_category"]

    return {
        "organization": _organization(org_id),
        "posts": posts,
        "posts_series": posts_series,
        "lifetime_uniques": lifetime_uniques,
        "followers_base": FOLLOWERS_BASE,
        "followers_series": _followers_series(rng, post_days),
        "staff_count": STAFF_COUNT,
        "demographics_shares": DEMOGRAPHICS_SHARES,
        "pages_shares": PAGES_SHARES,
        "views_series": _views_series(rng, post_days, recruitment_days),
        "series_start": SERIES_START,
    }
