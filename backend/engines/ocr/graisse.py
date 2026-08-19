"""LA GRAISSE : reconnaître le gras, qui porte la structure du document.

⚠ LA V1 A ÉCHOUÉ ICI, ET IL FAUT SAVOIR POURQUOI
--------------------------------------------------
Elle a essayé la **densité d'encre** — la part de pixels sombres dans la
boîte — et s'est trompée : 0,340 pour du gras contre 0,337 pour du maigre.
On ne refait donc pas la même mesure en espérant mieux.

Trois candidates ont été éprouvées contre 509 mots gras de vérité, sur deux
documents indépendants (banc du 10/08) :

    mesure           gras (médiane)   maigre (médiane)   sépare ?
    ──────────────────────────────────────────────────────────────
    densité d'encre     0,373            0,268           mal
    NOIRCEUR            0,878            0,878           **pas du tout**
    ÉPAISSEUR DE TRAIT  4,368            2,042           **franchement**

La densité échoue parce qu'elle mélange deux choses : un mot gras a des
traits épais, mais un mot maigre SERRÉ remplit autant sa boîte. La noirceur
échoue parce qu'un scan binarise : gras et maigre sont aussi noirs l'un que
l'autre.

CE QUI MARCHE : L'ÉPAISSEUR DU TRAIT
--------------------------------------
Le rapport `aire d'encre / pixels de bord` mesure combien de pixels d'épais
fait un trait — c'est la définition même du gras, et rien d'autre ne
l'imite. Un trait de 2 pixels a autant de bord que d'intérieur ; un trait de
6 pixels a trois fois plus d'intérieur que de bord.

⚠ RAPPORTÉE À LA PAGE, JAMAIS EN VALEUR ABSOLUE. Les deux documents donnent
4,368 et 1,795 pour du gras — un seuil fixe serait faux au premier changement
de police ou de résolution. Rapportée à la médiane de la page, la mesure
CONVERGE :

                    DSH      mv21
    gras           1,485    1,520     quartiles [1,33 - 1,67]
    maigre         0,998    0,991     quartiles [0,96 - 1,04]

Les médianes coïncident à 2 % près alors que les valeurs absolues variaient
du simple au double. C'est la même démarche que `_gw` pour les colonnes et
`_TRAIT_LARGEUR_GW` pour les flèches : on mesure un RAPPORT, pas des points.
"""
from __future__ import annotations

#: Épaisseur de trait, en multiple de la médiane de la page, à partir de
#: laquelle un mot est gras.
#:
#: Balayé sur les DEUX documents ensemble — jamais sur un seul, c'est
#: l'erreur qui a fait périmer `_ECART_MEME_LIGNE` :
#:
#:     seuil   gras trouvés   faux positifs
#:     ──────────────────────────────────────
#:      1,10      91 %           8,3 %
#:      1,15      90 %           5,3 %
#:     **1,20**   **89 %**       **4,1 %**   <- le coude
#:      1,25      82 %           3,5 %
#:      1,40      68 %           2,8 %
#:      1,60      34 %           2,7 %
#:
#: 1,20 est le coude : au-delà on perd des gras sans gagner en précision (le
#: taux de faux stagne à 2,7 %), en deçà les faux doublent.
_GRAS_MEDIANE = 1.20

#: Pixels d'encre minimaux pour que la mesure ait un sens. En dessous, le
#: rapport aire/bord est dominé par la forme du glyphe, pas par son trait.
_ENCRE_MIN = 12


def _epaisseur(gris, bbox, echelle) -> float | None:
    """Combien de pixels d'épais fait le trait de ce mot ?

    `aire d'encre / pixels de bord`. Un trait fin a autant de bord que
    d'intérieur (rapport ~1) ; un trait épais a beaucoup plus d'intérieur.

    Le seuil de binarisation est LOCAL — le milieu entre le pixel le plus
    sombre et le plus clair de la boîte. Un seuil global serait faux sur une
    page dont l'éclairage varie, ce qui est le cas de tout scan.
    """
    import numpy as np

    x0, y0, x1, y1 = (int(v * echelle) for v in bbox)
    sub = gris[max(0, y0):y1, max(0, x0):x1]
    if sub.size < _ENCRE_MIN:
        return None
    seuil = (int(sub.min()) + int(sub.max())) / 2.0
    encre = sub < seuil
    aire = int(encre.sum())
    if aire < 4:
        return None
    # Un pixel d'encre est AU BORD si l'un de ses quatre voisins ne l'est pas.
    bord = encre & ~(np.roll(encre, 1, 0) & np.roll(encre, -1, 0)
                     & np.roll(encre, 1, 1) & np.roll(encre, -1, 1))
    return aire / max(1, int(bord.sum()))


def marquer_le_gras(spans, image, echelle) -> None:
    """Pose `bold` et `flags` sur les spans dont le trait est épais.

    Modifie les spans en place — ils viennent d'être construits par
    `span_depuis_mot`, qui les crée tous maigres faute d'information.

    Ne touche RIEN si l'image manque ou si la page ne porte pas assez de
    texte pour qu'une médiane veuille dire quelque chose.
    """
    if not spans or image is None:
        return
    import numpy as np

    gris = np.asarray(image.convert("L"))
    mesures = {}
    for i, s in enumerate(spans):
        e = _epaisseur(gris, s["bbox"], echelle)
        if e is not None:
            mesures[i] = e
    if len(mesures) < 8:            # trop peu pour une médiane honnête
        return
    valeurs = sorted(mesures.values())
    mediane = valeurs[len(valeurs) // 2]
    if mediane <= 0:
        return
    for i, e in mesures.items():
        if e / mediane >= _GRAS_MEDIANE:
            # ⚠ ON POSE `_gras`, PAS `bold` NI `flags`, et c'est mesuré.
            #
            # `_group_paragraphs` du moteur PDF COUPE un paragraphe quand
            # `bold` change d'une ligne à l'autre (« titre gras vs corps »).
            # Marquer le gras cassait donc le regroupement : la référence
            # passait de 5 à 7 défauts, et `blocs_colles` de 2 à 5.
            #
            # La graisse est une propriété de RENDU, pas de structure. Elle
            # vit donc à côté, comme `_corps` : le regroupement garde la
            # géométrie qu'il avait, le rendu lira `_gras`.
            spans[i]["_gras"] = True
