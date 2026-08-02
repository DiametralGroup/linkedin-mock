SHELL := /bin/bash
.DEFAULT_GOAL := help
UV := uv

.PHONY: help bootstrap test lint format run image up contract

help:        ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

bootstrap:   ## Resolve and install the environment
	$(UV) sync

test:        ## pytest — dialect, consumer journeys, coherence, evolution, failure modes
	$(UV) run pytest tests/ -W ignore::DeprecationWarning

lint:        ## ruff + strict mypy
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy src/linkedin_mock

format:      ## Format the code
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

run:         ## Run the mock locally (admin plane open)
	LINKEDIN_MOCK_ADMIN_ENABLED=true $(UV) run python -m linkedin_mock

image:       ## Build the container image
	docker build -t linkedin-mock:dev .

up:          ## Run the mock in a container (docker compose up --build)
	docker compose up --build

contract:    ## Regenerate contracts/linkedin.openapi.yaml from the app
	@# `contrat_openapi()` rather than `app.openapi()`: the contract describes
	@# the LinkedIn DIALECT. /__admin is a mock affordance — publishing it would
	@# pass off as vendor API what is not, and it is only mounted conditionally,
	@# which would make the contract depend on the generation environment.
	$(UV) run python -c "import yaml, linkedin_mock as m; \
open('contracts/linkedin.openapi.yaml','w').write(yaml.safe_dump(m.contrat_openapi(), sort_keys=False, allow_unicode=True))"
	@echo "✓ contract regenerated — REVIEW the diff: a changed response shape is a contract change for consumers"
