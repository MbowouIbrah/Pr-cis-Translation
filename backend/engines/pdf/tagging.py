"""
pdf_engine_v2.tagging — Balisage par SEGMENT DE STYLE (méthode Word).

But : produire, pour chaque paragraphe extrait, un texte balisé
`[[0]]…[[/0]][[1]]…[[/1]]…` où chaque balise regroupe des runs CONSÉCUTIFS de
MÊME mise en forme (police, gras, italique, taille, couleur). C'est la même
sémantique que la traduction DOCX (`backend/docx_translator_engine.py`), sauf
qu'ici un « run Word » = un groupe de runs PDF de style identique — car un mot à
lettres espacées est éclaté en un run PAR LETTRE (voir « FOREWORD ») : les tagger
séparément serait absurde.

Le texte à l'intérieur des balises est reconstruit EXACTEMENT comme le champ
`paragraph["text"]` de l'extraction (mêmes espaces, même dé-césure) → en retirant
les balises on retrouve le texte du paragraphe. La seule chose que le balisage
ajoute, ce sont les FRONTIÈRES de style.

Ce module ne fait AUCUN rendu ni traduction : il prépare seulement le texte à
envoyer au traducteur et la table `segments` (style de chaque balise) qui servira
à la réinjection (reflow) une fois la traduction revenue.
"""

import re


# ── Signature de style d'un run (ce qui définit une frontière de balise) ──────
def style_sig(run):
    """Signature comparable du style visible d'un run.

    On normalise le nom de police (sans préfixe de sous-ensemble 'ABCDEF+'),
    et on arrondit taille/couleur pour éviter des frontières parasites dues à
    des écarts infimes. `bold`/`italic` viennent des flags de span PyMuPDF.
    NB : le soulignement n'est PAS un flag de police en PDF (c'est un trait
    dessiné à part) → non capté ici.
    """
    font = run.get("font") or ""
    clean = font.split("+")[-1] if "+" in font else font
    color = run.get("color") or [0, 0, 0]
    try:
        col = tuple(round(float(c), 3) for c in color)
    except Exception:
        col = (0.0, 0.0, 0.0)
    return (clean, bool(run.get("bold")), bool(run.get("italic")),
            round(float(run.get("size", 0) or 0), 1), col,
            bool(run.get("underline")),
            round(float(run.get("rise", 0) or 0), 1),
            bool(run.get("synth_bold")), run.get("synth_italic") or 0.0)


def _sig_to_meta(sig):
    """Signature -> dict de style lisible (pour la table `segments`)."""
    (font, bold, italic, size, color, underline, rise, synth_bold,
     synth_italic) = sig
    return {"font": font, "bold": bold, "italic": italic,
            "size": size, "color": list(color), "underline": underline,
            "rise": rise, "synth_bold": synth_bold,
            "synth_italic": synth_italic}


# ── Reconstruction du texte balisé d'un paragraphe ────────────────────────────
# On réplique la logique de composition du texte de l'extraction :
#   • au sein d'une ligne : espace insérée si l'écart dépasse 0.30×gw
#     (cf. engine `_make_text_line` / `_SPACE_FACTOR`) ;
#   • entre lignes : dé-césure d'un mot coupé, sinon espace
#     (cf. engine `_join_para_text`) ;
# tout en suivant le style de chaque fragment pour poser les frontières.
_SPACE_FACTOR = 0.30


_ISOLATED_RE = re.compile(r"^\S(?:\s+\S)+\s*$")   # « A D V I C E  A N D … »


def _axis(run):
    """Direction d'écriture d'un run (défaut : horizontale)."""
    d = run.get("dir") or [1, 0]
    return (float(d[0]), float(d[1]))


def _axis_extent(block):
    """Empan du bloc LE LONG de son axe d'écriture.

    Les bbox de l'extraction sont axis-aligned : pour un texte VERTICAL, l'empan
    d'écriture est la HAUTEUR de la bbox, pas sa largeur. Mesuré en x, un titre
    vertical (« TABLE OF CONTENTS » sur 196 pt) rendait 14 pt — la largeur du
    fût — d'où un tracking nul et un titre traduit rendu collé.
    """
    dx, dy = _axis(block[0])
    if abs(dy) > abs(dx):
        lo = min(r["bbox"][1] for r in block)
        hi = max(r["bbox"][3] for r in block)
    else:
        lo = min(r["bbox"][0] for r in block)
        hi = max(r["bbox"][2] for r in block)
    return hi - lo


def _axis_center(run, d):
    """Centre de la bbox d'un run PROJETÉ sur l'axe d'écriture — croissant dans
    le sens de lecture (y compris pour un axe descendant, ex. `dir` = (0,-1))."""
    cx = (run["bbox"][0] + run["bbox"][2]) / 2.0
    cy = (run["bbox"][1] + run["bbox"][3]) / 2.0
    return cx * d[0] + cy * d[1]


def _letterspaced_segments(runs, size):
    """Détecte une ligne À LETTRES ESPACÉES et RECONSTRUIT les mots. Deux
    structures existent dans les PDF réels :

      1. UN SEUL run contenant les glyphes isolés — la frontière de mot y est
         un DOUBLE espace (« A D V I C E␣␣A N D ») ; c'est l'écrasement
         `\\s{2,}` du balisage qui la détruisait ;
      2. UN RUN PAR GLYPHE — la frontière de mot est l'ESPACE DE TÊTE du run
         suivant (« D », « ␣B ») : les écarts géométriques, eux, sont uniformes
         (c'est le piège qui avait fait échouer la 1re tentative) ; à défaut,
         un SAUT net des pas entre glyphes trahit la frontière.

    Retourne des segments {sig, text (mots normaux), ls_width (largeur bbox du
    segment source — le moteur en déduit le tracking exact avec les vraies
    polices)} ou None si la ligne n'est pas à lettres espacées. Sans cette
    reconstruction, le traducteur reçoit « A D V I C E A N D … » et rend un
    texte collé (« CONSEILSETIDEESDE »)."""
    vis = [r for r in runs if (r.get("text") or "").strip()]
    if not vis:
        return None
    # La ligne est-elle globalement lettre-à-lettre ? (structure 1 ou 2)
    def isolated(rt):
        return bool(_ISOLATED_RE.match(rt.strip())) and len(rt.strip()) >= 3
    singles = sum(1 for r in vis if len((r.get("text") or "").strip()) == 1)
    monolith = sum(1 for r in vis if isolated(r.get("text") or ""))
    if singles < 0.8 * len(vis) and monolith == 0:
        return None
    total_letters = sum(len((r.get("text") or "").replace(" ", ""))
                        for r in vis)
    if total_letters < 2:
        return None

    segs = []
    i = 0
    while i < len(vis):
        sig = style_sig(vis[i])
        j = i
        while j < len(vis) and style_sig(vis[j]) == sig:
            j += 1
        block = vis[i:j]
        raw = [(r.get("text") or "") for r in block]
        strip = [t.strip() for t in raw]
        width = _axis_extent(block)

        # UNITÉS par glyphe (couvre les 3 formes rencontrées : mono-run
        # « C A R L », run-par-lettre ['C','A','R','L'], et MIXTE
        # ['C A R','L']). Chaque unité = (lettre, frontière_de_mot_avant) —
        # frontière = double espace INTERNE ou espace de TÊTE d'un run.
        units = None
        if sum(len(t) for t in strip) >= 2:
            units = []
            for bi, r in enumerate(block):
                rt, st = raw[bi], strip[bi]
                lead = rt[:1].isspace() and bi > 0
                if len(st) == 1:
                    units.append([st, lead])
                elif isolated(rt) or " " not in st and len(st) <= 2:
                    for ci, chunk in enumerate(re.split(r"\s{2,}", st)):
                        letters = chunk.split()
                        for li, ch in enumerate(letters):
                            if len(ch) != 1:
                                units = None
                                break
                            units.append([ch, li == 0 and (ci > 0 or lead)])
                        if units is None:
                            break
                else:
                    units = None
                if units is None:
                    break
        if units and len(units) >= 2:
            # Repli géométrique : uniquement pour les runs-par-lettre sans
            # aucune frontière textuelle (pas d'espaces de tête).
            if (not any(b for _c, b in units) and len(block) >= 3
                    and all(len(t) == 1 for t in strip)):
                d = _axis(block[0])
                centers = [_axis_center(r, d) for r in block]
                steps = [centers[k + 1] - centers[k]
                         for k in range(len(block) - 1)]
                srt = sorted(steps)
                cut = None
                for k in range(len(srt) - 1):
                    if srt[k] > 0 and srt[k + 1] / srt[k] > 1.30:
                        cut = 0.5 * (srt[k] + srt[k + 1])
                if cut is not None:
                    for k, st_ in enumerate(steps):
                        if st_ > cut:
                            units[k + 1][1] = True
            words, word = [], units[0][0]
            for ch, boundary in units[1:]:
                if boundary:
                    words.append(word)
                    word = ch
                else:
                    word += ch
            words.append(word)
            segs.append({"sig": sig, "text": " ".join(w for w in words if w),
                         "ls_width": width})
        else:
            # Bloc non lettre-à-lettre au sein de la ligne : texte brut.
            segs.append({"sig": sig, "text": " ".join(t for t in strip if t)})
        # Changement de style au sein d'une ligne à lettres espacées = frontière
        # de mot (« C A R L » maigre + « S H A N » gras) → espace de jointure.
        if len(segs) >= 2 and segs[-1]["text"]:
            segs[-1]["text"] = " " + segs[-1]["text"]
        i = j
    _strip_edges(segs)
    return segs or None


def _line_segments(line):
    """Liste de {sig, text} pour UNE ligne, espaces internes replaçées comme à
    l'extraction, puis rognée en tête/queue."""
    runs = line.get("runs", [])
    if not runs:
        return []
    size = max((r.get("size", 0) or 0 for r in runs), default=10.0)
    ls = _letterspaced_segments(runs, size)
    if ls is not None:
        return ls
    gw = line.get("gw") or 1.0
    space_gap = _SPACE_FACTOR * gw
    segs = []

    def emit(sig, s):
        if not s:
            return
        if segs and segs[-1]["sig"] == sig:
            segs[-1]["text"] += s
        else:
            segs.append({"sig": sig, "text": s})

    last_char = ""
    for i, r in enumerate(runs):
        sig = style_sig(r)
        txt = r.get("text", "")
        if i > 0:
            gap = r["bbox"][0] - runs[i - 1]["bbox"][2]
            if (gap > space_gap and last_char and last_char != " "
                    and not txt.startswith(" ")):
                emit(style_sig(runs[i - 1]), " ")
                last_char = " "
        emit(sig, txt)
        if txt:
            last_char = txt[-1]
    _strip_edges(segs)
    return segs


def _strip_edges(segs):
    """Rogne l'espace de tête du 1er segment et de queue du dernier (comme le
    `.strip()` appliqué à chaque ligne dans `_join_para_text`)."""
    while segs and not segs[0]["text"].lstrip():
        segs.pop(0)
    if segs:
        segs[0]["text"] = segs[0]["text"].lstrip()
    while segs and not segs[-1]["text"].rstrip():
        segs.pop()
    if segs:
        segs[-1]["text"] = segs[-1]["text"].rstrip()


def paragraph_segments(para):
    """Retourne la liste des segments {sig, text} d'un paragraphe, fusionnés par
    style sur l'ENSEMBLE du paragraphe (lignes jointes, dé-césure comprise)."""
    lines = para.get("lines")
    # Un `text_line` isolé (paragraphe mono-ligne non groupé) : on l'enveloppe.
    if lines is None:
        lines = [para]
    segs = []
    for line in lines:
        ls = _line_segments(line)
        if not ls:
            continue
        if not segs:
            segs = ls
            continue
        # Jointure inter-lignes : dé-césure ou espace (cf. `_join_para_text`).
        prev = segs[-1]
        pt = prev["text"]
        first_char = ls[0]["text"][:1]
        if (pt.endswith("-") and len(pt) >= 2 and pt[-2].isalpha()
                and first_char.islower()):
            prev["text"] = pt[:-1]                 # dé-césure : retire le trait
        else:
            prev["text"] = pt + " "                # sinon espace de jointure
        for s in ls:
            if segs and segs[-1]["sig"] == s["sig"]:
                segs[-1]["text"] += s["text"]
            else:
                segs.append(dict(s))
    # Écrasement des espaces multiples À L'INTÉRIEUR d'un segment (le global
    # `\s{2,}->  ` de l'extraction ; on ne touche pas aux frontières).
    for s in segs:
        s["text"] = re.sub(r"\s{2,}", " ", s["text"])
    return segs


def tag_paragraph(para):
    """Produit (tagged_text, segments_meta) pour un paragraphe.

    tagged_text : « [[0]]…[[/0]][[1]]…[[/1]]… » — prêt pour le traducteur.
    segments_meta : liste de dicts de style (index = numéro de balise), pour la
    réinjection.
    """
    segs = paragraph_segments(para)
    parts = []
    meta = []
    for i, s in enumerate(segs):
        parts.append(f"[[{i}]]{s['text']}[[/{i}]]")
        m = _sig_to_meta(s["sig"])
        if s.get("ls_width"):
            # Le moteur déduit le tracking exact : (largeur source − largeur
            # naturelle des glyphes) / nombre de joints, avec les vraies polices.
            m["letter_spaced"] = {"width": round(s["ls_width"], 2),
                                  "text": s["text"]}
        meta.append(m)
    return "".join(parts), meta


# ── Diagnostic : dump du balisage d'un JSON d'extraction ──────────────────────
def _diagnose(json_path, show=8):
    import json
    data = json.load(open(json_path, encoding="utf-8"))
    total = multi = 0
    examples = []
    mismatches = []
    for page in data.get("pages", []):
        for el in page.get("elements", []):
            if el.get("type") != "paragraph":
                continue
            total += 1
            tagged, meta = tag_paragraph(el)
            if len(meta) > 1:
                multi += 1
                if len(examples) < show:
                    examples.append((page["page_num"], len(meta), tagged, meta))
            # Contrôle de cohérence : texte sans balises ≈ texte d'extraction.
            stripped = re.sub(r"\[\[/?\d+\]\]", "", tagged)
            norm = lambda t: re.sub(r"\s+", " ", (t or "")).strip()
            if norm(stripped) != norm(el.get("text", "")):
                if len(mismatches) < 6:
                    mismatches.append((page["page_num"],
                                       norm(el.get("text", ""))[:120],
                                       norm(stripped)[:120]))
    print(f"== {json_path}")
    print(f"   paragraphes: {total} | multi-styles: {multi} "
          f"({100*multi/total:.1f}%)" if total else "   (aucun paragraphe)")
    print(f"   incohérences texte: {len(mismatches)}")
    for pn, a, b in mismatches:
        print(f"     p{pn} EXTRACT: {a!r}")
        print(f"         TAGGED : {b!r}")
    print("   — exemples multi-styles —")
    for pn, n, tagged, meta in examples:
        print(f"   p{pn} ({n} segments): {tagged[:200]}")
        for j, m in enumerate(meta):
            print(f"       [[{j}]] {m['font']} b={int(m['bold'])} i={int(m['italic'])} "
                  f"sz={m['size']} col={m['color']}")


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        _diagnose(p)
