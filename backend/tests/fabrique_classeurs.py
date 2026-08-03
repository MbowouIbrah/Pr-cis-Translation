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

# ── Graphique ───────────────────────────────────────────────────────────────
# Reproduit la structure d'un `chart1.xml` réel, relevée sur un graphique
# produit par Excel. Deux familles de texte s'y côtoient, et TOUT l'enjeu est
# de ne pas les confondre :
#
#   `<a:t>`  le texte RICHE — titre, noms d'axes. Il n'existe QUE là.
#   `<c:v>`  une valeur. Sous un `<c:strCache>`, c'est le CACHE d'une cellule
#            de la feuille, déjà traduite via `sharedStrings` : le traduire
#            ici la soumettrait DEUX FOIS au modèle, qui peut rendre deux
#            formulations. Le graphique afficherait alors autre chose que sa
#            feuille — et Excel réécrit ce cache au premier rafraîchissement.
#
# Le dernier `<c:v>` est délibérément SANS cache : un texte saisi en dur, qui
# n'est le reflet de rien. Celui-là se traduit.
_CHART = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
<c:chart>
<c:title><c:tx><c:rich><a:p><a:r><a:t>Evolution des ventes</a:t></a:r></a:p></c:rich></c:tx></c:title>
<c:plotArea>
<c:barChart>
<c:ser>
<c:tx><c:strRef><c:f>Ventes!$B$1</c:f>
<c:strCache><c:ptCount val="1"/><c:pt idx="0"><c:v>Marge brute</c:v></c:pt></c:strCache>
</c:strRef></c:tx>
<c:cat><c:strRef><c:f>Ventes!$A$2</c:f>
<c:strCache><c:ptCount val="1"/><c:pt idx="0"><c:v>Janvier</c:v></c:pt></c:strCache>
</c:strRef></c:cat>
<c:val><c:numRef><c:f>Ventes!$B$2</c:f>
<c:numCache><c:ptCount val="1"/><c:pt idx="0"><c:v>1200</c:v></c:pt></c:numCache>
</c:numRef></c:val>
</c:ser>
</c:barChart>
<c:catAx><c:title><c:tx><c:rich><a:p><a:r><a:t>Periode</a:t></a:r></a:p></c:rich></c:tx></c:title></c:catAx>
<c:valAx><c:title><c:tx><c:rich><a:p><a:r><a:t>Euros</a:t></a:r></a:p></c:rich></c:tx></c:title></c:valAx>
</c:plotArea>
<c:legend><c:tx><c:v>Legende en dur</c:v></c:tx></c:legend>
</c:chart>
</c:chartSpace>"""

#: Ce que le moteur DOIT relever dans le graphique, dans l'ordre.
CHART_TRADUISIBLES = ["Evolution des ventes", "Periode", "Euros",
                      "Legende en dur"]
#: Ce qu'il ne doit PAS toucher : les caches de cellules et les nombres.
CHART_INTOUCHABLES = ["Marge brute", "Janvier", "1200"]


def construire_avec_graphique(chemin: str) -> str:
    """Le classeur multi-feuilles, plus un `xl/charts/chart1.xml`."""
    construire_multi(chemin)
    with zipfile.ZipFile(chemin, "a", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/charts/chart1.xml", _CHART)
    return chemin


# ── Zone de texte, commentaires ─────────────────────────────────────────────
# `name="TextBox 1"` est un IDENTIFIANT interne, jamais affiché, que des macros
# peuvent citer. Le traduire ne se verrait nulle part et casserait ce qui s'y
# réfère : il est ici pour vérifier qu'on n'y touche PAS.
_DRAWING = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
<xdr:twoCellAnchor>
<xdr:sp macro="" textlink="">
<xdr:nvSpPr><xdr:cNvPr id="2" name="TextBox 1"/><xdr:cNvSpPr txBox="1"/></xdr:nvSpPr>
<xdr:txBody><a:bodyPr/><a:p><a:r><a:t>Attention aux arrondis</a:t></a:r></a:p>
<a:p><a:r><a:t>Verifier avant diffusion</a:t></a:r></a:p></xdr:txBody>
</xdr:sp>
</xdr:twoCellAnchor>
</xdr:wsDr>"""

# Format HISTORIQUE. `<authors>` porte des NOMS DE PERSONNES : les traduire
# ferait d'un auteur quelqu'un d'autre, et `authorId` y renvoie par index.
_COMMENTS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<comments xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<authors><author>Marie Durand</author></authors>
<commentList>
<comment ref="A1" authorId="0"><text><r><t>Note du reviseur</t></r></text></comment>
<comment ref="B2" authorId="0"><text><r><t>Chiffre </t></r><r><t>a confirmer</t></r></text></comment>
</commentList>
</comments>"""

# Format MODERNE (fils de discussion). Coexiste avec l'ancien : Excel maintient
# les deux, et `comments*.xml` reste celui qui porte le texte affiché.
_THREADED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<ThreadedComments xmlns="http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments">
<threadedComment ref="A1" dT="2026-07-29T10:00:00" personId="{1}" id="{2}">
<text>Peux-tu revoir ce total</text>
</threadedComment>
</ThreadedComments>"""

#: Ce que le moteur DOIT relever hors feuilles et graphique.
DRAWING_TRADUISIBLES = ["Attention aux arrondis", "Verifier avant diffusion"]
COMMENT_TRADUISIBLES = ["Note du reviseur", "Chiffre a confirmer"]
THREAD_TRADUISIBLES = ["Peux-tu revoir ce total"]
#: Ce qu'il ne doit JAMAIS toucher.
JAMAIS_TRADUIT = ["TextBox 1", "Marie Durand"]


def construire_complet(chemin: str) -> str:
    """Le classeur multi-feuilles + graphique + zone de texte + commentaires."""
    construire_avec_graphique(chemin)
    with zipfile.ZipFile(chemin, "a", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/drawings/drawing1.xml", _DRAWING)
        z.writestr("xl/comments1.xml", _COMMENTS)
        z.writestr("xl/threadedComments/threadedComment1.xml", _THREADED)
    return chemin


# ── Tableaux structurés ─────────────────────────────────────────────────────
# UN EN-TÊTE DE TABLEAU VIT À DEUX ENDROITS QUI DOIVENT RESTER IDENTIQUES :
#
#   · `<tableColumn name="Produit">` dans `xl/tables/table1.xml` ;
#   · la CELLULE de la ligne d'en-tête, dans la feuille, à la colonne
#     correspondante — ici via `sharedStrings`.
#
# Excel refuse d'ouvrir le classeur (« contenu illisible ») quand les deux
# divergent. Or `sharedStrings` est traduit par ailleurs : traduire les
# `tableColumn` SÉPAREMENT, avec un second appel au modèle, produirait deux
# formulations pour le même en-tête — et donc la divergence. La règle du
# moteur est d'ALIGNER la colonne sur la cellule déjà traduite, sans jamais
# soumettre l'en-tête une deuxième fois.
#
# `name="Tableau1"` (le nom du TABLEAU) est un identifiant cité par les
# références structurées `Tableau1[Produit]` : jamais traduit, comme le
# `name=` d'une forme.
# LE TABLEAU NE COMMENCE PAS EN A1, ET C'EST DÉLIBÉRÉ.
# Un tableau posé à l'origine rend INVISIBLE toute erreur de décalage : la
# première colonne du tableau est alors la première de la feuille, et un code
# qui ignore la colonne de départ donne le même résultat qu'un code juste.
# Ici le tableau occupe `B2:C3`, avec un TITRE en A1 qui n'en fait pas partie —
# la disposition la plus banale d'un classeur réel.
_TABLE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<table xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 id="1" name="Tableau1" displayName="Tableau1" ref="B2:C3" totalsRowShown="0">
<autoFilter ref="B2:C3"/>
<tableColumns count="2">
<tableColumn id="1" name="Produit"/>
<tableColumn id="2" name="Quantite vendue"/>
</tableColumns>
</table>"""

_TABLE_SHEET = _feuille(
    '<row r="1"><c r="A1" t="s"><v>3</v></c></row>'
    '<row r="2"><c r="B2" t="s"><v>0</v></c><c r="C2" t="s"><v>1</v></c></row>'
    '<row r="3"><c r="B3" t="s"><v>2</v></c><c r="C3"><v>42</v></c></row>')

# Le magasin du classeur À TABLEAU. Les deux premières entrées sont les
# en-têtes ; la troisième est une donnée ordinaire et la quatrième un TITRE
# hors tableau — ni l'une ni l'autre ne doit aligner quoi que ce soit.
_TABLE_SHARED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="4" uniqueCount="4">
<si><t>Produit</t></si>
<si><t>Quantite vendue</t></si>
<si><t>Cafe moulu</t></si>
<si><t>Inventaire du trimestre</t></si>
</sst>"""

_TABLE_WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Stock" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""

_TABLE_WB_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId8" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>"""

_TABLE_SHEET_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/table" Target="../tables/table1.xml"/>
</Relationships>"""

_TABLE_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
<Override PartName="/xl/tables/table1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/>
</Types>"""

#: Les en-têtes du tableau, dans l'ordre des colonnes. Ils sont AUSSI dans le
#: magasin partagé : c'est tout l'enjeu de l'alignement.
TABLE_ENTETES = ["Produit", "Quantite vendue"]
#: Le nom du TABLEAU — un identifiant cité par `Tableau1[Produit]`.
TABLE_JAMAIS_TRADUIT = ["Tableau1"]


def construire_avec_tableau(chemin: str) -> str:
    """Un classeur d'une feuille portant un TABLEAU STRUCTURÉ.

    Volontairement séparé de `construire_multi` : l'enjeu est l'ACCORD entre
    `xl/tables/table1.xml` et la ligne d'en-tête de la feuille, et il se lit
    plus clairement sur un classeur qui ne contient que cela.
    """
    with zipfile.ZipFile(chemin, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _TABLE_CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("xl/workbook.xml", _TABLE_WORKBOOK)
        z.writestr("xl/_rels/workbook.xml.rels", _TABLE_WB_RELS)
        z.writestr("xl/sharedStrings.xml", _TABLE_SHARED)
        z.writestr("xl/worksheets/sheet1.xml", _TABLE_SHEET)
        z.writestr("xl/worksheets/_rels/sheet1.xml.rels", _TABLE_SHEET_RELS)
        z.writestr("xl/tables/table1.xml", _TABLE)
    return chemin


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
