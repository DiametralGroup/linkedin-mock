---
type: reference
description: >
  Registry of every field, value or behaviour served by the mock that is NOT
  backed by official documentation or a live probe. Enforced by
  tests/test_contract_is_current.py — a field marked `unverified` in the
  contract MUST appear here.
sources_of_truth:
  - src/linkedin_mock/models/common.py
  - src/linkedin_mock/models/entities.py
  - src/linkedin_mock/errors.py
review_triggers:
  - contracts/linkedin.openapi.yaml
  - scripts/compare_real.py
update_policy: propose
last_verified: 2026-08-02
---

# Unverified fields & behaviours

Everything below is **plausible, not proven**. The official docs
(learn.microsoft.com, Community Management, monikers li-lms-2026-06/07) either
omit it or contradict themselves about it. `scripts/compare_real.py` replays
GET-only probes against the real API (real token required) — each entry names
what would settle it. Constants live in ONE place (`errors.py`, `models/`), so
a probe campaign fixes them in one diff.

## Schema fields (marked `x-linkedin-confidence: unverified` in the contract)

| Field | Where | Doubt | To settle it |
|---|---|---|---|
| `links` | `Paging` | Every official example shows `[]`; the shape of a non-empty entry is documented nowhere. The mock always serves `[]` — consumers must page by start/count arithmetic and stop on short pages. | Walk a >10-element real collection and inspect `paging.links`. |
| `content` | `Post` | The official finder example shows `content: {}` on one text post while other examples omit the key entirely — the emission rule is not documented. The mock serves BOTH variants. | List real posts; observe when `content` is `{}` vs absent. |
| `uniquePageViews` | `VuesPage` | Present in time-bound pageStatistics examples, but the exact subset of view families carrying it is inconsistent between examples. The mock serves it on all five daily families. | One daily-bucket probe on the real page. |
| `serviceErrorCode` | `ErreurLinkedIn` | Only the empty-token 401 body is attested verbatim (`serviceErrorCode: 401`). The 65600/65601/65604 codes and messages for invalid/expired/revoked, and the 429 body, are community-observed, not documented. | Probe with a forged, an expired and a revoked token; exhaust a quota. |
| `geo` | `FacetteAbonnes` | The integer semantics of `urn:li:geo:…` values are plausible (105015875 = France appears in samples) but not attested segment by segment. | Fetch the real lifetime follower statistics once. |
| `function` | `FacetteAbonnes` | Same doubt for `urn:li:function:…` integers. | Same probe. |
| `industry` | `FacetteAbonnes` | Same doubt for `urn:li:industry:…` integers. | Same probe. |
| `seniority` | `FacetteAbonnes` | Same doubt for `urn:li:seniority:…` integers. | Same probe. |

## Behaviours (not expressible as schema markers)

| Behaviour | Mock's choice | Doubt / to settle |
|---|---|---|
| `shares`/`ugcPosts` + `timeIntervals` combined | **400** by default (`LINKEDIN_MOCK_STRICT_SHARES_TIMEBOUND=true`), message taken from the doc sentence "Time-bound statistics is not supported for specific share queries". | The doc states the restriction but not the HTTP outcome — the real API may 400, ignore the share list, or serve something. **Top-priority probe** (`compare_real.py` runs it first): the insights360 per-post-daily strategy (snapshots) was chosen assuming the restriction holds. |
| Auth checked BEFORE version | A request with neither token nor version gets the 401. | Real precedence unknown. Probe: bare request, observe which error wins. |
| Missing/unknown `q` | 400 with `"Query parameter 'q' is required on this resource"` / `"Unknown query 'q=…'"`. | Real messages unknown (Rest.li usually names the finder). |
| Malformed `Linkedin-Version` | 400 `INVALID_VERSION` "Invalid version {v}". | Only missing (400 VERSION_MISSING) and sunset (426) are attested. |
| `count` > 100 on the posts finder | 400. | The doc states the max; the rejection body is not shown. It may silently cap instead — silently-capped pagination skips records, which is why the mock refuses loudly. |
| Unknown `/rest/*` route | 404 `"No root resource defined for path '/…'"`. | Rest.li-typical message, not attested for this host. |
| Author of another organization | 403 ACCESS_DENIED. | Could be an empty result set instead. |
| 2.0 syntax without `X-Restli-Protocol-Version: 2.0.0` | 400 (mock guard, `LINKEDIN_MOCK_REQUIRE_RESTLI_2`). | The real API would parse it as protocol 1.0 and likely fail differently; the guard trains connectors to always send the header. |
| WEEK bucket alignment (follower stats) | ISO Monday. | Not documented. |
| pageStatistics `timeRange` bound semantics | start inclusive / end exclusive, like share statistics. | The pageStatistics parameter table says the opposite (start exclusive / end inclusive) — almost certainly a docs bug, every sample shows plain UTC day buckets. |
| pageStatistics historical window | Served since page creation (no window). | No window is documented — but none being documented is not proof none exists. |
| Rolling-window edges (shareStatistics) | Requested ranges preceding J-365 are CLAMPED; in-window part served. | Real edge behaviour (clamp vs empty vs error) unverified. |
| Zero/empty daily buckets | Contiguous buckets served with zero counters for days without activity. | Real API may omit empty buckets instead. |
| Post id numbering | 19-digit, strictly increasing with publication time. | Plausible (snowflake-style), not documented. |
| Batch posts `statuses` | Populated only for failed URNs (successes live in `results`); the organizations batch populates it for ALL ids (200/403) — each mirrors its own doc sample. | The general rule is not documented. |
| Org-lifetime `uniqueImpressionsCount` | Sum of per-post lifetime uniques (cross-post overlap ignored). | Real de-duplication scope unknown. |
