"""Recoller deux blocs qui sont le MÊME paragraphe, coupé en deux.

L'IDÉE, ET LA RAISON DE SA PRUDENCE
------------------------------------
Quand deux blocs se chevauchent, il arrive qu'ils soient un seul paragraphe
que le regroupement a scindé. Mesuré page 1 :

    [18] « Après deux ans de permis B, vous êtes autorisé à conduire une 125
          avec une »
    [20] « formation pratique complémentaire. Si vous désirez conduire une
          moto de plus grosse cylindrée, vous devez passer le permis A. »

C'est UNE phrase. Les fusionner supprime le chevauchement et rétablit le
texte.

MAIS « SE TOUCHER » NE SUFFIT PAS À DÉCIDER, ET C'EST TOUT L'ENJEU
--------------------------------------------------------------------
Sur les 15 paires en conflit relevées sur 3 pages, **3 seulement** sont un
paragraphe coupé. Les 12 autres se touchent aussi et ne doivent surtout pas
fusionner :

  · deux COLONNES côte à côte (9 cas). Page 3, « Le dépassement est interdit
    si… » et « Le dépassement est autorisé si… » sont deux légendes OPPOSÉES
    sous deux images. Les souder produit un contresens ;
  · une entrée de SOMMAIRE et son numéro de page (3 cas), séparés par des
    points de conduite : « La nuit et la météo ····· 145 à 158 ».

Une règle qui fusionnerait tout ce qui se touche abîmerait donc 12 cas pour
en réparer 3. L'asymétrie des erreurs est la même que partout ici : recoller
à tort MÉLANGE deux textes et c'est irréparable ; ne pas recoller laisse un
chevauchement, qui est visible et réparable.

LE DISCRIMINANT, LU DANS LES MESURES
--------------------------------------
Les trois familles se séparent sans ambiguïté sur la géométrie :

    famille          fers gauches     disposition
    paragraphe coupé  IDENTIQUES      empilés (l'un SOUS l'autre)
    deux colonnes     DIFFÉRENTS      côte à côte (même bande verticale)
    sommaire          DIFFÉRENTS      côte à côte, très écartés

On exige donc les DEUX conditions, et elles suffisent :

  1. même FER GAUCHE — deux colonnes n'en partagent jamais ;
  2. EMPILÉS — le second commence sous le premier, et leurs bandes
     horizontales se recouvrent largement.

CE MODULE NE FUSIONNE JAMAIS EN CHAÎNE
----------------------------------------
Mesuré : l'union de 32 et 34 (page 2) AVALE le bloc 33, et celle de 44 et 45
avale le 46. Fusionner par transitivité souderait des listes entières.

Chaque bloc ne participe donc qu'à UNE fusion, et l'union ne doit contenir
aucun tiers. Une paire, jamais une chaîne.
"""
from __future__ import annotations

#: Écart maximal entre les deux fers gauches, en fraction de la hauteur de
#: ligne. Deux morceaux d'un même paragraphe partagent leur fer ; un scan ne
#: les pose pas au point près, d'où une tolérance — mais étroite.
#:
#: Mesuré sur les 3 pages : les paragraphes coupés ont 0,00 à 0,24 pt d'écart
#: de fer, les couples de colonnes 8 à 306 pt. Aucun recouvrement entre les
#: deux populations, donc aucun réglage délicat.
_FER_TOL_LIGNE = 0.5

#: Part de la largeur du plus étroit que les deux blocs doivent partager pour
#: être « l'un sous l'autre » et non « côte à côte ».
#:
#: 0,80 : deux morceaux d'un paragraphe occupent la même colonne, donc se
#: recouvrent presque totalement en x. Deux colonnes voisines ne se
#: recouvrent pas du tout.
_RECOUVREMENT_X_MIN = 0.80

#: Recouvrement en x qui vaut à lui seul preuve de « même colonne », quand le
#: fer gauche ne dit rien.
#:
#: Le cas qui l'exige : le texte à CONTOUR. Page 2, le texte s'enroule autour
#: de l'image du camion et chaque ligne commence à un x différent — 79,9 /
#: 73,4 / 38,9 pour trois morceaux du MÊME paragraphe. Le fer gauche y est
#: muet, mais l'un des blocs est entièrement compris dans la colonne de
#: l'autre : recouvrement mesuré à 1,00.
#:
#: 0,99 et non 0,80 : ce critère se substitue au fer gauche, qui est le
#: garde-fou principal contre la fusion de deux colonnes. Il doit donc être
#: quasi total pour ne pas l'affaiblir. Mesuré sur les couples de colonnes
#: voisines : recouvrement 0,00 — aucun risque de confusion.
_RECOUVREMENT_X_TOTAL = 0.99

#: Rapport minimal entre les largeurs de deux blocs pour qu'ils puissent être
#: deux morceaux du même paragraphe, quand seul le recouvrement les rapproche.
#:
#: Sans cette condition, le critère de contour avale n'importe quel petit bloc
#: pris dans la colonne d'un grand : un fragment de 20 pt inclus dans un
#: paragraphe de 104 pt donne un recouvrement de 1,00 tout en n'ayant rien à
#: voir avec lui.
#:
#: 0,60 : deux morceaux d'un même paragraphe occupent la même colonne, donc
#: des largeurs proches — sauf le dernier, qui peut être plus court. Mesuré
#: sur les paragraphes coupés réels : 0,84 et 0,81.
_LARGEURS_COMPARABLES = 0.60


def _largeur(b) -> float:
    return max(0.0, b[2] - b[0])


def _recouvrement_x(a, b) -> float:
    """Part de largeur commune, rapportée au PLUS ÉTROIT des deux.

    Rapportée au plus étroit, et non au plus large : un petit bloc entièrement
    dans la colonne d'un grand donne 100 %, ce qui est le fait qu'on cherche.
    """
    commun = min(a[2], b[2]) - max(a[0], b[0])
    court = min(_largeur(a), _largeur(b))
    return commun / court if court > 0 and commun > 0 else 0.0


def _hauteur_ligne(blocs) -> float:
    hs = []
    for bloc in blocs:
        for ligne in (bloc.get("lines") or []):
            bb = ligne.get("bbox")
            if bb and bb[3] > bb[1]:
                hs.append(bb[3] - bb[1])
    return sorted(hs)[len(hs) // 2] if hs else 0.0


def _empiles(a, b) -> bool:
    """`b` est-il SOUS `a`, dans la même colonne ?

    « Sous » se juge sur le HAUT des deux boîtes : deux blocs qui se
    chevauchent ont forcément leurs bas et hauts entremêlés, et comparer les
    bas ferait dépendre le verdict de la longueur du paragraphe.
    """
    return b[1] > a[1] and _recouvrement_x(a, b) >= _RECOUVREMENT_X_MIN


def _peut_fusionner(a, b, hl) -> bool:
    """Les deux blocs sont-ils deux morceaux d'un même paragraphe ?"""
    ba, bb = a.get("bbox"), b.get("bbox")
    if not ba or not bb or hl <= 0:
        return False
    # MÊME COLONNE, prouvée de l'une des deux façons — et il en faut DEUX,
    # parce qu'un seul critère laisse passer un cas réel :
    #
    #   · même FER GAUCHE : le cas ordinaire, celui d'un paragraphe justifié
    #     ou aligné à gauche ;
    #   · recouvrement en x TOTAL : le cas du texte à CONTOUR. Page 2, le
    #     texte s'enroule autour de l'image du camion, et chaque ligne
    #     commence à un x différent (79,9 / 73,4 / 38,9) — le fer gauche n'y
    #     veut plus rien dire. Mais l'un des blocs reste entièrement dans la
    #     colonne de l'autre (recouvrement 1,00), et c'est ce qui prouve
    #     qu'ils ne sont pas côte à côte.
    #
    # Aucune des deux ne rapproche des COLONNES voisines : elles ne partagent
    # ni leur fer, ni leur bande horizontale (recouvrement mesuré à 0,00).
    meme_fer = abs(ba[0] - bb[0]) <= _FER_TOL_LIGNE * hl
    if not meme_fer:
        if _recouvrement_x(ba, bb) < _RECOUVREMENT_X_TOTAL:
            return False
        # ...ET des largeurs COMPARABLES. Sans cette condition, le critère de
        # contour avale n'importe quel petit bloc pris dans la colonne d'un
        # grand : un fragment de 20 pt inclus dans un paragraphe de 104 pt
        # donne un recouvrement de 1,00 tout en n'ayant rien à voir avec lui.
        # Deux morceaux d'un même paragraphe ont, eux, presque la même
        # largeur — ils occupent la même colonne.
        etroit, large = sorted((_largeur(ba), _largeur(bb)))
        if large <= 0 or etroit / large < _LARGEURS_COMPARABLES:
            return False
    # EMPILÉS, et non côte à côte.
    haut, bas = (a, b) if ba[1] <= bb[1] else (b, a)
    return _empiles(haut["bbox"], bas["bbox"])


def _se_croisent(a, b) -> bool:
    l = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return l > 0 and h > 0


def _union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]),
            max(a[2], b[2]), max(a[3], b[3]))


def _avale_un_tiers(u, blocs, i, j) -> bool:
    """L'union engloberait-elle un bloc qui n'est ni `i` ni `j` ?

    LE GARDE-FOU CONTRE LA FUSION EN CHAÎNE. Mesuré page 2 : l'union de 32 et
    34 contient le bloc 33, celle de 44 et 45 contient le 46. Sans ce
    contrôle, une liste entière finirait en un seul bloc.
    """
    for k, autre in enumerate(blocs):
        if k in (i, j):
            continue
        bb = autre.get("bbox")
        if not bb:
            continue
        if (bb[0] >= u[0] and bb[1] >= u[1]
                and bb[2] <= u[2] and bb[3] <= u[3]):
            return True
    return False


def _fusionner_deux(a, b) -> dict:
    """Un seul bloc, portant les lignes des deux, dans l'ordre de lecture."""
    lignes = list(a.get("lines") or []) + list(b.get("lines") or [])
    lignes.sort(key=lambda l: (round((l.get("bbox") or [0, 0])[1], 1),
                               (l.get("bbox") or [0])[0]))
    neuf = dict(a)
    neuf["bbox"] = list(_union(a["bbox"], b["bbox"]))
    neuf["lines"] = lignes
    neuf["text"] = " ".join((l.get("text") or "").strip() for l in lignes
                            if (l.get("text") or "").strip())
    return neuf


def fusionner(blocs) -> list:
    """Recolle les paires de blocs qui sont un même paragraphe coupé.

    Ne touche à rien d'autre : un bloc sans partenaire ressort tel quel, et
    une page sans le défaut ressort identique. C'est ce qui rend l'opération
    sûre — elle ne peut pas dégrader une page qui va bien.
    """
    blocs = list(blocs or [])
    if len(blocs) < 2:
        return blocs
    hl = _hauteur_ligne(blocs)
    if hl <= 0:
        return blocs

    pris: set[int] = set()
    paires: list[tuple[int, int]] = []
    for i, a in enumerate(blocs):
        if i in pris:
            continue
        for j in range(i + 1, len(blocs)):
            if j in pris:
                continue
            b = blocs[j]
            if not _se_croisent(a["bbox"], b["bbox"]):
                continue
            if not _peut_fusionner(a, b, hl):
                continue
            if _avale_un_tiers(_union(a["bbox"], b["bbox"]), blocs, i, j):
                continue
            paires.append((i, j))
            pris.add(i)
            pris.add(j)
            break                      # UNE fusion par bloc, jamais de chaîne

    if not paires:
        return blocs
    fusionnes = {i: _fusionner_deux(blocs[i], blocs[j]) for i, j in paires}
    absorbes = {j for _, j in paires}
    return [fusionnes.get(k, bloc) for k, bloc in enumerate(blocs)
            if k not in absorbes]
