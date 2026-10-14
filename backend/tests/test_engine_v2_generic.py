"""
GÉNÉRICITÉ du moteur v2 — preuve sur un document SYNTHÉTIQUE.

Les correctifs P10-P13 ont été trouvés sur mv21, le Handbook et la démo journal.
Rien ne prouve, à ce stade, qu'ils traitent la CLASSE du problème plutôt que ces
trois documents. Ce test fabrique donc de toutes pièces un PDF que le moteur n'a
jamais vu — autres polices, autres corps, autres couleurs, autres coordonnées —
mais qui rejoue les MÊMES STRUCTURES :

  P10  titre VERTICAL à lettres espacées (+ en-tête de tableau pivoté serré)
  P12  titre pleine colonne suivi d'un FILET DE SECTION (à ne pas confondre avec
       un soulignement) · et un VRAI soulignement (à ne pas perdre)
  P13  deux colonnes séparées par une gouttière PLUS ÉTROITE que les blancs de
       justification de leurs propres lignes · et une liste à PUCES (dont
       l'indentation ne doit pas passer pour une gouttière)
  P15  (page 2) colonne ÉTROITE JUSTIFIÉE dont les blancs de mots DÉPASSENT le
       seuil de coupe (le mot doit rester dans sa phrase) · et, en contre-épreuve
       sur les MÊMES lignes de base, une CITATION courte enjambée par le corps de
       texte voisin (qui, elle, doit rester séparée). Les deux structures sont
       indiscernables à l'échelle de la ligne : seul le fer aux DEUX bords du
       bloc les départage.

Aucun appel API : la traduction est simulée. Le test échoue si un correctif ne
tient que sur les documents d'origine.

    backend/venv/Scripts/python.exe backend/test_engine_v2_generic.py
"""

import os
import re
import sys

import fitz

import racine  # noqa: F401
from engines.pdf.engine import PDFObjectEngine        # noqa: E402
from engines.pdf import tagging                        # noqa: E402

# Volontairement DIFFÉRENT des documents de test : autre police, autres corps,
# autres couleurs, autres marges.
FONT = "tiro"                       # Times (mv21 = ProximaNova, hb = Avenir)
ENCRE_TITRE = (0.10, 0.20, 0.55)    # bleu nuit
ENCRE_FILET = (0.62, 0.62, 0.62)    # gris — DIFFÉRENT du titre (piège P12)
ENCRE_LIEN = (0.00, 0.45, 0.35)     # vert — le trait sera de la MÊME couleur
NOIR = (0.15, 0.15, 0.15)

W, H = 612, 792                     # Letter (les 3 docs sont en A4/A5)


def _w(txt, size, font=FONT):
    return fitz.get_text_length(txt, fontname=font, fontsize=size)


def build(path):
    doc = fitz.open()
    pg = doc.new_page(width=W, height=H)

    # ── P12 : titre PLEINE COLONNE + filet de section (couleur différente) ───
    titre = "Regional Output Exceeds Every Published Forecast This Quarter"
    pg.insert_text((45, 70), titre, fontname=FONT, fontsize=15.5,
                   color=ENCRE_TITRE)
    # Le filet court sur TOUTE la colonne — donc de largeur ~= celle du titre :
    # le seul critère de largeur est aveugle ici (c'est le piège).
    for y in (40, 78, 700, 726, 752):          # famille de filets identiques
        pg.draw_line(fitz.Point(45, y), fitz.Point(567, y),
                     color=ENCRE_FILET, width=0.6)

    # ── P12 : VRAI soulignement (trait de la MÊME encre que son texte) ───────
    lien = "reports.example.org/quarterly"
    pg.insert_text((45, 105), lien, fontname=FONT, fontsize=10, color=ENCRE_LIEN)
    pg.draw_line(fitz.Point(45, 107.2),
                 fitz.Point(45 + _w(lien, 10), 107.2),
                 color=ENCRE_LIEN, width=0.5)

    # ── P13 : deux colonnes, gouttière PLUS ÉTROITE que les blancs de mots ───
    # Corps 9 ; on place chaque mot À LA MAIN pour imposer la géométrie :
    #   • blancs de mots (justification lâche) : 1.9 × largeur de glyphe
    #   • gouttière entre colonnes             : 2.3 × largeur de glyphe
    # -> la gouttière est PLUS ÉTROITE que le seuil de coupe (2.5) ET plus
    #    étroite que certains blancs de mots : seul le corridor peut trancher.
    size = 9
    gw = _w("n", size)                       # largeur de glyphe de référence
    mot_gap = 1.9 * gw
    gouttiere = 2.3 * gw
    colA = [
        "Regional plants raised output", "again during the period as",
        "demand held firm across the", "network of supply partners",
        "and logistics operators who", "handle the bulk of transit",
        "volumes between the ports.",
    ]
    colB = [
        "Managers credited steady", "hiring and a wider base of",
        "contracts for the result and", "expect the trend to hold as",
        "new capacity arrives at the", "northern sites during the",
        "second half of the year.",
    ]
    x_a = 45
    largeur_a = max(_w(l.replace(" ", ""), size)
                    + mot_gap * l.count(" ") for l in colA)
    x_b = x_a + largeur_a + gouttiere
    for i, (la, lb) in enumerate(zip(colA, colB)):
        y = 150 + i * 13
        for x0, ligne in ((x_a, la), (x_b, lb)):
            x = x0
            for mot in ligne.split(" "):
                pg.insert_text((x, y), mot, fontname=FONT, fontsize=size,
                               color=NOIR)
                x += _w(mot, size) + mot_gap

    # ── P13 : liste à PUCES (l'indentation NE DOIT PAS passer pour une gouttière)
    for i, item in enumerate([
            "Capacity was added at three northern sites during the period",
            "Hiring continued across the logistics and transit divisions",
            "Contract renewals covered the majority of regional partners",
            "Inventory levels returned to their long term seasonal average",
            "Maintenance windows were shortened without affecting output",
            "Freight costs eased slightly after the new routes opened up"]):
        y = 275 + i * 15
        pg.insert_text((45, y), "•", fontname=FONT, fontsize=10, color=NOIR)
        pg.insert_text((63, y), item, fontname=FONT, fontsize=10, color=NOIR)

    # ── P14 : bloc FERRÉ À DROITE (adresse / date d'un courrier) ─────────────
    # Bord droit à fleur, bord gauche franchement déchiqueté.
    droite = ["Northern Operations Office", "1420 Harbour Road, Suite 7",
              "Portsmouth, PO1 3AX", "14 November"]
    x_fer = 545
    for i, l in enumerate(droite):
        pg.insert_text((x_fer - _w(l, 9.5), 400 + i * 12), l, fontname=FONT,
                       fontsize=9.5, color=NOIR)

    # ── P14 : bloc CENTRÉ multi-lignes (exergue) ────────────────────────────
    centre = ["Output held firm through the quarter",
              "across every regional site",
              "without additional capacity"]
    cx = 180
    for i, l in enumerate(centre):
        pg.insert_text((cx - _w(l, 10) / 2, 400 + i * 13), l, fontname=FONT,
                       fontsize=10, color=NOIR)

    # ── P10 : titre VERTICAL à lettres espacées (marge gauche) ───────────────
    mot = "APPENDIX SECTION"
    y = 640                                   # part du bas, monte (dir = 0,-1)
    pas = 13.0                                # lettres ESPACÉES
    for ch in mot:
        if ch != " ":
            pg.insert_text((30, y), ch, fontname=FONT, fontsize=11,
                           color=ENCRE_TITRE, rotate=90)
        y -= pas

    # ── P10 : en-tête de tableau PIVOTÉ, dans une boîte SERRÉE ───────────────
    for k, lab in enumerate(["Region", "Output", "Change"]):
        x = 330 + k * 60
        pg.draw_rect(fitz.Rect(x - 6, 560, x + 12, 650), color=ENCRE_FILET,
                     width=0.5)
        pg.insert_text((x, 646), lab, fontname=FONT, fontsize=9, color=NOIR,
                       rotate=90)

    _build_p15(doc.new_page(width=W, height=H))
    _build_p16(doc.new_page(width=W, height=H))
    _build_p17(doc.new_page(width=W, height=H))
    _build_p18(doc.new_page(width=W, height=H))
    _build_p19(doc.new_page(width=W, height=H))
    _build_p20(doc.new_page(width=W, height=H))
    _build_p22(doc.new_page(width=W, height=H))
    _build_p24(doc.new_page(width=W, height=H))
    _build_p25(doc.new_page(width=W, height=H))

    doc.save(path)
    doc.close()
    return path


def _justifie(pg, x, y, mots, size, largeur, color=NOIR):
    """Pose `mots` en JUSTIFIÉ : le blanc est ce qu'il faut pour que la ligne
    tombe pile sur `x + largeur` — exactement ce que fait un fondeur. En colonne
    étroite, ce blanc enfle jusqu'à dépasser le seuil de coupe du moteur.
    Retourne la liste des blancs posés."""
    nat = sum(_w(m, size) for m in mots)
    blanc = (largeur - nat) / max(len(mots) - 1, 1)
    cx = x
    for m in mots:
        pg.insert_text((cx, y), m, fontname=FONT, fontsize=size, color=color)
        cx += _w(m, size) + blanc
    return blanc


def _pave(pg, x, y0, texte, size, largeur, interligne, color=NOIR):
    """Compose `texte` en JUSTIFIÉ dans une colonne de `largeur`, comme le ferait
    un fondeur : coupe les lignes au plus juste, puis étire les blancs de chaque
    ligne (sauf la dernière, laissée au blanc naturel) pour tomber au fer des
    deux côtés.

    On ne pose donc AUCUN blanc à la main : les lignes lâches apparaissent
    d'elles-mêmes, là où la coupe laisse peu de mots — c'est exactement le
    mécanisme qui fabrique le défaut dans un vrai document. Retourne les lignes.
    """
    esp = _w(" ", size)
    lignes, cur = [], []
    for mot in texte.split():
        essai = cur + [mot]
        if cur and (sum(_w(m, size) for m in essai)
                    + esp * (len(essai) - 1)) > largeur:
            lignes.append(cur)
            cur = [mot]
        else:
            cur = essai
    if cur:
        lignes.append(cur)
    for i, mots in enumerate(lignes):
        y = y0 + i * interligne
        if i == len(lignes) - 1:                # dernière ligne : pas d'étirement
            cx = x
            for m in mots:
                pg.insert_text((cx, y), m, fontname=FONT, fontsize=size,
                               color=color)
                cx += _w(m, size) + esp
        else:
            _justifie(pg, x, y, mots, size, largeur, color)
    return lignes


MILIEU_X, MILIEU_W = 160, 100          # colonne du milieu, page 3 (P16)


def _build_p16(pg):
    """Page 3 : le corridor FANTÔME nourri par le VIDE.

    Structure d'un journal : trois colonnes justifiées étroites, celle du MILIEU
    plus COURTE que ses voisines (comme une brève à côté d'articles longs), et
    des interlignes DIFFÉRENTS (10,2 / 9,4) — de sorte que les lignes de base
    des trois colonnes dérivent les unes par rapport aux autres, exactement
    comme dans la démo journal.

    Sous la colonne du milieu s'ouvre une vaste ZONE VIDE, bordée au loin par
    les deux colonnes hautes. À l'ancienne, chaque rangée de cette zone
    « confirmait » n'importe quel corridor tombant dans son immense blanc : il
    suffisait alors que deux blancs de justification s'alignent par hasard dans
    la colonne du milieu pour qu'un corridor FANTÔME atteigne le quorum et
    vienne couper le texte. Le vide n'est pas une preuve.
    """
    long_txt = ("Regional plants raised output again during the period as demand "
                "held firm across the network of supply partners and logistics "
                "operators who handle the bulk of transit volumes between the "
                "northern ports and the inland terminals throughout the season.")
    court = ("Software providers also reshaped how regional distribution "
             "partners handle considerable volumes.")
    _pave(pg, 45, 110, long_txt, 9, 100, 10.2)              # colonne haute
    _pave(pg, MILIEU_X, 110, court, 9, MILIEU_W, 9.4)       # colonne COURTE
    _pave(pg, 275, 110, long_txt, 9, 100, 10.2)             # colonne haute


P17_X, P17_W, P17_PAS, P17_Y = 45, 120, 134, 150     # 4 colonnes, page 4


def _build_p17(pg):
    """Page 4 : le CHAPÔ pleine largeur ne doit pas souder les colonnes.

    Mise en pages de une : un chapô JUSTIFIÉ courant sur toute la largeur
    (45 → 567), puis quatre colonnes étroites justifiées dont les fers extrêmes
    tombent EXACTEMENT sur les siens (45 et 567) — c'est la règle typographique,
    et c'est aussi celle de la démo journal.

    Le piège : les lignes du chapô attestent, à elles seules, une « colonne »
    allant de 45 à 567. Les rangées des quatre colonnes, elles, commencent bien
    à 45 et finissent bien à 567. Rien, à ce stade, ne les distingue d'une ligne
    justifiée trouée de gros blancs — et le recollage les soudait en charabia
    (« Regional plants raised output Regional plants raised output… »). Ce qui
    les sauve : chaque fragment remplit exactement une colonne avérée PLUS
    ÉTROITE, dont il est une ligne à part entière.
    """
    chapo = ("Markets rallied hard this quarter as every major exporter reported "
             "earnings comfortably ahead of the published forecasts, and analysts "
             "now expect the trend to hold well into the coming year across every "
             "region we track, with the northern corridors leading the advance and "
             "the southern terminals following close behind them through the whole "
             "of the period under review by the committee that published the data.")
    corps = ("Regional plants raised output again during the period as demand held "
             "firm across the network of supply partners and logistics operators "
             "who handle the bulk of transit volumes between the northern ports "
             "and the terminals throughout the season and well beyond it later.")
    _pave(pg, P17_X, 90, chapo, 11, 522, 14)
    for k in range(4):
        _pave(pg, P17_X + k * P17_PAS, P17_Y, corps, 9, P17_W, 10.2)


# P18 — photo DÉCENTRÉE sur la page et sa légende (page 5).
P18_PHOTO = (60, 300)                  # empan x de la photo
P18_LEG_Y = 300                        # ligne de base de la légende


def _build_p18(pg):
    """Page 5 : une légende centrée sous une photo DÉCENTRÉE dans la page.

    Le piège est le bandeau : un TITRE pleine largeur, posé loin au-dessus,
    chevauche la légende et commence à sa gauche. La « colonne » de la légende,
    déduite des paragraphes qui la chevauchent, devient donc la PAGE ENTIÈRE —
    et une légende centrée sur sa photo n'y paraît plus centrée du tout (blancs
    très inégaux). C'est le cas de la démo journal.

    La photo est délibérément DÉCENTRÉE dans la page : une légende centrée sur
    la page passerait le test par hasard, comme « Trading floor » le faisait.
    Ici l'axe de la photo (180) et celui de la page (306) sont bien distincts.
    """
    # Le titre pleine largeur qui empoisonne l'estimation de colonne.
    pg.insert_text((45, 70), "Regional Output Beats Every Published Forecast",
                   fontname=FONT, fontsize=15.5, color=ENCRE_TITRE)
    # La photo, décentrée, et sa légende centrée SUR ELLE.
    x0, x1 = P18_PHOTO
    pg.draw_rect(fitz.Rect(x0, 200, x1, 292), color=ENCRE_FILET,
                 fill=(0.88, 0.88, 0.88), width=0.5)
    leg = "Northern terminals during the quarter"
    pg.insert_text(((x0 + x1) / 2 - _w(leg, 7) / 2, P18_LEG_Y), leg,
                   fontname=FONT, fontsize=7, color=NOIR)


# P19 — CONTRE-ÉPREUVE de P18 : sous une photo, ce n'est PAS toujours une
# légende. Le cadre-photo ne connaît aucun test « est-ce une légende ? » : il se
# déclenche sur tout paragraphe collé sous un bloc contenant. Il faut donc
# prouver qu'il ne déforme pas le TEXTE COURANT qui reprend sous une photo.
P19_GCOL = (60, 300)                   # colonne gauche : la photo en fait la largeur
P19_DCOL = (340, 560)                  # colonne droite : la photo y est plus ÉTROITE
P19_DPHOTO = (340, 460)


def _build_p19(pg):
    """Page 6 : du TEXTE COURANT — pas une légende — reprend sous une photo.

    C'est le cas que le cadre-photo pourrait confondre : rien ne distingue
    formellement une légende d'un paragraphe qui continue sous une illustration.
    La page est EMPOISONNÉE comme la démo (titre pleine largeur qui donne la PAGE
    pour colonne), sans quoi le cadre-photo ne se déclencherait pas du tout et le
    test passerait à vide.

    Deux colonnes, deux pièges différents :

      • GAUCHE — la photo fait EXACTEMENT la largeur de la colonne (cas normal :
        une illustration se cale sur sa colonne). Le cadre-photo se déclenche sur
        le corps qui reprend dessous. Il doit alors retrouver la VRAIE colonne :
        le texte reste au fer à gauche, son conteneur est celui de la colonne.
        Le cadre-photo ne doit rien inventer.

      • DROITE — la photo est plus ÉTROITE que sa colonne. Si le cadre-photo
        s'appliquait, il ÉCRASERAIT le corps à la largeur de la photo (460 au
        lieu de 560) et le ferait déborder à la traduction. Il ne doit pas
        s'appliquer : le corps n'est pas contenu par la photo.
    """
    # Le poison : titre pleine largeur, loin au-dessus, qui chevauche tout.
    pg.insert_text((45, 70), "Quarterly Freight Volumes Across Every Corridor",
                   fontname=FONT, fontsize=15.5, color=ENCRE_TITRE)

    corps = ("Freight volumes across the northern corridor rose again this "
             "quarter as operators added capacity on the busiest routes and "
             "shippers brought forward orders ahead of the seasonal peak. "
             "Terminal handlers reported steadier turnaround times despite "
             "the heavier flow of containers moving inland.")

    # ── GAUCHE : photo À LA LARGEUR de la colonne, corps collé dessous ───────
    gx0, gx1 = P19_GCOL
    pg.draw_rect(fitz.Rect(gx0, 120, gx1, 220), color=ENCRE_FILET,
                 fill=(0.88, 0.88, 0.88), width=0.5)
    # 232 - 220 = 12 pt < 1.5 x 9 : le corps est COLLÉ -> le cadre-photo se
    # déclenche. C'est bien le cas adverse qu'on veut éprouver.
    _pave(pg, gx0, 232, corps, 9, gx1 - gx0, 11.0)

    # ── DROITE : photo PLUS ÉTROITE que la colonne, corps collé dessous ──────
    dx0, dx1 = P19_DCOL
    px0, px1 = P19_DPHOTO
    pg.draw_rect(fitz.Rect(px0, 120, px1, 220), color=ENCRE_FILET,
                 fill=(0.88, 0.88, 0.88), width=0.5)
    _pave(pg, dx0, 232, corps, 9, dx1 - dx0, 11.0)


# P20 — liste numérotée à ALINÉA NÉGATIF dans une colonne étroite.
P20_X, P20_W = 60, 150                 # colonne de la liste
P20_IND = 9.0                          # alinéa négatif (marqueur -> texte)
P20_CX = 340                           # colonne de la CONTRE-ÉPREUVE
P20_SZ, P20_IL = 8, 10.0


def _item(pg, marqueur, texte, y, x=P20_X, largeur=P20_W):
    """Item de liste à ALINÉA NÉGATIF : le marqueur seul à `x`, le texte et
    toutes ses continuations à `x + P20_IND` — la géométrie de tout manuel.
    Retourne le y de la ligne suivante."""
    pg.insert_text((x, y), marqueur, fontname=FONT, fontsize=P20_SZ, color=NOIR)
    esp = _w(" ", P20_SZ)
    lg = largeur - P20_IND
    lignes, cur = [], []
    for mot in texte.split():
        essai = cur + [mot]
        if cur and (sum(_w(m, P20_SZ) for m in essai)
                    + esp * (len(essai) - 1)) > lg:
            lignes.append(cur)
            cur = [mot]
        else:
            cur = essai
    if cur:
        lignes.append(cur)
    for i, mots in enumerate(lignes):
        pg.insert_text((x + P20_IND, y + i * P20_IL), " ".join(mots),
                       fontname=FONT, fontsize=P20_SZ, color=NOIR)
    return y + len(lignes) * P20_IL


def _build_p20(pg):
    """Page 7 : une liste numérotée que l'ORTHOGRAPHE ne sait pas découper.

    Le moteur coupait déjà sur « 1. », mais seulement si la ligne précédente
    FINISSAIT UNE PHRASE. Deux tournures très banales désarment ce garde-fou, et
    elles sont toutes deux ici :

      • l'intro finit par « : » — qui ne finit pas une phrase ;
      • l'item 5 finit par « or » — mot non terminal, donc « continuation
        certaine ».

    Résultat avant correctif (mesuré sur mv21 p12) : « …of your: 1. Parent » et
    « 5. …; or 6. Anyone who… » soudés en prose. Le vrai signal est GÉOMÉTRIQUE :
    six marqueurs au MÊME bord gauche, aux rangs qui se SUIVENT.

    CONTRE-ÉPREUVE à droite : deux lignes numérotées au même bord gauche mais de
    rangs NON ADJACENTS (1. puis 7.). Ce n'est pas une liste — rien ne prouve
    qu'elles s'enchaînent. Elles ne doivent PAS être marquées, sinon la règle
    couperait n'importe quel nombre en tête de ligne.
    """
    y = 100
    lignes = _pave(pg, P20_X, y, "You must drive only under the immediate "
                                 "supervision of your:", P20_SZ, P20_W, P20_IL)
    y += len(lignes) * P20_IL + 4
    y = _item(pg, "1.", "Parent", y)
    y = _item(pg, "2.", "Guardian", y)
    y = _item(pg, "3.", "Person in loco parentis", y)
    y = _item(pg, "4.", "Driver Education Teacher", y)
    # Item 5 : finit par « or », mot NON TERMINAL -> désarme l'ancienne règle.
    y = _item(pg, "5.", "Driving School Instructor; or", y)
    y = _item(pg, "6.", "Anyone who has been designated in writing by the "
                        "parent, guardian, or person in loco parentis.", y)

    # ── CONTRE-ÉPREUVE : rangs NON ADJACENTS, même bord gauche ──────────────
    yc = 100
    _pave(pg, P20_CX, yc, "The fee schedule below applies to every renewal "
                          "filed after the cutoff:", P20_SZ, P20_W, P20_IL)
    yc += 3 * P20_IL + 4
    _item(pg, "1.", "Standard renewal", yc, x=P20_CX)
    _item(pg, "7.", "Late renewal surcharge", yc + P20_IL, x=P20_CX)


def _rangees(eng, page):
    """Rangées (lignes de base) d'une page, telles que le moteur les construit —
    on passe par SES méthodes, pour que le test ne puisse pas diverger de lui."""
    spans = []
    for bl in page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]:
        if bl.get("type") != 0:
            continue
        for ln in bl.get("lines", []):
            for sp in ln.get("spans", []):
                if not sp["text"].strip():
                    continue
                bb, o = sp["bbox"], sp.get("origin", (sp["bbox"][0], sp["bbox"][3]))
                spans.append({
                    "bbox": list(bb), "text": sp["text"],
                    "size": sp.get("size", 0) or 0, "dir": list(ln.get("dir", (1, 0))),
                    "_gw": (bb[2] - bb[0]) / max(1, len(sp["text"].strip())),
                    "_base": o[1]})
    return eng._baseline_rows([s for s in spans if abs(s["dir"][1]) <= 0.01])


def _blancs(mots, size, largeur, x):
    """Abscisses des blancs d'une ligne justifiée, et leur largeur."""
    nat = sum(_w(m, size) for m in mots)
    blanc = (largeur - nat) / max(len(mots) - 1, 1)
    trous, cx = [], x
    for m in mots[:-1]:
        cx += _w(m, size)
        trous.append(cx + blanc / 2)
        cx += blanc
    return trous, blanc


def _build_p15(pg):
    """Page 2 : le piège de la colonne étroite justifiée, ET sa contre-épreuve."""
    # ── P15a : colonne ÉTROITE justifiée ────────────────────────────────────
    # Colonne de 104 pt au corps 9 : quelques mots longs suffisent à ce que
    # certaines lignes n'en portent que trois, avec des blancs bien au-delà du
    # seuil de coupe (2,5 × la largeur de glyphe).
    _pave(pg, 45, 120,
          "Software providers reshaped how regional distribution partners "
          "handle the considerable volumes moving between the northern ports "
          "and the inland terminals throughout every season.",
          9, 104, 13)

    # ── P15b : CITATION étroite ENJAMBÉE par le CORPS voisin ────────────────
    # Contre-épreuve du Handbook p20, et le vrai juge de ce correctif. Les deux
    # blocs sont des colonnes justifiées étroites à réparer, et leurs lignes de
    # base sont DÉCALÉES de 1 pt : le moteur les regroupe donc dans les MÊMES
    # rangées. Sur une seule rangée coexistent ainsi les fragments de DEUX
    # colonnes — le moteur doit recoller chacune CHEZ ELLE. Un veto fondé sur la
    # seule absence de corridor vertical soudait ici les deux blocs.
    # Chaque bloc mélange, comme un vrai texte justifié, une MAJORITÉ de lignes
    # serrées (les témoins) et quelques lignes lâches (les lignes à réparer) :
    # c'est ce mélange, et non une géométrie uniformément extrême, qui reproduit
    # la classe du défaut.
    _pave(pg, 60, 320,
          "The story we tell about our own past remains entirely ours to "
          "rewrite, and the accompanying documentation inevitably changes "
          "alongside it every single time.",
          11, 150, 16)
    _pave(pg, 300, 321,
          "Freight costs eased after the new routes opened and the yards were "
          "cleared well before the quarter closed, with transhipment throughput "
          "comfortably exceeding every projection.",
          10, 160, 16)

    # ── P15c : l'ENCRE a le dernier mot ─────────────────────────────────────
    # Même géométrie que P15a — une colonne justifiée avec une ligne lâche —
    # mais un FILET VERTICAL (mur de cellule, cadre) traverse le gros blanc. La
    # géométrie seule conclurait au recollage ; l'encre l'interdit. Sans ce
    # garde-fou, un tableau dont les cellules remplissent bien leurs rangées
    # verrait ses colonnes fusionner.
    txt_c = ("The northern yards took in every extra ton we sent them last "
             "year without any measurable disruption whatsoever to the "
             "sailing plan.")
    lignes_c = _pave(pg, 60, 450, txt_c, 11, 150, 16)
    for i, mots in enumerate(lignes_c[:-1]):
        trous, blanc = _blancs(mots, 11, 150, 60)
        if blanc > 2.5 * _w("n", 11):          # la ligne lâche : on y plante le filet
            y = 450 + i * 16
            pg.draw_line(fitz.Point(trous[0], y - 11), fitz.Point(trous[0], y + 4),
                         color=NOIR, width=0.8)
            break


# ═════════════════════════════════════════════════════════════════════════════
# Page 8 (P22) — LE BLANC N'A PAS DE BORD (mais reste un PONT).
# mv21 p22 : les cellules commencent par des ESPACES dont le bbox s'étend
# jusqu'au mur — le rendu posait l'encre traduite SUR le filet (« Infraction »
# au « I » mangé). Le remède naïf (raboter le bbox) casse l'inverse : les blancs
# sont le TISSU qui relie les spans (« F O R E W O R D  B Y » éclatait en 3).
# La règle : bbox complet pour la SEGMENTATION, encre pour FERS et RENDU.
P22_MUR = 100.0          # abscisse du filet vertical de la cellule
P22_TXT = "  Safety restraint violation was recorded"


def _build_p22(pg):
    pg.draw_rect(fitz.Rect(P22_MUR, 104, 360, 132), color=NOIR, width=0.7)
    # Les DEUX espaces de tête sont posés PILE sur le mur : leur bbox le touche,
    # l'encre du « S » commence deux chasses plus loin.
    pg.insert_text((P22_MUR + 0.9, 122), P22_TXT, fontname=FONT, fontsize=10,
                   color=NOIR)


# Page 9 (P24) — RALLONGE DE DÉTRESSE : le blanc réel avant la compression.
# mv21 p20 : un fragment rendu à 4,8 pt (0,69×) dans un corps de 7 — la
# croissance ordinaire retient 30 % du blanc et se borne à 2,5 interlignes,
# des retenues esthétiques qui n'ont aucun sens quand l'alternative est un
# texte illisible. Règle : sous le plancher de lisibilité (0,88), TOUT le blanc
# réel est accordé d'abord ; la compression profonde ne reste que pour les
# blocs réellement coincés (garantie anti-collision).
P24_TXT_A = "Winter schedules resume next week across the county lines"
P24_TXT_B = "Summer schedules resume next month across the county lines"
P24_VOISIN_B_Y = 133.0


def _build_p24(pg):
    # A : une ligne avec un GRAND blanc réel dessous (prochain bloc à ~110 pt).
    pg.insert_text((45, 120), P24_TXT_A, fontname=FONT, fontsize=10, color=NOIR)
    pg.insert_text((45, 232), "Farther section resumes here after the gap",
                   fontname=FONT, fontsize=10, color=NOIR)
    # B : la même ligne, COINCÉE (un voisin immédiatement dessous). Corps
    # DIFFÉRENT (8 pt) pour que l'Étape A ne fusionne pas les deux blocs — le
    # piège veut un voisin distinct, pas une continuation.
    pg.insert_text((330, 120), P24_TXT_B, fontname=FONT, fontsize=10, color=NOIR)
    pg.insert_text((330, P24_VOISIN_B_Y),
                   "Immediate neighbour paragraph sits right here below",
                   fontname=FONT, fontsize=8, color=NOIR)


# Page 10 (P25) — SATELLITES : exposants et indices restent avec leur mot.
# mv21 p58 : « re » de « 1re » peint deux fois ; hb p239 : indice « 1 » orphelin
# en bord de ligne et « 2 » percutant le « ? ». Un run à corps réduit, baseline
# décalée et collé à un voisin plus grand est un SATELLITE : il rejoint la
# rangée de son hôte (extraction), voyage avec lui (reflow : chaîne soudée) et
# se repeint à sa hauteur (`rise`).
P25_Y = 120.0
P25_Y2 = 150.0


def _build_p25(pg):
    x = 45.0
    for txt, size, dy in (("Values L", 11, 0), ("1", 7, 3.2),
                          (" and L", 11, 0), ("2", 7, 3.2),
                          (" apply here", 11, 0)):
        pg.insert_text((x, P25_Y + dy), txt, fontname=FONT, fontsize=size,
                       color=NOIR)
        x += _w(txt, size)
    x = 45.0
    for txt, size, dy in (("1", 10, 0), ("re", 6.5, -3.0),
                          (" infraction observed there", 10, 0)):
        pg.insert_text((x, P25_Y2 + dy), txt, fontname=FONT, fontsize=size,
                       color=NOIR)
        x += _w(txt, size)


def _p27_page():
    """Page synthétique écrite en opérateurs PDF bruts (q / W n / cm / Do)."""
    doc = fitz.open()
    pg = doc.new_page(width=300, height=320)

    def _png(rgb):
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), 0)
        pix.set_rect(pix.irect, rgb)
        return pix.tobytes("png")

    pg.insert_image(fitz.Rect(0, 0, 1, 1), stream=_png((230, 20, 20)))
    pg.insert_image(fitz.Rect(0, 0, 1, 1), stream=_png((20, 160, 20)))
    noms = [im[7] for im in pg.get_images(full=True)]
    rouge, vert = noms[0], noms[1]

    def _chemin(trace):
        sh = pg.new_shape()
        trace(sh)
        return sh.draw_cont

    cercle = _chemin(lambda sh: sh.draw_circle((70, 70), 50))
    triangle = _chemin(lambda sh: sh.draw_polyline(
        [(150, 20), (280, 20), (150, 120)]))
    disque = _chemin(lambda sh: sh.draw_circle((150, 280), 30))
    H = 320
    ops = (
        # A. image rouge [20,20,120,120] découpée en DISQUE.
        f"q {cercle} h W n 100 0 0 100 20 {H - 120} cm /{rouge} Do Q\n"
        # B. rectangle navy [150,20,280,120] découpé en TRIANGLE.
        f"q {triangle} h W n 0.1 0.2 0.4 rg 150 {H - 120} 130 100 re f Q\n"
        # C. image verte [20,140,120,190], PUIS un fond bleu qui la recouvre.
        f"q 100 0 0 50 20 {H - 190} cm /{vert} Do Q\n"
        f"q 0 0 1 rg 70 {H - 190} 100 50 re f Q\n"
        # D. deux images À LA SUITE, chacune sous SA découpe rectangulaire
        # (aucun tracé peint entre elles) : la seconde ne doit pas hériter de
        # la découpe de la première.
        f"q 180 {H - 195} 50 60 re W n 50 0 0 60 180 {H - 195} cm /{vert} Do Q\n"
        f"q 235 {H - 195} 50 60 re W n 50 0 0 60 235 {H - 195} cm /{rouge} Do Q\n"
        # E. image rouge [20,210,80,260] SANS découpe, puis un rectangle vert
        # sous une découpe qui couvre presque toute l'image : cette découpe
        # n'est pas la sienne.
        f"q 60 0 0 50 20 {H - 260} cm /{rouge} Do Q\n"
        f"q 25 {H - 260} 75 50 re W n 0 1 0 rg 90 {H - 260} 10 50 re f Q\n"
        # F. photo ronde : DISQUE, fond peint, puis l'image sous la MÊME
        # découpe (le fond ne referme pas la découpe).
        f"q {disque} h W n 0 0 1 rg 120 {H - 310} 60 60 re f "
        f"60 0 0 60 120 {H - 310} cm /{rouge} Do Q\n"
        # G. image verte sans découpe PUIS image rouge découpée, sans tracé
        # peint entre elles : la découpe ne glisse pas d'un rang.
        f"q 48 0 0 40 190 {H - 260} cm /{vert} Do Q\n"
        f"q 250 {H - 260} 40 40 re W n 50 0 0 40 240 {H - 260} cm /{rouge} Do Q\n"
    )
    doc.update_stream(pg.get_contents()[0], ops.encode("latin-1"))
    for x in pg.get_contents()[1:]:
        doc.update_stream(x, b"")
    return doc


def _p27_clips_et_ordre(eng):
    src = _p27_page()
    pd = eng.extract_page_data(src, 0, src[0], embed_images=True)
    out = fitz.open()
    eng.render_page_into(out, pd, draw_borders=False)

    def _px(doc, x, y):
        pix = doc[0].get_pixmap(dpi=72, clip=fitz.Rect(x, y, x + 1, y + 1))
        return tuple(pix.pixel(0, 0))

    def _proche(c, ref, tol=40):
        return all(abs(a - b) <= tol for a, b in zip(c, ref))

    BLANC, ROUGE, NAVY, BLEU, VERT = ((255, 255, 255), (230, 20, 20),
                                      (26, 51, 102), (0, 0, 255), (20, 160, 20))
    res = []
    arme = (_proche(_px(src, 24, 24), BLANC) and _proche(_px(src, 270, 110), BLANC)
            and _proche(_px(src, 95, 165), BLEU)
            and _proche(_px(src, 22, 235), ROUGE)
            and _proche(_px(src, 122, 252), BLANC)
            and _proche(_px(src, 192, 240), VERT)
            and _proche(_px(src, 242, 240), BLANC))
    res.append(("P27  piège armé (l'original découpe et recouvre)", arme, ""))
    coin, centre = _px(out, 24, 24), _px(out, 70, 70)
    res.append(("P27  photo découpée en DISQUE : le coin du carré reste vide",
                _proche(coin, BLANC) and _proche(centre, ROUGE),
                f"coin={coin} centre={centre}"))
    hors, dans = _px(out, 270, 110), _px(out, 160, 30)
    res.append(("P27  bandeau découpé en TRIANGLE : rien hors du biais",
                _proche(hors, BLANC) and _proche(dans, NAVY),
                f"hors={hors} dans={dans}"))
    dessus = _px(out, 95, 165)
    res.append(("P27  ordre de peinture : le fond peint APRÈS l'image la recouvre",
                _proche(dessus, BLEU), f"pixel={dessus}"))
    seconde = _px(out, 260, 170)
    res.append(("P27  deux images découpées à la suite : la seconde reste visible",
                _proche(seconde, ROUGE), f"pixel={seconde}"))
    bord = _px(out, 22, 235)
    res.append(("P27  image sans découpe : celle de l'objet suivant ne la rogne pas",
                _proche(bord, ROUGE), f"pixel={bord}"))
    coin, centre = _px(out, 122, 252), _px(out, 150, 280)
    res.append(("P27  photo ronde sous fond peint : le disque tient malgré le fond",
                _proche(coin, BLANC) and _proche(centre, ROUGE),
                f"coin={coin} centre={centre}"))
    libre, rognee = _px(out, 192, 240), _px(out, 242, 240)
    res.append(("P27  image libre puis image découpée : chacune sa découpe",
                _proche(libre, VERT) and _proche(rognee, BLANC),
                f"libre={libre} rognee={rognee}"))

    # Page PIVOTÉE : les découpes se relèvent dans l'espace non pivoté, comme
    # les bbox d'images. Sans dé-rotation, elles tombaient à côté de l'image.
    def _clips(doc):
        d = eng.extract_page_data(doc, 0, doc[0], embed_images=True)
        return [repr(e.get("clips")) for e in d["elements"]
                if e.get("type") == "image"]
    droit = _clips(src)
    pivots = {}
    for rot in (90, 180, 270):
        tourne = _p27_page()
        tourne[0].set_rotation(rot)
        pivots[rot] = _clips(tourne) == droit
    res.append(("P27  page pivotée (90/180/270) : mêmes découpes qu'à plat",
                all(pivots.values()) and any(c != "None" for c in droit),
                str(pivots)))
    return res


def run():
    scratch = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "_generic_tmp")
    os.makedirs(scratch, exist_ok=True)
    pdf = build(os.path.join(scratch, "synthetique.pdf"))

    eng = PDFObjectEngine()
    doc = fitz.open(pdf)
    pd = eng.extract_page_data(doc, 0, doc[0], embed_images=True)
    paras = [e for e in pd["elements"] if e.get("type") == "paragraph"]
    draws = [e for e in pd["elements"] if e.get("type") == "drawing"]

    checks = []

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    # ── P12 ─────────────────────────────────────────────────────────────────
    soulignes = [r for p in paras for ln in p.get("lines", [])
                 for r in ln.get("runs", []) if r.get("underline")]
    txt_soulignes = " ".join(r["text"] for r in soulignes)
    ok("P12  le FILET de section n'est pas pris pour un soulignement",
       "Regional Output Exceeds" not in txt_soulignes,
       f"runs soulignés = {txt_soulignes[:60]!r}")
    ok("P12  le VRAI soulignement (même encre) est conservé",
       "reports.example.org" in txt_soulignes,
       f"runs soulignés = {txt_soulignes[:60]!r}")
    consommes = [d for d in draws if d.get("_underline_consumed")]
    ok("P12  un seul trait consommé (celui du lien)",
       len(consommes) == 1, f"{len(consommes)} trait(s) consommé(s)")

    # ── P13 : les deux colonnes ─────────────────────────────────────────────
    def _txt(p):
        return (p.get("text") or "")
    melanges = [p for p in paras
                if "Regional plants" in _txt(p) and "Managers credited" in _txt(p)]
    ok("P13  les 2 colonnes ne sont PAS entrelacées",
       not melanges,
       "un paragraphe contient les deux colonnes" if melanges else "")
    colA = [p for p in paras if "Regional plants raised" in _txt(p)]
    colB = [p for p in paras if "Managers credited steady" in _txt(p)]
    ok("P13  colonne GAUCHE reconstituée d'un bloc",
       colA and "ports" in _txt(colA[0]),
       _txt(colA[0])[:70] if colA else "(absente)")
    ok("P13  colonne DROITE reconstituée d'un bloc",
       colB and "second half" in _txt(colB[0]),
       _txt(colB[0])[:70] if colB else "(absente)")

    # ── P13 : les puces ─────────────────────────────────────────────────────
    # Contrôle GÉOMÉTRIQUE (et non textuel : la base-14 Times rend « • » en
    # « · »). Un item de liste doit démarrer à l'abscisse de SA PUCE (45), pas à
    # celle de son texte (63) — sinon le corridor d'indentation l'a détachée.
    items = [p for p in paras if "Capacity was added" in _txt(p)
             or "Freight costs eased" in _txt(p)]
    orphelines = [p for p in paras
                  if len(_txt(p).strip()) <= 1 and _txt(p).strip()]
    ok("P13  la PUCE n'est pas détachée de son texte",
       items and all(p["bbox"][0] < 55 for p in items) and not orphelines,
       f"{len(orphelines)} marqueur(s) orphelin(s) ; "
       f"x0 des items = {[round(p['bbox'][0], 1) for p in items]} (attendu ≈ 45)")

    # ── P14 : ALIGNEMENTS ───────────────────────────────────────────────────
    def _para(frag):
        return next((p for p in paras if frag in _txt(p)), None)

    # Le bloc d'adresse est LÉGITIMEMENT découpé (ses retours à la ligne sont
    # volontaires — l'Étape A les sépare) : on vérifie que CHAQUE morceau est
    # reconnu ferré à droite, qu'il ait plusieurs lignes ou une seule (la pile).
    morceaux = [p for p in paras
                if any(k in _txt(p) for k in ("Northern Operations", "Harbour Road",
                                              "Portsmouth", "14 November"))]
    p_dr = morceaux[0] if morceaux else None
    ok("P14  bloc ferré à DROITE détecté (tous ses morceaux)",
       morceaux and all(p.get("align") == "right" for p in morceaux),
       f"aligns={[p.get('align') for p in morceaux]}")
    p_ce = _para("Output held firm through the quarter")
    ok("P14  bloc CENTRÉ multi-lignes détecté",
       p_ce is not None and p_ce.get("align") == "center",
       f"align={(p_ce or {}).get('align')}")
    p_pu = _para("Capacity was added")
    ok("P14  une liste à PUCES reste ferrée à gauche",
       p_pu is not None and p_pu.get("align") == "left",
       f"align={(p_pu or {}).get('align')}")
    p_col = _para("Regional plants raised")
    ok("P14  une colonne de texte au fer à gauche le reste",
       p_col is not None and p_col.get("align") in ("left", "justify"),
       f"align={(p_col or {}).get('align')}")
    if p_dr:
        cb = p_dr.get("container_bbox") or p_dr["bbox"]
        ok("P14  le conteneur du bloc DROITE s'étend vers la GAUCHE "
           "(bord droit figé)",
           cb[0] < p_dr["bbox"][0] - 1 and cb[2] <= p_dr["bbox"][2] + 1,
           f"bbox=[{p_dr['bbox'][0]:.0f},{p_dr['bbox'][2]:.0f}] "
           f"conteneur=[{cb[0]:.0f},{cb[2]:.0f}]")

    # ── P10 : le vertical est LU ────────────────────────────────────────────
    def _rot(p):
        return any(abs((r.get("dir") or [1, 0])[1]) > 0.01
                   for ln in p.get("lines", []) for r in ln.get("runs", []))
    verticaux = [p for p in paras if _rot(p)]
    titre_v = [p for p in verticaux if "APPENDIX" in _txt(p)]
    ok("P10  le titre vertical à lettres espacées est LU comme un mot",
       titre_v and "APPENDIX SECTION" in _txt(titre_v[0]),
       _txt(titre_v[0])[:40] if titre_v else "(absent)")
    if titre_v:
        _tag, meta = tagging.tag_paragraph(titre_v[0])
        ls = (meta[0] or {}).get("letter_spaced") or {}
        bb = titre_v[0]["bbox"]
        empan = bb[3] - bb[1]                 # empan VERTICAL (axe d'écriture)
        ok("P10  l'empan est mesuré LE LONG DE L'AXE (pas en x)",
           ls and abs(ls.get("width", 0) - empan) < 2.0,
           f"ls_width={ls.get('width')} vs empan d'axe={empan:.1f} "
           f"(largeur en x = {bb[2]-bb[0]:.1f})")

    # ── P10 : le vertical est TRADUIT et RENDU dans sa bande ────────────────
    FAUX = {"APPENDIX SECTION": "SECTION ANNEXE", "Region": "Région",
            "Output": "Production", "Change": "Variation"}
    for p in paras:
        tagged, meta = tagging.tag_paragraph(p)
        p["tr_segments"] = meta
        cle = (p.get("text") or "").strip()
        p["tr_tagged"] = (f"[[0]]{FAUX[cle]}[[/0]]" if cle in FAUX else tagged)

    out = fitz.open()
    eng.render_page_into(out, pd, draw_borders=False, translated=True)
    rendu = out[0].get_text("dict")["blocks"]
    lignes_rot = [(ln["dir"], ln["bbox"],
                   "".join(s["text"] for s in ln["spans"]))
                  for b in rendu for ln in b.get("lines", [])
                  if abs(ln["dir"][1]) > 0.01]
    txt_rot = " ".join(t for _d, _b, t in lignes_rot).replace(" ", "")
    ok("P10  le titre vertical est TRADUIT au rendu",
       "SECTIONANNEXE" in txt_rot, f"rendu = {txt_rot[:60]!r}")
    ok("P10  les en-têtes pivotés sont TRADUITS",
       "Région" in txt_rot and "Production" in txt_rot,
       f"rendu = {txt_rot[:80]!r}")
    if titre_v:
        src = titre_v[0]["bbox"]
        cibles = [b for _d, b, t in lignes_rot
                  if b[1] >= src[1] - 3 and b[3] <= src[3] + 3 and b[0] < 60]
        ok("P10  le titre traduit reste DANS SA BANDE (aucun débordement)",
           cibles, f"bande source y=[{src[1]:.0f}, {src[3]:.0f}]")
    # Les en-têtes pivotés, serrés dans leur boîte, ne doivent pas se replier.
    entetes = [t for _d, _b, t in lignes_rot
               if any(k in t for k in ("Région", "Production", "Variation"))]
    ok("P10  un en-tête pivoté serré ne se REPLIE pas sur 2 lignes",
       len(entetes) == 3, f"{len(entetes)} en-tête(s) rendu(s) : {entetes}")

    # ── P14 : le RENDU traduit conserve le fer à droite ─────────────────────
    horiz = [(ln["bbox"], "".join(s["text"] for s in ln["spans"]))
             for b in rendu for ln in b.get("lines", [])
             if abs(ln["dir"][1]) <= 0.01]
    if morceaux:
        y0 = min(p["bbox"][1] for p in morceaux) - 3
        y1 = max(p["bbox"][3] for p in morceaux) + 3
        src_r = max(p["bbox"][2] for p in morceaux)
        rendues = [bb for bb, t in horiz
                   if y0 <= bb[1] <= y1 and bb[0] > 300]   # le bloc de droite seul
        if len(rendues) >= 2:
            bords = [bb[2] for bb in rendues]
            gauches = [bb[0] for bb in rendues]
            ok("P14  au RENDU, le bloc DROITE reste ferré à droite "
               "(bords droits alignés, gauches déchiquetés)",
               max(bords) - min(bords) <= 3.0
               and max(gauches) - min(gauches) > 8.0
               and abs(max(bords) - src_r) <= 4.0,
               f"bords droits={[round(b) for b in bords]} (source {src_r:.0f}) ; "
               f"gauches={[round(g) for g in gauches]}")
        else:
            ok("P14  au RENDU, le bloc DROITE reste ferré à droite",
               False, f"{len(rendues)} ligne(s) retrouvée(s)")

    # ── P15 : colonne étroite justifiée vs citation enjambée (page 2) ────────
    pd2 = eng.extract_page_data(doc, 1, doc[1], embed_images=True)
    paras2 = [e for e in pd2["elements"] if e.get("type") == "paragraph"]

    # (0) Le PIÈGE EST-IL ARMÉ ? Sans ce contrôle, le test pourrait passer pour
    #     la seule raison que la géométrie fabriquée est trop sage — il ne
    #     prouverait alors rien du tout. La mesure se prend sur les spans BRUTS
    #     de PyMuPDF, en amont de toute décision du moteur : le contrôle doit
    #     rester vrai que le correctif soit là ou non.
    brut = {}
    for bl in doc[1].get_text("dict")["blocks"]:
        for ln in bl.get("lines", []):
            for sp in ln.get("spans", []):
                if sp["text"].strip():
                    brut.setdefault(round(sp["origin"][1], 1), []).append(sp)
    arme = 0
    for _base, sps in brut.items():
        sps.sort(key=lambda s: s["bbox"][0])
        gws = sorted((s["bbox"][2] - s["bbox"][0]) / max(1, len(s["text"].strip()))
                     for s in sps)
        gw = gws[len(gws) // 2]
        for a, b in zip(sps, sps[1:]):
            if b["bbox"][0] - a["bbox"][2] > 2.5 * gw:
                arme += 1
    ok("P15  le piège est ARMÉ (des blancs de mots dépassent le seuil de coupe)",
       arme >= 3, f"{arme} blanc(s) au-delà de 2.5 × gw — la géométrie fabriquée "
                  f"doit vraiment mettre le moteur en défaut")

    def _t2(p):
        return " ".join((p.get("text") or "").split())

    # Égalité EXACTE, et paragraphe UNIQUE : une inclusion (« …in _t2(p) ») se
    # contenterait d'un bloc où les fragments se sont recollés dans le désordre
    # — c'est précisément ce que produit le moteur non corrigé.
    def _unique(nom, attendu):
        trouves = [p for p in paras2 if _t2(p) == attendu]
        autres = [_t2(p) for p in paras2 if _t2(p) != attendu]
        ok(nom, len(trouves) == 1,
           f"attendu 1 paragraphe exact, trouvé {len(trouves)} ; "
           f"page = {autres[:3]}")

    # (a) Colonne étroite justifiée : le bloc doit être reconstitué mot pour mot,
    #     dans l'ordre — les mots à gros blanc compris.
    _unique("P15  colonne étroite justifiée reconstituée mot pour mot",
            "Software providers reshaped how regional distribution partners "
            "handle the considerable volumes moving between the northern ports "
            "and the inland terminals throughout every season.")

    # (b) Contre-épreuve : la citation enjambée NE DOIT PAS absorber le corps.
    #     C'est le cas exact qui a fait échouer un veto fondé sur la seule
    #     absence de corridor vertical.
    soudes = [p for p in paras2
              if "rewrite" in _t2(p) and "Freight" in _t2(p)]
    ok("P15  les deux colonnes ENJAMBÉES ne sont pas soudées entre elles",
       not soudes,
       f"paragraphe mixte = {_t2(soudes[0])[:90]!r}" if soudes else "")
    _unique("P15  la citation enjambée est réparée DANS SA colonne",
            "The story we tell about our own past remains entirely ours to "
            "rewrite, and the accompanying documentation inevitably changes "
            "alongside it every single time.")
    _unique("P15  le corps enjambé est réparé DANS SA colonne",
            "Freight costs eased after the new routes opened and the yards were "
            "cleared well before the quarter closed, with transhipment "
            "throughput comfortably exceeding every projection.")

    # (c) L'ENCRE prime sur la géométrie : un filet vertical dans le blanc
    #     interdit le recollage (mur de cellule / cadre).
    recolle = [p for p in paras2 if "without any measurable" in _t2(p)]
    ok("P15  un FILET vertical dans le blanc empêche le recollage",
       not recolle,
       f"recollé malgré le filet = {_t2(recolle[0])[:80]!r}" if recolle else "")

    # ── P16 : le VIDE ne prouve pas un corridor (page 3) ─────────────────────
    # Contrôle de la RÈGLE, et non de son effet de bord : sur la démo, la passe
    # de recollage (P15) répare après coup les dégâts des corridors fantômes, si
    # bien qu'aucun compte de paragraphes ne les trahit. On interroge donc
    # directement le juge : quels corridors retient-il, et sur quelles rangées ?
    rows3 = _rangees(eng, doc[2])
    guts3 = eng._column_gutters(rows3)
    dedans = []
    for row in rows3:
        xs = [s["bbox"] for s in row["spans"]]
        # rangée APPARTENANT à la colonne du milieu (elle y a du texte)
        if not any(MILIEU_X - 1 <= b[0] and b[2] <= MILIEU_X + MILIEU_W + 1
                   for b in xs):
            continue
        for g0, g1 in guts3.get(id(row), ()):
            mid = 0.5 * (g0 + g1)
            if MILIEU_X < mid < MILIEU_X + MILIEU_W:
                dedans.append((round(g0, 1), round(g1, 1)))
    ok("P16  aucun corridor FANTÔME à l'intérieur de la colonne du milieu",
       not dedans,
       f"corridors retenus dans la colonne : {sorted(set(dedans))} — "
       f"une zone VIDE a servi de preuve")

    # ── P17 : un CHAPÔ pleine largeur ne soude pas les colonnes (page 4) ─────
    pd4 = eng.extract_page_data(doc, 3, doc[3], embed_images=True)
    paras4 = [e for e in pd4["elements"] if e.get("type") == "paragraph"]
    sous = [p for p in paras4 if p["bbox"][1] >= P17_Y - 12]     # sous le chapô
    larges = [p for p in sous if p["bbox"][2] - p["bbox"][0] > P17_W + 10]
    ok("P17  le CHAPÔ pleine largeur ne SOUDE pas les colonnes",
       not larges,
       f"{len(larges)} paragraphe(s) à cheval sur plusieurs colonnes : "
       f"{[(round(p['bbox'][0]), round(p['bbox'][2])) for p in larges]}")
    # Et chaque colonne doit bien être là, entière et à sa place.
    attendus = [P17_X + k * P17_PAS for k in range(4)]
    trouves = sorted({round(p["bbox"][0]) for p in sous})
    ok("P17  les 4 colonnes restent distinctes et à leur fer",
       trouves == attendus,
       f"fers gauches trouvés={trouves}, attendus={attendus}")

    # ── P18 : légende sous une photo décentrée (page 5) ──────────────────────
    pd5 = eng.extract_page_data(doc, 4, doc[4], embed_images=True)
    p_leg = next((e for e in pd5["elements"] if e.get("type") == "paragraph"
                  and "Northern terminals" in (e.get("text") or "")), None)
    ok("P18  la légende sous une photo est reconnue CENTRÉE",
       p_leg is not None and p_leg.get("align") == "center",
       f"align={(p_leg or {}).get('align')!r} — le titre pleine largeur a fait "
       f"passer la PAGE pour sa colonne")
    if p_leg:
        cb = p_leg.get("container_bbox") or p_leg["bbox"]
        px0, px1 = P18_PHOTO
        # Le contrôle décisif : le conteneur doit être celui de la PHOTO, sur les
        # DEUX bords. Un bord droit resté au voisin recentrerait la légende de
        # travers et la ferait déborder.
        ok("P18  son conteneur est celui de la PHOTO (deux bords)",
           abs(cb[0] - px0) <= 2.0 and abs(cb[2] - px1) <= 2.0,
           f"conteneur=[{cb[0]:.1f},{cb[2]:.1f}] attendu=[{px0},{px1}]")
        ok("P18  la légende est centrée sur l'axe de la PHOTO, pas de la page",
           abs((cb[0] + cb[2]) / 2 - (px0 + px1) / 2) <= 2.0,
           f"axe conteneur={(cb[0]+cb[2])/2:.1f} · axe photo={(px0+px1)/2:.1f} "
           f"· axe page={W/2:.1f}")

    # ── P19 : sous une photo, ce n'est PAS toujours une légende ─────────────
    pd6 = eng.extract_page_data(doc, 5, doc[5], embed_images=True)
    corps6 = [e for e in pd6["elements"] if e.get("type") == "paragraph"
              and "Freight volumes" in (e.get("text") or "")]
    gauche = next((e for e in corps6 if e["bbox"][0] < 320), None)
    droite = next((e for e in corps6 if e["bbox"][0] >= 320), None)

    # Le cadre-photo ne sait PAS ce qu'est une légende : il se déclenche aussi
    # sur ce corps de texte (vérifié : cadre déduit [45;358] -> [60;300]). Ce
    # qu'il faut prouver n'est donc pas qu'il s'abstient, mais qu'il ne DÉFORME
    # rien — mieux : qu'il RÉPARE la colonne empoisonnée du corps comme celle
    # d'une légende. Même règle, même bénéfice, aucune notion de « légende ».
    if gauche:
        cb = gauche.get("container_bbox") or gauche["bbox"]
        gx0, gx1 = P19_GCOL
        ok("P19  photo à la largeur de la colonne : le corps retrouve sa colonne",
           abs(cb[0] - gx0) <= 3.0 and abs(cb[2] - gx1) <= 3.0,
           f"conteneur=[{cb[0]:.1f},{cb[2]:.1f}] attendu=[{gx0},{gx1}] — sans le "
           f"cadre-photo, le titre pleine largeur donne [45;358] pour colonne")

    # Photo PLUS ÉTROITE que sa colonne : le cadre-photo doit DÉCLINER, sinon il
    # écraserait le corps à la largeur de la photo. Contrôle EN BOÎTE BLANCHE :
    # une assertion sur le conteneur passerait à vide ici — un paragraphe
    # justifié garde sa marge droite par un tout autre chemin, qui masquerait la
    # panne. On interroge donc la règle elle-même.
    pg6 = doc[5]
    d6 = eng._extract_drawings(pg6)
    i6 = eng._extract_images(doc, pg6, 5, False, None)
    ctx6 = eng._build_page_ctx(pg6, eng._extract_text(pg6, d6, i6), d6, i6)
    if droite:
        b = droite["bbox"]
        cadre = eng._overhead_frame(b[0], b[2], b[1], droite.get("size") or 9.0,
                                    ctx6["obstacles"], P19_DCOL)
        ok("P19  une photo plus étroite que la colonne ne CAPTURE pas le corps",
           cadre is None,
           f"cadre-photo={cadre} — le corps serait écrasé à la largeur de la "
           f"photo {P19_DPHOTO} au lieu de sa colonne {P19_DCOL}")

    # ── P20 : liste à alinéa négatif que l'orthographe ne sait pas couper ───
    pd7 = eng.extract_page_data(doc, 6, doc[6], embed_images=True)
    par7 = [e for e in pd7["elements"] if e.get("type") == "paragraph"]
    txt7 = [(e["bbox"][0], " ".join((e.get("text") or "").split()))
            for e in par7]

    def _existe(attendu, x_min, x_max):
        return any(t == attendu and x_min <= x <= x_max for x, t in txt7)

    # 1. L'intro finit par « : » : elle ne doit PAS avaler l'item 1.
    ok("P20  l'intro finissant par « : » n'avale pas l'item 1",
       _existe("You must drive only under the immediate supervision of your:",
               P20_X - 3, P20_X + 3),
       f"intro soudée — trouvé : "
       f"{[t for x, t in txt7 if t.startswith('You must')]}")

    # 2. Les 6 items sont 6 paragraphes distincts.
    items = [t for x, t in txt7 if P20_X - 3 <= x <= P20_X + 3
             and re.match(r"^\d\.", t)]
    ok("P20  les 6 items de la liste sont 6 paragraphes distincts",
       len(items) == 6, f"{len(items)} item(s) : {items}")

    # 3. L'item 5 finit par « or » (mot NON TERMINAL) : il garde son « or »
    #    sans avaler l'item 6.
    ok("P20  l'item finissant par « or » n'avale pas le suivant",
       _existe("5. Driving School Instructor; or", P20_X - 3, P20_X + 3),
       f"item 5 = {[t for x, t in txt7 if t.startswith('5.')]}")

    # 4. CONTRE-ÉPREUVE, en boîte blanche : des rangs NON ADJACENTS au même bord
    #    ne sont pas une liste. Une assertion sur le découpage passerait à vide
    #    (d'autres règles coupent déjà ces lignes) — on interroge la preuve
    #    elle-même.
    pg7 = doc[6]
    d7 = eng._extract_drawings(pg7)
    im7 = eng._extract_images(doc, pg7, 6, False, None)
    lignes7 = [eng._line_metrics(ln) for ln in eng._extract_text(pg7, d7, im7)]
    eng._tag_list_markers(lignes7)
    marq = {round(m["left"]): m.get("list_marker", False) for m in lignes7
            if eng._list_ordinal(m["text"])}
    vrais = [x for x, v in marq.items() if v and abs(x - P20_X) <= 3]
    faux = [x for x, v in marq.items() if v and abs(x - P20_CX) <= 3]
    ok("P20  la liste 1..6 est PROUVÉE par ses rangs qui se suivent",
       len(vrais) >= 1, f"aucun marqueur prouvé à x={P20_X}")
    ok("P20  des rangs NON adjacents (1. puis 7.) ne font pas une liste",
       not faux, f"marqueurs faussement prouvés à x={P20_CX} : {faux}")

    # ── P22 — LE BLANC N'A PAS DE BORD (mais reste un PONT) ─────────────────
    pd22 = eng.extract_page_data(doc, 7, doc[7])
    p22 = next((e for e in pd22["elements"] if e.get("type") == "paragraph"
                and "restraint violation" in (e.get("text") or "")), None)
    ok("P22  le paragraphe à espaces de tête existe (piège armé)", p22 is not None)
    if p22 is not None:
        largeur_espaces = _w("  ", 10)
        encre_attendue = P22_MUR + 0.9 + largeur_espaces
        # Le PONT : le bbox complet inclut toujours les espaces (les raboter
        # rouvre l'éclatement des lignes — « F O R E W O R D / B Y / J A K E »).
        ok("P22  le bbox COMPLET commence sur les espaces (pont conservé)",
           p22["bbox"][0] <= P22_MUR + 1.6, f"bbox[0]={p22['bbox'][0]:.2f}")
        # Le BORD : l'encre et le conteneur (donc le rendu) commencent au « S »,
        # jamais sur le mur de la cellule.
        ok("P22  l'ENCRE du paragraphe commence après les espaces",
           p22.get("ink_bbox")
           and abs(p22["ink_bbox"][0] - encre_attendue) <= 0.8,
           f"ink={p22.get('ink_bbox')} attendu x0≈{encre_attendue:.2f}")
        # On juge la BANDE (`container_lines`), pas `container_bbox` : c'est elle
        # que le reflow consomme — un premier jet de ce contrôle lisait le bbox
        # du conteneur et passait sous mutation (contrôle vacant).
        bandes = p22.get("container_lines") or [p22.get("container_bbox")
                                                or p22["bbox"]]
        ok("P22  la BANDE de rendu part de l'encre, pas du blanc",
           all(b[0] >= encre_attendue - 0.8 for b in bandes),
           f"bandes x0={[round(b[0], 2) for b in bandes]} "
           f"attendu ≥ {encre_attendue - 0.8:.2f}")

    # ── P24 — RALLONGE DE DÉTRESSE : le blanc réel avant la compression ─────
    pd24 = eng.extract_page_data(doc, 8, doc[8])
    paras24 = [e for e in pd24["elements"] if e.get("type") == "paragraph"]
    pA = next((p for p in paras24 if "Winter schedules" in (p.get("text") or "")), None)
    pB = next((p for p in paras24 if "Summer schedules" in (p.get("text") or "")), None)
    ok("P24  les deux blocs jumeaux existent (piège armé)",
       pA is not None and pB is not None)
    if pA is not None and pB is not None:
        for p in paras24:
            tagged, meta = tagging.tag_paragraph(p)
            p["tr_segments"] = meta
            p["tr_tagged"] = tagged
        # « Traduction » 5× plus longue pour A et B : sans espace supplémentaire,
        # il faudrait descendre très en dessous du plancher de lisibilité.
        longA = " ".join(["Les horaires reviennent la semaine prochaine"] * 5)
        longB = " ".join(["Les horaires reviennent le mois prochain dès"] * 2)
        pA["tr_tagged"] = f"[[0]]{longA}[[/0]]"
        pB["tr_tagged"] = f"[[0]]{longB}[[/0]]"
        out24 = fitz.open()
        eng.render_page_into(out24, pd24, draw_borders=False, translated=True)
        spans24 = [(s["bbox"], round(s["size"], 2), s["text"])
                   for b in out24[0].get_text("dict")["blocks"]
                   for ln in b.get("lines", []) for s in ln.get("spans", [])]
        tailleA = max((sz for bb, sz, t in spans24
                       if bb[0] < 300 and "horaires" in t), default=0)
        tailleB = max((sz for bb, sz, t in spans24
                       if bb[0] >= 300 and "horaires" in t), default=0)
        ok("P24  avec du BLANC réel dessous : jamais sous le plancher (0,88 min)",
           tailleA >= 0.88 * 10 - 0.05, f"corps rendu A = {tailleA}")
        ok("P24  coincé sous un voisin : la compression profonde reste (sous 0,88)",
           0 < tailleB < 0.88 * 10, f"corps rendu B = {tailleB}")
        basB = max((bb[3] for bb, sz, t in spans24
                    if bb[0] >= 300 and "horaires" in t), default=0)
        ok("P24  et le bloc coincé ne MORD pas sur son voisin (anti-collision)",
           0 < basB <= P24_VOISIN_B_Y - 7.0,
           f"bas de B = {basB:.1f}, voisin vers y={P24_VOISIN_B_Y}")

    # ── P25 — SATELLITES : exposants/indices restent avec leur mot ──────────
    pd25 = eng.extract_page_data(doc, 9, doc[9])
    paras25 = [e for e in pd25["elements"] if e.get("type") == "paragraph"]
    pL = next((p for p in paras25 if "Values" in (p.get("text") or "")), None)
    pOrd = next((p for p in paras25 if "infraction" in (p.get("text") or "")), None)
    orphelins = [p for p in paras25
                 if (p.get("text") or "").strip() in ("1", "2", "re")]
    ok("P25  aucun satellite ORPHELIN (ni « 1 », ni « 2 », ni « re » seuls)",
       not orphelins, f"{len(orphelins)} orphelin(s)")
    ok("P25  l'indice reste dans la phrase de son hôte (« L1 … L2 »)",
       pL is not None and "L1" in pL["text"].replace(" ", "")
       and "L2" in pL["text"].replace(" ", ""),
       (pL or {}).get("text", "(absent)")[:60])
    ok("P25  l'ordinal reste soudé (« 1re infraction »)",
       pOrd is not None and pOrd["text"].replace(" ", "").startswith("1re"),
       (pOrd or {}).get("text", "(absent)")[:40])
    if pL is not None and pOrd is not None:
        _tg1, meta_L = tagging.tag_paragraph(pL)
        rises = [m.get("rise", 0) for m in meta_L]
        ok("P25  le décalage de baseline SURVIT au balisage (rise dans la meta)",
           any(abs(r or 0) > 0.5 for r in rises), f"rises={rises}")
        # Rendu identité : le satellite est repeint À SA HAUTEUR, collé.
        for p in paras25:
            tg, meta = tagging.tag_paragraph(p)
            p["tr_segments"] = meta
            p["tr_tagged"] = tg
        out25 = fitz.open()
        eng.render_page_into(out25, pd25, draw_borders=False, translated=True)
        chars = [(c["c"], round(s["size"], 1), c["origin"])
                 for b in out25[0].get_text("rawdict")["blocks"]
                 for ln in b.get("lines", []) for s in ln.get("spans", [])
                 for c in s.get("chars", [])]
        y_L = [o[1] for ch, sz, o in chars if ch == "L" and sz > 9]
        y_1 = [o[1] for ch, sz, o in chars if ch == "1" and sz < 9
               and abs(o[1] - P25_Y) < 12]
        ok("P25  au rendu, l'INDICE est peint SOUS la baseline de son hôte",
           y_L and y_1 and min(y_1) > min(y_L) + 1.0,
           f"y_L={y_L[:2]} y_indice={y_1[:2]}")
        y_h = [o[1] for ch, sz, o in chars if ch == "1" and sz > 9]
        y_re = [o[1] for ch, sz, o in chars if ch == "r" and sz < 8
                and abs(o[1] - P25_Y2) < 12]
        ok("P25  au rendu, l'EXPOSANT est peint AU-DESSUS de la baseline",
           y_h and y_re and max(y_re) < max(y_h) - 1.0,
           f"y_hote={y_h[:2]} y_exposant={y_re[:2]}")

    # ── P25 — la chaîne soudée ne casse JAMAIS au bord de ligne ─────────────
    # Reproduit hb p239 : conteneur étroit, la coupe tombe pile avant l'indice.
    from engines.pdf import reflow as _rf25
    _f25 = [(fitz.Font("helv"), None)]
    segsN = [{"text": "la norme L", "fonts": _f25, "size": 10,
              "color": (0, 0, 0)},
             {"text": "2", "fonts": _f25, "size": 6.5, "color": (0, 0, 0),
              "rise": -2.0}]
    larg = _rf25.text_width("la norme L", _f25, 10) + 2.0   # « 2 » ne tient pas
    resN = _rf25.reflow_paragraph(segsN, [[0, 0, larg, 40]], lang="fr_FR",
                                  first_baseline=10, align="left")
    lignesN = ["".join(r["text"] for r in ln["runs"]) for ln in resN["lines"]]
    ok("P25  la coupe de ligne n'orpheline JAMAIS l'indice (L2 passe entier)",
       all(("L" in l) == ("2" in l) for l in lignesN), f"lignes={lignesN}")

    # ── P21 — GRAISSE PONDÉRÉE : un demi-gras n'est pas un gras ─────────────
    # mv21 : ProximaNova-Semibold (tableaux entiers) était rendu Montserrat-Bold
    # — le drapeau binaire de PyMuPDF promeut tout poids intermédiaire. La règle
    # lit le POIDS dans le nom source (propriété de la convention de nommage des
    # fontes, pas d'un document) et charge la variante la plus proche.
    from engines.pdf.engine import (_weight_class, _load_matched_font,
                                      _FONTS_DIR)

    ok("P21  le NOM prime sur le drapeau : Semibold n'est PAS un gras",
       _weight_class("AnyFace-Semibold", True) == "semibold"
       and _weight_class("AnyFace-DemiBold", True) == "semibold"
       and _weight_class("AnyFace-Bold", True) == "bold"
       and _weight_class("AnyFace-Black", True) == "extrabold"
       and _weight_class("AnyFace", False) == "regular")

    # La preuve est PHYSIQUE (encre), pas nominale : la variante servie pour un
    # demi-gras doit peser STRICTEMENT entre Regular et Bold de la même famille.
    def _ink(font):
        d = fitz.open(); pg = d.new_page(width=260, height=50)
        pg.insert_font(fontname="T", fontbuffer=font.buffer)
        pg.insert_text((8, 34), "Héberger çà Éœ", fontsize=22, fontname="T")
        pix = pg.get_pixmap(dpi=120, colorspace=fitz.csGRAY)
        ink = sum(1 for b in pix.samples if b < 128) / len(pix.samples)
        d.close()
        return ink
    f_reg = _load_matched_font("montserrat", False, False, weight="regular")
    f_sb = _load_matched_font("montserrat", True, False, weight="semibold")
    f_b = _load_matched_font("montserrat", True, False, weight="bold")
    ok("P21  l'encre du demi-gras servi est STRICTEMENT entre Regular et Bold",
       _ink(f_reg) < _ink(f_sb) < _ink(f_b),
       f"{_ink(f_reg):.4f} / {_ink(f_sb):.4f} / {_ink(f_b):.4f}")

    # Famille SANS variante intermédiaire : repli sur Bold — l'ancien
    # comportement, jamais pire (contrôle de non-régression).
    import os as _os
    assert not _os.path.exists(_os.path.join(_FONTS_DIR, "ptserif-SemiBold.ttf"))
    f_pt = _load_matched_font("ptserif", True, False, weight="semibold")
    ok("P21  sans variante disponible, repli sur Bold (jamais pire qu'avant)",
       f_pt is not None and "Bold" in (f_pt.name or ""), f_pt.name if f_pt else None)

    # ── P23 — CÉSURE : au moins 3 lettres de chaque côté de la coupe ────────
    # 27 césures fautives mesurées sur les documents de référence (« don-né »,
    # « socié-té », « demande-ra ») : toutes laissaient un moignon de 2 lettres
    # en tête de ligne. La règle est typographique, indépendante du document.
    from engines.pdf import reflow as _rf
    _fonts = [(fitz.Font("helv"), None)]        # (police, couverture) — cf. glyph_font

    def _coupe(mot, avail=9999.0):
        return _rf._hyphen_split(mot, _fonts, 10.0, 1.0, avail, "fr_FR")

    c1 = _coupe("donné")
    ok("P23  « donné » n'est plus coupé (reste de 2 lettres interdit)",
       c1 is None, f"obtenu {c1!r}")
    c2 = _coupe("véhicule")
    ok("P23  « véhicule » reste coupable (véhi-cule : 4 lettres de chaque côté)",
       c2 is not None and len(c2[1]) >= 3 and len(c2[0].rstrip('-')) >= 3,
       f"obtenu {c2!r}")
    c3 = _coupe("utilisé")
    ok("P23  « utilisé » ne laisse jamais « sé » orphelin",
       c3 is None or len(c3[1]) >= 3, f"obtenu {c3!r}")

    # ── P27 : découpes (clip) et ordre de peinture images / dessins ────────
    # Une photo RONDE est une image carrée découpée par un cercle ; un bandeau
    # en BIAIS, un rectangle découpé par un triangle. Ignorer la découpe
    # repeint le carré, le rectangle entier. Et une image peinte AVANT un fond
    # qui la recouvre doit rester dessous.
    for nom, bon, detail in _p27_clips_et_ordre(eng):
        ok(nom, bon, detail)

    # ── Rapport ─────────────────────────────────────────────────────────────
    print("=" * 78)
    print("GÉNÉRICITÉ — document SYNTHÉTIQUE (jamais vu par le moteur)")
    print("=" * 78)
    n_ok = 0
    for nom, bon, detail in checks:
        print(f"  [{'OK ' if bon else 'ÉCHEC'}] {nom}")
        if detail and not bon:
            print(f"          {detail}")
        n_ok += bon
    print()
    print(f"== {n_ok}/{len(checks)} ==")
    return 0 if n_ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(run())
