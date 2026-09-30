"""Failure modes — a mock's real reason for existing.

The LinkedIn regime has its own specifics, and those are what we exercise:
DAILY quota reset at (virtual) UTC midnight WITHOUT Retry-After, four
non-retryable 401 variants, mid-quarter version retirement.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from conftest import ADMIN, ORG_URN_ENC, H

import linkedin_mock as mock

URL_POSTS = f"/rest/posts?q=author&author={ORG_URN_ENC}"
URL_SHARES = (
    "/rest/organizationalEntityShareStatistics"
    f"?q=organizationalEntity&organizationalEntity={ORG_URN_ENC}"
)


def _inject(client, **kwargs):
    r = client.post("/__admin/inject", json=kwargs, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()["rule_id"]


# ── The control plane itself ──────────────────────────────────────────────────


def test_admin_requires_its_token(client):
    assert client.get("/__admin/state").status_code == 401
    assert client.get("/__admin/state", headers={"X-Mock-Admin-Token": "wrong"}).status_code == 401


def test_admin_absent_when_disabled():
    """The router is NOT mounted when the control plane is disabled — absent,
    not forbidden. Checked in a FRESH process: the mount happens at import
    time."""
    code = (
        "import os\n"
        "os.environ['LINKEDIN_MOCK_ADMIN_ENABLED'] = 'false'\n"
        "from fastapi.testclient import TestClient\n"
        "import linkedin_mock as mock\n"
        "r = TestClient(mock.app).get('/__admin/state')\n"
        "assert r.status_code == 404, r.status_code\n"
        "assert 'No root resource' in r.json()['message']\n"
    )
    environment = {k: v for k, v in os.environ.items() if k != "LINKEDIN_MOCK_ADMIN_ENABLED"}
    result = subprocess.run(
        [sys.executable, "-c", code], env=environment, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_unknown_kind_rejected(client):
    r = client.post("/__admin/inject", json={"kind": "explosion"}, headers=ADMIN)
    assert r.status_code == 422


# ── Daily quota ────────────────────────────────────────────────────────────────


def test_daily_quota_and_utc_midnight(client):
    """429 past the day's quota, then the clock crosses virtual UTC midnight
    and the counter restarts — with no Retry-After at any point."""
    _inject(client, kind="rate_limit", scope="/rest/posts", after_requests=2)
    assert client.get(URL_POSTS, headers=H).status_code == 200
    assert client.get(URL_POSTS, headers=H).status_code == 200
    denial = client.get(URL_POSTS, headers=H)
    assert denial.status_code == 429
    assert "Retry-After" not in denial.headers
    # Ignore the 429 and retry immediately: still 429.
    assert client.get(URL_POSTS, headers=H).status_code == 429

    client.post("/__admin/clock", json={"advance_seconds": 86_400}, headers=ADMIN)
    assert client.get(URL_POSTS, headers=H).status_code == 200


def test_quota_does_not_leak_across_paths(client):
    _inject(client, kind="rate_limit", scope="/rest/posts", after_requests=1)
    assert client.get(URL_POSTS, headers=H).status_code == 200
    assert client.get(URL_POSTS, headers=H).status_code == 429
    # The quota targets /rest/posts: statistics keep being served.
    assert client.get(URL_SHARES, headers=H).status_code == 200


def test_baseline_quota_survives_reset(client):
    """The baseline declared by the environment is reapplied on every
    reset — a test can't accidentally cancel it."""
    os.environ["LINKEDIN_MOCK_DAILY_QUOTA"] = "1"
    try:
        mock.settings.reload()
        mock.state.reset()
        rules = client.get("/__admin/state", headers=ADMIN).json()["injections"]
        assert any(r["kind"] == "rate_limit" for r in rules)
        assert client.get(URL_POSTS, headers=H).status_code == 200
        assert client.get(URL_POSTS, headers=H).status_code == 429
    finally:
        del os.environ["LINKEDIN_MOCK_DAILY_QUOTA"]
        mock.settings.reload()
        mock.state.reset()


# ── Hard and transient failures ───────────────────────────────────────────────


def test_transient_status_exhausts(client):
    _inject(client, kind="status", scope="/rest/posts", status=503, times=2)
    assert client.get(URL_POSTS, headers=H).status_code == 503
    assert client.get(URL_POSTS, headers=H).status_code == 503
    assert client.get(URL_POSTS, headers=H).status_code == 200


def test_persistent_status_does_not_exhaust(client):
    _inject(client, kind="status", scope="/rest/posts", status=500)
    for _ in range(3):
        assert client.get(URL_POSTS, headers=H).status_code == 500


def test_real_latency(client):
    _inject(client, kind="latency", scope="/rest/posts", seconds=0.2, times=1)
    start = time.monotonic()
    assert client.get(URL_POSTS, headers=H).status_code == 200
    assert time.monotonic() - start >= 0.15
    # The rule is exhausted: the next request is fast.
    start = time.monotonic()
    client.get(URL_POSTS, headers=H)
    assert time.monotonic() - start < 0.15


# ── Auth and version rejections ───────────────────────────────────────────────


def test_auth_reject_all_variants(client):
    expected = {
        "empty": "Empty oauth2_access_token",
        "invalid": "Invalid access token",
        "expired": "The token used in the request has expired",
        "revoked": "The token used in the request has been revoked by the member",
    }
    for variant, message in expected.items():
        rule_id = _inject(client, kind="auth_reject", scope="/rest/posts", variant=variant, times=1)
        r = client.get(URL_POSTS, headers=H)  # credentials are otherwise VALID
        assert r.status_code == 401, variant
        assert r.json()["message"] == message
        client.delete(f"/__admin/inject/{rule_id}", headers=ADMIN)
    assert client.get(URL_POSTS, headers=H).status_code == 200


def test_version_reject_mid_quarter(client):
    """A version retirement WITHOUT touching the window: the 426 the
    connector will see the day LinkedIn retires its pinned version."""
    _inject(client, kind="version_reject", scope="*", times=1)
    r = client.get(URL_POSTS, headers=H)
    assert r.status_code == 426
    assert r.json()["code"] == "NONEXISTENT_VERSION"
    assert client.get(URL_POSTS, headers=H).status_code == 200


# ── Pagination drift ───────────────────────────────────────────────────────────


def test_page_drift_insert_duplicates(client):
    """An upstream insert between two pages: page 1's last element resurfaces
    at the head of page 2 — the silent duplication a merge-on-key must
    absorb."""
    page_1 = client.get(f"{URL_POSTS}&start=0&count=10", headers=H).json()["elements"]
    _inject(client, kind="page_drift", scope="/rest/posts", mode="insert")
    page_2 = client.get(f"{URL_POSTS}&start=10&count=10", headers=H).json()["elements"]
    assert page_2[0]["id"] == page_1[-1]["id"]


def test_page_drift_remove_skips(client):
    all_elements = client.get(f"{URL_POSTS}&start=0&count=30", headers=H).json()["elements"]
    _inject(client, kind="page_drift", scope="/rest/posts", mode="remove")
    page_2 = client.get(f"{URL_POSTS}&start=10&count=10", headers=H).json()["elements"]
    assert page_2[0]["id"] == all_elements[11]["id"]  # element 10 is never served


# ── Observability ──────────────────────────────────────────────────────────────


def test_last_query_params_proves_the_time_intervals(client):
    """THE mechanism that lets a consumer prove it SENT its window — a
    pipeline that forgot it would otherwise pass all its other tests."""
    window = "(timeRange:(start:1780617600000,end:1781222400000),timeGranularityType:DAY)"
    client.get(f"{URL_SHARES}&timeIntervals={window}", headers=H)
    admin_state = client.get("/__admin/state", headers=ADMIN).json()
    params = admin_state["last_query_params_by_path"]["/rest/organizationalEntityShareStatistics"]
    assert params["timeIntervals"] == window
    assert params["q"] == "organizationalEntity"


def test_reset_with_new_seed_changes_the_world(client):
    before = client.get(f"{URL_POSTS}&count=1", headers=H).json()["elements"][0]["id"]
    client.post("/__admin/reset", json={"seed": 7}, headers=ADMIN)
    after = client.get(f"{URL_POSTS}&count=1", headers=H).json()["elements"][0]["id"]
    assert before != after
