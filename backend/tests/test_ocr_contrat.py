"""Le contrat de spans OCR : le raccordement au moteur PDF tient.

CE QUE CETTE SUITE PROUVE
-------------------------
1. CONTRAT   — un span OCR porte les 14 champs que le regroupement lit, et
               `valider_spans` refuse ceux qui n'en portent pas.
2. TRAVERSÉE — ces spans traversent `_group_text_lines` puis
               `_group_paragraphs` et en ressortent en lignes et paragraphes
               conformes. C'est la preuve que la réutilisation est possible.
3. CONFIANCE — la confiance OCR est RETROUVABLE après le regroupement, alors
               même que le run ne la porte pas. C'est le défaut qui a fait
               échouer la v2 en silence.
4. NON-INTRUSION — le moteur PDF n'est pas modifié et n'a besoin ni de
               fichier, ni d'état, ni de contexte de page.

AUCUN TESSERACT N'EST REQUIS, et c'est délibéré : le binaire n'existe pas sur
le poste de développement (il vit dans Docker). Un test qu'on ne peut pas
lancer chez soi ne protège rien. Le contrat de spans permet d'injecter des
mots dont on CONNAÎT la vérité — c'est plus sûr qu'un vrai scan, dont on
ignore la vérité terrain.

    backend/venv/Scripts/python.exe backend/tests/test_ocr_contrat.py
"""
from __future__ import annotations

import sys

import racine  # noqa: F401  -- met backend/ sur le chemin

from engines.ocr.spans import (CHAMPS_REQUIS,          # noqa: E402
                               CHAMP_CONFIANCE,
                               RepertoireConfiance,
                               mots_du_bloc,
                               span_depuis_mot,
                               valider_spans)
from engines.pdf.engine import PDFObjectEngine          # noqa: E402

_ok = _ko = 0


def ok(libelle, cond, detail=""):
    global _ok, _ko
    if cond:
        _ok += 1
        print(f"  [OK ] {libelle}")
    else:
        _ko += 1
        print(f"  [ERR] {libelle}" + (f" -- {detail}" if detail else ""))


def _page_synthetique():
    """Une page dont on connaît la vérité : 2 paragraphes de 3 lignes.

    Écrite ici, pas relevée sur un document : un correctif prouvé sur le
    fichier qui l'a révélé est une heuristique déguisée. Les coordonnées
    imitent une page A4 à 72 dpi, interligne 16 pt, corps ~11 pt.
    """
    spans = []
    # Paragraphe A — 3 lignes, colonne de gauche (x de 50 à ~250).
    for i, mots in enumerate([("Le", "moteur", "lit"),
                              ("une", "page", "scannee"),
                              ("et", "la", "structure")]):
        y = 100 + i * 16
        x = 50.0
        for m in mots:
            larg = 7.0 * len(m)
            spans.append(span_depuis_mot(m, (x, y, x + larg, y + 11),
                                         confiance=90.0))
            x += larg + 5.0
    # Paragraphe B — 3 lignes, NETTEMENT plus bas (écart de 40 pt : une
    # frontière de paragraphe franche, pas un simple interligne).
    for i, mots in enumerate([("Un", "second", "bloc"),
                              ("suit", "le", "premier"),
                              ("sur", "la", "meme", "colonne")]):
        y = 200 + i * 16
        x = 50.0
        for m in mots:
            larg = 7.0 * len(m)
            spans.append(span_depuis_mot(m, (x, y, x + larg, y + 11),
                                         confiance=75.0))
            x += larg + 5.0
    return spans


def run():
    print("== Contrat de spans OCR ==\n")
    moteur = PDFObjectEngine()

    # ── 1. Le contrat lui-même ───────────────────────────────────────────
    print("-- le contrat --")
    s = span_depuis_mot("Bonjour", (50, 100, 110, 114), confiance=91.0)
    ok("un span porte les 14 champs requis",
       all(c in s for c in CHAMPS_REQUIS),
       f"absents : {[c for c in CHAMPS_REQUIS if c not in s]}")
    ok("valider_spans accepte un span conforme", valider_spans([s]) == [])
    ok("_gw = largeur / nb de caracteres (7 lettres, 60 pt)",
       abs(s["_gw"] - 60.0 / 7) < 1e-6, f"{s['_gw']}")
    ok("_base est le BAS du mot (ligne de base approchee)",
       s["_base"] == 114)
    ok("la police est None, jamais devinee",
       s["font"] is None)
    ok("l'encre d'un mot lu EST sa boite (pas de blancs de tete)",
       s["_ink_x0"] == 50 and s["_ink_x1"] == 110)

    # Le validateur doit ATTRAPER ce que Python laisserait passer en silence.
    for champ in ("_gw", "_base", "bbox", "dir"):
        mutant = dict(s)
        del mutant[champ]
        ok(f"valider_spans REFUSE un span sans « {champ} »",
           valider_spans([mutant]) != [])
    zero = dict(s, _gw=0.0)
    ok("valider_spans REFUSE une largeur de glyphe nulle "
       "(sinon chaque espace coupe une colonne)",
       valider_spans([zero]) != [])

    # ── 2. La traversee du moteur PDF ────────────────────────────────────
    print("\n-- la traversee du moteur PDF --")
    ok("le moteur PDF s'instancie SANS etat ni fichier",
       vars(moteur) == {}, f"{list(vars(moteur))[:5]}")

    spans = _page_synthetique()
    ok("la page synthetique est conforme au contrat",
       valider_spans(spans) == [], str(valider_spans(spans))[:120])

    lignes = moteur._group_text_lines(spans)
    ok("les mots se regroupent en 6 lignes (3 + 3)",
       len(lignes) == 6, f"{len(lignes)} lignes")

    textes = [ln["text"] for ln in lignes]
    ok("une ligne recompose ses mots avec des espaces",
       "Le moteur lit" in textes, str(textes[:2]))
    ok("aucun mot n'est perdu au regroupement",
       sum(len(ln["runs"]) for ln in lignes) == len(spans),
       f"{sum(len(ln['runs']) for ln in lignes)} / {len(spans)}")

    blocs = moteur._group_paragraphs(lignes)
    ok("les lignes se regroupent en 2 paragraphes",
       len(blocs) == 2, f"{len(blocs)} blocs")
    ok("_group_paragraphs fonctionne SANS contexte de page (ctx=None)",
       all(b.get("bbox") and b.get("lines") for b in blocs))

    if len(blocs) == 2:
        a, b = blocs
        ok("le bloc A porte ses 3 lignes", len(a["lines"]) == 3,
           f"{len(a['lines'])}")
        ok("le bloc B porte ses 3 lignes", len(b["lines"]) == 3,
           f"{len(b['lines'])}")
        ok("le cadre du bloc A encadre bien ses mots",
           a["bbox"][0] <= 50.0 and a["bbox"][1] <= 100.0
           and a["bbox"][3] >= 100 + 2 * 16,
           str(a["bbox"]))
        ok("les deux blocs ne se chevauchent PAS verticalement",
           a["bbox"][3] < b["bbox"][1],
           f"{a['bbox'][3]} vs {b['bbox'][1]}")

    # ── 3. La confiance, qui ne traverse pas toute seule ─────────────────
    print("\n-- la confiance OCR --")
    run0 = lignes[0]["runs"][0]
    ok("un RUN ne porte PAS la confiance (le moteur PDF la jette)",
       CHAMP_CONFIANCE not in run0,
       "si ceci rougit, le moteur PDF a change : relire RepertoireConfiance")
    ok("lire ligne['spans'] rend une liste VIDE (le piege de la v2)",
       (lignes[0].get("spans") or []) == [])
    ok("mots_du_bloc trouve quand meme les mots (il lit « runs »)",
       len(mots_du_bloc(blocs[0])) == 9, f"{len(mots_du_bloc(blocs[0]))}")

    rep = RepertoireConfiance(spans)
    c_a = rep.confiances_du_bloc(blocs[0])
    c_b = rep.confiances_du_bloc(blocs[1])
    ok("la confiance du bloc A est RETROUVEE pour ses 9 mots",
       len(c_a) == 9, f"{len(c_a)} confiances")
    ok("le bloc A rend bien 90 (sa vraie confiance)",
       c_a and all(abs(c - 90.0) < 1e-6 for c in c_a), str(c_a[:3]))
    ok("le bloc B rend bien 75, distinct de A",
       c_b and all(abs(c - 75.0) < 1e-6 for c in c_b), str(c_b[:3]))
    ok("un mot inconnu du repertoire rend None, JAMAIS zero "
       "(« je ne sais pas » n'est pas « mal lu »)",
       rep.confiance({"bbox": [9999, 9999, 10000, 10000]}) is None)
    ok("un span BRUT rend sa confiance sans passer par la geometrie",
       rep.confiance(s) == 91.0)

    # MUTATION — si le repertoire n'indexait rien, tout bloc paraitrait
    # « sans confiance » et un tri l'ecarterait. C'est le silence de la v2.
    vide = RepertoireConfiance([])
    ok("MUTATION : un repertoire vide ne rend AUCUNE confiance "
       "(c'est ce silence qui a fait echouer la v2)",
       vide.confiances_du_bloc(blocs[0]) == [])

    print(f"\n== {_ok}/{_ok + _ko} ==")
    return 0 if _ko == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
