# Passation

## Objectif

Mettre **v1.1.0** en ligne **aujourd'hui** sur AWS EC2 + `docker compose`,
piloté par CloudFormation + GitHub Actions (OIDC) + SSM. Guide suivi avec
l'utilisateur, une étape à la fois.

## Fait

- **IaC complète, 12 fichiers neufs** : `infra/aws/bootstrap-github-oidc.yml`
  (OIDC, 2 rôles, 2 dépôts ECR, budget) ; `infra/aws/precis-translator.yml`
  (VPC, SG 80/443, EC2, EIP, gp3 chiffré, IMDSv2, disque de données `Retain`) ;
  `.github/workflows/` `ci.yml` (21 suites en barrage, aucune CI n'existait),
  `deploy.yml` (4 verrous : version→tests→images→SSM), `infra.yml` ;
  `deploy/scripts/aws-ssm-deploy.sh` ; `deploy/docker/docker-compose.aws.yml` ;
  `infra/aws/poser-secrets.sh` + `README.md` ; `release/version.txt`.
- **Guide pas à pas** : `docs/guide-premiere-mise-en-ligne.md` (11 étapes,
  chaque clic, une preuve par étape). `feuille-de-route-production.md` réécrite.
- `package.json:3` → **1.1.0** (sinon la garde de version arrête tout).
- **Free tier** : `precis-translator.yml:47` `t3.micro`, `:71` racine 20 Go,
  `:79` données 10 Go, `:492` swap **4 Go**.
- **Vérifié** : 21/21 suites exit 0, **418/418 checks** ; `bash -n` OK (2
  scripts), 6 YAML parsent ; tous les chemins cités dans les docs existent.
- **Étape 1 faite par l'utilisateur** : compte AWS créé.

## Décisions

- **ECR et non GHCR** : GHCR imposerait un PAT stocké ; le rôle d'instance
  suffit pour ECR.
- **`t3.micro` choisi par l'utilisateur**, contre la mesure du projet
  (`docs/deploiement.md:57` : « moins de 4 Go ne suffit pas »). Compensé par 4 Go
  de swap. **Risque accepté** : une conversion PPTX/XLSX lourde peut faire tuer
  Postgres. Remède sans rebuild : entrée `type_instance` du workflow.
- **Cloudflare DNS/proxy utilisé** (étape 10) : la décision « AWS et non
  Cloudflare » ne visait que **Workers** (exécuter l'app), pas le frontal. La
  doc entretenait la confusion ; le guide la lève en tête.
- **`infra.yml` séparé de `deploy.yml`** : une nouvelle AMI Canonical remplace
  l'instance — cela se décide, pas au milieu d'une mise en ligne.
- **4 valeurs en Variables GitHub, pas en Secrets** : un ARN de rôle ne donne
  rien sans jeton OIDC du dépôt ; les masquer gênerait le dépannage pour rien.
- **Boîte à outils AWS (`aws configure agent-toolkit`) écartée** : légitime
  (dépôt `aws/`, aucune clé longue durée) mais elle écrit dans le fichier de
  conventions à la racine, ajoute un serveur MCP, et n'apporte rien à un
  déploiement déjà en IaC. À réévaluer après, scripts inspectés avant exécution.

## Reste à faire

1. **Étape 2 du guide** : utilisateur IAM d'administration + clé CLI.
   Le compte AWS est celui du **groupe Trigenys**, pas de Precis : l'utilisateur
   porte donc le nom de la **personne** (`ibrah`), pas du projet. Seules les
   ressources sont préfixées `precis-*` — vérifié : pile, rôles, ECR, SSM,
   budget le sont tous, un projet voisin cohabite sans collision. Confirmer au
   passage que le MFA racine est actif (dit « ok », non vérifié).
2. Étapes 3 à 11, dans l'ordre, une par une.
3. **Commit + tag `v1.1.0`** — l'étape 9 le fait ; rien n'est committé à ce jour
   (12 fichiers neufs + ~15 modifiés en attente).
4. **Après la mise en ligne, par urgence réelle** : purge des fichiers traduits
   (rien ne supprime → disque plein = service arrêté) ; sauvegarde du volume
   `pgdata` (rien ne le sauvegarde) ; puis Supabase, paiements réels.

## Pièges

- **`VITE_API_KEY` est cuite dans l'image au build** : elle ne peut pas venir de
  SSM à l'exécution. `deploy.yml:181` la lit dans SSM **pour le build**, donc
  dans la source exacte où l'instance lira `FRONTEND_API_KEY`. Ne pas
  « simplifier » en secret GitHub.
- **`env_file:` et `--env-file` ne font PAS doublon** : l'un injecte dans le
  conteneur, l'autre alimente la substitution `${...}`. Les deux nécessaires,
  même `backend/.env`.
- **Le premier `changeset` (étape 7.1) n'a jamais tourné contre AWS** : attendre
  1-2 refus (quota, nom pris, droit trop serré). D'où le mode `changeset`.
- **Cloudflare SSL/TLS en `Flexible` fait figer l'aperçu** (SSE mis en tampon),
  page blanche sans erreur. Il faut **Full**.
- **Ne jamais changer `postgres_password` après le premier démarrage** : le
  volume a figé l'ancien, et l'erreur ne dit pas la cause.
- **Free tier — j'avais écrit « 12 mois (pas 6) », c'est à vérifier.** Les
  comptes ouverts depuis juillet 2025 (c'est le cas du compte Trigenys, créé le
  09/10/2026) reçoivent des **crédits valables ~6 mois**, pas 12 mois de free
  tier. À lire dans Billing → Free Tier / Crédits. Dans les deux cas, **toute
  IPv4 publique est facturée** depuis 2024 (~3,6 $/mois) : l'EIP n'est pas
  gratuite. Budget d'alerte ramené à **20 $** (défaut du modèle, `MinValue: 1`),
  et il **n'arrête rien** : deux e-mails, la facture continue.
- Le **fournisseur OIDC n'existe qu'une fois par compte** : s'il existe, passer
  son ARN en `ExistingOidcProviderArn`.
- Le disque de données est en `Retain` : il survit à la suppression de la pile
  et reste facturé jusqu'à suppression manuelle.
- Tests : `cd backend && PYTHONIOENCODING=utf-8 venv/Scripts/python.exe
  tests/test_X.py` (sinon Windows décode en Latin-1 et les assertions
  accentuées échouent à tort). 21 suites, 418 checks.
- `backend/.env` n'est pas suivi par git et ne doit jamais l'être ; ses secrets
  ont circulé sur le poste et sont refusés en production.
- `stash@{0}` = WIP moteur PDF hybride (p148), **à ne pas ressortir** ici.

## Mise à jour 09/10 (fin de session)

- **Étape 2 faite** : compte AWS Trigenys `137513282332`, utilisateur IAM
  `Ibrahim` (console). **Une clé d'accès CLI a été créée par erreur** : le modèle
  atelier2026 n'en utilise aucune. À SUPPRIMER (IAM → Ibrahim → Informations
  d'identification → Clés d'accès → Désactiver puis Supprimer) et supprimer le
  `.csv` téléchargé. Restent à vérifier : MFA sur `Ibrahim`, MFA racine,
  politique `AdministratorAccess` (non vue), reconnexion via l'URL.
- **Guide réécrit en mode console** : plus d'`aws configure` ; étape 4 = repérer
  le fichier ; étape 5 = CloudFormation → charger `bootstrap-github-oidc.yml` ;
  7.3 et 8 = Session Manager par la console EC2. Prochaine : **étape 3 puis 4-5**.
- `infra/aws/README.md` et `docs/feuille-de-route-production.md` citent encore
  des commandes CLI (équivalents optionnels) : non réécrits.

## Etat au 09/10 12:15 — etapes 1 a 5 FAITES

- Compte AWS **Trigenys `137513282332`**, region **eu-west-3 (Paris)**.
- Utilisateur IAM **`Ibrahim`** (console uniquement), `AdministratorAccess`
  attachee directement — verifie a l'ecran. **Cle d'acces CLI supprimee**
  (creee par erreur : le modele atelier2026 n'en utilise aucune).
  **Restent a faire, non bloquants : MFA sur `Ibrahim` et MFA racine** (aucun
  dispositif a ce jour, l'ecran affichait « Active sans MFA »).
- **Pile `precis-bootstrap` : `CREATE_COMPLETE`** le 09/10 a 12:14:16 (47 s).
  Creee **par la console** (Charger un fichier de modele), pas par CLI.
  Le fournisseur OIDC **n'existait pas** sur ce compte : `ExistingOidcProviderArn`
  laisse vide, aucun echec.
- Parametres retenus : `AlertEmail=contact@trigenys.com`, **`BudgetMonthlyUsd=20`**
  (defaut du modele abaisse de 40 a 20), le reste aux valeurs par defaut.
  ⚠ **L'e-mail de confirmation AWS sur `contact@trigenys.com` doit etre valide**,
  sinon l'alerte de budget ne partira jamais.

### Prochaine action : **etape 6** — les 4 variables GitHub

GitHub → Settings → Secrets and variables → Actions → onglet **Variables**
(PAS Secrets) → New repository variable, 4 fois. **Valeurs relevees le 09/10 :**

| Name GitHub | Valeur |
|---|---|
| `AWS_REGION` | `eu-west-3` |
| `AWS_ROLE_DEPLOY` | `arn:aws:iam::137513282332:role/precis-deploy` |
| `AWS_ROLE_CFN_EXECUTION` | `arn:aws:iam::137513282332:role/precis-cfn-execution` |
| `ECR_REGISTRY` | `137513282332.dkr.ecr.eu-west-3.amazonaws.com` |

Autres sorties : `OidcProviderArn` =
`arn:aws:iam::137513282332:oidc-provider/token.actions.githubusercontent.com`
(a repasser en `ExistingOidcProviderArn` si un AUTRE projet du compte Trigenys
refait un bootstrap : il n'existe qu'une fois par compte) ;
`SsmPrefixUtilise` = `/precis-translator/prod`.

⚠ **Avant l'etape 7** : le depot GitHub ne contient PAS encore les workflows
(12 fichiers neufs + ~15 modifies, non commites, branche
`chore/mise-en-production-v1.1.0`). Le workflow Infrastructure doit exister sur
`main` pour etre declenchable. Donc : commit + push + PR vers `main` AVANT
l'etape 7.

Puis etape 7 (workflow Infrastructure, mode `changeset` d'abord).
