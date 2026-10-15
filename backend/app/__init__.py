"""Fabrique de l'application FastAPI.

POURQUOI UNE FABRIQUE ET NON UN `app = FastAPI()` DE MODULE
-----------------------------------------------------------
L'instance vivait au niveau du module, entourée de tout ce qui la configurait :
routes décorées, middleware, moteurs chargés, thread de pré-chauffage lancé.
Importer le module — pour un test, pour un script d'administration, pour lire
une constante — exécutait donc TOUT cela.

Avec `create_app()`, l'import ne fait rien ; c'est l'appel qui construit. Un
test peut monter une instance propre, un script peut importer un service sans
réveiller LibreOffice, et l'ordre de construction est lisible en un écran.

L'ORDRE DES DÉPENDANCES
-----------------------
    app.api  ->  app.services  ->  engines  ->  (rien du projet)
    app.api  ->  app.core      ->  (rien du projet)

Aucune flèche ne remonte. C'est ce qui permet de sortir un moteur du projet, ou
d'appeler un service depuis un script, sans rien entraîner derrière.
"""
from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import (ALLOWED_ORIGINS, STARTUP_NOTES, logger,
                        verifier_configuration)

__all__ = ["create_app"]


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Démarrage et arrêt.

    Les messages d'init sont affichés ICI et pas à l'import : sous
    `uvicorn --reload`, le module est chargé deux fois (process parent + worker)
    et chaque ligne apparaissait en double. Seul le worker traverse le lifespan.
    """
    from app.services import office
    from app.services.startup import reconcilier_jobs_orphelins
    from app.services.stockage import balayer_au_demarrage

    # `print` peut LEVER : la console Windows est en cp1252, et un caractère
    # hors de cette table (une flèche, un symbole d'avertissement) faisait
    # remonter un UnicodeEncodeError jusqu'au lifespan — l'application entière
    # refusait de démarrer à cause d'un message décoratif. Un message d'init ne
    # doit jamais pouvoir tuer le serveur : on retombe sur une version ASCII.
    for _level, message in STARTUP_NOTES:
        try:
            print(message, flush=True)
        except UnicodeEncodeError:
            print(message.encode("ascii", "replace").decode("ascii"), flush=True)
    # Le filet « absolument toutes les erreurs » : tout logger.error/exception
    # applicatif atterrit désormais aussi dans le journal central.
    from app.services.error_log import install_db_log_handler
    install_db_log_handler()
    await reconcilier_jobs_orphelins()
    # APRÈS la réconciliation : les jobs perdus sont clos, la table des jobs
    # est vide, tout ce qui appartient à un job est orphelin par construction.
    await balayer_au_demarrage()
    office.prewarm()
    # Décor de l'aperçu d'essai : ~5 s au premier calque, payées ici en tâche
    # de fond plutôt que par le premier visiteur gratuit après un déploiement.
    from app.services import trial_preview
    threading.Thread(target=trial_preview.prechauffer, daemon=True).start()
    _afficher_banniere()
    yield


def _afficher_banniere() -> None:
    """Bannière du démarrage DIRECT (`uvicorn main:app`).

    `npm run dev` a la sienne, affichée quand les DEUX serveurs écoutent et qui
    pointe l'interface. Ici, l'API tourne seule : elle est la destination.

    Silencieuse si `PRECIS_NO_BANNER` est posé — les tests montent l'application
    des dizaines de fois, et une bannière par montage noierait leur sortie.
    """
    import os

    if os.getenv("PRECIS_NO_BANNER"):
        return
    try:
        from app import banner, versions
        port = int(os.getenv("PORT") or os.getenv("UVICORN_PORT") or 8000)
        cadre = banner.construire(None, port, versions.toutes())
        print("\n" + cadre + "\n", flush=True)
    except Exception:
        pass    # un décor ne fait jamais échouer un démarrage


def create_app() -> FastAPI:
    """Construit et câble l'application."""
    # AVANT tout le reste : un service qui démarre avec un secret public est
    # pire qu'un service qui ne démarre pas. Le contrôle est bloquant en
    # production seulement — en développement il se contente d'avertir.
    verifier_configuration()

    application = FastAPI(
        title="Précis Translator API",
        version="1.0.0",
        description=(
            "Traduction de documents à mise en forme préservée — PDF, PPTX, "
            "DOCX. Le texte est relevé avec sa géométrie et ses styles, "
            "traduit, puis réinjecté dans le document d'origine."
        ),
        lifespan=_lifespan,
    )

    # CORS — une LISTE, jamais `*`.
    #
    # `allow_origins=["*"]` avec `allow_credentials=True` : Starlette renvoie
    # alors l'origine de l'appelant, QUELLE QU'ELLE SOIT, accompagnée de
    # `Access-Control-Allow-Credentials: true`. MESURÉ : une origine
    # `https://un-site-malveillant.example` recevait l'autorisation complète.
    #
    # Le vol de session n'était pas possible — les jetons vivent dans
    # `localStorage` et aucun cookie n'est posé, donc une origine tierce ne peut
    # pas forger l'en-tête `Authorization`. Mais la clé d'API, elle, est
    # PUBLIQUE (elle voyage dans le bundle du frontend) : n'importe quelle page
    # pouvait déclencher nos conversions LibreOffice, qui coûtent des secondes
    # de CPU chacune. Et le jour où un cookie apparaît, le trou devient un vol
    # de session.
    #
    # `ALLOWED_ORIGINS` existait déjà dans la configuration et n'était utilisée
    # NULLE PART. Le lien manquait, voilà tout.
    #
    # En développement, le frontend passe par le proxy Vite (même origine) :
    # CORS n'intervient pas du tout. Ce réglage ne concerne qu'un déploiement où
    # l'interface et l'API sont sur deux domaines.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def log_requests(request: Request, call_next):
        ip = request.client.host if request.client else "unknown"
        logger.info(f"Incoming request from IP: {ip} | Method: {request.method} "
                    f"| Path: {request.url.path}")
        response = await call_next(request)
        logger.info(f"Response status for IP {ip}: {response.status_code}")
        return response

    # Limitation de débit : `limiter` est None si slowapi manque, et
    # `rate_limit_decorator` devient alors neutre. Rien d'autre ne change.
    from app.rate_limit import limiter
    if limiter is not None:
        from slowapi import _rate_limit_exceeded_handler
        from slowapi.errors import RateLimitExceeded
        application.state.limiter = limiter
        application.add_exception_handler(RateLimitExceeded,
                                          _rate_limit_exceeded_handler)

    # Les routeurs sont importés ICI, pas en tête de module : ils tirent les
    # services, qui tirent les moteurs. Au niveau du module, ce chargement
    # partirait au simple `import app`.
    from app.api.admin_users import router as admin_users_router
    from app.api.auth import router as auth_router
    from app.api.documents import router as documents_router
    from app.api.logs import router as logs_router
    from app.api.payments import router as payments_router
    from app.api.support import router as support_router
    from app.api.system import router as system_router
    from app.api.translate import router as translate_router

    for router in (admin_users_router, auth_router, documents_router,
                   logs_router, payments_router, support_router,
                   system_router, translate_router):
        application.include_router(router)

    # Toute exception NON rattrapée est journalisée (avec sa pile et le contexte
    # de la requête) avant de rendre un 500 sobre. Sans ce filet, un défaut
    # serveur ne laissait qu'une trace en console, perdue au redémarrage.
    @application.exception_handler(Exception)
    async def _journaliser_non_rattrapee(request: Request, exc: Exception):
        import traceback
        from fastapi.responses import JSONResponse
        from app.services.error_log import log_error
        user = getattr(request.state, "user", None)
        await log_error(
            "backend", f"{type(exc).__name__}: {exc}", level="error",
            stack=traceback.format_exc(), location=request.url.path,
            context={"method": request.method, "path": request.url.path},
            user_id=getattr(user, "id", None),
            user_email=getattr(user, "email", None),
        )
        return JSONResponse(status_code=500, content={"detail": "Erreur interne."})

    return application
