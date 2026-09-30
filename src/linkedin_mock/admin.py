"""`/__admin` control plane — steering failures and time over HTTP.

Same architecture as boondmanager-mock: the prefix is OUTSIDE `/rest` (no
possible collision with a LinkedIn path, blockable in bulk by a network
rule), and the router is NOT MOUNTED when `LINKEDIN_MOCK_ADMIN_ENABLED` is
false — absent, not "mounted then forbidden".
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from . import stats
from .errors import error
from .injection import AUTH_VARIANTS, engine
from .settings import settings
from .state import state

router = APIRouter(prefix="/__admin", tags=["admin"])


def _guard(token: str | None) -> JSONResponse | None:
    if not token or token != settings.admin_token:
        return error(401, "invalid or missing X-Mock-Admin-Token")
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
    if (refusal := _guard(x_mock_admin_token)) is not None:
        return refusal
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
    if (refusal := _guard(x_mock_admin_token)) is not None:
        return refusal
    # Observation advances the world: the rendered state reflects evolution
    # events that have become due, even without a prior data request.
    state.advance_evolution(engine.now())
    return JSONResponse(
        {
            "seed": state.seed,
            "totals": {**state.totals(), "first_degree_size": stats.total_followers()},
            "virtual_today": stats.virtual_day().isoformat(),
            "request_counts_by_path": dict(engine.request_counts),
            "last_query_params_by_path": dict(engine.last_query_params),
            "injections": engine.snapshot(),
            "clock_offset": engine.clock_offset,
            "evolution": state.evolution.overview(),
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
    if (refusal := _guard(x_mock_admin_token)) is not None:
        return refusal
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
        return error(422, f"unknown injection kind: {kind!r}")
    if kind == "auth_reject" and body.get("variant", "invalid") not in AUTH_VARIANTS:
        return error(422, f"unknown auth_reject variant: {body.get('variant')!r}")
    try:
        rule = engine.add(kind=kind, **body)
    except TypeError as exc:
        return error(422, f"invalid injection payload: {exc}")
    return JSONResponse({"rule_id": rule.id, "kind": rule.kind, "scope": rule.scope})


@router.delete("/inject/{rule_id}")
def delete_inject(
    rule_id: str, x_mock_admin_token: str | None = Header(default=None)
) -> JSONResponse:
    if (refusal := _guard(x_mock_admin_token)) is not None:
        return refusal
    if not engine.remove(rule_id):
        return error(404, f"rule {rule_id} not found")
    return JSONResponse({"status": "removed", "rule_id": rule_id})


@router.post("/inject/clear")
def clear_inject(x_mock_admin_token: str | None = Header(default=None)) -> JSONResponse:
    if (refusal := _guard(x_mock_admin_token)) is not None:
        return refusal
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
    if (refusal := _guard(x_mock_admin_token)) is not None:
        return refusal
    body = await request.json()
    target = str(body.get("post_id", ""))
    for post in state.dataset["posts"]:
        if post["id"] == target or post["id"].endswith(f":{target}"):
            if (commentary := body.get("commentary")) is not None:
                post["commentary"] = str(commentary)
            # Deterministic: the max of the lastModifiedAt values + 1s — no
            # wall clock, the mutation stays reproducible from run to run.
            ceiling = max(int(p["lastModifiedAt"]) for p in state.dataset["posts"])
            post["lastModifiedAt"] = ceiling + 1000
            post["lifecycleStateInfo"] = {"isEditedByAuthor": True}
            return JSONResponse({"status": "mutated", "id": post["id"]})
    return error(404, f"post {target!r} not found")


@router.post("/delete")
async def delete_post(
    request: Request,
    x_mock_admin_token: str | None = Header(default=None),
) -> JSONResponse:
    """Remove a post from the finder — its HISTORY stays in the aggregates.

    This is the real regime: a deleted post disappears from the listing and
    from `shareStatistics?shares=List(it)` (the "zero-stat omitted" rule),
    but the organization aggregates keep its past. A pipeline that merges
    without a full refresh CANNOT observe the deletion — that's the behavior
    to exercise, not to hide.

    Body: {"post_id": "urn:li:share:7…" | "7…"}
    """
    if (refusal := _guard(x_mock_admin_token)) is not None:
        return refusal
    body = await request.json()
    target = str(body.get("post_id", ""))
    posts = state.dataset["posts"]
    for index, post in enumerate(posts):
        if post["id"] == target or post["id"].endswith(f":{target}"):
            del posts[index]
            return JSONResponse({"status": "deleted", "id": post["id"]})
    return error(404, f"post {target!r} not found")


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
    if (refusal := _guard(x_mock_admin_token)) is not None:
        return refusal
    body = await request.json()
    engine.clock_offset += float(body.get("advance_seconds", 0))
    state.advance_evolution(engine.now())
    return JSONResponse(
        {"clock_offset": engine.clock_offset, "virtual_today": stats.virtual_day().isoformat()}
    )
