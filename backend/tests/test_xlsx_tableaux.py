"""XLSX — les en-têtes de tableaux structurés, alignés et non retraduits.

CE QUI EST EN JEU
-----------------
Un en-tête de tableau structuré vit à DEUX endroits qui doivent rester
rigoureusement identiques :

  · `<tableColumn name="Produit">` dans `xl/tables/table1.xml` ;
  · la CELLULE de la ligne d'en-tête, dans la feuille — déjà traduite via
    `sharedStrings`.

Excel refuse d'ouvrir un classeur où les deux divergent (« nous avons trouvé un
problème dans le contenu »). C'est un échec TOTAL : pas une colonne mal
traduite, un fichier qui ne s'ouvre pas.

POURQUOI ON N'ENVOIE PAS CES EN-TÊTES AU MODÈLE
------------------------------------------------
Les relever comme un texte de plus les soumettrait une SECONDE fois : le modèle
peut rendre « Product » ici et « Article » là. On fabriquerait exactement la
divergence qu'on veut éviter, et on paierait deux fois pour cela.

La règle est donc l'inverse d'un relevé : on ne traduit rien dans le fichier de
tableau, on RECOPIE ce que la cellule affiche désormais.

C'EST CE QUE MESURE CETTE SUITE, et le contrôle central n'est pas « l'en-tête
est traduit » mais « les DEUX endroits disent la même chose » — un accord entre
modules, pas une constante partagée entre le test et le code.

    backend/venv/Scripts/python.exe backend/tests/test_xlsx_tableaux.py
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile

import racine  # noqa: F401  -- met backend/ sur le chemin

import engines                                              # noqa: E402
from fabrique_classeurs import (TABLE_ENTETES,              # noqa: E402
                                TABLE_JAMAIS_TRADUIT,
                                construire_avec_tableau,
                                construire_multi)

_checks: list[tuple[bool, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _checks.append((bool(cond), label))
    print(f"  {'OK  ' if cond else 'ECHEC'} {label}"
          + (f"   [{detail}]" if detail and not cond else ""))


def _traduire(src: str, dossier: str, prefixe: str = "EN:") -> str:
    """Traduit tout sauf les noms d'onglets, en préfixant chaque run."""
    extraction, _ = engines.new_engine("xlsx").extract_text(
        src, os.path.join(dossier, "ex.json"))
    for el in extraction["workbook"]["elements"]:
        if el["context"]["part"] == "sheetName":
            el["translated_text"] = el["text"]
            continue
        el["translated_text"] = re.sub(
            r"(\[\[(\d+)\]\])(.*?)(\[\[/\2\]\])",
            lambda m: m.group(1) + prefixe + m.group(3) + m.group(4),
            el["text"])
    tr = os.path.join(dossier, "tr.json")
    with open(tr, "w", encoding="utf-8") as f:
        json.dump(extraction, f, ensure_ascii=False)
    sortie = os.path.join(dossier, "out.xlsx")
    ok, msg = engines.new_engine("xlsx").inject_translation(src, tr, sortie)
    assert ok, msg
    return sortie


def _magasin(texte_xml: str) -> list[str]:
    """Le contenu de chaque `<si>`, runs recollés."""
    return ["".join(re.findall(r"<t[^>]*>([^<]*)</t>", si))
            for si in re.findall(r"<si>(.*?)</si>", texte_xml, re.S)]


def _col(ref: str) -> int:
    """« C2 » → 2. Recalculé ICI, sans appeler le moteur.

    Réutiliser `_colonne_de_ref` du moteur ferait partager au test l'erreur
    qu'il cherche : le test doit mesurer l'ACCORD entre deux calculs
    indépendants, pas la cohérence d'un calcul avec lui-même.
    """
    n = 0
    for c in "".join(x for x in ref if x.isalpha()).upper():
        n = n * 26 + (ord(c) - 64)
    return n - 1


def _entetes_affiches(table_xml: str, feuille_xml: str,
                      magasin: list[str]) -> list[str]:
    """Ce que la FEUILLE affiche là où le `ref=` du tableau dit son en-tête.

    Les bornes sont lues dans le fichier de tableau — ligne d'en-tête et
    colonne de départ — puis appliquées à la feuille. Rien n'est écrit en dur :
    déplacer le tableau dans la fabrique déplace ce que ce contrôle lit.
    """
    ref = re.search(r"<table[^>]*\sref=\"([^\"]*)\"", table_xml).group(1)
    debut, fin = ref.split(":")
    ligne = "".join(c for c in debut if c.isdigit())
    premiere, derniere = _col(debut), _col(fin)

    bloc = re.search(rf"<row r=\"{ligne}\".*?</row>", feuille_xml, re.S)
    if not bloc:
        return []
    out = []
    for cellule in re.findall(r"<c [^>]*r=\"([^\"]*)\"[^>]*>(.*?)</c>",
                              bloc.group(0), re.S):
        ref_cell, corps = cellule
        if not (premiere <= _col(ref_cell) <= derniere):
            continue          # hors du tableau : un titre, une marge
        idx = re.search(r"<v>(\d+)</v>", corps)
        if idx and int(idx.group(1)) < len(magasin):
            out.append(magasin[int(idx.group(1))])
    return out


def main() -> int:
    travail = tempfile.mkdtemp(prefix="precis_test_xlsx_tab_")
    src = construire_avec_tableau(os.path.join(travail, "src.xlsx"))

    # ── 1. Le relevé : l'en-tête part UNE FOIS, par la cellule ────────────
    extraction, _ = engines.new_engine("xlsx").extract_text(
        src, os.path.join(travail, "ex0.json"))
    elements = extraction["workbook"]["elements"]

    check(not [e for e in elements if e["context"]["part"] == "table"],
          "RELEVE  le fichier de TABLEAU ne relève aucun texte",
          str([e["context"]["part"] for e in elements]))

    # Le contre-test qui donne son sens au précédent : si les en-têtes
    # n'étaient relevés NULLE PART, le contrôle ci-dessus passerait pour une
    # bonne raison apparente et une mauvaise raison réelle.
    from engines import runtags
    tous = [runtags.sans_balises(e["text"]) for e in elements]
    check(all(h in tous for h in TABLE_ENTETES),
          "RELEVE  mais chaque en-tête part BIEN une fois, via sa cellule",
          f"{[h for h in TABLE_ENTETES if h not in tous]} manquant(s)")
    check(sum(1 for t in tous if t in TABLE_ENTETES) == len(TABLE_ENTETES),
          "RELEVE  une fois exactement — jamais en double", str(tous))

    # ── 2. L'accord, qui est le vrai sujet ────────────────────────────────
    sortie = _traduire(src, travail)
    with zipfile.ZipFile(sortie) as z:
        abime = z.testzip()
        table = z.read("xl/tables/table1.xml").decode("utf-8")
        shared = z.read("xl/sharedStrings.xml").decode("utf-8")
        feuille = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    check(abime is None, "INJECTE l'archive reste intacte", str(abime))

    noms_colonnes = re.findall(r"<tableColumn[^>]*name=\"([^\"]*)\"", table)
    magasin = _magasin(shared)

    # LE CONTRÔLE CENTRAL. On ne compare pas à une valeur écrite dans le test :
    # on lit dans la FEUILLE les cellules que le `ref=` du tableau désigne
    # comme sa ligne d'en-tête, et on exige que le TABLEAU déclare exactement
    # cela. Une mutation qui traduirait le tableau séparément casse ici, quelle
    # que soit la traduction choisie.
    #
    # Les bornes viennent du `ref=` LU DANS LE FICHIER, jamais d'une constante
    # du test : sans quoi le test et le code partageraient l'hypothèse même
    # qu'il s'agit de vérifier.
    affiches = _entetes_affiches(table, feuille, magasin)

    check(bool(affiches) and noms_colonnes == affiches,
          "ACCORD  le TABLEAU déclare exactement ce que la FEUILLE affiche",
          f"tableau={noms_colonnes} feuille={affiches}")

    # Et l'alignement a bien eu lieu — sans quoi l'accord serait celui de deux
    # textes restés en langue source, vrai mais sans valeur.
    check(all(n.startswith("EN:") for n in noms_colonnes) and noms_colonnes,
          "ACCORD  et ce texte commun est bien le texte TRADUIT",
          str(noms_colonnes))

    # ── 3. Le nom du TABLEAU est un identifiant ───────────────────────────
    check(all(f'name="{n}"' in table for n in TABLE_JAMAIS_TRADUIT),
          "IDENTITE le nom du TABLEAU est rendu intact",
          str(re.findall(r"<table[^>]*name=\"([^\"]*)\"", table)))

    # ── 4. Les cas où l'on ne touche à rien ───────────────────────────────
    # Une colonne dont l'en-tête n'est pas du texte n'a aucune preuve de ce
    # qu'elle doit devenir : on garde son nom plutôt que d'inventer.
    sans_entete = os.path.join(travail, "sans.xlsx")
    construire_avec_tableau(sans_entete)
    _forcer_header_row_count_zero(sans_entete)
    d2 = os.path.join(travail, "d2")
    os.makedirs(d2, exist_ok=True)
    out2 = _traduire(sans_entete, d2)
    with zipfile.ZipFile(out2) as z:
        table2 = z.read("xl/tables/table1.xml").decode("utf-8")
    noms2 = re.findall(r"<tableColumn[^>]*name=\"([^\"]*)\"", table2)
    check(noms2 == TABLE_ENTETES,
          "PRUDENCE un tableau SANS ligne d'en-tête garde ses noms",
          str(noms2))

    # ── 5. Les références structurées suivent le renommage ────────────────
    avec_formule = os.path.join(travail, "formule.xlsx")
    construire_avec_tableau(avec_formule)
    _ajouter_formule_structuree(avec_formule)
    d3 = os.path.join(travail, "d3")
    os.makedirs(d3, exist_ok=True)
    out3 = _traduire(avec_formule, d3)
    with zipfile.ZipFile(out3) as z:
        feuille3 = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    formules = re.findall(r"<f>([^<]*)</f>", feuille3)
    check(any("[EN:Produit]" in f for f in formules),
          "REFERENCE une référence structurée suit le renommage",
          str(formules))
    # Le piège : une COLONNE s'appelle « #Totals ». Sans la garde sur le `#`,
    # le SPÉCIFICATEUR `[#Totals]` serait pris pour elle et réécrit.
    check(any("[#Totals]" in f for f in formules),
          "REFERENCE un SPÉCIFICATEUR homonyme d'une colonne reste intact",
          str(formules))

    # ── 6. Un classeur sans tableau ───────────────────────────────────────
    nu = construire_multi(os.path.join(travail, "nu.xlsx"))
    d4 = os.path.join(travail, "d4")
    os.makedirs(d4, exist_ok=True)
    check(os.path.exists(_traduire(nu, d4)),
          "ABSENT  un classeur sans tableau s'injecte quand même")

    rates = [lbl for ok_, lbl in _checks if not ok_]
    print(f"\n{len(_checks) - len(rates)}/{len(_checks)} verifications")
    if rates:
        for lbl in rates:
            print(f"  ECHEC {lbl}")
        return 1
    return 0


def _rejouer(chemin: str, membre: str, nouveau: str) -> None:
    """Réécrit un membre d'un ZIP existant, les autres inchangés."""
    with zipfile.ZipFile(chemin) as z:
        contenu = {n: z.read(n) for n in z.namelist()}
    contenu[membre] = nouveau.encode("utf-8")
    with zipfile.ZipFile(chemin, "w", zipfile.ZIP_DEFLATED) as z:
        for nom, octets in contenu.items():
            z.writestr(nom, octets)


def _forcer_header_row_count_zero(chemin: str) -> None:
    with zipfile.ZipFile(chemin) as z:
        table = z.read("xl/tables/table1.xml").decode("utf-8")
    _rejouer(chemin, "xl/tables/table1.xml",
             table.replace("<table ", '<table headerRowCount="0" ', 1))


def _ajouter_formule_structuree(chemin: str) -> None:
    """Ajoute une ligne portant `SUM(Tableau1[Quantite vendue])`.

    Et un `[#Totals]` : un SPÉCIFICATEUR du format, pas un nom de colonne.

    LE PIÈGE EST ARMÉ VOLONTAIREMENT. Une colonne du tableau s'appelle ici
    « #Totals » — Excel l'autorise, et c'est le SEUL cas où la garde sur le
    `#` change quelque chose. Sans elle, le spécificateur `[#Totals]` de la
    formule serait pris pour cette colonne et réécrit : la formule devient
    invalide, alors qu'aucune colonne n'a bougé de place.

    Sans cette collision, le contrôle passerait pour une mauvaise raison — le
    nom ne serait simplement pas dans la table de renommage.
    """
    with zipfile.ZipFile(chemin) as z:
        feuille = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        table = z.read("xl/tables/table1.xml").decode("utf-8")
        shared = z.read("xl/sharedStrings.xml").decode("utf-8")

    # La 2ᵉ colonne s'appelle « #Totals », des deux côtés (tableau ET cellule)
    # — c'est l'état cohérent dont part un vrai classeur.
    _rejouer(chemin, "xl/tables/table1.xml",
             table.replace('name="Quantite vendue"', 'name="#Totals"'))
    _rejouer(chemin, "xl/sharedStrings.xml",
             shared.replace("<t>Quantite vendue</t>", "<t>#Totals</t>"))

    ajout = ('<row r="5"><c r="B5">'
             '<f>SUM(Tableau1[#Totals])+SUM(Tableau1[Produit])</f>'
             '<v>42</v></c></row>')
    _rejouer(chemin, "xl/worksheets/sheet1.xml",
             feuille.replace("</sheetData>", ajout + "</sheetData>"))


if __name__ == "__main__":
    raise SystemExit(main())
