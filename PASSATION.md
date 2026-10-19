# PASSATION — 19/10/2026

## 1. Objectif en cours
Corriger, de façon GÉNÉRIQUE, les défauts du moteur PDF révélés par le ticket support « préservation de mise en forme et de l'arrière-plan » (prod, 19/10/2026).

## 2. Fait
- Diagnostic complet, AUCUN code modifié (branche main propre).
- Docs : `C:/Users/IBRAH/Downloads/CD26052553710.pdf` (original iText, bilingue : p1 FR, p2 EN) et `CD26052553710 (1).pdf` (traduit ES).
- Expansion XLSX déjà fusionnée (0d48e30) ; déploiement 1.1.0 toujours côté utilisateur.

## 3. Décisions prises
- Norme document officiel : TOUT traduire, en-têtes, devise et versions bilingues comprises ; tout bloc qui n'est pas déjà dans la langue cible est traduit, quelle que soit sa langue d'origine (un traducteur assermenté traduit « République du Cameroun », la devise, les administrations).
- Chaque correctif prouvé sur PDF SYNTHÉTIQUE (`test_engine_v2_generic.py`), jamais calé sur ce certificat ; bump ENGINE_VERSION (cache de rendu).

## 4. Reste à faire (ordre)
Invoquer la skill mission, puis dans `backend/engines/pdf/engine.py` :
1. ~~Gras synthétique~~ FAIT (moteur 1.0.6) : passe `TEXT_COLLECT_STYLES` À PART (`_synth_bold_keys`) car ce drapeau fusionne aussi les glyphes superposés (cassait P28) ; span scindé au changement de mode ; rendu en `render_mode=2` avec la police embarquée seulement (sinon vraie Bold de repli) ; test P31.
2. ~~Italique synthétique~~ FAIT (moteur 1.0.7) : pente lue au device MuPDF (`_synth_italic_keys`, `trm`×`ctm`, clé glyphe arrondie au dixième) ; span scindé au changement de pente ; rendu par `morph` cisaillé (`_morph`, ESPACE PDF y vers le haut : +pente = vers l'avant) avec la police embarquée seulement ; test P32 (sens de la pente vérifié, piège d'un test qui penchait à l'envers).
3. ~~Filigrane perdu~~ FAIT (moteur 1.0.8) : poses d'images lues au device (`_image_clip_stacks(page, xrefs)`), xref rattaché par IDENTITÉ d'objet (`pdf_load_image` tenu pendant le passage = même pointeur que `fill_image`), `get_image_rects` en simple repli ; opacité de pose notée (`alpha`) et reportée en multipliant le canal alpha (`_pixmap_attenuee`) ; test P33 (piège : `update_stream(compress=False)` retire `/Filter`).
4. **Blocs non traduits** : colonne droite EN « REPUBLIC OF CAMEROON / Peace - Work - Fatherland », « DIRECTORATE GENERAL OF TAXATION » restées en anglais alors que « MINISTERIO DE HACIENDA » est traduit. Trouver la cause (saut d'un bloc hors langue source ? recopie du modèle acceptée par l'invariant « recopié » ?) et corriger.
5. **Reflow** : deux lignes centrées indépendantes fusionnées puis recoupées (« REPUBLIC OF CAMEROON Peace - » / « Work - Fatherland ») → ne pas fusionner des lignes centrées courtes distinctes.
6. **Libellé contaminé** : p1 « Tax Structure » → « Centro de Impuestos » (doublon du libellé voisin) ; p2 donne « Estructura fiscal ».
7. **Incohérence inter-pages** : Paraje/Distrito, Régimen/Régimen fiscal, registro/fichero, 1 000 000/1.000.000, L 1.º/L 1 → appliquer le glossaire de document entre pages.
8. Répondre au ticket (vue admin /admin/support) une fois livré.

## 5. Pièges / contexte
- Diagnostic rapide : `page.get_text("dict", flags=fitz.TEXTFLAGS_DICT|fitz.TEXT_COLLECT_STYLES)` → `char_flags` ; `engines.pdf.engine._image_clip_stacks(page)` liste les vrais rects d'images.
- `page.get_image_info()` et les blocs image de `get_text` OMETTENT aussi le filigrane : ne pas s'y fier.
- Tests sans appel payant (traduction simulée) ; Python des PDF téléchargés lancé avec `-I`.
- Vérifier visuellement le rendu (gras, italique, filigrane à 20 %) avant de déclarer fini.
