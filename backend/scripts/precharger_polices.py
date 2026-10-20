"""Pré-charge les familles les plus répandues dans `backend/fonts/catalogue`.

POURQUOI CE SCRIPT EXISTE
-------------------------
Le moteur sait télécharger à la demande la police qu'un PDF n'embarque pas en
entier (`engines/pdf/polices.py`). Mais à la demande, c'est au milieu d'une
traduction, et seulement si le serveur joint Google Fonts à ce moment-là. Les
familles qu'on croise dans la plupart des documents sont donc rangées dès la
construction de l'image : le téléchargement devient l'exception, pas la règle.

Chaque fichier passe par la même validation que le téléchargement à la demande
(famille, graisse, pente, encre réellement rendue). Une variante qui n'existe
pas (pas d'italique pour Oswald, par exemple) est simplement sautée.

Jamais bloquant : sans réseau, l'image se construit quand même et le moteur
garde ses replis (clone métrique, puis police la plus proche).

    python backend/scripts/precharger_polices.py [--famille "Open Sans" ...]
"""
from __future__ import annotations

import argparse
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from engines.pdf import polices  # noqa: E402

# Familles Google les plus employées dans les documents bureautiques et les
# PDF générés (sites, gabarits, outils de PAO), plus les clones métriques
# libres des polices Microsoft / Adobe.
FAMILLES = (
    # clones métriques
    "Arimo", "Tinos", "Cousine", "Carlito", "Caladea", "Gelasio",
    # sans
    "Roboto", "Open Sans", "Lato", "Montserrat", "Poppins", "Inter",
    "Source Sans 3", "Noto Sans", "Raleway", "Nunito", "Nunito Sans",
    "Ubuntu", "PT Sans", "Work Sans", "Rubik", "Fira Sans", "Mulish",
    "Barlow", "Barlow Condensed", "Titillium Web", "Heebo", "DM Sans",
    "Manrope", "Karla", "Josefin Sans", "Quicksand", "Hind", "Cabin",
    "Oxygen", "Exo 2", "Archivo", "Assistant", "IBM Plex Sans",
    "Roboto Condensed", "Oswald", "Kanit", "Mukta", "Libre Franklin",
    "Public Sans", "Red Hat Display", "Figtree", "Outfit", "Lexend",
    "Plus Jakarta Sans", "Archivo Narrow",
    "Encode Sans", "Signika", "Varela Round", "Asap", "Overpass",
    "Questrial", "Catamaran", "Maven Pro", "Abel", "Dosis",
    # serif
    "Merriweather", "Playfair Display", "Lora", "PT Serif", "Noto Serif",
    "Roboto Slab", "Libre Baskerville", "EB Garamond", "Crimson Text",
    "Source Serif 4", "Bitter", "Cormorant Garamond", "Arvo", "Domine",
    "Libre Caslon Text", "Zilla Slab", "Spectral", "Cardo", "Old Standard TT",
    "Alegreya", "Vollkorn", "Frank Ruhl Libre", "IBM Plex Serif", "DM Serif Display",
    # chasse fixe
    "Roboto Mono", "Source Code Pro", "IBM Plex Mono", "Fira Mono",
    "JetBrains Mono", "Inconsolata", "Space Mono", "Ubuntu Mono",
)
VARIANTES = ((400, False), (700, False), (400, True), (700, True))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--famille", action="append",
                    help="famille à charger (répétable) ; défaut : la liste intégrée")
    ap.add_argument("--dossier", default=polices.CATALOGUE_DIR)
    args = ap.parse_args(argv)

    r = polices.Resolveur([], args.dossier, polices.google_fonts)
    obtenues = deja = sautees = 0
    for famille in args.famille or FAMILLES:
        for poids, italique in VARIANTES:
            if r.possede(famille, poids, italique):
                deja += 1
                continue
            if r.telecharger_variante(famille, poids, italique):
                obtenues += 1
            else:
                sautees += 1
        if not r.reseau_disponible():
            print("polices : réseau injoignable, pré-chargement interrompu")
            break
    print(f"polices : {obtenues} téléchargées, {deja} déjà là, "
          f"{sautees} absentes ou refusées -> {args.dossier}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                      # jamais bloquant pour le build
        print(f"polices : pré-chargement abandonné ({e})")
        sys.exit(0)
