"""Point d'entrée conteneur : `python -m linkedin_mock`.

Volontairement minimal — pas de CLI, pas d'options. Tout se configure par
variables d'environnement (cf. settings.py), parce que c'est le seul mécanisme
qui marche identiquement en docker compose, en Deployment Kubernetes et en
sidecar Tekton.
"""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    uvicorn.run(
        "linkedin_mock:app",
        host=os.environ.get("LINKEDIN_MOCK_HOST", "0.0.0.0"),
        port=int(os.environ.get("LINKEDIN_MOCK_PORT", "8000")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
