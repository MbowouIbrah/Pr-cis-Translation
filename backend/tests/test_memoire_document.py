"""Mémoire de document dans le pipeline PDF.

Un texte court déjà traduit dans le document est resservi tel quel aux pages
suivantes, sans repasser par le modèle. Le même libellé saisi selon deux
typographies (« Tax Structure : » à la française, « Tax Structure: » à
l'anglaise) est le même texte : traduit deux fois, il ressortait de deux
façons. La casse, elle, reste distinctive (« Part One » / « PART ONE »).

PDF SYNTHÉTIQUE, modèle FACTICE (aucun appel réseau) : chaque appel rend une
traduction marquée de son numéro, si bien que deux traductions identiques
prouvent qu'un seul appel a servi.

Exécution :  cd backend && venv/Scripts/python.exe tests/test_memoire_document.py
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
from engines import translation_ai                             # noqa: E402
from engines.pdf import stream                                 # noqa: E402

envoyes = []         # textes balisés envoyés au modèle


def _batch(self, batch, target_lang, progress_callback, *a, **k):
    for b in batch:
        envoyes.append(b["text"])
        n = len(envoyes)
        b["translated_text"] = re.sub(
            r"(\[\[/?\d+\]\])|([^\[]+)",
            lambda m: m.group(1) or f"{m.group(2).strip()}#{n} ", b["text"])


translation_ai.TranslatorAI.__init__ = lambda self: None
translation_ai.TranslatorAI._translate_batch = _batch
translation_ai.TranslatorAI.fiche_document = lambda self, *a, **k: ""

PAGES = [["Tax Structure : CFLP", "Part One"],
         ["Tax Structure: CFLP", "PART ONE"]]


def _pdf(path: str) -> None:
    doc = fitz.open()
    for page in PAGES:
        pg = doc.new_page(width=400, height=400)
        for i, t in enumerate(page):
            pg.insert_text((30, 60 + 140 * i), t, fontsize=12)
    doc.save(path)
    doc.close()


def run() -> int:
    checks = []

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    tmp = tempfile.mkdtemp(prefix="memoire_doc_")
    try:
        src = os.path.join(tmp, "original.pdf")
        _pdf(src)
        cache = os.path.join(tmp, "pages.json")
        stream.translate_pdf_progressive(src, os.path.join(tmp, "out.pdf"),
                                         target_lang="es", cache_path=cache)
        with open(cache, encoding="utf-8") as f:
            pgs = json.load(f)["pages"]
        tr = [{e["text"].strip(): e.get("tr_tagged") for e in pg["elements"]
               if e["type"] == "paragraph"} for pg in pgs]
        p1, p2 = tr[0].get("Tax Structure : CFLP"), tr[1].get("Tax Structure: CFLP")
        ok("TYPO  le libellé à l'anglaise reçoit la traduction de la page 1",
           p1 and p1 == p2, f"{p1!r} / {p2!r}")
        ok("COÛT  le libellé n'est envoyé qu'une fois au modèle",
           sum("Tax Structure" in t for t in envoyes) == 1, str(envoyes))
        ok("CASSE  « PART ONE » n'hérite pas de « Part One »",
           tr[0].get("Part One") != tr[1].get("PART ONE")
           and any("PART ONE" in t for t in envoyes), str(tr))
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
