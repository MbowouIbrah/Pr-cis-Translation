# ══════════════════════════════════════════════════════════════════════════════
#  Précis Translator — image du service de traduction
#
#  Construction depuis la RACINE du dépôt :
#      docker build -f backend/Dockerfile -t precis-backend .
#
#  Le contexte est la racine et non `backend/`, pour que les deux images
#  (service et interface) partagent un seul `.dockerignore` — donc une seule
#  liste de ce qui ne doit jamais entrer dans une image.
# ══════════════════════════════════════════════════════════════════════════════

# `slim-bookworm` et non `alpine` : Alpine utilise musl, et les roues
# précompilées de PyMuPDF, bcrypt et asyncpg n'existent que pour glibc. Sur
# Alpine, pip les recompile — plusieurs dizaines de minutes sur les 2 cœurs
# d'une machine Oracle gratuite, à chaque construction.
#
# Python 3.12 et non 3.14 (la version du poste) : c'est la version pour laquelle
# TOUTES les dépendances publient des roues ARM64, l'architecture de la machine
# Oracle. Sans roue, il faut un compilateur et beaucoup de patience.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ── Dépendances système ──────────────────────────────────────────────────────
# LibreOffice sert UNIQUEMENT à convertir les documents non-PDF en PDF pour
# l'aperçu. On n'installe que les trois modules utilisés (writer/impress/calc) :
# le paquet `libreoffice` complet tire Base, Draw, Math et une JVM, soit près
# d'un gigaoctet dont rien n'est appelé.
#
# `fonts-liberation` n'est pas décoratif : ce sont les substituts aux mesures
# identiques d'Arial, Times New Roman et Courier New. Sans elles, LibreOffice
# remplace ces polices par ce qu'il trouve, les largeurs changent, et la mise en
# page de l'aperçu ne correspond plus au document d'origine.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        libreoffice-impress \
        libreoffice-calc \
        fonts-liberation \
        fonts-dejavu-core \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Dépendances Python ───────────────────────────────────────────────────────
# Copiées SEULES d'abord : cette couche n'est reconstruite que si
# `requirements.txt` change. Copier le code avant réinstallerait tout à chaque
# modification d'une ligne de Python.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# ── Le code ──────────────────────────────────────────────────────────────────
COPY backend/ ./backend/

# `app/versions.py` lit la version du projet et de l'interface dans ces deux
# fichiers, sur disque, plutôt que de la recopier dans une constante — pour
# qu'une version affichée ne puisse jamais mentir. Absents de l'image, ils
# faisaient afficher « ? » dans /health et dans la bannière : le service ne
# savait plus dire quelle version il servait, ce qui rend tout rapport de bogue
# ininterprétable. Ils ne pèsent rien et ne contiennent aucun secret.
COPY package.json ./package.json
COPY frontend/package.json ./frontend/package.json

# ── Contrôle des polices de repli — BLOQUANT ────────────────────────────────
# `_load_matched_font()` retombe SANS UN MOT sur Helvetica quand un fichier
# manque. Bon comportement à l'exécution (mieux vaut un PDF en Helvetica qu'une
# traduction payée qui échoue), désastre silencieux à la construction : le
# service tournerait en perdant la fidélité typographique qui est son objet.
#
# 22 des 28 polices étaient absentes du dépôt jusqu'au 27/07/2026 — une image
# construite avant cette date aurait eu exactement ce défaut, sans le dire.
RUN python backend/scripts/verifier_polices.py

# `--chmod` est indispensable : le dépôt est édité sous Windows, qui ne porte
# pas le bit d'exécution. Sans lui, le conteneur meurt sur « permission denied »
# avant d'avoir exécuté une seule ligne.
COPY --chmod=0755 backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# ── Utilisateur non privilégié ───────────────────────────────────────────────
# Un service exposé sur Internet ne tourne pas en root. `HOME` doit être
# accessible en écriture : LibreOffice y écrit son profil au premier appel, et
# échoue en silence s'il ne peut pas.
RUN useradd --create-home --shell /bin/bash precis \
    && mkdir -p /app/backend/translations \
    && chown -R precis:precis /app /home/precis
USER precis
ENV HOME=/home/precis

# Chemin explicite plutôt que détection : sur cette image, `soffice` est
# toujours là. La détection automatique reste le comportement hors conteneur.
ENV SOFFICE_PATH=/usr/bin/soffice

EXPOSE 8000

# ── Sonde de santé ───────────────────────────────────────────────────────────
# `start-period` généreux : le premier démarrage applique les migrations et
# pré-chauffe LibreOffice. Sans ce délai, l'orchestrateur déclarerait le service
# mort avant qu'il ait fini de naître, et le redémarrerait en boucle.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
