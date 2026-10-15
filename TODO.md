# À faire — Précis Translator

Liste simplifiée de ce qui reste. Ordre = priorité indicative.
Cochez au fur et à mesure ; les détails vivent dans les `CONTEXTE.md` / mémoires.

---

## 1. OCR — documents scannés  (chantier suspendu)
Étude d'architecture : `docs/etude-ocr.md`. Le travail a été remis à zéro le
07/08 et se poursuit sur la branche `feat/ocr-moteur-propre` (anciennes
tentatives archivées en tags) ; rien de l'OCR n'est sur cette branche.

Décision d'architecture (irréversible) : **on conserve l'image d'origine et on
ne remplace que les zones de texte traduites.** Reconstruire la page sur une
feuille vierge jetterait tout ce que l'OCR n'a pas vu : tampons, signatures,
sceaux, filets. Rédhibitoire sur un acte d'état civil.

- [x] **Étape 0** — détecter un scan, ne rien facturer d'illisible.
- [x] Scans synthétiques avec vérité terrain : `backend/tests files/scans/`
      (`scan_*.pdf` + `scan_*_verite.json`, hors dépôt).
- [ ] Étapes suivantes (identification, redressement, OCR → spans, masquage,
      seuils, intégration) : voir la branche OCR.

**Tarif : inchangé pour l'instant** (décision du 27/07).

## 2. Paiement & abonnement  ⭐ bloquant avant testeurs payants
- [x] Paiement à la page : le droit est au **document** (`documents.paid`), pas au plan.
- [x] Abonnements Starter / Pro (branche `feat/abonnements`) : droits revérifiés
      au crédit, quota de date à date, prorata.
- [ ] Choisir le PSP de production : **ZitoPay** recommandé, NotchPay en secours.
- [ ] Test bout-en-bout réel avec webhook public (jamais cru sur parole).

## 3. Hébergement
- [x] Recette Docker complète (`docker-compose.yml`, `scripts/precis.sh`).
- [x] AWS retenu : EC2 + compose en IaC (`infra/aws/`, `deploy/`,
      `.github/workflows/`). Mode d'emploi : `docs/deploiement.md`,
      `docs/guide-premiere-mise-en-ligne.md`.
- [ ] Nom de domaine + HTTPS.
- [ ] Sauvegarde HORS de l'hébergeur + alerte de disque plein.

## 4. Excel — moteur XLSX 0.2.0
- [x] Couverture du texte complète (branche `feat/xlsx-complet` fusionnée le 15/10) :
      cellules, onglets, graphiques, zones de texte, commentaires, en-têtes de
      tableaux, croisés dynamiques, formats personnalisés, propriétés.
- [ ] L'expansion du texte traduit (largeurs de colonnes) reste à traiter.

## 5. PDF — défauts à résoudre
- [x] **22 polices de repli absentes de `main`** (28/07). Ajoutées par `ee1cf67`
      sur une branche jamais fusionnée : jamais supprimées, jamais arrivées.
      `regular`/`bold` ne résolvaient pour AUCUNE famille, `ptserif` rien du
      tout → repli silencieux sur la base-14. Effet de bord : la suite
      `test_engine_v2_generic.py` **plantait** sur main (P21, `None.buffer`),
      donc ses 6 dernières vérifications n'étaient jamais exécutées. 59/59.
- [x] `p14-bis` — **SANS OBJET : déjà résolu sur main, autrement et mieux** (28/07).
      Le correctif y vit sous le nom **P18** (`_overhead_frame`, engine.py:2220),
      arrivé indépendamment de la branche WIP. Même diagnostic (la légende
      « Shoppers return » ; « Trading floor » qui ne s'en tirait que par accident,
      son panneau étant centré sur la page), même garde-fou (le cadre ne peut que
      RESSERRER), mais en plus : **5 vérifications** dans la suite générique
      (P18 ×3 + P19 ×2 en contre-épreuve), que la branche WIP n'a jamais eues.
      Rejoué explicitement, P18 rejette les DEUX pièges que `d360029`
      documentait — le filet d'un demi-point (→ `None`) et l'aplat large qui
      élargirait le cadre (→ `None`) — et retient la photo qui serre vraiment.
      **Ne pas transposer `d360029`** : n'apporterait rien, risquerait une
      régression. La branche `feat/p14-bis-ancrage` peut être abandonnée.
- [x] `mv21` compact — **relancé et vérifié** (28/07). 12 pages rendues,
      segmentation identique à la référence (525 éléments / 318 paragraphes),
      deux colonnes intactes, césure et justification correctes, bloc « PART 1 »
      et en-tête courant en place. Rien à signaler.
- [x] `p15/p16` — **colonne justifiée courte : limite STRUCTURELLE, close** (28/07).
      Défaut reproduit sur document synthétique : une colonne de 4 lignes à
      100 pt déchire « plants » et « raised » hors de leur phrase (3 paragraphes
      au lieu d'un) ; la même colonne en 13 lignes reste intacte.

      **Ce n'est pas un problème de seuil.** Le témoin est une ligne INTACTE, or
      c'est ce qu'une colonne étroite justifiée produit le moins — ses blancs
      enflent et PyMuPDF éclate ses lignes. Mesuré sur 6 textes différents, le
      taux est de ~0,45 témoin par ligne :

          3 lignes  moy 1,2  max 2  -> quorum 3 INATTEIGNABLE
          4 lignes  moy 1,7  max 3       8 lignes  moy 3,5  -> fiable
          6 lignes  moy 2,3  max 3      12 lignes  moy 5,7

      Une colonne de 3 lignes ne peut JAMAIS fournir 3 témoins : la preuve
      n'existe pas. Abaisser le quorum ne la fait pas apparaître, il fait
      accepter des preuves fausses — vérifié sur la suite complète :
      **quorum 2 → 58/59** (la vérification centrale de P15 tombe, soit le
      défaut que P15 corrige), **quorum 3 → 59/59**, **quorum 4 → 58/59**.
      La valeur en place est donc l'optimum mesuré, pas un réglage prudent.

      ⚠ Piste re-vérifiée et CLOSE : attester la colonne par les FRAGMENTS.
      Rejouée sur la page P17, elle donne la colonne [45,0 ; 567,0] — la page
      entière — et souderait les 4 colonnes. La preuve doit rester la ligne
      INTACTE, qui ne peut pas exister à travers une gouttière.

      Tout est consigné dans `engine.py` au-dessus de `_JUST_MIN_LINES` ; banc
      de mesure : `scripts/mesurer_colonne_courte.py`.

### Téléchargement automatique des polices — SUPPRIMÉ avec le moteur v1
Vérifié le 28/07 : `_ensure_font` / `_download_google_font` vivaient dans
`backend/pdf_translator_engine.py` (v1, supprimé). **Zéro occurrence** dans le
v2. Le fichier `backend/fonts/.dl_failed.json` est un vestige que plus personne
n'écrit ni ne lit.

Le v2 le remplace par 5 familles de repli choisies par classe typographique
(`_matched_family`) — un substitut assumé, jamais la police exacte.

**Mesuré avant d'envisager de le rétablir** : sur les deux documents de
référence, les polices sont **déjà toutes embarquées** (mv21 7/7, Handbook
14/16), et le moteur les extrait directement. Les seules non embarquées du
Handbook sont Helvetica et Helvetica-Bold — **32 caractères sur 39 519
(0,08 %)** — or Helvetica est une base-14, reproduite à l'identique sans
fichier. Un téléchargeur ne rapporterait donc **rien** ici.
Il ne redeviendrait utile que sur un document citant une police NON embarquée
ET hors base-14 (bureautique : Calibri, Segoe UI, Cambria).

## 6. PPTX
- [ ] **Lot 1 — fidélité** à finir (le Lot 0 aperçu est fait).

## 7. Cohérence commerciale
- [ ] **La formule Entreprise promet 200 Go à UN client** — plus que le disque
      entier du serveur envisagé. Ne pas la vendre avant d'avoir prévu le
      stockage correspondant.
- [ ] Interface : **afficher `pages_ignorees`**. Le backend le renvoie déjà ;
      sans affichage, l'utilisateur d'un document mixte voit une page revenir
      non traduite sans comprendre qu'elle ne lui a pas été facturée.

## 8. Optimisation (optionnel)
- [ ] Coût prompt : le message `system` (4 700 car.) est réémis **à chaque page**
      (~60 % des tokens d'entrée). Regrouper par lots ≈ **−45 %** sur un long doc.
- [ ] Bundle JavaScript > 500 Ko → plusieurs secondes d'écran blanc sur une
      connexion mobile camerounaise.

---

### Fait le 27/07/2026
Garde-fou de configuration (refus de démarrer en production avec un secret
public) · clé publique révoquée, modèles `.env` assainis · origines localhost
coupées en ligne · recette Docker complète vérifiée de bout en bout · fuite de
la clé dans le journal de build · **pages scannées plus jamais facturées** ·
fabrique de scans synthétiques · étude des outils OCR.

**Défauts trouvés en chemin, qu'aucun test ne couvrait :** 22 polices absentes
du dépôt (PDF rendus en Helvetica, en silence) · base de données jointe au
mauvais endroit sans le dire · aperçu progressif qui aurait disparu en
production seulement · message de démarrage capable de tuer le serveur.
