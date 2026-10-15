"""Un document en mode STRUCTURE se rouvre en mode structure.

LE DÉFAUT QUE CETTE SUITE VERROUILLE
------------------------------------
Rouvert depuis la bibliothèque, l'aperçu n'est plus le PDF du job : il est
RECALCULÉ depuis l'original et le JSON stocké (`render_translation_bytes`).
Ce recalcul ignorait le mode structure à deux endroits — `debug=False` en dur,
et des pages « à rendre » choisies sur leurs paragraphes traduits, qu'une
structure n'a jamais. Résultat : rien n'était reconstruit, l'original était
recopié, et l'aperçu montrait original/original.

Le PDF est synthétique : le défaut tient au mode, pas à un document.

    backend/venv/Scripts/python.exe backend/tests/test_rendu_structure.py
"""
from __future__ import annotations

import os
import sys
import tempfile

import fitz

import racine  # noqa: F401  -- met backend/ sur le chemin

from app.services.translation_runner import render_translation_bytes  # noqa: E402
from engines.pdf import stream as pdf_v2_stream  # noqa: E402

resultats: list[bool] = []


def check(nom: str, ok: bool, detail: str = "") -> None:
    resultats.append(ok)
    print(f"  {'OK  ' if ok else 'ECHEC'}  {nom}" + (f" — {detail}" if detail else ""))


def _pdf_synthetique(chemin: str) -> None:
    doc = fitz.open()
    for n in range(2):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 90), f"Titre de la page {n + 1}", fontsize=18)
        page.insert_textbox(fitz.Rect(72, 120, 523, 400),
                            "Un paragraphe de corps, assez long pour tenir sur "
                            "plusieurs lignes et former un bloc. " * 4,
                            fontsize=11)
    doc.save(chemin)
    doc.close()


with tempfile.TemporaryDirectory() as tmp:
    original = os.path.join(tmp, "original.pdf")
    _pdf_synthetique(original)
    # Même nommage que /translate : le suffixe `_debug` porte le mode.
    cache = os.path.join(tmp, "pages_debug.json")
    sortie = os.path.join(tmp, "render_debug.pdf")
    pdf_v2_stream.translate_pdf_progressive(original, sortie, target_lang="en",
                                            debug=True, cache_path=cache)

    with fitz.open(original) as o, fitz.open(sortie) as j:
        orig = [len(p.get_drawings()) for p in o]
        job = [len(p.get_drawings()) for p in j]
    check("JOB      le rendu du job trace des contours", all(a > b for a, b in zip(job, orig)),
          f"original {orig}, job {job}")

    rendu = render_translation_bytes(original, cache, "pdf", "en")
    with fitz.open("pdf", rendu) as r:
        recalc = [len(p.get_drawings()) for p in r]
    check("ROUVERT  le recalcul trace les mêmes contours que le job", recalc == job,
          f"job {job}, recalcul {recalc}")

    # Même JSON sous un nom SANS `_debug` : rendu de traduction normal, donc
    # aucun contour (le mode vient du nom, pas d'une heuristique sur le contenu).
    normal = os.path.join(tmp, "pages.json")
    with open(cache, "rb") as s, open(normal, "wb") as d:
        d.write(s.read())
    with fitz.open("pdf", render_translation_bytes(original, normal, "pdf", "en")) as r:
        sans = [len(p.get_drawings()) for p in r]
    check("NORMAL   une traduction ordinaire ne trace aucun contour", sans == orig,
          f"original {orig}, rendu {sans}")

print(f"\n  {sum(resultats)}/{len(resultats)}")
sys.exit(0 if all(resultats) else 1)
