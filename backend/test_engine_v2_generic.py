"""
GÉNÉRICITÉ du moteur v2 — preuve sur un document SYNTHÉTIQUE.

Les correctifs P10-P13 ont été trouvés sur mv21, le Handbook et la démo journal.
Rien ne prouve, à ce stade, qu'ils traitent la CLASSE du problème plutôt que ces
trois documents. Ce test fabrique donc de toutes pièces un PDF que le moteur n'a
jamais vu — autres polices, autres corps, autres couleurs, autres coordonnées —
mais qui rejoue les MÊMES STRUCTURES :

  P10  titre VERTICAL à lettres espacées (+ en-tête de tableau pivoté serré)
  P12  titre pleine colonne suivi d'un FILET DE SECTION (à ne pas confondre avec
       un soulignement) · et un VRAI soulignement (à ne pas perdre)
  P13  deux colonnes séparées par une gouttière PLUS ÉTROITE que les blancs de
       justification de leurs propres lignes · et une liste à PUCES (dont
       l'indentation ne doit pas passer pour une gouttière)

Aucun appel API : la traduction est simulée. Le test échoue si un correctif ne
tient que sur les documents d'origine.

    backend/venv/Scripts/python.exe backend/test_engine_v2_generic.py
"""

import os
import sys

import fitz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pdf_engine_v2.engine import PDFObjectEngine        # noqa: E402
from pdf_engine_v2 import tagging                        # noqa: E402

# Volontairement DIFFÉRENT des documents de test : autre police, autres corps,
# autres couleurs, autres marges.
FONT = "tiro"                       # Times (mv21 = ProximaNova, hb = Avenir)
ENCRE_TITRE = (0.10, 0.20, 0.55)    # bleu nuit
ENCRE_FILET = (0.62, 0.62, 0.62)    # gris — DIFFÉRENT du titre (piège P12)
ENCRE_LIEN = (0.00, 0.45, 0.35)     # vert — le trait sera de la MÊME couleur
NOIR = (0.15, 0.15, 0.15)

W, H = 612, 792                     # Letter (les 3 docs sont en A4/A5)


def _w(txt, size, font=FONT):
    return fitz.get_text_length(txt, fontname=font, fontsize=size)


def build(path):
    doc = fitz.open()
    pg = doc.new_page(width=W, height=H)

    # ── P12 : titre PLEINE COLONNE + filet de section (couleur différente) ───
    titre = "Regional Output Exceeds Every Published Forecast This Quarter"
    pg.insert_text((45, 70), titre, fontname=FONT, fontsize=15.5,
                   color=ENCRE_TITRE)
    # Le filet court sur TOUTE la colonne — donc de largeur ~= celle du titre :
    # le seul critère de largeur est aveugle ici (c'est le piège).
    for y in (40, 78, 700, 726, 752):          # famille de filets identiques
        pg.draw_line(fitz.Point(45, y), fitz.Point(567, y),
                     color=ENCRE_FILET, width=0.6)

    # ── P12 : VRAI soulignement (trait de la MÊME encre que son texte) ───────
    lien = "reports.example.org/quarterly"
    pg.insert_text((45, 105), lien, fontname=FONT, fontsize=10, color=ENCRE_LIEN)
    pg.draw_line(fitz.Point(45, 107.2),
                 fitz.Point(45 + _w(lien, 10), 107.2),
                 color=ENCRE_LIEN, width=0.5)

    # ── P13 : deux colonnes, gouttière PLUS ÉTROITE que les blancs de mots ───
    # Corps 9 ; on place chaque mot À LA MAIN pour imposer la géométrie :
    #   • blancs de mots (justification lâche) : 1.9 × largeur de glyphe
    #   • gouttière entre colonnes             : 2.3 × largeur de glyphe
    # -> la gouttière est PLUS ÉTROITE que le seuil de coupe (2.5) ET plus
    #    étroite que certains blancs de mots : seul le corridor peut trancher.
    size = 9
    gw = _w("n", size)                       # largeur de glyphe de référence
    mot_gap = 1.9 * gw
    gouttiere = 2.3 * gw
    colA = [
        "Regional plants raised output", "again during the period as",
        "demand held firm across the", "network of supply partners",
        "and logistics operators who", "handle the bulk of transit",
        "volumes between the ports.",
    ]
    colB = [
        "Managers credited steady", "hiring and a wider base of",
        "contracts for the result and", "expect the trend to hold as",
        "new capacity arrives at the", "northern sites during the",
        "second half of the year.",
    ]
    x_a = 45
    largeur_a = max(_w(l.replace(" ", ""), size)
                    + mot_gap * l.count(" ") for l in colA)
    x_b = x_a + largeur_a + gouttiere
    for i, (la, lb) in enumerate(zip(colA, colB)):
        y = 150 + i * 13
        for x0, ligne in ((x_a, la), (x_b, lb)):
            x = x0
            for mot in ligne.split(" "):
                pg.insert_text((x, y), mot, fontname=FONT, fontsize=size,
                               color=NOIR)
                x += _w(mot, size) + mot_gap

    # ── P13 : liste à PUCES (l'indentation NE DOIT PAS passer pour une gouttière)
    for i, item in enumerate([
            "Capacity was added at three northern sites during the period",
            "Hiring continued across the logistics and transit divisions",
            "Contract renewals covered the majority of regional partners",
            "Inventory levels returned to their long term seasonal average",
            "Maintenance windows were shortened without affecting output",
            "Freight costs eased slightly after the new routes opened up"]):
        y = 275 + i * 15
        pg.insert_text((45, y), "•", fontname=FONT, fontsize=10, color=NOIR)
        pg.insert_text((63, y), item, fontname=FONT, fontsize=10, color=NOIR)

    # ── P14 : bloc FERRÉ À DROITE (adresse / date d'un courrier) ─────────────
    # Bord droit à fleur, bord gauche franchement déchiqueté.
    droite = ["Northern Operations Office", "1420 Harbour Road, Suite 7",
              "Portsmouth, PO1 3AX", "14 November"]
    x_fer = 545
    for i, l in enumerate(droite):
        pg.insert_text((x_fer - _w(l, 9.5), 400 + i * 12), l, fontname=FONT,
                       fontsize=9.5, color=NOIR)

    # ── P14 : bloc CENTRÉ multi-lignes (exergue) ────────────────────────────
    centre = ["Output held firm through the quarter",
              "across every regional site",
              "without additional capacity"]
    cx = 180
    for i, l in enumerate(centre):
        pg.insert_text((cx - _w(l, 10) / 2, 400 + i * 13), l, fontname=FONT,
                       fontsize=10, color=NOIR)

    # ── P10 : titre VERTICAL à lettres espacées (marge gauche) ───────────────
    mot = "APPENDIX SECTION"
    y = 640                                   # part du bas, monte (dir = 0,-1)
    pas = 13.0                                # lettres ESPACÉES
    for ch in mot:
        if ch != " ":
            pg.insert_text((30, y), ch, fontname=FONT, fontsize=11,
                           color=ENCRE_TITRE, rotate=90)
        y -= pas

    # ── P10 : en-tête de tableau PIVOTÉ, dans une boîte SERRÉE ───────────────
    for k, lab in enumerate(["Region", "Output", "Change"]):
        x = 330 + k * 60
        pg.draw_rect(fitz.Rect(x - 6, 560, x + 12, 650), color=ENCRE_FILET,
                     width=0.5)
        pg.insert_text((x, 646), lab, fontname=FONT, fontsize=9, color=NOIR,
                       rotate=90)

    # ═════════════════════════════════════════════════════════════════════════
    # PAGE 2 — P14-bis : LÉGENDES centrées sous des photos HORS AXE.
    # Le piège : un titre PLEINE LARGEUR pollue la « colonne » des légendes
    # jusqu'aux bords de la page. Leur cadre réel est LA PHOTO au-dessus d'elles
    # — ni une cellule, ni une boîte les contenant. Les deux photos sont
    # volontairement DÉCENTRÉES : une légende qui ne serait « centrée » que par
    # coïncidence avec l'axe de la PAGE échouerait ici.
    # ═════════════════════════════════════════════════════════════════════════
    p2 = doc.new_page(width=W, height=H)
    bandeau = ("Northern Corridor Traffic Climbs For A Fourth Consecutive "
               "Quarter As New Depots Open")
    p2.insert_text((45, 60), bandeau, fontname=FONT, fontsize=14,
                   color=ENCRE_TITRE)                      # pollue la colonne

    for x0, x1, cap in ((45, 245, "Freight volumes at the northern depot"),
                        (330, 567, "Transit hub during the evening peak")):
        p2.draw_rect(fitz.Rect(x0, 95, x1, 205), color=None, fill=(0.86, 0.86, 0.88))
        cx = (x0 + x1) / 2
        p2.insert_text((cx - _w(cap, 8) / 2, 217), cap, fontname=FONT,
                       fontsize=8, color=NOIR)

    # ═════════════════════════════════════════════════════════════════════════
    # PAGE 3 — P14-bis (LEURRE) : un grand APLAT large s'arrête juste au-dessus
    # d'un paragraphe de corps EN DRAPEAU, dans une colonne étroite. L'aplat
    # n'est PAS son cadre : il ne fait que passer. S'il était pris pour tel, la
    # « colonne » du paragraphe passerait de ~170 à ~425 pt, le seuil de
    # justification suivrait, et le drapeau serait étiré au fer.
    # ═════════════════════════════════════════════════════════════════════════
    p3 = doc.new_page(width=W, height=H)
    p3.draw_rect(fitz.Rect(45, 80, 470, 260), color=None, fill=(0.90, 0.90, 0.92))

    def _ligne(pg_, x0, x1, y, mots, size):
        """Pose `mots` entre x0 et x1 EXACTEMENT (bord droit imposé)."""
        larg = [_w(m, size) for m in mots]
        gap = ((x1 - x0 - sum(larg)) / (len(mots) - 1)) if len(mots) > 1 else 0
        x = x0
        for m, lw in zip(mots, larg):
            pg_.insert_text((x, y), m, fontname=FONT, fontsize=size, color=NOIR)
            x += lw + gap

    # Bords droits des lignes INTÉRIEURES : 460 et 468 -> écart = 8 pt.
    #   • cadre = colonne (170 pt) -> seuil = max(4.5 ; 5.1) = 5.1  -> 8 > 5.1
    #     -> DRAPEAU (correct)
    #   • cadre = aplat  (425 pt) -> seuil = max(4.5 ; 12.8) = 12.8 -> 8 <= 12.8
    #     -> justifié (le bug)
    # Le piège est donc ARMÉ : les deux cadres donnent des verdicts opposés.
    _ligne(p3, 300, 460, 280, ["Depot", "throughput", "held", "firm"], 9)
    _ligne(p3, 300, 468, 293, ["across", "the", "northern", "network"], 9)
    _ligne(p3, 300, 395, 306, ["through", "the", "winter."], 9)
    _ligne(p3, 300, 465, 334, ["Managers", "expect", "the", "trend"], 9)
    _ligne(p3, 300, 430, 347, ["to", "hold", "next", "year."], 9)
    for i, l in enumerate(["Traffic on the corridor rose",
                           "steadily through the period",
                           "as the new depots opened."]):
        p3.insert_text((45, 280 + i * 13), l, fontname=FONT, fontsize=9,
                       color=NOIR)

    doc.save(path)
    doc.close()
    return path


# ═════════════════════════════════════════════════════════════════════════════
def run():
    scratch = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "_generic_tmp")
    os.makedirs(scratch, exist_ok=True)
    pdf = build(os.path.join(scratch, "synthetique.pdf"))

    eng = PDFObjectEngine()
    doc = fitz.open(pdf)
    pd = eng.extract_page_data(doc, 0, doc[0], embed_images=True)
    paras = [e for e in pd["elements"] if e.get("type") == "paragraph"]
    draws = [e for e in pd["elements"] if e.get("type") == "drawing"]

    checks = []

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    # ── P12 ─────────────────────────────────────────────────────────────────
    soulignes = [r for p in paras for ln in p.get("lines", [])
                 for r in ln.get("runs", []) if r.get("underline")]
    txt_soulignes = " ".join(r["text"] for r in soulignes)
    ok("P12  le FILET de section n'est pas pris pour un soulignement",
       "Regional Output Exceeds" not in txt_soulignes,
       f"runs soulignés = {txt_soulignes[:60]!r}")
    ok("P12  le VRAI soulignement (même encre) est conservé",
       "reports.example.org" in txt_soulignes,
       f"runs soulignés = {txt_soulignes[:60]!r}")
    consommes = [d for d in draws if d.get("_underline_consumed")]
    ok("P12  un seul trait consommé (celui du lien)",
       len(consommes) == 1, f"{len(consommes)} trait(s) consommé(s)")

    # ── P13 : les deux colonnes ─────────────────────────────────────────────
    def _txt(p):
        return (p.get("text") or "")
    melanges = [p for p in paras
                if "Regional plants" in _txt(p) and "Managers credited" in _txt(p)]
    ok("P13  les 2 colonnes ne sont PAS entrelacées",
       not melanges,
       "un paragraphe contient les deux colonnes" if melanges else "")
    colA = [p for p in paras if "Regional plants raised" in _txt(p)]
    colB = [p for p in paras if "Managers credited steady" in _txt(p)]
    ok("P13  colonne GAUCHE reconstituée d'un bloc",
       colA and "ports" in _txt(colA[0]),
       _txt(colA[0])[:70] if colA else "(absente)")
    ok("P13  colonne DROITE reconstituée d'un bloc",
       colB and "second half" in _txt(colB[0]),
       _txt(colB[0])[:70] if colB else "(absente)")

    # ── P13 : les puces ─────────────────────────────────────────────────────
    # Contrôle GÉOMÉTRIQUE (et non textuel : la base-14 Times rend « • » en
    # « · »). Un item de liste doit démarrer à l'abscisse de SA PUCE (45), pas à
    # celle de son texte (63) — sinon le corridor d'indentation l'a détachée.
    items = [p for p in paras if "Capacity was added" in _txt(p)
             or "Freight costs eased" in _txt(p)]
    orphelines = [p for p in paras
                  if len(_txt(p).strip()) <= 1 and _txt(p).strip()]
    ok("P13  la PUCE n'est pas détachée de son texte",
       items and all(p["bbox"][0] < 55 for p in items) and not orphelines,
       f"{len(orphelines)} marqueur(s) orphelin(s) ; "
       f"x0 des items = {[round(p['bbox'][0], 1) for p in items]} (attendu ≈ 45)")

    # ── P14 : ALIGNEMENTS ───────────────────────────────────────────────────
    def _para(frag):
        return next((p for p in paras if frag in _txt(p)), None)

    # Le bloc d'adresse est LÉGITIMEMENT découpé (ses retours à la ligne sont
    # volontaires — l'Étape A les sépare) : on vérifie que CHAQUE morceau est
    # reconnu ferré à droite, qu'il ait plusieurs lignes ou une seule (la pile).
    morceaux = [p for p in paras
                if any(k in _txt(p) for k in ("Northern Operations", "Harbour Road",
                                              "Portsmouth", "14 November"))]
    p_dr = morceaux[0] if morceaux else None
    ok("P14  bloc ferré à DROITE détecté (tous ses morceaux)",
       morceaux and all(p.get("align") == "right" for p in morceaux),
       f"aligns={[p.get('align') for p in morceaux]}")
    p_ce = _para("Output held firm through the quarter")
    ok("P14  bloc CENTRÉ multi-lignes détecté",
       p_ce is not None and p_ce.get("align") == "center",
       f"align={(p_ce or {}).get('align')}")
    p_pu = _para("Capacity was added")
    ok("P14  une liste à PUCES reste ferrée à gauche",
       p_pu is not None and p_pu.get("align") == "left",
       f"align={(p_pu or {}).get('align')}")
    p_col = _para("Regional plants raised")
    ok("P14  une colonne de texte au fer à gauche le reste",
       p_col is not None and p_col.get("align") in ("left", "justify"),
       f"align={(p_col or {}).get('align')}")
    if p_dr:
        cb = p_dr.get("container_bbox") or p_dr["bbox"]
        ok("P14  le conteneur du bloc DROITE s'étend vers la GAUCHE "
           "(bord droit figé)",
           cb[0] < p_dr["bbox"][0] - 1 and cb[2] <= p_dr["bbox"][2] + 1,
           f"bbox=[{p_dr['bbox'][0]:.0f},{p_dr['bbox'][2]:.0f}] "
           f"conteneur=[{cb[0]:.0f},{cb[2]:.0f}]")

    # ── P10 : le vertical est LU ────────────────────────────────────────────
    def _rot(p):
        return any(abs((r.get("dir") or [1, 0])[1]) > 0.01
                   for ln in p.get("lines", []) for r in ln.get("runs", []))
    verticaux = [p for p in paras if _rot(p)]
    titre_v = [p for p in verticaux if "APPENDIX" in _txt(p)]
    ok("P10  le titre vertical à lettres espacées est LU comme un mot",
       titre_v and "APPENDIX SECTION" in _txt(titre_v[0]),
       _txt(titre_v[0])[:40] if titre_v else "(absent)")
    if titre_v:
        _tag, meta = tagging.tag_paragraph(titre_v[0])
        ls = (meta[0] or {}).get("letter_spaced") or {}
        bb = titre_v[0]["bbox"]
        empan = bb[3] - bb[1]                 # empan VERTICAL (axe d'écriture)
        ok("P10  l'empan est mesuré LE LONG DE L'AXE (pas en x)",
           ls and abs(ls.get("width", 0) - empan) < 2.0,
           f"ls_width={ls.get('width')} vs empan d'axe={empan:.1f} "
           f"(largeur en x = {bb[2]-bb[0]:.1f})")

    # ── P10 : le vertical est TRADUIT et RENDU dans sa bande ────────────────
    FAUX = {"APPENDIX SECTION": "SECTION ANNEXE", "Region": "Région",
            "Output": "Production", "Change": "Variation"}
    for p in paras:
        tagged, meta = tagging.tag_paragraph(p)
        p["tr_segments"] = meta
        cle = (p.get("text") or "").strip()
        p["tr_tagged"] = (f"[[0]]{FAUX[cle]}[[/0]]" if cle in FAUX else tagged)

    out = fitz.open()
    eng.render_page_into(out, pd, draw_borders=False, translated=True)
    rendu = out[0].get_text("dict")["blocks"]
    lignes_rot = [(ln["dir"], ln["bbox"],
                   "".join(s["text"] for s in ln["spans"]))
                  for b in rendu for ln in b.get("lines", [])
                  if abs(ln["dir"][1]) > 0.01]
    txt_rot = " ".join(t for _d, _b, t in lignes_rot).replace(" ", "")
    ok("P10  le titre vertical est TRADUIT au rendu",
       "SECTIONANNEXE" in txt_rot, f"rendu = {txt_rot[:60]!r}")
    ok("P10  les en-têtes pivotés sont TRADUITS",
       "Région" in txt_rot and "Production" in txt_rot,
       f"rendu = {txt_rot[:80]!r}")
    if titre_v:
        src = titre_v[0]["bbox"]
        cibles = [b for _d, b, t in lignes_rot
                  if b[1] >= src[1] - 3 and b[3] <= src[3] + 3 and b[0] < 60]
        ok("P10  le titre traduit reste DANS SA BANDE (aucun débordement)",
           cibles, f"bande source y=[{src[1]:.0f}, {src[3]:.0f}]")
    # Les en-têtes pivotés, serrés dans leur boîte, ne doivent pas se replier.
    entetes = [t for _d, _b, t in lignes_rot
               if any(k in t for k in ("Région", "Production", "Variation"))]
    ok("P10  un en-tête pivoté serré ne se REPLIE pas sur 2 lignes",
       len(entetes) == 3, f"{len(entetes)} en-tête(s) rendu(s) : {entetes}")

    # ── P14 : le RENDU traduit conserve le fer à droite ─────────────────────
    horiz = [(ln["bbox"], "".join(s["text"] for s in ln["spans"]))
             for b in rendu for ln in b.get("lines", [])
             if abs(ln["dir"][1]) <= 0.01]
    if morceaux:
        y0 = min(p["bbox"][1] for p in morceaux) - 3
        y1 = max(p["bbox"][3] for p in morceaux) + 3
        src_r = max(p["bbox"][2] for p in morceaux)
        rendues = [bb for bb, t in horiz
                   if y0 <= bb[1] <= y1 and bb[0] > 300]   # le bloc de droite seul
        if len(rendues) >= 2:
            bords = [bb[2] for bb in rendues]
            gauches = [bb[0] for bb in rendues]
            ok("P14  au RENDU, le bloc DROITE reste ferré à droite "
               "(bords droits alignés, gauches déchiquetés)",
               max(bords) - min(bords) <= 3.0
               and max(gauches) - min(gauches) > 8.0
               and abs(max(bords) - src_r) <= 4.0,
               f"bords droits={[round(b) for b in bords]} (source {src_r:.0f}) ; "
               f"gauches={[round(g) for g in gauches]}")
        else:
            ok("P14  au RENDU, le bloc DROITE reste ferré à droite",
               False, f"{len(rendues)} ligne(s) retrouvée(s)")

    # ── Rapport ─────────────────────────────────────────────────────────────
    print("=" * 78)
    print("GÉNÉRICITÉ — document SYNTHÉTIQUE (jamais vu par le moteur)")
    print("=" * 78)
    n_ok = 0
    for nom, bon, detail in checks:
        print(f"  [{'OK ' if bon else 'ÉCHEC'}] {nom}")
        if detail and not bon:
            print(f"          {detail}")
        n_ok += bon
    print()
    print(f"== {n_ok}/{len(checks)} ==")
    return 0 if n_ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(run())
