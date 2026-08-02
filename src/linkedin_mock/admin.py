"""Plan de contrôle `/__admin` — piloter les pannes et le temps par HTTP.

Même architecture que boondmanager-mock : le préfixe est HORS de `/rest` (aucune
collision possible avec un chemin LinkedIn, blocable en bloc par une règle
réseau), et le routeur n'est PAS MONTÉ quand `LINKEDIN_MOCK_ADMIN_ENABLED` est
faux — absent, pas « monté puis interdit ».
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from . import stats
from .errors import erreur
from .injection import VARIANTES_AUTH, engine
from .settings import settings
from .state import state

router = APIRouter(prefix="/__admin", tags=["admin"])


def _guard(token: str | None) -> JSONResponse | None:
    if not token or token != settings.admin_token:
        return erreur(401, "invalid or missing X-Mock-Admin-Token")
    return None


@router.post("/reset")
async def reset(
    request: Request,
    x_mock_admin_token: str | None = Header(default=None),
) -> JSONResponse:
    """Rebuild the dataset and reset all counters.

    Injection rules return to the BASELINE declared by the environment
    (LINKEDIN_MOCK_DAILY_QUOTA), not to empty — see state.MockState.reset.
    """
    if (refus := _guard(x_mock_admin_token)) is not None:
        return refus
    body: dict[str, Any] = {}
    if request.headers.get("content-length") not in (None, "0"):
        body = await request.json()
    seed = body.get("seed", request.query_params.get("seed"))
    state.reset(seed=int(seed) if seed is not None else None)
    return JSONResponse({"status": "reset", "seed": state.seed})


@router.get("/state")
def get_state(x_mock_admin_token: str | None = Header(default=None)) -> JSONResponse:
    """What the mock has seen and what it will do.

    `last_query_params_by_path` is the load-bearing part: it lets a consumer
    PROVE it sent `timeIntervals`, its pagination and its `q` — a pipeline
    that forgot its date window would otherwise pass every test.
    """
    if (refus := _guard(x_mock_admin_token)) is not None:
        return refus
    # L'observation avance le monde : l'état rendu reflète les événements
    # d'évolution devenus dus, même sans requête de données préalable.
    state.avancer_evolution(engine.now())
    return JSONResponse(
        {
            "seed": state.seed,
            "totals": {**state.totals(), "first_degree_size": stats.total_abonnes()},
            "virtual_today": stats.jour_virtuel().isoformat(),
            "request_counts_by_path": dict(engine.request_counts),
            "last_query_params_by_path": dict(engine.last_query_params),
            "injections": engine.snapshot(),
            "clock_offset": engine.clock_offset,
            "evolution": state.evolution.apercu(),
        }
    )


@router.post("/inject")
async def inject(
    request: Request,
    x_mock_admin_token: str | None = Header(default=None),
) -> JSONResponse:
    """Add an injection rule.

    Body: a union discriminated on `kind` —
      {"kind":"rate_limit","scope":"/rest/*","after_requests":50}
      {"kind":"status","scope":"/rest/organizationPageStatistics","status":500,"times":2}
      {"kind":"latency","scope":"*","seconds":2.5,"times":1}
      {"kind":"page_drift","scope":"/rest/posts","mode":"insert"}
      {"kind":"auth_reject","scope":"*","variant":"expired"}
      {"kind":"version_reject","scope":"*","times":1}
    """
    if (refus := _guard(x_mock_admin_token)) is not None:
        return refus
    body = await request.json()
    kind = body.pop("kind", None)
    if kind not in {
        "rate_limit",
        "status",
        "latency",
        "page_drift",
        "auth_reject",
        "version_reject",
    }:
        return erreur(422, f"unknown injection kind: {kind!r}")
    if kind == "auth_reject" and body.get("variant", "invalid") not in VARIANTES_AUTH:
        return erreur(422, f"unknown auth_reject variant: {body.get('variant')!r}")
    try:
        rule = engine.add(kind=kind, **body)
    except TypeError as exc:
        return erreur(422, f"invalid injection payload: {exc}")
    return JSONResponse({"rule_id": rule.id, "kind": rule.kind, "scope": rule.scope})


@router.delete("/inject/{rule_id}")
def delete_inject(
    rule_id: str, x_mock_admin_token: str | None = Header(default=None)
) -> JSONResponse:
    if (refus := _guard(x_mock_admin_token)) is not None:
        return refus
    if not engine.remove(rule_id):
        return erreur(404, f"rule {rule_id} not found")
    return JSONResponse({"status": "removed", "rule_id": rule_id})


@router.post("/inject/clear")
def clear_inject(x_mock_admin_token: str | None = Header(default=None)) -> JSONResponse:
    if (refus := _guard(x_mock_admin_token)) is not None:
        return refus
    engine.clear()
    return JSONResponse({"status": "cleared"})


@router.post("/mutate")
async def mutate(
    request: Request,
    x_mock_admin_token: str | None = Header(default=None),
) -> JSONResponse:
    """Edit one post and push its `lastModifiedAt` ABOVE every other.

    ESSENTIAL to incrementality testing once the mock runs in a container:
    the edited post must surface at the top of the LAST_MODIFIED finder and
    be re-seen by any lastModifiedAt cursor.

    Body: {"post_id": "urn:li:share:7…" | "7…", "commentary": "…"(optional)}
    """
    if (refus := _guard(x_mock_admin_token)) is not None:
        return refus
    body = await request.json()
    cible = str(body.get("post_id", ""))
    for post in state.dataset["posts"]:
        if post["id"] == cible or post["id"].endswith(f":{cible}"):
            if (commentaire := body.get("commentary")) is not None:
                post["commentary"] = str(commentaire)
            # Déterministe : le max des lastModifiedAt + 1 s — pas d'horloge
            # murale, la mutation reste reproductible d'un run à l'autre.
            plafond = max(int(p["lastModifiedAt"]) for p in state.dataset["posts"])
            post["lastModifiedAt"] = plafond + 1000
            post["lifecycleStateInfo"] = {"isEditedByAuthor": True}
            return JSONResponse({"status": "mutated", "id": post["id"]})
    return erreur(404, f"post {cible!r} not found")


@router.post("/delete")
async def delete_post(
    request: Request,
    x_mock_admin_token: str | None = Header(default=None),
) -> JSONResponse:
    """Remove a post from the finder — its HISTORY stays in the aggregates.

    C'est le régime réel : un post supprimé disparaît du listage et de
    `shareStatistics?shares=List(it)` (règle « zéro-stat omis »), mais les
    agrégats organisation gardent son passé. Un pipeline qui merge sans full
    refresh ne peut PAS observer la suppression — c'est le comportement à
    éprouver, pas à masquer.

    Body: {"post_id": "urn:li:share:7…" | "7…"}
    """
    if (refus := _guard(x_mock_admin_token)) is not None:
        return refus
    body = await request.json()
    cible = str(body.get("post_id", ""))
    posts = state.dataset["posts"]
    for index, post in enumerate(posts):
        if post["id"] == cible or post["id"].endswith(f":{cible}"):
            del posts[index]
            return JSONResponse({"status": "deleted", "id": post["id"]})
    return erreur(404, f"post {cible!r} not found")


@router.post("/clock")
async def clock(
    request: Request,
    x_mock_admin_token: str | None = Header(default=None),
) -> JSONResponse:
    """Advance the virtual clock — time windows without `sleep`.

    Fast-forwards PAGE LIFE too: due evolution events are applied immediately,
    each into the UTC day of ITS OWN timestamp — advancing four days fills
    four days of buckets, exactly as if time had really passed. This is what
    makes the daily quota (midnight UTC reset), the rolling 12-month window
    and the J-2 follower ceiling testable without sleeping.
    """
    if (refus := _guard(x_mock_admin_token)) is not None:
        return refus
    body = await request.json()
    engine.clock_offset += float(body.get("advance_seconds", 0))
    state.avancer_evolution(engine.now())
    return JSONResponse(
        {"clock_offset": engine.clock_offset, "virtual_today": stats.jour_virtuel().isoformat()}
    )
