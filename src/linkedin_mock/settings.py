"""Configuration — tout par variables d'environnement, aucun fichier.

Même règle que boondmanager-mock : les variables sont lues à l'import dans un
objet relu par `reload()`, parce que c'est le seul mécanisme qui marche
identiquement en docker compose, en Deployment Kubernetes et en sidecar Tekton
— et que les tests doivent pouvoir en changer sans recharger le module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """État de configuration, relu à chaud par `reload()`."""

    # ── Authentification ─────────────────────────────────────────────────────
    # Un Bearer statique, pas de flux OAuth : le vrai endpoint de jeton vit sur
    # www.linkedin.com (un AUTRE hôte que l'API) et le connecteur consomme un
    # jeton de 60 jours depuis ses secrets — il ne fait jamais le flux 3-legged
    # à l'exécution. Les variantes expired/revoked permettent de répéter les
    # gestes de gestion d'erreur 401 côté client.
    access_token: str = "mock-linkedin-token"
    expired_token: str = "mock-linkedin-token-expired"
    revoked_token: str = "mock-linkedin-token-revoked"

    # ── Organisation servie ──────────────────────────────────────────────────
    # L'id numérique de la page « Boréal Conseil » (urn:li:organization:{id}).
    org_id: str = "40123456"

    seed: int = 42

    # ── Versionnement LinkedIn ───────────────────────────────────────────────
    # L'API réelle exige `Linkedin-Version: YYYYMM` et retire une version ~12
    # mois après sa publication. La fenêtre acceptée est configurable pour que
    # le mock puisse répéter un retrait de version (426) sans nouvelle image.
    oldest_active_version: str = "202408"
    latest_active_version: str = "202607"

    # Rest.li 2.0 : la syntaxe List()/(timeRange:...) sans l'en-tête
    # X-Restli-Protocol-Version: 2.0.0 → 400. Comportement réel NON attesté
    # (cf. docs/UNVERIFIED-FIELDS.md) — le but est d'entraîner le connecteur à
    # toujours envoyer l'en-tête.
    require_restli_2: bool = True

    # « Time-bound statistics is not supported for specific share queries » —
    # la doc officielle est formelle. true (défaut) : la combinaison
    # shares+timeIntervals → 400 explicite. false : le mock la sert quand même
    # (exploration), en sachant que l'API réelle ne le fera probablement pas.
    strict_shares_timebound: bool = True

    # Quota journalier applicatif (0 = désactivé) : au-delà de N requêtes par
    # jour UTC VIRTUEL et par chemin /rest/*, 429 sans Retry-After — le vrai
    # régime LinkedIn (reset à minuit UTC, quotas non publiés).
    daily_quota: int = 0

    # Plan de contrôle /__admin. Fermé par défaut : il n'a de sens qu'en test.
    admin_enabled: bool = False
    admin_token: str = "mock-admin-token"

    # ── Évolution temporelle (extraction incrémentale) ───────────────────────
    # La page VIT : un événement scripté toutes les `evolution_interval`
    # secondes (stats du jour, nouveau post, gains d'abonnés, édition…).
    # false — ou intervalle à 0 — fige le jeu de données à l'octet près.
    evolution_enabled: bool = True
    evolution_interval: float = 60.0

    # Pagination Rest.li : start/count, défaut 10, plafond posts 100.
    default_count: int = 10
    posts_count_cap: int = 100

    def reload(self) -> None:
        self.access_token = os.environ.get("LINKEDIN_MOCK_ACCESS_TOKEN", "mock-linkedin-token")
        self.expired_token = os.environ.get(
            "LINKEDIN_MOCK_EXPIRED_TOKEN", "mock-linkedin-token-expired"
        )
        self.revoked_token = os.environ.get(
            "LINKEDIN_MOCK_REVOKED_TOKEN", "mock-linkedin-token-revoked"
        )
        self.org_id = os.environ.get("LINKEDIN_MOCK_ORG_ID", "40123456")
        self.seed = int(os.environ.get("LINKEDIN_MOCK_SEED", "42"))
        self.oldest_active_version = os.environ.get("LINKEDIN_MOCK_OLDEST_ACTIVE_VERSION", "202408")
        self.latest_active_version = os.environ.get("LINKEDIN_MOCK_LATEST_ACTIVE_VERSION", "202607")
        self.require_restli_2 = _flag("LINKEDIN_MOCK_REQUIRE_RESTLI_2", True)
        self.strict_shares_timebound = _flag("LINKEDIN_MOCK_STRICT_SHARES_TIMEBOUND", True)
        self.daily_quota = int(os.environ.get("LINKEDIN_MOCK_DAILY_QUOTA", "0"))
        self.admin_enabled = _flag("LINKEDIN_MOCK_ADMIN_ENABLED", False)
        self.admin_token = os.environ.get("LINKEDIN_MOCK_ADMIN_TOKEN", "mock-admin-token")
        self.evolution_enabled = _flag("LINKEDIN_MOCK_EVOLUTION", True)
        self.evolution_interval = float(os.environ.get("LINKEDIN_MOCK_EVOLUTION_INTERVAL", "60"))

    @property
    def organization_urn(self) -> str:
        return f"urn:li:organization:{self.org_id}"


settings = Settings()
settings.reload()
