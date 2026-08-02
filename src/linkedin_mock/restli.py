"""Le dialecte Rest.li — LE module sans équivalent boondmanager-mock.

L'API versionnée parle Rest.li 2.0 : listes `List(a,b,c)`, objets parenthésés
`(timeRange:(start:MS,end:MS),timeGranularityType:DAY)`, URN percent-encodés en
position de valeur (`urn%3Ali%3Aorganization%3A40123456`). Sans l'en-tête
`X-Restli-Protocol-Version: 2.0.0`, la requête est interprétée en protocole
1.0 : paramètres pointés (`timeIntervals.timeRange.start=…`) et tableaux
indexés (`shares[0]=…`).

Ce que le parseur doit accepter — et que ce module centralise :

  • formes 2.0 brutes ET intégralement percent-encodées : Starlette décode une
    fois la query string, donc `%28timeRange…%29` et `(timeRange…)` arrivent
    identiques ; les exemples officiels montrent LES DEUX écritures ;
  • formes 1.0 pointées/indexées, montrées par les mêmes pages de doc ;
  • les virgules d'une List() ne sont jamais ambiguës : les URN n'en
    contiennent pas.

L'enveloppe de réponse est `{"elements": [...], "paging": {start, count,
links: []}}` — `links` est toujours vide ici : chaque exemple officiel le
montre vide et sa forme non vide n'est pas documentée (cf. registre). Un
consommateur pagine par arithmétique start/count et s'arrête sur page courte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_LISTE = re.compile(r"^List\((.*)\)$", re.DOTALL)
_START = re.compile(r"start:(\d+)")
_END = re.compile(r"end:(\d+)")
_GRANULARITE = re.compile(r"timeGranularityType:([A-Za-z_]+)")
_INDEXE = re.compile(r"^(?P<nom>[A-Za-z]+)\[(?P<index>\d+)\]$")

URN_ORGANISATION = re.compile(r"^urn:li:organization:(\d+)$")
URN_POST = re.compile(r"^urn:li:(share|ugcPost):(\d+)$")


def parse_liste(valeur: str) -> list[str] | None:
    """`List(a,b,c)` → [a, b, c] — None si la valeur n'est pas une List()."""
    m = _LISTE.match(valeur.strip())
    if m is None:
        return None
    interieur = m.group(1).strip()
    if not interieur:
        return []
    return [element.strip() for element in interieur.split(",")]


def liste_urns(params: dict[str, str], nom: str) -> list[str] | None:
    """Les URN d'un paramètre multi-valeurs, dans les DEUX protocoles.

    2.0 : `shares=List(urn%3A…,urn%3A…)` ;
    1.0 : `shares[0]=urn:…&shares[1]=urn:…` (ordre des index respecté).
    Rend None si le paramètre est absent sous les deux formes.
    """
    if (valeur := params.get(nom)) is not None:
        elements = parse_liste(valeur)
        if elements is not None:
            return elements
        # Valeur nue (un seul URN sans List()) — toléré, un stub ne casse pas.
        return [valeur]
    indexes: list[tuple[int, str]] = []
    for cle, valeur in params.items():
        m = _INDEXE.match(cle)
        if m is not None and m.group("nom") == nom:
            indexes.append((int(m.group("index")), valeur))
    if not indexes:
        return None
    return [v for _, v in sorted(indexes)]


@dataclass(frozen=True)
class Intervalle:
    """Le paramètre `timeIntervals`, une fois décodé."""

    start_ms: int | None
    end_ms: int | None
    granularite: str | None


def parse_time_intervals(params: dict[str, str]) -> Intervalle | None:
    """`timeIntervals` dans les deux protocoles, None s'il est absent.

    2.0 : `timeIntervals=(timeRange:(start:MS,end:MS),timeGranularityType:DAY)`
          — l'ordre des clés n'est pas garanti, chaque morceau est cherché
          indépendamment ;
    1.0 : `timeIntervals.timeRange.start=MS&timeIntervals.timeGranularityType=DAY`.
    """
    if (valeur := params.get("timeIntervals")) is not None:
        start = _START.search(valeur)
        end = _END.search(valeur)
        granularite = _GRANULARITE.search(valeur)
        return Intervalle(
            start_ms=int(start.group(1)) if start else None,
            end_ms=int(end.group(1)) if end else None,
            granularite=granularite.group(1) if granularite else None,
        )
    start_1 = params.get("timeIntervals.timeRange.start")
    end_1 = params.get("timeIntervals.timeRange.end")
    granularite_1 = params.get("timeIntervals.timeGranularityType")
    if start_1 is None and end_1 is None and granularite_1 is None:
        return None
    return Intervalle(
        start_ms=int(start_1) if start_1 and start_1.isdigit() else None,
        end_ms=int(end_1) if end_1 and end_1.isdigit() else None,
        granularite=granularite_1,
    )


def syntaxe_2_utilisee(params: dict[str, str]) -> bool:
    """La requête emploie-t-elle la syntaxe Rest.li 2.0 ?

    Sert au garde-fou `LINKEDIN_MOCK_REQUIRE_RESTLI_2` : List() ou objet
    parenthésé sans `X-Restli-Protocol-Version: 2.0.0`, c'est une requête qui
    ne marchera probablement pas contre l'API réelle.
    """
    return any(v.startswith(("List(", "(")) for v in params.values())


def lire_pagination(
    params: dict[str, str], *, defaut: int, plafond: int | None = None
) -> tuple[int, int] | None:
    """(start, count) — None si illisible ou hors bornes (→ 400 côté appelant)."""
    try:
        start = int(params.get("start", "0"))
        count = int(params.get("count", str(defaut)))
    except ValueError:
        return None
    if start < 0 or count < 1:
        return None
    if plafond is not None and count > plafond:
        return None
    return start, count


def enveloppe_elements(elements: list[dict[str, Any]], start: int, count: int) -> dict[str, Any]:
    """L'enveloppe Rest.li de collection — paging AVANT elements, comme les
    exemples officiels (l'ordre des clés JSON n'engage à rien, mais autant
    ressembler aux relevés)."""
    return {
        "paging": {"start": start, "count": count, "links": []},
        "elements": elements,
    }
