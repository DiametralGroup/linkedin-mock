"""Does the committed contract tell the truth?

Two properties, and the second is the one that really matters.

**The contract does not drift.** `contracts/linkedin.openapi.yaml` is
generated from the application, but it is COMMITTED — the only arrangement
where the file is both readable in a PR diff and guaranteed exact.

**The honesty inventory is complete.** Every field tagged
`x-linkedin-confidence: unverified` MUST appear in
docs/UNVERIFIED-FIELDS.md — a marker nobody follows up on is just a
comment. Honesty is a build constraint, not a good intention.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

import linkedin_mock as mock

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "linkedin.openapi.yaml"
REGISTRY = ROOT / "docs" / "UNVERIFIED-FIELDS.md"

FINDERS = (
    "/rest/posts",
    "/rest/organizationalEntityShareStatistics",
    "/rest/organizationalEntityFollowerStatistics",
    "/rest/organizationPageStatistics",
)


def _tagged_fields(schemas: dict[str, Any], marker: str) -> dict[str, str]:
    """Return {field_name: note} for every field carrying this confidence level."""
    found: dict[str, str] = {}
    for schema in schemas.values():
        for name, prop in (schema.get("properties") or {}).items():
            if prop.get("x-linkedin-confidence") == marker:
                found[name] = prop.get("x-linkedin-note", "")
    return found


@pytest.fixture(scope="module")
def generated() -> dict[str, Any]:
    # `openapi_contract()` and not `app.openapi()`: the contract describes
    # the LinkedIn dialect. Comparing against the raw application would fail
    # depending on whether LINKEDIN_MOCK_ADMIN_ENABLED is true at run time.
    return mock.openapi_contract()


def test_committed_contract_is_current(generated: dict[str, Any]) -> None:
    """The committed file == what the application produces."""
    assert CONTRACT.exists(), f"{CONTRACT} missing — run `make contract`"
    committed = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    assert committed == generated, (
        "The committed contract has drifted from the application. Run "
        "`make contract`, READ the diff — a changing response shape is a "
        "contract change for consumers — then commit it."
    )


def test_contract_carries_real_shapes(generated: dict[str, Any]) -> None:
    """A contract with no schemas is not a contract."""
    schemas = generated.get("components", {}).get("schemas", {})
    assert len(schemas) > 15, f"only {len(schemas)} schemas — are the routes typed?"

    for path in FINDERS:
        content = generated["paths"][path]["get"]["responses"]["200"]["content"]
        schema = content["application/json"]["schema"]
        assert "$ref" in schema or "allOf" in schema, (
            f"{path} does not declare a usable response shape: {schema}"
        )


def test_contract_does_not_leak_mock_affordances(generated: dict[str, Any]) -> None:
    """`/__admin` is not LinkedIn — publishing it would pass off the mock's
    own affordances as the provider's."""
    leaks = [p for p in generated["paths"] if p.startswith("/__admin")]
    assert not leaks, (
        f"the contract publishes mock affordances: {leaks}. "
        "They're documented in the README, not in the contract."
    )


@pytest.mark.parametrize("code", ["400", "401", "426", "429"])
def test_errors_are_documented(generated: dict[str, Any], code: str) -> None:
    """Error codes are part of the contract — a consumer must know that a
    400 can be VERSION_MISSING and a 429 arrives WITHOUT Retry-After."""
    responses = generated["paths"]["/rest/organizationalEntityShareStatistics"]["get"]["responses"]
    assert code in responses, f"code {code} is not documented on shareStatistics"


def test_every_unverified_field_is_registered(generated: dict[str, Any]) -> None:
    """THE test that makes honesty verifiable."""
    schemas = generated.get("components", {}).get("schemas", {})
    tagged = _tagged_fields(schemas, "unverified")
    assert tagged, (
        "NO field tagged `unverified`. That would be good news if the "
        "dialect were fully attested — it isn't (401 bodies, paging.links, "
        "daily uniquePageViews…). Was the tagging removed?"
    )

    registry = REGISTRY.read_text(encoding="utf-8")
    missing = sorted(name for name in tagged if name not in registry)
    assert not missing, (
        "Fields tagged `unverified` but ABSENT from docs/UNVERIFIED-FIELDS.md:\n  "
        + "\n  ".join(f"{n} — {tagged[n]}" for n in missing)
        + "\n\nA marker nobody follows up on is just a comment. Register "
        "every field, with what it would take to resolve the doubt."
    )


def test_invented_fields_are_flagged_as_such(generated: dict[str, Any]) -> None:
    """`invented` is more serious than `unverified` and must stay exceptional."""
    schemas = generated.get("components", {}).get("schemas", {})
    invented = _tagged_fields(schemas, "invented")
    if not invented:
        return
    registry = REGISTRY.read_text(encoding="utf-8")
    missing = sorted(name for name in invented if name not in registry)
    assert not missing, f"INVENTED fields absent from the registry: {missing}"
