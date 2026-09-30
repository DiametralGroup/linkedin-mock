"""Authentication — static Bearer, real validation of fake credentials.

No OAuth flow: the real token endpoint (`POST /oauth/v2/accessToken`) lives on
www.linkedin.com, a DIFFERENT host from api.linkedin.com — mocking it here
would misrepresent the topology a connector must know. The connector consumes
a ~60-day access token from its secrets; what the mock needs to know how to do
is distinguish the states of that token:

    absent / empty  → 401 "Empty oauth2_access_token"   (ATTESTED form)
    unknown         → 401 "Invalid access token"         (plausible)
    expired         → 401 "The token … has expired"      (plausible)
    revoked         → 401 "The token … has been revoked" (plausible)

The expired/revoked tokens are dedicated env values: a test (or a human with
curl) picks its scenario by picking its token.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from .errors import (
    error_empty_token,
    error_expired_token,
    error_invalid_token,
    error_revoked_token,
)
from .settings import settings


def extract_bearer(request: Request) -> str | None:
    """The token from the Authorization header, or None if there's no Bearer."""
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    return authorization[len("Bearer ") :].strip()


def verify_bearer(request: Request) -> JSONResponse | None:
    """None if the request is authenticated, otherwise the exact 401 response."""
    token = extract_bearer(request)
    if not token:
        return error_empty_token()
    if token == settings.access_token:
        return None
    if token == settings.expired_token:
        return error_expired_token()
    if token == settings.revoked_token:
        return error_revoked_token()
    return error_invalid_token()
