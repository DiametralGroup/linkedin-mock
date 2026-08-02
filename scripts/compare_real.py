"""Sonde structurelle GET-only contre la VRAIE API LinkedIn.

Objectif : trancher les entrées de docs/UNVERIFIED-FIELDS.md — pas extraire
des données. Chaque sonde fait UN GET, relève le statut et la FORME (clés,
types), et la confronte à ce que le mock sert. Aucune écriture, aucune
mutation, aucun POST.

Usage :

    LINKEDIN_REAL_TOKEN=... LINKEDIN_REAL_ORG_ID=12345 \\
        uv run python scripts/compare_real.py [--version 202506]

La PREMIÈRE sonde est la seule qui engage une décision d'architecture :
`shares=List(...)` + `timeIntervals` combinés — la doc dit « not supported »,
le connecteur insights360 a choisi les snapshots quotidiens en conséquence.
Si la vraie API sert la combinaison, ouvrir une issue : le connecteur peut
gagner un backfill par post.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

VRAI_HOTE = "https://api.linkedin.com/rest"


def _forme(noeud: Any, profondeur: int = 0) -> Any:
    """La forme d'un JSON : clés et types, valeurs élaguées."""
    if profondeur > 4:
        return "…"
    if isinstance(noeud, dict):
        return {cle: _forme(valeur, profondeur + 1) for cle, valeur in sorted(noeud.items())}
    if isinstance(noeud, list):
        return [_forme(noeud[0], profondeur + 1)] if noeud else []
    return type(noeud).__name__


def _get(url: str, jeton: str, version: str) -> tuple[int, Any]:
    requete = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {jeton}",
            "Linkedin-Version": version,
            "X-Restli-Protocol-Version": "2.0.0",
        },
    )
    try:
        with urllib.request.urlopen(requete, timeout=30) as reponse:
            return reponse.status, json.loads(reponse.read().decode())
    except urllib.error.HTTPError as erreur:
        try:
            corps = json.loads(erreur.read().decode())
        except Exception:
            corps = {"brut": "corps illisible"}
        return erreur.code, corps
    except urllib.error.URLError as erreur:
        return 0, {"transport": str(erreur)}


def _sondes(org: str, urn_share: str | None) -> list[tuple[str, str]]:
    urn_org = f"urn%3Ali%3Aorganization%3A{org}"
    fenetre = (
        "timeIntervals=(timeRange:(start:1767225600000,end:1767830400000),timeGranularityType:DAY)"
    )
    base_partage = (
        f"{VRAI_HOTE}/organizationalEntityShareStatistics"
        f"?q=organizationalEntity&organizationalEntity={urn_org}"
    )
    sondes = [
        # LA sonde prioritaire — cf. docstring.
        (
            "shares+timeIntervals combinés (PRIORITAIRE)",
            f"{base_partage}&shares=List({urn_share})&{fenetre}"
            if urn_share
            else f"{base_partage}&shares=List(urn%3Ali%3Ashare%3A0)&{fenetre}",
        ),
        ("organization lookup", f"{VRAI_HOTE}/organizations/{org}"),
        (
            "networkSizes",
            f"{VRAI_HOTE}/networkSizes/{urn_org}?edgeType=COMPANY_FOLLOWED_BY_MEMBER",
        ),
        ("posts finder page 1", f"{VRAI_HOTE}/posts?q=author&author={urn_org}&count=3"),
        ("posts finder SANS q", f"{VRAI_HOTE}/posts?author={urn_org}"),
        ("posts count>100", f"{VRAI_HOTE}/posts?q=author&author={urn_org}&count=101"),
        ("share stats vie entière", base_partage),
        ("share stats daily", f"{base_partage}&{fenetre}"),
        (
            "follower stats vie entière",
            f"{VRAI_HOTE}/organizationalEntityFollowerStatistics"
            f"?q=organizationalEntity&organizationalEntity={urn_org}",
        ),
        (
            "page stats vie entière",
            f"{VRAI_HOTE}/organizationPageStatistics?q=organization&organization={urn_org}",
        ),
        (
            "page stats MAUVAIS finder (organizationalEntity)",
            f"{VRAI_HOTE}/organizationPageStatistics"
            f"?q=organizationalEntity&organizationalEntity={urn_org}",
        ),
        ("route inconnue", f"{VRAI_HOTE}/nimporte"),
        ("organisation étrangère (403 attendu)", f"{VRAI_HOTE}/organizations/1337"),
    ]
    return sondes


def _sondes_sans_jeton(org: str) -> list[tuple[str, str, dict[str, str]]]:
    """Les précédences : sans jeton, sans version — qui gagne ?"""
    urn_org = f"urn%3Ali%3Aorganization%3A{org}"
    url = f"{VRAI_HOTE}/posts?q=author&author={urn_org}"
    return [
        ("ni jeton ni version (précédence auth/version)", url, {}),
        ("jeton sans version (VERSION_MISSING attendu)", url, {"Authorization": "Bearer …"}),
        (
            "jeton forgé (corps 401 invalid)",
            url,
            {
                "Authorization": "Bearer jeton-forge-pour-la-sonde",
                "Linkedin-Version": "202506",
                "X-Restli-Protocol-Version": "2.0.0",
            },
        ),
    ]


def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--version", default="202506", help="Linkedin-Version à envoyer")
    analyseur.add_argument(
        "--share-urn", default=None, help="URN share réel (encodé) pour la sonde prioritaire"
    )
    arguments = analyseur.parse_args()

    jeton = os.environ.get("LINKEDIN_REAL_TOKEN", "")
    org = os.environ.get("LINKEDIN_REAL_ORG_ID", "")
    if not jeton or not org:
        print("LINKEDIN_REAL_TOKEN et LINKEDIN_REAL_ORG_ID sont requis.", file=sys.stderr)
        return 2

    print(f"# Sondes structurelles — {VRAI_HOTE}, version {arguments.version}\n")
    for nom, url in _sondes(org, arguments.share_urn):
        statut, corps = _get(url, jeton, arguments.version)
        print(f"## {nom}\n   GET {url}\n   → HTTP {statut}")
        print("   " + json.dumps(_forme(corps), ensure_ascii=False)[:600] + "\n")

    print("# Précédences d'erreurs (requêtes volontairement malformées)\n")
    for nom, url, en_tetes in _sondes_sans_jeton(org):
        requete = urllib.request.Request(url, headers=en_tetes)
        try:
            with urllib.request.urlopen(requete, timeout=30) as reponse:
                statut, corps = reponse.status, json.loads(reponse.read().decode())
        except urllib.error.HTTPError as erreur:
            try:
                statut, corps = erreur.code, json.loads(erreur.read().decode())
            except Exception:
                statut, corps = erreur.code, {}
        print(f"## {nom}\n   → HTTP {statut} {json.dumps(corps, ensure_ascii=False)[:300]}\n")

    print(
        "Confronter chaque relevé à docs/UNVERIFIED-FIELDS.md, corriger les\n"
        "constantes (errors.py, models/) et RÉGÉNÉRER le contrat (make contract)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
