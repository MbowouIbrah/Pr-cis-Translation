#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
#  POSER LES SECRETS — une seule fois, depuis l'instance
#
#      aws ssm start-session --target <instance-id>
#      sudo -i
#      curl -fsSL <url-brute-de-ce-fichier> -o /tmp/poser-secrets.sh
#      bash /tmp/poser-secrets.sh
#
#  POURQUOI DEPUIS L'INSTANCE, et non depuis votre poste : les trois secrets
#  générés ici ne traversent alors AUCUN disque et AUCUN historique de
#  terminal. Ils naissent dans ce processus et vont droit dans SSM.
#
#  POURQUOI `--no-overwrite` PARTOUT : relancer ce script ne doit JAMAIS
#  régénérer un secret existant.
#    • `jwt_secret` régénéré = tout le monde est déconnecté sur le champ ;
#    • `postgres_password` régénéré = le service perd sa propre base, car
#      `pgdata` a figé l'ancien mot de passe au premier démarrage de Postgres.
#  Le rôle d'instance porte d'ailleurs la même contrainte côté IAM
#  (`ssm:Overwrite = false`) : même un script fautif ne pourrait pas écraser.
#
#  Le script est donc IDEMPOTENT et sans danger : il crée ce qui manque,
#  laisse ce qui existe, et nomme ce que vous devez poser vous-même.
# ═════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SSM_PREFIX="${SSM_PREFIX:-/precis-translator/prod}"
REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-}}"

if [ -z "$REGION" ]; then
  # Par IMDSv2 : un jeton d'abord, sinon le service de métadonnées refuse.
  jeton=$(curl -fsS -X PUT "http://169.254.169.254/latest/api/token" \
            -H "X-aws-ec2-metadata-token-ttl-seconds: 60" 2>/dev/null || echo "")
  if [ -n "$jeton" ]; then
    REGION=$(curl -fsS -H "X-aws-ec2-metadata-token: $jeton" \
      "http://169.254.169.254/latest/meta-data/placement/region" 2>/dev/null || echo "")
  fi
fi
[ -n "$REGION" ] || { echo "Region introuvable : posez AWS_DEFAULT_REGION." >&2; exit 1; }
export AWS_DEFAULT_REGION="$REGION"

echo "Prefixe : $SSM_PREFIX    region : $REGION"
echo

existe() {
  aws ssm get-parameter --name "$SSM_PREFIX/$1" >/dev/null 2>&1
}

# `--no-overwrite` fait échouer la commande si le paramètre existe ; on
# vérifie donc d'abord, pour distinguer « déjà posé » d'une vraie erreur.
poser() {
  local nom="$1" valeur="$2" type="$3"
  if existe "$nom"; then
    echo "  = $nom (deja pose, intact)"
    return 0
  fi
  aws ssm put-parameter \
    --name "$SSM_PREFIX/$nom" \
    --value "$valeur" \
    --type "$type" \
    --no-overwrite >/dev/null
  echo "  + $nom ($type)"
}

aleatoire() {
  python3 -c "import secrets; print(secrets.token_urlsafe($1))"
}

# ── Les secrets que personne n'a besoin de connaître ───────────────────────
echo "Secrets generes :"
poser jwt_secret        "$(aleatoire 48)" SecureString
poser frontend_api_key  "$(aleatoire 24)" SecureString
poser postgres_password "$(aleatoire 24)" SecureString

# ── Les valeurs non secrètes, mais nécessaires ─────────────────────────────
# `precis`/`precis` : les mêmes que `backend/scripts/preparer_env_production.py`.
# Changer l'un sans l'autre créerait une base sous un nom et la chercherait
# sous un autre.
echo
echo "Conventions :"
poser postgres_user       precis    String
poser postgres_db         precis    String
poser campay_env          DEMO      String
poser email_enabled       true      String
poser translation_workers 2         String
poser pricing_default_country CM    String

# ── Ce que seul l'exploitant connaît ───────────────────────────────────────
echo
echo "─────────────────────────────────────────────────────────────"
echo "A POSER VOUS-MEME : personne ne peut les inventer."
echo "─────────────────────────────────────────────────────────────"

A_POSER_SECRETS=(deepseek_api_key smtp_password campay_username
                 campay_password campay_webhook_key)
A_POSER_CLAIRS=(frontend_url allowed_origins admin_alert_email
                smtp_host smtp_port smtp_user google_client_id)

manque=0
for p in "${A_POSER_SECRETS[@]}"; do
  if existe "$p"; then
    echo "  = $p"
  else
    echo "  ! $p  MANQUANT"
    echo "      aws ssm put-parameter --name $SSM_PREFIX/$p \\"
    echo "        --type SecureString --value '<la valeur>'"
    manque=$((manque + 1))
  fi
done
for p in "${A_POSER_CLAIRS[@]}"; do
  if existe "$p"; then
    echo "  = $p"
  else
    echo "  ! $p  MANQUANT"
    echo "      aws ssm put-parameter --name $SSM_PREFIX/$p \\"
    echo "        --type String --value '<la valeur>'"
    manque=$((manque + 1))
  fi
done

cat <<'RAPPELS'

─────────────────────────────────────────────────────────────
RAPPELS QUI EVITENT DES HEURES DE RECHERCHE

• `frontend_url` et `allowed_origins` doivent porter le VRAI domaine, en
  https. Le garde de production refuse une valeur vide ou sur `localhost`,
  et il a raison : une origine locale autorisée en production accorderait
  l'API complete a n'importe quelle page servie depuis un poste.

• `allowed_origins` ne contient QUE le domaine public. Pas de `localhost`,
  pas de port de developpement.

• Ne changez JAMAIS `postgres_password` apres le premier demarrage : le
  volume `pgdata` a fige l'ancien, et le service ne joindrait plus sa base
  avec un message qui ne dit pas que la cause est la.

• `campay_env` reste sur DEMO au lancement. Passer aux paiements reels, c'est
  changer ce parametre et redemarrer — aucun redeploiement.
─────────────────────────────────────────────────────────────
RAPPELS

if [ "$manque" -gt 0 ]; then
  echo
  echo "$manque parametre(s) manquant(s) : le deploiement ECHOUERA tant qu'ils"
  echo "ne sont pas poses (le script de deploiement les nomme et s'arrete)."
  exit 1
fi

echo
echo "Tous les parametres sont poses. Le deploiement peut partir."
