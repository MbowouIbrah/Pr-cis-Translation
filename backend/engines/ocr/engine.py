"""Moteur OCR — étape 1 : L'IDENTIFICATION, et rien d'autre.

CE QU'IL FAIT AUJOURD'HUI
--------------------------
Il lit un scan, en déduit des mots, des lignes, des paragraphes, et rend le
document d'origine avec les blocs de paragraphes ENCADRÉS. Il ne traduit pas,
n'efface rien, ne réécrit rien.

    « le cœur du travail n'est pas la traduction mais la détection : une bonne
      détection donne un document de meilleure qualité »

Les deux versions précédentes traduisaient déjà, et le résultat n'était pas
livrable parce que la détection ne l'était pas. On construit donc la détection
d'abord, et on la REGARDE avant d'écrire quoi que ce soit par-dessus.

CE QUI EST EMPRUNTÉ, ET CE QUI NE L'EST PAS
--------------------------------------------
La mise en page vient du moteur PDF, sans copie ni fourche :

    spans OCR ──▶ _group_text_lines ──▶ _group_paragraphs ──▶ blocs

`PDFObjectEngine` s'instancie sans état ni fichier (`vars(e) == {}`) et ses
méthodes de regroupement sont des fonctions pures de leurs arguments. On s'en
sert comme d'une BIBLIOTHÈQUE de mise en page.

C'est un choix d'architecture, pas une commodité : dupliquer le regroupement
donnerait deux implémentations qui divergent, et le PDF a déjà payé des
dizaines de correctifs (gouttières, corridors, satellites, cellules) qu'on
n'aurait aucune raison de repayer.

**Conséquence à respecter** : toute correction de `_group_text_lines` ou
`_group_paragraphs` sert les DEUX moteurs. On ne les modifie donc jamais pour
arranger un cas OCR sans repasser la suite générique du PDF
(`test_engine_v2_generic.py`, 59/59). La contrepartie est acceptée : un
correctif PDF peut changer un rendu OCR.

UNE INSTANCE PAR OPÉRATION — voir `engines/CONTEXTE.md`.
"""
from __future__ import annotations

import logging
import os

import fitz

from engines.ocr import apercu as apercu_debug
from engines.ocr import lecture
from engines.ocr.spans import RepertoireConfiance, valider_spans

logger = logging.getLogger(__name__)


class OCREngine:
    """Moteur OCR — documents scannés. Étape d'identification.

    Absent du registre des moteurs, comme le moteur PDF : il n'honore pas le
    contrat `extract_text` / `inject_translation` en deux temps. Il est appelé
    nommément.
    """

    extension = "pdf"          # un scan arrive dans un PDF

    def __init__(self, langue: str = "fra", dpi: int = lecture.DPI_LECTURE):
        self.langue = langue
        self.dpi = dpi
        self._mise_en_page = None

    @property
    def mise_en_page(self):
        """Le moteur PDF, utilisé comme bibliothèque de mise en page.

        Construit à la demande : l'importer tire PyMuPDF et les polices, ce qui
        est inutile tant qu'on n'analyse rien.
        """
        if self._mise_en_page is None:
            from engines.pdf.engine import PDFObjectEngine
            self._mise_en_page = PDFObjectEngine()
        return self._mise_en_page

    # ── Analyse d'UNE page ────────────────────────────────────────────────
    def analyser_page(self, page, double_lecture: bool = True) -> dict:
        """Rend `{spans, lignes, blocs, retenus, ecartes, confiances}`.

        L'ORDRE EST LE FOND DU SUJET : rien n'est jugé avant que la structure
        ne soit connue. La v1 triait au MOT, avant tout regroupement, et c'est
        ce qui la faisait retenir des fragments de dessin.

        À cette étape, AUCUN TRI N'EST APPLIQUÉ : tous les blocs sont retenus.
        C'est délibéré — on veut voir la détection brute, faux blocs compris,
        avant d'écrire des seuils qui la filtreraient sans qu'on les ait vus
        agir.
        """
        spans = lecture.spans_de_page(page, langue=self.langue, dpi=self.dpi,
                                      double_lecture=double_lecture)
        vide = {"spans": [], "lignes": [], "blocs": [], "retenus": [],
                "ecartes": [], "confiances": None}
        if not spans:
            return vide

        # Le contrat est vérifié ICI, pas seulement en test : un span
        # incomplet ne lèverait rien et viderait le résultat en silence.
        manques = valider_spans(spans)
        if manques:
            logger.error("Spans OCR non conformes au contrat : %s",
                         manques[:3])
            return vide

        moteur = self.mise_en_page
        lignes = moteur._group_text_lines(spans)
        blocs = moteur._group_paragraphs(lignes)
        return {"spans": spans, "lignes": lignes, "blocs": blocs,
                "retenus": blocs, "ecartes": [],
                "confiances": RepertoireConfiance(spans)}

    # ── L'aperçu de détection ─────────────────────────────────────────────
    def rendre_apercu(self, chemin_pdf: str, sortie_pdf: str,
                      max_pages: int = 0, double_lecture: bool = True,
                      montrer_lignes: bool = True,
                      progression=None) -> dict:
        """Écrit un PDF où chaque page porte la détection par-dessus le scan.

        Rend un compte rendu par page — utile pour repérer une RÉGRESSION
        entre deux versions, pas pour juger la qualité : c'est l'œil qui juge.
        """
        doc = fitz.open(chemin_pdf)
        try:
            total = len(doc) if max_pages <= 0 else min(max_pages, len(doc))
            comptes = []
            for i in range(total):
                page = doc[i]
                vu = self.analyser_page(page, double_lecture=double_lecture)
                compte = apercu_debug.dessiner(
                    page, vu["retenus"], vu["ecartes"], vu["lignes"],
                    montrer_lignes=montrer_lignes)
                apercu_debug.legende(page)
                compte["page"] = i + 1
                compte["mots"] = len(vu["spans"])
                comptes.append(compte)
                if progression:
                    progression(i + 1, total, compte)

            # Les pages non analysées sont RETIRÉES : un aperçu qui montre 4
            # pages annotées suivies de 200 pages brutes laisse croire que la
            # détection n'a rien trouvé sur les 200.
            if total < len(doc):
                doc.delete_pages(from_page=total, to_page=len(doc) - 1)

            dossier = os.path.dirname(os.path.abspath(sortie_pdf))
            if dossier:
                os.makedirs(dossier, exist_ok=True)
            doc.save(sortie_pdf, garbage=3, deflate=True)
            return {"pages": comptes, "sortie": sortie_pdf}
        finally:
            doc.close()
