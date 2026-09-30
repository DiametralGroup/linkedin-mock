"""Mutable server state.

`state.dataset`, `state.reset(seed=…)`, `state.advance_evolution()` — the same
contract as boondmanager-mock, minus the search caches (the mocked LinkedIn
API has neither `keywords` nor `included`).
"""

from __future__ import annotations

import time
from typing import Any

from .evolution import Evolution
from .injection import engine
from .settings import settings


def build_dataset(seed: int = 42, org_id: str | None = None) -> dict[str, Any]:
    """Builds the "Boréal Conseil on LinkedIn" dataset."""
    from .dataset.realiste import build_realiste_dataset

    return build_realiste_dataset(seed, org_id or settings.org_id)


class MockState:
    """Mutable server state, for simulating remote changes and failures."""

    def __init__(self) -> None:
        self.dataset: dict[str, Any] = {}
        self.seed: int = settings.seed
        self.evolution: Evolution = Evolution(self.seed, time.time())
        self.reset()

    def reset(self, seed: int | None = None) -> None:
        """Rebuilds the dataset and resets the counters to zero.

        Injection rules are reset to the BASELINE DECLARED BY THE
        ENVIRONMENT, not to empty: if a reset cleared the rules, the first
        request of a test suite would silently wipe out a daily quota
        configured at the compose level.

        Time evolution is REARMED: the timeline starts over from zero.
        """
        self.seed = settings.seed if seed is None else seed
        self.dataset = build_dataset(self.seed)
        engine.clear()
        engine.reset_counters()
        self.evolution = Evolution(self.seed, time.time())
        _apply_baseline_injections()

    def advance_evolution(self, now: float) -> None:
        """Advances the page's life (the events that have become due)."""
        self.evolution.advance(self.dataset, now)

    def totals(self) -> dict[str, int]:
        """The volumes that /__admin/state exposes."""
        stats_days = {day for series in self.dataset["posts_series"].values() for day in series}
        return {
            "posts": len(self.dataset["posts"]),
            "stats_days": len(stats_days),
            "followers_days": len(self.dataset["followers_series"]),
            "views_days": len(self.dataset["views_series"]),
        }


def _apply_baseline_injections() -> None:
    """The daily quota declared by the environment, reapplied on every reset —
    a compose can run the mock permanently rate-limited without a test
    accidentally cancelling it."""
    if settings.daily_quota > 0:
        engine.add(kind="rate_limit", scope="/rest/*", after_requests=settings.daily_quota)


state = MockState()
