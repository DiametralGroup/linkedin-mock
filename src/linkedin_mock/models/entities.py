"""Les entités du dialecte LinkedIn — formes relevées sur la doc officielle.

Chaque modèle suit les exemples des pages Community Management (Posts API,
share/follower/page statistics, organization lookup, networkSizes), monikers
li-lms-2026-06/07. Les champs plausibles-mais-non-attestés portent le marqueur
`x-linkedin-confidence` (cf. models/common.py).
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .common import Permissif, unverified

# ── Posts ────────────────────────────────────────────────────────────────────


class Distribution(Permissif):
    feedDistribution: str = Field(description="MAIN_FEED | NONE")
    thirdPartyDistributionChannels: list[str] = Field(default_factory=list)


class InfoCycleDeVie(Permissif):
    isEditedByAuthor: bool = False


class ContexteRepartage(Permissif):
    """Présent sur les repartages : le post relayé (`parent`) et l'origine."""

    parent: str
    root: str | None = None


class ContenuMedia(Permissif):
    id: str = Field(description="urn:li:video:… | urn:li:image:… | urn:li:document:…")
    title: str | None = None


class ContenuArticle(Permissif):
    source: str
    title: str | None = None
    description: str | None = None


class ContenuPost(Permissif):
    """Le bloc `content` — `{}` sur certains posts texte, absent sur d'autres."""

    media: ContenuMedia | None = None
    article: ContenuArticle | None = None


class Post(Permissif):
    id: str = Field(description="urn:li:share:{19 chiffres} | urn:li:ugcPost:{19 chiffres}")
    author: str = Field(description="urn:li:organization:{id}")
    commentary: str = Field(
        description=(
            "Texte du post. Hashtags en gabarit little-format "
            "`{hashtag|\\#|WeAreHiring}`, mentions `@[Nom](urn:li:organization:…)`."
        )
    )
    createdAt: int = Field(description="Epoch millisecondes.")
    publishedAt: int = Field(description="Epoch millisecondes.")
    lastModifiedAt: int = Field(description="Epoch millisecondes — bougé par une édition.")
    lifecycleState: str = Field(
        description="PUBLISHED | DRAFT | PUBLISH_REQUESTED | PUBLISH_FAILED"
    )
    lifecycleStateInfo: InfoCycleDeVie
    visibility: str = Field(description="PUBLIC | CONNECTIONS | LOGGED_IN")
    distribution: Distribution
    isReshareDisabledByAuthor: bool = False
    content: ContenuPost | None = Field(
        default=None,
        json_schema_extra=unverified(
            "émis `{}` sur certains éléments du finder officiel et absent sur "
            "d'autres — la règle d'émission n'est pas documentée ; le mock sert "
            "les deux variantes"
        ),
    )
    reshareContext: ContexteRepartage | None = None


class LotPosts(Permissif):
    """Réponse BATCH_GET `?ids=List(...)` — results/statuses/errors par URN."""

    results: dict[str, Post]
    statuses: dict[str, Any] = Field(default_factory=dict)
    errors: dict[str, Any] = Field(default_factory=dict)


# ── Statistiques de partage ──────────────────────────────────────────────────


class PlageTemporelle(Permissif):
    start: int = Field(description="Epoch ms, minuit UTC, inclusif.")
    end: int = Field(description="Epoch ms, minuit UTC, exclusif.")


class StatistiquesPartage(Permissif):
    """`totalShareStatistics` — engagement = (clics + réactions + commentaires
    + partages) / impressions, formule vérifiée numériquement sur les exemples
    officiels."""

    uniqueImpressionsCount: int | None = Field(
        default=None,
        description="Présent sur les agrégats vie-entière ; OMIS des buckets temporels.",
    )
    clickCount: int
    engagement: float
    likeCount: int
    commentCount: int
    shareCount: int
    impressionCount: int


class ElementStatsPartage(Permissif):
    timeRange: PlageTemporelle | None = None
    totalShareStatistics: StatistiquesPartage
    share: str | None = Field(default=None, description="Présent en mode per-share (URN share).")
    ugcPost: str | None = Field(default=None, description="Présent en mode per-ugcPost.")
    organizationalEntity: str


# ── Abonnés ──────────────────────────────────────────────────────────────────


class CompteursAbonnes(Permissif):
    """Les démographies roulent le payant dans l'organique — note officielle :
    « Do not refer to the paidFollowerCount field »."""

    organicFollowerCount: int
    paidFollowerCount: int


class FacetteAbonnes(Permissif):
    followerCounts: CompteursAbonnes
    associationType: str | None = None
    geo: str | None = Field(
        default=None,
        json_schema_extra=unverified(
            "les entiers des URN urn:li:geo:… sont plausibles, pas attestés segment par segment"
        ),
    )
    function: str | None = Field(
        default=None,
        json_schema_extra=unverified("sémantique des entiers urn:li:function:… plausible"),
    )
    industry: str | None = Field(
        default=None,
        json_schema_extra=unverified("sémantique des entiers urn:li:industry:… plausible"),
    )
    seniority: str | None = Field(
        default=None,
        json_schema_extra=unverified("sémantique des entiers urn:li:seniority:… plausible"),
    )
    staffCountRange: str | None = None


class GainsAbonnes(Permissif):
    organicFollowerGain: int = Field(description="Gain NET — peut être négatif.")
    paidFollowerGain: int


class ElementStatsAbonnes(Permissif):
    """Vie entière : les 7 familles de facettes. Time-bound : `followerGains`
    par bucket — les facettes ne sont pas servies en time-bound."""

    timeRange: PlageTemporelle | None = None
    followerGains: GainsAbonnes | None = None
    followerCountsByAssociationType: list[FacetteAbonnes] | None = None
    followerCountsByGeoCountry: list[FacetteAbonnes] | None = None
    followerCountsByFunction: list[FacetteAbonnes] | None = None
    followerCountsByIndustry: list[FacetteAbonnes] | None = None
    followerCountsByGeo: list[FacetteAbonnes] | None = None
    followerCountsBySeniority: list[FacetteAbonnes] | None = None
    followerCountsByStaffCountRange: list[FacetteAbonnes] | None = None
    organizationalEntity: str


# ── Vues de page ─────────────────────────────────────────────────────────────


class VuesPage(Permissif):
    pageViews: int
    uniquePageViews: int | None = Field(
        default=None,
        json_schema_extra=unverified(
            "présent dans les buckets temporels des exemples officiels, mais "
            "le sous-ensemble exact de familles qui le porte est incohérent "
            "d'un exemple à l'autre"
        ),
    )


class ClicsPage(Permissif):
    desktopCustomButtonClickCounts: list[dict[str, Any]] = Field(default_factory=list)
    mobileCustomButtonClickCounts: list[dict[str, Any]] = Field(default_factory=list)


class StatistiquesPage(Permissif):
    """`totalPageStatistics` — vie entière : 15 compteurs de vues, arithmétique
    vérifiée (all = desktop + mobile = overview + careers ; careers = jobs +
    lifeAt). Time-bound : jeu de familles réduit, avec uniquePageViews."""

    clicks: ClicsPage | None = None
    views: dict[str, VuesPage]


class FacettePage(Permissif):
    pageStatistics: dict[str, dict[str, VuesPage]]
    geo: str | None = None
    function: str | None = None
    industryV2: str | None = None
    seniority: str | None = None
    staffCountRange: str | None = None


class ElementStatsPage(Permissif):
    timeRange: PlageTemporelle | None = None
    pageStatisticsByGeoCountry: list[FacettePage] | None = None
    pageStatisticsByFunction: list[FacettePage] | None = None
    pageStatisticsByIndustryV2: list[FacettePage] | None = None
    pageStatisticsByGeo: list[FacettePage] | None = None
    pageStatisticsBySeniority: list[FacettePage] | None = None
    pageStatisticsByStaffCountRange: list[FacettePage] | None = None
    totalPageStatistics: StatistiquesPage
    organization: str


# ── Organisation & réseau ────────────────────────────────────────────────────


class LocaleOrganisation(Permissif):
    country: str
    language: str


class NomLocalise(Permissif):
    localized: dict[str, str]
    preferredLocale: LocaleOrganisation


class Organisation(Permissif):
    """`GET /rest/organizations/{id}` — entité plate Rest.li ; `id` est un
    NOMBRE et `$URN` est présent (relevé officiel)."""

    vanityName: str
    localizedName: str
    name: NomLocalise
    defaultLocale: LocaleOrganisation
    organizationType: str
    staffCountRange: str
    industries: list[str] = Field(default_factory=list)
    specialties: list[str] = Field(default_factory=list)
    localizedSpecialties: list[str] = Field(default_factory=list)
    alternativeNames: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    locations: list[dict[str, Any]] = Field(default_factory=list)
    versionTag: str
    foundedOn: dict[str, int] | None = None
    localizedWebsite: str | None = None
    logoV2: dict[str, Any] | None = None
    coverPhotoV2: dict[str, Any] | None = None
    primaryOrganizationType: str | None = None
    id: int
    urn: str = Field(alias="$URN", serialization_alias="$URN")


class LotOrganisations(Permissif):
    """Réponse batch `?ids=List(...)` — `statuses` porte le code PAR id."""

    results: dict[str, Organisation]
    statuses: dict[str, int] = Field(default_factory=dict)
    errors: dict[str, Any] = Field(default_factory=dict)


class TailleReseau(Permissif):
    """`GET /rest/networkSizes/{urn}?edgeType=COMPANY_FOLLOWED_BY_MEMBER` —
    LE total d'abonnés (les follower statistics n'ont plus de total)."""

    firstDegreeSize: int
