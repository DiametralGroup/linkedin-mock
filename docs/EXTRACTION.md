---
type: features
description: >
  The four consumer journeys the mock is designed around — the exact calls the
  insights360 dlt connector makes, with copy-pastable curl. If a journey stops
  working, the downstream pipeline breaks the same way.
sources_of_truth:
  - src/linkedin_mock/app.py
  - tests/test_endpoints.py
review_triggers:
  - contracts/linkedin.openapi.yaml
update_policy: propose
last_verified: 2026-08-02
---

# Extraction journeys

All calls carry the three headers:

```bash
AUTH='Authorization: Bearer mock-linkedin-token'
VERSION='Linkedin-Version: 202506'
RESTLI='X-Restli-Protocol-Version: 2.0.0'
BASE=http://localhost:8012   # compose port; `make run` → 8000
ORG=urn%3Ali%3Aorganization%3A40123456
```

## 1. Credentials smoke test (before opening the pipeline)

```bash
curl -sf -H "$AUTH" -H "$VERSION" -H "$RESTLI" "$BASE/rest/organizations/40123456"
curl -sf -H "$AUTH" -H "$VERSION" -H "$RESTLI" \
  "$BASE/rest/networkSizes/$ORG?edgeType=COMPANY_FOLLOWED_BY_MEMBER"
```

`organizations/{id}` is the analog of BoondManager's `current-user`: cheap,
side-effect-free, and it fails with the exact 401 body the token state
deserves. `networkSizes` is THE follower total — follower statistics no longer
carry one.

## 2. Posts, paged (full re-list every run)

```bash
curl -sf -H "$AUTH" -H "$VERSION" -H "$RESTLI" \
  "$BASE/rest/posts?q=author&author=$ORG&start=0&count=50"
```

- `start += count` until a SHORT page; there is no `paging.total` here.
- Default sort is `LAST_MODIFIED` descending — an edited post resurfaces at
  the top, which is the only way to observe edits (no `updatedSince` exists).
- Merge on `id` downstream: the `page_drift` failure mode exists precisely to
  punish pipelines that trust pagination instead of their merge key.

## 3. Per-post lifetime statistics (the snapshot source)

```bash
# share URNs and ugcPost URNs go to DIFFERENT parameters:
curl -sf -H "$AUTH" -H "$VERSION" -H "$RESTLI" \
  "$BASE/rest/organizationalEntityShareStatistics?q=organizationalEntity&organizationalEntity=$ORG&shares=List(urn%3Ali%3Ashare%3A...,urn%3Ali%3Ashare%3A...)"
curl -sf -H "$AUTH" -H "$VERSION" -H "$RESTLI" \
  "$BASE/rest/organizationalEntityShareStatistics?q=organizationalEntity&organizationalEntity=$ORG&ugcPosts=List(urn%3Ali%3AugcPost%3A...)"
```

- Batch by ~20 URNs; posts with zero activity are **omitted** from `elements`
  ("can be assumed to have counts of 0").
- **Per-post daily series are NOT served by the API** — the documented
  restriction. The consumer journals these lifetime counters as daily
  snapshots and diffs consecutive snapshots downstream.

## 4. Daily buckets (org-level share stats, follower gains, page views)

```bash
TI='timeIntervals=(timeRange:(start:1780617600000,end:1781222400000),timeGranularityType:DAY)'
curl -sf -H "$AUTH" -H "$VERSION" -H "$RESTLI" \
  "$BASE/rest/organizationalEntityShareStatistics?q=organizationalEntity&organizationalEntity=$ORG&$TI"
curl -sf -H "$AUTH" -H "$VERSION" -H "$RESTLI" \
  "$BASE/rest/organizationalEntityFollowerStatistics?q=organizationalEntity&organizationalEntity=$ORG&$TI"
# ⚠ different finder AND different parameter name:
curl -sf -H "$AUTH" -H "$VERSION" -H "$RESTLI" \
  "$BASE/rest/organizationPageStatistics?q=organization&organization=$ORG&$TI"
```

- Windows: share stats live in a **rolling 12-month window**; follower stats
  from J-365 to **J-2** with `timeRange.start` REQUIRED; page stats since page
  creation.
- Buckets are UTC-midnight aligned, start inclusive, end exclusive; daily
  share buckets do NOT carry `uniqueImpressionsCount`.
- Incrementality is date-window based: persist the last loaded day, re-request
  a lookback of a few days (recent buckets get restated as the day fills), and
  merge on `(entity, day)`.

## Rehearsing the failure modes

```bash
ADMIN='X-Mock-Admin-Token: mock-admin-token'
# Daily quota (no Retry-After!) — then cross midnight UTC without sleeping:
curl -s -X POST -H "$ADMIN" "$BASE/__admin/inject" \
  -d '{"kind": "rate_limit", "scope": "/rest/*", "after_requests": 50}'
curl -s -X POST -H "$ADMIN" "$BASE/__admin/clock" -d '{"advance_seconds": 86400}'
# Prove the window was actually SENT:
curl -s -H "$ADMIN" "$BASE/__admin/state" | jq '.last_query_params_by_path'
```
