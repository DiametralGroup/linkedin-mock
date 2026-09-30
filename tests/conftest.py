"""Test harness.

Two environment settings, set BEFORE the package import (config is read at
import time):

  • the `/__admin` control plane is mounted — the mount is conditional;
  • the evolution interval is set to 3600 s: no event fires on the wall clock
    during the suite, even on a slow CI. Evolution tests advance time
    EXPLICITLY via /__admin/clock — that's what makes them deterministic.
"""

from __future__ import annotations

import os

os.environ.setdefault("LINKEDIN_MOCK_ADMIN_ENABLED", "true")
os.environ.setdefault("LINKEDIN_MOCK_EVOLUTION_INTERVAL", "3600")

import pytest
from fastapi.testclient import TestClient

import linkedin_mock as mock

ORG_URN = "urn:li:organization:40123456"
ORG_URN_ENC = "urn%3Ali%3Aorganization%3A40123456"

#: The three headers of a well-formed request — the default keyring.
H = {
    "Authorization": "Bearer mock-linkedin-token",
    "Linkedin-Version": "202506",
    "X-Restli-Protocol-Version": "2.0.0",
}
ADMIN = {"X-Mock-Admin-Token": "mock-admin-token"}


@pytest.fixture()
def client():
    """A client on a FRESHLY RESET state — before AND after, so no test hands
    down an injection rule or an evolution event to the next one."""
    c = TestClient(mock.app)
    mock.state.reset()
    yield c
    mock.state.reset()


@pytest.fixture()
def linkedin_state(client):  # noqa: ARG001 — the fixture chains the reset
    """The mock's mutable state, for tests that inspect the dataset."""
    return mock.state


def all_posts(client) -> list[dict]:
    """The full paginated walk of the finder — the journeys' workhorse."""
    posts: list[dict] = []
    start = 0
    while True:
        r = client.get(
            f"/rest/posts?q=author&author={ORG_URN_ENC}&start={start}&count=25", headers=H
        )
        assert r.status_code == 200, r.text
        page = r.json()["elements"]
        posts.extend(page)
        if len(page) < 25:
            return posts
        start += 25
