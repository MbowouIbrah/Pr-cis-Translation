"""RELIRE une ligne isolée pour défaire les mots que Tesseract a FONDUS.

LE DÉFAUT, SIGNALÉ À L'ŒIL sur le sommaire
--------------------------------------------
La colonne des paginations ressortait en `'532'`, `'59à80'`, `'81à96'`,
`'(07à52'` — plusieurs nombres soudés en un seul « mot ». Or ces lignes sont
parfaitement lisibles : c'est la SEGMENTATION de la page entière qui échoue,
pas la reconnaissance des caractères.

Vérifié en donnant la bande seule à Tesseract, quel que soit son `--psm` :

    '(07à52' (29 %)  ->  '07' 'à' '52'   à 95-96 %
    '81à96'  (80 %)  ->  '81' 'à' '96'   à 93-96 %

`--psm 7` annonce « une seule ligne de texte », ce qui est vrai par
construction ici : on lui donne une ligne DÉJÀ délimitée par le regroupement.
Il n'a plus à deviner la mise en page, seulement à lire.

CE QU'ON PREND, ET CE QU'ON NE PREND PAS
------------------------------------------
On ne remplace **que le découpage**, jamais la position verticale : chaque
mot relu est reposé dans la HAUTEUR et sur la BASELINE du span qu'il
remplace. C'est la leçon payée deux fois — un mot venu d'une autre lecture
apporte sa boîte et sa clé de ligne, donc il déplace la baseline de toute sa
rangée et disloque des blocs corrects ailleurs (mesuré : 15 -> 34 défauts).

    en X   le RELU     il a su séparer ce que l'autre voyait fondu
    en Y   l'ANCIEN    sa hauteur porte la baseline de la rangée

DEUX PORTES, ET IL EN FAUT DEUX
---------------------------------
Un span n'est éclaté que si la relecture est SÛRE (≥ 85 %) et que l'une des
deux conditions tient :

  · MÊME CONTENU, mieux découpé — `'81à96'` -> `'81' 'à' '96'`. Lu à 80 %, la
    relecture donne 93-96 % : l'écart (13-16) ne suffirait pas, mais le
    découpage prouve tout. **La confiance n'est pas le bon signal ici** : un
    span fondu peut être lu avec assurance ;

  · CONTENU DIFFÉRENT — `'532'` -> `'53' 'à' '68'`. Le découpage ne prouve
    plus rien puisque le texte change ; on exige alors un écart de confiance
    FRANC.

MESURE (3 pages du Code de la Route, vrai scan)
-------------------------------------------------
    paginations correctes   8/12  ->  **9/12**
    défauts de l'audit         5  ->  **5**   (inchangé)
    spans éclatés                       4

Stable de 15 à 30 pour l'écart de confiance. Les trois paginations qui
résistent sont hors de portée de cette technique, et c'est mesuré :

  · `'532'` est une mauvaise LECTURE (aucune passe ne lit mieux, et les
    pixels de `'53 à 68'` sont identiques à ceux de `'07 à 52'`, qui passe) ;
  · `'187 à 200'` et `'201 à 212'` ont leurs nombres justes — seul le `'à'`
    manque, écarté en amont parce qu'il fait UN caractère. Abaisser
    `_CARS_MIN_COULEUR` de 2 à 1 pour le récupérer a été mesuré : audit
    **5 -> 16**, sans gagner un seul nombre.

COÛT
-----
Un appel Tesseract PAR LIGNE, en plus des trois passes de page. Sur ces
3 pages : ~300 appels. C'est le prix d'une étape d'identification qu'on
regarde à l'œil ; à généraliser, il faudra la réserver aux lignes suspectes.
"""
from __future__ import annotations

import logging

from engines.ocr.spans import span_depuis_mot

logger = logging.getLogger(__name__)

#: Confiance minimale exigée de CHAQUE mot relu pour qu'on éclate un span.
#:
#: 85 : la relecture doit être franche. En dessous, on remplacerait un
#: découpage douteux par un autre — mesuré, le gain disparaît.
_SUR_MIN = 85.0

#: Écart de confiance exigé quand le CONTENU CHANGE (`'532'` -> `'53 à 68'`).
#: Le découpage ne prouve plus rien, il faut que la relecture soit nettement
#: meilleure. Balayé 15 / 20 / 25 / 30 : 9/12 partout, palier franc.
_ECART_CONTENU_DIFFERENT = 20.0

#: Part de sa largeur qu'un mot relu doit avoir DANS le span qu'il remplace.
#: 0,6 : plus de la moitié, sans exiger l'inclusion totale — les deux
#: lectures ne posent pas leurs bords au même pixel.
_DANS_LE_SPAN = 0.6

#: Marge ajoutée autour de la ligne avant de la découper, en points. Sans
#: elle, les jambages et accents sont rognés et la relecture se dégrade.
_MARGE_LIGNE = 2.0

#: Confiance minimale d'un mot pour être seulement pris en compte.
_CONF_PLANCHER = 60.0


def _mots_relus(image, boite, echelle, langue):
    """Les mots que Tesseract lit dans cette ligne SEULE, en points."""
    import pytesseract

    x0, y0, x1, y1 = boite
    coupe = (max(0, int((x0 - _MARGE_LIGNE) * echelle)),
             max(0, int((y0 - _MARGE_LIGNE) * echelle)),
             int((x1 + _MARGE_LIGNE) * echelle),
             int((y1 + _MARGE_LIGNE) * echelle))
    if coupe[2] - coupe[0] < 8 or coupe[3] - coupe[1] < 8:
        return []
    try:
        d = pytesseract.image_to_data(
            image.crop(coupe), lang=langue, config="--psm 7",
            output_type=pytesseract.Output.DICT)
    except Exception as e:                        # une ligne illisible n'est
        logger.debug("relecture de ligne impossible : %s", e)
        return []                                 # pas une erreur fatale
    out = []
    for k, texte in enumerate(d["text"]):
        texte = (texte or "").strip()
        if not texte:
            continue
        conf = float(d["conf"][k])
        if conf < _CONF_PLANCHER:
            continue
        gx0 = coupe[0] / echelle + d["left"][k] / echelle
        out.append((gx0, gx0 + d["width"][k] / echelle, texte, conf))
    return out


def _sans_ponctuation(texte: str) -> str:
    """Le texte réduit à ses lettres et chiffres, pour comparer deux lectures.

    `'81à96'` et `'81' 'à' '96'` doivent se reconnaître comme le même contenu
    malgré l'espacement ; `'532'` et `'53à68'` doivent se distinguer.
    """
    return "".join(c for c in texte.lower() if c.isalnum())


def defaire_les_mots_fondus(spans, lignes, image, echelle, langue="fra"):
    """Rend les spans, ceux qui étaient FONDUS ayant été éclatés.

    `lignes` vient de `_group_text_lines` : on relit une bande déjà délimitée,
    ce qui est précisément ce que `--psm 7` sait faire.

    Ne touche à rien si la relecture échoue ou n'apprend rien — l'absence de
    Tesseract, un `crop` vide ou une ligne illisible laissent les spans
    intacts.
    """
    if not spans or not lignes or image is None:
        return spans

    morts, neufs = set(), []
    for ligne in lignes:
        boite = ligne.get("bbox")
        if not boite or len(boite) < 4:
            continue
        relus = _mots_relus(image, boite, echelle, langue)
        if len(relus) < 2:
            continue
        dedans = [s for s in spans
                  if min(s["bbox"][3], boite[3]) - max(s["bbox"][1], boite[1]) > 0
                  and s["bbox"][0] >= boite[0] - 0.5
                  and s["bbox"][2] <= boite[2] + 0.5]
        for s in dedans:
            if id(s) in morts:
                continue
            sx0, sx1 = s["bbox"][0], s["bbox"][2]
            pris = [r for r in relus
                    if min(r[1], sx1) - max(r[0], sx0)
                    > _DANS_LE_SPAN * (r[1] - r[0])]
            # UN span pour PLUSIEURS mots relus : c'est la signature du mot
            # fondu, et c'est la seule situation qu'on traite ici.
            if len(pris) < 2:
                continue
            sur = min(r[3] for r in pris)
            if sur < _SUR_MIN:
                continue
            ancien = (s.get("text") or "").strip()
            meme = (_sans_ponctuation("".join(r[2] for r in pris))
                    == _sans_ponctuation(ancien))
            if not meme:
                # Le contenu change : le découpage ne prouve plus rien.
                if sur - (s.get("_conf") or 0.0) < _ECART_CONTENU_DIFFERENT:
                    continue
            morts.add(id(s))
            for rx0, rx1, texte, conf in pris:
                # EN X LE RELU, EN Y L'ANCIEN — voir l'en-tête du module.
                neuf = span_depuis_mot(texte, (rx0, s["bbox"][1],
                                               rx1, s["bbox"][3]),
                                       confiance=conf)
                neuf["_base"] = s["_base"]
                neuf["_ligne_ocr"] = s.get("_ligne_ocr")
                neufs.append(neuf)

    if not morts:
        return spans
    out = [s for s in spans if id(s) not in morts] + neufs
    out.sort(key=lambda s: (round(s["_base"], 1), s["bbox"][0]))
    return out
