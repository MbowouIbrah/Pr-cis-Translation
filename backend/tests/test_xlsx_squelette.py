"""Le squelette XLSX : le chemin est complet, et ce qu'il ne couvre pas se voit.

CE QUE CETTE SUITE PROUVE
-------------------------
1. CHEMIN     — un classeur entre, ressort traduit, et reste un ZIP ouvrable.
2. CHAÎNES    — les deux formes qu'Excel emploie sont lues : le magasin partagé
                ET les chaînes en ligne. Ne lire que la première rend certains
                classeurs INCHANGÉS sans lever la moindre erreur — le pire des
                échecs, celui qui se croit réussi.
3. NOMBRES    — un nombre n'est jamais relevé. Traduit, il cesse d'être un
                nombre et toute formule qui le lit se casse.
4. FORMULES   — une formule n'est jamais touchée. C'est du code : « SUM »
                traduit donne `#NAME?`, et l'erreur se propage à la feuille.
5. PRÉSERVE   — styles, largeurs de colonnes et formules survivent au
                aller-retour : on modifie les nœuds SUR PLACE.
6. AVEU       — le moteur déclare ce qu'il ne lit pas encore. L'écart entre la
                promesse et le code doit rester MESURABLE, pas être une
                impression.

Le classeur est SYNTHÉTIQUE, écrit ici même. Un correctif prouvé sur le fichier
qui l'a révélé est une heuristique déguisée : il tient jusqu'au fichier suivant.

    backend/venv/Scripts/python.exe backend/tests/test_xlsx_squelette.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import zipfile

import racine  # noqa: F401  -- met backend/ sur le chemin

from engines.xlsx.engine import XLSXTranslatorEngine, est_numerique  # noqa: E402

CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheets><sheet name="Ventes" sheetId="1" r:id="rId1"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></sheets>
</workbook>"""

# Le magasin partagé. La 3e entrée est un nombre pur : elle NE DOIT PAS être
# relevée. La 4e mêle deux mises en forme -> deux <t>, donc deux balises.
SHARED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="4" uniqueCount="4">
<si><t>Chiffre d'affaires</t></si>
<si><t>Marge brute</t></si>
<si><t>1234,50</t></si>
<si><r><t>Total </t></r><r><t>2026</t></r></si>
</sst>"""

# La feuille : styles, largeurs, une FORMULE, et une cellule en ligne.
SHEET = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<cols><col min="1" max="1" width="28.5" customWidth="1"/></cols>
<sheetData>
<row r="1"><c r="A1" t="s" s="3"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
<row r="2"><c r="A2"><v>1200</v></c><c r="B2"><f>SUM(A2:A9)</f><v>1200</v></c></row>
<row r="3"><c r="A3" t="inlineStr"><is><t>Prevision annuelle</t></is></c></row>
<row r="4"><c r="A4" t="inlineStr"><is><t>42</t></is></c></row>
</sheetData>
</worksheet>"""


def construire(chemin: str) -> str:
    with zipfile.ZipFile(chemin, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CT)
        z.writestr("_rels/.rels", RELS)
        z.writestr("xl/workbook.xml", WORKBOOK)
        z.writestr("xl/sharedStrings.xml", SHARED)
        z.writestr("xl/worksheets/sheet1.xml", SHEET)
    return chemin


def lire(chemin: str, partie: str) -> str:
    with zipfile.ZipFile(chemin) as z:
        return z.read(partie).decode("utf-8")


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    travail = tempfile.mkdtemp(prefix="precis_test_xlsx_")
    try:
        source = construire(os.path.join(travail, "synthetique.xlsx"))
        releve = os.path.join(travail, "releve.json")

        # ── 1. Extraction ────────────────────────────────────────────────
        extraction, _ = XLSXTranslatorEngine().extract_text(source, releve)
        elements = extraction["workbook"]["elements"]
        par_id = {e["id"]: e["text"] for e in elements}

        ok("CHAINES  le magasin partagé est lu", "ss_0" in par_id, str(list(par_id)))
        ok("CHAINES  les chaînes EN LIGNE sont lues aussi",
           any(i.startswith("inline_") for i in par_id), str(list(par_id)))
        ok("CHAINES  une cellule à deux mises en forme donne DEUX balises",
           par_id.get("ss_3") == "[[0]]Total [[/0]][[1]]2026[[/1]]",
           repr(par_id.get("ss_3")))

        ok("NOMBRES  un nombre du magasin n'est pas relevé",
           "ss_2" not in par_id, "1234,50 releve a tort")
        ok("NOMBRES  un nombre en ligne n'est pas relevé non plus",
           not any(i.endswith("A4") for i in par_id), str(list(par_id)))
        for valeur in ("12", "1234,50", "1 234.50", "8%", "-3"):
            ok(f"NOMBRES  « {valeur} » est reconnu comme un nombre",
               est_numerique(valeur))
        for valeur in ("Total 2026", "A1", "12 pommes"):
            ok(f"NOMBRES  « {valeur} » n'est PAS un nombre",
               not est_numerique(valeur))

        # ── 2. Injection ─────────────────────────────────────────────────
        for el in elements:
            # Traduction feinte : on marque chaque balise, structure conservée.
            el["translated_text"] = el["text"].replace("Chiffre d'affaires", "Revenue") \
                                              .replace("Marge brute", "Gross margin") \
                                              .replace("Total ", "Total ") \
                                              .replace("Prevision annuelle", "Annual forecast")
        with open(releve, "w", encoding="utf-8") as f:
            json.dump(extraction, f, ensure_ascii=False)

        sortie = os.path.join(travail, "traduit.xlsx")
        reussi, message = XLSXTranslatorEngine().inject_translation(
            source, releve, sortie)
        ok("CHEMIN  l'injection réussit", reussi, message)
        ok("CHEMIN  le classeur produit existe", os.path.exists(sortie))
        ok("CHEMIN  c'est un ZIP valide et complet",
           os.path.exists(sortie) and zipfile.is_zipfile(sortie)
           and "[Content_Types].xml" in zipfile.ZipFile(sortie).namelist())

        if os.path.exists(sortie):
            ss = lire(sortie, "xl/sharedStrings.xml")
            sh = lire(sortie, "xl/worksheets/sheet1.xml")

            ok("TRADUIT  le magasin porte la traduction", "Revenue" in ss, ss[:200])
            ok("TRADUIT  plus aucune trace de la source",
               "Chiffre d'affaires" not in ss)
            ok("TRADUIT  la chaîne en ligne est traduite",
               "Annual forecast" in sh, sh[:300])

            # ── 3. Ce qui NE DOIT PAS bouger ─────────────────────────────
            ok("PRESERVE  la formule est intacte", "SUM(A2:A9)" in sh)
            ok("PRESERVE  la largeur de colonne est intacte", 'width="28.5"' in sh)
            ok("PRESERVE  le style de cellule est intact", 's="3"' in sh)
            ok("PRESERVE  le nombre du magasin est resté tel quel",
               "1234,50" in ss)
            ok("PRESERVE  le nombre en ligne est resté tel quel", ">42<" in sh)

        # ── 4. L'aveu de couverture ──────────────────────────────────────
        couverture = XLSXTranslatorEngine.couverture()
        ok("AVEU  le moteur déclare ce qu'il lit",
           couverture.get("xl/sharedStrings.xml") is True)
        ok("AVEU  et ce qu'il ne lit PAS encore",
           any(fait is False for fait in couverture.values()),
           "aucune partie non traitee declaree")
        ok("AVEU  le relevé transporte la liste des manques",
           len(extraction["workbook"]["non_traite"]) > 0)
        # L'ACCORD entre les deux façons de dire la même chose, et non le nom
        # d'une partie précise : nommer « pivot » ici obligeait à corriger ce
        # test le jour où les croisés seraient traités — un test qui doit être
        # réécrit à chaque progrès ne mesure pas le progrès, il le suit.
        ok("AVEU  la liste annoncée est EXACTEMENT celle des parties à faire",
           sorted(extraction["workbook"]["non_traite"])
           == sorted(n for n, fait in couverture.items() if not fait),
           str(extraction["workbook"]["non_traite"]))

        # ── 5. Le moteur reste indépendant ───────────────────────────────
        ok("COUCHES  importer le moteur ne charge aucun module `app`",
           not [m for m in sys.modules if m.startswith("app.")],
           str([m for m in sys.modules if m.startswith("app.")])[:120])
    finally:
        shutil.rmtree(travail, ignore_errors=True)

    print()
    n_ok = sum(1 for _, c, _ in checks if c)
    for nom, cond, detail in checks:
        ligne = ("  OK  " if cond else " FAIL ") + f"  {nom}"
        if not cond and detail:
            ligne += f"   [{detail}]"
        print(ligne)
    print(f"\n{n_ok}/{len(checks)}")
    return 0 if n_ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
