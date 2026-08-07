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

from engines.ocr import invariants                     # noqa: E402
from engines.ocr import tri                            # noqa: E402
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
    # On teste l'ACCORD entre le module et le contrat, jamais la VALEUR de la
    # constante : un test qui recopie 0,78 rougirait au premier reglage sans
    # qu'aucun defaut n'apparaisse, et resterait vert si le module cessait de
    # tenir compte des jambages. Cf. la lecon « un test qui partage la
    # constante est aveugle ».
    from engines.ocr.spans import _DESCENTE          # noqa: E402
    ok("_base se deduit du HAUT, pas du bas (immunise aux jambages)",
       abs(s["_base"] - (100 + _DESCENTE * 14)) < 1e-6, f"{s['_base']}")
    sans = span_depuis_mot("traits", (0, 100, 30, 114))
    ok("un mot SANS jambage pose sa baseline au BAS de sa boite",
       abs(sans["_base"] - 114) < 1e-6, f"{sans['_base']}")
    # LA PREUVE, et non la formule : deux mots de MEME ligne dont l'un porte un
    # jambage doivent partager leur baseline. Mesure du Code de la Route,
    # page 3 : « composées » descendait 1,2 pt sous « traits », soit assez pour
    # basculer dans une autre rangee (tolerance 0,45 x corps).
    sans_jambage = span_depuis_mot("traits", (417.4, 113.8, 429.4, 117.6))
    avec_jambage = span_depuis_mot("composees", (378.7, 114.0, 407.0, 118.8))
    ecart = abs(sans_jambage["_base"] - avec_jambage["_base"])
    tolerance = 0.45 * min(sans_jambage["size"], avec_jambage["size"])
    ok("deux mots d'une MEME ligne restent dans la meme rangee, "
       "jambage ou non (cas reel mesure)",
       ecart < tolerance, f"ecart {ecart:.2f} pt / tolerance {tolerance:.2f} pt")
    # MUTATION : l'ancienne regle prenait le BAS de la boite. Sur ce meme
    # couple de mots reels, elle dispersait 1,20 pt la ou la nouvelle en
    # disperse 0,98 -- et surtout, l'ecart ancien ETAIT LE JAMBAGE ENTIER,
    # donc il grandissait avec le corps, alors que le nouveau est un residu
    # de mesure. Le controle qui juge vraiment est celui de la page entiere
    # (invariants ci-dessous) : 47 croisements avant, mesures apres.
    ancien = abs(117.6 - 118.8)          # bas de boite : traits vs composees
    ok("MUTATION : l'ancienne regle (bas de boite) dispersait davantage",
       ecart < ancien, f"ancien {ancien:.2f} pt vs nouveau {ecart:.2f} pt")
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

    # ── 4. Les invariants de structure ───────────────────────────────────
    # Les trois regles posees par l'utilisateur. Elles n'ont AUCUN seuil :
    # on ne demande pas « ce bloc est-il assez regulier ? » mais « ce mot
    # est-il compte deux fois ? », dont la reponse est un fait.
    print("\n-- les invariants de structure --")
    rapport = invariants.controler(lignes, blocs)
    c = rapport["comptes"]
    ok("aucun mot n'appartient a DEUX LIGNES",
       c["mot_deux_lignes"] == 0, f"{c['mot_deux_lignes']} mots")
    ok("aucun mot n'appartient a DEUX PARAGRAPHES",
       c["mot_deux_blocs"] == 0, f"{c['mot_deux_blocs']} mots")
    ok("aucun paragraphe n'en CONTIENT un autre",
       c["bloc_dans_bloc"] == 0, f"{c['bloc_dans_bloc']} inclusions")
    ok("aucun paragraphe n'en CROISE un autre",
       c["blocs_croises"] == 0, f"{c['blocs_croises']} croisements")
    ok("la page synthetique est integralement CONFORME",
       rapport["conforme"], str(c))

    # MUTATION — les invariants doivent ROUGIR sur une structure fausse,
    # sinon ils ne prouvent rien. On fabrique les trois fautes a la main.
    faux_mot = dict(spans[0])
    l1 = {"bbox": [0, 0, 10, 10], "runs": [faux_mot]}
    l2 = {"bbox": [0, 0, 10, 10], "runs": [faux_mot]}
    ok("MUTATION : un mot pose dans deux lignes est ATTRAPE",
       len(invariants.mot_dans_deux_lignes([l1, l2])) == 1)
    ok("MUTATION : un mot pose dans deux blocs est ATTRAPE",
       len(invariants.mot_dans_deux_blocs(
           [{"bbox": [0, 0, 9, 9], "lines": [l1]},
            {"bbox": [0, 0, 9, 9], "lines": [l2]}])) == 1)
    grand = {"bbox": [0, 0, 100, 100], "lines": []}
    petit = {"bbox": [10, 10, 20, 20], "lines": []}
    ok("MUTATION : un bloc INCLUS dans un autre est ATTRAPE",
       len(invariants.bloc_dans_bloc([grand, petit])) == 1)
    a = {"bbox": [0, 0, 60, 20], "lines": []}
    b = {"bbox": [40, 10, 100, 40], "lines": []}
    ok("MUTATION : deux blocs qui se CROISENT sont attrapes",
       len(invariants.blocs_qui_se_croisent([a, b])) == 1)
    ok("deux blocs qui se TOUCHENT par le bord ne sont pas un croisement",
       invariants.blocs_qui_se_croisent(
           [{"bbox": [0, 0, 50, 20], "lines": []},
            {"bbox": [50, 0, 100, 20], "lines": []}]) == [])

    # ── 5. Le tri des debris ─────────────────────────────────────────────
    # Chaque cas vient d'une MESURE sur le Code de la Route, pas d'une idee
    # de ce qui « devrait » etre un debris.
    print("\n-- le tri des debris --")

    def _bloc(txt, n=1):
        return {"text": txt, "bbox": [0, 0, 50, 10],
                "lines": [{"runs": []}] * n}

    for debris in ("-", "ar", "ou", "si", "LUS", "—>", "4:", "+"):
        r, e = tri.trier([_bloc(debris)])
        ok(f"debris ecarte : {debris!r}", len(e) == 1 and not r,
           f"retenus={len(r)}")
    # Ce que le tri ne doit JAMAIS emporter -- tous releves dans le document.
    for vrai in ("532", "3", "12 50", "E(B).",
                 "Elles delimitent la chaussee de l'accotement."):
        r, e = tri.trier([_bloc(vrai)])
        ok(f"vrai texte GARDE : {vrai[:28]!r}", len(r) == 1 and not e,
           f"ecarte : {e[0]['_raison'] if e else ''}")
    # LIMITE ASSUMEE, ecrite pour qu'elle ne se decouvre pas en production :
    # un mot court ALPHABETIQUE isole est ecarte, « Oui » compris. Isole sur
    # une ligne, c'est presque toujours le fragment d'une ligne voisine mal
    # decoupee -- et l'erreur n'est pas destructive, le mot reste lisible en
    # langue source.
    ok("LIMITE : un mot court alphabetique isole est ecarte (« Oui »)",
       len(tri.trier([_bloc("Oui")])[1]) == 1)
    # MUTATION mesuree : `isalnum()` gardait « ar », « ou », « si », « LUS »
    # (violations 31 -> 37) ; « contient un chiffre » gardait « 4: ».
    ok("MUTATION : ni isalnum() ni « contient un chiffre » ne suffisent",
       tri.trier([_bloc("ar")])[1] and tri.trier([_bloc("4:")])[1]
       and tri.trier([_bloc("532")])[0])
    # Un bloc de PLUSIEURS lignes n'est jamais un debris, meme tres court :
    # trois lignes de deux caracteres sont une colonne de chiffres.
    r, _ = tri.trier([_bloc("de", n=3)])
    ok("un bloc de 3 lignes n'est jamais un debris, meme court", len(r) == 1)

    print(f"\n== {_ok}/{_ok + _ko} ==")
    return 0 if _ko == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
