"""Banc de comparaison des fournisseurs de traduction, sur un VRAI PDF.

Chaque fournisseur (ou modèle OpenRouter) traduit les mêmes pages par le même
chemin que l'application — extraction, balisage, `translate_extraction` page
par page — et l'on compte ce qui se constate sans connaître le document :

  • non traduits : paragraphes restés sans traduction ;
  • défauts de forme (runtags.defauts) APRÈS la passe de qualité : balises
    perdues, morceaux recopiés, nombres modifiés ;
  • requêtes réseau (ce que consomme le quota) et durée.

Ces chiffres écartent un modèle ; ils ne suffisent pas à en élire un. Le
fichier `banc_<pdf>.json` met source et traductions côte à côte pour la
lecture humaine, seule juge du registre et du mot à mot.

Le rendu (retraduction compacte des débordements) n'est pas exercé : le banc
mesure la traduction, pas la mise en page.

Attention au quota : un modèle :free d'OpenRouter = 50 requêtes/jour, et une
page coûte au moins une requête (plus la passe de qualité).

Usage :
  venv/Scripts/python.exe scripts/banc_fournisseurs.py "tests files/mv21.pdf" \
      --pages 1-3 --cible en \
      --candidats deepseek,openrouter:google/gemma-4-31b-it:free
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                                            # noqa: E402

from engines import runtags, translation_ai                            # noqa: E402
from engines.pdf import translate                                      # noqa: E402
from engines.pdf.engine import PDFObjectEngine                         # noqa: E402
from engines.translation_ai import TranslatorAI                        # noqa: E402


def _pages(spec, doc):
    total = len(doc)
    if not spec:
        # Les 3 premières pages qui ont du texte : une couverture ou un scan
        # (sans couche texte) ne mesurerait rien.
        return [i + 1 for i in range(total)
                if doc[i].get_text().strip()][:3]
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out.extend(range(int(a), int(b or a) + 1))
    return [p for p in out if 1 <= p <= total]


def _candidat(spec):
    """« deepseek » ou « openrouter:<modèle>[|<repli>] »."""
    nom, _, modele = spec.partition(":")
    return nom, modele.replace("|", ",")


def _executer(pdf, pages, cible, nom, modele):
    os.environ["TRADUCTION_FOURNISSEURS"] = nom
    if modele:
        os.environ["OPENROUTER_MODELS"] = modele

    vus: dict[str, dict] = {}
    compte = {"appels": 0}
    batch_orig = TranslatorAI._translate_batch
    init_orig = translation_ai._Fournisseur.__init__

    def _batch(self, batch, *a, **kw):
        for b in batch:
            vus.setdefault(b["id"], b)
        return batch_orig(self, batch, *a, **kw)

    def _init(self, nom, client, *a, **kw):
        # Compte les requêtes RÉSEAU (quota), pas les tentatives internes
        # qu'un fournisseur hors service écarte sans rien envoyer.
        creer = client.chat.completions.create

        def _compter(**k):
            compte["appels"] += 1
            return creer(**k)
        client.chat.completions.create = _compter
        init_orig(self, nom, client, *a, **kw)

    TranslatorAI._translate_batch = _batch
    translation_ai._Fournisseur.__init__ = _init
    messages: list[str] = []
    t0 = time.monotonic()
    try:
        eng = PDFObjectEngine()
        src = fitz.open(pdf)
        data = {"pages": [], "fonts": eng._extract_fonts(src)}
        eng._build_fonts(data)
        memoire: dict = {}
        for p in pages:
            page = eng.extract_page_data(src, p - 1, src[p - 1],
                                         images_par_reference=True)
            translate.translate_extraction({"pages": [page], "fonts": {}},
                                           target_lang=cible,
                                           progress=messages.append,
                                           doc_memory=memoire)
    finally:
        TranslatorAI._translate_batch = batch_orig
        translation_ai._Fournisseur.__init__ = init_orig
    duree = time.monotonic() - t0

    defauts = {"balises": 0, "recopie": 0, "nombres": 0}
    non_traduits = 0
    lignes = []
    for i, b in vus.items():
        out = b.get("translated_text")
        if not out:
            non_traduits += 1
        else:
            for m in runtags.defauts(b["text"], out):
                defauts[m] = defauts.get(m, 0) + 1
        lignes.append({"id": i, "source": b["text"], "traduction": out})
    return {"candidat": f"{nom}:{modele}" if modele else nom,
            "paragraphes": len(vus), "non_traduits": non_traduits,
            "defauts": defauts, "appels": compte["appels"],
            "duree_s": round(duree, 1),
            "erreurs": [m for m in messages if "⚠" in m][:5],
            "lignes": lignes}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pdf")
    ap.add_argument("--pages", help="ex. 1-3,7 (défaut : 3 premières pages avec texte)")
    ap.add_argument("--cible", default="en")
    ap.add_argument("--candidats",
                    default="deepseek,openrouter:google/gemma-4-31b-it:free")
    ap.add_argument("--sortie", help="dossier du rapport JSON (défaut : .)")
    args = ap.parse_args()

    pages = _pages(args.pages, fitz.open(args.pdf))
    if not pages:
        print("Aucune page avec du texte à traduire (scan sans couche texte ?).")
        return 1
    resultats = []
    for spec in args.candidats.split(","):
        nom, modele = _candidat(spec.strip())
        print(f"→ {spec} : pages {pages} vers {args.cible}…", flush=True)
        resultats.append(_executer(args.pdf, pages, args.cible, nom, modele))

    print(f"\n{'candidat':48} {'para':>5} {'non tr.':>7} {'balises':>7} "
          f"{'recopie':>7} {'nombres':>7} {'appels':>6} {'durée':>7}")
    for r in resultats:
        d = r["defauts"]
        print(f"{r['candidat'][:48]:48} {r['paragraphes']:>5} "
              f"{r['non_traduits']:>7} {d['balises']:>7} {d['recopie']:>7} "
              f"{d['nombres']:>7} {r['appels']:>6} {r['duree_s']:>6}s")
        for e in r["erreurs"]:
            print(f"    {e[:150]}")

    base = os.path.splitext(os.path.basename(args.pdf))[0]
    chemin = os.path.join(args.sortie or ".", f"banc_{base}.json")
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump({"pdf": args.pdf, "pages": pages, "cible": args.cible,
                   "resultats": resultats}, f, ensure_ascii=False, indent=2)
    print(f"\nCôte à côte : {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
