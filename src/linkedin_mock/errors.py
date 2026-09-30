"""Error envelope — the shape of the LinkedIn dialect.

The error body of the versioned APIs is FLAT (none of the Boond meta):

    {"message": "...", "serviceErrorCode": N, "status": N}
    — form VERIFIED (the "error handling" doc): {"message": "Empty
      oauth2_access_token", "serviceErrorCode": 401, "status": 401}

More recent responses add a constant `code`:

    {"status": 400, "code": "VERSION_MISSING", "message": "A version must be
     present. Please specify a version by adding the Linkedin-Version header."}
    {"status": 426, "code": "NONEXISTENT_VERSION",
     "message": "Requested version 20230101 is not active"}

The exact bodies of 401 invalid/expired/revoked and the serviceErrorCode of
the 429 are NOT attested by the official doc (it documents error *types*, not
the numeric codes) — constants centralized here so a `scripts/compare_real.py`
campaign can fix them in one single place, tracked in
docs/UNVERIFIED-FIELDS.md.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

# ── Attested messages (official doc) ─────────────────────────────────────────

MSG_EMPTY_TOKEN = "Empty oauth2_access_token"
MSG_MISSING_VERSION = (
    "A version must be present. Please specify a version by adding the Linkedin-Version header."
)
MSG_INACTIVE_VERSION = "Requested version {version} is not active"
MSG_QUOTA = "Resource level throttle limit for calls to this resource is reached"
MSG_ORG_INACTIVE = "Organization {org_id} is inactive"

# ── Plausible constants, NOT attested (cf. docs/UNVERIFIED-FIELDS.md) ────────

CODE_INVALID_TOKEN = 65600
CODE_EXPIRED_TOKEN = 65601
CODE_REVOKED_TOKEN = 65604
MSG_INVALID_TOKEN = "Invalid access token"
MSG_EXPIRED_TOKEN = "The token used in the request has expired"
MSG_REVOKED_TOKEN = "The token used in the request has been revoked by the member"
CODE_ACCESS_DENIED = 100  # serviceErrorCode observed on the 403 ACCESS_DENIED


def error_body(
    status_code: int,
    message: str,
    *,
    service_error_code: int | None = None,
    code: str | None = None,
) -> dict[str, Any]:
    """The JSON body alone — for the `errors` entries of batch responses."""
    body: dict[str, Any] = {"message": message}
    if service_error_code is not None:
        body["serviceErrorCode"] = service_error_code
    if code is not None:
        body["code"] = code
    body["status"] = status_code
    return body


def error(
    status_code: int,
    message: str,
    *,
    service_error_code: int | None = None,
    code: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_body(status_code, message, service_error_code=service_error_code, code=code),
        headers=headers or {},
    )


# ── The recurring errors, in their exact form ────────────────────────────────


def error_empty_token() -> JSONResponse:
    """401 with no token — the ONLY auth error form attested word for word.

    Dialect detail: no `code` key, and `serviceErrorCode` is 401 (not a 65xxx
    code) — that's what the official example shows.
    """
    return error(401, MSG_EMPTY_TOKEN, service_error_code=401)


def error_invalid_token() -> JSONResponse:
    return error(
        401, MSG_INVALID_TOKEN, service_error_code=CODE_INVALID_TOKEN, code="INVALID_ACCESS_TOKEN"
    )


def error_expired_token() -> JSONResponse:
    return error(
        401, MSG_EXPIRED_TOKEN, service_error_code=CODE_EXPIRED_TOKEN, code="EXPIRED_ACCESS_TOKEN"
    )


def error_revoked_token() -> JSONResponse:
    return error(
        401, MSG_REVOKED_TOKEN, service_error_code=CODE_REVOKED_TOKEN, code="REVOKED_ACCESS_TOKEN"
    )


def error_missing_version() -> JSONResponse:
    return error(400, MSG_MISSING_VERSION, code="VERSION_MISSING")


def error_inactive_version(version: str) -> JSONResponse:
    return error(426, MSG_INACTIVE_VERSION.format(version=version), code="NONEXISTENT_VERSION")


def error_quota() -> JSONResponse:
    """429 — daily quota. NO Retry-After: LinkedIn doesn't emit one (key
    difference from BoondManager); the client must wait until midnight UTC."""
    return error(429, MSG_QUOTA, service_error_code=429, code="TOO_MANY_REQUESTS")


def error_access_denied(target: str) -> JSONResponse:
    """403 — organization not administered by the token."""
    return error(
        403,
        f"Not enough permissions to access: {target}",
        service_error_code=CODE_ACCESS_DENIED,
        code="ACCESS_DENIED",
    )


def error_unknown_route(path: str) -> JSONResponse:
    """404 Rest.li for a path outside the surface — plausible form, not attested."""
    resource = path.removeprefix("/rest")
    return error(404, f"No root resource defined for path '{resource}'", code="NOT_FOUND")
