#!/bin/sh
# Démarrage du service dans le conteneur : migrations, puis serveur.
#
# `set -e` : si les migrations échouent, on NE démarre PAS. Un serveur qui
# répond sur un schéma de base périmé rend des erreurs incompréhensibles à
# l'utilisateur et corrompt ce qu'il écrit. Mieux vaut un conteneur qui refuse
# de vivre — l'orchestrateur le signale, et le journal dit pourquoi.
set -e

cd /app/backend

# Une commande passée au conteneur est EXÉCUTÉE telle quelle, au lieu de
# démarrer le service. Sans cette porte de sortie, tout `docker run … <cmd>`
# lançait les migrations puis le serveur en ignorant la commande demandée :
# impossible d'inspecter l'image (vérifier LibreOffice, lister les polices,
# ouvrir un terminal) sans passer par `--entrypoint`, et un diagnostic qui
# exige de contourner l'outil est un diagnostic qu'on ne fait pas.
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

echo "[precis] Application des migrations de base de donnees..."
# La base met quelques secondes à accepter les connexions au tout premier
# démarrage de la pile. `depends_on: condition: service_healthy` couvre le cas
# normal ; cette boucle couvre le redémarrage d'une machine où les deux
# conteneurs repartent ensemble.
essais=0
until alembic upgrade head; do
    essais=$((essais + 1))
    if [ "$essais" -ge 10 ]; then
        echo "[precis] ECHEC : la base reste injoignable apres 10 tentatives." >&2
        exit 1
    fi
    echo "[precis] Base pas encore prete, nouvelle tentative dans 3s ($essais/10)..."
    sleep 3
done
echo "[precis] Migrations a jour."

# ── UN SEUL worker, et ce n'est pas négociable ───────────────────────────────
# Le registre des travaux de traduction vit EN MÉMOIRE, et l'ordonnanceur de
# priorité aussi. Avec deux workers, chacun aurait son propre registre : un
# client interrogerait l'avancement d'un travail lancé dans l'autre processus,
# recevrait « inconnu », et son flux SSE se fermerait sur une traduction
# pourtant en cours. Le parallélisme se règle par TRANSLATION_WORKERS, à
# l'intérieur de ce processus unique.
exec uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --proxy-headers \
    --forwarded-allow-ips '*' \
    --timeout-keep-alive 65
