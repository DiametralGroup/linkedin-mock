"""Time evolution — the page's life between two extractions.

An incremental connector can't be tested against a frozen dataset: buckets
must CHANGE between two runs (a post's counters keep climbing, a new post
gets published, another gets edited), otherwise the date window and the
snapshots pass every test without having proven anything.

  cadence     one event every `LINKEDIN_MOCK_EVOLUTION_INTERVAL` seconds
              (60 by default; 0 or LINKEDIN_MOCK_EVOLUTION=false to freeze);
  cycle       daily_stats → new_post → follower_gains → post_edit →
              viral_spike → daily_stats — weighted toward stats growth, the
              dominant change in the real world;
  clock       `engine.now()` — so `POST /__admin/clock {advance_seconds}`
              fast-forwards the page's life without waiting.

DETERMINISM: event k draws its randomness from `Random(f"{seed}:{k}")` and its
timestamp is always EPOCH + (k+1) x interval. Two servers at the same age
serve the same data. Each event writes into the UTC day of ITS OWN
timestamp — a four-day clock advance therefore fills four days of buckets,
exactly as if time had really passed.

Increments are proportional to the interval: one event covers `interval`
seconds of the page's life, whatever cadence was chosen.
"""

from __future__ import annotations

import math
import random
import threading
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from .settings import settings

#: The events' epoch: the dataset's anchor, 9 AM Paris time.
#: STRICTLY later than the base dataset's ceiling (stats up to 2026-07-13,
#: lastModifiedAt up to 2026-07-12) — the incremental delta between "before"
#: and "after" stays clean.
EPOCH = datetime(2026, 7, 15, 9, 0, 0, tzinfo=timezone(timedelta(hours=2)))

CYCLE = (
    "daily_stats",
    "new_post",
    "follower_gains",
    "post_edit",
    "viral_spike",
    "daily_stats",
)

_NEW_POST_COMMENTS = (
    "Retour à chaud sur notre dernier atelier data : les slides sont en ligne. {hashtag|\\#|data}",
    "Un nouveau projet démarre cette semaine — socle analytics et gouvernance au menu. "
    "{hashtag|\\#|analytics}",
    "Boréal Conseil recrute un·e Analytics Engineer — parlez-en autour de vous ! "
    "{hashtag|\\#|WeAreHiring}",
    "Sur le blog : les leçons d'une migration dbt menée en trois sprints. {hashtag|\\#|dbt}",
)

_COUNTERS = ("impressionCount", "clickCount", "likeCount", "commentCount", "shareCount")


def _ts_ms(age: float) -> int:
    return int((EPOCH + timedelta(seconds=age)).timestamp() * 1000)


def _day_iso(age: float) -> str:
    return (EPOCH + timedelta(seconds=age)).astimezone(UTC).date().isoformat()


def _increment(series: dict[str, dict[str, int]], day: str, values: dict[str, int]) -> None:
    bucket = series.setdefault(day, dict.fromkeys(_COUNTERS, 0))
    for counter, value in values.items():
        bucket[counter] = bucket.get(counter, 0) + value


def _stochastic_round(value: float, rng: random.Random) -> int:
    whole = int(value)
    return whole + (1 if rng.random() < value - whole else 0)


class Evolution:
    """An instance's event timeline, rearmed on every reset."""

    def __init__(self, seed: int, start: float) -> None:
        self.seed = seed
        self.start = start
        self.applied = 0
        self.journal: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    # ── Driving ──────────────────────────────────────────────────────────────

    def advance(self, dataset: dict[str, Any], now: float) -> int:
        """Applies every event that has become due; returns their count.

        Idempotent and locked — FastAPI handlers run in a thread pool.
        """
        if not settings.evolution_enabled or settings.evolution_interval <= 0:
            return 0
        due = int((now - self.start) // settings.evolution_interval)
        if due <= self.applied:
            return 0
        applied_here = 0
        with self._lock:
            while self.applied < due:
                k = self.applied
                age = (k + 1) * settings.evolution_interval
                detail = self._apply(dataset, k, age)
                self.journal.append(
                    {
                        "index": k,
                        "at": _day_iso(age),
                        "kind": CYCLE[k % len(CYCLE)],
                        "detail": detail,
                    }
                )
                del self.journal[:-50]
                self.applied += 1
                applied_here += 1
        return applied_here

    def overview(self) -> dict[str, Any]:
        """What /__admin/state exposes — the delta's observability."""
        return {
            "enabled": settings.evolution_enabled and settings.evolution_interval > 0,
            "interval_seconds": settings.evolution_interval,
            "applied": self.applied,
            "journal": self.journal[-20:],
        }

    # ── The events ────────────────────────────────────────────────────────────

    def _apply(self, dataset: dict[str, Any], k: int, age: float) -> str:
        rng = random.Random(f"{self.seed}:{k}")
        kind = CYCLE[k % len(CYCLE)]
        handlers = {
            "daily_stats": self._daily_stats,
            "new_post": self._new_post,
            "follower_gains": self._follower_gains,
            "post_edit": self._post_edit,
            "viral_spike": self._viral_spike,
        }
        detail = handlers[kind](dataset, rng, k, age)
        # A handler with nothing to report falls back to the most neutral event.
        return detail or self._daily_stats(dataset, rng, k, age)

    def _daily_stats(self, dataset: dict[str, Any], rng: random.Random, _k: int, age: float) -> str:
        """The day's growth: recent posts and page views.

        The event covers `interval` seconds of life — increments are scaled
        so that a full day of events produces volumes of the same order as
        the base dataset, whatever cadence is chosen.
        """
        day = _day_iso(age)
        now_ms = _ts_ms(age)
        day_fraction = settings.evolution_interval / 86_400
        touched = 0
        for post in dataset["posts"]:
            age_days = (now_ms - post["publishedAt"]) / 86_400_000
            if age_days > 21:
                continue
            expected = (900 * math.exp(-0.35 * age_days) + 2) * day_fraction
            impressions = _stochastic_round(expected * rng.uniform(0.7, 1.3), rng)
            if impressions <= 0:
                continue
            _increment(
                dataset["posts_series"].setdefault(post["id"], {}),
                day,
                {
                    "impressionCount": impressions,
                    "clickCount": _stochastic_round(impressions * 0.025, rng),
                    "likeCount": _stochastic_round(impressions * 0.02, rng),
                    "commentCount": _stochastic_round(impressions * 0.003, rng),
                    "shareCount": _stochastic_round(impressions * 0.004, rng),
                },
            )
            dataset["lifetime_uniques"][post["id"]] = dataset["lifetime_uniques"].get(
                post["id"], 0
            ) + round(impressions * 0.7)
            touched += 1

        views = dataset["views_series"].setdefault(
            day,
            {
                "overview": 0,
                "jobs": 0,
                "lifeAt": 0,
                "part_bureau": round(rng.uniform(0.55, 0.75), 3),
                "part_uniques": round(rng.uniform(0.72, 0.85), 3),
            },
        )
        views["overview"] += _stochastic_round(28 * day_fraction * rng.uniform(0.6, 1.6), rng)
        views["jobs"] += _stochastic_round(1.5 * day_fraction, rng)
        views["lifeAt"] += _stochastic_round(0.8 * day_fraction, rng)
        return f"stats for {day} ({touched} recent posts)"

    def _new_post(self, dataset: dict[str, Any], rng: random.Random, k: int, age: float) -> str:
        """A fresh publication — it MUST come out on top of the finder
        (LAST_MODIFIED desc sort): the happy path of extraction."""
        posts = dataset["posts"]
        last_id = max(int(p["id"].rsplit(":", 1)[1]) for p in posts)
        identifier = last_id + rng.randint(30, 120) * 10**14
        published = _ts_ms(age)
        urn = f"urn:li:share:{identifier}"
        posts.append(
            {
                "id": urn,
                "author": dataset["organization"]["$URN"],
                "commentary": _NEW_POST_COMMENTS[k % len(_NEW_POST_COMMENTS)],
                "createdAt": published,
                "publishedAt": published,
                "lastModifiedAt": published,
                "lifecycleState": "PUBLISHED",
                "lifecycleStateInfo": {"isEditedByAuthor": False},
                "visibility": "PUBLIC",
                "distribution": {
                    "feedDistribution": "MAIN_FEED",
                    "thirdPartyDistributionChannels": [],
                },
                "isReshareDisabledByAuthor": False,
            }
        )
        dataset["posts_series"][urn] = {}
        dataset["lifetime_uniques"][urn] = 0
        return f"post {urn} published"

    def _follower_gains(
        self, dataset: dict[str, Any], rng: random.Random, _k: int, age: float
    ) -> str:
        """The day's followers — the event covers 1/6 of the cycle, scaled to
        aim for ~4 organic gains per full day of events."""
        day = _day_iso(age)
        expected = 4 * len(CYCLE) * settings.evolution_interval / 86_400
        organic = _stochastic_round(expected * rng.uniform(0.5, 2.0), rng)
        if organic == 0:
            organic = 1
        gains = dataset["followers_series"].setdefault(
            day, {"organicFollowerGain": 0, "paidFollowerGain": 0}
        )
        gains["organicFollowerGain"] += organic
        return f"+{organic} followers on {day}"

    def _post_edit(self, dataset: dict[str, Any], rng: random.Random, k: int, age: float) -> str:
        """Editing an OLD post: a cursor on lastModifiedAt must see exactly
        that one again — the analogue of boond's `_profile`."""
        posts = dataset["posts"]
        old = posts[: max(1, len(posts) - 10)]
        post = old[k % len(old)]
        post["lastModifiedAt"] = _ts_ms(age)
        post["lifecycleStateInfo"] = {"isEditedByAuthor": True}
        if rng.random() < 0.5 and "(mise à jour)" not in post["commentary"]:
            post["commentary"] = post["commentary"] + " (mise à jour)"
        return f"post {post['id']} edited"

    def _viral_spike(self, dataset: dict[str, Any], rng: random.Random, _k: int, age: float) -> str:
        """A recent post takes off — the anomaly downstream must tolerate
        without choking."""
        day = _day_iso(age)
        now_ms = _ts_ms(age)
        recent = [p for p in dataset["posts"] if (now_ms - p["publishedAt"]) / 86_400_000 <= 30]
        if not recent:
            return ""
        post = recent[rng.randrange(len(recent))]
        impressions = rng.randint(800, 2500)
        _increment(
            dataset["posts_series"].setdefault(post["id"], {}),
            day,
            {
                "impressionCount": impressions,
                "clickCount": round(impressions * 0.05),
                "likeCount": round(impressions * 0.06),
                "commentCount": round(impressions * 0.012),
                "shareCount": round(impressions * 0.02),
            },
        )
        dataset["lifetime_uniques"][post["id"]] = dataset["lifetime_uniques"].get(
            post["id"], 0
        ) + round(impressions * 0.8)
        return f"viral spike on {post['id']} (+{impressions} impressions)"
