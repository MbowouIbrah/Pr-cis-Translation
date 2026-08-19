"""Le BANC : ce que le moteur retrouve d'un document dont on connait la verite.

CE QU'IL MESURE, ET POURQUOI CES MESURES-LA
---------------------------------------------
Un compte de mots ne dit rien de la qualite d'un rendu. On mesure donc les
quatre proprietes qui font l'IDENTITE d'un document, chacune separement :

    TEXTE       le mot est-il lu, et lu juste ?
    POSITION    est-il au bon endroit ? (le rendu se joue au point pres)
    CORPS       la taille est-elle la bonne ? (un titre reste un titre)
    GRAISSE     le gras est-il vu ? (il porte la structure)

Les quatre sont rendues separement parce qu'elles se corrigent separement :
un texte parfait mal positionne et un texte fautif bien place ne demandent
pas le meme travail.

⚠ ON APPARIE PAR LA POSITION, JAMAIS PAR LE TEXTE. Apparier deux mots parce
qu'ils s'ecrivent pareil ferait disparaitre du calcul les mots mal lus --
c'est-a-dire exactement ce qu'on veut compter. On apparie donc sur le
recouvrement des boites, puis on compare les textes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                                    # noqa: E402

from engines.ocr import audit                                  # noqa: E402
from engines.ocr.engine import OCREngine                       # noqa: E402

#: Part de recouvrement a partir de laquelle deux boites designent le meme
#: mot. 0,3 : assez pour apparier malgre une mesure d'encre approximative,
#: assez peu pour ne pas confondre deux mots voisins.
_MEME_MOT = 0.3


def _plat(t: str) -> str:
    """Le texte reduit a sa substance, pour comparer deux lectures.

    Accents et ponctuation retires : `'l'arret'` et `'l'arrêt'` sont la meme
    lecture pour ce qui nous interesse ici, et compter l'accent comme une
    erreur noierait les vraies.
    """
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^0-9a-z]", "", t.lower())


def _recouvre(a, b) -> float:
    l = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if l <= 0 or h <= 0:
        return 0.0
    aire_a = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    aire_b = max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))
    return (l * h) / min(aire_a, aire_b)


def mesurer_page(attendus, lus) -> dict:
    """Compare une page de verite a ce que le moteur en a lu."""
    libres = list(range(len(lus)))
    exacts = approx = manques = 0
    d_pos, d_corps = [], []
    gras_vus = gras_total = 0
    for a in attendus:
        ba = a["bbox"]
        best, bscore = None, 0.0
        for k in libres:
            s = _recouvre(ba, lus[k]["bbox"])
            if s > bscore:
                best, bscore = k, s
        if best is None or bscore < _MEME_MOT:
            manques += 1
            continue
        libres.remove(best)
        lu = lus[best]
        pa, pl = _plat(a["texte"]), _plat(lu["text"])
        if pa == pl:
            exacts += 1
        elif pa and (pa in pl or pl in pa):
            approx += 1
        else:
            manques += 1
        bl = lu["bbox"]
        d_pos.append(max(abs(ba[0] - bl[0]), abs(ba[1] - bl[1])))
        if a["corps"] > 0:
            # `_corps` et non `size` : `size` est la hauteur d'encre, échelle
            # relative du regroupement ; `_corps` est le corps typographique
            # estimé, celui qu'un rendu doit reproduire. Voir `spans.py`.
            estime = lu.get("_corps") or lu.get("size", 0.0)
            d_corps.append(abs(estime - a["corps"]) / a["corps"])
        if a["gras"]:
            gras_total += 1
            if lu.get("_gras") or lu.get("bold"):
                gras_vus += 1
    return {
        "attendus": len(attendus), "lus": len(lus),
        "exacts": exacts, "approx": approx, "manques": manques,
        "intrus": len(libres),
        "pos": d_pos, "corps": d_corps,
        "gras_vus": gras_vus, "gras_total": gras_total,
    }


def _med(v):
    return sorted(v)[len(v) // 2] if v else 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scan")
    ap.add_argument("--verite", default="")
    ap.add_argument("--langue", default="fra")
    ap.add_argument("--dpi", type=int, default=300)
    args = ap.parse_args()

    chemin = args.verite or os.path.splitext(args.scan)[0] + "_verite.json"
    with open(chemin, encoding="utf-8") as f:
        verite = json.load(f)

    moteur = OCREngine(langue=args.langue, dpi=args.dpi)
    doc = fitz.open(args.scan)
    tot = {k: 0 for k in ("attendus", "lus", "exacts", "approx", "manques",
                          "intrus", "gras_vus", "gras_total")}
    pos, corps, defauts = [], [], 0
    print(f"=== {os.path.basename(args.scan)}")
    for i, page_verite in enumerate(verite):
        if i >= len(doc):
            break
        vu = moteur.analyser_page(doc[i])
        m = mesurer_page(page_verite["spans"], vu["spans"])
        defauts += len(audit.auditer(vu["retenus"])["defauts"])
        for k in tot:
            tot[k] += m[k]
        pos += m["pos"]
        corps += m["corps"]
        att = max(1, m["attendus"])
        print(f"  p{i+1}: {m['exacts']:>4}/{m['attendus']:<4} exacts "
              f"({100*m['exacts']/att:5.1f} %)  approx={m['approx']:<3} "
              f"manques={m['manques']:<3} intrus={m['intrus']:<3} "
              f"blocs={len(vu['retenus'])}")
    doc.close()

    att = max(1, tot["attendus"])
    print(f"\n  TEXTE     {tot['exacts']}/{tot['attendus']} exacts "
          f"({100*tot['exacts']/att:.1f} %), "
          f"{tot['approx']} approchants ({100*(tot['exacts']+tot['approx'])/att:.1f} % cumules)")
    print(f"            {tot['manques']} manques, {tot['intrus']} intrus")
    print(f"  POSITION  ecart median {_med(pos):.2f} pt, "
          f"90e centile {sorted(pos)[int(0.9*len(pos))] if pos else 0:.2f} pt")
    print(f"  CORPS     ecart median {100*_med(corps):.1f} %")
    g = max(1, tot["gras_total"])
    print(f"  GRAISSE   {tot['gras_vus']}/{tot['gras_total']} gras retrouves "
          f"({100*tot['gras_vus']/g:.1f} %)")
    print(f"  AUDIT     {defauts} defauts de structure")
    return 0


if __name__ == "__main__":
    sys.exit(main())
