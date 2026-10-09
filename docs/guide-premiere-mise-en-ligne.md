Écrit pour : vous, à suivre écran allumé, du compte AWS inexistant au site en ligne.

# Première mise en ligne — guide pas à pas

Ce guide ne suppose **aucun compte AWS** et décrit chaque clic. Il se suit dans
l'ordre, sans sauter d'étape, et chaque étape finit par une **preuve** : si la
preuve n'apparaît pas, ne passez pas à la suivante — c'est là que se trouve
votre problème, pas trois étapes plus loin.

| | |
|---|---|
| **Durée** | 3 h environ, dont ~1 h d'attente (vérification AWS, propagation DNS) |
| **Coût** | **~4 $/mois** : l'IPv4 publique est facturée (~3,6 $) même sur le free tier. `t3.micro` et 30 Go d'EBS sont couverts tant que durent le free tier ou les crédits de départ — voir l'encadré ci-dessous |
| **À avoir sous la main** | une carte bancaire, un téléphone, votre domaine et ses accès registrar, la clé DeepSeek, les identifiants SMTP et Campay |

**Deux documents voisins, à ne pas confondre :**
- [`feuille-de-route-production.md`](feuille-de-route-production.md) — la même séquence en version courte, pour les fois suivantes
- [`deploiement.md`](deploiement.md) — la mise en ligne **à la main**, à garder pour dépanner

---

> ⚠️ **« Gratuit » ne veut pas dire 0 $.** Deux régimes existent et ils
> n'expirent pas pareil :
> - comptes **ouverts avant juillet 2025** : free tier 12 mois (`t3.micro`,
>   30 Go d'EBS) ;
> - comptes **ouverts depuis** : un montant en **crédits**, valable environ
>   6 mois.
>
> Dans les deux cas, l'**adresse IPv4 publique est facturée** (~3,6 $/mois
> depuis 2024, y compris attachée à une instance). Le budget de l'étape 5 est
> réglé sur **20 $/mois** : assez au-dessus de ce plancher pour ne pas sonner
> pour rien, assez bas pour voir une dérive.
>
> Vérifiez votre régime dans **Billing and Cost Management → Free Tier /
> Crédits** : le montant et la date d'expiration y sont écrits.
>
> Le budget **n'arrête rien** : il envoie deux e-mails. Pour stopper les frais,
> il faut supprimer la pile applicative à la main — puis le disque de données,
> qui est en `Retain` et lui survit.

## Avant de commencer : où tourne quoi

Un quiproquo fréquent, qu'il vaut mieux lever tout de suite.

**Cloudflare, ce sont deux produits différents :**

| | Cloudflare **Workers** | Cloudflare **DNS + proxy** |
|---|---|---|
| Rôle | *Exécuter* l'application | Mettre un nom de domaine et du HTTPS **devant** une machine |
| Dans ce projet | ❌ **écarté** | ✅ **utilisé, étape 7** |
| Pourquoi | Pas de LibreOffice, pas de file en mémoire, durée d'exécution limitée | Ce n'est qu'un frontal : il n'exécute aucun code à vous |

La décision « AWS et non Cloudflare Workers » portait sur **où tourne
l'application**. Elle n'a jamais exclu Cloudflare comme frontal : les deux
cohabitent, et c'est ce que fait ce guide. L'application tourne sur une machine
AWS, Cloudflare met le nom et le certificat devant.

**Ce qui tournera, à la fin :** une machine AWS, trois conteneurs (`db`,
`backend`, `web`), deux volumes de données sur un disque séparé, **un seul port
ouvert** (le 80, et Cloudflare s'occupe du HTTPS).

---

# ÉTAPE 1 — Créer le compte AWS

⏱ 20 min, dont une attente de vérification qui peut aller jusqu'à 24 h (en
général quelques minutes).

1. Aller sur **https://portal.aws.amazon.com/billing/signup**
2. Adresse e-mail, mot de passe, nom du compte (`precis-translator` fait très
   bien l'affaire)
3. Type de compte : **Personnel** (ou Professionnel si vous facturez au nom
   d'une société) — adresse postale, téléphone
4. **Carte bancaire.** AWS prélève ~1 USD de vérification, remboursé. Sans carte
   valide, pas de compte.
5. Vérification par téléphone (code SMS ou appel)
6. Formule d'assistance : **Basic — gratuit**. N'en prenez pas d'autre, elles
   sont à 29 $/mois minimum et ne vous serviront pas ici.

> ⚠️ **Attendez l'e-mail « Your AWS account is ready ».** Avant lui, la console
> s'ouvre mais les créations de ressources échouent avec des erreurs qui
> n'indiquent pas la cause. Si vous voyez des refus inexpliqués à l'étape 3,
> c'est presque toujours ça.

### Activer le MFA sur le compte racine — ne sautez pas ceci

Le compte racine peut tout faire, y compris supprimer la facturation et les
données. Un compte racine sans second facteur, c'est un mot de passe entre vous
et la perte totale.

1. En haut à droite, votre nom → **Security credentials**
2. Section **Multi-factor authentication (MFA)** → **Assign MFA device**
3. Nom du périphérique, puis **Authenticator app**
4. Scanner le QR code avec Google Authenticator, Authy ou 1Password
5. Saisir **deux** codes consécutifs

**Preuve de l'étape 1 :** vous recevez l'e-mail « Your AWS account is ready »,
et la page Security credentials affiche un périphérique MFA actif.

---

# ÉTAPE 2 — Un utilisateur d'administration (et non le compte racine)

⏱ 15 min. À partir d'ici, **on n'utilise plus le compte racine** — on le réserve
à la facturation et aux urgences.

### 2.1 Créer l'utilisateur

1. Barre de recherche en haut → taper `IAM` → cliquer le service **IAM**
2. Menu de gauche → **Users** → bouton **Create user**
3. Nom : **votre prénom**, par exemple `ibrah` — et non le nom du projet
   (voir l'encadré sous cette liste)
4. ✅ Cocher **Provide user access to the AWS Management Console**
5. Choisir **I want to create an IAM user**
6. Mot de passe : **Custom password**, et décocher « Users must create a new
   password at next sign-in »
7. **Next**
8. Permissions : **Attach policies directly** → rechercher et cocher
   **AdministratorAccess**
9. **Next** → **Create user**

> ℹ️ **Pourquoi pas `admin-precis`.** Un utilisateur IAM représente **une
> personne**, pas un projet. Si ce compte AWS porte plusieurs projets du groupe,
> un nom de projet devient faux au deuxième. Ce qui porte le nom du projet, ce
> sont les ressources, et elles sont déjà préfixées : pile
> `precis-translator-prod`, rôles `precis-deploy` / `precis-cfn-execution`,
> dépôts ECR `precis-backend` / `precis-web`, paramètres
> `/precis-translator/prod/*`, budget `precis-translator-mensuel`. Un projet
> suivant vivra dans le même compte avec ses propres préfixes, sans collision.

### 2.2 Noter l'adresse de connexion

Sur la page de confirmation, AWS affiche une URL du type
`https://123456789012.signin.aws.amazon.com/console`.

**Notez-la, ainsi que les 12 chiffres** : ce numéro est votre *Account ID*, il
resservira à l'étape 5.

### 2.3 Activer le MFA sur cet utilisateur

Cet utilisateur est administrateur complet : son mot de passe seul vaut le
compte entier.

1. Cliquer sur l'utilisateur → onglet **Security credentials**
2. **Multi-factor authentication (MFA)** → **Assign MFA device**
3. **Authenticator app**, scanner le QR code, saisir **deux** codes consécutifs

> **Aucune clé d'accès, aucune CLI.** Tout ce guide se fait dans la console AWS
> et dans GitHub, comme le modèle atelier2026 dont cette infrastructure est
> tirée. Les workflows, eux, n'utilisent aucune clé : ils passent par OIDC
> (étape 5). Ne créez donc **pas** de clé d'accès : c'est un secret
> administrateur de moins à garder.

**Preuve de l'étape 2 :** vous vous déconnectez, vous reconnectez via l'URL
notée avec cet utilisateur, et vous voyez la console.

---

# ÉTAPE 3 — Choisir la région, une fois pour toutes

⏱ 2 min.

En haut à droite de la console, à côté de votre nom, se trouve le sélecteur de
région. Choisissez **Europe (Paris) eu-west-3**.

Ce guide emploie `eu-west-3` partout. Les modèles CloudFormation, eux,
n'imposent aucune région : c'est ce sélecteur qui décide.

> ⚠️ **Les ressources sont rangées par région.** Une pile créée à Paris est
> invisible depuis le sélecteur positionné sur l'Irlande — et vous la chercherez
> en croyant qu'elle n'existe pas. Si une ressource « disparaît » plus tard,
> vérifiez d'abord la région affichée en haut à droite.

Pourquoi Paris : c'est la région la plus proche du Cameroun parmi celles au
tarif européen standard, et vos données restent dans l'Union européenne.

**Preuve de l'étape 3 :** le sélecteur affiche **Paris** / `eu-west-3`.

---

# ÉTAPE 4 — Repérer le fichier à charger

⏱ 2 min. Rien à installer.

À l'étape 5, la console AWS vous demandera de **charger un fichier**. Il est
dans votre copie du projet :

```
infrawsootstrap-github-oidc.yml
```

Repérez-le dans l'Explorateur de fichiers de Windows (dossier du projet).

**Preuve de l'étape 4 :** vous savez ouvrir ce dossier et voyez le fichier.

---

# ÉTAPE 5 — Amorcer la fédération d'identité

⏱ 10 min. **Une seule fois dans la vie du projet.**

C'est l'étape qui fait que GitHub pourra agir sur AWS **sans qu'aucune clé AWS
ne soit stockée dans GitHub**. À la place, GitHub présente un jeton signé, et
AWS n'accepte que les jetons venant de votre dépôt, sur la branche `main`.

C'est aussi le seul déploiement qui se fait depuis votre poste : il crée
précisément ce qui permettra aux workflows de faire le reste.

### 5.1 Déployer, dans la console

Région **Europe (Paris) eu-west-3** affichée en haut à droite.

1. Barre de recherche → `CloudFormation` → **Piles** → **Créer une pile** →
   **Avec de nouvelles ressources (standard)**
2. **Choisir un modèle existant** → **Charger un fichier de modèle** →
   **Choisir un fichier** → `infrawsootstrap-github-oidc.yml` → **Suivant**
3. Nom de la pile : `precis-bootstrap`
4. Paramètres, à renseigner :

| Paramètre | Valeur |
|---|---|
| `GitHubOrg` | `MbowouIbrah` |
| `GitHubRepo` | `Pr-cis-Translation` |
| `AlertEmail` | votre adresse (AWS envoie une confirmation **à valider**, sinon aucune alerte) |
| `BudgetMonthlyUsd` | `20` (défaut) |

   Laissez les autres tels quels. **Suivant**, **Suivant**.
5. En bas de la dernière page, cochez **Je reconnais qu'AWS CloudFormation
   peut créer des ressources IAM avec des noms personnalisés**
   (`CAPABILITY_NAMED_IAM`), puis **Envoyer**.

Comptez 2 à 3 minutes, jusqu'à l'état `CREATE_COMPLETE` (bouton d'actualisation
de l'onglet **Événements**). Le modèle crée des rôles portant un nom choisi, et
AWS exige que vous le reconnaissiez.

> Si la pile échoue sur le fournisseur OIDC (« already exists »), il existe
> déjà dans ce compte : c'est le cas si un autre projet du groupe l'a créé.
> Reprenez en passant son ARN dans le paramètre `ExistingOidcProviderArn`.

Ce que cela crée :

| Ressource | À quoi elle sert |
|---|---|
| Fournisseur OIDC | apprend à AWS à reconnaître les jetons GitHub |
| Rôle `precis-deploy` | ce que GitHub endosse ; droits étroits |
| Rôle `precis-cfn-execution` | le rôle large, que **seul CloudFormation** peut endosser |
| 2 dépôts ECR | où atterrissent les images construites |
| Budget d'alerte | un e-mail à 80 % (constaté) et à 100 % (projeté) de 20 $/mois ; n'arrête rien |

> **Pourquoi deux rôles.** GitHub ne reçoit jamais les droits larges. Il reçoit
> le droit de *demander à CloudFormation* d'agir, et CloudFormation détient les
> droits. Un jeton GitHub volé ne peut donc pas créer n'importe quoi : il ne
> peut que déclencher la pile décrite dans le dépôt.

### 5.2 Relever les quatre valeurs

Ouvrir la pile `precis-bootstrap` → onglet **Sorties** (Outputs). Le tableau
liste les quatre valeurs de l'étape 6 : région, rôle de déploiement, rôle
d'exécution CloudFormation, registre ECR.

**Preuve de l'étape 5 :** l'onglet Sorties affiche les ARN des deux rôles et
l'adresse du registre ECR. Gardez cet onglet ouvert, l'étape 6 y puise.

---

# ÉTAPE 6 — Déclarer les quatre variables dans GitHub

⏱ 5 min.

1. Ouvrir le dépôt sur github.com
2. **Settings** (onglet du dépôt, pas celui de votre profil)
3. Menu de gauche → **Secrets and variables** → **Actions**
4. Onglet **Variables** — ⚠️ **pas** l'onglet « Secrets »
5. **New repository variable**, quatre fois :

Les quatre valeurs sont **affichées telles quelles** dans l'onglet Sorties de
l'étape 5.2 — rien à composer ni à deviner :

| Name (dans GitHub) | Value (Sorties, étape 5.2) |
|---|---|
| `AWS_REGION` | `RegionAUtiliser` |
| `AWS_ROLE_DEPLOY` | `RoleDeployArn` |
| `AWS_ROLE_CFN_EXECUTION` | `RoleExecutionCfnArn` |
| `ECR_REGISTRY` | `RegistreEcr` |

Copiez la colonne « Valeur » du tableau, ligne par ligne.

> **Variables et non Secrets, et ce n'est pas une négligence.** Aucune de ces
> quatre valeurs n'est un secret : un ARN de rôle ne donne aucun accès sans un
> jeton OIDC valide émis par *votre* dépôt sur `main`. Les mettre en Secrets
> les masquerait dans les journaux, ce qui rend le dépannage pénible pour un
> gain nul.

**Preuve de l'étape 6 :** l'onglet Variables liste les quatre noms.

---

# ÉTAPE 7 — Créer l'infrastructure

⏱ 15 min. Ici, vous ne touchez plus à AWS directement : c'est un workflow.

### 7.1 D'abord regarder, sans rien créer

1. Dépôt GitHub → onglet **Actions**
2. Menu de gauche → **Infrastructure**
3. Bouton **Run workflow** (à droite)
4. Laisser `action` sur **changeset**, `figer_ami` sur **oui**
5. **Run workflow**

Le workflow calcule ce qu'il *créerait* et s'arrête. Ouvrez l'exécution et lisez
la liste. Vous devez y voir un VPC, un groupe de sécurité, une instance, une
adresse IP, deux disques.

> ⚠️ **Dimensionnement free tier, et sa limite mesurée.** Les modèles sont
> réglés sur `t3.micro` (1 Go de RAM) + **4 Go de swap**, et 30 Go d'EBS :
> couverts par le free tier ou les crédits. Mais `docs/deploiement.md` a mesuré que « moins de 4 Go ne
> suffit pas » — LibreOffice et Postgres partagent la machine. **Attendez-vous
> à ce qu'une conversion PPTX ou XLSX lourde soit lente, voire fasse tuer
> Postgres par le noyau.** Si cela arrive, passez en `t3.small` ou
> `t3.medium` : c'est l'entrée `type_instance` du workflow Infrastructure,
> sans rien reconstruire. Surveillez `free -m` et
> `docker logs precis-db-1` au premier gros fichier.

> **C'est le vrai premier test.** Tout a été validé syntaxiquement, mais rien
> n'avait tourné contre AWS avant cet instant. Un refus ici est normal : quota
> de compte, nom déjà pris, droit trop serré. Le message nomme la cause.

### 7.2 Puis appliquer

Même workflow, `action` = **appliquer**.

Comptez 10 minutes. La machine s'installe seule : Docker, le disque de données
formaté puis monté, 2 Go d'échange, l'agent SSM.

> ⚠️ **Le disque de données est marqué « Retain ».** Il survit à la suppression
> de la pile et au remplacement de l'instance. C'est voulu : vos comptes et vos
> documents y vivent. Conséquence à connaître : si vous détruisez la pile, le
> disque **reste** et continue d'être facturé jusqu'à ce que vous le
> supprimiez à la main.

### 7.3 Relever l'IP et ouvrir une session sur la machine

1. CloudFormation → pile `precis-translator-prod` → onglet **Sorties** :
   `AdresseIpPublique` (l'IP fixe) et `InstanceId`
2. Console **EC2** → **Instances** → cocher l'instance → **Se connecter**
   (Connect) → onglet **Gestionnaire de session** (Session Manager) →
   **Se connecter**

**Preuve de l'étape 7 :** la pile est `CREATE_COMPLETE`, et la session s'ouvre
**sur la machine, sans clé SSH ni port 22 ouvert**. Si le bouton est grisé ou
répond `TargetNotConnected`, attendez 2 minutes : l'agent SSM n'a pas fini de
démarrer.

---

# ÉTAPE 8 — Poser les secrets

⏱ 20 min. **Depuis la machine**, et non depuis votre poste.

Ouvrez une session comme à l'étape 7.3 (EC2 → **Se connecter** → Gestionnaire de
session). Puis, dans la session ouverte :

```bash
sudo -i
curl -fsSL https://raw.githubusercontent.com/MbowouIbrah/Pr-cis-Translation/main/infra/aws/poser-secrets.sh \
  -o /tmp/poser-secrets.sh
bash /tmp/poser-secrets.sh
```

> **Pourquoi depuis la machine.** Le script génère `jwt_secret`,
> `frontend_api_key` et `postgres_password`. Nés dans ce processus, ils vont
> droit dans le magasin de paramètres chiffré : ils ne traversent **aucun
> disque et aucun historique de terminal**, ni le vôtre ni celui de GitHub.

Le script pose ce qu'il sait générer, puis **nomme ce qui manque** et s'arrête
en échec. Posez alors chaque valeur manquante, en remplaçant `<...>` :

```bash
P=/precis-translator/prod

# Secrets
aws ssm put-parameter --type SecureString --name $P/deepseek_api_key   --value '<votre cle DeepSeek>'
aws ssm put-parameter --type SecureString --name $P/smtp_password      --value '<mot de passe SMTP>'
aws ssm put-parameter --type SecureString --name $P/campay_username    --value '<Campay DEMO>'
aws ssm put-parameter --type SecureString --name $P/campay_password    --value '<Campay DEMO>'
aws ssm put-parameter --type SecureString --name $P/campay_webhook_key --value '<Campay DEMO>'

# Valeurs en clair — METTEZ VOTRE VRAI DOMAINE
aws ssm put-parameter --type String --name $P/frontend_url      --value 'https://precis-translator.com'
aws ssm put-parameter --type String --name $P/allowed_origins   --value 'https://precis-translator.com'
aws ssm put-parameter --type String --name $P/admin_alert_email --value 'vous@exemple.com'
aws ssm put-parameter --type String --name $P/smtp_host         --value 'smtp.exemple.com'
aws ssm put-parameter --type String --name $P/smtp_port         --value '587'
aws ssm put-parameter --type String --name $P/smtp_user         --value 'vous@exemple.com'
aws ssm put-parameter --type String --name $P/google_client_id  --value ''
```

Puis relancez `bash /tmp/poser-secrets.sh` : il est **idempotent**, il ne
régénère jamais un secret existant.

**Trois pièges, chacun coûte une heure de recherche :**

> ⚠️ **`frontend_url` et `allowed_origins` portent le vrai domaine, en
> `https://`, sans barre oblique finale.** Le garde de production refuse une
> valeur vide ou locale, et il a raison : une origine locale autorisée en
> production ouvrirait l'API entière à n'importe quelle page servie depuis un
> poste.

> ⚠️ **Ne changez jamais `postgres_password` après le premier démarrage.** Le
> volume de la base a figé l'ancien au tout premier lancement. Le service ne
> joindrait plus sa propre base, avec un message qui ne dit pas que la cause
> est là.

> ⚠️ **`campay_env` reste sur `DEMO`.** Aucun paiement réel au lancement.

**Preuve de l'étape 8 :** le script se termine par « Tous les parametres sont
poses. Le deploiement peut partir. »

---

# ÉTAPE 9 — Déployer la version 1.1.0

⏱ 25 min, dont ~20 min de construction des images (LibreOffice est volumineux ;
les fois suivantes, le cache ramène cela à ~5 min).

Le déploiement se déclenche par **une seule chose** : le contenu de
`release/version.txt` poussé sur `main`.

```powershell
git add -A
git commit -m "release: infrastructure AWS et version 1.1.0"
git tag v1.1.0
git push origin main --tags
```

Puis **Actions → Mise en ligne** pour suivre les quatre verrous :

| | Verrou | Ce qu'il empêche |
|---|---|---|
| 1 | **Version** — `version.txt`, `package.json`, `CHANGELOG.md` et le tag concordent, et le tag pointe sur le commit déployé | mettre en ligne une version que le dépôt ne décrit pas |
| 2 | **Tests** — 21 suites + construction du frontend | livrer du code cassé ; un échec n'envoie rien |
| 3 | **Images** — étiquetées `1.1.0-<sha>`, **jamais `latest`** | ne plus savoir ce qui tourne, et ne plus pouvoir revenir |
| 4 | **Déploiement** — par SSM, attente réelle, puis `/health` interrogé de l'extérieur | un workflow vert annonçant un service mort |

> **Le piège que ce découpage supprime.** La clé d'API du frontend est **cuite
> dans l'image** au moment de la construction, et doit valoir exactement celle
> du service. Le workflow la lit donc **dans le magasin de paramètres** —
> c'est-à-dire la source exacte où la machine lira la sienne quelques minutes
> plus tard. Une seule valeur, un seul endroit : la divergence devient
> mécaniquement impossible.

**Preuve de l'étape 9 :** le workflow est vert de bout en bout, et depuis votre
poste :

```powershell
curl http://<IP-fixe>/health
```

répond `"status": "healthy"` avec `"projet": "1.1.0"`.

---

# ÉTAPE 10 — Le domaine et le HTTPS

⏱ 30 min d'actions, puis 15 min à 24 h de propagation.

Votre domaine est acheté chez un registrar. On le laisse là, on change seulement
**qui répond aux questions DNS**.

### 10.1 Ajouter le site dans Cloudflare

1. Créer un compte sur **https://dash.cloudflare.com/sign-up**
2. **Add a site** → saisir votre domaine → formule **Free**
3. Cloudflare analyse vos enregistrements existants et les recopie. **Vérifiez
   la liste** : s'il manque un enregistrement MX, votre messagerie
   s'arrêterait.

### 10.2 Changer les serveurs de noms chez le registrar

Cloudflare affiche deux adresses du type `ana.ns.cloudflare.com`.

Dans l'interface de votre registrar, cherchez **Serveurs de noms / Nameservers /
DNS** et **remplacez** les serveurs actuels par les deux de Cloudflare.

> ⚠️ Remplacer, et non ajouter. Des serveurs de noms mélangés donnent des
> réponses contradictoires selon le visiteur : le site marche chez vous et pas
> chez votre client.

### 10.3 Pointer sur la machine

De retour dans Cloudflare → **DNS** → **Add record** :

| Champ | Valeur |
|---|---|
| Type | `A` |
| Name | `@` |
| IPv4 address | l'**IP fixe** de l'étape 7.3 |
| Proxy status | **Proxied** — le nuage **orange** |

Ajoutez-en un second, identique, avec Name = `www`.

> **L'IP fixe, et pas celle de l'instance.** Une IP d'instance change à chaque
> redémarrage ; l'IP fixe de la pile, non. C'est pour cela qu'elle existe.

> **Le nuage doit être orange.** Gris, Cloudflare ne fait que du DNS : pas de
> HTTPS, et le port 80 de la machine se retrouve exposé en direct.

### 10.4 Le réglage qui fait figer l'aperçu

**SSL/TLS** → **Overview** → mode de chiffrement : **Full**.

> ⚠️ En mode *Flexible*, Cloudflare met le flux d'événements en tampon et
> **l'aperçu de traduction reste figé**, page blanche, sans aucune erreur.
> C'est le symptôme le plus déroutant de toute la mise en ligne. Si l'aperçu ne
> se remplit pas, revenez ici avant de chercher ailleurs.

**Preuve de l'étape 10 :** `https://votre-domaine/health` répond, avec un
cadenas valide dans le navigateur.

---

# ÉTAPE 11 — Vérifier le vrai parcours client

⏱ 30 min. **C'est cette liste, et non l'état des workflows, qui dit que la mise
en ligne est réussie.**

Sur le domaine public, avec une **adresse e-mail réelle** :

- [ ] Créer un compte → l'e-mail de vérification arrive → le lien fonctionne
- [ ] Se connecter, puis se déconnecter et se reconnecter
- [ ] Déposer un PDF de quelques pages → l'aperçu se remplit **page par page**
- [ ] Télécharger le PDF traduit → il s'ouvre dans une visionneuse
- [ ] Déposer un PPTX → télécharger → **PowerPoint l'ouvre sans se plaindre**
- [ ] Déposer un XLSX → télécharger → Excel l'ouvre
- [ ] Lancer un paiement en mode DEMO → le document devient payé
- [ ] Ouvrir `/admin` avec le compte administrateur → documents et tickets
      visibles
- [ ] Envoyer un ticket d'assistance depuis un compte client → il apparaît côté
      admin

---

# Ce qui peut mal tourner

| Symptôme | Cause la plus probable |
|---|---|
| Créations AWS refusées sans raison claire | compte pas encore vérifié (étape 1) |
| Pile `ROLLBACK_COMPLETE` à l'étape 5 | onglet Événements : la première ligne en échec nomme la cause (souvent le fournisseur OIDC déjà existant) |
| Ressource « disparue » | mauvaise région dans le sélecteur (étape 3) |
| GitHub refusé par AWS (`AssumeRole`) | variables `AWS_ROLE_*` absentes, ou exécution depuis une autre branche que `main` |
| Workflow arrêté sur la garde de version | `version.txt`, `package.json`, `CHANGELOG.md` et le tag ne concordent pas |
| `TargetNotConnected` | l'agent SSM n'a pas fini de démarrer ; attendre 2 min |
| Déploiement arrêté, un paramètre nommé | relancer `poser-secrets.sh`, il dit ce qui manque |
| Refus de démarrer, liste de réglages affichée | **voulu** : le message nomme la faute *et* sa correction |
| `password authentication failed` | `postgres_password` changé après le premier démarrage |
| Site injoignable après le DNS | propagation en cours ; vérifier l'IP fixe et le nuage orange |
| **Aperçu vide ou figé** | **SSL/TLS en Flexible au lieu de Full** |
| E-mail de vérification jamais reçu | `smtp_password`, ou `email_enabled` resté à `false` |
| Lien d'e-mail qui ne mène nulle part | `frontend_url` |
| Service lent, puis arrêté | disque plein : `df -h /var/lib/docker/volumes` |

---

# Revenir en arrière

Le script de déploiement **revient seul** à l'étiquette précédente si la
nouvelle pile ne devient pas saine. Un retour réussi reste signalé comme un
**échec** — c'est voulu : sans quoi un workflow vert annoncerait une version qui
n'est pas en ligne.

Les volumes survivent : **ni les comptes, ni les documents ne sont perdus**.

> ⚠️ **Une migration de base ne se dé-applique pas** — ni par le script, ni par
> le retour en arrière. Le script affiche un avertissement encadré quand la
> version quittée en a appliqué une. Consultez `CHANGELOG.md`.

---

# Les trois jours qui suivent

**1. La purge des fichiers traduits — le seul point à échéance.** Rien ne
supprime les fichiers aujourd'hui : seul l'utilisateur peut effacer les siens.
Le dossier grossit sans limite jusqu'à remplir le disque, et **un disque plein
arrête le service**. Surveillez avec `df -h /var/lib/docker/volumes`. À écrire
dans la semaine, pas dans le mois.

**2. La sauvegarde du volume de base.** Le disque survit au remplacement de
l'instance, mais **rien ne le sauvegarde**. Une suppression de pile avec son
disque, et les comptes sont perdus. Un cliché quotidien est le filet manquant.

**3. Surveiller la facture.** Le budget d'alerte de l'étape 5 vous écrit à 80 %
de 40 $. Si un e-mail arrive le 5 du mois, quelque chose tourne qui ne devrait
pas.

Le reste — Supabase, paiements réels, pages ignorées affichées — est dans
[`feuille-de-route-production.md`](feuille-de-route-production.md), sans
urgence.
