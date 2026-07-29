"""XLSX — traduire un graphique sans dupliquer le texte de la feuille.

CE QUI EST EN JEU
-----------------
Un graphique est ce qu'on regarde en premier dans un classeur : son titre et
ses axes non traduits sautent aux yeux, même quand tout le reste est juste.

LA DIFFÉRENCE AVEC LE MOTEUR PPTX, ET POURQUOI ON NE LE RECOPIE PAS
--------------------------------------------------------------------
Le PPTX traduit les `<c:v>` d'un graphique, et il a raison : là-bas, le
graphique porte ses PROPRES données.

Dans un classeur, c'est faux. Un `<c:v>` sous un `<c:strCache>` est le CACHE
d'une cellule de la feuille — Excel y recopie ce qu'affiche `Ventes!$B$1` pour
dessiner sans relire le classeur. Or cette cellule est DÉJÀ traduite via
`sharedStrings`. Traduire aussi son cache, c'est :

  · soumettre deux fois le même texte au modèle, qui peut rendre deux
    formulations différentes — le graphique afficherait alors autre chose que
    sa feuille ;
  · pour rien : Excel réécrit le cache depuis la cellule au premier
    rafraîchissement.

MESURÉ sur un graphique produit par Excel (titre + 2 axes + série +
catégories) : les 7 `<c:v>` sont TOUS sous un cache, et les 3 vrais textes du
graphique sont TOUS des `<a:t>`.

Reste le `<c:v>` SANS cache au-dessus : un texte saisi en dur, qui n'est le
reflet de rien. Celui-là se traduit — c'est le sens du test d'ancêtres, et le
classeur d'essai en contient un exprès.

    backend/venv/Scripts/python.exe backend/tests/test_xlsx_graphiques.py
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile

import racine  # noqa: F401  -- met backend/ sur le chemin

import engines                                              # noqa: E402
from engines import runtags                                  # noqa: E402
from engines.xlsx.engine import NS                           # noqa: E402
from fabrique_classeurs import (CHART_INTOUCHABLES,          # noqa: E402
                                CHART_TRADUISIBLES,
                                construire_avec_graphique)
from lxml import etree                                       # noqa: E402

_checks: list[tuple[bool, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _checks.append((bool(cond), label))
    print(f"  {'OK  ' if cond else 'ECHEC'} {label}"
          + (f"   [{detail}]" if detail and not cond else ""))


def main() -> int:
    travail = tempfile.mkdtemp(prefix="precis_test_xlsx_gr_")
    src = construire_avec_graphique(os.path.join(travail, "src.xlsx"))

    # ── 1. Le relevé : ce qu'on prend, ce qu'on laisse ─────────────────────
    extraction, _ = engines.new_engine("xlsx").extract_text(
        src, os.path.join(travail, "ex.json"))
    du_graphique = [e for e in extraction["workbook"]["elements"]
                    if e["context"]["part"] == "chart"]
    textes = [runtags.sans_balises(e["text"]) for e in du_graphique]

    check(textes == CHART_TRADUISIBLES,
          "RELEVE  les textes du graphique sont relevés, dans l'ordre",
          f"{textes} au lieu de {CHART_TRADUISIBLES}")
    check(all(t not in textes for t in CHART_INTOUCHABLES),
          "RELEVE  les CACHES de cellules ne sont PAS relevés",
          f"{[t for t in CHART_INTOUCHABLES if t in textes]} relevé(s) à tort")
    # Le contre-test qui donne son sens au précédent : le classeur d'essai
    # contient bien des caches. Sans eux, le contrôle passerait à vide.
    with zipfile.ZipFile(src) as z:
        chart_src = z.read("xl/charts/chart1.xml").decode("utf-8")
    check("strCache" in chart_src and "numCache" in chart_src,
          "RELEVE  le piège est ARMÉ (le graphique a bien des caches)")
    # Et un `<c:v>` SANS cache est bien pris — sinon la règle serait
    # simplement « ignorer tous les <c:v> », ce qui perdrait ce texte.
    check("Legende en dur" in textes,
          "RELEVE  un <c:v> SANS cache (texte en dur) est bien pris",
          str(textes))

    # ── 2. L'injection ────────────────────────────────────────────────────
    for el in extraction["workbook"]["elements"]:
        if el["context"]["part"] == "sheetName":
            el["translated_text"] = el["text"]
            continue
        el["translated_text"] = re.sub(
            r"(\[\[(\d+)\]\])(.*?)(\[\[/\2\]\])",
            lambda m: m.group(1) + "EN:" + m.group(3) + m.group(4),
            el["text"])
    tr_json = os.path.join(travail, "tr.json")
    with open(tr_json, "w", encoding="utf-8") as f:
        json.dump(extraction, f, ensure_ascii=False)

    sortie = os.path.join(travail, "out.xlsx")
    ok, msg = engines.new_engine("xlsx").inject_translation(src, tr_json,
                                                            sortie)
    check(ok, "INJECTE l'injection réussit", msg)

    with zipfile.ZipFile(sortie) as z:
        abime = z.testzip()
        chart = z.read("xl/charts/chart1.xml").decode("utf-8")
    check(abime is None, "INJECTE l'archive reste intacte", str(abime))

    racine_xml = etree.fromstring(chart.encode("utf-8"))
    a_t = [t.text for t in racine_xml.xpath("//a:t", namespaces=NS)]
    c_v = [v.text for v in racine_xml.xpath("//c:v", namespaces=NS)]

    check(all((t or "").startswith("EN:") for t in a_t),
          "INJECTE le texte riche (<a:t>) est traduit", str(a_t))
    check(all(t in c_v for t in CHART_INTOUCHABLES),
          "INJECTE les CACHES sont rendus INTACTS", str(c_v))
    check("EN:Legende en dur" in c_v,
          "INJECTE le <c:v> sans cache est bien traduit", str(c_v))

    # ── 3. Un classeur SANS graphique ne casse pas ────────────────────────
    # `xl/charts/` n'existe pas dans la plupart des classeurs : le releveur et
    # l'injecteur doivent s'en accommoder sans rien lever.
    from fabrique_classeurs import construire_multi
    nu = construire_multi(os.path.join(travail, "nu.xlsx"))
    ex2, _ = engines.new_engine("xlsx").extract_text(
        nu, os.path.join(travail, "ex2.json"))
    check(not [e for e in ex2["workbook"]["elements"]
               if e["context"]["part"] == "chart"],
          "ABSENT  un classeur sans graphique ne relève rien de tel")
    for el in ex2["workbook"]["elements"]:
        el["translated_text"] = el["text"]
    tr2 = os.path.join(travail, "tr2.json")
    with open(tr2, "w", encoding="utf-8") as f:
        json.dump(ex2, f, ensure_ascii=False)
    ok2, msg2 = engines.new_engine("xlsx").inject_translation(
        nu, tr2, os.path.join(travail, "out2.xlsx"))
    check(ok2, "ABSENT  et son injection réussit quand même", msg2)

    rates = [lbl for ok_, lbl in _checks if not ok_]
    print(f"\n{len(_checks) - len(rates)}/{len(_checks)} verifications")
    if rates:
        for lbl in rates:
            print(f"  ECHEC {lbl}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
