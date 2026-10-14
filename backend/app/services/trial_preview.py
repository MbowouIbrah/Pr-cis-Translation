"""
Aperçu d'ESSAI — ce qu'un plan gratuit a le droit de recevoir.

POURQUOI CE MODULE EXISTE
-------------------------
Le verrou d'essai n'a longtemps vécu que dans le navigateur : le serveur
envoyait le PDF traduit EN CLAIR et le client le repeignait
(`filter: brightness(15%)`). Mesuré : un compte `free` appelant /partial
recevait « 200, 454 882 octets, application/pdf — 1 page, 782 mots
EXTRACTIBLES, 31 dessins ». Autrement dit, pas « il peut copier le texte » :
il repartait avec LE FICHIER, identique à celui d'un client payant, en trois
clics dans l'onglet Réseau. Repeindre une image ne protège rien quand
l'original est déjà dans la mémoire de l'onglet.

CE QUE ÇA PROTÈGE, ET CE QUE ÇA NE PROTÈGE PAS
----------------------------------------------
Le pixel est copiable par nature : une capture d'écran restera toujours
possible, aucune technologie web n'y peut rien. On ne protège donc PAS le fait
de REGARDER — on protège le fait de REPARTIR AVEC UN DOCUMENT EXPLOITABLE :
texte sélectionnable, mise en page réutilisable, fichier propre.

Après rastérisation, le document n'est plus que des pixels : aucun mot du texte
traduit n'est extractible (mesuré : 782 -> 0). AUCUNE couche texte ne subsiste,
pas même le filigrane : il est CUIT dans les pixels (double rastérisation), donc
on ne le retire pas en supprimant des objets texte. Plus de polices, plus de
vecteurs. Pour en tirer un document, il faudrait l'OCR —
c'est-à-dire refaire soi-même, en moins bien, le travail qu'on vend. Le vol
devient plus cher que l'abonnement : c'est le seul niveau de protection honnête
pour du contenu affiché dans un navigateur.

Le projecteur au survol continue de fonctionner : il a besoin de pixels nets en
local, et il en a. C'est ce qui a fait écarter l'assombrissement côté serveur,
qui l'aurait tué.
"""
from __future__ import annotations

import functools
import html

import fitz

from app.config import BACKEND_DIR, BRAND_NAME, PUBLIC_SITE

# Résolution de l'aperçu d'essai. Assez pour juger la mise en page et lire au
# projecteur ; pas de quoi produire une réimpression propre. Ce n'est pas un
# réglage de qualité, c'est le curseur du teaser.
TRIAL_DPI = 110

# Filigrane = le DÉCOR de l'application, figé : les quatre bandes de mots
# multilingues qui défilent derrière les sections (`hero/LanguageMarquee`),
# ici immobiles et répétées sur toute la page. Garder ces listes alignées sur
# `BANDES` du composant. La marque et le site sont glissés dans chaque bande : le décor
# rend toute fuite IDENTIFIABLE, et cuit dans les pixels (voir plus bas) il
# n'est plus une couche de texte qu'on détache.
_BANDES: tuple[tuple[str, ...], ...] = (
    ("Translation", "Traduction", "Traducción", "Übersetzung", "Traduzione",
     "Overzetting", "翻訳", "번역", "翻译", "ترجمة", "Перевод"),
    ("Documents", "Actes", "Certificats", "Contrats", "Diplômes",
     "書類", "문서", "文档", "عقود", "Справки"),
    ("Precision", "Précision", "Precisión", "Präzision", "Precisione",
     "Precisie", "精度", "정밀도", "精确", "دقة", "Точность"),
    ("AI & Human", "IA & Humain", "IA y Humano", "KI & Mensch", "IA & Umano",
     "AI & Mens", "AI & 人間", "AI & 인간", "AI & 人类", "ذكاء بشري واصطناعي"),
)
_MARK_ENSEIGNE = f"{BRAND_NAME} · {PUBLIC_SITE}"
# Serif italique, au plus près de la police d'affichage du site (Cormorant
# Garamond, non embarquée). CJK, arabe et cyrillique passent par le repli de
# polices de MuPDF (`insert_htmlbox`), qui met aussi l'arabe en forme.
_MARK_FONT = BACKEND_DIR / "fonts" / "ptserif-Italic.ttf"
_MARK_SIZE = 26           # corps des mots, en points
_MARK_INTERLIGNE = 1.9    # pas vertical entre bandes, en multiples du corps
_MARK_COLOR = "#0b1f4d"   # navy du décor de l'app
_MARK_OPACITY = 0.11


def rasterize_for_trial(pdf_bytes: bytes, pages=None,
                        dpi: int = TRIAL_DPI) -> bytes:
    """Rend en IMAGES filigranées les pages TRADUITES de `pdf_bytes`.

    `pages` : numéros 1-basés des pages effectivement traduites. None = toutes.

    On ne rastérise QUE ce qu'on a produit. Les autres pages du PDF partiel sont
    des copies conformes de l'original, que l'utilisateur possède déjà : les
    protéger n'a aucun sens, et coûte cher. Mesuré sur un document de 84 pages :
    5 544 ms et 9,6 Mo à tout rastériser, contre ~190 ms pour la seule page
    traduite — or le client recharge le partiel APRÈS CHAQUE PAGE. Un compte
    `free` n'a droit qu'à 1 page traduite : le cas courant est donc une page.

    Les pages gardent leurs dimensions d'origine : le viewer, la pagination et
    le zoom du client n'ont rien à savoir de tout ceci.

    En cas d'échec, on RELÈVE : renvoyer le PDF clair « pour ne pas casser
    l'aperçu » transformerait un bug en fuite silencieuse. L'appelant décide.
    """
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        cibles = None if pages is None else {int(p) for p in pages}
        for i in range(len(src)):
            if cibles is not None and (i + 1) not in cibles:
                continue                      # copie de l'original : rien à cacher
            page = src[i]
            rect = page.rect
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            # On VIDE la page (texte, polices, vecteurs disparaissent) avant d'y
            # reposer son image : sans ce nettoyage, la couche texte survivrait
            # SOUS le pixmap — invisible à l'œil, intacte au copier-coller.
            page.clean_contents()
            src.delete_page(i)
            page = src.new_page(pno=i, width=rect.width, height=rect.height)
            page.insert_image(page.rect, pixmap=pix)
            _stamp(page)

            # CUISSON DU FILIGRANE. `_stamp` pose le texte en COUCHE, détachable
            # d'un coup en supprimant les objets texte. On re-rastérise donc la
            # page tamponnée en une image, qu'on repose seule : le filigrane vit
            # désormais dans les PIXELS. L'enlever demande de retoucher l'image
            # (bien plus cher qu'un simple « supprimer la couche texte »).
            baked = page.get_pixmap(dpi=dpi, alpha=False)
            src.delete_page(i)
            page = src.new_page(pno=i, width=rect.width, height=rect.height)
            page.insert_image(page.rect, pixmap=baked)
        return src.tobytes(garbage=3, deflate=True)
    finally:
        src.close()


def _stamp(page: fitz.Page) -> None:
    """Pose le décor de bandes multilingues sur toute la page.

    Un filigrane manqué ne doit pas perdre la page : l'échec est avalé.
    """
    r = page.rect
    try:
        page.insert_image(r, stream=_calque(round(r.width), round(r.height)))
    except Exception:
        pass


def prechauffer() -> None:
    """Construit d'avance le décor des formats courants (A4, Letter).

    Le premier calque d'un processus coûte ~5 s (chargement des polices de
    repli CJK/arabe) : sans ça, c'est le premier aperçu d'essai après chaque
    déploiement qui les paierait.
    """
    for largeur, hauteur in ((595, 842), (612, 792)):
        try:
            _calque(largeur, hauteur)
        except Exception:
            pass


@functools.lru_cache(maxsize=16)
def _calque(largeur: int, hauteur: int) -> bytes:
    """Le décor pour UN format de page, en PNG transparent, construit une fois.

    MESURÉ (A4) : composé en PDF, le calque embarque les polices de repli
    entières — 35 Mo, et ~0,5 s à re-rastériser à CHAQUE page. Figé en image à
    la résolution d'essai : 125 Ko, ~50 ms. Il finit de toute façon cuit dans
    les pixels de la page. Mis en cache en OCTETS : un Document fitz partagé
    entre threads n'est pas sûr.
    """
    doc = fitz.open()
    page = doc.new_page(width=largeur, height=hauteur)
    archive = fitz.Archive()
    archive.add(fitz.Font(fontfile=str(_MARK_FONT)).buffer, "deco.ttf")
    css = ("@font-face {font-family: deco; src: url(deco.ttf);}"
           f"p {{font-family: deco, serif; font-style: italic; margin: 0;"
           f" font-size: {_MARK_SIZE}px; color: {_MARK_COLOR};"
           " white-space: nowrap;}")
    pas = _MARK_SIZE * _MARK_INTERLIGNE
    y, i = 0.0, 0
    while y < hauteur:
        bande = _BANDES[i % len(_BANDES)]
        mots = bande[:3] + (_MARK_ENSEIGNE,) + bande[3:]
        ligne = html.escape("  •  ".join(mots * 2))
        # Décalage propre à chaque bande : figer le défilement, c'est figer
        # des bandes qui ne sont pas alignées entre elles.
        dx = -float((i * 137) % 400)
        page.insert_htmlbox(fitz.Rect(dx, y, dx + 6000, y + pas),
                            f"<p>{ligne}</p>", css=css, archive=archive,
                            opacity=_MARK_OPACITY)
        y += pas
        i += 1
    try:
        return page.get_pixmap(dpi=TRIAL_DPI, alpha=True).tobytes("png")
    finally:
        doc.close()
