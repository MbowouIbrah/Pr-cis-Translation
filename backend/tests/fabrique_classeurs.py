"""Classeurs SYNTHÉTIQUES à plusieurs feuilles, pour l'aperçu progressif.

POURQUOI UNE FABRIQUE, ET PAS UN CLASSEUR RÉEL
----------------------------------------------
Règle du projet : un correctif se prouve sur du synthétique, jamais sur le seul
document qui l'a révélé. Ici c'est aussi une nécessité pratique — il faut
pouvoir poser des pièges précis, et un classeur réel ne les contient qu'au
hasard.

POURQUOI PAS `openpyxl` NI `xlsxwriter`
---------------------------------------
`openpyxl` est absent. `xlsxwriter` est présent, mais **par accident** : c'est
une dépendance transitive de `python-pptx`, non déclarée dans
`requirements.txt`. Une suite de tests qui reposerait dessus tomberait le jour
où `python-pptx` cesse de le tirer. On écrit donc le XML à la main, comme le
fait déjà `test_xlsx_squelette.py`.

LE PIÈGE QUE CE CLASSEUR POSE, ET QUI COMPTE
--------------------------------------------
**Le numéro du fichier ne dit RIEN de la position de l'onglet.** Ici, la
deuxième feuille affichée (« Charges ») est le fichier `sheet7.xml`, et la
troisième (« Synthèse ») est `sheet2.xml` — exactement ce que produit un
classeur dont on a déplacé, supprimé puis rajouté des onglets.

Un code qui déduit l'ordre de `sheetN.xml` affiche donc les feuilles dans le
désordre. L'ordre d'affichage est celui de `<sheets>` dans `workbook.xml`, et
la cible réelle de chaque onglet se lit dans `workbook.xml.rels` — jamais
ailleurs.
"""
from __future__ import annotations

import zipfile

_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/worksheets/sheet7.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

# L'ORDRE D'AFFICHAGE est celui de <sheets>. Les `r:id` renvoient au fichier
# réel via workbook.xml.rels — et ils sont volontairement DÉSORDONNÉS.
_WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets>
<sheet name="Ventes" sheetId="1" r:id="rId1"/>
<sheet name="Charges" sheetId="7" r:id="rId2"/>
<sheet name="Synthese" sheetId="2" r:id="rId3"/>
</sheets>
<definedNames>
<definedName name="Zone_ventes">Ventes!$A$1:$B$4</definedName>
<definedName name="Zone_charges">Charges!$A$1:$B$3</definedName>
</definedNames>
</workbook>"""

_WB_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet7.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
<Relationship Id="rId8" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>"""

# Magasin partagé COMMUN aux trois feuilles — c'est ce qui interdit de l'élaguer
# feuille par feuille : il est indexé par POSITION.
_SHARED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="6" uniqueCount="6">
<si><t>Chiffre d'affaires</t></si>
<si><t>Marge brute</t></si>
<si><t>1234,50</t></si>
<si><r><t>Total </t></r><r><t>2026</t></r></si>
<si><t>Loyer et charges</t></si>
<si><t>Resultat net</t></si>
</sst>"""


def _feuille(paires: str) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="http://schemas.openxmlformats.org/'
            f'spreadsheetml/2006/main"><sheetData>{paires}</sheetData>'
            f'</worksheet>')


# Chaque feuille cite le magasin partagé par INDEX (t="s"). Si l'on élaguait le
# magasin, ces index pointeraient sur le mauvais texte.
_SHEET1 = _feuille(
    '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
    '<row r="2"><c r="A2"><v>1200</v></c></row>'
    '<row r="3"><c r="A3" t="inlineStr"><is><t>Prevision annuelle</t></is></c></row>')
_SHEET7 = _feuille(
    '<row r="1"><c r="A1" t="s"><v>4</v></c></row>'
    '<row r="2"><c r="A2"><v>340</v></c></row>')
_SHEET2 = _feuille(
    '<row r="1"><c r="A1" t="s"><v>5</v></c><c r="B1" t="s"><v>3</v></c></row>'
    '<row r="2"><c r="A2"><f>Ventes!A2-Charges!A2</f><v>860</v></c></row>')

#: Ordre d'AFFICHAGE attendu, et fichier réel de chacune. La vérité terrain.
FEUILLES = [
    ("Ventes", "xl/worksheets/sheet1.xml"),
    ("Charges", "xl/worksheets/sheet7.xml"),
    ("Synthese", "xl/worksheets/sheet2.xml"),
]


def construire_multi(chemin: str) -> str:
    """Un classeur de TROIS feuilles dont la numérotation trompe l'ordre."""
    with zipfile.ZipFile(chemin, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("xl/workbook.xml", _WORKBOOK)
        z.writestr("xl/_rels/workbook.xml.rels", _WB_RELS)
        z.writestr("xl/sharedStrings.xml", _SHARED)
        z.writestr("xl/worksheets/sheet1.xml", _SHEET1)
        z.writestr("xl/worksheets/sheet7.xml", _SHEET7)
        z.writestr("xl/worksheets/sheet2.xml", _SHEET2)
    return chemin
