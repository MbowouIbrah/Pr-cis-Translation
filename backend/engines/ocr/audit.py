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
            if hl > 0:
                d = _distance(ba, bb)
                if d < _BLANC_MIN_LIGNE * hl:
                    defauts.append({
                        "type": "blocs_colles",
                        "blocs": (i, j),
                        "mesure": f"{d:.2f} pt de blanc "
                                  f"({d / hl:.2f} x ligne, exigé "
                                  f"{_BLANC_MIN_LIGNE})",
                        "texte": (a.get("text") or "")[:30] + " // "
                                 + (b.get("text") or "")[:30],
                    })

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
