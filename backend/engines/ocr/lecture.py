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


#: Confiance minimale exigée d'un mot venu de la passe COULEUR, et longueur
#: minimale de son texte.
#:
#: Cette passe voit peu et se trompe beaucoup : mesurée sur 3 pages, elle
#: ajoute 37 mots dont **2 seulement sont bons** (« LA » + « SIGNALISATION »,
#: à 95-96 %). Les 35 autres sont du bruit — `'pi'`, `'£'`, `'Watlt'`, `'"L]'`
#: — avec des confiances de 1 à 54. Sans filtre, elle coûterait plus qu'elle
#: ne rapporte, exactement comme la double lecture.
#:
#: 45, et c'est BAS À DESSEIN. Un texte coloré est précisément ce que
#: Tesseract lit le moins bien : il annonce lui-même une confiance faible sur
#: des mots parfaitement corrects. Mesuré, « feux » (orange sur blanc) sort à
#: **47** et « LA » de « LA SIGNALISATION » à **60** — au-dessus de 75, les
#: deux étaient perdus.
#:
#: LE COÛT EST MESURÉ, PAS SUPPOSÉ. Descendre de 75 à 45 fait entrer 5 mots
#: sur 3 pages : 2 vrais (« LA », « feux ») et 3 rebuts (« D. », « Ps »,
#: « tie »). Le total de défauts de l'audit ne bouge pas — 16 avant, 16 après
#: — parce que `tri.py` écarte déjà les fragments de 2-3 caractères sans
#: structure. On paye donc 3 débris que l'aval absorbe pour 2 mots de titre.
#:
#: ⚠ 2 CARACTÈRES ET NON 3. Exiger 3 écartait « LA », pourtant lu à 96 % par
#: la passe saturation — le titre ressortait amputé de son article.
_CONF_MIN_COULEUR = 45.0
_CARS_MIN_COULEUR = 2


def _canal_couleur(img):
    """L'image vue par sa SATURATION : le texte coloré y devient sombre.

    LE DÉFAUT QU'ELLE RÉPARE
    -------------------------
    Un titre écrit en couleur vive sur fond clair a une LUMINOSITÉ proche de
    celle du fond — c'est la couleur qui le distingue, pas le contraste. La
    conversion en gris standard (`L`), qui est une moyenne pondérée des
    canaux, l'efface donc au lieu de le révéler.

    Mesuré page 2 : « LA SIGNALISATION » (vert sur blanc) est **absent** de la
    lecture normale et lu à **95 %** ici.

    COMMENT
    -------
    On calcule `max(RGB) - min(RGB)`, qui vaut 0 pour tout gris (noir, blanc,
    gris moyen) et devient grand dès qu'une couleur est saturée. En
    l'inversant, le texte coloré devient sombre sur fond clair — la forme que
    Tesseract attend.

    ⚠ ELLE NE REMPLACE JAMAIS LA LECTURE NORMALE : elle rend 19 à 39 mots là
    où l'autre en rend 450 à 690, puisqu'elle est aveugle au texte noir, qui
    est l'immense majorité. C'est une passe COMPLÉMENTAIRE, filtrée par
    `_CONF_MIN_COULEUR`, et fusionnée avec la première.
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        # Sans numpy, la passe couleur perd son oeil ; elle ne devient pas
        # fausse — on rend le gris, qui ne fera qu'ajouter des doublons que
        # `_fusionner` ecarte.
        return img.convert("L")
    a = np.asarray(img.convert("RGB")).astype(int)
    sat = a.max(axis=2) - a.min(axis=2)
    return Image.fromarray((255 - sat).astype("uint8"))


def _canal_distance_blanc(img):
    """L'image vue par sa DISTANCE AU BLANC : tout ce qui n'est pas blanc
    devient sombre, quelle que soit sa couleur.

    LE DÉFAUT QU'ELLE RÉPARE, ET EN QUOI ELLE DIFFÈRE DE `_canal_couleur`
    ----------------------------------------------------------------------
    Un titre JAUNE sur fond blanc a une saturation forte — `_canal_couleur`
    devrait donc le voir — mais aussi une luminosité très haute : il reste
    pâle dans les deux canaux, et Tesseract ne l'accroche pas. Mesuré page 2,
    « Véhicule » (jaune) et « Usagers » (bleu clair) manquaient aux deux
    lectures.

    On calcule `max(255 - R, 255 - G, 255 - B)`, c'est-à-dire l'écart au blanc
    sur le canal le PLUS éloigné. Le blanc pur donne 0 ; le jaune (255,200,0)
    donne 255 par son canal bleu, alors que sa luminosité vaut ~230. Un pâle
    coloré devient donc franchement sombre.

    ⚠ CELLE-CI EST UNE LECTURE COMPLÈTE, pas un complément marginal : mesurée,
    elle rend **659 mots** contre 663 pour la lecture normale — elle voit
    aussi tout le texte noir. C'est ce qui la distingue de `_canal_couleur`
    (19 mots) et ce qui justifie de la fusionner comme une vraie seconde
    lecture, filtrée par la même confiance.
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return img.convert("L")
    a = np.asarray(img.convert("RGB")).astype(int)
    return Image.fromarray((255 - (255 - a).max(axis=2)).astype("uint8"))


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


def _mots_bruts(img, langue: str, passe: str = "n"):
    """Les mots lus par Tesseract sur UNE image, en pixels de cette image.

    `passe` identifie la LECTURE dont proviennent ces mots, et cette étiquette
    est indispensable dès qu'on en fusionne deux.

    LE BUG QU'ELLE FERME. Les clés de ligne (`block_num`, `par_num`,
    `line_num`) sont numérotées par Tesseract PAR APPEL : la ligne (21,1,1) de
    la passe couleur n'a rien à voir avec la (21,1,1) de la passe normale.
    Sans distinction, `_aligner_sur_lignes_ocr` les confond et donne une
    baseline commune à des mots distants de toute la page — mesuré,
    « SIGNALISATION » (y = 352,8) recevait la baseline 64,56, et le total des
    défauts passait de 15 à 35.
    """
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
        cle = (passe,
               d.get("block_num", [0])[i] if "block_num" in d else 0,
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
        # Même garde HORIZONTALE que dans le cas ordinaire : la hauteur de
        # cette boîte est fausse, sa LARGEUR ne l'est pas — Tesseract sait où
        # le mot commence et finit. Sans elle, l'encre mordait le voisin.
        demi = 0.5 * marge
        ex0 = max(ex0, x0 - demi)
        ex1 = min(ex1, x1 + demi)
        if ex1 <= ex0:
            return None
        return (ex0, ey0, ex1, ey1)
    # La garde : on borne l'encre à la boîte de Tesseract élargie d'une
    # DEMI-marge. Resserrer reste libre ; s'étendre jusqu'à la ligne voisine
    # ne l'est pas.
    #
    # ⚠ EN X AUTANT QU'EN Y, et l'oubli coûtait cher. La garde ne bornait que
    # le vertical : horizontalement l'encre s'étendait librement sur toute la
    # marge et mordait le mot d'à côté. Mesuré page 2 : Tesseract rend
    # **32 paires** de mots qui se chevauchent, et notre mesure d'encre en
    # produisait **109** — elle élargissait au lieu de resserrer.
    #
    # C'est l'origine des textes collés qu'on lit dans les blocs
    # (« permis »+« C » se recouvraient de 4,80 pt, « to »+« MA » de 8,64),
    # et donc des lignes que le regroupement n'arrivait plus à séparer.
    demi = 0.5 * marge
    ex0 = max(ex0, x0 - demi)
    ex1 = min(ex1, x1 + demi)
    ey0 = max(ey0, y0 - demi)
    ey1 = min(ey1, y1 + demi)
    if ey1 <= ey0 or ex1 <= ex0:         # la garde a tout mangé : on renonce
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
    par_bas: dict[tuple, list[int]] = {}
    hauteurs = sorted(m["boite"][3] - m["boite"][1] for m in mots
                      if m["boite"][3] > m["boite"][1])
    med = hauteurs[len(hauteurs) // 2] if hauteurs else 0.0
    for i, m in enumerate(mots):
        _, y0, _, y1 = m["boite"]
        h = y1 - y0
        if h <= 0:
            continue
        # Clé arrondie à la tolérance : deux boîtes « identiques » tombent
        # dans la même case sans exiger l'égalité stricte des flottants.
        pas = max(1.0, tolerance * h)
        par_cle.setdefault((round(y0 / pas), round(y1 / pas)), []).append(i)
        # LE BORD BAS SEUL SUFFIT QUAND LA BOÎTE EST TROP HAUTE, et c'est le
        # cas que la règle des DEUX bords laissait passer. Mesuré page 1 sur
        # le texte BARRÉ « Après deux ans de permis B, vous êtes / autorisé à
        # conduire une 125 avec une » :
        #
        #     bas partagé  550,08 · 550,08 · 550,08 · 550,08   (identique)
        #     haut         541,68 · 541,92 · 542,64 · 542,88   (variable)
        #
        # La barre traverse la ligne : Tesseract l'inclut et gonfle la boîte
        # VERS LE HAUT, de façon inégale selon les glyphes rencontrés. Le bas,
        # lui, reste posé sur la ligne — c'est le repère qui tient, et c'est
        # déjà la leçon retenue pour les baselines.
        #
        # On n'accepte ce signal affaibli QUE pour les boîtes manifestement
        # trop hautes (> 1,5 x la médiane) : sinon trois mots ordinaires sans
        # jambage, alignés sur leur ligne, seraient tous suspects.
        if med > 0 and h > 1.5 * med:
            par_bas.setdefault((round(y1 / pas),), []).append(i)
    for idx in par_cle.values():
        if len(idx) >= 3:
            suspects.update(idx)
    for idx in par_bas.values():
        if len(idx) >= 3:
            suspects.update(idx)
    return suspects


def _plafonds_par_ligne(mots, interligne: float) -> dict:
    """Le plafond de hauteur, calculé LIGNE PAR LIGNE et non pour la page.

    LA RÉGRESSION QUE CETTE FONCTION RÉPARE
    -----------------------------------------
    Plafonner à l'interligne de la PAGE écrase les TITRES : ils dépassent
    légitimement l'interligne du corps, puisqu'ils sont écrits plus gros.
    Mesuré page 2 : **78 mots rabotés**, dont

        'LES'  9,84 -> 4,56 pt      'AUTRES'  10,08 -> 4,56 pt
        'PERMIS'  9,84 -> 4,56      'SOMMAIRE'  9,84 -> 4,56

    — c'est-à-dire tous les titres de la page, ramenés à la taille du corps.
    Signalé à l'œil : « le paragraphe n'encadre pas bien la hauteur du mot ».

    CE QUI DISTINGUE UN TITRE D'UNE BOÎTE DE LIGNE FAUSSE
    -------------------------------------------------------
    Les deux sont plus hauts que l'interligne du corps. Mais :

      · un TITRE est écrit gros, et TOUS les mots de sa ligne le sont ;
      · une BOÎTE DE LIGNE fausse cohabite sur sa ligne avec des mots
        correctement mesurés — c'est même la définition du défaut (deux
        populations sur une même ligne physique).

    On compare donc chaque mot aux AUTRES MOTS DE SA LIGNE, jamais à la page.
    Le plafond d'une ligne est la MÉDIANE des hauteurs qu'elle porte, majorée
    d'une marge pour les hampes et jambages. Une ligne de titre a une médiane
    haute, donc un plafond haut : rien n'y est raboté. Une ligne de corps où
    trois mots portent la boîte de la ligne a une médiane basse : ceux-là sont
    ramenés, et c'est le but.

    L'interligne de la page ne sert plus que de GARDE-FOU ABSOLU : aucun
    plafond ne descend sous lui, sinon une ligne entièrement composée de
    boîtes fausses se plafonnerait sur sa propre erreur.
    """
    par_ligne: dict = {}
    for i, m in enumerate(mots):
        h = m["boite"][3] - m["boite"][1]
        if h > 0:
            par_ligne.setdefault(m.get("ligne"), []).append(h)
    out = {}
    for cle, hs in par_ligne.items():
        hs = sorted(hs)
        med = hs[len(hs) // 2]
        # 1,6 x la médiane de la LIGNE : de quoi loger une capitale à jambage
        # sans laisser passer une boîte qui vaut deux lignes.
        out[cle] = max(1.6 * med, interligne)
    return out


def _paires_trop_hautes(mots, interligne: float,
                        tolerance: float = 0.15) -> set[int]:
    """Les PAIRES de mots qui portent la boîte de leur ligne.

    `_boites_de_ligne` exige trois mots partageant leurs bords, et cette
    exigence est juste : deux mots voisins sans hampe ni jambage (« du » et
    « la ») les partagent légitimement, et les marquer suspects abîmerait des
    mesures correctes.

    Mais un second fait lève l'ambiguïté sans rien supposer : une boîte plus
    haute que l'INTERLIGNE ne peut pas être celle d'un mot, parce qu'elle
    empiéterait sur la ligne voisine. Deux mots aux bords identiques ET plus
    hauts que l'interligne portent donc la boîte de leur ligne — la condition
    est plus forte que celle des trois mots, pas plus faible.

    Mesuré page 1 : `'pratique'` et `'complémentaire.'`, 9,90 pt pour un
    interligne de 6,3. Jamais marqués faute d'un troisième mot, et à eux seuls
    la cause du chevauchement de 86,40 x 4,59 pt entre les blocs 18 et 20.

    ⚠ IMPASSE MESURÉE — CETTE FONCTION N'EST PAS APPELÉE.
    Le raisonnement ci-dessus est juste sur le cas qui l'a inspiré et FAUX en
    général : page 1 passe de 4 à 6 défauts, total 17 -> 19. Deux mots aux
    bords identiques restent trop peu — même plus hauts que l'interligne, ils
    se produisent par hasard sur une page dense, et les rectifier décale des
    baselines correctes.

    Conservée ici avec sa mesure, pour ne pas la refaire. Cf. la règle
    « tout correctif se prouve sur du synthétique, et on écrit les impasses ».
    """
    if interligne <= 0:
        return set()
    par_cle: dict[tuple, list[int]] = {}
    for i, m in enumerate(mots):
        _, y0, _, y1 = m["boite"]
        h = y1 - y0
        if h <= interligne:
            continue
        pas = max(1.0, tolerance * h)
        par_cle.setdefault((round(y0 / pas), round(y1 / pas)), []).append(i)
    out: set[int] = set()
    for idx in par_cle.values():
        if len(idx) >= 2:
            out.update(idx)
    return out


#: Écart maximal, en points, entre deux morceaux d'un MÊME mot coupé par
#: Tesseract. Deux lettres consécutives d'un mot se touchent ; deux mots
#: distincts sont séparés d'au moins une chasse d'espace.
#:
#: 0,4 pt à 300 dpi ≈ 1,7 pixel. C'est le jeu de mesure, pas un espace.
_ECART_MEME_MOT = 0.4

#: Part de bande verticale commune exigée. Deux morceaux d'un même mot
#: partagent leur ligne d'écriture presque exactement.
_BANDE_MEME_MOT = 0.7


def _recoller_mots_coupes(mots, echelle: float = 1.0):
    """Recolle les morceaux d'un mot que Tesseract a coupé en deux.

    LE CAS RÉEL, MESURÉ. L'onglet « Véhicule » (page 2) ressort en « Véhic »
    (conf 91) + « ule » (conf 80), **dans deux blocs Tesseract différents**
    (35 et 39) alors que les deux boîtes sont jointives à 0,24 pt près. Rien
    en aval ne les recolle : le regroupement en lignes travaille sur des
    boîtes et voit deux mots voisins normaux.

    TROIS CONDITIONS, ET LEUR CONJONCTION EST TRÈS SÉLECTIVE — mesurée sur
    3 pages et 2 passes de lecture, elle ne désigne **qu'une seule paire**,
    celle-là :

      1. ADJACENTES : l'écart horizontal est nul à la mesure près. Deux mots
         distincts sont séparés d'une chasse d'espace, jamais de 0,2 pt ;
      2. MÊME BANDE : elles partagent leur ligne d'écriture ;
      3. LIGNES OCR DIFFÉRENTES : c'est le signal qui achève la preuve.
         Deux mots réellement voisins appartiennent à la MÊME ligne pour
         Tesseract ; deux morceaux qu'il a mal découpés tombent dans deux
         blocs, et c'est précisément l'erreur qu'on répare. Sans cette
         condition, on souderait tous les mots serrés d'une même ligne.
    """
    if len(mots) < 2:
        return mots
    ordonnes = sorted(mots, key=lambda m: (m["boite"][1], m["boite"][0]))
    absorbes = set()
    for i, a in enumerate(ordonnes):
        if i in absorbes:
            continue
        for j, b in enumerate(ordonnes):
            if j == i or j in absorbes:
                continue
            ba, bb = a["boite"], b["boite"]
            if ba[0] > bb[0]:
                continue
            ecart = (bb[0] - ba[2]) / echelle
            if not (-_ECART_MEME_MOT < ecart < _ECART_MEME_MOT):
                continue
            rec = min(ba[3], bb[3]) - max(ba[1], bb[1])
            court = min(ba[3] - ba[1], bb[3] - bb[1])
            if court <= 0 or rec / court < _BANDE_MEME_MOT:
                continue
            if a["ligne"] == b["ligne"]:
                continue            # vrais voisins : on ne touche pas
            a["texte"] = (a.get("texte") or "") + (b.get("texte") or "")
            a["boite"] = (min(ba[0], bb[0]), min(ba[1], bb[1]),
                          max(ba[2], bb[2]), max(ba[3], bb[3]))
            a["conf"] = min(float(a.get("conf", 0)), float(b.get("conf", 0)))
            absorbes.add(j)
    return [m for k, m in enumerate(ordonnes) if k not in absorbes]


#: Hauteur, en multiples de la médiane de la page, au-delà de laquelle un span
#: n'est plus du corps de texte. Un titre de page atteint 2,5 à 3 x ; au-delà,
#: c'est presque toujours un morceau de dessin lu comme du texte.
_GEANT_MEDIANE = 2.5

#: Part de sa hauteur qu'un voisin doit partager pour être « du même corps ».
_MEME_CORPS = 0.4


def _debris_de_dessin(spans) -> list[int]:
    """Les spans qui sont des morceaux de DESSIN lus comme du texte.

    LE DÉFAUT, VU À L'ÉCRAN. Le bloc « La signalisation — 07 à 52 » s'étendait
    jusqu'au bord droit de la page parce qu'il avalait « LUS » — en réalité le
    panneau de signalisation, lu à 60 % de confiance sur 21,9 pt de haut.
    Même chose pour « 4: » (23,7 pt), « | » (22,4), « Ps » (18,0), et pour les
    détails du camion (« ar », « ae\\a ») page 1.

    TROIS CONDITIONS, ET IL LES FAUT TOUTES LES TROIS
    ---------------------------------------------------
    Aucune ne suffit seule, et chacune protège d'une erreur mesurée :

      1. BIEN PLUS HAUT que le corps de la page. Nécessaire mais très
         insuffisant : `'2009'`, `'CHAUSSÉES'`, `'PERMIS'` le sont aussi ;

      2. SEUL DE SON CORPS sur sa bande. Un vrai titre a des compagnons de
         même taille — « CHAUSSÉES SANS MARQUAGE » en compte 4, « Edition
         2009 TVL » en compte 2. Un morceau de dessin n'en a aucun. Ce
         critère seul emporterait pourtant « Usagers » et « Véhicule », qui
         sont de vrais titres isolés dans leur onglet ;

      3. PAS UN MOT. C'est ce qui les sauve : `'Usagers'` et `'Véhicule'`
         sont alphabétiques et longs, `'4:'`, `'|'`, `'Ps'`, `'ae\\a'` non.

    Mesuré sur 3 pages : la conjonction écarte **5 spans, tous du bruit**, et
    ne touche aucun mot réel. La confiance n'entre PAS dans la règle — voir
    `tri.py` : elle dit si un mot est bien LU, pas s'il est du TEXTE, et « | »
    est lu à 87 %.

    ⚠ IMPASSE MESURÉE — CETTE FONCTION N'EST PAS APPELÉE.
    Le tri est juste (5 rebuts, 0 vrai mot perdu) et pourtant le résultat est
    NÉGATIF : **17 -> 20 défauts**, avec `bloc_dans_bloc` qui reparaît. Deux
    raisons, et elles se cumulent :

      · elle RATE sa cible principale. « LUS » (le panneau de signalisation)
        est alphabétique et long : l'exception n° 3, écrite pour sauver
        « Usagers », le sauve lui aussi. Le bloc #6 s'étend toujours jusqu'au
        bord de la page ;

      · retirer un span change la MÉDIANE des hauteurs et les rangées de
        baseline. Cinq retraits suffisent à disloquer des blocs corrects
        ailleurs — le même mécanisme que la substitution de mots (15 -> 34).

    Écarter du bruit ne se paye donc pas seulement en mots perdus : ça se
    paye en géométrie. Toute reprise doit se mesurer sur l'audit complet, pas
    sur la qualité du tri.

    AUTRES CRITÈRES ESSAYÉS SUR LE MÊME DÉFAUT, TOUS MESURÉS, TOUS ÉCARTÉS :

      · DENSITÉ D'ENCRE dans la boîte. Ne sépare rien : « Ps » (bruit) donne
        0,531 et « Index » (vrai mot) 0,354 ;
      · ÉCART-TYPE des gris. Même conclusion, les deux populations se
        recouvrent entièrement ;
      · CONFIANCE de Tesseract. « | » est lu à 87 %, « Véhicule » à 80 % —
        elle classe à l'envers. C'est la mesure déjà faite dans `tri.py` ;
      · TITRE COLLÉ À SON TEXTE (« Accidents » + « Les statistiques, ») :
        chercher une chute de corps DURABLE dans une ligne déjà formée. Le
        trait vertical « | » (h = 22,4) fausse la mesure et la coupe tombe
        après lui ; en passant par les médianes de chaque groupe pour le
        neutraliser, on récolte 2 faux positifs sur 3 (une entrée de sommaire
        « L'arrêt et le stationnement / 81 à 96 » coupée en deux). Aucun
        ratio de 1,5 à 2,0 ne fait mieux.
    """
    if not spans:
        return []
    hs = sorted(s["bbox"][3] - s["bbox"][1] for s in spans)
    med = hs[len(hs) // 2] if hs else 0.0
    if med <= 0:
        return []
    out = []
    for i, s in enumerate(spans):
        b = s["bbox"]
        h = b[3] - b[1]
        if h <= _GEANT_MEDIANE * med:
            continue
        texte = (s.get("text") or "").strip()
        if texte.isalpha() and len(texte) >= 3:
            continue                      # un vrai mot : on n'y touche pas
        compagnon = False
        for autre in spans:
            if autre is s:
                continue
            ab = autre["bbox"]
            ah = ab[3] - ab[1]
            if (min(b[3], ab[3]) - max(b[1], ab[1]) > 0.5 * ah
                    and abs(ah - h) < _MEME_CORPS * h):
                compagnon = True
                break
        if not compagnon:
            out.append(i)
    return out


def _interligne(mots) -> float:
    """L'écart médian entre deux lignes d'écriture consécutives, en pixels.

    RELEVÉ SUR LA PAGE, JAMAIS CHOISI. C'est ce qui en fait un repère et non
    un réglage : l'interligne est une propriété du document qu'on lit, pas une
    valeur qu'on ajuste jusqu'à ce que le résultat plaise.

    On mesure sur le BAS des boîtes, pour la raison déjà retenue partout
    ici : le bas repose sur la ligne d'écriture, le haut dépend des glyphes
    rencontrés (et, sur du texte barré, de la barre elle-même).

    Médiane et non moyenne : une page mêle du corps de texte et des titres,
    et quelques grands écarts de titre ne doivent pas déplacer le repère du
    corps courant.

    Rend 0,0 si la page n'a pas assez de lignes pour conclure — l'appelant
    n'applique alors aucun plafond, ce qui est le comportement d'avant.
    """
    bas = sorted({round(m["boite"][3], 1) for m in mots})
    if len(bas) < 3:
        return 0.0
    # On ne retient que les écarts PLAUSIBLES pour un interligne. Deux mots de
    # la même ligne donnent un écart quasi nul (bruit de mesure), deux blocs
    # éloignés un écart énorme : ni l'un ni l'autre n'est un interligne.
    hauteurs = sorted(m["boite"][3] - m["boite"][1] for m in mots)
    med_h = hauteurs[len(hauteurs) // 2] if hauteurs else 0.0
    if med_h <= 0:
        return 0.0
    ecarts = sorted(b - a for a, b in zip(bas, bas[1:])
                    if 0.5 * med_h < b - a < 5.0 * med_h)
    return float(ecarts[len(ecarts) // 2]) if ecarts else 0.0


def _fusionner(primaires, secondaires, tolerance: float = 0.5):
    """Union des deux lectures : les mots vus par l'un et pas par l'autre.

    Un mot déjà vu par la lecture primaire n'est pas repris — on ne veut pas
    deux spans superposés, qui feraient deux fois la même ligne. Le
    recouvrement se juge en aire, pas en distance : deux lectures du même mot
    se recouvrent largement, deux mots voisins non.

    DEUX DÉFAUTS CORRIGÉS, TOUS DEUX VUS SUR DES CAS RÉELS
    --------------------------------------------------------
    1. ON COMPARE À TOUT CE QUI EST DÉJÀ RETENU, et non aux seules primaires.
       Avec deux passes secondaires, la seconde ne voyait pas ce que la
       première venait d'ajouter : « Usagers » (lu entier par la passe
       distance-au-blanc) cohabitait avec « rs », son propre fragment.

    2. L'AIRE COMMUNE SE RAPPORTE AU PLUS PETIT DES DEUX. Rapportée au seul
       candidat, un candidat LARGE passait à côté d'un petit qu'il recouvre :
       « Véhic » (250,8 → 287,0) et « ule » (281,0 → 299,5) étaient tous deux
       retenus, alors que le second est la fin du premier. Comparer au plus
       petit rend le test symétrique — c'est la même règle que partout
       ailleurs ici (`bloc_dans_bloc`, `_recouvrement_x`).
    """
    out = list(primaires)
    for cand in secondaires:
        cx0, cy0, cx1, cy1 = cand["boite"]
        aire_c = max(1e-6, (cx1 - cx0) * (cy1 - cy0))
        rival = None
        for k, vu in enumerate(out):
            vx0, vy0, vx1, vy1 = vu["boite"]
            l = min(cx1, vx1) - max(cx0, vx0)
            h = min(cy1, vy1) - max(cy0, vy0)
            if l <= 0 or h <= 0:
                continue
            aire_v = max(1e-6, (vx1 - vx0) * (vy1 - vy0))
            if (l * h) >= tolerance * min(aire_c, aire_v):
                rival = k
                break
        if rival is None:
            out.append(cand)
        elif _prolonge(cand, out[rival]):
            out[rival] = cand
    return out


def _prolonge(cand, vu) -> bool:
    """Le candidat est-il le mot ENTIER dont `vu` n'est qu'un morceau ?

    LE CAS RÉEL : « rs », fragment de « Usagers » vu par la lecture normale,
    bloquait le mot entier lu à 96 % par la passe distance-au-blanc — au seul
    motif qu'il était arrivé le premier. L'onglet ressortait donc en « rs ».

    ⚠ LA RÈGLE EST VOLONTAIREMENT ÉTROITE, et l'avoir élargie a été mesuré.
    Substituer dès que le candidat est « meilleur » (texte plus long, à défaut
    confiance plus haute) fait passer l'audit de **15 à 34 défauts** (page 1 :
    2 -> 17). Un mot venu d'une autre passe n'apporte pas que son texte : il
    apporte sa boîte, sa hauteur et sa clé de ligne, donc il déplace la
    baseline de toute sa rangée et disloque des blocs corrects ailleurs.

    On exige donc que le rival soit littéralement CONTENU dans le candidat.
    C'est ce qui distingue « je lis mieux le même mot » de « je lis autre
    chose au même endroit ». Coût mesuré ainsi : +1 défaut, pas +19.
    """
    tc = (cand.get("texte") or "").strip()
    tv = (vu.get("texte") or "").strip()
    return bool(tv) and len(tc) > len(tv) and tv.lower() in tc.lower()


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
        mots = _fusionner(mots, _mots_bruts(_pretraitee(img), langue,
                                            passe="b"))

    # DEUX PASSES DE COULEUR, toujours actives et SÉVÈREMENT filtrées. Elles
    # ne voient pas la même chose, et il faut les deux :
    #
    #   `_canal_couleur`        SATURATION — le vert soutenu de
    #                           « LA SIGNALISATION », absent de la lecture
    #                           normale, lu à 95 % ici ;
    #   `_canal_distance_blanc` DISTANCE AU BLANC — les tons PÂLES et
    #                           colorés (« Véhicule » jaune, « Usagers »
    #                           bleu clair) que la saturation seule
    #                           n'accroche pas non plus.
    #
    # Le filtre n'est pas une précaution de confort : sans lui, la première
    # ajoute 37 mots dont 35 sont du bruit. Voir `_CONF_MIN_COULEUR`.
    for canal, passe in ((_canal_couleur, "c"), (_canal_distance_blanc, "w")):
        vus = [m for m in _mots_bruts(canal(img), langue, passe=passe)
               if m["conf"] >= _CONF_MIN_COULEUR
               and len((m["texte"] or "").strip()) >= _CARS_MIN_COULEUR]
        if vus:
            mots = _fusionner(mots, vus)

    # APRÈS la fusion des passes : un mot coupé par Tesseract peut avoir ses
    # deux moitiés dans la MÊME passe (« Véhic » + « ule »), et il faut que
    # les deux soient présentes pour les reconnaître.
    mots = _recoller_mots_coupes(mots, echelle)

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
    # LE PLAFOND QUI MORD : la hauteur d'encre d'une boîte de LIGNE ne peut
    # pas dépasser ce que porte SA PROPRE LIGNE.
    #
    # Le défaut, mesuré page 1 sur le texte BARRÉ « Après deux ans de permis
    # B... » : la barre traverse la ligne, la mesure d'encre l'attrape et
    # remonte jusqu'à la ligne du dessus. Trois lignes de 8,4 / 9,0 / 9,9 pt
    # pour un interligne de 6,3 -> leurs boîtes se recouvrent, et les blocs
    # héritent du recouvrement (86,40 x 4,59 pt, mesure de l'audit).
    #
    # ⚠ PAR LIGNE, ET NON POUR LA PAGE. Un plafond global à l'interligne
    # écrase les TITRES, qui sont légitimement plus hauts que le corps :
    # mesuré page 2, **78 mots rabotés**, dont « LES AUTRES PERMIS » et
    # « SOMMAIRE » ramenés de 9,84 à 4,56 pt. Voir `_plafonds_par_ligne`.
    inter = _interligne(mots)
    plafonds = _plafonds_par_ligne(mots, inter)
    # ABAISSER À DEUX MOTS LE SEUIL DE `_boites_de_ligne` (quand la boîte
    # dépasse l'interligne) : ESSAYÉ, MESURÉ, REJETÉ — voir
    # `_paires_trop_hautes`, conservée et documentée mais NON APPELÉE.
    # Mesuré : page 1 passe de 4 à 6 défauts, total 17 -> 19.

    spans = []
    for i, m in enumerate(mots):
        encre = _boite_encre(gris, m["boite"], boite_de_ligne=(i in suspects),
                             hauteur_max=plafonds.get(m.get("ligne"), plafond))
        if encre is None:
            continue                     # aucune encre : lecture de bruit
        x0, y0, x1, y1 = (v / echelle for v in encre)
        if x1 <= x0 or y1 <= y0:
            continue
        # PLAFONNER **TOUS** LES MOTS À L'INTERLIGNE : ESSAYÉ, MESURÉ, REJETÉ.
        #
        # L'argument semblait imparable — un mot plus haut que l'interligne
        # empiète sur la ligne voisine par construction. Il est faux dans un
        # cas fréquent : une capitale suivie d'un jambage (« Jg », « À »)
        # occupe légitimement plus que l'interligne, et la raboter décale sa
        # baseline. Mesuré : 17 -> 20 défauts, et `bloc_dans_bloc` reparaît
        # (0 -> 2). Le plafond reste donc réservé aux boîtes de LIGNE, où l'on
        # sait que la hauteur est fausse.
        s = span_depuis_mot(m["texte"], (x0, y0, x1, y1),
                            confiance=m["conf"])
        s["_ligne_ocr"] = m.get("ligne")
        spans.append(s)

    # ⚠ ÉCARTER LES MORCEAUX DE DESSIN ICI : ESSAYÉ, MESURÉ, REJETÉ.
    # `_debris_de_dessin` est conservée et documentée mais NON APPELÉE — voir
    # sa docstring pour le détail de la mesure.
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
