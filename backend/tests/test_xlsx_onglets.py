"""XLSX — traduire un nom d'onglet SANS casser les formules qui le citent.

LE DÉFAUT QUE CE TRAVAIL ÉVITE
------------------------------
Un nom d'onglet est visible en bas de la fenêtre : le laisser en langue source
dans un classeur par ailleurs traduit se remarque tout de suite. Mais il est
aussi RÉFÉRENCÉ, à trois endroits :

  · `<sheet name="…">` dans workbook.xml — le nom lui-même ;
  · `<definedName>` — les noms définis (`Zone_ventes` -> `Ventes!$A$1`) ;
  · `<f>` dans chaque feuille — les formules (`=Ventes!A2-Charges!A2`).

Renommer sans réécrire produit `#REF!` partout : l'utilisateur reçoit un
classeur ouvrable, d'apparence traduite, dont TOUS les calculs sont morts. Les
deux gestes ne font donc qu'un.

POURQUOI CE N'EST PAS UN `str.replace`
--------------------------------------
Quatre façons de casser un classeur avec un remplacement naïf, toutes réelles
et toutes vérifiées ici :

  · `="Ventes du mois"` — une chaîne LITTÉRALE qui contient le nom ;
  · « Ventes » et « Ventes2 » — deux onglets dont l'un préfixe l'autre ;
  · `[1]Ventes!A1` — un onglet d'un classeur EXTERNE, qu'on ne traduit pas ;
  · `'Chiffre d''affaires'!A1` — apostrophes doublées.

CE QU'ON REFUSE DE TRADUIRE, ET POURQUOI C'EST UN SUCCÈS
---------------------------------------------------------
Excel impose ses règles : 31 caractères, pas de `: \\ / ? * [ ]`, pas
d'apostrophe, pas de doublon. Une traduction qui les viole n'est PAS appliquée
— l'onglet garde son nom d'origine. Un onglet non traduit se voit ; un fichier
qu'Excel refuse d'ouvrir ne se rattrape pas.

    backend/venv/Scripts/python.exe backend/tests/test_xlsx_onglets.py
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile

import racine  # noqa: F401  -- met backend/ sur le chemin

import engines                                              # noqa: E402
from engines.xlsx.engine import (nom_onglet_valide,          # noqa: E402
                                 reecrire_references)
from fabrique_classeurs import construire_multi              # noqa: E402

_checks: list[tuple[bool, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _checks.append((bool(cond), label))
    print(f"  {'OK  ' if cond else 'ECHEC'} {label}"
          + (f"   [{detail}]" if detail and not cond else ""))


def _traduire(src: str, dossier: str, noms: dict[str, str]) -> str:
    """Traduit le classeur en ne changeant QUE les noms d'onglets donnés."""
    ex_json = os.path.join(dossier, "ex.json")
    extraction, _ = engines.new_engine("xlsx").extract_text(src, ex_json)
    for el in extraction["workbook"]["elements"]:
        if el["context"]["part"] == "sheetName":
            el["translated_text"] = noms.get(el["text"], el["text"])
        else:
            el["translated_text"] = el["text"]
    tr_json = os.path.join(dossier, "tr.json")
    with open(tr_json, "w", encoding="utf-8") as f:
        json.dump(extraction, f, ensure_ascii=False)
    sortie = os.path.join(dossier, "out.xlsx")
    ok, msg = engines.new_engine("xlsx").inject_translation(src, tr_json,
                                                            sortie)
    assert ok, msg
    return sortie


def main() -> int:
    travail = tempfile.mkdtemp(prefix="precis_test_xlsx_ong_")
    src = construire_multi(os.path.join(travail, "src.xlsx"))

    # ── 1. La réécriture des références, cas par cas ────────────────────────
    ren = {"Ventes": "Sales", "Charges": "Costs", "Ventes2": "Sales2",
           "Chiffre d affaires": "Revenue"}
    cas = [
        ("Ventes!A2-Charges!A2", "Sales!A2-Costs!A2", "deux onglets"),
        ("SUM(Ventes!A1:A9)", "SUM(Sales!A1:A9)", "dans une fonction"),
        ("Ventes:Charges!A1", "Sales:Costs!A1", "plage 3D"),
        ("'Chiffre d affaires'!A1", "Revenue!A1", "nom entre apostrophes"),
        ("[1]Ventes!A1", "[1]Ventes!A1", "classeur EXTERNE intouchable"),
        ('"Ventes du mois"', '"Ventes du mois"', "chaîne littérale"),
        ("Ventes2!A1", "Sales2!A1", "nom voisin non confondu"),
        ('CONCAT("Ventes!",Ventes!A1)', 'CONCAT("Ventes!",Sales!A1)',
         "littérale ET référence mêlées"),
        ("SUM(A1:A9)", "SUM(A1:A9)", "aucune référence d'onglet"),
    ]
    for source, attendu, quoi in cas:
        obtenu = reecrire_references(source, ren)
        check(obtenu == attendu, f"REECRIT {quoi}",
              f"{source!r} -> {obtenu!r}, attendu {attendu!r}")

    # ── 2. Les noms qu'Excel refuserait ────────────────────────────────────
    for nom, attendu, quoi in (
        ("Sales", True, "nom simple"),
        ("Bilan 2026", True, "espaces et chiffres autorisés"),
        ("", False, "nom vide"),
        ("a" * 32, False, "plus de 31 caractères"),
        ("Ven/tes", False, "caractère interdit"),
        ("L'ete", False, "apostrophe (délimiteur de référence)"),
    ):
        check(nom_onglet_valide(nom) is attendu, f"VALIDE  {quoi}",
              f"{nom[:12]!r} -> {nom_onglet_valide(nom)}")

    # ── 3. La chaîne complète, sur un vrai classeur ────────────────────────
    d1 = os.path.join(travail, "trad")
    os.makedirs(d1, exist_ok=True)
    sortie = _traduire(src, d1, {"Ventes": "Sales", "Charges": "Costs",
                                 "Synthese": "Summary"})
    with zipfile.ZipFile(sortie) as z:
        wb = z.read("xl/workbook.xml").decode("utf-8")
        sh2 = z.read("xl/worksheets/sheet2.xml").decode("utf-8")

    onglets = re.findall(r'<sheet name="([^"]+)"', wb)
    check(onglets == ["Sales", "Costs", "Summary"],
          "CHAINE les trois onglets sont renommés", str(onglets))

    definis = dict(re.findall(
        r'<definedName name="([^"]+)">([^<]*)</definedName>', wb))
    check(definis.get("Zone_ventes") == "Sales!$A$1:$B$4"
          and definis.get("Zone_charges") == "Costs!$A$1:$B$3",
          "CHAINE les NOMS DÉFINIS suivent le renommage", str(definis))

    formules = re.findall(r"<f>([^<]+)</f>", sh2)
    check(formules == ["Sales!A2-Costs!A2"],
          "CHAINE les FORMULES suivent le renommage", str(formules))

    # La preuve réelle : LibreOffice ouvre le classeur et rend ses 3 feuilles.
    # Une formule cassée n'empêche pas l'ouverture — mais un classeur illisible
    # se verrait ici, et c'est ce qu'on ne veut à aucun prix.
    import fitz
    from engines.office import convert_to_pdf
    with open(sortie, "rb") as f:
        pdf = convert_to_pdf(f.read(), "xlsx")
    with fitz.open("pdf", pdf) as doc:
        n_pages = doc.page_count
    check(n_pages == 3, "CHAINE LibreOffice ouvre le classeur renommé",
          f"{n_pages} page(s)")

    # ── 4. Le refus : un nom invalide laisse l'onglet INTACT ───────────────
    d2 = os.path.join(travail, "refus")
    os.makedirs(d2, exist_ok=True)
    sortie2 = _traduire(src, d2, {"Ventes": "Ven/tes:2026"})
    with zipfile.ZipFile(sortie2) as z:
        wb2 = z.read("xl/workbook.xml").decode("utf-8")
        sh2b = z.read("xl/worksheets/sheet2.xml").decode("utf-8")
    check(re.findall(r'<sheet name="([^"]+)"', wb2)[0] == "Ventes",
          "REFUS   un nom qu'Excel refuserait n'est PAS appliqué",
          str(re.findall(r'<sheet name="([^"]+)"', wb2)))
    check(re.findall(r"<f>([^<]+)</f>", sh2b) == ["Ventes!A2-Charges!A2"],
          "REFUS   et la formule reste donc COHÉRENTE avec l'onglet",
          str(re.findall(r"<f>([^<]+)</f>", sh2b)))

    # ── 5. La collision : deux onglets ne peuvent pas partager un nom ──────
    d3 = os.path.join(travail, "collision")
    os.makedirs(d3, exist_ok=True)
    sortie3 = _traduire(src, d3, {"Ventes": "Bilan", "Charges": "Bilan"})
    with zipfile.ZipFile(sortie3) as z:
        wb3 = z.read("xl/workbook.xml").decode("utf-8")
    noms3 = re.findall(r'<sheet name="([^"]+)"', wb3)
    check(len(set(n.casefold() for n in noms3)) == len(noms3),
          "COLLIS  deux onglets ne portent jamais le même nom", str(noms3))

    rates = [lbl for ok_, lbl in _checks if not ok_]
    print(f"\n{len(_checks) - len(rates)}/{len(_checks)} verifications")
    if rates:
        for lbl in rates:
            print(f"  ECHEC {lbl}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
