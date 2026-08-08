"""L'APERÇU : montrer ce que le moteur a compris, sans rien réécrire.

POURQUOI LE SCAN RESTE VISIBLE DESSOUS
---------------------------------------
Une page reconstruite à blanc montre ce que le moteur a TROUVÉ, mais pas ce
qu'il a MANQUÉ. Or c'est le manque qui condamne une détection : un paragraphe
non vu ne laisse aucune trace sur une page blanche, alors qu'il saute aux yeux
quand on voit le scan sans son cadre.

On dessine donc PAR-DESSUS le scan intact. Rien n'est effacé, rien n'est
réécrit — c'est l'étape d'identification, et elle s'arrête là.

CE QU'ON DESSINE, ET POURQUOI DEUX COULEURS
--------------------------------------------
Les blocs RETENUS et les blocs ÉCARTÉS sont tous les deux montrés. Ne montrer
que les retenus reviendrait à juger la détection sur ce qu'elle affirme, en
cachant ce qu'elle refuse — or c'est là que se logent les deux erreurs, et
elles ne coûtent pas la même chose :

  · un cadre autour d'un DESSIN  ->  destructif plus tard (on effacera) ;
  · un texte SANS cadre          ->  restera en langue source, lisible.

Les voir séparément est la seule façon de régler le tri à l'œil, ce qui est le
mode de jugement retenu : les indicateurs chiffrés de la v1 étaient bons
(0,2 % de mots perdus) sur une page jugée mauvaise. Ils mesurent des pixels ;
l'œil juge une page.
"""
from __future__ import annotations

import fitz

#: Bleu — un bloc RETENU comme paragraphe réel.
_RETENU = (0.10, 0.45, 0.95)
#: Orange — un bloc ÉCARTÉ par le tri. Volontairement voyant : c'est ce qu'on
#: vient inspecter.
_ECARTE = (0.95, 0.55, 0.10)
#: Gris — la ligne, sous le bloc. Fine, pour ne pas masquer le texte du scan.
_LIGNE = (0.55, 0.55, 0.55)


def _cadre(page, bbox, couleur, epaisseur=0.5, marge=0.0):
    x0, y0, x1, y1 = bbox
    r = fitz.Rect(x0 - marge, y0 - marge, x1 + marge, y1 + marge)
    if r.is_empty or r.is_infinite:
        return
    page.draw_rect(r, color=couleur, width=epaisseur)


def _lignes_du_bloc(bloc):
    """Les boîtes des lignes d'un paragraphe, dans l'ordre de lecture."""
    out = []
    for ligne in (bloc.get("lines") or []):
        bb = ligne.get("bbox")
        if bb and len(bb) >= 4 and bb[2] > bb[0] and bb[3] > bb[1]:
            out.append(list(bb[:4]))
    return out


def _contour(page, bloc, couleur, epaisseur, mise_en_page=None):
    """Le contour du paragraphe, EN ESCALIER plutôt qu'en rectangle.

    POURQUOI L'ESCALIER
    --------------------
    Un rectangle englobant revendique du vide : la dernière ligne d'un
    paragraphe est presque toujours plus courte que les autres, et le cadre
    couvre pourtant la largeur entière. À l'écran, deux paragraphes voisins
    semblent alors se toucher là où leur ENCRE ne se touche pas — c'est
    précisément la confusion signalée sur l'aperçu.

    Le contour en escalier passe par les sommets de chaque ligne : il épouse
    l'étendue réelle du texte, exactement comme le fait le moteur PDF pour ses
    conteneurs élargis.

    ON RÉUTILISE `_draw_stair_outline` DU MOTEUR PDF, on ne le recopie pas.
    Il gère déjà la frontière au milieu de l'écart entre deux lignes, sans
    quoi le tracé se recouvre et s'auto-intersecte. Le dupliquer donnerait
    deux implémentations qui divergent — c'est la règle du moteur-bibliothèque.

    Repli sur le rectangle si le bloc n'a pas de lignes exploitables : mieux
    vaut un cadre grossier que pas de cadre.
    """
    lignes = _lignes_du_bloc(bloc)
    if mise_en_page is not None and len(lignes) >= 2:
        try:
            mise_en_page._draw_stair_outline(page, lignes, couleur,
                                             width=epaisseur)
            return
        except Exception:
            pass                      # repli : le rectangle vaut mieux que rien
    bb = bloc.get("bbox")
    if bb:
        _cadre(page, bb, couleur, epaisseur=epaisseur)


def _etiquette(page, bbox, texte, couleur):
    """Un petit numéro au coin du bloc, posé SUR un fond plein.

    Sans fond, un chiffre sombre posé sur du texte scanné est illisible — et
    une étiquette qu'on ne peut pas lire ne sert à rien.
    """
    x0, y0, _, _ = bbox
    largeur = 5.5 * len(texte) + 4
    fond = fitz.Rect(x0, max(0, y0 - 9), x0 + largeur, max(9, y0))
    page.draw_rect(fond, color=couleur, fill=couleur, width=0)
    page.insert_text((x0 + 2, max(7, y0 - 2)), texte, fontsize=7,
                     fontname="helv", color=(1, 1, 1))


def dessiner(page, retenus=(), ecartes=(), lignes=(), montrer_lignes=True,
             mise_en_page=None):
    """Dessine la détection sur la page. Rend le compte de ce qui a été posé.

    `mise_en_page` est le moteur PDF, prêté pour tracer les contours en
    ESCALIER. Absent, on retombe sur des rectangles — l'aperçu reste lisible,
    il est seulement moins précis.
    """
    if montrer_lignes:
        for ln in lignes or ():
            bb = ln.get("bbox")
            if bb:
                _cadre(page, bb, _LIGNE, epaisseur=0.4)

    # AUCUNE MARGE, et c'est un correctif, pas un detail de rendu.
    #
    # Les cadres etaient dessines 1 pt PLUS GRANDS que les blocs. A l'ecran,
    # deux blocs separes de 1,5 pt apparaissaient donc colles, alors que le
    # calcul les voyait disjoints -- et il avait raison. Mesure sur 3 pages :
    #
    #     marge 0,0 pt  ->   6 paires se touchent   (les vraies)
    #     marge 1,0 pt  ->  14 paires se touchent   (dont 8 fabriquees ici)
    #
    # L'utilisateur voyait donc des defauts que l'audit ne signalait pas, pour
    # la seule raison que l'aperçu et l'audit ne parlaient pas de la meme
    # geometrie. Un aperçu qui ment sur ce qu'il montre invalide le jugement a
    # l'oeil, qui est le mode de jugement retenu ici.
    for i, bloc in enumerate(ecartes or (), 1):
        bb = bloc.get("bbox")
        if bb:
            _contour(page, bloc, _ECARTE, 0.5, mise_en_page)
            _etiquette(page, bb, f"x{i}", _ECARTE)

    for i, bloc in enumerate(retenus or (), 1):
        bb = bloc.get("bbox")
        if bb:
            _contour(page, bloc, _RETENU, 0.7, mise_en_page)
            _etiquette(page, bb, str(i), _RETENU)

    return {"retenus": len(retenus or ()), "ecartes": len(ecartes or ()),
            "lignes": len(lignes or ())}


def legende(page):
    """La légende, en bas de page, sur fond blanc.

    Un aperçu qu'on relit trois jours plus tard sans savoir ce que veut dire
    l'orange ne vaut rien.
    """
    h = page.rect.height
    fond = fitz.Rect(8, h - 26, 320, h - 6)
    page.draw_rect(fond, color=(0.85, 0.85, 0.85), fill=(1, 1, 1), width=0.5)
    page.draw_rect(fitz.Rect(14, h - 21, 24, h - 13), color=_RETENU,
                   width=1.6)
    page.insert_text((28, h - 14), "paragraphe retenu", fontsize=7,
                     fontname="helv", color=(0.2, 0.2, 0.2))
    page.draw_rect(fitz.Rect(126, h - 21, 136, h - 13), color=_ECARTE,
                   width=1.0)
    page.insert_text((140, h - 14), "ecarte", fontsize=7,
                     fontname="helv", color=(0.2, 0.2, 0.2))
    page.draw_rect(fitz.Rect(186, h - 21, 196, h - 13), color=_LIGNE,
                   width=0.4)
    page.insert_text((200, h - 14), "ligne detectee", fontsize=7,
                     fontname="helv", color=(0.2, 0.2, 0.2))
