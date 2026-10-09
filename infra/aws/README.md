# L'infrastructure, en code

Tout ce qui tourne en production est décrit par les fichiers de ce dossier.
Aucune ressource n'est créée à la main dans la console AWS : ce qui n'est pas
ici n'existe pas, et ce qui est ici peut être recréé à l'identique.

**Aucune clé AWS n'est stockée nulle part** — ni sur un poste, ni dans les
secrets GitHub. GitHub Actions présente un jeton signé par son propre
fournisseur d'identité, AWS le vérifie et accorde un rôle pour une heure.

**Aucun port 22.** L'accès administrateur passe par Session Manager.

---

## Les fichiers

| Fichier | Rôle | Quand on y touche |
|---|---|---|
| `bootstrap-github-oidc.yml` | Rôles OIDC, dépôts d'images, alerte de budget | Une fois, à la main |
| `precis-translator.yml` | VPC, instance, disque de données, pare-feu | Rarement, par le workflow « Infrastructure » |
| `poser-secrets.sh` | Génère et pose les secrets dans SSM | Une fois, depuis l'instance |
| `../../.github/workflows/ci.yml` | Les 21 suites, sur chaque proposition de fusion | — |
| `../../.github/workflows/infra.yml` | Provisionner, à la main | Changement d'infrastructure |
| `../../.github/workflows/deploy.yml` | Mise en ligne sur publication d'une version | — |
| `../../deploy/scripts/aws-ssm-deploy.sh` | Le déploiement, sur la machine | — |
| `../../deploy/docker/docker-compose.aws.yml` | La pile, avec images pré-construites | — |

---

## La première mise en ligne, dans l'ordre

Les étapes marquées 🧑 se font à la main, une seule fois. Les 🤖 sont
automatiques ensuite.

### 1. 🧑 L'amorçage

```bash
# Le fournisseur OIDC existe-t-il déjà sur ce compte ? Il n'y en a qu'un.
aws iam list-open-id-connect-providers

aws cloudformation deploy \
  --stack-name precis-translator-bootstrap \
  --template-file infra/aws/bootstrap-github-oidc.yml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      AlertEmail=vous@votredomaine.com \
      ExistingOidcProviderArn=     # ← l'ARN ci-dessus s'il existe, sinon vide
```

Puis relever les sorties :

```bash
aws cloudformation describe-stacks --stack-name precis-translator-bootstrap \
  --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output table
```

### 2. 🧑 Les variables du dépôt

Dans GitHub → *Settings* → *Secrets and variables* → *Actions* → onglet
**Variables** (et non *Secrets* : un ARN de rôle ne sert à rien sans un jeton
OIDC de ce dépôt précis) :

| Variable | Valeur |
|---|---|
| `AWS_ROLE_DEPLOY` | sortie `RoleDeployArn` |
| `AWS_ROLE_CFN_EXECUTION` | sortie `RoleExecutionCfnArn` |
| `AWS_REGION` | sortie `RegionAUtiliser` |
| `ECR_REGISTRY` | sortie `RegistreEcr` |

**Facultatif, mais recommandé** — verrouiller le rôle sur l'identité
*immuable* du dépôt, de sorte qu'un dépôt homonyme créé après suppression ne
retrouve pas l'accès :

```bash
gh api repos/MbowouIbrah/Pr-cis-Translation --jq .id
```

Ajouter `repository_id` aux claims OIDC du dépôt (*Settings* → *Actions* →
*General*), **puis** redéployer l'amorçage avec `GitHubRepositoryId=<id>`.
Renseigner ce paramètre **sans** avoir activé le claim produit une condition
qui ne correspondra jamais, et le déploiement échouera sur un refus
incompréhensible.

### 3. 🤖 Provisionner la machine

GitHub → *Actions* → **Infrastructure** → *Run workflow*.

- D'abord avec `action: changeset` : le journal montre exactement ce qui serait
  créé, modifié et surtout **remplacé**. On lit, puis on décide.
- Ensuite `action: appliquer`.

La pile échoue franchement si l'amorçage de la machine rate (`cfn-signal`),
plutôt que de livrer une machine muette que l'on ne pourrait pas inspecter.

### 4. 🧑 Poser les secrets

**Avant toute mise en ligne.** Le déploiement s'arrête en nommant ce qui manque.

```bash
aws ssm start-session --target <InstanceId>   # sortie de la pile
sudo -i
curl -fsSL https://raw.githubusercontent.com/MbowouIbrah/Pr-cis-Translation/main/infra/aws/poser-secrets.sh \
  -o /tmp/poser-secrets.sh
bash /tmp/poser-secrets.sh
```

Le script génère `jwt_secret`, `frontend_api_key` et `postgres_password` —
**depuis l'instance, donc ils ne traversent aucun disque ni aucun historique de
terminal** — puis **nomme** ceux que vous devez poser vous-même :
`deepseek_api_key`, `smtp_*`, `frontend_url`, `allowed_origins`,
`admin_alert_email`, `campay_*`, `google_client_id`.

Il est idempotent : le relancer ne régénère rien. Le rôle d'instance ne peut de
toute façon **que créer** un paramètre absent, jamais écraser un existant
(`ssm:Overwrite = false`).

### 5. 🧑 Le domaine

- Enregistrement DNS **A** sur la sortie `AdresseIpPublique` (l'adresse est
  fixe : remplacer l'instance ne la change pas).
- Cloudflare, proxy **actif**, SSL/TLS **Full**.
- **Puis resserrer** : workflow *Infrastructure* avec `cidr_http` sur les
  plages Cloudflare. Tant que ce n'est pas fait, l'adresse IP nue reste
  joignable en clair et **contourne le TLS** — c'est une étape à part entière,
  pas une finition.
- Avant le domaine, **ne créez aucun compte réel** : le mot de passe
  circulerait en clair.

### 6. 🧑 Publier la version

```bash
# Les quatre doivent concorder — le workflow le vérifie et refuse sinon.
cat release/version.txt            # 1.1.0
node -p "require('./package.json').version"   # doit valoir 1.1.0
grep '## \[1.1.0\]' CHANGELOG.md

git tag -a v1.1.0 -m "version 1.1.0"
git push origin v1.1.0
git push                            # pousse release/version.txt → déclenche
```

Pourquoi cette concordance : `app/versions.py` lit la version du projet **dans
`package.json` sur disque**. Si les deux divergent, `/health` et la bannière
annoncent une version qui n'est pas celle qui tourne, et plus aucun rapport de
bogue n'est interprétable.

### 7. 🧑 Vérifier pour de vrai

`/health` ne prouve que le démarrage. Le parcours client complet est décrit
dans `docs/deploiement.md`, section « Vérifier que le service rend bien le
service ».

---

## Les conventions partagées

Ces valeurs apparaissent dans plusieurs fichiers. **Une divergence ne produit
pas une erreur claire, mais une panne silencieuse.**

| Valeur | Où elle apparaît | Ce qui arrive si elle diverge |
|---|---|---|
| `precis-translator-prod` | amorçage (`DeployStackName`), les deux workflows | `AccessDenied` CloudFormation, sans dire que c'est le nom |
| `/precis-translator/prod` | amorçage, modèle, script, workflow | `.env` vide → le garde réclame une variable que vous venez de poser |
| `parameter${SsmPrefix}/*` | modèle, amorçage | `//precis-translator/...` : autorisation qui ne correspond à rien |
| `Projet=precis-translator` | tags de l'instance, condition `ssm:SendCommand` | commande refusée, sans dire lequel des deux |
| `/opt/precis` | amorçage de la machine, script, workflow | `cd` qui échoue, sans contexte |
| `frontend_api_key` | SSM → `FRONTEND_API_KEY` **et** `VITE_API_KEY` au build | **401 sur chaque requête, rien dans les journaux** |
| `precis` / `precis` | SSM, `preparer_env_production.py` | base créée sous un nom, cherchée sous un autre |
| `/app/backend/translations` | compose, `config.py`, Dockerfile | traductions dans la couche éphémère → **perdues au déploiement suivant** |
| `name: precis` (projet Compose) | les deux fichiers compose | `docker_pgdata` au lieu de `precis_pgdata` → base « vide » |
| un seul `IMAGE_TAG` | workflow, compose | interface et service de versions différentes |
| `VITE_API_BASE=""` | workflow | le navigateur appelle un autre domaine → partage entre origines |

---

## Le piège qui mérite son paragraphe

`VITE_API_KEY` est **cuite dans l'image** de l'interface au moment du build
(`ARG` + `ENV` dans `frontend/Dockerfile`, avec un contrôle Node bloquant).
Elle ne peut donc pas arriver au démarrage comme les autres réglages, et elle
doit valoir **exactement** `FRONTEND_API_KEY` du service — sinon l'interface
reçoit un 401 sur chaque requête et **rien** dans les journaux ne l'explique.

En local, `docker-compose.yml` garantissait l'égalité en prenant les deux
valeurs à la *même ligne du même fichier*. En production le `build:` a disparu,
donc ce filet n'existe plus.

Il est remplacé par : **le workflow lit la clé dans SSM pour construire
l'image**, c'est-à-dire dans la source *exacte* où le script de déploiement
lira `FRONTEND_API_KEY` quelques minutes plus tard. Une seule valeur, un seul
endroit, divergence mécaniquement impossible. Un secret GitHub aurait été une
*seconde copie* — précisément le défaut qu'on élimine.

---

## Les choix, et pourquoi

**Une seule instance, pas d'autoscaling.** L'application tient son registre de
travaux **en mémoire** (`JobManager._jobs`, dict de `queue.Queue`) et
`docker-entrypoint.sh` impose `--workers 1`. Deux instances, ou deux workers,
auraient chacun leur registre : un client interrogeant la progression d'une
traduction tomberait une fois sur deux sur le processus qui ne la connaît pas.
Sortir cet état en base ou en Redis est un chantier séparé ; tant qu'il n'est
pas fait, l'autoscaling produirait des pannes intermittentes inexplicables.

**EC2 + `docker compose`, pas ECS/Fargate.** Conséquence directe du point
précédent : un orchestrateur sans réplication n'apporte que sa complexité.

**Postgres en conteneur, ni RDS ni EFS.** Ses données sont dans un volume
nommé, posé sur un disque **séparé** et **retenu**.

**`t3.medium` et non `t3.micro`.** `docs/deploiement.md` a mesuré le besoin :
**moins de 4 Go ne suffit pas**, LibreOffice et le rendu PDF cohabitent mal
dans 2 Go. Avec 1 Go, le tueur de mémoire du noyau s'en prend au plus gros
processus — Postgres — au premier aperçu PPTX. `t3.micro` serait couvert par le
free tier ; il ne servirait pas le service.

**ECR et non GHCR.** Le rôle d'instance s'authentifie seul
(`aws ecr get-login-password`) : **aucun jeton à poser en SSM**, aucun secret à
faire tourner, rien qui expire. Un dépôt GHCR privé aurait exigé un jeton
personnel stocké — un secret de plus à surveiller.

**Le provisionnement est séparé du déploiement.** `LatestAmiId` est résolu sur
« la dernière Ubuntu 24.04 », et un changement d'AMI **remplace l'instance**. Si
le provisionnement tournait à chaque mise en ligne, il suffirait que Canonical
publie pendant la nuit pour qu'un déploiement de routine détruise et recrée la
machine. Le workflow *Infrastructure* fige donc l'AMI par défaut
(`UsePreviousValue`), et ne se lance qu'à la main.

**Le disque de données est une ressource séparée, en `Retain`.** C'est le
dernier filet : même un remplacement d'instance ne touche ni à la base ni aux
traductions. **Contrepartie à connaître :** détruire la pile **laisse** ce
disque, qui continue d'être facturé jusqu'à sa suppression manuelle.

---

## Ce que cette infrastructure ne fait pas encore

À traiter, par ordre d'urgence — ce sont des limites connues, pas des oublis :

1. **Rien ne purge les traductions.** Le disque grossit sans borne, et un
   disque plein arrête le service. Le contrôle des 5 Go libres du script de
   déploiement ne fait que *retarder* le problème. C'est le chantier n° 1.
2. **`pgdata` n'est pas sauvegardé.** Le disque est retenu et chiffré, mais un
   `DROP TABLE` malheureux ou une corruption restent sans recours. Un
   instantané EBS quotidien (AWS Backup) est la réponse la plus simple.
3. **Pas de surveillance de la mémoire ni du disque.** Ces métriques n'existent
   pas sans l'agent CloudWatch, qui n'est pas installé. Seule l'alarme de panne
   d'hôte est posée, avec reprise automatique. Annoncer une surveillance qu'on
   n'a pas instrumentée est pire que de ne rien annoncer : on cesse de regarder.
4. **Le retour en arrière ne défait pas les migrations.** Le script le détecte
   et le dit explicitement ; il ne le répare pas.
5. **Pas de serveur d'attente.** Une interruption pendant un remplacement
   d'instance est assumée : c'est la conséquence de l'état en mémoire.

---

## Mémo d'exploitation

```bash
# Ouvrir une session (sans port 22, sans clé privée)
aws ssm start-session --target <InstanceId>

# L'état de la pile
sudo docker compose -p precis \
  -f /opt/precis/src/deploy/docker/docker-compose.aws.yml ps

# Les journaux du service — c'est là que le garde de production
# NOMME chaque réglage fautif
sudo docker compose -p precis \
  -f /opt/precis/src/deploy/docker/docker-compose.aws.yml logs --tail=100 backend

# Quelle version tourne vraiment
curl -s http://127.0.0.1/health | jq .versions

# La version déployée, mémorisée par le script
cat /opt/precis/deploy/.tag-courant

# Revenir en arrière : republier release/version.txt sur la version
# précédente (son tag et ses images existent encore — le dépôt en garde dix).
```
