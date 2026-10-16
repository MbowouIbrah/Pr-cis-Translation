# PASSATION — 16/10/2026 (fin de soirée)

## 1. Objectif en cours
Tests réels faits (PPTX + CV). Passe de cohérence PDF faite. Reste : déploiement de la 1.1.0 et petites dettes.

## 2. Fait
- Moteur contextuel fusionné dans main : fiche, ordre de lecture, lots par page, cohérence avec la fiche.
- Test réel PPTX `d3a01a4d40bd` (CFAO, 50 diapos) analysé :
  - les diapos 23-50 sont VIDES dans la source (seuls le masque et le numéro) : pas un bug ;
  - 42 reprises « recopie » sur 50 sont des faux positifs (MOBILITY, marques) ;
  - quelques incohérences « 1° Proposition ».
- Fusionné dans main (831089f), poussé :
  - afc3a20 : l'aperçu partiel affichait « 1 » sur chaque diapo convertie seule.
    - `engines/pptx/engine.py` `_figer_numeros` (après `build_partial_pptx`) remplace `<a:fld type="slidenum">` par le numéro dans le deck complet (ordre `sldIdLst` + `firstSlideNum`).
    - Ne s'applique que si des slides sont exclues ; le fichier livré garde ses champs.
    - Vérifié par un rendu LibreOffice réel : la diapo 24 isolée affiche 24.
  - 350d470 : un fragment répété n'est traduit qu'une fois.
    - `app/services/translation_runner.py` `_memoriser` (clé = texte source balisé ; le premier rendu gagne ; les fragments sans lettre sont ignorés) + paramètre `par_texte` de `_reprendre_slide`.
    - Alimenté par la reprise et après chaque slide traduite.
- Tests : `tests/test_pptx_preview.py` (sections NUMERO et DOUBLON), 37/37, 3 mutations tuées. Toutes les suites backend sont vertes.

## 3. Décisions
- ENGINE_VERSION n'est pas bumpé : seul le partiel change, le rendu complet est identique (le cache n'est pas concerné).
- Dédoublonnage par texte BALISÉ : deux paragraphes au découpage de runs différent ne sont pas interchangeables.
- Après la fin, `/preview` sert le rendu complet (numéros corrects). Le partiel n'est servi que pendant la traduction et 60 s après la fin hors PDF (`app/api/documents.py:~422`).

## 4. Reste à faire
1. ~~Cohérence PDF~~ FAIT (16/10, feat/coherence-pdf) : `engines/pdf/translate.py` `coherence_document` (même consigne `runtags.CONSIGNE_COHERENCE`, source unique partagée avec le PPTX, + fiche), branchée en fin de `stream.translate_pdf_progressive`.
   - Seules les pages touchées sont re-rendues (`delete_page` + `insert_pdf`), le cache est réécrit, le partiel resauvé et les pages ré-annoncées « done » (l'aperçu se recharge).
   - Jamais sur une pure reprise (`nouvelles == 0`) : un cognat gardé ne coûte pas un appel à chaque ouverture. Reprise aux balises mutilées ou identique : écartée.
   - ENGINE_VERSION non bumpé : le rendu d'un pages.json donné est inchangé.
   - `tests/test_coherence_pdf.py` 10/10, 5 mutations tuées ; 44 suites vertes. Pas encore vérifié sur le CV réel `b885219a18f7` (il faut le retraduire, le cache de reprise court-circuite la passe).
2. ~~Faux positifs `runs_recopies`~~ CLOS sans code (16/10) : depuis 350d470 les 41 cas du deck CFAO = 2 textes distincts (« MOBILITY », « & restrictions »), déjà en langue cible → 2 redemandes par document. Règle « le document disculpe » (mot recopié produit ailleurs par le modèle) testée : réfutée, aucun des deux n'apparaît ailleurs. Le texte ne permet pas de trancher ; le juge reste la redemande.
3. Restent d'avant :
   - police de repli Times→Liberation Serif (`engines/pdf/engine.py` `_matched_family`) ;
   - clignotement de la carte PPTX ;
   - doc `d3a01a4d40bd` (original de 0 octet, vu précédemment) ;
   - OCR du Code de la Route ;
   - déploiement 1.1.0.

## 5. Pièges / contexte
- Tests : `cd backend && for t in tests/test_*.py; do PYTHONIOENCODING=utf-8 venv/Scripts/python.exe "$t" </dev/null; done`.
  - `test_pptx_preview.py --sans-db` saute le test de cache.
  - Toujours `</dev/null` : une commande qui lit stdin bloque le shell.
- Éditer par script Python (`newline=''`, CRLF/LF mixtes) ; les heredocs bash écrasent `\\`.
- Serveur sans rechargement automatique : le relancer après chaque modification du backend.
  - `cd backend && PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m uvicorn main:app --port 8000`
  - front : `cd frontend && npm run dev` (port 3000).
- Banc : `scripts/banc_deepseek.py <sortie.json> [--configs] [--docs] [--pages N]`.
