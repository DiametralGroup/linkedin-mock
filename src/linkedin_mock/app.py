"""Assemblage de l'application FastAPI.

UN pipeline par requête, dans cet ordre : injections → authentification →
version → dialecte Rest.li → handler. Le dispatch des pannes précède
l'authentification pour qu'`auth_reject` puisse la préempter, et toute route
`/rest` passe par le même prélude — il ne peut pas y avoir de route « oubliée »
où les pannes ne s'appliqueraient pas.

L'ordre auth-AVANT-version est une DÉCISION, pas un relevé : la précédence
réelle des deux contrôles n'est pas documentée (cf. registre ; la sonde
scripts/compare_real.py la mesure).

La surface reproduit l'API versionnée (api.linkedin.com/rest, Community
Management), confrontée à la doc officielle — monikers li-lms-2026-06/07 :

  • `GET /rest/posts?q=author` (finder), `?ids=List(...)` (batch), `/{urn}` ;
  • `GET /rest/organizationalEntityShareStatistics` — vie entière, per-share
    (`shares=`/`ugcPosts=`, zéro-stat OMIS), buckets via `timeIntervals` ;
    la combinaison per-share + timeIntervals est REFUSÉE par défaut : la doc
    dit « Time-bound statistics is not supported for specific share queries » ;
  • `GET /rest/organizationalEntityFollowerStatistics` — 7 facettes en vie
    entière, `followerGains` par bucket (DAY/WEEK/MONTH, start OBLIGATOIRE) ;
  • `GET /rest/organizationPageStatistics` — piège du dialecte : le finder est
    `q=organization` et le paramètre `organization` ;
  • `GET /rest/organizations/{id}` (+ batch, + q=vanityName),
    `GET /rest/networkSizes/{urn}` ;
  • les routes inconnues rendent l'enveloppe d'erreur LinkedIn, pas le 404
    FastAPI.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import restli, stats
from .auth import verifier_bearer
from .errors import (
    MSG_ORG_INACTIVE,
    corps_erreur,
    erreur,
    erreur_acces_refuse,
    erreur_quota,
    erreur_route_inconnue,
    erreur_token_expire,
    erreur_token_invalide,
    erreur_token_revoque,
    erreur_token_vide,
    erreur_version_inactive,
)
from .injection import engine
from .models import (
    REPONSES_ERREUR,
    ElementStatsAbonnes,
    ElementStatsPage,
    ElementStatsPartage,
    EnveloppeElements,
    LotOrganisations,
    Organisation,
    Post,
    TailleReseau,
)
from .settings import settings
from .state import state
from .versioning import verifier_version

_REJETS_AUTH = {
    "empty": erreur_token_vide,
    "invalid": erreur_token_invalide,
    "expired": erreur_token_expire,
    "revoked": erreur_token_revoque,
}


def _dispatch_injections(path: str, rang_du_jour: int) -> JSONResponse | None:
    """Le point de dispatch unique des pannes. Ordre significatif :
    auth_reject préempte l'authentification réelle ; latency s'applique même
    quand la requête finit par réussir ; rate_limit dépend du compteur du
    jour ; status est la panne franche."""
    if (regle := engine.first("auth_reject", path)) is not None and regle.consume():
        return _REJETS_AUTH.get(regle.variant, erreur_token_invalide)()

    if (regle := engine.first("latency", path)) is not None and regle.consume():
        # Un vrai sleep : c'est le seul moyen d'éprouver un timeout côté client.
        time.sleep(regle.seconds)

    if (
        (regle := engine.first("rate_limit", path)) is not None
        and rang_du_jour > regle.after_requests
        and regle.consume()
    ):
        # Quota JOURNALIER, sans Retry-After — le régime LinkedIn : le compteur
        # repart à zéro à minuit UTC (virtuel), pas après un délai annoncé.
        return erreur_quota()

    if (regle := engine.first("status", path)) is not None and regle.consume():
        return erreur(regle.status, f"mock: injected {regle.status} on {path}")
    return None


def _controles_transport(
    request: Request, path: str, params: dict[str, str]
) -> JSONResponse | None:
    """Auth, version, protocole Rest.li — l'ordre auth-avant-version est une
    décision documentée (précédence réelle non attestée)."""
    if (refus := verifier_bearer(request)) is not None:
        return refus

    if (regle := engine.first("version_reject", path)) is not None and regle.consume():
        return erreur_version_inactive(request.headers.get("Linkedin-Version", "000000"))

    if (refus := verifier_version(request)) is not None:
        return refus

    if (
        settings.require_restli_2
        and restli.syntaxe_2_utilisee(params)
        and request.headers.get("X-Restli-Protocol-Version") != "2.0.0"
    ):
        return erreur(
            400,
            "Rest.li 2.0 syntax requires the X-Restli-Protocol-Version: 2.0.0 header",
            code="RESTLI_PROTOCOL_VERSION_MISSING",
        )
    return None


def _prelude(request: Request, path: str) -> JSONResponse | None:
    """Le pipeline commun à toute route /rest : pannes, auth, version, Rest.li."""
    params = dict(request.query_params)
    state.avancer_evolution(engine.now())
    rang_du_jour = engine.observe(path, params, stats.jour_virtuel().isoformat())

    if (refus := _dispatch_injections(path, rang_du_jour)) is not None:
        return refus
    return _controles_transport(request, path, params)


def _garde_finder(params: dict[str, str], q_attendu: str, param_entite: str) -> JSONResponse | None:
    """Les gardes communs des finders de statistiques : `q` et l'URN d'entité."""
    q = params.get("q")
    if q is None:
        return erreur(400, "Query parameter 'q' is required on this resource")
    if q != q_attendu:
        return erreur(400, f"Unknown query 'q={q}' on this resource")
    entite = params.get(param_entite)
    if entite is None:
        return erreur(400, f"Parameter '{param_entite}' is required")
    if restli.URN_ORGANISATION.match(entite) is None:
        return erreur(400, f"Invalid urn type in {param_entite}: {entite}", code="INVALID_URN_TYPE")
    if entite != settings.organization_urn:
        return erreur_acces_refuse(f"the ADMIN_ONLY VisibilityReduction for {entite}")
    return None


def _granularite_invalide(
    intervalle: restli.Intervalle, autorisees: tuple[str, ...]
) -> JSONResponse | None:
    if intervalle.granularite not in autorisees:
        return erreur(
            400,
            f"Invalid timeGranularityType: {intervalle.granularite!r} "
            f"(expected one of {', '.join(autorisees)})",
        )
    if intervalle.start_ms is None:
        return erreur(400, "timeIntervals.timeRange.start is required")
    return None


def _fin_par_defaut(intervalle: restli.Intervalle) -> int:
    if intervalle.end_ms is not None:
        return intervalle.end_ms
    return int(stats.maintenant_virtuel().timestamp() * 1000)


def _paging_echo(params: dict[str, str]) -> tuple[int, int]:
    """Les statistiques ne paginent pas ; `paging` fait seulement écho aux
    paramètres (comportement des exemples officiels : paging {count:10,
    start:0} avec tous les éléments)."""
    pagination = restli.lire_pagination(params, defaut=settings.default_count)
    return pagination if pagination is not None else (0, settings.default_count)


# ─────────────────────────────────────────────────────────────────────────────
#  Application
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="LinkedIn mock", version="0.1.0", docs_url="/docs")
rest = APIRouter(prefix="/rest")


@app.exception_handler(StarletteHTTPException)
async def _erreur_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Routes et méthodes inconnues : l'enveloppe LinkedIn, pas le 404 FastAPI."""
    if exc.status_code == 404:
        return erreur_route_inconnue(request.url.path)
    if exc.status_code == 405:
        return erreur(405, f"Method {request.method} not allowed", code="METHOD_NOT_ALLOWED")
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Unauthenticated — it is a probe, not an entity."""
    return {"status": "ok", "service": "linkedin-mock"}


# ── Posts ────────────────────────────────────────────────────────────────────


def _garde_auteur(params: dict[str, str]) -> JSONResponse | None:
    """Les gardes du finder posts : `q=author` et l'URN de l'organisation."""
    q = params.get("q")
    if q != "author":
        message = (
            "Query parameter 'q' is required on this resource"
            if q is None
            else f"Unknown query 'q={q}' on this resource"
        )
        return erreur(400, message)
    auteur = params.get("author")
    if auteur is None:
        return erreur(400, "Parameter 'author' is required")
    if restli.URN_ORGANISATION.match(auteur) is None:
        return erreur(400, f"Invalid urn type in author: {auteur}", code="INVALID_URN_TYPE")
    if auteur != settings.organization_urn:
        return erreur_acces_refuse(f"posts of {auteur}")
    tri = params.get("sortBy", "LAST_MODIFIED")
    if tri not in ("LAST_MODIFIED", "CREATED"):
        return erreur(400, f"Invalid value for sortBy: {tri}", code="INVALID_VALUE_FOR_FIELD")
    return None


@rest.get(
    "/posts",
    response_model=EnveloppeElements[Post],
    responses=REPONSES_ERREUR,
    summary="Finder by author — the organization's posts",
    description=(
        "Finder `q=author&author={urn}` : tri `sortBy=LAST_MODIFIED` (défaut) "
        "ou `CREATED`, descendant ; pagination `start`/`count` (défaut 10, "
        "plafond 100), fin de données = page courte. La MÊME route sert le "
        "batch get `?ids=List(urn,urn)` — réponse `{results, statuses, errors}` "
        "par URN, non décrite par ce schéma."
    ),
)
def lister_posts(request: Request) -> JSONResponse:
    path = "/rest/posts"
    if (refus := _prelude(request, path)) is not None:
        return refus
    params = dict(request.query_params)

    if (ids := restli.liste_urns(params, "ids")) is not None:
        return _lot_posts(ids)

    if (refus := _garde_auteur(params)) is not None:
        return refus
    tri = params.get("sortBy", "LAST_MODIFIED")
    pagination = restli.lire_pagination(
        params, defaut=settings.default_count, plafond=settings.posts_count_cap
    )
    if pagination is None:
        return erreur(400, "Invalid pagination parameters: start/count")
    start, count = pagination

    cle = "lastModifiedAt" if tri == "LAST_MODIFIED" else "createdAt"
    tries = sorted(state.dataset["posts"], key=lambda p: (int(p[cle]), p["id"]), reverse=True)

    decalage = 0
    regle = engine.first("page_drift", path)
    if regle is not None and start > 0 and regle.consume():
        # Un post publié entre deux pages : tout glisse d'un cran — un élément
        # est servi deux fois (insert) ou jamais (remove). La raison d'être du
        # merge sur clé côté pipeline.
        decalage = -1 if regle.mode == "insert" else 1

    debut = max(0, start + decalage)
    return JSONResponse(restli.enveloppe_elements(tries[debut : debut + count], start, count))


def _lot_posts(ids: list[str]) -> JSONResponse:
    """`GET /rest/posts?ids=List(...)` — resultats/statuts/erreurs par URN."""
    index = {p["id"]: p for p in state.dataset["posts"]}
    resultats: dict[str, Any] = {}
    erreurs: dict[str, Any] = {}
    statuts: dict[str, int] = {}
    for urn in ids:
        if urn in index:
            resultats[urn] = index[urn]
        else:
            statuts[urn] = 404
            erreurs[urn] = corps_erreur(404, f"Cannot find entity {urn}", code="NOT_FOUND")
    return JSONResponse({"results": resultats, "statuses": statuts, "errors": erreurs})


@rest.get(
    "/posts/{post_urn}",
    response_model=Post,
    responses=REPONSES_ERREUR,
    summary="Get a post by URN (URL-encoded)",
)
def lire_post(request: Request, post_urn: str) -> JSONResponse:
    if (refus := _prelude(request, f"/rest/posts/{post_urn}")) is not None:
        return refus
    if restli.URN_POST.match(post_urn) is None:
        return erreur(400, f"Invalid urn type in id: {post_urn}", code="INVALID_URN_TYPE")
    for post in state.dataset["posts"]:
        if post["id"] == post_urn:
            return JSONResponse(post)
    return erreur(404, f"Cannot find entity {post_urn}", code="NOT_FOUND")


# ── Statistiques de partage ──────────────────────────────────────────────────


@rest.get(
    "/organizationalEntityShareStatistics",
    response_model=EnveloppeElements[ElementStatsPartage],
    responses=REPONSES_ERREUR,
    summary="Share statistics — lifetime, per-share, or time-bound buckets",
    description=(
        "Sans paramètre : l'agrégat organisation vie entière (fenêtre glissante "
        "de 12 mois sur les buckets). `shares=List(...)`/`ugcPosts=List(...)` : "
        "un élément par post ACTIF — les posts sans activité sont omis "
        "(« can be assumed to have counts of 0 »). `timeIntervals=(timeRange:"
        "(start:ms,end:ms),timeGranularityType:DAY|MONTH)` : un élément par "
        "bucket. La combinaison per-share + timeIntervals est refusée par "
        "défaut — comportement documenté de l'API réelle."
    ),
)
def stats_partage(request: Request) -> JSONResponse:
    if (refus := _prelude(request, "/rest/organizationalEntityShareStatistics")) is not None:
        return refus
    params = dict(request.query_params)
    if (refus := _garde_finder(params, "organizationalEntity", "organizationalEntity")) is not None:
        return refus

    partages = restli.liste_urns(params, "shares")
    ugc = restli.liste_urns(params, "ugcPosts")
    urns: list[str] = []
    for liste, prefixe in ((partages, "urn:li:share:"), (ugc, "urn:li:ugcPost:")):
        for urn in liste or []:
            if restli.URN_POST.match(urn) is None or not urn.startswith(prefixe):
                return erreur(400, f"Invalid urn type: {urn}", code="INVALID_URN_TYPE")
            urns.append(urn)

    intervalle = restli.parse_time_intervals(params)
    start, count = _paging_echo(params)

    if intervalle is not None and urns and settings.strict_shares_timebound:
        return erreur(400, "Time-bound statistics is not supported for specific share queries")

    if intervalle is not None:
        if (refus := _granularite_invalide(intervalle, stats.GRANULARITES_PARTAGE)) is not None:
            return refus
        elements = stats.elements_partage_buckets(
            intervalle.start_ms or 0,
            _fin_par_defaut(intervalle),
            intervalle.granularite or "DAY",
            urns=urns or None,
        )
    elif urns:
        elements = stats.elements_partage_par_post(urns)
    else:
        elements = [stats.element_partage_vie()]
    return JSONResponse(restli.enveloppe_elements(elements, start, count))


# ── Statistiques d'abonnés ───────────────────────────────────────────────────


@rest.get(
    "/organizationalEntityFollowerStatistics",
    response_model=EnveloppeElements[ElementStatsAbonnes],
    responses=REPONSES_ERREUR,
    summary="Follower statistics — demographics (lifetime) or daily gains",
    description=(
        "Vie entière : les 7 familles de facettes démographiques (chacune "
        "couvre MOINS que le total — le total vit sur /networkSizes). "
        "Time-bound (DAY|WEEK|MONTH, `timeRange.start` OBLIGATOIRE) : "
        "`followerGains` par bucket, données disponibles de J-365 à J-2 UTC."
    ),
)
def stats_abonnes(request: Request) -> JSONResponse:
    if (refus := _prelude(request, "/rest/organizationalEntityFollowerStatistics")) is not None:
        return refus
    params = dict(request.query_params)
    if (refus := _garde_finder(params, "organizationalEntity", "organizationalEntity")) is not None:
        return refus

    intervalle = restli.parse_time_intervals(params)
    start, count = _paging_echo(params)
    if intervalle is not None:
        if (refus := _granularite_invalide(intervalle, stats.GRANULARITES_ABONNES)) is not None:
            return refus
        elements = stats.elements_abonnes_buckets(
            intervalle.start_ms or 0,
            _fin_par_defaut(intervalle),
            intervalle.granularite or "DAY",
        )
    else:
        elements = [stats.element_abonnes_vie()]
    return JSONResponse(restli.enveloppe_elements(elements, start, count))


# ── Statistiques de page ─────────────────────────────────────────────────────


@rest.get(
    "/organizationPageStatistics",
    response_model=EnveloppeElements[ElementStatsPage],
    responses=REPONSES_ERREUR,
    summary="Page statistics — views (lifetime) or daily buckets",
    description=(
        "PIÈGE du dialecte : le finder est `q=organization` et le paramètre "
        "`organization` — pas `organizationalEntity` comme les deux autres. "
        "Vie entière : `totalPageStatistics` (15 compteurs de vues, "
        "arithmétique vérifiée) + 6 facettes. Time-bound (DAY|MONTH) : jeu de "
        "familles réduit, avec `uniquePageViews`."
    ),
)
def stats_page(request: Request) -> JSONResponse:
    if (refus := _prelude(request, "/rest/organizationPageStatistics")) is not None:
        return refus
    params = dict(request.query_params)
    if (refus := _garde_finder(params, "organization", "organization")) is not None:
        return refus

    intervalle = restli.parse_time_intervals(params)
    start, count = _paging_echo(params)
    if intervalle is not None:
        if (refus := _granularite_invalide(intervalle, stats.GRANULARITES_PAGE)) is not None:
            return refus
        elements = stats.elements_page_buckets(
            intervalle.start_ms or 0,
            _fin_par_defaut(intervalle),
            intervalle.granularite or "DAY",
        )
    else:
        elements = [stats.element_page_vie()]
    return JSONResponse(restli.enveloppe_elements(elements, start, count))


# ── Organisations & réseau ───────────────────────────────────────────────────


@rest.get(
    "/organizations",
    response_model=LotOrganisations,
    responses=REPONSES_ERREUR,
    summary="Batch get (?ids=List) or finder by vanityName",
    description=(
        "Batch `?ids=List(40123456,27056405)` : `statuses` porte le code PAR "
        "id (200/403). Finder `?q=vanityName&vanityName=…` : enveloppe "
        "elements/paging avec `total` — un des rares finders qui l'émettent."
    ),
)
def organisations(request: Request) -> JSONResponse:
    if (refus := _prelude(request, "/rest/organizations")) is not None:
        return refus
    params = dict(request.query_params)
    organisation = state.dataset["organisation"]

    if (ids := restli.liste_urns(params, "ids")) is not None:
        resultats: dict[str, Any] = {}
        statuts: dict[str, int] = {}
        erreurs: dict[str, Any] = {}
        for ident in ids:
            if ident == settings.org_id:
                resultats[ident] = organisation
                statuts[ident] = 200
            else:
                statuts[ident] = 403
                erreurs[ident] = corps_erreur(
                    403,
                    "Viewer don't have permission to the ADMIN_ONLY VisibilityReduction "
                    f"for urn:li:organization:{ident}",
                    service_error_code=100,
                    code="ACCESS_DENIED",
                )
        return JSONResponse({"results": resultats, "statuses": statuts, "errors": erreurs})

    q = params.get("q")
    if q != "vanityName":
        return erreur(400, f"Unknown query 'q={q}' on this resource")
    vanite = params.get("vanityName", "")
    elements = [organisation] if vanite == organisation["vanityName"] else []
    corps = restli.enveloppe_elements(elements, 0, 10)
    corps["paging"]["total"] = len(elements)
    return JSONResponse(corps)


@rest.get(
    "/organizations/{org_id}",
    response_model=Organisation,
    responses=REPONSES_ERREUR,
    summary="Organization lookup — the credentials smoke test",
    description=(
        "L'entité plate Rest.li : `id` est un NOMBRE et `$URN` est présent. "
        "C'est l'appel que le connecteur fait AVANT d'ouvrir son pipeline — "
        "l'analogue du current-user de BoondManager."
    ),
)
def lire_organisation(request: Request, org_id: str) -> JSONResponse:
    if (refus := _prelude(request, f"/rest/organizations/{org_id}")) is not None:
        return refus
    if not org_id.isdigit():
        return erreur(400, f"Invalid organization id: {org_id}")
    if org_id != settings.org_id:
        return erreur(404, MSG_ORG_INACTIVE.format(org_id=org_id), code="NOT_FOUND")
    return JSONResponse(state.dataset["organisation"])


@rest.get(
    "/networkSizes/{entity_urn}",
    response_model=TailleReseau,
    responses=REPONSES_ERREUR,
    summary="firstDegreeSize — THE follower total",
    description=(
        "`?edgeType=COMPANY_FOLLOWED_BY_MEMBER` obligatoire. Les follower "
        "statistics n'ont PLUS de total : il vit ici."
    ),
)
def taille_reseau(request: Request, entity_urn: str) -> JSONResponse:
    if (refus := _prelude(request, f"/rest/networkSizes/{entity_urn}")) is not None:
        return refus
    if request.query_params.get("edgeType") != "COMPANY_FOLLOWED_BY_MEMBER":
        return erreur(400, "Parameter 'edgeType' is required (COMPANY_FOLLOWED_BY_MEMBER)")
    if restli.URN_ORGANISATION.match(entity_urn) is None:
        return erreur(400, f"Invalid urn type: {entity_urn}", code="INVALID_URN_TYPE")
    if entity_urn != settings.organization_urn:
        return erreur_acces_refuse(f"the ADMIN_ONLY VisibilityReduction for {entity_urn}")
    return JSONResponse({"firstDegreeSize": stats.total_abonnes()})


app.include_router(rest)

# Le plan de contrôle n'est pas « monté puis interdit » : quand il est
# désactivé, la surface n'existe pas.
if settings.admin_enabled:
    from .admin import router as admin_router

    app.include_router(admin_router)


# ─────────────────────────────────────────────────────────────────────────────
#  Le contrat
# ─────────────────────────────────────────────────────────────────────────────


def contrat_openapi() -> dict[str, Any]:
    """Le contrat OpenAPI — le DIALECTE LinkedIn, et lui seul.

    Les chemins `/__admin` sont RETIRÉS : ce sont des affordances du mock, pas
    du fournisseur — et le routeur n'est monté que si
    LINKEDIN_MOCK_ADMIN_ENABLED est vrai, ce qui ferait dépendre le contrat de
    l'environnement de génération.
    """
    spec = app.openapi()
    spec["paths"] = {
        chemin: op for chemin, op in spec["paths"].items() if not chemin.startswith("/__admin")
    }
    _elaguer_schemas_orphelins(spec)
    return spec


def _elaguer_schemas_orphelins(spec: dict[str, Any]) -> None:
    """Retire les schémas que plus aucun chemin ne référence.

    Sans cet élagage, le contrat committé contiendrait `HTTPValidationError`
    uniquement quand /__admin était monté au moment de la génération — et le
    test anti-dérive échouerait selon l'environnement.
    """
    schemas = spec.get("components", {}).get("schemas", {})
    if not schemas:
        return

    def refs(noeud: Any) -> set[str]:
        trouves: set[str] = set()
        if isinstance(noeud, dict):
            for cle, valeur in noeud.items():
                if cle == "$ref" and isinstance(valeur, str):
                    trouves.add(valeur.rsplit("/", 1)[-1])
                else:
                    trouves |= refs(valeur)
        elif isinstance(noeud, list):
            for element in noeud:
                trouves |= refs(element)
        return trouves

    gardes = refs(spec["paths"])
    a_explorer = set(gardes)
    while a_explorer:
        nom = a_explorer.pop()
        for suivant in refs(schemas.get(nom, {})):
            if suivant not in gardes:
                gardes.add(suivant)
                a_explorer.add(suivant)

    spec["components"]["schemas"] = {n: c for n, c in schemas.items() if n in gardes}
