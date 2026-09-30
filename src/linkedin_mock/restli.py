"""The Rest.li dialect — THE module with no boondmanager-mock equivalent.

The versioned API speaks Rest.li 2.0: `List(a,b,c)` lists, parenthesized
objects `(timeRange:(start:MS,end:MS),timeGranularityType:DAY)`, percent-encoded
URNs in value position (`urn%3Ali%3Aorganization%3A40123456`). Without the
`X-Restli-Protocol-Version: 2.0.0` header, the request is interpreted as
protocol 1.0: dotted params (`timeIntervals.timeRange.start=…`) and indexed
arrays (`shares[0]=…`).

What the parser must accept — and what this module centralizes:

  • raw 2.0 forms AND fully percent-encoded ones: Starlette decodes the query
    string once, so `%28timeRange…%29` and `(timeRange…)` arrive identical;
    the official examples show BOTH spellings;
  • 1.0 dotted/indexed forms, shown by the same doc pages;
  • the commas of a List() are never ambiguous: URNs never contain any.

The response envelope is `{"elements": [...], "paging": {start, count,
links: []}}` — `links` is always empty here: every official example shows it
empty and its non-empty form is undocumented (cf. registry). A consumer pages
by start/count arithmetic and stops on a short page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_LIST = re.compile(r"^List\((.*)\)$", re.DOTALL)
_START = re.compile(r"start:(\d+)")
_END = re.compile(r"end:(\d+)")
_GRANULARITY = re.compile(r"timeGranularityType:([A-Za-z_]+)")
_INDEXED = re.compile(r"^(?P<name>[A-Za-z]+)\[(?P<index>\d+)\]$")

URN_ORGANIZATION = re.compile(r"^urn:li:organization:(\d+)$")
URN_POST = re.compile(r"^urn:li:(share|ugcPost):(\d+)$")


def parse_list(value: str) -> list[str] | None:
    """`List(a,b,c)` → [a, b, c] — None if the value isn't a List()."""
    m = _LIST.match(value.strip())
    if m is None:
        return None
    inner = m.group(1).strip()
    if not inner:
        return []
    return [element.strip() for element in inner.split(",")]


def list_urns(params: dict[str, str], name: str) -> list[str] | None:
    """The URNs of a multi-valued parameter, in BOTH protocols.

    2.0: `shares=List(urn%3A…,urn%3A…)`;
    1.0: `shares[0]=urn:…&shares[1]=urn:…` (index order respected).
    Returns None if the parameter is absent under both forms.
    """
    if (value := params.get(name)) is not None:
        elements = parse_list(value)
        if elements is not None:
            return elements
        # Bare value (a single URN with no List()) — tolerated, a stub shouldn't break.
        return [value]
    indexes: list[tuple[int, str]] = []
    for key, value in params.items():
        m = _INDEXED.match(key)
        if m is not None and m.group("name") == name:
            indexes.append((int(m.group("index")), value))
    if not indexes:
        return None
    return [v for _, v in sorted(indexes)]


@dataclass(frozen=True)
class Interval:
    """The `timeIntervals` parameter, once decoded."""

    start_ms: int | None
    end_ms: int | None
    granularity: str | None


def parse_time_intervals(params: dict[str, str]) -> Interval | None:
    """`timeIntervals` in both protocols, None if absent.

    2.0: `timeIntervals=(timeRange:(start:MS,end:MS),timeGranularityType:DAY)`
         — key order isn't guaranteed, each piece is looked up independently;
    1.0: `timeIntervals.timeRange.start=MS&timeIntervals.timeGranularityType=DAY`.
    """
    if (value := params.get("timeIntervals")) is not None:
        start = _START.search(value)
        end = _END.search(value)
        granularity = _GRANULARITY.search(value)
        return Interval(
            start_ms=int(start.group(1)) if start else None,
            end_ms=int(end.group(1)) if end else None,
            granularity=granularity.group(1) if granularity else None,
        )
    start_v1 = params.get("timeIntervals.timeRange.start")
    end_v1 = params.get("timeIntervals.timeRange.end")
    granularity_v1 = params.get("timeIntervals.timeGranularityType")
    if start_v1 is None and end_v1 is None and granularity_v1 is None:
        return None
    return Interval(
        start_ms=int(start_v1) if start_v1 and start_v1.isdigit() else None,
        end_ms=int(end_v1) if end_v1 and end_v1.isdigit() else None,
        granularity=granularity_v1,
    )


def uses_restli_2_syntax(params: dict[str, str]) -> bool:
    """Does the request use Rest.li 2.0 syntax?

    Feeds the `LINKEDIN_MOCK_REQUIRE_RESTLI_2` guard rail: a List() or a
    parenthesized object without `X-Restli-Protocol-Version: 2.0.0` is a
    request that probably won't work against the real API.
    """
    return any(v.startswith(("List(", "(")) for v in params.values())


def read_pagination(
    params: dict[str, str], *, default: int, cap: int | None = None
) -> tuple[int, int] | None:
    """(start, count) — None if unreadable or out of bounds (→ 400 on the caller side)."""
    try:
        start = int(params.get("start", "0"))
        count = int(params.get("count", str(default)))
    except ValueError:
        return None
    if start < 0 or count < 1:
        return None
    if cap is not None and count > cap:
        return None
    return start, count


def elements_envelope(elements: list[dict[str, Any]], start: int, count: int) -> dict[str, Any]:
    """The Rest.li collection envelope — paging BEFORE elements, like the
    official examples (JSON key order carries no meaning, but might as well
    resemble the recorded traces)."""
    return {
        "paging": {"start": start, "count": count, "links": []},
        "elements": elements,
    }
