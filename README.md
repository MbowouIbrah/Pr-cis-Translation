# Précis Translator

**Traduire un document sans lui faire perdre sa mise en forme.**

Le texte est relevé avec sa géométrie et ses styles, traduit, puis réinjecté
dans le document d'origine — jamais reconstruit. PDF, PPTX, DOCX, XLSX.

Projet **1.1.0** · backend 1.1.0 · frontend 1.0.0 · moteurs PDF 1.0.0, PPTX
1.0.0, DOCX 1.0.0, XLSX **0.1.0** (squelette)

## Démarrer

```bash
npm install                      # à la racine (concurrently)
npm run dev                      # backend + interface, avec les adresses
```

Séparément : [`backend/README.md`](backend/README.md) ·
[`frontend/README.md`](frontend/README.md)

Prérequis : Python 3.12+, Node 20+, PostgreSQL, et **LibreOffice** pour les
aperçus non-PDF.

## Où lire quoi

| | |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Les couches, la règle de dépendance, le chemin d'une traduction |
| [`backend/README.md`](backend/README.md) | API, configuration, tests |
| [`frontend/README.md`](frontend/README.md) | Interface, flux d'aperçu, variables `VITE_` |
| [`backend/engines/CONTEXTE.md`](backend/engines/CONTEXTE.md) | Ce que tous les moteurs partagent |
| [`backend/engines/pdf/CONTEXTE.md`](backend/engines/pdf/CONTEXTE.md) | Le moteur PDF — et sa clé de cache |
| [`backend/engines/pptx/CONTEXTE.md`](backend/engines/pptx/CONTEXTE.md) | Le moteur PPTX — parties partagées, objets OLE |
| [`backend/engines/docx/CONTEXTE.md`](backend/engines/docx/CONTEXTE.md) | Le moteur DOCX |
| [`backend/engines/xlsx/CONTEXTE.md`](backend/engines/xlsx/CONTEXTE.md) | Le moteur XLSX — **squelette**, couverture partielle |
| [`docs/feuille-de-route-production.md`](docs/feuille-de-route-production.md) | **Aller en production** : les 6 étapes dans l'ordre, qui fait quoi, et la preuve de chacune |
| [`docs/deploiement.md`](docs/deploiement.md) | **Mettre en ligne** : machine AWS, configuration, HTTPS, limites connues |
| [`docs/etude-ocr.md`](docs/etude-ocr.md) | Étude : traduire des PDF **scannés** (OCR) — feature à venir |
| `docs/api/index.html` | Les 32 opérations HTTP (`npm run docs:api`) |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | **Ajouter une fonctionnalité** : cycle, checklist, versions, retour arrière |
| [`CHANGELOG.md`](CHANGELOG.md) | L'historique des versions |

> L'historique détaillé des campagnes de correction (mesures, essais, impasses)
> n'encombre plus la racine : il reste consultable dans l'**historique git**. La
> carte à jour est dans `ARCHITECTURE.md` et les `CONTEXTE.md` par moteur.

## Les commandes

```bash
npm run dev              # backend + frontend
npm run dev:backend      # uvicorn seul
npm run dev:frontend     # Vite seul
npm run docs:api         # régénère docs/api/index.html
npm run stop             # arrête les deux
```

## Ce qui ne se négocie pas

**Un moteur n'importe jamais l'application.** `api → services → engines → rien`.
Le contrôle tient en une ligne, et il doit rester vrai :

```bash
cd backend && venv/Scripts/python.exe -c "import engines, sys; \
  print([m for m in sys.modules if m.startswith('app')])"     # => []
```

**Rien de bloquant dans un `async def`.** Un seul appel synchrone y fige
l'application entière, flux SSE compris.

**Aucune heuristique calée sur un document.** Un correctif se prouve sur un
document **synthétique**, jamais sur celui qui a révélé le défaut — sinon il
tiendra jusqu'au document suivant.

**Le cache de rendu est versionné.** Tout correctif qui peut changer un pixel
impose d'incrémenter la version du moteur PDF. L'oublier resert un rendu périmé
**en silence** : on croit avoir corrigé, l'utilisateur voit le contraire.

## Vérifier

```bash
cd backend
for t in tests/test_*.py; do venv/Scripts/python.exe "$t"; done
```

Vingt et une suites, **418 contrôles**, hors ligne. Une suite doit sortir en `exit=0` :
un score vert avec un code de retour non nul cache toujours quelque chose.
