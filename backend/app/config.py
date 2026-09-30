"""Configuration : tout ce que l'environnement décide, lu en UN seul endroit.

Avant, `os.getenv` était appelé depuis huit modules. Retrouver ce qu'une
variable pilotait supposait de fouiller le dépôt, et une valeur par défaut
pouvait différer d'un appel à l'autre sans que rien ne le signale.

Les chemins sont dérivés de l'emplacement de ce fichier, jamais du répertoire
courant : l'application doit se comporter pareil qu'on la lance depuis la
racine du dépôt, depuis `backend/`, ou via un service système.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

# `backend/` — deux crans au-dessus de `backend/app/config.py`.
BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

# Charger .env depuis le dossier backend/, quel que soit le cwd
load_dotenv(BACKEND_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(message)s")
logger = logging.getLogger("backend_app")

# Sous `uvicorn --reload`, le module applicatif est importé DEUX fois : dans le
# process parent (qui appelle load_app() pour échouer vite) et dans le worker.
# Les messages d'init sont donc empilés ici puis émis dans le lifespan, que seul
# le worker traverse — sinon chaque ligne apparaît en double au démarrage.
STARTUP_NOTES: list[tuple[int, str]] = []


def note(level: int, message: str) -> None:
    """Empile un message d'initialisation, affiché au démarrage du worker."""
    STARTUP_NOTES.append((level, message))


# ── Accès ────────────────────────────────────────────────────────────────────
# Clé RÉVOQUÉE le 27/07/2026. Elle a vécu dans `.env.example` et dans le bundle
# du frontend, donc dans neuf commits : elle est publique. Elle n'est plus une
# valeur de repli — la garder comme défaut, c'était laisser le dépôt contenir
# une clé qui marche. Elle ne subsiste ICI que pour être REFUSÉE, afin qu'un
# vieux `.env` recopié ne la ressuscite pas en silence.
DEFAUT_FRONTEND_API_KEY = "precis_frontend_secure_key_2026_xK9mP2vL"
FRONTEND_API_KEY = os.getenv("FRONTEND_API_KEY", "")

_DEV_ORIGINS = [
    "http://localhost:5173", "http://localhost:3000", "http://localhost:3001",
    "http://127.0.0.1:5173", "http://127.0.0.1:3000", "http://127.0.0.1:3001",
]
_origins_str = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://localhost:8000,http://127.0.0.1:5173,"
    "http://127.0.0.1:8000,http://localhost:3000,http://localhost:3001,"
    "http://127.0.0.1:3001")
# Les origines DÉCLARÉES par l'exploitant, avant tout ajout de confort : c'est
# cette liste-là — et elle seule — qui dit si l'on sert un domaine public.
DECLARED_ORIGINS = [o.strip() for o in _origins_str.split(",") if o.strip()]


# ── Sommes-nous en production ? ──────────────────────────────────────────────
# Question posée ICI, avant de composer ALLOWED_ORIGINS, parce que la réponse
# décide de sa composition.
#
# DEUX SIGNAUX, dont un qu'on ne peut pas oublier. Si l'on ne s'appuyait que sur
# `PRECIS_ENV=production`, oublier cette variable — l'oubli même que tout ceci
# rattrape — désarmerait la protection. Or servir une interface depuis un vrai
# domaine OBLIGE à déclarer son origine ci-dessus. Une origine déclarée qui
# n'est ni `localhost` ni `127.0.0.1`, c'est un déploiement, quoi qu'en dise
# `PRECIS_ENV`.
#
# Le faux positif est sans danger (on réclame un vrai secret à un développeur,
# ce qui est le bon conseil) ; le faux négatif met un service en ligne avec un
# secret connu de tous. D'où un test qui penche du côté strict.

def _est_locale(origine: str) -> bool:
    hote = (urlparse(origine).hostname or "").lower()
    return hote in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "")


IS_PRODUCTION = (
    os.getenv("PRECIS_ENV", "").strip().lower() in ("prod", "production")
    or any(not _est_locale(o) for o in DECLARED_ORIGINS)
)

# Les six origines de développement étaient ajoutées INCONDITIONNELLEMENT, y
# compris en ligne : un service en production autorisait `http://localhost:5173`
# à l'appeler. Ce n'est pas anodin — une page servie depuis la machine d'un
# visiteur (un outil local, une extension, un serveur de développement laissé
# ouvert) se voyait accorder l'accès complet à l'API de production, clé d'API
# comprise puisqu'elle voyage dans le bundle.
#
# En production, on ne garde donc QUE ce que l'exploitant a déclaré. En
# développement, le confort reste : les six adresses habituelles marchent sans
# rien configurer.
ALLOWED_ORIGINS = list(set(
    DECLARED_ORIGINS if IS_PRODUCTION else DECLARED_ORIGINS + _DEV_ORIGINS))


# ── Base de données ──────────────────────────────────────────────────────────
# Lue ICI et non dans `core/database.py`, qui appelait `load_dotenv` sur
# `app/core/.env` — un chemin qui n'existe pas. Ce module ne voyait donc le
# `.env` que par l'effet de bord de l'import de `config`, et retombait sinon
# SILENCIEUSEMENT sur l'URL de démonstration ci-dessous.
DEFAUT_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/precis"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAUT_DATABASE_URL)


# ── Authentification ─────────────────────────────────────────────────────────
# Idem : `core/security.py` lisait ces variables de son côté. Le garde-fou de
# production doit vérifier la valeur qui SIGNE réellement les jetons — s'il
# contrôlait une autre lecture, il pourrait donner un feu vert sur un secret que
# personne n'utilise.
DEFAUT_JWT_SECRET = "change-me-in-production-64-chars-minimum!!"
JWT_SECRET = os.getenv("JWT_SECRET", DEFAUT_JWT_SECRET)
JWT_EXPIRY_MINUTES = int(os.getenv("JWT_EXPIRY_MINUTES", "60"))
REFRESH_TOKEN_EXPIRY_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRY_DAYS", "30"))

# ── Envois ───────────────────────────────────────────────────────────────────
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 Mo
ALLOWED_EXTENSIONS = {"txt", "pdf", "docx", "pptx", "xlsx"}

# ── Stockage ─────────────────────────────────────────────────────────────────
TRANSLATIONS_DIR = str(BACKEND_DIR / "translations")
os.makedirs(TRANSLATIONS_DIR, exist_ok=True)

# L'aperçu côte-à-côte s'appuie sur pdf.js : pour obtenir un rendu EXACT des
# formats non-PDF (DOCX, PPTX, TXT), on les convertit en PDF via LibreOffice
# headless. La conversion ne sert QUE l'aperçu — le téléchargement garde le
# format d'origine. Résultats mis en cache disque (clé = hash du contenu).
PREVIEW_CACHE_DIR = os.path.join(TRANSLATIONS_DIR, "_previews")

# ── Rétention des jobs ───────────────────────────────────────────────────────
# Un job terminé reste consultable (partiel/résultat) pendant cette durée, puis
# est oublié et son PDF partiel effacé. Le commentaire d'origine promettait ce
# ménage (« nettoyés après 30 minutes ») mais RIEN ne l'implémentait : les jobs
# s'accumulaient en mémoire et les partial_*.pdf sur le disque, sans borne.
JOB_RETENTION_SECONDS = 30 * 60
# Un job encore « en vie » au-delà de cette durée a perdu son worker (plantage
# sans job_error) : on l'oublie aussi.
JOB_MAX_AGE_SECONDS = 24 * 3600
# Intervalle minimal entre deux écritures d'avancement en base.
PROGRESS_EVERY_S = 3.0

# ── Ordonnanceur de traduction ───────────────────────────────────────────────
# Nombre de traductions menées EN PARALLÈLE. Au-delà, les demandes attendent
# dans une file de PRIORITÉ (le plan décide de l'ordre : admin > pro > starter >
# gratuit). C'est ce qui donne corps à la vitesse vendue sur la carte de tarifs.
#
# Le coût dominant d'une traduction est le CPU de rendu : trop de workers sur
# une seule machine se marchent dessus et RALENTISSENT tout le monde. 2 est un
# défaut prudent pour un mono-serveur ; à monter avec le nombre de cœurs.
TRANSLATION_WORKERS = max(1, int(os.getenv("TRANSLATION_WORKERS", "2")))

# ── Modèles de traduction ────────────────────────────────────────────────────
# Deux modes, choisis par requête via le paramètre `quality` :
#  • "fast"    → modèle non-raisonnant, ~secondes/page, version stable (défaut) ;
#  • "precise" → modèle à raisonnement, alignement id↔texte fiable sur les pages
#                complexes (numéros + formules), mais ~1-2 min/page.
# Noms surchargeables via .env si DeepSeek renomme ses modèles.
# `deepseek-chat` était un ALIAS de compatibilité vers `deepseek-v4-flash` en
# mode non-raisonnant, supprimé par DeepSeek le 24/07/2026 à 15:59 UTC. On vise
# donc le modèle réel : à cette date, le comportement est identique — c'est le
# même modèle — mais le nom, lui, survivra.
FAST_MODEL = os.getenv("DEEPSEEK_MODEL_FAST", "deepseek-v4-flash")
PRECISE_MODEL = os.getenv("DEEPSEEK_MODEL_PRECISE", "deepseek-v4-flash")


# ── Marque publique ──────────────────────────────────────────────────────────
# Nom et adresse du service, tels qu'ils apparaissent DANS l'aperçu d'essai
# (filigrane = mini-publicité). Configurables : au changement de domaine, on ne
# recompile pas le moteur d'aperçu. Le filigrane est cuit dans les pixels, donc
# ces valeurs se figent au moment du rendu — un aperçu ancien garde l'ancien
# libellé, ce qui est sans conséquence.
BRAND_NAME = os.getenv("BRAND_NAME", "PRÉCIS")
BRAND_TAGLINE = os.getenv("BRAND_TAGLINE", "Professional Translation")
PUBLIC_SITE = os.getenv("PUBLIC_SITE", "precis-translator.com")


# ── Journal des erreurs ──────────────────────────────────────────────────────
# Au-delà de ce nombre de logs NON traités, l'admin reçoit une alerte e-mail —
# une seule à la fois (throttle), pour signaler qu'il y a du grain à moudre sans
# noyer sa boîte. Puis il traite (exporte) et supprime : la table ne gonfle que
# tant qu'on ne s'en occupe pas.
ERROR_LOG_ALERT_THRESHOLD = int(os.getenv("ERROR_LOG_ALERT_THRESHOLD", "50"))
ERROR_LOG_ALERT_COOLDOWN = int(os.getenv("ERROR_LOG_ALERT_COOLDOWN", "3600"))  # s
# Destinataire de repli si AUCUN compte n'a le plan `admin` en base (bootstrap).
ADMIN_ALERT_EMAIL = os.getenv("ADMIN_ALERT_EMAIL") or None


# ── Garde-fou de production ──────────────────────────────────────────────────
# Tous les secrets de ce projet ont une valeur par défaut, et l'application
# démarrait avec elles sans un mot. Trois d'entre elles sont PUBLIQUES : elles
# figurent dans `.env.example`, donc dans le dépôt, donc dans tout clone. Avec
# le `JWT_SECRET` par défaut, n'importe qui forge un jeton d'accès admin ; avec
# la `FRONTEND_API_KEY` par défaut, n'importe qui déclenche nos conversions.
#
# Le garde s'appuie sur `IS_PRODUCTION`, défini plus haut — deux signaux, dont
# un qu'on ne peut pas oublier. Voir l'explication à cet endroit.

# Valeurs qu'un secret n'a JAMAIS le droit de porter en production. On y met les
# défauts du code ET les gabarits de `.env.example` : les deux diffèrent, et
# c'est le gabarit qu'un exploitant copie. `test_garde_secrets.py` vérifie
# l'accord — si `.env.example` change de gabarit sans que cette liste suive, la
# suite échoue plutôt que de laisser passer un secret public.
SECRETS_INTERDITS = {
    DEFAUT_JWT_SECRET,
    "change-me-in-production-64-chars-minimum-long-random-string",
    DEFAUT_FRONTEND_API_KEY,
    DEFAUT_DATABASE_URL,
    "your_deepseek_api_key_here",
}
# Marqueurs de gabarit : attrapent les variantes non listées (« your_x_here »,
# « changeme », « à remplacer »…) sans qu'on ait à les énumérer.
_MARQUEURS_GABARIT = ("change-me", "changeme", "your_", "_here", "remplacer",
                      "xxx", "todo")

# Longueur minimale d'un secret de signature. En dessous, HS256 se force.
JWT_SECRET_MIN_LEN = 32


def _est_gabarit(valeur: str) -> bool:
    v = (valeur or "").strip()
    if not v:
        return True
    if v in SECRETS_INTERDITS:
        return True
    bas = v.lower()
    return any(m in bas for m in _MARQUEURS_GABARIT)


def defauts_de_production() -> list[str]:
    """Liste des réglages qui interdisent une mise en ligne. Vide = bon pour le
    service. Toujours calculée, même hors production : c'est ce qui permet de
    l'AFFICHER en avertissement en développement, et de la tester."""
    fautes: list[str] = []

    if _est_gabarit(JWT_SECRET):
        fautes.append(
            "JWT_SECRET porte encore une valeur de gabarit (publique) — "
            "quiconque lit le dépôt peut forger un jeton admin. "
            "Générer : python -c \"import secrets;print(secrets.token_urlsafe(48))\"")
    elif len(JWT_SECRET) < JWT_SECRET_MIN_LEN:
        fautes.append(
            f"JWT_SECRET fait {len(JWT_SECRET)} caractères, minimum "
            f"{JWT_SECRET_MIN_LEN}.")

    if _est_gabarit(FRONTEND_API_KEY):
        fautes.append(
            "FRONTEND_API_KEY porte encore la valeur de gabarit (publique) — "
            "n'importe quelle page peut déclencher nos conversions.")

    if _est_gabarit(DATABASE_URL):
        fautes.append(
            "DATABASE_URL est l'URL de démonstration (postgres:postgres) — "
            "base sans mot de passe propre.")

    if _est_gabarit(os.getenv("DEEPSEEK_API_KEY", "")):
        fautes.append(
            "DEEPSEEK_API_KEY absente ou gabarit — aucune traduction ne "
            "pourra aboutir.")

    if os.getenv("EMAIL_ENABLED", "true").lower() == "true" and not os.getenv(
            "SMTP_PASSWORD", "").strip():
        fautes.append(
            "EMAIL_ENABLED=true sans SMTP_PASSWORD — aucune vérification de "
            "compte ne partira. Poser le mot de passe, ou EMAIL_ENABLED=false.")

    return fautes


def verifier_configuration() -> None:
    """Refuse de démarrer si un secret public traîne en production.

    Appelée par `create_app()`. En développement, les mêmes défauts sont
    seulement signalés : on ne bloque pas le travail local, on le documente."""
    fautes = defauts_de_production()
    if not fautes:
        return
    if IS_PRODUCTION:
        detail = "\n".join(f"  • {f}" for f in fautes)
        raise RuntimeError(
            "Configuration de production refusée — "
            f"{len(fautes)} réglage(s) à corriger dans backend/.env :\n"
            f"{detail}\n"
            "(Ce contrôle s'arme dès qu'une origine publique est déclarée dans "
            "ALLOWED_ORIGINS, ou que PRECIS_ENV=production.)")
    for f in fautes:
        note(logging.WARNING, f"Attention (config de developpement) : {f}")


def resolve_quality(quality: str) -> tuple[str, int]:
    """(model, max_tokens) selon le mode demandé. Le mode précis a besoin d'un
    gros budget de tokens car le raisonnement en consomme avant la réponse."""
    if quality == "precise":
        return PRECISE_MODEL, 65536
    return FAST_MODEL, 8192
