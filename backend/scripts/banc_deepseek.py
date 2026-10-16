"""Banc coût / qualité des réglages DeepSeek, par le chemin de l'application.

Pour chaque configuration (modèle[:effort]) : traduction des N premières
pages de texte de chaque document (extraction PDF + translate_extraction),
jetons facturés × tarif. Le solde du compte (deux décimales seulement, débit
différé de quelques minutes) contrôle la somme des estimations en fin de banc.
Sortie : JSON côte à côte par document.

Exécution :
  cd backend && PYTHONIOENCODING=utf-8 venv/Scripts/python.exe \
      scripts/banc_deepseek.py [sortie.json] [--configs a,b] [--pages N]
      [--docs cv,mv21]

Contexte : comme l'application, chaque document reçoit sa FICHE (écrite sur
tout le PDF, pages réparties) et chaque lot le texte de sa page.
"""
import copy
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import app.config  # noqa: E402,F401  (charge backend/.env)
from engines import runtags, translation_ai  # noqa: E402
from engines.pdf import tagging  # noqa: E402
from engines.pdf.engine import PDFObjectEngine  # noqa: E402
from engines.pdf.translate import (matiere_fiche_pdf,  # noqa: E402
                                   translate_extraction)
import fitz  # noqa: E402

CONFIGS = ["deepseek-flash", "deepseek-flash:high",
           "deepseek-v4-pro", "deepseek-v4-pro:high"]

DOCS = [  # (nom, chemin, langue cible)
    ("cv", BACKEND / "translations/b885219a18f7/original.pdf", "en-GB"),
    ("code_route", BACKEND / "tests files/Code_de_la_Route.pdf", "en"),
    ("data_science", BACKEND / "tests files/The Data Science Handbook.pdf", "fr"),
    ("mv21", BACKEND / "tests files/mv21.pdf", "fr"),
]

# USD par million de jetons, heures pleines : (cache, hors cache, sortie).
TARIFS = {"deepseek-flash": (0.006, 0.30, 1.20),
          "deepseek-v4-pro": (0.044, 1.32, 3.96)}


def heures_creuses(t=None):
    """Pleines = 01-04 h et 06-10 h UTC, du lundi au vendredi ; creuses = −50 %."""
    t = t or datetime.now(timezone.utc)
    pleine = t.weekday() < 5 and (1 <= t.hour < 4 or 6 <= t.hour < 10)
    return not pleine


def solde():
    req = urllib.request.Request(
        "https://api.deepseek.com/user/balance",
        headers={"Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        infos = json.load(r)["balance_infos"]
    return sum(float(i["total_balance"]) for i in infos if i["currency"] == "USD")


def attendre_maj_solde(avant, max_s=420, pas_s=30):
    """Le solde est débité en différé : on attend qu'il bouge, puis qu'il se
    stabilise sur deux lectures."""
    fin = time.monotonic() + max_s
    dernier = solde()
    while time.monotonic() < fin:
        time.sleep(pas_s)
        actuel = solde()
        if actuel != avant and actuel == dernier:
            return actuel
        dernier = actuel
    return dernier


def cout_estime(spec, conso):
    nom, _ = translation_ai.decouper_modele(spec)
    cache, miss, sortie = TARIFS.get(nom, (0, 0, 0))
    usd = (conso.get("entree_cache", 0) * cache
           + conso.get("entree_hors_cache", 0) * miss
           + conso.get("sortie", 0) * sortie) / 1e6
    return round(usd / 2 if heures_creuses() else usd, 5)


def longueur_texte(page):
    return sum(len(tagging.tag_paragraph(el)[0])
               for el in page.get("elements", []) if el.get("type") == "paragraph")


def main():
    args = sys.argv[1:]
    options = {"--configs", "--pages", "--docs"}
    libres = [a for i, a in enumerate(args)
              if a not in options and (i == 0 or args[i - 1] not in options)]
    sortie = Path(libres[0] if libres else "banc_deepseek.json")
    configs, pages = CONFIGS, 3
    if "--configs" in args:
        configs = args[args.index("--configs") + 1].split(",")
    if "--pages" in args:
        pages = int(args[args.index("--pages") + 1])
    docs = DOCS
    if "--docs" in args:
        noms = args[args.index("--docs") + 1].split(",")
        docs = [d for d in DOCS if d[0] in noms]

    moteur = PDFObjectEngine()
    extractions = {}
    for nom, chemin, _ in docs:
        data, _ = moteur.extract(str(chemin), output_json=str(
            Path(os.environ.get("TEMP", ".")) / f"banc_{nom}.json"))
        # Pages de TEXTE seulement : une couverture ne mesure rien.
        texte = [pg for pg in data.get("pages", []) if longueur_texte(pg) > 400]
        extractions[nom] = dict(data, pages=texte[:pages])
        print(f"extrait {nom} : pages "
              f"{[pg['page_num'] for pg in extractions[nom]['pages']]}", flush=True)

    resultats = {"date": datetime.now(timezone.utc).isoformat(),
                 "pages_par_doc": pages, "configs": {}}
    debut = solde()
    for spec in configs:
        os.environ["DEEPSEEK_MODEL"] = spec
        translation_ai.consommation.clear()
        print(f"\n== {spec}", flush=True)
        par_doc, t0 = {}, time.monotonic()
        for nom, chemin, langue in docs:
            data = copy.deepcopy(extractions[nom])
            journal = []
            t = time.monotonic()
            with fitz.open(str(chemin)) as doc:
                fiche = translation_ai.TranslatorAI().fiche_document(
                    *matiere_fiche_pdf(doc), langue,
                    progress_callback=journal.append)
            translate_extraction(data, target_lang=langue, progress=journal.append,
                                 fiche=fiche)
            lignes = []
            for page in data["pages"]:
                for el in page.get("elements", []):
                    if el.get("type") != "paragraph" or not el.get("tr_tagged", "").strip():
                        continue
                    src = tagging.tag_paragraph(el)[0]
                    trad = el["tr_tagged"]
                    lignes.append({"page": page["page_num"],
                                   "source": runtags.sans_balises(src),
                                   "traduit": runtags.sans_balises(trad),
                                   "defauts": runtags.defauts(src, trad)})
            non_trad = [m for m in journal if "non traduit" in m]
            echecs = [m for m in journal if "échoué" in m]
            par_doc[nom] = {"duree_s": round(time.monotonic() - t, 1),
                            "fiche": fiche,
                            "defauts": sum(bool(li["defauts"]) for li in lignes),
                            "non_traduits": non_trad, "lots_echoues": echecs,
                            "paragraphes": lignes}
            print(f"  {nom}: {len(lignes)} par., {par_doc[nom]['duree_s']} s, "
                  f"{par_doc[nom]['defauts']} défaut(s), "
                  f"{len(non_trad)} non traduit(s), {len(echecs)} lot(s) échoué(s)",
                  flush=True)
        duree = round(time.monotonic() - t0, 1)
        conso = translation_ai.consommation.get(spec, {})
        resultats["configs"][spec] = {
            "cout_estime_usd": cout_estime(spec, conso),
            "heures_creuses": heures_creuses(),
            "duree_s": duree, "jetons": conso, "documents": par_doc}
        print(f"  estimé {cout_estime(spec, conso):.4f} $ — {duree} s — {conso}",
              flush=True)
        sortie.write_text(json.dumps(resultats, ensure_ascii=False, indent=1),
                          encoding="utf-8")

    # Contrôle global : une config seule passe sous la résolution du solde ;
    # on compare la baisse TOTALE à la somme des estimations.
    print("\nattente du débit…", flush=True)
    fin = attendre_maj_solde(debut)
    resultats["solde"] = {
        "avant": debut, "apres": fin, "baisse_usd": round(debut - fin, 4),
        "somme_estimee_usd": round(sum(c["cout_estime_usd"]
                                       for c in resultats["configs"].values()), 4)}
    sortie.write_text(json.dumps(resultats, ensure_ascii=False, indent=1),
                      encoding="utf-8")
    print(f"solde {resultats['solde']}\nrésultats : {sortie}")


if __name__ == "__main__":
    main()
