"""Modèles pydantic — la SOURCE du contrat OpenAPI.

Même mécanique que boondmanager-mock : typer donne d'un coup le contrat, la
documentation /docs et des formes exploitables par les consommateurs — mais
typer pousse à INVENTER des champs. La parade est structurelle :

  • `extra="allow"` partout — le modèle décrit ce qu'on SAIT, pas ce qui EST ;
  • `x-linkedin-confidence` sur tout champ non adossé à un relevé ou à la doc
    officielle (learn.microsoft.com, monikers li-lms-2026-06/07) ;
  • un test échoue si un champ `unverified` n'est pas inscrit dans
    docs/UNVERIFIED-FIELDS.md — l'honnêteté est une contrainte de build.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def unverified(description: str) -> dict[str, Any]:
    """Marque un champ dont le nom ou la forme n'est PAS attesté.

    À utiliser via `json_schema_extra`. Tout champ ainsi marqué DOIT figurer
    dans `docs/UNVERIFIED-FIELDS.md` — `tests/test_contract_is_current.py` le
    vérifie.
    """
    return {"x-linkedin-confidence": "unverified", "x-linkedin-note": description}


def invented(description: str) -> dict[str, Any]:
    """Marque un champ ou un comportement qui n'existe PAS chez LinkedIn."""
    return {"x-linkedin-confidence": "invented", "x-linkedin-note": description}


class Permissif(BaseModel):
    """Base commune : les champs inconnus passent au lieu d'être rejetés.

    Un modèle strict transformerait chaque évolution de l'API réelle en panne
    du mock. Les modèles décrivent ce qui est émis, pas tout ce que LinkedIn
    peut exposer.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)


# ── Enveloppe Rest.li ────────────────────────────────────────────────────────


class Paging(Permissif):
    """Le bloc `paging` — pagination par index `start`/`count`.

    Fin de données = page plus courte que `count`. `total` n'apparaît que sur
    certains finders (organizations) — jamais sur posts ni sur les
    statistiques : un consommateur ne doit PAS s'y adosser.
    """

    start: int
    count: int
    links: list[dict[str, Any]] = Field(
        default_factory=list,
        json_schema_extra=unverified(
            "toujours [] dans chaque exemple officiel ; la forme d'une entrée "
            "non vide n'est documentée nulle part"
        ),
    )
    total: int | None = None


class EnveloppeElements[T](BaseModel):
    """`{"elements": [...], "paging": {...}}` — l'enveloppe de collection."""

    model_config = ConfigDict(extra="allow")

    paging: Paging
    elements: list[T]


# ── Erreurs ──────────────────────────────────────────────────────────────────


class ErreurLinkedIn(Permissif):
    """Le corps d'erreur plat des API versionnées.

    Attesté : `{"message", "serviceErrorCode", "status"}` (401 sans jeton) et
    les variantes à `code` (VERSION_MISSING, NONEXISTENT_VERSION).
    """

    message: str
    serviceErrorCode: int | None = Field(
        default=None,
        json_schema_extra=unverified(
            "les codes numériques des 401 invalid/expired/revoked (65600…) et "
            "du 429 ne sont pas publiés par la doc officielle"
        ),
    )
    code: str | None = Field(default=None, description="Constante symbolique (VERSION_MISSING…).")
    status: int


REPONSES_ERREUR: dict[int | str, dict[str, Any]] = {
    400: {
        "model": ErreurLinkedIn,
        "description": (
            "Paramètre `q` absent ou inconnu, URN malformé, `count` hors "
            "bornes, granularité invalide — ou en-tête `Linkedin-Version` "
            "absent (`code: VERSION_MISSING`)."
        ),
    },
    401: {
        "model": ErreurLinkedIn,
        "description": (
            "Jeton absent (`Empty oauth2_access_token`), invalide, expiré ou "
            "révoqué. Un 401 n'est PAS retryable : c'est un état du jeton."
        ),
    },
    403: {
        "model": ErreurLinkedIn,
        "description": "Organisation non administrée par le jeton (ACCESS_DENIED).",
    },
    404: {"model": ErreurLinkedIn, "description": "Entité inconnue ou organisation inactive."},
    426: {
        "model": ErreurLinkedIn,
        "description": (
            "Version retirée ou inexistante (`NONEXISTENT_VERSION`) — LinkedIn "
            "retire une version ~12 mois après sa publication."
        ),
    },
    429: {
        "model": ErreurLinkedIn,
        "description": (
            "Quota JOURNALIER atteint — remise à zéro à minuit UTC, SANS "
            "en-tête Retry-After (différence clé avec BoondManager)."
        ),
    },
    500: {"model": ErreurLinkedIn, "description": "Panne injectée."},
    503: {"model": ErreurLinkedIn, "description": "Panne transitoire injectée."},
}
