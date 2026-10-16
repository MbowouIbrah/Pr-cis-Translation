# PASSATION — 16/10/2026 (soir)

## 1. Objectif en cours
Moteur de traduction CONTEXTUEL : livré et commité sur `feat/deepseek-seul` (1a0216a, fe7385e). Reste à fusionner vers main puis déployer.

## 2. Fait (16/10)
- DeepSeek seul, surveillance du solde (`app/services/solde_ia.py`, `GET /api/admin/ia`), banc `scripts/banc_deepseek.py [--docs cv,...]`.
- Moteur contextuel (`engines/translation_ai.py`, `engines/pdf/translate.py`) :
  - fiche de document : 1 appel flash, jointe au prompt système ;
  - ordre de lecture XY-cut ;
  - lots par page ;
  - fenêtre de page autour du lot ;
  - défaut « ajout » (parenthèse en trop → redemande).
  - Couvre le PDF progressif (fiche dans le cache de reprise) et le PPTX (fiche `.fiche` à côté de la reprise). `translate_json` (DOCX/XLSX) écrit sa fiche lui-même.
- Pannes :
  - 402/401/403 : la page échoue au lieu d'être cachée avec le texte source.
  - 429 : une pause, puis le lot est relancé entier ; une seconde 429 compte comme hors service.
  - Une alerte par série de 402.
- Tests : `tests/test_contexte_traduction.py` 29/29 (PDF synthétique à extraction renversée), 6/6 mutations tuées. Suites : 43/43 vertes.
- Banc avec contexte, 3 pages par doc (`scratchpad/banc_ctx.json`) :
  - défauts CV 21→0, mv21 8→1 ;
  - flash et flash:high : écart seulement stylistique ; high coûte 1,6× et met 2× plus de temps ;
  - FAST=flash et PRECISE=flash:high gardés (`app/config.py:182`).

## 3. Décisions / constats
- La fiche avait substitué des diplômes étrangers (BEPC → « GCE O Level »). Règle désormais : garder le NOM OFFICIEL et traduire ses mots communs (« BTS in Organisational Communication », « Probatoire A4 German »).
- Le compteur « recopie » du banc donne 21 faux positifs sur Data Science (lignes de sommaire à noms propres), sans défaut réel.
- ENGINE_VERSION n'est pas bumpé : le rendu est inchangé, seule la traduction change.

## 4. Reste à faire
1. Optionnel :
   - passer `fiche` au `_translate_batch` de `_coherence_document` ;
   - lire l'ordre des diapositives depuis `presentation.xml` (`sldIdLst`) plutôt que depuis le nom `slideN.xml` (`_textes_diapositives`) ;
   - `runs_recopies` : faux positifs sur les lignes « Nom, Titre at Société ».
2. Front : `npm run build` et `npm run lint` (non relancés, aucun fichier front touché).
3. Restent d'avant :
   - police de repli Times→Liberation Serif (`engines/pdf/engine.py` `_matched_family`) ;
   - clignotement de la carte PPTX ;
   - doc `d3a01a4d40bd` (original de 0 octet) ;
   - OCR du Code de la Route ;
   - fusion vers main ;
   - déploiement 1.1.0.

## 5. Pièges / contexte
- Tarifs USD/1M (pleines ; creuses −50 %, pleines = 01-04 h et 06-10 h UTC lun-ven) : flash 0,006 cache / 0,30 / sortie 1,20 ; v4-pro 0,044 / 1,32 / 3,96.
- Réflexion : `extra_body={"thinking":{"type":"enabled|disabled"}}`, `reasoning_effort` ; temperature sans effet en réflexion.
- Banc : `cd backend && PYTHONIOENCODING=utf-8 venv/Scripts/python.exe scripts/banc_deepseek.py <sortie.json> [--configs a,b] [--docs a,b] [--pages N]`.
- Tests : `cd backend && for t in tests/test_*.py; do PYTHONIOENCODING=utf-8 venv/Scripts/python.exe "$t"; done`.
- Éditer par script Python écrit avec Write (`newline=''`, CRLF/LF mixtes) : les heredocs bash écrasent `\\`.
- Serveur : `cd backend && PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m uvicorn main:app --port 8000`.
