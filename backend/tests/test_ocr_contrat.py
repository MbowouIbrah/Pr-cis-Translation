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
    ok("une LIGNE AMPUTEE de son debut est detectee",
       audit.auditer([dechiree[0]])["comptes"].get("ligne_amputee") == 1)

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
