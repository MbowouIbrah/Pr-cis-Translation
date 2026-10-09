# Contribuer à Précis Translator

Ce document décrit **comment ajouter une fonctionnalité ou un correctif** sans
casser le code, les tests, les docs ni la traçabilité des versions. Il est court
et il fait foi.

Principe de base : **`main` est la version stable.** On n'y travaille jamais
directement. On ne « change réellement de version » qu'en **fusionnant sur `main`
et en posant un tag**. Chaque tag est un **point de restauration**.

---

## 1. Le cycle, en cinq temps

```
1. brancher          git checkout main && git checkout -b feat/<sujet>
2. coder + tester    le code ET son test, dans la même branche
3. documenter        docs + version + CHANGELOG (voir §3, la checklist)
4. vérifier          les portes du §4 doivent être vertes
5. livrer            merge --no-ff sur main + tag si release (voir §5)
```

### Nommage des branches
| Préfixe | Pour |
|---|---|
| `feat/<sujet>` | nouvelle fonctionnalité |
| `fix/<sujet>` | correctif |
| `refactor/<sujet>` | remaniement sans changement de comportement |
| `docs/<sujet>` | documentation seule |

---

## 2. Les commits — [Conventional Commits](https://www.conventionalcommits.org)

```
<type>(<portée>): <résumé à l'impératif>

<corps facultatif : le POURQUOI, pas le quoi>

Co-Authored-By: …
```

Types : `feat`, `fix`, `docs`, `refactor`, `test`, `chore`. La **portée** est le
domaine touché (`ui`, `pptx`, `auth`, `pdf`, `api`…). Un `feat!:` ou un footer
`BREAKING CHANGE:` signale une rupture (→ version **majeure**).

Exemples réels du dépôt : `feat(xlsx): squelette du moteur Excel`,
`fix(layout): cadre commun`, `refactor(i18n): tarifs passent aux dictionnaires`.

---

## 3. La checklist — quels fichiers mettre à jour

Selon ce que touche le changement, **avant de livrer** :

| Si le changement… | …mettre à jour |
|---|---|
| ajoute/modifie du code | le **test** correspondant (`backend/tests/test_*.py`, ou vérif front) |
| touche l'architecture ou un flux | la **doc canonique** concernée (voir la carte plus bas) |
| **change le contrat HTTP** (route, champ, sémantique) | `backend/version.py` (SemVer, voir en-tête du fichier) |
| **peut changer un pixel du rendu** d'un moteur | ⚠ le `ENGINE_VERSION`/`__version__` du moteur — **obligatoire** |
| ajoute du **texte d'interface** | `frontend/src/locales/{fr,en}/translation.json` (les deux) |
| est visible pour l'utilisateur | la section `[Unreleased]` de [`CHANGELOG.md`](CHANGELOG.md) |

> **Le piège du cache de rendu.** Le moteur PDF met ses pages en cache sous une
> clé qui inclut `ENGINE_VERSION`. Corriger le rendu **sans** incrémenter la
> version resert l'ancien rendu **en silence** : on croit avoir corrigé,
> l'utilisateur voit le contraire. Toute retouche qui peut bouger un pixel
> **impose** de monter le patch du moteur. Détail : `backend/engines/pdf/CONTEXTE.md`.

### Où vit la doc canonique
| Sujet | Fichier |
|---|---|
| Couches, règle de dépendance, chemin d'une traduction | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| API, config, comptes & auth, tests | [`backend/README.md`](backend/README.md) |
| Interface, flux d'aperçu, pièges UI, décor hero | [`frontend/README.md`](frontend/README.md) |
| Socle commun des moteurs | [`backend/engines/CONTEXTE.md`](backend/engines/CONTEXTE.md) |
| Un moteur en particulier | `backend/engines/<moteur>/CONTEXTE.md` |
| Étude d'une feature à venir (OCR) | [`docs/etude-ocr.md`](docs/etude-ocr.md) |

---

## 4. Les portes de vérification (avant tout merge)

Toutes doivent être **vertes**. Une seule au rouge = on ne livre pas.

```bash
# Interface : types + build de production
cd frontend && npm run build

# Interface : parité des dictionnaires (un oubli casse une langue, pas le build)
node scripts/check-i18n.mjs

# Backend : les 21 suites, hors ligne. Chacune DOIT sortir en exit=0.
cd ../backend
for t in tests/test_*.py; do venv/Scripts/python.exe "$t" || echo "FAIL $t"; done

# Invariant d'architecture : un moteur n'importe jamais l'application → []
venv/Scripts/python.exe -c "import engines, sys; print([m for m in sys.modules if m.startswith('app')])"
```

Deux règles de fond, rappelées ici car elles ont déjà coûté cher :

* **Aucune heuristique calée sur un document.** Un correctif de moteur se prouve
  sur un document **synthétique** (`backend/tests/test_engine_v2_generic.py`),
  jamais sur celui qui a révélé le défaut.
* **Rien de bloquant dans un `async def`.** Réseau, LibreOffice, rendu PDF partent
  en thread — un seul appel bloquant fige toute l'app, flux SSE compris.

---

## 5. Livrer et versionner ([SemVer](https://semver.org))

On ne tague que sur `main`, portes vertes.

```bash
# 1. figer le CHANGELOG : [Unreleased] -> [X.Y.Z] daté
# 2. fusionner la branche (--no-ff garde la trace de la fonctionnalité)
git checkout main
git merge --no-ff feat/<sujet>
# 3. tag annoté = point de restauration
git tag -a vX.Y.Z -m "<résumé>"
```

Choix du numéro `X.Y.Z` :

| Incrément | Quand |
|---|---|
| **patch** `x.y.Z` | correctif, aucun changement visible du contrat |
| **mineure** `x.Y.0` | ajout compatible (route, champ, option) |
| **majeure** `X.0.0` | rupture : le consommateur doit s'adapter |

Le tag `vX.Y.Z` est la **version projet** (ce qui est publié). Les numéros de
`backend/version.py`, des moteurs et de `package.json` sont des versions de
**composant** — internes, elles évoluent à leur rythme et n'ont pas à coïncider
avec le tag.

---

## 6. Revenir sur une version antérieure

Les tags sont les points de retour. Selon la situation :

```bash
# Annuler UN commit fautif sans réécrire l'histoire (recommandé après release)
git revert <sha>

# Repartir exactement d'une version publiée (branche jetable pour inspecter)
git checkout vX.Y.Z            # HEAD détachée, lecture seule
git checkout -b hotfix/<sujet> vX.Y.Z   # pour corriger à partir de là

# Réaligner main sur un tag (DESTRUCTIF — seulement si rien n'a été partagé)
git reset --hard vX.Y.Z
```

Règle d'or : **`revert` sur ce qui a déjà été livré**, `reset --hard`
uniquement sur une branche locale que personne d'autre n'a.
