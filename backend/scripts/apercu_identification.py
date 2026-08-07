"""Aperçu de l'identification : les blocs de paragraphes, encadrés sur la page.

DEUX SOURCES DE MOTS, UN SEUL AVAL
------------------------------------
    --source tesseract   les mots lus par l'OCR (exige Docker)
    --source page        les mots relevés dans le PDF lui-même (partout)

La seconde n'est PAS un raccourci de confort : c'est un banc de mesure. Elle
fournit des mots dont la position est EXACTE, ce qui permet de juger le
REGROUPEMENT seul — sans que les erreurs de lecture de l'OCR ne viennent s'y
mêler. Si un bloc est mal formé avec des mots parfaits, le défaut est dans le
regroupement, pas dans la lecture.

L'aval est identique dans les deux cas (même contrat de spans, mêmes appels au
moteur PDF, même rendu), donc ce que montre `--source page` sur le
regroupement vaut aussi pour `--source tesseract`.

    venv/Scripts/python.exe scripts/apercu_identification.py entree.pdf sortie.pdf
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                                    # noqa: E402

from engines.ocr import apercu as apercu_debug                 # noqa: E402
from engines.ocr.engine import OCREngine                       # noqa: E402
from engines.ocr.spans import (RepertoireConfiance,            # noqa: E402
                               span_depuis_mot, valider_spans)


def spans_depuis_page_pdf(page) -> list[dict]:
    """Les MOTS du PDF, convertis au contrat OCR.

    `get_text("words")` rend un mot par entrée avec sa boîte — exactement la
    forme que rend `image_to_data`. On passe donc par la même porte
    (`span_depuis_mot`), et le contrat est le même.

    La confiance est fixée à 100 : ces mots ne sont pas lus, ils sont relevés.
    C'est honnête — on ne simule pas une incertitude qu'on n'a pas mesurée.
    """
    spans = []
    for x0, y0, x1, y1, mot, *_ in page.get_text("words"):
        if not (mot or "").strip():
            continue
        spans.append(span_depuis_mot(mot, (x0, y0, x1, y1), confiance=100.0))
    spans.sort(key=lambda s: (round(s["_base"], 1), s["bbox"][0]))
    return spans


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("entree")
    ap.add_argument("sortie")
    ap.add_argument("--source", choices=("page", "tesseract"), default="page")
    ap.add_argument("--langue", default="fra")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--pages", type=int, default=0,
                    help="0 = toutes")
    ap.add_argument("--sans-lignes", action="store_true",
                    help="ne pas dessiner le cadre de chaque ligne")
    args = ap.parse_args()

    moteur_ocr = OCREngine(langue=args.langue, dpi=args.dpi)
    if args.source == "tesseract":
        infos = moteur_ocr.rendre_apercu(
            args.entree, args.sortie, max_pages=args.pages,
            montrer_lignes=not args.sans_lignes)
        for p in infos["pages"]:
            print(f"  page {p['page']:>3} : {p['mots']:>5} mots, "
                  f"{p['lignes']:>4} lignes, {p['retenus']:>3} blocs")
        print(f"\nEcrit : {infos['sortie']}")
        return 0

    mise_en_page = moteur_ocr.mise_en_page
    doc = fitz.open(args.entree)
    try:
        total = len(doc) if args.pages <= 0 else min(args.pages, len(doc))
        for i in range(total):
            page = doc[i]
            spans = spans_depuis_page_pdf(page)
            manques = valider_spans(spans)
            if manques:
                print(f"  page {i+1} : spans NON CONFORMES {manques[:2]}")
                continue
            lignes = mise_en_page._group_text_lines(spans)
            blocs = mise_en_page._group_paragraphs(lignes)
            # Aucun tri a cette etape : on montre la detection BRUTE.
            compte = apercu_debug.dessiner(page, blocs, [], lignes,
                                           montrer_lignes=not args.sans_lignes)
            apercu_debug.legende(page)
            rep = RepertoireConfiance(spans)
            confs = rep.confiances_du_bloc(blocs[0]) if blocs else []
            print(f"  page {i+1:>3} : {len(spans):>5} mots, "
                  f"{compte['lignes']:>4} lignes, {compte['retenus']:>3} blocs"
                  + (f", confiance bloc 1 retrouvee sur {len(confs)} mots"
                     if confs else ""))
        if total < len(doc):
            doc.delete_pages(from_page=total, to_page=len(doc) - 1)
        dossier = os.path.dirname(os.path.abspath(args.sortie))
        if dossier:
            os.makedirs(dossier, exist_ok=True)
        doc.save(args.sortie, garbage=3, deflate=True)
        print(f"\nEcrit : {args.sortie}")
    finally:
        doc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
