"""Versionnement — l'en-tête `Linkedin-Version: YYYYMM`, obligatoire.

« No unversioned calls » : l'API versionnée n'applique JAMAIS la dernière
version par défaut. Les deux refus sont attestés par la doc officielle :

    absent      → 400 {"code": "VERSION_MISSING", ...}
    hors fenêtre→ 426 {"code": "NONEXISTENT_VERSION",
                       "message": "Requested version ... is not active"}

Le refus d'une version MALFORMÉE (`foo`, `2024-08`) est un 400 dont le corps
exact n'est pas attesté (cf. docs/UNVERIFIED-FIELDS.md).

La fenêtre active [oldest, latest] est configurable par env : c'est ce qui
permet de répéter un retrait de version en cours de trimestre (le scénario
`version_reject` de l'injection en est le raccourci ponctuel).
"""

from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import JSONResponse

from .errors import erreur, erreur_version_absente, erreur_version_inactive
from .settings import settings

HEADER_VERSION = "Linkedin-Version"

_FORMAT_VERSION = re.compile(r"^\d{6}$")


def verifier_version(request: Request) -> JSONResponse | None:
    """None si la version est présente et active, sinon le refus exact."""
    version = request.headers.get(HEADER_VERSION)  # lookup insensible à la casse
    if version is None:
        return erreur_version_absente()
    version = version.strip()
    mois = version[4:6]
    if not _FORMAT_VERSION.match(version) or not "01" <= mois <= "12":
        # Corps non attesté — message plausible, inventorié au registre.
        return erreur(400, f"Invalid version {version}", code="INVALID_VERSION")
    if not settings.oldest_active_version <= version <= settings.latest_active_version:
        return erreur_version_inactive(version)
    return None
