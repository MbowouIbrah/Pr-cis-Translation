"""Fabrique de pages dont on CONNAÎT la vérité.

POURQUOI DU SYNTHÉTIQUE, ET NON UN VRAI SCAN
----------------------------------------------
Un document réel n'a pas de vérité terrain : on ne peut pas dire si le moteur
a trouvé « les » 12 paragraphes, puisqu'on ignore combien il y en a. On peut
seulement dire si le résultat nous plaît — et un correctif prouvé sur le
fichier qui l'a révélé est une heuristique déguisée, qui tient jusqu'au fichier
suivant.

Ici, chaque page déclare ce qu'elle contient. Un test peut donc exiger un
NOMBRE, et le test de mutation devient possible : casser le regroupement fait
rougir un compte, pas une impression.

Ces pages servent DEUX usages :
  · les tests, qui posent des spans directement (sans Tesseract) ;
  · l'aperçu de contrôle, qui a besoin d'un vrai PDF à annoter.
"""
from __future__ import annotations

import fitz

#: Interligne et corps « papier » ordinaires, en points.
_CORPS = 10.5
_INTERLIGNE = 15.0


def _poser(page, x, y, texte, corps=_CORPS, police="helv"):
    page.insert_text((x, y), texte, fontsize=corps, fontname=police,
                     color=(0, 0, 0))


def page_deux_colonnes(doc):
    """Deux colonnes, 4 paragraphes, plus un titre. Vérité : 5 blocs.

    Le cas qui met en échec la segmentation de Tesseract (`par_num` fusionne
    les colonnes) et que le regroupement du moteur PDF sait traiter grâce aux
    gouttières.
    """
    page = doc.new_page(width=595, height=842)
    _poser(page, 60, 70, "RAPPORT ANNUEL DE SECURITE", corps=16,
           police="hebo")

    gauche = [
        ["La securite routiere repose sur", "trois piliers complementaires",
         "que ce rapport detaille tour", "a tour dans les pages qui suivent."],
        ["Le premier pilier concerne la", "formation initiale des jeunes",
         "conducteurs et son evaluation."],
    ]
    droite = [
        ["Le deuxieme pilier porte sur", "l'entretien du parc automobile",
         "et les controles techniques."],
        ["Le troisieme pilier, enfin,", "traite de l'amenagement des",
         "infrastructures urbaines et", "de la signalisation verticale."],
    ]
    y = 120
    for para in gauche:
        for ligne in para:
            _poser(page, 60, y, ligne)
            y += _INTERLIGNE
        y += 12                      # blanc inter-paragraphes
    y = 120
    for para in droite:
        for ligne in para:
            _poser(page, 320, y, ligne)
            y += _INTERLIGNE
        y += 12
    return {"page": page, "blocs_attendus": 5,
            "description": "titre + 2 colonnes de 2 paragraphes"}


def page_avec_illustration(doc):
    """Un paragraphe, un dessin, et des fragments PIÈGES autour du dessin.

    LE CAS QUI A FAIT ÉCHOUER LES DEUX VERSIONS PRÉCÉDENTES. Les fragments
    posés sur le dessin sont ALIGNÉS entre eux — comme le sont les pseudo-mots
    qu'un OCR tire d'un trait. Ils imitent « Ho » et « LA » peints sur les
    camions du document d'essai.

    Vérité : 1 vrai paragraphe. Les fragments ne doivent PAS former de bloc
    retenu une fois le tri écrit (étape suivante).
    """
    page = doc.new_page(width=595, height=842)
    for i, ligne in enumerate([
            "Le present document decrit les regles",
            "applicables aux vehicules de transport",
            "de marchandises sur le reseau national.",
            "Il entre en vigueur des sa publication."]):
        _poser(page, 60, 90 + i * _INTERLIGNE, ligne)

    # Le « dessin » : un cadre et des traits, comme un schema technique.
    page.draw_rect(fitz.Rect(60, 200, 520, 470), color=(0.2, 0.2, 0.2),
                   width=1.5)
    for i in range(6):
        page.draw_line(fitz.Point(80, 230 + i * 38), fitz.Point(500, 230 + i * 38),
                       color=(0.45, 0.45, 0.45), width=2.5)
    page.draw_circle(fitz.Point(150, 400), 42, color=(0.2, 0.2, 0.2), width=2)
    page.draw_circle(fitz.Point(430, 400), 42, color=(0.2, 0.2, 0.2), width=2)

    # Les FRAGMENTS pieges : courts, isoles, alignes sur les traits du dessin.
    for x, y, t in [(95, 235, "Ho"), (300, 235, "LA"), (95, 273, "II"),
                    (300, 311, "rn"), (95, 349, "l-"), (430, 387, "cx")]:
        _poser(page, x, y, t, corps=9)
    return {"page": page, "blocs_attendus": 1,
            "description": "1 paragraphe + illustration a fragments pieges"}


def page_tableau(doc):
    """Un tableau borde de 3 colonnes. Les cellules ne doivent pas fusionner.

    Le cas ou une coupe prise à l'echelle de la LIGNE est indiscernable d'une
    justification lache : seul le corridor vertical tranche.
    """
    page = doc.new_page(width=595, height=842)
    _poser(page, 60, 70, "BAREME DES CONTRAVENTIONS", corps=14, police="hebo")
    cols = [60, 250, 400, 535]
    lignes_y = [100, 130, 160, 190, 220]
    for x in cols:
        page.draw_line(fitz.Point(x, lignes_y[0]), fitz.Point(x, lignes_y[-1]),
                       color=(0.3, 0.3, 0.3), width=0.8)
    for y in lignes_y:
        page.draw_line(fitz.Point(cols[0], y), fitz.Point(cols[-1], y),
                       color=(0.3, 0.3, 0.3), width=0.8)
    contenu = [("Infraction", "Montant", "Points"),
               ("Exces de vitesse", "135 EUR", "3"),
               ("Feu rouge grille", "135 EUR", "4"),
               ("Stationnement genant", "35 EUR", "0")]
    for r, rangee in enumerate(contenu):
        for c, cell in enumerate(rangee):
            _poser(page, cols[c] + 6, lignes_y[r] + 20, cell,
                   police="hebo" if r == 0 else "helv")
    return {"page": page, "blocs_attendus": None,
            "description": "tableau borde 3 colonnes (cellules cloisonnees)"}


def document_de_controle(chemin: str) -> dict:
    """Écrit un PDF de 3 pages couvrant les cas qui comptent."""
    doc = fitz.open()
    verites = [page_deux_colonnes(doc), page_avec_illustration(doc),
               page_tableau(doc)]
    doc.save(chemin, garbage=3, deflate=True)
    doc.close()
    return {"chemin": chemin,
            "pages": [{k: v for k, v in t.items() if k != "page"}
                      for t in verites]}


if __name__ == "__main__":
    import sys
    sortie = sys.argv[1] if len(sys.argv) > 1 else "controle.pdf"
    infos = document_de_controle(sortie)
    print(f"Ecrit : {infos['chemin']}")
    for i, p in enumerate(infos["pages"], 1):
        print(f"  page {i} : {p['description']} "
              f"(blocs attendus : {p['blocs_attendus']})")
