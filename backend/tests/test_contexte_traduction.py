"""Traduction CONTEXTUELLE : fiche de document, contexte de page, lots par page.

Ce que l'on vérifie, sur un PDF SYNTHÉTIQUE dont l'ordre d'extraction est
volontairement RENVERSÉ (le titre relevé en dernier, les colonnes à l'envers) :
  • le contexte de page envoyé au modèle suit l'ordre de LECTURE reconstruit
    par la géométrie (titre, colonne gauche, colonne droite, pied) ;
  • la fiche de document est écrite par UN appel, nourrie des titres repérés
    par la mise en page, et jointe au prompt système de CHAQUE lot ;
  • un lot ne mêle jamais deux pages et ne sépare jamais un titre de son texte ;
  • une glose ajoutée par le modèle (« Probatoire (AS-levels) ») est
    redemandée, jamais gardée ;
  • l'échantillon de la fiche couvre tout le document, pas son début ;
  • `translate_json` (PPTX/DOCX/XLSX) écrit sa fiche, ou prend celle reçue.

Exécution :  backend/venv/Scripts/python.exe backend/tests/test_contexte_traduction.py
Aucune dépendance réseau : le client est simulé.
"""
from __future__ import annotations
import copy
import json
import os
import re
import sys
import tempfile
import types

import fitz

import racine  # noqa: F401  -- met backend/ sur le chemin

from engines import translation_ai                                     # noqa: E402
from engines.translation_ai import (TranslatorAI, echantillon_reparti,  # noqa: E402
                                    ordre_lecture, contexte_page)
from engines import runtags                                            # noqa: E402
from engines.pdf.engine import PDFObjectEngine                         # noqa: E402
from engines.pdf import translate                                      # noqa: E402

FONT = "helv"
FICHE = "FICHE-TEST : relevé scolaire d'un pays imaginaire."
GLOSE = "Probatoire"

# Libellés dans l'ordre de LECTURE attendu de la page 1.
P1 = ["Titre Relevé De Parcours",
      "Gauche un ouvre la colonne de gauche sur deux lignes de texte suivi.",
      "Gauche deux poursuit la colonne de gauche sur deux lignes de texte.",
      "Gauche trois termine la colonne de gauche sur deux lignes de texte.",
      "Droite un ouvre la colonne de droite sur deux lignes de texte suivi.",
      "Droite deux poursuit la colonne de droite sur deux lignes de texte.",
      "Droite trois termine la colonne de droite sur deux lignes de texte.",
      "Pied de page commun aux deux colonnes du document synthétique."]


def build(path):
    doc = fitz.open()
    pg = doc.new_page(width=612, height=792)
    pg.insert_text((60, 80), P1[0], fontname=FONT, fontsize=24)
    for col, x in ((0, 60), (1, 330)):
        for k in range(3):
            y = 140 + k * 90                    # mêmes hauteurs à gauche et à droite
            pg.insert_textbox(fitz.Rect(x, y, x + 220, y + 40),
                              P1[1 + 3 * col + k], fontname=FONT, fontsize=10)
    pg.insert_text((60, 720), P1[7], fontname=FONT, fontsize=10)
    pg2 = doc.new_page(width=612, height=792)
    y = 80
    for k in range(3):
        pg2.insert_text((60, y), f"Section Numero {k + 1}", fontname=FONT,
                        fontsize=20)
        y += 40
        for j in range(2):
            mot = GLOSE if (k, j) == (1, 0) else f"Texte {k + 1}.{j + 1} de la section."
            pg2.insert_text((60, y), mot, fontname=FONT, fontsize=10)
            y += 30
        y += 20
    doc.save(path)
    doc.close()


class _Client:
    """Simule DeepSeek : la fiche demandée renvoie FICHE ; une traduction
    renvoie chaque texte en casse inversée (balises intactes), sauf la
    PREMIÈRE fois qu'elle voit GLOSE, qu'elle enrichit d'une parenthèse."""
    def __init__(self):
        self.appels: list[dict] = []
        self.glose_servie = False
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        msgs = kw["messages"]
        systeme = next((m["content"] for m in msgs if m["role"] == "system"), "")
        user = msgs[-1]["content"]
        if "Ne traduis RIEN" in user:
            self.appels.append({"type": "fiche", "user": user})
            contenu = json.dumps({"fiche": FICHE})
        else:
            items = json.loads(user[user.rindex("\n\n[") + 2:])
            self.appels.append({"type": "lot", "system": systeme,
                                "user": user, "items": items})
            out = []
            for it in items:
                t = re.sub(r"(\]\])([^\[]+)(\[\[)",
                           lambda m: m[1] + m[2].swapcase() + m[3], it["text"])
                if (GLOSE in it["text"] and not self.glose_servie):
                    self.glose_servie = True
                    t = t.replace(GLOSE.swapcase(), GLOSE.swapcase() + " (AS-levels)")
                out.append({"id": it["id"], "translated_text": t})
            contenu = json.dumps({"translations": out})
        return types.SimpleNamespace(usage=None, choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=contenu), finish_reason="stop")])


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    # ── Ordre de lecture, sur des boîtes nues ───────────────────────────────
    boites = [(60, 60, 550, 90),                                    # titre
              (60, 140, 280, 180), (60, 230, 280, 270),             # gauche
              (330, 140, 550, 180), (330, 230, 550, 270),           # droite
              (60, 700, 550, 720)]                                  # pied
    attendu = [0, 1, 2, 3, 4, 5]
    ok("ORDRE  boîtes : titre, gauche, droite, pied",
       ordre_lecture(boites) == attendu, str(ordre_lecture(boites)))
    inv = boites[::-1]
    ok("ORDRE  indépendant de l'ordre reçu",
       [inv[i] for i in ordre_lecture(inv)] == boites)

    # ── Échantillon réparti ─────────────────────────────────────────────────
    textes = [f"T{i:03d} " + "x" * 195 for i in range(100)]
    ech = echantillon_reparti(textes, budget=2000)
    ok("FICHE  échantillon : début ET fin du document",
       ech and ech[0].startswith("T000") and any(
           int(t[1:4]) >= 80 for t in ech), str([t[:4] for t in ech]))
    ok("FICHE  échantillon : budget tenu",
       sum(len(t) for t in ech) <= 2400, str(sum(len(t) for t in ech)))

    # ── Défaut « ajout » ────────────────────────────────────────────────────
    ok("AJOUT  glose détectée",
       "ajout" in runtags.defauts("[[0]]Probatoire[[/0]]",
                                  "[[0]]Probatoire (AS-levels)[[/0]]"))
    ok("AJOUT  parenthèse d'origine tolérée",
       "ajout" not in runtags.defauts("[[0]]Note (voir)[[/0]]",
                                      "[[0]]Note (see)[[/0]]"))
    ok("AJOUT  parenthèse pleine chasse d'origine tolérée",
       "ajout" not in runtags.defauts("[[0]]\u6771\u4eac\uff08\u65e5\u672c\uff09[[/0]]",
                                      "[[0]]Tokyo (Japan)[[/0]]"))
    ok("AJOUT  écriture inclusive tolérée",
       "ajout" not in runtags.defauts("[[0]]Dear students[[/0]]",
                                      "[[0]]Chers étudiant(e)s[[/0]]"))

    # ── Fenêtre de contexte ─────────────────────────────────────────────────
    longs = [f"L{i:03d} " + "y" * 200 for i in range(40)]
    fin = contexte_page(longs, autour=(38, 39))
    ok("CONTEXTE  page longue : fenêtre autour du lot, pas le haut de page",
       "L038" in fin and "L039" in fin and "L037" in fin and "L000" not in fin,
       fin[:40])
    ok("CONTEXTE  page courte : entière",
       "L000" in contexte_page(longs[:5], autour=(4, 5)))

    # ── Extraction réelle d'un PDF synthétique, ordre RENVERSÉ ──────────────
    tmp = tempfile.mkdtemp()
    pdf = os.path.join(tmp, "synth.pdf")
    build(pdf)
    eng = PDFObjectEngine()
    src = fitz.open(pdf)
    pages = [eng.extract_page_data(src, i, src[i], embed_images=False)
             for i in range(len(src))]
    titres_pdf, textes_pdf = translate.matiere_fiche_pdf(src)
    src.close()
    ok("FICHE  titres lus dans le PDF",
       P1[0] in titres_pdf and "Section Numero 2" in titres_pdf,
       repr(titres_pdf))
    for p in pages:
        p["elements"].reverse()                 # l'extraction ment sur l'ordre
    vierges = copy.deepcopy(pages)

    client = _Client()
    translation_ai._hors_service["jusqua"] = 0.0
    ancien = translation_ai._client_deepseek
    translation_ai._client_deepseek = lambda: client
    try:
        data = {"pages": pages, "fonts": {}}
        translate.translate_extraction(data, target_lang="en", batch_size=4)
    finally:
        translation_ai._client_deepseek = ancien

    fiches = [a for a in client.appels if a["type"] == "fiche"]
    lots = [a for a in client.appels if a["type"] == "lot"]
    ok("FICHE  un seul appel pour tout le document", len(fiches) == 1,
       str(len(fiches)))
    ok("FICHE  nourrie des titres de mise en page",
       fiches and P1[0] in fiches[0]["user"]
       and "Section Numero 3" in fiches[0]["user"])
    ok("FICHE  jointe au prompt système de CHAQUE lot",
       lots and all(FICHE in a["system"] for a in lots),
       f"{sum(FICHE in a['system'] for a in lots)}/{len(lots)}")

    pages_par_lot = [{it["id"].split("_")[0] for it in a["items"]} for a in lots]
    ok("LOTS  jamais deux pages dans un lot",
       all(len(s) == 1 for s in pages_par_lot), str(pages_par_lot))

    # Contexte de la page 1 (envoyé : 8 paragraphes, lots de 4).
    ctx = next((a["user"] for a in lots
                if a["items"][0]["id"].startswith("p1_")
                and "CONTEXTE DE LA PAGE" in a["user"]), "")
    pos = [ctx.find(lib) for lib in P1]
    ok("CONTEXTE  envoyé quand le lot ne couvre pas la page", bool(ctx))
    ok("CONTEXTE  ordre de lecture malgré l'extraction renversée",
       -1 not in pos and pos == sorted(pos), str(pos))
    ok("CONTEXTE  titre marqué « # »", f"# {P1[0]}" in ctx)

    # Ordre des items DANS les lots de la page 1 : lecture, pas extraction.
    vus = [it["text"] for a in lots for it in a["items"]
           if it["id"].startswith("p1_")]
    rang = [next((k for k, lib in enumerate(P1) if lib[:12] in v), -1)
            for v in vus]
    ok("LOTS  items envoyés dans l'ordre de lecture",
       rang[:len(P1)] == list(range(len(P1))), str(rang))

    # Un titre ne termine jamais un lot s'il reste du texte sur sa page.
    coupes = []
    for a in lots:
        if a["items"][-1].get("support") != "titre":
            continue
        page = a["items"][-1]["id"].split("_")[0]
        suivants = [b for b in lots[lots.index(a) + 1:]
                    if b["items"][0]["id"].startswith(page + "_")]
        if suivants:
            coupes.append(a["items"][-1]["text"])
    ok("LOTS  aucun titre séparé de son texte", not coupes, repr(coupes))

    els = [e for p in pages for e in p["elements"]
           if e.get("type") == "paragraph" and (e.get("text") or "").strip()]
    glose = [e for e in els if GLOSE in (e.get("text") or "")]
    ok("AJOUT  glose servie puis redemandée", client.glose_servie
       and any("AJOUTÉ" in json.dumps(a["items"], ensure_ascii=False)
               for a in lots))
    ok("AJOUT  aucune parenthèse dans le texte final",
       glose and all("(" not in e["tr_tagged"] for e in glose),
       repr([e.get("tr_tagged") for e in glose]))
    ok("TRADUIT  chaque paragraphe a reçu sa traduction",
       all(e.get("tr_tagged") and runtags.sans_balises(e["tr_tagged"])
           != runtags.sans_balises(tagging_src(e)) for e in els))

    # Un lot qui porte toute sa page ne la reçoit pas une seconde fois.
    c1 = _Client()
    translation_ai._client_deepseek = lambda: c1
    try:
        translate.translate_extraction({"pages": copy.deepcopy(vierges),
                                        "fonts": {}}, target_lang="en",
                                       batch_size=40, fiche="")
    finally:
        translation_ai._client_deepseek = ancien
    # (la passe de qualité, elle, isole un fragment : il garde sa page)
    entiers = [a for a in c1.appels if a["type"] == "lot" and len(a["items"]) > 1]
    ok("CONTEXTE  absent quand le lot couvre la page",
       len(entiers) == 2 and not any("CONTEXTE DE LA PAGE" in a["user"]
                                     for a in entiers), str(len(entiers)))

    # Solde épuisé (402) : la page ne passe pas pour traduite.
    class _Refus(Exception):
        status_code = 402

    def refuse(**_kw):
        raise _Refus("solde")
    c2 = _Client()
    c2.chat.completions.create = refuse
    translation_ai._client_deepseek = lambda: c2
    leve = None
    try:
        translate.translate_extraction({"pages": copy.deepcopy(vierges),
                                        "fonts": {}}, target_lang="en",
                                       batch_size=40, fiche="")
    except translation_ai.FournisseursIndisponibles as e:
        leve = e
    finally:
        translation_ai._client_deepseek = ancien
        translation_ai._hors_service.update(jusqua=0.0, statut=None)
    ok("PANNE  402 : la traduction échoue au lieu de rendre la source", leve)

    # Quota par minute (429) : on attend son tour, on ne rend pas la source.
    c3, vu = _Client(), []
    vrai = c3._create

    def une_429(**kw):
        if not vu:
            vu.append(1)
            e = _Refus("quota")
            e.status_code = 429
            raise e
        return vrai(**kw)
    c3.chat.completions.create = une_429
    translation_ai._client_deepseek = lambda: c3
    dormir = translation_ai.time.sleep
    translation_ai.time.sleep = lambda _s: None
    p429 = copy.deepcopy(vierges)
    try:
        translate.translate_extraction({"pages": p429, "fonts": {}},
                                       target_lang="en", batch_size=40,
                                       fiche="")
        leve = None
    except Exception as e:
        leve = e
    finally:
        translation_ai.time.sleep = dormir
        translation_ai._client_deepseek = ancien
        translation_ai._hors_service.update(jusqua=0.0, statut=None)
    els429 = [e for p in p429 for e in p["elements"]
              if e.get("type") == "paragraph" and (e.get("text") or "").strip()]
    ok("PANNE  429 : attendue puis traduite", leve is None and vu and all(
        runtags.sans_balises(e.get("tr_tagged") or "")
        != runtags.sans_balises(tagging_src(e)) for e in els429), repr(leve))
    seuls = [a for a in c3.appels if a["type"] == "lot" and len(a["items"]) == 1]
    ok("PANNE  429 : le lot est relancé entier, pas fragment par fragment",
       len(seuls) <= 1, str(len(seuls)))     # (1 = la glose redemandée)

    # ── translate_json (PPTX/DOCX/XLSX) ─────────────────────────────────────
    for fiche, attendu_fiche in ((None, 1), ("Fiche reçue", 0)):
        c = _Client()
        tr = object.__new__(TranslatorAI)
        tr.client, tr.model, tr.target_batch_size = c, "deepseek-flash", 50
        tr.system_instruction = ""
        chemin = os.path.join(tmp, f"slides_{attendu_fiche}.json")
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump({"slides": [{"text_elements": [
                {"id": "s1_a", "text": "[[0]]Bonjour le monde[[/0]]"}]}]}, f)
        okj, _ = tr.translate_json(chemin, "en", fiche=fiche)
        n = sum(a["type"] == "fiche" for a in c.appels)
        sys_lot = [a["system"] for a in c.appels if a["type"] == "lot"]
        ok(f"JSON  fiche={'reçue' if fiche else 'à écrire'} : "
           f"{attendu_fiche} appel de fiche, jointe au lot",
           okj and n == attendu_fiche
           and all((fiche or FICHE) in s for s in sys_lot), f"{n} {sys_lot}")

    print()
    n_ok = sum(1 for _, c, _ in checks if c)
    for nom, cond, detail in checks:
        etat = "  OK  " if cond else " FAIL "
        ligne = f"{etat}  {nom}"
        if not cond and detail:
            ligne += f"   [{detail}]"
        print(ligne)
    print(f"\n{n_ok}/{len(checks)}")
    return 0 if n_ok == len(checks) else 1


def tagging_src(el):
    from engines.pdf import tagging
    return tagging.tag_paragraph(el)[0]


if __name__ == "__main__":
    sys.exit(main())
