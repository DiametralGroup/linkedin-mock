"""Configuration — everything via environment variables, no file.

Same rule as boondmanager-mock: variables are read at import time into an
object re-read by `reload()`, because that's the only mechanism that works
identically in docker compose, in a Kubernetes Deployment and in a Tekton
sidecar — and because tests must be able to change them without reloading the
module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """Configuration state, hot-reloaded by `reload()`."""

    # ── Authentication ────────────────────────────────────────────────────────
    # A static Bearer, no OAuth flow: the real token endpoint lives on
    # www.linkedin.com (a DIFFERENT host from the API) and the connector
    # consumes a 60-day token from its secrets — it never runs the 3-legged
    # flow at runtime. The expired/revoked variants let client-side 401
    # error-handling behavior be exercised repeatedly.
    access_token: str = "mock-linkedin-token"
    expired_token: str = "mock-linkedin-token-expired"
    revoked_token: str = "mock-linkedin-token-revoked"

    # ── Organization served ───────────────────────────────────────────────────
    # The numeric id of the "Boréal Conseil" page (urn:li:organization:{id}).
    org_id: str = "40123456"

    seed: int = 42

    # ── LinkedIn versioning ───────────────────────────────────────────────────
    # The real API requires `Linkedin-Version: YYYYMM` and retires a version
    # ~12 months after its release. The accepted window is configurable so the
    # mock can repeat a version retirement (426) without a new image.
    oldest_active_version: str = "202408"
    latest_active_version: str = "202607"

    # Rest.li 2.0: List()/(timeRange:...) syntax without the
    # X-Restli-Protocol-Version: 2.0.0 header → 400. Real behavior NOT
    # attested (cf. docs/UNVERIFIED-FIELDS.md) — the goal is to train the
    # connector to always send the header.
    require_restli_2: bool = True

    # "Time-bound statistics is not supported for specific share queries" —
    # the official doc is explicit. true (default): the shares+timeIntervals
    # combination → explicit 400. false: the mock serves it anyway
    # (exploration), knowing the real API probably won't.
    strict_shares_timebound: bool = True

    # Daily application-level quota (0 = disabled): past N requests per
    # VIRTUAL UTC day and per /rest/* path, 429 without Retry-After — the real
    # LinkedIn regime (midnight UTC reset, unpublished quotas).
    daily_quota: int = 0

    # /__admin control plane. Closed by default: it only makes sense in tests.
    admin_enabled: bool = False
    admin_token: str = "mock-admin-token"

    # ── Time evolution (incremental extraction) ──────────────────────────────
    # The page LIVES: a scripted event every `evolution_interval` seconds
    # (daily stats, new post, follower gains, edit…).
    # false — or an interval of 0 — freezes the dataset byte for byte.
    evolution_enabled: bool = True
    evolution_interval: float = 60.0

    # Rest.li pagination: start/count, default 10, posts cap 100.
    default_count: int = 10
    posts_count_cap: int = 100

    def reload(self) -> None:
        self.access_token = os.environ.get("LINKEDIN_MOCK_ACCESS_TOKEN", "mock-linkedin-token")
        self.expired_token = os.environ.get(
            "LINKEDIN_MOCK_EXPIRED_TOKEN", "mock-linkedin-token-expired"
        )
        self.revoked_token = os.environ.get(
            "LINKEDIN_MOCK_REVOKED_TOKEN", "mock-linkedin-token-revoked"
        )
        self.org_id = os.environ.get("LINKEDIN_MOCK_ORG_ID", "40123456")
        self.seed = int(os.environ.get("LINKEDIN_MOCK_SEED", "42"))
        self.oldest_active_version = os.environ.get("LINKEDIN_MOCK_OLDEST_ACTIVE_VERSION", "202408")
        self.latest_active_version = os.environ.get("LINKEDIN_MOCK_LATEST_ACTIVE_VERSION", "202607")
        self.require_restli_2 = _flag("LINKEDIN_MOCK_REQUIRE_RESTLI_2", True)
        self.strict_shares_timebound = _flag("LINKEDIN_MOCK_STRICT_SHARES_TIMEBOUND", True)
        self.daily_quota = int(os.environ.get("LINKEDIN_MOCK_DAILY_QUOTA", "0"))
        self.admin_enabled = _flag("LINKEDIN_MOCK_ADMIN_ENABLED", False)
        self.admin_token = os.environ.get("LINKEDIN_MOCK_ADMIN_TOKEN", "mock-admin-token")
        self.evolution_enabled = _flag("LINKEDIN_MOCK_EVOLUTION", True)
        self.evolution_interval = float(os.environ.get("LINKEDIN_MOCK_EVOLUTION_INTERVAL", "60"))

    @property
    def organization_urn(self) -> str:
        return f"urn:li:organization:{self.org_id}"


settings = Settings()
settings.reload()
