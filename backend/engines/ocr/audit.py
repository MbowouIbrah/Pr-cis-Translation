"""L'AUDIT CHIRURGICAL : un bloc encadre son paragraphe, et rien d'autre.

CE QUE CE MODULE EXIGE, ET EN QUOI IL DIFFÈRE DES INVARIANTS
---------------------------------------------------------------
`invariants.py` répond à « la structure est-elle cohérente ? » — un mot
compté deux fois, un bloc dans un bloc. C'est nécessaire, et **insuffisant** :
le sur-découpage des lignes justifiées (6 blocs pour un paragraphe de 4
lignes) n'y produisait **aucune** violation, alors que c'était le défaut le
plus visible à l'œil.

Ici la règle est plus dure, et c'est celle qui a été demandée :

    « chaque bloc doit délimiter au millimètre près uniquement le paragraphe
      concerné, sans empiéter ni toucher un autre bloc de paragraphe, ni être
      inscrit dans un paragraphe »

Trois exigences distinctes, donc trois familles de défauts :

  1. NE PAS SE CHEVAUCHER — deux blocs ne partagent aucune surface ;
  2. NE PAS SE TOUCHER — il reste un blanc entre eux ;
  3. ÊTRE AJUSTÉ — le cadre colle à l'encre qu'il contient, sans marge morte.

DEUX ANGLES MORTS, SIGNALÉS À L'ŒIL ET AJOUTÉS ENSUITE
--------------------------------------------------------
Les trois familles ci-dessus jugent les CADRES, deux à deux. Deux défauts
réels leur échappaient, tous deux repérés sur une image et non par le compte —
c'est la troisième fois que cela arrive, et cela vaut d'être écrit.

**4. LIGNE PARTAGÉE.** Mesuré page 3 : la ligne « 3 mètres et d'intervalles
de » ressort en trois blocs — `'3'` (352,3), `'mètres'` dans #10 (361,7) et
`"d'intervalles"` (396,0), tous à la même baseline. Aucune règle ne disait que
**deux blocs ne peuvent pas se partager une ligne de texte**, alors que c'est
un fait de mise en page et non un réglage : une ligne appartient à un
paragraphe et à un seul.

C'est le contrôle qui manquait le plus, parce que c'est celui qui remonte à la
CAUSE. Les chevauchements de cadres en sont la conséquence : le bout de ligne
expulsé retombe forcément dans le cadre du paragraphe qu'il a quitté.

**5. LIGNE AMPUTÉE.** #10 revendique 352,6 → 437,5, mais sa deuxième ligne ne
contient que `'mètres'` (361,7 → 378,2). Le cadre est pourtant « ajusté » au
sens de `cadre_lache`, qui ne regarde que l'enveloppe : il colle bien à l'encre
de sa PREMIÈRE ligne. Entre les deux il enferme du vide — précisément le vide
où logent `'3'` et `"d'intervalles"`.

Juger l'enveloppe ne suffit donc pas ; il faut juger LIGNE PAR LIGNE. Mais
attention à ce qu'on y mesure : la LONGUEUR ne prouve rien (une ligne est
courte pour vingt raisons légitimes), c'est le FER GAUCHE qui trahit. Voir
`_FER_DECALE_LIGNE`.

TOLÉRANCE ZÉRO, VRAIMENT
-------------------------
`invariants.py` accepte 0,5 pt de recouvrement (pour ne pas rougir sur deux
cadres qui se rangent au point près) et ne signale une inclusion qu'au-delà de
90 % de la petite surface. Ces deux tolérances cachent précisément ce qu'on
cherche maintenant.

Ici : **tout recouvrement strictement positif est un défaut**, et toute
inclusion l'est quelle que soit sa proportion.

CE MODULE NE CORRIGE RIEN
--------------------------
Il constate, localise, classe et compte. Corriger demande de savoir LAQUELLE
des deux géométries est la bonne — ce que le constat ne dit pas.
"""
from __future__ import annotations

#: Blanc minimal exigé entre deux blocs voisins, en fraction de la hauteur de
#: ligne médiane. Deux paragraphes distincts sont séparés par un interligne ;
#: en deçà, les cadres « se touchent » au sens de la consigne.
#:
#: 0,25 : le quart d'une ligne. Assez pour distinguer deux cadres à l'œil,
#: assez peu pour ne pas exiger un blanc que la mise en page ne donne pas.
_BLANC_MIN_LIGNE = 0.25

#: Marge morte tolérée entre le cadre d'un bloc et l'encre qu'il contient, en
#: fraction de la hauteur de ligne médiane. Au-delà, le cadre est « lâche » :
#: il revendique de la place qui n'est pas à lui, et c'est ainsi qu'il finit
#: par toucher son voisin.
_MARGE_MORTE_MAX = 0.35

#: Recouvrement vertical à partir duquel deux lignes sont LA MÊME ligne de
#: texte, en fraction de la plus courte. Ce n'est pas un réglage de
#: sensibilité : deux lignes successives d'un paragraphe ne se recouvrent
#: quasiment pas (l'interligne les sépare), tandis que deux morceaux d'une même
#: ligne se recouvrent presque totalement. Mesuré page 3 : `'3'` et `'mètres'`
#: se recouvrent à 94 %, deux lignes voisines du même bloc à 0 %.
_MEME_LIGNE_REC = 0.60

#: Écart horizontal maximal entre deux morceaux d'UNE MÊME ligne, en fraction
#: de la hauteur de ligne.
#:
#: SANS CE SECOND CRITÈRE LA RÈGLE EST FAUSSE, ET C'EST L'ERREUR QUE J'AI
#: FAITE. Le seul recouvrement vertical attrape aussi toutes les COLONNES : sur
#: une page à deux colonnes, chaque ligne de gauche partage sa bande avec une
#: ligne de droite. Mesuré : 61 « défauts » dont l'immense majorité étaient des
#: colonnes parfaitement légitimes (`'SOMMAIRE' | 'LES AUTRES PERMIS'`).
#:
#: Ce qui sépare les deux cas est l'ÉCART, et il ne se recouvre pas :
#:
#:     vrais defauts   `'mètres' | '3'`  1,33 x  ·  `"l'appelle"`  1,81 x
#:     vraies colonnes                   13 x à 55 x
#:
#: 4,0 : un blanc de mot vaut ~1 x la hauteur de ligne, une gouttière de
#: colonne en vaut plus de 10. On se place largement entre les deux.
#:
#: ⚠ CE SEUIL S'EST PÉRIMÉ, ET C'EST LA LEÇON DE LA MESURE DU 08/08.
#: Les correctifs suivants (alignement des lignes OCR, bridage des corps,
#: recollage justifié) ont RESSERRÉ la géométrie. Les gouttières mesurées à
#: 13-55 x la hauteur de ligne sont retombées à 0,5-2,6 x — sous le seuil.
#: Les deux populations qui « ne se recouvraient pas » se recouvrent
#: désormais entièrement, et 9 colonnes légitimes étaient accusées.
#:
#: **Un seuil calibré sur une mesure survit rarement au correctif suivant.**
#: C'est pourquoi le garde-fou qui décide vraiment est en dessous, et qu'il
#: est SANS SEUIL : voir `_lignes_partagees`.
_ECART_MEME_LIGNE = 4.0

#: Décalage du FER GAUCHE toléré pour une ligne, en fraction de la hauteur de
#: ligne.
#:
#: C'EST LE FER GAUCHE QUI TRAHIT UN CREUX, PAS LA LONGUEUR — seconde erreur
#: que j'ai faite ici. Juger la ligne COURTE attrape tout ce qu'un document
#: contient de légitime : les listes (`'Les voies'` au milieu d'une
#: énumération), les titres de première ligne, et surtout toute ligne qui
#: FINIT une phrase (`'des élèves.'`, `'aptitudes des candidats.'`). Mesuré :
#: 17 signalements, dont 11 parfaitement normaux.
#:
#: Une ligne courte commence quand même au FER du paragraphe. Une ligne
#: AMPUTÉE À GAUCHE, elle, ne le peut pas — il manque son début :
#:
#:     amputées   `"l'appelle"` g=16,56  ·  `'mètres'` g=9,12
#:     normales   fer à g=0,00 à 1,20, toutes lignes de tous les blocs
#:
#: 1,5 : au-dessus du bruit de scan (mesuré ≤ 1,2 pt pour hl = 5,04), très en
#: dessous d'une amputation (≥ 9 pt). Un alinéa de première ligne est plus
#: grand, mais il ne concerne que la ligne 0 — qu'on exclut pour cela.
_FER_DECALE_LIGNE = 1.5


#: Épaisseur du trait de cadre dans l'aperçu, en points. Un trait est CENTRÉ
#: sur le bord : il deborde donc de la moitié de son épaisseur de chaque côté.
#:
#: Deux cadres séparés de moins de `_TRAIT` voient leurs traits se toucher à
#: l'écran, quelles que soient leurs boîtes. Juger la boîte seule laissait
#: donc passer des défauts parfaitement visibles — l'aperçu et l'audit
#: doivent parler de la MÊME géométrie, sinon le jugement à l'œil ne vaut rien.
#:
#: Doit rester accordé à `apercu._cadre` (0,7 pt pour un bloc retenu).
_TRAIT = 0.7


def _aire(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _recouvrement(a, b):
    """(largeur, hauteur, aire) de l'intersection. Zéro si disjoints."""
    l = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if l <= 0 or h <= 0:
        return (0.0, 0.0, 0.0)
    return (l, h, l * h)


def _distance(a, b) -> float:
    """Le blanc qui sépare deux boîtes. 0 si elles se touchent ou se
    chevauchent."""
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    if dx > 0 and dy > 0:
        return (dx * dx + dy * dy) ** 0.5     # diagonale
    return max(dx, dy)


def _mots(bloc):
    out = []
    for ligne in (bloc.get("lines") or []):
        out.extend(ligne.get("runs") or ligne.get("spans") or [])
    return out


def _boite_encre(bloc):
    """La boîte de l'ENCRE réellement contenue, et non celle revendiquée."""
    mots = _mots(bloc)
    if not mots:
        return None
    return (min(m["bbox"][0] for m in mots), min(m["bbox"][1] for m in mots),
            max(m["bbox"][2] for m in mots), max(m["bbox"][3] for m in mots))


def _hauteur_ligne(blocs) -> float:
    hs = []
    for b in blocs:
        for ligne in (b.get("lines") or []):
            bb = ligne.get("bbox")
            if bb and bb[3] > bb[1]:
                hs.append(bb[3] - bb[1])
    return sorted(hs)[len(hs) // 2] if hs else 0.0


def _lignes_de(bloc):
    """Les lignes d'un bloc, avec leur boîte. Ignore celles qui n'en ont pas."""
    return [l for l in (bloc.get("lines") or []) if l.get("bbox")]


def _meme_bande(a, b) -> float:
    """Part de recouvrement VERTICAL de deux lignes, sur la plus courte.

    C'est le seul critère qui distingue « deux morceaux d'une même ligne » de
    « deux lignes voisines » sans rien supposer de leur position horizontale :
    des morceaux côte à côte partagent leur bande, des lignes empilées non.
    """
    rec = min(a[3], b[3]) - max(a[1], b[1])
    court = min(a[3] - a[1], b[3] - b[1])
    return rec / court if court > 0 and rec > 0 else 0.0


def _lignes_partagees(blocs, hl) -> list[dict]:
    """Les LIGNES de texte revendiquées par deux blocs différents.

    Une ligne appartient à un paragraphe et à un seul. Deux blocs dont une
    ligne partage la bande verticale — et dont les encres se suivent
    horizontalement sans qu'un autre bloc s'intercale — sont deux morceaux
    d'une même ligne déchirée.

    C'est le contrôle qui remonte à la CAUSE : les chevauchements de cadres en
    découlent, puisque le morceau expulsé retombe dans le cadre qu'il a quitté.

    LE GARDE-FOU QUI DÉCIDE VRAIMENT, ET IL EST SANS SEUIL
    --------------------------------------------------------
    Deux blocs dont les CADRES SONT DISJOINTS ne peuvent pas se déchirer une
    ligne : chacun contient entièrement la sienne, aucun mot n'est revendiqué
    deux fois. Une ligne déchirée laisse forcément une trace dans les cadres —
    c'est le mécanisme même décrit plus haut, le morceau expulsé retombant
    dans le cadre qu'il a quitté.

    Mesuré sur 3 pages : les 9 couples signalés avaient **tous** leurs cadres
    disjoints (recouvrement en x de -2,4 à -12,96 pt) et **zéro mot commun**.
    C'étaient neuf colonnes correctement séparées, accusées à tort parce que
    `_ECART_MEME_LIGNE` s'était périmé sous elles.

    POURQUOI PAS UN AUTRE DISCRIMINANT — MESURÉ ET REJETÉ. L'idée suivante
    était de compter les lignes APPARIÉES côte à côte : deux colonnes
    s'apparient sur toute leur hauteur, une ligne déchirée est un accident
    isolé. Éprouvé sur des cas synthétiques dont la vérité est connue :

      · rapporté au bloc le plus COURT  -> 1,00 dans les 4 cas, discrimine rien ;
      · rapporté au plus LONG           -> sépare bien la ligne arrachée (0,17)
        des colonnes égales (1,00), mais condamne les colonnes de hauteurs
        INÉGALES (0,33) — or c'est exactement `'Usagers' | 'Le conducteur'`,
        un titre court en regard d'une colonne longue. Cas réel, pas d'école.

    Le critère des cadres disjoints n'a pas ce défaut, et ne coûte aucun
    réglage.
    """
    if hl <= 0:
        return []
    plafond = _ECART_MEME_LIGNE * hl
    out = []
    for i, a in enumerate(blocs):
        for j in range(i + 1, len(blocs)):
            b = blocs[j]
            # CADRES DISJOINTS -> rien à partager. Voir plus haut.
            ba, bb = a.get("bbox"), b.get("bbox")
            if ba and bb and not (min(ba[2], bb[2]) - max(ba[0], bb[0]) > 0
                                  and min(ba[3], bb[3]) - max(ba[1], bb[1]) > 0):
                continue
            pire = None
            for la in _lignes_de(a):
                for lb in _lignes_de(b):
                    part = _meme_bande(la["bbox"], lb["bbox"])
                    if part < _MEME_LIGNE_REC:
                        continue
                    # Deux morceaux d'une MÊME ligne sont côte à côte, donc
                    # disjoints horizontalement. Deux lignes qui se recouvrent
                    # AUSSI en x sont un autre défaut (chevauchement), déjà
                    # compté : ne pas le compter deux fois.
                    ga, gb = la["bbox"], lb["bbox"]
                    if min(ga[2], gb[2]) - max(ga[0], gb[0]) > 0:
                        continue
                    ecart = max(ga[0], gb[0]) - min(ga[2], gb[2])
                    if ecart > plafond:       # une GOUTTIÈRE, pas un blanc
                        continue
                    # On garde le PLUS PETIT écart : c'est le couple le plus
                    # proche qui décide, pas celui qui se recouvre le mieux.
                    if pire is None or ecart < pire[1]:
                        pire = (part, ecart, la, lb)
            if pire:
                part, ecart, la, lb = pire
                out.append({
                    "type": "ligne_partagee",
                    "blocs": (i, j),
                    "mesure": f"{part:.0%} de bande commune, "
                              f"{ecart:.2f} pt d'ecart "
                              f"({ecart / hl:.2f} x ligne, max "
                              f"{_ECART_MEME_LIGNE})",
                    "texte": (la.get("text") or "")[:30] + " | "
                             + (lb.get("text") or "")[:30],
                })
    return out


def _ligne_amputee(bloc, i, hl) -> dict | None:
    """Une ligne du bloc commence-t-elle APRÈS le fer gauche du paragraphe ?

    `cadre_lache` juge l'ENVELOPPE : il compare le cadre à l'encre totale, et
    ne peut donc rien dire d'une ligne isolée au milieu. Ici on juge LIGNE PAR
    LIGNE, et on regarde le FER GAUCHE et lui seul.

    Ce que la longueur ne dit pas : une ligne peut être courte pour vingt
    raisons légitimes (fin de phrase, entrée de liste, titre). Mais toutes
    commencent au fer du paragraphe. Une ligne qui démarre en retrait a perdu
    son début — et ce début est ailleurs, en bloc séparé.

    On exclut la ligne 0 : un alinéa de première ligne est un retrait voulu.
    """
    lignes = _lignes_de(bloc)
    if len(lignes) < 2 or hl <= 0:
        return None
    fers = [l["bbox"][0] for l in lignes]
    # Le fer du paragraphe est le PLUS À GAUCHE : un retrait s'ajoute au fer,
    # il ne le déplace jamais vers la gauche.
    fer = min(fers)
    pire = None
    for k, lg in enumerate(lignes):
        if k == 0:                        # alinéa : retrait légitime
            continue
        d = lg["bbox"][0] - fer
        if d > _FER_DECALE_LIGNE * hl and (pire is None or d > pire[0]):
            pire = (d, k, lg)
    if not pire:
        return None
    d, k, lg = pire
    return {
        "type": "ligne_amputee",
        "blocs": (i,),
        "mesure": f"ligne {k} commence {d:.2f} pt apres le fer "
                  f"({d / hl:.2f} x ligne, max {_FER_DECALE_LIGNE})",
        "texte": (lg.get("text") or "")[:40],
    }


def auditer(blocs) -> dict:
    """Inventaire complet des blocs qui ne respectent pas la consigne.

    Rend `{defauts: [...], comptes: {...}, blocs_fautifs: {...}}`. Chaque
    défaut porte son TYPE, les blocs concernés, et la MESURE qui le prouve —
    un rapport sans chiffre ne permet pas de vérifier qu'un correctif a agi.
    """
    blocs = list(blocs or [])
    hl = _hauteur_ligne(blocs)
    defauts = []

    for i, a in enumerate(blocs):
        ba = a.get("bbox")
        if not ba:
            continue

        # ── 3. Le cadre colle-t-il à son encre ? ─────────────────────────
        enc = _boite_encre(a)
        if enc and hl > 0:
            marges = (enc[0] - ba[0], enc[1] - ba[1],
                      ba[2] - enc[2], ba[3] - enc[3])
            pire = max(marges)
            if pire > _MARGE_MORTE_MAX * hl:
                cote = ("gauche", "haut", "droite", "bas")[marges.index(pire)]
                defauts.append({
                    "type": "cadre_lache",
                    "blocs": (i,),
                    "mesure": f"{pire:.2f} pt de vide au {cote} "
                              f"({pire / hl:.2f} x ligne)",
                    "texte": (a.get("text") or "")[:40],
                })

        # ── 5. Une ligne est-elle amputée de son début ? ─────────────────
        ampute = _ligne_amputee(a, i, hl)
        if ampute:
            defauts.append(ampute)

        for j in range(i + 1, len(blocs)):
            b = blocs[j]
            bb = b.get("bbox")
            if not bb:
                continue
            l, h, aire = _recouvrement(ba, bb)

            # ── 1. Chevauchement, tolérance ZÉRO ─────────────────────
            if aire > 0:
                petit = min(_aire(ba), _aire(bb))
                part = aire / petit if petit > 0 else 1.0
                # Inclusion COMPLÈTE : un bloc entièrement dans l'autre.
                dedans = (ba[0] <= bb[0] and ba[1] <= bb[1]
                          and ba[2] >= bb[2] and ba[3] >= bb[3]) or \
                         (bb[0] <= ba[0] and bb[1] <= ba[1]
                          and bb[2] >= ba[2] and bb[3] >= ba[3])
                defauts.append({
                    "type": "bloc_dans_bloc" if dedans else "chevauchement",
                    "blocs": (i, j),
                    "mesure": f"{l:.2f} x {h:.2f} pt "
                              f"({part:.0%} du plus petit)",
                    "texte": (a.get("text") or "")[:30] + " // "
                             + (b.get("text") or "")[:30],
                })
                continue

            # ── 2. Se touchent-ils ? (aucun blanc entre eux) ─────────
            # Le blanc exigé doit rester SUPÉRIEUR à `_TRAIT` : en deçà, les
            # traits des deux cadres se rejoignent à l'écran et l'utilisateur
            # voit un défaut que l'audit ne signale pas. C'est exactement ce
            # qui s'est produit avec la marge de dessin de l'aperçu.
            if hl > 0:
                exige = max(_BLANC_MIN_LIGNE * hl, _TRAIT)
                d = _distance(ba, bb)
                if d < exige:
                    defauts.append({
                        "type": "blocs_colles",
                        "blocs": (i, j),
                        "mesure": f"{d:.2f} pt de blanc "
                                  f"({d / hl:.2f} x ligne, exigé "
                                  f"{exige:.2f} pt)",
                        "texte": (a.get("text") or "")[:30] + " // "
                                 + (b.get("text") or "")[:30],
                    })

    # ── 4. Deux blocs se partagent-ils une LIGNE de texte ? ─────────────
    defauts.extend(_lignes_partagees(blocs, hl))

    comptes: dict = {}
    fautifs: dict = {}
    for d in defauts:
        comptes[d["type"]] = comptes.get(d["type"], 0) + 1
        for k in d["blocs"]:
            fautifs.setdefault(k, []).append(d["type"])
    return {
        "conforme": not defauts,
        "defauts": defauts,
        "comptes": comptes,
        "blocs_fautifs": fautifs,
        "total": len(defauts),
        "hauteur_ligne": hl,
    }
