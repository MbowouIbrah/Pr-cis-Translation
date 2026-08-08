"""Recoller les lignes JUSTIFIÉES coupées en morceaux.

LE DÉFAUT, VU À L'ÉCRAN
------------------------
Un paragraphe de quatre lignes ressortait en SIX blocs :

    Voiture  de   tourisme  plus        ->  'Voiture de plus' + 'tourisme'
    remorque  si  l'ensemble            ->  'remorque si' + "l'ensemble"
    n'entre pas dans la catégorie       ->  intacte
    E(B).                               ->  intacte

Justifier une colonne étroite étire ses blancs de mots. Mesuré ici : les
écarts valent 2,54 x et 3,55 x la largeur de glyphe, au-dessus du seuil de
coupe en colonnes (`_COL_SPLIT_FACTOR` = 2,5). Le moteur y voit des colonnes
et sépare les mots de leur phrase.

POURQUOI `_rejoin_justified` DU MOTEUR PDF NE SUFFIT PAS ICI
--------------------------------------------------------------
Il existe et traite exactement ce cas, mais il exige `_JUST_MIN_LINES` = 3
lignes **INTACTES** partageant les deux fers, pour prouver la colonne. Un
fragment ne peut pas se porter témoin de lui-même — règle juste, et c'est elle
qui empêche de souder quatre colonnes de journal.

Sur ce bloc, il n'y a qu'UNE ligne intacte utilisable :

    r1 'Voiture de plus'            coupée
    r2 'tourisme'                   fragment isolé
    r3 "remorque si l'ensemble"     coupée
    r4 "n'entre pas dans la catégorie"   INTACTE  <- la seule
    r5 'E(B).'                      dernière ligne, non étirée

1 témoin < 3 exigés : la réparation ne se déclenche jamais. C'est la « colonne
justifiée courte », limite déjà mesurée et documentée côté PDF.

LE SIGNAL QUE L'ON A ICI ET QUE LE PDF N'A PAS
------------------------------------------------
On ne juge pas les FRAGMENTS, on juge l'ÉTENDUE DES RANGÉES. Une rangée coupée
en trois morceaux commence toujours au fer gauche de la colonne et finit
toujours à son fer droit — l'étirement déplace les blancs INTÉRIEURS, jamais
les bords.

Mesuré sur le bloc ci-dessus, en prenant les fers de TOUTES les rangées,
coupées comprises :

    fer gauche : 139,7 · 139,9 · 139,9 · 139,9 · 172,8   ->  4/5 à ~139,9
    fer droit  : 152,2 · 197,8 · 212,2 · 212,2 · 212,2   ->  3/5 à ~212,2

Trois rangées partagent les DEUX fers. La colonne est donc attestée, par une
mesure que `_justified_columns` écarte parce qu'elle vient de rangées coupées.

CE QUI REND LA RÈGLE SÛRE
--------------------------
Le risque est de recoller deux VRAIES colonnes. Trois garde-fous, tous
nécessaires :

  1. il faut au moins 3 rangées au MÊME fer gauche ET au MÊME fer droit —
     deux colonnes voisines ne partagent pas leur fer droit avec le fer gauche
     de l'autre ;
  2. la rangée recollée doit REMPLIR la colonne attestée, d'un bord à l'autre.
     Un fragment qui s'arrête au milieu n'est pas une ligne de cette colonne ;
  3. aucun MUR D'ENCRE (filet, image) ne doit traverser la zone recollée —
     c'est le seul signal qui prouve une séparation matérielle.

On n'applique cela QUE dans le moteur OCR. Le moteur PDF n'est pas touché : sa
suite générique (59/59) protège des cas que celui-ci n'a pas à connaître, et
son `_rejoin_justified` reste le chemin normal quand les témoins existent.
"""
from __future__ import annotations

#: Nombre de rangées devant partager les deux fers pour attester une colonne.
#: 3, comme `_JUST_MIN_LINES` côté PDF : deux rangées peuvent s'aligner par
#: hasard, trois non.
_RANGEES_MIN = 3

#: Tolérance d'alignement des fers, en fraction de la largeur de glyphe. Un
#: scan ne pose pas deux lignes au pixel près.
_FER_TOL_GW = 1.2

#: Un blanc de justification reste BORNÉ. Au-delà, c'est une vraie gouttière :
#: on ne recolle pas. Mesuré sur ce document : les écarts de justification
#: valent 2,5 à 3,6 x la largeur de glyphe.
_ECART_MAX_GW = 6.0


def _fers(ligne):
    bb = ligne.get("bbox")
    return (bb[0], bb[2]) if bb else (None, None)


def _gw(ligne):
    g = ligne.get("gw") or 0.0
    return g if g > 0 else 1.0


def _rangees(lignes):
    """Regroupe les lignes en RANGÉES : celles qui se recouvrent verticalement.

    ÉTAPE INDISPENSABLE, ET C'EST LE PIÈGE QUI M'A COÛTÉ UN ESSAI.
    `_group_text_lines` rend chaque FRAGMENT comme une ligne à part entière.
    Mesurer les fers sur cette liste mesure donc les fers des fragments —
    « tourisme » commence à 172,8, pas au fer de la colonne — et la colonne
    n'est jamais attestée.

    Il faut donc reconstituer les rangées AVANT de mesurer quoi que ce soit.
    """
    restants = sorted((l for l in lignes if l.get("bbox")),
                      key=lambda l: (l["bbox"][1], l["bbox"][0]))
    rangees, pris = [], set()
    for i, a in enumerate(restants):
        if i in pris:
            continue
        grp = [a]
        pris.add(i)
        for j, b in enumerate(restants):
            if j <= i or j in pris:
                continue
            rec = min(a["bbox"][3], b["bbox"][3]) - max(a["bbox"][1], b["bbox"][1])
            hmin = min(a["bbox"][3] - a["bbox"][1], b["bbox"][3] - b["bbox"][1])
            if hmin > 0 and rec >= 0.5 * hmin:
                grp.append(b)
                pris.add(j)
        rangees.append(grp)
    return rangees


def _colonnes_attestees(rangees):
    """Les couples (gauche, droite) partagés par au moins `_RANGEES_MIN`
    RANGÉES — que celles-ci soient intactes ou coupées.

    C'est là toute la différence avec `_justified_columns` du moteur PDF, qui
    n'accepte que des lignes INTACTES : une rangée coupée commence quand même
    au fer gauche de sa colonne et finit à son fer droit, parce que
    l'étirement de la justification déplace les blancs INTÉRIEURS et jamais
    les bords.
    """
    vus = []
    for grp in rangees:
        g = min(m["bbox"][0] for m in grp)
        d = max(m["bbox"][2] for m in grp)
        tol = _FER_TOL_GW * max(_gw(m) for m in grp)
        for c in vus:
            if abs(c["g"] - g) <= tol and abs(c["d"] - d) <= tol:
                c["n"] += 1
                break
        else:
            vus.append({"g": g, "d": d, "n": 1, "tol": tol})
    return [(c["g"], c["d"], c["tol"]) for c in vus
            if c["n"] >= _RANGEES_MIN]


def _mots(ligne):
    return ligne.get("runs") or ligne.get("spans") or []


def _mur_entre(x0, x1, y0, y1, murs) -> bool:
    """Un mur d'encre traverse-t-il verticalement la zone ?"""
    for mx0, my0, mx1, my1 in murs or ():
        if my1 <= y0 or my0 >= y1:
            continue
        if x0 < 0.5 * (mx0 + mx1) < x1:
            return True
    return False


def recoller(lignes, murs=()):
    """Fusionne les lignes qui sont des morceaux d'une même ligne justifiée.

    Ne touche à rien d'autre : si aucune colonne n'est attestée, la liste
    ressort telle quelle. C'est ce qui rend l'opération sûre — elle ne peut pas
    dégrader une page qui n'a pas le défaut.
    """
    if not lignes or len(lignes) < _RANGEES_MIN:
        return lignes
    rangees = _rangees(lignes)
    cols = _colonnes_attestees(rangees)
    if not cols:
        return lignes

    out = []
    for rangee in rangees:
        if len(rangee) < 2:                   # rangée déjà intacte
            out.extend(rangee)
            continue
        # UNE RANGÉE TRAVERSE LA PAGE, PAS UNE COLONNE. Mesuré : la rangée
        # « Voiture | de | tourisme | plus » s'étend jusqu'à 331,0 parce
        # qu'elle emporte les fragments de la colonne VOISINE, qui partagent
        # sa bande de baseline. Aucune colonne ne peut alors correspondre.
        #
        # On découpe donc la rangée PAR COLONNE ATTESTÉE avant de juger, et on
        # laisse tel quel ce qui n'appartient à aucune.
        restes = list(rangee)
        # DE LA PLUS ÉTROITE À LA PLUS LARGE, et c'est indispensable.
        # `cols` sort dans l'ordre où les colonnes ont été rencontrées, qui
        # n'a aucun sens géométrique. Mesuré page 3 : la rangée « 3 | mètres |
        # et | d'intervalles | de » (352,3 → 437,5) était jugée contre la
        # colonne pleine largeur (42,0 → 437,0), qu'elle ne remplit
        # évidemment pas — puis ses mots étaient consommés, et la colonne
        # (352,1 → 437,0) qui leur va exactement n'était jamais essayée.
        #
        # La plus étroite qui contient les fragments est toujours la bonne :
        # une colonne large les contient aussi, mais par accident.
        for col in sorted(cols, key=lambda c: c[1] - c[0]):
            dans = [m for m in restes
                    if m["bbox"][0] >= col[0] - col[2]
                    and m["bbox"][2] <= col[1] + col[2]]
            if len(dans) < 2:
                continue
            fusion = _fusionner_si_possible(dans, col, murs, cols)
            if len(fusion) == len(dans):
                continue        # refusée : ne PAS consommer les fragments
            restes = [m for m in restes if m not in dans]
            out.extend(fusion)
        out.extend(restes)
    out.sort(key=lambda l: (round(l["bbox"][1], 1), l["bbox"][0]))
    return out


def _franchit_une_colonne(membres, col, cols) -> bool:
    """La fusion enjamberait-elle le FER GAUCHE d'une AUTRE colonne ?

    Le garde-fou qui manquait, et le défaut qu'il ferme est spectaculaire :
    page 2, la rangée « On | l'appelle | communément | nationale est de 30
    heures) » mêle trois fragments de la colonne de gauche (39,6 → 124,6) et
    un fragment de la colonne du milieu, qui commence à 130,8. Fusionnée, elle
    produisait un bloc chevauchant son voisin sur 178 pt.

    Aucun autre signal ne l'attrapait :

      * il n'y a PAS de gouttière de page entre ces deux colonnes — mesuré,
        les bandes totalement vides sont ailleurs (219→250, 432→438) ;
      * l'écart intérieur vaut 3,3 x la largeur de glyphe, et le balayage de
        `_ECART_MAX_GW` montre que le SERRER DÉGRADE (38 défauts à 2,5 contre
        23 à 6,0) — il coupe alors de vraies lignes justifiées.

    Ce qui reste est la seule chose qu'on sache vraiment : une AUTRE colonne
    est attestée, et son fer gauche tombe au milieu de ce qu'on s'apprête à
    souder. Une ligne n'enjambe pas le début d'une autre colonne.
    """
    gx0 = min(m["bbox"][0] for m in membres)
    gx1 = max(m["bbox"][2] for m in membres)
    for autre in cols or ():
        if autre is col:
            continue
        fer = autre[0]
        if gx0 + autre[2] < fer < gx1 - autre[2]:
            return True
    return False


def _fusionner_si_possible(membres, col, murs, cols=()):
    """Fusionne ces fragments s'ils remplissent la colonne, sinon les rend
    inchangés. Rend TOUJOURS une liste."""
    membres = sorted(membres, key=lambda m: m["bbox"][0])
    gx0 = min(m["bbox"][0] for m in membres)
    gx1 = max(m["bbox"][2] for m in membres)
    gy0 = min(m["bbox"][1] for m in membres)
    gy1 = max(m["bbox"][3] for m in membres)
    # 1) les fragments doivent REMPLIR la colonne, d'un fer à l'autre. Un
    #    groupe qui s'arrête au milieu n'est pas une ligne de cette colonne —
    #    c'est le garde-fou contre le recollage de deux blocs distincts.
    if not (abs(col[0] - gx0) <= col[2] and abs(col[1] - gx1) <= col[2]):
        return membres
    # 2) aucun écart intérieur ÉNORME : au-delà, c'est une vraie gouttière.
    gw = max(_gw(m) for m in membres)
    ecart_max = max((b["bbox"][0] - a["bbox"][2])
                    for a, b in zip(membres, membres[1:]))
    if ecart_max > _ECART_MAX_GW * gw:
        return membres
    # 3) aucun MUR D'ENCRE ne traverse : seul signal d'une séparation
    #    matérielle (filet de tableau, bord d'image).
    if _mur_entre(gx0, gx1, gy0, gy1, murs):
        return membres
    # 4) la fusion n'enjambe pas le FER GAUCHE d'une AUTRE colonne attestée.
    if _franchit_une_colonne(membres, col, cols):
        return membres

    tous = []
    for m in membres:
        tous.extend(_mots(m))
    tous.sort(key=lambda m: m["bbox"][0])
    neuve = dict(membres[0])
    neuve["bbox"] = [gx0, gy0, gx1, gy1]
    neuve["ink_x0"] = gx0
    neuve["ink_x1"] = gx1
    neuve["runs"] = tous
    neuve["text"] = " ".join((m.get("text") or "").strip() for m in tous
                             if (m.get("text") or "").strip())
    return [neuve]
