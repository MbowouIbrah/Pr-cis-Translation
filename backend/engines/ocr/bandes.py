"""RELIRE la page par BANDES VERTICALES, pour ce que la page entière cache.

LE DÉFAUT, SIGNALÉ À L'ŒIL sur deux documents indépendants
------------------------------------------------------------
Une colonne étroite en marge — les paginations d'un sommaire — est
entièrement ignorée par Tesseract quand on lui donne la page complète :

    mv21 page 5    '45' '45' '45' '46' '47' ... 55 nombres, AUCUN lu
    Code de la Route, sommaire : le même défaut, déjà traité au cas par cas

Ce n'est ni la reconnaissance ni la ligne qui échoue. Donnée SEULE, la
colonne se lit parfaitement :

    la colonne entière, `--psm 6`   ->  '45\n45\n45\n45\n46\n47\n...'
    la moitié gauche,   `--psm 3`   ->  la même chose, plus le reste

C'est la SEGMENTATION de la page qui décide qu'une colonne étroite en marge
n'est pas du texte. On lui donne donc moins de page à la fois : sur une
moitié, la colonne pèse assez pour être vue.

CE QU'ON AJOUTE, ET CE QU'ON NE TOUCHE PAS
--------------------------------------------
On **complète**, on ne remplace jamais : tout mot relu qui recouvre un span
déjà lu est ignoré. La géométrie existante n'est donc pas modifiée d'un
point — c'est la leçon payée trois fois sur ce moteur, un gain de texte qui
se paye en structure n'est pas un gain.

Mesuré sur 20 pages (deux documents, 5687 mots de vérité) :

    mv21   +50 vrais mots, **0 bruit**, audit 20 -> 20 (inchangé)
    DSH     +1 vrai mot,   **0 bruit**, audit  7 ->  7 (inchangé)

UN SEUL CARACTÈRE NE PASSE PAS, et c'est mesuré : sans ce filtre, DSH gagnait
3 vrais mots pour **6 bruits** (`'»'`, `'A'`, `'a'`, `'4'`), tous sur une
image de couverture. Les vrais gains sont des nombres de deux chiffres. Le
filtre coûte 5 gains sur mv21 et supprime tout le bruit : c'est le bon
échange, vu l'asymétrie des erreurs (`tri.py`) — un mot manquant reste
lisible en langue source, un faux mot fait effacer du dessin.

COÛT
-----
Deux appels Tesseract par page, en plus des trois passes. C'est le prix d'une
étape d'identification qu'on regarde à l'œil.
"""
from __future__ import annotations

import logging

from engines.ocr.spans import span_depuis_mot

logger = logging.getLogger(__name__)

#: Recouvrement à partir duquel un mot relu désigne un span DÉJÀ lu, et n'est
#: donc pas ajouté. 0,3 : assez pour reconnaître le même mot malgré deux
#: mesures d'encre différentes, assez peu pour ne pas confondre deux voisins.
_DEJA_LU = 0.3

#: Longueur minimale d'un mot ajouté par cette relecture.
#:
#: 2, et c'est MESURÉ : sans ce filtre, DSH gagnait 3 vrais mots pour 6 bruits
#: (`'»'`, `'A'`, `'a'`, `'4'`), tous sur une image de couverture. Le filtre
#: coûte 5 gains sur mv21 et supprime **tout** le bruit des deux documents.
_CARS_MIN = 2

#: Confiance minimale d'un mot relu.
_CONF_MIN = 60.0


def _mots_de_bande(image, x0, x1, hauteur, echelle, langue):
    """Les mots lus dans une bande verticale, en points de la page."""
    import pytesseract

    coupe = (max(0, int(x0 * echelle)), 0,
             int(x1 * echelle), int(hauteur * echelle))
    if coupe[2] - coupe[0] < 8 or coupe[3] - coupe[1] < 8:
        return []
    try:
        d = pytesseract.image_to_data(
            image.crop(coupe), lang=langue, config="--psm 3",
            output_type=pytesseract.Output.DICT)
    except Exception as e:
        logger.debug("relecture de bande impossible : %s", e)
        return []
    out = []
    for k, texte in enumerate(d["text"]):
        texte = (texte or "").strip()
        if len(texte) < _CARS_MIN:
            continue
        conf = float(d["conf"][k])
        if conf < _CONF_MIN:
            continue
        gx = x0 + d["left"][k] / echelle
        gy = d["top"][k] / echelle
        out.append((gx, gy, gx + d["width"][k] / echelle,
                    gy + d["height"][k] / echelle, texte, conf))
    return out


def _recouvre(a, b) -> float:
    l = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if l <= 0 or h <= 0:
        return 0.0
    aire_a = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    aire_b = max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))
    return (l * h) / min(aire_a, aire_b)


def completer_par_bandes(spans, image, echelle, largeur, hauteur,
                         langue="fra"):
    """Rend les spans, complétés de ce que les demi-pages révèlent.

    N'enlève ni ne déplace jamais un span existant : voir l'en-tête.
    """
    if image is None or largeur <= 0 or hauteur <= 0:
        return spans

    neufs = []
    for x0, x1 in ((0.0, largeur / 2.0), (largeur / 2.0, largeur)):
        for bx0, by0, bx1, by1, texte, conf in _mots_de_bande(
                image, x0, x1, hauteur, echelle, langue):
            bb = (bx0, by0, bx1, by1)
            if any(_recouvre(bb, s["bbox"]) >= _DEJA_LU for s in spans):
                continue
            if any(_recouvre(bb, n["bbox"]) >= _DEJA_LU for n in neufs):
                continue          # les deux bandes se rejoignent au milieu
            neufs.append(span_depuis_mot(texte, bb, confiance=conf))

    if not neufs:
        return spans
    out = list(spans) + neufs
    out.sort(key=lambda s: (round(s["_base"], 1), s["bbox"][0]))
    return out
