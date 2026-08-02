"""Injection de pannes — le vrai intérêt d'un mock.

Architecture reprise de boondmanager-mock (règles déclaratives, pilotables par
HTTP via /__admin, UN point de dispatch évalué avant l'authentification), avec
les modes de panne PROPRES au régime LinkedIn :

  rate_limit      quota JOURNALIER : se déclenche au-delà de `after_requests`
                  requêtes dans le jour UTC VIRTUEL courant, compteur remis à
                  zéro à minuit UTC virtuel — et SANS Retry-After, comme la
                  vraie API (quotas non publiés, reset minuit UTC) ; le client
                  doit décider à l'aveugle, c'est le geste à répéter ;
  status          la panne franche (5xx) ;
  latency         un vrai sleep — seul moyen d'éprouver un timeout client ;
  auth_reject     préempte l'authentification ; `variant` choisit le corps 401
                  (empty | invalid | expired | revoked) ;
  version_reject  force le 426 NONEXISTENT_VERSION — répète un retrait de
                  version en cours de trimestre, sans toucher à la fenêtre ;
  page_drift      décale la tranche start/count du finder posts : un post
                  publié entre deux pages fait apparaître un doublon (ou un
                  trou) — la raison d'être du merge sur clé côté pipeline.
"""

from __future__ import annotations

import fnmatch
import time
from dataclasses import dataclass
from typing import Any, Literal

Kind = Literal["rate_limit", "status", "latency", "page_drift", "auth_reject", "version_reject"]

VARIANTES_AUTH = ("empty", "invalid", "expired", "revoked")


@dataclass
class Rule:
    """Une règle d'injection. `scope` est un motif glob sur le chemin."""

    id: str
    kind: Kind
    scope: str = "*"
    # Nombre d'applications restantes. None = illimité. C'est la différence
    # entre une panne transitoire (que le retry doit absorber) et une panne
    # persistante (qui doit faire échouer le run avec un code non nul).
    times: int | None = None

    # rate_limit — quota par jour UTC virtuel.
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
        """Décrémente le compteur. Rend False quand la règle est épuisée."""
        if self.times is None:
            return True
        if self.times <= 0:
            return False
        self.times -= 1
        return True


class InjectionEngine:
    """Le moteur, et les compteurs de requêtes dont il dépend."""

    def __init__(self) -> None:
        self.rules: list[Rule] = []
        self.request_counts: dict[str, int] = {}
        #: (chemin, jour ISO) → compteur — la matière du quota journalier.
        self.compte_jour: dict[tuple[str, str], int] = {}
        self.last_query_params: dict[str, dict[str, str]] = {}
        self._next_id = 1
        # Horloge virtuelle : fenêtres temporelles (quota, stats) sans sleep.
        self.clock_offset: float = 0.0

    # ── Gestion des règles ───────────────────────────────────────────────────

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
        self.compte_jour.clear()
        self.last_query_params.clear()
        self.clock_offset = 0.0

    # ── Observation ──────────────────────────────────────────────────────────

    def observe(self, path: str, params: dict[str, str], jour: str) -> int:
        """Enregistre le passage d'une requête ; rend son rang DANS LE JOUR.

        `last_query_params` est porteur : c'est ce qui permet à un consommateur
        de prouver qu'il a bien ENVOYÉ `timeIntervals`, sa pagination et son
        `q`, au lieu de simplement tolérer leur absence.
        """
        self.request_counts[path] = self.request_counts.get(path, 0) + 1
        self.compte_jour[(path, jour)] = self.compte_jour.get((path, jour), 0) + 1
        self.last_query_params[path] = dict(params)
        return self.compte_jour[(path, jour)]

    def now(self) -> float:
        return time.time() + self.clock_offset

    # ── Dispatch ─────────────────────────────────────────────────────────────

    def first(self, kind: Kind, path: str) -> Rule | None:
        """Première règle active du type demandé pour ce chemin."""
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


_CHAMPS_PAR_KIND: dict[str, set[str]] = {
    "rate_limit": {"after_requests"},
    "status": {"status"},
    "latency": {"seconds"},
    "page_drift": {"mode"},
    "auth_reject": {"variant"},
    "version_reject": set(),
}


def _relevant(kind: str, champ: str) -> bool:
    return champ in _CHAMPS_PAR_KIND.get(kind, set())


engine = InjectionEngine()
