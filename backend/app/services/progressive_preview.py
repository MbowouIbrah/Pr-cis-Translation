"""L'aperçu qui se remplit page par page.

LE FLUX, EN DEUX TEMPS
----------------------
1. **SOCLE** — le document d'ORIGINE, converti une fois en PDF. Affichable
   immédiatement : l'utilisateur voit tout le document, en langue source, dès
   la première seconde.
2. **GREFFE** — chaque page traduite est convertie SEULE, et remplace la page
   correspondante du socle. Le document se traduit sous les yeux.

POURQUOI C'EST POSSIBLE MAINTENANT
----------------------------------
Une campagne précédente avait mesuré 11 s par diapositive isolée et conclu que
le page-par-page coûtait 288 s contre 17 s pour le deck entier. La conclusion
était juste, la CAUSE était fausse : ce n'était pas le poids des médias, mais le
PROFIL LibreOffice, reconstruit à chaque appel.

MESURÉ, profil partagé (cf. `engines/office.py`) :

    profil neuf ...................... 6,99 s
    profil réutilisé ................. 2,66 s
    UNE diapositive, profil chaud .... 1,58 s     <- 7x plus rapide

Mesuré aussi, et **faux** : élaguer les médias d'une diapositive isolée
(12,7 Mo -> 0,5 Mo) ne gagne RIEN. LibreOffice ne lit pas les images que rien ne
référence. Ne pas réécrire cette optimisation-là.

LE PRIX, ET CE QUI NOUS EN PROTÈGE
----------------------------------
26 x 1,58 s = 41 s de calcul contre 2,66 s pour le deck entier : quinze fois
plus de CPU par document. Invisible pour un utilisateur, ruineux pour cent.

D'où la COALESCENCE, qui vit dans l'appelant : un seul convertisseur par job,
qui prend à chaque tour TOUTES les pages en attente. Seul, l'utilisateur reçoit
ses pages une par une ; sous charge, la conversion prend du retard, les pages
s'accumulent et partent par lots — le système glisse de lui-même vers le régime
économe, sans seuil à deviner.
"""
from __future__ import annotations

import os
import tempfile
from typing import Callable

import fitz


class ProgressivePreview:
    """Le PDF d'aperçu d'un job, et la façon dont il se remplit.

    L'objet ne connaît ni LibreOffice ni le moteur PPTX : il reçoit deux
    fonctions. C'est ce qui le rend vérifiable sans convertir quoi que ce soit,
    et réutilisable pour un autre format.

    :param extraire: `(chemin_sortie, pages) -> None` — écrit un document ne
        contenant QUE ces pages, traduites.
    :param convertir: `(octets_du_document) -> octets_pdf`.
    """

    def __init__(self, extraire: Callable[[str, set[int]], None],
                 convertir: Callable[[bytes], bytes], extension: str = "pptx"):
        self._extraire = extraire
        self._convertir = convertir
        self._extension = extension
        self._socle: bytes | None = None
        self._pages: dict[int, bytes] = {}

    # ── État ─────────────────────────────────────────────────────────────
    @property
    def pret(self) -> bool:
        """Le socle est-il posé ? Avant lui, il n'y a rien à montrer."""
        return self._socle is not None

    @property
    def pages_traduites(self) -> int:
        return len(self._pages)

    @property
    def pages_socle(self) -> int:
        """Nombre de pages du document d'origine. 0 tant qu'il n'est pas posé."""
        if self._socle is None:
            return 0
        with fitz.open(stream=self._socle, filetype="pdf") as d:
            return d.page_count

    # ── Construction ─────────────────────────────────────────────────────
    def poser_socle(self, pdf_origine: bytes) -> bytes:
        """Installe le document d'origine comme état initial."""
        self._socle = pdf_origine
        return self.assembler()

    def greffer(self, pages: list[int]) -> bytes:
        """Convertit ces pages et remplace les leurs dans le socle.

        UNE seule conversion pour tout le lot — c'est ce qui fait que le retard
        se paie en débit, jamais en latence par page.

        Lève si le nombre de pages rendues ne correspond pas à ce qui était
        demandé : sans cet appariement certain, on afficherait la traduction
        d'une page en face d'une autre. Mieux vaut ne rien greffer et réessayer
        au tour suivant que mentir sur ce qu'on montre.
        """
        if not pages:
            return self.assembler()
        if self._socle is None:
            raise RuntimeError("greffe demandée avant le socle")

        ordonnees = sorted(set(pages))

        # PREMIÈRE garde : la page demandée existe-t-elle dans le document ?
        #
        # Le comptage seul ne suffit PAS, et le test l'a prouvé : demander une
        # page inexistante produit un document vide, que LibreOffice rend en UNE
        # page blanche. `1 == 1` — la garde de comptage passait, et une page
        # fantôme était enregistrée comme traduite. Compter, c'est vérifier une
        # conséquence ; vérifier l'existence, c'est vérifier la cause.
        hors = [n for n in ordonnees if not (1 <= n <= self.pages_socle)]
        if hors:
            raise ValueError(
                f"page(s) {hors} hors du document ({self.pages_socle} pages)")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            lot_path = os.path.join(td, f"lot.{self._extension}")
            self._extraire(lot_path, set(ordonnees))
            with open(lot_path, "rb") as f:
                rendu = self._convertir(f.read())

        with fitz.open(stream=rendu, filetype="pdf") as lot:
            # SECONDE garde, en profondeur. Elle couvre le cas où une page
            # EXISTE mais ne produit rien au rendu : le lot serait alors décalé
            # et chaque traduction atterrirait en face de la mauvaise page.
            #
            # HONNÊTETÉ : aucun test ne l'atteint aujourd'hui — la garde
            # d'existence ci-dessus tire la première, et je n'ai pas su
            # fabriquer un document dont une page se rende « à vide ». Elle est
            # conservée parce que le défaut qu'elle empêche est SILENCIEUX (une
            # traduction affichée sous une autre page) et qu'elle ne coûte
            # qu'une comparaison. À supprimer si l'on démontre qu'elle ne peut
            # pas se déclencher.
            if lot.page_count != len(ordonnees):
                raise ValueError(
                    f"{lot.page_count} page(s) rendue(s) pour "
                    f"{len(ordonnees)} demandée(s) : appariement incertain")
            for i, num in enumerate(ordonnees):
                une = fitz.open()
                une.insert_pdf(lot, from_page=i, to_page=i)
                self._pages[num] = une.tobytes()
                une.close()
        return self.assembler()

    def assembler(self) -> bytes:
        """Socle + pages traduites. Quelques millisecondes.

        On repart TOUJOURS du socle plutôt que de muter le PDF précédent : une
        greffe ratée ne peut pas laisser l'aperçu dans un état intermédiaire, et
        l'ordre des pages est vrai par construction plutôt que par récurrence.
        """
        if self._socle is None:
            raise RuntimeError("aucun socle posé")
        with fitz.open(stream=self._socle, filetype="pdf") as sortie:
            for num, page_pdf in sorted(self._pages.items()):
                idx = num - 1
                if not (0 <= idx < sortie.page_count):
                    continue          # page hors du document : on n'invente pas
                with fitz.open(stream=page_pdf, filetype="pdf") as une:
                    sortie.delete_page(idx)
                    sortie.insert_pdf(une, from_page=0, to_page=0, start_at=idx)
            return sortie.tobytes()


def ecrire_atomiquement(chemin: str, donnees: bytes) -> None:
    """Écrit sans jamais exposer un fichier à moitié écrit.

    Un lecteur (`/partial`) peut arriver à tout instant : sans le passage par un
    temporaire et le renommage, il lirait un PDF tronqué et le signalerait comme
    corrompu.
    """
    tmp = chemin + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(donnees)
        os.replace(tmp, chemin)
    except BaseException:
        # Disque plein : le temporaire à moitié écrit ne doit pas rester.
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
