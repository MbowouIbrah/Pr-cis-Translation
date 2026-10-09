"""Banc d'identité du rendu HYBRIDE (sans traduction).

Pour chaque page : on garde la page d'origine, on en retire le TEXTE seul
(redaction), on repose le même texte depuis le JSON d'extraction, puis on
compare au PDF d'origine. Arrêt à la première page qui s'écarte.

Contrôles par page (aucun seuil réglé sur un document) :
  1. RESIDU   : après redaction, plus aucun mot des zones retirées.
  2. TEXTE    : mêmes mots après repose (appariement texte + position).
  3. HORS TEXTE : pixels STRICTEMENT identiques hors des zones de texte.
  4. DANS TEXTE : écart d'encre mesuré et rapporté (jamais masqué).

Usage : hybride_identite.py <pdf> [--pages 1,5,9 | --n 32] [--dpi 144]
"""
import argparse
import unicodedata
import os
import sys

import fitz
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from engines.pdf.engine import PDFObjectEngine  # noqa: E402

POS_TOL = 0.5     # pt, tolérance d'appariement des mots (position)


def _runs(page_data):
    for el in page_data["elements"]:
        if el.get("type") == "paragraph":
            for ln in el.get("lines", []):
                for r in ln.get("runs", []):
                    yield r
        elif el.get("type") == "text_line":
            for r in el.get("runs", []):
                yield r


def _norm(c):
    """Équivalences de CODAGE seulement (ligature, trait d'union conditionnel) :
    le glyphe peint est le même, seule l'étiquette du texte copiable change."""
    return unicodedata.normalize("NFKC", c).replace("­", "-")


def _words(page):
    """Caractères (c, x, y d'ORIGINE) : indépendants des métriques de boîte
    de la police, contrairement à bbox de mot (ascendante/descendante)."""
    out = []
    lig = PDFObjectEngine._ligature_counts(page)
    for b in page.get_text("rawdict")["blocks"]:
        for ln in b.get("lines", []):
            for sp in ln["spans"]:
                chars = sp["chars"]
                org = PDFObjectEngine._ligature_origins(chars, lig)
                for ch, (ox, oy) in zip(chars, org):
                    if ch["c"].strip() and unicodedata.category(ch["c"]) != "Cc":
                        for c1 in _norm(ch["c"]):      # ligature = N lettres, 1 origine
                            out.append((c1, ox, oy, *ch["bbox"]))
    return out


def _match(orig, new):
    """Appariement mot à mot (texte égal, coin haut-gauche à POS_TOL)."""
    pool = {}
    for w in new:
        pool.setdefault(w[0], []).append(w)
    miss, worst = [], 0.0
    for t, x0, y0, *_ in orig:
        best, bi = None, None
        for i, c in enumerate(pool.get(t, [])):
            d = max(abs(c[1] - x0), abs(c[2] - y0))
            if best is None or d < best:
                best, bi = d, i
        if best is None or best > POS_TOL:
            miss.append((t, round(x0, 1), round(y0, 1), None if best is None else round(best, 2)))
        else:
            worst = max(worst, best)
            pool[t].pop(bi)
    extra = sum(len(v) for v in pool.values())
    return miss, extra, worst


def _pix(page, dpi):
    p = page.get_pixmap(dpi=dpi, alpha=False)
    return np.frombuffer(p.samples, np.uint8).reshape(p.h, p.w, p.n).astype(np.int16)


def _one_page(src, pno):
    """Page conservée en rouvrant le document et en ne gardant qu'elle :
    `insert_pdf` perd des attributs hérités du catalogue (rendu à ±1 niveau)."""
    d = fitz.open(src.name)
    d.select([pno])
    return d


def hybrid_page(src, pno, eng, dpi):
    page_src = src[pno]
    pd = eng.extract_page_data(src, pno, page_src, embed_images=True)
    out = _one_page(src, pno)
    pg = out[0]

    # 1) retrait du texte seul, zone = encre de chaque run extrait
    n = 0
    for r in _runs(pd):
        bb = r.get("bbox")
        if bb and (bb[2] - bb[0]) > 0 and (bb[3] - bb[1]) > 0:
            pg.add_redact_annot(fitz.Rect(bb), fill=False)
            n += 1
    pg.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                        graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                        text=fitz.PDF_REDACT_TEXT_REMOVE)
    # Extraire le texte d'une page puis la RENDRE fait dériver ses images d'un
    # niveau (cache MuPDF) : le résidu se lit sur une copie jetable.
    residue = _words(fitz.open("pdf", out.tobytes())[0])

    # 1b) texte INVISIBLE à l'origine : si retirer un run ne change aucun pixel
    #     de sa zone, il n'était pas visible (masqué sous un aplat, blanc sur
    #     blanc…) ; on ne le repose pas par-dessus, il reste dans la couche texte.
    a, b = _pix(src[pno], dpi), _pix(pg, dpi)
    sc = dpi / 72.0
    for r in _runs(pd):
        bb = r.get("bbox")
        if not bb:
            continue
        ys = slice(max(0, int(bb[1] * sc) - 1), int(bb[3] * sc) + 2)
        xs = slice(max(0, int(bb[0] * sc) - 1), int(bb[2] * sc) + 2)
        r["hidden"] = bool(np.array_equal(a[ys, xs], b[ys, xs]))

    # 2) repose du même texte (JSON = seule source du texte)
    for el in pd["elements"]:
        if el.get("type") == "paragraph":
            eng._draw_paragraph(pg, el)
        elif el.get("type") == "text_line":
            eng._draw_text_line(pg, el)
    eng._fix_variant_tounicode(out)
    return out, pg, residue, n


def check_page(src, pno, eng, dpi):
    orig_words = _words(src[pno])
    data_fonts = eng._fonts_src
    eng._build_fonts({"fonts": data_fonts})
    out, pg, residue, nruns = hybrid_page(src, pno, eng, dpi)
    a, b = _pix(src[pno], dpi), _pix(pg, dpi)    # pixels AVANT toute extraction
    miss, extra, worst = _match(orig_words, _words(pg))
    scale = dpi / 72.0
    mask = np.zeros(a.shape[:2], bool)
    for _, _ox, _oy, x0, y0, x1, y1 in orig_words:
        # marge de 2 pt : le crochet d'un « f » déborde de sa boîte de ~1,7 pt
        mask[max(0, int((y0 - 2) * scale)):int((y1 + 2) * scale) + 1,
             max(0, int((x0 - 2) * scale)):int((x1 + 2) * scale) + 1] = True
    diff = np.abs(a - b).max(axis=2)
    # Plancher propre à PyMuPDF : une rédaction VIDE réécrit le flux et arrondit
    # quelques tracés (1-8 niveaux sur 2-4 px). Hors texte, on exige l'identité
    # avec cette référence, pas avec l'original brut.
    nop = _one_page(src, pno)
    nop[0].add_redact_annot(fitz.Rect(1, 1, 3, 3), fill=False)
    nop[0].apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                            graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                            text=fitz.PDF_REDACT_TEXT_REMOVE)
    base = _pix(nop[0], dpi)
    outside = int((np.abs(base - b).max(axis=2)[~mask] > 0).sum())
    ga, gb = a.min(axis=2) < 128, b.min(axis=2) < 128
    ink = int(ga[mask].sum())
    inside_n = int((ga ^ gb)[mask].sum())      # encre XOR : surface divergente
    # mupdf pose chaque glyphe sur une grille de 1/4 de pixel : deux rendus
    # d'un même glyphe diffèrent d'au plus 255/4 niveaux sans rien déplacer.
    # Au-delà de 64 niveaux, c'est un vrai écart.
    big = int((diff[mask] > 64).sum())
    res = {
        "page": pno + 1, "runs": nruns, "mots": len(orig_words),
        "residu": len(residue), "manquants": len(miss), "en_trop": extra,
        "ecart_max_pt": round(worst, 2), "pix_hors_texte": outside,
        "pix_dans_texte_diff": inside_n, "pix_ecart_reel": big,
        "pix_encre": ink,
    }
    res["ok"] = (res["residu"] == 0 and not miss and extra == 0
                 and outside == 0 and big == 0)
    res["_miss"] = miss[:5]
    return res, out


def pick(n_pages, n):
    if n >= n_pages:
        return list(range(n_pages))
    return sorted({round(i * (n_pages - 1) / (n - 1)) for i in range(n)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages")
    ap.add_argument("--n", type=int, default=32)
    ap.add_argument("--dpi", type=int, default=144)
    ap.add_argument("--keep-going", action="store_true")
    ap.add_argument("--save-dir")
    a = ap.parse_args()

    src = fitz.open(a.pdf)
    eng = PDFObjectEngine()
    eng._fonts_src = eng._extract_fonts(src)
    pages = ([int(x) - 1 for x in a.pages.split(",")] if a.pages
             else pick(len(src), a.n))
    bad = 0
    for pno in pages:
        try:
            r, out = check_page(src, pno, eng, a.dpi)
        except Exception as e:  # noqa: BLE001
            print(f"p{pno+1}: ERREUR {type(e).__name__}: {e}")
            bad += 1
            if not a.keep_going:
                break
            continue
        print({k: v for k, v in r.items() if not k.startswith("_")},
              "OK" if r["ok"] else "ECART " + str(r["_miss"]))
        if a.save_dir:
            os.makedirs(a.save_dir, exist_ok=True)
            out.save(os.path.join(a.save_dir, f"p{pno+1}.pdf"))
        if not r["ok"]:
            bad += 1
            if not a.keep_going:
                break
    print("RESULTAT:", "tout identique" if not bad else f"{bad} page(s) en écart")


if __name__ == "__main__":
    main()
