"""Coherence invariants — what makes the mock comparable to a real page.

The equivalent of boondmanager-mock's test_coherence_*: each invariant is a
property the real world has by construction, so the generator must have it
by construction TOO — not by luck of the seed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from conftest import ORG_URN_ENC, H

import linkedin_mock as mock
from linkedin_mock.dataset.realiste import LAST_STAT_DAY, LAST_UPDATE_DAY, build_realiste_dataset

URL_SHARES = (
    "/rest/organizationalEntityShareStatistics"
    f"?q=organizationalEntity&organizationalEntity={ORG_URN_ENC}"
)
URL_FOLLOWERS = (
    "/rest/organizationalEntityFollowerStatistics"
    f"?q=organizationalEntity&organizationalEntity={ORG_URN_ENC}"
)
URL_PAGE = f"/rest/organizationPageStatistics?q=organization&organization={ORG_URN_ENC}"

DAY_MS = 86_400_000


def _enc(urn: str) -> str:
    return urn.replace(":", "%3A")


def test_orders_of_magnitude_per_bucket(linkedin_state):
    """impressions >= clicks, and likes+comments+shares <= impressions —
    on EVERY bucket of EVERY series."""
    for series in linkedin_state.dataset["posts_series"].values():
        for day, bucket in series.items():
            assert bucket["impressionCount"] >= bucket["clickCount"] >= 0, day
            social_interactions = (
                bucket["likeCount"] + bucket["commentCount"] + bucket["shareCount"]
            )
            assert social_interactions <= bucket["impressionCount"], day
            assert all(v >= 0 for v in bucket.values()), day


def test_lifetime_is_sum_of_buckets(client, linkedin_state):
    """The central invariant: sum(daily) == lifetime, per post and org-wide.

    Lifetime counters are DERIVED by summing at serialization time — this
    test locks the contract so a future "optimization" doesn't quietly
    denormalize it."""
    org = client.get(URL_SHARES, headers=H).json()["elements"][0]["totalShareStatistics"]
    expected = sum(
        bucket["impressionCount"]
        for series in linkedin_state.dataset["posts_series"].values()
        for bucket in series.values()
    )
    assert org["impressionCount"] == expected

    # Per post: for a RECENT post (series entirely within the sliding
    # window), the sum of its served buckets == its per-share aggregate.
    posts = linkedin_state.dataset["posts"]
    recent = posts[-3]
    lifetime = client.get(f"{URL_SHARES}&shares=List({_enc(recent['id'])})", headers=H)
    if not lifetime.json()["elements"]:  # ugcPost depending on the seed: query the other family
        lifetime = client.get(f"{URL_SHARES}&ugcPosts=List({_enc(recent['id'])})", headers=H)
    counts = lifetime.json()["elements"][0]["totalShareStatistics"]
    series = linkedin_state.dataset["posts_series"][recent["id"]]
    assert counts["impressionCount"] == sum(b["impressionCount"] for b in series.values())
    assert counts["clickCount"] == sum(b["clickCount"] for b in series.values())


def test_engagement_exact_formula_everywhere(client, linkedin_state):
    posts = linkedin_state.dataset["posts"][:8]
    urns = ",".join(_enc(p["id"]) for p in posts if p["id"].startswith("urn:li:share"))
    r = client.get(f"{URL_SHARES}&shares=List({urns})", headers=H)
    for element in r.json()["elements"]:
        stats = element["totalShareStatistics"]
        expected = (
            stats["clickCount"] + stats["likeCount"] + stats["commentCount"] + stats["shareCount"]
        ) / stats["impressionCount"]
        assert stats["engagement"] == expected


def test_series_start_at_publication(linkedin_state):
    for post in linkedin_state.dataset["posts"]:
        series = linkedin_state.dataset["posts_series"][post["id"]]
        publication_day = datetime.fromtimestamp(post["publishedAt"] / 1000, tz=UTC).date()
        days = sorted(series)
        assert days[0] == publication_day.isoformat(), post["id"]
        assert days[-1] <= LAST_STAT_DAY.isoformat(), post["id"]


def test_time_ceilings_of_base_dataset(linkedin_state):
    """createdAt <= publishedAt <= lastModifiedAt, and the lastModifiedAt
    ceiling: a cursor set after LAST_UPDATE_DAY must see zero base-dataset
    posts."""
    ceiling_ms = (LAST_UPDATE_DAY.toordinal() + 1 - datetime(1970, 1, 1).toordinal()) * DAY_MS
    for post in linkedin_state.dataset["posts"]:
        assert post["createdAt"] <= post["publishedAt"] <= post["lastModifiedAt"]
        assert post["lastModifiedAt"] < ceiling_ms


def test_network_and_facets(client):
    network = client.get(
        f"/rest/networkSizes/{ORG_URN_ENC}?edgeType=COMPANY_FOLLOWED_BY_MEMBER", headers=H
    ).json()["firstDegreeSize"]
    data = mock.state.dataset
    gains = sum(
        g["organicFollowerGain"] + g["paidFollowerGain"] for g in data["followers_series"].values()
    )
    assert network == data["followers_base"] + gains

    element = client.get(URL_FOLLOWERS, headers=H).json()["elements"][0]
    for family, _key, _segments, coverage in data["demographics_shares"]:
        facet_total = sum(e["followerCounts"]["organicFollowerCount"] for e in element[family])
        # Each facet sums to EXACTLY round(total x coverage) — the
        # largest-remainder split neither loses nor invents anyone.
        assert facet_total == round(network * coverage), family


def test_views_arithmetic(client):
    views = client.get(URL_PAGE, headers=H).json()["elements"][0]["totalPageStatistics"]["views"]

    def n(key: str) -> int:
        return views[key]["pageViews"]

    assert n("allPageViews") == n("allDesktopPageViews") + n("allMobilePageViews")
    assert n("allPageViews") == n("overviewPageViews") + n("careersPageViews")
    assert n("careersPageViews") == n("jobsPageViews") + n("lifeAtPageViews")
    assert n("desktopCareersPageViews") == n("desktopJobsPageViews") + n("desktopLifeAtPageViews")


def test_views_correlated_with_post_days(linkedin_state):
    """Publication days see more views than quiet days."""
    post_days = {
        datetime.fromtimestamp(p["publishedAt"] / 1000, tz=UTC).date().isoformat()
        for p in linkedin_state.dataset["posts"]
    }
    series = linkedin_state.dataset["views_series"]
    post_views = [rec["overview"] for day, rec in series.items() if day in post_days]
    quiet_views = [rec["overview"] for day, rec in series.items() if day not in post_days]
    assert sum(post_views) / len(post_views) > sum(quiet_views) / len(quiet_views)


def test_viral_post_present(linkedin_state):
    """A deterministic spike — the anomaly downstream must be able to absorb."""
    impressions_per_post = [
        sum(b["impressionCount"] for b in series.values())
        for series in linkedin_state.dataset["posts_series"].values()
    ]
    sorted_vals = sorted(impressions_per_post)
    assert sorted_vals[-1] > 5 * sorted_vals[-2] or sorted_vals[-1] > 30_000


def test_byte_exact_determinism():
    """Two builds with the same seed -> the same world, byte for byte."""
    a = json.dumps(build_realiste_dataset(42), sort_keys=True, default=str)
    b = json.dumps(build_realiste_dataset(42), sort_keys=True, default=str)
    assert a == b
    other = json.dumps(build_realiste_dataset(7), sort_keys=True, default=str)
    assert a != other
