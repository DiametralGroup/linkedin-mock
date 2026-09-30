"""The four consumer journeys, end to end.

These are the EXACT gestures of the insights360 connector: credentials
smoke test, paginated post listing, per-share lifetime stats in batches,
daily org/follower/view buckets. If any of these tests break, the
downstream pipeline breaks the same way.
"""

from __future__ import annotations

from datetime import UTC, datetime

from conftest import ORG_URN, ORG_URN_ENC, H, all_posts

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


# ── Journey 1: credentials smoke test ─────────────────────────────────────────


def test_smoke_credentials(client):
    org = client.get("/rest/organizations/40123456", headers=H)
    assert org.status_code == 200
    assert org.json()["id"] == 40123456  # a NUMBER, not a string
    assert org.json()["$URN"] == ORG_URN
    assert org.json()["vanityName"] == "boreal-conseil"

    network = client.get(
        f"/rest/networkSizes/{ORG_URN_ENC}?edgeType=COMPANY_FOLLOWED_BY_MEMBER", headers=H
    )
    assert network.status_code == 200
    assert network.json()["firstDegreeSize"] > 2612  # base + gains

    unknown = client.get("/rest/organizations/999999", headers=H)
    assert unknown.status_code == 404
    assert unknown.json()["message"] == "Organization 999999 is inactive"


# ── Journey 2: paginated post listing ─────────────────────────────────────────


def test_full_finder_walk(client):
    posts = all_posts(client)
    assert len(posts) == 72
    assert len({p["id"] for p in posts}) == 72
    assert all(p["lifecycleState"] == "PUBLISHED" for p in posts)
    assert all(p["author"] == ORG_URN for p in posts)
    types = {p["id"].split(":")[2] for p in posts}
    assert types == {"share", "ugcPost"}  # both URN families


def test_dataset_editorial_content(client):
    posts = all_posts(client)
    edited = [p for p in posts if p["lifecycleStateInfo"]["isEditedByAuthor"]]
    assert len(edited) == 3
    assert all(p["lastModifiedAt"] > p["publishedAt"] for p in edited)
    reshares = [p for p in posts if "reshareContext" in p]
    assert len(reshares) == 3
    ids = {p["id"] for p in posts}
    assert all(r["reshareContext"]["parent"] in ids for r in reshares)
    with_hashtag = [p for p in posts if "{hashtag|\\#|" in p["commentary"]]
    assert with_hashtag, "little-format templates must be served"
    with_mention = [p for p in posts if "@[" in p["commentary"]]
    assert all("](urn:li:organization:" in p["commentary"] for p in with_mention)


def test_single_read_and_errors(client, linkedin_state):
    urn = linkedin_state.dataset["posts"][5]["id"]
    r = client.get(f"/rest/posts/{_enc(urn)}", headers=H)
    assert r.status_code == 200
    assert r.json()["id"] == urn
    assert client.get("/rest/posts/urn%3Ali%3Ashare%3A42", headers=H).status_code == 404
    assert client.get("/rest/posts/notaurn", headers=H).status_code == 400


# ── Journey 3: per-post stats, batched ────────────────────────────────────────


def test_lifetime_stats_in_batches(client):
    """The connector's gesture: batches of 20 URNs, split shares/ugcPosts."""
    posts = all_posts(client)
    elements: list[dict] = []
    for start in range(0, len(posts), 20):
        batch = posts[start : start + 20]
        shares = [p["id"] for p in batch if p["id"].startswith("urn:li:share:")]
        ugc = [p["id"] for p in batch if p["id"].startswith("urn:li:ugcPost:")]
        for name, urns in (("shares", shares), ("ugcPosts", ugc)):
            if not urns:
                continue
            r = client.get(
                f"{URL_SHARES}&{name}=List({','.join(_enc(u) for u in urns)})", headers=H
            )
            assert r.status_code == 200
            elements.extend(r.json()["elements"])

    # Every post in the base dataset has activity: one element each.
    assert len(elements) == len(posts)
    for element in elements:
        key = "ugcPost" if "ugcPost" in element else "share"
        assert element[key].startswith("urn:li:")
        assert element["organizationalEntity"] == ORG_URN
        stats = element["totalShareStatistics"]
        assert stats["impressionCount"] > 0
        assert stats["uniqueImpressionsCount"] <= stats["impressionCount"]


def test_unknown_or_inactive_share_omitted(client, linkedin_state):
    known = next(
        p["id"] for p in linkedin_state.dataset["posts"] if p["id"].startswith("urn:li:share")
    )
    ghost = "urn:li:share:1111111111111111111"
    r = client.get(f"{URL_SHARES}&shares=List({_enc(known)},{_enc(ghost)})", headers=H)
    assert r.status_code == 200
    assert len(r.json()["elements"]) == 1  # the ghost is OMITTED, not zeroed


# ── Journey 4: daily buckets ───────────────────────────────────────────────────


def test_org_buckets_contiguous_and_aligned(client):
    start = 1780617600000  # 2026-06-05 UTC midnight
    end = start + 10 * DAY_MS
    r = client.get(
        f"{URL_SHARES}&timeIntervals=(timeRange:(start:{start},end:{end}),timeGranularityType:DAY)",
        headers=H,
    )
    elements = r.json()["elements"]
    assert len(elements) == 10
    for rank, element in enumerate(elements):
        span = element["timeRange"]
        assert span["end"] - span["start"] == DAY_MS
        assert span["start"] == start + rank * DAY_MS
        assert datetime.fromtimestamp(span["start"] / 1000, tz=UTC).hour == 0


def test_12_month_sliding_window_clipped(client):
    """Requesting from 2024: buckets older than D-365 don't exist."""
    start_2024 = 1725148800000  # 2024-09-01
    end = 1780617600000  # 2026-06-05
    r = client.get(
        f"{URL_SHARES}&timeIntervals=(timeRange:(start:{start_2024},end:{end}),"
        "timeGranularityType:MONTH)",
        headers=H,
    )
    elements = r.json()["elements"]
    assert elements, "the part within the window must be served"
    first = datetime.fromtimestamp(elements[0]["timeRange"]["start"] / 1000, tz=UTC).date()
    assert first.isoformat() >= "2025-07-01"  # clipped to ~D-365, month-aligned


def test_follower_gains_bounded_at_d_minus_2(client):
    """Window requested up to "now": the last served bucket must stop at
    D-2 — the documented availability rule."""
    start = 1780272000000  # 2026-06-01
    end = 1784678400000  # 2026-07-22 — past the dataset's anchor
    r = client.get(
        f"{URL_FOLLOWERS}&timeIntervals=(timeRange:(start:{start},end:{end}),"
        "timeGranularityType:DAY)",
        headers=H,
    )
    elements = r.json()["elements"]
    assert elements
    last = datetime.fromtimestamp(elements[-1]["timeRange"]["end"] / 1000, tz=UTC).date()
    assert last.isoformat() <= "2026-07-14"  # exclusive end -> data <= 07-13 = D-2
    assert any(
        e["followerGains"]["organicFollowerGain"] != 0
        or e["followerGains"]["paidFollowerGain"] != 0
        for e in elements
    )


def test_weekly_gains_and_paid_campaign(client):
    start = 1759104000000  # 2025-09-29 (Monday) — covers the October 2025 campaign
    end = start + 35 * DAY_MS
    r = client.get(
        f"{URL_FOLLOWERS}&timeIntervals=(timeRange:(start:{start},end:{end}),"
        "timeGranularityType:WEEK)",
        headers=H,
    )
    elements = r.json()["elements"]
    assert elements
    assert sum(e["followerGains"]["paidFollowerGain"] for e in elements) > 0


def test_lifetime_demographics(client):
    r = client.get(URL_FOLLOWERS, headers=H)
    element = r.json()["elements"][0]
    families = [
        "followerCountsByAssociationType",
        "followerCountsByGeoCountry",
        "followerCountsByFunction",
        "followerCountsByIndustry",
        "followerCountsByGeo",
        "followerCountsBySeniority",
        "followerCountsByStaffCountRange",
    ]
    for family in families:
        assert element[family], family
    staff = element["followerCountsByAssociationType"][0]
    assert staff["associationType"] == "EMPLOYEE"
    assert staff["followerCounts"] == {"organicFollowerCount": 34, "paidFollowerCount": 0}
    # Demographics roll paid into organic.
    for entry in element["followerCountsByGeoCountry"]:
        assert entry["followerCounts"]["paidFollowerCount"] == 0


def test_daily_page_views(client):
    start = 1780617600000  # 2026-06-05
    end = start + 5 * DAY_MS
    r = client.get(
        f"{URL_PAGE}&timeIntervals=(timeRange:(start:{start},end:{end}),timeGranularityType:DAY)",
        headers=H,
    )
    elements = r.json()["elements"]
    assert len(elements) == 5
    for element in elements:
        views = element["totalPageStatistics"]["views"]
        assert element["organization"] == ORG_URN
        for family in ("allPageViews", "overviewPageViews", "careersPageViews"):
            assert views[family]["uniquePageViews"] <= views[family]["pageViews"]
