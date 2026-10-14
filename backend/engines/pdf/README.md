# Moteur PDF — référence technique

`engines/pdf/` · version 1.0.0

> Le **contexte** — pourquoi ce moteur a cette forme, les pièges déjà payés, la
> clé de cache — vit dans [`CONTEXTE.md`](CONTEXTE.md). Cette page-ci est la
> référence : format JSON, CLI, règles de détection.

Il fait **une seule chose**, en deux temps :

1. **Extraction** — lit chaque page et sérialise **chaque objet détectable**
   (texte, image, dessin vectoriel) en JSON, dans l'ordre de peinture. Le texte
   passe par une analyse de disposition (façon « sélection de texte » d'un
   lecteur PDF, **sans OCR**) : caractères/spans → **lignes visuelles** →
   **paragraphes**. Un paragraphe détecté = un objet (avec ses lignes/runs).

2. **Réinjection** — reconstruit chaque page sur une feuille **vierge** (mêmes
   dimensions) en redessinant chaque objet **exactement à sa position et avec
   sa mise en forme d'origine**, puis trace une **bordure** autour de chaque
   objet.

Le JSON est la **seule source** du rendu reconstruit (rien n'est copié depuis
le PDF original) — c'est un vrai test de fidélité de l'extraction.

## Utilisation

Depuis `backend/`, avec son venv :

```bash
# Extraction  ->  <pdf>_objects.json  (images encodées en base64 dans le JSON)
backend/venv/Scripts/python.exe -m engines.pdf.cli extract "mon.pdf"

# Extraction avec images en dossier annexe (JSON plus léger)
backend/venv/Scripts/python.exe -m engines.pdf.cli extract "mon.pdf" --assets

# Réinjection : reconstruit le PDF depuis le JSON, avec bordures
backend/venv/Scripts/python.exe -m engines.pdf.cli reinject "mon_objects.json" "sortie.pdf"

# Réinjection sans bordures
backend/venv/Scripts/python.exe -m engines.pdf.cli reinject "mon_objects.json" "sortie.pdf" --no-borders

# Aller-retour complet (extraire puis reconstruire) — test de fidélité
backend/venv/Scripts/python.exe -m engines.pdf.cli roundtrip "mon.pdf" "sortie.pdf"
```

En Python :

```python
from engines.pdf import PDFObjectEngine

engine = PDFObjectEngine()
data, json_path = engine.extract("mon.pdf")      # PDF -> JSON
engine.reinject(data, "sortie.pdf")              # JSON -> PDF (+ bordures)
```

## Format JSON

```jsonc
{
  "source": "mon.pdf",
  "embed_images": true,
  "fonts": {                       // polices embarquées du PDF source
    "PTSerif-Regular": [           // 1..N sous-ensembles (par xref)
      { "ext": "ttf", "b64": "..." }
    ]
  },
  "pages": [
    {
      "page_num": 1,
      "width": 612.0,
      "height": 792.0,
      "elements": [                // dessins et images entrelacés par `seqno`, puis texte
        { "type": "drawing", "draw_type": "f", "bbox": [...], "items": [...],
          "stroke_color": [...], "fill_color": [...], "width": 1.0, "seqno": 12,
          "clips": [ { "items": [...], "even_odd": false } ], ... },
        { "type": "image", "bbox": [...], "xref": 582, "ext": "png",
          "asset_b64": "...", "seqno": 13, "clips": [...] },
        { "type": "paragraph", "bbox": [...],
          "text": "texte du paragraphe (dé-césuré, lignes jointes)",
          "lines": [               // 1..N lignes visuelles
            { "type": "text_line", "bbox": [...], "text": "…",
              "runs": [            // 1..N spans, rendus à leur position exacte
                { "text": "Chapter 1: ", "origin": [x, y], "bbox": [...],
                  "font": "PTSerif-Regular", "size": 11.0, "color": [r, g, b],
                  "flags": 0, "bold": false, "italic": false, "dir": [1, 0] }
              ] }
          ] }
      ]
    }
  ]
}
```

## Détection des lignes (analyse de disposition)

Le texte est extrait span par span (PyMuPDF), puis regroupé en **lignes
visuelles** :

1. **Clustering vertical** — les spans partageant une même ligne de base sont
   regroupés (tolérance relative à la taille de police).
2. **Coupe aux colonnes** — au sein d'une ligne, on coupe aux **grands écarts
   horizontaux**, mesurés **relativement à la largeur de glyphe de la ligne**
   (et non à la taille de police) :
   - écart `< 2.5 × largeur_glyphe` → même ligne (un mot très espacé —
     *letter-spacing* — reste soudé) ;
   - écart `≥ 2.5 × largeur_glyphe` → **séparateur de colonne** → coupe (un
     titre de chapitre et son numéro de page restent deux objets distincts).

C'est ce qui distingue un *letter-spacing* (~1–2× la largeur d'un glyphe) d'un
saut de colonne (~3–5×) — impossible avec un seuil basé sur la taille de police.
Chaque ligne détectée devient un objet `text_line` contenant ses *runs* (les
spans, rendus fidèlement à leur position/police/style) et **une seule bordure**.

Seuils réglables sur l'instance : `PDFObjectEngine._COL_SPLIT_FACTOR` (2.5) et
`_SPACE_FACTOR` (0.30, insertion d'espace dans le texte lisible).

## Regroupement en paragraphes (étapes 7–8)

Les lignes sont regroupées en **paragraphes**, de façon **column-aware** : deux
lignes ne peuvent fusionner que si elles se chevauchent horizontalement, et le
parent d'une ligne est la ligne au-dessus la **plus proche verticalement**
(flot de lecture) — c'est ce qui permet à un texte de s'**enrouler autour d'une
image ou d'un encart** (les lignes reviennent à gauche sans être happées par
l'encart voisin). La **géométrie est prioritaire**.

Hiérarchie de décision entre deux lignes voisines (`g` = saut de ligne de base /
taille) :

1. **Signaux DURS** → coupe immédiate :
   - **Liste** : ligne commençant par une puce (`•`, `-`, …) ou un numéro
     (`1.`, `a)`) → nouvel item ;
   - **Style** : changement de graisse (titre gras vs corps) ou de taille
     (`> 0.20 ×`) → isole titres/sous-titres ;
   - **Géométrie** : `g > 1.8` (ligne à blanc) → nouveau paragraphe.
2. **Flot continu** : si `g < 1.35` → **fusion obligatoire**, quels que soient
   les décalages horizontaux (`x0`) ou une ponctuation faible (`,` `;` `:`).
   Si la ligne suivante commence par une **minuscule** → fusion aussi.
3. **Zone modérée** (signaux faibles) :
   - **Indentation** : alinéa `> 1.2 × taille` → coupe ; distinction d'avec un
     bloc **centré** par comparaison des **axes centraux** (même centre que la
     ligne précédente → centré → on ne coupe pas). (Comparer au bord droit
     précédent était instable : titres de longueurs variables.) ;
   - **Ponctuation** : ligne finissant par `.` `!` `?` suivie d'une majuscule,
     **uniquement** si `g > 1.6` et hors **conjonction de coordination**
     (`But`, `And`, `So`, `However`, …) → coupe.

Points clés (corrigent les cas complexes) :
- **Aucune coupe basée sur `x0` seul** : l'indentation ne coupe qu'en zone
  modérée (gap élevé), donc le texte enroulé autour d'une image (gap normal
  malgré un `x0` décalé) reste soudé.
- **Seuls `.` `!` `?` peuvent couper** ; `,` `;` `:` ne coupent jamais.
- **Minuscule ou conjonction en tête de ligne** → continuation → fusion.

**Étape 8 (nettoyage du texte lisible)** : dé-césure des mots coupés en fin de
ligne (`trans-` + `port` → `transport`) et écrasement des espaces multiples.
N'affecte **que** le champ `text` du paragraphe (le rendu reste run par run,
fidèle).

Seuils réglables : `_PARA_GAP_FACTOR` (1.8), `_PARA_MODERATE_FACTOR` (1.35),
`_PARA_PUNCT_FACTOR` (1.6), `_PARA_INDENT_FACTOR` (1.2), `_PARA_SIZE_FACTOR`
(0.20). Pour revenir à des objets **ligne** (sans paragraphes) :
`engine.group_paragraphs = False`.

**Non implémenté** (volontairement) : l'ordre de lecture inter-colonnes global
(étape 6) — chaque paragraphe est correct et boxé, mais la concaténation d'une
page multi-colonnes suit l'ordre haut→bas/gauche→droite, pas un vrai flux
colonne par colonne. À ajouter si besoin pour l'extraction de texte continu.

## Fidélité — points clés

- **Polices** : les polices embarquées du PDF sont extraites et réutilisées
  telles quelles (glyphes exacts). Les polices **CID / Type0 (Identity-H)**
  sont embarquées sans cmap Unicode ; le moteur reconstruit cette cmap à partir
  de `get_texttrace()` (couples Unicode↔glyph-id) via **fontTools**. Repli
  base‑14 (Helvetica/Times/Courier, selon serif/sans/mono) si une police n'est
  pas embarquée.
- **Images** : composées avec leur masque de transparence (**SMask**) en PNG à
  alpha, pour préserver les zones transparentes.
- **Ordre de peinture** : dessins et images dans l'ordre de l'original (`seqno`,
  indice du journal `get_bboxlog()`), puis le texte au-dessus.
- **Découpes (clips)** : chaque dessin ou image garde la pile des découpes
  actives (`clips`), rejouée en « chemin W n » autour de lui seul. Celles d'une
  image sont relevées par un device MuPDF au moment où elle est peinte.
- **Bordures** (debug) : vert = texte, rouge = image, bleu = dessin. Tracées en
  dernier, toujours visibles. Désactivables (`--no-borders`). Un paragraphe
  multi-lignes est cadré par un **contour rectilinéaire en escalier** qui passe
  par les sommets de chaque ligne (bords droits irréguliers, retraits,
  enroulement autour d'une image/encart en L) — pas un rectangle englobant.

## Limites connues (assumées — moteur « basique »)

- **Dégradés / shadings PDF purs** (`sh`) non capturés (ni raster ni tracé) ;
  seuls les remplissages/traits vectoriels de `get_drawings()` le sont.
- **Images pivotées / transformées** : placées dans leur rectangle
  d'englobement (pas de matrice de rotation).
- **Texte pivoté** : rendu à sa baseline sans rotation (cas basique).
- **Découpes non rejouables** (par texte, masque d'image ou contour de trait) :
  omises, l'objet est peint entier. Groupes et masques doux ignorés.
- **Emprise des dessins découpés** : le `bbox` reste celui du tracé entier, non
  resserré à sa découpe (la détection de conteneurs en dépend).
