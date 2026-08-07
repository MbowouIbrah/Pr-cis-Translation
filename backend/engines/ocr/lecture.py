"""La LECTURE d'une page scannée : des pixels aux spans.

CE MODULE NE DÉCIDE RIEN
-------------------------
Il lit, il mesure, il rend des spans. Il ne juge pas si un mot mérite d'être
traduit — c'est le rôle de `structure.py`, et l'ordre compte : la v1 triait au
MOT, avant tout regroupement, et c'est ce qui la faisait retenir des fragments
de dessin. Un mot seul ne dit pas s'il appartient à un paragraphe ou à une
illustration.

CE QUI EST REPRIS DES DEUX VERSIONS PRÉCÉDENTES
------------------------------------------------
Ces points ont été payés par la mesure ; ils ne se rediscutent pas sans
nouvelle mesure (bilan du 28/07) :

  · LA DOUBLE LECTURE — image prétraitée + image brute, puis union. La
    binarisation seule détruit les gros titres gras : une fenêtre adaptative de
    31 px tombe entièrement à l'intérieur d'un fût de lettre et le blanchit.
    Mesuré : 628 mots -> 464, confiance 73 -> 51 ;
  · LA BOÎTE D'ENCRE MESURÉE, jamais celle de Tesseract. Tesseract rend souvent
    la boîte de la LIGNE pour chaque mot (mesuré : 12 mots à y=[89,3 ; 96,7]),
    ce qui fait sortir des lignes au double de leur taille et ruine tout
    regroupement par baseline ;
  · ON RELIT TOUT, sans protéger les accents ni les mots courts : les protéger
    donnait 3,0 % et 4,8 % d'erreur contre 2,1 % sans garde.

CE QU'ON NE FAIT PAS, ET POURQUOI
----------------------------------
**On ne garde pas la segmentation de Tesseract.** `image_to_data` rend
`block_num`, `par_num`, `line_num` — et `par_num` est notoirement faux en
multi-colonnes et en présence de tableaux (c'est écrit dans la documentation de
Tesseract elle-même, « Hybrid Page Layout Analysis via Tab-Stop Detection »).

S'en servir importerait ses erreurs ET ferait diverger l'OCR du moteur PDF,
c'est-à-dire exactement les conflits qu'on cherche à éviter. **On ne garde que
les MOTS** ; la mise en page vient du moteur PDF.

OÙ CE MODULE TOURNE
--------------------
Dans Docker, et nulle part ailleurs : il dépend du binaire Tesseract, que le
poste de développement n'a pas. `disponible()` répond sans jamais lever, pour
que `/health` puisse le dire au lieu de laisser deviner.
"""
from __future__ import annotations

import logging

from engines.ocr.spans import span_depuis_mot

logger = logging.getLogger(__name__)

#: Résolution de lecture, en points par pouce. 300 est le seuil admis sous
#: lequel la précision chute quel que soit le moteur, et au-dessus duquel on
#: paye du temps sans rien gagner.
DPI_LECTURE = 300

#: Confiance sous laquelle un mot n'est même pas retenu comme span.
#:
#: TRÈS BAS, ET C'EST VOULU. Ce n'est pas ici qu'on trie — trier au mot est
#: précisément l'erreur de la v1. On écarte seulement ce que Tesseract lui-même
#: donne pour du bruit (`conf` négatif ou nul). Le vrai tri se fait sur des
#: BLOCS, dans `structure.py`, quand la structure est connue.
CONFIANCE_PLANCHER = 1.0


def disponible() -> dict:
    """Ce que `/health` doit pouvoir dire de l'OCR, sans jamais lever.

    C'est la seule capacité du projet qui repose sur un exécutable externe,
    donc la seule qui puisse manquer sur une machine où tout le reste marche.
    Une sonde qui plante rendrait le service « malade » alors que c'est la
    sonde qui casse.
    """
    etat = {"disponible": False, "tesseract": None, "langues": [],
            "raison": None}
    try:
        import pytesseract
    except ImportError as e:
        etat["raison"] = f"pytesseract absent : {e}"
        return etat
    try:
        etat["tesseract"] = str(pytesseract.get_tesseract_version())
        etat["langues"] = sorted(pytesseract.get_languages(config=""))
        etat["disponible"] = True
    except Exception as e:                       # binaire absent ou cassé
        etat["raison"] = f"binaire tesseract injoignable : {e}"
    return etat


def _image_de_page(page, dpi: int):
    """La page rendue en image, et l'échelle pour revenir aux points.

    On rend nous-mêmes plutôt que d'extraire l'image incorporée : un scan peut
    porter plusieurs images par page, orientées ou découpées. Rendre la page
    donne UNE image dont les coordonnées se ramènent aux points par un simple
    facteur — et c'est en points que le moteur PDF raisonne.
    """
    import fitz
    from PIL import Image

    echelle = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(echelle, echelle), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return img, echelle


def _pretraitee(img):
    """L'image binarisée, pour les textes ternes ou bruités.

    ON NE L'UTILISE JAMAIS SEULE — voir la double lecture en tête de module.
    Le seuil est GLOBAL (Otsu) et non adaptatif : une fenêtre adaptative de
    31 px tombe entièrement à l'intérieur d'un fût de gros titre gras et le
    blanchit. C'est l'impasse mesurée du 28/07 (628 mots -> 464).
    """
    gris = img.convert("L")
    try:
        import numpy as np
        a = np.asarray(gris)
        # Otsu à la main : pas d'OpenCV pour un histogramme de 256 cases.
        hist = np.bincount(a.ravel(), minlength=256).astype(float)
        total = hist.sum()
        if total <= 0:
            return gris
        omega = np.cumsum(hist) / total
        mu = np.cumsum(hist * np.arange(256)) / total
        mu_t = mu[-1]
        denom = omega * (1.0 - omega)
        denom[denom == 0] = 1e-9
        variance = (mu_t * omega - mu) ** 2 / denom
        seuil = int(np.argmax(variance))
        return gris.point(lambda v: 255 if v > seuil else 0, mode="L")
    except ImportError:
        # Sans numpy, on rend le gris : la double lecture perd son second
        # oeil, elle ne devient pas fausse.
        return gris


def _mots_bruts(img, langue: str):
    """Les mots lus par Tesseract sur UNE image, en pixels de cette image."""
    import pytesseract
    from pytesseract import Output

    # PSM 3 : segmentation automatique SANS orientation. On ne demande pas à
    # Tesseract de grouper (on jette ses blocs) mais il doit voir la page
    # entière pour lire des colonnes — PSM 6 (« bloc uniforme ») souderait
    # deux colonnes voisines en une seule ligne.
    d = pytesseract.image_to_data(img, lang=langue, config="--psm 3",
                                  output_type=Output.DICT)
    mots = []
    for i in range(len(d["text"])):
        texte = (d["text"][i] or "").strip()
        if not texte:
            continue
        try:
            conf = float(d["conf"][i])
        except (TypeError, ValueError):
            continue
        if conf < CONFIANCE_PLANCHER:
            continue
        x, y = float(d["left"][i]), float(d["top"][i])
        w, h = float(d["width"][i]), float(d["height"][i])
        if w <= 0 or h <= 0:
            continue
        # `line_num` — LA LIGNE selon Tesseract, et non son paragraphe.
        #
        # On jette sa segmentation en PARAGRAPHES (`par_num`), notoirement
        # fausse en multi-colonnes. Mais `line_num` est un autre signal, et il
        # est bon : mesuré sur la zone « Les permis moto » (page 1), où le
        # regroupement géométrique échouait, Tesseract rend les 6 lignes
        # EXACTEMENT, mots dans l'ordre :
        #
        #     ligne1 'Après deux ans de permis B, vous êtes'
        #     ligne2 'autorisé à conduire une 125 om avecune'
        #     ligne3 'formation pratique complémentaire.'
        #
        # C'est logique : Tesseract dispose des pixels et de son modèle de
        # ligne, là où l'aval ne voit plus que des boîtes. Refuser cette
        # information nous obligeait à la redéduire, moins bien.
        cle = (d.get("block_num", [0])[i] if "block_num" in d else 0,
               d.get("par_num", [0])[i] if "par_num" in d else 0,
               d.get("line_num", [0])[i] if "line_num" in d else 0)
        mots.append({"texte": texte, "conf": conf, "ligne": cle,
                     "boite": (x, y, x + w, y + h)})
    return mots


def _boite_encre(img_gris, boite, marge_relative: float = 0.25,
                 boite_de_ligne: bool = False, hauteur_max: float = 0.0):
    """Resserre une boîte sur l'ENCRE qu'elle contient réellement.

    POURQUOI C'EST INDISPENSABLE
    -----------------------------
    Tesseract rend souvent, pour chaque mot d'une ligne, la boîte de la LIGNE
    ENTIÈRE (mesuré en v1 : 12 mots partageant y=[89,3 ; 96,7]). Utilisée
    telle quelle, la hauteur du mot vaut celle de la ligne, le corps déduit est
    trop grand, et le regroupement par baseline part de travers.

    On cherche donc les pixels sombres dans une fenêtre ÉLARGIE : une boîte
    trop serrée coupe les jambages (p, g, q) et laisse des fantômes au
    masquage.

    LE PIÈGE DE LA MARGE, MESURÉ
    -----------------------------
    Cette fenêtre élargie attrape aussi l'encre de la ligne VOISINE quand
    l'interligne est serré. Mesuré sur le Code de la Route (page 1, bloc
    « Voiture de tourisme… ») :

        mot          Tesseract   encre AVEC marge libre
        tourisme       6,7 pt        8,2 pt   (+1,4)
        si             3,1 pt        4,1 pt   (+1,0)
        les 9 autres   ~3,8 pt       ~3,8 pt  (+0,0)

    Une minorité de mots enflait, et cela suffisait : leur `_base` glissait de
    plusieurs points, ils quittaient leur rangée, et leur paragraphe se
    déchirait — laissant des blocs d'un mot À L'INTÉRIEUR du bloc voisin
    (« si », « l'ensemble », « assises, »). C'est l'essentiel des inclusions
    signalées par les invariants.

    LA GARDE : l'encre trouvée ne peut pas DÉBORDER la boîte de Tesseract
    au-delà de la marge demandée. Tesseract se trompe sur la hauteur d'un mot,
    mais il ne se trompe pas de LIGNE — sa boîte reste le meilleur repère de
    l'endroit où le mot se trouve. On resserre donc librement (c'est le but) et
    on n'élargit que dans la limite de la marge.

    Rend `None` si la fenêtre ne contient aucune encre : le mot est alors une
    lecture de bruit, et l'appelant l'écarte.
    """
    try:
        import numpy as np
    except ImportError:
        return boite
    x0, y0, x1, y1 = boite
    marge = max(1.0, marge_relative * (y1 - y0))
    L, H = img_gris.size
    fx0 = max(0, int(x0 - marge)); fy0 = max(0, int(y0 - marge))
    fx1 = min(L, int(x1 + marge)); fy1 = min(H, int(y1 + marge))
    if fx1 <= fx0 or fy1 <= fy0:
        return None
    fen = np.asarray(img_gris.crop((fx0, fy0, fx1, fy1)))
    # Seuil relatif au contenu de la fenêtre : un texte blanc sur fond sombre
    # doit être trouvé aussi. On prend le quart le plus sombre comme encre.
    if fen.size == 0:
        return None
    sombre = fen < (int(fen.min()) + int(fen.max())) // 2
    if not sombre.any():
        return None
    lignes = np.where(sombre.any(axis=1))[0]
    cols = np.where(sombre.any(axis=0))[0]
    ex0 = float(fx0 + cols[0]); ey0 = float(fy0 + lignes[0])
    ex1 = float(fx0 + cols[-1] + 1); ey1 = float(fy0 + lignes[-1] + 1)
    if boite_de_ligne:
        # La boîte reçue est celle de la LIGNE : sa hauteur est fausse, et
        # surtout elle n'est pas COMPARABLE à celle d'un mot mesuré.
        #
        # C'est ce défaut de comparabilité qui casse tout, et il se calcule.
        # Mesuré page 2 : 28 % des mots portent une boîte de ligne. Sur une
        # même ligne physique cohabitent donc deux populations :
        #
        #     mot « de ligne »  h = 7,2 pt  ->  baseline = y0 + 0,70 x 7,2
        #     mot mesuré        h = 3,9 pt  ->  baseline = y0 + 0,70 x 3,9
        #     écart de baseline .............. 2,57 pt
        #     tolérance de rangée du plus petit (0,45 x 3,9) ... 1,75 pt
        #     2,57 > 1,75  ->  SÉPARÉS
        #
        # Deux mots voisins d'une même phrase partaient donc dans deux
        # rangées, la ligne se déchirait, et le paragraphe se dédoublait —
        # d'où les blocs #9 et #10 superposés sur 175 x 17 pt.
        #
        # CE QUI EST STABLE, ET QU'ON UTILISE : le BAS DE L'ENCRE. Mesuré sur
        # les mêmes mots, ligne par ligne :
        #     ligne 1 : tous les mots, encre_y1 = 72,7   (boîtes 7,2 ou 3,1)
        #     ligne 2 : tous les mots, encre_y1 = 79,4
        # Le bas de l'encre ne dépend pas de l'erreur de Tesseract sur la
        # hauteur : c'est la position du dernier pixel sombre, et les mots
        # d'une ligne reposent sur la même ligne d'écriture.
        #
        # On garde donc le BAS mesuré, et on reconstruit un haut plausible à
        # partir de la hauteur MÉDIANE d'un mot — inconnue ici, d'où le
        # recours à la boîte de Tesseract pour la seule borne haute.
        if ey1 <= ey0:
            return None
        # PLAFOND DE HAUTEUR — ESSAYÉ, MESURÉ, REJETÉ. Ne pas le refaire.
        #
        # L'idée : dans le texte qui s'enroule autour du camion (page 2),
        # l'interligne vaut ~6,5 pt et les hauteurs mesurées montaient à 8,9,
        # 9,1, 10,9 pt — PLUS que l'interligne lui-même. Plafonner la hauteur à
        # un multiple de la médiane de la page semblait donc évident.
        #
        # BALAYÉ de 1,2x à 3,0x la médiane : le total des violations reste
        # entre 22 et 29 sans tendance (22 / 29 / 23 / 23 / 22 / 22 / 22).
        # Le plafond ne mord pas là où ça compte. Même constat qu'en plafonnant
        # la `size` des spans (19 à 24, bruit).
        #
        # Le paramètre est conservé dans la signature parce qu'il est correct
        # et sans coût, mais il n'est PAS le levier : 15 des 22 violations
        # restantes viennent de LIGNES FUSIONNÉES, c'est-à-dire de la
        # tolérance de rangée du regroupement, pas de la mesure des boîtes.
        if hauteur_max > 0 and (ey1 - ey0) > hauteur_max:
            ey0 = ey1 - hauteur_max      # on garde le BAS, seul repère stable
        else:
            ey0 = max(ey0, y0)
        return (ex0, ey0, ex1, ey1)
    # La garde : on borne l'encre à la boîte de Tesseract élargie d'une
    # DEMI-marge. Resserrer reste libre ; s'étendre jusqu'à la ligne voisine
    # ne l'est pas.
    demi = 0.5 * marge
    ey0 = max(ey0, y0 - demi)
    ey1 = min(ey1, y1 + demi)
    if ey1 <= ey0:                       # la garde a tout mangé : on renonce
        return None
    return (ex0, ey0, ex1, ey1)


def _boites_de_ligne(mots, tolerance: float = 0.15) -> set[int]:
    """Les mots dont Tesseract a rendu la boîte de la LIGNE, pas celle du mot.

    LE DÉFAUT, MESURÉ
    ------------------
    Tesseract rend fréquemment, pour plusieurs mots consécutifs, une boîte
    verticale IDENTIQUE — celle de la ligne entière. Relevé page 1 du Code de
    la Route :

        autorisé conduire une 125 avecune  ->  tous y = 547,0-554,9  (h 7,9)
        Si vous désirez conduire une moto  ->  h 4,3 / 2,9 / 3,8 / 3,6 / 3,1

    La seconde ligne est mesurée mot par mot, la première non. Le v1 l'avait
    déjà constaté (« 12 mots à y=[89,3 ; 96,7] ») sans en tirer de détection.

    CE QUE ÇA CASSE
    ---------------
    La hauteur sert de `size` au regroupement, et la baseline s'en déduit. Un
    mot deux fois trop haut reçoit donc une baseline décalée de plusieurs
    points : il quitte sa rangée, sa ligne se déchire, et ses morceaux
    deviennent des paragraphes qui en croisent d'autres. C'est l'origine des
    blocs 32/34/35/40/41/42 signalés à l'écran.

    COMMENT ON LE RECONNAÎT SANS SEUIL ARBITRAIRE
    ----------------------------------------------
    Une boîte de LIGNE est partagée à l'identique par plusieurs mots. Deux
    mots réellement de même hauteur (« une » et « moto ») ne le sont qu'à
    quelques centièmes près ; deux mots qui portent la boîte de leur ligne le
    sont EXACTEMENT.

    On cherche donc les groupes d'au moins trois mots dont les bords haut ET
    bas coïncident à `tolerance` près (en fraction de leur hauteur). Trois, et
    non deux : deux mots voisins peuvent légitimement partager leurs bords
    (« du » et « la » côte à côte, sans hampe ni jambage). Trois identiques
    au centième ne se produisent pas par hasard.
    """
    suspects: set[int] = set()
    par_cle: dict[tuple, list[int]] = {}
    for i, m in enumerate(mots):
        _, y0, _, y1 = m["boite"]
        h = y1 - y0
        if h <= 0:
            continue
        # Clé arrondie à la tolérance : deux boîtes « identiques » tombent
        # dans la même case sans exiger l'égalité stricte des flottants.
        pas = max(1.0, tolerance * h)
        par_cle.setdefault((round(y0 / pas), round(y1 / pas)), []).append(i)
    for idx in par_cle.values():
        if len(idx) >= 3:
            suspects.update(idx)
    return suspects


def _fusionner(primaires, secondaires, tolerance: float = 0.5):
    """Union des deux lectures : les mots vus par l'un et pas par l'autre.

    Un mot déjà vu par la lecture primaire n'est pas repris — on ne veut pas
    deux spans superposés, qui feraient deux fois la même ligne. Le
    recouvrement se juge en aire, pas en distance : deux lectures du même mot
    se recouvrent largement, deux mots voisins non.
    """
    out = list(primaires)
    for cand in secondaires:
        cx0, cy0, cx1, cy1 = cand["boite"]
        aire_c = max(1e-6, (cx1 - cx0) * (cy1 - cy0))
        double = False
        for vu in primaires:
            vx0, vy0, vx1, vy1 = vu["boite"]
            l = min(cx1, vx1) - max(cx0, vx0)
            h = min(cy1, vy1) - max(cy0, vy0)
            if l > 0 and h > 0 and (l * h) >= tolerance * aire_c:
                double = True
                break
        if not double:
            out.append(cand)
    return out


def spans_de_page(page, langue: str = "fra", dpi: int = DPI_LECTURE,
                  double_lecture: bool = False) -> list[dict]:
    """Lit une page scannée et rend des spans conformes au contrat.

    Les coordonnées rendues sont en POINTS de la page (pas en pixels) : c'est
    l'unité du moteur PDF, et celle où les cadres se dessineront.

    LA DOUBLE LECTURE EST DÉSORMAIS OPTIONNELLE, ET ÉTEINTE PAR DÉFAUT
    -------------------------------------------------------------------
    La v1 l'avait rendue obligatoire, sur une mesure juste : la binarisation
    seule détruit les gros titres gras (628 mots -> 464, confiance 73 -> 51).
    D'où l'union « image prétraitée + image brute ».

    Mais cette mesure portait sur la lecture PRÉTRAITÉE SEULE contre la lecture
    BRUTE SEULE. Elle ne disait rien du coût de leur UNION, et ce coût est
    réel. Mesuré sur 3 pages du Code de la Route :

        lecture              mots    violations d'invariants
        double (union)       1437         22
        double, conf >= 60   1427         20
        SIMPLE (brute)       1415         18

    Ce que la seconde lecture apporte vraiment, une fois filtrée à 60 de
    confiance : **12 mots sur 1415**, dont plusieurs sont des MORCEAUX de mots
    que la première avait déjà lus entiers (« Usag » + « er » pour
    « Usagers », « Vie » pour « Vie pratique »). Les autres sont des débris :
    « mani, » (conf 35), « CL » (42), « TL, » (32), « V4 » (33), « ——— » (3).

    Ces doublons partiels sont précisément ce qui fabrique les boîtes de ligne
    et les chevauchements : deux lectures du même texte à des découpes
    différentes se superposent, et le regroupement doit trancher entre elles.

    On garde donc le paramètre — un document au contraste très faible pourrait
    en avoir besoin, et la mesure de la v1 reste vraie dans son cadre — mais on
    ne le paye plus par défaut.
    """
    img, echelle = _image_de_page(page, dpi)
    gris = img.convert("L")

    mots = _mots_bruts(img, langue)
    if double_lecture:
        mots = _fusionner(mots, _mots_bruts(_pretraitee(img), langue))

    # Les mots à qui Tesseract a donné la boîte de leur LIGNE. Pour ceux-là, sa
    # boîte n'est pas un repère : on ne s'en sert pas pour borner l'encre.
    suspects = _boites_de_ligne(mots)

    # L'ÉCHELLE DE LA PAGE : la hauteur médiane d'un mot, relevée sur les
    # boîtes de Tesseract. Elle sert de plafond à la mesure d'encre, pour que
    # celle-ci ne traverse pas la ligne voisine dans les zones denses.
    #
    # Médiane et non moyenne : un titre géant ou une cote de 90 pt (relevée
    # page 1) tirerait la moyenne sans rien dire du corps de texte courant.
    hauteurs = sorted(m["boite"][3] - m["boite"][1] for m in mots)
    med_h = hauteurs[len(hauteurs) // 2] if hauteurs else 0.0
    # 2,2 x la médiane : assez pour un mot à hampe ET jambage (« Jg »), qui
    # dépasse largement un mot moyen, mais moins que deux lignes.
    plafond = 2.2 * med_h if med_h > 0 else 0.0

    spans = []
    for i, m in enumerate(mots):
        encre = _boite_encre(gris, m["boite"], boite_de_ligne=(i in suspects),
                             hauteur_max=plafond)
        if encre is None:
            continue                     # aucune encre : lecture de bruit
        x0, y0, x1, y1 = (v / echelle for v in encre)
        if x1 <= x0 or y1 <= y0:
            continue
        s = span_depuis_mot(m["texte"], (x0, y0, x1, y1),
                            confiance=m["conf"])
        s["_ligne_ocr"] = m.get("ligne")
        spans.append(s)

    _aligner_sur_lignes_ocr(spans)
    _brider_les_corps(spans)
    spans.sort(key=lambda s: (round(s["_base"], 1), s["bbox"][0]))
    return spans


#: Plafond du corps d'un span, en multiple de la MÉDIANE de la page.
#:
#: `size` gouverne la tolérance de rangée du regroupement (0,45 x size) : une
#: boîte gonflée élargit sa propre tolérance et avale la ligne voisine. Le
#: plafond l'en empêche.
#:
#: BALAYÉ, une fois les lignes alignées : 14 / 9 / 8 / 8 / 11 pour
#: aucun / 1,0x / 1,2x / 1,4x / 1,6x. On prend 1,2 — le début du palier, qui
#: laisse respirer les vrais titres (mesurés à 1,19x au 90e centile).
#:
#: LE MÊME PLAFOND AVAIT ÉTÉ ESSAYÉ ET REJETÉ AVANT l'alignement des lignes
#: (51 / 21 / 19 / 24, bruit) : il ne mordait pas, parce que les baselines
#: étaient déjà dispersées en amont. Un correctif inefficace ne l'est pas
#: toujours définitivement — il peut attendre celui qui le rend utile.
_CORPS_MAX_MEDIANE = 1.2


def _brider_les_corps(spans) -> None:
    """Empêche une boîte gonflée d'élargir sa propre tolérance de rangée.

    On ne touche qu'à `size` — la boîte, l'encre et la baseline restent ce
    qu'on a mesuré. `size` n'est pas une donnée du document : c'est
    l'estimation de corps que le regroupement utilise pour ses comparaisons
    relatives, et une estimation trop grande y fait plus de mal que de bien.
    """
    tailles = sorted(s["size"] for s in spans if s.get("size"))
    if not tailles:
        return
    med = tailles[len(tailles) // 2]
    if med <= 0:
        return
    plafond = _CORPS_MAX_MEDIANE * med
    for s in spans:
        if s["size"] > plafond:
            s["size"] = plafond


def _aligner_sur_lignes_ocr(spans) -> None:
    """Donne UNE SEULE baseline à tous les mots d'une même ligne Tesseract.

    LE DÉFAUT QUE CECI CORRIGE
    ---------------------------
    Les hauteurs de boîtes ne sont pas homogènes : sur une même ligne
    cohabitent des mots à boîte de LIGNE (h ≈ 7,7 pt) et des mots mesurés
    individuellement (« B, » h 4,5 ; « à » h 4,1 ; « formation » h 4,1). Comme
    la baseline se déduit de la hauteur, ces deux populations reçoivent des
    baselines différentes — et le regroupement les met dans des rangées
    différentes.

    Résultat mesuré sur « Les permis moto » (page 1) : un bloc unique de 23 pt
    de haut contenant « autoriséformationdeuxàpratiqueconduireansde… », soit
    trois phrases mélangées, alors que Tesseract avait rendu les trois lignes
    PARFAITEMENT.

    LA CORRECTION
    -------------
    On aligne sur la MÉDIANE des baselines de la ligne. Médiane et non
    moyenne : un seul mot à boîte aberrante ne doit pas tirer toute la ligne.

    On ne touche PAS aux boîtes elles-mêmes — seulement à `_base` et
    `origin`, qui sont ce que lit le regroupement. La géométrie visible
    (cadres, encre) reste celle qu'on a mesurée.

    CE QU'ON N'UTILISE TOUJOURS PAS : `par_num`, la segmentation en
    PARAGRAPHES de Tesseract, fausse en multi-colonnes. La structure reste au
    moteur PDF ; on ne lui emprunte que le fait « ces mots sont sur la même
    ligne », que les pixels lui donnent et que nous n'avons plus.
    """
    par_ligne: dict = {}
    for s in spans:
        cle = s.get("_ligne_ocr")
        if cle is None:
            continue
        par_ligne.setdefault(cle, []).append(s)
    for groupe in par_ligne.values():
        if len(groupe) < 2:
            continue
        bases = sorted(s["_base"] for s in groupe)
        med = bases[len(bases) // 2]
        for s in groupe:
            s["_base"] = med
            s["origin"] = [s["bbox"][0], med]
