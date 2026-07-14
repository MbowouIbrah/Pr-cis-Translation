"""
pdf_engine_v2 — Moteur PDF minimaliste et « from scratch ».

Une seule responsabilité, en deux temps :

  1. EXTRACTION  : lit chaque page et sérialise CHAQUE objet détectable
                   (texte, image, dessin vectoriel) sous forme de JSON,
                   dans l'ordre de rendu, sans AUCUNE fusion ni détection
                   de paragraphe. Tout est basique : un span = un objet,
                   un tracé = un objet, une image = un objet.

  2. RÉINJECTION : reconstruit chaque page sur une feuille VIERGE (même
                   dimensions) en redessinant chaque objet exactement à sa
                   position d'origine, avec sa mise en forme d'origine, puis
                   trace une BORDURE autour de chaque objet.

Aucune dépendance au moteur historique (backend/pdf_translator_engine.py).
Seul PyMuPDF (fitz) est requis.
"""

import os
import io
import re
import json
import math
import base64
from pathlib import Path
from collections import defaultdict

import fitz  # PyMuPDF >= 1.23

from . import reflow

# Caractères de contrôle parasites (hors \t \n \r) à purger du texte extrait.
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Ligne commençant par une puce ou un numéro d'item de liste.
_LIST_RE = re.compile(
    r'^\s*([•◦▪‣·●○∙\-–—*]\s+|\(?\d{1,3}[.)]\s+|\(?[a-zA-Z][.)]\s+)')
# Puce seule (marqueur non ambigu) vs numéro/lettre (ambigu : peut être un
# nombre du texte, ex. « age is 16. », « (MV-44) »).
_BULLET_RE = re.compile(r'^\s*[•◦▪‣·●○∙\-–—*]\s+')
_NUMITEM_RE = re.compile(r'^\s*\(?(?:\d{1,3}|[a-zA-Z])[.)]\s+')

# Mots NON TERMINAUX : une ligne finissant par l'un d'eux n'achève pas une
# phrase → la ligne suivante est une continuation (jamais un nouveau paragraphe).
_NON_TERMINAL = {
    # anglais
    "the", "a", "an", "to", "of", "from", "and", "or", "for", "with", "in",
    "on", "at", "by", "as", "is", "are", "was", "were", "be", "been", "that",
    "this", "these", "those", "which", "who", "but", "nor", "so", "into",
    "onto", "than", "then", "if", "when", "while", "your", "our", "their",
    "its", "his", "her", "no", "not", "any", "each", "per", "via",
    # français
    "le", "la", "les", "un", "une", "des", "de", "du", "à", "au", "aux", "et",
    "ou", "en", "dans", "sur", "sous", "par", "pour", "avec", "que", "qui",
    "dont", "est", "sont", "ce", "cette", "ces", "son", "sa", "ses", "leur",
    "leurs", "votre", "vos", "notre", "nos", "ne", "se", "sans", "vers",
}

# Conjonctions de coordination : une ligne qui commence par l'une d'elles
# CONTINUE le paragraphe précédent même après un point (pas de coupe).
_COORD_CONJ = {
    "But", "And", "So", "Yet", "Or", "Nor", "For", "However", "Because",
    "Although", "Though", "While", "Thus", "Therefore", "Also", "Then",
    "Still", "Besides", "Moreover", "Furthermore", "Hence",
}


# ── Couleurs des bordures de debug par type d'objet ─────────────────────────
BORDER_COLORS = {
    "paragraph": (0.00, 0.55, 0.00),   # vert
    "text_line": (0.00, 0.55, 0.00),   # vert
    "image":     (1.00, 0.00, 0.00),   # rouge
    "drawing":   (0.00, 0.30, 1.00),   # bleu
}
BORDER_WIDTH = 0.6

# Cadre du CONTENEUR élargi d'un paragraphe (Étape D : expansion vers la
# droite). Orange pointillé, pour le distinguer du contour de texte (vert).
EXPAND_COLOR = (1.00, 0.50, 0.00)
EXPAND_WIDTH = 0.8


# ════════════════════════════════════════════════════════════════════════════
# Helpers de sérialisation (fitz -> JSON natif)
# ════════════════════════════════════════════════════════════════════════════
def _pt(p):
    """fitz.Point / (x, y) -> [x, y]."""
    if p is None:
        return None
    if hasattr(p, "x"):
        return [p.x, p.y]
    return [p[0], p[1]]


def _rect(r):
    """fitz.Rect / (x0, y0, x1, y1) -> [x0, y0, x1, y1]."""
    if r is None:
        return None
    if hasattr(r, "x0"):
        return [r.x0, r.y0, r.x1, r.y1]
    return [r[0], r[1], r[2], r[3]]


def _color(c):
    """Couleur fitz (tuple de floats) -> liste de floats, ou None."""
    if c is None:
        return None
    return [float(v) for v in c]


def _serialize_drawing_item(item):
    """Sérialise un item de tracé get_drawings() en structure JSON.

    Formes possibles (PyMuPDF) :
      ('l',  p1, p2)              segment
      ('c',  p1, p2, p3, p4)      courbe de Bézier
      ('re', rect, orientation)   rectangle
      ('qu', quad)                quadrilatère
    """
    op = item[0]
    if op == "l":
        return {"op": "l", "p1": _pt(item[1]), "p2": _pt(item[2])}
    if op == "c":
        return {"op": "c", "p1": _pt(item[1]), "p2": _pt(item[2]),
                "p3": _pt(item[3]), "p4": _pt(item[4])}
    if op == "re":
        d = {"op": "re", "rect": _rect(item[1])}
        if len(item) > 2:
            d["orientation"] = item[2]
        return d
    if op == "qu":
        q = item[1]
        # Quad -> 4 points
        return {"op": "qu", "quad": [_pt(q.ul), _pt(q.ur), _pt(q.lr), _pt(q.ll)]}
    # Type inconnu : on garde une trace brute (ignoré à la réinjection).
    return {"op": op, "raw": str(item)}


def _drawing_item_boxes(el):
    """Bboxes des ITEMS d'un dessin sérialisé. Un dessin composite (grille de
    tableau, encadré, barres pleines) ne se réduit pas à son bbox englobant :
    ce sont ses traits/rectangles qui occupent réellement l'espace — c'est le
    mur vertical d'une cellule qui doit borner l'expansion, pas la grille
    entière (dont le x0 est celui du tableau)."""
    boxes = []
    for it in el.get("items", ()):
        op = it.get("op")
        pts = None
        if op == "l":
            pts = [it.get("p1"), it.get("p2")]
        elif op == "c":
            pts = [it.get("p1"), it.get("p2"), it.get("p3"), it.get("p4")]
        elif op == "re":
            r = it.get("rect")
            if r and len(r) >= 4:
                boxes.append((r[0], r[1], r[2], r[3]))
            continue
        elif op == "qu":
            pts = it.get("quad")
        if pts and all(p and len(p) >= 2 for p in pts):
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            boxes.append((min(xs), min(ys), max(xs), max(ys)))
    return boxes


def _serialize_drawing(d):
    """Sérialise un dessin vectoriel complet (une entrée get_drawings())."""
    return {
        "type": "drawing",
        "draw_type": d.get("type"),                 # 's', 'f', 'fs', 'clip', 'group'
        "bbox": _rect(d.get("rect")),
        "items": [_serialize_drawing_item(it) for it in d.get("items", [])],
        "stroke_color": _color(d.get("color")),
        "fill_color": _color(d.get("fill")),
        "width": d.get("width"),
        "dashes": d.get("dashes"),
        "close_path": d.get("closePath"),
        "even_odd": d.get("even_odd"),
        "line_cap": d.get("lineCap"),
        "line_join": d.get("lineJoin"),
        "stroke_opacity": d.get("stroke_opacity"),
        "fill_opacity": d.get("fill_opacity"),
        "seqno": d.get("seqno"),
    }


# ════════════════════════════════════════════════════════════════════════════
# Moteur
# ════════════════════════════════════════════════════════════════════════════
class PDFObjectEngine:
    """Extraction/réinjection objet par objet, sans fusion ni interprétation."""

    # ─────────────────────────────────────────────────────────────────────────
    # 1. EXTRACTION
    # ─────────────────────────────────────────────────────────────────────────
    def extract(self, pdf_path, output_json=None, embed_images=True):
        """Extrait tous les objets de chaque page vers un JSON.

        Args:
            pdf_path:    chemin du PDF source.
            output_json: chemin du JSON de sortie (défaut : <pdf>_objects.json).
            embed_images: True = octets d'image encodés en base64 DANS le JSON
                          (fichier autonome). False = images écrites dans un
                          dossier <json>_assets/ et référencées par nom.

        Returns:
            (data:dict, output_json:str)
        """
        pdf_path = str(pdf_path)
        if output_json is None:
            output_json = str(Path(pdf_path).with_suffix("")) + "_objects.json"

        assets_dir = None
        if not embed_images:
            assets_dir = str(Path(output_json).with_suffix("")) + "_assets"
            os.makedirs(assets_dir, exist_ok=True)

        data = {
            "source": os.path.basename(pdf_path),
            "embed_images": embed_images,
            "fonts": {},
            "pages": [],
        }

        doc = fitz.open(pdf_path)
        try:
            # Polices embarquées du PDF source (pour un rendu fidèle des glyphes
            # et de la vraie police à la réinjection). Encodées en base64 dans
            # le JSON → fichier autonome.
            data["fonts"] = self._extract_fonts(doc)
            for page_num, page in enumerate(doc):
                data["pages"].append(self.extract_page_data(
                    doc, page_num, page, embed_images=embed_images,
                    assets_dir=assets_dir))
        finally:
            doc.close()

        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return data, output_json

    def extract_page_data(self, doc, page_num, page, embed_images=True,
                          assets_dir=None):
        """Extrait UNE page (élément `pages[]` du schéma JSON) — brique de
        l'extraction complète et du pipeline progressif page par page."""
        page_data = {
            "page_num": page_num + 1,
            "width": page.rect.width,
            "height": page.rect.height,
            "elements": [],
        }
        # Ordre de peinture (du fond vers l'avant), pour que les
        # objets se recouvrent comme dans l'original : dessins
        # vectoriels / fonds d'abord, puis images, puis texte au-dessus.
        draw_els = self._extract_drawings(page)
        img_els = self._extract_images(
            doc, page, page_num, embed_images, assets_dir)
        page_data["elements"].extend(draw_els)
        page_data["elements"].extend(img_els)
        text_lines = self._extract_text(page)
        if self.group_paragraphs:
            ctx = self._build_page_ctx(page, text_lines,
                                       draw_els, img_els)
            # Cellules de tableau persistées : un recalcul d'expansion
            # depuis le JSON (sans page fitz) doit pouvoir cloisonner.
            page_data["cells"] = [list(c) for c in ctx.get("cells", ())]
            page_data["elements"].extend(
                self._group_paragraphs(text_lines, ctx))
        else:
            page_data["elements"].extend(text_lines)
        self._mark_underlines(page_data["elements"])
        return page_data

    # ── Polices embarquées : nom propre -> liste de sous-ensembles (b64) ─────
    def _extract_fonts(self, doc):
        """Collecte les octets de chaque police embarquée du PDF.

        Une même police (ex. 'PTSerif-Regular') peut être embarquée en
        PLUSIEURS sous-ensembles (un par xref, chacun couvrant un jeu de
        glyphes partiel). On les garde TOUS : à la réinjection, on choisira
        pour chaque texte le sous-ensemble qui couvre réellement ses glyphes.
        Clé = nom SANS préfixe de sous-ensemble ('ABCDEF+') pour matcher le
        champ 'font' des spans.
        """
        # 1) Mapping Unicode -> glyph-id PAR XREF (sous-ensemble). Les polices
        #    CID/Type0 (Identity-H) sont embarquées sans cmap Unicode : on la
        #    reconstruit à partir de get_texttrace() qui expose, pour chaque
        #    caractère, son code Unicode ET son glyph-id dans le sous-ensemble.
        u2g_by_xref = defaultdict(dict)     # xref -> {unicode: gid}
        name_xrefs = defaultdict(set)       # nom propre -> {xref, ...}
        name_ext = {}                       # xref -> ext
        for pno in range(len(doc)):
            page = doc[pno]
            page_name2xref = defaultdict(list)   # nom propre -> [xref] (cette page)
            try:
                for entry in doc.get_page_fonts(pno):
                    xref, ext, _ftype, basefont = entry[0], entry[1], entry[2], entry[3]
                    if ext == "n/a":
                        continue
                    clean = basefont.split("+")[-1] if "+" in basefont else basefont
                    page_name2xref[clean].append(xref)
                    name_xrefs[clean].add(xref)
                    name_ext[xref] = ext
            except Exception:
                pass
            try:
                trace = page.get_texttrace()
            except Exception:
                trace = []
            for span in trace:
                nm = span.get("font", "")
                nm = nm.split("+")[-1] if "+" in nm else nm
                xrefs = page_name2xref.get(nm)
                if not xrefs:
                    continue
                for ch in span.get("chars", []):
                    u, g = ch[0], ch[1]
                    for xr in xrefs:     # ambiguïté (rare) : on renseigne tous
                        u2g_by_xref[xr][u] = g

        # 2) Extraction des octets de chaque sous-ensemble + patch de cmap si
        #    nécessaire. On garde TOUS les sous-ensembles d'un même nom : à la
        #    réinjection, on choisira celui qui couvre les glyphes du texte.
        fonts = {}
        for clean, xrefs in name_xrefs.items():
            for xr in sorted(xrefs):
                try:
                    buf = doc.extract_font(xr)[3]
                except Exception:
                    buf = None
                if not buf:
                    continue
                buf = self._maybe_patch_cmap(buf, u2g_by_xref.get(xr))
                fonts.setdefault(clean, []).append({
                    "ext": name_ext.get(xr, "ttf"),
                    "b64": base64.b64encode(buf).decode("ascii"),
                })
        return fonts

    @staticmethod
    def _maybe_patch_cmap(buf, u2g):
        """Ajoute une cmap Unicode au sous-ensemble s'il n'en a pas (police
        CID/Identity-H). Sans effet si la police a déjà une cmap utilisable ou
        si aucun mapping n'est disponible."""
        # Police avec cmap Unicode déjà utilisable → inchangée.
        try:
            if len(fitz.Font(fontbuffer=buf).valid_codepoints()) > 0:
                return buf
        except Exception:
            pass
        if not u2g:
            return buf
        try:
            from fontTools.ttLib import TTFont, newTable
            from fontTools.ttLib.tables._c_m_a_p import cmap_format_4
            ft = TTFont(io.BytesIO(buf))
            order = ft.getGlyphOrder()
            bmp = {u: order[g] for u, g in u2g.items()
                   if u <= 0xFFFF and 0 <= g < len(order)}
            if not bmp:
                return buf
            sub = cmap_format_4(4)
            sub.platformID, sub.platEncID, sub.language = 3, 1, 0
            sub.cmap = bmp
            cmap = newTable("cmap")
            cmap.tableVersion = 0
            cmap.tables = [sub]
            ft["cmap"] = cmap
            out = io.BytesIO()
            ft.save(out)
            return out.getvalue()
        except Exception:
            return buf

    # ── Texte : détection de LIGNES VISUELLES (façon sélection PDF) ───────────
    # Chaque span est d'abord capté tel quel (rendu fidèle : sa position, sa
    # police, son style), puis les spans sont regroupés en LIGNES par analyse
    # de disposition — sans OCR, sans détection de paragraphe :
    #   1. regroupement par ligne de base (clustering vertical) ;
    #   2. au sein d'une ligne, coupe aux GRANDS écarts horizontaux
    #      (séparateurs de colonne), mesurés RELATIVEMENT à la largeur de
    #      glyphe de la ligne. Un letter-spacing (~1–2× la largeur d'un glyphe)
    #      reste soudé ; un saut de colonne (≥ ~2.5×) coupe.
    # Une ligne détectée = un objet `text_line` { bbox, runs:[spans] }.
    _COL_SPLIT_FACTOR = 2.5    # écart de coupe = facteur × largeur de glyphe
    _SPACE_FACTOR     = 0.30   # écart au-delà duquel on insère une espace

    # ── Regroupement en paragraphes (étape 7) ───────────────────────────────
    group_paragraphs     = True    # False → objets = lignes (pas de paragraphes)
    para_remaining_space = True    # Étape A : coupe « espace restant » (togglable)
    expand_paragraphs    = True    # Étape D : conteneur élargi vers la droite
    detect_tables        = True    # Étape B : cloisonnement des cellules de table
    vertical_flow        = False   # Étape E : push-down vertical + anti-collision
                                   # ANNULÉ (trop d'heuristiques) — le code reste
                                   # derrière ce toggle mais est INACTIF par défaut.
                                   # Conteneurs à hauteur fixe : la cascade du
                                   # reflow ajuste chaque paragraphe dans sa boîte.
    justify_text         = True    # Rendu JUSTIFIÉ des paragraphes dont la source
                                   # l'était (détection géométrique au rendu).
    shrink_to_fit        = True    # RÈGLE UNIQUE anti-débordement : chaque
                                   # paragraphe traduit est GARANTI de tenir dans
                                   # sa propre boîte (compression taille/interligne
                                   # jusqu'à 46 %). Zéro chevauchement, aucune
                                   # heuristique de déplacement.
    _EXPAND_GAP          = 6.0     # (héritage) espace laissé vers un objet/bord
    # Gouttière de sécurité NORMALISÉE laissée entre un bloc élargi et l'objet /
    # la colonne voisine à sa droite : recommandation typographique standard,
    # proportionnelle au corps (≈ gouttière inter-colonnes ~1 pica), avec un
    # plancher en points. Sert à l'expansion horizontale (Étape D v2).
    _SAFE_GUTTER_FACTOR  = 1.5     # × taille de police
    _SAFE_GUTTER_MIN     = 12.0    # plancher (points)
    _PARA_GAP_FACTOR     = 1.8     # saut vertical > facteur × taille → coupe DURE
    _PARA_MODERATE_FACTOR = 1.35   # gap au-delà duquel l'INDENTATION peut couper
    _PARA_PUNCT_FACTOR   = 1.6     # gap au-delà duquel la PONCTUATION peut couper
    _PARA_INDENT_FACTOR  = 1.2     # indentation > facteur × taille → coupe
    _PARA_SIZE_FACTOR    = 0.20    # écart de taille relatif → coupe (titre)
    _RS_SPACE_FACTOR     = 1.0     # marge (en largeurs de glyphe) exigée en plus
                                   # du 1er mot pour parler de coupe volontaire
    _RS_WINDOW_FACTOR    = 2.5     # fenêtre verticale (× taille) pour estimer la
                                   # marge droite de COLONNE d'une ligne

    # ── Contexte de page : géométrie + BLOQUEURS (fondation partagée) ────────
    # Sert aux étapes qui raisonnent sur l'espace horizontal (règle « espace
    # restant », future expansion). Un « bloqueur » est tout objet qui empêche
    # une ligne de s'étendre vers la droite : image, dessin significatif, et
    # toute AUTRE ligne de texte (colonne voisine). Le bord droit utilisable de
    # la page est déduit de la disposition réelle (plus grande abscisse de fin
    # de texte) → aucune constante calée sur un document donné.
    def _build_page_ctx(self, page, text_lines, draw_els, img_els):
        # `obstacles` : objets non-texte (image / dessin) qui bornent DUR une
        # extension horizontale. `line_rects` : rectangles des autres lignes,
        # servant à retrouver la MARGE DROITE DE LA COLONNE d'une ligne (le bord
        # où le texte s'aligne réellement) — à ne pas confondre avec la colonne
        # voisine de l'autre côté d'une gouttière.
        obstacles = []
        for el in img_els:
            bb = el.get("bbox")
            if bb and len(bb) >= 4:
                obstacles.append((bb[0], bb[1], bb[2], bb[3]))
        for el in draw_els:
            bb = el.get("bbox")
            if bb and len(bb) >= 4 and (bb[2] - bb[0] > 0.5 or bb[3] - bb[1] > 0.5):
                obstacles.append((bb[0], bb[1], bb[2], bb[3]))
        xs = [bb[2] for ln in text_lines
              if (bb := ln.get("bbox")) and len(bb) >= 4]
        # Étape B : cellules de tableau (tables BORDÉES uniquement — stratégie
        # « lines » : faible faux-positif). Chaque cellule borne le texte qu'elle
        # contient : ajoutée aux obstacles (murs de cellule) ET conservée pour
        # taguer les lignes (empêche la fusion de paragraphes entre cellules).
        cells = []
        if self.detect_tables:
            try:
                tf = page.find_tables(vertical_strategy="lines",
                                      horizontal_strategy="lines")
                for t in (tf.tables if tf else []):
                    for c in t.cells:
                        if c and len(c) >= 4:
                            cells.append((c[0], c[1], c[2], c[3]))
            except Exception:
                pass
        obstacles.extend(cells)
        # Obstacles d'EXPANSION (chemin rendu uniquement — le regroupement garde
        # `obstacles` inchangé) : les dessins sont décomposés en ITEMS pour que
        # le mur/la barre d'un tableau non détecté par find_tables borne quand
        # même l'expansion à sa vraie position.
        expand_obstacles = list(obstacles)
        for el in draw_els:
            expand_obstacles.extend(_drawing_item_boxes(el))
        return {
            "width": page.rect.width,
            "height": page.rect.height,
            "obstacles": obstacles,
            "expand_obstacles": expand_obstacles,
            "text_right": max(xs) if xs else page.rect.width,
            "cells": cells,
        }

    # ── Soulignements (liens) : associer chaque trait au texte au-dessus ─────
    # Écart max PAR CANAL entre l'encre d'un trait et celle du texte au-dessus
    # pour les tenir pour la même couleur.
    _UL_INK_TOL = 0.25
    # À partir de combien de traits IDENTIQUES sur une page parle-t-on d'une
    # grille de filets (et non d'une décoration de texte) ?
    _RULE_FAMILY_MIN = 3

    @staticmethod
    def _ink(el):
        """Encre visible d'un trait : sa couleur de trait, sinon son remplissage."""
        return el.get("stroke_color") or el.get("fill_color")

    @classmethod
    def _rule_key(cls, el):
        """Signature d'un filet : empan x, épaisseur, encre. Deux filets de même
        signature à des hauteurs différentes sont des CLONES."""
        bb = el["bbox"]
        ink = cls._ink(el) or ()
        return (round(bb[0]), round(bb[2]), round(bb[3] - bb[1], 1),
                tuple(round(float(c), 2) for c in ink))

    @classmethod
    def _same_ink(cls, a, b):
        """Deux encres se confondent-elles à l'œil ? Une couleur ABSENTE ne
        permet pas de trancher : on ne rejette pas sur ce seul motif."""
        if not a or not b or len(a) < 3 or len(b) < 3:
            return True
        return max(abs(float(x) - float(y))
                   for x, y in zip(a[:3], b[:3])) <= cls._UL_INK_TOL

    def _mark_underlines(self, elements):
        """Détecte les traits de SOULIGNEMENT et marque les runs concernés
        (`underline=True` → le rendu traduit les redessine SOUS le texte reflowé)
        ainsi que le trait (`_underline_consumed` → non redessiné en mode traduit,
        sinon il resterait figé sous le texte déplacé).

        Le piège : un **filet de section** posé juste sous un titre qui remplit sa
        colonne a EXACTEMENT la largeur du texte — le seul critère de largeur le
        laissait passer (démo journal : titre rouge, filet gris, pris pour un
        soulignement… puis redessiné en ROUGE sous le texte traduit). Deux
        discriminants, tous deux vérifiés sur les 35 vrais soulignements de mv21
        et du Handbook :

        1. **L'ENCRE.** Un soulignement est une décoration du TEXTE : il est peint
           dans SON encre (les 35 vrais : trait et texte exactement de la même
           couleur). Un trait d'une autre couleur n'appartient pas à ce texte.
        2. **LES CLONES.** Un filet de la grille du document a des jumeaux ailleurs
           sur la page (même empan, même encre, même épaisseur — la démo en compte
           7). Un vrai soulignement est unique : il épouse SON texte.
        """
        unders = []
        for el in elements:
            if el.get("type") != "drawing":
                continue
            bb = el.get("bbox")
            if not bb or len(bb) < 4:
                continue
            w, h = bb[2] - bb[0], bb[3] - bb[1]
            if h <= 2.5 and w >= 6 and el.get("draw_type") in ("s", "fs", None):
                unders.append(el)
        if not unders:
            return

        # (2) Écarte d'emblée les filets de la grille (traits clonés).
        from collections import Counter
        fam = Counter(self._rule_key(u) for u in unders)
        unders = [u for u in unders
                  if fam[self._rule_key(u)] < self._RULE_FAMILY_MIN]
        if not unders:
            return

        for el in elements:
            if el.get("type") != "paragraph":
                continue
            for ln in el.get("lines", []):
                for r in ln.get("runs", []):
                    o, bb = r.get("origin"), r.get("bbox")
                    if not o or not bb or len(bb) < 4:
                        continue
                    by, rx0, rx1 = o[1], bb[0], bb[2]
                    rw = rx1 - rx0
                    size = r.get("size", 10) or 10
                    for u in unders:
                        ub = u["bbox"]
                        uy = 0.5 * (ub[1] + ub[3])
                        if not (by - 1.0 <= uy <= by + 0.35 * size + 3.0):
                            continue                     # pas juste sous la ligne
                        ov = min(rx1, ub[2]) - max(rx0, ub[0])
                        if ov < 0.6 * rw:
                            continue                     # ne couvre pas le run
                        if (ub[2] - ub[0]) > 1.4 * rw:
                            continue                     # filet pleine largeur
                        # (1) L'encre du trait doit être celle du texte.
                        if not self._same_ink(self._ink(u), r.get("color")):
                            continue
                        r["underline"] = True
                        u["_underline_consumed"] = True

    def _extract_text(self, page):
        raw = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        spans = []
        for block in raw.get("blocks", []):
            if block.get("type") != 0:      # 0 = texte
                continue
            for line in block.get("lines", []):
                direction = line.get("dir", (1, 0))
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    # Retire les caractères de CONTRÔLE (BEL \x07, etc.) : sans
                    # sens visuel, souvent superposés au texte (ex. « \x07 » sur
                    # « Note: »), ils créent des spans/lignes parasites qui
                    # cassent le regroupement en lignes et paragraphes.
                    text = _CTRL_RE.sub("", text)
                    if not text.strip():
                        continue
                    bb = span["bbox"]
                    o = span.get("origin", (bb[0], bb[3]))
                    flags = span.get("flags", 0)
                    nchar = max(1, len(text.strip()))
                    spans.append({
                        "bbox": [bb[0], bb[1], bb[2], bb[3]],
                        "origin": [o[0], o[1]],
                        "text": text,
                        "font": span.get("font"),
                        "size": span.get("size", 0) or 0,
                        "color": _int_color_to_rgb(span.get("color", 0)),
                        "flags": flags,
                        "bold": bool(flags & 16),
                        "italic": bool(flags & 2),
                        "dir": list(direction),
                        "_gw": (bb[2] - bb[0]) / nchar,   # largeur de glyphe approx.
                        "_base": o[1],
                    })
        return self._group_text_lines(spans)

    def _group_text_lines(self, spans):
        """Regroupe des spans en lignes visuelles. Le texte HORIZONTAL suit le
        clustering baseline + coupe colonne (inchangé) ; le texte INCLINÉ/VERTICAL
        (Étape C) est regroupé le long de son axe d'écriture par
        `_group_rotated_lines`."""
        if not spans:
            return []
        horiz, other = [], []
        for s in spans:
            if abs(s["dir"][1]) <= 0.01 and s["dir"][0] >= 0:
                horiz.append(s)
            else:
                other.append(s)
        elements = self._group_horizontal_lines(horiz)
        if other:
            elements.extend(self._group_rotated_lines(other))
        return elements

    def _group_horizontal_lines(self, spans):
        """Clustering baseline + coupe aux séparateurs de colonne (texte
        horizontal). Retourne une liste d'objets `text_line`."""
        if not spans:
            return []
        # 1) Lignes de base : tri par (baseline, x) puis clustering vertical.
        spans.sort(key=lambda s: (round(s["_base"], 1), s["bbox"][0]))
        rows = []
        for s in spans:
            # Tolérance basée sur la PLUS PETITE taille des deux : un glyphe
            # géant (numéro décoratif, capitale ornementale) ne doit pas avaler
            # une petite ligne voisine dont la baseline diffère (ex. le « 1 »
            # taille 62 qui happait « & VEHICLE OWNERS » taille 20). Deux spans
            # d'une même ligne réelle partagent la baseline (écart ≈ 0) → restent
            # groupés quelle que soit la taille.
            if rows and abs(s["_base"] - rows[-1]["_base"]) <= 0.45 * max(
                    min(s["size"], rows[-1]["_size_ref"]), 1.0):
                rows[-1]["spans"].append(s)
                rows[-1]["_base"] = s["_base"]
            else:
                rows.append({"spans": [s], "_base": s["_base"],
                             "_size_ref": s["size"]})

        # 2) GOUTTIÈRES : corridors blancs VERTICAUX (cf. `_column_gutters`).
        gutters = self._column_gutters(rows)

        # 3) Coupe de chaque ligne aux grands écarts (colonnes) ET aux gouttières.
        elements = []
        for row in rows:
            row_spans = sorted(row["spans"], key=lambda s: s["bbox"][0])
            gws = sorted(s["_gw"] for s in row_spans if s["_gw"] > 0)
            med_gw = gws[len(gws) // 2] if gws else 1.0
            split_gap = self._COL_SPLIT_FACTOR * med_gw
            guts = gutters.get(id(row)) or []

            def _in_gutter(a, b):
                return any(a <= 0.5 * (g0 + g1) <= b for g0, g1 in guts)

            # NB — on a tenté ici de neutraliser la coupe de largeur quand la
            # ligne porte PLUSIEURS grands blancs de taille voisine (signature
            # d'une justification lâche : « reshaped ␣␣ how ␣␣ businesses »).
            # C'est FAUX : une rangée de tableau à 3 cellules présente exactement
            # la même signature (mv21 p12/p16 : « New York City | Long Island |
            # Upstate » fusionnés en un seul paragraphe). Les deux cas sont
            # géométriquement indiscernables à l'échelle de la LIGNE — seul le
            # corridor vertical, lui, tranche. Ne pas réessayer sans un signal
            # nouveau (cellules de tableau, régularité inter-lignes).
            segment = [row_spans[0]]
            for prev, cur in zip(row_spans, row_spans[1:]):
                a, b = prev["bbox"][2], cur["bbox"][0]
                gap = b - a
                # Un écart QUELCONQUE qui tombe dans un corridor vertical est une
                # frontière de colonne, même s'il est trop étroit pour `split_gap`.
                in_gutter = _in_gutter(a, b)
                # MARQUEUR DE LISTE : à gauche de l'écart, une PUCE (« • ») ou un
                # marqueur numéroté (« 1. », « a) ») n'est pas une COLONNE — et
                # son retrait peut dépasser `split_gap` (le Handbook y échappait
                # de justesse ; un retrait un peu plus large détachait la puce de
                # son texte). Même esprit que `_gutter_sides_ok`.
                #
                # Le marqueur doit être un SYMBOLE ou un numéro PONCTUÉ : un
                # NOMBRE NU n'en est pas un — c'est une donnée (le folio à gauche
                # d'une entrée de sommaire, une cellule de tableau). Sans cette
                # réserve, le sommaire de mv21 fusionnait « 6 » avec « Chapter 1 –
                # Driver Licenses » (31 paragraphes perdus).
                left_w = prev["bbox"][2] - segment[0]["bbox"][0]
                mtxt = segment[0]["text"].strip()
                marqueur = (len(segment) == 1
                            and left_w < self._GUTTER_MIN_SIDE * med_gw
                            and (_BULLET_RE.match(mtxt + " ")
                                 or _NUMITEM_RE.match(mtxt + " ")))
                if marqueur:
                    segment.append(cur)
                    continue
                if gap > split_gap or in_gutter:
                    elements.append(self._make_text_line(segment, med_gw))
                    segment = [cur]
                else:
                    segment.append(cur)
            elements.append(self._make_text_line(segment, med_gw))
        return elements

    # ── Gouttières de colonnes (corridor blanc VERTICAL) ─────────────────────
    _GUTTER_MIN_LINES = 5      # un corridor doit persister sur ≥ 5 lignes
    _GUTTER_MIN_W = 1.2        # largeur minimale, en largeurs de glyphe
    _GUTTER_MIN_SIDE = 4.0     # texte minimal DE CHAQUE CÔTÉ, en largeurs de glyphe

    @classmethod
    def _gutter_sides_ok(cls, d, mid):
        """La ligne `d` a-t-elle du texte SUBSTANTIEL de part et d'autre de `mid` ?
        C'est ce qui distingue une frontière de COLONNE d'une indentation de PUCE
        (à gauche du corridor, une puce ne pèse qu'un glyphe)."""
        need = cls._GUTTER_MIN_SIDE * d["gw"]
        return (mid - d["x0"]) >= need and (d["x1"] - mid) >= need

    def _column_gutters(self, rows):
        """Corridors blancs VERTICAUX d'un bloc → {id(row): [(x0, x1), …]}.

        La coupe de l'étape 3 raisonne ligne par ligne, donc sur la seule LARGEUR
        du blanc. Or dans un texte JUSTIFIÉ, l'espace entre deux mots enfle
        jusqu'à rivaliser avec la gouttière : dans la démo journal, la gouttière
        entre les deux encadrés mesure 8,0 pt (2,33 × la largeur de glyphe, SOUS
        le seuil de 2,5) tandis que des espaces de mot de la même ligne atteignent
        5,8 pt (1,75 ×). Aucun seuil de largeur ne sépare proprement les deux — et
        les deux colonnes se retrouvaient entrelacées mot à mot (« Among the
        standout performers, Software / several leading semiconductor delivered
        upbeat results, with… »).

        Ce qui les sépare n'est pas la largeur mais la PERSISTANCE : un blanc de
        justification se DÉPLACE d'une ligne à l'autre (c'est le principe même de
        la justification), une gouttière reste à la MÊME abscisse sur tout le
        bloc.

        Depuis chaque blanc candidat, on REMONTE et on DESCEND le long des lignes
        voisines : le corridor s'arrête net dès qu'une ligne le TRAVERSE (du texte
        s'y trouve) ou dès que la continuité verticale se rompt. Une ligne trop
        courte pour l'atteindre ne le confirme ni ne l'infirme : on l'enjambe.

        DEUX GARDE-FOUS, sans lesquels le corridor attrape n'importe quoi :

        - `_GUTTER_MIN_SIDE` — il faut du TEXTE SUBSTANTIEL DES DEUX CÔTÉS sur
          chaque ligne membre. Sans ça, l'indentation d'une PUCE forme un corridor
          parfait sur toute une liste (le « • » du Handbook, aligné sur 10 lignes)
          et se retrouve détaché de son texte. Une colonne a du corps ; un marqueur
          de liste, non.
        - `_GUTTER_MIN_LINES` — il en faut BEAUCOUP. Dans une justification lâche
          (encadré étroit), deux ou trois blancs de mots s'alignent par hasard et
          feignent une colonne. Une vraie gouttière, elle, court sur toute la
          hauteur de son bloc.
        """
        info = []
        for row in rows:
            sp = sorted(row["spans"], key=lambda s: s["bbox"][0])
            gws = sorted(s["_gw"] for s in sp if s["_gw"] > 0)
            info.append({
                "row": row,
                "x0": sp[0]["bbox"][0], "x1": sp[-1]["bbox"][2],
                "y0": min(s["bbox"][1] for s in sp),
                "y1": max(s["bbox"][3] for s in sp),
                "free": [(a["bbox"][2], b["bbox"][0])
                         for a, b in zip(sp, sp[1:])
                         if b["bbox"][0] > a["bbox"][2]],
                "gw": gws[len(gws) // 2] if gws else 1.0,
            })
        info.sort(key=lambda d: d["y0"])

        out = {}
        for i, d in enumerate(info):
            min_w = self._GUTTER_MIN_W * d["gw"]
            for a, b in d["free"]:
                if b - a < min_w:
                    continue
                mid = 0.5 * (a + b)
                if not self._gutter_sides_ok(d, mid):
                    continue
                lo, hi = a, b
                members = [i]
                for step in (1, -1):        # descendre puis remonter le bloc
                    j, prev = i + step, d
                    while 0 <= j < len(info):
                        e = info[j]
                        h = max(prev["y1"] - prev["y0"], 1.0)
                        vgap = (e["y0"] - prev["y1"] if step == 1
                                else prev["y0"] - e["y1"])
                        if vgap > 1.5 * h:
                            break           # bloc suivant : le corridor s'arrête
                        if e["x0"] < mid < e["x1"]:
                            hit = next(((c, f) for c, f in e["free"]
                                        if c <= mid <= f), None)
                            if hit is None:
                                break       # du texte TRAVERSE : pas un corridor
                            if not self._gutter_sides_ok(e, mid):
                                break       # puce / marqueur : pas une colonne
                            lo, hi = max(lo, hit[0]), min(hi, hit[1])
                            members.append(j)
                        prev = e            # ligne trop courte : on l'enjambe
                        j += step
                if len(members) < self._GUTTER_MIN_LINES or hi - lo < min_w:
                    continue
                for k in members:
                    guts = out.setdefault(id(info[k]["row"]), [])
                    if not any(abs(g0 - lo) < 1.0 and abs(g1 - hi) < 1.0
                               for g0, g1 in guts):
                        guts.append((lo, hi))
        return out

    def _make_text_line(self, seg_spans, med_gw):
        """Construit un objet `text_line` depuis les spans d'un segment."""
        x0 = min(s["bbox"][0] for s in seg_spans)
        y0 = min(s["bbox"][1] for s in seg_spans)
        x1 = max(s["bbox"][2] for s in seg_spans)
        y1 = max(s["bbox"][3] for s in seg_spans)
        # Texte lisible : concatène les runs en insérant une espace là où
        # l'écart le justifie. Cas particulier des titres à LETTRES ESPACÉES
        # (« A D V I C E A N D … »), où chaque lettre est un span : sans
        # traitement, une espace tombe entre CHAQUE lettre → charabia illisible
        # (mauvaise entrée pour la traduction). On reconstruit alors les mots :
        # les gaps entre lettres sont réguliers, les gaps entre MOTS nettement
        # plus grands → on n'insère une espace qu'aux grands gaps.
        text = self._compose_line_text(seg_spans, med_gw)
        runs = [self._run_from_span(s) for s in seg_spans]
        return {
            "type": "text_line",
            "bbox": [x0, y0, x1, y1],
            "text": text,
            "gw": med_gw,          # largeur de glyphe médiane (estimation de mot)
            "runs": runs,
        }

    def _compose_line_text(self, seg_spans, med_gw):
        """Texte lisible d'une ligne : concatène les runs en insérant une espace
        aux écarts significatifs (> `_SPACE_FACTOR × gw`).

        L'écart se mesure LE LONG DE L'AXE D'ÉCRITURE, pas en x : pour un texte
        vertical, tous les runs partagent la même abscisse, donc l'écart en x est
        NUL et aucune espace n'était jamais insérée (« APPENDIXSECTION »). mv21
        masquait le défaut — ses runs portaient une espace de tête (« ␣O ») qui
        fournissait la frontière de mot. Un titre vertical dont la frontière est
        purement GÉOMÉTRIQUE, lui, sortait collé.
        """
        parts = []
        space_gap = self._SPACE_FACTOR * med_gw
        for i, s in enumerate(seg_spans):
            if i > 0:
                p = seg_spans[i - 1]
                dx, dy = s.get("dir") or (1, 0)
                if abs(dy) > abs(dx):           # écriture verticale
                    gap = (s["bbox"][1] - p["bbox"][3] if dy > 0
                           else p["bbox"][1] - s["bbox"][3])
                else:
                    gap = (s["bbox"][0] - p["bbox"][2] if dx >= 0
                           else p["bbox"][0] - s["bbox"][2])
                prev_t = parts[-1] if parts else ""
                if gap > space_gap and prev_t and not prev_t.endswith(" ") \
                        and not s["text"].startswith(" "):
                    parts.append(" ")
            parts.append(s["text"])
        return "".join(parts)

    @staticmethod
    def _run_from_span(s):
        return {
            "text": s["text"],
            "origin": s["origin"],
            "bbox": s["bbox"],
            "font": s["font"],
            "size": s["size"],
            "color": s["color"],
            "flags": s["flags"],
            "bold": s["bold"],
            "italic": s["italic"],
            "dir": s["dir"],
        }

    # ── Texte INCLINÉ / VERTICAL (Étape C) : regroupement le long de l'axe ────
    def _group_rotated_lines(self, spans):
        """Regroupe les spans non horizontaux en lignes le long de leur axe
        d'écriture. Pour chaque direction, on projette l'origine sur l'axe
        d'avancée `a = o·dir` et l'axe transverse `c = o·perp` : les spans de même
        `c` (même « ligne ») sont ordonnés par `a`. bbox axis-aligned ; le texte
        conserve son `dir` pour un rendu pivoté fidèle."""
        if not spans:
            return []
        out = []
        groups = defaultdict(list)
        for s in spans:
            dx, dy = s["dir"]
            groups[(round(dx, 2), round(dy, 2))].append(s)
        for (dx, dy), gspans in groups.items():
            def adv(s):
                return s["origin"][0] * dx + s["origin"][1] * dy

            def cross(s):
                return -s["origin"][0] * dy + s["origin"][1] * dx

            gspans.sort(key=lambda s: (round(cross(s), 1), adv(s)))
            rows = []
            for s in gspans:
                if rows and abs(cross(s) - rows[-1]["_c"]) <= 0.6 * max(
                        s["size"], 1.0):
                    rows[-1]["spans"].append(s)
                    rows[-1]["_c"] = cross(s)
                else:
                    rows.append({"spans": [s], "_c": cross(s)})
            for row in rows:
                seg = sorted(row["spans"], key=adv)
                out.append(self._make_rotated_line(seg, dx, dy))
        return out

    def _make_rotated_line(self, seg, dx, dy):
        x0 = min(s["bbox"][0] for s in seg); y0 = min(s["bbox"][1] for s in seg)
        x1 = max(s["bbox"][2] for s in seg); y1 = max(s["bbox"][3] for s in seg)
        return {
            "type": "text_line",
            "bbox": [x0, y0, x1, y1],
            "text": self._compose_rotated_text(seg, dx, dy),
            "gw": self._axis_gw(seg, dx, dy),
            "runs": [self._run_from_span(s) for s in seg],
            "dir": [dx, dy],
        }

    @staticmethod
    def _axis_span(s, dx, dy):
        """Empan d'un span LE LONG de son axe d'écriture (bbox axis-aligned :
        pour un texte vertical, c'est la HAUTEUR, pas la largeur)."""
        bb = s["bbox"]
        return (bb[3] - bb[1]) if abs(dy) > abs(dx) else (bb[2] - bb[0])

    @classmethod
    def _axis_gw(cls, seg, dx, dy):
        """Largeur de glyphe médiane, mesurée le long de l'axe d'écriture."""
        gws = sorted(cls._axis_span(s, dx, dy) / max(1, len(s["text"].strip()))
                     for s in seg if s["text"].strip())
        return gws[len(gws) // 2] if gws else 1.0

    @classmethod
    def _compose_rotated_text(cls, seg, dx, dy):
        """Texte lisible d'une ligne INCLINÉE / VERTICALE.

        La concaténation brute suffisait tant que le PDF portait lui-même
        l'espace de mot dans le texte d'un run (mv21 : « ␣O »). Un titre vertical
        dont la frontière de mot est purement GÉOMÉTRIQUE en sortait collé
        (« APPENDIXSECTION »), et le traducteur recevait un mot inexistant.

        Signal général : dans un titre à lettres espacées, les pas entre LETTRES
        sont réguliers ; le pas entre MOTS est nettement plus grand. On insère
        donc une espace là où l'écart, mesuré LE LONG DE L'AXE, dépasse
        franchement le pas médian — jamais entre les lettres d'un même mot.
        """
        if len(seg) < 2:
            return "".join(s["text"] for s in seg)
        gaps = []
        for p, s in zip(seg, seg[1:]):
            pb, sb = p["bbox"], s["bbox"]
            if abs(dy) > abs(dx):
                gaps.append(sb[1] - pb[3] if dy > 0 else pb[1] - sb[3])
            else:
                gaps.append(sb[0] - pb[2] if dx >= 0 else pb[0] - sb[2])
        pos = sorted(g for g in gaps if g > 0)
        median = pos[len(pos) // 2] if pos else 0.0
        parts = [seg[0]["text"]]
        for g, s in zip(gaps, seg[1:]):
            # Frontière de MOT : un écart franchement supérieur au pas courant.
            if (median > 0 and g > 1.6 * median
                    and not parts[-1].endswith(" ")
                    and not s["text"].startswith(" ")):
                parts.append(" ")
            parts.append(s["text"])
        return "".join(parts)

    # ── Numéros ANCRÉS À DROITE (sommaire sans points de conduite) ───────────
    _TRAILNUM_RE = re.compile(r"^\d{1,4}$")

    def _split_trailing_numbers(self, lines):
        """Détache en ligne séparée le NUMÉRO DE PAGE final d'une entrée de
        sommaire quand l'écart avec le texte est trop petit pour la coupe en
        colonnes. Signal GÉNÉRAL (aucun seuil de document) : plusieurs lignes
        voisines terminent par un nombre nu dont les bords droits S'ALIGNENT —
        c'est la colonne de folios d'un sommaire. Sans cette coupe, le numéro
        est avalé par le paragraphe et perd son fer à droite au reflow."""
        cands = []                              # (idx, k, right) k = 1er run du n°
        for i, ln in enumerate(lines):
            runs = ln.get("runs") or []
            if len(runs) < 2:
                continue
            d = ln.get("dir")
            if d and abs(d[0] - 1) > 1e-3:      # incliné/vertical : ne pas toucher
                continue
            tail = runs[-1]
            txt = (tail.get("text") or "").strip()
            if not self._TRAILNUM_RE.match(txt):
                continue
            prev = runs[-2]
            gw = ln.get("gw") or ((tail.get("size") or 10) * 0.5)
            gap = tail["bbox"][0] - prev["bbox"][2]
            if gap < 1.2 * gw:                  # collé au texte : nombre du texte
                continue
            cands.append((i, len(runs) - 1, round(tail["bbox"][2], 1)))
        if not cands:
            return lines
        # Cluster par bord droit (± 2.5 pt) : il faut ≥ 2 folios alignés. Les
        # folios DÉJÀ autonomes (lignes-nombres séparées par la coupe en
        # colonnes) comptent dans l'alignement : c'est la colonne de folios.
        from collections import Counter
        edges = Counter()
        for _i, _k, r in cands:
            edges[r] += 1
        for ln in lines:
            t = (ln.get("text") or "").strip()
            if self._TRAILNUM_RE.match(t) and ln.get("bbox"):
                edges[round(ln["bbox"][2], 1)] += 1
        out = list(lines)
        for i, k, r in cands:
            aligned = sum(n for e, n in edges.items() if abs(e - r) <= 2.5)
            if aligned < 2:
                continue
            ln = out[i]
            runs = ln["runs"]
            num_runs, txt_runs = runs[k:], runs[:k]
            if not txt_runs:
                continue
            def _mk(rs):
                x0 = min(x["bbox"][0] for x in rs)
                y0 = min(x["bbox"][1] for x in rs)
                x1 = max(x["bbox"][2] for x in rs)
                y1 = max(x["bbox"][3] for x in rs)
                return {**ln, "bbox": [x0, y0, x1, y1], "runs": rs,
                        "text": " ".join((x.get("text") or "").strip()
                                         for x in rs).strip()}
            out[i] = _mk(txt_runs)
            out.append(_mk(num_runs))
        return out

    # ── Regroupement en PARAGRAPHES (étape 7 : géométrie + linguistique) ─────
    def _group_paragraphs(self, lines, ctx=None):
        """Regroupe des lignes (`text_line`) en paragraphes. Column-aware :
        deux lignes ne fusionnent que si elles se chevauchent horizontalement
        (même colonne). La priorité va à la GÉOMÉTRIE (interligne) ; les signaux
        faibles (indentation, ponctuation) n'agissent qu'en cas d'écart élevé.
        Retourne des objets `paragraph` { bbox, text, lines:[...] }."""
        if not lines:
            return []
        lines = self._split_trailing_numbers(lines)
        items = [self._line_metrics(ln) for ln in lines]
        self._assign_column_margins(items, ctx)
        self._tag_cells(items, ctx)
        items.sort(key=lambda it: (round(it["top"], 1), it["left"]))

        paras = []
        for it in items:
            # Candidats parents : paragraphes ouverts dont la dernière ligne est
            # AU-DESSUS et chevauche horizontalement (même colonne).
            cands = []
            for p in paras:
                last = p["items"][-1]
                if last.get("cell") != it.get("cell"):    # cloisonnement table
                    continue
                if last["bottom"] > it["top"] + 0.5 * it["size"]:
                    continue
                ov = min(last["right"], it["right"]) - max(last["left"], it["left"])
                minw = max(1.0, min(last["right"] - last["left"],
                                    it["right"] - it["left"]))
                if ov <= 0.3 * minw:
                    continue
                cands.append(p)
            # Parent = la ligne au-dessus la PLUS PROCHE verticalement (flot de
            # lecture). Choisir « le plus aligné à gauche » cassait le texte qui
            # s'enroule autour d'une image/encart : une ligne revenue à gauche
            # était rattachée à l'encart voisin (gauche proche) au lieu du
            # paragraphe qu'elle continue, puis coupée (grand écart à l'encart).
            parent = None
            if cands:
                parent = min(cands,
                             key=lambda p: it["base"] - p["items"][-1]["base"])
            if parent is not None and not self._para_break(parent, it, ctx):
                parent["items"].append(it)
                parent["left_min"] = min(parent["left_min"], it["left"])
                parent["right_max"] = max(parent["right_max"], it["right"])
            else:
                paras.append({"items": [it], "left_min": it["left"],
                              "right_max": it["right"]})

        out = []
        for p in paras:
            its = p["items"]
            x0 = min(i["left"] for i in its); y0 = min(i["top"] for i in its)
            x1 = max(i["right"] for i in its); y1 = max(i["bottom"] for i in its)
            out.append({
                "type": "paragraph",
                "bbox": [x0, y0, x1, y1],
                "text": self._join_para_text([i["text"] for i in its]),
                "lines": [i["line"] for i in its],
            })
        out.sort(key=lambda e: (round(e["bbox"][1], 1), e["bbox"][0]))
        if self.expand_paragraphs and ctx is not None:
            self._expand_paragraphs(out, ctx)
        return out

    # ── Étape D : expansion du CONTENEUR vers la droite ──────────────────────
    _COL_SIB_TOL = 12.0    # tolérance « voisin de la même colonne »

    def _expand_paragraphs(self, paras, ctx):
        """Élargit la zone utilisable de chaque paragraphe **au paragraphe ENTIER**
        (bord droit UNIFORME = minimum disponible sur toute la hauteur → aucune
        ligne ne dépasse dans un autre bloc).

        Colonne d'un paragraphe = paragraphes qui le **chevauchent horizontalement**
        (pas seulement de même marge gauche) : une ligne indentée/centrée référence
        ainsi la vraie marge de la colonne. Sécurité multi-colonnes conservée par
        la règle de côté : seuls les voisins qui **commencent à gauche** de `p`
        définissent sa marge DROITE (une colonne voisine, qui démarre à droite,
        n'est jamais prise comme référence — elle borne via `right_block`).

        Bord droit cible :
        - **objet/colonne à droite** dans la bande → `bord − gouttière` (sécurité) ;
        - sinon **marge droite de la colonne** ; sinon (isolé) marge symétrique.

        Texte **CENTRÉ** (marges gauche/droite ~égales et larges dans la colonne) :
        conteneur = colonne entière et rendu **recentré** (`align=center`) → reste
        centré tout en occupant l'espace. Sinon bord gauche figé, rendu ferré à
        gauche. Ne modifie ni le texte ni sa position d'origine."""
        page_w = ctx.get("width") or 0
        obstacles = ctx.get("expand_obstacles") or ctx.get("obstacles", ())
        boxed = [p for p in paras if p.get("bbox") and len(p["bbox"]) >= 4]
        tol = self._COL_SIB_TOL

        # Bloqueurs à la granularité LIGNE : le bbox d'un paragraphe enroulé
        # (lignes pleine largeur incluses) commence à la marge de colonne et ne
        # serait jamais « à droite » d'une ligne d'encart — ce sont ses LIGNES
        # qui matérialisent l'espace réellement occupé à chaque hauteur.
        blocker_lines = []
        for q in boxed:
            for ln in q.get("lines", []):
                bb = ln.get("bbox")
                if bb and len(bb) >= 4:
                    blocker_lines.append((q, bb))

        for p in boxed:
            pleft, ptop, pright, pbottom = p["bbox"]
            size = max((r.get("size", 0) or 0 for ln in p.get("lines", [])
                        for r in ln.get("runs", [])), default=10.0)
            safety = self._safe_gutter(size)

            # Objet le plus proche à droite sur TOUTE la bande verticale.
            right_block = float("inf")
            for q in boxed:
                if q is p:
                    continue
                qb = q["bbox"]
                if qb[3] <= ptop or qb[1] >= pbottom:
                    continue
                if qb[0] >= pright - 0.5 and qb[0] < right_block:
                    right_block = qb[0]
            for ox0, oy0, ox1, oy1 in obstacles:
                if oy1 <= ptop or oy0 >= pbottom:
                    continue
                if ox0 >= pright - 0.5 and ox0 < right_block:
                    right_block = ox0

            # Objet le plus proche à GAUCHE — symétrique. Il n'existait pas :
            # l'expansion n'allait jamais vers la gauche, et le conteneur d'un
            # bloc CENTRÉ partait du bord de colonne sans s'arrêter au premier
            # objet à sa gauche. Il borne désormais le côté gauche d'un bloc
            # centré (et, à terme, d'un bloc ferré à droite).
            left_block = float("-inf")
            for q in boxed:
                if q is p:
                    continue
                qb = q["bbox"]
                if qb[3] <= ptop or qb[1] >= pbottom:
                    continue
                if qb[2] <= pleft + 0.5 and qb[2] > left_block:
                    left_block = qb[2]
            for ox0, oy0, ox1, oy1 in obstacles:
                if oy1 <= ptop or oy0 >= pbottom:
                    continue
                if ox1 <= pleft + 0.5 and ox1 > left_block:
                    left_block = ox1

            # Marges de la COLONNE via les paragraphes qui chevauchent p.
            col_left, col_right = pleft, pright
            has_col_sibling = False
            for q in boxed:
                if q is p:
                    continue
                qb = q["bbox"]
                if min(qb[2], pright) - max(qb[0], pleft) <= 0.5:
                    continue                            # pas de chevauchement
                if qb[0] <= pleft + tol:                # commence à gauche → marge droite
                    col_right = max(col_right, qb[2])
                    has_col_sibling = True              # p appartient à une colonne
                if qb[2] >= pright - tol:               # finit à droite → marge gauche
                    col_left = min(col_left, qb[0])

            # ── CADRE DE RÉFÉRENCE DE L'ALIGNEMENT ──────────────────────────
            # Un alignement n'a de sens que DANS UNE BOÎTE. Hiérarchie, du plus
            # serré au plus lâche : cellule de tableau → boîte contenante
            # (panneau, encadré) → colonne. Sans elle, une cellule de tableau
            # prend la LARGEUR DE PAGE pour cadre et son texte paraît centré
            # (démo : « Fiches produits et avis clients », blancs de 219 et
            # 227 pt vers les bords de la PAGE → faux centrage).
            # NB : ce cadre sert au DIAGNOSTIC d'alignement ; l'expansion du
            # conteneur garde sa propre logique (`ref_right`), déjà bornée par
            # les cellules et les boîtes plus bas.
            page_area = (page_w or 1.0) * (ctx.get("height") or 1.0)
            pcx, pcy = (pleft + pright) / 2, (ptop + pbottom) / 2
            frame = None
            frame_dur = False          # cadre AUTORITAIRE (cellule / boîte) ?
            for cx0, cy0, cx1, cy1 in ctx.get("cells", ()):
                if cx0 <= pcx <= cx1 and cy0 <= pcy <= cy1:
                    frame = (cx0 + 2.0, cx1 - 2.0)
                    frame_dur = True
                    break
            if frame is None:
                box, box_area = None, None
                for ob in obstacles:
                    if (ob[0] <= pleft + 2 and ob[1] <= ptop + 2
                            and ob[2] >= pright - 2 and ob[3] >= pbottom - 2):
                        area = (max(0.0, ob[2] - ob[0])
                                * max(0.0, ob[3] - ob[1]))
                        if area < 0.6 * page_area and (box_area is None
                                                       or area < box_area):
                            box, box_area = ob, area
                if box is not None:
                    frame = (box[0], box[2])
                    frame_dur = True
            if frame is None:
                # OBJET D'ANCRAGE — une LÉGENDE n'est ni dans une cellule ni dans
                # une boîte : l'objet qu'elle légende est AU-DESSUS d'elle (ou en
                # dessous), pas autour. Sans ce barreau, son cadre retombe sur la
                # « colonne », polluée jusqu'à la largeur de page par le moindre
                # bandeau ou titre pleine largeur — et une légende parfaitement
                # centrée sous SON image paraît ferrée à gauche (démo : les trois
                # légendes de photo ; seule celle du milieu s'en tirait, par
                # ACCIDENT, son image étant centrée sur l'axe de la page).
                # Ancre = objet vertically ADJACENT dont l'empan CONTIENT p ; le
                # plus petit gagne. Les fonds quasi pleine page sont ignorés.
                # Une ancre doit avoir de la SURFACE. `expand_obstacles` contient
                # les dessins décomposés en items : sans cette exigence, un simple
                # FILET d'un demi-point posé au-dessus d'un paragraphe lui servirait
                # de cadre (mesuré : mv21 p12, un paragraphe de corps basculait de
                # `left` à `justify` parce qu'un filet élargissait sa « colonne »).
                # C'est la distinction déjà tranchée en P12 : un filet SÉPARE, il ne
                # CONTIENT pas. Une légende s'adosse à une photo, un panneau — un
                # objet haut d'au moins une ligne de texte.
                gap_max = max(6.0, 1.5 * size)
                h_min = max(8.0, 1.5 * size)
                anc, anc_area = None, None
                for ob in obstacles:
                    if not (ob[0] <= pleft + 2.0 and ob[2] >= pright - 2.0):
                        continue
                    if ob[3] - ob[1] < h_min:
                        continue                        # filet, pas un contenant
                    above = 0.0 <= ptop - ob[3] <= gap_max
                    below = 0.0 <= ob[1] - pbottom <= gap_max
                    if not (above or below):
                        continue
                    area = (max(0.0, ob[2] - ob[0]) * max(0.0, ob[3] - ob[1]))
                    if area >= 0.6 * page_area:
                        continue
                    if anc_area is None or area < anc_area:
                        anc, anc_area = ob, area
                # UNE ANCRE NE PEUT QUE RESSERRER LE CADRE, JAMAIS L'ÉLARGIR.
                # C'est la loi de la hiérarchie elle-même (du plus serré au plus
                # lâche) : un barreau qui DÉBORDE la colonne n'est pas un cadre
                # plus fin, c'est un objet qui passe par là. Sans elle, un grand
                # aplat de fond qui s'arrête juste au-dessus d'un paragraphe de
                # corps lui servait de cadre : sa « colonne » passait de 175 à
                # 361 pt, le seuil de justification suivait, et un paragraphe en
                # drapeau était étiré au fer (mesuré : mv21 p12).
                # AUCUNE EXCEPTION, même sans voisin de colonne : un cadre doit
                # être PROUVÉ plus serré. Sans colonne, la preuve n'existe pas,
                # donc on ne touche à rien. Limite assumée : une légende sans le
                # moindre voisin de colonne (figure seule sur sa page) reste
                # ferrée à gauche — on préfère la manquer plutôt que d'étirer un
                # paragraphe de corps sous un aplat large.
                if (anc is not None
                        and (anc[2] - anc[0]) > (col_right - col_left) - 1.0):
                    anc = None
                if anc is not None:
                    frame = (anc[0], anc[2])
                    frame_dur = True
            if frame is None:
                frame = (col_left, col_right)
            f_left, f_right = frame
            if f_right - f_left < 1.0:                  # cadre dégénéré
                f_left, f_right = col_left, col_right

            col_w = max(1.0, f_right - f_left)
            lg, rg = pleft - f_left, f_right - pright
            min_gap = max(14.0, 0.08 * col_w)
            col_center, para_center = (f_left + f_right) / 2, (pleft + pright) / 2
            near_center = abs(para_center - col_center) <= 0.12 * col_w
            # Le vrai signe du CENTRAGE : les bords GAUCHES des lignes varient
            # fortement (chaque ligne recentrée), pas les marges du bloc. Un bloc
            # ferré à gauche (même une puce courte) garde des gauches ~constantes.
            # Un bloc (paragraphe, image, dessin) qui chevauche PARTIELLEMENT le
            # bbox du paragraphe (encart imbriqué, photo d'enroulement) explique
            # des gauches variables : c'est un ENROULEMENT, jamais un centrage.
            # Un bloc qui ENGLOBE le paragraphe (fond plein, bandeau) ne compte
            # pas — un titre centré sur fond coloré reste détectable centré.
            def _partial_overlap(bb):
                ox = min(bb[2], pright) - max(bb[0], pleft)
                oy = min(bb[3], pbottom) - max(bb[1], ptop)
                if ox <= 1.0 or oy <= 1.0:
                    return False
                contains = (bb[0] <= pleft + 2 and bb[1] <= ptop + 2
                            and bb[2] >= pright - 2 and bb[3] >= pbottom - 2)
                return not contains
            wrapped = any(_partial_overlap(q["bbox"]) for q in boxed if q is not p)
            if not wrapped:
                wrapped = any(_partial_overlap(ob) for ob in obstacles)

            lbb = [ln["bbox"] for ln in p.get("lines", [])
                   if ln.get("bbox") and len(ln["bbox"]) >= 4]
            ferre_droite = False
            if len(lbb) >= 2:
                # TAXONOMIE PAR VARIANCES — le bord le plus STABLE d'un paragraphe
                # trahit son alignement :
                #     vG minimale -> ferré à GAUCHE       vD minimale -> à DROITE
                #     vC minimale -> CENTRÉ               vG et vD ~0 -> JUSTIFIÉ
                # Elle est SANS CADRE, donc immunisée à une colonne mal estimée
                # (un bandeau pleine largeur élargit la « colonne » d'un article
                # et ruine tout calcul de marge — mesuré sur la démo).
                lefts = [b[0] for b in lbb]
                rights = [b[2] for b in lbb]
                cents = [(b[0] + b[2]) / 2 for b in lbb]
                vg = max(lefts) - min(lefts)
                vd = max(rights) - min(rights)
                vc = max(cents) - min(cents)
                largeur = max(1.0, max(rights) - min(lefts))
                tol_b = max(2.5, 0.25 * size)          # « à fleur » : ~¼ de cadratin
                # Un bord n'est « franchement déchiqueté » que s'il varie BEAUCOUP.
                # Sans cette exigence, deux lignes d'un item à puce qui finissent
                # par hasard au même bord droit passeraient pour ferrées à droite
                # (mesuré sur mv21) — or c'est le lot de tout texte qui remplit sa
                # ligne.
                franc = max(4.0 * tol_b, 0.12 * largeur)
                min_left = min(lefts)
                at_left = sum(1 for l in lefts if l - min_left <= 3.0)
                left_aligned = at_left >= 0.6 * len(lefts)

                # ORDRE DE DÉCISION — le FER À DROITE se juge EN PREMIER, sinon un
                # bloc ferré à droite passe pour JUSTIFIÉ : ses bords droits sont
                # à fleur, et il suffit que deux de ses bords gauches tombent près
                # l'un de l'autre par hasard pour qu'il paraisse ferré à gauche
                # (adresse du test synthétique : gauches 438 / 440 / 467 → le
                # comptage majoritaire concluait « ferré à gauche »). Le VRAI
                # discriminant est le bord GAUCHE : déchiqueté pour un fer à
                # droite, à fleur pour un justifié.
                ferre_droite = (not wrapped and vd <= tol_b and vg >= franc)

                # `near_center` n'intervient PAS ici : il exigerait que le bloc
                # soit centré sur l'axe de sa COLONNE — or cette colonne est
                # souvent polluée (un bandeau pleine largeur l'élargit). Des
                # lignes qui partagent un axe commun (vC ≈ 0) alors que leurs DEUX
                # bords sont déchiquetés sont centrées, que cet axe coïncide ou
                # non avec celui de la colonne. La variance se suffit à elle-même ;
                # le cadre ne sert qu'au mono-ligne, où il n'y a pas de variance.
                centered = (not ferre_droite and not wrapped
                            and vc <= tol_b and vg >= franc and vd >= franc)

                # JUSTIFIÉ (inchangé) : ferré à gauche ET lignes INTÉRIEURES au
                # même bord droit (la dernière est libre). ≥ 3 lignes.
                if (not ferre_droite and not centered
                        and left_aligned and len(lbb) >= 3):
                    inner_rights = [b[2] for b in lbb[:-1]]
                    spread = max(inner_rights) - min(inner_rights)
                    justified = spread <= max(0.5 * size, 0.03 * col_w)
                else:
                    justified = False
            else:
                # MONO-LIGNE — pas de variance interne : deux signaux distincts.
                #
                # (a) CENTRÉ : la SYMÉTRIE des deux blancs dans SON cadre, pas
                # leur taille. L'ancien seuil (`0.18 × col_w`) rejetait un bloc
                # parfaitement symétrique parce que ses marges valaient 17,5 % de
                # la colonne au lieu de 18 % (démo : « BREAKING NEWS », blancs de
                # 92,8 pt de CHAQUE côté, raté de 2,8 pt). On assouplit la taille
                # exigée et on DURCIT la symétrie en échange.
                big = max(min_gap, 0.10 * col_w)
                sym = max(4.0, 0.03 * col_w)
                centered = (lg > big and rg > big and near_center
                            and abs(lg - rg) <= sym)
                # (b) FERRÉ À DROITE : une ligne SEULE ne peut rien dire d'elle-
                # même — c'est sa PILE qui parle. Un bloc ferré à droite (adresse,
                # date, colonne de folios) est découpé en lignes autonomes par
                # l'Étape A, ses retours étant volontaires. On le reconnaît à ce
                # que ses voisines VERTICALES partagent son bord DROIT tandis que
                # leurs bords gauches se dispersent.
                tol_b = max(2.5, 0.25 * size)
                # Un bloc ferré à droite est ADOSSÉ au bord droit de son cadre —
                # c'est le sens même de « ferré à droite ». Sans cette exigence,
                # deux lignes quelconques dont les bords droits coïncident (une
                # pile de 2 est une preuve mince) suffisaient : l'en-tête courant
                # de mv21, « 6 | Driver's Manual », collé à la marge GAUCHE de sa
                # page, passait pour ferré à droite.
                colle_droite = pright >= f_right - max(2.0 * safety, 0.05 * col_w)
                if not centered and not wrapped and colle_droite:
                    pile_r, pile_l = [pright], [pleft]
                    h = max(1.0, pbottom - ptop)
                    for q in boxed:
                        if q is p:
                            continue
                        qb = q["bbox"]
                        if min(qb[2], pright) - max(qb[0], pleft) <= 0.5:
                            continue                    # pas la même colonne
                        if qb[1] - pbottom > 2.5 * h or ptop - qb[3] > 2.5 * h:
                            continue                    # hors de la pile
                        pile_r.append(qb[2])
                        pile_l.append(qb[0])
                    if len(pile_r) >= 2:                # p + au moins 1 voisine
                        vd_p = max(pile_r) - min(pile_r)
                        vg_p = max(pile_l) - min(pile_l)
                        # « Franc » se mesure sur la largeur de LA PILE, pas sur
                        # celle de la colonne (une pile étroite adossée à la marge
                        # droite d'une page large ne pourrait jamais l'atteindre).
                        larg_p = max(1.0, max(pile_r) - min(pile_l))
                        ferre_droite = (vd_p <= tol_b and vg_p >=
                                        max(4.0 * tol_b, 0.12 * larg_p))
                justified = False

            # Bord droit de référence. La marge SYMÉTRIQUE n'est un repli que si
            # le paragraphe est VRAIMENT SEUL (aucun voisin de colonne) : sinon,
            # même s'il définit lui-même la marge droite de sa colonne
            # (col_right == pright), on reste à cette marge (pas de débordement).
            if col_right > pright + 0.5:
                ref_right = col_right
            elif has_col_sibling or centered:
                ref_right = col_right
            else:
                ref_right = page_w - pleft if page_w else pright
            if right_block != float("inf"):
                ref_right = min(ref_right, right_block - safety)
            # Cloisonnement : un paragraphe DANS une cellule de tableau ne
            # s'étend jamais au-delà du mur droit de SA cellule (y compris la
            # dernière colonne, qui n'a pas de cellule voisine pour la borner).
            pcx, pcy = (pleft + pright) / 2, (ptop + pbottom) / 2
            for cx0, cy0, cx1, cy1 in ctx.get("cells", ()):
                if cx0 <= pcx <= cx1 and cy0 <= pcy <= cy1:
                    ref_right = min(ref_right, cx1 - 2.0)
                    break
            # Boîte CONTENANTE (cellule dessinée en boîte pleine, panneau,
            # encadré) : un paragraphe dessiné DANS une boîte reste dans sa
            # boîte — bord droit = bord de boîte moins un padding symétrique au
            # padding gauche constaté. La plus PETITE boîte contenante gagne
            # (la boîte-cellule avant la boîte-tableau) ; les fonds quasi
            # pleine page sont ignorés.
            best_box, best_area = None, None
            for ob in obstacles:
                if (ob[0] <= pleft + 2 and ob[1] <= ptop + 2
                        and ob[2] >= pright - 2 and ob[3] >= pbottom - 2):
                    area = max(0.0, ob[2] - ob[0]) * max(0.0, ob[3] - ob[1])
                    if area < 0.6 * page_area and (best_area is None
                                                   or area < best_area):
                        best_box, best_area = ob, area
            if best_box is not None:
                pad = max(2.0, min(pleft - best_box[0], 8.0))
                ref_right = min(ref_right, best_box[2] - pad)
            ref_right = max(ref_right, pright)          # jamais rétrécir

            # Conteneur d'un bloc CENTRÉ : il s'étend des DEUX côtés, borné par le
            # premier objet de CHAQUE côté (l'« espace disponible » réel).
            #
            # Avant, un bloqueur à droite faisait `centered = False` : l'objet ne
            # bornait pas le conteneur, il DÉTRUISAIT l'alignement. Or dans une
            # page multi-colonnes tout est borné à droite — donc toute légende,
            # tout bandeau centré dans sa colonne repassait ferré à gauche (démo :
            # « Trading floor at the New York Stock Exchange », blancs de 201 pt
            # de chaque côté, rendu à gauche). Un bloqueur BORNE, il n'annule pas.
            # Texte INCLINÉ / VERTICAL : toute cette géométrie est mesurée en x,
            # or pour lui l'axe d'écriture est l'AUTRE. Les blancs gauche/droite
            # y sont l'espace TRANSVERSE — les interpréter comme un alignement
            # n'a aucun sens (les en-têtes pivotés de la démo ressortaient
            # « centrés »). On ne conclut rien : le rendu part de l'origine
            # source, ce qui est fidèle.
            if not self._para_is_horizontal(p):
                centered = justified = ferre_droite = False

            # Le CADRE BORNE AUSSI LE CONTENEUR — mais seulement quand
            # l'alignement s'y adosse. Diagnostiquer « centré » sans borner
            # l'expansion ne sert à rien : la légende serait recentrée dans un
            # conteneur qui déborde son image (démo : « Shoppers return… »
            # recentrée dans [87 ; 352] au lieu de [32 ; 288] — axe faux de 60 pt).
            # Restreint à centré/ferré à droite pour NE RIEN AMPUTER : un corps de
            # texte ferré à gauche sous une image large garde sa colonne entière.
            # No-op pour les cellules et les boîtes (déjà bornées plus haut) : ce
            # verrou ne mord que sur le nouveau barreau d'ANCRAGE.
            if frame_dur and (centered or ferre_droite):
                ref_right = max(min(ref_right, f_right), pright)

            # Bord GAUCHE du conteneur. Il ne bouge que si l'alignement l'exige :
            #   centré  → s'étend des DEUX côtés (espace disponible réel) ;
            #   droite  → s'étend vers la GAUCHE (le texte croît vers la gauche) ;
            #   gauche/justifié → bord gauche FIGÉ (comportement historique).
            if ferre_droite:
                # Le texte croît vers la GAUCHE : on lui donne le blanc réel de ce
                # côté — jusqu'au 1er objet à sa gauche, sinon jusqu'à la marge
                # SYMÉTRIQUE (miroir exact du `page_w - pleft` dont bénéficie un
                # bloc ferré à gauche vraiment seul). `col_left` ne convient pas :
                # il reste collé au bord du bloc quand aucun voisin ne finit à sa
                # droite (cas d'un bloc adossé à la marge droite de la page).
                if left_block != float("-inf"):
                    lo = left_block + safety
                elif page_w:
                    lo = max(0.0, page_w - pright)
                else:
                    lo = pleft
                if frame_dur:
                    lo = max(lo, f_left)                # cellule / boîte : borne dure
                left_edge = min(lo, pleft)              # jamais rétrécir la ligne
            elif centered:
                left_edge = f_left                      # bord gauche de SON cadre
                if left_block != float("-inf"):
                    left_edge = max(left_edge, left_block + safety)
                left_edge = min(left_edge, pleft)       # jamais rétrécir la ligne
            else:
                left_edge = None
            # FERRÉ À DROITE : le bord droit est le point FIXE — on n'étend pas
            # vers la droite (ce serait déplacer le fer). Symétrique exact du
            # bord gauche figé d'un texte ferré à gauche.
            if ferre_droite:
                ref_right = pright
            align_mode = ("center" if centered
                          else "right" if ferre_droite
                          else "justify" if justified else "left")

            # Bord droit cible PAR LIGNE : chaque bande y s'arrête au 1er
            # bloqueur (paragraphe TEXTE compris, image, dessin) qui commence à
            # droite d'ELLE — un encart imbriqué dans l'empan du paragraphe
            # borne ainsi les lignes qui le côtoient, sans priver les autres
            # lignes de l'expansion (l'escalier en L est préservé ; le reflow
            # sait le suivre via `_bounds_at`).
            def _line_target(lb):
                blk = float("inf")
                for q, qb in blocker_lines:
                    if q is p:
                        continue
                    if qb[3] <= lb[1] or qb[1] >= lb[3]:
                        continue
                    if qb[0] >= lb[2] - 0.5 and qb[0] < blk:
                        blk = qb[0]
                for ox0, oy0, ox1, oy1 in obstacles:
                    if oy1 <= lb[1] or oy0 >= lb[3]:
                        continue
                    if ox0 >= lb[2] - 0.5 and ox0 < blk:
                        blk = ox0
                t = ref_right
                if blk != float("inf"):
                    t = min(t, blk - safety)
                return max(t, lb[2])                    # jamais rétrécir la ligne
            self._set_container(p, left_edge, _line_target, align_mode)

    def _set_container(self, p, left_edge, target_right, align):
        """Pose `container_lines`/`container_bbox`. `left_edge` None = garder le
        bord gauche de chaque ligne (ferré à gauche) ; sinon bord gauche commun
        (colonne, pour un bloc centré). `target_right` : bord droit commun, ou
        une FONCTION bbox_ligne -> bord droit (cible par ligne, escalier
        préservé autour d'un encart). `align` mémorisé pour le rendu."""
        pleft, ptop, pright, pbottom = p["bbox"]
        clines = []
        for ln in p.get("lines", []):
            bb = ln.get("bbox")
            if not bb or len(bb) < 4:
                continue
            lx0 = left_edge if left_edge is not None else bb[0]
            tr = target_right(bb) if callable(target_right) else target_right
            clines.append([lx0, bb[1], max(tr, bb[2]), bb[3]])
        if clines:
            p["container_lines"] = clines
            cl = left_edge if left_edge is not None else pleft
            p["container_bbox"] = [min(cl, pleft), ptop,
                                   max(c[2] for c in clines), pbottom]
            p["align"] = align

    def _safe_gutter(self, size):
        """Gouttière de sécurité normalisée (recommandation typographique) :
        proportionnelle au corps, avec un plancher en points."""
        return max(self._SAFE_GUTTER_MIN,
                   self._SAFE_GUTTER_FACTOR * (size or 10.0))

    @staticmethod
    def _line_metrics(ln):
        bb = ln["bbox"]
        runs = ln.get("runs", [])
        sizes = [r.get("size", 0) or 0 for r in runs]
        size = max(sizes) if sizes else 0
        base = max((r["origin"][1] for r in runs
                    if r.get("origin")), default=bb[3])
        total = sum(len(r.get("text", "")) for r in runs) or 1
        bold_chars = sum(len(r.get("text", "")) for r in runs if r.get("bold"))
        return {
            "line": ln, "text": ln.get("text", ""),
            "left": bb[0], "right": bb[2], "top": bb[1], "bottom": bb[3],
            "base": base, "size": size or 1.0,
            "gw": ln.get("gw") or ((size or 1.0) * 0.5),
            "bold": (bold_chars / total) >= 0.6,
        }

    def _para_break(self, parent, it, ctx=None):
        """True si `it` doit démarrer un NOUVEAU paragraphe (coupe).

        Hiérarchie stricte (la géométrie prime, cf. cas texte enroulé) :
          1. signaux DURS : liste, changement de style, gros saut vertical ;
          2. continuation certaine (minuscule) → fusion, prioritaire sur A ;
          3. règle « espace restant » (Étape A, togglable) : si le 1er mot de la
             ligne suivante AURAIT PU tenir dans l'espace libre à droite de la
             ligne précédente (jusqu'au 1er bloqueur : marge, colonne, image,
             dessin), c'est un retour à la ligne VOLONTAIRE → coupe ;
          4. flot continu : sous le seuil « modéré », on FUSIONNE toujours —
             peu importe x0 (enroulement) ou une ponctuation faible ;
          5. zone modérée : indentation d'alinéa, puis (gap plus élevé)
             ponctuation forte + majuscule — sauf conjonction / minuscule.
        """
        last = parent["items"][-1]
        size_ref = max(last["size"], it["size"], 1.0)
        g = (it["base"] - last["base"]) / size_ref

        # Règle d'ORTHOGRAPHE : la ligne précédente finit-elle EN PLEINE PHRASE ?
        # Si elle se termine par un mot NON TERMINAL (article, préposition,
        # conjonction, auxiliaire : the, a, to, from, and, is… / le, de, à, et…)
        # ou un tiret, la suivante est une CONTINUATION certaine — jamais un
        # nouveau paragraphe (gère « …from the / U.S. », « (MV- / 44) »…).
        last_txt = last["text"].rstrip()
        # « / » final = énumération coupée (« Statement/ | Record ») : même
        # continuation certaine qu'un tiret.
        parent_continues = (last_txt.endswith("-") or last_txt.endswith("/")
                            or _last_word(last_txt) in _NON_TERMINAL)

        # ── 1. Signaux DURS ─────────────────────────────────────────────────
        if _BULLET_RE.match(it["text"]):               # puce → toujours coupe
            return True
        # Numéro/lettre en tête = vrai item de liste SEULEMENT si la phrase
        # précédente est finie ; sinon c'est un nombre du texte (« age is 16. »,
        # « (MV-44) ») → pas de coupe.
        if _NUMITEM_RE.match(it["text"]) and not parent_continues \
                and _ends_sentence(last_txt):
            return True
        if last["bold"] != it["bold"]:                 # titre gras vs corps
            return True
        if abs(last["size"] - it["size"]) > self._PARA_SIZE_FACTOR * size_ref:
            return True                                # changement de taille
        if g > self._PARA_GAP_FACTOR:                  # gros saut vertical
            return True

        # Ligne suivante en minuscule → continuation certaine → fusion.
        # (Prioritaire : un mot en minuscule n'ouvre quasi jamais un paragraphe,
        # même si l'espace restant aurait pu l'accueillir.)
        lower_next = _starts_lower(it["text"])
        # Continuation certaine aussi si la ligne démarre par une conjonction de
        # coordination (« And », « But »… ou son symbole « & »/« + ») ou si la
        # ligne précédente finit en pleine phrase (mot non terminal).
        # Un NOMBRE NU suivi d'un mot en minuscule (« 17 ans avec… », « 26 000
        # livres ») est une continuation de MESURE, jamais un début d'item.
        measure_next = bool(re.match(r"^\d[\d\s., ]*\s+[a-zà-ÿ]",
                                     it["text"]))
        continues = (lower_next or measure_next or _starts_coord(it["text"])
                     or parent_continues)

        # ── A. Coupe « espace restant » (tie-breaker géométrique, togglable) ─
        # Appliquée AVANT le flot continu : elle discrimine un vrai saut de
        # paragraphe d'un simple retour à la ligne, y compris à interligne
        # normal. En texte justifié/ferré, le mot suivant ne rentre jamais dans
        # le reliquat (c'est pourquoi il a wrappé) → fusion ; seul un mot qui
        # « aurait pu tenir » trahit une coupe voulue.
        # Dans un ITEM de liste (le paragraphe a commencé par une puce ou un
        # numéro), la ligne suivante qui n'ouvre pas elle-même un item est le
        # WRAP de l'item : l'« espace ouvert » à droite (qui va jusqu'à la
        # colonne voisine) ne dit rien de sa marge réelle → pas de coupe
        # « espace restant » (c'est elle qui scindait « …Official / Transcript »
        # en deux paragraphes, dupliqués à la traduction).
        first_txt = parent["items"][0]["text"]
        item_parent = bool(_BULLET_RE.match(first_txt)
                           or _NUMITEM_RE.match(first_txt))
        it_opens_item = bool(_BULLET_RE.match(it["text"])
                             or _NUMITEM_RE.match(it["text"]))
        if (self.para_remaining_space and ctx is not None and not continues
                and not (item_parent and not it_opens_item)
                and self._remaining_space_break(parent, it, ctx)):
            return True

        # ── Lignes INDIVIDUELLEMENT centrées et AUTO-SUFFISANTES ────────────
        # (bloc de contacts, liste d'organismes, adresse : une ligne = une
        # entrée). Signal : axes centraux alignés, gauches ET droites toutes
        # deux décalées, ligne précédente loin de la marge de colonne (donc pas
        # du texte qui coule) et finissant une UNITÉ complète (ponctuation
        # finale, parenthèse fermée, nombre, domaine « .org ») — un TITRE
        # centré sur 2 lignes ne finit pas ainsi et reste soudé.
        if not continues:
            c_it = (it["left"] + it["right"]) / 2.0
            c_last = (last["left"] + last["right"]) / 2.0
            gw_last = last.get("gw") or (size_ref * 0.5)
            colm = last.get("col_margin", last["right"])
            unit_end = bool(re.search(r"([.!?)\]»]|\d|\.\w{2,4})\s*$", last_txt))
            if (abs(c_it - c_last) <= 0.6 * size_ref
                    and abs(it["left"] - last["left"]) > 6.0
                    and abs(it["right"] - last["right"]) > 6.0
                    and last["right"] < colm - 2.0 * gw_last
                    and unit_end):
                return True

        # ── 2. Flot continu : interligne normal → FUSION obligatoire ────────
        # (x0 ignoré : c'est ce qui permet au texte de s'enrouler autour d'une
        # image / d'un encart sans être scindé.)
        if g < self._PARA_MODERATE_FACTOR:
            return False
        if lower_next:
            return False

        # ── 3. Zone modérée : signaux faibles ───────────────────────────────
        # Indentation d'alinéa — mais pas un bloc CENTRÉ. On distingue les deux
        # par l'AXE CENTRAL : un alinéa décale la ligne vers la droite (centre
        # déplacé) ; un bloc centré garde le même axe central que la ligne
        # précédente. Comparer au bord droit du titre précédent était instable
        # (les titres ont des longueurs variables → coupe incohérente).
        left_shift = it["left"] - parent["left_min"]
        indent = self._PARA_INDENT_FACTOR * size_ref
        if left_shift > indent:
            c_it = (it["left"] + it["right"]) / 2.0
            c_last = (last["left"] + last["right"]) / 2.0
            if abs(c_it - c_last) > 0.5 * size_ref:
                return True
        # Ponctuation forte + majuscule, uniquement si l'interligne est déjà
        # nettement élevé, et hors conjonction de coordination.
        if (g > self._PARA_PUNCT_FACTOR and _ends_sentence(last["text"])
                and _starts_capital(it["text"])
                and _first_word(it["text"]) not in _COORD_CONJ):
            return True
        return False

    @staticmethod
    def _tag_cells(items, ctx=None):
        """Étape B : tague chaque ligne avec l'indice de la cellule de tableau
        qui contient son centre (`cell`), ou None hors tableau. Deux lignes de
        cellules différentes ne fusionneront jamais en un même paragraphe."""
        cells = (ctx or {}).get("cells", ())
        for it in items:
            it["cell"] = None
            if not cells:
                continue
            cx = 0.5 * (it["left"] + it["right"])
            cy = 0.5 * (it["top"] + it["bottom"])
            for i, (x0, y0, x1, y1) in enumerate(cells):
                if x0 <= cx <= x1 and y0 <= cy <= y1:
                    it["cell"] = i
                    break

    def _assign_column_margins(self, items, ctx=None):
        """Attribue à chaque ligne sa `col_margin` = bord droit de référence pour
        juger un retour à la ligne volontaire, selon la nature de sa colonne :

          • Colonne JUSTIFIÉE (≥ 2 lignes proches atteignent le MÊME bord droit
            max) → `col_margin` = ce bord. Les lignes internes l'atteignent → le
            mot suivant n'y rentre pas → pas de fausse coupe (texte qui coule).
          • Sinon (bords droits DISPERSÉS = sommaire / liste / titres) →
            `col_margin` = ESPACE OUVERT à droite (1er obstacle non-texte ou
            colonne de texte voisine, sinon bord droit du texte de la page). Une
            entrée courte y laisse largement la place au mot suivant → coupe du
            retour volontaire.

        Les voisines sont prises dans une fenêtre verticale (`_RS_WINDOW_FACTOR ×
        taille`) et doivent chevaucher horizontalement la ligne (même colonne) :
        la fenêtre isole la colonne d'un autre bloc pleine largeur séparé
        verticalement mais de même marge gauche."""
        page_w = (ctx or {}).get("width")
        obstacles = (ctx or {}).get("obstacles", ())
        for it in items:
            cy = 0.5 * (it["top"] + it["bottom"])
            win = self._RS_WINDOW_FACTOR * it["size"]
            left, right = it["left"], it["right"]
            # Voisines de colonne (chevauchement horizontal + proximité verticale).
            neigh = [it["right"]]
            neigh_l = [it["left"]]
            for jt in items:
                if jt is it:
                    continue
                if jt["right"] <= left or jt["left"] >= right:
                    continue
                if abs(0.5 * (jt["top"] + jt["bottom"]) - cy) > win:
                    continue
                neigh.append(jt["right"])
                neigh_l.append(jt["left"])
            max_x1 = max(neigh)
            tol = 0.5 * it["size"]
            at_max = sum(1 for x in neigh if max_x1 - x <= tol)
            # Une colonne qui COULE est à fleur des DEUX bords. Si les bords
            # gauches sont déchiquetés alors que les droits s'alignent, ce n'est
            # pas du texte qui coule : c'est un bloc FERRÉ À DROITE (adresse,
            # date, signature), dont les retours à la ligne sont VOLONTAIRES.
            # Sans cette réserve, son bord droit commun le faisait passer pour une
            # colonne justifiée et ses 4 lignes fusionnaient en un paragraphe —
            # que le reflow recoulait ensuite en une seule ligne.
            min_x0 = min(neigh_l)
            at_min = sum(1 for x in neigh_l if x - min_x0 <= tol)
            coule = at_max >= 2 and at_min >= 2
            if page_w is None:
                it["col_margin"] = max_x1
                continue
            # Espace OUVERT à droite (jusqu'au 1er obstacle/colonne, sinon bord de
            # page) et largeur de contenu de la ligne.
            openr = self._open_right(it, items, page_w, obstacles)
            content_w = right - left
            remaining_open = openr - right
            # « Texte qui coule » (à protéger) = plusieurs lignes alignées au même
            # bord droit ET ligne dont le CONTENU est plus large que l'espace
            # restant (ce reliquat n'est qu'une gouttière). Sinon = item court
            # dans un espace ouvert (sommaire, liste, numéros) → référence =
            # espace ouvert, pour couper le retour à la ligne volontaire.
            if coule and content_w >= remaining_open:
                it["col_margin"] = max_x1
            else:
                it["col_margin"] = openr

    @staticmethod
    def _open_right(it, items, page_w, obstacles):
        """Bord droit UTILISABLE à droite d'une ligne : 1er obstacle non-texte ou
        1re ligne d'une AUTRE colonne à droite dans sa bande ; sinon bord droit
        de la PAGE (une colonne seule a donc bien tout l'espace ouvert à sa
        droite, et non son propre bord)."""
        top, bottom, right = it["top"], it["bottom"], it["right"]
        bound = page_w
        for jt in items:
            if jt is it:
                continue
            if jt["left"] <= right:                      # pas à droite
                continue
            if jt["bottom"] <= top or jt["top"] >= bottom:
                continue
            if jt["left"] < bound:
                bound = jt["left"]
        for ox0, oy0, ox1, oy1 in obstacles:
            if ox0 <= right:
                continue
            if oy1 <= top or oy0 >= bottom:
                continue
            if ox0 < bound:
                bound = ox0
        return bound

    def _remaining_space_break(self, parent, it, ctx):
        """Vrai si le PREMIER MOT de `it` aurait tenu dans l'espace libre à
        droite de la dernière ligne du paragraphe → retour à la ligne volontaire.

        Espace libre = (marge droite de la COLONNE) − (fin de la dernière ligne).
        La marge de colonne (`col_margin`) est le bord droit où le texte de la
        colonne s'aligne réellement, estimé à partir des lignes verticalement
        proches qui chevauchent horizontalement la ligne (cf.
        `_assign_column_margins`). Elle vaut la marge d'une colonne justifiée
        (→ pas de fausse coupe : le mot suivant n'y rentre pas) tout en restant
        bien à droite pour un sommaire/liste (→ coupe des retours volontaires).
        Plafonnée par le 1er obstacle non-texte (image/dessin) intercalé."""
        last = parent["items"][-1]
        col_right = self._cap_by_obstacles(last.get("col_margin", last["right"]),
                                           last, ctx)
        remaining = col_right - last["right"]
        if remaining <= 0:
            return False
        word = _first_word(it["text"])
        if not word:
            return False
        gw = it.get("gw") or (it["size"] * 0.5)
        if gw <= 0:
            return False
        # Largeur estimée du 1er mot + une espace de séparation ; on exige une
        # petite marge (_RS_SPACE_FACTOR) pour éviter les faux positifs quand le
        # mot tient tout juste (cas limite d'un wrap serré).
        needed = (len(word) + self._RS_SPACE_FACTOR) * gw
        return remaining >= needed

    @staticmethod
    def _cap_by_obstacles(col_right, m, ctx):
        """Réduit la marge droite `col_right` au 1er obstacle non-texte
        (image/dessin) intercalé à droite de la ligne `m`, dans sa bande
        verticale."""
        top, bottom, right = m["top"], m["bottom"], m["right"]
        bound = col_right
        for bx0, by0, bx1, by1 in ctx.get("obstacles", ()):
            if bx0 <= right:
                continue
            if by1 <= top or by0 >= bottom:
                continue
            if bx0 < bound:
                bound = bx0
        return bound

    @staticmethod
    def _join_para_text(texts):
        """Concatène les lignes d'un paragraphe (étape 8) : dé-césure des mots
        coupés en fin de ligne, insertion d'espaces, écrasement des doublons."""
        res = ""
        for i, t in enumerate(texts):
            t = t.strip()
            if not t:
                continue
            if not res:
                res = t
                continue
            # Césure : « trans-\nport » → « transport » (mot suivant en minuscule).
            if (res.endswith("-") and len(res) >= 2 and res[-2].isalpha()
                    and t[:1].islower()):
                res = res[:-1] + t
            else:
                res = res + " " + t
        return re.sub(r"\s{2,}", " ", res)

    # ── Images : une occurrence = un objet ───────────────────────────────────
    def _extract_images(self, doc, page, page_num, embed_images, assets_dir):
        elements = []
        try:
            infos = page.get_images(full=True)
        except Exception:
            infos = []
        for img_index, info in enumerate(infos):
            xref = info[0]
            try:
                rects = page.get_image_rects(xref)
            except Exception:
                rects = []
            if not rects:
                continue
            # Octets de l'image (extraits une fois par xref). Si l'image a un
            # masque de transparence (SMask), on la compose avec son alpha en
            # PNG — sinon les zones transparentes deviennent noires à la
            # réinjection (insert_image ne connaît pas le masque séparé).
            img_bytes, ext = None, "png"
            try:
                extracted = doc.extract_image(xref)
                smask_xref = extracted.get("smask", 0)
                if smask_xref:
                    base = fitz.Pixmap(doc, xref)
                    if base.alpha:
                        base = fitz.Pixmap(base, 0)          # retire alpha existant
                    if base.colorspace and base.colorspace.n > 3:
                        base = fitz.Pixmap(fitz.csRGB, base)  # CMYK -> RGB
                    mask = fitz.Pixmap(doc, smask_xref)
                    pix = fitz.Pixmap(base, mask)             # applique l'alpha
                    img_bytes = pix.tobytes("png")
                    ext = "png"
                else:
                    img_bytes = extracted["image"]
                    ext = extracted.get("ext", "png")
            except Exception:
                # Repli : pixmap composite direct.
                try:
                    pix = fitz.Pixmap(doc, xref)
                    if pix.colorspace and pix.colorspace.n > 3:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    img_bytes = pix.tobytes("png")
                    ext = "png"
                except Exception:
                    img_bytes, ext = None, "png"

            asset_ref = None
            asset_b64 = None
            if img_bytes is not None:
                if embed_images:
                    asset_b64 = base64.b64encode(img_bytes).decode("ascii")
                else:
                    fname = f"p{page_num + 1}_img{xref}.{ext}"
                    with open(os.path.join(assets_dir, fname), "wb") as fh:
                        fh.write(img_bytes)
                    asset_ref = fname

            for rect in rects:
                elements.append({
                    "type": "image",
                    "bbox": _rect(rect),
                    "xref": xref,
                    "ext": ext,
                    "asset_file": asset_ref,   # si embed_images=False
                    "asset_b64": asset_b64,    # si embed_images=True
                })
        return elements

    # ── Dessins vectoriels : un tracé = un objet ─────────────────────────────
    def _extract_drawings(self, page):
        elements = []
        try:
            drawings = page.get_drawings()
        except Exception:
            drawings = []
        for d in drawings:
            elements.append(_serialize_drawing(d))
        return elements

    # ─────────────────────────────────────────────────────────────────────────
    # 2. RÉINJECTION (reconstruction sur page vierge + bordures)
    # ─────────────────────────────────────────────────────────────────────────
    reflow_lang = "fr_FR"          # langue de césure pour le rendu traduit

    def reinject(self, data_or_json, output_pdf, draw_borders=True,
                 assets_dir=None, translated=False):
        """Reconstruit un PDF depuis le JSON d'extraction.

        Chaque objet est redessiné à sa position d'origine sur une page
        vierge de mêmes dimensions ; une bordure est tracée autour de chaque
        objet si draw_borders=True.

        Args:
            data_or_json: dict d'extraction, ou chemin vers le JSON.
            output_pdf:   chemin du PDF de sortie.
            draw_borders: trace un cadre autour de chaque objet.
            assets_dir:   dossier des images (si extraction embed_images=False).
                          Déduit du chemin JSON si non fourni.
        """
        if isinstance(data_or_json, (str, os.PathLike)):
            json_path = str(data_or_json)
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if assets_dir is None:
                assets_dir = str(Path(json_path).with_suffix("")) + "_assets"
        else:
            data = data_or_json

        # Reconstruit les objets fitz.Font depuis les polices embarquées du JSON.
        self._build_fonts(data)

        doc = fitz.open()
        try:
            for page_data in data.get("pages", []):
                self.render_page_into(doc, page_data, draw_borders=draw_borders,
                                      assets_dir=assets_dir, translated=translated)
            doc.save(output_pdf, garbage=4, deflate=True, clean=True)
        finally:
            doc.close()
        return output_pdf

    def render_page_into(self, doc, page_data, draw_borders=False,
                         assets_dir=None, translated=False):
        """Peint UNE page (page_data) dans un document fitz existant — brique du
        rendu progressif page par page (le PDF partiel grandit au fil des pages
        traduites) et de `reinject`. Nécessite `_build_fonts(data)` au préalable."""
        page = doc.new_page(width=page_data["width"],
                            height=page_data["height"])
        # Flux vertical (Étape E) : calcule DÉCALAGE + croissance de
        # chaque paragraphe traduit AVANT de peindre (place récupérée par
        # push-down des blocs libres, borné par les objets ancrés ;
        # anti-collision garanti). No-op si non traduit ou toggle off.
        if translated and self.vertical_flow:
            try:
                self._apply_vertical_flow(page_data)
            except Exception:
                pass
        # Grow-into-gap + échelle de groupe (P3) : blanc existant offert
        # aux conteneurs, page visuellement homogène. Aucun déplacement.
        if translated:
            try:
                self._prepare_translated_page(page_data)
            except Exception:
                pass
        for el in page_data.get("elements", []):
            kind = el.get("type")
            try:
                if kind == "image":
                    self._draw_image(page, el, assets_dir)
                elif kind == "drawing":
                    # En mode traduit, un soulignement de lien consommé
                    # est redessiné SOUS le texte reflowé, pas ici (sinon
                    # il resterait figé sous le texte déplacé).
                    if not (translated and el.get("_underline_consumed")):
                        self._draw_drawing(page, el)
                elif kind == "paragraph":
                    if translated and el.get("tr_tagged") is not None:
                        self._draw_paragraph_translated(page, el)
                    else:
                        self._draw_paragraph(page, el)
                elif kind == "text_line":
                    self._draw_text_line(page, el)
            except Exception:
                # Un objet fautif ne doit pas casser toute la page.
                pass
        # Bordures en dernier : toujours visibles, jamais recouvertes.
        if draw_borders:
            self._draw_borders(page, page_data.get("elements", []))
        return page

    # ── Polices : b64 -> objets fitz.Font (regroupés par nom propre) ─────────
    def _build_fonts(self, data):
        self._fonts = {}   # clean -> [fitz.Font, ...]
        for clean, variants in data.get("fonts", {}).items():
            objs = []
            for v in variants:
                try:
                    buf = base64.b64decode(v["b64"])
                    objs.append(fitz.Font(fontbuffer=buf))
                except Exception:
                    pass
            if objs:
                self._fonts[clean] = objs

    def _pick_font(self, font_raw, text):
        """Choisit le sous-ensemble embarqué qui couvre les glyphes du texte."""
        fonts = getattr(self, "_fonts", {})
        clean = font_raw.split("+")[-1] if "+" in (font_raw or "") else (font_raw or "")
        variants = fonts.get(clean)
        if not variants:
            return None
        chars = [c for c in text if not c.isspace()]
        for f in variants:
            try:
                if all(f.has_glyph(ord(c)) for c in chars):
                    return f
            except Exception:
                continue
        return variants[0]   # meilleur effort : couverture partielle

    # ── Rendu image ──────────────────────────────────────────────────────────
    def _draw_image(self, page, el, assets_dir):
        rect = fitz.Rect(el["bbox"])
        if rect.is_empty or rect.is_infinite:
            return
        stream = None
        if el.get("asset_b64"):
            stream = base64.b64decode(el["asset_b64"])
        elif el.get("asset_file") and assets_dir:
            path = os.path.join(assets_dir, el["asset_file"])
            if os.path.exists(path):
                with open(path, "rb") as fh:
                    stream = fh.read()
        if stream is None:
            return
        page.insert_image(rect, stream=stream)

    # ── Rendu dessin vectoriel ───────────────────────────────────────────────
    def _draw_drawing(self, page, el):
        draw_type = el.get("draw_type")
        if draw_type in ("clip", "group"):
            return   # basique : on ignore les clips/groupes
        shape = page.new_shape()
        drew = False
        for it in el.get("items", []):
            op = it.get("op")
            try:
                if op == "l":
                    shape.draw_line(fitz.Point(it["p1"]), fitz.Point(it["p2"]))
                    drew = True
                elif op == "c":
                    shape.draw_bezier(fitz.Point(it["p1"]), fitz.Point(it["p2"]),
                                      fitz.Point(it["p3"]), fitz.Point(it["p4"]))
                    drew = True
                elif op == "re":
                    shape.draw_rect(fitz.Rect(it["rect"]))
                    drew = True
                elif op == "qu":
                    pts = it["quad"]
                    quad = fitz.Quad(fitz.Point(pts[0]), fitz.Point(pts[1]),
                                     fitz.Point(pts[2]), fitz.Point(pts[3]))
                    shape.draw_quad(quad)
                    drew = True
            except Exception:
                pass
        if not drew:
            return

        # draw_type : 's'=trait, 'f'=remplissage, 'fs'=les deux.
        stroke = el.get("stroke_color")
        fill = el.get("fill_color")
        if draw_type == "f":
            stroke = None
        elif draw_type == "s":
            fill = None

        finish_kwargs = {
            "color": tuple(stroke) if stroke else None,
            "fill": tuple(fill) if fill else None,
            "width": el.get("width") or 1.0,
            "closePath": bool(el.get("close_path")),
        }
        if el.get("even_odd") is not None:
            finish_kwargs["even_odd"] = bool(el.get("even_odd"))
        if el.get("dashes"):
            finish_kwargs["dashes"] = el.get("dashes")
        if el.get("line_cap") is not None:
            lc = el["line_cap"]
            finish_kwargs["lineCap"] = max(lc) if isinstance(lc, (list, tuple)) else lc
        if el.get("line_join") is not None:
            finish_kwargs["lineJoin"] = el["line_join"]
        if el.get("stroke_opacity") is not None:
            finish_kwargs["stroke_opacity"] = el["stroke_opacity"]
        if el.get("fill_opacity") is not None:
            finish_kwargs["fill_opacity"] = el["fill_opacity"]

        try:
            shape.finish(**finish_kwargs)
        except Exception:
            # Repli minimal si un paramètre n'est pas accepté par cette version.
            shape.finish(color=finish_kwargs["color"], fill=finish_kwargs["fill"],
                         width=finish_kwargs["width"])
        shape.commit()

    # ── Rendu texte : paragraphe = lignes, ligne = runs (positions exactes) ──
    def _draw_paragraph(self, page, el):
        for line in el.get("lines", []):
            self._draw_text_line(page, line)

    # ═════════════════════════════════════════════════════════════════════════
    # Étape E — FLUX VERTICAL (push-down des blocs libres + anti-collision)
    # ═════════════════════════════════════════════════════════════════════════
    _VFLOW_SAFETY = 3.0        # gouttière verticale mini avant un objet ancré (pt)

    def _apply_vertical_flow(self, page_data):
        """Décale vers le bas les paragraphes traduits pour absorber les
        traductions plus longues, SANS jamais chevaucher un objet ancré ni sortir
        d'un conteneur. Écrit sur chaque paragraphe traduit : `_yshift` (décalage
        vers le bas), `_grow` (hauteur ajoutée à sa boîte) et `_force_fit`
        (compression imposée, garantie de tenue).

        Modèle GÉNÉRAL (aucune règle propre à un document) :
        - **Colonne** = paragraphes qui se chevauchent horizontalement.
        - **Frontières** qui NE BOUGENT PAS : images, blocs liés à un conteneur
          (panneau/cellule = rectangle plein/tracé de taille moyenne qui les
          enclot), marges de page (en-tête / pied de page).
        - Dans un **segment** de blocs libres bornés par deux frontières, la place
          nécessaire (somme des croissances) est accordée si elle tient jusqu'à la
          frontière ; sinon répartie au prorata et le reste absorbé par
          compression (force-fit). ⇒ jamais de collision, jamais de déplacement
          d'un objet ancré, jamais de franchissement de colonne/conteneur."""
        W = page_data.get("width") or 0.0
        H = page_data.get("height") or 0.0
        els = page_data.get("elements", [])
        paras = [e for e in els if e.get("type") == "paragraph"
                 and e.get("tr_tagged") is not None
                 and self._para_is_horizontal(e)]
        if not paras:
            return
        page_area = max(1.0, W * H)
        boxes = self._container_boxes(els, page_area)
        images = [e["bbox"] for e in els if e.get("type") == "image"
                  and e.get("bbox") and len(e["bbox"]) >= 4]
        margin = 0.05 * H

        for p in paras:
            p["_bound"] = self._is_boxed(p["bbox"], boxes)
            p["_delta"] = self._natural_delta(p)
            p["_yshift"] = 0.0
            p["_grow"] = 0.0
            # Bloc lié à un conteneur (panneau/cellule) qui déborde : compression
            # garantie DANS sa boîte (jamais de sortie du panneau). Indépendant
            # du push-down.
            p["_force_fit"] = bool(p["_bound"] and p["_delta"] > 0)

        for col in self._columns(paras):
            # Les blocs qui se chevauchent VERTICALEMENT (contenu parallèle :
            # colonnes côte à côte, « préface | auteur », puce gauche/droite) sont
            # réunis en une RANGÉE qui se décale SOLIDAIREMENT — le push-down agit
            # sur les rangées, jamais en séquençant du contenu parallèle (ce qui
            # créerait des collisions). Une pile propre = une rangée par bloc.
            rows = self._group_rows(col)
            xr = (min(p["bbox"][0] for p in col),
                  max(p["bbox"][2] for p in col))
            # Frontières = images ET panneaux/encadrés (composants) : le texte
            # d'AU-DESSUS est borné par le haut du composant, jamais poussé DEDANS
            # (corrige l'intrusion dans une zone grise / un encart).
            barriers = sorted((bb[1], bb[3]) for bb in (images + boxes)
                              if bb[2] > xr[0] and bb[0] < xr[1]
                              and not (bb[1] <= min(p["bbox"][1] for p in col)
                                       and bb[3] >= max(p["bbox"][3] for p in col)))
            seg, bi = [], 0
            for u in rows:
                while bi < len(barriers) and barriers[bi][0] <= u["top"]:
                    self._flow_units(seg, barriers[bi][0])
                    seg = []
                    bi += 1
                if u["bound"]:
                    self._flow_units(seg, u["top"])
                    seg = []
                else:
                    seg.append(u)
            while bi < len(barriers):
                self._flow_units(seg, barriers[bi][0])
                seg = []
                bi += 1
            self._flow_units(seg, H - margin)

    @staticmethod
    def _group_rows(col):
        """Réunit en RANGÉES les blocs d'une colonne qui se chevauchent
        verticalement (contenu parallèle) → une rangée bouge solidairement. Une
        pile propre donne une rangée par bloc. Rangée = {top, bottom, delta (=max
        des membres), bound (=un membre lié), paras:[…]}."""
        rows = []
        for p in sorted(col, key=lambda q: q["bbox"][1]):
            pt, pb = p["bbox"][1], p["bbox"][3]
            hit = None
            for r in rows:
                # Vrai contenu PARALLÈLE (même rangée) = chevauchement vertical
                # SUBSTANTIEL (> 50 % de la plus petite hauteur). Des lignes
                # simplement EMPILÉES se touchent de quelques points (ascendantes/
                # descendantes) : ce chevauchement minime ne doit PAS les réunir,
                # sinon la croissance de l'une empiète sur l'autre au lieu de la
                # pousser.
                ov = min(pb, r["bottom"]) - max(pt, r["top"])
                if ov > 0.5 * max(1.0, min(pb - pt, r["bottom"] - r["top"])):
                    hit = r
                    break
            if hit is None:
                rows.append({"top": pt, "bottom": pb, "paras": [p],
                             "delta": max(0.0, p["_delta"]),
                             "bound": bool(p.get("_bound"))})
            else:
                hit["paras"].append(p)
                hit["top"] = min(hit["top"], pt)
                hit["bottom"] = max(hit["bottom"], pb)
                hit["delta"] = max(hit["delta"], max(0.0, p["_delta"]))
                hit["bound"] = hit["bound"] or bool(p.get("_bound"))
        return sorted(rows, key=lambda r: r["top"])

    def _flow_units(self, seg, boundary_top):
        """Absorbe la croissance des RANGÉES libres `seg` (bornées en bas par
        `boundary_top`), en préservant les écarts d'origine.

        Deux régimes :
        - **Assez de place** → PUSH-DOWN : chaque rangée est décalée du cumul des
          croissances des rangées au-dessus ; chaque bloc grandit de sa propre
          croissance. Tailles d'origine CONSERVÉES.
        - **Segment saturé** → COMPRESSION UNIFORME : une seule échelle `s` sur
          tout le segment (taille + interligne + écarts), repositionnement
          proportionnel → tailles cohérentes, aucun débordement.
        Le décalage/échelle d'une rangée s'applique à TOUS ses blocs (solidaire)."""
        if not seg:
            return
        seg = sorted(seg, key=lambda u: u["top"])
        total = sum(u["delta"] for u in seg)
        if total <= 0:
            return
        first_top = seg[0]["top"]
        last_bottom = max(u["bottom"] for u in seg)
        slack = boundary_top - self._VFLOW_SAFETY - last_bottom

        if total <= slack:                      # place suffisante : push simple
            cum = 0.0
            for u in seg:
                for p in u["paras"]:
                    p["_yshift"] = cum
                    p["_grow"] = max(0.0, p["_delta"])
                cum += u["delta"]
            return

        # Segment saturé : échelle uniforme s telle que le segment (avec toute sa
        # croissance) tienne dans l'espace disponible depuis first_top.
        natural_h = (last_bottom + total) - first_top
        avail = max(1.0, boundary_top - self._VFLOW_SAFETY - first_top)
        s = max(0.4, min(1.0, avail / natural_h)) if natural_h > 0 else 1.0
        off = 0.0
        prev_bottom = None
        for u in seg:
            gap = 0.0 if prev_bottom is None else max(0.0, u["top"] - prev_bottom)
            off += gap
            new_top = first_top + off * s
            dy = new_top - u["top"]
            for p in u["paras"]:
                ph = p["bbox"][3] - p["bbox"][1]
                own_h = ph + max(0.0, p["_delta"])
                p["_yshift"] = dy
                p["_vscale"] = s
                p["_grow"] = own_h * s - ph
                p["_force_fit"] = True
            off += (u["bottom"] - u["top"]) + u["delta"]
            prev_bottom = u["bottom"]

    def _natural_delta(self, el):
        """Hauteur (pt) dont la traduction DÉPASSE réellement le bas du conteneur
        du paragraphe (0 si elle y tient). C'est le seul déplacement à propager :
        un paragraphe dont la traduction — même en plus de lignes qu'à l'origine —
        rentre dans sa boîte (conteneur multi-lignes) ne pousse RIEN."""
        segs = self._parse_translated_segments(el)
        clines = el.get("container_lines")
        if not clines:
            cbb = el.get("container_bbox") or el.get("bbox")
            clines = [cbb] if cbb else None
        if not segs or not clines:
            return 0.0
        fb = None
        for ln in el.get("lines", []):
            runs = ln.get("runs")
            if runs and runs[0].get("origin"):
                fb = runs[0]["origin"][1]
                break
        pitch = reflow._orig_pitch(clines, segs)
        top = min(c[1] for c in clines)
        if fb is None:
            fb = top + 0.78 * pitch
        n0 = reflow.natural_lines(segs, clines, self.reflow_lang, fb)
        size_est = max((s.get("size", 0) or 0 for s in segs), default=10.0)
        natural_bottom = fb + (n0 - 1) * pitch + 0.25 * size_est
        container_bottom = max(c[3] for c in clines)
        return max(0.0, natural_bottom - container_bottom)

    @staticmethod
    def _para_is_horizontal(el):
        for ln in el.get("lines", []):
            for r in ln.get("runs", []):
                d = r.get("dir") or [1, 0]
                if not (abs(d[1]) <= 0.01 and d[0] >= 0):
                    return False
        return True

    @staticmethod
    def _container_boxes(els, page_area):
        """Rectangles conteneurs (panneau/cellule/encadré) : dessin PLEIN ou
        TRACÉ, de taille MOYENNE (entre ~1,5 % et ~55 % de la page) — ni un
        filet, ni un fond plein-page (qui, lui, laisse couler le texte)."""
        boxes = []
        for e in els:
            if e.get("type") != "drawing":
                continue
            if e.get("draw_type") not in ("f", "fs", "s"):
                continue
            bb = e.get("bbox")
            if not bb or len(bb) < 4:
                continue
            a = (bb[2] - bb[0]) * (bb[3] - bb[1])
            if a < 0.015 * page_area or a > 0.55 * page_area:
                continue
            boxes.append(bb)
        return boxes

    @staticmethod
    def _is_boxed(pb, boxes):
        """Le paragraphe `pb` est-il ENCLOS par un rectangle conteneur (avec une
        petite tolérance) ? → son texte est lié à ce conteneur (ne pas décaler)."""
        for bx in boxes:
            if (bx[0] - 3 <= pb[0] and bx[1] - 3 <= pb[1]
                    and bx[2] + 3 >= pb[2] and bx[3] + 3 >= pb[3]):
                return True
        return False

    @staticmethod
    def _columns(paras):
        """Groupe les paragraphes en COLONNES par chevauchement horizontal
        transitif (union-find). Deux blocs de la même colonne se chevauchent en
        x ; deux colonnes côte à côte ne fusionnent pas."""
        items = list(paras)
        n = len(items)
        parent = list(range(n))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        for i in range(n):
            bi = items[i]["bbox"]
            for j in range(i + 1, n):
                bj = items[j]["bbox"]
                ov = min(bi[2], bj[2]) - max(bi[0], bj[0])
                minw = max(1.0, min(bi[2] - bi[0], bj[2] - bj[0]))
                if ov > 0.25 * minw:
                    parent[find(i)] = find(j)
        groups = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(items[i])
        return list(groups.values())

    # ── Rendu TRADUIT : coulée du texte traduit dans le conteneur (reflow) ────
    # ── Préparation d'une page traduite : grow-into-gap + échelle de groupe ──
    grow_into_gap = True     # accorde au conteneur le BLANC déjà présent sous le
                             # bloc (aucun déplacement d'aucun bloc → sans risque)
    group_scale   = True     # fratries (même corps, même colonne, contiguës) →
                             # échelle commune = la pire du groupe (uniformité)
    _GROW_KEEP_FRAC   = 0.30   # fraction du blanc TOUJOURS préservée
    _GROW_MAX_PITCH   = 2.5    # croissance max (en interlignes)
    _GROUP_MIN_SCALE  = 0.88   # en-deçà : hors groupe (candidat retraduction)

    def _prepare_translated_page(self, page_data):
        """Avant de peindre une page traduite : (1) donne à chaque paragraphe le
        blanc RÉELLEMENT disponible sous lui (`_grow` — aucun bloc n'est déplacé,
        on ne consomme que du vide existant, borné par le prochain élément, la
        boîte contenante et une réserve) ; (2) mesure l'échelle nécessaire de
        chaque paragraphe (reflow à blanc) ; (3) impose aux FRATRIES l'échelle
        du plus contraint (`_vscale`) pour une page visuellement homogène ;
        (4) marque `_needs_shorter` les paragraphes qui exigeraient une
        compression au-delà du plancher visuel (→ retraduction plus courte)."""
        els = page_data.get("elements", [])
        paras = [e for e in els if e.get("type") == "paragraph"
                 and (e.get("tr_tagged") or "").strip()]
        if not paras:
            return
        for p in paras:                        # idempotence (re-préparation)
            p.pop("_vscale", None)
            p.pop("_needs_shorter", None)
            p.pop("_grow", None)
        # Bloqueurs verticaux : tout élément occupe l'espace ; les dessins par
        # leurs ITEMS (un filet sous un encart doit arrêter la croissance).
        vboxes = []
        for e in els:
            bb = e.get("bbox")
            if e.get("type") == "drawing":
                vboxes.extend(_drawing_item_boxes(e))
            elif bb and len(bb) >= 4:
                vboxes.append(tuple(bb))
        page_h = page_data.get("height") or 0
        page_w = page_data.get("width") or 0
        page_area = max(1.0, page_w * page_h)

        if self.grow_into_gap:
            for p in paras:
                bb = p.get("bbox")
                cl = p.get("container_lines")
                if not bb or len(bb) < 4 or not cl:
                    continue
                pl, pt, pr, pb = bb
                pitch = reflow._orig_pitch(cl, [])
                # 1er élément SOUS le paragraphe qui chevauche sa largeur.
                nxt = None
                for vb in vboxes:
                    if vb[1] < pb - 1.0:                    # pas en dessous
                        continue
                    if min(vb[2], pr) - max(vb[0], pl) <= 2.0:
                        continue                            # pas la même colonne
                    if nxt is None or vb[1] < nxt:
                        nxt = vb[1]
                gap = (nxt - pb) if nxt is not None else 0.0
                # Boîte contenante : jamais de croissance hors de sa boîte.
                for vb in vboxes:
                    if (vb[0] <= pl + 2 and vb[1] <= pt + 2 and vb[2] >= pr - 2
                            and vb[3] >= pb - 2):
                        area = (vb[2] - vb[0]) * (vb[3] - vb[1])
                        if area < 0.6 * page_area:
                            gap = min(gap, vb[3] - 2.0 - pb)
                if gap <= 1.0:
                    continue
                grow = gap * (1.0 - self._GROW_KEEP_FRAC)
                p["_grow"] = max(0.0, min(grow, self._GROW_MAX_PITCH * pitch))

        # Échelle NÉCESSAIRE de chaque paragraphe (reflow à blanc, sans force).
        needs = {}
        for p in paras:
            lay = self._translated_layout(p)
            if lay is None:
                continue
            res = reflow.reflow_paragraph(lay["segs"], lay["clines"],
                                          lang=self.reflow_lang,
                                          first_baseline=lay["first_baseline"],
                                          align=lay["align"], force_fit=True)
            scale = res.get("size_scale", 1.0)
            needs[id(p)] = scale if res.get("fitted") else min(scale, 0.4)
            if needs[id(p)] < self._GROUP_MIN_SCALE:
                p["_needs_shorter"] = True      # candidat retraduction compacte

        if not self.group_scale:
            return
        # Fratries : même corps (±0.6 pt), chevauchement horizontal majoritaire,
        # contiguïté verticale (< 2.5 × corps). Échelle commune = min du groupe
        # (bornée au plancher : les cas extrêmes restent traités seuls).
        def psize(p):
            return max((r.get("size", 0) or 0 for ln in p.get("lines", [])
                        for r in ln.get("runs", [])), default=10.0)
        items = sorted((p for p in paras if id(p) in needs),
                       key=lambda p: p["bbox"][1])
        used = set()
        for i, p in enumerate(items):
            if id(p) in used:
                continue
            group = [p]
            used.add(id(p))
            sz, bb = psize(p), p["bbox"]
            last = p
            for q in items[i + 1:]:
                if id(q) in used:
                    continue
                qb, lb = q["bbox"], last["bbox"]
                if abs(psize(q) - sz) > 0.6:
                    continue                     # autre corps : n'interrompt pas
                ovl = min(qb[2], bb[2]) - max(qb[0], bb[0])
                if ovl < 0.5 * min(qb[2] - qb[0], bb[2] - bb[0]):
                    continue
                # Contiguïté tolérante : une structure ALTERNÉE (titre/sous-titre
                # de sommaire, item/sous-item) intercale un bloc d'un autre corps
                # entre deux frères — on tolère ce saut (4 × corps).
                if qb[1] - lb[3] > 4.0 * sz:
                    break                        # trop loin → fin de chaîne
                group.append(q)
                used.add(id(q))
                last = q
            if len(group) < 2:
                continue
            gscale = min(max(needs[id(g)], self._GROUP_MIN_SCALE)
                         for g in group)
            if gscale < 0.999:
                for g in group:
                    g["_vscale"] = gscale

    def translated_fit(self, el):
        """Mesure la TENUE d'un paragraphe traduit dans son conteneur :
        { scale (échelle nécessaire), budget (nb de caractères qui tiendraient
        sans compression visible) } — ou None si non reflowable. Sert à la
        retraduction compacte (décision produit : reformulation, jamais une
        mise en forme dégradée)."""
        lay = self._translated_layout(el)
        if lay is None:
            return None
        res = reflow.reflow_paragraph(lay["segs"], lay["clines"],
                                      lang=self.reflow_lang,
                                      first_baseline=lay["first_baseline"],
                                      align=lay["align"], force_fit=True)
        scale = res.get("size_scale", 1.0)
        if not res.get("fitted"):
            scale = min(scale, 0.4)
        clines = lay["clines"]
        pitch = reflow._orig_pitch(clines, lay["segs"])
        bottom = max(c[3] for c in clines)
        fb = lay["first_baseline"]
        if fb is None:
            fb = min(c[1] for c in clines) + 0.78 * pitch
        avail = max(1, int((bottom - fb) / max(pitch, 1e-6) + 1.35))
        natural = reflow.natural_lines(lay["segs"], clines,
                                       lang=self.reflow_lang,
                                       first_baseline=lay["first_baseline"])
        plain = re.sub(r"\[\[/?\d+\]\]", "", el.get("tr_tagged") or "")
        budget = len(plain)
        if natural > avail:
            budget = int(len(plain) * (avail / natural) * 0.92)
        return {"scale": scale, "budget": budget,
                "avail": avail, "natural": natural}

    def _translated_layout(self, el):
        """Prépare le REFLOW d'un paragraphe traduit : segments + conteneur +
        baseline + alignement (avec `_yshift`/`_grow` appliqués). Retourne un
        dict ou None si le paragraphe ne se reflowe pas (incliné, vide)."""
        lines = el.get("lines", [])
        horiz = all(_is_horizontal(r) for ln in lines
                    for r in ln.get("runs", []))
        if not horiz:
            return None                        # incliné/vertical : pas de reflow
        segs = self._parse_translated_segments(el)
        if not segs:
            return None                        # rien de traduit : repli
        clines = el.get("container_lines")
        if not clines:
            cbb = el.get("container_bbox") or el.get("bbox")
            clines = [cbb] if cbb else None
        if not clines:
            return None
        # Baseline de la 1re ligne d'origine → le reflow y ancre son texte
        # (garde la position verticale ; les soulignements suivent le texte).
        first_baseline = None
        for ln in lines:
            runs = ln.get("runs")
            if runs and runs[0].get("origin"):
                first_baseline = runs[0]["origin"][1]
                break
        # `_yshift` : bloc décalé (flux vertical) ; `_grow` : hauteur accordée
        # en plus (blanc déjà disponible sous le bloc) ; `_force_fit` : hauteur
        # imposée → compression garantie, jamais de débordement.
        yshift = el.get("_yshift", 0.0) or 0.0
        grow = el.get("_grow", 0.0) or 0.0
        if yshift or grow:
            clines = [[c[0], c[1] + yshift, c[2], c[3] + yshift] for c in clines]
            if grow and clines:
                clines[-1] = [clines[-1][0], clines[-1][1],
                              clines[-1][2], clines[-1][3] + grow]
        if first_baseline is not None:
            first_baseline += yshift
        align = el.get("align", "left")
        if align == "left" and self.justify_text and self._is_justified(el):
            align = "justify"                  # source justifiée → rendu justifié
        return {"segs": segs, "clines": clines, "first_baseline": first_baseline,
                "align": align,
                "force_fit": self.shrink_to_fit or bool(el.get("_force_fit")),
                "vscale": el.get("_vscale")}

    def _draw_paragraph_translated(self, page, el):
        """Peint la version traduite d'un paragraphe : les segments traduits
        (balises `[[n]]` + styles `tr_segments`) sont coulés dans le polygone
        `container_lines` par `reflow`, puis peints. Un paragraphe INCLINÉ /
        VERTICAL passe par son propre repère d'écriture (`_draw_paragraph_rotated`).
        Repli : parsing vide → rendu original run-par-run (inchangé)."""
        lay = self._translated_layout(el)
        if lay is None:
            d = self._writing_dir(el)
            if d is not None and self._draw_paragraph_rotated(page, el, d):
                return
            self._draw_paragraph(page, el)
            return
        res = reflow.reflow_paragraph(lay["segs"], lay["clines"],
                                      lang=self.reflow_lang,
                                      first_baseline=lay["first_baseline"],
                                      align=lay["align"],
                                      force_fit=lay["force_fit"],
                                      fixed_scale=lay["vscale"])
        self._paint_reflow(page, res)

    # ── Traduction du texte INCLINÉ / VERTICAL ────────────────────────────────
    # Le reflow est purement 2D : il lui suffit d'un conteneur et d'une baseline.
    # On exprime donc les deux dans le REPÈRE D'ÉCRITURE du paragraphe (X le long
    # de `dir`, Y vers le bas du texte), on coule normalement, puis on repeint en
    # tournant chaque glyphe. Sans cela, un titre vertical était bien traduit
    # (`tr_tagged` était rempli) mais son rendu retombait sur les runs SOURCE :
    # la traduction était calculée, payée, puis jetée.

    @staticmethod
    def _writing_dir(el):
        """Direction d'écriture d'un paragraphe incliné/vertical, ou None s'il
        est horizontal (chemin normal) ou mêle plusieurs directions."""
        dirs = set()
        for ln in el.get("lines", []):
            for r in ln.get("runs", []):
                if not (r.get("text") or "").strip():
                    continue
                d = r.get("dir") or [1, 0]
                dirs.add((round(float(d[0]), 2), round(float(d[1]), 2)))
        if len(dirs) != 1:
            return None
        d = dirs.pop()
        if abs(d[1]) <= 0.01 and d[0] >= 0:
            return None
        return d

    @staticmethod
    def _to_frame(px, py, d):
        """Page → repère d'écriture. Le « bas » du texte est `p = (-dy, dx)` :
        pour un texte horizontal (1,0) il vaut (0,1), le bas de page — le repère
        se confond alors avec celui de la page."""
        dx, dy = d
        return (px * dx + py * dy, -px * dy + py * dx)

    @staticmethod
    def _to_page(fx, fy, d):
        """Repère d'écriture → page (inverse de `_to_frame`)."""
        dx, dy = d
        return (fx * dx - fy * dy, fx * dy + fy * dx)

    def _translated_layout_rotated(self, el, d):
        """`_translated_layout` exprimé dans le repère d'écriture `d`.

        `_yshift` / `_grow` ne s'appliquent pas : ils récupèrent du blanc
        VERTICAL de page, notion sans objet pour une bande pivotée (dont la
        hauteur est imposée par la largeur du fût)."""
        segs = self._parse_translated_segments(el)
        if not segs:
            return None
        # Conteneur = la bbox PROPRE du paragraphe, jamais `container_lines`.
        # L'expansion (étape D) élargit vers la DROITE en coordonnées PAGE : pour
        # un texte vertical, cette direction n'est pas l'axe d'écriture mais la
        # PERPENDICULAIRE — le conteneur élargi n'offrait donc pas de la longueur,
        # il offrait des LIGNES SUPPLÉMENTAIRES, et le reflow y renvoyait à la
        # ligne (« Juridiqu / e » dans un en-tête de tableau pivoté). La bbox
        # source donne l'empan exact : même longueur d'axe, même nombre de lignes.
        clines = [el.get("bbox")] if el.get("bbox") else None
        if not clines or not clines[0]:
            return None
        fclines = []
        for c in clines:
            pts = [self._to_frame(x, y, d)
                   for x in (c[0], c[2]) for y in (c[1], c[3])]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            fclines.append([min(xs), min(ys), max(xs), max(ys)])
        first_baseline = None
        for ln in el.get("lines", []):
            runs = ln.get("runs")
            if runs and runs[0].get("origin"):
                o = runs[0]["origin"]
                first_baseline = self._to_frame(o[0], o[1], d)[1]
                break
        return {"segs": segs, "clines": fclines,
                "first_baseline": first_baseline,
                "align": el.get("align", "left")}

    def _draw_paragraph_rotated(self, page, el, d):
        """Peint la traduction d'un paragraphe incliné/vertical. Retourne False
        si rien n'est exploitable → l'appelant retombe sur le rendu original."""
        lay = self._translated_layout_rotated(el, d)
        if lay is None:
            return False
        extent = (max(c[2] for c in lay["clines"])
                  - min(c[0] for c in lay["clines"]))
        self._respread_letterspacing(lay["segs"], extent)
        res = reflow.reflow_paragraph(lay["segs"], lay["clines"],
                                      lang=self.reflow_lang,
                                      first_baseline=lay["first_baseline"],
                                      align=lay["align"],
                                      force_fit=True)   # bande à hauteur imposée
        if not res.get("lines"):
            return False
        self._paint_reflow_rotated(page, res, d)
        return True

    def _respread_letterspacing(self, segs, extent):
        """Titre à LETTRES ESPACÉES : re-répartit le tracking sur le texte
        TRADUIT pour qu'il occupe exactement la bande source (`extent`).

        Le tracking mémorisé au balisage a été mesuré sur le texte SOURCE ;
        appliqué tel quel à une traduction plus longue, il la fait déborder — le
        force-fit rapetisse alors tout le titre. Or un titre espacé se rejustifie
        par son TRACKING, pas par son corps : on garde le corps, on resserre les
        lettres. Sans effet si le titre n'était pas espacé (`lsp` nul).

        Modèle de largeur identique à celui du reflow : chaque joint de lettre
        coûte `lsp`, et une espace de mot en coûte 2 de plus.
        """
        if not segs or not all((s.get("lsp") or 0) > 0 for s in segs):
            return
        text = "".join(s.get("text") or "" for s in segs)
        letters = len(text.replace(" ", ""))
        n_sp = text.count(" ")
        joints = letters + n_sp - 1
        if joints < 1:
            return
        natural = sum(reflow.text_width(s["text"], s["fonts"], s["size"])
                      for s in segs)
        lsp = max(0.0, (extent - natural) / joints)
        for s in segs:
            size = s.get("size") or 0
            if size:
                s["lsp"] = lsp / size

    def _paint_reflow_rotated(self, page, res, d):
        """`_paint_reflow` pivoté : chaque glyphe est ramené du repère d'écriture
        en coordonnées page, puis tourné de `atan2(-dy, dx)` autour de son
        origine — `dir` est en y-vers-le-bas (page) alors que `fitz.Matrix(deg)`
        attend un angle en y-vers-le-haut (cf. `_write_rotated`)."""
        deg = math.degrees(math.atan2(-d[1], d[0]))
        for ln in res.get("lines", []):
            for r in ln["runs"]:
                self._paint_run_glyphs_rotated(page, r, ln["baseline"], d, deg)

    def _paint_run_glyphs_rotated(self, page, r, base, d, deg):
        """Équivalent pivoté de `_paint_run_glyphs` : mêmes avances (chasse du
        glyphe × `sx`, plus le tracking), mais chaque glyphe est posé en page et
        tourné. Toujours glyphe par glyphe : les blocs inclinés sont courts
        (titres, libellés de marge) et le placement colle ainsi exactement à la
        mesure du reflow."""
        fonts = r.get("fonts") or []
        if not fonts:
            return
        size, color, sx = r["size"], r["color"], r.get("sx", 1.0)
        lsp = (r.get("lsp") or 0.0) * size
        x = r["x"]
        for ch in r["text"]:
            gf = reflow.glyph_font(fonts, ch)
            if ch.strip():
                pt = fitz.Point(*self._to_page(x, base, d))
                try:
                    tw = fitz.TextWriter(page.rect, color=color)
                    tw.append(pt, ch, font=gf, fontsize=size)
                    mat = fitz.Matrix(deg)
                    if abs(sx - 1.0) > 0.001:
                        mat = fitz.Matrix(sx, 1) * mat
                    tw.write_text(page, morph=(pt, mat))
                except Exception:
                    pass
            x += reflow.text_width(ch, [(gf, None)], size, sx) + lsp

    @staticmethod
    def _is_justified(el):
        """Le paragraphe SOURCE était-il justifié ? Signal géométrique général :
        ≥ 3 lignes, ferré à gauche (bords gauches ~alignés) ET lignes INTÉRIEURES
        (hors dernière) atteignant le MÊME bord droit (faible variance = fer à
        droite du texte justifié). En « drapeau » ces bords varient beaucoup."""
        lbb = [ln.get("bbox") for ln in el.get("lines", [])
               if ln.get("bbox") and len(ln.get("bbox")) >= 4]
        if len(lbb) < 3:
            return False
        lefts = [b[0] for b in lbb]
        min_left = min(lefts)
        if sum(1 for l in lefts if l - min_left <= 3.0) < 0.6 * len(lefts):
            return False                       # pas ferré à gauche (centré/drapeau)
        inner_rights = [b[2] for b in lbb[:-1]]
        spread = max(inner_rights) - min(inner_rights)
        size = max((r.get("size", 0) or 0 for ln in el.get("lines", [])
                    for r in ln.get("runs", [])), default=10.0)
        col_w = max(b[2] for b in lbb) - min_left
        return spread <= max(0.5 * size, 0.03 * col_w)

    def _parse_translated_segments(self, el):
        """Reconstruit les segments {text, font, size, color} depuis le texte
        traduit balisé (`tr_tagged`) et la table de styles (`tr_segments`)."""
        tagged = el.get("tr_tagged") or ""
        meta = el.get("tr_segments") or []
        segs = []
        for idx, txt in re.findall(r"\[\[(\d+)\]\](.*?)\[\[/\1\]\]",
                                   tagged, re.DOTALL):
            if not txt:
                continue
            i = int(idx)
            m = meta[i] if 0 <= i < len(meta) else (meta[-1] if meta else {})
            segs.append(self._seg_from_meta(m, txt))
        if not segs and tagged.strip():
            # Traduction renvoyée SANS balises → un seul segment, style dominant.
            clean = re.sub(r"\[\[/?\d+\]\]", "", tagged).strip()
            if clean:
                segs.append(self._seg_from_meta(meta[0] if meta else {}, clean))
        return segs

    def _seg_from_meta(self, m, txt):
        """Segment de reflow : UNE SEULE police pour tout le segment — jamais de
        mélange intra-mot (« MANᴜEL », « aveᴢ » en Helvetica détonnaient).

        Choix : la police EMBARQUÉE si elle rend RÉELLEMENT **tous** les glyphes
        du segment (typeface d'origine, fidèle) ; sinon la police ASSORTIE
        complète (PT Serif / Montserrat / Open Sans…) pour **tout** le segment
        (cohérent, proche du typeface). Le test de rendu (`_renders_glyph`) est le
        seul fiable — un sous-ensemble embarqué sur-déclare sa couverture. Une
        base-14 reste en secours ultime pour un glyphe rare absent partout."""
        font_raw = m.get("font", "")
        matched = self._full_fallback_font(font_raw, m.get("bold"),
                                           m.get("italic"))
        primary = self._pick_font(font_raw, txt)
        use_embedded = False
        if primary is not None:
            chars = set(c for c in txt if not c.isspace())
            use_embedded = all(self._renders_glyph(primary, c) for c in chars)
        # Police unique du segment (couverture None = universelle), + base-14 en
        # secours pour un éventuel glyphe non couvert.
        size = m.get("size", 10) or 10
        if use_embedded:
            fonts = [(primary, None), (matched, None)]
        else:
            fonts = [(matched, None)]
            # COMPENSATION DE HAUTEUR D'X : la police assortie n'a pas la même
            # hauteur d'x que le typeface source — à corps égal elle paraît
            # plus grosse/petite et les lignes voisines semblent dépareillées.
            # On ajuste le corps pour égaler la hauteur d'x MESURÉE (rendu à
            # l'encre — les glyphes latins de base du subset sont fiables).
            if primary is not None and self._renders_glyph(primary, "x"):
                xh_src = self._font_xheight(primary)
                xh_mat = self._font_xheight(matched)
                if xh_src and xh_mat:
                    k = max(0.85, min(1.12, xh_src / xh_mat))
                    size = size * k
        # Tracking d'un titre à LETTRES ESPACÉES reconstruit au balisage :
        # lsp = (largeur source − largeur naturelle des glyphes+espaces) /
        # (nombre de joints), mesuré avec les polices RÉELLES du rendu.
        lsp = 0.0
        ls = m.get("letter_spaced")
        if isinstance(ls, dict):
            src = (ls.get("text") or "").strip()
            w_src = float(ls.get("width") or 0.0)
            letters = src.replace(" ", "")
            n_sp = src.count(" ")
            if w_src > 0 and len(letters) > 1:
                base = (reflow.text_width(letters, fonts, size)
                        + n_sp * reflow.text_width(" ", fonts, size))
                lsp = max(0.0, (w_src - base) / (len(letters) - 1)) / size
        elif ls:
            try:
                lsp = max(0.0, float(ls))
            except Exception:
                lsp = 0.0
        return {"text": txt, "fonts": fonts,
                "size": size,
                "color": tuple(m.get("color", (0, 0, 0))),
                "underline": bool(m.get("underline")),
                "lsp": lsp}

    def _font_xheight(self, font):
        """Hauteur d'x RÉELLE d'une police (fraction du corps), mesurée à
        l'encre sur un rendu du glyphe « x » — les métriques déclarées d'un
        sous-ensemble embarqué ne sont pas fiables. En cache par police."""
        cache = getattr(self, "_xh_cache", None)
        if cache is None:
            cache = self._xh_cache = {}
        key = id(font)
        if key in cache:
            return cache[key]
        xh = None
        try:
            doc = fitz.open()
            pg = doc.new_page(width=200, height=200)
            tw = fitz.TextWriter(pg.rect, color=(0, 0, 0))
            tw.append(fitz.Point(30, 150), "x", font=font, fontsize=100)
            tw.write_text(pg)
            pix = pg.get_pixmap(alpha=False)
            w, h = pix.width, pix.height
            s = pix.samples
            rows = [y for y in range(h)
                    if any(s[(y * w + x) * 3] < 200 for x in range(w))]
            if rows:
                xh = (rows[-1] - rows[0] + 1) / 100.0
            doc.close()
        except Exception:
            xh = None
        cache[key] = xh
        return xh

    def _renders_glyph(self, font, ch):
        """True si `font` couvre RÉELLEMENT `ch`. Deux garde-fous complémentaires,
        car chacun ment seul sur un sous-ensemble embarqué :
        - `has_glyph(ch) == 0` → glyphe ABSENT du cmap : fitz SUBSTITUE alors un
          glyphe d'une autre police à l'écran (le test d'encre seul renverrait
          True à tort — c'est le piège du « U »/« É » d'un titre Avenir subsetté
          pour l'anglais) → on rejette ;
        - sinon test d'ENCRE sur pixmap : un glyphe présent au cmap mais dont le
          contour a été retiré au subsetting ne dessine rien → on rejette.
        Les espaces sont toujours « couverts »."""
        if ch.isspace():
            return True
        cache = getattr(self, "_render_cache", None)
        if cache is None:
            cache = self._render_cache = {}
        key = (id(font), ch)
        if key in cache:
            return cache[key]
        try:
            if font.has_glyph(ord(ch)) == 0:     # absent → fitz substituerait
                cache[key] = False
                return False
        except Exception:
            pass
        ok = False
        try:
            doc = fitz.open()
            pg = doc.new_page(width=48, height=48)
            tw = fitz.TextWriter(pg.rect, color=(0, 0, 0))
            tw.append(fitz.Point(4, 34), ch, font=font, fontsize=28)
            tw.write_text(pg)
            pix = pg.get_pixmap(alpha=False)
            ok = min(pix.samples) < 250          # un pixel non blanc = de l'encre
            doc.close()
        except Exception:
            ok = False
        cache[key] = ok
        return ok

    def _full_fallback_font(self, font_raw, bold, italic):
        """Police de REPLI à couverture complète (accents FR + ponctuation), pour
        les glyphes absents du sous-ensemble embarqué. On privilégie une vraie
        police libre ASSORTIE à la famille source (`backend/fonts/` : PT Serif,
        Montserrat, Open Sans, Roboto, Oswald, Merriweather) — bien plus proche du
        typeface embarqué qu'Helvetica (le « z », les accents, le « U » d'un titre
        géométrique ne détonnent plus). Base-14 seulement en dernier recours."""
        cache = getattr(self, "_fallback_cache", None)
        if cache is None:
            cache = self._fallback_cache = {}
        fam = _matched_family(font_raw)
        key = (fam, bool(bold), bool(italic))
        f = cache.get(key)
        if f is None:
            f = _load_matched_font(fam, bold, italic)
            if f is None:                       # aucune police assortie : base-14
                try:
                    f = fitz.Font(_base14_full_name(font_raw, bold, italic))
                except Exception:
                    f = fitz.Font("Helvetica")
            cache[key] = f
        return f

    def _paint_reflow(self, page, res):
        """Peint les lignes de runs placés produites par `reflow`. Chaque run est
        redécoupé en sous-runs par police de glyphe (embarquée / repli) pour que
        les accents s'affichent sans perdre le typeface embarqué ailleurs. Les
        soulignements (liens) sont tracés APRÈS, en fusionnant les runs soulignés
        consécutifs d'une ligne → un trait CONTINU (espaces compris)."""
        for ln in res.get("lines", []):
            base = ln["baseline"]
            for r in ln["runs"]:
                self._paint_run_glyphs(page, r, base)
            self._paint_underlines(page, ln["runs"], base)

    def _paint_underlines(self, page, runs, base):
        """Trace un soulignement CONTINU sous chaque suite de runs soulignés d'une
        ligne (de la gauche du 1er à la droite du dernier, espaces inclus)."""
        i, n = 0, len(runs)
        while i < n:
            if not runs[i].get("underline"):
                i += 1
                continue
            j = i
            x0 = runs[i]["x"]
            x1 = x0
            size = 0.0
            color = runs[i]["color"]
            while j < n and runs[j].get("underline"):
                r = runs[j]
                rw = reflow.text_width(r["text"], r.get("fonts") or [],
                                       r["size"], r.get("sx", 1.0))
                x1 = r["x"] + rw
                size = max(size, r["size"])
                j += 1
            uy = base + 0.12 * (size or 10.0)
            try:
                page.draw_line(fitz.Point(x0, uy), fitz.Point(x1, uy),
                               color=color, width=max(0.4, 0.045 * (size or 10)))
            except Exception:
                pass
            i = j

    def _paint_run_glyphs(self, page, r, base):
        fonts = r.get("fonts") or []
        if not fonts:
            return
        size, color, sx = r["size"], r["color"], r.get("sx", 1.0)
        x = r["x"]
        lsp = (r.get("lsp") or 0.0) * size
        if lsp:
            # Titre à lettres espacées : chaque glyphe est peint séparément,
            # avancé de son propre chasse + tracking (cohérent avec la mesure
            # du reflow).
            for ch in r["text"]:
                gf = reflow.glyph_font(fonts, ch)
                try:
                    pt = fitz.Point(x, base)
                    tw = fitz.TextWriter(page.rect, color=color)
                    tw.append(pt, ch, font=gf, fontsize=size)
                    if abs(sx - 1.0) > 0.001:
                        tw.write_text(page, morph=(pt, fitz.Matrix(sx, 1)))
                    else:
                        tw.write_text(page)
                except Exception:
                    pass
                x += reflow.text_width(ch, [(gf, None)], size, sx) + lsp
            return
        # Regroupe les caractères consécutifs partageant la même police.
        chunk, chunk_font = "", None
        def flush(cx):
            if not chunk:
                return cx
            pt = fitz.Point(cx, base)
            try:
                tw = fitz.TextWriter(page.rect, color=color)
                tw.append(pt, chunk, font=chunk_font, fontsize=size)
                if abs(sx - 1.0) > 0.001:
                    tw.write_text(page, morph=(pt, fitz.Matrix(sx, 1)))
                else:
                    tw.write_text(page)
            except Exception:
                pass
            return cx + reflow.text_width(chunk, [(chunk_font, None)], size, sx)
        for ch in r["text"]:
            gf = reflow.glyph_font(fonts, ch)
            if chunk and gf is not chunk_font:
                x = flush(x)
                chunk = ""
            chunk_font = gf
            chunk += ch
        flush(x)

    def _draw_text_line(self, page, el):
        for run in el.get("runs", []):
            self._draw_run(page, run)

    def _draw_run(self, page, run):
        bb = run.get("bbox")
        origin = run.get("origin") or [bb[0], bb[3]]
        text = run.get("text", "")
        if not text.strip():
            return
        size = run.get("size", 12) or 12
        color = tuple(run.get("color", [0, 0, 0]))
        # Largeur d'origine du run : on rend le texte MIS À L'ÉCHELLE
        # horizontalement pour l'occuper exactement. Les avances de glyphe du
        # sous-ensemble embarqué diffèrent légèrement (~2 %) du placement réel
        # du PDF ; sans correction, la dérive cumulée fait déborder un run sur
        # le suivant (texte qui se colle / se superpose).
        # La cible est la longueur du run LE LONG DE SON AXE D'ÉCRITURE : extent
        # horizontal (largeur) pour le texte horizontal, extent vertical
        # (hauteur) pour le texte vertical/incliné — la bbox est axis-aligned.
        d = run.get("dir") or (1, 0)
        if bb and len(bb) >= 4:
            target_w = (bb[3] - bb[1]) if abs(d[1]) > abs(d[0]) \
                else (bb[2] - bb[0])
        else:
            target_w = None

        # 1) Police source embarquée (fidélité exacte de police + glyphes).
        font = self._pick_font(run.get("font", ""), text)
        # 2) Sinon police base-14 (Helvetica/Times/Courier), comme objet Font
        #    pour bénéficier du même rendu mis à l'échelle.
        if font is None:
            fontname = _base14_fontname(run.get("font", ""), run.get("bold"),
                                        run.get("italic"))
            try:
                font = fitz.Font(fontname)
            except Exception:
                font = None

        if font is not None:
            try:
                self._write_scaled(page, origin, text, font, size, color,
                                   target_w, run.get("dir"))
                return
            except Exception:
                pass

        # 3) Ultime repli.
        try:
            page.insert_text(fitz.Point(origin), text, fontsize=size,
                             fontname="helv", color=color)
        except Exception:
            pass

    def _write_scaled(self, page, origin, text, font, size, color, target_w,
                      direction=None):
        """Écrit `text` à `origin` avec `font`. Texte horizontal : mis à
        l'échelle en x pour occuper exactement `target_w` (anti-dérive). Texte
        incliné/vertical (Étape C, `direction` ≠ (1,0)) : tourné par la matrice
        de rotation d'angle atan2(dy, dx) autour de l'origine."""
        dx, dy = (direction or (1, 0))
        if abs(dy) > 0.01 or dx < 0:            # direction non horizontale
            self._write_rotated(page, origin, text, font, size, color,
                                 target_w, dx, dy)
            return

        core = text.strip()
        if not core:
            return                              # espace pure : rien de visible
        n_lead = len(text) - len(text.lstrip())
        if n_lead:
            # Espace(s) DE TÊTE : dans l'original ce gap peut être large (mot
            # séparé, ou espace « tracké » d'un titre à lettres espacées). La
            # chasse d'espace de la police embarquée est bien plus étroite → si
            # on laisse la police avancer l'espace, le glyphe suivant se colle
            # trop à gauche (gaps de mots écrasés). On positionne donc le core
            # explicitement : slack (largeur d'origine − chasse du core) réparti
            # sur les espaces de tête/queue (même principe que `_write_rotated`).
            try:
                core_w = font.text_length(core, fontsize=size) if target_w else 0.0
            except Exception:
                core_w = 0.0
            slack = max(0.0, (target_w or core_w) - core_w)
            n_trail = len(text) - len(text.rstrip())
            lead_off = slack * n_lead / (n_lead + n_trail) if (n_lead + n_trail) else 0.0
            pt = fitz.Point(origin[0] + lead_off, origin[1])
            tw = fitz.TextWriter(page.rect, color=color)
            tw.append(pt, core, font=font, fontsize=size)
            tw.write_text(page)                 # rendu naturel (pas de distorsion)
            return

        pt = fitz.Point(origin)
        tw = fitz.TextWriter(page.rect, color=color)
        tw.append(pt, text, font=font, fontsize=size)
        sx = self._hscale(font, text, size, target_w)   # anti-dérive (ou None)
        if sx is not None:
            tw.write_text(page, morph=(pt, fitz.Matrix(sx, 1)))
        else:
            tw.write_text(page)

    def _write_rotated(self, page, origin, text, font, size, color,
                       target_w, dx, dy):
        """Rendu du texte INCLINÉ / VERTICAL (Étape C).

        Rotation : `dir` de PyMuPDF est en espace page (y vers le BAS) alors que
        fitz.Matrix(deg) attend un angle au sens mathématique (y vers le HAUT) —
        on nie dy pour convertir le sens (cf. moteur stable `_dir_to_angle`),
        sinon la rotation est inversée (texte à l'envers).

        Espaces de mot : le texte vertical est éclaté en glyphes, souvent avec une
        espace de tête (ex. run ' O'). Dans l'original ce gap est large, mais la
        chasse d'espace de la police embarquée est étroite → au naturel les mots
        se colleraient ; à l'échelle (bbox entière) le glyphe se déformerait. On
        NE rend donc PAS l'espace : on décale le glyphe visible de la largeur du
        gap d'origine (`target_w − chasse(core)`) et on le rend au NATUREL."""
        core = text.strip()
        if not core:
            return                              # espace pure : rien de visible
        deg = math.degrees(math.atan2(-dy, dx))

        lead_off = 0.0
        sx = None
        if core == text:                        # pas d'espace : anti-dérive normal
            sx = self._hscale(font, core, size, target_w)
        elif target_w and target_w > 0:         # espace(s) autour du glyphe
            try:
                core_w = font.text_length(core, fontsize=size)
            except Exception:
                core_w = 0.0
            n_lead = len(text) - len(text.lstrip())
            n_trail = len(text) - len(text.rstrip())
            slack = max(0.0, target_w - core_w)
            if n_lead + n_trail:
                lead_off = slack * n_lead / (n_lead + n_trail)

        pt = fitz.Point(origin[0] + dx * lead_off, origin[1] + dy * lead_off)
        tw = fitz.TextWriter(page.rect, color=color)
        tw.append(pt, core, font=font, fontsize=size)
        mat = fitz.Matrix(deg)
        if sx is not None:
            mat = fitz.Matrix(sx, 1) * mat      # scale (le long de l'axe) puis rotation
        tw.write_text(page, morph=(pt, mat))

    @staticmethod
    def _hscale(font, text, size, target_w):
        """Facteur d'échelle horizontal anti-dérive : ramène la chasse du run à sa
        largeur d'origine `target_w`. Retourne None (pas de correction) si :
        - pas de cible ; ou
        - **run d'un seul glyphe** : sa bbox reflète l'INK (approches latérales),
          pas la chasse — le mettre à l'échelle déformerait le glyphe alors que
          sa position vient déjà de son origine (cas du texte vertical éclaté en
          lettres) ; ou
        - écart aberrant (données incohérentes) ou négligeable.
        Sinon un `sx` borné à [0.5, 2.0]."""
        if not target_w or target_w <= 0:
            return None
        if len(text.strip()) < 2:            # glyphe isolé : positionné par origine
            return None
        try:
            rw = font.text_length(text, fontsize=size)
        except Exception:
            return None
        if rw <= 0:
            return None
        sx = target_w / rw
        if 0.5 <= sx <= 2.0 and abs(sx - 1.0) > 0.005:
            return sx
        return None

    # ── Bordures : une par objet (les lignes de texte sont déjà groupées) ────
    def _draw_borders(self, page, elements):
        for el in elements:
            self._draw_border(page, el)

    # ── Bordure autour d'un objet ────────────────────────────────────────────
    def _draw_border(self, page, el):
        color = BORDER_COLORS.get(el.get("type"), (0.5, 0.5, 0.5))

        # Paragraphe multi-lignes : contour EXACT en escalier épousant chaque
        # ligne (bords droits irréguliers, retraits, enroulement autour d'une
        # image/encart) plutôt qu'un rectangle englobant. Puis, si présent, le
        # CONTENEUR élargi (Étape D) en orange pointillé.
        if el.get("type") == "paragraph":
            bboxes = [ln.get("bbox") for ln in el.get("lines", [])]
            if len([b for b in bboxes if b and len(b) >= 4]) > 1:
                self._draw_stair_outline(page, bboxes, color)
                self._draw_expansion_frame(page, el)
                return
            self._draw_expansion_frame(page, el)

        bbox = el.get("bbox")
        if not bbox or len(bbox) < 4:
            return
        rect = fitz.Rect(bbox)
        if rect.x1 < rect.x0 or rect.y1 < rect.y0:
            return
        # Objet dégénéré (ligne fine) : on l'épaissit un peu pour rester visible.
        if (rect.x1 - rect.x0) < 1 or (rect.y1 - rect.y0) < 1:
            rect = fitz.Rect(rect.x0 - 0.8, rect.y0 - 0.8,
                             rect.x1 + 0.8, rect.y1 + 0.8)
        try:
            page.draw_rect(rect, color=color, width=BORDER_WIDTH)
        except Exception:
            pass

    def _draw_expansion_frame(self, page, el):
        """Trace le cadre du CONTENEUR élargi d'un paragraphe (Étape D) en orange
        pointillé : contour en escalier des lignes élargies (`container_lines`),
        qui épouse un L autour d'un encart. No-op si absent."""
        clines = el.get("container_lines")
        if clines:
            self._draw_stair_outline(page, clines, EXPAND_COLOR,
                                     width=EXPAND_WIDTH, dashes="[3 2] 0")
            return
        cbb = el.get("container_bbox")
        if cbb and len(cbb) >= 4:
            self._draw_stair_outline(page, [cbb], EXPAND_COLOR,
                                     width=EXPAND_WIDTH, dashes="[3 2] 0")

    def _draw_stair_outline(self, page, bboxes, color,
                            width=BORDER_WIDTH, dashes=None):
        """Trace un contour rectilinéaire (en escalier) qui passe par les
        sommets de chaque ligne du paragraphe : côté droit haut→bas, côté
        gauche bas→haut, fermé. Épouse exactement l'étendue réelle des lignes."""
        rows = []
        for bb in bboxes:
            if not bb or len(bb) < 4 or bb[2] <= bb[0] or bb[3] <= bb[1]:
                continue
            rows.append([bb[0], bb[1], bb[2], bb[3]])   # [l, t, r, b]
        if not rows:
            return
        if len(rows) == 1:
            r = rows[0]
            rect = fitz.Rect(r[0], r[1], r[2], r[3])
            try:
                page.draw_rect(rect, color=color, width=width, dashes=dashes)
            except Exception:
                try:
                    page.draw_rect(rect, color=color, width=width)
                except Exception:
                    pass
            return
        rows.sort(key=lambda r: (r[1], r[0]))
        # Frontière verticale nette entre deux lignes voisines (milieu de
        # l'écart) → escalier propre, sans recouvrement ni auto-intersection.
        for i in range(len(rows) - 1):
            mid = (rows[i][3] + rows[i + 1][1]) / 2.0
            rows[i][3] = mid
            rows[i + 1][1] = mid
        pts = []
        for r in rows:                       # côté droit, haut → bas
            pts.append(fitz.Point(r[2], r[1]))
            pts.append(fitz.Point(r[2], r[3]))
        for r in reversed(rows):             # côté gauche, bas → haut
            pts.append(fitz.Point(r[0], r[3]))
            pts.append(fitz.Point(r[0], r[1]))
        pts.append(pts[0])
        try:
            page.draw_polyline(pts, color=color, width=width, dashes=dashes)
        except Exception:
            try:
                page.draw_polyline(pts, color=color, width=width)
            except Exception:
                pass


# ════════════════════════════════════════════════════════════════════════════
# Utilitaires
# ════════════════════════════════════════════════════════════════════════════
def _ends_sentence(text):
    """La ligne se termine-t-elle par une ponctuation forte (hors guillemets) ?"""
    t = text.rstrip().rstrip('"”’\')')
    return bool(t) and t[-1] in ".!?…"


def _starts_capital(text):
    """La ligne commence-t-elle par une majuscule ? (False si chiffre/rien)."""
    for ch in text.lstrip():
        if ch.isalpha():
            return ch.isupper()
        if ch.isdigit():
            return False
    return False


def _starts_lower(text):
    """La ligne commence-t-elle par une minuscule ? (→ continuation certaine)."""
    for ch in text.lstrip():
        if ch.isalpha():
            return ch.islower()
        if ch.isdigit():
            return False
    return False


def _first_word(text):
    t = text.strip()
    return t.split(" ", 1)[0].strip(".,;:!?)]}»”\"'") if t else ""


def _last_word(text):
    """Dernier mot d'une ligne, en minuscules, dépouillé de sa ponctuation
    (pour tester l'appartenance à `_NON_TERMINAL`)."""
    t = text.strip()
    if not t:
        return ""
    return t.rsplit(" ", 1)[-1].strip(".,;:!?([{«\"'").lower()


def _starts_coord(text):
    """La ligne commence-t-elle par une CONJONCTION DE COORDINATION (mot de
    `_COORD_CONJ`) ou son symbole (« & » = and, « + » = plus) ? → continuation
    du paragraphe précédent, pas un nouveau (ex. titre « … DRIVERS / & VEHICLE
    OWNERS »)."""
    t = text.lstrip()
    if t[:1] in "&+":
        return True
    return _first_word(text) in _COORD_CONJ


def _is_horizontal(run):
    """Un run est horizontal si sa direction d'écriture est (≈1, 0)."""
    d = run.get("dir") or [1, 0]
    return abs(d[1]) <= 0.01 and d[0] >= 0


def _int_color_to_rgb(c):
    """Couleur span PyMuPDF (entier 0xRRGGBB) -> [r, g, b] en 0..1."""
    if isinstance(c, (list, tuple)):
        return [float(v) for v in c]
    c = int(c)
    return [((c >> 16) & 255) / 255.0,
            ((c >> 8) & 255) / 255.0,
            (c & 255) / 255.0]


# ── Polices de repli COMPLÈTES assorties (dossier backend/fonts) ─────────────
# Vraies polices libres embarquables couvrant le Latin étendu (accents FR),
# choisies pour RESSEMBLER au typeface source là où le sous-ensemble embarqué ne
# rend pas un glyphe (bien mieux qu'Helvetica). Familles disponibles :
#   ptserif (serif ≈ PT Serif/Times/Minion) · merriweather (serif) ·
#   montserrat (sans géométrique ≈ Avenir/Proxima Nova/Futura) ·
#   opensans / roboto (sans humaniste ≈ Helvetica/Arial/Segoe) ·
#   oswald (sans condensé ≈ titres condensés).
_FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                          "backend", "fonts")


def _matched_family(font_raw):
    """Famille de repli (dossier backend/fonts) la plus proche du nom de police
    source. Choix purement typographique (serif/sans/géométrique/condensé),
    général et indépendant de tout document."""
    n = (font_raw or "").lower()
    if any(t in n for t in ("oswald", "condens", "compress", "narrow", "impact")):
        return "oswald"
    if any(t in n for t in ("avenir", "proxima", "futura", "gotham", "montserrat",
                            "circular", "gothic", "geometr", "poppins", "brandon",
                            "century gothic")):
        return "montserrat"
    if any(t in n for t in ("ptserif", "pt serif", "merri", "georgia", "cambria",
                            "minion", "garamond", "times", "roman", "serif",
                            "book antiqua", "palatino")):
        return "ptserif"
    if "roboto" in n:
        return "roboto"
    # sans par défaut (helvetica, arial, segoe, calibri, open sans, verdana, DIN…)
    return "opensans"


def _load_matched_font(fam, bold, italic):
    """Charge la variante (Regular/Bold/Italic/BoldItalic) d'une famille de repli
    depuis backend/fonts. Retombe sur Regular si la variante manque (ex. Oswald
    n'a pas d'italique). Retourne None si le fichier est introuvable/illisible
    (→ l'appelant utilisera la base-14)."""
    bold, italic = bool(bold), bool(italic)
    variant = ("BoldItalic" if bold and italic else "Bold" if bold
               else "Italic" if italic else "Regular")
    for v in (variant, "Bold" if bold else "Regular", "Regular"):
        path = os.path.join(_FONTS_DIR, f"{fam}-{v}.ttf")
        if os.path.exists(path):
            try:
                return fitz.Font(fontfile=path)
            except Exception:
                continue
    return None


def _base14_family(font_raw):
    """Famille base-14 déduite du nom : 'times' (serif), 'cour' (mono) ou
    'helv' (sans, défaut)."""
    name = (font_raw or "").lower()
    if any(t in name for t in ("mono", "courier", "consol", "typewriter")):
        return "cour"
    if any(t in name for t in ("times", "serif", "georgia", "garamond", "roman",
                               "minion", "cambria", "book")):
        return "times"
    return "helv"


def _base14_full_name(font_raw, bold, italic):
    """Nom base-14 COMPLET accepté par `fitz.Font` (ex. 'Times-BoldItalic',
    'Helvetica-Oblique', 'Courier-Bold') — polices à couverture Latin-1 complète,
    utilisées comme repli pour les glyphes absents du sous-ensemble embarqué."""
    fam = _base14_family(font_raw)
    bold, italic = bool(bold), bool(italic)
    if fam == "times":
        if bold and italic:
            return "Times-BoldItalic"
        if bold:
            return "Times-Bold"
        if italic:
            return "Times-Italic"
        return "Times-Roman"
    base = "Courier" if fam == "cour" else "Helvetica"
    slant = "Oblique"
    if bold and italic:
        return f"{base}-Bold{slant}"
    if bold:
        return f"{base}-Bold"
    if italic:
        return f"{base}-{slant}"
    return base


def _base14_fontname(font_raw, bold, italic):
    """Mappe un nom de police vers l'une des 14 polices PDF de base.

    Volontairement BASIQUE : on préserve la famille (serif/sans/mono) et le
    style (gras/italique) sans embarquer de police. Pour une fidélité de
    glyphes exacte, un embarquement de police serait nécessaire (hors scope
    de ce moteur minimal)."""
    name = (font_raw or "").lower()
    is_serif = any(t in name for t in
                   ("times", "serif", "georgia", "garamond", "roman",
                    "minion", "cambria", "book"))
    is_mono = any(t in name for t in
                  ("mono", "courier", "consol", "typewriter"))
    if is_mono:
        base = "cour"
    elif is_serif:
        base = "times"
    else:
        base = "helv"

    if base == "times":
        if bold and italic:
            return "tibi"
        if bold:
            return "tibo"
        if italic:
            return "tiit"
        return "times"
    # helv / cour partagent le schéma de suffixes b / o / bo
    suffix = ("bo" if bold and italic else
              "b" if bold else
              "o" if italic else "")
    return base + suffix
