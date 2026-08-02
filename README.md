# linkedin-mock

A faithful mock of the **LinkedIn versioned REST API** (Community Management,
`api.linkedin.com/rest` dialect) serving the organization-page analytics of
**Boréal Conseil** — the same fictional French consultancy as
[boondmanager-mock](https://github.com/LittleBigCode/boondmanager-mock), whose
philosophy and structure this repo mirrors deliberately.

A connector developed against this mock is meant to work unchanged against
`https://api.linkedin.com/rest` in production: same headers, same Rest.li 2.0
syntax, same envelopes, same error bodies, same windows — and the same failure
modes, on demand.

## Start in one command

```bash
# 1. Prebuilt image (once published & public)
docker run --rm -p 8012:8000 -e LINKEDIN_MOCK_ADMIN_ENABLED=true \
    ghcr.io/littlebigcode/linkedin-mock:latest

# 2. Compose (local build)
docker compose up --build

# 3. From source
make bootstrap && make run
```

Then:

```bash
curl http://localhost:8012/health

curl -H "Authorization: Bearer mock-linkedin-token" \
     -H "Linkedin-Version: 202506" \
     -H "X-Restli-Protocol-Version: 2.0.0" \
     "http://localhost:8012/rest/posts?q=author&author=urn%3Ali%3Aorganization%3A40123456&count=3"
```

(`make run` binds port 8000; the compose maps host port **8012** — the
insights360 allocation: boond 8010, entra 8011, linkedin 8012.)

## Default credentials

| What | Value |
|---|---|
| Access token (Bearer) | `mock-linkedin-token` |
| Expired-token rehearsal | `mock-linkedin-token-expired` → 401 expired body |
| Revoked-token rehearsal | `mock-linkedin-token-revoked` → 401 revoked body |
| Organization | `urn:li:organization:40123456` (`boreal-conseil`) |
| Active `Linkedin-Version` window | `202408` … `202607` |
| Admin token (`X-Mock-Admin-Token`) | `mock-admin-token` (plane disabled by default) |

There is **no OAuth flow**: the real token endpoint lives on
`www.linkedin.com` — a different host than the API — and a production
connector consumes a ~60-day token from its secrets; it never runs the
3-legged browser flow at runtime. What the mock does provide is the three 401
states (invalid / expired / revoked) so the client's error handling can be
rehearsed.

## Served surface

| Endpoint | Notes |
|---|---|
| `GET /rest/posts?q=author&author={urn}` | finder, `sortBy=LAST_MODIFIED` (default) / `CREATED`, `start`/`count` (default 10, cap 100), short page = end |
| `GET /rest/posts?ids=List(urn,urn)` | batch get → `{results, statuses, errors}` |
| `GET /rest/posts/{urn}` | single get (URL-encoded URN) |
| `GET /rest/organizationalEntityShareStatistics` | lifetime aggregate; per-share via `shares=List()` / `ugcPosts=List()` (zero-stat posts **omitted**); daily/monthly buckets via `timeIntervals`; rolling 12-month window |
| `GET /rest/organizationalEntityFollowerStatistics` | lifetime = 7 demographic facet families; time-bound = `followerGains` (DAY/WEEK/MONTH, `timeRange.start` required, data J-365 → J-2) |
| `GET /rest/organizationPageStatistics?q=organization` | dialect trap: finder `q=organization`, param `organization`; 15 view counters lifetime, reduced set + `uniquePageViews` daily |
| `GET /rest/organizations/{id}` (+ `?ids=List`, `?q=vanityName`) | org lookup — the credentials smoke test; `id` is a **number**, `$URN` present |
| `GET /rest/networkSizes/{urn}?edgeType=COMPANY_FOLLOWED_BY_MEMBER` | `firstDegreeSize` — THE follower total |
| `GET /health` | unauthenticated probe |
| `/__admin/*` | control plane — only mounted when `LINKEDIN_MOCK_ADMIN_ENABLED=true` |

**`shares` + `timeIntervals` combined is refused by default** (HTTP 400):
the official docs state *"Time-bound statistics is not supported for specific
share queries"*. Per-post daily series are therefore a consumer-side concern —
daily snapshots of per-post lifetime counters, diffed downstream. Set
`LINKEDIN_MOCK_STRICT_SHARES_TIMEBOUND=false` to serve the combination anyway
for exploration.

## The reproduced dialect

| Aspect | Behaviour |
|---|---|
| Auth | `Authorization: Bearer` — missing → the attested `{"message": "Empty oauth2_access_token", "serviceErrorCode": 401, "status": 401}`; invalid/expired/revoked variants (bodies plausible, see `docs/UNVERIFIED-FIELDS.md`) |
| Versioning | `Linkedin-Version: YYYYMM` required — missing → 400 `VERSION_MISSING`; outside the active window → 426 `NONEXISTENT_VERSION`; **no unversioned calls** |
| Rest.li 2.0 | `List(...)`, `(timeRange:(start:ms,end:ms),timeGranularityType:DAY)`, URL-encoded URNs; raw **and** fully-encoded forms accepted; protocol-1.0 dotted/indexed forms too; 2.0 syntax without `X-Restli-Protocol-Version: 2.0.0` → 400 |
| Envelope | `{"elements": [...], "paging": {start, count, links: []}}`; `paging.total` only on the organizations finder; statistics endpoints do **not** paginate |
| Engagement | `(clickCount + likeCount + commentCount + shareCount) / impressionCount`, recomputed at serialization — formula verified numerically on the official examples |
| Windows | share statistics: rolling 12 months; follower statistics: J-365 → J-2, `timeRange.start` required; buckets aligned to UTC midnight, start inclusive / end exclusive |
| Daily buckets | omit `uniqueImpressionsCount` (official example is inconsistent — see registry) |
| Rate limiting | **daily quota, reset at midnight UTC (virtual), no `Retry-After` header** — the key difference from BoondManager; enable with `LINKEDIN_MOCK_DAILY_QUOTA` |
| Errors | flat bodies `{message, serviceErrorCode?, code?, status}`; unknown `/rest/*` routes wrapped in the LinkedIn shape, not FastAPI's |

## The dataset

One seeded, deterministic world (`LINKEDIN_MOCK_SEED`, default 42), anchored at
**2026-07-15** with statistics through **J-2 (2026-07-13)** and a
`lastModifiedAt` ceiling at 2026-07-12 — a cursor set after the ceiling must
see zero base posts, which is what makes incremental tests meaningful:

- **72 posts over ~24 months** (~85% `urn:li:share:`, ~15% `urn:li:ugcPost:`),
  French ESN flavor: recruiting (`{hashtag|\#|WeAreHiring}`), client case
  studies with `@[…](urn)` mentions, events, partnerships, blog articles,
  agency life; a few reshares and edited posts;
- **per-post daily series** with realistic decay (peak at D0-D2, exponential
  tail, one deterministic viral outlier) — lifetime counters are **sums of the
  daily buckets**, so `sum(daily) == lifetime` holds by construction;
- **follower gains** daily (spikes on posting days, occasional negative days,
  two paid-campaign windows) — `networkSizes.firstDegreeSize` = base + Σ gains;
- **page views** daily, correlated with posting activity, careers/jobs spikes
  after recruiting posts, device/surface arithmetic verified
  (`all = desktop + mobile = overview + careers`, `careers = jobs + lifeAt`);
- **demographics** as fixed shares materialized against the CURRENT total —
  each facet covers less than the total (members without the attribute are
  absent, as in the real API); `associationType` lists only the 34 employees.

## Time evolution

The page **lives**: one scripted event every `LINKEDIN_MOCK_EVOLUTION_INTERVAL`
seconds (default 60) — today's stats growth on recent posts, a fresh post, the
day's follower gains, an edit of an old post (the `lastModifiedAt` cursor
rehearsal), a viral spike. The sequence is fully deterministic per seed;
`POST /__admin/clock {"advance_seconds": 345600}` fast-forwards four days and
each event lands in the UTC day of its own timestamp — four days of buckets
appear, exactly as if time had passed. Set `LINKEDIN_MOCK_EVOLUTION=false` for
a byte-stable dataset.

## Failure modes (`/__admin/inject`)

| Kind | What it rehearses |
|---|---|
| `rate_limit` | the daily quota: 429 after `after_requests` calls in the current **virtual UTC day**, counter reset at virtual midnight, **no Retry-After** |
| `status` | blunt 5xx (transient with `times`, persistent without) |
| `latency` | slow responses — client timeout rehearsal |
| `auth_reject` | 401 preempting real auth; `variant`: `empty` / `invalid` / `expired` / `revoked` |
| `version_reject` | 426 mid-quarter — a version sunset without touching the window |
| `page_drift` | start/count slice shifted on `/rest/posts`: a post published between two pages duplicates (or skips) an element — why pipelines merge on key |

Plus `POST /__admin/reset {"seed": …}`, `GET /__admin/state` (request counts,
**`last_query_params_by_path`** — the proof a consumer actually SENT its
`timeIntervals`), `POST /__admin/mutate` (edit a post, pushes its
`lastModifiedAt` above every other), `POST /__admin/delete` (the post leaves
the finder, its history stays in the org aggregates), `POST /__admin/clock`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `LINKEDIN_MOCK_ACCESS_TOKEN` | `mock-linkedin-token` | the accepted Bearer |
| `LINKEDIN_MOCK_EXPIRED_TOKEN` / `_REVOKED_TOKEN` | `…-expired` / `…-revoked` | 401 rehearsal tokens |
| `LINKEDIN_MOCK_ORG_ID` | `40123456` | the administered organization |
| `LINKEDIN_MOCK_SEED` | `42` | dataset seed |
| `LINKEDIN_MOCK_OLDEST_ACTIVE_VERSION` / `_LATEST_ACTIVE_VERSION` | `202408` / `202607` | accepted `Linkedin-Version` window (keep aligned with prod reality) |
| `LINKEDIN_MOCK_REQUIRE_RESTLI_2` | `true` | 2.0 syntax without the protocol header → 400 |
| `LINKEDIN_MOCK_STRICT_SHARES_TIMEBOUND` | `true` | refuse `shares`+`timeIntervals` (documented real behaviour) |
| `LINKEDIN_MOCK_DAILY_QUOTA` | `0` (off) | baseline daily quota per `/rest/*` path |
| `LINKEDIN_MOCK_ADMIN_ENABLED` / `_ADMIN_TOKEN` | `false` / `mock-admin-token` | control plane |
| `LINKEDIN_MOCK_EVOLUTION` / `_EVOLUTION_INTERVAL` | `true` / `60` | page life cadence |
| `LINKEDIN_MOCK_HOST` / `_PORT` | `0.0.0.0` / `8000` | uvicorn bind |

## Development

```bash
make bootstrap   # uv sync
make test        # pytest
make lint        # ruff + mypy --strict
make contract    # regenerate contracts/linkedin.openapi.yaml — REVIEW the diff
```

The OpenAPI contract is **committed** and a test fails if it drifts from the
application. Every response field not backed by official documentation carries
`x-linkedin-confidence: unverified` and MUST be listed in
`docs/UNVERIFIED-FIELDS.md` — another test enforces it. `scripts/compare_real.py`
replays GET-only probes against the real API (real token required) to settle
the registry entries — the `shares`+`timeIntervals` combination first.

Version `0.1.0` is bumped in lockstep in: `pyproject.toml`,
`src/linkedin_mock/__init__.py`, `app.py` (`FastAPI(version=…)`),
`docker-compose.yml` — and the committed contract embeds it (`make contract`).
