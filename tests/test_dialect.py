"""The dialect: headers, Rest.li, envelopes, errors.

Every shape assertion corresponds to either an official-doc reading
(learn.microsoft.com, li-lms-2026-06/07 monikers) or an entry in the
docs/UNVERIFIED-FIELDS.md registry.
"""

from __future__ import annotations

from conftest import ADMIN, ORG_URN, ORG_URN_ENC, H

URL_POSTS = f"/rest/posts?q=author&author={ORG_URN_ENC}"
URL_SHARES = (
    "/rest/organizationalEntityShareStatistics"
    f"?q=organizationalEntity&organizationalEntity={ORG_URN_ENC}"
)
URL_FOLLOWERS = (
    "/rest/organizationalEntityFollowerStatistics"
    f"?q=organizationalEntity&organizationalEntity={ORG_URN_ENC}"
)
URL_PAGE = f"/rest/organizationPageStatistics?q=organization&organization={ORG_URN_ENC}"

#: A 7-day window WITHIN the 12-month sliding window (June 2026).
WINDOW = "timeRange:(start:1780617600000,end:1781222400000)"


# ── Authentication ────────────────────────────────────────────────────────────


def test_401_no_token_attested_body(client):
    """The ONLY auth error shape attested word for word: no `code` key, and
    serviceErrorCode is 401 — not a 65xxx code."""
    r = client.get(URL_POSTS)
    assert r.status_code == 401
    assert r.json() == {
        "message": "Empty oauth2_access_token",
        "serviceErrorCode": 401,
        "status": 401,
    }


def test_401_unknown_token(client):
    r = client.get(URL_POSTS, headers={**H, "Authorization": "Bearer whatever"})
    assert r.status_code == 401
    assert r.json()["code"] == "INVALID_ACCESS_TOKEN"


def test_401_expired_and_revoked_token(client):
    expired = client.get(
        URL_POSTS, headers={**H, "Authorization": "Bearer mock-linkedin-token-expired"}
    )
    assert (expired.status_code, expired.json()["code"]) == (401, "EXPIRED_ACCESS_TOKEN")
    revoked = client.get(
        URL_POSTS, headers={**H, "Authorization": "Bearer mock-linkedin-token-revoked"}
    )
    assert (revoked.status_code, revoked.json()["code"]) == (401, "REVOKED_ACCESS_TOKEN")


def test_basic_scheme_treated_as_empty_token(client):
    r = client.get(URL_POSTS, headers={**H, "Authorization": "Basic abc"})
    assert r.status_code == 401
    assert r.json()["message"] == "Empty oauth2_access_token"


# ── Versioning ────────────────────────────────────────────────────────────────


def test_400_missing_version_attested_body(client):
    r = client.get(URL_POSTS, headers={"Authorization": H["Authorization"]})
    assert r.status_code == 400
    assert r.json()["code"] == "VERSION_MISSING"
    assert "Linkedin-Version header" in r.json()["message"]


def test_426_version_outside_window(client):
    """Below AND above the active window: NONEXISTENT_VERSION."""
    for version in ("202301", "202612"):
        r = client.get(URL_POSTS, headers={**H, "Linkedin-Version": version})
        assert r.status_code == 426, version
        assert r.json() == {
            "message": f"Requested version {version} is not active",
            "code": "NONEXISTENT_VERSION",
            "status": 426,
        }


def test_400_malformed_version(client):
    for version in ("foo", "2024-08", "202413", "20240"):
        r = client.get(URL_POSTS, headers={**H, "Linkedin-Version": version})
        assert r.status_code == 400, version
        assert r.json()["code"] == "INVALID_VERSION"


def test_entire_active_window_is_accepted(client):
    """Both bounds inclusive — and the header lookup is case-insensitive."""
    for version in ("202408", "202506", "202607"):
        r = client.get(URL_POSTS, headers={**H, "Linkedin-Version": version})
        assert r.status_code == 200, version
    r = client.get(URL_POSTS, headers={**H} | {"LINKEDIN-VERSION": "202506"})
    assert r.status_code == 200


# ── Rest.li 2.0 / 1.0 ─────────────────────────────────────────────────────────


def test_time_intervals_2_0_raw(client):
    r = client.get(f"{URL_SHARES}&timeIntervals=({WINDOW},timeGranularityType:DAY)", headers=H)
    assert r.status_code == 200
    assert len(r.json()["elements"]) == 7


def test_time_intervals_2_0_fully_encoded(client):
    """The doc shows BOTH spellings — Starlette only decodes once, they
    must converge."""
    encoded = (
        "%28timeRange%3A%28start%3A1780617600000%2Cend%3A1781222400000%29"
        "%2CtimeGranularityType%3ADAY%29"
    )
    r = client.get(f"{URL_SHARES}&timeIntervals={encoded}", headers=H)
    assert r.status_code == 200
    assert len(r.json()["elements"]) == 7


def test_time_intervals_2_0_key_order_indifferent(client):
    r = client.get(f"{URL_SHARES}&timeIntervals=(timeGranularityType:DAY,{WINDOW})", headers=H)
    assert r.status_code == 200
    assert len(r.json()["elements"]) == 7


def test_time_intervals_1_0_dotted(client):
    """The 1.0 protocol shape — dotted parameters, no 2.0 header."""
    r = client.get(
        f"{URL_SHARES}&timeIntervals.timeRange.start=1780617600000"
        "&timeIntervals.timeRange.end=1781222400000&timeIntervals.timeGranularityType=DAY",
        headers={k: v for k, v in H.items() if k != "X-Restli-Protocol-Version"},
    )
    assert r.status_code == 200
    assert len(r.json()["elements"]) == 7


def test_2_0_syntax_without_protocol_header(client):
    """List()/parentheses without X-Restli-Protocol-Version: 2.0.0 -> 400
    (mock guardrail, real behavior unattested — see registry)."""
    without_protocol = {k: v for k, v in H.items() if k != "X-Restli-Protocol-Version"}
    r = client.get(
        f"{URL_SHARES}&timeIntervals=({WINDOW},timeGranularityType:DAY)",
        headers=without_protocol,
    )
    assert r.status_code == 400
    assert "X-Restli-Protocol-Version" in r.json()["message"]


def test_1_0_indexed_list(client, linkedin_state):
    """`shares[0]=…&shares[1]=…` — the 1.0 protocol's array shape."""
    posts = [p["id"] for p in linkedin_state.dataset["posts"] if p["id"].startswith("urn:li:share")]
    r = client.get(
        f"{URL_SHARES}&shares[0]={posts[0]}&shares[1]={posts[1]}",
        headers={k: v for k, v in H.items() if k != "X-Restli-Protocol-Version"},
    )
    assert r.status_code == 200
    assert len(r.json()["elements"]) == 2


def test_author_urn_encoded_or_raw(client):
    for author in (ORG_URN_ENC, ORG_URN):
        r = client.get(f"/rest/posts?q=author&author={author}", headers=H)
        assert r.status_code == 200, author


# ── Finders: q, params, pagination ───────────────────────────────────────────


def test_q_missing_then_unknown(client):
    assert client.get("/rest/posts", headers=H).status_code == 400
    r = client.get("/rest/posts?q=somethingelse", headers=H)
    assert r.status_code == 400
    assert "somethingelse" in r.json()["message"]


def test_page_statistics_trap_wrong_finder(client):
    """THE dialect trap: pageStatistics wants `q=organization` — the
    `q=organizationalEntity` used by the other two finders is rejected."""
    r = client.get(
        "/rest/organizationPageStatistics"
        f"?q=organizationalEntity&organizationalEntity={ORG_URN_ENC}",
        headers=H,
    )
    assert r.status_code == 400
    r2 = client.get(URL_PAGE, headers=H)
    assert r2.status_code == 200


def test_author_urn_malformed_then_foreign(client):
    r = client.get("/rest/posts?q=author&author=urn%3Ali%3Aperson%3A123", headers=H)
    assert r.status_code == 400
    assert r.json()["code"] == "INVALID_URN_TYPE"
    r2 = client.get("/rest/posts?q=author&author=urn%3Ali%3Aorganization%3A999", headers=H)
    assert r2.status_code == 403
    assert r2.json()["code"] == "ACCESS_DENIED"


def test_pagination_defaults_and_ceiling(client):
    r = client.get(URL_POSTS, headers=H)
    assert r.json()["paging"] == {"start": 0, "count": 10, "links": []}
    assert len(r.json()["elements"]) == 10
    assert client.get(f"{URL_POSTS}&count=100", headers=H).status_code == 200
    assert client.get(f"{URL_POSTS}&count=101", headers=H).status_code == 400
    assert client.get(f"{URL_POSTS}&start=-1", headers=H).status_code == 400
    assert client.get(f"{URL_POSTS}&count=abc", headers=H).status_code == 400


def test_end_of_data_short_page(client):
    """The end-of-data signal is the SHORT page — no paging.total on posts."""
    r = client.get(f"{URL_POSTS}&start=70&count=25", headers=H)
    assert r.status_code == 200
    assert 0 < len(r.json()["elements"]) < 25
    assert "total" not in r.json()["paging"]
    empty = client.get(f"{URL_POSTS}&start=500&count=25", headers=H)
    assert empty.json()["elements"] == []


def test_sort_last_modified_then_created(client):
    default = [
        p["lastModifiedAt"]
        for p in client.get(f"{URL_POSTS}&count=100", headers=H).json()["elements"]
    ]
    assert default == sorted(default, reverse=True)
    created = [
        p["createdAt"]
        for p in client.get(f"{URL_POSTS}&count=100&sortBy=CREATED", headers=H).json()["elements"]
    ]
    assert created == sorted(created, reverse=True)
    assert client.get(f"{URL_POSTS}&sortBy=WHATEVER", headers=H).status_code == 400


def test_view_context_accepted_and_ignored(client):
    """A stub must not break a real client that sends viewContext."""
    r = client.get(f"{URL_POSTS}&viewContext=AUTHOR", headers=H)
    assert r.status_code == 200


# ── Batches ───────────────────────────────────────────────────────────────────


def test_batch_posts_results_statuses_errors(client, linkedin_state):
    known = linkedin_state.dataset["posts"][0]["id"]
    unknown = "urn:li:share:1111111111111111111"
    ids = f"List({known.replace(':', '%3A')},{unknown.replace(':', '%3A')})"
    r = client.get(f"/rest/posts?ids={ids}", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert known in body["results"]
    assert body["statuses"][unknown] == 404
    assert body["errors"][unknown]["status"] == 404


def test_batch_organizations_mixed_statuses(client):
    r = client.get("/rest/organizations?ids=List(40123456,27056405)", headers=H)
    body = r.json()
    assert body["statuses"] == {"40123456": 200, "27056405": 403}
    assert body["results"]["40123456"]["localizedName"] == "Boréal Conseil"
    assert body["errors"]["27056405"]["code"] == "ACCESS_DENIED"


def test_finder_vanity_name_with_total(client):
    r = client.get("/rest/organizations?q=vanityName&vanityName=boreal-conseil", headers=H)
    assert r.json()["paging"]["total"] == 1
    empty = client.get("/rest/organizations?q=vanityName&vanityName=other", headers=H)
    assert (empty.json()["paging"]["total"], empty.json()["elements"]) == (0, [])


# ── Statistics: served shapes ─────────────────────────────────────────────────


def test_engagement_recalculated_on_served_element(client):
    stats = client.get(URL_SHARES, headers=H).json()["elements"][0]["totalShareStatistics"]
    expected = (
        stats["clickCount"] + stats["likeCount"] + stats["commentCount"] + stats["shareCount"]
    ) / stats["impressionCount"]
    assert stats["engagement"] == expected
    assert stats["impressionCount"] >= stats["uniqueImpressionsCount"]


def test_daily_buckets_without_uniques(client):
    """The official example is inconsistent on this field — the mock OMITS
    it from buckets and keeps it lifetime-only (see registry)."""
    r = client.get(f"{URL_SHARES}&timeIntervals=({WINDOW},timeGranularityType:DAY)", headers=H)
    for element in r.json()["elements"]:
        assert "uniqueImpressionsCount" not in element["totalShareStatistics"]
        assert "timeRange" in element


def test_shares_and_time_intervals_combo_rejected(client, linkedin_state):
    """ "Time-bound statistics is not supported for specific share queries" —
    the mock is STRICT by default; the compare_real probe will settle it."""
    share = next(
        p["id"] for p in linkedin_state.dataset["posts"] if p["id"].startswith("urn:li:share")
    )
    r = client.get(
        f"{URL_SHARES}&shares=List({share.replace(':', '%3A')})"
        f"&timeIntervals=({WINDOW},timeGranularityType:DAY)",
        headers=H,
    )
    assert r.status_code == 400
    assert "not supported" in r.json()["message"]


def test_invalid_granularity_and_required_start(client):
    r = client.get(f"{URL_SHARES}&timeIntervals=({WINDOW},timeGranularityType:WEEK)", headers=H)
    assert r.status_code == 400  # WEEK only exists on followers
    r2 = client.get(f"{URL_FOLLOWERS}&timeIntervals=(timeGranularityType:DAY)", headers=H)
    assert r2.status_code == 400
    assert "start" in r2.json()["message"]


# ── Miscellaneous ─────────────────────────────────────────────────────────────


def test_unknown_route_linkedin_envelope(client):
    r = client.get("/rest/whatever", headers=H)
    assert r.status_code == 404
    assert r.json()["message"] == "No root resource defined for path '/whatever'"
    assert r.json()["status"] == 404


def test_network_sizes_edge_type_required(client):
    without = client.get(f"/rest/networkSizes/{ORG_URN_ENC}", headers=H)
    assert without.status_code == 400
    invalid = client.get(f"/rest/networkSizes/{ORG_URN_ENC}?edgeType=OTHER", headers=H)
    assert invalid.status_code == 400


def test_daily_quota_without_retry_after(client):
    """THE LinkedIn regime: 429 WITHOUT Retry-After — the client must wait
    for UTC midnight, not an announced delay (a key difference from
    BoondManager)."""
    client.post(
        "/__admin/inject",
        json={"kind": "rate_limit", "scope": "/rest/posts", "after_requests": 2},
        headers=ADMIN,
    )
    assert client.get(URL_POSTS, headers=H).status_code == 200
    assert client.get(URL_POSTS, headers=H).status_code == 200
    denial = client.get(URL_POSTS, headers=H)
    assert denial.status_code == 429
    assert "Retry-After" not in denial.headers
    assert denial.json()["message"].startswith("Resource level throttle limit")
