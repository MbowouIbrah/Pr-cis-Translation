# Mettre Précis Translator en ligne

Cible : **une machine AWS** qui fait tourner la pile Docker du dépôt, derrière
**Cloudflare** pour le nom de domaine et le HTTPS. Compter une heure.

Ce document ne décrit pas une pile idéale, mais celle qui existe : trois
conteneurs (`db`, `backend`, `web`), deux volumes qui survivent aux
reconstructions, un seul port ouvert.

> **Deux chemins mènent en ligne, et celui-ci n'est plus le chemin normal.**
>
> | | Ce document | `infra/aws/README.md` |
> |---|---|---|
> | Comment | À la main, en SSH/Session Manager | CloudFormation + GitHub Actions |
> | Secrets | Fichier `.env` écrit sur la machine | SSM Parameter Store, chiffré |
> | Déploiement | Commandes recopiées | `release/version.txt` poussé sur `main` |
> | Reproductible | Non : l'état vit dans la machine | Oui : l'état vit dans le dépôt |
>
> **Suivez `infra/aws/README.md`.** Gardez ce document-ci pour comprendre ce que
> l'automatisation fait à votre place, pour dépanner une machine déjà en ligne,
> ou pour monter un bac à sable jetable. Les deux décrivent la MÊME pile — mais
> si vous appliquez celui-ci sur l'instance gérée par CloudFormation, vous
> écrivez un `.env` à la main que le prochain déploiement écrasera depuis SSM,
> et vos valeurs disparaîtront sans un mot.

---

## Pourquoi AWS et non Cloudflare Workers

Ce n'est pas une préférence, c'est une contrainte de l'application :

| Ce dont le service a besoin | Workers |
|---|---|
| Lancer **LibreOffice** (aperçus PPTX / DOCX / XLSX) | pas de sous-processus |
| Rendre un livre entier (minutes de calcul) | temps processeur borné par requête |
| **File de priorité par plan**, threads persistants | pas d'état entre requêtes |
| Volume de fichiers traduits | pas de disque |

Trois des quatre piliers du produit — dont la priorité de file, qui est
l'argument de vente des plans payants — ne peuvent pas exister sur Workers.
Cloudflare garde malgré tout un rôle utile, décrit plus bas : DNS, TLS et
protection en frontal.

**Supabase pour le stockage** est un chantier distinct, pas un travail du jour
de la mise en ligne : `config.py` **dérive** `TRANSLATIONS_DIR` de
l'emplacement du code, sans lire aucune variable, et chaque écriture passe par
un chemin disque. Y substituer un stockage objet demande une couche de
stockage et des URL signées. La bonne nouvelle : la **comptabilité d'espace**
que Supabase devait servir (`User.storage_used`, `Document.storage_charged`,
plafonds par plan) existe déjà en base et ne dépend pas de l'endroit où
dorment les fichiers. Le volume Docker fait l'affaire pour cette version.

---

## 1. La machine

**Lightsail**, instance **4 Go de mémoire / 2 vCPU**, Ubuntu 24.04 (une EC2
`t3.medium` convient aussi). Moins de 4 Go ne suffit pas : LibreOffice et le
rendu PDF cohabitent mal dans 2 Go.

Prévoir **20 Go de disque au moins**. Les documents traduits s'accumulent dans
le volume `translations` et **rien ne les supprime automatiquement** aujourd'hui
(seul l'utilisateur peut effacer les siens) — voir « Surveiller » à la fin.

Pare-feu de l'instance : ouvrir **80** et **443**, plus 22 pour
l'administration. Ne jamais ouvrir 5432 ni 8000 ; ni la base ni le service ne
publient de port, c'est voulu.

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker
```

## 2. Le code

```bash
git clone <dépôt> precis && cd precis
git checkout v1.1.0          # une version publiée, jamais une branche
```

## 3. La configuration — l'étape qui décide de tout

```bash
cp backend/.env.example backend/.env
```

Générer **chaque** secret, un par un, et ne jamais recopier ceux d'une machine
de développement :

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT_SECRET
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # FRONTEND_API_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # POSTGRES_PASSWORD
```

À poser obligatoirement dans `backend/.env` :

| Variable | Valeur |
|---|---|
| `PRECIS_ENV` | `production` |
| `JWT_SECRET` | fraîchement généré, ≥ 32 caractères |
| `FRONTEND_API_KEY` | fraîchement généré |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Compose en **dérive** `DATABASE_URL`, ne pas la recopier |
| `DEEPSEEK_API_KEY` | la clé réelle — sans elle, aucune traduction |
| `ALLOWED_ORIGINS` | `https://votre-domaine` **seul**, sans localhost |
| `FRONTEND_URL` | `https://votre-domaine` |
| `SMTP_*`, `EMAIL_ENABLED` | ou `EMAIL_ENABLED=false`, et alors aucun compte ne s'active |
| `ADMIN_ALERT_EMAIL` | destinataire des alertes d'erreurs et des tickets |

Le service **refuse de démarrer** si l'un de ces réglages trahit un gabarit, et
le message nomme chaque faute avec la commande qui la corrige. Ce refus est une
fonctionnalité : un service qui démarre avec un secret public laisse n'importe
quel lecteur du dépôt forger un jeton d'administrateur.

Deux pièges que le garde attrape, parce qu'ils sont réellement arrivés :

- un secret qui **s'annonce provisoire** (`dev-secret-key-change-in-production…`)
  est refusé même s'il est long et sans `change-me` ;
- `FRONTEND_URL` resté sur `localhost` est refusé : les liens de vérification
  partiraient vers la machine de l'utilisateur et **aucune inscription
  n'aboutirait**, sans une ligne de journal.

## 4. Démarrer

```bash
./scripts/precis.sh up      # construit et démarre
./scripts/precis.sh logs    # suivre jusqu'à « Application startup complete »
```

Toujours passer par ce script : il force `--env-file backend/.env`. Un
`docker compose` nu lit un second fichier de secrets à la racine, qui finit par
diverger du premier.

Les **migrations s'appliquent seules** au démarrage (`alembic upgrade head`,
avec attente de la base et dix tentatives). Il n'y a rien à lancer à la main, et
surtout pas de création de schéma implicite : une base non migrée est une erreur,
pas un cas à rattraper.

Vérifier :

```bash
curl -fsS http://localhost/health      # {"status":"ok"}
./scripts/precis.sh ps                 # les trois conteneurs « healthy »
```

## 5. Le domaine et le HTTPS

La pile ne publie que le port **80, en clair** : le chiffrement est le rôle du
frontal. Sans cette étape, **les mots de passe circulent en clair** — elle n'est
pas optionnelle.

Dans Cloudflare : un enregistrement **A** vers l'IP publique de l'instance,
**proxy activé** (nuage orange), puis SSL/TLS en mode **Full**. Cloudflare
termine le TLS et sert le certificat ; l'origine reste en 80 sur le réseau
Cloudflare.

Resserrer ensuite : n'autoriser le port 80 de l'instance que depuis les
[plages d'adresses Cloudflare](https://www.cloudflare.com/ips/), sinon l'IP
nue reste joignable en clair et contourne le frontal.

Cloudflare apporte aussi, sans travail supplémentaire, la limitation de débit et
la protection contre les abus — utile sur un service qui consomme une API payante
à chaque traduction.

> Sans Cloudflare : `certbot --nginx` sur l'instance, en publiant 443 et en
> montant les certificats dans le conteneur `web`. Plus d'entretien, même
> résultat.

## 6. Vérifier que le service rend bien le service

Le `/health` ne prouve que le démarrage. Faire le parcours réel, une fois :

1. créer un compte → **l'e-mail de vérification arrive et son lien fonctionne**
   (c'est le test de `FRONTEND_URL`) ;
2. téléverser un PDF de quelques pages → l'aperçu apparaît **page par page**
   pendant la traduction (le flux SSE passe le frontal) ;
3. télécharger le résultat → le fichier s'ouvre ;
4. téléverser un PPTX → l'aperçu arrive (preuve que LibreOffice tourne dans le
   conteneur) ;
5. un document **scanné** → refusé proprement, et **non facturé**.

## 7. Ce que cette version ne fait pas

À savoir avant d'ouvrir le service à des clients :

- **Paiements en mode DEMO** tant que `CAMPAY_ENV=DEMO`. Aucun encaissement
  réel. Les clés de production s'activent par un simple changement de `.env` et
  un redémarrage — pas besoin de redéployer.
- **Aucune expiration automatique des fichiers traduits.** Le volume ne fait que
  croître ; surveiller et purger (voir ci-dessous).
- **Documents scannés exclus** : détectés, refusés, non facturés. L'OCR est une
  étude en cours (`docs/etude-ocr.md`).
- **Un seul worker uvicorn**, imposé par l'entrypoint : la file de priorité et le
  registre des travaux vivent en mémoire. Monter en charge se fait en
  agrandissant la machine, pas en ajoutant des workers — deux workers auraient
  deux files qui s'ignorent.
- Le nombre de pages ignorées est calculé par le service mais **pas encore
  affiché** dans l'interface.

## Surveiller

```bash
./scripts/precis.sh logs backend                 # journaux du service
docker system df -v | grep translations          # taille des fichiers traduits
df -h /                                          # disque de l'instance
```

La vue **`/admin/logs`** regroupe les erreurs par empreinte et alerte par e-mail
au-delà d'un seuil ; **`/admin/support`** reçoit les demandes des utilisateurs.
Les consulter vaut mieux que lire les journaux.

## Revenir en arrière

```bash
git checkout v1.0.0 && ./scripts/precis.sh rebuild
```

Les volumes survivent : ni les comptes, ni les documents ne sont perdus. Une
migration de base, en revanche, ne se dé-applique pas toute seule — vérifier
`CHANGELOG.md` pour savoir si la version quittée en a introduit une.
