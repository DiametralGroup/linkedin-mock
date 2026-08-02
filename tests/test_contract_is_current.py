"""Le contrat committé dit-il la vérité ?

Deux propriétés, et la seconde est celle qui compte vraiment.

**Le contrat ne dérive pas.** `contracts/linkedin.openapi.yaml` est généré
depuis l'application, mais il est COMMITTÉ — la seule disposition où le
fichier est à la fois relisible dans un diff de PR et garanti exact.

**L'inventaire d'honnêteté est complet.** Tout champ marqué
`x-linkedin-confidence: unverified` DOIT figurer dans
docs/UNVERIFIED-FIELDS.md — un marqueur que personne ne relève est un
commentaire. L'honnêteté est une contrainte de build, pas une bonne intention.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

import linkedin_mock as mock

RACINE = Path(__file__).resolve().parents[1]
CONTRAT = RACINE / "contracts" / "linkedin.openapi.yaml"
REGISTRE = RACINE / "docs" / "UNVERIFIED-FIELDS.md"

FINDERS = (
    "/rest/posts",
    "/rest/organizationalEntityShareStatistics",
    "/rest/organizationalEntityFollowerStatistics",
    "/rest/organizationPageStatistics",
)


def _champs_marques(schemas: dict[str, Any], marqueur: str) -> dict[str, str]:
    """Rend {nom_de_champ: note} pour tous les champs portant ce niveau de confiance."""
    trouves: dict[str, str] = {}
    for schema in schemas.values():
        for nom, prop in (schema.get("properties") or {}).items():
            if prop.get("x-linkedin-confidence") == marqueur:
                trouves[nom] = prop.get("x-linkedin-note", "")
    return trouves


@pytest.fixture(scope="module")
def genere() -> dict[str, Any]:
    # `contrat_openapi()` et non `app.openapi()` : le contrat décrit le
    # dialecte LinkedIn. Comparer à l'application brute échouerait selon que
    # LINKEDIN_MOCK_ADMIN_ENABLED est vrai ou non au moment du run.
    return mock.contrat_openapi()


def test_le_contrat_committe_est_a_jour(genere: dict[str, Any]) -> None:
    """Le fichier committé == ce que l'application produit."""
    assert CONTRAT.exists(), f"{CONTRAT} absent — lancer `make contract`"
    committe = yaml.safe_load(CONTRAT.read_text(encoding="utf-8"))
    assert committe == genere, (
        "Le contrat committé a dérivé de l'application. Lancer `make contract`, "
        "RELIRE le diff — une forme de réponse qui change est un changement de "
        "contrat pour les consommateurs — puis committer."
    )


def test_le_contrat_porte_de_vraies_formes(genere: dict[str, Any]) -> None:
    """Un contrat sans schémas n'est pas un contrat."""
    schemas = genere.get("components", {}).get("schemas", {})
    assert len(schemas) > 15, f"seulement {len(schemas)} schémas — les routes sont-elles typées ?"

    for chemin in FINDERS:
        contenu = genere["paths"][chemin]["get"]["responses"]["200"]["content"]
        schema = contenu["application/json"]["schema"]
        assert "$ref" in schema or "allOf" in schema, (
            f"{chemin} ne déclare pas de forme de réponse exploitable : {schema}"
        )


def test_le_contrat_ne_publie_pas_les_affordances_du_mock(genere: dict[str, Any]) -> None:
    """`/__admin` n'est pas du LinkedIn — le publier ferait passer pour du
    fournisseur ce qui n'en est pas."""
    intrus = [c for c in genere["paths"] if c.startswith("/__admin")]
    assert not intrus, (
        f"le contrat publie des affordances du mock : {intrus}. "
        "Elles sont documentées dans le README, pas dans le contrat."
    )


@pytest.mark.parametrize("code", ["400", "401", "426", "429"])
def test_les_erreurs_sont_documentees(genere: dict[str, Any], code: str) -> None:
    """Les codes d'erreur font partie du contrat — un consommateur doit savoir
    qu'un 400 peut être VERSION_MISSING et qu'un 429 arrive SANS Retry-After."""
    reponses = genere["paths"]["/rest/organizationalEntityShareStatistics"]["get"]["responses"]
    assert code in reponses, f"le code {code} n'est pas documenté sur shareStatistics"


def test_tout_champ_non_verifie_est_inscrit_au_registre(genere: dict[str, Any]) -> None:
    """LE test qui rend l'honnêteté vérifiable."""
    schemas = genere.get("components", {}).get("schemas", {})
    marques = _champs_marques(schemas, "unverified")
    assert marques, (
        "AUCUN champ marqué `unverified`. Ce serait une bonne nouvelle si le "
        "dialecte était intégralement attesté — il ne l'est pas (corps des 401, "
        "paging.links, uniquePageViews quotidien…). Le marquage a-t-il été retiré ?"
    )

    registre = REGISTRE.read_text(encoding="utf-8")
    absents = sorted(nom for nom in marques if nom not in registre)
    assert not absents, (
        "Champs marqués `unverified` mais ABSENTS de docs/UNVERIFIED-FIELDS.md :\n  "
        + "\n  ".join(f"{n} — {marques[n]}" for n in absents)
        + "\n\nUn marqueur que personne ne relève est un commentaire. Inscrire "
        "chaque champ au registre, avec ce qu'il faudrait faire pour lever le doute."
    )


def test_les_champs_inventes_sont_signales_comme_tels(genere: dict[str, Any]) -> None:
    """`invented` est plus grave qu'`unverified` et doit rester exceptionnel."""
    schemas = genere.get("components", {}).get("schemas", {})
    inventes = _champs_marques(schemas, "invented")
    if not inventes:
        return
    registre = REGISTRE.read_text(encoding="utf-8")
    absents = sorted(nom for nom in inventes if nom not in registre)
    assert not absents, f"champs INVENTÉS absents du registre : {absents}"
