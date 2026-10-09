Écrit pour : vous, comme suite d'actions à exécuter dans l'ordre.

# Aller en production — étape par étape

Décisions déjà prises : **AWS** (pas Cloudflare Workers), **paiements en mode
DEMO** au lancement, **Supabase après** la mise en ligne. Les raisons sont dans
[`deploiement.md`](deploiement.md) ; ici, on agit.

L'infrastructure est **décrite en code** : deux modèles CloudFormation et trois
workflows GitHub. Vous n'installez rien à la main sur la machine — vous lancez
des workflows et vous posez des secrets. La référence complète de chaque fichier
est dans [`../infra/aws/README.md`](../infra/aws/README.md) ; cette feuille de
route en est l'ordre d'exécution.

Chaque étape se termine par une **preuve**. Si la preuve manque, ne passez pas à
la suivante.

**Durée réaliste : 2 à 3 heures**, dont beaucoup d'attente (propagation DNS,
première construction des images).

---

## Vue d'ensemble

```
ÉTAPE 0  Compte AWS + dépôt GitHub prêts          vous,  20 min
ÉTAPE 1  Amorcer OIDC et les dépôts d'images      workflow, 10 min
ÉTAPE 2  Créer l'infrastructure (CloudFormation)  workflow, 15 min
ÉTAPE 3  Poser les secrets dans SSM               vous,  20 min
ÉTAPE 4  Déployer la version                      workflow, 25 min
ÉTAPE 5  Domaine + HTTPS (Cloudflare)             vous,  30 min + attente
ÉTAPE 6  Vérifier le vrai parcours client         vous,  30 min
────────────────────────────────────────────────────────────────
APRÈS    Purge des fichiers, sauvegarde, Supabase, paiements réels
```

> **Pourquoi aucun build Docker sur votre PC ?** Mesuré : il vous reste **5,2 Go
> libres sur 238 Go (disque à 98 %)**, et la pile demande ~4,5 à 5,5 Go (l'image
> backend embarque LibreOffice). Les images sont construites **en intégration
> continue**, où il y a de la place, du cache et des gardes — puis tirées depuis
> le registre par l'instance. Rien ne se construit sur la machine de production
> non plus : elle n'aurait pas la mémoire pour le faire tout en servant.

---

## ÉTAPE 0 — Ce qu'il faut avoir sous la main

Rien à automatiser ici : ce sont les choses que seul le titulaire du compte peut
faire.

1. Un **compte AWS** avec un moyen de paiement valide, et une région choisie une
   fois pour toutes — `eu-west-3` (Paris) dans ce guide. Les modèles
   n'en imposent aucune : c'est la configuration de l'AWS CLI qui décide.
2. Le dépôt poussé sur GitHub, branche `main`.
3. Les **valeurs que personne ne peut inventer**, rassemblées : clé DeepSeek,
   identifiants SMTP, identifiants Campay (DEMO suffit au lancement), le nom de
   domaine public.

**Preuve :** vous pouvez ouvrir la console AWS dans la bonne région, et
`git log origin/main -1` affiche bien votre dernier commit.

---

## ÉTAPE 1 — Amorcer la fédération d'identité

Une seule fois dans la vie du projet. Ce modèle crée les deux rôles que GitHub
endossera, les deux dépôts d'images, et un budget d'alerte. **Il est le seul à
se déployer à la main**, puisqu'il crée précisément ce qui permet à GitHub
d'agir ensuite.

```bash
aws cloudformation deploy \
  --template-file infra/aws/bootstrap-github-oidc.yml \
  --stack-name precis-bootstrap \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides GitHubOrg=<votre-org> GitHubRepo=<votre-depot> \
                        AlertEmail=<votre-email>
```

Puis reportez les deux ARN de sortie dans les **variables** du dépôt GitHub
(*Settings → Secrets and variables → Actions → Variables*) :
`AWS_ROLE_DEPLOY`, `AWS_ROLE_CFN_EXECUTION`, `AWS_REGION`, `ECR_REGISTRY`.

**Aucune clé AWS n'est stockée dans GitHub** — c'est tout l'objet de l'étape.
Le détail du verrouillage (`sub` sur `refs/heads/main`, forme par identifiant
immuable) est expliqué dans `infra/aws/README.md`.

**Preuve :** `aws cloudformation describe-stacks --stack-name precis-bootstrap
--query 'Stacks[0].Outputs'` liste les ARN, et les trois variables sont visibles
dans GitHub.

---

## ÉTAPE 2 — Créer l'infrastructure

Workflow **Infrastructure**, lancé à la main (*Actions → Infrastructure → Run
workflow*).

1. D'abord avec `action = changeset` : rien n'est créé, vous **lisez ce qui le
   serait**.
2. Puis avec `action = appliquer`.

Cela crée le réseau, le groupe de sécurité (80 et 443 **seulement** — pas de
port 22, l'accès se fait par Session Manager), l'instance `t3.medium`, l'adresse
IP fixe, et le **disque de données séparé** marqué « Retain », monté sous
`/var/lib/docker/volumes`.

> **Pourquoi ce workflow reste manuel.** Une nouvelle image Ubuntu publiée par
> Canonical **remplace l'instance**. Tant que le disque de données est séparé et
> en « Retain », les comptes et les documents survivent — mais le service est
> interrompu le temps du remplacement. Cela se décide, cela ne s'improvise pas
> au milieu d'une mise en ligne. Le paramètre `figer_ami = oui` (défaut)
> conserve l'image déjà utilisée.

**Preuve :** l'état de la pile est `CREATE_COMPLETE`, et
`aws ssm start-session --target <instance-id>` ouvre un interpréteur de commande
sur la machine — sans clé SSH.

---

## ÉTAPE 3 — Poser les secrets

Depuis l'instance, par Session Manager — **et non depuis votre poste** : les
secrets générés ne traversent alors aucun disque ni aucun historique de
terminal.

```bash
aws ssm start-session --target <InstanceId>   # sortie de la pile de l'etape 2
sudo -i
curl -fsSL https://raw.githubusercontent.com/MbowouIbrah/Pr-cis-Translation/main/infra/aws/poser-secrets.sh   -o /tmp/poser-secrets.sh
bash /tmp/poser-secrets.sh
```

Le script génère ce que personne n'a besoin de connaître (`jwt_secret`,
`frontend_api_key`, `postgres_password`), pose les conventions, puis **nomme ce
qui manque encore** et s'arrête en échec tant que vous ne l'avez pas posé :
DeepSeek, SMTP, Campay, `frontend_url`, `allowed_origins`.

Il est **idempotent** : le relancer ne régénère jamais un secret existant.

> ⚠️ **`postgres_password` ne se change jamais après le premier démarrage.** Le
> volume `pgdata` a figé l'ancien, et le service ne joindrait plus sa propre
> base — avec un message qui ne dit pas que la cause est là.

> ⚠️ **`allowed_origins` et `frontend_url` portent le vrai domaine, en https.**
> Le garde de production refuse une valeur vide ou locale, et il a raison : une
> origine locale autorisée en production ouvrirait l'API complète à n'importe
> quelle page servie depuis un poste.

**Preuve :** le script se termine par « Tous les parametres sont poses ».

---

## ÉTAPE 4 — Déployer la version

Le déploiement est déclenché par **une seule chose** : le contenu de
`release/version.txt` poussé sur `main`.

```bash
echo 1.1.0 > release/version.txt
git add release/version.txt && git commit -m "release: 1.1.0"
git tag v1.1.0 && git push origin main --tags
```

Le workflow **Mise en ligne** enchaîne alors quatre verrous :

1. **Garde de version** — `release/version.txt`, la `version` de
   `package.json`, la section du `CHANGELOG.md` et le tag `v1.1.0` doivent
   désigner la même version, et le tag doit pointer sur le commit déployé.
2. **Garde de tests** — les suites du backend et la construction du frontend.
   Un échec ici n'envoie rien.
3. **Images** — construites puis poussées, étiquetées `<version>-<sha court>`.
   **Jamais `latest`** : on doit toujours pouvoir dire ce qui tourne.
4. **Déploiement** — par SSM sur l'instance, qui tire les images, applique les
   migrations et redémarre. Le workflow **attend** la fin réelle de la commande
   et interroge `/health` depuis l'extérieur.

> **Le piège que ce découpage supprime.** `VITE_API_KEY` est **cuite dans
> l'image du frontend** au moment du build ; elle doit valoir exactement le
> `FRONTEND_API_KEY` du service. Le workflow lit donc cette clé **dans SSM**,
> c'est-à-dire dans la source exacte où le script de déploiement lira
> `FRONTEND_API_KEY` quelques minutes plus tard. Une seule valeur, un seul
> endroit — la divergence devient mécaniquement impossible.

**Preuve :** le workflow est vert de bout en bout, et
`curl http://<ip>/health` renvoie `"status": "healthy"` avec
`"projet": "1.1.0"`.

---

## ÉTAPE 5 — Domaine et HTTPS

Chez Cloudflare, pour votre domaine :

| Réglage | Valeur | Pourquoi |
|---|---|---|
| Enregistrement `A` | l'**IP fixe** de la sortie de pile | une IP d'instance change au redémarrage, l'IP fixe non |
| Proxy | activé (nuage orange) | c'est lui qui apporte le HTTPS |
| SSL/TLS | **Full** | en « Flexible », le flux d'aperçu se met en tampon et l'aperçu reste figé |

Puis, si ce n'est pas déjà fait, mettez le vrai domaine dans `frontend_url` et
`allowed_origins` (SSM) et redémarrez la pile.

**Preuve :** `https://<votre-domaine>/health` répond, avec un cadenas valide.

---

## ÉTAPE 6 — Le vrai parcours client

Sur le domaine public, dans cet ordre, avec une **adresse e-mail réelle** :

1. Créer un compte → l'e-mail de vérification arrive → le lien fonctionne.
2. Déposer un PDF de quelques pages → l'aperçu se remplit **page par page**.
3. Télécharger le résultat → le fichier s'ouvre dans sa visionneuse d'origine.
4. Déposer un PPTX et un XLSX → même vérification.
5. Lancer un paiement en mode DEMO → le document devient payé.
6. Ouvrir `/admin` avec le compte administrateur → les documents et les tickets
   sont visibles.

**Preuve :** les six points passent. C'est cette liste, et non l'état des
workflows, qui dit que la mise en ligne est réussie.

---

## Après la mise en ligne

Dans cet ordre de priorité réelle :

**1. Purge automatique des fichiers traduits** — *le seul point qui peut vous
tomber dessus*. Rien ne supprime les fichiers aujourd'hui : seul l'utilisateur
peut effacer les siens. Le dossier grossit sans limite jusqu'à remplir le
disque, et un disque plein arrête le service. Surveiller avec
`df -h /var/lib/docker/volumes`. À écrire dans la semaine (délai de
conservation par plan).

**2. Sauvegarde du volume `pgdata`** — le disque de données survit au
remplacement de l'instance, mais **rien ne le sauvegarde**. Une suppression
accidentée de la pile avec son disque, et les comptes sont perdus. Un cliché
quotidien du volume est le filet manquant.

**3. Supabase Storage** — vous voulez un projet dédié, et c'est le bon choix à
terme : les fichiers quittent le disque du serveur, l'espace cesse d'être un
risque. **Mais ce n'est pas une case à cocher** : `config.py` *dérive* le chemin
de stockage de l'emplacement du code et chaque écriture passe par un chemin
disque. Il faut écrire une couche de stockage et des URL signées. Créez le
projet Supabase quand vous voulez, il attendra sans frais. Bonne nouvelle : la
**comptabilité d'espace** que vous visiez (espace utilisé par compte, plafonds
par plan) **existe déjà en base** et fonctionnera telle quelle.

**4. Paiements réels** — quand vous aurez les clés Campay de production :
changer les paramètres `campay_*` et `campay_env` dans SSM, puis relancer le
déploiement. **Aucune reconstruction d'image.** ⚠️ Prévoir ~3 % de frais Campay
dans les prix.

**5. Afficher les pages ignorées** — le nombre de pages non traduites (scans)
est calculé par le service mais pas montré à l'utilisateur. Petit travail
d'interface.

---

## Ce qui peut mal tourner, et la réponse

| Symptôme | Cause la plus probable |
|---|---|
| Refus de démarrer, liste de réglages | voulu : lire le message, il nomme la faute **et sa correction** |
| Workflow arrêté sur la garde de version | `version.txt`, `package.json`, `CHANGELOG.md` et le tag ne concordent pas |
| GitHub refusé par AWS (`AssumeRole`) | variables `AWS_ROLE_*` absentes, ou déclenchement depuis une autre branche que `main` |
| Déploiement arrêté : paramètre manquant | relancer `poser-secrets.sh`, il nomme ce qui manque |
| `password authentication failed` au démarrage | `postgres_password` changé après le premier démarrage — `pgdata` a figé l'ancien |
| Site injoignable après le DNS | propagation en cours ; vérifier l'**IP fixe** et le proxy orange |
| Aperçu PDF vide ou figé | Cloudflare met le flux SSE en tampon → **SSL/TLS = Full** |
| E-mail de vérification jamais reçu | `smtp_password`, ou `email_enabled` resté à `false` |
| Lien d'e-mail qui ne mène nulle part | `frontend_url` — le garde le refuse maintenant au démarrage |
| Service lent, puis arrêté | disque plein : `df -h /var/lib/docker/volumes` (voir « purge ») |

---

## Revenir en arrière

Le script de déploiement **revient seul** à l'étiquette précédente si la
nouvelle pile ne devient pas saine : il la retient avant de commencer. Un retour
réussi reste signalé comme un **échec** de déploiement — c'est voulu, sans quoi
un workflow vert annoncerait une version qui n'est pas en ligne.

Pour revenir à une version plus ancienne, délibérément : republier son numéro
par le même chemin (`release/version.txt`), de façon que la version en ligne
reste celle que le dépôt déclare.

Les volumes survivent : **ni les comptes, ni les documents ne sont perdus**. Une
**migration de base, en revanche, ne se dé-applique pas** — ni par le script, ni
par le retour en arrière. Consulter `CHANGELOG.md` pour savoir si la version
quittée en a introduit une ; le script affiche un avertissement encadré quand
c'est le cas.
