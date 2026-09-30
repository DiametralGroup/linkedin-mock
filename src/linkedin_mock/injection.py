"""Failure injection — the real point of a mock.

Architecture reused from boondmanager-mock (declarative rules, drivable over
HTTP via /__admin, ONE dispatch point evaluated before authentication), with
failure modes SPECIFIC to the LinkedIn regime:

  rate_limit      DAILY quota: triggers past `after_requests` requests within
                  the current VIRTUAL UTC day, counter reset at virtual
                  midnight UTC — and WITHOUT Retry-After, like the real API
                  (unpublished quotas, midnight UTC reset); the client must
                  decide blind, that's the behavior to exercise;
  status          the outright failure (5xx);
  latency         a real sleep — the only way to exercise a client timeout;
  auth_reject     preempts authentication; `variant` picks the 401 body
                  (empty | invalid | expired | revoked);
  version_reject  forces the 426 NONEXISTENT_VERSION — repeats a mid-quarter
                  version removal, without touching the window;
  page_drift      shifts the posts finder's start/count slice: a post
                  published between two pages makes a duplicate (or a gap)
                  appear — the reason merge-on-key exists on the pipeline side.
"""

from __future__ import annotations

import fnmatch
import time
from dataclasses import dataclass
from typing import Any, Literal

Kind = Literal["rate_limit", "status", "latency", "page_drift", "auth_reject", "version_reject"]

AUTH_VARIANTS = ("empty", "invalid", "expired", "revoked")


@dataclass
class Rule:
    """An injection rule. `scope` is a glob pattern on the path."""

    id: str
    kind: Kind
    scope: str = "*"
    # Number of applications left. None = unlimited. This is the difference
    # between a transient failure (that a retry must absorb) and a persistent
    # failure (that must make the run fail with a non-zero exit code).
    times: int | None = None

    # rate_limit — quota per virtual UTC day.
    after_requests: int = 0
    # status
    status: int = 500
    # latency
    seconds: float = 0.0
    # page_drift
    mode: Literal["insert", "remove"] = "insert"
    # auth_reject
    variant: str = "invalid"

    def matches(self, path: str) -> bool:
        return fnmatch.fnmatch(path, self.scope)

    def consume(self) -> bool:
        """Decrements the counter. Returns False once the rule is exhausted."""
        if self.times is None:
            return True
        if self.times <= 0:
            return False
        self.times -= 1
        return True


class InjectionEngine:
    """The engine, and the request counters it depends on."""

    def __init__(self) -> None:
        self.rules: list[Rule] = []
        self.request_counts: dict[str, int] = {}
        #: (path, ISO day) → count — the substance of the daily quota.
        self.day_count: dict[tuple[str, str], int] = {}
        self.last_query_params: dict[str, dict[str, str]] = {}
        self._next_id = 1
        # Virtual clock: time windows (quota, stats) without sleeping.
        self.clock_offset: float = 0.0

    # ── Rule management ──────────────────────────────────────────────────────

    def add(self, **kwargs: Any) -> Rule:
        rule = Rule(id=f"r{self._next_id}", **kwargs)
        self._next_id += 1
        self.rules.append(rule)
        return rule

    def remove(self, rule_id: str) -> bool:
        before = len(self.rules)
        self.rules = [r for r in self.rules if r.id != rule_id]
        return len(self.rules) != before

    def clear(self) -> None:
        self.rules.clear()

    def reset_counters(self) -> None:
        self.request_counts.clear()
        self.day_count.clear()
        self.last_query_params.clear()
        self.clock_offset = 0.0

    # ── Observation ──────────────────────────────────────────────────────────

    def observe(self, path: str, params: dict[str, str], day: str) -> int:
        """Records a request's passage; returns its rank WITHIN THE DAY.

        `last_query_params` is the load-bearing part: it's what lets a
        consumer PROVE it actually SENT `timeIntervals`, its pagination and
        its `q`, instead of merely tolerating their absence.
        """
        self.request_counts[path] = self.request_counts.get(path, 0) + 1
        self.day_count[(path, day)] = self.day_count.get((path, day), 0) + 1
        self.last_query_params[path] = dict(params)
        return self.day_count[(path, day)]

    def now(self) -> float:
        return time.time() + self.clock_offset

    # ── Dispatch ─────────────────────────────────────────────────────────────

    def first(self, kind: Kind, path: str) -> Rule | None:
        """First active rule of the requested kind for this path."""
        for rule in self.rules:
            if rule.kind == kind and rule.matches(path):
                if rule.times is not None and rule.times <= 0:
                    continue
                return rule
        return None

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "id": r.id,
                "kind": r.kind,
                "scope": r.scope,
                "times_left": r.times,
                **{
                    k: v
                    for k, v in (
                        ("after_requests", r.after_requests),
                        ("status", r.status),
                        ("seconds", r.seconds),
                        ("mode", r.mode),
                        ("variant", r.variant),
                    )
                    if _relevant(r.kind, k)
                },
            }
            for r in self.rules
        ]


_FIELDS_BY_KIND: dict[str, set[str]] = {
    "rate_limit": {"after_requests"},
    "status": {"status"},
    "latency": {"seconds"},
    "page_drift": {"mode"},
    "auth_reject": {"variant"},
    "version_reject": set(),
}


def _relevant(kind: str, field: str) -> bool:
    return field in _FIELDS_BY_KIND.get(kind, set())


engine = InjectionEngine()
