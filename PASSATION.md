# PASSATION — 15/10/2026 (nuit)

## 1. Objectif en cours
Aucun sujet ouvert : abonnements Starter / Pro et outillage admin livrés sur `feat/abonnements` (non poussée). Prochain sujet : §4.

## 2. Fait
- `main` local = `6116be5` (moteur 1.0.5, stockage, aperçu plein écran). **Non poussé.**
- `feat/paiement-fournisseurs` (non poussée) : `eea96ef` registre moyen → prestataire (`backend/app/core/payment_methods.py`, env `PAYMENT_METHODS`), `GET /api/payments/methods`, opérateur déduit du numéro ; `6b9f0ba` modale 2 tuiles + logos (`frontend/src/components/payment/PayLogos.tsx`).
- `feat/abonnements` (depuis la précédente, non poussée) :
  - `6127c94` + `9304e46` : `POST /api/payments/plan(/quote)` (`backend/app/api/payments.py`), migration `0009_abonnements` (users.plan_expires_at / plan_started_at, payments.plan / months / method / refund_due), échéance paresseuse (`backend/app/core/security.py` `_expire_plan_if_due`), cartes → `PaymentModal` (`frontend/src/components/pricing/PricingCards.tsx`).
  - `18596a8` revue 9/9 : `_loses_rights` revérifié au crédit → `refund_due` + message ; report au prorata (`_carry_over`) ; quota de date à date (`backend/app/services/usage.py` `quota_period`) ; 2e achat refusé pendant une invite (3 min) ; `expire_due_plans` avant la liste admin ; `PLAN_RANK` / `SELF_SERVE_PLANS` dans `backend/app/core/pricing.py`, servis par `/api/pricing`.
  - `8227a73` : `backend/seed_admin.py` idempotent (ADMIN_EMAIL, défaut mbowouibrah@gmail.com) lancé par `docker-entrypoint.sh` ; `backend/reinitialiser_donnees.py --confirmer EFFACER-TOUT` (toutes tables + fichiers traduits, puis l'admin seul) ; tests admin/journal purgent leurs comptes ; `ResizeObserver loop…` filtré (`frontend/src/lib/errorReporting.ts`).
  - Preuves : `test_abonnements` 30/30 (11 mutants tués), paiement/quota/verrous/admin/journal verts, `tsc -b` OK, Playwright bureau + mobile.
- Base LOCALE remise à zéro le 15/10 : seul `mbowouibrah@gmail.com` (admin) reste. Prod non touchée.

## 3. Décisions prises
- Le client choisit un MOYEN, jamais un prestataire ; routage dans `.env`. Moyen indisponible : grisé, et refusé par le serveur.
- Abonnement = paiement qui ouvre/prolonge une échéance (Campay ne prélève pas). Droits revérifiés AU CRÉDIT : le compte peut changer entre l'invite USSD et la validation.
- Plan attribué par l'admin = sans échéance, ne s'achète pas par-dessus. Remboursement `refund_due` : manuel pour l'instant.
- Quota d'un abonnement acheté : de date à date depuis `plan_started_at` (au mois civil, un achat du 28 donnait deux quotas en 4 jours).
- Pas de Firebase : l'authentification est interne (Postgres : code e-mail, mot de passe, Google). L'admin n'a pas de mot de passe.

## 4. Reste à faire
1. Pousser `main`, `feat/paiement-fournisseurs`, `feat/abonnements` ; PR — **sur accord explicite**. Puis, si voulu, vider la prod via SSM : `docker compose exec backend python reinitialiser_donnees.py --confirmer EFFACER-TOUT` (irréversible).
2. Vue admin des paiements `refund_due` ; rappel avant échéance d'abonnement (e-mail/SMS).
3. Échec de `getMethods` : afficher une erreur réessayable au lieu de « aucun moyen ».
4. ZitoPay prestataire carte (page hébergée → flux de redirection dans la modale), puis Camoo SMS.
5. 3b « Amélioration disponible » (lire `amelioration` des pages, retraduire les segments changés à nos frais).
6. Vérifs manuelles : quota/remboursement au redémarrage, réouverture pendant traduction, navigation mobile > 2 pages.

## 5. Pièges / contexte
- Serveurs : backend `cd backend && PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m uvicorn main:app --port 8000` (pas de rechargement auto) ; frontend `cd frontend && npm run dev` (port 3000).
- Tests = scripts : `cd backend && PYTHONIOENCODING=utf-8 venv/Scripts/python.exe tests/<t>.py` (pas de pytest). Tests de paiement : remplacent `campay.collect` / `transaction_status` sur l'instance.
- Fichiers souvent en CRLF : éditer par script Python `newline=''`. Heredocs bash avec backticks : échouent.
- Playwright via `C:/Python314/python.exe`, `launch(channel="chrome")` ; jeton dans localStorage `precis_tokens` puis `reload()` ; en-tête `X-Country: CM` pour la zone FCFA (les erreurs CORS Google Fonts qui en découlent sont un artefact).
- `tsc -b` laisse `frontend/tsconfig.tsbuildinfo` : le supprimer.
- L'alerte du journal d'erreurs part à TOUS les comptes `plan=admin` (seuil 50, `backend/app/services/error_log.py`).
- Correctif de rendu PDF → bump `backend/engines/pdf/version.py`. Ne jamais lancer `balayer_magasin` à la main. CI GitHub bloquée (facturation). Clés uniquement dans `backend/.env`. Ne pas fusionner xlsx/ocr sans accord.
