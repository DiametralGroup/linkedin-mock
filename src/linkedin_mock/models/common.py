"""Pydantic models — the SOURCE of the OpenAPI contract.

Same mechanics as boondmanager-mock: typing gives the contract, the /docs
documentation and consumer-usable shapes all at once — but typing pushes
toward INVENTING fields. The safeguard is structural:

  • `extra="allow"` everywhere — the model describes what we KNOW, not what IS;
  • `x-linkedin-confidence` on every field not backed by a recorded trace or
    the official doc (learn.microsoft.com, monikers li-lms-2026-06/07);
  • a test fails if an `unverified` field isn't listed in
    docs/UNVERIFIED-FIELDS.md — honesty is a build constraint.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def unverified(description: str) -> dict[str, Any]:
    """Marks a field whose name or shape is NOT attested.

    Used via `json_schema_extra`. Any field marked this way MUST appear in
    `docs/UNVERIFIED-FIELDS.md` — `tests/test_contract_is_current.py`
    verifies it.
    """
    return {"x-linkedin-confidence": "unverified", "x-linkedin-note": description}


def invented(description: str) -> dict[str, Any]:
    """Marks a field or behavior that does NOT exist at LinkedIn."""
    return {"x-linkedin-confidence": "invented", "x-linkedin-note": description}


class Permissive(BaseModel):
    """Common base: unknown fields pass through instead of being rejected.

    A strict model would turn every evolution of the real API into a mock
    failure. Models describe what is emitted, not everything LinkedIn can
    expose.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)


# ── Rest.li envelope ─────────────────────────────────────────────────────────


class Paging(Permissive):
    """The `paging` block — index pagination via `start`/`count`.

    End of data = a page shorter than `count`. `total` only appears on
    certain finders (organizations) — never on posts or on the statistics: a
    consumer must NOT rely on it.
    """

    start: int
    count: int
    links: list[dict[str, Any]] = Field(
        default_factory=list,
        json_schema_extra=unverified(
            "always [] in every official example; the shape of a non-empty "
            "entry is documented nowhere"
        ),
    )
    total: int | None = None


class ElementsEnvelope[T](BaseModel):
    """`{"elements": [...], "paging": {...}}` — the collection envelope."""

    model_config = ConfigDict(extra="allow")

    paging: Paging
    elements: list[T]


# ── Errors ───────────────────────────────────────────────────────────────────


class LinkedInError(Permissive):
    """The flat error body of the versioned APIs.

    Attested: `{"message", "serviceErrorCode", "status"}` (401 with no
    token) and the `code` variants (VERSION_MISSING, NONEXISTENT_VERSION).
    """

    message: str
    serviceErrorCode: int | None = Field(
        default=None,
        json_schema_extra=unverified(
            "the numeric codes for the 401 invalid/expired/revoked (65600…) "
            "and for the 429 aren't published by the official doc"
        ),
    )
    code: str | None = Field(default=None, description="Symbolic constant (VERSION_MISSING…).")
    status: int


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {
        "model": LinkedInError,
        "description": (
            "Missing or unknown `q` parameter, malformed URN, `count` out of "
            "bounds, invalid granularity — or missing `Linkedin-Version` "
            "header (`code: VERSION_MISSING`)."
        ),
    },
    401: {
        "model": LinkedInError,
        "description": (
            "Missing (`Empty oauth2_access_token`), invalid, expired or "
            "revoked token. A 401 is NOT retryable: it's a token state."
        ),
    },
    403: {
        "model": LinkedInError,
        "description": "Organization not administered by the token (ACCESS_DENIED).",
    },
    404: {"model": LinkedInError, "description": "Unknown entity or inactive organization."},
    426: {
        "model": LinkedInError,
        "description": (
            "Retired or nonexistent version (`NONEXISTENT_VERSION`) — LinkedIn "
            "retires a version ~12 months after its release."
        ),
    },
    429: {
        "model": LinkedInError,
        "description": (
            "DAILY quota reached — reset at midnight UTC, WITHOUT a "
            "Retry-After header (key difference from BoondManager)."
        ),
    },
    500: {"model": LinkedInError, "description": "Injected failure."},
    503: {"model": LinkedInError, "description": "Injected transient failure."},
}
