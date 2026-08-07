"""La LECTURE d'une page scannée : des pixels aux spans.

CE MODULE NE DÉCIDE RIEN
-------------------------
Il lit, il mesure, il rend des spans. Il ne juge pas si un mot mérite d'être
traduit — c'est le rôle de `structure.py`, et l'ordre compte : la v1 triait au
MOT, avant tout regroupement, et c'est ce qui la faisait retenir des fragments
de dessin. Un mot seul ne dit pas s'il appartient à un paragraphe ou à une
illustration.

CE QUI EST REPRIS DES DEUX VERSIONS PRÉCÉDENTES
------------------------------------------------
Ces points ont été payés par la mesure ; ils ne se rediscutent pas sans
nouvelle mesure (bilan du 28/07) :

  · LA DOUBLE LECTURE — image prétraitée + image brute, puis union. La
    binarisation seule détruit les gros titres gras : une fenêtre adaptative de
    31 px tombe entièrement à l'intérieur d'un fût de lettre et le blanchit.
    Mesuré : 628 mots -> 464, confiance 73 -> 51 ;
  · LA BOÎTE D'ENCRE MESURÉE, jamais celle de Tesseract. Tesseract rend souvent
    la boîte de la LIGNE pour chaque mot (mesuré : 12 mots à y=[89,3 ; 96,7]),
    ce qui fait sortir des lignes au double de leur taille et ruine tout
    regroupement par baseline ;
  · ON RELIT TOUT, sans protéger les accents ni les mots courts : les protéger
    donnait 3,0 % et 4,8 % d'erreur contre 2,1 % sans garde.

CE QU'ON NE FAIT PAS, ET POURQUOI
----------------------------------
**On ne garde pas la segmentation de Tesseract.** `image_to_data` rend
`block_num`, `par_num`, `line_num` — et `par_num` est notoirement faux en
multi-colonnes et en présence de tableaux (c'est écrit dans la documentation de
Tesseract elle-même, « Hybrid Page Layout Analysis via Tab-Stop Detection »).

S'en servir importerait ses erreurs ET ferait diverger l'OCR du moteur PDF,
c'est-à-dire exactement les conflits qu'on cherche à éviter. **On ne garde que
les MOTS** ; la mise en page vient du moteur PDF.

OÙ CE MODULE TOURNE
--------------------
Dans Docker, et nulle part ailleurs : il dépend du binaire Tesseract, que le
poste de développement n'a pas. `disponible()` répond sans jamais lever, pour
que `/health` puisse le dire au lieu de laisser deviner.
"""
from __future__ import annotations

import logging

from engines.ocr.spans import span_depuis_mot

logger = logging.getLogger(__name__)

#: Résolution de lecture, en points par pouce. 300 est le seuil admis sous
#: lequel la précision chute quel que soit le moteur, et au-dessus duquel on
#: paye du temps sans rien gagner.
DPI_LECTURE = 300

#: Confiance sous laquelle un mot n'est même pas retenu comme span.
#:
#: TRÈS BAS, ET C'EST VOULU. Ce n'est pas ici qu'on trie — trier au mot est
#: précisément l'erreur de la v1. On écarte seulement ce que Tesseract lui-même
#: donne pour du bruit (`conf` négatif ou nul). Le vrai tri se fait sur des
#: BLOCS, dans `structure.py`, quand la structure est connue.
CONFIANCE_PLANCHER = 1.0


def disponible() -> dict:
    """Ce que `/health` doit pouvoir dire de l'OCR, sans jamais lever.

    C'est la seule capacité du projet qui repose sur un exécutable externe,
    donc la seule qui puisse manquer sur une machine où tout le reste marche.
    Une sonde qui plante rendrait le service « malade » alors que c'est la
    sonde qui casse.
    """
    etat = {"disponible": False, "tesseract": None, "langues": [],
            "raison": None}
    try:
        import pytesseract
    except ImportError as e:
        etat["raison"] = f"pytesseract absent : {e}"
        return etat
    try:
        etat["tesseract"] = str(pytesseract.get_tesseract_version())
        etat["langues"] = sorted(pytesseract.get_languages(config=""))
        etat["disponible"] = True
    except Exception as e:                       # binaire absent ou cassé
        etat["raison"] = f"binaire tesseract injoignable : {e}"
    return etat


def _image_de_page(page, dpi: int):
    """La page rendue en image, et l'échelle pour revenir aux points.

    On rend nous-mêmes plutôt que d'extraire l'image incorporée : un scan peut
    porter plusieurs images par page, orientées ou découpées. Rendre la page
    donne UNE image dont les coordonnées se ramènent aux points par un simple
    facteur — et c'est en points que le moteur PDF raisonne.
    """
    import fitz
    from PIL import Image

    echelle = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(echelle, echelle), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return img, echelle


def _pretraitee(img):
    """L'image binarisée, pour les textes ternes ou bruités.

    ON NE L'UTILISE JAMAIS SEULE — voir la double lecture en tête de module.
    Le seuil est GLOBAL (Otsu) et non adaptatif : une fenêtre adaptative de
    31 px tombe entièrement à l'intérieur d'un fût de gros titre gras et le
    blanchit. C'est l'impasse mesurée du 28/07 (628 mots -> 464).
    """
    gris = img.convert("L")
    try:
        import numpy as np
        a = np.asarray(gris)
        # Otsu à la main : pas d'OpenCV pour un histogramme de 256 cases.
        hist = np.bincount(a.ravel(), minlength=256).astype(float)
        total = hist.sum()
        if total <= 0:
            return gris
        omega = np.cumsum(hist) / total
        mu = np.cumsum(hist * np.arange(256)) / total
        mu_t = mu[-1]
        denom = omega * (1.0 - omega)
        denom[denom == 0] = 1e-9
        variance = (mu_t * omega - mu) ** 2 / denom
        seuil = int(np.argmax(variance))
        return gris.point(lambda v: 255 if v > seuil else 0, mode="L")
    except ImportError:
        # Sans numpy, on rend le gris : la double lecture perd son second
        # oeil, elle ne devient pas fausse.
        return gris


def _mots_bruts(img, langue: str):
    """Les mots lus par Tesseract sur UNE image, en pixels de cette image."""
    import pytesseract
    from pytesseract import Output

    # PSM 3 : segmentation automatique SANS orientation. On ne demande pas à
    # Tesseract de grouper (on jette ses blocs) mais il doit voir la page
    # entière pour lire des colonnes — PSM 6 (« bloc uniforme ») souderait
    # deux colonnes voisines en une seule ligne.
    d = pytesseract.image_to_data(img, lang=langue, config="--psm 3",
                                  output_type=Output.DICT)
    mots = []
    for i in range(len(d["text"])):
        texte = (d["text"][i] or "").strip()
        if not texte:
            continue
        try:
            conf = float(d["conf"][i])
        except (TypeError, ValueError):
            continue
        if conf < CONFIANCE_PLANCHER:
            continue
        x, y = float(d["left"][i]), float(d["top"][i])
        w, h = float(d["width"][i]), float(d["height"][i])
        if w <= 0 or h <= 0:
            continue
        mots.append({"texte": texte, "conf": conf,
                     "boite": (x, y, x + w, y + h)})
    return mots


def _boite_encre(img_gris, boite, marge_relative: float = 0.25):
    """Resserre une boîte sur l'ENCRE qu'elle contient réellement.

    POURQUOI C'EST INDISPENSABLE
    -----------------------------
    Tesseract rend souvent, pour chaque mot d'une ligne, la boîte de la LIGNE
    ENTIÈRE (mesuré en v1 : 12 mots partageant y=[89,3 ; 96,7]). Utilisée
    telle quelle, la hauteur du mot vaut celle de la ligne, le corps déduit est
    trop grand, et le regroupement par baseline part de travers.

    On cherche donc les pixels sombres dans une fenêtre ÉLARGIE (d'un quart de
    la hauteur) : élargie, parce qu'une boîte trop serrée coupe les jambages
    (p, g, q) et laisse des fantômes au masquage — l'autre défaut mesuré.

    Rend `None` si la fenêtre ne contient aucune encre : le mot est alors une
    lecture de bruit, et l'appelant l'écarte.
    """
    try:
        import numpy as np
    except ImportError:
        return boite
    x0, y0, x1, y1 = boite
    marge = max(1.0, marge_relative * (y1 - y0))
    L, H = img_gris.size
    fx0 = max(0, int(x0 - marge)); fy0 = max(0, int(y0 - marge))
    fx1 = min(L, int(x1 + marge)); fy1 = min(H, int(y1 + marge))
    if fx1 <= fx0 or fy1 <= fy0:
        return None
    fen = np.asarray(img_gris.crop((fx0, fy0, fx1, fy1)))
    # Seuil relatif au contenu de la fenêtre : un texte blanc sur fond sombre
    # doit être trouvé aussi. On prend le quart le plus sombre comme encre.
    if fen.size == 0:
        return None
    sombre = fen < (int(fen.min()) + int(fen.max())) // 2
    if not sombre.any():
        return None
    lignes = np.where(sombre.any(axis=1))[0]
    cols = np.where(sombre.any(axis=0))[0]
    return (float(fx0 + cols[0]), float(fy0 + lignes[0]),
            float(fx0 + cols[-1] + 1), float(fy0 + lignes[-1] + 1))


def _fusionner(primaires, secondaires, tolerance: float = 0.5):
    """Union des deux lectures : les mots vus par l'un et pas par l'autre.

    Un mot déjà vu par la lecture primaire n'est pas repris — on ne veut pas
    deux spans superposés, qui feraient deux fois la même ligne. Le
    recouvrement se juge en aire, pas en distance : deux lectures du même mot
    se recouvrent largement, deux mots voisins non.
    """
    out = list(primaires)
    for cand in secondaires:
        cx0, cy0, cx1, cy1 = cand["boite"]
        aire_c = max(1e-6, (cx1 - cx0) * (cy1 - cy0))
        double = False
        for vu in primaires:
            vx0, vy0, vx1, vy1 = vu["boite"]
            l = min(cx1, vx1) - max(cx0, vx0)
            h = min(cy1, vy1) - max(cy0, vy0)
            if l > 0 and h > 0 and (l * h) >= tolerance * aire_c:
                double = True
                break
        if not double:
            out.append(cand)
    return out


def spans_de_page(page, langue: str = "fra", dpi: int = DPI_LECTURE,
                  double_lecture: bool = True) -> list[dict]:
    """Lit une page scannée et rend des spans conformes au contrat.

    Les coordonnées rendues sont en POINTS de la page (pas en pixels) : c'est
    l'unité du moteur PDF, et celle où les cadres se dessineront.
    """
    img, echelle = _image_de_page(page, dpi)
    gris = img.convert("L")

    mots = _mots_bruts(img, langue)
    if double_lecture:
        mots = _fusionner(mots, _mots_bruts(_pretraitee(img), langue))

    spans = []
    for m in mots:
        encre = _boite_encre(gris, m["boite"])
        if encre is None:
            continue                     # aucune encre : lecture de bruit
        x0, y0, x1, y1 = (v / echelle for v in encre)
        if x1 <= x0 or y1 <= y0:
            continue
        spans.append(span_depuis_mot(m["texte"], (x0, y0, x1, y1),
                                     confiance=m["conf"]))
    spans.sort(key=lambda s: (round(s["_base"], 1), s["bbox"][0]))
    return spans
