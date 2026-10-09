#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
#  LE DÉPLOIEMENT, SUR LA MACHINE
#
#      aws-ssm-deploy.sh <version> <tag-image>
#
#  Exécuté par SSM, EN ROOT (c'est le compte sous lequel SSM lance les
#  commandes — ne pas supposer l'appartenance au groupe `docker`, et ne pas
#  faire de `sudo -u ubuntu`, qui perdrait l'environnement).
#
#  Il récupère les secrets, écrit le `.env`, tire les images, démarre la pile,
#  vérifie que le service rend le service, et REVIENT EN ARRIÈRE si ce n'est
#  pas le cas.
#
#  CE QU'IL NE FAIT PAS, DÉLIBÉRÉMENT :
#    • il ne lance pas les migrations — `docker-entrypoint.sh` les applique au
#      démarrage, avec attente de la base et dix tentatives, et `set -e`
#      empêche le serveur de démarrer si elles échouent. Un second
#      `alembic upgrade head` lancé d'ici ferait tourner deux migrations
#      concurrentes sur la même base ;
#    • il ne construit aucune image — elles arrivent du registre, construites
#      en intégration continue.
# ═════════════════════════════════════════════════════════════════════════════
set -euo pipefail
IFS=$'\n\t'

VERSION="${1:-}"
TAG_IMAGE="${2:-}"
SSM_PREFIX="${SSM_PREFIX:-/precis-translator/prod}"
RACINE="${RACINE:-/opt/precis}"
ECR_REGISTRY="${ECR_REGISTRY:-}"
REGION="${AWS_DEFAULT_REGION:-}"

# Nom de projet Compose FIGÉ. Sans lui, Compose nomme le projet d'après le
# répertoire du fichier — ici `docker` — et les volumes deviendraient
# `docker_pgdata` au lieu de `precis_pgdata`. On ouvrirait alors une base
# VIERGE en croyant avoir perdu toutes les données.
export COMPOSE_PROJECT_NAME=precis

SRC="$RACINE/src"
COMPOSE="$SRC/deploy/docker/docker-compose.aws.yml"
ENV_FICHIER="$SRC/backend/.env"
MEMO_TAG="$RACINE/deploy/.tag-courant"
MEMO_SCHEMA="$RACINE/deploy/.schema-avant"

dire()   { printf '\n── %s\n' "$*"; }
mourir() { printf '\nECHEC : %s\n' "$*" >&2; exit 1; }

compose() { docker compose -p precis -f "$COMPOSE" --env-file "$ENV_FICHIER" "$@"; }

[ -n "$VERSION" ]      || mourir "version manquante (argument 1)"
[ -n "$TAG_IMAGE" ]    || mourir "tag d'image manquant (argument 2)"
[ -n "$ECR_REGISTRY" ] || mourir "ECR_REGISTRY n'est pas dans l'environnement"
[ -n "$REGION" ]       || mourir "AWS_DEFAULT_REGION n'est pas dans l'environnement"

export IMAGE_TAG="$TAG_IMAGE"
export ECR_REGISTRY

echo "═══════════════════════════════════════════════════════════"
echo " Precis Translator $VERSION — image $TAG_IMAGE"
echo " $(date -Is)"
echo "═══════════════════════════════════════════════════════════"

# ── 1. Préflight ────────────────────────────────────────────────────────────
dire "Controles prealables"

docker info >/dev/null 2>&1 || mourir "le service Docker ne repond pas"
docker compose version >/dev/null 2>&1 \
  || mourir "le greffon « docker compose » est absent (paquet docker-compose-plugin)"
[ -f "$COMPOSE" ] || mourir "fichier Compose introuvable : $COMPOSE"

# Le montage du disque de données : s'il a sauté, Docker écrirait sur le disque
# racine et les données seraient perdues au prochain remplacement d'instance,
# SANS la moindre erreur. On refuse de déployer dans ce cas.
findmnt -n /var/lib/docker/volumes >/dev/null \
  || mourir "le disque de donnees n'est PAS monte sur /var/lib/docker/volumes.
  Deployer maintenant ecrirait la base sur le disque systeme, qui disparait
  avec l'instance. Verifiez : findmnt /var/lib/docker/volumes ; mount -a"

libre_mo=$(df -Pm --output=avail / | tail -1 | tr -d ' ')
echo "Espace libre sur / : ${libre_mo} Mo"
if [ "$libre_mo" -lt 5000 ]; then
  # Mieux vaut refuser franchement que voir `docker pull` échouer au milieu en
  # laissant des couches orphelines qui aggravent le manque de place.
  mourir "moins de 5 Go libres. Liberez de la place :
  docker image prune -af --filter 'until=720h'
  (ATTENTION : garder l'image precedente, elle sert au retour en arriere)"
fi

# Les images sans tag seulement. JAMAIS `prune -a`, qui supprimerait l'image
# de la version précédente — celle du retour en arrière.
docker image prune -f >/dev/null 2>&1 || true

# ── 2. De quoi revenir en arrière ───────────────────────────────────────────
dire "Etat actuel, pour pouvoir revenir"

TAG_PRECEDENT=""
if [ -f "$MEMO_TAG" ]; then
  TAG_PRECEDENT=$(tr -d ' \t\r\n' < "$MEMO_TAG")
fi
if [ -n "$TAG_PRECEDENT" ]; then
  echo "Version en place : $TAG_PRECEDENT"
else
  echo "Aucune version precedente : premier deploiement."
  echo "En cas d'echec, la pile sera arretee — il n'y a pas de retour possible."
fi

# Le schéma AVANT, pour pouvoir dire honnêtement, en cas de retour en arrière,
# si une migration a été appliquée entre-temps.
SCHEMA_AVANT=""
if compose ps --quiet backend 2>/dev/null | grep -q .; then
  SCHEMA_AVANT=$(compose exec -T backend alembic current 2>/dev/null \
                 | tr -d '\r' | head -1 || echo "")
  echo "Schema actuel : ${SCHEMA_AVANT:-inconnu}"
  echo "${SCHEMA_AVANT}" > "$MEMO_SCHEMA" 2>/dev/null || true
fi

# ── 3. Les secrets ──────────────────────────────────────────────────────────
dire "Lecture des parametres SSM"

mkdir -p "$(dirname "$ENV_FICHIER")"
umask 077

# `get-parameters-by-path` PAGINE. Avec une vingtaine de paramètres, oublier la
# pagination donne un `.env` tronqué — et le symptôme serait un garde de
# production qui réclame une variable que vous venez précisément de poser.
# `--no-paginate` est absent exprès : on laisse l'AWS CLI parcourir les pages.
brut=$(aws ssm get-parameters-by-path \
         --path "$SSM_PREFIX" \
         --recursive --with-decryption \
         --query 'Parameters[].[Name,Value]' --output text) \
  || mourir "lecture SSM impossible. Le role d'instance a-t-il ssm:GetParametersByPath
  sur $SSM_PREFIX/* et kms:Decrypt ?"

tmp_env=$(mktemp)
trap 'rm -f "$tmp_env"' EXIT

{
  echo "# Genere par aws-ssm-deploy.sh le $(date -Is)"
  echo "# NE PAS MODIFIER A LA MAIN : le prochain deploiement ecrase ce fichier."
  echo "# La source de verite est SSM, sous $SSM_PREFIX/"
  echo "PRECIS_ENV=production"
} > "$tmp_env"

nb=0
while IFS=$'\t' read -r nom valeur; do
  [ -n "$nom" ] || continue
  # `/precis-translator/prod/jwt_secret` → `JWT_SECRET`
  cle=$(basename "$nom" | tr '[:lower:]' '[:upper:]')

  # Une valeur sur plusieurs lignes casserait le `.env` en silence : la suite
  # du fichier deviendrait du texte libre, et des variables disparaîtraient
  # sans erreur.
  case "$valeur" in
    *$'\n'*) mourir "le parametre $nom contient un retour a la ligne" ;;
  esac

  # Écrit NU, sans guillemets : `python-dotenv` les retirerait, Compose non.
  # On aurait alors un secret entouré de guillemets d'un côté et pas de
  # l'autre — donc une signature de jeton qui ne correspond pas.
  printf '%s=%s\n' "$cle" "$valeur" >> "$tmp_env"
  nb=$((nb + 1))
done <<< "$brut"

echo "$nb parametres lus."

# ── DATABASE_URL : l'inversion du garde du modèle d'origine ─────────────────
#
# Le script dont ce déploiement s'inspire REFUSE une URL de base locale, parce
# que leur base est hébergée ailleurs. ICI C'EST L'INVERSE QU'IL FAUT : la base
# est un conteneur de cette pile, et une URL qui pointerait ailleurs
# (`localhost` → une base inexistante sur l'hôte ; un hôte distant → les
# données des clients parties chez un tiers) est une faute.
#
# On ne lit donc PAS `DATABASE_URL` depuis SSM : on la DÉRIVE des trois
# `POSTGRES_*`, exactement comme `docker-compose.yml` le fait. Des identifiants
# écrits deux fois finissent par différer, et l'on cherche alors une heure
# pourquoi le service ne joint plus une base qui fonctionne très bien.
sed -i '/^DATABASE_URL=/d' "$tmp_env"
echo "# DATABASE_URL est DERIVEE par Compose des POSTGRES_* (service « db »)." >> "$tmp_env"
echo "# La valeur ci-dessous ne sert qu'aux outils lances hors conteneur." >> "$tmp_env"
echo "DATABASE_URL=postgresql+asyncpg://localhost:5432/precis" >> "$tmp_env"

# ── Les variables sans lesquelles le service ne démarrera pas ───────────────
# Le garde de `config.py` les refuserait de toute façon, mais il le ferait
# depuis l'intérieur d'un conteneur dont les journaux sont moins commodes.
manquantes=()
for cle in JWT_SECRET FRONTEND_API_KEY DEEPSEEK_API_KEY \
           POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB \
           FRONTEND_URL ALLOWED_ORIGINS; do
  grep -q "^${cle}=.\+$" "$tmp_env" || manquantes+=("$cle")
done
if [ ${#manquantes[@]} -gt 0 ]; then
  printf 'Parametre SSM absent ou vide : %s/%s\n' \
    "$SSM_PREFIX" "$(printf '%s\n' "${manquantes[@]}" | tr '[:upper:]' '[:lower:]' | paste -sd, -)" >&2
  mourir "${#manquantes[@]} parametre(s) obligatoire(s) manquant(s).
  Posez-les : voir infra/aws/README.md, section « Poser les secrets »."
fi

install -m 600 "$tmp_env" "$ENV_FICHIER"
echo "Ecrit : $ENV_FICHIER (600)"

# Cohérence avec `preparer_env_production.py`, qui pose `precis`/`precis` :
# si SSM portait d'autres valeurs, la base serait créée sous un nom et
# cherchée sous un autre.
pg_user=$(grep '^POSTGRES_USER=' "$ENV_FICHIER" | cut -d= -f2-)
pg_db=$(grep '^POSTGRES_DB=' "$ENV_FICHIER" | cut -d= -f2-)
echo "Base : utilisateur « $pg_user », base « $pg_db »"

# ── 4. Les images ───────────────────────────────────────────────────────────
dire "Recuperation des images $TAG_IMAGE"

# Aucun jeton de registre à stocker : le rôle d'instance suffit.
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ECR_REGISTRY" \
  || mourir "authentification au registre refusee (role d'instance : ecr:GetAuthorizationToken ?)"

# `--env-file` N'EST PAS OPTIONNEL, pour la raison écrite en tête de
# `docker-compose.yml` : sans lui Compose lit un `.env` à côté du fichier
# Compose, soit un SECOND fichier de secrets qui divergerait du premier — et
# l'interface reçoit un 401 que rien n'explique.
compose pull || mourir "impossible de tirer les images $TAG_IMAGE.
  Le tag existe-t-il dans $ECR_REGISTRY ?"

# ── 5. Démarrage ────────────────────────────────────────────────────────────
dire "Demarrage"

# Pas de `--build` : les images sont prêtes, c'est tout l'objet du fichier
# `.aws.yml`.
compose up -d --remove-orphans || mourir "« compose up » a echoue"

# ── 6. La pile est-elle saine ? ─────────────────────────────────────────────
dire "Attente du service"

# On ATTEND, on ne migre pas : l'entrypoint applique les migrations, et le
# contrôle de santé du Dockerfile laisse 120 s de démarrage. On patiente donc
# largement — une migration sur une base chargée prend du temps.
sain=non
for essai in $(seq 1 60); do   # 60 × 5 s = 5 min
  cid=$(compose ps --quiet backend 2>/dev/null | head -1)
  if [ -n "$cid" ]; then
    etat=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}sans-controle{{end}}' \
             "$cid" 2>/dev/null || echo absent)
    case "$etat" in
      healthy)      sain=oui; break ;;
      unhealthy)    echo "[$essai] en mauvaise sante" ;;
      sans-controle) sain=oui; break ;;
      *)            echo "[$essai] $etat" ;;
    esac
  else
    echo "[$essai] conteneur absent"
  fi
  sleep 5
done

verifier_sante_http() {
  # Par le conteneur `web` : c'est le seul à publier un port, et c'est le
  # chemin qu'emprunteront les clients.
  local corps
  corps=$(curl -fsS --max-time 10 http://127.0.0.1/health 2>/dev/null) || return 1
  echo "$corps" | grep -q '"status"[[:space:]]*:[[:space:]]*"healthy"' || return 1
  # La VERSION, et pas seulement « ça répond » : l'ancienne version répondrait
  # tout aussi gaiement, et l'on croirait avoir déployé.
  echo "$corps" | grep -q "\"projet\"[[:space:]]*:[[:space:]]*\"$VERSION\"" || {
    echo "Le service repond mais n'annonce pas $VERSION :" >&2
    echo "$corps" >&2
    return 1
  }
  echo "$corps"
}

if [ "$sain" = "oui" ] && verifier_sante_http; then
  dire "Deploiement reussi"
  echo "$TAG_IMAGE" > "$MEMO_TAG"
  docker logout "$ECR_REGISTRY" >/dev/null 2>&1 || true
  echo "DEPLOIEMENT OK v$VERSION $TAG_IMAGE"
  exit 0
fi

# ── 7. Le retour en arrière ─────────────────────────────────────────────────
dire "Le service ne repond pas — retour en arriere"

echo "::: Journal du service (100 dernieres lignes) :::"
# C'est ici que le garde de production NOMME chaque réglage fautif.
compose logs --tail=100 backend 2>&1 || true
echo "::: Journal de la base :::"
compose logs --tail=30 db 2>&1 || true

# Un cas particulier qui coûte une heure de recherche si on ne le nomme pas :
# `pgdata` FIGE le mot de passe au premier démarrage. Changer
# `postgres_password` dans SSM après coup ne change pas la base — le service ne
# peut plus s'y connecter, et le message ne dit pas que la cause est là.
if compose logs --tail=200 backend 2>&1 | grep -qi 'password authentication failed'; then
  cat >&2 <<'CAUSE'

  ╔══════════════════════════════════════════════════════════════════════╗
  ║  CAUSE PROBABLE : le mot de passe de la base a change dans SSM.      ║
  ║                                                                      ║
  ║  Le volume `pgdata` a FIGE le mot de passe au premier demarrage de   ║
  ║  Postgres. Le modifier dans SSM ne le modifie pas dans la base.      ║
  ║                                                                      ║
  ║  Deux issues :                                                       ║
  ║   • remettre dans SSM l'ancien mot de passe (le plus simple) ;       ║
  ║   • ou changer celui de la base :                                    ║
  ║     docker compose -p precis exec db psql -U <user> -c \             ║
  ║       "ALTER USER <user> PASSWORD '<nouveau>';"                      ║
  ╚══════════════════════════════════════════════════════════════════════╝
CAUSE
fi

if [ -z "$TAG_PRECEDENT" ]; then
  compose down || true
  mourir "premier deploiement en echec : la pile est arretee.
  Il n'y a aucune version precedente vers laquelle revenir."
fi

echo "Retour a $TAG_PRECEDENT"
export IMAGE_TAG="$TAG_PRECEDENT"
if compose up -d --remove-orphans; then
  sleep 30
  if verifier_sante_http >/dev/null 2>&1; then
    echo "Le service est revenu sur $TAG_PRECEDENT."
  else
    echo "ATTENTION : meme $TAG_PRECEDENT ne repond pas. Le service est INTERROMPU." >&2
  fi
else
  echo "ATTENTION : le retour en arriere a echoue. Le service est INTERROMPU." >&2
fi

# ── L'honnêteté sur ce que le retour en arrière ne fait pas ─────────────────
#
# Il ramène les IMAGES. Il ne défait AUCUNE migration. Si la version qui vient
# d'échouer a modifié le schéma, l'ancienne image se retrouve devant une base
# plus récente qu'elle. Promettre un retour complet serait un mensonge, et
# c'est le genre de mensonge qu'on ne découvre qu'au pire moment.
if [ -f "$MEMO_SCHEMA" ]; then
  schema_apres=$(compose exec -T backend alembic current 2>/dev/null | tr -d '\r' | head -1 || echo "")
  schema_avant=$(cat "$MEMO_SCHEMA" 2>/dev/null || echo "")
  if [ -n "$schema_apres" ] && [ -n "$schema_avant" ] \
     && [ "$schema_apres" != "$schema_avant" ]; then
    cat >&2 <<AVERTISSEMENT

  ╔══════════════════════════════════════════════════════════════════════╗
  ║  UNE MIGRATION A ETE APPLIQUEE ET N'EST PAS DEFAITE                  ║
  ║                                                                      ║
  ║    avant : $schema_avant
  ║    apres : $schema_apres
  ║                                                                      ║
  ║  Les images sont revenues en arriere, LE SCHEMA NON. Intervention    ║
  ║  manuelle requise :                                                  ║
  ║    docker compose -p precis exec backend alembic downgrade <revision> ║
  ╚══════════════════════════════════════════════════════════════════════╝
AVERTISSEMENT
  fi
fi

docker logout "$ECR_REGISTRY" >/dev/null 2>&1 || true

# Un retour en arrière RÉUSSI reste un déploiement ÉCHOUÉ : le workflow doit
# l'afficher en rouge, sans quoi on croirait la version en ligne.
mourir "v$VERSION n'a pas demarre. Retour a $TAG_PRECEDENT effectue."
