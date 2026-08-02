"""Enveloppe d'erreur — la forme du dialecte LinkedIn.

Le corps d'erreur des API versionnées est PLAT (rien du meta Boond) :

    {"message": "...", "serviceErrorCode": N, "status": N}
    — forme VÉRIFIÉE (doc « error handling ») : {"message": "Empty
      oauth2_access_token", "serviceErrorCode": 401, "status": 401}

Les réponses plus récentes ajoutent une constante `code` :

    {"status": 400, "code": "VERSION_MISSING", "message": "A version must be
     present. Please specify a version by adding the Linkedin-Version header."}
    {"status": 426, "code": "NONEXISTENT_VERSION",
     "message": "Requested version 20230101 is not active"}

Les corps exacts des 401 invalid/expired/revoked et le serviceErrorCode du 429
ne sont PAS attestés par la doc officielle (elle documente les *types*
d'erreur, pas les codes numériques) — constantes centralisées ici pour qu'une
campagne `scripts/compare_real.py` les corrige en un seul endroit, et
inventaire tenu dans docs/UNVERIFIED-FIELDS.md.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

# ── Messages attestés (doc officielle) ───────────────────────────────────────

MSG_TOKEN_VIDE = "Empty oauth2_access_token"
MSG_VERSION_ABSENTE = (
    "A version must be present. Please specify a version by adding the Linkedin-Version header."
)
MSG_VERSION_INACTIVE = "Requested version {version} is not active"
MSG_QUOTA = "Resource level throttle limit for calls to this resource is reached"
MSG_ORG_INACTIVE = "Organization {org_id} is inactive"

# ── Constantes plausibles, NON attestées (cf. docs/UNVERIFIED-FIELDS.md) ─────

CODE_TOKEN_INVALIDE = 65600
CODE_TOKEN_EXPIRE = 65601
CODE_TOKEN_REVOQUE = 65604
MSG_TOKEN_INVALIDE = "Invalid access token"
MSG_TOKEN_EXPIRE = "The token used in the request has expired"
MSG_TOKEN_REVOQUE = "The token used in the request has been revoked by the member"
CODE_ACCES_REFUSE = 100  # serviceErrorCode observé du 403 ACCESS_DENIED


def corps_erreur(
    status_code: int,
    message: str,
    *,
    service_error_code: int | None = None,
    code: str | None = None,
) -> dict[str, Any]:
    """Le corps JSON seul — pour les entrées `errors` des réponses batch."""
    corps: dict[str, Any] = {"message": message}
    if service_error_code is not None:
        corps["serviceErrorCode"] = service_error_code
    if code is not None:
        corps["code"] = code
    corps["status"] = status_code
    return corps


def erreur(
    status_code: int,
    message: str,
    *,
    service_error_code: int | None = None,
    code: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=corps_erreur(
            status_code, message, service_error_code=service_error_code, code=code
        ),
        headers=headers or {},
    )


# ── Les erreurs récurrentes, sous leur forme exacte ──────────────────────────


def erreur_token_vide() -> JSONResponse:
    """401 sans jeton — la SEULE forme d'erreur d'auth attestée mot pour mot.

    Détail du dialecte : pas de clé `code`, et `serviceErrorCode` vaut 401
    (pas un code 65xxx) — c'est ce que montre l'exemple officiel.
    """
    return erreur(401, MSG_TOKEN_VIDE, service_error_code=401)


def erreur_token_invalide() -> JSONResponse:
    return erreur(
        401, MSG_TOKEN_INVALIDE, service_error_code=CODE_TOKEN_INVALIDE, code="INVALID_ACCESS_TOKEN"
    )


def erreur_token_expire() -> JSONResponse:
    return erreur(
        401, MSG_TOKEN_EXPIRE, service_error_code=CODE_TOKEN_EXPIRE, code="EXPIRED_ACCESS_TOKEN"
    )


def erreur_token_revoque() -> JSONResponse:
    return erreur(
        401, MSG_TOKEN_REVOQUE, service_error_code=CODE_TOKEN_REVOQUE, code="REVOKED_ACCESS_TOKEN"
    )


def erreur_version_absente() -> JSONResponse:
    return erreur(400, MSG_VERSION_ABSENTE, code="VERSION_MISSING")


def erreur_version_inactive(version: str) -> JSONResponse:
    return erreur(426, MSG_VERSION_INACTIVE.format(version=version), code="NONEXISTENT_VERSION")


def erreur_quota() -> JSONResponse:
    """429 — quota journalier. PAS de Retry-After : LinkedIn n'en émet pas
    (différence clé avec BoondManager) ; le client doit attendre minuit UTC."""
    return erreur(429, MSG_QUOTA, service_error_code=429, code="TOO_MANY_REQUESTS")


def erreur_acces_refuse(cible: str) -> JSONResponse:
    """403 — organisation non administrée par le jeton."""
    return erreur(
        403,
        f"Not enough permissions to access: {cible}",
        service_error_code=CODE_ACCES_REFUSE,
        code="ACCESS_DENIED",
    )


def erreur_route_inconnue(chemin: str) -> JSONResponse:
    """404 Rest.li pour un chemin hors surface — forme plausible, non attestée."""
    ressource = chemin.removeprefix("/rest")
    return erreur(404, f"No root resource defined for path '{ressource}'", code="NOT_FOUND")
