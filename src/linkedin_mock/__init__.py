"""Mock de l'API LinkedIn versionnée — image container et paquet Python installable.

La surface reproduit la Community Management API (api.linkedin.com/rest,
en-têtes `Linkedin-Version` + `X-Restli-Protocol-Version`) sur UN jeu de
données réaliste et cohérent — la page LinkedIn de « Boréal Conseil », la même
ESN fictive que boondmanager-mock — qui ÉVOLUE dans le temps pour éprouver
l'extraction par fenêtres de dates et par snapshots (cf. evolution.py).

Deux modes d'utilisation, délibérément maintenus tous les deux :

  • **En process** — `TestClient(linkedin_mock.app)`. La propriété qu'il ne
    faut pas perdre : l'application que la stack interroge EST celle que les
    tests exercent.

  • **En conteneur** — `python -m linkedin_mock`, en docker compose comme en
    sidecar CI. C'est ce mode qui rend indispensable le plan de contrôle
    `/__admin` : hors du processus, on ne peut plus muter l'état en Python.

Ré-exports pour que rien n'ait besoin de connaître la structure interne :

    app              l'application FastAPI
    state            l'état mutable (dataset, reset)
    build_dataset    construction du jeu de données
    settings         la configuration (relue par reload())
    engine           le moteur d'injection de pannes
    contrat_openapi  le contrat OpenAPI (sans les affordances du mock)
"""

from __future__ import annotations

from .app import app, contrat_openapi
from .injection import engine
from .settings import settings
from .state import build_dataset, state

__all__ = [
    "app",
    "build_dataset",
    "contrat_openapi",
    "engine",
    "settings",
    "state",
]

__version__ = "0.1.0"
