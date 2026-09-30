"""Statistics — UTC bucketing, rolling windows, recomputed engagement.

The dataset's INTERNAL series (impressions/clicks/reactions per post and per
UTC day, follower gains, page views) are the sole source; everything the API
serves is DERIVED from them at serialization time:

  • lifetime counters are SUMS of the buckets — the invariant
    sum(daily) == lifetime holds by construction, not by luck;
  • `engagement` is recomputed on every element served:
    (clicks + likes + comments + shares) / impressions — formula VERIFIED
    numerically against the three official examples, 0 when there are no
    impressions;
  • daily buckets OMIT `uniqueImpressionsCount` (the official example only
    carries it on one bucket, under a typo'd name — real emission not
    attested, cf. registry); the lifetime aggregate carries it.

Windows reproduced (official doc):

  shares      "rolling 12-month window" — buckets earlier than J-365 are
              clipped;
  followers   data available from J-365 to J-2 (UTC), `timeRange.start`
              mandatory;
  page        no documented window — served since the page's creation.

"Now" is VIRTUAL: dataset epoch + elapsed time since reset, including
/__admin/clock advances. Two servers at the same age serve the same windows —
the property that makes extraction tests replayable.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from .injection import engine
from .settings import settings

#: Granularities accepted per endpoint (official doc).
GRANULARITIES_SHARES = ("DAY", "MONTH")
GRANULARITIES_FOLLOWERS = ("DAY", "WEEK", "MONTH")
GRANULARITIES_PAGE = ("DAY", "MONTH")

_COUNTERS = ("impressionCount", "clickCount", "likeCount", "commentCount", "shareCount")


# ── Virtual clock ────────────────────────────────────────────────────────────


def virtual_now() -> datetime:
    """EPOCH + elapsed time since reset (/__admin/clock advances included)."""
    from .evolution import EPOCH
    from .state import state

    elapsed = engine.now() - state.evolution.start
    return (EPOCH + timedelta(seconds=elapsed)).astimezone(UTC)


def virtual_day() -> date:
    return virtual_now().date()


def _ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)


def _day_from_ms(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date()


# ── Bucketing ────────────────────────────────────────────────────────────────


def _bucket_start(day: date, granularity: str) -> date:
    if granularity == "DAY":
        return day
    if granularity == "WEEK":
        # ISO Monday alignment — not attested by the doc, cf. registry.
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def _next_bucket(start: date, granularity: str) -> date:
    if granularity == "DAY":
        return start + timedelta(days=1)
    if granularity == "WEEK":
        return start + timedelta(days=7)
    if start.month == 12:
        return date(start.year + 1, 1, 1)
    return date(start.year, start.month + 1, 1)


def split_buckets(
    start_ms: int,
    end_ms: int,
    granularity: str,
    *,
    min_day: date,
    end_day_ex: date,
) -> list[tuple[date, date]]:
    """The [start, end) buckets, clipped to the endpoint's window.

    Requested `start` is inclusive, `end` exclusive, normalized to the UTC
    day — the documented regime of share statistics, applied everywhere (the
    pageStatistics parameter table says the opposite, very likely a doc typo;
    cf. registry).
    """
    requested_start = max(_day_from_ms(start_ms), min_day)
    requested_end = min(_day_from_ms(end_ms - 1) + timedelta(days=1), end_day_ex)
    if requested_end <= requested_start:
        return []
    buckets: list[tuple[date, date]] = []
    cursor = _bucket_start(requested_start, granularity)
    while cursor < requested_end:
        bucket_end = _next_bucket(cursor, granularity)
        buckets.append((cursor, min(bucket_end, requested_end)))
        cursor = bucket_end
    return buckets


def shares_window() -> tuple[date, date]:
    """[J-365, J) — the share statistics' rolling 12-month window."""
    today = virtual_day()
    return today - timedelta(days=365), today + timedelta(days=1)


def followers_window() -> tuple[date, date]:
    """[J-365, J-2] inclusive, i.e. an exclusive bound at J-1."""
    today = virtual_day()
    return today - timedelta(days=365), today - timedelta(days=1)


def page_window() -> tuple[date, date]:
    """Since the page's creation — no documented window."""
    from .state import state

    return state.dataset["series_start"], virtual_day() + timedelta(days=1)


def time_range(start: date, end_ex: date) -> dict[str, int]:
    return {"start": _ms(start), "end": _ms(end_ex)}


# ── Share statistics ─────────────────────────────────────────────────────────


def _zero() -> dict[str, int]:
    return dict.fromkeys(_COUNTERS, 0)


def _sum(series: dict[str, dict[str, int]], start: date, end_ex: date) -> dict[str, int]:
    """Sum of the counters of ONE series {ISO day → counters} over [start, end)."""
    total = _zero()
    day = start
    while day < end_ex:
        if (bucket := series.get(day.isoformat())) is not None:
            for counter in _COUNTERS:
                total[counter] += bucket.get(counter, 0)
        day += timedelta(days=1)
    return total


def _series_bounds(series: dict[str, dict[str, int]]) -> tuple[date, date] | None:
    if not series:
        return None
    days = sorted(series)
    return date.fromisoformat(days[0]), date.fromisoformat(days[-1]) + timedelta(days=1)


def _share_stats(counters: dict[str, int], uniques: int | None = None) -> dict[str, Any]:
    """The `totalShareStatistics` block, engagement recomputed at serialization.

    Key order mirrors the official lifetime example; time-bound buckets omit
    `uniqueImpressionsCount`.
    """
    impressions = counters["impressionCount"]
    interactions = (
        counters["clickCount"]
        + counters["likeCount"]
        + counters["commentCount"]
        + counters["shareCount"]
    )
    block: dict[str, Any] = {}
    if uniques is not None:
        block["uniqueImpressionsCount"] = uniques
    block["clickCount"] = counters["clickCount"]
    block["engagement"] = interactions / impressions if impressions else 0
    block["likeCount"] = counters["likeCount"]
    block["commentCount"] = counters["commentCount"]
    block["shareCount"] = counters["shareCount"]
    block["impressionCount"] = impressions
    return block


def _urn_key(urn: str) -> str:
    """The per-share element's key: `share` or `ugcPost` depending on the URN type."""
    return "ugcPost" if urn.startswith("urn:li:ugcPost:") else "share"


def lifetime_share_element() -> dict[str, Any]:
    """The organization-wide lifetime aggregate — a single element."""
    from .state import state

    total = _zero()
    for series in state.dataset["posts_series"].values():
        bounds = _series_bounds(series)
        if bounds is None:
            continue
        partial = _sum(series, *bounds)
        for counter in _COUNTERS:
            total[counter] += partial[counter]
    uniques = sum(state.dataset["lifetime_uniques"].values())
    return {
        "totalShareStatistics": _share_stats(total, uniques=uniques),
        "organizationalEntity": settings.organization_urn,
    }


def share_elements_per_post(urns: list[str]) -> list[dict[str, Any]]:
    """One element per ACTIVE URN — posts with no activity (or unknown, or
    deleted) are OMITTED: "can be assumed to have counts of 0" (doc)."""
    from .state import state

    existing = {p["id"] for p in state.dataset["posts"]}
    elements: list[dict[str, Any]] = []
    for urn in urns:
        series = state.dataset["posts_series"].get(urn)
        if urn not in existing or not series:
            continue
        bounds = _series_bounds(series)
        if bounds is None:
            continue
        counters = _sum(series, *bounds)
        if all(v == 0 for v in counters.values()):
            continue
        elements.append(
            {
                "totalShareStatistics": _share_stats(
                    counters, uniques=state.dataset["lifetime_uniques"].get(urn, 0)
                ),
                _urn_key(urn): urn,
                "organizationalEntity": settings.organization_urn,
            }
        )
    return elements


def share_bucket_elements(
    start_ms: int, end_ms: int, granularity: str, urns: list[str] | None = None
) -> list[dict[str, Any]]:
    """The time buckets — whole org, or per post (non-strict mode)."""
    from .state import state

    min_day, end_day_ex = shares_window()
    buckets = split_buckets(start_ms, end_ms, granularity, min_day=min_day, end_day_ex=end_day_ex)
    elements: list[dict[str, Any]] = []
    if urns is None:
        all_series = list(state.dataset["posts_series"].values())
        for start, end_ex in buckets:
            total = _zero()
            for series in all_series:
                partial = _sum(series, start, end_ex)
                for counter in _COUNTERS:
                    total[counter] += partial[counter]
            elements.append(
                {
                    "timeRange": time_range(start, end_ex),
                    "totalShareStatistics": _share_stats(total),
                    "organizationalEntity": settings.organization_urn,
                }
            )
        return elements
    for urn in urns:
        series = state.dataset["posts_series"].get(urn)
        if not series:
            continue
        for start, end_ex in buckets:
            elements.append(
                {
                    "timeRange": time_range(start, end_ex),
                    "totalShareStatistics": _share_stats(_sum(series, start, end_ex)),
                    _urn_key(urn): urn,
                    "organizationalEntity": settings.organization_urn,
                }
            )
    return elements


# ── Followers ────────────────────────────────────────────────────────────────


def total_followers() -> int:
    """`networkSizes.firstDegreeSize` = base + Σ gains (net, negatives included)."""
    from .state import state

    gains = sum(
        g["organicFollowerGain"] + g["paidFollowerGain"]
        for g in state.dataset["followers_series"].values()
    )
    return int(state.dataset["followers_base"] + gains)


def _facet_counts(organic: int) -> dict[str, int]:
    """Demographics roll the paid count into the organic one (official note:
    "Do not refer to the paidFollowerCount field")."""
    return {"organicFollowerCount": organic, "paidFollowerCount": 0}


def _apportion(total: int, weights: list[float]) -> list[int]:
    """Largest-remainder apportionment — the shares sum to EXACTLY total."""
    raw = [total * p for p in weights]
    bases = [int(b) for b in raw]
    remainders = sorted(range(len(raw)), key=lambda i: raw[i] - bases[i], reverse=True)
    missing = total - sum(bases)
    for i in remainders[:missing]:
        bases[i] += 1
    return bases


def lifetime_followers_element() -> dict[str, Any]:
    """The lifetime element: the 7 demographic facet families.

    Each facet covers LESS than the total (members without the attribute are
    absent, as in reality) — except `associationType`, which only lists
    staff. The total itself lives on /networkSizes.
    """
    from .state import state

    total = total_followers()
    element: dict[str, Any] = {}
    element["followerCountsByAssociationType"] = [
        {
            "followerCounts": _facet_counts(state.dataset["staff_count"]),
            "associationType": "EMPLOYEE",
        }
    ]
    for facet, key, segments, coverage in state.dataset["demographics_shares"]:
        covered = round(total * coverage)
        shares = _apportion(covered, [weight for _, weight in segments])
        element[facet] = [
            {"followerCounts": _facet_counts(n), key: value}
            for (value, _), n in zip(segments, shares, strict=True)
        ]
    element["organizationalEntity"] = settings.organization_urn
    return element


def follower_bucket_elements(start_ms: int, end_ms: int, granularity: str) -> list[dict[str, Any]]:
    from .state import state

    min_day, end_day_ex = followers_window()
    series = state.dataset["followers_series"]
    elements: list[dict[str, Any]] = []
    for start, end_ex in split_buckets(
        start_ms, end_ms, granularity, min_day=min_day, end_day_ex=end_day_ex
    ):
        organic = 0
        paid = 0
        day = start
        while day < end_ex:
            if (gains := series.get(day.isoformat())) is not None:
                organic += gains["organicFollowerGain"]
                paid += gains["paidFollowerGain"]
            day += timedelta(days=1)
        elements.append(
            {
                "timeRange": time_range(start, end_ex),
                "followerGains": {
                    "organicFollowerGain": organic,
                    "paidFollowerGain": paid,
                },
                "organizationalEntity": settings.organization_urn,
            }
        )
    return elements


# ── Page views ───────────────────────────────────────────────────────────────


def _day_views(rec: dict[str, Any]) -> dict[str, int]:
    """The 15 counters of a day, derived from the compact storage.

    Arithmetic VERIFIED against the official example: all = allDesktop +
    allMobile = overview + careers, and careers = jobs + lifeAt.
    """
    overview, jobs, life = rec["overview"], rec["jobs"], rec["lifeAt"]
    desktop_share = rec["part_bureau"]
    overview_desktop = round(overview * desktop_share)
    jobs_desktop = round(jobs * desktop_share)
    life_desktop = round(life * desktop_share)
    careers = jobs + life
    careers_desktop = jobs_desktop + life_desktop
    total = overview + careers
    total_desktop = overview_desktop + careers_desktop
    return {
        "allDesktopPageViews": total_desktop,
        "allMobilePageViews": total - total_desktop,
        "allPageViews": total,
        "careersPageViews": careers,
        "desktopCareersPageViews": careers_desktop,
        "desktopJobsPageViews": jobs_desktop,
        "desktopLifeAtPageViews": life_desktop,
        "desktopOverviewPageViews": overview_desktop,
        "jobsPageViews": jobs,
        "lifeAtPageViews": life,
        "mobileCareersPageViews": careers - careers_desktop,
        "mobileJobsPageViews": jobs - jobs_desktop,
        "mobileLifeAtPageViews": life - life_desktop,
        "mobileOverviewPageViews": overview - overview_desktop,
        "overviewPageViews": overview,
    }


def _cumulative_views(start: date, end_ex: date) -> tuple[dict[str, int], float]:
    """Sum of the 15 counters over [start, end) + average unique share."""
    from .state import state

    series = state.dataset["views_series"]
    total = dict.fromkeys(_day_views({"overview": 0, "jobs": 0, "lifeAt": 0, "part_bureau": 0}), 0)
    unique_shares: list[float] = []
    day = start
    while day < end_ex:
        if (rec := series.get(day.isoformat())) is not None:
            for key, value in _day_views(rec).items():
                total[key] += value
            unique_shares.append(rec["part_uniques"])
        day += timedelta(days=1)
    return total, (sum(unique_shares) / len(unique_shares) if unique_shares else 0.78)


def lifetime_page_element() -> dict[str, Any]:
    """The lifetime element: full totalPageStatistics + 6 facets."""
    from .state import state

    start, end_ex = page_window()
    views, _ = _cumulative_views(start, end_ex)
    element: dict[str, Any] = {}
    for facet, key, segments, coverage in state.dataset["pages_shares"]:
        covered = round(views["allPageViews"] * coverage)
        shares = _apportion(covered, [weight for _, weight in segments])
        element[facet] = [
            {"pageStatistics": {"views": {"allPageViews": {"pageViews": n}}}, key: value}
            for (value, _), n in zip(segments, shares, strict=True)
        ]
    element["totalPageStatistics"] = {
        "clicks": {"desktopCustomButtonClickCounts": [], "mobileCustomButtonClickCounts": []},
        "views": {key: {"pageViews": value} for key, value in sorted(views.items())},
    }
    element["organization"] = settings.organization_urn
    return element


def page_bucket_elements(start_ms: int, end_ms: int, granularity: str) -> list[dict[str, Any]]:
    """Time buckets — a REDUCED set of families, with `uniquePageViews`
    (the exact subset isn't attested, cf. registry)."""
    min_day, end_day_ex = page_window()
    elements: list[dict[str, Any]] = []
    for start, end_ex in split_buckets(
        start_ms, end_ms, granularity, min_day=min_day, end_day_ex=end_day_ex
    ):
        views, unique_share = _cumulative_views(start, end_ex)
        families = {
            "allPageViews": views["allPageViews"],
            "overviewPageViews": views["overviewPageViews"],
            "careersPageViews": views["careersPageViews"],
            "jobsPageViews": views["jobsPageViews"],
            "lifeAtPageViews": views["lifeAtPageViews"],
        }
        elements.append(
            {
                "timeRange": time_range(start, end_ex),
                "totalPageStatistics": {
                    "views": {
                        key: {
                            "pageViews": value,
                            "uniquePageViews": round(value * unique_share),
                        }
                        for key, value in families.items()
                    }
                },
                "organization": settings.organization_urn,
            }
        )
    return elements
