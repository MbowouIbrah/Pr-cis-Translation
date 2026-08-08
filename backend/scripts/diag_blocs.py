"""Diagnostic : la geometrie EXACTE de chaque bloc, et l'audit dessus.

Sert a examiner un defaut signale a l'oeil : on veut les chiffres des blocs
mis en cause, pas une impression.

    python backend/scripts/diag_blocs.py doc.pdf --pages 3 [--bloc 40 41]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                                    # noqa: E402

from engines.ocr import audit as audit_mod                     # noqa: E402
from engines.ocr.engine import OCREngine                       # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("entree")
    ap.add_argument("--langue", default="fra")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--pages", type=int, default=0)
    ap.add_argument("--page", type=int, default=0, help="detailler cette page")
    ap.add_argument("--bloc", type=int, nargs="*", default=[])
    args = ap.parse_args()

    moteur = OCREngine(langue=args.langue, dpi=args.dpi)
    doc = fitz.open(args.entree)
    total = len(doc) if args.pages <= 0 else min(args.pages, len(doc))
    for i in range(total):
        res = moteur.analyser_page(doc[i])
        blocs = res["retenus"]
        rap = audit_mod.auditer(blocs)
        print(f"\n=== page {i+1} : {len(blocs)} blocs, "
              f"{rap['total']} defauts, ligne={rap['hauteur_ligne']:.2f} pt")
        if args.page and args.page != i + 1:
            continue
        for k, b in enumerate(blocs):
            if args.bloc and k not in args.bloc:
                continue
            bb = b["bbox"]
            enc = audit_mod._boite_encre(b)
            nl = len(b.get("lines") or [])
            print(f"  #{k:<3} bbox=({bb[0]:7.2f},{bb[1]:7.2f},"
                  f"{bb[2]:7.2f},{bb[3]:7.2f})  {nl}L")
            if enc:
                print(f"       encre=({enc[0]:7.2f},{enc[1]:7.2f},"
                      f"{enc[2]:7.2f},{enc[3]:7.2f})")
            print(f"       {(b.get('text') or '')[:90]!r}")
            for li, lg in enumerate(b.get("lines") or []):
                lb = lg.get("bbox")
                print(f"         L{li} ({lb[0]:7.2f},{lb[1]:7.2f},"
                      f"{lb[2]:7.2f},{lb[3]:7.2f}) "
                      f"{(lg.get('text') or '')[:60]!r}")
    doc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
