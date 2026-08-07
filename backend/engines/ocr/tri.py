"""Ce qu'on retient comme paragraphe, et ce qu'on laisse tranquille.

L'ASYMÉTRIE QUI FIXE TOUT
--------------------------
Les deux erreurs ne coûtent pas la même chose, et c'est ce qui décide de
chaque règle de ce module :

  · RETENIR à tort un bloc qui n'en est pas un  ->  on effacera une partie du
    dessin et on peindra du charabia dessus. DESTRUCTIF ;
  · ÉCARTER à tort un vrai bloc de texte        ->  ce texte reste en langue
    source, à sa place, parfaitement lisible.

Le second se voit et se corrige ; le premier détruit. **En cas de doute, on ne
touche pas.** C'est la règle de l'utilisateur, appliquée à la structure.

CE QUE CE MODULE ÉCARTE, ET SUR QUELLE MESURE
-----------------------------------------------
Il n'écarte QUE des débris de lecture, jamais du texte douteux. Relevé sur les
blocs réellement impliqués dans une violation d'invariant (Code de la Route,
3 pages) :

    '9', '-', 'si', '9', '—>', '-', '-', 'CL', 'de', 'et', '-', '-'

Ce sont des traits de dessin, des flèches et des fragments — pas des mots
qu'on renonce à traduire. Un titre court (« Sommaire », « Pages ») ou une cote
(« 07 à 52 ») ne leur ressemble en rien : ils portent des lettres et forment
un mot.

POURQUOI PAS DE SEUIL DE CONFIANCE ICI
----------------------------------------
La tentation serait d'écarter ce que l'OCR a mal lu. Mesuré en v1 : à 40 de
confiance « Ho » et « LA » passaient quand même, et remonter le seuil faisait
perdre de vrais titres. La confiance dit si un mot est BIEN LU, pas s'il est
du TEXTE — un trait de dessin peut être lu « avec confiance » comme « | ».

On juge donc sur ce que le bloc EST (sa forme, ses caractères), pas sur ce que
l'OCR pense en savoir.
"""
from __future__ import annotations

#: Nombre de caractères en dessous duquel un bloc d'UNE SEULE ligne n'est pas
#: un paragraphe mais un débris.
#:
#: 3 et non 5 : « Oui », « Non », « Fin », « Bus » sont des mots entiers, et un
#: tableau peut légitimement porter une cellule « 3 » ou « A 13 ». On ne
#: retire donc que ce qui ne peut PAS former un mot.
#:
#: La règle ne s'applique qu'aux blocs d'UNE ligne : trois lignes de deux
#: caractères sont une colonne de chiffres, pas un débris.
_CAR_MIN_LIGNE_SEULE = 3

#: Part minimale de caractères LISIBLES — lettres ou chiffres.
#:
#: Ce n'est pas un filtre de langue, c'est un filtre de BRUIT : un trait de
#: dessin lu par Tesseract donne « |_-, », « }{ », « ~~ », « —> ».
#:
#: LES CHIFFRES COMPTENT COMME DU TEXTE, et ce n'est pas un détail : mesurer
#: les seules LETTRES écartait « 12,50 EUR » à 38 %, donc tout barème, toute
#: colonne de dates, tout tableau de prix. Trouvé par le calcul en v2, pas à
#: la lecture.
_PART_LISIBLE_MIN = 0.5


def _texte(bloc) -> str:
    return (bloc.get("text") or "").strip()


def _part_lisible(texte: str) -> float:
    """Proportion de caractères qui sont des lettres ou des chiffres."""
    utiles = [c for c in texte if not c.isspace()]
    if not utiles:
        return 0.0
    return sum(1 for c in utiles if c.isalnum()) / len(utiles)


def _est_debris(bloc) -> str | None:
    """Pourquoi ce bloc est un débris, ou None s'il n'en est pas un.

    Rend la RAISON et non un booléen : un aperçu qui dit « écarté » sans dire
    pourquoi ne permet pas de juger si la règle a bien fait.
    """
    texte = _texte(bloc)
    lignes = bloc.get("lines") or []
    if not texte:
        return "vide"
    if len(lignes) <= 1 and len(texte) <= _CAR_MIN_LIGNE_SEULE:
        # SAUF s'il porte un CHIFFRE : « 532 » (numéro de page du sommaire),
        # « 3 », « 4 » (colonne « Points » d'un barème), « A 13 » (cote
        # d'autoroute) sont des textes réels que leur brièveté condamnerait.
        #
        # POURQUOI « ENTIÈREMENT NUMÉRIQUE » ET RIEN D'AUTRE — deux tentatives
        # mesurées avant celle-ci :
        #
        #   · `texte.isalnum()` gardait « ar », « ou », « de », « si »,
        #     « LUS » — des débris parfaitement alphabétiques. Violations
        #     31 -> 37 ;
        #   · « contient un chiffre » gardait « 4: », un débris de dessin, et
        #     écartait toujours « Oui ».
        #
        # Un bloc court ENTIÈREMENT numérique est une cellule de barème, un
        # numéro de page, une cote. Dès qu'un symbole s'y mêle (« 4: »), c'est
        # une lecture de trait.
        #
        # Le texte court ALPHABÉTIQUE (« Oui », « Bus ») reste écarté et c'est
        # assumé : isolé sur une ligne, il est presque toujours le fragment
        # d'une ligne voisine mal découpée. L'erreur n'est pas destructive —
        # ce mot reste lisible en langue source, à sa place.
        if not texte.replace(" ", "").isdigit():
            return f"une ligne de {len(texte)} caractère(s)"
    if _part_lisible(texte) < _PART_LISIBLE_MIN:
        # La part de lisible n'a de sens que sur un texte assez LONG pour
        # qu'une proportion veuille dire quelque chose. « E(B). » compte 2
        # lettres sur 5 (40 %) et se ferait écarter, alors que c'est une
        # catégorie de permis parfaitement réelle.
        #
        # En dessous de 8 caractères, une proportion se décide sur un ou deux
        # signes de ponctuation — ce n'est plus une mesure de bruit, c'est du
        # hasard. On exige alors seulement qu'il y ait AU MOINS une lettre ou
        # un chiffre.
        if len([c for c in texte if not c.isspace()]) >= 8:
            return (f"{_part_lisible(texte):.0%} de caractères lisibles "
                    f"(< {_PART_LISIBLE_MIN:.0%})")
        if not any(c.isalnum() for c in texte):
            return "aucune lettre ni chiffre"
    return None


def trier(blocs):
    """Sépare les blocs en (retenus, écartés).

    Les écartés portent une clé `_raison`, lue par l'aperçu. On ne les jette
    pas : ils restent visibles à l'écran, en orange, pour qu'on puisse vérifier
    que la règle n'a pas emporté du vrai texte. Un tri qu'on ne peut pas
    inspecter ne se règle pas.
    """
    retenus, ecartes = [], []
    for bloc in blocs or []:
        raison = _est_debris(bloc)
        if raison is None:
            retenus.append(bloc)
        else:
            bloc = dict(bloc)
            bloc["_raison"] = raison
            ecartes.append(bloc)
    return retenus, ecartes
