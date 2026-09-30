"""The entities of the LinkedIn dialect — shapes recorded from the official doc.

Every model follows the examples from the Community Management pages (Posts
API, share/follower/page statistics, organization lookup, networkSizes),
monikers li-lms-2026-06/07. Plausible-but-unattested fields carry the
`x-linkedin-confidence` marker (cf. models/common.py).
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .common import Permissive, unverified

# ── Posts ────────────────────────────────────────────────────────────────────


class Distribution(Permissive):
    feedDistribution: str = Field(description="MAIN_FEED | NONE")
    thirdPartyDistributionChannels: list[str] = Field(default_factory=list)


class LifecycleInfo(Permissive):
    isEditedByAuthor: bool = False


class ReshareContext(Permissive):
    """Present on reshares: the relayed post (`parent`) and its origin."""

    parent: str
    root: str | None = None


class MediaContent(Permissive):
    id: str = Field(description="urn:li:video:… | urn:li:image:… | urn:li:document:…")
    title: str | None = None


class ArticleContent(Permissive):
    source: str
    title: str | None = None
    description: str | None = None


class PostContent(Permissive):
    """The `content` block — `{}` on some text posts, absent on others."""

    media: MediaContent | None = None
    article: ArticleContent | None = None


class Post(Permissive):
    id: str = Field(description="urn:li:share:{19 digits} | urn:li:ugcPost:{19 digits}")
    author: str = Field(description="urn:li:organization:{id}")
    commentary: str = Field(
        description=(
            "Post text. Hashtags as little-format template "
            "`{hashtag|\\#|WeAreHiring}`, mentions `@[Name](urn:li:organization:…)`."
        )
    )
    createdAt: int = Field(description="Epoch milliseconds.")
    publishedAt: int = Field(description="Epoch milliseconds.")
    lastModifiedAt: int = Field(description="Epoch milliseconds — moved by an edit.")
    lifecycleState: str = Field(
        description="PUBLISHED | DRAFT | PUBLISH_REQUESTED | PUBLISH_FAILED"
    )
    lifecycleStateInfo: LifecycleInfo
    visibility: str = Field(description="PUBLIC | CONNECTIONS | LOGGED_IN")
    distribution: Distribution
    isReshareDisabledByAuthor: bool = False
    content: PostContent | None = Field(
        default=None,
        json_schema_extra=unverified(
            "emitted as `{}` on some elements of the official finder and "
            "absent on others — the emission rule isn't documented; the mock "
            "serves both variants"
        ),
    )
    reshareContext: ReshareContext | None = None


class PostsBatch(Permissive):
    """BATCH_GET `?ids=List(...)` response — results/statuses/errors per URN."""

    results: dict[str, Post]
    statuses: dict[str, Any] = Field(default_factory=dict)
    errors: dict[str, Any] = Field(default_factory=dict)


# ── Share statistics ─────────────────────────────────────────────────────────


class TimeRange(Permissive):
    start: int = Field(description="Epoch ms, midnight UTC, inclusive.")
    end: int = Field(description="Epoch ms, midnight UTC, exclusive.")


class ShareStatistics(Permissive):
    """`totalShareStatistics` — engagement = (clicks + reactions + comments
    + shares) / impressions, formula verified numerically against the
    official examples."""

    uniqueImpressionsCount: int | None = Field(
        default=None,
        description="Present on lifetime aggregates; OMITTED from time buckets.",
    )
    clickCount: int
    engagement: float
    likeCount: int
    commentCount: int
    shareCount: int
    impressionCount: int


class ShareStatsElement(Permissive):
    timeRange: TimeRange | None = None
    totalShareStatistics: ShareStatistics
    share: str | None = Field(default=None, description="Present in per-share mode (share URN).")
    ugcPost: str | None = Field(default=None, description="Present in per-ugcPost mode.")
    organizationalEntity: str


# ── Followers ────────────────────────────────────────────────────────────────


class FollowerCounts(Permissive):
    """Demographics roll the paid count into the organic one — official note:
    "Do not refer to the paidFollowerCount field"."""

    organicFollowerCount: int
    paidFollowerCount: int


class FollowerFacet(Permissive):
    followerCounts: FollowerCounts
    associationType: str | None = None
    geo: str | None = Field(
        default=None,
        json_schema_extra=unverified(
            "the urn:li:geo:… integers are plausible, not attested segment by segment"
        ),
    )
    function: str | None = Field(
        default=None,
        json_schema_extra=unverified("semantics of the urn:li:function:… integers are plausible"),
    )
    industry: str | None = Field(
        default=None,
        json_schema_extra=unverified("semantics of the urn:li:industry:… integers are plausible"),
    )
    seniority: str | None = Field(
        default=None,
        json_schema_extra=unverified("semantics of the urn:li:seniority:… integers are plausible"),
    )
    staffCountRange: str | None = None


class FollowerGains(Permissive):
    organicFollowerGain: int = Field(description="NET gain — can be negative.")
    paidFollowerGain: int


class FollowerStatsElement(Permissive):
    """Lifetime: the 7 facet families. Time-bound: `followerGains` per
    bucket — facets aren't served in time-bound mode."""

    timeRange: TimeRange | None = None
    followerGains: FollowerGains | None = None
    followerCountsByAssociationType: list[FollowerFacet] | None = None
    followerCountsByGeoCountry: list[FollowerFacet] | None = None
    followerCountsByFunction: list[FollowerFacet] | None = None
    followerCountsByIndustry: list[FollowerFacet] | None = None
    followerCountsByGeo: list[FollowerFacet] | None = None
    followerCountsBySeniority: list[FollowerFacet] | None = None
    followerCountsByStaffCountRange: list[FollowerFacet] | None = None
    organizationalEntity: str


# ── Page views ───────────────────────────────────────────────────────────────


class PageViews(Permissive):
    pageViews: int
    uniquePageViews: int | None = Field(
        default=None,
        json_schema_extra=unverified(
            "present in the time buckets of the official examples, but the "
            "exact subset of families that carries it is inconsistent from "
            "one example to another"
        ),
    )


class PageClicks(Permissive):
    desktopCustomButtonClickCounts: list[dict[str, Any]] = Field(default_factory=list)
    mobileCustomButtonClickCounts: list[dict[str, Any]] = Field(default_factory=list)


class PageStatistics(Permissive):
    """`totalPageStatistics` — lifetime: 15 view counters, verified
    arithmetic (all = desktop + mobile = overview + careers; careers = jobs +
    lifeAt). Time-bound: reduced set of families, with uniquePageViews."""

    clicks: PageClicks | None = None
    views: dict[str, PageViews]


class PageFacet(Permissive):
    pageStatistics: dict[str, dict[str, PageViews]]
    geo: str | None = None
    function: str | None = None
    industryV2: str | None = None
    seniority: str | None = None
    staffCountRange: str | None = None


class PageStatsElement(Permissive):
    timeRange: TimeRange | None = None
    pageStatisticsByGeoCountry: list[PageFacet] | None = None
    pageStatisticsByFunction: list[PageFacet] | None = None
    pageStatisticsByIndustryV2: list[PageFacet] | None = None
    pageStatisticsByGeo: list[PageFacet] | None = None
    pageStatisticsBySeniority: list[PageFacet] | None = None
    pageStatisticsByStaffCountRange: list[PageFacet] | None = None
    totalPageStatistics: PageStatistics
    organization: str


# ── Organization & network ───────────────────────────────────────────────────


class OrganizationLocale(Permissive):
    country: str
    language: str


class LocalizedName(Permissive):
    localized: dict[str, str]
    preferredLocale: OrganizationLocale


class Organization(Permissive):
    """`GET /rest/organizations/{id}` — flat Rest.li entity; `id` is a
    NUMBER and `$URN` is present (recorded from the official trace)."""

    vanityName: str
    localizedName: str
    name: LocalizedName
    defaultLocale: OrganizationLocale
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


class OrganizationsBatch(Permissive):
    """Batch response `?ids=List(...)` — `statuses` carries the code PER id."""

    results: dict[str, Organization]
    statuses: dict[str, int] = Field(default_factory=dict)
    errors: dict[str, Any] = Field(default_factory=dict)


class NetworkSize(Permissive):
    """`GET /rest/networkSizes/{urn}?edgeType=COMPANY_FOLLOWED_BY_MEMBER` —
    THE follower total (follower statistics no longer carry a total)."""

    firstDegreeSize: int
