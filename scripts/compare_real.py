"""Structural GET-only probe against the REAL LinkedIn API.

Goal: settle the entries in docs/UNVERIFIED-FIELDS.md — not extract data.
Each probe fires ONE GET, records the status and the SHAPE (keys, types),
and confronts it with what the mock serves. No write, no mutation, no POST.

Usage:

    LINKEDIN_REAL_TOKEN=... LINKEDIN_REAL_ORG_ID=12345 \\
        uv run python scripts/compare_real.py [--version 202506]

The FIRST probe is the only one that commits an architecture decision:
`shares=List(...)` + `timeIntervals` combined — the docs say "not supported",
so the insights360 connector chose daily snapshots as a result. If the real
API serves the combination, open an issue: the connector could gain a
per-post backfill.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

REAL_HOST = "https://api.linkedin.com/rest"


def _shape(node: Any, depth: int = 0) -> Any:
    """The shape of a JSON value: keys and types, values pruned."""
    if depth > 4:
        return "…"
    if isinstance(node, dict):
        return {key: _shape(value, depth + 1) for key, value in sorted(node.items())}
    if isinstance(node, list):
        return [_shape(node[0], depth + 1)] if node else []
    return type(node).__name__


def _get(url: str, token: str, version: str) -> tuple[int, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Linkedin-Version": version,
            "X-Restli-Protocol-Version": "2.0.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read().decode())
        except Exception:
            body = {"raw": "unreadable body"}
        return error.code, body
    except urllib.error.URLError as error:
        return 0, {"transport": str(error)}


def _probes(org: str, urn_share: str | None) -> list[tuple[str, str]]:
    urn_org = f"urn%3Ali%3Aorganization%3A{org}"
    window = (
        "timeIntervals=(timeRange:(start:1767225600000,end:1767830400000),timeGranularityType:DAY)"
    )
    base_share = (
        f"{REAL_HOST}/organizationalEntityShareStatistics"
        f"?q=organizationalEntity&organizationalEntity={urn_org}"
    )
    probes = [
        # THE priority probe — see docstring.
        (
            "combined shares+timeIntervals (PRIORITY)",
            f"{base_share}&shares=List({urn_share})&{window}"
            if urn_share
            else f"{base_share}&shares=List(urn%3Ali%3Ashare%3A0)&{window}",
        ),
        ("organization lookup", f"{REAL_HOST}/organizations/{org}"),
        (
            "networkSizes",
            f"{REAL_HOST}/networkSizes/{urn_org}?edgeType=COMPANY_FOLLOWED_BY_MEMBER",
        ),
        ("posts finder page 1", f"{REAL_HOST}/posts?q=author&author={urn_org}&count=3"),
        ("posts finder WITHOUT q", f"{REAL_HOST}/posts?author={urn_org}"),
        ("posts count>100", f"{REAL_HOST}/posts?q=author&author={urn_org}&count=101"),
        ("share stats full lifetime", base_share),
        ("share stats daily", f"{base_share}&{window}"),
        (
            "follower stats full lifetime",
            f"{REAL_HOST}/organizationalEntityFollowerStatistics"
            f"?q=organizationalEntity&organizationalEntity={urn_org}",
        ),
        (
            "page stats full lifetime",
            f"{REAL_HOST}/organizationPageStatistics?q=organization&organization={urn_org}",
        ),
        (
            "page stats WRONG finder (organizationalEntity)",
            f"{REAL_HOST}/organizationPageStatistics"
            f"?q=organizationalEntity&organizationalEntity={urn_org}",
        ),
        ("unknown route", f"{REAL_HOST}/nimporte"),
        ("foreign organization (403 expected)", f"{REAL_HOST}/organizations/1337"),
    ]
    return probes


def _probes_without_token(org: str) -> list[tuple[str, str, dict[str, str]]]:
    """The precedences: no token, no version — which wins?"""
    urn_org = f"urn%3Ali%3Aorganization%3A{org}"
    url = f"{REAL_HOST}/posts?q=author&author={urn_org}"
    return [
        ("neither token nor version (auth/version precedence)", url, {}),
        ("token without version (VERSION_MISSING expected)", url, {"Authorization": "Bearer …"}),
        (
            "forged token (401 invalid body)",
            url,
            {
                "Authorization": "Bearer forged-token-for-the-probe",
                "Linkedin-Version": "202506",
                "X-Restli-Protocol-Version": "2.0.0",
            },
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="202506", help="Linkedin-Version to send")
    parser.add_argument(
        "--share-urn", default=None, help="Real share URN (encoded) for the priority probe"
    )
    args = parser.parse_args()

    token = os.environ.get("LINKEDIN_REAL_TOKEN", "")
    org = os.environ.get("LINKEDIN_REAL_ORG_ID", "")
    if not token or not org:
        print("LINKEDIN_REAL_TOKEN and LINKEDIN_REAL_ORG_ID are required.", file=sys.stderr)
        return 2

    print(f"# Structural probes — {REAL_HOST}, version {args.version}\n")
    for name, url in _probes(org, args.share_urn):
        status, body = _get(url, token, args.version)
        print(f"## {name}\n   GET {url}\n   → HTTP {status}")
        print("   " + json.dumps(_shape(body), ensure_ascii=False)[:600] + "\n")

    print("# Error precedences (deliberately malformed requests)\n")
    for name, url, headers in _probes_without_token(org):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status, body = response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            try:
                status, body = error.code, json.loads(error.read().decode())
            except Exception:
                status, body = error.code, {}
        print(f"## {name}\n   → HTTP {status} {json.dumps(body, ensure_ascii=False)[:300]}\n")

    print(
        "Confront each reading against docs/UNVERIFIED-FIELDS.md, fix the\n"
        "constants (errors.py, models/) and REGENERATE the contract (make contract)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
