# PASSATION

## 1. Objectif en cours
Résolution des polices non embarquées dans le moteur PDF (téléchargement à l'identique, sinon clone métrique, sinon police la plus proche), branche `feat/polices-telechargement-substitution`, moteur 1.0.12.

## 2. Fait
- Ticket CD26052553710 : points 1–7 commités (point 8 laissé de côté à la demande).
- `backend/engines/pdf/polices.py` (nouveau) : `Resolveur` (identique locale / Google Fonts css2 validée famille-pente-graisse-encre / clone `CLONES` / plus proche par largeurs), cache `translations/_polices`, absences 30 j, disjoncteur réseau, verrou par variante en vol, redirections hors `_HOTES` refusées, échéance globale.
- `backend/engines/pdf/engine.py` : profils de largeur (`_measure_span`, `_font_profiles_from`), chaîne `_full_fallback_font`.
- `backend/engines/pdf/stream.py:149-153` : profils passés à `_extract_fonts` (sans eux, la couche « proche » était aveugle en production).
- `backend/scripts/precharger_polices.py` : ~90 familles pré-chargées dans `backend/fonts/catalogue` (gitignoré + dockerignoré) ; lancé au build (`Dockerfile`, non bloquant) ; Carlito/Caladea installés.
- `backend/tests/test_polices.py` : 20/20 ; `racine.py` pose `PRECIS_FONT_DOWNLOAD/SYSTEM/CATALOGUE=0`.
- `test_rendu_structure.py` : `fiche_document` simulé (appelait réellement DeepSeek).
- Commits : c6dfe71, a588a76, 2192374. 9 suites moteur en code 0.

## 3. Décisions prises
- Serif détecté par le nom seulement : le drapeau MuPDF n'est pas fiable.
- Largeurs mesurées prioritaires dans le score « proche » (chasse fixe pénalité 10).
- Base14 (Helvetica/Times/Courier) gardé en raccourci : métriques identiques à Liberation.
- Pré-chargement laissé après `COPY backend/` : `engines/__init__.py` importe tous les moteurs.

## 4. Reste à faire
1. FAIT (1.0.11) : liste blanche `backend/fonts/google_familles.txt` (1950 familles, seules interrogées) ; rendu fait réseau dégradé non conservé (`render_cache.marque_polices()` → `store_render(..., marque)`) ; polices système désactivées par défaut (`PRECIS_FONT_SYSTEM=1` pour les activer). test_polices 23/23.
2. FAIT (1.0.12) : `fsType=2` exclu de l'index ; « Arial Black », « Helvetica-Narrow » et autres chasses gardés dans la famille ; `absences.json` purgé et borné à 5000, plafond de 60 téléchargements par heure (refus noté comme dégradation) ; `PRECIS_FONTS_CACHE` posé par `app/config.py`.
3. FAIT (1.0.12) : `categorie()` (catalogue Google `Famille	Catégorie`, puis marques du nom) sert aux fiches locales (panose souvent muet) et à la cible ; un changement de catégorie coûte 5.0 dans « proche ». test_polices 28/28, générique 100/100.
4. Pousser la branche / PR vers main.

## 5. Pièges / contexte
- Comparatif identité (sans API) : scripts dans le scratchpad de session (`run_corpus.py`, `comparer.py`) ; résultat 1.0.10 = 1.0.9 avec polices livrées ; sans elles, Inter/Oswald/PT Serif retéléchargées.
- Python lisant des PDF téléchargés : `venv/Scripts/python.exe -I`.
- Windows verrouille les fichiers ouverts : lire les polices en bytes.
- Tout changement de rendu : bump `backend/engines/pdf/version.py`.
- Tests : `cd backend && PYTHONIOENCODING=utf-8 venv/Scripts/python.exe tests/<fichier>`.
