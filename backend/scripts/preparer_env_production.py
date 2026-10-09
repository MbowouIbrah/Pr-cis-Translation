"""Fabrique le `.env` de production, secrets neufs, et le VÉRIFIE.

À lancer sur la machine de production, une seule fois :

    python backend/scripts/preparer_env_production.py --domaine precis-exemple.com

Il part de `backend/.env.example`, remplace ce qui doit l'être, génère chaque
secret au hasard, puis **soumet le résultat au garde de configuration** —
le même que celui qui décide si l'application accepte de démarrer en ligne.
Un fichier qui sort d'ici démarre, ou le script dit pourquoi non.

Pourquoi un script plutôt qu'une consigne : un `.env` de production se remplit
à la main une fois, mal, et l'on cherche ensuite pendant une heure laquelle des
trente variables manquait. Les trois secrets ci-dessous ne doivent SURTOUT pas
être recopiés d'une machine de développement — c'est le défaut que le garde
attrape le plus souvent.

Le script n'écrase jamais un `.env` existant sans `--forcer` : sur un service
en ligne, regénérer `JWT_SECRET` déconnecte tout le monde, et perdre
`POSTGRES_PASSWORD` sépare le service de sa propre base.
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
EXEMPLE = BACKEND / ".env.example"
CIBLE = BACKEND / ".env"


def secret(n: int = 48) -> str:
    return secrets.token_urlsafe(n)


def poser(texte: str, cle: str, valeur: str) -> str:
    """Remplace `CLE=...` dans le texte, en gardant les commentaires autour.

    On réécrit la ligne existante au lieu d'ajouter la variable à la fin : un
    `.env` où la même clé figure deux fois est une source de pannes
    incompréhensibles (c'est la DERNIÈRE qui gagne, pas celle qu'on a lue).
    """
    motif = re.compile(r"^%s=.*$" % re.escape(cle), re.MULTILINE)
    ligne = "%s=%s" % (cle, valeur)
    if motif.search(texte):
        return motif.sub(lambda _: ligne, texte, count=1)
    return texte.rstrip("\n") + "\n" + ligne + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--domaine", required=True,
                    help="domaine public SANS schéma, ex. precis-exemple.com")
    ap.add_argument("--email-admin", default="",
                    help="destinataire des alertes d'erreurs et des tickets")
    ap.add_argument("--forcer", action="store_true",
                    help="écrase un .env existant (une sauvegarde est faite)")
    a = ap.parse_args()

    # `lstrip` retirerait n'importe quel caractère de l'ENSEMBLE "https://" —
    # pas le préfixe : « precis-exemple.com » y perdait son « p » initial et
    # devenait « recis-exemple.com ». Un domaine faux de cette façon ne se
    # remarque qu'au premier e-mail de vérification qui ne mène nulle part.
    domaine = a.domaine.strip()
    for prefixe in ("https://", "http://"):
        if domaine.lower().startswith(prefixe):
            domaine = domaine[len(prefixe):]
            break
    domaine = domaine.rstrip("/")
    if not domaine or "." not in domaine or " " in domaine:
        print("Domaine invalide : %r" % a.domaine)
        return 2
    site = "https://" + domaine

    if not EXEMPLE.exists():
        print("Introuvable : %s" % EXEMPLE)
        return 2

    if CIBLE.exists() and not a.forcer:
        print("%s existe déjà.\n"
              "Relancer avec --forcer pour l'écraser (une sauvegarde sera\n"
              "faite). Attention : sur un service EN LIGNE, un nouveau\n"
              "JWT_SECRET déconnecte tous les utilisateurs, et un nouveau\n"
              "POSTGRES_PASSWORD coupe le service de sa base existante."
              % CIBLE)
        return 1

    if CIBLE.exists():
        sauve = CIBLE.with_suffix(".env.sauvegarde")
        shutil.copy2(CIBLE, sauve)
        print("Sauvegarde : %s" % sauve.name)

    t = EXEMPLE.read_text(encoding="utf-8")

    # Les trois secrets. Générés ici, jamais recopiés d'ailleurs.
    t = poser(t, "JWT_SECRET", secret(48))
    t = poser(t, "FRONTEND_API_KEY", secret(24))
    mdp_bdd = secret(24)
    t = poser(t, "POSTGRES_PASSWORD", mdp_bdd)

    # Les réglages qui disent « je suis en ligne ».
    t = poser(t, "PRECIS_ENV", "production")
    t = poser(t, "FRONTEND_URL", site)
    t = poser(t, "ALLOWED_ORIGINS", site)

    # DATABASE_URL reste sur localhost À DESSEIN : Compose la REMPLACE par
    # l'adresse interne du conteneur `db`, dérivée des POSTGRES_*. La poser à
    # « db » ici casserait l'usage hors conteneur (migrations à la main, psql).
    t = poser(t, "DATABASE_URL",
              "postgresql+asyncpg://precis:%s@localhost:5432/precis" % mdp_bdd)
    t = poser(t, "POSTGRES_USER", "precis")
    t = poser(t, "POSTGRES_DB", "precis")

    if a.email_admin:
        t = poser(t, "ADMIN_ALERT_EMAIL", a.email_admin)

    CIBLE.write_text(t, encoding="utf-8")
    try:
        os.chmod(CIBLE, 0o600)   # sans effet utile sous Windows, correct ailleurs
    except OSError:
        pass
    print("Écrit : %s" % CIBLE)

    # ── La vérification : le même garde que celui du démarrage ──────────────
    #
    # Sans elle, le script ne serait qu'un presse-papiers sophistiqué. On
    # recharge `config` dans un processus NEUF, car il lit ses valeurs à
    # l'import : les relire ici ne verrait que l'ancien environnement.
    import subprocess
    code = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "from app import config\n"
        "f = config.defauts_de_production()\n"
        "print('PRECIS_ENV=' + repr(config.IS_PRODUCTION))\n"
        "[print('FAUTE: ' + x) for x in f]\n"
        "sys.exit(1 if f else 0)\n" % BACKEND
    )
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PRECIS_NO_BANNER"] = "1"
    # On vide les variables que le shell courant pourrait imposer : sans cela,
    # on vérifierait l'environnement du poste au lieu du fichier qu'on écrit.
    for cle in ("JWT_SECRET", "FRONTEND_API_KEY", "DATABASE_URL", "FRONTEND_URL",
                "ALLOWED_ORIGINS", "PRECIS_ENV", "DEEPSEEK_API_KEY",
                "EMAIL_ENABLED", "SMTP_PASSWORD"):
        env.pop(cle, None)
    p = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(BACKEND),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    sortie = (p.stdout + p.stderr).strip()

    print("\n── Contrôle de configuration ─────────────────────────────────")
    if p.returncode == 0:
        print("Aucun défaut : ce fichier démarre en production.")
    else:
        for l in sortie.splitlines():
            if l.startswith("FAUTE: "):
                print("  • " + l[7:])
        print("\nIl reste des réglages à poser À LA MAIN dans backend/.env.\n"
              "Ce sont des valeurs que personne ne peut générer au hasard :\n"
              "  DEEPSEEK_API_KEY   la clé d'API de traduction (sans elle,\n"
              "                     aucune traduction n'aboutit)\n"
              "  SMTP_PASSWORD      l'envoi d'e-mails, OU EMAIL_ENABLED=false\n"
              "                     (et alors aucun compte ne peut s'activer)")

    print("\nÀ NE PAS OUBLIER : ce fichier contient des secrets.\n"
          "Il n'est pas suivi par git (vérifié), ne le copiez pas dans un\n"
          "message ni dans une capture d'écran.")
    return 0 if p.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
