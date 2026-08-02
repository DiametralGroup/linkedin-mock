"""Authentification — Bearer statique, validation réelle d'identifiants factices.

Pas de flux OAuth : le vrai endpoint de jeton (`POST /oauth/v2/accessToken`)
vit sur www.linkedin.com, un AUTRE hôte que api.linkedin.com — le mocker ici
travestirait la topologie qu'un connecteur doit connaître. Le connecteur
consomme un jeton d'accès de ~60 jours depuis ses secrets ; ce que le mock doit
savoir faire, c'est distinguer les états de ce jeton :

    absent / vide   → 401 « Empty oauth2_access_token »   (forme ATTESTÉE)
    inconnu         → 401 « Invalid access token »        (plausible)
    expiré          → 401 « The token … has expired »     (plausible)
    révoqué         → 401 « The token … has been revoked »(plausible)

Les jetons expiré/révoqué sont des valeurs d'env dédiées : un test (ou un
humain avec curl) choisit son scénario en choisissant son jeton.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from .errors import (
    erreur_token_expire,
    erreur_token_invalide,
    erreur_token_revoque,
    erreur_token_vide,
)
from .settings import settings


def extraire_bearer(request: Request) -> str | None:
    """Le jeton du header Authorization, ou None s'il n'y a pas de Bearer."""
    autorisation = request.headers.get("Authorization", "")
    if not autorisation.startswith("Bearer "):
        return None
    return autorisation[len("Bearer ") :].strip()


def verifier_bearer(request: Request) -> JSONResponse | None:
    """None si la requête est authentifiée, sinon la réponse 401 exacte."""
    jeton = extraire_bearer(request)
    if not jeton:
        return erreur_token_vide()
    if jeton == settings.access_token:
        return None
    if jeton == settings.expired_token:
        return erreur_token_expire()
    if jeton == settings.revoked_token:
        return erreur_token_revoque()
    return erreur_token_invalide()
