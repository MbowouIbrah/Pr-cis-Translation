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


def dessiner(page, retenus=(), ecartes=(), lignes=(), montrer_lignes=True):
    """Dessine la détection sur la page. Rend le compte de ce qui a été posé."""
    if montrer_lignes:
        for ln in lignes or ():
            bb = ln.get("bbox")
            if bb:
                _cadre(page, bb, _LIGNE, epaisseur=0.4)

    for i, bloc in enumerate(ecartes or (), 1):
        bb = bloc.get("bbox")
        if bb:
            _cadre(page, bb, _ECARTE, epaisseur=0.5, marge=1.0)
            _etiquette(page, bb, f"x{i}", _ECARTE)

    for i, bloc in enumerate(retenus or (), 1):
        bb = bloc.get("bbox")
        if bb:
            _cadre(page, bb, _RETENU, epaisseur=0.7, marge=1.0)
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
