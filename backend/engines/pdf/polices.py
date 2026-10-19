"""Résolution d'une police que le PDF n'embarque pas, ou pas en entier.

Le moteur peint d'abord avec la police EMBARQUÉE (`engine._pick_font`). Quand
elle manque, ou qu'un sous-ensemble ne couvre pas les glyphes de la traduction,
ce module fournit la police de remplacement, du plus fidèle au plus proche :

1. la police IDENTIQUE déjà sur disque : dossier livré `backend/fonts`,
   catalogue pré-chargé à la construction de l'image, cache des
   téléchargements, polices du système ;
2. la police identique TÉLÉCHARGÉE (Google Fonts, fichier complet et non un
   sous-ensemble), mise en cache pour toujours ; à défaut son CLONE MÉTRIQUE
   libre (Arial → Arimo, Calibri → Carlito…), dont chaque glyphe a la largeur
   de l'original : la mise en page ne bouge pas ;
3. la police locale la plus PROCHE : même chasse (fixe ou proportionnelle),
   largeurs des glyphes réellement composés dans le document, graisse, pente,
   empattements. Les largeurs priment : c'est d'elles que dépendent les coupes
   de ligne, donc la mise en forme.

Rien ici ne connaît un document : les clones sont une propriété publique des
polices, les mesures viennent du PDF traité.
"""
from __future__ import annotations

import io
import json
import logging
import math
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import fitz

log = logging.getLogger(__name__)

_BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FONTS_DIR = os.path.join(_BACKEND, "fonts")
# Pré-chargé par `scripts/precharger_polices.py` à la construction de l'image.
CATALOGUE_DIR = os.path.join(FONTS_DIR, "catalogue")
# Sur le volume persistant : un téléchargement survit à la reconstruction.
CACHE_DIR = os.path.join(_BACKEND, "translations", "_polices")

_CATALOGUE_NORM = os.path.normcase(os.path.abspath(CATALOGUE_DIR))

# Glyphes mesurés sur chaque police candidate : de quoi comparer des largeurs
# et vérifier qu'une police écrit le français.
_SONDE = ("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
          ".,;:!?'-()/%")
_FRANCAIS = "aeAEéèêàçôùÉÀ"

# Clones MÉTRIQUES libres : chaque glyphe a l'avance de l'original. Arial a été
# dessinée aux mesures d'Helvetica, d'où les deux entrées.
CLONES = {
    "arial": ("Arimo", "Liberation Sans"),
    "helvetica": ("Arimo", "Liberation Sans", "Arial"),
    "timesnewroman": ("Tinos", "Liberation Serif"),
    "times": ("Tinos", "Liberation Serif", "Times New Roman"),
    "couriernew": ("Cousine", "Liberation Mono"),
    "courier": ("Cousine", "Liberation Mono", "Courier New"),
    "calibri": ("Carlito",),
    "cambria": ("Caladea",),
    "georgia": ("Gelasio",),
}

# Base-14 et leurs alias de générateurs : le nom porte la famille.
_ALIAS = {"helv": "Helvetica", "tiro": "Times", "cour": "Courier",
          "timesroman": "Times"}

_POIDS = (("extralight", 200), ("ultralight", 200), ("semibold", 600),
          ("demibold", 600), ("extrabold", 800), ("ultrabold", 800),
          ("hairline", 100), ("thin", 100), ("light", 300), ("medium", 500),
          ("black", 900), ("heavy", 900), ("demi", 600), ("bold", 700),
          ("book", 400), ("regular", 400), ("normal", 400), ("roman", 400))
_MOTS_STYLE = {m for m, _ in _POIDS} | {"italic", "oblique", "it"}


def cle(famille):
    """Clé de comparaison d'un nom de famille : minuscules, lettres et chiffres."""
    return re.sub(r"[^0-9a-z]", "", (famille or "").lower())


def analyser_nom(font_raw):
    """« ABCDEF+TimesNewRomanPS-BoldItalicMT » → ('Times New Roman', 700, True).

    Le poids vaut None quand le nom n'en dit rien (l'appelant décide alors avec
    le drapeau gras du span)."""
    nom = (font_raw or "").split("+")[-1].strip()
    m = re.match(r"^(.*?)[-,](.*)$", nom)
    base, style = (m.group(1), m.group(2)) if m else (nom, "")
    base = re.sub(r"(PSMT|PS|MT)$", "", base)
    style = re.sub(r"(PSMT|PS|MT)$", "", style)
    mots = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
                  r"|(?<=[a-zA-Z])(?=[0-9])|[_\s]+", " ", base).split()
    # Style collé au nom sans séparateur (« Open Sans Bold », « ArialBold »).
    # « Roman » fait partie du nom de « Times New Roman ».
    while (len(mots) > 1 and mots[-1].lower() in _MOTS_STYLE
           and cle(" ".join(mots)) not in CLONES):
        style = mots.pop() + style
    famille = _ALIAS.get(cle(" ".join(mots)), " ".join(mots))
    s = style.lower()
    poids = next((p for mot, p in _POIDS if mot in s), None)
    italique = bool(re.search(r"italic|oblique|ital|it$", s))
    return famille, poids, italique


# ── Index des polices sur disque ─────────────────────────────────────────────

def _decrire(path):
    """Fiche d'une police sur disque, ou None si elle n'écrit pas le latin."""
    from fontTools.ttLib import TTFont
    try:
        with open(path, "rb") as fh:        # fichier relâché aussitôt (Windows)
            ft = TTFont(io.BytesIO(fh.read()), lazy=True)
        cmap = ft.getBestCmap() or {}
        if not all(ord(c) in cmap for c in "aA"):
            return None
        noms = ft["name"]
        famille = str(noms.getDebugName(16) or "")
        if not famille:
            # Instance statique sans nom typographique : « Open Sans Medium ».
            mots = str(noms.getDebugName(1) or "").split()
            while len(mots) > 1 and cle(mots[-1]) in _MOTS_STYLE:
                mots.pop()
            famille = " ".join(mots)
        upm = ft["head"].unitsPerEm or 1000
        hmtx = ft["hmtx"]
        adv = {c: round(hmtx[cmap[ord(c)]][0] / upm, 4)
               for c in _SONDE if ord(c) in cmap}
        os2 = ft["OS/2"] if "OS/2" in ft else None
        post = ft["post"] if "post" in ft else None
        italique = bool((os2 and os2.fsSelection & 1)
                        or ft["head"].macStyle & 2
                        or (post and post.italicAngle))
        serif = None
        if os2 is not None and getattr(os2, "panose", None) is not None \
                and os2.panose.bFamilyType == 2:
            st = os2.panose.bSerifStyle
            serif = True if 2 <= st <= 10 else (False if 11 <= st <= 13 else None)
        if serif is None:
            n = famille.lower()
            serif = False if "sans" in n else (True if "serif" in n else None)
        xh = getattr(os2, "sxHeight", 0) if os2 is not None else 0
        return {
            "famille": famille, "cle": cle(famille),
            "poids": int(os2.usWeightClass) if os2 is not None else 400,
            "italique": italique,
            "fixe": bool(post and post.isFixedPitch),
            "serif": serif,
            "xh": round(xh / upm, 4) if xh else None,
            "francais": all(ord(c) in cmap for c in _FRANCAIS),
            "adv": adv,
        }
    except Exception:
        return None


def _rend_de_l_encre(path, texte="aAé"):
    """Chaque glyphe du texte dessine-t-il quelque chose ? Un fichier tronqué
    ou corrompu se charge parfois sans erreur et ne peint rien."""
    try:
        with open(path, "rb") as fh:
            font = fitz.Font(fontbuffer=fh.read())
        for ch in texte:
            doc = fitz.open()
            pg = doc.new_page(width=48, height=48)
            tw = fitz.TextWriter(pg.rect, color=(0, 0, 0))
            tw.append(fitz.Point(4, 34), ch, font=font, fontsize=28)
            tw.write_text(pg)
            encre = min(pg.get_pixmap(alpha=False).samples) < 250
            doc.close()
            if not encre:
                return False
        return True
    except Exception:
        return False


# ── Téléchargement ───────────────────────────────────────────────────────────

_HOTES = {"fonts.googleapis.com", "fonts.gstatic.com"}
_TAILLE_MAX = 20 * 1024 * 1024
_BLOC = 64 * 1024
# Attente maximale d'UNE opération réseau (connexion, lecture d'un bloc).
_DELAI_REQUETE = 8.0
# Durée maximale d'un téléchargement ENTIER (feuille de style + fichier) : un
# serveur qui goutte-à-goutte ne retient pas une traduction au-delà.
_ECHEANCE_TELECHARGEMENT = 30.0
# Après un échec réseau : pas de nouvel essai, pour aucune famille, pendant ce
# délai (en secondes) ; l'échec d'une variante précise est oublié après lui.
_DELAI_RESEAU_COUPE = 600
# Une variante que le catalogue dit ne pas avoir n'est pas redemandée avant.
_DELAI_ABSENCE = 30 * 86400


class _RedirectionSure(urllib.request.HTTPRedirectHandler):
    """Refuse toute redirection hors des hôtes autorisés."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urllib.parse.urlparse(newurl)
        if u.scheme != "https" or u.hostname not in _HOTES:
            raise urllib.error.URLError(f"redirection refusée : {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OUVREUR = urllib.request.build_opener(_RedirectionSure)


def _get(url, timeout, echeance=None):
    """Octets de `url` (hôtes autorisés seulement). `echeance` : date
    (`time.monotonic`) au-delà de laquelle on abandonne, même si le serveur
    répond encore."""
    if urllib.parse.urlparse(url).hostname not in _HOTES:
        raise ValueError(f"hôte non autorisé : {url}")
    # Sans en-tête de navigateur, l'API sert le TrueType complet (et non des
    # tranches WOFF2 par plage Unicode).
    req = urllib.request.Request(url, headers={"User-Agent": "precis-fonts"})
    blocs, total = [], 0
    with _OUVREUR.open(req, timeout=timeout) as r:
        while True:
            if echeance is not None and time.monotonic() > echeance:
                raise TimeoutError(f"échéance dépassée : {url}")
            bloc = r.read(_BLOC)
            if not bloc:
                break
            total += len(bloc)
            if total > _TAILLE_MAX:
                raise ValueError("fichier trop gros")
            blocs.append(bloc)
    return b"".join(blocs)


class Absente(Exception):
    """Le catalogue répond que la famille ou la variante n'existe pas."""


def google_fonts(famille, poids, italique, timeout=_DELAI_REQUETE):
    """Octets TTF COMPLETS de la variante demandée, depuis Google Fonts.
    Lève `Absente` si la variante n'existe pas ; toute autre exception est un
    échec RÉSEAU (à retenter plus tard, jamais mémorisé comme absence)."""
    echeance = time.monotonic() + _ECHEANCE_TELECHARGEMENT
    spec = f"{famille}:ital,wght@{1 if italique else 0},{poids}"
    url = "https://fonts.googleapis.com/css2?" + urllib.parse.urlencode(
        {"family": spec})
    try:
        css = _get(url, timeout, echeance).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 400:
            raise Absente(spec) from e
        raise
    m = re.search(r"url\((https://fonts\.gstatic\.com/[^)]+\.ttf)\)", css)
    if not m:
        raise Absente(spec)
    return _get(m.group(1), timeout, echeance)


# ── Résolveur ────────────────────────────────────────────────────────────────

def _repertoires_systeme():
    if os.name == "nt":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        return [os.path.join(windir, "Fonts"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""),
                             "Microsoft", "Windows", "Fonts")]
    return ["/usr/share/fonts", "/usr/local/share/fonts",
            os.path.expanduser("~/.fonts")]


class Resolveur:
    """Trouve, télécharge ou approche une police. Sûr entre threads."""

    def __init__(self, repertoires, cache_dir, telecharger=google_fonts,
                 delai_absence=_DELAI_ABSENCE):
        self.repertoires = [d for d in repertoires if d]
        self.cache_dir = cache_dir
        self.telecharger = telecharger
        self.delai_absence = delai_absence
        # Le verrou global ne garde QUE l'état (index, absences, échecs) : ni
        # réseau, ni lecture de police, ni rendu d'encre ne le tiennent.
        self._verrou = threading.RLock()
        self._verrou_index = threading.Lock()   # un seul fil construit l'index
        self._en_vol = {}                 # (clé, poids, ital) -> verrou propre
        self._generation = 0              # change quand un fichier entre au cache
        self._index = None                # chemin -> fiche
        self._echecs_reseau = {}          # (clé, poids, ital) -> date de l'échec
        self._reseau_coupe = 0.0          # pas de nouvel essai avant cette date
        self._absences = None             # persistées : "clé|poids|ital" -> date

    # Index ------------------------------------------------------------------
    def _fichier_index(self):
        return os.path.join(self.cache_dir, "index.json") if self.cache_dir else None

    def index(self):
        with self._verrou:
            if self._index is not None:
                return self._index
        with self._verrou_index:
            with self._verrou:
                if self._index is not None:
                    return self._index
                generation = self._generation
            index = self._construire_index()            # hors du verrou global
            with self._verrou:
                # Un fichier entré au cache pendant la lecture n'y figure pas :
                # cet index-ci sert à l'appelant mais n'est pas retenu.
                if generation == self._generation:
                    self._index = index
            return index

    def _construire_index(self):
        ancien = {}
        fi = self._fichier_index()
        if fi and os.path.exists(fi):
            try:
                with open(fi, encoding="utf-8") as f:
                    ancien = json.load(f)
            except Exception:
                ancien = {}
        index, change = {}, False
        for d in self.repertoires + ([self.cache_dir] if self.cache_dir else []):
            if not os.path.isdir(d):
                continue
            for racine, sous, fichiers in os.walk(d):
                # Le catalogue pré-chargé est indexé pour lui-même (ou ignoré
                # sur demande) : le parcours de `fonts/` ne l'y ajoute pas.
                sous[:] = [x for x in sous if os.path.normcase(os.path.abspath(
                    os.path.join(racine, x))) != _CATALOGUE_NORM]
                for nom in fichiers:
                    if not nom.lower().endswith((".ttf", ".otf")):
                        continue
                    path = os.path.join(racine, nom)
                    try:
                        st = os.stat(path)
                    except OSError:
                        continue
                    sig = [st.st_mtime_ns, st.st_size]
                    vieux = ancien.get(path)
                    if vieux and vieux.get("sig") == sig:
                        if vieux.get("fiche"):
                            index[path] = vieux["fiche"]
                        continue
                    fiche = _decrire(path)
                    ancien[path] = {"sig": sig, "fiche": fiche}
                    change = True
                    if fiche:
                        index[path] = fiche
        if change and fi:
            self._ecrire_json(fi, {p: v for p, v in ancien.items()
                                   if os.path.exists(p)})
        return index

    def _ecrire_json(self, path, obj):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(obj, f)
            os.replace(tmp, path)
        except Exception as e:
            log.warning("polices : écriture de %s impossible (%s)", path, e)

    # Absences mémorisées ------------------------------------------------------
    def _absences_connues(self):
        if self._absences is None:
            self._absences = {}
            if self.cache_dir:
                p = os.path.join(self.cache_dir, "absences.json")
                try:
                    with open(p, encoding="utf-8") as f:
                        self._absences = json.load(f)
                except Exception:
                    pass
        return self._absences

    def _absente(self, famille, poids, italique):
        with self._verrou:
            t = self._absences_connues().get(
                f"{cle(famille)}|{poids}|{int(italique)}")
        return t is not None and time.time() - t < self.delai_absence

    def _noter_absente(self, famille, poids, italique):
        with self._verrou:
            self._absences_connues()[
                f"{cle(famille)}|{poids}|{int(italique)}"] = time.time()
            if self.cache_dir:
                self._ecrire_json(os.path.join(self.cache_dir, "absences.json"),
                                  self._absences)

    # Couches ----------------------------------------------------------------
    def _variantes(self, famille):
        k = cle(famille)
        return [(p, f) for p, f in self.index().items()
                if f["cle"] == k and f["francais"]]

    @staticmethod
    def _plus_proche_variante(variantes, poids, italique):
        """Variante d'une famille : la pente d'abord (un italique rendu droit
        perd la mise en forme), puis le poids le plus proche."""
        if not variantes:
            return None
        return min(variantes, key=lambda pf: (pf[1]["italique"] != italique,
                                              abs(pf[1]["poids"] - poids)))[0]

    def _exacte(self, famille, poids, italique):
        for p, f in self._variantes(famille):
            if f["italique"] == italique and f["poids"] == poids:
                return p
        return None

    def possede(self, famille, poids, italique):
        """La variante exacte est-elle déjà sur disque ?"""
        return self._exacte(famille, poids, italique) is not None

    def reseau_disponible(self):
        """Faux pendant le délai qui suit un échec réseau."""
        with self._verrou:
            return time.time() >= self._reseau_coupe

    def _refusee(self, cle_r, famille, poids, italique):
        """Pas de téléchargement : échec réseau récent, réseau coupé, ou
        variante que le catalogue a dit absente."""
        with self._verrou:
            maintenant = time.time()
            t = self._echecs_reseau.get(cle_r)
            if t is not None and maintenant - t >= _DELAI_RESEAU_COUPE:
                del self._echecs_reseau[cle_r]          # oublié : on retente
                t = None
            if t is not None or maintenant < self._reseau_coupe:
                return True
        return self._absente(famille, poids, italique)

    def telecharger_variante(self, famille, poids, italique):
        """Télécharge et met en cache la variante ; chemin ou None.

        Le réseau et la validation se font HORS du verrou global : un verrou
        par variante en vol suffit à ce que deux demandes de la même variante
        ne téléchargent qu'une fois, sans bloquer les autres familles."""
        if not self.telecharger or not self.cache_dir:
            return None
        cle_r = (cle(famille), poids, italique)
        with self._verrou:
            verrou_cle = self._en_vol.setdefault(cle_r, threading.Lock())
        with verrou_cle:
            # Une demande identique a pu aboutir (ou échouer) pendant l'attente.
            if self._refusee(cle_r, famille, poids, italique):
                return None
            deja = self._exacte(famille, poids, italique)
            if deja:
                return deja
            try:
                data = self.telecharger(famille, poids, italique)
            except Absente:
                self._noter_absente(famille, poids, italique)
                return None
            except Exception as e:
                log.info("polices : téléchargement de %s %s%s impossible (%s)",
                         famille, poids, " italique" if italique else "", e)
                with self._verrou:
                    maintenant = time.time()
                    self._echecs_reseau[cle_r] = maintenant
                    # Réseau coupé : chaque famille suivante attendrait le
                    # délai d'expiration. On n'essaie plus pendant un moment.
                    self._reseau_coupe = maintenant + _DELAI_RESEAU_COUPE
                return None
            try:
                path = self._enregistrer(famille, poids, italique, data)
            except Exception as e:              # disque plein, droits…
                log.warning("polices : mise en cache de %s impossible (%s)",
                            famille, e)
                return None
            if path is None:
                self._noter_absente(famille, poids, italique)
            return path

    def _enregistrer(self, famille, poids, italique, data):
        """Valide (famille, poids, pente, encre) puis range dans le cache."""
        os.makedirs(self.cache_dir, exist_ok=True)
        nom = f"{re.sub(r'[^0-9A-Za-z]', '', famille)}-{poids}{'i' if italique else ''}.ttf"
        path = os.path.join(self.cache_dir, nom)
        fd, tmp = tempfile.mkstemp(dir=self.cache_dir, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            fiche = _decrire(tmp)
            valide = (fiche is not None and fiche["cle"] == cle(famille)
                      and fiche["italique"] == italique
                      and abs(fiche["poids"] - poids) <= 100
                      and _rend_de_l_encre(tmp))
            if not valide:
                log.warning("polices : fichier reçu pour %s %s rejeté",
                            famille, poids)
                return None
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):             # rejet ou exception avant le rangement
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        with self._verrou:
            self._index = None                # réindexe : le fichier y entre
            self._generation += 1
        return path

    def _identique(self, famille, poids, italique):
        """Même famille : variante exacte locale, sinon téléchargée, sinon une
        variante locale de même pente et de graisse voisine. Un gras rendu
        maigre, un italique rendu droit perdraient la mise en forme : mieux
        vaut alors une autre famille à la bonne graisse."""
        p = (self._exacte(famille, poids, italique)
             or self.telecharger_variante(famille, poids, italique))
        if p:
            return p
        voisines = [(q, f) for q, f in self._variantes(famille)
                    if f["italique"] == italique and abs(f["poids"] - poids) <= 200]
        return self._plus_proche_variante(voisines, poids, italique)

    def plus_proche(self, poids, italique, profil=None, nom=""):
        """Police locale la plus proche du profil mesuré dans le document."""
        cibles = [(p, f) for p, f in self.index().items() if f["francais"]]
        if not cibles:
            return None
        profil = profil or {}
        adv = {c: w for c, w in (profil.get("adv") or {}).items() if w > 0}
        fixe = _chasse_fixe(adv)
        # Le drapeau « serif » de MuPDF n'est pas une preuve : une police
        # insérée sans descripteur le reçoit (Open Sans y passe pour serif).
        # Seul le nom le dit ; les chasses mesurées font le reste.
        n = (nom or "").lower()
        serif = False if "sans" in n else (True if "serif" in n else None)

        def score(f):
            s = 0.0
            if fixe is not None and f["fixe"] != fixe:
                s += 10.0                       # chasse : jamais l'une pour l'autre
            communs = [c for c in adv if c in f["adv"] and f["adv"][c] > 0]
            if communs:
                s += sum(abs(math.log(adv[c] / f["adv"][c])) for c in communs) \
                    / len(communs)
            else:
                s += 0.3                        # rien de mesuré : neutre
            s += abs(f["poids"] - poids) / 1000.0
            s += 0.2 if f["italique"] != italique else 0.0
            if serif is not None and f["serif"] is not None and f["serif"] != serif:
                s += 0.15
            return s

        return min(cibles, key=lambda pf: (score(pf[1]), pf[0]))[0]

    def resoudre(self, font_raw, bold=False, italic=False, profil=None):
        """Chemin de la meilleure police de remplacement, et la couche qui l'a
        fournie ('identique', 'clone', 'proche'), ou (None, None)."""
        famille, poids, ital = analyser_nom(font_raw)
        poids = poids or (700 if bold else 400)
        ital = ital or bool(italic)
        try:
            # Un nom sans caractère ASCII n'a pas de clé : aucune famille à
            # chercher ni à télécharger, on passe aux chasses mesurées.
            if famille and cle(famille):
                p = self._identique(famille, poids, ital)
                if p:
                    return p, "identique"
                for clone in CLONES.get(cle(famille), ()):
                    p = self._identique(clone, poids, ital)
                    if p:
                        return p, "clone"
            p = self.plus_proche(poids, ital, profil, font_raw)
            return (p, "proche") if p else (None, None)
        except Exception as e:                  # jamais fatal : repli de l'appelant
            log.warning("polices : résolution de %r impossible (%s)", font_raw, e)
            return None, None


def _chasse_fixe(adv):
    """Chasse fixe (True), proportionnelle (False), ou inconnue (None) quand
    trop peu de glyphes distincts ont été mesurés pour le dire."""
    vals = [w for c, w in adv.items() if not c.isspace()]
    if len(vals) < 4:
        return None
    return max(vals) / min(vals) <= 1.02


_RESOLVEUR = None
_VERROU = threading.Lock()


def resolveur():
    """Résolveur du processus, réglé par l'environnement :
    PRECIS_FONTS_CACHE (dossier du cache), PRECIS_FONT_DOWNLOAD=0 (hors ligne),
    PRECIS_FONT_SYSTEM=0 (ignorer les polices du système),
    PRECIS_FONT_CATALOGUE=0 (ignorer le catalogue pré-chargé)."""
    global _RESOLVEUR
    with _VERROU:
        if _RESOLVEUR is None:
            reps = [FONTS_DIR]
            if os.environ.get("PRECIS_FONT_CATALOGUE", "1") != "0":
                reps.append(CATALOGUE_DIR)
            if os.environ.get("PRECIS_FONT_SYSTEM", "1") != "0":
                reps += _repertoires_systeme()
            dl = google_fonts if os.environ.get("PRECIS_FONT_DOWNLOAD", "1") != "0" \
                else None
            _RESOLVEUR = Resolveur(reps, os.environ.get("PRECIS_FONTS_CACHE",
                                                        CACHE_DIR), dl)
        return _RESOLVEUR


def configurer(r):
    """Remplace le résolveur du processus (tests, script de pré-chargement)."""
    global _RESOLVEUR
    with _VERROU:
        _RESOLVEUR = r
