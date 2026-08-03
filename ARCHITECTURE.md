# Architecture

Précis Translator traduit des documents **en préservant leur mise en forme**.
C'est la promesse du produit, et c'est elle qui explique la forme du code : le
texte est relevé avec sa géométrie et ses styles, traduit, puis réinjecté dans
le document d'origine — jamais reconstruit.

---

## La règle qui tient tout

Les dépendances vont dans **un seul sens** :

```
app/api  ──▶  app/services  ──▶  engines  ──▶  (rien du projet)
   │
   └──────▶  app/core       ──▶  (rien du projet)
```

Aucune flèche ne remonte. Concrètement :

- un **moteur** n'importe jamais l'application, ne lit pas la base, ne connaît
  ni les comptes ni les forfaits ;
- un **service** ignore FastAPI : il reçoit des chemins, il rend des octets ;
- une **route** ne fait aucun travail long elle-même.

Ce n'est pas une préférence de style. Le moteur PPTX faisait
`from app import _preview_lock`, en import tardif, avec un commentaire qui
s'excusait du cycle. Un moteur qui importe son application ne peut plus en
sortir : ni pour un banc d'essai, ni pour un autre projet, ni pour être remplacé.

**Vérification** — le contrôle tient en une ligne, et il doit rester vrai :

```bash
cd backend && venv/Scripts/python.exe -c "import engines, sys; \
  print([m for m in sys.modules if m.startswith('app')])"
# => []
```

---

## Les couches

| Dossier | Rôle | Ne doit jamais |
|---|---|---|
| `backend/main.py` | Point d'entrée ASGI. Rien d'autre que `create_app()`. | contenir de la logique |
| `backend/app/` | La fabrique. `create_app()` assemble middleware, limiteur et routeurs. | s'exécuter à l'import |
| `backend/app/config.py` | **Toute** lecture d'environnement, en un seul endroit. | être contourné par un `os.getenv` ailleurs |
| `backend/app/api/` | Routes HTTP : valider, autoriser, déléguer, répondre. | bloquer la boucle asyncio |
| `backend/app/core/` | Socle technique : base, sécurité, tarifs, e-mail, passerelle de paiement. | connaître les moteurs |
| `backend/app/models/` | Modèles SQLAlchemy et forfaits. | contenir des règles de traduction |
| `backend/app/services/` | Le travail : jobs, exécuteurs, cache de rendu, aperçu d'essai. | importer FastAPI |
| `backend/engines/` | Les moteurs, et ce qu'ils partagent (LibreOffice, balises, client IA). | importer `app` |
| `backend/tests/` | Treize suites, **313 contrôles**, exécutables séparément. | dépendre du réseau |

---

## Ajouter un moteur

Le registre (`backend/engines/__init__.py`) est le **seul** endroit du projet
qui nomme un format. Ni les routes ni les exécuteurs n'importent un moteur
directement — ils demandent « qui traite le `.xlsx` ? ».

1. Créer `backend/engines/xlsx/engine.py`, avec une classe qui hérite de
   `TranslationEngine` (`engines/base.py`) et honore ses deux méthodes :
   `extract_text` et `inject_translation`.
2. L'inscrire dans `_REGISTRE`.
3. Lui donner un `version.py` et un `CONTEXTE.md`.
4. Ajouter l'extension à `ALLOWED_EXTENSIONS` (`app/config.py`) **et** à
   `ACCEPTED` côté interface — sans quoi le moteur existe mais le fichier est
   refusé à la porte.
5. C'est tout. Aucune route, aucun exécuteur à modifier.

**Le numéro de version dit l'état réel.** Un moteur qui ne rend pas encore un
document fidèle part en `0.x` : le déclarer `1.0.0` ferait croire le contrat
stable à quiconque lit `/health`.

C'est le cas du moteur XLSX, en `0.2.0`. Sa couverture du **texte** est
pourtant complète — les dix endroits où Excel range du texte sont traités — et
c'est précisément ce que ce numéro sert à ne pas laisser croire : il reste un
défaut de **rendu**, l'expansion (une traduction plus longue déborde de sa
colonne ou s'affiche en `#####`). Le passage en `1.0.0` se mérite sur ce que
l'utilisateur voit, pas sur une table de couverture remplie.

Le **moteur PDF fait exception** : il ne figure pas au registre. Il n'a pas le
contrat en deux temps — sa traduction est progressive par construction
(`engines/pdf/stream.py`), page extraite puis traduite puis rendue avant de
passer à la suivante. L'exécuteur l'appelle nommément. L'asymétrie est assumée ;
la maquiller derrière une fausse conformité coûterait plus qu'elle ne rapporte.

Chaque moteur porte son propre contexte :
[commun](backend/engines/CONTEXTE.md) ·
[PDF](backend/engines/pdf/CONTEXTE.md) ·
[PPTX](backend/engines/pptx/CONTEXTE.md) ·
[DOCX](backend/engines/docx/CONTEXTE.md) ·
[XLSX](backend/engines/xlsx/CONTEXTE.md).

### Une instance par opération — jamais un singleton

Un moteur porte un `self.temp_dir` : c'est un objet **à état**. Une instance
partagée entre deux traductions simultanées les fait écrire dans le même
dossier. Ce n'est pas théorique : on a demandé l'anglais et reçu le portugais,
et un aperçu ouvert pendant une traduction supprimait le dossier temporaire du
travail en cours. Le même défaut vivait encore côté DOCX (`docx_engine =
DOCXTranslatorEngine()` au niveau du module) jusqu'à cette réorganisation.

`engines.new_engine("pptx")` rend donc une instance **neuve** à chaque appel, et
le registre ne rend jamais autre chose qu'une classe.

---

## Le chemin d'une traduction

```
POST /api/translate
      │
      ├─ app/api/translate.py ......... valide, facture, ouvre un job
      │                                 (jobs.create) et rend le job_id
      │
      └─ THREAD worker ................ app/services/translation_runner.py
            │
            ├─ engines.new_engine(ext).extract_text()
            ├─ engines.translation_ai.TranslatorAI  ──▶  DeepSeek
            ├─ engines.runtags .............. invariants sans seuil :
            │                                  balises, recopie, nombres
            └─ engines.new_engine(ext).inject_translation()
                     │
                     └─ jobs.emit(...)  ──▶  GET /api/translate/events/{id}  (SSE)
```

**Tout ce qui bloque tourne dans un thread**, jamais dans la boucle asyncio :
réseau, LibreOffice, rendu PDF. Un appel bloquant dans un `async def` fige
l'application entière, flux SSE compris — le symptôme est un écran qui ne
rafraîchit plus, et la cause est à vingt fichiers de là.

Les formats PDF et PPTX sont **progressifs** : chaque page (ou diapositive)
terminée devient immédiatement visible, sans attendre la fin du document.

---

## Trois pièges déjà payés

**LibreOffice ne supporte pas deux invocations concurrentes.** Tout passe par
`engines/office.py` et son verrou. Il garde de plus son profil ouvert un instant
après avoir rendu la main : sous Windows, le nettoyage du dossier temporaire
lève alors `PermissionError` et une conversion **réussie** remonte comme un
échec. D'où `ignore_cleanup_errors=True` partout où un profil est créé.

**Le cache de rendu doit être versionné.** `ENGINE_VERSION`
(`backend/engines/pdf/version.py`) entre dans les clés de cache. Tout correctif
qui peut changer un pixel impose de l'incrémenter — sinon le rendu d'avant le
correctif continue d'être servi, sans que rien ne le signale.

**Un test qui partage la constante qu'il vérifie est aveugle.** Vérifier
l'*accord* entre deux modules, pas la valeur. Et mieux vaut supprimer la seconde
définition que tester leur égalité : `STORAGE_BASE` avait divergé deux fois de
`TRANSLATIONS_DIR`, chaque fois par un simple déplacement de fichier.

---

## Documentation de l'API

```bash
backend/venv/Scripts/python.exe scripts/docs_api.py
```

Produit `docs/api/index.html` : une page **autonome** (aucun CDN, aucun serveur)
listant les 32 opérations, groupées, avec paramètres, exemple d'appel et codes
de réponse.

La source de vérité reste le **code** — le schéma OpenAPI que FastAPI déduit des
signatures et des docstrings. Une route mal documentée se voit dans la page et
se corrige dans la route, jamais dans le HTML.

---

## Lancer et vérifier

```bash
npm run dev                    # backend + frontend
npm run dev:backend            # uvicorn main:app --app-dir backend

# Les treize suites — hors ligne, aucun appel à DeepSeek
cd backend
for t in tests/test_*.py; do venv/Scripts/python.exe "$t"; done
```

Une suite **doit** sortir en `exit=0`. Un score vert suivi d'un code de retour
non nul cache toujours quelque chose : c'est ainsi que le verrou de profil
LibreOffice est resté invisible plusieurs semaines.
