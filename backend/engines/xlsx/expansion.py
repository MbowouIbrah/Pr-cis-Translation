"""Expansion — une traduction plus longue que sa source doit encore se LIRE.

CE QUI EST EN JEU
-----------------
« Total » devient « Gesamtbetrag », « Qté » devient « Quantity ». Le texte
est juste ; sa présentation ne l'est plus :

- une cellule de texte déborde sur sa voisine VIDE (Excel la laisse
  s'étaler) — mais sur une voisine PLEINE, elle est coupée net ;
- une cellule à retour à la ligne garde la hauteur de sa ligne, et ses
  dernières lignes disparaissent ;
- un nombre dont le format porte un littéral traduit (`0" jours"` →
  `0" days"`) ne déborde jamais : il s'affiche en `#####`.

LA RÈGLE : PRÉSERVER CE QUE LA SOURCE MONTRAIT, SANS SEUIL
-----------------------------------------------------------
On ne décide pas qu'une colonne « est trop étroite » : on compare la
traduction à SA source, dans la MÊME place.

- Texte sur une ligne : la place disponible `A` est la colonne plus les
  voisines vides où le texte s'étale. Si la source tenait, la traduction doit
  tenir ; si la source était déjà coupée, la traduction l'est dans la même
  proportion (`A·t/s`) — l'auteur avait accepté cette coupe, on ne la
  rattrape pas en ouvrant une colonne de deux mètres. Une seule formule :
  `besoin = min(t, A·t/s)`.
- Retour à la ligne : la largeur ne bouge pas, la hauteur de la ligne suit
  le nombre de lignes (`ht · Lt/Ls`) — si la source avait une hauteur ;
  une ligne en hauteur automatique est laissée à Excel, qui la recalcule.
- Nombre : l'écart de largeur du littéral est rendu exactement à la colonne.
  On ne sait pas si le nombre tenait ; on sait que s'il tenait, il tient
  encore.

On n'élargit JAMAIS en dessous de l'origine et on ne rétrécit rien.

IDEMPOTENT PAR CONSTRUCTION
---------------------------
L'aperçu progressif réinjecte plusieurs fois dans le même dossier. Chaque
passe repart de l'instantané SOURCE (largeurs, hauteurs, textes) capturé à
l'ouverture : elle restaure `<cols>` et les hauteurs d'origine puis applique
le résultat. Deux passes sur le même état donnent le même fichier.

LA MESURE
---------
L'unité de largeur d'Excel est la largeur du chiffre « 0 » de la police par
défaut. On mesure donc le texte EN ZÉROS, avec Helvetica (intégrée à
PyMuPDF) comme police témoin : le rapport lettre/zéro varie peu d'une
sans-empattement à l'autre, ce qui rend la mesure indépendante de la police
réelle du classeur, qu'on n'a pas. Un caractère absent de la police témoin
compte un cadratin s'il est large (idéogrammes), 0,6 sinon.
"""
from __future__ import annotations

import bisect
import copy
import logging
import math
import unicodedata

import fitz
from lxml import etree

log = logging.getLogger(__name__)

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS = {"s": S}

#: Marge intérieure d'une cellule : 5 px sur un chiffre de 7 px (Calibri 11),
#: la convention d'Excel elle-même pour convertir une largeur en caractères.
MARGE = 5 / 7
#: Plafond du format : Excel refuse une colonne plus large.
LARGEUR_MAX = 255.0
#: Largeur par défaut DANS LE FICHIER (baseColWidth 8 + marge) — le 8,43
#: qu'affiche Excel est la même colonne, comptée sans marge.
LARGEUR_DEFAUT = 8 + MARGE


def _nombre(valeur, defaut: float) -> float:
    try:
        return float(valeur)
    except (TypeError, ValueError):
        return defaut


def _entier(valeur) -> int | None:
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return None

_POLICES = {}
_AVANCES: dict[tuple[bool, str], float] = {}


def _avance(car: str, gras: bool) -> float:
    """Avance d'un caractère, en cadratins, dans la police témoin."""
    cle = (gras, car)
    if cle not in _AVANCES:
        if gras not in _POLICES:
            _POLICES[gras] = fitz.Font("hebo" if gras else "helv")
        police = _POLICES[gras]
        cp = ord(car)
        if police.has_glyph(cp):
            _AVANCES[cle] = police.glyph_advance(cp)
        elif unicodedata.east_asian_width(car) in ("W", "F"):
            _AVANCES[cle] = 1.0
        else:
            _AVANCES[cle] = 0.6
    return _AVANCES[cle]


def mesure(morceaux: list[tuple[str, float, bool]], taille_defaut: float
           ) -> list[float]:
    """Largeur de chaque PARAGRAPHE, en zéros de la police par défaut.

    `morceaux` = [(texte, taille, gras)] — les runs d'une cellule riche. Un
    saut de ligne ouvre un nouveau paragraphe ; la liste rendue en a au moins
    un.
    """
    zero = _avance("0", False) * taille_defaut
    paragraphes = [0.0]
    for texte, taille, gras in morceaux:
        for car in texte:
            if car == "\n":
                paragraphes.append(0.0)
                continue
            paragraphes[-1] += _avance(car, gras) * taille / zero
    return paragraphes


# ── Styles ─────────────────────────────────────────────────────────────────
class Styles:
    """Ce que `styles.xml` dit de la place d'une cellule : police, alignement,
    format de nombre."""

    def __init__(self, racine) -> None:
        self.polices = []
        for f in racine.xpath("./s:fonts/s:font", namespaces=_NS):
            sz = f.find(f"{{{S}}}sz")
            b = f.find(f"{{{S}}}b")
            self.polices.append((
                _nombre(sz.get("val") if sz is not None else None, 11.0),
                b is not None and b.get("val", "1") not in ("0", "false")))
        if not self.polices:
            self.polices = [(11.0, False)]
        self.formats = {
            _entier(n.get("numFmtId")): n.get("formatCode") or ""
            for n in racine.xpath("./s:numFmts/s:numFmt", namespaces=_NS)
            if (n.get("numFmtId") or "").isdigit()}
        self.xfs = []
        for xf in racine.xpath("./s:cellXfs/s:xf", namespaces=_NS):
            al = xf.find(f"{{{S}}}alignment")
            fid = _entier(xf.get("fontId")) or 0
            self.xfs.append({
                "police": self.polices[fid] if fid < len(self.polices)
                else self.polices[0],
                "horizontal": (al.get("horizontal") if al is not None
                               else None) or "general",
                "wrap": al is not None and al.get("wrapText") in ("1", "true"),
                "shrink": al is not None
                and al.get("shrinkToFit") in ("1", "true"),
                "fmt": _entier(xf.get("numFmtId")) or 0,
            })
        if not self.xfs:
            self.xfs = [{"police": self.polices[0], "horizontal": "general",
                         "wrap": False, "shrink": False, "fmt": 0}]

    @property
    def taille_defaut(self) -> float:
        return self.polices[0][0]

    def xf(self, cellule) -> dict:
        s = cellule.get("s")
        i = int(s) if s and s.isdigit() else 0
        return self.xfs[i] if i < len(self.xfs) else self.xfs[0]


def _morceaux(element, police) -> list[tuple[str, float, bool]]:
    """Les runs d'un `<si>` / `<is>` : texte, taille, graisse. Les `<rPh>`
    (phonétique) ne s'affichent pas dans la cellule et sont ignorés."""
    if element is None:
        return []
    taille, gras = police
    out = []
    t = element.find(f"{{{S}}}t")
    if t is not None:
        out.append((t.text or "", taille, gras))
    for r in element.findall(f"{{{S}}}r"):
        rpr = r.find(f"{{{S}}}rPr")
        tz, gz = taille, gras
        if rpr is not None:
            sz = rpr.find(f"{{{S}}}sz")
            if sz is not None:
                tz = _nombre(sz.get("val"), taille)
            b = rpr.find(f"{{{S}}}b")
            if b is not None:
                gz = b.get("val", "1") not in ("0", "false")
        rt = r.find(f"{{{S}}}t")
        out.append(((rt.text or "") if rt is not None else "", tz, gz))
    return out


def _texte(morceaux) -> str:
    return "".join(m[0] for m in morceaux)


# ── Géométrie d'une feuille ────────────────────────────────────────────────
def _colonne(ref: str) -> int:
    n = 0
    for c in ref:
        if c.isalpha():
            n = n * 26 + (ord(c.upper()) - 64)
        else:
            break
    return n - 1


def _ligne(ref: str) -> int:
    chiffres = "".join(c for c in ref if c.isdigit())
    return int(chiffres) if chiffres else 0


def _plages_fusion(racine) -> list[tuple[int, int, int, int]]:
    out = []
    for m in racine.xpath("./s:mergeCells/s:mergeCell", namespaces=_NS):
        ref = m.get("ref") or ""
        if ":" not in ref:
            continue
        a, b = ref.split(":", 1)
        out.append((_colonne(a), _ligne(a), _colonne(b), _ligne(b)))
    return out


class Largeurs:
    """Largeur de chaque colonne telle que `<cols>` la déclare (0 si
    masquée)."""

    def __init__(self, racine) -> None:
        fmt = racine.find(f"{{{S}}}sheetFormatPr")
        defaut = LARGEUR_DEFAUT
        if fmt is not None:
            if fmt.get("defaultColWidth"):
                defaut = _nombre(fmt.get("defaultColWidth"), defaut)
            elif fmt.get("baseColWidth"):
                defaut = _nombre(fmt.get("baseColWidth"), 8) + MARGE
        self.defaut = defaut
        self.plages = []
        for col in racine.xpath("./s:cols/s:col", namespaces=_NS):
            lo, hi = _entier(col.get("min")), _entier(col.get("max"))
            if lo is None or hi is None:
                continue
            if col.get("hidden") in ("1", "true"):
                w = 0.0
            else:
                w = _nombre(col.get("width"), defaut)
            self.plages.append((lo - 1, hi - 1, w))

    def __call__(self, j: int) -> float:
        for lo, hi, w in self.plages:
            if lo <= j <= hi:
                return w
        return self.defaut


def _occupee(cellule) -> bool:
    """Une cellule BLOQUE le débordement dès qu'elle affiche quelque chose."""
    v = cellule.find(f"{{{S}}}v")
    if v is not None and (v.text or "") != "":
        return True
    # Une formule bloque même quand son résultat est vide ou pas en cache.
    return (cellule.find(f"{{{S}}}is") is not None
            or cellule.find(f"{{{S}}}f") is not None)


def _lignes(racine):
    """(numéro, `<row>`) — un `<row>` sans `r` suit le précédent, comme Excel
    le lit."""
    r = 0
    for row in racine.xpath("./s:sheetData/s:row", namespaces=_NS):
        r = _entier(row.get("r")) or r + 1
        yield r, row


def _cellules(row):
    """(colonne, `<c>`) — même règle de position implicite."""
    j = -1
    for c in row.findall(f"{{{S}}}c"):
        ref = c.get("r")
        j = _colonne(ref) if ref and ref[0].isalpha() else j + 1
        yield j, c


# ── Le calcul ──────────────────────────────────────────────────────────────
def _sections(code: str) -> list[str]:
    """Les sections `;` d'un `formatCode` (positif ; négatif ; zéro ; texte),
    sans couper dans un littéral entre guillemets ni sur un point-virgule
    échappé."""
    sections, cour, cite, i = [], [], False, 0
    while i < len(code):
        car = code[i]
        if car == "\\" and not cite and i + 1 < len(code):
            cour.append(code[i:i + 2])
            i += 2
            continue
        if car == '"':
            cite = not cite
        if car == ";" and not cite:
            sections.append("".join(cour))
            cour = []
        else:
            cour.append(car)
        i += 1
    sections.append("".join(cour))
    return sections


def _litteraux(code: str) -> list[str]:
    from engines.xlsx.engine import morceaux_de_format
    return morceaux_de_format(code)


def ajuster_feuille(source, courante, sst_source, sst_courant,
                    styles_source: Styles, styles_courants: Styles) -> bool:
    """Élargit les colonnes et rehausse les lignes de `courante` (arbre de la
    feuille traduite) d'après `source` (la même feuille à l'ouverture).

    Rend True si l'arbre a été modifié.
    """
    rs, rc = source.getroot(), courante.getroot()
    restaure = _restaurer(rs, rc)
    w0 = Largeurs(rs)
    taille_defaut = styles_courants.taille_defaut
    rtl = bool(rc.xpath("./s:sheetViews/s:sheetView[@rightToLeft='1']",
                        namespaces=_NS))

    cellules_src = {(r, j): c for r, row in _lignes(rs)
                    for j, c in _cellules(row)}
    lignes = dict(_lignes(rc))

    # Les fusions, ligne par ligne, mais seulement sur les lignes PRÉSENTES :
    # une fusion A1:XFD1048576 ne doit pas coûter 17 milliards de cases.
    presentes = sorted(lignes)
    fusions_de_ligne: dict[int, list[tuple]] = {}
    for f in _plages_fusion(rc):
        i = bisect.bisect_left(presentes, f[1])
        while i < len(presentes) and presentes[i] <= f[3]:
            fusions_de_ligne.setdefault(presentes[i], []).append(f)
            i += 1

    def fusion_de(r, j):
        for f in fusions_de_ligne.get(r, ()):
            if f[0] <= j <= f[2]:
                return f
        return None

    # Une fusion arrête le débordement sur toute sa largeur ; ses deux bords
    # suffisent à `_place`, qui ne cherche que la plus proche voisine pleine.
    occupees: dict[int, list[int]] = {
        r: [b for f in fs for b in (f[0], f[2])]
        for r, fs in fusions_de_ligne.items()}
    cellules = []
    for r, row in lignes.items():
        for j, c in _cellules(row):
            if _occupee(c):
                occupees.setdefault(r, []).append(j)
            cellules.append((r, j, c, cellules_src.get((r, j))))
    for r in occupees:
        occupees[r].sort()

    def texte_cellule(c, sst, police):
        typ = c.get("t") or ""
        if typ == "s":
            v = c.find(f"{{{S}}}v")
            if v is None or not (v.text or "").strip().isdigit():
                return None
            i = int(v.text.strip())
            return _morceaux(sst[i], police) if 0 <= i < len(sst) else None
        if typ == "inlineStr":
            return _morceaux(c.find(f"{{{S}}}is"), police)
        return None

    # Une DEMANDE = « ces colonnes, ensemble, doivent atteindre telle
    # largeur, compte tenu de la place que la cellule trouve autour d'elle ».
    demandes = []        # (colonnes, besoin, r, j, mode)
    enroulees = []       # (r, j, lignes_src_fn, morceaux_src, morceaux_trad)
    for r, j, c, c_src in cellules:
        if c_src is None:
            continue
        xf = styles_courants.xf(c)
        police = xf["police"]
        fusion = fusion_de(r, j)
        if fusion is not None and (fusion[0], fusion[1]) != (j, r):
            continue                       # seule l'ancre d'une fusion porte
        cols = (list(range(fusion[0], fusion[2] + 1)) if fusion else [j])
        propre0 = sum(w0(k) for k in cols)
        if propre0 <= 0:
            continue                       # colonne masquée : rien à lire

        trad = texte_cellule(c, sst_courant, police)
        src = texte_cellule(c_src, sst_source, police)
        if trad is not None and src is not None:
            if xf["shrink"]:
                continue                   # Excel réduit déjà la police
            horiz = xf["horizontal"]
            if xf["wrap"] or horiz in ("justify", "distributed"):
                if fusion is None or fusion[1] == fusion[3]:
                    enroulees.append((r, cols, src, trad))
                continue
            # Sans retour à la ligne, les paragraphes s'affichent bout à bout.
            t = sum(mesure(trad, taille_defaut))
            s = sum(mesure(src, taille_defaut))
            if t <= s:
                continue
            mode = "fige" if fusion else _sens(horiz, rtl)
            a0 = _place(cols, r, mode, w0, occupees)
            besoin = min(t, (a0 - MARGE) * t / s if s > 0 else t) + MARGE
            if besoin > a0:
                demandes.append((cols, besoin, r, mode))
            continue

        # Un NOMBRE : seul le littéral de son format a pu grandir.
        if (c.get("t") or "n") != "n" or not _occupee(c):
            continue
        code_src = styles_source.formats.get(styles_source.xf(c)["fmt"])
        code_trad = styles_courants.formats.get(xf["fmt"])
        if not code_src or not code_trad or code_src == code_trad:
            continue
        # Une section affiche TOUS ses littéraux à la fois : leurs écarts
        # s'additionnent ; une seule section s'affiche : on garde la pire.
        def largeur(texte):
            return sum(mesure([(texte, *police)], taille_defaut))
        ecart = max((sum(largeur(lt) - largeur(ls)
                         for ls, lt in zip(_litteraux(sec_s), _litteraux(sec_t)))
                     for sec_s, sec_t in zip(_sections(code_src),
                                             _sections(code_trad))),
                    default=0.0)
        if ecart > 0:
            demandes.append((cols, propre0 + ecart, r, "fige"))

    largeurs: dict[int, float] = {}

    def w(k: int) -> float:
        return largeurs.get(k, w0(k))

    # Élargir une colonne agrandit aussi la place des cellules qui débordent
    # sur elle : on répète jusqu'à ce que plus aucune demande ne manque de
    # place. Les largeurs ne font que croître ; le plafond LARGEUR_MAX peut
    # laisser une demande à jamais insatisfaite, d'où la borne explicite.
    for _ in range(len(demandes) + 1):
        bouge = False
        for cols, besoin, r, mode in demandes:
            manque = besoin - _place(cols, r, mode, w, occupees)
            if manque <= 1e-6:
                continue
            total = sum(w(k) for k in cols)
            for k in cols:
                part = w(k) / total if total > 0 else 1 / len(cols)
                largeurs[k] = min(LARGEUR_MAX, w(k) + manque * part)
            bouge = True
        if not bouge:
            break

    modifie = restaure
    for k, valeur in largeurs.items():
        if abs(valeur - w0(k)) > 1e-6:
            _poser_largeur(rc, k, round(valeur, 2))
            modifie = True

    # Hauteurs : la ligne prend le nombre de lignes de sa cellule la plus
    # exigeante, rapporté à ce que la source demandait.
    par_ligne: dict[int, list[int]] = {}
    for r, cols, src, trad in enroulees:
        propre0 = sum(w0(k) for k in cols) - MARGE
        propre = sum(w(k) for k in cols) - MARGE
        if propre0 <= 0 or propre <= 0:
            continue
        ls = sum(max(1, math.ceil(p / propre0))
                 for p in mesure(src, taille_defaut))
        lt = sum(max(1, math.ceil(p / propre))
                 for p in mesure(trad, taille_defaut))
        paire = par_ligne.setdefault(r, [1, 1])
        paire[0] = max(paire[0], ls)
        paire[1] = max(paire[1], lt)
    # Une ligne SANS hauteur source est en hauteur automatique : Excel et
    # LibreOffice la recalculent d'après le texte traduit, la figer la
    # casserait. Une hauteur posée sans `customHeight` reste non figée.
    lignes_src = dict(_lignes(rs))
    for r, (ls, lt) in par_ligne.items():
        ancienne = lignes_src.get(r)
        if lt <= ls or r not in lignes or ancienne is None:
            continue
        ht0 = _nombre(ancienne.get("ht"), 0.0)
        if ht0 <= 0:
            continue
        lignes[r].set("ht", f"{min(409.0, ht0 * lt / ls):.2f}")
        modifie = True
    return modifie


def _sens(horizontal: str, rtl: bool) -> str:
    """De quel côté un texte non enroulé déborde."""
    if horizontal in ("center", "centerContinuous"):
        return "centre"
    droite = horizontal == "right"
    if rtl:
        droite = not droite
    return "gauche" if droite else "droite"


def _place(cols, r, mode, w, occupees) -> float:
    """La place qu'un texte trouve : sa colonne, plus les voisines VIDES où il
    s'étale. Sans voisine pleine du côté où il déborde, la place est infinie
    — rien ne le coupe."""
    propre = sum(w(k) for k in cols)
    if mode == "fige":
        return propre
    pleines = occupees.get(r, [])
    j = cols[0]

    def a_droite():
        i = bisect.bisect_right(pleines, j)
        if i >= len(pleines):
            return math.inf
        return sum(w(k) for k in range(j + 1, pleines[i]))

    def a_gauche():
        i = bisect.bisect_left(pleines, j) - 1
        if i < 0:
            # Le bord de la feuille arrête le texte comme une voisine pleine.
            return sum(w(k) for k in range(0, j))
        return sum(w(k) for k in range(pleines[i] + 1, j))

    if mode == "droite":
        return propre + a_droite()
    if mode == "gauche":
        return propre + a_gauche()
    return propre + 2 * min(a_droite(), a_gauche())


# ── Écriture ───────────────────────────────────────────────────────────────
def _restaurer(rs, rc) -> bool:
    """Remet `<cols>` et les hauteurs de lignes dans leur état SOURCE.

    C'est ce qui rend la passe idempotente : elle s'applique toujours sur
    l'origine, jamais sur le résultat d'une passe précédente.
    """
    change = False
    cols_s = rs.find(f"{{{S}}}cols")
    cols_c = rc.find(f"{{{S}}}cols")
    if cols_c is not None and (cols_s is None or etree.tostring(cols_c)
                               != etree.tostring(cols_s)):
        rc.remove(cols_c)
        cols_c = None
        change = True
    if cols_c is None and cols_s is not None:
        donnees = rc.find(f"{{{S}}}sheetData")
        if donnees is not None:
            donnees.addprevious(copy.deepcopy(cols_s))
            change = True
    src = dict(_lignes(rs))
    for r, row in _lignes(rc):
        origine = src.get(r)
        for attr in ("ht", "customHeight"):
            voulu = origine.get(attr) if origine is not None else None
            if row.get(attr) != voulu:
                change = True
                if voulu is None:
                    del row.attrib[attr]
                else:
                    row.set(attr, voulu)
    return change


def _poser_largeur(racine, j: int, largeur: float) -> None:
    """Fixe la largeur de la colonne `j` (0-basée), en découpant la plage
    `<col min max>` qui la couvre si elle en couvre d'autres."""
    cols = racine.find(f"{{{S}}}cols")
    if cols is None:
        cols = etree.Element(f"{{{S}}}cols")
        racine.find(f"{{{S}}}sheetData").addprevious(cols)
    n = j + 1
    for col in cols.findall(f"{{{S}}}col"):
        lo, hi = _entier(col.get("min")), _entier(col.get("max"))
        if lo is None or hi is None or not lo <= n <= hi:
            continue
        if lo < n:
            avant = copy.deepcopy(col)
            avant.set("max", str(n - 1))
            col.addprevious(avant)
        if n < hi:
            apres = copy.deepcopy(col)
            apres.set("min", str(n + 1))
            col.addnext(apres)
        col.set("min", str(n))
        col.set("max", str(n))
        col.set("width", f"{largeur:g}")
        col.set("customWidth", "1")
        return
    neuve = etree.Element(f"{{{S}}}col", min=str(n), max=str(n),
                          width=f"{largeur:g}", customWidth="1")
    suivantes = [c for c in cols.findall(f"{{{S}}}col")
                 if (_entier(c.get("min")) or 0) > n]
    if suivantes:
        suivantes[0].addprevious(neuve)
    else:
        cols.append(neuve)


def chaines(racine) -> list:
    """Les `<si>` du magasin partagé, dans l'ordre des index."""
    return racine.xpath("./s:si", namespaces=_NS) if racine is not None else []


def instantane(dossier) -> dict[str, bytes]:
    """Les octets SOURCE de ce que la passe compare : magasin, styles,
    feuilles. À prendre avant toute injection."""
    xl = dossier / "xl"
    fichiers = [xl / "sharedStrings.xml", xl / "styles.xml",
                *sorted((xl / "worksheets").glob("sheet*.xml"))]
    return {str(f.relative_to(dossier)): f.read_bytes()
            for f in fichiers if f.exists()}


def ajuster_dossier(dossier, origine: dict[str, bytes]) -> None:
    """Applique la passe d'expansion à chaque feuille du classeur extrait."""
    def lire_origine(rel):
        return (etree.fromstring(origine[rel]).getroottree()
                if rel in origine else None)

    def lire_courant(rel):
        chemin = dossier / rel
        return etree.parse(str(chemin)) if chemin.exists() else None

    rel_sst = str((dossier / "xl" / "sharedStrings.xml").relative_to(dossier))
    rel_sty = str((dossier / "xl" / "styles.xml").relative_to(dossier))
    sst_s, sst_c = lire_origine(rel_sst), lire_courant(rel_sst)
    sty_s, sty_c = lire_origine(rel_sty), lire_courant(rel_sty)
    if sty_c is None:
        return
    styles_c = Styles(sty_c.getroot())
    styles_s = Styles(sty_s.getroot()) if sty_s is not None else styles_c
    chaines_s = chaines(sst_s.getroot() if sst_s is not None else None)
    chaines_c = chaines(sst_c.getroot() if sst_c is not None else None)
    for chemin in sorted((dossier / "xl" / "worksheets").glob("sheet*.xml")):
        rel = str(chemin.relative_to(dossier))
        if rel not in origine:
            continue
        # Une feuille que la passe ne sait pas lire reste telle quelle : la
        # présentation est un bonus, elle ne doit jamais coûter la traduction.
        try:
            courante = etree.parse(str(chemin))
            if ajuster_feuille(lire_origine(rel), courante, chaines_s,
                               chaines_c, styles_s, styles_c):
                courante.write(str(chemin), xml_declaration=True,
                               encoding="UTF-8", standalone=True)
        except Exception:
            log.warning("expansion ignorée pour %s", rel, exc_info=True)
