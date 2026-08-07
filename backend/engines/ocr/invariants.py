"""Ce qu'une structure correcte ne peut PAS contenir.

TROIS RÈGLES, POSÉES PAR L'UTILISATEUR
---------------------------------------
    « un mot ne peut pas appartenir à deux lignes et ne peut pas appartenir à
      deux paragraphes distincts ; de même un paragraphe ne peut pas contenir
      un autre paragraphe »

Elles ont une propriété rare et précieuse : **elles ne comportent aucun
seuil**. On ne demande pas « ce bloc est-il assez régulier ? » — question dont
la réponse dépend d'un nombre choisi sur les documents qu'on a sous la main.
On demande « ce mot est-il compté deux fois ? », dont la réponse est un fait.

C'est exactement le genre de contrôle qui manquait aux deux versions
précédentes. Le bilan du 28/07 en tire la leçon : leurs indicateurs mesuraient
des PIXELS (mots perdus, taux de superposition) et restaient bons sur une page
jugée mauvaise. Ceux-ci mesurent la COHÉRENCE de la structure, et une
structure incohérente est fausse quelle que soit sa jolie apparence.

POURQUOI C'EST UN MODULE DE PRODUCTION, ET NON UN TEST
-------------------------------------------------------
Un test vérifie des pages qu'on lui donne. Ces invariants doivent pouvoir être
mesurés sur N'IMPORTE QUELLE page, y compris celles qu'on n'a jamais vues —
c'est le seul moyen de savoir si un correctif tient ailleurs que sur le
document qui l'a inspiré.

L'aperçu s'en sert pour AFFICHER les violations : une erreur qu'on voit à sa
place vaut mieux qu'un compte en bas de page.

CE QUE CE MODULE NE FAIT PAS
-----------------------------
Il ne corrige rien. Il constate, il localise, il compte. Corriger demande de
savoir LAQUELLE des deux appartenances est la bonne — ce que le constat ne dit
pas.
"""
from __future__ import annotations

#: Deux boîtes qui se touchent par leur bord ne se « chevauchent » pas. Un
#: rendu place des cadres au point près ; exiger un recouvrement STRICTEMENT
#: positif ferait rougir des voisins parfaitement rangés.
#:
#: 0,5 pt = un demi-point typographique, très en deçà d'un glyphe (≈ 5 pt) :
#: aucun vrai chevauchement ne passe sous ce seuil. Ce n'est pas un réglage de
#: sensibilité, c'est la tolérance du zéro.
_EPS = 0.5


def _cle(bbox) -> tuple | None:
    """L'identité géométrique d'un mot : sa boîte, au dixième de point.

    On identifie un mot par sa POSITION et non par son texte : deux « de » sur
    la même page sont deux mots différents, et le même mot recopié dans deux
    lignes garde sa position. C'est précisément ce qu'on cherche à détecter.
    """
    if not bbox or len(bbox) < 4:
        return None
    return tuple(round(float(v), 1) for v in bbox[:4])


def _aire(bbox) -> float:
    x0, y0, x1, y1 = bbox[:4]
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _recouvrement(a, b) -> float:
    """L'aire commune à deux boîtes. Zéro si elles ne se touchent pas."""
    ax0, ay0, ax1, ay1 = a[:4]
    bx0, by0, bx1, by1 = b[:4]
    l = min(ax1, bx1) - max(ax0, bx0)
    h = min(ay1, by1) - max(ay0, by0)
    return l * h if (l > _EPS and h > _EPS) else 0.0


def _mots(porteur) -> list[dict]:
    """Les mots d'une ligne, quelle que soit la forme qu'elle porte.

    `runs` est ce que rend le moteur PDF ; `spans` ce que fournit un appelant
    qui construit ses lignes à la main. Lire une seule des deux clés ne lève
    aucune erreur — on obtient une liste vide et tout contrôle qui suit est
    faussé en silence.
    """
    return list(porteur.get("runs") or porteur.get("spans") or [])


def mot_dans_deux_lignes(lignes) -> list[dict]:
    """Les mots comptés dans plus d'une ligne.

    Un mot appartient à UNE ligne visuelle. S'il en occupe deux, c'est que le
    regroupement l'a dupliqué — et tout ce qui suit (paragraphes, cadres,
    traduction) le comptera deux fois.
    """
    vus: dict[tuple, list[int]] = {}
    for i, ligne in enumerate(lignes or []):
        for mot in _mots(ligne):
            k = _cle(mot.get("bbox"))
            if k is None:
                continue
            vus.setdefault(k, []).append(i)
    return [{"mot": k, "lignes": idx, "n": len(idx)}
            for k, idx in vus.items() if len(idx) > 1]


def mot_dans_deux_blocs(blocs) -> list[dict]:
    """Les mots comptés dans plus d'un paragraphe.

    Même faute, un étage plus haut. C'est la plus grave des trois : un mot
    partagé signifie que deux blocs revendiquent le même texte, donc qu'au
    moins un des deux est faux.
    """
    vus: dict[tuple, list[int]] = {}
    for i, bloc in enumerate(blocs or []):
        for ligne in (bloc.get("lines") or []):
            for mot in _mots(ligne):
                k = _cle(mot.get("bbox"))
                if k is None:
                    continue
                if not vus.get(k) or vus[k][-1] != i:
                    vus.setdefault(k, []).append(i)
    return [{"mot": k, "blocs": idx, "n": len(idx)}
            for k, idx in vus.items() if len(idx) > 1]


def bloc_dans_bloc(blocs) -> list[dict]:
    """Les paragraphes contenus dans un autre paragraphe.

    Un paragraphe est une PARTITION de la page : il en occupe une zone, et
    cette zone n'est celle de personne d'autre. Un bloc inclus dans un autre
    dit que la page a été découpée deux fois, à deux échelles.

    L'inclusion se juge sur l'aire commune rapportée au PLUS PETIT des deux :
    un petit bloc entièrement dans un grand donne 100 %, quelle que soit la
    taille du grand. Comparer à l'aire du grand ferait passer une inclusion
    parfaite pour un détail négligeable.
    """
    out = []
    for i, a in enumerate(blocs or []):
        for j, b in enumerate(blocs or []):
            if i >= j:
                continue
            ba, bb = a.get("bbox"), b.get("bbox")
            if not ba or not bb:
                continue
            commun = _recouvrement(ba, bb)
            if commun <= 0:
                continue
            petit = min(_aire(ba), _aire(bb))
            if petit <= 0:
                continue
            part = commun / petit
            if part >= 0.90:            # l'un est DANS l'autre
                out.append({"blocs": (i, j), "part": part, "type": "inclusion"})
    return out


def blocs_qui_se_croisent(blocs) -> list[dict]:
    """Les paragraphes qui se chevauchent SANS que l'un contienne l'autre.

    Distinct de l'inclusion, et le défaut est différent : deux blocs qui se
    croisent en travers (le cas des cadres « à cheval » de l'aperçu réel)
    viennent de lignes mal formées, pas d'un double découpage.

    On les compte à part pour ne pas confondre deux causes dans un seul
    chiffre — un total qui mélange deux défauts ne dit pas lequel on a corrigé.
    """
    out = []
    for i, a in enumerate(blocs or []):
        for j, b in enumerate(blocs or []):
            if i >= j:
                continue
            ba, bb = a.get("bbox"), b.get("bbox")
            if not ba or not bb:
                continue
            commun = _recouvrement(ba, bb)
            if commun <= 0:
                continue
            petit = min(_aire(ba), _aire(bb))
            if petit <= 0 or (commun / petit) >= 0.90:
                continue                # c'est une inclusion, comptée ailleurs
            out.append({"blocs": (i, j), "part": commun / petit,
                        "type": "croisement"})
    return out


def controler(lignes=(), blocs=()) -> dict:
    """Tous les invariants d'un coup. `conforme` est vrai si aucun n'est violé.

    Rend aussi les violations elles-mêmes : un compte dit qu'il y a un
    problème, la liste dit OÙ, et c'est la liste qui permet de le corriger.
    """
    d_lignes = mot_dans_deux_lignes(lignes)
    d_blocs = mot_dans_deux_blocs(blocs)
    inclus = bloc_dans_bloc(blocs)
    croises = blocs_qui_se_croisent(blocs)
    return {
        "conforme": not (d_lignes or d_blocs or inclus or croises),
        "mot_deux_lignes": d_lignes,
        "mot_deux_blocs": d_blocs,
        "bloc_dans_bloc": inclus,
        "blocs_croises": croises,
        "comptes": {
            "mot_deux_lignes": len(d_lignes),
            "mot_deux_blocs": len(d_blocs),
            "bloc_dans_bloc": len(inclus),
            "blocs_croises": len(croises),
        },
    }


def blocs_fautifs(rapport) -> set[int]:
    """Les indices des blocs impliqués dans au moins une violation.

    Sert à les PEINDRE différemment dans l'aperçu : voir la faute à sa place
    vaut mieux que lire un total en bas de page.
    """
    fautifs: set[int] = set()
    for v in rapport.get("bloc_dans_bloc", ()):
        fautifs.update(v["blocs"])
    for v in rapport.get("blocs_croises", ()):
        fautifs.update(v["blocs"])
    for v in rapport.get("mot_deux_blocs", ()):
        fautifs.update(v["blocs"])
    return fautifs
