"""Versioning — the `Linkedin-Version: YYYYMM` header, mandatory.

"No unversioned calls": the versioned API NEVER applies the latest version by
default. Both refusals are attested by the official doc:

    absent       → 400 {"code": "VERSION_MISSING", ...}
    out of range → 426 {"code": "NONEXISTENT_VERSION",
                        "message": "Requested version ... is not active"}

The refusal of a MALFORMED version (`foo`, `2024-08`) is a 400 whose exact
body is not attested (cf. docs/UNVERIFIED-FIELDS.md).

The active window [oldest, latest] is configurable by env: that's what allows
repeating a mid-quarter version removal (the `version_reject` injection
scenario is the one-off shortcut for it).
"""

from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import JSONResponse

from .errors import error, error_inactive_version, error_missing_version
from .settings import settings

HEADER_VERSION = "Linkedin-Version"

_VERSION_FORMAT = re.compile(r"^\d{6}$")


def verify_version(request: Request) -> JSONResponse | None:
    """None if the version is present and active, otherwise the exact refusal."""
    version = request.headers.get(HEADER_VERSION)  # case-insensitive lookup
    if version is None:
        return error_missing_version()
    version = version.strip()
    month = version[4:6]
    if not _VERSION_FORMAT.match(version) or not "01" <= month <= "12":
        # Body not attested — plausible message, tracked in the registry.
        return error(400, f"Invalid version {version}", code="INVALID_VERSION")
    if not settings.oldest_active_version <= version <= settings.latest_active_version:
        return error_inactive_version(version)
    return None
