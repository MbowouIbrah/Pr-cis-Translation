"""XLSX — les tableaux croisés, où le texte est DUPLIQUÉ.

CE QUI EST EN JEU
-----------------
Un croisé range son texte à deux endroits, et ils ne jouent pas le même rôle :

  · le CACHE (`pivotCacheDefinition.xml`) est une COPIE de la source — nom des
    champs et valeurs distinctes, recopiés de la feuille ;
  · la TABLE (`pivotTable1.xml`) porte les libellés PROPRES au croisé —
    `dataCaption`, `rowHeaderCaption`, le nom d'un `<dataField>` — et renvoie
    au cache PAR INDEX pour tout le reste.

LA RÈGLE QUI EN DÉCOULE, ET QUI EST LE SUJET DE CETTE SUITE
------------------------------------------------------------
On TRADUIT ce qui n'existe que dans la table. On ALIGNE ce que le cache
duplique — c'est-à-dire qu'on y recopie la traduction déjà faite pour la
cellule, sans jamais soumettre ce texte une seconde fois au modèle.

Traduire les deux séparément produirait deux formulations pour la même donnée
(« Produit » d'un côté, « Article » de l'autre) : le croisé cesserait de
correspondre à sa source. Et ce serait doublement perdu, puisque Excel réécrit
le cache depuis la source au premier rafraîchissement.

CE QU'ON NE TOUCHE JAMAIS
-------------------------
Les `<item x="0"/>` de la table renvoient aux `<sharedItems>` PAR INDEX.
L'ordre est le lien : y ajouter, en retirer ou en réordonner une seule
réattribuerait les lignes du croisé. L'alignement remplace SUR PLACE.

    backend/venv/Scripts/python.exe backend/tests/test_xlsx_croises.py
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
from fabrique_classeurs import (PIVOT_DUPLIQUES,             # noqa: E402
                                PIVOT_JAMAIS_TRADUIT,
                                PIVOT_TRADUISIBLES,
                                construire_avec_croise,
                                construire_multi)

_checks: list[tuple[bool, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _checks.append((bool(cond), label))
    print(f"  {'OK  ' if cond else 'ECHEC'} {label}"
          + (f"   [{detail}]" if detail and not cond else ""))


def _traduire(src: str, dossier: str) -> str:
    """Traduit tout sauf les noms d'onglets, en préfixant chaque run."""
    extraction, _ = engines.new_engine("xlsx").extract_text(
        src, os.path.join(dossier, "ex.json"))
    for el in extraction["workbook"]["elements"]:
        if el["context"]["part"] == "sheetName":
            el["translated_text"] = el["text"]
            continue
        el["translated_text"] = re.sub(
            r"(\[\[(\d+)\]\])(.*?)(\[\[/\2\]\])",
            lambda m: m.group(1) + "EN:" + m.group(3) + m.group(4),
            el["text"])
    tr = os.path.join(dossier, "tr.json")
    with open(tr, "w", encoding="utf-8") as f:
        json.dump(extraction, f, ensure_ascii=False)
    sortie = os.path.join(dossier, "out.xlsx")
    ok, msg = engines.new_engine("xlsx").inject_translation(src, tr, sortie)
    assert ok, msg
    return sortie


def main() -> int:
    travail = tempfile.mkdtemp(prefix="precis_test_xlsx_tcd_")
    src = construire_avec_croise(os.path.join(travail, "src.xlsx"))

    # ── 1. Le relevé : une fois, et une seule ─────────────────────────────
    extraction, _ = engines.new_engine("xlsx").extract_text(
        src, os.path.join(travail, "ex0.json"))
    elements = extraction["workbook"]["elements"]
    tous = [runtags.sans_balises(e["text"]) for e in elements]

    check(all(t in tous for t in PIVOT_TRADUISIBLES),
          "RELEVE  les libellés PROPRES au croisé sont relevés",
          str([t for t in PIVOT_TRADUISIBLES if t not in tous]))

    # LE CONTRÔLE QUI PORTE TOUT LE SUJET. Chaque texte dupliqué ne doit
    # partir au modèle QU'UNE FOIS — par sa cellule. S'il partait aussi par le
    # cache, on paierait deux traductions pour obtenir deux formulations
    # divergentes.
    doubles = {t: tous.count(t) for t in PIVOT_DUPLIQUES if tous.count(t) > 1}
    check(not doubles,
          "RELEVE  aucun texte DUPLIQUÉ n'est relevé deux fois", str(doubles))

    # Contre-test : ces textes partent bien UNE fois. Sans lui, un moteur qui
    # ne relèverait rien du tout passerait le contrôle ci-dessus.
    check(all(t in tous for t in PIVOT_DUPLIQUES),
          "RELEVE  mais ils partent bien une fois, via leur cellule",
          str([t for t in PIVOT_DUPLIQUES if t not in tous]))

    check(all(n not in tous for n in PIVOT_JAMAIS_TRADUIT),
          "RELEVE  l'IDENTIFIANT du croisé n'est pas relevé",
          str([n for n in PIVOT_JAMAIS_TRADUIT if n in tous]))

    # ── 2. L'injection ────────────────────────────────────────────────────
    sortie = _traduire(src, travail)
    with zipfile.ZipFile(sortie) as z:
        abime = z.testzip()
        tcd = z.read("xl/pivotTables/pivotTable1.xml").decode("utf-8")
        cache = z.read(
            "xl/pivotCache/pivotCacheDefinition1.xml").decode("utf-8")
        shared = z.read("xl/sharedStrings.xml").decode("utf-8")
    check(abime is None, "INJECTE l'archive reste intacte", str(abime))

    captions = dict(re.findall(r"(\w+Caption)=\"([^\"]*)\"", tcd))
    check(captions and all(v.startswith("EN:") for v in captions.values()),
          "INJECTE les libellés du croisé sont traduits", str(captions))
    champs = re.findall(r"<dataField[^>]*name=\"([^\"]*)\"", tcd)
    check(champs and all(c.startswith("EN:") for c in champs),
          "INJECTE le nom d'un champ de valeurs l'est aussi", str(champs))

    # ── 3. L'ACCORD cache ↔ feuille, qui est le vrai sujet ────────────────
    magasin = ["".join(re.findall(r"<t[^>]*>([^<]*)</t>", si))
               for si in re.findall(r"<si>(.*?)</si>", shared, re.S)]
    noms_champs = re.findall(r"<cacheField[^>]*name=\"([^\"]*)\"", cache)
    valeurs_cache = re.findall(r"<s v=\"([^\"]*)\"", cache)

    # On ne compare à aucune constante du test : tout ce que le cache déclare
    # doit se retrouver TEL QUEL dans le magasin de la feuille. Une mutation
    # qui traduirait le cache séparément produirait un texte absent du magasin.
    absents = [t for t in noms_champs + valeurs_cache if t not in magasin]
    check(not absents,
          "ACCORD  tout ce que le CACHE déclare existe dans la FEUILLE",
          f"{absents} introuvable(s) dans {magasin}")

    check(all(t.startswith("EN:") for t in valeurs_cache) and valeurs_cache,
          "ACCORD  et les valeurs du cache sont bien les TRADUITES",
          str(valeurs_cache))
    check(all(t.startswith("EN:") for t in noms_champs) and noms_champs,
          "ACCORD  les noms de champs aussi", str(noms_champs))

    # ── 4. Les index, intacts ─────────────────────────────────────────────
    items_avant = re.findall(r"<item x=\"(\d+)\"", _lire(
        src, "xl/pivotTables/pivotTable1.xml"))
    items_apres = re.findall(r"<item x=\"(\d+)\"", tcd)
    check(items_avant == items_apres,
          "INDEX   les `<item x=>` ne sont ni réordonnés ni renumérotés",
          f"{items_avant} -> {items_apres}")
    check(len(re.findall(r"<s v=", _lire(
        src, "xl/pivotCache/pivotCacheDefinition1.xml")))
        == len(valeurs_cache),
        "INDEX   le cache garde EXACTEMENT autant de valeurs")

    check(all(f'name="{n}"' in tcd for n in PIVOT_JAMAIS_TRADUIT),
          "IDENTITE le nom du croisé est rendu intact",
          str(re.findall(r"<pivotTableDefinition[^>]*name=\"([^\"]*)\"", tcd)))

    # ── 5. Un classeur sans croisé ────────────────────────────────────────
    nu = construire_multi(os.path.join(travail, "nu.xlsx"))
    d2 = os.path.join(travail, "d2")
    os.makedirs(d2, exist_ok=True)
    ex2, _ = engines.new_engine("xlsx").extract_text(
        nu, os.path.join(d2, "ex.json"))
    parts = {e["context"]["part"] for e in ex2["workbook"]["elements"]}
    check(not ({"pivotTable", "pivotDataField"} & parts),
          "ABSENT  un classeur sans croisé n'en relève rien", str(parts))
    check(os.path.exists(_traduire(nu, d2)),
          "ABSENT  et son injection réussit quand même")

    rates = [lbl for ok_, lbl in _checks if not ok_]
    print(f"\n{len(_checks) - len(rates)}/{len(_checks)} verifications")
    if rates:
        for lbl in rates:
            print(f"  ECHEC {lbl}")
        return 1
    return 0


def _lire(xlsx: str, membre: str) -> str:
    with zipfile.ZipFile(xlsx) as z:
        return z.read(membre).decode("utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
