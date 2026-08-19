"""Fabriquer un SCAN a partir d'un document natif, et sa VERITE de reference.

POURQUOI FABRIQUER PLUTOT QUE CHERCHER UN VRAI SCAN
----------------------------------------------------
Un vrai scan ne dit pas ce qu'il contient : on ne peut le juger qu'a l'oeil,
et l'oeil ne compte pas 1400 mots. En partant d'un document NATIF, on connait
le texte, sa position, sa police, son corps et sa graisse -- exactement les
proprietes qu'on veut restituer. L'ecart devient MESURABLE au lieu d'etre
apprecie.

C'est le meme principe que `--source page` du banc d'apercu : donner au
moteur des donnees dont on possede la verite.

⚠ LE SCAN DOIT RESSEMBLER A UN SCAN. Rendre la page en image et s'arreter la
fabriquerait un cas trop facile : pas de bruit de capteur, pas de compression
JPEG, pas d'inclinaison. Le moteur paraitrait meilleur qu'il n'est, et les
seuils qu'on calerait dessus seraient faux sur un vrai document. On ajoute
donc les trois degradations d'un scan de bureau, chacune reglable :

    · JPEG          la compression d'un scanner (qualite 85 par defaut)
    · bruit         le grain du capteur (ecart-type en niveaux de gris)
    · inclinaison   la feuille jamais parfaitement droite (en degres)

La verite, elle, est relevee sur le document NATIF avant toute degradation.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                                    # noqa: E402


def verite_de_page(page) -> list[dict]:
    """Ce que la page contient VRAIMENT : un enregistrement par MOT.

    ⚠ AU MOT, ET NON AU SPAN, et ce n'est pas un detail de forme. Un span PDF
    regroupe tout ce qui partage une mise en forme : `'Preface by Jake
    Klamka,'` en est UN seul. L'OCR, lui, rend un enregistrement par mot. Une
    verite au span comptait donc 3 « intrus » pour une lecture PARFAITE, et
    le banc mesurait sa propre granularite au lieu du moteur.

    On decoupe donc chaque span en mots (`get_text("words")` donne les boites
    exactes) et on reporte sur chacun la mise en forme de son span. Le
    rattachement se fait par recouvrement : un mot appartient au span qui le
    contient.

    On garde tout ce qu'on voudra restituer plus tard -- pas seulement le
    texte, mais la police, le corps, la graisse et l'italique. La mise en
    forme fait partie de l'identite du document.
    """
    spans = []
    for bloc in page.get_text("dict")["blocks"]:
        if bloc.get("type") != 0:                    # 0 = texte
            continue
        for ligne in bloc.get("lines", []):
            for s in ligne.get("spans", []):
                if (s.get("text") or "").strip():
                    spans.append(s)

    def mise_en_forme(bb):
        """Le span qui recouvre le mieux cette boite de mot."""
        best, score = None, 0.0
        for s in spans:
            sb = s["bbox"]
            l = min(bb[2], sb[2]) - max(bb[0], sb[0])
            h = min(bb[3], sb[3]) - max(bb[1], sb[1])
            if l <= 0 or h <= 0:
                continue
            aire = l * h / max(1e-6, (bb[2] - bb[0]) * (bb[3] - bb[1]))
            if aire > score:
                best, score = s, aire
        return best

    out = []
    for x0, y0, x1, y1, mot, *_ in page.get_text("words"):
        mot = (mot or "").strip()
        if not mot:
            continue
        bb = [round(v, 2) for v in (x0, y0, x1, y1)]
        s = mise_en_forme(bb)
        out.append({
            "texte": mot,
            "bbox": bb,
            "police": s.get("font", "") if s else "",
            "corps": round(s.get("size", 0.0), 2) if s else 0.0,
            "gras": bool(s.get("flags", 0) & 16) if s else False,
            "italique": bool(s.get("flags", 0) & 2) if s else False,
            "couleur": s.get("color", 0) if s else 0,
        })
    return out


def degrader(img, jpeg: int, bruit: float, inclinaison: float):
    """Les trois degradations d'un scan de bureau. Voir l'en-tete du module."""
    import io

    import numpy as np
    from PIL import Image

    if inclinaison:
        # `expand=False` : une page scannee de travers est ROGNEE par les
        # bords du plateau, elle ne s'agrandit pas.
        img = img.rotate(inclinaison, resample=Image.BICUBIC,
                         fillcolor=(255, 255, 255))
    if bruit > 0:
        a = np.asarray(img).astype(np.float32)
        a += np.random.normal(0.0, bruit, a.shape)
        img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    if jpeg < 100:
        tampon = io.BytesIO()
        img.save(tampon, format="JPEG", quality=jpeg)
        tampon.seek(0)
        img = Image.open(tampon).convert("RGB")
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("entree")
    ap.add_argument("sortie")
    ap.add_argument("--pages", type=int, default=5)
    ap.add_argument("--depuis", type=int, default=1,
                    help="premiere page a prendre (1 = la premiere)")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--jpeg", type=int, default=85)
    ap.add_argument("--bruit", type=float, default=4.0)
    ap.add_argument("--inclinaison", type=float, default=0.0)
    ap.add_argument("--graine", type=int, default=20260810,
                    help="pour que le scan soit REPRODUCTIBLE")
    args = ap.parse_args()

    import numpy as np
    from PIL import Image
    np.random.seed(args.graine)

    src = fitz.open(args.entree)
    out = fitz.open()
    verite = []
    debut = max(0, args.depuis - 1)
    fin = min(debut + args.pages, len(src))
    for i in range(debut, fin):
        page = src[i]
        verite.append({"page": len(verite) + 1, "spans": verite_de_page(page)})
        ech = args.dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(ech, ech), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        img = degrader(img, args.jpeg, args.bruit, args.inclinaison)

        neuve = out.new_page(width=page.rect.width, height=page.rect.height)
        tampon = __import__("io").BytesIO()
        img.save(tampon, format="JPEG", quality=args.jpeg)
        neuve.insert_image(neuve.rect, stream=tampon.getvalue())

    dossier = os.path.dirname(os.path.abspath(args.sortie))
    if dossier:
        os.makedirs(dossier, exist_ok=True)
    out.save(args.sortie, garbage=3, deflate=True)
    chemin_verite = os.path.splitext(args.sortie)[0] + "_verite.json"
    with open(chemin_verite, "w", encoding="utf-8") as f:
        json.dump(verite, f, ensure_ascii=False, indent=1)

    total = sum(len(p["spans"]) for p in verite)
    print(f"{fin - debut} pages -> {args.sortie}")
    print(f"  verite : {total} spans -> {chemin_verite}")
    print(f"  degradation : jpeg={args.jpeg} bruit={args.bruit} "
          f"inclinaison={args.inclinaison} deg")
    out.close()
    src.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
