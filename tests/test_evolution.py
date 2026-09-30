"""Time evolution — the delta an incremental extraction must see.

Tests advance time EXPLICITLY via /__admin/clock (the evolution interval is
3600 s in the harness: nothing moves on its own). Every event writes into
the UTC day of ITS OWN timestamp — a four-day advance fills four days of
buckets.
"""

from __future__ import annotations

import os

from conftest import ADMIN, ORG_URN_ENC, H, all_posts

import linkedin_mock as mock

URL_POSTS = f"/rest/posts?q=author&author={ORG_URN_ENC}"
URL_SHARES = (
    "/rest/organizationalEntityShareStatistics"
    f"?q=organizationalEntity&organizationalEntity={ORG_URN_ENC}"
)
URL_FOLLOWERS = (
    "/rest/organizationalEntityFollowerStatistics"
    f"?q=organizationalEntity&organizationalEntity={ORG_URN_ENC}"
)

DAY_MS = 86_400_000
#: 2026-07-14 UTC midnight — the day after the base dataset's stats ceiling.
DAY_AFTER_CEILING_MS = 1783987200000
FOUR_DAYS = 4 * 86_400


def _advance(client, seconds: int) -> None:
    r = client.post("/__admin/clock", json={"advance_seconds": seconds}, headers=ADMIN)
    assert r.status_code == 200


def test_frozen_without_clock_advance(client):
    """3600 s interval and no advance: two reads identical byte for byte."""
    a = client.get(URL_SHARES, headers=H).json()
    b = client.get(URL_SHARES, headers=H).json()
    assert a == b


def test_advance_fills_following_days(client):
    """+4 days: daily buckets AFTER the base dataset's ceiling."""
    _advance(client, FOUR_DAYS)
    end = DAY_AFTER_CEILING_MS + 6 * DAY_MS
    r = client.get(
        f"{URL_SHARES}&timeIntervals=(timeRange:(start:{DAY_AFTER_CEILING_MS},end:{end}),"
        "timeGranularityType:DAY)",
        headers=H,
    )
    elements = r.json()["elements"]
    assert elements
    assert sum(e["totalShareStatistics"]["impressionCount"] for e in elements) > 0


def test_follower_gains_after_advance(client):
    """The D-2 window opens as time advances: event gains become visible."""
    _advance(client, FOUR_DAYS)
    end = DAY_AFTER_CEILING_MS + 10 * DAY_MS
    r = client.get(
        f"{URL_FOLLOWERS}&timeIntervals=(timeRange:(start:{DAY_AFTER_CEILING_MS},end:{end}),"
        "timeGranularityType:DAY)",
        headers=H,
    )
    elements = r.json()["elements"]
    assert elements
    assert sum(e["followerGains"]["organicFollowerGain"] for e in elements) > 0


def test_new_post_at_head_of_finder(client):
    before = client.get(f"{URL_POSTS}&count=1", headers=H).json()["elements"][0]
    _advance(client, FOUR_DAYS)
    after = client.get(f"{URL_POSTS}&count=1", headers=H).json()["elements"][0]
    assert after["lastModifiedAt"] > before["lastModifiedAt"]
    assert len(all_posts(client)) > 72


def test_edit_resurfaces_an_old_post(client):
    """An OLD post resurfaced by an edit: the gesture the lastModifiedAt
    cursor must catch — the analogue of boond's _profil."""
    base_ceiling = max(p["lastModifiedAt"] for p in mock.state.dataset["posts"])
    _advance(client, FOUR_DAYS)
    posts = all_posts(client)
    old_edited = [
        p
        for p in posts
        if p["lastModifiedAt"] > base_ceiling
        and p["publishedAt"] < base_ceiling - 30 * DAY_MS
        and p["lifecycleStateInfo"]["isEditedByAuthor"]
    ]
    assert old_edited, "at least one old post must have been edited"


def test_sum_equals_lifetime_after_evolution(client):
    """The central invariant SURVIVES evolution: lifetime derived from buckets."""
    _advance(client, FOUR_DAYS)
    posts = all_posts(client)
    newest = max(posts, key=lambda p: p["publishedAt"])
    series = mock.state.dataset["posts_series"][newest["id"]]
    if not series:
        return  # published within the last virtual hour: no stats yet
    r = client.get(f"{URL_SHARES}&shares=List({newest['id'].replace(':', '%3A')})", headers=H)
    element = r.json()["elements"][0]["totalShareStatistics"]
    assert element["impressionCount"] == sum(b["impressionCount"] for b in series.values())


def test_deterministic_timeline(client):
    """reset -> +1 day -> capture, twice: the same bytes."""

    def capture() -> tuple:
        mock.state.reset()
        _advance(client, 86_400)
        page = client.get(f"{URL_POSTS}&count=5", headers=H).json()
        end = DAY_AFTER_CEILING_MS + 3 * DAY_MS
        day = client.get(
            f"{URL_SHARES}&timeIntervals=(timeRange:(start:{DAY_AFTER_CEILING_MS},end:{end}),"
            "timeGranularityType:DAY)",
            headers=H,
        ).json()
        return page, day

    assert capture() == capture()


def test_journal_exposes_the_delta(client):
    _advance(client, 6 * 3600)
    admin_state = client.get("/__admin/state", headers=ADMIN).json()
    assert admin_state["evolution"]["applied"] >= 6
    kinds = {entry["kind"] for entry in admin_state["evolution"]["journal"]}
    assert kinds <= {"daily_stats", "new_post", "follower_gains", "post_edit", "viral_spike"}


def test_disableable_via_env(client):
    os.environ["LINKEDIN_MOCK_EVOLUTION"] = "false"
    try:
        mock.settings.reload()
        mock.state.reset()
        _advance(client, FOUR_DAYS)
        admin_state = client.get("/__admin/state", headers=ADMIN).json()
        assert admin_state["evolution"]["applied"] == 0
        assert len(all_posts(client)) == 72
    finally:
        del os.environ["LINKEDIN_MOCK_EVOLUTION"]
        mock.settings.reload()
        mock.state.reset()


def test_reset_rearms_the_timeline(client):
    _advance(client, FOUR_DAYS)
    assert client.get("/__admin/state", headers=ADMIN).json()["evolution"]["applied"] > 0
    client.post("/__admin/reset", headers=ADMIN)
    admin_state = client.get("/__admin/state", headers=ADMIN).json()
    assert admin_state["evolution"]["applied"] == 0
    assert admin_state["totals"]["posts"] == 72


def test_mutate_pushes_post_to_head(client):
    target = mock.state.dataset["posts"][3]["id"]
    r = client.post(
        "/__admin/mutate",
        json={"post_id": target, "commentary": "Content corrected after the fact."},
        headers=ADMIN,
    )
    assert r.status_code == 200
    head = client.get(f"{URL_POSTS}&count=1", headers=H).json()["elements"][0]
    assert head["id"] == target
    assert head["commentary"] == "Content corrected after the fact."
    assert head["lifecycleStateInfo"]["isEditedByAuthor"] is True


def test_delete_leaves_finder_but_not_history(client):
    """The real-world regime: a deleted post disappears from the listing and
    from per-share, but org-wide aggregates keep its past."""
    before = client.get(URL_SHARES, headers=H).json()["elements"][0]["totalShareStatistics"]
    target = mock.state.dataset["posts"][0]["id"]
    client.post("/__admin/delete", json={"post_id": target}, headers=ADMIN)

    assert len(all_posts(client)) == 71
    per_share = client.get(f"{URL_SHARES}&shares=List({target.replace(':', '%3A')})", headers=H)
    if not per_share.json()["elements"]:
        pass  # share: omitted, as expected
    else:  # the target was a ugcPost: the shares family would never have served it
        raise AssertionError("the deleted post must no longer be served per-share")
    after = client.get(URL_SHARES, headers=H).json()["elements"][0]["totalShareStatistics"]
    assert after["impressionCount"] == before["impressionCount"]
