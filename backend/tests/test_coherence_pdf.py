"""Cohérence de document dans le pipeline PDF.

Chaque page est traduite sans voir les autres : un terme laissé en langue
source sur une page (« Ambassade de Suisse ») alors que le document le traduit
ailleurs (« the Embassy ») ne se voit qu'une fois tout le document lu. La passe
de fin de document le reprend, reconstruit SEULE la page touchée, et ne coûte
rien sur une simple réouverture.

PDF SYNTHÉTIQUE, modèle FACTICE (aucun appel réseau).

Exécution :  cd backend && venv/Scripts/python.exe tests/test_coherence_pdf.py
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
from engines.pdf import stream, translate                      # noqa: E402

LEXIQUE = {"ambassade": "Embassy", "l'ambassade": "the Embassy",
           "de": "of", "la": "the", "est": "is", "ouverte": "open",
           "suisse": "Switzerland", "fermee": "closed", "le": "the",
           "consulat": "consulate", "aujourd'hui": "today",
           "adresse": "address", "nouvelle": "new", "une": "a"}
appels = []          # (nb de fragments, avec consigne ?)
mode = {"fautif": "suisse", "reprise": "corrige"}


def _mot(m):
    w = m.group(0)
    return LEXIQUE.get(w.lower(), w)


def _traduire(texte: str, consigne: bool) -> str:
    oublie = (mode["fautif"] and mode["fautif"] in texte.lower()
              and (not consigne or mode["reprise"] == "identique"))
    def seg(m):
        if m.group(1):
            return m.group(1)
        out = re.sub(r"[\w']+", _mot, m.group(2))
        if oublie:   # le modèle « oublie » le terme sur ce fragment
            out = out.replace("Embassy", "Ambassade")
        return out
    out = re.sub(r"(\[\[/?\d+\]\])|([^\[]+)", seg, texte)
    if consigne and mode["reprise"] == "mutile":
        out = re.sub(r"\[\[/?\d+\]\]", "", out)
    return out


def _batch(self, batch, target_lang, progress_callback, *a, **k):
    coh = [("COHÉRENCE" in (b.get("consigne") or "")) for b in batch]
    appels.append((len(batch), any(coh)))
    for b, c in zip(batch, coh):
        b["translated_text"] = _traduire(b["text"], c)


translation_ai.TranslatorAI.__init__ = lambda self: None
translation_ai.TranslatorAI._translate_batch = _batch
translation_ai.TranslatorAI.fiche_document = lambda self, *a, **k: ""


def _pdf(path: str) -> None:
    doc = fitz.open()
    textes = [["L'Ambassade est ouverte aujourd'hui.", "Le consulat est fermee."],
              ["Une nouvelle adresse.", "Ambassade de la Suisse."],
              ["Le consulat est ouverte."]]
    for page in textes:
        pg = doc.new_page(width=400, height=400)
        for i, t in enumerate(page):
            pg.insert_text((30, 60 + 140 * i), t, fontsize=12)
    doc.save(path)
    doc.close()


def _texte(pdf: str, n: int) -> str:
    with fitz.open(pdf) as d:
        return d[n].get_text()


def run() -> int:
    checks = []

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    tmp = tempfile.mkdtemp(prefix="coherence_pdf_")
    try:
        src = os.path.join(tmp, "original.pdf")
        _pdf(src)
        out = os.path.join(tmp, "out.pdf")
        partiel = os.path.join(tmp, "partiel.pdf")
        cache = os.path.join(tmp, "pages.json")
        evts = []

        # ── Premier passage : la page 2 est reprise et reconstruite ─────────
        stream.translate_pdf_progressive(src, out, target_lang="en",
                                         cache_path=cache, partial_path=partiel,
                                         on_event=evts.append)
        reprises = [n for n, c in appels if c]
        ok("REPRISE  un seul fragment redemandé, avec la consigne",
           reprises == [1], str(appels))
        p2 = _texte(out, 1)
        ok("RENDU  la page 2 livrée porte le terme traduit",
           "Embassy" in p2 and "Ambassade" not in p2, p2)
        ok("RENDU  le partiel aussi", "Embassy" in _texte(partiel, 1))
        ok("RENDU  les autres pages sont intactes et dans l'ordre",
           "the Embassy" in _texte(out, 0) and "consulate" in _texte(out, 2)
           and len(fitz.open(out)) == 3)
        with open(cache, encoding="utf-8") as f:
            trs = [e.get("tr_tagged") for pg in json.load(f)["pages"]
                   for e in pg["elements"] if e["type"] == "paragraph"]
        ok("CACHE  la correction est persistée",
           any("Embassy of the Switzerland" in (t or "") for t in trs), str(trs))
        fins = [e["page"] for e in evts
                if e.get("type") == "page" and e.get("status") == "done"]
        ok("APERÇU  la page touchée est ré-annoncée (et elle seule)",
           fins == [1, 2, 3, 2], str(fins))

        # ── Réouverture : rien de neuf, aucun appel — même si le cache garde
        # un terme resté en langue source (un cognat légitime, par exemple).
        with open(cache, encoding="utf-8") as f:
            prev = json.load(f)
        for e in prev["pages"][1]["elements"]:
            if "Switzerland" in (e.get("tr_tagged") or ""):
                e["tr_tagged"] = e["tr_tagged"].replace("Embassy", "Ambassade")
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(prev, f, ensure_ascii=False)
        appels.clear()
        stream.translate_pdf_progressive(src, out, target_lang="en",
                                         cache_path=cache)
        ok("COÛT  une réouverture ne redemande rien", not appels, str(appels))

        # ── Unité : réponses refusées ────────────────────────────────────────
        def pages_neuves():
            os.remove(cache)
            mode["reprise"] = "corrige"
            mode["fautif"] = None              # traduction sans faute...
            stream.translate_pdf_progressive(src, out, target_lang="en",
                                             cache_path=cache)
            with open(cache, encoding="utf-8") as f:
                pgs = json.load(f)["pages"]
            for e in pgs[1]["elements"]:       # ...puis l'oubli, posé à la main
                if "Switzerland" in (e.get("tr_tagged") or ""):
                    e["tr_tagged"] = e["tr_tagged"].replace("Embassy",
                                                            "Ambassade")
            mode["fautif"] = "suisse"
            return pgs

        for reprise, nom in (("identique", "COGNAT  le modèle garde le terme"),
                             ("mutile", "BALISES  reprise aux balises mutilées")):
            pgs = pages_neuves()
            avant = json.dumps(pgs)
            mode["reprise"] = reprise
            touchees = translate.coherence_document(pgs, "en")
            ok(f"{nom} : aucune page reconstruite, texte inchangé",
               touchees == set() and json.dumps(pgs) == avant, str(touchees))
        mode["reprise"] = "corrige"
        pgs = pages_neuves()
        ok("UNITÉ  seule la page fautive est touchée",
           translate.coherence_document(pgs, "en") == {2})
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
