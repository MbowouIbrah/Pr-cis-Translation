"""Le CONTRAT DE SPANS — le seul raccordement entre l'OCR et la mise en page.

POURQUOI CE MODULE EXISTE, ALORS QU'IL NE FAIT PRESQUE RIEN
------------------------------------------------------------
Le moteur PDF sait regrouper des mots en lignes puis en paragraphes. Il est
éprouvé sur des centaines de pages et il **ignore d'où viennent les spans**
qu'on lui donne. C'est ce qui permet de le réutiliser tel quel pour un scan,
sans le modifier d'une ligne.

Mais cette réutilisation tient à un fil : les spans doivent porter EXACTEMENT
les champs que le regroupement lit. Le contrat n'est écrit nulle part — il est
implicite dans `PDFObjectEngine._extract_text` (engine.py:581), qui les
construit à partir de `rawdict`.

Ce module l'écrit noir sur blanc, en un seul endroit, et le vérifie.

CE QUE COÛTE UNE CLÉ MANQUANTE — ET POURQUOI ON NE PEUT PAS S'EN REMETTRE
À PYTHON POUR LA SIGNALER
--------------------------------------------------------------------------
Un span est un `dict`. Une clé absente ne lève une `KeyError` que si quelqu'un
la lit avec `[...]` — or le regroupement lit la plupart des champs avec
`.get(...)`, avec une valeur par défaut. Le défaut se propage alors en silence :

  · `_gw` manquant  ->  largeur de glyphe nulle  ->  `split_gap` nul  ->  CHAQUE
    espace devient une frontière de colonne, et chaque mot un paragraphe ;
  · `_base` manquant -> toutes les baselines à 0 -> toute la page sur UNE ligne.

Aucune erreur, aucune trace, un résultat absurde. La v2 a payé exactement ce
défaut sous une autre forme (voir `_confiances` plus bas). D'où
`valider_spans()`, qui refuse un span incomplet AVANT qu'il n'atteigne le
regroupement.

LE PIÈGE QUI A COÛTÉ LE PLUS CHER : LA CONFIANCE NE TRAVERSE PAS
------------------------------------------------------------------
Une ligne rendue par `_group_text_lines` ne porte PAS les spans qu'on lui a
donnés. Elle porte des **runs** — une refonte des spans par segment de style.

La v2 croyait qu'un run « conserve les clés hors contrat, dont `_conf` ». C'est
FAUX, et vérifié ici même (`test_ocr_contrat.py`) : `_run_from_span`
(engine.py:1313) construit un dict EXPLICITE de 11 clés. Tout champ qu'il ne
nomme pas est **jeté**.

Le défaut se présente en deux temps, et les deux sont silencieux :

  · lire `ligne["spans"]`  ->  clé absente, liste vide ;
  · lire `ligne["runs"]`   ->  liste pleine, mais AUCUNE confiance dedans.

Corriger le premier ne corrige donc pas le second : on passe d'« aucun mot » à
« aucune confiance », et un tri qui exige une confiance écarte tout dans les
deux cas. En v2, le moteur ne retenait plus rien — en silence.

D'où `RepertoireConfiance` : on garde les spans d'origine et on retrouve la
confiance d'un run **par sa géométrie**, seule chose qui traverse intacte.
"""
from __future__ import annotations

#: Les champs qu'un span DOIT porter pour traverser `_group_text_lines` puis
#: `_group_paragraphs` sans perte. Relevés dans `PDFObjectEngine._extract_text`.
#:
#: Ce n'est pas une liste de souhaits : chacun est lu par le regroupement, et
#: son absence produit un des silences décrits en tête de module.
CHAMPS_REQUIS = (
    "bbox",      # [x0, y0, x1, y1] — boîte complète, blancs compris
    "origin",    # [x, y] du point de départ de l'écriture
    "text",      # le texte du span
    "font",      # nom de police, ou None si inconnue
    "size",      # corps, en points
    "color",     # (r, v, b) normalisés
    "flags",     # drapeaux de style (bit 2 = italique, bit 16 = gras)
    "bold",
    "italic",
    "dir",       # [dx, dy] — direction d'écriture ; [1, 0] = horizontal
    "_gw",       # largeur de glyphe APPROCHÉE = largeur / nb de caractères
    "_base",     # ligne de base = origin.y
    "_ink_x0",   # bord GAUCHE de l'encre (hors blancs de tête)
    "_ink_x1",   # bord DROIT de l'encre (hors blancs de queue)
)

#: Champ hors contrat, propre à l'OCR : la confiance de lecture du mot (0-100).
#: Le regroupement l'ignore et le laisse passer — c'est ce qui permet de juger
#: un bloc APRÈS coup, sur ce que l'OCR a su lire.
CHAMP_CONFIANCE = "_conf"


def span_depuis_mot(texte: str, bbox, *, confiance: float = 100.0,
                    taille: float | None = None,
                    police: str | None = None,
                    gras: bool = False, italique: bool = False,
                    couleur=(0.0, 0.0, 0.0)) -> dict:
    """Construit UN span conforme au contrat, à partir d'un mot lu.

    C'est le seul endroit du moteur OCR qui fabrique un span. Tout le reste —
    Tesseract, les tests, un banc de mesure — passe par ici, donc le contrat ne
    peut pas diverger entre le code de production et le code qui le vérifie.

    POURQUOI `_gw` EST CALCULÉ ET NON DEMANDÉ
    ------------------------------------------
    La largeur de glyphe gouverne la coupe en colonnes : `_group_horizontal_lines`
    coupe une rangée là où un blanc dépasse `_COL_SPLIT_FACTOR * _gw` médian.
    C'est une mesure RELATIVE, et c'est ce qui la rend valable à toute échelle —
    un seuil en points serait faux au premier changement de résolution.

    On la déduit de la boîte et du nombre de caractères, exactement comme le
    moteur PDF. Un mot de 5 lettres large de 50 pt donne 10 pt par glyphe.

    POURQUOI LA TAILLE SE DÉDUIT DE LA HAUTEUR
    -------------------------------------------
    Un scan n'a pas de « corps de police » : il a des pixels. La hauteur de la
    boîte d'encre en est la meilleure approximation disponible, et elle suffit
    au regroupement, qui ne s'en sert que pour des comparaisons relatives
    (tolérance de baseline, écart de paragraphe).

    On ne prétend PAS mesurer le corps typographique réel. La v1 a essayé d'en
    déduire la graisse et s'est trompée (densité d'encre 0,340 contre 0,337).
    """
    x0, y0, x1, y1 = (float(v) for v in bbox)
    texte = texte or ""
    # Le nombre de caractères NON BLANCS : « le » et « l e » n'ont pas la même
    # largeur de glyphe si l'on compte l'espace.
    nchar = max(1, len(texte.strip()))
    hauteur = max(1.0, y1 - y0)
    corps = float(taille) if taille else hauteur
    return {
        "bbox": [x0, y0, x1, y1],
        # La ligne de base d'un mot scanné est son BAS. Les jambages (p, g, q)
        # la font descendre un peu ; le regroupement tolère 0,45 × le corps,
        # donc l'approximation tient. Prétendre mieux demanderait de mesurer
        # les jambages, ce qui n'apporterait rien au regroupement.
        "origin": [x0, y1],
        "text": texte,
        # None, et non « Helvetica » : on ne SAIT pas quelle police c'est. La
        # v1 écrivait un nom par défaut, et tout ressortait en linéale — le
        # défaut de forme le plus visible. Une valeur inconnue se déclare.
        "font": police,
        "size": corps,
        "color": tuple(couleur),
        "flags": (16 if gras else 0) | (2 if italique else 0),
        "bold": bool(gras),
        "italic": bool(italique),
        "dir": [1.0, 0.0],          # Tesseract redresse la page avant de lire
        "_gw": (x1 - x0) / nchar,
        "_base": y1,
        # Un mot lu par l'OCR n'a pas de blancs de tête ni de queue : sa boîte
        # EST son encre. L'égalité est donc juste ici, alors qu'elle serait
        # fausse pour un span PDF (« ␣␣Child safety » s'étend au mur de sa
        # cellule).
        "_ink_x0": x0,
        "_ink_x1": x1,
        CHAMP_CONFIANCE: float(confiance),
    }


def valider_spans(spans) -> list[str]:
    """Les manquements au contrat, en clair. Liste vide = spans conformes.

    Rend des messages plutôt que de lever : l'appelant décide s'il refuse la
    page ou s'il journalise. Un test, lui, exige la liste vide.
    """
    manques = []
    for i, s in enumerate(spans or []):
        if not isinstance(s, dict):
            manques.append(f"span #{i} : {type(s).__name__} au lieu d'un dict")
            continue
        absents = [c for c in CHAMPS_REQUIS if c not in s]
        if absents:
            manques.append(f"span #{i} : champs absents {absents}")
            continue
        if len(s["bbox"]) != 4:
            manques.append(f"span #{i} : bbox à {len(s['bbox'])} valeurs")
        if s["_gw"] <= 0:
            # Silencieux et dévastateur : chaque espace deviendrait une
            # frontière de colonne.
            manques.append(f"span #{i} : _gw nulle ou négative ({s['_gw']})")
        if not s["dir"] or len(s["dir"]) != 2:
            manques.append(f"span #{i} : dir invalide ({s.get('dir')})")
    return manques


def mots_du_bloc(bloc) -> list[dict]:
    """Les mots (runs ou spans) de toutes les lignes d'un bloc.

    ACCEPTE LES DEUX FORMES, ET CE N'EST PAS DE LA COMPLAISANCE
    ------------------------------------------------------------
    `_group_text_lines` rend des lignes qui portent `runs` (refonte par style),
    pas `spans`. Un appelant qui fabrique ses blocs à la main, lui, fournira
    naturellement `spans`.

    Lire une seule des deux clés ne lève aucune erreur : on obtient une liste
    vide, et tout jugement porté ensuite sur ces mots est faussé en silence.
    C'est le défaut qui a fait qu'en v2 le moteur n'a plus rien retenu.
    """
    out = []
    for ligne in (bloc.get("lines") or []):
        out.extend(ligne.get("runs") or ligne.get("spans") or [])
    return out


class RepertoireConfiance:
    """Retrouve la confiance d'un mot APRÈS le regroupement, par sa géométrie.

    POURQUOI IL FAUT EN PASSER PAR LÀ
    ----------------------------------
    `_run_from_span` ne recopie que 11 clés nommées : `_conf` est jeté. On ne
    peut donc pas lire la confiance sur un run, et on ne veut PAS modifier le
    moteur PDF pour qu'il la transporte — ce serait le modifier pour arranger
    un cas OCR, exactement ce qu'on s'est interdit.

    Ce qui traverse le regroupement sans jamais changer, en revanche, c'est la
    GÉOMÉTRIE : un run porte le `bbox` de son span d'origine. On indexe donc
    les spans par leur boîte, et on retrouve la confiance à l'arrivée.

    LA CLÉ EST ARRONDIE, ET C'EST NÉCESSAIRE
    -----------------------------------------
    Les coordonnées sont des flottants. Le regroupement les recopie sans
    calcul, donc l'égalité stricte marcherait aujourd'hui — mais elle est
    fragile : une seule opération en virgule flottante introduite plus tard
    (une mise à l'échelle, une rotation) la casserait, et le silence
    reviendrait. On arrondit au dixième de point, bien en deçà de la taille
    d'un glyphe : deux mots distincts ne peuvent pas partager une clé.
    """

    def __init__(self, spans=()):
        self._par_boite: dict[tuple, float] = {}
        for s in spans or ():
            conf = s.get(CHAMP_CONFIANCE)
            if conf is not None:
                self._par_boite[self._cle(s.get("bbox"))] = float(conf)

    @staticmethod
    def _cle(bbox):
        if not bbox or len(bbox) < 4:
            return None
        return tuple(round(float(v), 1) for v in bbox[:4])

    def confiance(self, mot) -> float | None:
        """La confiance de ce run/span, ou None si on ne la connaît pas.

        `None` n'est pas 0 : « je ne sais pas » et « mal lu » appellent des
        décisions opposées. Les confondre ferait écarter du texte parfaitement
        lu dès que l'indexation raterait.
        """
        direct = mot.get(CHAMP_CONFIANCE)
        if direct is not None:
            return float(direct)          # span brut, pas encore regroupé
        return self._par_boite.get(self._cle(mot.get("bbox")))

    def confiances_du_bloc(self, bloc) -> list[float]:
        """Les confiances connues des mots d'un bloc, dans l'ordre de lecture.

        Les mots dont la confiance est introuvable sont OMIS, pas comptés zéro.
        Un appelant qui reçoit une liste vide doit le traiter comme « aucune
        information », jamais comme « mal lu ».
        """
        out = []
        for mot in mots_du_bloc(bloc):
            c = self.confiance(mot)
            if c is not None:
                out.append(c)
        return out
