"""État mutable du serveur.

`state.dataset`, `state.reset(seed=…)`, `state.avancer_evolution()` — le même
contrat que boondmanager-mock, sans les caches de recherche (l'API LinkedIn
mockée n'a ni `keywords` ni `included`).
"""

from __future__ import annotations

import time
from typing import Any

from .evolution import Evolution
from .injection import engine
from .settings import settings


def build_dataset(seed: int = 42, org_id: str | None = None) -> dict[str, Any]:
    """Construit le jeu de données « Boréal Conseil sur LinkedIn »."""
    from .dataset.realiste import build_realiste_dataset

    return build_realiste_dataset(seed, org_id or settings.org_id)


class MockState:
    """État serveur mutable, pour simuler des changements distants et des pannes."""

    def __init__(self) -> None:
        self.dataset: dict[str, Any] = {}
        self.seed: int = settings.seed
        self.evolution: Evolution = Evolution(self.seed, time.time())
        self.reset()

    def reset(self, seed: int | None = None) -> None:
        """Reconstruit le jeu de données et remet les compteurs à zéro.

        ⚠️ Les règles d'injection sont remises À LA LIGNE DE BASE DÉCLARÉE PAR
        L'ENVIRONNEMENT, pas à vide : si un reset vidait les règles, la
        première requête d'une suite de tests effacerait silencieusement un
        quota journalier configuré au niveau du compose.

        L'évolution temporelle est RÉARMÉE : la chronologie repart de zéro.
        """
        self.seed = settings.seed if seed is None else seed
        self.dataset = build_dataset(self.seed)
        engine.clear()
        engine.reset_counters()
        self.evolution = Evolution(self.seed, time.time())
        _appliquer_injections_de_base()

    def avancer_evolution(self, maintenant: float) -> None:
        """Fait avancer la vie de la page (les événements devenus dus)."""
        self.evolution.avancer(self.dataset, maintenant)

    def totals(self) -> dict[str, int]:
        """Les volumes que /__admin/state expose."""
        jours_stats = {jour for serie in self.dataset["series_posts"].values() for jour in serie}
        return {
            "posts": len(self.dataset["posts"]),
            "jours_stats": len(jours_stats),
            "jours_abonnes": len(self.dataset["serie_abonnes"]),
            "jours_vues": len(self.dataset["serie_vues"]),
        }


def _appliquer_injections_de_base() -> None:
    """Le quota journalier déclaré par l'environnement, réappliqué à chaque
    reset — un compose peut faire tourner le mock en permanence contingenté
    sans qu'un test ne l'annule par inadvertance."""
    if settings.daily_quota > 0:
        engine.add(kind="rate_limit", scope="/rest/*", after_requests=settings.daily_quota)


state = MockState()
