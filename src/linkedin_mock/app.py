"""FastAPI application assembly.

ONE pipeline per request, in this order: injections → authentication →
version → Rest.li dialect → handler. Failure dispatch precedes authentication
so `auth_reject` can preempt it, and every `/rest` route goes through the same
prelude — there can be no "forgotten" route where failures wouldn't apply.

The auth-BEFORE-version ordering is a DECISION, not a recorded fact: the real
precedence of the two checks isn't documented (cf. registry; the
scripts/compare_real.py probe measures it).

The surface reproduces the versioned API (api.linkedin.com/rest, Community
Management), checked against the official doc — monikers li-lms-2026-06/07:

  • `GET /rest/posts?q=author` (finder), `?ids=List(...)` (batch), `/{urn}`;
  • `GET /rest/organizationalEntityShareStatistics` — lifetime, per-share
    (`shares=`/`ugcPosts=`, zero-stat OMITTED), buckets via `timeIntervals`;
    the per-share + timeIntervals combination is REFUSED by default: the doc
    is explicit that "Time-bound statistics is not supported for specific
    share queries";
  • `GET /rest/organizationalEntityFollowerStatistics` — 7 lifetime
    demographic facets, `followerGains` per bucket (DAY/WEEK/MONTH, start
    MANDATORY);
  • `GET /rest/organizationPageStatistics` — dialect trap: the finder is
    `q=organization` and the parameter `organization`;
  • `GET /rest/organizations/{id}` (+ batch, + q=vanityName),
    `GET /rest/networkSizes/{urn}`;
  • unknown routes render the LinkedIn error envelope, not the FastAPI 404.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import restli, stats
from .auth import verify_bearer
from .errors import (
    MSG_ORG_INACTIVE,
    error,
    error_access_denied,
    error_body,
    error_empty_token,
    error_expired_token,
    error_inactive_version,
    error_invalid_token,
    error_quota,
    error_revoked_token,
    error_unknown_route,
)
from .injection import engine
from .models import (
    ERROR_RESPONSES,
    ElementsEnvelope,
    FollowerStatsElement,
    NetworkSize,
    Organization,
    OrganizationsBatch,
    PageStatsElement,
    Post,
    ShareStatsElement,
)
from .settings import settings
from .state import state
from .versioning import verify_version

_AUTH_REJECTIONS = {
    "empty": error_empty_token,
    "invalid": error_invalid_token,
    "expired": error_expired_token,
    "revoked": error_revoked_token,
}


def _dispatch_injections(path: str, day_rank: int) -> JSONResponse | None:
    """The single dispatch point for failures. Order matters:
    auth_reject preempts real authentication; latency applies even when the
    request ends up succeeding; rate_limit depends on the day's counter;
    status is the outright failure."""
    if (rule := engine.first("auth_reject", path)) is not None and rule.consume():
        return _AUTH_REJECTIONS.get(rule.variant, error_invalid_token)()

    if (rule := engine.first("latency", path)) is not None and rule.consume():
        # A real sleep: the only way to exercise a client timeout.
        time.sleep(rule.seconds)

    if (
        (rule := engine.first("rate_limit", path)) is not None
        and day_rank > rule.after_requests
        and rule.consume()
    ):
        # DAILY quota, no Retry-After — the LinkedIn regime: the counter
        # resets to zero at virtual midnight UTC, not after an announced delay.
        return error_quota()

    if (rule := engine.first("status", path)) is not None and rule.consume():
        return error(rule.status, f"mock: injected {rule.status} on {path}")
    return None


def _transport_checks(request: Request, path: str, params: dict[str, str]) -> JSONResponse | None:
    """Auth, version, Rest.li protocol — the auth-before-version ordering is a
    documented decision (real precedence not attested)."""
    if (refusal := verify_bearer(request)) is not None:
        return refusal

    if (rule := engine.first("version_reject", path)) is not None and rule.consume():
        return error_inactive_version(request.headers.get("Linkedin-Version", "000000"))

    if (refusal := verify_version(request)) is not None:
        return refusal

    if (
        settings.require_restli_2
        and restli.uses_restli_2_syntax(params)
        and request.headers.get("X-Restli-Protocol-Version") != "2.0.0"
    ):
        return error(
            400,
            "Rest.li 2.0 syntax requires the X-Restli-Protocol-Version: 2.0.0 header",
            code="RESTLI_PROTOCOL_VERSION_MISSING",
        )
    return None


def _prelude(request: Request, path: str) -> JSONResponse | None:
    """The pipeline common to every /rest route: failures, auth, version, Rest.li."""
    params = dict(request.query_params)
    state.advance_evolution(engine.now())
    day_rank = engine.observe(path, params, stats.virtual_day().isoformat())

    if (refusal := _dispatch_injections(path, day_rank)) is not None:
        return refusal
    return _transport_checks(request, path, params)


def _finder_guard(
    params: dict[str, str], expected_q: str, entity_param: str
) -> JSONResponse | None:
    """The common guards of the statistics finders: `q` and the entity URN."""
    q = params.get("q")
    if q is None:
        return error(400, "Query parameter 'q' is required on this resource")
    if q != expected_q:
        return error(400, f"Unknown query 'q={q}' on this resource")
    entity = params.get(entity_param)
    if entity is None:
        return error(400, f"Parameter '{entity_param}' is required")
    if restli.URN_ORGANIZATION.match(entity) is None:
        return error(400, f"Invalid urn type in {entity_param}: {entity}", code="INVALID_URN_TYPE")
    if entity != settings.organization_urn:
        return error_access_denied(f"the ADMIN_ONLY VisibilityReduction for {entity}")
    return None


def _invalid_granularity(
    interval: restli.Interval, allowed: tuple[str, ...]
) -> JSONResponse | None:
    if interval.granularity not in allowed:
        return error(
            400,
            f"Invalid timeGranularityType: {interval.granularity!r} "
            f"(expected one of {', '.join(allowed)})",
        )
    if interval.start_ms is None:
        return error(400, "timeIntervals.timeRange.start is required")
    return None


def _default_end(interval: restli.Interval) -> int:
    if interval.end_ms is not None:
        return interval.end_ms
    return int(stats.virtual_now().timestamp() * 1000)


def _paging_echo(params: dict[str, str]) -> tuple[int, int]:
    """Statistics don't paginate; `paging` merely echoes back the parameters
    (behavior of the official examples: paging {count:10, start:0} with every
    element)."""
    pagination = restli.read_pagination(params, default=settings.default_count)
    return pagination if pagination is not None else (0, settings.default_count)


# ─────────────────────────────────────────────────────────────────────────────
#  Application
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="LinkedIn mock", version="0.1.0", docs_url="/docs")
rest = APIRouter(prefix="/rest")


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Unknown routes and methods: the LinkedIn envelope, not the FastAPI 404."""
    if exc.status_code == 404:
        return error_unknown_route(request.url.path)
    if exc.status_code == 405:
        return error(405, f"Method {request.method} not allowed", code="METHOD_NOT_ALLOWED")
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Unauthenticated — it is a probe, not an entity."""
    return {"status": "ok", "service": "linkedin-mock"}


# ── Posts ────────────────────────────────────────────────────────────────────


def _author_guard(params: dict[str, str]) -> JSONResponse | None:
    """The posts finder's guards: `q=author` and the organization's URN."""
    q = params.get("q")
    if q != "author":
        message = (
            "Query parameter 'q' is required on this resource"
            if q is None
            else f"Unknown query 'q={q}' on this resource"
        )
        return error(400, message)
    author = params.get("author")
    if author is None:
        return error(400, "Parameter 'author' is required")
    if restli.URN_ORGANIZATION.match(author) is None:
        return error(400, f"Invalid urn type in author: {author}", code="INVALID_URN_TYPE")
    if author != settings.organization_urn:
        return error_access_denied(f"posts of {author}")
    sort_by = params.get("sortBy", "LAST_MODIFIED")
    if sort_by not in ("LAST_MODIFIED", "CREATED"):
        return error(400, f"Invalid value for sortBy: {sort_by}", code="INVALID_VALUE_FOR_FIELD")
    return None


@rest.get(
    "/posts",
    response_model=ElementsEnvelope[Post],
    responses=ERROR_RESPONSES,
    summary="Finder by author — the organization's posts",
    description=(
        "Finder `q=author&author={urn}`: sort `sortBy=LAST_MODIFIED` (default) "
        "or `CREATED`, descending; pagination `start`/`count` (default 10, "
        "cap 100), end of data = short page. The SAME route serves the "
        "batch get `?ids=List(urn,urn)` — response `{results, statuses, errors}` "
        "per URN, not described by this schema."
    ),
)
def list_posts(request: Request) -> JSONResponse:
    path = "/rest/posts"
    if (refusal := _prelude(request, path)) is not None:
        return refusal
    params = dict(request.query_params)

    if (ids := restli.list_urns(params, "ids")) is not None:
        return _posts_batch(ids)

    if (refusal := _author_guard(params)) is not None:
        return refusal
    sort_by = params.get("sortBy", "LAST_MODIFIED")
    pagination = restli.read_pagination(
        params, default=settings.default_count, cap=settings.posts_count_cap
    )
    if pagination is None:
        return error(400, "Invalid pagination parameters: start/count")
    start, count = pagination

    key = "lastModifiedAt" if sort_by == "LAST_MODIFIED" else "createdAt"
    sorted_posts = sorted(
        state.dataset["posts"], key=lambda p: (int(p[key]), p["id"]), reverse=True
    )

    offset = 0
    rule = engine.first("page_drift", path)
    if rule is not None and start > 0 and rule.consume():
        # A post published between two pages: everything shifts by one — an
        # element gets served twice (insert) or never (remove). The reason
        # merge-on-key exists on the pipeline side.
        offset = -1 if rule.mode == "insert" else 1

    begin = max(0, start + offset)
    return JSONResponse(restli.elements_envelope(sorted_posts[begin : begin + count], start, count))


def _posts_batch(ids: list[str]) -> JSONResponse:
    """`GET /rest/posts?ids=List(...)` — results/statuses/errors per URN."""
    index = {p["id"]: p for p in state.dataset["posts"]}
    results: dict[str, Any] = {}
    errors: dict[str, Any] = {}
    statuses: dict[str, int] = {}
    for urn in ids:
        if urn in index:
            results[urn] = index[urn]
        else:
            statuses[urn] = 404
            errors[urn] = error_body(404, f"Cannot find entity {urn}", code="NOT_FOUND")
    return JSONResponse({"results": results, "statuses": statuses, "errors": errors})


@rest.get(
    "/posts/{post_urn}",
    response_model=Post,
    responses=ERROR_RESPONSES,
    summary="Get a post by URN (URL-encoded)",
)
def get_post(request: Request, post_urn: str) -> JSONResponse:
    if (refusal := _prelude(request, f"/rest/posts/{post_urn}")) is not None:
        return refusal
    if restli.URN_POST.match(post_urn) is None:
        return error(400, f"Invalid urn type in id: {post_urn}", code="INVALID_URN_TYPE")
    for post in state.dataset["posts"]:
        if post["id"] == post_urn:
            return JSONResponse(post)
    return error(404, f"Cannot find entity {post_urn}", code="NOT_FOUND")


# ── Share statistics ─────────────────────────────────────────────────────────


@rest.get(
    "/organizationalEntityShareStatistics",
    response_model=ElementsEnvelope[ShareStatsElement],
    responses=ERROR_RESPONSES,
    summary="Share statistics — lifetime, per-share, or time-bound buckets",
    description=(
        "No parameter: the organization-wide lifetime aggregate (rolling "
        "12-month window on the buckets). `shares=List(...)`/`ugcPosts=List(...)`: "
        "one element per ACTIVE post — posts with no activity are omitted "
        '("can be assumed to have counts of 0"). `timeIntervals=(timeRange:'
        "(start:ms,end:ms),timeGranularityType:DAY|MONTH)`: one element per "
        "bucket. The per-share + timeIntervals combination is refused by "
        "default — documented behavior of the real API."
    ),
)
def share_stats(request: Request) -> JSONResponse:
    if (refusal := _prelude(request, "/rest/organizationalEntityShareStatistics")) is not None:
        return refusal
    params = dict(request.query_params)
    refusal = _finder_guard(params, "organizationalEntity", "organizationalEntity")
    if refusal is not None:
        return refusal

    shares = restli.list_urns(params, "shares")
    ugc = restli.list_urns(params, "ugcPosts")
    urns: list[str] = []
    for values, prefix in ((shares, "urn:li:share:"), (ugc, "urn:li:ugcPost:")):
        for urn in values or []:
            if restli.URN_POST.match(urn) is None or not urn.startswith(prefix):
                return error(400, f"Invalid urn type: {urn}", code="INVALID_URN_TYPE")
            urns.append(urn)

    interval = restli.parse_time_intervals(params)
    start, count = _paging_echo(params)

    if interval is not None and urns and settings.strict_shares_timebound:
        return error(400, "Time-bound statistics is not supported for specific share queries")

    if interval is not None:
        if (refusal := _invalid_granularity(interval, stats.GRANULARITIES_SHARES)) is not None:
            return refusal
        elements = stats.share_bucket_elements(
            interval.start_ms or 0,
            _default_end(interval),
            interval.granularity or "DAY",
            urns=urns or None,
        )
    elif urns:
        elements = stats.share_elements_per_post(urns)
    else:
        elements = [stats.lifetime_share_element()]
    return JSONResponse(restli.elements_envelope(elements, start, count))


# ── Follower statistics ──────────────────────────────────────────────────────


@rest.get(
    "/organizationalEntityFollowerStatistics",
    response_model=ElementsEnvelope[FollowerStatsElement],
    responses=ERROR_RESPONSES,
    summary="Follower statistics — demographics (lifetime) or daily gains",
    description=(
        "Lifetime: the 7 demographic facet families (each covers LESS than "
        "the total — the total lives on /networkSizes). Time-bound "
        "(DAY|WEEK|MONTH, `timeRange.start` MANDATORY): `followerGains` per "
        "bucket, data available from J-365 to J-2 UTC."
    ),
)
def follower_stats(request: Request) -> JSONResponse:
    if (refusal := _prelude(request, "/rest/organizationalEntityFollowerStatistics")) is not None:
        return refusal
    params = dict(request.query_params)
    refusal = _finder_guard(params, "organizationalEntity", "organizationalEntity")
    if refusal is not None:
        return refusal

    interval = restli.parse_time_intervals(params)
    start, count = _paging_echo(params)
    if interval is not None:
        if (refusal := _invalid_granularity(interval, stats.GRANULARITIES_FOLLOWERS)) is not None:
            return refusal
        elements = stats.follower_bucket_elements(
            interval.start_ms or 0,
            _default_end(interval),
            interval.granularity or "DAY",
        )
    else:
        elements = [stats.lifetime_followers_element()]
    return JSONResponse(restli.elements_envelope(elements, start, count))


# ── Page statistics ──────────────────────────────────────────────────────────


@rest.get(
    "/organizationPageStatistics",
    response_model=ElementsEnvelope[PageStatsElement],
    responses=ERROR_RESPONSES,
    summary="Page statistics — views (lifetime) or daily buckets",
    description=(
        "Dialect TRAP: the finder is `q=organization` and the parameter "
        "`organization` — not `organizationalEntity` like the other two. "
        "Lifetime: `totalPageStatistics` (15 view counters, verified "
        "arithmetic) + 6 facets. Time-bound (DAY|MONTH): reduced set of "
        "families, with `uniquePageViews`."
    ),
)
def page_stats(request: Request) -> JSONResponse:
    if (refusal := _prelude(request, "/rest/organizationPageStatistics")) is not None:
        return refusal
    params = dict(request.query_params)
    if (refusal := _finder_guard(params, "organization", "organization")) is not None:
        return refusal

    interval = restli.parse_time_intervals(params)
    start, count = _paging_echo(params)
    if interval is not None:
        if (refusal := _invalid_granularity(interval, stats.GRANULARITIES_PAGE)) is not None:
            return refusal
        elements = stats.page_bucket_elements(
            interval.start_ms or 0,
            _default_end(interval),
            interval.granularity or "DAY",
        )
    else:
        elements = [stats.lifetime_page_element()]
    return JSONResponse(restli.elements_envelope(elements, start, count))


# ── Organizations & network ──────────────────────────────────────────────────


@rest.get(
    "/organizations",
    response_model=OrganizationsBatch,
    responses=ERROR_RESPONSES,
    summary="Batch get (?ids=List) or finder by vanityName",
    description=(
        "Batch `?ids=List(40123456,27056405)`: `statuses` carries the code PER "
        "id (200/403). Finder `?q=vanityName&vanityName=…`: elements/paging "
        "envelope with `total` — one of the few finders that emit it."
    ),
)
def organizations(request: Request) -> JSONResponse:
    if (refusal := _prelude(request, "/rest/organizations")) is not None:
        return refusal
    params = dict(request.query_params)
    organization = state.dataset["organization"]

    if (ids := restli.list_urns(params, "ids")) is not None:
        results: dict[str, Any] = {}
        statuses: dict[str, int] = {}
        errors: dict[str, Any] = {}
        for id_ in ids:
            if id_ == settings.org_id:
                results[id_] = organization
                statuses[id_] = 200
            else:
                statuses[id_] = 403
                errors[id_] = error_body(
                    403,
                    "Viewer don't have permission to the ADMIN_ONLY VisibilityReduction "
                    f"for urn:li:organization:{id_}",
                    service_error_code=100,
                    code="ACCESS_DENIED",
                )
        return JSONResponse({"results": results, "statuses": statuses, "errors": errors})

    q = params.get("q")
    if q != "vanityName":
        return error(400, f"Unknown query 'q={q}' on this resource")
    vanity = params.get("vanityName", "")
    elements = [organization] if vanity == organization["vanityName"] else []
    body = restli.elements_envelope(elements, 0, 10)
    body["paging"]["total"] = len(elements)
    return JSONResponse(body)


@rest.get(
    "/organizations/{org_id}",
    response_model=Organization,
    responses=ERROR_RESPONSES,
    summary="Organization lookup — the credentials smoke test",
    description=(
        "The flat Rest.li entity: `id` is a NUMBER and `$URN` is present. "
        "This is the call the connector makes BEFORE opening its pipeline — "
        "the analogue of BoondManager's current-user."
    ),
)
def get_organization(request: Request, org_id: str) -> JSONResponse:
    if (refusal := _prelude(request, f"/rest/organizations/{org_id}")) is not None:
        return refusal
    if not org_id.isdigit():
        return error(400, f"Invalid organization id: {org_id}")
    if org_id != settings.org_id:
        return error(404, MSG_ORG_INACTIVE.format(org_id=org_id), code="NOT_FOUND")
    return JSONResponse(state.dataset["organization"])


@rest.get(
    "/networkSizes/{entity_urn}",
    response_model=NetworkSize,
    responses=ERROR_RESPONSES,
    summary="firstDegreeSize — THE follower total",
    description=(
        "`?edgeType=COMPANY_FOLLOWED_BY_MEMBER` mandatory. Follower "
        "statistics no LONGER have a total: it lives here."
    ),
)
def network_size(request: Request, entity_urn: str) -> JSONResponse:
    if (refusal := _prelude(request, f"/rest/networkSizes/{entity_urn}")) is not None:
        return refusal
    if request.query_params.get("edgeType") != "COMPANY_FOLLOWED_BY_MEMBER":
        return error(400, "Parameter 'edgeType' is required (COMPANY_FOLLOWED_BY_MEMBER)")
    if restli.URN_ORGANIZATION.match(entity_urn) is None:
        return error(400, f"Invalid urn type: {entity_urn}", code="INVALID_URN_TYPE")
    if entity_urn != settings.organization_urn:
        return error_access_denied(f"the ADMIN_ONLY VisibilityReduction for {entity_urn}")
    return JSONResponse({"firstDegreeSize": stats.total_followers()})


app.include_router(rest)

# The control plane isn't "mounted then forbidden": when it's disabled, the
# surface doesn't exist.
if settings.admin_enabled:
    from .admin import router as admin_router

    app.include_router(admin_router)


# ─────────────────────────────────────────────────────────────────────────────
#  The contract
# ─────────────────────────────────────────────────────────────────────────────


def openapi_contract() -> dict[str, Any]:
    """The OpenAPI contract — the LinkedIn DIALECT, and only that.

    The `/__admin` paths are REMOVED: they are mock affordances, not the
    provider's — and the router is only mounted if
    LINKEDIN_MOCK_ADMIN_ENABLED is true, which would make the contract depend
    on the generation environment.
    """
    spec = app.openapi()
    spec["paths"] = {
        path: op for path, op in spec["paths"].items() if not path.startswith("/__admin")
    }
    _prune_orphan_schemas(spec)
    return spec


def _prune_orphan_schemas(spec: dict[str, Any]) -> None:
    """Removes schemas that no path references any more.

    Without this pruning, the committed contract would contain
    `HTTPValidationError` only when /__admin happened to be mounted at
    generation time — and the anti-drift test would fail depending on the
    environment.
    """
    schemas = spec.get("components", {}).get("schemas", {})
    if not schemas:
        return

    def refs(node: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str):
                    found.add(value.rsplit("/", 1)[-1])
                else:
                    found |= refs(value)
        elif isinstance(node, list):
            for element in node:
                found |= refs(element)
        return found

    kept = refs(spec["paths"])
    to_explore = set(kept)
    while to_explore:
        name = to_explore.pop()
        for next_name in refs(schemas.get(name, {})):
            if next_name not in kept:
                kept.add(next_name)
                to_explore.add(next_name)

    spec["components"]["schemas"] = {n: c for n, c in schemas.items() if n in kept}
