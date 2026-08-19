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

from engines.ocr import audit                          # noqa: E402
from engines.ocr import fusion                         # noqa: E402
from engines.ocr import invariants                     # noqa: E402
from engines.ocr import justifie                       # noqa: E402
# `lecture` s'importe SANS Tesseract : ses imports de `pytesseract` sont tous
# dans des fonctions, precisement pour que cette suite tourne sur un poste qui
# n'a pas le binaire.
from engines.ocr import lecture                        # noqa: E402
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

    # ── 6. Le recollage des lignes JUSTIFIEES ────────────────────────────
    # Cas releve a l'ecran : un paragraphe de 4 lignes ressortait en 6 blocs
    # parce que la justification etire les blancs au-dela du seuil de coupe.
    print("\n-- le recollage des lignes justifiees --")

    def _frag(x0, x1, y0, y1, txt, gw=2.5):
        return {"type": "text_line", "bbox": [x0, y0, x1, y1], "gw": gw,
                "ink_x0": x0, "ink_x1": x1, "text": txt,
                "runs": [span_depuis_mot(txt, (x0, y0, x1, y1))]}

    # Une colonne justifiee etroite : 3 rangees au MEME fer gauche (140) et
    # au MEME fer droit (212), dont deux sont coupees en morceaux.
    #
    # Les coordonnees sont celles RELEVEES sur le document (page 1, « Voiture
    # de tourisme plus / remorque si l'ensemble... ») : ecarts de 2,0x a 3,5x
    # la largeur de glyphe, au-dessus du seuil de coupe (2,5x) mais sous le
    # plafond de gouttiere (6x). Inventer ces valeurs donnerait un test qui
    # passe sur une geometrie que le document ne produit pas.
    col = [_frag(140, 157, 480, 484, "Voiture", gw=3.12),
           _frag(163, 168, 480, 484, "de", gw=3.12),
           _frag(173, 198, 480, 488, "tourisme", gw=3.12),
           _frag(202, 212, 480, 485, "plus", gw=3.12),
           _frag(140, 163, 487, 492, "remorque"),
           _frag(173, 176, 487, 491, "si"),
           _frag(186, 212, 487, 491, "l'ensemble"),
           _frag(140, 212, 493, 498, "n'entre pas dans la categorie")]
    rec = justifie.recoller(col)
    ok("les fragments d'une ligne justifiee sont RECOLLES",
       len(rec) == 3, f"{len(rec)} lignes au lieu de 3")
    textes = " | ".join(l["text"] for l in rec)
    ok("le mot isole « de » revient dans sa phrase",
       any("Voiture" in l["text"] and "de" in l["text"] for l in rec), textes)

    # GARDE-FOU 1 : deux VRAIES colonnes ne doivent JAMAIS etre recollees.
    # Elles ne partagent pas leurs fers -- le fer droit de l'une n'est pas le
    # fer gauche de l'autre.
    deux = [_frag(40, 110, 100, 105, "colonne gauche une"),
            _frag(140, 210, 100, 105, "colonne droite une"),
            _frag(40, 110, 110, 115, "colonne gauche deux"),
            _frag(140, 210, 110, 115, "colonne droite deux"),
            _frag(40, 110, 120, 125, "colonne gauche trois"),
            _frag(140, 210, 120, 125, "colonne droite trois")]
    r2 = justifie.recoller(deux)
    ok("MUTATION : deux vraies colonnes ne sont PAS soudees",
       len(r2) == 6, f"{len(r2)} lignes au lieu de 6")

    # GARDE-FOU 2 : un MUR D'ENCRE (filet, bord d'image) interdit le recollage.
    mur = [(170.0, 470.0, 172.0, 510.0)]
    r3 = justifie.recoller(col, murs=mur)
    ok("MUTATION : un mur d'encre empeche le recollage",
       len(r3) > 3, f"{len(r3)} lignes")

    # GARDE-FOU 4 : une fusion n'ENJAMBE PAS le fer gauche d'une AUTRE
    # colonne attestee. Geometries RELEVEES page 2 : la rangee « On |
    # l'appelle | communement » (colonne 39,6 -> 124,6) touchait « nationale
    # est de 30 heures) », qui appartient a la colonne du milieu (fer 130,8).
    # Fusionnee, elle produisait un bloc chevauchant son voisin sur 178 pt.
    #
    # Ni la gouttiere de page ni l'ecart intérieur ne l'attrapaient : mesure,
    # aucune bande vide entre ces deux colonnes, et serrer `_ECART_MAX_GW`
    # DEGRADE (38 defauts a 2,5 contre 23 a 6,0).
    # On teste le garde-fou DIRECTEMENT, et non a travers `recoller`. Passer
    # par la chaine complete demanderait de reproduire les colonnes attestees
    # de la page entiere -- une donnee que le cas isole ne porte pas, et deux
    # tentatives l'ont montre : le test restait vert garde-fou retire, donc
    # AVEUGLE. Cf. « un test qui partage la constante est aveugle ».
    fers = [(39.60, 124.56, 3.11), (130.80, 413.52, 3.46)]
    gauche = [_frag(40.32, 47.28, 394.6, 399.7, "On", gw=2.4),
              _frag(89.28, 125.52, 394.6, 399.7, "communement", gw=2.4)]
    ok("une fusion qui reste DANS sa colonne est autorisee",
       not justifie._franchit_une_colonne(gauche, fers[0], fers))
    # Le meme groupe, mais qui deborde sur la colonne du milieu (fer 130,8).
    a_cheval = gauche + [_frag(133.44, 197.52, 394.6, 399.7, "nationale est de")]
    ok("MUTATION : une fusion qui ENJAMBE le fer d'une autre colonne est "
       "refusee",
       justifie._franchit_une_colonne(a_cheval, fers[0], fers))

    # GARDE-FOU 3 : sans colonne attestee (moins de 3 rangees), on ne touche
    # a rien. « Le vide n'est pas une preuve. »
    r4 = justifie.recoller(col[:3])
    ok("sans colonne attestee, la liste ressort INCHANGEE",
       len(r4) == 3 and all(a is b for a, b in zip(r4, col[:3])))

    # ── 7. L'alignement sur les lignes de Tesseract ──────────────────────
    # Cas releve a l'ecran (« Les permis moto », page 1) : Tesseract rendait
    # les 3 lignes PARFAITEMENT, et l'aval les melangeait -- parce que les
    # hauteurs de boites ne sont pas homogenes sur une meme ligne (mots a
    # boite de LIGNE a 7,7 pt cotoyant des mots mesures a 4,1).
    print("\n-- l'alignement sur les lignes de Tesseract --")
    hetero = [
        span_depuis_mot("Apres", (249, 541, 262, 550), confiance=96),
        span_depuis_mot("permis", (291, 542, 308, 550), confiance=93),
        span_depuis_mot("B,", (308, 541, 312, 546), confiance=88),   # boite serree
        span_depuis_mot("vous", (313, 542, 325, 550), confiance=95),
    ]
    for s in hetero:
        s["_ligne_ocr"] = (15, 1, 1)
    bases_av = {round(s["_base"], 2) for s in hetero}
    ok("AVANT alignement, les baselines DIVERGENT (boites heterogenes)",
       len(bases_av) > 1, f"{sorted(bases_av)}")
    lecture._aligner_sur_lignes_ocr(hetero)
    bases_ap = {round(s["_base"], 2) for s in hetero}
    ok("APRES alignement, tous les mots d'une ligne OCR partagent leur base",
       len(bases_ap) == 1, f"{sorted(bases_ap)}")
    ok("l'alignement ne touche PAS aux boites (l'encre reste mesuree)",
       hetero[2]["bbox"] == [308.0, 541.0, 312.0, 546.0])
    # Deux lignes OCR DIFFERENTES ne doivent jamais etre alignees ensemble.
    autre = span_depuis_mot("suite", (249, 552, 268, 560), confiance=95)
    autre["_ligne_ocr"] = (15, 1, 2)
    tous = hetero + [autre]
    lecture._aligner_sur_lignes_ocr(tous)
    ok("MUTATION : deux lignes OCR distinctes gardent des baselines distinctes",
       round(autre["_base"], 2) != round(hetero[0]["_base"], 2))

    # Le bridage du corps : une boite gonflee ne doit pas elargir sa propre
    # tolerance de rangee (0,45 x size) et avaler la ligne voisine.
    corps = [span_depuis_mot("mot", (0, 0, 10, 4)) for _ in range(9)]
    corps.append(span_depuis_mot("gonfle", (0, 0, 20, 12)))
    lecture._brider_les_corps(corps)
    med = 4.0
    ok("un corps aberrant est BRIDE a 1,2x la mediane de la page",
       abs(corps[-1]["size"] - lecture._CORPS_MAX_MEDIANE * med) < 1e-6,
       f"{corps[-1]['size']:.2f} au lieu de {lecture._CORPS_MAX_MEDIANE * med:.2f}")
    ok("un corps normal n'est PAS touche",
       abs(corps[0]["size"] - 4.0) < 1e-6, f"{corps[0]['size']:.2f}")

    # ── 8. L'audit CHIRURGICAL ───────────────────────────────────────────
    # Regle demandee : « chaque bloc doit delimiter au millimetre pres
    # uniquement le paragraphe concerne, sans empieter ni toucher un autre
    # bloc, ni etre inscrit dans un paragraphe ».
    #
    # Plus dur que les invariants : tolerance ZERO (invariants.py accepte
    # 0,5 pt et n'annonce une inclusion qu'au-dela de 90 %).
    # ---- LA FUSION DES PARAGRAPHES COUPES -------------------------------
    # Geometries RELEVEES sur les 3 pages. Sur 15 paires en conflit, 3
    # seulement sont un paragraphe coupe : les mutations qui suivent gardent
    # les 12 autres, et ce sont elles qui portent la valeur du test.
    print("\n-- la fusion des paragraphes coupes --")

    def _par(x0, y0, x1, y1, lignes):
        ls = [{"bbox": list(bb), "text": t,
               "runs": [{"bbox": list(bb), "text": t}]} for bb, t in lignes]
        return {"bbox": [x0, y0, x1, y1], "lines": ls,
                "text": " ".join(t for _, t in lignes)}

    # CAS REEL page 1 : une seule phrase, coupee en deux blocs qui se
    # chevauchent. Meme fer gauche (249,1 / 248,9), l'un SOUS l'autre.
    coupe = [
        _par(248.88, 541.68, 335.28, 556.80,
             [((249.12, 541.68, 335.28, 550.08), "Apres deux ans de permis B"),
              ((248.88, 547.92, 335.28, 556.80), "autorise a conduire une 125")]),
        _par(248.64, 552.21, 335.28, 578.40,
             [((248.88, 552.21, 327.84, 562.11), "formation complementaire."),
              ((248.88, 560.40, 335.28, 564.96), "Si vous desirez conduire"),
              ((248.64, 573.60, 310.80, 578.40), "devez passer le permis A")]),
    ]
    r6 = fusion.fusionner(coupe)
    ok("un paragraphe coupe en deux est RECOLLE", len(r6) == 1,
       f"{len(r6)} blocs")
    ok("...et le texte recolle est dans l'ordre de lecture",
       "permis B" in (r6[0].get("text") or "")
       and (r6[0].get("text") or "").index("permis B")
       < (r6[0].get("text") or "").index("formation"),
       r6[0].get("text"))

    # MUTATION 1 -- DEUX COLONNES. Le cas le plus dangereux, releve page 3 :
    # « Le depassement est interdit si... » et « ...est autorise si... » sont
    # deux legendes OPPOSEES sous deux images. Les souder est un contresens.
    colonnes2 = [
        _par(38.40, 608.00, 123.00, 625.00,
             [((38.40, 608.0, 123.0, 613.0), "Le depassement est interdit si"),
              ((38.40, 615.0, 123.0, 620.0), "la ligne continue se trouve")]),
        _par(131.00, 608.00, 217.00, 639.00,
             [((131.0, 608.0, 217.0, 613.0), "Le depassement est autorise si"),
              ((131.0, 615.0, 217.0, 620.0), "la ligne discontinue se trouve")]),
    ]
    ok("MUTATION : deux COLONNES cote a cote ne sont PAS fusionnees",
       len(fusion.fusionner(colonnes2)) == 2,
       " // ".join(x.get("text") or "" for x in fusion.fusionner(colonnes2)))

    # MUTATION 2 -- SOMMAIRE : entree et numero de page, separes par des
    # points de conduite. Meme bande horizontale, fers differents.
    sommaire = [
        _par(310.0, 221.0, 377.0, 225.0,
             [((310.0, 221.0, 377.0, 225.0), "La nuit et la meteo")]),
        _par(388.0, 221.0, 427.0, 226.0,
             [((388.0, 221.0, 427.0, 226.0), "145 a 158")]),
    ]
    ok("MUTATION : une entree de sommaire et son numero restent SEPARES",
       len(fusion.fusionner(sommaire)) == 2)

    # MUTATION 3 -- LA CHAINE. Releve page 2 : l'union de deux blocs AVALE un
    # troisieme. Sans garde-fou, une liste entiere finirait en un seul bloc.
    # Le tiers est STRICTEMENT compris dans l'union des deux autres, et lui
    # meme ne peut pas fusionner (fer decale, il est A COTE). C'est la
    # configuration relevee page 2 : l'union de 32 et 34 contient 33.
    # Le tiers est ETROIT et loge dans la zone ou les deux autres se
    # chevauchent : il est donc STRICTEMENT compris dans leur union, sans
    # pouvoir fusionner lui-meme (fer decale, largeur sans rapport).
    chaine = [
        _par(251.0, 284.0, 355.0, 302.0,
             [((251.0, 284.0, 355.0, 289.0), "Accidents Les statistiques")]),
        _par(270.0, 297.0, 290.0, 301.0,
             [((270.0, 297.0, 290.0, 300.0), "le tiers avale")]),
        _par(251.0, 300.0, 355.0, 319.0,
             [((251.0, 300.0, 355.0, 305.0), "pratique et Index 213")]),
    ]
    ok("MUTATION : une fusion qui AVALERAIT un tiers est refusee",
       len(fusion.fusionner(chaine)) == 3,
       f"{len(fusion.fusionner(chaine))} blocs")

    # Une page SANS le defaut doit ressortir identique -- c'est ce qui rend
    # l'operation sure.
    sains = [_par(40, 100, 120, 120, [((40, 100, 120, 105), "un")]),
             _par(40, 140, 120, 160, [((40, 140, 120, 145), "deux")])]
    ok("une page sans blocs qui se touchent ressort INCHANGEE",
       fusion.fusionner(sains) == sains)

    print("\n-- l'audit chirurgical --")

    def _bl(x0, y0, x1, y1, txt="t", n=1):
        ln = {"bbox": [x0, y0, x1, y1],
              "runs": [{"bbox": [x0, y0, x1, y1], "text": txt}]}
        return {"bbox": [x0, y0, x1, y1], "text": txt, "lines": [ln] * n}

    r = audit.auditer([_bl(0, 0, 100, 10, "a"), _bl(0, 20, 100, 30, "b")])
    ok("deux blocs bien SEPARES sont conformes", r["conforme"], str(r["comptes"]))

    r = audit.auditer([_bl(0, 0, 100, 20, "a"), _bl(0, 15, 100, 35, "b")])
    ok("un CHEVAUCHEMENT est detecte", r["comptes"].get("chevauchement") == 1)

    r = audit.auditer([_bl(0, 0, 100, 100, "g"), _bl(10, 10, 20, 20, "p")])
    ok("un bloc INSCRIT dans un autre est detecte",
       r["comptes"].get("bloc_dans_bloc") == 1)

    r = audit.auditer([_bl(0, 0, 100, 10, "a"), _bl(0, 11, 100, 21, "b")])
    ok("deux blocs qui se TOUCHENT (0,1 x ligne) sont detectes",
       r["comptes"].get("blocs_colles") == 1)

    # Le cadre doit coller a son encre : un cadre qui revendique du vide
    # finira par toucher son voisin.
    lache = {"bbox": [0, 0, 100, 40], "text": "x",
             "lines": [{"bbox": [0, 0, 100, 10],
                        "runs": [{"bbox": [0, 0, 100, 10], "text": "x"}]}]}
    r = audit.auditer([lache])
    ok("un cadre LACHE (vide sous l'encre) est detecte",
       r["comptes"].get("cadre_lache") == 1, str(r["comptes"]))

    # MUTATION : la tolerance ZERO doit attraper ce que les invariants
    # laissent passer -- 0,4 pt de recouvrement, sous leur seuil de 0,5.
    frole = [_bl(0, 0, 100, 10, "a"), _bl(0, 9.6, 100, 20, "b")]
    ok("MUTATION : un recouvrement de 0,4 pt (invisible pour les invariants) "
       "est bien un defaut ici",
       audit.auditer(frole)["comptes"].get("chevauchement") == 1
       and invariants.controler([], frole)["comptes"]["blocs_croises"] == 0)

    # ---- LIGNE PARTAGEE : deux blocs ne peuvent pas se partager une ligne --
    # Geometries RELEVEES page 3 du Code de la Route : la ligne « 3 mètres et
    # d'intervalles de » ressort en trois blocs a la meme baseline.
    def _multi(lignes, txt="p"):
        ls = [{"bbox": list(b), "text": t,
               "runs": [{"bbox": list(b), "text": t}]} for b, t in lignes]
        return {"bbox": [min(b[0] for b, _ in lignes),
                         min(b[1] for b, _ in lignes),
                         max(b[2] for b, _ in lignes),
                         max(b[3] for b, _ in lignes)],
                "text": txt, "lines": ls}

    dechiree = [
        _multi([((352.56, 184.08, 437.52, 189.12), "Elles sont composees"),
                ((361.68, 190.80, 378.24, 194.88), "metres"),
                ((352.80, 197.52, 378.72, 201.36), "1,33 metre.")], "p"),
        # `'3'`, le debut de la ligne, expulse a GAUCHE du bloc qui la porte.
        _multi([((352.32, 191.04, 354.96, 195.12), "3")], "q"),
    ]
    r = audit.auditer(dechiree)
    ok("une LIGNE PARTAGEE entre deux blocs est detectee",
       r["comptes"].get("ligne_partagee") == 1, str(r["comptes"]))

    # MUTATION la plus importante : deux COLONNES partagent aussi leur bande
    # verticale. Sans le critere d'ECART, la regle les condamnait toutes --
    # mesure : 61 « defauts » dont l'immense majorite etaient legitimes.
    colonnes = [
        _multi([((40.0, 100.0, 160.0, 105.0), "colonne de gauche")], "g"),
        _multi([((310.0, 100.0, 430.0, 105.0), "colonne de droite")], "d"),
    ]
    ok("MUTATION : deux COLONNES a la meme hauteur ne sont PAS un defaut",
       audit.auditer(colonnes)["comptes"].get("ligne_partagee") is None)

    # LE SEUIL D'ECART S'EST PERIME, LE GARDE-FOU SANS SEUIL PREND LE RELAIS.
    #
    # `_ECART_MEME_LIGNE = 4.0` avait ete calibre sur des gouttieres mesurees
    # a 13-55 x la hauteur de ligne. Les correctifs suivants (alignement des
    # lignes OCR, bridage des corps, recollage justifie) ont RESSERRE la
    # geometrie : les memes gouttieres sont retombees a 0,5-2,6 x, donc SOUS
    # le seuil, et 9 colonnes legitimes etaient accusees (mesure du 08/08,
    # 3 pages du Code de la Route : 17 defauts dont 9 faux).
    #
    # Le garde-fou qui decide desormais ne se regle pas : deux blocs dont les
    # CADRES SONT DISJOINTS ne peuvent pas se dechirer une ligne. Les trois
    # cas ci-dessous passent tous SOUS l'ancien seuil -- ils echouent donc si
    # on retire le garde-fou, ce qui est exactement ce qu'un test doit faire.
    def _col(x0, x1, n, etiq):
        return _multi([((x0, 10.0 + i * 8.0, x1, 15.0 + i * 8.0),
                        f"{etiq}{i}") for i in range(n)], etiq)

    serrees = [_col(40.0, 140.0, 6, "g"), _col(142.0, 250.0, 6, "d")]
    ok("MUTATION : colonnes a gouttiere SERREE (2 pt, sous l'ancien seuil) "
       "ne sont PAS un defaut",
       audit.auditer(serrees)["comptes"].get("ligne_partagee") is None,
       str(audit.auditer(serrees)["comptes"]))

    # Le cas reel « Usagers | Le conducteur » : un titre court en regard
    # d'une colonne longue. C'est lui qui condamne le discriminant par
    # APPARIEMENT des lignes (0,33 rapporte au bloc le plus long).
    inegales = [_col(40.0, 140.0, 6, "g"), _col(149.0, 250.0, 2, "d")]
    ok("MUTATION : colonnes de hauteurs INEGALES ne sont PAS un defaut",
       audit.auditer(inegales)["comptes"].get("ligne_partagee") is None,
       str(audit.auditer(inegales)["comptes"]))

    # ET LE GARDE-FOU NE DOIT PAS TOUT ETEINDRE. Une ligne dechiree laisse
    # forcement une trace dans les cadres : le morceau expulse retombe dans
    # l'emprise du bloc qu'il a quitte. Ici la ligne 2 s'arrete a x=120 (elle
    # a perdu sa fin) et le morceau 130..190 la PROLONGE -- il ne la recouvre
    # pas, sans quoi ce serait un `chevauchement`, deja compte ailleurs.
    corps = _multi([((40.0, 10.0, 200.0, 15.0), "l0"),
                    ((40.0, 18.0, 200.0, 23.0), "l1"),
                    ((40.0, 26.0, 120.0, 31.0), "debut"),
                    ((40.0, 34.0, 200.0, 39.0), "l3"),
                    ((40.0, 42.0, 200.0, 47.0), "l4")], "corps")
    bout = _multi([((130.0, 26.0, 190.0, 31.0), "fin arrachee")], "bout")
    ok("MUTATION : le garde-fou n'eteint PAS la vraie ligne dechiree",
       audit.auditer([corps, bout])["comptes"].get("ligne_partagee") == 1,
       str(audit.auditer([corps, bout])["comptes"]))

    # ---- LIGNE AMPUTEE : le FER GAUCHE trahit, pas la longueur -----------
    #
    # ON PASSE LES DEUX BLOCS, et c'est le fond de la regle : une ligne
    # amputee a perdu son debut AU PROFIT D'UN AUTRE BLOC. Ici `'3'` porte le
    # debut de `'metres'`, sur sa bande et a sa gauche.
    ok("une LIGNE AMPUTEE de son debut est detectee",
       audit.auditer(dechiree)["comptes"].get("ligne_amputee") == 1,
       str(audit.auditer(dechiree)["comptes"]))

    # MUTATION : LE TEXTE ENROULE AUTOUR D'UNE IMAGE N'EST PAS UN DEFAUT.
    #
    # Mesure du 09/08, page 2 : les blocs #7, #12 et #50 contournent un
    # camion. Leur fer gauche descend en marches puis revient au fer plein une
    # fois l'image passee -- `min(fers)` accusait alors les lignes enroulees
    # de commencer jusqu'a 60 pt trop loin, alors que leur texte est
    # parfaitement lu. 3 des 8 defauts restants n'en etaient pas.
    #
    # Ce cas echoue si l'on retire l'exigence « le debut manquant existe
    # ailleurs » : le decalage seul (55,9 a 60,0 pt pour hl = 5,04) est tres
    # au-dessus du seuil.
    enroule = _multi([((94.6, 109.9, 217.9, 114.7), "Il permet de conduire"),
                      ((97.2, 116.4, 217.7, 121.2), "affectes au transport"),
                      ((98.6, 129.4, 217.7, 133.4), "18 ans minimum."),
                      ((38.6, 135.4, 216.5, 140.9), "maniere que le permis C")],
                     "contourne l'image")
    ok("MUTATION : le texte ENROULE autour d'une image n'est PAS ampute",
       audit.auditer([enroule])["comptes"].get("ligne_amputee") is None,
       str(audit.auditer([enroule])["comptes"]))

    # MUTATION : une ligne COURTE au fer est normale (fin de phrase, entree
    # de liste, titre). Juger la longueur signalait 11 blocs corrects sur 17.
    courte = _multi([((40.0, 100.0, 160.0, 105.0), "une ligne pleine ici"),
                     ((40.0, 107.0, 160.0, 112.0), "une autre ligne pleine"),
                     ((40.0, 114.0, 70.0, 119.0), "fin.")], "c")
    ok("MUTATION : une ligne COURTE mais au fer n'est PAS un defaut",
       audit.auditer([courte])["comptes"].get("ligne_amputee") is None)

    # ---- LA PASSE COULEUR -----------------------------------------------
    # Un titre en couleur vive sur fond clair a une LUMINOSITE proche du
    # fond : la conversion en gris l'efface au lieu de le reveler. Mesure
    # page 2, « LA SIGNALISATION » (vert sur blanc) est ABSENT de la lecture
    # normale et lu a 95 % par le canal de saturation.
    try:
        from PIL import Image                             # noqa: E402
        vert = Image.new("RGB", (12, 6), (255, 255, 255))
        for x in range(2, 9):                             # un trait vert vif
            for y in range(1, 5):
                vert.putpixel((x, y), (0, 170, 60))
        canal = lecture._canal_couleur(vert)
        px = list(canal.getdata())
        ok("le canal COULEUR rend le texte colore SOMBRE",
           min(px) < 120, f"min={min(px)}")
        ok("...et laisse le fond blanc CLAIR", max(px) > 240, f"max={max(px)}")
        # MUTATION : un gris n'a aucune saturation, il doit rester invisible
        # pour cette passe -- sinon elle doublerait tout le texte noir.
        gris_img = Image.new("RGB", (12, 6), (255, 255, 255))
        for x in range(2, 9):
            for y in range(1, 5):
                gris_img.putpixel((x, y), (40, 40, 40))   # noir, non colore
        ok("MUTATION : un texte NOIR reste invisible pour la passe couleur",
           min(lecture._canal_couleur(gris_img).getdata()) > 240)
    except ImportError:
        pass

    # ---- LA GARDE DE LA MESURE D'ENCRE ----------------------------------
    # `_boite_encre` cherche l'encre dans une fenetre ELARGIE, pour ne pas
    # couper les jambages. Elle attrape donc aussi l'encre du mot VOISIN, et
    # la garde borne le resultat a la boite de Tesseract elargie d'une
    # demi-marge -- EN X AUTANT QU'EN Y.
    #
    # L'oubli du X coutait cher : mesure page 2, Tesseract rend 32 paires de
    # mots qui se chevauchent et notre mesure en produisait 109. Elle
    # elargissait au lieu de resserrer, d'ou les textes colles
    # (« permis »+« C » recouverts de 4,80 pt).
    try:
        from PIL import Image                             # noqa: E402
        # Deux traits d'encre nettement separes : on mesure celui de GAUCHE,
        # mais avec une boite volontairement etroite. Sans garde en x,
        # l'encre trouvee s'etendrait jusqu'au trait de droite.
        # Le voisin doit tomber DANS la fenetre elargie, sinon le test ne
        # discrimine rien -- premiere version ecrite avec des traits trop
        # ecartes, elle restait verte garde retiree.
        # Boite (4,6)-(16,14) : hauteur 8, marge = 0,25 x 8 = 2, donc la
        # fenetre va de x=2 a x=18. Le voisin est pose a x=17, dedans.
        page_test = Image.new("L", (60, 20), 255)
        for x in list(range(4, 16)) + list(range(17, 30)):
            for y in range(6, 14):
                page_test.putpixel((x, y), 0)
        enc = lecture._boite_encre(page_test, (4, 6, 16, 14))
        ok("la mesure d'encre ne DEBORDE pas sur le mot voisin",
           enc is not None and enc[2] <= 16 + 0.5 * max(1.0, 0.25 * 8),
           f"x1={enc[2] if enc else None} (attendu <= 17)")
        ok("...et elle trouve bien l'encre du mot qu'on lui donne",
           enc is not None and enc[0] <= 5 and enc[2] >= 15,
           f"{enc}")
    except ImportError:
        pass

    # ---- LA FUSION DES PASSES -------------------------------------------
    def _m(x0, y0, x1, y1, t, conf=90.0):
        return {"texte": t, "conf": conf, "ligne": ("n", 0, 0, 0),
                "boite": (x0, y0, x1, y1)}

    # Un candidat qui recouvre un mot deja retenu ne doit pas s'ajouter.
    base = [_m(100, 100, 160, 120, "Usagers")]
    ok("un doublon evident n'est PAS ajoute",
       len(lecture._fusionner(base, [_m(102, 101, 158, 119, "Usagers")])) == 1)
    ok("un mot AILLEURS est bien ajoute",
       len(lecture._fusionner(base, [_m(300, 100, 360, 120, "Vehicule")])) == 2)

    # L'aire commune se rapporte au PLUS PETIT des deux. Rapportee au seul
    # candidat, un candidat LARGE passait a cote d'un petit qu'il recouvre
    # presque entierement -- il suffisait qu'il soit assez grand pour que le
    # rapport tombe sous la tolerance.
    # Le sens qui DISCRIMINE : c'est le GRAND qui est candidat. L'aire commune
    # vaut 100 % du petit mais seulement 29 % du grand -- rapportee au seul
    # candidat, elle passe sous la tolerance et le doublon s'ajoute.
    deja_vu = [_m(280.0, 262.0, 298.0, 278.0, "ule")]
    candidat = [_m(250.0, 260.0, 300.0, 280.0, "Vehicule")]
    ok("un grand mot qui ENGLOBE un fragment deja vu est un doublon",
       len(lecture._fusionner(deja_vu, candidat)) == 1,
       f"{len(lecture._fusionner(deja_vu, candidat))} mots")
    # MUTATION : deux fragments COTE A COTE qui se frolent ne sont PAS des
    # doublons. Mesure sur le cas reel « Vehic » / « ule » : 32 % de
    # recouvrement seulement -- les garder tous les deux est le bon choix,
    # sinon on perdrait la fin du mot.
    a_cote = [_m(281.0, 262.0, 299.5, 272.6, "ule")]
    ok("MUTATION : deux fragments qui se FROLENT sont tous deux gardes",
       len(lecture._fusionner([_m(250.8, 261.4, 287.0, 279.4, "Vehic")],
                              a_cote)) == 2)

    # On compare a TOUT ce qui est deja retenu, y compris aux mots AJOUTES
    # pendant cet appel -- pas aux seules primaires. Le cas discriminant tient
    # en un seul appel : deux candidats identiques, dont le second doit etre
    # rejete a cause du PREMIER, qui n'etait pas primaire.
    jumeaux = [_m(100, 100, 160, 120, "Usagers"),
               _m(102, 101, 158, 119, "Usagers")]
    ok("deux candidats identiques ne s'ajoutent pas deux fois",
       len(lecture._fusionner([], jumeaux)) == 1,
       f"{len(lecture._fusionner([], jumeaux))} mots")

    # ---- LES MOTS COUPES PAR TESSERACT ----------------------------------
    # Geometries RELEVEES page 2 : l'onglet « Vehicule » ressort en « Vehic »
    # (conf 91) + « ule » (conf 80), dans DEUX blocs Tesseract differents
    # (35 et 39), alors que les boites sont jointives a 0,24 pt pres.
    coupe = [{"texte": "Vehic", "conf": 91.0, "ligne": ("w", 35, 1, 1),
              "boite": (254.4, 261.4, 283.4, 275.8)},
             {"texte": "ule", "conf": 80.0, "ligne": ("w", 39, 1, 1),
              "boite": (283.7, 263.3, 299.8, 273.8)}]
    r7 = lecture._recoller_mots_coupes([dict(m) for m in coupe])
    ok("deux moities d'un mot coupe sont RECOLLEES",
       len(r7) == 1 and r7[0]["texte"] == "Vehicule",
       f"{[m['texte'] for m in r7]}")

    # MUTATION 1 : deux mots VOISINS de la MEME ligne OCR ne se soudent pas.
    # Sans cette condition on collerait tous les mots serres d'une ligne --
    # c'est le garde-fou qui porte toute la regle.
    voisins = [dict(m) for m in coupe]
    voisins[1]["ligne"] = voisins[0]["ligne"]
    ok("MUTATION : deux mots de la MEME ligne OCR ne sont PAS soudes",
       len(lecture._recoller_mots_coupes(voisins)) == 2)

    # MUTATION 2 : deux mots separes par une vraie espace restent separes.
    ecartes2 = [dict(m) for m in coupe]
    ecartes2[1]["boite"] = (290.0, 263.3, 306.0, 273.8)   # +6 pt d'ecart
    ok("MUTATION : deux mots separes par une ESPACE restent separes",
       len(lecture._recoller_mots_coupes(ecartes2)) == 2)

    # ...ET LE RECOLLAGE EST BIEN BRANCHE. Tester la fonction seule ne dit
    # rien de son appel : la debrancher laissait la suite VERTE.
    import inspect as _insp                               # noqa: E402
    ok("le recollage des mots coupes est appele par la lecture",
       "_recoller_mots_coupes(mots" in _insp.getsource(lecture.spans_de_page),
       "la fonction existe mais n'est pas branchee")

    # LA CLE DE LIGNE PORTE SA PASSE. Tesseract numerote `block/par/line` PAR
    # APPEL : la ligne (21,1,1) de la passe couleur n'a rien a voir avec la
    # (21,1,1) de la passe normale. Sans distinction, l'alignement leur donne
    # une baseline commune -- mesure, « SIGNALISATION » (y=352,8) recevait la
    # baseline 64,56, et le total des defauts passait de 15 a 35.
    import inspect                                        # noqa: E402
    src_mb = inspect.getsource(lecture._mots_bruts)
    ok("la cle de ligne inclut la PASSE dont elle vient",
       "cle = (passe," in src_mb,
       "les cles de deux passes peuvent entrer en collision")

    # ---- LA MEILLEURE LECTURE GAGNE, MAIS PAS A N'IMPORTE QUEL PRIX ------
    #
    # Defaut signale A L'OEIL sur le sommaire (09/08) : la colonne des
    # paginations ressortait en 'md sis' et 'min' au lieu de '187 a 200' et
    # '201 a 212', alors que la passe distance-au-blanc les lit a 75-95 %.
    # Ce ne sont pas des fragments -- '201' ne CONTIENT pas 'min' -- donc la
    # regle du contenu ne pouvait rien.
    #
    # ⚠ L'AUDIT NE VOIT PAS CE DEFAUT : il juge la geometrie, et rend 5
    # defauts pour tout ecart de 0 a 60. La mesure qui fait foi est le TEXTE,
    # contre les 24 nombres du sommaire releves a l'oeil : 13/24 sans la
    # regle, 17/24 avec.
    def _mot(t, conf):
        return {"texte": t, "conf": conf, "boite": (0, 0, 10, 5)}

    ok("une lecture NETTEMENT plus sure remplace la moins sure",
       lecture._prolonge(_mot("201", 95.0), _mot("min", 37.0)))

    # LES DEUX GARDE-FOUS, chacun impose par une REGRESSION mesuree parmi les
    # 9 substitutions des 3 pages.
    ok("MUTATION : on ne remplace pas par PLUS COURT ('59a80' -> '69a')",
       not lecture._prolonge(_mot("69a", 89.0), _mot("59a80", 58.0)))
    ok("MUTATION : on ne remplace pas par MOINS LISIBLE ('Il' -> '||')",
       not lecture._prolonge(_mot("||", 55.0), _mot("Il", 30.0)))

    # ET L'ECART DOIT ETRE FRANC : une confiance a peine meilleure ne suffit
    # pas, sinon on substitue au bruit de mesure.
    ok("MUTATION : un ecart de confiance FAIBLE ne substitue pas",
       not lecture._prolonge(_mot("201", 50.0), _mot("min", 37.0)))

    # La regle du CONTENU reste prioritaire, ecart de confiance ou non :
    # « rs », fragment de « Usagers », ne doit jamais bloquer le mot entier.
    ok("la regle du CONTENU tient toujours ('rs' -> 'Usagers')",
       lecture._prolonge(_mot("Usagers", 96.0), _mot("rs", 96.0)))

    # ---- LES FLECHES NE SONT PAS DES MOTS -------------------------------
    #
    # Signale A L'OEIL sur le sommaire : les fleches et les filets de conduite
    # ('—', '——', '—>', '—+—', '==') etaient encadres comme des mots, onze
    # fois sur la seule page 2.
    #
    # `tri.py` NE PEUT PAS les attraper : il juge des BLOCS. Une fleche SEULE
    # est bien ecartee, mais celle qui a ete absorbee dans un bloc portant du
    # vrai texte le rend lisible a 80 % -- le bloc est retenu, sa fleche avec.
    #
    # Le discriminant est la LARGEUR PAR CARACTERE, rapportee a la largeur de
    # glyphe de la page. Mesure sur 3 pages, 54 spans sans caractere lisible,
    # deux populations qui NE SE RECOUVRENT PAS :
    #
    #     ponctuation reelle  ':' '-' '+'         0,24 a 2,42 x gw
    #     traits et fleches   '—' '——' '—>' '=='  3,01 a 12,27 x gw
    def _sp(t, x0, x1):
        return {"text": t, "bbox": (x0, 100.0, x1, 105.0),
                "origin": (x0, 105.0), "font": "f", "size": 5.0, "color": 0,
                "flags": 0, "bold": False, "italic": False, "dir": (1, 0),
                "_gw": 2.5, "_base": 105.0, "_ink_x0": x0, "_ink_x1": x1}

    # Des mots reels donnent l'echelle : 2,5 pt par caractere.
    echelle = [_sp("signalisation", 10.0, 42.5), _sp("priorite", 50.0, 70.0),
               _sp("stationnement", 80.0, 112.5)]
    garde = lecture._sans_traits_de_dessin(
        echelle + [_sp("—", 120.0, 130.0)])          # 10 pt pour 1 caractere
    ok("une FLECHE large n'est pas un mot",
       all((s.get("text") or "") != "—" for s in garde),
       str([s["text"] for s in garde]))

    # MUTATION : LA PONCTUATION REELLE SURVIT. C'est elle qui interdit de se
    # contenter du critere « aucun caractere lisible » -- ':' et '-' sont
    # mesures 43 fois sur 3 pages ("Le permis C :", "semi-").
    garde2 = lecture._sans_traits_de_dessin(
        echelle + [_sp(":", 120.0, 121.0), _sp("-", 125.0, 126.3)])
    ok("MUTATION : la PONCTUATION etroite est conservee",
       sum(1 for s in garde2 if (s.get("text") or "") in (":", "-")) == 2,
       str([s["text"] for s in garde2]))

    # ...ET LE FILTRE EST BIEN BRANCHE. Tester la fonction seule ne dit rien
    # de son appel -- la debrancher laisserait les deux checks ci-dessus VERTS.
    ok("le filtre des traits est appele par la lecture",
       "_sans_traits_de_dessin(spans)" in _insp.getsource(lecture.spans_de_page),
       "la fonction existe mais n'est pas branchee")

    # ---- DEFAIRE LES MOTS FONDUS (relecture par ligne) -------------------
    #
    # Signale A L'OEIL : la colonne des paginations ressortait en '532',
    # '59a80', '81a96' -- plusieurs nombres soudes en un seul « mot ». Donner
    # la bande SEULE a Tesseract (--psm 7) les separe a 93-96 %.
    #
    # Teste SANS Tesseract : on injecte la relecture qu'on aurait obtenue, ce
    # que le contrat de spans rend possible. Un test qu'on ne peut pas lancer
    # chez soi ne protege rien.
    from engines.ocr import relecture as _rel               # noqa: E402

    def _span(t, x0, x1, conf, base=105.0):
        s = _sp(t, x0, x1)
        s["_conf"] = conf
        s["_base"] = base
        return s

    def _faux_relire(mots):
        """Remplace l'appel Tesseract par une lecture connue."""
        return lambda *a, **k: mots

    _vrai = _rel._mots_relus
    try:
        # CAS 1 : MEME CONTENU, mieux decoupe. '81a96' lu a 80 %, la relecture
        # rend '81' 'a' '96' a 93-96 %. L'ecart (13-16) ne suffirait PAS --
        # c'est le DECOUPAGE qui prouve, pas la confiance.
        fondu = _span("81à96", 393.4, 412.3, 80.0)
        ligne = {"bbox": (310.0, 100.0, 413.0, 105.0)}
        _rel._mots_relus = _faux_relire([
            (393.1, 398.9, "81", 93.0), (401.0, 404.4, "à", 96.0),
            (406.1, 412.6, "96", 96.0)])
        out = _rel.defaire_les_mots_fondus([fondu], [ligne], object(), 4.17)
        ok("un mot FONDU est defait par la relecture de sa ligne",
           [s["text"] for s in out] == ["81", "à", "96"],
           str([s["text"] for s in out]))

        # LA GEOMETRIE VERTICALE EST CELLE DE L'ANCIEN, jamais celle du relu.
        # C'est la lecon payee deux fois : un mot venu d'une autre lecture
        # apporte sa boite et sa cle de ligne, donc deplace la baseline de
        # toute sa rangee et disloque des blocs corrects ailleurs (15 -> 34).
        ok("MUTATION : les mots defaits gardent la BASELINE de l'ancien",
           all(s["_base"] == fondu["_base"] and s["bbox"][1] == fondu["bbox"][1]
               and s["bbox"][3] == fondu["bbox"][3] for s in out),
           str([(s["_base"], s["bbox"][1], s["bbox"][3]) for s in out]))

        # ...et la geometrie HORIZONTALE est celle du RELU : c'est lui qui a
        # su separer ce que l'autre voyait fondu.
        ok("les mots defaits prennent la position X de la relecture",
           [round(s["bbox"][0], 1) for s in out] == [393.1, 401.0, 406.1],
           str([s["bbox"][0] for s in out]))

        # CAS 2 : CONTENU DIFFERENT ('532' -> '53 a 68'). Le decoupage ne
        # prouve plus rien puisque le texte change : on exige un ecart FRANC.
        _rel._mots_relus = _faux_relire([
            (393.1, 398.9, "53", 95.0), (401.0, 404.4, "à", 95.0),
            (406.1, 412.6, "68", 95.0)])
        sur = _rel.defaire_les_mots_fondus(
            [_span("532", 393.4, 412.3, 64.0)], [ligne], object(), 4.17)
        ok("un contenu DIFFERENT est accepte si la relecture est bien plus sure",
           [s["text"] for s in sur] == ["53", "à", "68"],
           str([s["text"] for s in sur]))

        # MUTATION : le meme cas, mais la relecture n'est PAS plus sure --
        # on ne remplace pas le texte sur un ecart de bruit.
        tiede = _rel.defaire_les_mots_fondus(
            [_span("532", 393.4, 412.3, 90.0)], [ligne], object(), 4.17)
        ok("MUTATION : contenu different SANS ecart franc n'est pas remplace",
           [s["text"] for s in tiede] == ["532"],
           str([s["text"] for s in tiede]))

        # MUTATION : une relecture PEU SURE ne defait rien, meme a contenu
        # identique -- on remplacerait un decoupage douteux par un autre.
        _rel._mots_relus = _faux_relire([
            (393.1, 398.9, "81", 70.0), (401.0, 404.4, "à", 70.0),
            (406.1, 412.6, "96", 70.0)])
        doux = _rel.defaire_les_mots_fondus(
            [_span("81à96", 393.4, 412.3, 80.0)], [ligne], object(), 4.17)
        ok("MUTATION : une relecture PEU SURE ne defait rien",
           [s["text"] for s in doux] == ["81à96"],
           str([s["text"] for s in doux]))

        # MUTATION : UN SEUL mot relu DANS LE SPAN n'est pas un mot fondu.
        # Sans cette garde, la relecture remplacerait un span parfaitement
        # lu des qu'elle le lit un peu mieux -- ce qui est un AUTRE sujet
        # (voir `_mieux_lu`), traite sans toucher aux boites.
        #
        # ⚠ Le cas doit fournir DEUX mots relus sur la ligne pour franchir le
        # `len(relus) < 2` d'entree, mais UN SEUL qui tombe dans le span
        # juge. Sinon le test reste VERT quand on neutralise la garde, et il
        # ne protege rien -- verifie par mutation.
        # Le mot relu porte un texte DIFFERENT et une confiance tres
        # superieure : sans la garde, il passerait par la seconde porte et
        # remplacerait le span. C'est ce qui rend la mutation visible.
        _rel._mots_relus = _faux_relire([
            (393.1, 412.3, "AUTRE", 99.0),      # couvre tout le span
            (330.0, 340.0, "et", 96.0)])        # hors du span, sur la ligne
        seul = _rel.defaire_les_mots_fondus(
            [_span("81à96", 393.4, 412.3, 40.0)], [ligne], object(), 4.17)
        ok("MUTATION : un seul mot relu ne declenche pas l'eclatement",
           [s["text"] for s in seul] == ["81à96"],
           str([s["text"] for s in seul]))
    finally:
        _rel._mots_relus = _vrai

    # ...ET LA RELECTURE EST BIEN BRANCHEE. La debrancher laisserait tous les
    # checks ci-dessus VERTS.
    from engines.ocr.engine import OCREngine as _Moteur      # noqa: E402
    ok("la relecture est appelee par l'analyse de page",
       "defaire_les_mots_fondus" in _insp.getsource(_Moteur.analyser_page),
       "le module existe mais n'est pas branche")

    # ---- COMPLETER PAR DEMI-PAGES ---------------------------------------
    #
    # Signale A L'OEIL sur DEUX documents : une colonne etroite en marge (les
    # paginations d'un sommaire) est ENTIEREMENT ignoree par Tesseract sur la
    # page complete -- 55 nombres sur mv21 page 5, aucun lu. Donnee seule, la
    # colonne se lit parfaitement. C'est la SEGMENTATION qui echoue.
    from engines.ocr import bandes as _bd                      # noqa: E402

    _vrai_bande = _bd._mots_de_bande
    try:
        # La demi-page gauche revele un mot que la page entiere a manque.
        _bd._mots_de_bande = lambda img, x0, x1, h, e, lg: (
            [(48.0, 60.0, 58.0, 67.0, "45", 95.0)] if x0 < 1.0 else [])
        deja = [_span("Chapter", 77.0, 98.0, 96.0, base=67.0)]
        out = _bd.completer_par_bandes(deja, object(), 4.17, 600.0, 800.0)
        ok("une colonne manquee est RETROUVEE par la demi-page",
           [s["text"] for s in out] == ["45", "Chapter"],
           str([s["text"] for s in out]))

        # MUTATION : ON COMPLETE, ON NE REMPLACE JAMAIS. Un mot relu qui
        # recouvre un span deja lu est ignore -- sinon la geometrie existante
        # bouge, et c'est la lecon payee trois fois sur ce moteur.
        # La boite du mot relu doit RECOUVRIR celle du span deja lu, sinon le
        # test ne teste rien : `_span` pose ses boites en y = 100..105.
        _bd._mots_de_bande = lambda img, x0, x1, h, e, lg: (
            [(77.0, 100.0, 98.0, 105.0, "Chapiter", 99.0)] if x0 < 1.0 else [])
        garde = _bd.completer_par_bandes(deja, object(), 4.17, 600.0, 800.0)
        ok("MUTATION : un mot DEJA LU n'est pas remplace par la relecture",
           [s["text"] for s in garde] == ["Chapter"],
           str([s["text"] for s in garde]))

        # MUTATION : UN SEUL CARACTERE ne passe pas. Sans ce filtre, mesure sur
        # DSH : +3 vrais mots pour +6 bruits ('»', 'A', 'a', '4'), tous sur une
        # image de couverture. Le filtre supprime TOUT le bruit des deux
        # documents et ne coute que 5 gains sur mv21.
        #
        # ⚠ ON TESTE LE COMPORTEMENT, PAS LA CONSTANTE. Comparer `_CARS_MIN`
        # a 2 laisse le test VERT quand on neutralise le filtre -- verifie par
        # mutation. On passe donc par la vraie fonction de lecture, avec une
        # image dont on connait le contenu.
        from PIL import Image as _Img                          # noqa: E402
        blanche = _Img.new("RGB", (400, 120), (255, 255, 255))
        try:
            import pytesseract as _pt                          # noqa: E402,F401
            _bd._mots_de_bande = _vrai_bande
            lus = _bd._mots_de_bande(blanche, 0.0, 96.0, 28.8, 4.17, "eng")
            ok("un mot d'UN caractere n'entre pas par les bandes",
               all(len(m[4]) >= 2 for m in lus), str([m[4] for m in lus]))
        except ImportError:
            # Sans Tesseract, on verifie au moins que le filtre est ECRIT dans
            # la fonction -- faible, mais honnete sur ce qu'il vaut.
            #
            # ⚠ On lit la VRAIE fonction (`_vrai_bande`), pas `_bd._mots_de_bande`
            # qui porte encore le faux de ce bloc : la restauration n'a lieu
            # qu'au `finally`, apres ce check.
            ok("le filtre a 2 caracteres est ecrit dans la lecture de bande",
               "len(texte) < _CARS_MIN" in _insp.getsource(_vrai_bande))
    finally:
        _bd._mots_de_bande = _vrai_bande

    # ...ET LES BANDES SONT BIEN BRANCHEES.
    ok("la relecture par bandes est appelee par l'analyse de page",
       "completer_par_bandes" in _insp.getsource(_Moteur.analyser_page),
       "le module existe mais n'est pas branche")

    # ---- LA GRAISSE : l'EPAISSEUR DU TRAIT, pas la densite ---------------
    #
    # La v1 avait essaye la DENSITE D'ENCRE et s'etait trompee (0,340 contre
    # 0,337). Trois mesures eprouvees contre 509 mots gras de verite :
    #
    #     densite d'encre     0,373 / 0,268   mal
    #     NOIRCEUR            0,878 / 0,878   pas du tout
    #     EPAISSEUR DE TRAIT  4,368 / 2,042   franchement
    from engines.ocr import graisse as _gr                     # noqa: E402
    import numpy as _np                                        # noqa: E402

    # Une image SYNTHETIQUE dont on connait la verite : deux traits fins et
    # un trait EPAIS, sur fond blanc.
    tableau = _np.full((40, 120), 255, dtype=_np.uint8)
    tableau[10:30, 10:12] = 0        # trait de 2 px
    tableau[10:30, 30:32] = 0        # trait de 2 px
    tableau[10:30, 50:58] = 0        # trait de 8 px -- le gras

    class _FausseImage:
        def convert(self, mode):
            class _G:
                def __array__(self, dtype=None):
                    return tableau
            return _G()

    def _sg(x0, x1):
        sp = _sp("mot", x0, x1)
        sp["bbox"] = (x0, 10.0, x1, 30.0)
        return sp

    trio = [_sg(9.0, 13.0), _sg(29.0, 33.0), _sg(49.0, 59.0)]
    # 8 spans minimum pour qu'une mediane ait un sens : on complete avec des
    # copies des traits fins.
    trio += [_sg(9.0, 13.0) for _ in range(6)]
    _gr.marquer_le_gras(trio, _FausseImage(), 1.0)
    ok("le TRAIT EPAIS est reconnu comme gras",
       trio[2].get("_gras") is True, str([t.get("_gras") for t in trio[:3]]))
    ok("MUTATION : les traits FINS ne sont pas marques gras",
       not trio[0].get("_gras") and not trio[1].get("_gras"),
       str([t.get("_gras") for t in trio[:3]]))

    # ⚠ ON POSE `_gras`, JAMAIS `bold` NI `flags`. `_group_paragraphs` du
    # moteur PDF COUPE un paragraphe quand `bold` change (« titre gras vs
    # corps ») : marquer le gras faisait passer la reference de 5 a 7 defauts
    # et `blocs_colles` de 2 a 5. La graisse est une propriete de RENDU, pas
    # de structure -- elle vit a cote, comme `_corps`.
    ok("MUTATION : la graisse ne touche NI bold NI flags (le regroupement "
       "coupe dessus)",
       trio[2].get("bold") is False and not (trio[2].get("flags", 0) & 16),
       f"bold={trio[2].get('bold')} flags={trio[2].get('flags')}")

    ok("la graisse est appelee par la lecture",
       "marquer_le_gras" in _insp.getsource(lecture.spans_de_page),
       "le module existe mais n'est pas branche")

    # ---- L'INTERLIGNE, RELEVE ET NON CHOISI -----------------------------
    # Une page synthetique dont on CONNAIT l'interligne : 5 lignes posees
    # tous les 16 pt, mots de 11 pt de haut.
    faux = [{"boite": (50 + 40 * j, 100 + 16 * i, 80 + 40 * j, 111 + 16 * i)}
            for i in range(5) for j in range(3)]
    ok("l'interligne mesure est celui de la page (16 pt)",
       abs(lecture._interligne(faux) - 16.0) < 1e-6,
       f"{lecture._interligne(faux)}")
    # MUTATION : des mots tous sur la MEME ligne n'ont pas d'interligne, et
    # rendre leur hauteur serait pire que rendre zero -- l'appelant
    # n'applique alors aucun plafond.
    ok("MUTATION : sans plusieurs lignes, l'interligne est nul (aucun plafond)",
       lecture._interligne([{"boite": (50, 100, 80, 111)}] * 4) == 0.0)
    # Le plafond garde le BAS : c'est le repere stable, le haut porte la
    # barre, la hampe, ou l'encre de la ligne du dessus.
    import inspect                                        # noqa: E402
    ok("le plafond de hauteur conserve le BAS de l'encre",
       "on garde le BAS" in inspect.getsource(lecture._boite_encre),
       "le plafond ne dit plus quel bord il conserve")

    # ---- L'APERCU ET L'AUDIT PARLENT DE LA MEME GEOMETRIE ----------------
    # Le defaut qui a coute le plus cher a comprendre : l'apercu dessinait
    # chaque cadre 1 pt PLUS GRAND que le bloc. Deux blocs separes de 1,5 pt
    # apparaissaient donc colles a l'ecran, alors que l'audit les voyait
    # disjoints -- et il avait raison. Mesure : 6 vraies paires en contact,
    # 14 a l'ecran, soit 8 defauts fabriques par le dessin.
    #
    # On teste l'ACCORD entre les deux modules, jamais la valeur d'un cote.
    import inspect                                        # noqa: E402
    from engines.ocr import apercu                         # noqa: E402
    src = inspect.getsource(apercu.dessiner)
    ok("l'apercu ne GONFLE plus les cadres (aucune marge de dessin)",
       "marge=" not in src, "une marge de dessin est revenue")

    # Le trait a une epaisseur, CENTREE sur le bord : deux cadres plus proches
    # que `_TRAIT` se touchent a l'ecran quoi qu'en disent leurs boites. On
    # verifie l'ACCORD entre l'epaisseur declaree par l'audit et celle que
    # l'apercu dessine reellement -- deux valeurs qui derivent en silence
    # rouvrent exactement le defaut qu'on vient de fermer.
    # On DESSINE vraiment et on relit le trait pose, plutot que de chercher
    # une chaine dans le code source : la premiere version cherchait
    # « epaisseur=0.7 » et rougissait des que l'appel changeait de forme,
    # alors que l'epaisseur, elle, n'avait pas bouge.
    try:
        import fitz as _fitz                              # noqa: E402
        doc = _fitz.open()
        pg = doc.new_page(width=200, height=100)
        apercu.dessiner(pg, [_bl(20, 20, 120, 40, "x")], [], [],
                        montrer_lignes=False)
        traits = {round(d.get("width") or 0, 3) for d in pg.get_drawings()}
        doc.close()
        ok("l'epaisseur du trait connue de l'audit est celle que l'apercu pose",
           audit._TRAIT in traits,
           f"audit._TRAIT={audit._TRAIT}, apercu pose {sorted(traits)}")
    except ImportError:
        pass
    # ---- LE CONTOUR EN ESCALIER -----------------------------------------
    # Un rectangle englobant revendique du VIDE : la derniere ligne d'un
    # paragraphe est presque toujours plus courte, et le cadre couvre pourtant
    # la largeur entiere. Deux paragraphes voisins semblent alors se toucher
    # la ou leur ENCRE ne se touche pas.
    try:
        import fitz as _fitz                              # noqa: E402
        escalier = {"bbox": [20, 20, 160, 50], "text": "p", "lines": [
            {"bbox": [20, 20, 160, 30], "text": "ligne pleine",
             "runs": [{"bbox": [20, 20, 160, 30], "text": "ligne pleine"}]},
            {"bbox": [20, 35, 60, 45], "text": "fin.",
             "runs": [{"bbox": [20, 35, 60, 45], "text": "fin."}]}]}
        doc = _fitz.open()
        pg = doc.new_page(width=200, height=100)
        apercu.dessiner(pg, [escalier], [], [], montrer_lignes=False,
                        mise_en_page=moteur)
        # Le trace ne doit PAS couvrir le coin bas-DROIT : la 2e ligne
        # s'arrete a x=60, l'escalier s'y retracte, le rectangle non. On juge
        # sur la meme mesure que le repli ci-dessous, pour que les deux tests
        # soient comparables -- et parce qu'une premiere version qui cherchait
        # des sommets `Point` ne trouvait rien dans AUCUN des deux cas, donc
        # restait verte escalier desactive.
        couvre_tout = [d for d in pg.get_drawings()
                       if d.get("rect") and d["rect"].x1 >= 159
                       and d["rect"].y1 >= 49]
        doc.close()
        ok("le contour EPOUSE la ligne courte (escalier, pas rectangle)",
           not couvre_tout,
           f"{len(couvre_tout)} traces couvrent toute la bbox")

        # MUTATION : sans moteur prete, on retombe sur le RECTANGLE englobant
        # -- l'apercu reste lisible, il est seulement moins precis. Le repli
        # produit un item `re` (et non une polyligne de `Point`), qui couvre
        # toute la bbox, ligne courte comprise.
        doc2 = _fitz.open()
        pg2 = doc2.new_page(width=200, height=100)
        apercu.dessiner(pg2, [escalier], [], [], montrer_lignes=False)
        couvre = [d for d in pg2.get_drawings()
                  if d.get("rect") and d["rect"].x1 >= 159
                  and d["rect"].y1 >= 49]
        doc2.close()
        ok("MUTATION : sans moteur prete, le repli RECTANGLE dessine quand meme",
           bool(couvre))
    except ImportError:
        pass

    colles = [_bl(0, 0, 100, 10, "a"), _bl(0, 10.5, 100, 20, "b")]
    ok("MUTATION : deux cadres a 0,5 pt (leurs TRAITS se touchent) sont "
       "un defaut",
       audit.auditer(colles)["comptes"].get("blocs_colles") == 1)

    print(f"\n== {_ok}/{_ok + _ko} ==")
    return 0 if _ko == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
