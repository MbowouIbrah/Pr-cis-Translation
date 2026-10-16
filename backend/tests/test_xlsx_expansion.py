"""Expansion XLSX — une traduction plus longue doit encore se lire.

Une feuille par scénario, sur un classeur SYNTHÉTIQUE écrit à la main (voir
`fabrique_classeurs.py` pour la raison). La « traduction » est un
dictionnaire local : aucun appel réseau.

Les attentes sont exprimées en ORDRES DE GRANDEUR indépendants de la mesure
du moteur (un caractère latin vaut entre 0,4 et 1,2 zéro), jamais en
recalculant sa formule : un test qui partagerait la mesure ne verrait pas
qu'elle est fausse.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import racine  # noqa: F401  -- met backend/ sur le chemin

from lxml import etree                                       # noqa: E402

import engines                                               # noqa: E402
from engines.xlsx import expansion                           # noqa: E402

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS = {"s": S}
_checks: list[tuple[bool, str, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _checks.append((bool(cond), label, detail))


LONG = "Montant total de la facture"          # ~27 car. → 20 à 32 zéros
TRAD = {
    "Total": LONG,
    "Prix": LONG,
    "Nom": LONG,
    "Libre": LONG,
    "Fusion": LONG,
    "Rétréci": LONG,
    "Pareil": "Pareil",
    "Note": "Une note bien plus longue que sa source, sur trois lignes",
    # Source DÉJÀ coupée (~20 zéros dans 10) ; la traduction double.
    "Une source déjà trop longue": "Une source déjà trop longue"
                                   " Une source déjà trop longue",
    " j": " jours ouvrés",
    "N° ": "Numéro ",
    "Deux\nlignes": "Montant total\nde la facture",
}

STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="{S}">
<numFmts count="2"><numFmt numFmtId="164" formatCode="0&quot; j&quot;"/>
<numFmt numFmtId="165" formatCode="&quot;N° &quot;0&quot; j&quot;;-0"/></numFmts>
<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="1"><fill><patternFill patternType="none"/></fill></fills>
<borders count="1"><border/></borders>
<cellStyleXfs count="1"><xf/></cellStyleXfs>
<cellXfs count="7">
<xf fontId="0"/>
<xf fontId="0" applyAlignment="1"><alignment horizontal="right"/></xf>
<xf fontId="0" applyAlignment="1"><alignment horizontal="center"/></xf>
<xf fontId="0" applyAlignment="1"><alignment wrapText="1"/></xf>
<xf fontId="0" numFmtId="164" applyNumberFormat="1"/>
<xf fontId="0" applyAlignment="1"><alignment shrinkToFit="1"/></xf>
<xf fontId="0" numFmtId="165" applyNumberFormat="1"/>
</cellXfs>
</styleSheet>"""

TEXTES: list[str] = []


def _t(texte: str, ref: str, style: int = 0) -> str:
    if texte not in TEXTES:
        TEXTES.append(texte)
    s = f' s="{style}"' if style else ""
    return f'<c r="{ref}" t="s"{s}><v>{TEXTES.index(texte)}</v></c>'


def _n(ref: str, style: int = 0) -> str:
    s = f' s="{style}"' if style else ""
    return f'<c r="{ref}"{s}><v>7</v></c>'


def _cols(*plages) -> str:
    return "<cols>" + "".join(
        f'<col min="{a}" max="{b}" width="{w}" customWidth="1"/>'
        for a, b, w in plages) + "</cols>"


# nom → (cols, lignes, fusions)
FEUILLES = {
    "bloquee": (_cols((1, 1, 10)),
                f'<row r="1">{_t("Total", "A1")}{_n("B1")}</row>', ""),
    "libre": ("", f'<row r="1">{_t("Libre", "A1")}</row>', ""),
    "voisine": (_cols((1, 3, 10)),
                f'<row r="1">{_t("Nom", "A1")}{_n("C1")}</row>', ""),
    "droite": (_cols((1, 3, 10)),
               f'<row r="1">{_t("Prix", "B1", 1)}{_n("C1")}</row>', ""),
    "centre": (_cols((1, 4, 5)),
               f'<row r="1">{_t("Total", "B1", 2)}{_n("D1")}</row>', ""),
    "fusion": (_cols((1, 2, 10)),
               f'<row r="1">{_t("Fusion", "A1")}<c r="B1"/></row>',
               '<mergeCells count="1"><mergeCell ref="A1:B1"/></mergeCells>'),
    "enroulee": (_cols((1, 1, 30)),
                 f'<row r="1" ht="15" customHeight="1">'
                 f'{_t("Note", "A1", 3)}</row>', ""),
    "nombre": (_cols((1, 1, 10)), f'<row r="1">{_n("A1", 4)}</row>', ""),
    "coupee": (_cols((1, 1, 10)),
               f'<row r="1">{_t("Une source déjà trop longue", "A1")}'
               f'{_n("B1")}</row>', ""),
    "retrecie": (_cols((1, 1, 10)),
                 f'<row r="1">{_t("Rétréci", "A1", 5)}{_n("B1")}</row>', ""),
    "pareil": (_cols((1, 1, 10)),
               f'<row r="1">{_t("Pareil", "A1")}{_n("B1")}</row>', ""),
    # Une ligne SANS hauteur source est automatique : on n'y touche pas.
    "auto": (_cols((1, 1, 30)), f'<row r="1">{_t("Note", "A1", 3)}</row>', ""),
    # Une hauteur posée SANS customHeight grandit mais ne se fige pas.
    "souple": (_cols((1, 1, 30)),
               f'<row r="1" ht="15">{_t("Note", "A1", 3)}</row>', ""),
    # Sans retour à la ligne, les paragraphes s'affichent bout à bout.
    "paragraphes": (_cols((1, 1, 10)),
                    '<row r="1">' + _t("Deux\nlignes", "A1") + _n("B1")
                    + '</row>',
                    ""),
    # Une formule au résultat vide bloque le débordement comme un nombre.
    "formule": (_cols((1, 1, 10)),
                f'<row r="1">{_t("Total", "A1")}<c r="B1"><f>""</f></c></row>',
                ""),
    # Deux littéraux dans LA MÊME section s'affichent ensemble.
    "deux": (_cols((1, 1, 10)), f'<row r="1">{_n("A1", 6)}</row>', ""),
    # Fusion géante : elle ne doit coûter que les lignes présentes.
    "geante": (_cols((1, 1, 10)),
               f'<row r="1">{_t("Total", "A1")}{_n("B1")}</row>',
               '<mergeCells count="1"><mergeCell ref="A2:XFD1048576"/>'
               '</mergeCells>'),
    # XML abîmé (plage sans min, largeur illisible, lignes et cellules sans
    # référence) : la passe doit le lire par position, sans planter.
    "abimee": ('<cols><col min="1" max="1" width="10" customWidth="1"/>'
               '<col max="3" width="abc"/></cols>',
               '<row>' + _t("Total", "A1").replace(' r="A1"', "")
               + '<c><v>7</v></c></row>', ""),
}


def construire(chemin: str) -> str:
    noms = list(FEUILLES)
    feuilles = {}
    for i, nom in enumerate(noms, 1):
        cols, lignes, fusions = FEUILLES[nom]
        feuilles[i] = (f'<?xml version="1.0" encoding="UTF-8" '
                       f'standalone="yes"?><worksheet xmlns="{S}">{cols}'
                       f'<sheetData>{lignes}</sheetData>{fusions}</worksheet>')
    sst = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<sst xmlns="{S}" count="{len(TEXTES)}" '
           f'uniqueCount="{len(TEXTES)}">'
           + "".join(f"<si><t>{t}</t></si>" for t in TEXTES) + "</sst>")
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    wb = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          f'<workbook xmlns="{S}" xmlns:r="{rel}"><sheets>'
          + "".join(f'<sheet name="{n}" sheetId="{i}" r:id="rId{i}"/>'
                    for i, n in enumerate(noms, 1))
          + "</sheets></workbook>")
    base = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/'
               'package/2006/relationships">'
               + "".join(f'<Relationship Id="rId{i}" Type="{base}/worksheet" '
                         f'Target="worksheets/sheet{i}.xml"/>' for i in feuilles)
               + f'<Relationship Id="rS" Type="{base}/sharedStrings" '
               'Target="sharedStrings.xml"/>'
               f'<Relationship Id="rT" Type="{base}/styles" '
               'Target="styles.xml"/></Relationships>')
    ct_ns = "application/vnd.openxmlformats-officedocument.spreadsheetml"
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
             'content-types"><Default Extension="rels" ContentType="'
             'application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             f'<Override PartName="/xl/workbook.xml" ContentType="{ct_ns}'
             '.sheet.main+xml"/>'
             + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                       f'ContentType="{ct_ns}.worksheet+xml"/>'
                       for i in feuilles)
             + f'<Override PartName="/xl/sharedStrings.xml" ContentType="'
             f'{ct_ns}.sharedStrings+xml"/><Override PartName="/xl/styles'
             f'.xml" ContentType="{ct_ns}.styles+xml"/></Types>')
    racine_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/relationships"><Relationship Id="r1" Type="'
                   f'{base}/officeDocument" Target="xl/workbook.xml"/>'
                   '</Relationships>')
    with zipfile.ZipFile(chemin, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", racine_rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/sharedStrings.xml", sst)
        z.writestr("xl/styles.xml", STYLES)
        for i, x in feuilles.items():
            z.writestr(f"xl/worksheets/sheet{i}.xml", x)
    return chemin


def traduire(src: str, dossier: str) -> str:
    import json
    moteur = engines.new_engine("xlsx")
    extraction, _ = moteur.extract_text(src, os.path.join(dossier, "ex.json"))
    for el in extraction["workbook"]["elements"]:
        el["translated_text"] = re.sub(
            r"(\[\[(\d+)\]\])(.*?)(\[\[/\2\]\])",
            lambda m: m.group(1) + TRAD.get(m.group(3), m.group(3))
            + m.group(4), el["text"], flags=re.S)
    tr = os.path.join(dossier, "tr.json")
    with open(tr, "w", encoding="utf-8") as f:
        json.dump(extraction, f, ensure_ascii=False)
    sortie = os.path.join(dossier, "out.xlsx")
    ok, msg = engines.new_engine("xlsx").inject_translation(src, tr, sortie)
    assert ok, msg
    return sortie


def feuille(chemin: str, nom: str):
    i = list(FEUILLES).index(nom) + 1
    with zipfile.ZipFile(chemin) as z:
        return etree.fromstring(z.read(f"xl/worksheets/sheet{i}.xml"))


def largeurs(racine_xml) -> dict[int, float]:
    out = {}
    for c in racine_xml.xpath("./s:cols/s:col", namespaces=NS):
        if not (c.get("min") or "").isdigit():
            continue                  # plage abîmée exprès (feuille ABIMEE)
        for j in range(int(c.get("min")), int(c.get("max")) + 1):
            out[j] = float(c.get("width"))
    return out


def main() -> int:
    travail = tempfile.mkdtemp(prefix="precis_test_xlsx_exp_")
    try:
        src = construire(os.path.join(travail, "src.xlsx"))
        debut = time.perf_counter()
        out = traduire(src, travail)
        duree = time.perf_counter() - debut
        check(duree < 30, "GEANTE une fusion A2:XFD1048576 reste instantanée",
              f"{duree:.1f} s")
        n = len(LONG)

        w = largeurs(feuille(out, "bloquee"))
        plein = w[1]          # la place que le texte entier demande
        check(0.4 * n + 0.5 <= w[1] <= 1.2 * n + 1.5,
              "BLOQUEE voisine pleine : la colonne s'ouvre à la traduction",
              str(w))

        r = feuille(out, "libre")
        check(r.find(f"{{{S}}}cols") is None,
              "LIBRE rien ne coupe le texte : aucune colonne touchée",
              etree.tostring(r)[:200].decode())

        w = largeurs(feuille(out, "voisine"))
        check(w.get(2) == 10 and w.get(3) == 10,
              "VOISINE la plage est découpée, B et C gardent 10", str(w))
        check(0.4 * n + 0.5 <= w[1] + w[2] <= 1.2 * n + 1.5 and w[1] > 10,
              "VOISINE la voisine vide compte dans la place", str(w))
        check(abs(w[1] + 10 - plein) < 0.05,
              "VOISINE on n'élargit que du MANQUE, pas de toute la traduction",
              f"{w} plein={plein}")

        w = largeurs(feuille(out, "droite"))
        check(w[2] > 10 and w[1] == 10 and w[3] == 10,
              "DROITE aligné à droite : la colonne propre s'ouvre", str(w))
        check(abs(w[2] + 10 - plein) < 0.05,
              "DROITE le débordement à gauche (A vide) compte",
              f"{w} plein={plein}")

        w = largeurs(feuille(out, "centre"))
        # Place centrée = B + 2·min(A, C) = 15 ; sans la voisine pleine en D,
        # le texte serait libre.
        check(w[2] > 5 and w[1] == 5 and w[3] == 5 and w[4] == 5,
              "CENTRE centré : seule la colonne propre s'ouvre", str(w))
        check(abs(w[2] + 10 - plein) < 0.05,
              "CENTRE la place des deux côtés compte", f"{w} plein={plein}")

        w = largeurs(feuille(out, "fusion"))
        check(w[1] > 10 and abs(w[1] - w[2]) < 0.02
              and 0.4 * n + 0.5 <= w[1] + w[2] <= 1.2 * n + 1.5,
              "FUSION l'écart est réparti sur les colonnes fusionnées", str(w))

        r = feuille(out, "enroulee")
        row = r.find(f"{{{S}}}sheetData/{{{S}}}row")
        check(largeurs(r) == {1: 30.0},
              "ENROULEE la largeur ne bouge pas", str(largeurs(r)))
        check(float(row.get("ht")) >= 30 and row.get("customHeight") == "1",
              "ENROULEE la ligne gagne la hauteur des lignes en plus",
              str(row.attrib))

        w = largeurs(feuille(out, "nombre"))
        ajout = len(" jours ouvrés") - len(" j")
        check(10 + 0.4 * ajout <= w[1] <= 10 + 1.2 * ajout,
              "NOMBRE la colonne gagne l'écart du littéral (pas de #####)",
              str(w))

        w = largeurs(feuille(out, "coupee"))
        check(10 * 1.6 <= w[1] <= 10 * 2.2,
              "COUPEE source déjà coupée : la coupe reste PROPORTIONNELLE",
              str(w))

        r = feuille(out, "auto")
        row = r.find(f"{{{S}}}sheetData/{{{S}}}row")
        check(row.get("ht") is None and row.get("customHeight") is None,
              "AUTO une ligne en hauteur automatique n'est pas figée",
              str(row.attrib))

        row = feuille(out, "souple").find(f"{{{S}}}sheetData/{{{S}}}row")
        check(float(row.get("ht")) >= 30 and row.get("customHeight") is None,
              "SOUPLE la hauteur grandit sans être figée", str(row.attrib))

        w = largeurs(feuille(out, "paragraphes"))
        check(w[1] > 0.8 * plein,
              "PARAGRAPHES sans retour à la ligne, la largeur est leur somme",
              f"{w} plein={plein}")

        w = largeurs(feuille(out, "formule"))
        check(w[1] > 10, "FORMULE une formule bloque le débordement", str(w))

        w_deux = largeurs(feuille(out, "deux"))[1]
        w_un = largeurs(feuille(out, "nombre"))[1]
        check(w_deux > w_un + 1,
              "DEUX les écarts des littéraux d'une section s'additionnent",
              f"deux={w_deux} un={w_un}")

        w = largeurs(feuille(out, "geante"))
        check(w[1] > 10, "GEANTE la fusion n'empêche pas l'ajustement", str(w))

        w = largeurs(feuille(out, "abimee"))
        check(w.get(1, 0) > 10,
              "ABIMEE XML sans références : lu par position, ajusté", str(w))

        for nom in ("retrecie", "pareil"):
            w = largeurs(feuille(out, nom))
            check(w == {1: 10.0}, f"{nom.upper()} rien ne bouge", str(w))

        # IDEMPOTENCE : l'aperçu réinjecte dans le même dossier.
        d = Path(travail) / "x"
        with zipfile.ZipFile(out) as z:
            z.extractall(d)
        with zipfile.ZipFile(src) as z:
            sd = Path(travail) / "s"
            z.extractall(sd)
        origine = expansion.instantane(sd)
        # La traduction est déjà ajustée : on la restaure puis on rejoue.
        expansion.ajuster_dossier(d, origine)
        premier = {p.name: p.read_bytes()
                   for p in (d / "xl" / "worksheets").glob("*.xml")}
        expansion.ajuster_dossier(d, origine)
        second = {p.name: p.read_bytes()
                  for p in (d / "xl" / "worksheets").glob("*.xml")}
        check(premier == second, "IDEMPOTENT deux passes, même fichier")
        with zipfile.ZipFile(out) as z:
            final = {Path(n).name: z.read(n) for n in z.namelist()
                     if n.startswith("xl/worksheets/")}
        check(all(etree.tostring(etree.fromstring(premier[k]))
                  == etree.tostring(etree.fromstring(final[k]))
                  for k in premier),
              "IDEMPOTENT la passe sur un résultat déjà ajusté ne l'élargit"
              " pas une seconde fois")

        # Une passe ultérieure sur un texte REDEVENU court (aperçu repris,
        # passe de cohérence) rend aux colonnes et aux lignes leur origine :
        # un élargissement ancien ne survit pas à la cause qui l'a produit.
        shutil.copy(sd / "xl" / "sharedStrings.xml",
                    d / "xl" / "sharedStrings.xml")
        shutil.copy(sd / "xl" / "styles.xml", d / "xl" / "styles.xml")
        expansion.ajuster_dossier(d, origine)
        rendus = {p.name: etree.tostring(etree.fromstring(p.read_bytes()))
                  for p in (d / "xl" / "worksheets").glob("*.xml")}
        sources = {p.name: etree.tostring(etree.fromstring(p.read_bytes()))
                   for p in (sd / "xl" / "worksheets").glob("*.xml")}
        check(rendus == sources,
              "RESTAURE un texte revenu à la source rend la feuille d'origine",
              str([k for k in rendus if rendus[k] != sources.get(k)]))

        # Une feuille illisible ne coûte pas la traduction : la passe la
        # saute et continue avec les autres.
        illisible = dict(origine, **{
            str(Path("xl") / "worksheets" / "sheet1.xml"): b"<pas du xml"})
        try:
            expansion.ajuster_dossier(d, illisible)
            leve = None
        except Exception as exc:              # noqa: BLE001
            leve = exc
        check(leve is None, "ILLISIBLE une feuille illisible ne lève pas",
              repr(leve))
    finally:
        shutil.rmtree(travail, ignore_errors=True)

    ko = [c for c in _checks if not c[0]]
    for ok, label, detail in _checks:
        print(("OK  " if ok else "KO  ") + label
              + ("" if ok else f"  [{detail}]"))
    print(f"{len(_checks) - len(ko)}/{len(_checks)}")
    return 1 if ko else 0


if __name__ == "__main__":
    sys.exit(main())
