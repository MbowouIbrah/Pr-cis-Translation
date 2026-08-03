"""XLSX — formats de nombre personnalisés et propriétés du document.

CE QUI EST EN JEU
-----------------
Un format de nombre peut afficher du texte à côté du chiffre :
`#,##0" F CFA"`, `0" jours"`. C'est aussi visible que la cellule elle-même —
une colonne de montants qui garde « F CFA » dans un document traduit en
anglais se remarque immédiatement.

MAIS UN `formatCode` EST UN MINI-LANGAGE, PAS UNE PHRASE
---------------------------------------------------------
Seul est du TEXTE ce qui se trouve entre guillemets. Tout le reste est de la
SYNTAXE :

    #,##0" F CFA"          `#,##0` = emplacements de chiffres
    ...;[Red]-#,##0" u"    `;` sépare les sections, `[Red]` est une couleur
    [$-40C]jjjj\\ j\\ mmmm   une locale, puis un motif de date

ET L'ERREUR NE SE VOIT PAS. Un format invalide n'ouvre aucune boîte de
dialogue : Excel le remplace silencieusement par « Standard », et toute la
colonne change d'apparence. C'est pourquoi cette suite vérifie autant ce qui
NE DOIT PAS bouger que ce qui doit être traduit.

LE PIÈGE DES SECTIONS
---------------------
`#,##0" unites";[Red]-#,##0" unites"` porte DEUX fois le même littéral — une
section pour les positifs, une pour les négatifs. N'en traduire qu'un
afficherait les nombres négatifs dans l'autre langue.

LES PROPRIÉTÉS DU DOCUMENT
--------------------------
Titre, sujet, mots-clés, description : visibles dans les propriétés du fichier.
`dc:creator` est un NOM DE PERSONNE, et les dates sont des dates — ni l'un ni
les autres ne se traduisent, exactement comme les `<author>` d'un commentaire.

    backend/venv/Scripts/python.exe backend/tests/test_xlsx_formats.py
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
from engines.xlsx.engine import (morceaux_de_format,          # noqa: E402
                                 remplacer_morceaux_de_format)
from fabrique_classeurs import (CORE_JAMAIS_TRADUIT,          # noqa: E402
                                CORE_TRADUISIBLES,
                                FORMATS_SYNTAXE,
                                FORMATS_TRADUISIBLES,
                                construire_avec_styles,
                                construire_multi)

_checks: list[tuple[bool, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _checks.append((bool(cond), label))
    print(f"  {'OK  ' if cond else 'ECHEC'} {label}"
          + (f"   [{detail}]" if detail and not cond else ""))


def _traduire(src: str, dossier: str) -> str:
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
    travail = tempfile.mkdtemp(prefix="precis_test_xlsx_fmt_")
    src = construire_avec_styles(os.path.join(travail, "src.xlsx"))

    # ── 1. Le découpage d'un formatCode, isolément ────────────────────────
    # La brique la plus délicate se teste seule, avant tout classeur : c'est
    # elle qui décide de ce qui est du texte et de ce qui est du code.
    check(morceaux_de_format('#,##0" F CFA"') == [" F CFA"],
          "DECOUPE le littéral entre guillemets est du TEXTE",
          str(morceaux_de_format('#,##0" F CFA"')))
    check(morceaux_de_format("0.0%") == [],
          "DECOUPE un format sans littéral ne rend RIEN",
          str(morceaux_de_format("0.0%")))
    check(morceaux_de_format('#,##0" u";[Red]-#,##0" u"') == [" u", " u"],
          "DECOUPE les DEUX sections rendent chacune leur littéral",
          str(morceaux_de_format('#,##0" u";[Red]-#,##0" u"')))
    check(morceaux_de_format('#,##0" "') == [],
          "DECOUPE un littéral d'ESPACEMENT n'est pas un texte",
          str(morceaux_de_format('#,##0" "')))

    # La reconstruction ne touche QU'À l'intérieur des guillemets.
    reconstruit = remplacer_morceaux_de_format(
        '#,##0" u";[Red]-#,##0" u"', [" x", " y"])
    check(reconstruit == '#,##0" x";[Red]-#,##0" y"',
          "DECOUPE la reconstruction remet chaque section à sa place",
          reconstruit)
    # Un guillemet dans la traduction couperait le format en deux.
    check('""' not in remplacer_morceaux_de_format('0" a"', ['b"c'])
          and remplacer_morceaux_de_format('0" a"', ['b"c']) == '0"bc"',
          "DECOUPE un GUILLEMET dans la traduction est retiré",
          remplacer_morceaux_de_format('0" a"', ['b"c']))

    # ── 2. Le relevé ──────────────────────────────────────────────────────
    extraction, _ = engines.new_engine("xlsx").extract_text(
        src, os.path.join(travail, "ex0.json"))
    elements = extraction["workbook"]["elements"]
    tous = [runtags.sans_balises(e["text"]) for e in elements]

    des_formats = [e for e in elements
                   if e["context"]["part"] == "numberFormat"]
    check(len(des_formats) == 2,
          "RELEVE  seuls les formats PORTEURS de texte sont relevés",
          str([e["context"]["numFmtId"] for e in des_formats]))

    check(all(any(t in x for x in tous) for t in FORMATS_TRADUISIBLES),
          "RELEVE  le texte littéral d'un format est relevé",
          str([t for t in FORMATS_TRADUISIBLES
               if not any(t in x for x in tous)]))
    check(not [s for s in FORMATS_SYNTAXE if any(s in x for x in tous)],
          "RELEVE  aucune SYNTAXE ne part au modèle",
          str([s for s in FORMATS_SYNTAXE if any(s in x for x in tous)]))

    check(all(t in tous for t in CORE_TRADUISIBLES),
          "RELEVE  les propriétés du document sont relevées",
          str([t for t in CORE_TRADUISIBLES if t not in tous]))
    check(all(t not in tous for t in CORE_JAMAIS_TRADUIT),
          "RELEVE  ni l'AUTEUR ni les DATES n'y figurent",
          str([t for t in CORE_JAMAIS_TRADUIT if t in tous]))

    # ── 3. L'injection ────────────────────────────────────────────────────
    sortie = _traduire(src, travail)
    with zipfile.ZipFile(sortie) as z:
        abime = z.testzip()
        styles = z.read("xl/styles.xml").decode("utf-8")
        core = z.read("docProps/core.xml").decode("utf-8")
    check(abime is None, "INJECTE l'archive reste intacte", str(abime))

    codes = re.findall(r'formatCode="([^"]*)"', styles)
    # Les entités XML sont rendues par le parseur : on compare sur le texte.
    codes = [c.replace("&quot;", '"') for c in codes]

    traduits = [c for c in codes if "EN:" in c]
    check(len(traduits) == 2,
          "INJECTE les deux formats porteurs de texte sont traduits",
          str(codes))

    # LE CONTRÔLE CENTRAL : la syntaxe survit. On ne vérifie pas « le format
    # ressemble à ceci » — on exige que chaque élément de syntaxe présent au
    # départ soit encore là.
    manquants = [s for s in FORMATS_SYNTAXE
                 if not any(s in c for c in codes)]
    check(not manquants,
          "SYNTAXE toute la syntaxe d'origine est encore là", str(manquants))

    # Le format à deux sections : les DEUX doivent avoir suivi.
    deux_sections = [c for c in codes if ";" in c]
    check(deux_sections and deux_sections[0].count("EN:") == 2,
          "SYNTAXE les DEUX sections d'un format sont traduites",
          str(deux_sections))

    # Et le format SANS littéral n'a pas bougé d'un caractère.
    check("0.0%" in codes, "SYNTAXE un format sans texte est rendu INTACT",
          str(codes))
    check(any(c.startswith("[$-40C]") for c in codes),
          "SYNTAXE une locale et un motif de date sont intacts", str(codes))

    # Aucun format ne peut avoir un nombre impair de guillemets : ce serait un
    # littéral non fermé, donc un format invalide — l'échec silencieux.
    check(all(c.count('"') % 2 == 0 for c in codes),
          "SYNTAXE aucun littéral n'est laissé ouvert", str(codes))

    # ── 4. Les propriétés ─────────────────────────────────────────────────
    valeurs = dict(re.findall(r"<(?:dc|cp):(\w+)>([^<]*)</(?:dc|cp):\w+>",
                              core))
    check(all(valeurs.get(k, "").startswith("EN:")
              for k in ("title", "subject", "description", "keywords")),
          "INJECTE titre, sujet, mots-clés et description sont traduits",
          str(valeurs))
    check(valeurs.get("creator") == "Marie Durand"
          and valeurs.get("lastModifiedBy") == "Marie Durand",
          "IDENTITE l'auteur est rendu intact", str(valeurs))
    check(CORE_JAMAIS_TRADUIT[1] in core,
          "IDENTITE la date de création est rendue intacte", core[:200])

    # ── 5. Un classeur sans styles ni propriétés ──────────────────────────
    nu = construire_multi(os.path.join(travail, "nu.xlsx"))
    d2 = os.path.join(travail, "d2")
    os.makedirs(d2, exist_ok=True)
    ex2, _ = engines.new_engine("xlsx").extract_text(
        nu, os.path.join(d2, "ex.json"))
    parts = {e["context"]["part"] for e in ex2["workbook"]["elements"]}
    check(not ({"numberFormat", "documentProperty"} & parts),
          "ABSENT  un classeur sans styles n'en relève rien", str(parts))
    check(os.path.exists(_traduire(nu, d2)),
          "ABSENT  et son injection réussit quand même")

    rates = [lbl for ok_, lbl in _checks if not ok_]
    print(f"\n{len(_checks) - len(rates)}/{len(_checks)} verifications")
    if rates:
        for lbl in rates:
            print(f"  ECHEC {lbl}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
