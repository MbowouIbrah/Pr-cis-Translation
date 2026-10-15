"""Correctif du moteur APRÈS une traduction : il s'applique, sans repayer ni
changer le texte en silence.

Une page sauvegardée rejoue les décisions du moteur qui l'a découpée. Chaque
page porte donc la version de ce moteur (`moteur`) ; à la réouverture, une page
d'une autre version est redécoupée et :
  - mode structure : la nouvelle découpe remplace l'ancienne (rien à traduire) ;
  - page traduite, texte retrouvé à l'identique : la traduction est réattachée
    mot pour mot, ZÉRO appel IA ;
  - page traduite, texte changé : la page ancienne reste, la mise à jour est
    PROPOSÉE (`amelioration`), jamais imposée — et pas retentée à chaque visite.

PDF SYNTHÉTIQUE. L'IA est INTERDITE dans ce test : tout appel le fait échouer.

Exécution :  cd backend && venv/Scripts/python.exe tests/test_redecoupage_version.py
"""
from __future__ import annotations
import json
import os
import re
import shutil
import sys
import tempfile

import racine  # noqa: F401  -- met backend/ sur le chemin

import fitz                                                    # noqa: E402
from engines.pdf import stream, tagging, translate             # noqa: E402
from engines.pdf.engine import PDFObjectEngine                 # noqa: E402
from engines.pdf.version import ENGINE_VERSION                 # noqa: E402

ANCIENNE = "v0.0.1"
appels_ia = []


def _interdit(*a, **k):
    appels_ia.append(1)
    raise RuntimeError("appel IA interdit dans ce test")


translate.translate_extraction = _interdit
translate.retranslate_overflows = _interdit


def _pdf(path: str) -> None:
    doc = fitz.open()
    for n in range(2):
        pg = doc.new_page(width=400, height=400)
        pg.insert_text((30, 60), f"Premier paragraphe de la page {n + 1}.",
                       fontsize=12)
        pg.insert_text((30, 200), "Un second bloc, bien separe du premier.",
                       fontsize=12)
    doc.save(path)
    doc.close()


def _traduire_factice(tagged: str) -> str:
    """Traduction factice qui garde les balises [[n]] : on inverse la casse."""
    return re.sub(r"(\[\[/?\d+\]\])|([^\[]+)",
                  lambda m: m.group(1) or m.group(2).swapcase(), tagged)


def _cache_ancien(src: str, cache: str, debug: bool, alterer: bool = False):
    """Cache tel qu'un moteur PLUS ANCIEN l'aurait écrit."""
    eng = PDFObjectEngine()
    pages = []
    with fitz.open(src) as d:
        for n in range(len(d)):
            pd = eng.extract_page_data(d, n, d[n], images_par_reference=True)
            pd["moteur"] = ANCIENNE
            for el in pd["elements"]:
                if el["type"] != "paragraph":
                    continue
                if alterer:   # l'ancien moteur avait regroupé AUTREMENT
                    el["lines"][0]["runs"][0]["text"] += " (ancien)"
                if not debug:
                    tagged, meta = tagging.tag_paragraph(el)
                    el["tr_segments"] = meta
                    el["tr_tagged"] = _traduire_factice(tagged)
            pages.append(pd)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump({"pages": pages}, f, ensure_ascii=False)


def _pages(cache: str) -> list[dict]:
    with open(cache, encoding="utf-8") as f:
        return json.load(f)["pages"]


def _trads(pages) -> list[str]:
    return [e.get("tr_tagged") for pg in pages for e in pg["elements"]
            if e["type"] == "paragraph"]


def run() -> int:
    checks = []

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    tmp = tempfile.mkdtemp(prefix="redecoupe_")
    try:
        src = os.path.join(tmp, "original.pdf")
        _pdf(src)
        out = os.path.join(tmp, "out.pdf")
        cache = os.path.join(tmp, "pages.json")

        # ── Page traduite, texte identique : réattachée, zéro IA ────────────
        _cache_ancien(src, cache, debug=False)
        avant = _trads(_pages(cache))
        stream.translate_pdf_progressive(src, out, target_lang="en",
                                         cache_path=cache)
        apres = _pages(cache)
        ok("REDÉCOUPE  les pages passent à la version COURANTE du moteur",
           all(p.get("moteur") == ENGINE_VERSION for p in apres),
           str([p.get("moteur") for p in apres]))
        ok("TEXTE  traductions réattachées MOT POUR MOT", _trads(apres) == avant,
           f"{avant} -> {_trads(apres)}")
        ok("COÛT  aucun appel IA", not appels_ia, f"{len(appels_ia)} appel(s)")
        ok("RENDU  le PDF est produit", os.path.isfile(out))

        # ── Version courante : rien n'est redécoupé ──────────────────────────
        eng = PDFObjectEngine()
        extractions = []
        orig = eng.extract_page_data
        eng.extract_page_data = lambda *a, **k: (extractions.append(1),
                                                  orig(*a, **k))[1]
        stream.translate_pdf_progressive(src, out, target_lang="en",
                                         cache_path=cache, engine=eng)
        ok("À JOUR  une page de la version courante n'est pas redécoupée",
           not extractions, f"{len(extractions)} extraction(s)")

        # ── Texte changé : la page ancienne reste, mise à jour proposée ─────
        _cache_ancien(src, cache, debug=False, alterer=True)
        avant = _trads(_pages(cache))
        stream.translate_pdf_progressive(src, out, target_lang="en",
                                         cache_path=cache)
        apres = _pages(cache)
        ok("ACCORD  texte qui changerait : la traduction reste INCHANGÉE",
           _trads(apres) == avant)
        ok("ACCORD  la page garde sa version, l'amélioration est proposée",
           all(p.get("moteur") == ANCIENNE
               and p.get("amelioration") == ENGINE_VERSION for p in apres))
        ok("COÛT  toujours aucun appel IA", not appels_ia)
        extractions.clear()
        stream.translate_pdf_progressive(src, out, target_lang="en",
                                         cache_path=cache, engine=eng)
        ok("ACCORD  proposition mémorisée : pas redécoupée à chaque visite",
           not extractions, f"{len(extractions)} extraction(s)")

        # ── Mode structure : la nouvelle découpe remplace l'ancienne ────────
        cache_dbg = os.path.join(tmp, "pages_debug.json")
        _cache_ancien(src, cache_dbg, debug=True, alterer=True)
        stream.translate_pdf_progressive(src, out, debug=True,
                                         cache_path=cache_dbg)
        apres = _pages(cache_dbg)
        textes = [s["text"] for pg in apres for e in pg["elements"]
                  if e["type"] == "paragraph"
                  for li in e["lines"] for s in li["runs"]]
        ok("STRUCTURE  redécoupée à la version courante",
           all(p.get("moteur") == ENGINE_VERSION for p in apres))
        ok("STRUCTURE  la découpe ancienne est remplacée",
           textes and not any("(ancien)" in t for t in textes))

        # ── Unité : tout ou rien ─────────────────────────────────────────────
        with fitz.open(src) as d:
            neuve = PDFObjectEngine().extract_page_data(d, 0, d[0])
        ancienne = json.loads(json.dumps(neuve))
        paras = [e for e in ancienne["elements"] if e["type"] == "paragraph"]
        tagged, _ = tagging.tag_paragraph(paras[0])
        paras[0]["tr_tagged"] = _traduire_factice(tagged)   # le 2e : jamais traduit
        ok("UNITÉ  un paragraphe inconnu : rien n'est posé",
           translate.reattacher_traductions(neuve, ancienne) is False
           and all("tr_tagged" not in e for e in neuve["elements"]))
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
