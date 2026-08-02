"""Évolution temporelle — la vie de la page entre deux extractions.

Un connecteur incrémental ne se teste pas contre un jeu figé : il faut que des
buckets CHANGENT entre deux exécutions (les compteurs d'un post continuent de
monter, un post est publié, un autre est édité), sinon la fenêtre de dates et
les snapshots passent tous les tests sans avoir rien prouvé.

  cadence     un événement toutes les `LINKEDIN_MOCK_EVOLUTION_INTERVAL`
              secondes (60 par défaut ; 0 ou LINKEDIN_MOCK_EVOLUTION=false
              pour figer) ;
  cycle       stats_jour → nouveau_post → gains_abonnes → edition_post →
              pic_viral → stats_jour — pondéré vers la croissance des stats,
              le changement dominant du monde réel ;
  horloge     `engine.now()` — donc `POST /__admin/clock {advance_seconds}`
              fait défiler la vie de la page sans attendre.

DÉTERMINISME : l'événement k tire son aléa de `Random(f"{seed}:{k}")` et son
horodatage vaut toujours EPOQUE + (k+1) x intervalle. Deux serveurs au même âge
servent les mêmes données. Chaque événement écrit dans le jour UTC de SON
horodatage — une avance d'horloge de quatre jours remplit donc quatre jours de
buckets, exactement comme si le temps s'était réellement écoulé.

Les incréments sont proportionnels à l'intervalle : un événement couvre
`intervalle` secondes de vie de la page, quelle que soit la cadence choisie.
"""

from __future__ import annotations

import math
import random
import threading
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from .settings import settings

#: L'époque des événements : l'ancre du jeu de données, 9 h heure de Paris.
#: STRICTEMENT postérieure au plafond du jeu de base (stats jusqu'au
#: 2026-07-13, lastModifiedAt jusqu'au 2026-07-12) — le delta incrémental
#: entre « avant » et « après » reste propre.
EPOQUE = datetime(2026, 7, 15, 9, 0, 0, tzinfo=timezone(timedelta(hours=2)))

CYCLE = (
    "stats_jour",
    "nouveau_post",
    "gains_abonnes",
    "edition_post",
    "pic_viral",
    "stats_jour",
)

_COMMENTAIRES_NOUVEAU_POST = (
    "Retour à chaud sur notre dernier atelier data : les slides sont en ligne. {hashtag|\\#|data}",
    "Un nouveau projet démarre cette semaine — socle analytics et gouvernance au menu. "
    "{hashtag|\\#|analytics}",
    "Boréal Conseil recrute un·e Analytics Engineer — parlez-en autour de vous ! "
    "{hashtag|\\#|WeAreHiring}",
    "Sur le blog : les leçons d'une migration dbt menée en trois sprints. {hashtag|\\#|dbt}",
)

_COMPTEURS = ("impressionCount", "clickCount", "likeCount", "commentCount", "shareCount")


def _ts_ms(age: float) -> int:
    return int((EPOQUE + timedelta(seconds=age)).timestamp() * 1000)


def _jour_iso(age: float) -> str:
    return (EPOQUE + timedelta(seconds=age)).astimezone(UTC).date().isoformat()


def _incrementer(serie: dict[str, dict[str, int]], jour: str, valeurs: dict[str, int]) -> None:
    bucket = serie.setdefault(jour, dict.fromkeys(_COMPTEURS, 0))
    for compteur, valeur in valeurs.items():
        bucket[compteur] = bucket.get(compteur, 0) + valeur


def _arrondi_stochastique(valeur: float, rng: random.Random) -> int:
    entier = int(valeur)
    return entier + (1 if rng.random() < valeur - entier else 0)


class Evolution:
    """La chronologie d'événements d'une instance, réarmée à chaque reset."""

    def __init__(self, seed: int, demarrage: float) -> None:
        self.seed = seed
        self.demarrage = demarrage
        self.appliques = 0
        self.journal: list[dict[str, Any]] = []
        self._verrou = threading.Lock()

    # ── Pilotage ─────────────────────────────────────────────────────────────

    def avancer(self, dataset: dict[str, Any], maintenant: float) -> int:
        """Applique tous les événements devenus dus ; rend leur nombre.

        Idempotent et verrouillé — les handlers FastAPI tournent dans un pool
        de threads.
        """
        if not settings.evolution_enabled or settings.evolution_interval <= 0:
            return 0
        dus = int((maintenant - self.demarrage) // settings.evolution_interval)
        if dus <= self.appliques:
            return 0
        appliques_ici = 0
        with self._verrou:
            while self.appliques < dus:
                k = self.appliques
                age = (k + 1) * settings.evolution_interval
                detail = self._appliquer(dataset, k, age)
                self.journal.append(
                    {
                        "index": k,
                        "at": _jour_iso(age),
                        "kind": CYCLE[k % len(CYCLE)],
                        "detail": detail,
                    }
                )
                del self.journal[:-50]
                self.appliques += 1
                appliques_ici += 1
        return appliques_ici

    def apercu(self) -> dict[str, Any]:
        """Ce que /__admin/state expose — l'observabilité du delta."""
        return {
            "enabled": settings.evolution_enabled and settings.evolution_interval > 0,
            "interval_seconds": settings.evolution_interval,
            "applied": self.appliques,
            "journal": self.journal[-20:],
        }

    # ── Les événements ───────────────────────────────────────────────────────

    def _appliquer(self, dataset: dict[str, Any], k: int, age: float) -> str:
        rng = random.Random(f"{self.seed}:{k}")
        genre = CYCLE[k % len(CYCLE)]
        gestionnaires = {
            "stats_jour": self._stats_jour,
            "nouveau_post": self._nouveau_post,
            "gains_abonnes": self._gains_abonnes,
            "edition_post": self._edition_post,
            "pic_viral": self._pic_viral,
        }
        detail = gestionnaires[genre](dataset, rng, k, age)
        # Un gestionnaire sans matière retombe sur l'événement le plus neutre.
        return detail or self._stats_jour(dataset, rng, k, age)

    def _stats_jour(self, dataset: dict[str, Any], rng: random.Random, _k: int, age: float) -> str:
        """La croissance du jour : les posts récents et les vues de page.

        L'événement couvre `intervalle` secondes de vie — les incréments sont
        mis à l'échelle pour qu'une journée complète d'événements produise des
        volumes du même ordre que le jeu de base, quelle que soit la cadence.
        """
        jour = _jour_iso(age)
        maintenant_ms = _ts_ms(age)
        fraction_jour = settings.evolution_interval / 86_400
        touches = 0
        for post in dataset["posts"]:
            age_jours = (maintenant_ms - post["publishedAt"]) / 86_400_000
            if age_jours > 21:
                continue
            attendu = (900 * math.exp(-0.35 * age_jours) + 2) * fraction_jour
            impressions = _arrondi_stochastique(attendu * rng.uniform(0.7, 1.3), rng)
            if impressions <= 0:
                continue
            _incrementer(
                dataset["series_posts"].setdefault(post["id"], {}),
                jour,
                {
                    "impressionCount": impressions,
                    "clickCount": _arrondi_stochastique(impressions * 0.025, rng),
                    "likeCount": _arrondi_stochastique(impressions * 0.02, rng),
                    "commentCount": _arrondi_stochastique(impressions * 0.003, rng),
                    "shareCount": _arrondi_stochastique(impressions * 0.004, rng),
                },
            )
            dataset["uniques_vie"][post["id"]] = dataset["uniques_vie"].get(post["id"], 0) + round(
                impressions * 0.7
            )
            touches += 1

        vues = dataset["serie_vues"].setdefault(
            jour,
            {
                "overview": 0,
                "jobs": 0,
                "lifeAt": 0,
                "part_bureau": round(rng.uniform(0.55, 0.75), 3),
                "part_uniques": round(rng.uniform(0.72, 0.85), 3),
            },
        )
        vues["overview"] += _arrondi_stochastique(28 * fraction_jour * rng.uniform(0.6, 1.6), rng)
        vues["jobs"] += _arrondi_stochastique(1.5 * fraction_jour, rng)
        vues["lifeAt"] += _arrondi_stochastique(0.8 * fraction_jour, rng)
        return f"stats du {jour} ({touches} posts récents)"

    def _nouveau_post(self, dataset: dict[str, Any], rng: random.Random, k: int, age: float) -> str:
        """Une publication fraîche — elle DOIT sortir en tête du finder
        (tri LAST_MODIFIED desc) : le chemin heureux de l'extraction."""
        posts = dataset["posts"]
        dernier_id = max(int(p["id"].rsplit(":", 1)[1]) for p in posts)
        identifiant = dernier_id + rng.randint(30, 120) * 10**14
        publie = _ts_ms(age)
        urn = f"urn:li:share:{identifiant}"
        posts.append(
            {
                "id": urn,
                "author": dataset["organisation"]["$URN"],
                "commentary": _COMMENTAIRES_NOUVEAU_POST[k % len(_COMMENTAIRES_NOUVEAU_POST)],
                "createdAt": publie,
                "publishedAt": publie,
                "lastModifiedAt": publie,
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
        dataset["series_posts"][urn] = {}
        dataset["uniques_vie"][urn] = 0
        return f"post {urn} publié"

    def _gains_abonnes(
        self, dataset: dict[str, Any], rng: random.Random, _k: int, age: float
    ) -> str:
        """Les abonnés du jour — l'événement couvre 1/6 du cycle, l'échelle
        vise ~4 gains organiques par jour complet d'événements."""
        jour = _jour_iso(age)
        attendu = 4 * len(CYCLE) * settings.evolution_interval / 86_400
        organique = _arrondi_stochastique(attendu * rng.uniform(0.5, 2.0), rng)
        if organique == 0:
            organique = 1
        gains = dataset["serie_abonnes"].setdefault(
            jour, {"organicFollowerGain": 0, "paidFollowerGain": 0}
        )
        gains["organicFollowerGain"] += organique
        return f"+{organique} abonnés le {jour}"

    def _edition_post(self, dataset: dict[str, Any], rng: random.Random, k: int, age: float) -> str:
        """L'édition d'un post ANCIEN : un curseur sur lastModifiedAt doit
        revoir exactement celui-là — l'analogue du `_profil` de boond."""
        posts = dataset["posts"]
        anciens = posts[: max(1, len(posts) - 10)]
        post = anciens[k % len(anciens)]
        post["lastModifiedAt"] = _ts_ms(age)
        post["lifecycleStateInfo"] = {"isEditedByAuthor": True}
        if rng.random() < 0.5 and "(mise à jour)" not in post["commentary"]:
            post["commentary"] = post["commentary"] + " (mise à jour)"
        return f"post {post['id']} édité"

    def _pic_viral(self, dataset: dict[str, Any], rng: random.Random, _k: int, age: float) -> str:
        """Un post récent part en vadrouille — l'anomalie que l'aval doit
        tolérer sans s'étrangler."""
        jour = _jour_iso(age)
        maintenant_ms = _ts_ms(age)
        recents = [
            p for p in dataset["posts"] if (maintenant_ms - p["publishedAt"]) / 86_400_000 <= 30
        ]
        if not recents:
            return ""
        post = recents[rng.randrange(len(recents))]
        impressions = rng.randint(800, 2500)
        _incrementer(
            dataset["series_posts"].setdefault(post["id"], {}),
            jour,
            {
                "impressionCount": impressions,
                "clickCount": round(impressions * 0.05),
                "likeCount": round(impressions * 0.06),
                "commentCount": round(impressions * 0.012),
                "shareCount": round(impressions * 0.02),
            },
        )
        dataset["uniques_vie"][post["id"]] = dataset["uniques_vie"].get(post["id"], 0) + round(
            impressions * 0.8
        )
        return f"pic viral sur {post['id']} (+{impressions} impressions)"
