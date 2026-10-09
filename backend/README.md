# Backend — Précis Translator

API **FastAPI** : comptes, facturation, jobs de traduction, aperçus.
Version **1.1.0**.

Le travail lui-même — relever le texte d'un document, le réinjecter traduit —
n'est pas ici : il vit dans [`engines/`](engines/CONTEXTE.md), que le backend
utilise sans que les moteurs le connaissent.

## Démarrer

```bash
cd backend
python -m venv venv
venv/Scripts/activate            # Windows  |  source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env             # puis renseigner DEEPSEEK_API_KEY

venv/Scripts/python.exe -m uvicorn main:app --reload --port 8000
```

Depuis la racine, `npm run dev` lance backend **et** frontend ensemble.

`GET /health` répond l'état et les versions de tous les composants.
`http://localhost:8000/docs` donne l'API interactive.

**LibreOffice** est requis pour tout aperçu non-PDF (PPTX, DOCX). Sans lui, la
traduction fonctionne, l'aperçu non.

## La carte

```
main.py            point d'entrée ASGI — rien d'autre que create_app()
app/
  __init__.py      la fabrique : middleware, limiteur, routeurs, lifespan
  config.py        TOUTE lecture d'environnement, en un seul endroit
  banner.py        la bannière de démarrage
  versions.py      lit (ne copie jamais) les versions de chaque composant
  rate_limit.py    plafonds de débit — neutre si slowapi manque
  api/             routes HTTP : valider, autoriser, déléguer, répondre
  core/            socle : base, sécurité, tarifs, e-mail, paiement
  models/          modèles SQLAlchemy et forfaits
  services/        le travail : jobs, exécuteurs, cache de rendu, aperçus
engines/           les moteurs — n'importent JAMAIS app/
migrations/        Alembic
tests/             21 suites, 418 controles, hors ligne
```

Le détail des couches et de leurs interdits : [`../ARCHITECTURE.md`](../ARCHITECTURE.md).

## Trois règles qui tiennent le reste

**Les dépendances vont dans un seul sens.** `api → services → engines → rien`.
Aucune flèche ne remonte. Le contrôle tient en une ligne et doit rester vrai :

```bash
venv/Scripts/python.exe -c "import engines, sys; \
  print([m for m in sys.modules if m.startswith('app')])"     # => []
```

**Rien de bloquant dans un `async def`.** Réseau, LibreOffice, rendu PDF : tout
part en thread. Un seul appel bloquant fige l'application **entière**, flux SSE
compris — le symptôme est un écran qui ne se rafraîchit plus, et la cause est à
vingt fichiers de là.

**L'import ne fait rien, l'appel construit.** `create_app()` est une fabrique :
un test peut monter une instance propre, un script peut importer un service sans
réveiller LibreOffice.

## Configuration

Tout se lit dans `app/config.py` — un `os.getenv` ailleurs est un contournement.

| Variable | Rôle | Défaut |
|---|---|---|
| `DEEPSEEK_API_KEY` | Clé du traducteur | **requis** |
| `DEEPSEEK_BASE_URL` | Point d'entrée du modèle | `https://api.deepseek.com` |
| `FRONTEND_API_KEY` | Vérifiée dans `X-API-Key` | — |
| `ALLOWED_ORIGINS` | Origines CORS, **liste** — jamais `*` | localhost |
| `DATABASE_URL` | PostgreSQL (async) | — |
| `SMTP_HOST` / `_PORT` / `_USER` / `_PASSWORD` | Envoi des e-mails | — |
| `EMAIL_ENABLED` | `false` journalise au lieu d'envoyer | `true` |
| `FRONTEND_URL` | Base des liens dans les e-mails | `http://localhost:3000` |
| `PRECIS_NO_BANNER` | Coupe la bannière (tests) | — |

> `ALLOWED_ORIGINS` existait et n'était **utilisée nulle part** : le CORS
> répondait `*` avec `allow_credentials=True`, donc Starlette renvoyait
> l'origine de n'importe quel appelant. La clé d'API voyageant dans le bundle,
> toute page pouvait déclencher nos conversions LibreOffice. Vérifié par
> `tests/test_gardes_http.py`.

## Base de données

```bash
venv/Scripts/python.exe -m alembic upgrade head
venv/Scripts/python.exe seed_admin.py            # crée le compte administrateur
```

## Tests

Vingt et une suites, **418 contrôles**, sans réseau et sans DeepSeek. Chacune
s'exécute seule :

```bash
venv/Scripts/python.exe tests/test_gardes_http.py
for t in tests/test_*.py; do venv/Scripts/python.exe "$t"; done
```

Deux exigences :

* une suite **doit** sortir en `exit=0`. Un score vert suivi d'un code de retour
  non nul cache toujours quelque chose — c'est ainsi que le verrou de profil
  LibreOffice est resté invisible plusieurs semaines ;
* tout test doit passer le **test de mutation** : casser exprès ce qu'il
  protège, et vérifier qu'il rougit. Un test qui partage la constante qu'il
  vérifie est aveugle — il faut tester l'**accord** entre deux modules, pas la
  valeur.

## Comptes & auth

Authentification **sans mot de passe** (l'app n'en stocke aucun par défaut) :

1. `POST /api/auth/register {email, name?}` ou `login {email}` → un **code** est
   envoyé par e-mail ;
2. `POST /api/auth/verify-email {email, code}` → renvoie un **JWT** (+ refresh) ;
3. `POST /api/auth/google {credential}` → connexion Google, même issue ;
4. `POST /api/auth/refresh {refresh_token}` → renouvelle le couple.

Deux tables métier portent l'essentiel (SQLAlchemy 2.0 async, dans `app/models/`) :

* **User** — `id`, `email` (unique, minuscule), `password_hash` *nullable*
  (passwordless / Google), `google_id`, `plan`, `storage_used` / `storage_limit` ;
* **Document** — `id`, `user_id`, `original_name`, `source_lang` / `target_lang`,
  `original_path` / `translated_path`, `size_bytes`, `status`
  (`pending`/`translating`/`done`/`error`), `page_count`.

**Forfaits** : `free` · `starter` · `pro` · `enterprise` · `admin`. Le `free` est
plafonné à **1 page par mois calendaire** (402 au-delà) ; les payants ont les pages
illimitées et un stockage croissant. Les valeurs exactes (quotas, prix, zones) ne
sont **pas** en dur ici : source unique dans `app/models/` et `app/core/` (tarifs).
Le droit acquis à un paiement porte sur **le document**, pas sur le plan.

Compte administrateur : `seed_admin.py` (usage unique) — stockage illimité et mode
« précis » activable.

## Documentation de l'API

```bash
npm run docs:api        # -> docs/api/index.html
```

Page autonome, sans CDN ni serveur : 32 opérations groupées, paramètres, exemple
d'appel, codes de réponse. La source de vérité reste le **code** — une route mal
documentée se corrige dans la route, jamais dans le HTML.
