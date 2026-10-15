"""Images PAR RÉFÉRENCE : le JSON ne garde que le `xref`, le rendu relit l'original.

Avant, chaque `pages*.json` embarquait les octets des images en base64 (94 %
du fichier mesuré), recopiés pour chaque langue et chaque plage de pages.
Ce que ce test défend :
  - le JSON ne contient plus aucun octet d'image ;
  - le rendu est IDENTIQUE AU PIXEL à celui de l'ancien mode (base64) ;
  - un ancien cache (base64) est allégé à la réouverture, rendu inchangé.

PDF SYNTHÉTIQUE : image avec transparence (SMask), JPEG, image posée deux
fois sur une page et réutilisée sur une autre.

Exécution :  cd backend && venv/Scripts/python.exe tests/test_images_par_reference.py
"""
from __future__ import annotations
import json
import os
import shutil
import sys
import tempfile

import racine  # noqa: F401  -- met backend/ sur le chemin

import fitz                                                    # noqa: E402
from engines.pdf.engine import PDFObjectEngine                 # noqa: E402
from engines.pdf import stream                                 # noqa: E402


def _pdf_synthetique(path: str) -> None:
    alpha = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 30), True)
    for x in range(40):
        for y in range(30):
            alpha.set_pixel(x, y, (200, 40 + 5 * y, 6 * x, 255 if x < 25 else 60))
    opaque = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 32, 32), False)
    for x in range(32):
        for y in range(32):
            opaque.set_pixel(x, y, ((x * 8) % 256, (y * 8) % 256, 120))
    jpeg = opaque.tobytes("jpeg")

    doc = fitz.open()
    p1 = doc.new_page(width=300, height=300)
    p1.draw_rect(fitz.Rect(10, 10, 290, 120), color=(0, 0, 1), fill=(0.9, 0.9, 1))
    p1.insert_image(fitz.Rect(20, 20, 140, 110), pixmap=alpha)
    xj = p1.insert_image(fitz.Rect(160, 20, 280, 110), stream=jpeg)
    p1.insert_image(fitz.Rect(160, 150, 220, 210), xref=xj)   # même image, 2e place
    p1.insert_text((20, 260), "Texte de controle", fontsize=12)
    p2 = doc.new_page(width=300, height=300)
    p2.insert_image(fitz.Rect(40, 40, 200, 200), xref=xj)     # réutilisée page 2
    doc.save(path)
    doc.close()


def _pixels(pdf_path: str) -> list[bytes]:
    with fitz.open(pdf_path) as d:
        return [pg.get_pixmap(dpi=72).samples for pg in d]


def _rendu_ancien_mode(pdf_path: str, out_path: str) -> None:
    """Rendu de RÉFÉRENCE : extraction avec octets en base64, sans original."""
    eng = PDFObjectEngine()
    src = fitz.open(pdf_path)
    out = fitz.open()
    data = {"pages": [], "fonts": eng._extract_fonts(src)}
    eng._build_fonts(data)
    for n in range(len(src)):
        pd = eng.extract_page_data(src, n, src[n], embed_images=True)
        eng.render_page_into(out, pd, draw_borders=True)
    out.save(out_path, garbage=4, deflate=True, clean=True)
    out.close()
    src.close()


def _images(cache: str) -> list[dict]:
    with open(cache, encoding="utf-8") as f:
        return [e for pg in json.load(f)["pages"] for e in pg["elements"]
                if e["type"] == "image"]


def run() -> int:
    checks = []

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    tmp = tempfile.mkdtemp(prefix="imgref_")
    try:
        src = os.path.join(tmp, "original.pdf")
        _pdf_synthetique(src)
        ref = os.path.join(tmp, "ref.pdf")
        _rendu_ancien_mode(src, ref)
        attendu = _pixels(ref)

        # ── Nouveau mode : extraction + rendu progressifs (mode structure) ──
        cache = os.path.join(tmp, "pages_debug.json")
        out = os.path.join(tmp, "nouveau.pdf")
        stream.translate_pdf_progressive(src, out, debug=True, cache_path=cache)
        imgs = _images(cache)
        eng0 = PDFObjectEngine()
        with fitz.open(src) as d:
            n_ancien = sum(1 for n in range(len(d))
                           for e in eng0.extract_page_data(d, n, d[n])["elements"]
                           if e["type"] == "image")
        ok("JSON  autant de placements d'image que l'ancien mode",
           len(imgs) == n_ancien and len(imgs) >= 4,
           f"{len(imgs)} contre {n_ancien}")
        ok("JSON  aucun octet d'image : seulement le xref",
           all(e.get("xref") and not ({"asset_b64", "asset_file", "ext"} & set(e))
               for e in imgs),
           str([sorted(e) for e in imgs]))
        ok("RENDU  identique au pixel à l'ancien mode (base64)",
           _pixels(out) == attendu)

        # ── Ancien cache (base64) : allégé à la réouverture ─────────────────
        eng = PDFObjectEngine()
        with fitz.open(src) as d:
            ancien = {"pages": [eng.extract_page_data(d, n, d[n], embed_images=True)
                                for n in range(len(d))]}
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(ancien, f)
        taille_avant = os.path.getsize(cache)
        out2 = os.path.join(tmp, "rouvert.pdf")
        stream.translate_pdf_progressive(src, out2, debug=True, cache_path=cache)
        taille_apres = os.path.getsize(cache)
        ok("REPRISE  l'ancien cache est allégé de ses octets d'image",
           all("asset_b64" not in e for e in _images(cache))
           and taille_apres < taille_avant / 2,
           f"{taille_avant} -> {taille_apres} octets")
        ok("REPRISE  rendu inchangé après allègement", _pixels(out2) == attendu)

        # ── Contrôle négatif : sans l'original, plus d'image ─────────────────
        # Prouve que le rendu dépend bien de la relecture par xref (sinon le
        # test d'identité pourrait passer pour une autre raison).
        sans = os.path.join(tmp, "sans_src.pdf")
        eng2 = PDFObjectEngine()
        with open(cache, encoding="utf-8") as f:
            pages = json.load(f)["pages"]
        with fitz.open(src) as d:
            data = {"pages": [], "fonts": eng2._extract_fonts(d)}
        eng2._build_fonts(data)
        o = fitz.open()
        for pd in pages:
            eng2.render_page_into(o, pd, draw_borders=True)
        o.save(sans)
        o.close()
        ok("TÉMOIN  sans l'original, le rendu diffère (images absentes)",
           _pixels(sans) != attendu)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    for nom, cond, detail in checks:
        print(f"  {'OK  ' if cond else 'ECHEC'}  {nom}"
              + (f"   [{detail}]" if detail and not cond else ""))
    passe = sum(1 for _, c, _ in checks if c)
    print(f"\n  {passe}/{len(checks)}\n")
    return 0 if passe == len(checks) else 1


if __name__ == "__main__":
    sys.exit(run())
