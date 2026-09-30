"""Mock of the versioned LinkedIn API — container image and installable Python package.

The surface reproduces the Community Management API (api.linkedin.com/rest,
`Linkedin-Version` + `X-Restli-Protocol-Version` headers) against ONE
realistic, coherent dataset — the LinkedIn page of "Boréal Conseil", the same
fictional consultancy as boondmanager-mock — which EVOLVES over time to
exercise date-window and snapshot extraction (cf. evolution.py).

Two usage modes, deliberately kept both:

  • **In-process** — `TestClient(linkedin_mock.app)`. The property that must
    not be lost: the application the stack queries IS the one the tests
    exercise.

  • **Containerized** — `python -m linkedin_mock`, in docker compose as in a
    CI sidecar. This mode is what makes the `/__admin` control plane
    indispensable: outside the process, state can no longer be mutated from
    Python.

Re-exports so nothing needs to know the internal structure:

    app              the FastAPI application
    state            the mutable state (dataset, reset)
    build_dataset    dataset construction
    settings         the configuration (re-read by reload())
    engine           the failure injection engine
    openapi_contract the OpenAPI contract (without the mock's affordances)
"""

from __future__ import annotations

from .app import app, openapi_contract
from .injection import engine
from .settings import settings
from .state import build_dataset, state

__all__ = [
    "app",
    "build_dataset",
    "engine",
    "openapi_contract",
    "settings",
    "state",
]

__version__ = "0.1.0"
