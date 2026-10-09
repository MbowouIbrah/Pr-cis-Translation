"""Le garde-fou de production : aucun secret public ne doit pouvoir démarrer.

LE DÉFAUT VERROUILLÉ ICI
------------------------
Tous les secrets avaient une valeur par défaut et l'application démarrait avec
elles sans un mot. Trois sont PUBLIQUES — elles figurent dans `.env.example`,
donc dans le dépôt, donc dans tout clone :

  • `JWT_SECRET`        → quiconque lit le dépôt forge un jeton d'accès admin ;
  • `FRONTEND_API_KEY`  → n'importe quelle page déclenche nos conversions ;
  • `DATABASE_URL`      → base `postgres:postgres`, sans mot de passe propre.

CE QUI EST MESURÉ (et pourquoi de cette façon)
----------------------------------------------
1. ACCORD `.env.example` ↔ garde. Ce n'est pas la valeur du gabarit qui est
   testée — la tester des deux côtés serait aveugle — mais le fait que TOUT
   gabarit publié dans `.env.example` soit refusé par le garde. Changer le
   gabarit sans mettre le garde à jour fait échouer cette suite, au lieu de
   laisser passer un secret connu de tous.

2. ACCORD entre modules : le secret que le garde inspecte est bien l'objet que
   `core/security.py` utilise pour SIGNER. Deux lectures `os.getenv` séparées
   se seraient tues.

3. Le comportement de bout en bout, dans de VRAIS processus fils avec un
   environnement distinct — parce que `config` lit l'environnement à l'import,
   et qu'un rechargement de module en cours de test ne prouverait pas ce que
   fait un démarrage réel.

    backend/venv/Scripts/python.exe backend/tests/test_garde_secrets.py
"""
from __future__ import annotations

import os
import subprocess
import sys

import racine  # noqa: F401  -- met backend/ sur le chemin

from app import config                                        # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_checks: list[tuple[bool, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _checks.append((bool(cond), label))
    marque = "OK  " if cond else "ÉCHEC"
    print(f"  {marque} {label}" + (f"   [{detail}]" if detail and not cond else ""))


# ── Un montage d'application dans un processus neuf ──────────────────────────

_MONTAGE = (
    "import os, sys;"
    "sys.path.insert(0, r'%s');"
    "from app import create_app;"
    "create_app();"
    "print('DEMARRE')" % BACKEND
)


def monter(**env: str) -> tuple[int, str]:
    """Monte l'application dans un processus fils. Rend (code, sortie).

    L'environnement du fils est reconstruit à partir de zéro pour les clés qui
    nous intéressent : `load_dotenv` n'écrase jamais une variable déjà posée,
    donc ce que l'on pose ici gagne sur le `backend/.env` de la machine.
    """
    e = dict(os.environ)
    e["PRECIS_NO_BANNER"] = "1"
    # Le fils parle UTF-8 et on le lit en UTF-8. Sans ces deux lignes,
    # Windows décode sa sortie en Latin-1 : « refusée » devient
    # « refusÃ©e », et le test ne reconnaît plus le message de refus
    # qu'il attend — il échoue alors que le garde a parfaitement
    # fonctionné. Un test aveugle à l'accent est un test qui ment.
    e["PYTHONIOENCODING"] = "utf-8"
    e.update(env)
    p = subprocess.run([sys.executable, "-c", _MONTAGE], env=e,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    return p.returncode, (p.stdout + p.stderr)


# Un jeu de secrets SOLIDES : rien d'un gabarit, longueur suffisante.
SOLIDES = {
    "JWT_SECRET": "K7f2Qm9xTz4Rb1Nv8Lw3Yd6Hs0Pj5Ac2Ge7Uk4Mn1Vr8Xt3Zq6Bw9",
    "FRONTEND_API_KEY": "cle_frontend_reelle_9f2b71ac4e",
    "DATABASE_URL": "postgresql+asyncpg://precis:m0tDeP4sseReel@localhost:5432/precis",
    "DEEPSEEK_API_KEY": "sk-4f9b2c7e1a8d3506b9f2e4c7a1d8b503",
    "EMAIL_ENABLED": "false",
    # Sans cette ligne, les cas « secrets solides -> demarre » ci-dessous
    # echouent : l'adresse publique du site fait partie des reglages
    # qu'une mise en ligne doit poser, au meme titre qu'un secret.
    "FRONTEND_URL": "https://precis-translator.com",
}
PUBLIC = "https://precis-translator.com"
LOCAL = "http://localhost:5173"


def main() -> int:
    # ── 1. Accord : les gabarits de `.env.example` sont TOUS refusés ─────────
    print("\n1. Accord entre .env.example et le garde")
    exemple = {}
    with open(os.path.join(BACKEND, ".env.example"), encoding="utf-8") as fh:
        for ligne in fh:
            ligne = ligne.strip()
            if not ligne or ligne.startswith("#") or "=" not in ligne:
                continue
            cle, _, val = ligne.partition("=")
            exemple[cle.strip()] = val.strip()

    for cle in ("JWT_SECRET", "FRONTEND_API_KEY", "DATABASE_URL",
                "DEEPSEEK_API_KEY"):
        val = exemple.get(cle)
        check(val is not None, f"{cle} est bien documentée dans .env.example")
        if val is not None:
            check(config._est_gabarit(val),
                  f"le gabarit publié pour {cle} est REFUSÉ par le garde", val)

    # ── 2. Accord entre modules : le garde inspecte le secret qui signe ──────
    print("\n2. Le secret contrôlé est celui qui signe")
    from app.core import security
    check(security.JWT_SECRET is config.JWT_SECRET,
          "core/security.py signe avec le JWT_SECRET de config (même objet)")
    check(security.ACCESS_TOKEN_EXPIRY_MINUTES == config.JWT_EXPIRY_MINUTES,
          "l'expiration vient aussi de config")
    from app.core import database
    check(database.DATABASE_URL is config.DATABASE_URL,
          "core/database.py se connecte à l'URL de config (même objet)")

    # ── 3. Un secret solide n'est jamais pris pour un gabarit ───────────────
    print("\n3. Un vrai secret passe")
    check(not config._est_gabarit(SOLIDES["JWT_SECRET"]),
          "un secret aléatoire de 52 caractères est accepté")
    check(config._est_gabarit(""), "une valeur VIDE est refusée (secret absent)")
    check(config._est_gabarit("x" * 60 + "_here"),
          "un marqueur de gabarit est vu même noyé dans une longue valeur")

    # Secrets de DÉVELOPPEMENT. Ils ne portent aucun marqueur de gabarit et
    # sont assez longs pour passer le plancher : ce sont eux qui traversent
    # le garde. Cas réel — le `.env` du poste disait
    # « change-IN-production » quand la liste ne cherchait que
    # « change-ME », et le service aurait démarré en ligne avec un secret
    # connu. Un secret qui s'annonce lui-même comme provisoire doit être
    # refusé, quelle que soit sa longueur.
    for faux in ("dev-secret-key-change-in-production-64-chars-minimum-pad!",
                 "development-jwt-key-not-for-production-use-0123456789abc",
                 "local-only-secret-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                 "insecure-default-secret-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                 "sample-secret-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"):
        check(config._est_gabarit(faux),
              "un secret de DÉVELOPPEMENT est refusé, même sans « change-me »",
              faux[:38] + "…")

    # ── 4. Détection de production : le signal qu'on ne peut pas oublier ────
    print("\n4. Démarrage réel, dans des processus distincts")

    code, sortie = monter(ALLOWED_ORIGINS=PUBLIC, PRECIS_ENV="",
                          JWT_SECRET=config.DEFAUT_JWT_SECRET,
                          FRONTEND_API_KEY=config.DEFAUT_FRONTEND_API_KEY,
                          DATABASE_URL=config.DEFAUT_DATABASE_URL,
                          DEEPSEEK_API_KEY="your_deepseek_api_key_here")
    check(code != 0 and "Configuration de production refusée" in sortie,
          "origine PUBLIQUE + secrets par défaut  ->  REFUS de démarrer",
          sortie[-400:])
    check("JWT_SECRET" in sortie and "FRONTEND_API_KEY" in sortie,
          "le refus NOMME chaque réglage fautif")

    code, sortie = monter(ALLOWED_ORIGINS=PUBLIC, PRECIS_ENV="", **SOLIDES)
    check(code == 0 and "DEMARRE" in sortie,
          "origine PUBLIQUE + secrets solides  ->  démarre", sortie[-400:])

    code, sortie = monter(ALLOWED_ORIGINS=LOCAL, PRECIS_ENV="production",
                          **SOLIDES)
    check(code == 0 and "DEMARRE" in sortie,
          "PRECIS_ENV=production + secrets solides  ->  démarre", sortie[-400:])

    code, sortie = monter(ALLOWED_ORIGINS=LOCAL, PRECIS_ENV="production",
                          JWT_SECRET="trop-court", **{
                              k: v for k, v in SOLIDES.items()
                              if k != "JWT_SECRET"})
    check(code != 0 and "caractères" in sortie,
          "PRECIS_ENV=production + JWT_SECRET trop court  ->  REFUS",
          sortie[-400:])

    code, sortie = monter(ALLOWED_ORIGINS=LOCAL, PRECIS_ENV="",
                          JWT_SECRET=config.DEFAUT_JWT_SECRET,
                          FRONTEND_API_KEY=config.DEFAUT_FRONTEND_API_KEY,
                          DATABASE_URL=config.DEFAUT_DATABASE_URL,
                          DEEPSEEK_API_KEY="your_deepseek_api_key_here")
    check(code == 0 and "DEMARRE" in sortie,
          "développement local + secrets par défaut  ->  démarre (avertit seulement)",
          sortie[-400:])

    # FRONTEND_URL reste le réglage qu'on oublie, parce que rien ne le réclame :
    # ce n'est pas un secret, l'application démarre sans lui, et sa valeur par
    # défaut — `http://localhost:3000` — a l'air inoffensive. En ligne, elle
    # envoie chaque lien de vérification de compte sur la machine de
    # l'utilisateur : AUCUN compte ne peut être activé, et la redirection après
    # connexion Google part au même endroit. Panne totale des inscriptions,
    # sans une ligne de journal.
    code, sortie = monter(ALLOWED_ORIGINS=PUBLIC, PRECIS_ENV="",
                          **{k: v for k, v in SOLIDES.items()
                             if k != "FRONTEND_URL"})
    check(code != 0 and "FRONTEND_URL" in sortie,
          "production sans FRONTEND_URL public  ->  REFUS de démarrer",
          sortie[-400:])

    code, sortie = monter(ALLOWED_ORIGINS=PUBLIC, PRECIS_ENV="",
                          **{**SOLIDES, "FRONTEND_URL": "http://localhost:3000"})
    check(code != 0 and "FRONTEND_URL" in sortie,
          "production + FRONTEND_URL=localhost  ->  REFUS de démarrer",
          sortie[-400:])

    # ── 5. Les origines de développement disparaissent une fois en ligne ────
    #
    # Elles étaient ajoutées INCONDITIONNELLEMENT : un service en production
    # autorisait `http://localhost:5173` à l'appeler. Une page servie depuis la
    # machine d'un visiteur obtenait donc l'accès complet à l'API de production.
    print("\n5. Origines autorisées selon le mode")

    def origines(**env: str) -> list[str]:
        e = dict(os.environ)
        e["PRECIS_NO_BANNER"] = "1"
        e.update(env)
        p = subprocess.run(
            [sys.executable, "-c",
             "import sys;sys.path.insert(0, r'%s');"
             "from app.config import ALLOWED_ORIGINS;"
             "print('|'.join(sorted(ALLOWED_ORIGINS)))" % BACKEND],
            env=e, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        return [o for o in p.stdout.strip().split("|") if o]

    en_ligne = origines(ALLOWED_ORIGINS=PUBLIC, PRECIS_ENV="", **SOLIDES)
    check(en_ligne == [PUBLIC],
          "en ligne : SEULE l'origine déclarée est autorisée",
          str(en_ligne))
    check(not any("localhost" in o or "127.0.0.1" in o for o in en_ligne),
          "en ligne : plus aucune adresse locale n'est autorisée",
          str(en_ligne))

    local = origines(ALLOWED_ORIGINS=LOCAL, PRECIS_ENV="", **SOLIDES)
    check(len(local) > 1 and any("127.0.0.1" in o for o in local),
          "en développement : le confort des six adresses habituelles est gardé",
          str(local))

    force = origines(ALLOWED_ORIGINS=LOCAL, PRECIS_ENV="production", **SOLIDES)
    check(force == [LOCAL],
          "PRECIS_ENV=production suffit à couper les adresses de confort",
          str(force))

    # ── Verdict ─────────────────────────────────────────────────────────────
    passed = sum(1 for ok, _ in _checks if ok)
    total = len(_checks)
    print(f"\n{passed}/{total} contrôles")
    if passed != total:
        print("ÉCHECS :", [lbl for ok, lbl in _checks if not ok])
        return 1
    print("OK — aucun secret public ne peut atteindre la production")
    return 0


if __name__ == "__main__":
    sys.exit(main())
