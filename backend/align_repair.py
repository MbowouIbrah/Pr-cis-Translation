"""Réparation déterministe de l'alignement id <-> traduction (post-traitement).

Contexte : le moteur stable traduit FRAGMENT PAR FRAGMENT. Sur les pages à mise
en page complexe (listes numérotées + formules inline éclatées en multiples
fragments), le modèle non-raisonnant « compacte » parfois N fragments en N-1
traductions : tout glisse alors d'un cran (un numéro de question atterrit dans
la case d'une phrase, et inversement). C'est le décalage en cascade.

Ce module RÉPARE cette sortie SANS toucher au prompt ni au rendu. Idée clé :

  Les fragments dont le texte SOURCE est un pur nombre / symbole / jeton
  mathématique (« 2 », « n », « 2n », « O(k) »…) sont des ANCRES : leur
  traduction = eux-mêmes, ET ils jouent le rôle de BARRIÈRES. Le contenu d'un
  segment (entre deux ancres) ne doit jamais déborder sur le segment suivant.

  On aligne donc le flux du modèle sur la source segment par segment, en se
  resynchronisant sur chaque ancre. À l'intérieur d'un segment, on distribue les
  morceaux de prose du modèle, dans l'ordre, sur les positions de prose de la
  source. S'il en manque, on LAISSE DES CASES VIDES (pas de glissement) ; s'il y
  en a trop, le surplus est ignoré au moment de la resynchronisation sur l'ancre
  suivante. Aucune contamination d'un segment à l'autre.

Le rendu (paragraphes regroupés par paragraph_key) concatène puis refait couler
le texte : des cases vides à l'intérieur d'un groupe sont neutres, le segment
reste correct.
"""

import re

# Un fragment est une ANCRE si son texte entier est uniquement :
#   - un entier (1, 11, 2020) / décimal (3.14)   - un nombre + lettre (2n, 3x)
#   - une seule lettre (n, k, x)                  - un jeton type O(k), f(x)
#   - un petit symbole mathématique
_ANCHOR = re.compile(
    r'^\s*('
    r'[0-9]+([.,][0-9]+)?'
    r'|[0-9]+[a-zA-Z]'
    r'|[a-zA-Z]'
    r'|[a-zA-Z]\([a-zA-Z0-9]\)'
    r'|[+\-*/=<>%^]'
    r')\s*$'
)


def is_anchor(text: str) -> bool:
    """Vrai si `text` (une fois rogné) est un pur nombre/symbole/jeton math."""
    return bool(text) and _ANCHOR.match(text.strip()) is not None


def _norm(t: str) -> str:
    return (t or "").strip()


def repair_blocks(blocks):
    """Répare l'alignement d'une liste de blocs d'UNE page (ordre de lecture).
    Modifie `translated_text` en place. Retourne un dict de stats.

    `blocks` : liste de dicts ayant 'text' (source) et 'translated_text' (sortie
    modèle, déjà mappée par id)."""
    src = [_norm(b.get("text")) for b in blocks]
    tr = [_norm(b.get("translated_text")) for b in blocks]
    src_anchor = [is_anchor(s) for s in src]

    # Séquence de CONTENU du modèle (positions vides ignorées), taguée A/P.
    model_seq = []  # liste de (kind, value)
    for t in tr:
        if not t:
            continue
        model_seq.append(("A", t) if is_anchor(t) else ("P", t))

    out = [None] * len(blocks)
    mp = 0  # pointeur dans model_seq

    for i in range(len(blocks)):
        if src_anchor[i]:
            # Ancre source : forcée à elle-même + resynchronisation du pointeur
            # modèle juste APRÈS l'ancre modèle de même valeur (barrière de segment).
            out[i] = src[i]
            j = mp
            while j < len(model_seq) and not (
                model_seq[j][0] == "A" and model_seq[j][1] == src[i]
            ):
                j += 1
            mp = j + 1 if j < len(model_seq) else mp
        else:
            # Position de prose : prendre le prochain morceau de prose DU SEGMENT
            # courant (avant la prochaine ancre modèle). Sinon, laisser VIDE.
            if mp < len(model_seq) and model_seq[mp][0] == "P":
                out[i] = model_seq[mp][1]
                mp += 1
            else:
                out[i] = ""  # case vide : pas de glissement

    anchors_fixed = sum(
        1 for i in range(len(blocks)) if src_anchor[i] and out[i] != tr[i]
    )
    emptied = sum(1 for i in range(len(blocks)) if not src_anchor[i] and out[i] == "")
    changed = 0
    for i, b in enumerate(blocks):
        if out[i] != tr[i]:
            changed += 1
        b["translated_text"] = out[i]

    return {
        "blocks": len(blocks),
        "anchors": sum(src_anchor),
        "anchors_fixed": anchors_fixed,
        "prose_positions": sum(1 for a in src_anchor if not a),
        "prose_stream": sum(1 for k, _ in model_seq if k == "P"),
        "emptied": emptied,
        "changed": changed,
    }
