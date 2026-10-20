"""Résolution des polices que le PDF n'embarque pas en entier (`engines.pdf.polices`).

Chaîne attendue, du plus fidèle au plus proche : la même police sur disque,
sinon téléchargée (puis gardée en cache), sinon son clone métrique libre,
sinon la police locale la plus proche par les chasses MESURÉES dans le PDF.

Tout est SYNTHÉTIQUE et hors ligne : les « téléchargements » sont des copies
renommées des polices livrées (`backend/fonts`), servies par un faux
catalogue ; aucune requête réseau.

Exécution :  cd backend && venv/Scripts/python.exe tests/test_polices.py
"""
from __future__ import annotations
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict

import racine  # noqa: F401  -- met backend/ sur le chemin

import fitz                                                    # noqa: E402
from fontTools.ttLib import TTFont                             # noqa: E402
from engines import translation_ai                             # noqa: E402
from engines.pdf import polices, stream                        # noqa: E402
from engines.pdf.engine import PDFObjectEngine                 # noqa: E402

FONTS = polices.FONTS_DIR


def _renommer(src, famille, ps=None, poids=None, fixe=False, chasse=6):
    """Octets d'une copie de `src` rebaptisée `famille` (et réglée en chasse
    fixe de `chasse` dixièmes d'em si demandé) : une « autre » police,
    fabriquée sans réseau."""
    ft = TTFont(os.path.join(FONTS, src))
    noms = ft["name"]
    for rec in list(noms.names):
        if rec.nameID in (1, 4, 16):
            rec.string = famille
        elif rec.nameID == 6:
            rec.string = ps or re.sub(r"\W", "", famille)
    if poids:
        ft["OS/2"].usWeightClass = poids
    if fixe:
        larg = ft["head"].unitsPerEm * chasse // 10
        hm = ft["hmtx"].metrics
        for g in hm:
            hm[g] = (larg, hm[g][1])
        ft["post"].isFixedPitch = 1
        ft["hhea"].advanceWidthMax = larg
    buf = io.BytesIO()
    ft.save(buf)
    return buf.getvalue()


class Catalogue:
    """Faux Google Fonts : {(famille, poids, italique): octets}, appels comptés."""

    def __init__(self, polices_=None, panne=False):
        self.polices = polices_ or {}
        self.panne = panne
        self.appels = []

    def __call__(self, famille, poids, italique):
        self.appels.append((famille, poids, italique))
        if self.panne:
            raise OSError("réseau injoignable")
        try:
            return self.polices[(famille, poids, italique)]
        except KeyError:
            raise polices.Absente(famille)


def _profil(fontfile=None, fontname="helv", texte="Hamburgefonstiv 0123 abcdxyz"):
    """Chasses mesurées par le moteur sur un PDF composé dans la police donnée."""
    doc = fitz.open()
    pg = doc.new_page()
    kw = {"fontfile": fontfile, "fontname": "F0"} if fontfile else {"fontname": fontname}
    pg.insert_text((40, 80), texte, fontsize=14, **kw)
    acc = defaultdict(list)
    for span in pg.get_texttrace():
        PDFObjectEngine._measure_span(span, acc)
    doc.close()
    return PDFObjectEngine._font_profiles_from({"x": acc})["x"]


def _simuler_traducteur():
    """Remplace le traducteur par une simulation locale (aucun appel payant) ;
    rend la fonction qui remet les méthodes d'origine."""
    cls = translation_ai.TranslatorAI
    noms = ("__init__", "_translate_batch", "fiche_document")
    avant = {n: cls.__dict__[n] for n in noms if n in cls.__dict__}

    def _batch(self, batch, target_lang, progress_callback, *a, **k):
        for b in batch:
            b["translated_text"] = re.sub(
                r"(\[\[/?\d+\]\])|([^\[]+)",
                lambda m: m.group(1) or "Été déjà élevé à présent", b["text"])
    cls.__init__ = lambda self: None
    cls._translate_batch = _batch
    cls.fiche_document = lambda self, *a, **k: ""

    def restaurer():
        for n in noms:
            if n in avant:
                setattr(cls, n, avant[n])
            elif n in cls.__dict__:
                delattr(cls, n)
    return restaurer


def run() -> int:
    checks = []
    restaurer = None

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    tmp = tempfile.mkdtemp(prefix="polices_")
    try:
        # ── 1. Lecture des noms PostScript ────────────────────────────────────
        a = polices.analyser_nom
        ok("NOM  « TimesNewRomanPS-BoldItalicMT » : famille entière, gras, italique",
           a("ABCDEF+TimesNewRomanPS-BoldItalicMT") == ("Times New Roman", 700, True),
           str(a("ABCDEF+TimesNewRomanPS-BoldItalicMT")))
        ok("NOM  « Calibri,Bold », « OpenSans-SemiBoldItalic », « helv »",
           a("Calibri,Bold") == ("Calibri", 700, False)
           and a("OpenSans-SemiBoldItalic") == ("Open Sans", 600, True)
           and a("helv")[0] == "Helvetica",
           f"{a('Calibri,Bold')} {a('OpenSans-SemiBoldItalic')} {a('helv')}")

        # ── 2. La police identique déjà sur disque ───────────────────────────
        loc = os.path.join(tmp, "locales")
        os.makedirs(loc)
        for f in ("roboto-Regular.ttf", "roboto-Medium.ttf", "roboto-Bold.ttf",
                  "ptserif-Regular.ttf", "opensans-Regular.ttf"):
            shutil.copy(os.path.join(FONTS, f), loc)
        cat = Catalogue()
        r = polices.Resolveur([loc], os.path.join(tmp, "c1"), cat)
        p, couche = r.resoudre("QWERTY+Roboto-Medium")
        ok("LOCALE  Roboto Medium trouvée sur disque, sans téléchargement",
           couche == "identique" and p.endswith("roboto-Medium.ttf") and not cat.appels,
           f"{couche} {p} {cat.appels}")

        # ── 3. Téléchargement, validation, cache ─────────────────────────────
        fam = _renommer("montserrat-Bold.ttf", "Testgeo")
        cat = Catalogue({("Testgeo", 700, False): fam})
        cache = os.path.join(tmp, "c2")
        r = polices.Resolveur([loc], cache, cat)
        p, couche = r.resoudre("ABCDEF+Testgeo-Bold")
        ok("TÉLÉCHARGÉE  la variante absente du disque est téléchargée",
           couche == "identique" and p and p.startswith(cache), f"{couche} {p}")
        r2 = polices.Resolveur([loc], cache, cat)
        p2, _ = r2.resoudre("ABCDEF+Testgeo-Bold")
        p3 = r2.telecharger_variante("Testgeo", 700, False)
        ok("CACHE  un second processus la reprend du cache, sans requête",
           p2 == p == p3 and len(cat.appels) == 1, str(cat.appels))

        tronque = Catalogue({("Testgeo", 700, False): fam[: len(fam) // 3]})
        r = polices.Resolveur([loc], os.path.join(tmp, "c3"), tronque)
        p, couche = r.resoudre("Testgeo-Bold")
        ok("REJET  un fichier tronqué n'entre pas dans le cache",
           couche == "proche"
           and not any(n.endswith(".ttf") for n in os.listdir(os.path.join(tmp, "c3"))),
           f"{couche} {os.listdir(os.path.join(tmp, 'c3'))}")
        maigre = Catalogue({("Testgeo", 700, False):
                            _renommer("montserrat-Regular.ttf", "Testgeo")})
        r = polices.Resolveur([loc], os.path.join(tmp, "c4"), maigre)
        p, couche = r.resoudre("Testgeo-Bold")
        ok("REJET  une Regular servie pour une Bold est refusée",
           couche == "proche", f"{couche} {p}")

        # ── 4. Absence mémorisée, panne réseau ───────────────────────────────
        cat = Catalogue()
        cache = os.path.join(tmp, "c5")
        polices.Resolveur([loc], cache, cat).resoudre("Inconnue-Regular")
        polices.Resolveur([loc], cache, cat).resoudre("Inconnue-Regular")
        ok("ABSENTE  une famille inconnue du catalogue n'est demandée qu'une fois",
           len(cat.appels) == 1, str(cat.appels))

        panne = Catalogue(panne=True)
        r = polices.Resolveur([loc], os.path.join(tmp, "c6"), panne)
        t = time.time()
        p1, c1 = r.resoudre("Alpha-Regular")
        p2, c2 = r.resoudre("Beta-Regular")
        ok("PANNE  sans réseau : repli immédiat, une seule tentative pour tout le lot",
           c1 == c2 == "proche" and len(panne.appels) == 1 and time.time() - t < 5,
           f"{c1} {c2} {panne.appels}")

        # ── 5. Clone métrique d'une police propriétaire ──────────────────────
        cat = Catalogue({("Carlito", 700, False):
                         _renommer("opensans-Bold.ttf", "Carlito")})
        r = polices.Resolveur([loc], os.path.join(tmp, "c7"), cat)
        p, couche = r.resoudre("Calibri-Bold")
        ok("CLONE  Calibri introuvable -> Carlito (mêmes chasses)",
           couche == "clone" and "Carlito" in os.path.basename(p),
           f"{couche} {p} {cat.appels}")

        # ── 6. La plus proche, par les chasses mesurées ──────────────────────
        mono = os.path.join(loc, "fixe.ttf")
        with open(mono, "wb") as f:
            # 1,2 em : plus loin du Courier, par les chasses, qu'une sans
            # proportionnelle ; seule la nature de la chasse doit la retenir.
            f.write(_renommer("opensans-Regular.ttf", "Fixe Mono", fixe=True, chasse=12))
        r = polices.Resolveur([loc], os.path.join(tmp, "c8"), None)
        p, couche = r.resoudre("Introuvable-Regular", profil=_profil(fontname="cour"))
        ok("PROCHE  un texte à chasse fixe reste en chasse fixe",
           couche == "proche" and p == mono, f"{couche} {p}")
        p, _ = r.resoudre("Introuvable-Regular", profil=_profil(
            fontfile=os.path.join(FONTS, "ptserif-Regular.ttf")))
        ok("PROCHE  chasses d'une serif -> la serif locale",
           p and p.endswith("ptserif-Regular.ttf"), str(p))
        p, _ = r.resoudre("Introuvable-Regular", profil=_profil(
            fontfile=os.path.join(FONTS, "opensans-Regular.ttf")))
        ok("PROCHE  chasses d'une sans -> la sans locale",
           p and p.endswith("opensans-Regular.ttf"), str(p))

        # ── 7. De bout en bout : le PDF traduit est composé dans la police
        #       téléchargée, pas dans un repli générique ────────────────────
        src_font = os.path.join(tmp, "testgeo-src.ttf")
        with open(src_font, "wb") as f:
            f.write(_renommer("montserrat-Regular.ttf", "Testgeo", ps="Testgeo-Regular"))
        complete = _renommer("montserrat-Regular.ttf", "Testgeo", ps="TestgeoComplete")
        cat = Catalogue({("Testgeo", 400, False): complete})
        polices.configurer(polices.Resolveur([], os.path.join(tmp, "c9"), cat))
        pdf = os.path.join(tmp, "src.pdf")
        doc = fitz.open()
        pg = doc.new_page(width=420, height=300)
        pg.insert_text((30, 80), "Hello simple world text", fontsize=16,
                       fontfile=src_font, fontname="Testgeo-Regular")
        doc.subset_fonts()                      # sous-ensemble : pas d'accent
        doc.save(pdf)
        doc.close()

        restaurer = _simuler_traducteur()
        out = os.path.join(tmp, "out.pdf")
        stream.translate_pdf_progressive(pdf, out, target_lang="fr",
                                         cache_path=os.path.join(tmp, "p.json"))
        d = fitz.open(out)
        noms = {f[3] for f in d.get_page_fonts(0)}
        texte = d[0].get_text()
        d.close()
        ok("RENDU  la traduction accentuée est composée dans la police téléchargée",
           noms and all("Testgeo" in n for n in noms) and "Été" in texte
           and cat.appels == [("Testgeo", 400, False)],
           f"{noms} {texte!r} {cat.appels}")

        # ── 8. Par le flux progressif : les largeurs MESURÉES dans le document
        #       choisissent la police locale la plus proche ───────────────────
        # Deux candidates de même graisse et même pente, l'une étroite, l'autre
        # large (nommées pour que, sans mesure, l'étroite l'emporte au départage
        # par chemin). La police source, large, n'est pas embarquée en entier.
        cand = os.path.join(tmp, "candidates")
        os.makedirs(cand)
        for fichier, src_, fam_, ps_ in (
                ("candidate-etroite.ttf", "oswald-Regular.ttf",
                 "Candidate Etroite", "CandidateEtroite"),
                ("candidate-large.ttf", "montserrat-Regular.ttf",
                 "Candidate Large", "CandidateLarge")):
            with open(os.path.join(cand, fichier), "wb") as f:
                f.write(_renommer(src_, fam_, ps=ps_, poids=400))
        source = os.path.join(tmp, "zorglub-src.ttf")
        with open(source, "wb") as f:
            f.write(_renommer("montserrat-Regular.ttf", "Zorglub", ps="Zorglub-Regular"))
        cat = Catalogue()
        polices.configurer(polices.Resolveur([cand], os.path.join(tmp, "c10"), cat))
        pdf2 = os.path.join(tmp, "src2.pdf")
        doc = fitz.open()
        pg = doc.new_page(width=420, height=300)
        pg.insert_text((30, 80), "Hello simple world text", fontsize=16,
                       fontfile=source, fontname="Zorglub-Regular")
        doc.subset_fonts()
        doc.save(pdf2)
        doc.close()
        out2 = os.path.join(tmp, "out2.pdf")
        stream.translate_pdf_progressive(pdf2, out2, target_lang="fr",
                                         cache_path=os.path.join(tmp, "p2.json"))
        d = fitz.open(out2)
        noms = {f[3] for f in d.get_page_fonts(0)}
        texte = d[0].get_text()
        d.close()
        ok("PROCHE-FLUX  les largeurs du document choisissent la candidate large",
           "Été" in texte and any("candidatelarge" in polices.cle(n) for n in noms)
           and not any("candidateetroite" in polices.cle(n) for n in noms),
           f"{noms} {texte!r}")

        # ── 9. Un nom sans caractère ASCII n'a pas de clé de famille ─────────
        cat = Catalogue()
        r = polices.Resolveur([loc], os.path.join(tmp, "c11"), cat)
        p, couche = r.resoudre("日本語-Regular")
        ok("NOM VIDE  sans clé de famille : ni identique, ni clone, ni requête",
           polices.cle("日本語") == "" and couche == "proche" and not cat.appels,
           f"{couche} {cat.appels}")

        # ── 10. Le réseau ne tient pas le verrou global ───────────────────────
        class Lent(Catalogue):
            def __call__(self, famille, poids, italique):
                time.sleep(0.6)
                return super().__call__(famille, poids, italique)

        lent = Lent({("Testgeo", 700, False): fam})
        r = polices.Resolveur([loc], os.path.join(tmp, "c12"), lent)
        r.index()
        res = []
        fils = [threading.Thread(
            target=lambda: res.append(r.telecharger_variante("Testgeo", 700, False)))
            for _ in range(2)]
        for t in fils:
            t.start()
        time.sleep(0.15)                        # le téléchargement est en vol
        t0 = time.time()
        autre = r.possede("Roboto", 400, False)
        rapide = time.time() - t0
        for t in fils:
            t.join()
        ok("VERROU  une lecture de l'index n'attend pas un téléchargement en vol",
           autre and rapide < 0.3, f"{autre} {rapide:.2f}s")
        ok("VERROU  deux demandes de la même variante ne téléchargent qu'une fois",
           len(lent.appels) == 1 and len(res) == 2 and res[0] == res[1] and res[0],
           f"{lent.appels} {res}")

        # ── 11. Un échec réseau est oublié après le délai ─────────────────────
        panne = Catalogue(panne=True)
        r = polices.Resolveur([loc], os.path.join(tmp, "c13"), panne)
        r.resoudre("Alpha-Regular")
        r.resoudre("Alpha-Regular")
        avant = len(panne.appels)
        for k in r._echecs_reseau:
            r._echecs_reseau[k] -= polices._DELAI_RESEAU_COUPE + 1
        r._reseau_coupe = 0.0
        r.resoudre("Alpha-Regular")
        ok("EXPIRE  l'échec réseau est retenté une fois le délai passé",
           avant == 1 and len(panne.appels) == 2, f"{avant} {panne.appels}")

        # ── 12. Aucune redirection hors des hôtes autorisés ───────────────────
        garde = polices._RedirectionSure()
        req = urllib.request.Request("https://fonts.googleapis.com/css2")
        try:
            garde.redirect_request(req, None, 302, "", {}, "https://exemple.org/x.ttf")
            refus = False
        except urllib.error.URLError:
            refus = True
        permis = garde.redirect_request(
            req, None, 302, "", {}, "https://fonts.gstatic.com/a.ttf")
        ok("REDIRECTION  hors des hôtes autorisés : refusée ; vers un hôte permis : suivie",
           refus and permis is not None, f"{refus} {permis}")

        # ── 13. Un nom de police maison ne part jamais sur le réseau ──────────
        urls = []
        get_reel = polices._get
        polices._get = lambda url, *a, **k: urls.append(url) or b""
        try:
            try:
                polices.google_fonts("BanqueXYZ Sans", 400, False)
                absente = False
            except polices.Absente:
                absente = True
            try:
                polices.google_fonts("PT Serif", 400, False)
            except polices.Absente:
                pass
        finally:
            polices._get = get_reel
        ok("LISTE  famille hors catalogue public : Absente sans requête ; "
           "famille Google : interrogée",
           absente and len(urls) == 1 and "PT+Serif" in urls[0], str(urls))

        # ── 14. Un rendu fait réseau coupé n'est pas conservé ─────────────────
        from app.services import render_cache
        trad = os.path.join(tmp, "doc_TRADUIT.json")
        open(trad, "w").close()
        marque = render_cache.marque_polices()
        polices.Resolveur([loc], os.path.join(tmp, "c14"),
                          Catalogue(panne=True)).resoudre("Gamma-Regular")
        render_cache.store_render(b"%PDF-degrade", trad, marque)
        degrade_absent = render_cache.cache_valid(trad) is None
        render_cache.store_render(b"%PDF-sain", trad, render_cache.marque_polices())
        ok("CACHE RENDU  dégradé (panne réseau) : non écrit ; sain : écrit",
           degrade_absent and render_cache.cache_valid(trad) is not None,
           f"{degrade_absent}")

        # ── 15. Polices du système ignorées sauf demande explicite ────────────
        systeme = set(polices._repertoires_systeme())
        ancien = os.environ.pop("PRECIS_FONT_SYSTEM", None)
        try:
            polices.configurer(None)
            defaut = set(polices.resolveur().repertoires) & systeme
            os.environ["PRECIS_FONT_SYSTEM"] = "1"
            polices.configurer(None)
            active = set(polices.resolveur().repertoires) & systeme
        finally:
            if ancien is None:
                os.environ.pop("PRECIS_FONT_SYSTEM", None)
            else:
                os.environ["PRECIS_FONT_SYSTEM"] = ancien
            polices.configurer(None)
        ok("SYSTÈME  polices de l'OS : ignorées par défaut, consultées si =1",
           not defaut and (active or not systeme), f"{defaut} {active}")

        # ── 16. Noms composés : chasse et graisse qui font partie de la famille
        ok("NOM  « Arial-Black », « Helvetica-NarrowBold », « RobotoCondensed-Bold »",
           a("Arial-Black")[0] == "Arial Black" and a("ArialBlack")[0] == "Arial Black"
           and a("Helvetica-NarrowBold") == ("Helvetica Narrow", 700, False)
           and a("RobotoCondensed-Bold") == ("Roboto Condensed", 700, False),
           f"{a('Arial-Black')} {a('Helvetica-NarrowBold')} {a('RobotoCondensed-Bold')}")

        # ── 17. « Proche » ne change pas de catégorie quand l'autre existe ──
        cat_d = os.path.join(tmp, "categories")
        os.makedirs(cat_d)
        for f in ("ptserif-Regular.ttf", "montserrat-Regular.ttf"):
            shutil.copy(os.path.join(FONTS, f), cat_d)
        r = polices.Resolveur([cat_d], None, None)
        serif_p = _profil(os.path.join(FONTS, "ptserif-Regular.ttf"))
        sans_p = _profil(os.path.join(FONTS, "montserrat-Regular.ttf"))
        sans_pour_serif = r.plus_proche(400, False, serif_p, "Gotham-Book")
        serif_pour_sans = r.plus_proche(400, False, sans_p, "MinionPro-Regular")
        ok("PROCHE  catégorie du nom (marques) prioritaire sur les largeurs",
           "montserrat" in str(sans_pour_serif) and "ptserif" in str(serif_pour_sans),
           f"{sans_pour_serif} {serif_pour_sans}")

        # ── 18. Licence : fsType « Restricted » jamais incorporée ──────────
        lic = os.path.join(tmp, "licences")
        os.makedirs(lic)
        for nom_f, fs in (("Libre", 0), ("Fermee", 2)):
            ft = TTFont(io.BytesIO(_renommer("opensans-Regular.ttf", f"Test{nom_f}")))
            ft["OS/2"].fsType = fs
            ft.save(os.path.join(lic, f"{nom_f}.ttf"))
        familles = {f["famille"] for f in polices.Resolveur([lic], None, None)
                    .index().values()}
        ok("LICENCE  fsType=2 exclue de l'index, fsType=0 gardée",
           familles == {"TestLibre"}, str(familles))

        # ── 19. absences.json borné, téléchargements plafonnés par heure ───
        anciens = (polices._ABSENCES_MAX, polices._PLAFOND_HEURE)
        appels = []

        def absent(famille, poids, italique):
            appels.append(famille)
            raise polices.Absente(famille)
        try:
            polices._ABSENCES_MAX, polices._PLAFOND_HEURE = 3, 4
            r = polices.Resolveur([], os.path.join(tmp, "c19"), absent)
            r._absences_connues()["perimee|400|0"] = time.time() - 40 * 86400
            for i in range(5):
                r._noter_absente(f"Famille{i}", 400, False)
            ab = json.load(open(os.path.join(tmp, "c19", "absences.json"),
                                encoding="utf-8"))
            ok("ABSENCES  périmées purgées, plus anciennes évincées au-delà du maximum",
               sorted(ab) == ["famille2|400|0", "famille3|400|0", "famille4|400|0"],
               str(sorted(ab)))
            marque = polices.degradations()
            for i in range(6):
                r.telecharger_variante(f"Autre{i}", 400, False)
            ok("PLAFOND  au-delà de N téléchargements/heure : refus noté dégradé",
               len(appels) == 4 and polices.degradations() > marque,
               f"{len(appels)} appels")
        finally:
            polices._ABSENCES_MAX, polices._PLAFOND_HEURE = anciens
    finally:
        if restaurer:
            restaurer()
        polices.configurer(None)
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    for nom, cond, detail in checks:
        print(f"  {'OK  ' if cond else 'ECHEC'}  {nom}"
              + (f"   [{detail}]" if detail and not cond else ""))
    passe = sum(1 for _, c, _ in checks if c)
    print(f"\n  {passe}/{len(checks)}\n")
    return 0 if passe == len(checks) else 1


if __name__ == "__main__":
    sys.exit(run())
