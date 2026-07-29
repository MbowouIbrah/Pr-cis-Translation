"""XLSX — le classeur PARTIEL, socle de l'aperçu progressif.

CE QUE CETTE PIÈCE REND POSSIBLE
--------------------------------
L'aperçu progressif convertit UNE feuille à la fois et greffe sa page dans le
PDF déjà affiché. Il lui faut donc un classeur ne contenant que cette feuille —
c'est `build_partial_xlsx`, pendant XLSX de `build_partial_pptx`.

LES TROIS PIÈGES VERROUILLÉS ICI
--------------------------------
1. **Le numéro du fichier ne dit rien de la position de l'onglet.** Le classeur
   d'essai a « Charges » en deuxième position dans un fichier `sheet7.xml`, et
   « Synthèse » en troisième dans `sheet2.xml` — ce que produit tout classeur
   dont on a déplacé des onglets. Déduire l'ordre de `sheetN.xml` afficherait
   les feuilles dans le désordre.

2. **`sharedStrings.xml` est indexé par POSITION.** L'élaguer feuille par
   feuille décalerait les index et les feuilles GARDÉES afficheraient le
   mauvais texte. La feuille « Synthèse » le prouve : elle cite les entrées 5
   et 3 du magasin, alors que ses propres cellules n'en produisent aucune.

3. **La méthode ne doit RIEN modifier du dossier temporaire.** Elle est appelée
   une fois par feuille pendant l'aperçu, puis une dernière pour le fichier
   final : une version destructive laisserait le classeur final amputé de tout
   sauf la première feuille. C'est le défaut exact déjà payé côté PPTX.

La preuve ultime n'est pas qu'un fichier existe, c'est que **LibreOffice
l'ouvre et en rend les bonnes pages** — un ZIP bien formé mais incohérent
passerait tout contrôle structurel.

    backend/venv/Scripts/python.exe backend/tests/test_xlsx_partiel.py
"""
from __future__ import annotations

import os
import tempfile
import zipfile

import racine  # noqa: F401  -- met backend/ sur le chemin

import engines                                              # noqa: E402
from fabrique_classeurs import FEUILLES, construire_multi    # noqa: E402

_checks: list[tuple[bool, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _checks.append((bool(cond), label))
    print(f"  {'OK  ' if cond else 'ECHEC'} {label}"
          + (f"   [{detail}]" if detail and not cond else ""))


def _pages_pdf(chemin_xlsx: str) -> list[str]:
    """Le texte de chaque page, vu par LibreOffice — la preuve réelle."""
    import fitz
    from engines.office import convert_to_pdf
    with open(chemin_xlsx, "rb") as f:
        pdf = convert_to_pdf(f.read(), "xlsx")
    doc = fitz.open("pdf", pdf)
    pages = [doc[i].get_text() for i in range(doc.page_count)]
    doc.close()
    return pages


def main() -> int:
    travail = tempfile.mkdtemp(prefix="precis_test_xlsx_part_")
    src = construire_multi(os.path.join(travail, "multi.xlsx"))

    # ── 1. L'ordre d'affichage, et non l'ordre des fichiers ─────────────────
    eng = engines.new_engine("xlsx")
    feuilles = eng.feuilles(src)

    check([f["nom"] for f in feuilles] == [n for n, _ in FEUILLES],
          "ORDRE   les feuilles sortent dans l'ordre d'AFFICHAGE",
          str([f["nom"] for f in feuilles]))
    check([f["cible"] for f in feuilles] == [c for _, c in FEUILLES],
          "ORDRE   chaque onglet pointe sur son VRAI fichier",
          str([f["cible"] for f in feuilles]))
    # Le contre-test qui donne son sens au précédent : si l'ordre des fichiers
    # suffisait, ce piège n'existerait pas.
    check([c for _, c in FEUILLES]
          != sorted(c for _, c in FEUILLES),
          "ORDRE   le piège est ARMÉ (numéros de fichiers dans le désordre)")

    # ── 2. Chaque feuille, isolée ───────────────────────────────────────────
    attendu = {0: "Chiffre d", 1: "Loyer et charges", 2: "Resultat"}
    for idx, marqueur in attendu.items():
        e = engines.new_engine("xlsx")
        e._ouvrir(src)
        out = os.path.join(travail, f"seule_{idx}.xlsx")
        e.build_partial_xlsx(out, only_sheets={idx})

        with zipfile.ZipFile(out) as z:
            abime = z.testzip()
            feuilles_zip = [n for n in z.namelist()
                            if n.startswith("xl/worksheets/sheet")]
        check(abime is None and len(feuilles_zip) == 1,
              f"ISOLE   feuille {idx} : le partiel ne garde QU'ELLE",
              f"{feuilles_zip}")

        pages = _pages_pdf(out)
        check(len(pages) == 1,
              f"ISOLE   feuille {idx} : LibreOffice en rend UNE page",
              f"{len(pages)} page(s)")
        check(pages and marqueur in pages[0],
              f"ISOLE   feuille {idx} : c'est bien LA BONNE feuille",
              repr(pages[0][:60]) if pages else "aucune page")
        e._nettoyer()

    # ── 3. Le magasin partagé n'est PAS élagué ──────────────────────────────
    # « Synthèse » cite les entrées 5 et 3 du magasin. Elle ne peut afficher le
    # bon texte que si le magasin est resté ENTIER : un élagage aurait décalé
    # tous les index.
    e = engines.new_engine("xlsx")
    e._ouvrir(src)
    out = os.path.join(travail, "synthese.xlsx")
    e.build_partial_xlsx(out, only_sheets={2})
    with zipfile.ZipFile(out) as z:
        magasin = z.read("xl/sharedStrings.xml").decode("utf-8")
    pages = _pages_pdf(out)
    check(magasin.count("<si>") == 6,
          "MAGASIN le magasin partagé reste ENTIER (indexé par position)",
          f"{magasin.count('<si>')} entrées au lieu de 6")
    check(pages and "Resultat" in pages[0] and "2026" in pages[0],
          "MAGASIN la feuille isolée affiche le BON texte (index intacts)",
          repr(pages[0][:70]) if pages else "aucune page")
    e._nettoyer()

    # ── 4. Non destructif : appels répétés, puis le classeur COMPLET ────────
    # C'est le défaut déjà payé côté PPTX : une version qui retire les feuilles
    # du dossier temporaire laisse le classeur final amputé.
    e = engines.new_engine("xlsx")
    e._ouvrir(src)
    for idx in (0, 1, 2):
        e.build_partial_xlsx(os.path.join(travail, f"suite_{idx}.xlsx"),
                             only_sheets={idx})
    complet = os.path.join(travail, "complet_apres.xlsx")
    e.build_partial_xlsx(complet, only_sheets=None)
    with zipfile.ZipFile(complet) as z:
        n_feuilles = len([n for n in z.namelist()
                          if n.startswith("xl/worksheets/sheet")])
    pages = _pages_pdf(complet)
    check(n_feuilles == 3,
          "INTACT  après 3 partiels, le classeur COMPLET a ses 3 feuilles",
          f"{n_feuilles} feuille(s)")
    check(len(pages) == 3,
          "INTACT  et LibreOffice en rend bien 3 pages", f"{len(pages)}")
    e._nettoyer()

    # ── 5. Le cas dégénéré : aucune feuille demandée ────────────────────────
    # Un classeur vide n'est pas ouvrable — LibreOffice rendrait une page
    # blanche au lieu de signaler l'erreur. On garde donc la première.
    e = engines.new_engine("xlsx")
    e._ouvrir(src)
    out = os.path.join(travail, "vide.xlsx")
    e.build_partial_xlsx(out, only_sheets=set())
    with zipfile.ZipFile(out) as z:
        n = len([x for x in z.namelist()
                 if x.startswith("xl/worksheets/sheet")])
    check(n == 1, "DEGENERE aucune feuille demandée -> on garde la première",
          f"{n} feuille(s)")
    e._nettoyer()

    rates = [lbl for ok_, lbl in _checks if not ok_]
    print(f"\n{len(_checks) - len(rates)}/{len(_checks)} verifications")
    if rates:
        for lbl in rates:
            print(f"  ECHEC {lbl}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
