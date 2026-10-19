"""
pdf_engine_v2.reflow — Coulée du texte traduit dans le conteneur (Étape reflow).

Prend les SEGMENTS traduits d'un paragraphe (chacun : texte + style + police
`fitz.Font` déjà résolue) et les COULE dans le polygone `container_lines` (les
cadres orange de l'Étape D), en respectant STRICTEMENT, à chaque ligne, le bord
gauche et le bord droit de la bande à ce niveau — donc sans débordement ni
chevauchement.

Le conteneur a une HAUTEUR FIXE (il s'étend à droite, pas vers le bas). Si le
texte traduit ne tient pas, on applique une CASCADE d'ajustements bornés, du
moins au plus intrusif :

    tracking (resserrement inter-lettres) → taille de police → interligne

et, à chaque ligne, une CÉSURE syllabique (pyphen) pour limiter le crénelage.

Ce module ne peint rien : il renvoie des lignes de « runs placés »
(texte, x, baseline, police, taille, couleur, sx) que le moteur peindra.
"""

import re

try:
    import pyphen
except Exception:                       # pyphen absent : pas de césure (dégradé)
    pyphen = None


# ── Résolution glyphe-par-glyphe (police embarquée sinon repli complet) ──────
# `fonts` = liste de (police, couverture) où `couverture` est un ENSEMBLE de
# caractères que la police rend FIDÈLEMENT, ou None = couverture universelle
# (police de repli base-14). NB : on n'utilise PAS has_glyph()/valid_codepoints()
# — un sous-ensemble embarqué les renvoie « présents » pour des glyphes dont le
# contour a été RETIRÉ au subsetting (rendus vides). La seule couverture fiable
# est l'ensemble des caractères réellement DESSINÉS dans le PDF source.
def glyph_font(fonts, ch):
    """Première police dont la couverture contient `ch` (ou universelle) ; sinon
    la dernière (repli). Les espaces passent partout."""
    ws = ch.isspace()
    for f, cover in fonts:
        if cover is None or ws or ch in cover:
            return f
    return fonts[-1][0]


def text_width(text, fonts, size, sx=1.0):
    """Largeur de `text` mesurée caractère par caractère avec la police qui
    couvre chaque glyphe (× `sx`)."""
    w = 0.0
    for ch in text:
        f = glyph_font(fonts, ch)
        try:
            w += f.text_length(ch, fontsize=size)
        except Exception:
            pass
    return w * sx


# ── Tokenisation : segments -> mots porteurs de style ────────────────────────
def build_tokens(segments):
    """segments : liste de {text, fonts:[fitz.Font,…], size, color}.
    Retourne une liste de tokens-mots : {text, fonts, size, color, space_before}.
    Les espaces (internes/aux frontières) deviennent le drapeau `space_before`
    du mot suivant → la coulée décide où tombent les retours à la ligne."""
    tokens = []
    pending_space = False
    for seg in segments:
        fonts = seg.get("fonts") or ([seg["font"]] if seg.get("font") else [])
        size = seg.get("size", 0) or 0
        color = seg.get("color", (0, 0, 0))
        underline = bool(seg.get("underline"))
        lsp = seg.get("lsp") or 0.0
        # Deux segments À LETTRES ESPACÉES adjacents (changement de style dans
        # un titre : « CARL » maigre + « SHAN » gras) = frontière de mot.
        if lsp and tokens and tokens[-1].get("lsp"):
            pending_space = True
        for part in re.split(r"(\s+)", seg.get("text", "")):
            if part == "":
                continue
            if part.isspace():
                pending_space = True
            else:
                tokens.append({"text": part, "fonts": fonts, "size": size,
                               "color": color, "underline": underline,
                               "space_before": pending_space, "lsp": lsp,
                               "rise": seg.get("rise", 0.0) or 0.0,
                               "synth_bold": seg.get("synth_bold")})
                pending_space = False
    return tokens


# ── Géométrie du conteneur en escalier ───────────────────────────────────────
def _bounds_at(container_lines, y0, y1):
    """[gauche, droite] utilisables pour une ligne occupant [y0, y1].
    Intersection CONSERVATRICE (max des gauches, min des droites) des bandes qui
    chevauchent verticalement → la ligne reste à l'intérieur de toutes, ce qui
    gère l'enroulement en L autour d'un encart sans jamais déborder."""
    lefts, rights = [], []
    for b in container_lines:
        lx0, lty, tgt, lby = b[0], b[1], b[2], b[3]
        if lby <= y0 or lty >= y1:
            continue
        lefts.append(lx0)
        rights.append(tgt)
    if not lefts:                       # aucune bande : prend la plus proche
        c = 0.5 * (y0 + y1)
        best = min(container_lines, key=lambda b: abs(0.5 * (b[1] + b[3]) - c))
        return best[0], best[2]
    return max(lefts), min(rights)


# ── Césure syllabique ────────────────────────────────────────────────────────
_HYPH_CACHE = {}


def _hyphenator(lang):
    if pyphen is None:
        return None
    if lang not in _HYPH_CACHE:
        try:
            _HYPH_CACHE[lang] = pyphen.Pyphen(lang=lang)
        except Exception:
            _HYPH_CACHE[lang] = None
    return _HYPH_CACHE[lang]


def _hyphen_split(word, fonts, size, sx, avail, lang):
    """Tente de couper `word` pour que « préfixe- » tienne dans `avail`.
    Retourne (prefixe_avec_trait, reste) ou None si aucune coupe ne convient.
    On ne coupe pas les mots courts (< 5 lettres) ni les jetons non alphabétiques
    (nombres, symboles : ce sont des ancres immuables)."""
    dic = _hyphenator(lang)
    if dic is None or len(word) < 5 or not word.isalpha():
        return None
    # Ne JAMAIS couper un mot à Majuscule initiale (nom propre : « Florida ») ni
    # tout en capitales (titre / acronyme : « INCROYABLES »). Règle typographique
    # standard et GÉNÉRALE (aucun calage sur un document) : une césure manquée ne
    # fait qu'écourter une ligne, alors qu'une mauvaise césure (« Flori-da »,
    # « IN-CROYABLES ») est un défaut visible.
    if word[0].isupper():
        return None
    positions = dic.positions(word)             # indices de coupe possibles
    best = None
    for p in positions:
        # Règle typographique (générale, aucun calage) : au moins 3 lettres de
        # CHAQUE côté de la coupe. Mesuré sur 2 222 césures des deux documents
        # de référence : les 27 fautives (« don-né », « socié-té »,
        # « demande-ra ») laissaient toutes un reste de 2 lettres — un moignon
        # muet en tête de ligne. Une césure manquée écourte une ligne ; une
        # césure fautive se voit.
        if p < 3 or len(word) - p < 3:
            continue
        head = word[:p] + "-"
        if text_width(head, fonts, size, sx) <= avail:
            best = (head, word[p:])             # garde la plus longue qui tient
        else:
            break
    return best


# ── Coupe des jetons insécables trop larges (URL, code, référence) ───────────
_HARD_SEPS = "/.-_=&?#:"


def _hard_split(word, fonts, size, sx, avail):
    """Coupe un jeton qui ne tiendra JAMAIS dans la largeur disponible (URL,
    chemin, référence) à un séparateur interne — sans ajouter de trait de
    césure (couper « …/driver-training/ » est l'usage typographique des URL).
    À défaut de séparateur utile, coupe au dernier caractère qui tient
    (dernier recours : mieux qu'un débordement dans le bloc voisin).
    Retourne (tete, reste) ou None si même un caractère ne tient pas."""
    best = None
    for i, ch in enumerate(word):
        if ch in _HARD_SEPS and 0 < i < len(word) - 1:
            head = word[:i + 1]
            if text_width(head, fonts, size, sx) <= avail:
                best = (head, word[i + 1:])
            else:
                break
    if best:
        return best
    n = 0                                   # repli : coupe au caractère
    w = 0.0
    for i, ch in enumerate(word):
        cw = text_width(ch, fonts, size, sx)
        if w + cw > avail:
            break
        w += cw
        n = i + 1
    if 0 < n < len(word):
        return word[:n], word[n:]
    return None


# ── Coulée gloutonne d'une passe (paramètres fixés) ──────────────────────────
def _layout(tokens, container_lines, first_baseline, bottom, size_scale, pitch,
            sx, lang, size_est, align="left"):
    """Une passe de coulée avec des paramètres FIXES. Les lignes sont posées à
    des baselines ANCRÉES sur l'original : `baseline(i) = first_baseline +
    i·pitch` (à interligne d'origine, `first_baseline` = baseline de la 1re ligne
    source → le texte reste à sa position verticale ; corrige la dérive qui
    décalait le texte sous ses soulignements). Retourne (lignes, hauteur, déborde).
    Chaque ligne : {"baseline", "runs":[{text,x,fonts,size,color,sx,underline}]}."""
    lines = []
    idx = 0

    def baseline_of(i):
        return first_baseline + i * pitch

    def bounds(i):
        b = baseline_of(i)
        return _bounds_at(container_lines, b - 0.8 * size_est, b + 0.25 * size_est)

    left, right = bounds(0)
    x = left
    cur = []

    def close_line(last=False):
        nonlocal cur, idx, left, right, x
        if cur:
            if align == "center":               # recentre la ligne dans [left,right]
                off = (right - x) / 2.0
                if off > 0.5:
                    for rr in cur:
                        rr["x"] += off
            elif align == "right":              # ferre la ligne sur `right`
                off = right - x                 # (symétrique exact du centrage)
                if off > 0.5:
                    for rr in cur:
                        rr["x"] += off
            elif align == "justify" and not last:
                # JUSTIFICATION : répartit le blanc restant sur les espaces
                # inter-mots (jamais la dernière ligne d'un paragraphe). On ne
                # justifie pas une ligne trop courte (slack important → rivières) :
                # elle est probablement suivie d'une coupe volontaire.
                gap_idx = [i for i in range(1, len(cur)) if cur[i].get("sb")]
                slack = right - x
                line_w = max(1.0, right - left)
                if gap_idx and 0.5 < slack < 0.30 * line_w:
                    per = slack / len(gap_idx)
                    gaps = set(gap_idx)
                    shift = 0.0
                    for i, rr in enumerate(cur):
                        if i in gaps:
                            shift += per
                        rr["x"] += shift
            lines.append({"baseline": baseline_of(idx), "runs": cur})
        idx += 1
        left, right = bounds(idx)
        x = left
        cur = []

    queue = list(tokens)
    while queue:
        t = queue.pop(0)
        size = (t["size"] or 0) * size_scale
        # Tracking d'un TITRE À LETTRES ESPACÉES reconstruit au balisage :
        # l'avance de chaque glyphe est majorée de `lsp × corps` (l'espace de
        # mot aussi) → le rendu retrouve l'espacement de l'original.
        lsp = (t.get("lsp") or 0.0) * size
        w = text_width(t["text"], t["fonts"], size, sx)
        if lsp and len(t["text"]) > 1:
            w += lsp * (len(t["text"]) - 1)
        # Espace de MOT d'un titre à lettres espacées : espace + 2× tracking
        # (l'original montre des blancs de mots nettement plus larges que le
        # pas des lettres — un seul tracking rendait les mots quasi collés).
        sp = ((text_width(" ", t["fonts"], size, sx) + 2.0 * lsp)
              if (cur and t["space_before"]) else 0.0)

        if cur and x + sp + w > right + 0.5:
            # CHAÎNE SOUDÉE : un jeton SANS espace de tête est la suite du même
            # mot typographique (satellite exposant/indice « L₁ », changement de
            # style intra-mot). Le couper là, c'est casser le mot — l'indice
            # « 1 » de hb p239 partait seul en bord de ligne. On ramène toute la
            # chaîne collée en fin de `cur` dans la file, et le mot entier passe
            # à la ligne. Garde-fou : si la chaîne EST toute la ligne (mot plus
            # large que la ligne), on retombe sur l'ancien comportement.
            if not t["space_before"] and not t.get("_dech"):
                chaine = []
                while cur and not cur[-1].get("sb"):
                    chaine.insert(0, cur.pop())
                if cur:
                    chaine.insert(0, cur.pop())          # la tête (avec espace)
                if cur and all(r.get("_tok") for r in chaine):
                    toks = [{**r["_tok"], "space_before": False}
                            for r in chaine] + [{**t, "space_before": False}]
                    toks[0]["space_before"] = True       # tête de mot
                    queue[0:0] = toks
                    tete = chaine[0]
                    x = tete["x"] - (text_width(" ", tete["fonts"],
                                                tete["size"], tete["sx"])
                                     if tete.get("sb") else 0.0)
                    close_line()
                    continue
                for r in chaine:                         # la chaîne = la ligne
                    cur.append(r)                        # entière : restaurer
            # Ne tient pas : tenter une césure du mot pour finir la ligne.
            avail = right - (x + sp)
            piece = (None if lsp
                     else _hyphen_split(t["text"], t["fonts"], size, sx,
                                        avail, lang))
            if piece:
                head, tail = piece
                cur.append({"text": head, "x": x + sp, "fonts": t["fonts"],
                            "size": size, "color": t["color"], "sx": sx,
                            "underline": t.get("underline"), "sb": sp > 0,
                            "synth_bold": t.get("synth_bold")})
                # Avancer x jusqu'à la fin du préfixe césuré : close_line()
                # mesure le slack de justification/centrage sur x — sans cette
                # avance, le slack est surestimé de (espace + préfixe) et la
                # répartition pousse la ligne AU-DELÀ du bord droit.
                x += sp + text_width(head, t["fonts"], size, sx)
                queue.insert(0, {**t, "text": tail, "space_before": False,
                                 "_dech": True})
                close_line()
                continue
            close_line()
            # replace le mot en début de nouvelle ligne (sans espace de tête)
            queue.insert(0, {**t, "space_before": False, "_dech": True})
            continue

        if not cur and w > (right - x) + 0.5:
            # Jeton seul plus large que la ligne entière (URL, référence) : la
            # césure syllabique ne s'applique pas → coupe aux séparateurs
            # internes, sinon au caractère (jamais de débordement).
            piece = _hard_split(t["text"], t["fonts"], size, sx, right - x)
            if piece:
                head, tail = piece
                cur.append({"text": head, "x": x, "fonts": t["fonts"],
                            "size": size, "color": t["color"], "sx": sx,
                            "underline": t.get("underline"), "sb": False,
                            "synth_bold": t.get("synth_bold")})
                x += text_width(head, t["fonts"], size, sx)
                queue.insert(0, {**t, "text": tail, "space_before": False})
                close_line()
                continue

        x += sp
        cur.append({"text": t["text"], "x": x, "fonts": t["fonts"],
                    "size": size, "color": t["color"], "sx": sx,
                    "underline": t.get("underline"), "sb": sp > 0,
                    "synth_bold": t.get("synth_bold"),
                    "lsp": t.get("lsp") or 0.0,
                    # décalage de baseline (satellite), à l'échelle du rendu
                    "rise": (t.get("rise") or 0.0) * size_scale,
                    "_tok": t})
        x += w

    close_line(last=True)              # dernière ligne : jamais justifiée
    n = len(lines)
    height_used = n * pitch
    last_bottom = baseline_of(n - 1) + 0.25 * size_est if n else first_baseline
    overflow = last_bottom > (bottom + 0.35 * pitch)
    return lines, height_used, overflow


# ── Cascade d'ajustement : première passe qui tient ──────────────────────────
# (pitch_scale, size_scale, sx) — du moins au plus intrusif. Marges infimes pour
# préserver la lisibilité (règle 3 : « toujours dans des marges infimes »).
_CASCADE = [
    (1.00, 1.00, 1.00),
    (1.00, 1.00, 0.98),                 # tracking léger
    (0.98, 1.00, 0.97),                 # + interligne
    (0.97, 0.98, 0.96),                 # + taille
    (0.95, 0.96, 0.96),
    (0.93, 0.94, 0.95),
    (0.90, 0.92, 0.95),
    (0.88, 0.90, 0.94),                 # plancher (lisibilité)
]


def natural_lines(segments, container_lines, lang="fr_FR", first_baseline=None):
    """Nombre de lignes que produit la coulée SANS aucune compression (échelle
    1.0). Sert à estimer le BESOIN vertical naturel d'un paragraphe traduit
    (→ combien de lignes de plus qu'à l'origine), en amont du flux vertical."""
    if not container_lines:
        return 0
    tokens = build_tokens(segments)
    top = min(b[1] for b in container_lines)
    bottom = max(b[3] for b in container_lines)
    orig_pitch = _orig_pitch(container_lines, segments)
    size_est = max((s.get("size", 0) or 0 for s in segments), default=10.0)
    if first_baseline is None:
        first_baseline = top + 0.78 * orig_pitch
    lines, _h, _ov = _layout(tokens, container_lines, first_baseline, bottom,
                             1.0, orig_pitch, 1.0, lang, size_est)
    return len(lines)


# Paliers de FORCE-FIT (au-delà de la cascade normale) : compression uniforme
# croissante jusqu'à un plancher, employée UNIQUEMENT quand le flux vertical n'a
# pas pu accorder assez de hauteur à un bloc (texte contraint dans une boîte
# fixe : panneau, cellule, ou segment borné). Garantit « jamais de dépassement »
# au prix d'une réduction — préférable à un chevauchement.
_FORCE_FIT = [0.86, 0.82, 0.78, 0.74, 0.70, 0.66, 0.62, 0.58, 0.54, 0.50, 0.46]


def reflow_paragraph(segments, container_lines, lang="fr_FR",
                     first_baseline=None, align="left", force_fit=False,
                     fixed_scale=None):
    """Coule les `segments` traduits dans `container_lines`.

    `first_baseline` : baseline (y) de la 1re ligne d'origine → les lignes
    reflowées sont posées à cette position (interligne d'origine), gardant le
    texte à sa place verticale. À défaut, déduit du haut du conteneur.

    `force_fit` : si True et que même le niveau le plus serré de la cascade
    déborde, on POURSUIT la compression (paliers `_FORCE_FIT`) jusqu'à ce que le
    texte tienne dans la hauteur du conteneur — garantie anti-collision pour un
    bloc à hauteur imposée (panneau, cellule, segment borné).

    Retourne un dict :
      { "lines":[…], "fitted":bool, "level":int, "pitch_scale","size_scale","sx",
        "n_lines":int }
    `lines` = lignes de runs placés (cf. `_layout`). `fitted` False = le texte
    déborde même au niveau le plus serré (à signaler / re-traduire plus court)."""
    if not container_lines:
        return {"lines": [], "fitted": False, "level": -1, "n_lines": 0,
                "pitch_scale": 1.0, "size_scale": 1.0, "sx": 1.0}
    tokens = build_tokens(segments)
    top = min(b[1] for b in container_lines)
    bottom = max(b[3] for b in container_lines)
    orig_pitch = _orig_pitch(container_lines, segments)
    size_est = max((s.get("size", 0) or 0 for s in segments), default=10.0)
    if first_baseline is None:
        first_baseline = top + 0.78 * orig_pitch

    # Échelle IMPOSÉE (compression uniforme d'un segment saturé, décidée par le
    # flux vertical) : on rend directement à cette échelle, sans passer par la
    # cascade → tous les blocs du segment partagent la même taille (cohérence
    # visuelle). Le force-fit reste disponible en dernier recours.
    if fixed_scale is not None:
        ss = max(0.3, min(1.0, fixed_scale))
        lines, h, overflow = _layout(tokens, container_lines, first_baseline,
                                     bottom, ss, orig_pitch * ss, 0.98, lang,
                                     size_est * ss, align)
        best = {"lines": lines, "fitted": not overflow, "level": -2,
                "pitch_scale": ss, "size_scale": ss, "sx": 0.98,
                "n_lines": len(lines)}
        if not overflow or not force_fit:
            return best
        for k, s2 in enumerate(_FORCE_FIT):
            if s2 >= ss:
                continue
            lines, h, overflow = _layout(tokens, container_lines, first_baseline,
                                         bottom, s2, orig_pitch * s2, 0.96, lang,
                                         size_est * s2, align)
            best = {"lines": lines, "fitted": not overflow, "level": -3,
                    "pitch_scale": s2, "size_scale": s2, "sx": 0.96,
                    "n_lines": len(lines)}
            if not overflow:
                break
        return best

    best = None
    for lvl, (ps, ss, sx) in enumerate(_CASCADE):
        pitch = orig_pitch * ps
        lines, h, overflow = _layout(tokens, container_lines, first_baseline,
                                     bottom, ss, pitch, sx, lang, size_est * ss,
                                     align)
        best = {"lines": lines, "fitted": not overflow, "level": lvl,
                "pitch_scale": ps, "size_scale": ss, "sx": sx,
                "n_lines": len(lines)}
        if not overflow:
            return best

    if force_fit:                        # garantie de tenue : compression accrue
        for k, ss in enumerate(_FORCE_FIT):
            pitch = orig_pitch * ss
            lines, h, overflow = _layout(tokens, container_lines, first_baseline,
                                         bottom, ss, pitch, 0.96, lang,
                                         size_est * ss, align)
            best = {"lines": lines, "fitted": not overflow,
                    "level": len(_CASCADE) + k, "pitch_scale": ss,
                    "size_scale": ss, "sx": 0.96, "n_lines": len(lines)}
            if not overflow:
                return best
    return best                          # rien ne tient : renvoie le plus serré


def _orig_pitch(container_lines, segments):
    """Interligne d'origine : pas vertical médian entre bandes successives,
    sinon repli sur la taille dominante des segments."""
    tops = sorted(b[1] for b in container_lines)
    gaps = [b - a for a, b in zip(tops, tops[1:]) if b - a > 0.5]
    if gaps:
        gaps.sort()
        return gaps[len(gaps) // 2]
    sizes = [s.get("size", 0) or 0 for s in segments]
    return (max(sizes) if sizes else 10.0) * 1.2
