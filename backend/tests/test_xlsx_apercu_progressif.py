"""XLSX — l'aperçu se remplit FEUILLE PAR FEUILLE, comme le PDF et le PPTX.

LE FLUX
-------
1. SOCLE — le classeur d'ORIGINE, converti une fois. L'utilisateur voit tout le
   document en langue source dès la première seconde.
2. GREFFE — chaque feuille traduite est convertie SEULE et sa page remplace
   celle du socle.

CE QUI DIFFÈRE D'UN DIAPORAMA, ET QUI JUSTIFIE LE DÉCOUPAGE RETENU
-------------------------------------------------------------------
Une diapositive est autonome : on l'extrait, on la traduit, on l'injecte. Une
feuille ne l'est pas — Excel déduplique le texte de TOUT le classeur dans
`sharedStrings.xml`, et une même chaîne peut servir dix feuilles. Aucune ne
peut la revendiquer, et « traduire la feuille 3 » n'a pas de sens.

On ne découpe donc pas la TRADUCTION par feuille (le classeur part d'un bloc,
seul découpage honnête) : c'est l'AFFICHAGE qui est progressif.

LES DEUX PIÈGES VERROUILLÉS ICI
-------------------------------
1. **Deux conventions d'index se rencontrent.** `ProgressivePreview` compte en
   NUMÉROS DE PAGE 1-basés, `feuilles()` en INDEX 0-basés. Les confondre décale
   tout l'aperçu d'un cran — la feuille 2 s'afficherait à la place de la 1.

2. **Une feuille ne fait pas toujours une page.** Une feuille longue s'imprime
   sur dix pages : le socle en compte alors dix quand `feuilles()` n'en annonce
   qu'une, et une greffe naïve poserait la traduction en face de la mauvaise
   page. On préfère alors NE PAS greffer — l'utilisateur garde le socle en
   langue source, ce qui est honnête, plutôt qu'un aperçu décalé.

AUCUN APPEL AU MODÈLE : le traducteur est simulé. On teste le FLUX.

    backend/venv/Scripts/python.exe backend/tests/test_xlsx_apercu_progressif.py
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile

import racine  # noqa: F401  -- met backend/ sur le chemin

import fitz                                                  # noqa: E402
from app.services import translation_runner as runner        # noqa: E402
from app.services.jobs import jobs                           # noqa: E402
from fabrique_classeurs import FEUILLES, construire_multi     # noqa: E402

_checks: list[tuple[bool, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _checks.append((bool(cond), label))
    print(f"  {'OK  ' if cond else 'ECHEC'} {label}"
          + (f"   [{detail}]" if detail and not cond else ""))


class _TraducteurSimule:
    """Préfixe « EN: » chaque segment balisé, structure intacte.

    Reproduit ce que rend le modèle sans dépendre du réseau ni d'un quota — un
    test qui appelle une API ne se lance pas.
    """

    appels = 0

    def translate_json(self, chemin, target_lang=None, progress_callback=None,
                       model=None, max_tokens=None, **_):
        type(self).appels += 1
        with open(chemin, encoding="utf-8") as f:
            data = json.load(f)
        for el in data["workbook"]["elements"]:
            el["translated_text"] = re.sub(
                r"(\[\[(\d+)\]\])(.*?)(\[\[/\2\]\])",
                lambda m: m.group(1) + "EN:" + m.group(3) + m.group(4),
                el["text"])
        sortie = chemin.replace("extraction", "translated")
        with open(sortie, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return True, sortie


def main() -> int:
    travail = tempfile.mkdtemp(prefix="precis_test_xlsx_prog_")
    src = construire_multi(os.path.join(travail, "src.xlsx"))
    with open(src, "rb") as f:
        octets = f.read()

    partial_path = os.path.join(travail, "partial.pdf")
    output_path = os.path.join(travail, "out.xlsx")

    vrai, vrai_actif = runner.ai_translator, runner.ai_active
    runner.ai_translator, runner.ai_active = _TraducteurSimule(), True
    try:
        job_id = jobs.create()
        runner.run_xlsx_progressive_job(
            job_id, octets, os.path.join(travail, "orig.xlsx"),
            output_path, "out.xlsx", partial_path,
            os.path.join(travail, "translated.json"), "en")
        job = jobs.get(job_id) or {}
    finally:
        runner.ai_translator, runner.ai_active = vrai, vrai_actif

    # ── 1. Le job aboutit ───────────────────────────────────────────────────
    check(job.get("state") == "done",
          "JOB     le job se termine sans erreur",
          f"state={job.get('state')} erreur={job.get('error')}")

    # ── 2. L'aperçu existe et porte la traduction ──────────────────────────
    check(os.path.exists(partial_path) and os.path.getsize(partial_path) > 0,
          "APERCU  le PDF partiel est écrit")

    with fitz.open(partial_path) as doc:
        pages = [doc[i].get_text() for i in range(doc.page_count)]

    check(len(pages) == len(FEUILLES),
          "APERCU  il compte une page par feuille",
          f"{len(pages)} page(s) pour {len(FEUILLES)} feuille(s)")
    check(all("EN:" in p for p in pages),
          "APERCU  TOUTES les feuilles y sont traduites",
          str([p[:24] for p in pages]))

    # ── 3. L'APPARIEMENT feuille -> page, le piège des deux conventions ────
    # Chaque feuille doit apparaître à SA page. Une confusion 0-basé/1-basé
    # décalerait tout d'un cran, et ce contrôle est le seul à le voir.
    marqueurs = {0: "Chiffre", 1: "Loyer", 2: "Resulta"}
    bien_placees = sum(1 for i, m in marqueurs.items()
                       if i < len(pages) and m in pages[i])
    check(bien_placees == len(marqueurs),
          "APPARIE chaque feuille est greffée SUR SA PAGE (0-basé vs 1-basé)",
          f"{bien_placees}/{len(marqueurs)} — "
          f"{[p[:28] for p in pages]}")

    # ── 4. Le classeur final ────────────────────────────────────────────────
    check(os.path.exists(output_path),
          "FINAL   le classeur traduit est produit")
    with zipfile.ZipFile(output_path) as z:
        abime = z.testzip()
        n_feuilles = len([n for n in z.namelist()
                          if n.startswith("xl/worksheets/sheet")])
        magasin = z.read("xl/sharedStrings.xml").decode("utf-8")
    check(abime is None, "FINAL   l'archive est intacte", str(abime))
    check(n_feuilles == len(FEUILLES),
          "FINAL   il garde TOUTES ses feuilles (partiels non destructifs)",
          f"{n_feuilles}")
    check("EN:" in magasin, "FINAL   son texte est traduit")
    # Le nombre pur n'a pas bougé : la garde du tableur survit au flux
    # progressif, qui réinjecte par un autre chemin qu'`inject_translation`.
    check("<t>1234,50</t>" in magasin,
          "FINAL   un nombre pur reste intact même en flux progressif")

    # ── 5. Le classeur est traduit UNE fois, pas une fois par feuille ──────
    # C'est ce qui distingue « affichage progressif » de « traduction
    # progressive » : découper la traduction par feuille n'aurait aucun sens,
    # le magasin partagé étant commun à tout le classeur.
    check(_TraducteurSimule.appels == 1,
          "COUT    le classeur n'est traduit QU'UNE fois",
          f"{_TraducteurSimule.appels} appel(s)")

    rates = [lbl for ok_, lbl in _checks if not ok_]
    print(f"\n{len(_checks) - len(rates)}/{len(_checks)} verifications")
    if rates:
        for lbl in rates:
            print(f"  ECHEC {lbl}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
