"""
Routes documents — CRUD par utilisateur.
"""
from __future__ import annotations
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import Response
from fastapi.concurrency import run_in_threadpool
# FileResponse retiré : ses réponses Range/206 faisaient échouer
# les fetch côté frontend (ERR_FAILED 206). On utilise Response partout.
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TRANSLATIONS_DIR, logger
from app.services import render_cache
from app.services.office import convert_to_pdf_bytes
from app.services.translation_runner import render_translation_bytes
from app.core.database import get_db
from app.models import User, Document, is_paid_plan
from app.core.security import require_auth
from app.services.trial_preview import rasterize_for_trial

router = APIRouter(prefix="/api/documents", tags=["Documents"])

# Le magasin est celui d'`app.py` : `backend/translations`. Cette constante
# valait `os.path.dirname(__file__)` — c'est-à-dire `backend/routes/` — et
# pointait donc sur `backend/routes/translations`, un dossier que la suppression
# créait à chaque appel et qui n'a jamais contenu un seul fichier (mesuré : 0
# ici contre 15 dans le vrai magasin). Le garde-fou de suppression « protégeait »
# les fichiers d'autrui en ne trouvant jamais rien à effacer : aucun octet n'a
# jamais été libéré, et `storage_used` dérivait du disque à chaque suppression.
#
# Le chemin n'est PLUS recalculé ici. Ce module avait sa propre expression,
# dérivée de son emplacement, et un simple déplacement de fichier suffisait à
# la faire diverger — c'est arrivé une deuxième fois lors de la réorganisation
# en paquets, et le test l'a rattrapé. Une seule définition, dans la config :
# la divergence devient impossible par construction, ce qui vaut mieux qu'un
# test qui la constate.
STORAGE_BASE = TRANSLATIONS_DIR


def _inside_store(path: str) -> bool:
    """Le chemin est-il bien DANS le magasin ? Dernier rempart avant `os.remove`.

    `commonpath` lève sur deux volumes différents (Windows) : on refuse alors,
    parce qu'un chemin qu'on ne sait pas situer n'est pas un chemin qu'on efface.
    """
    try:
        real = os.path.realpath(path)
        store = os.path.realpath(STORAGE_BASE)
        return os.path.commonpath([real, store]) == store
    except (ValueError, OSError):
        return False


def _job_root(path: str) -> str | None:
    """Racine du job dans le magasin : `translations/{nom}_{hash}`.

    C'est l'unité de PROPRIÉTÉ partagée : tout ce qu'un job produit (original,
    extractions, rendus par version de moteur) vit sous ce dossier. None si le
    chemin n'est pas strictement SOUS le magasin.
    """
    real = os.path.realpath(path)
    store = os.path.realpath(STORAGE_BASE)
    try:
        rel = os.path.relpath(real, store)
    except ValueError:                    # autre volume (Windows)
        return None
    premier = rel.split(os.sep)[0]
    if premier in ("..", ".", ""):
        return None
    return os.path.join(store, premier)


async def _purge_if_orphan(db: AsyncSession, path: str) -> bool:
    """Efface `path` SI plus aucun Document, de quelque compte que ce soit, ne
    référence LE JOB auquel il appartient.

    Les fichiers vivent dans un magasin PARTAGÉ adressé par le hash du contenu :
    deux comptes ayant déposé le même fichier pointent sur les mêmes octets.
    Effacer sur la seule foi de « mon » Document supprimerait la traduction
    d'autrui. On compte donc les références restantes — la ligne courante est
    déjà supprimée ET commitée, elle ne se compte pas elle-même.

    Le comptage se fait sur la RACINE du job, pas sur le chemin exact : le
    `translated_path` d'un Document reste NULL pendant toute la traduction, et
    un comptage au fichier près déclarait donc orphelin un rendu qu'un autre
    compte était en train de produire. Tant qu'UNE référence pointe quelque
    part sous la racine (ne serait-ce que l'original), rien n'y est effacé —
    conservateur, mais de la rétention de disque plutôt que la perte du
    fichier d'autrui.
    """
    if not _inside_store(path):
        return False
    racine = _job_root(path)
    if racine is None:
        return False
    prefixe = racine + os.sep
    refs = await db.execute(
        select(func.count()).select_from(Document).where(
            or_(
                Document.original_path == racine,
                Document.translated_path == racine,
                Document.original_path.startswith(prefixe, autoescape=True),
                Document.translated_path.startswith(prefixe, autoescape=True),
            ),
        )
    )
    if (refs.scalar() or 0) > 0:
        return False                      # quelqu'un d'autre tient encore au job
    real = os.path.realpath(path)
    if not os.path.isfile(real):
        return False
    try:
        os.remove(real)
    except OSError:
        return False
    _prune_empty_dirs(os.path.dirname(real))
    return True


def _prune_empty_dirs(start: str) -> None:
    """Remonte en effaçant les dossiers VIDES, sans jamais sortir du magasin.

    `os.rmdir` refuse un dossier non vide : c'est notre filet. Un dossier qui
    contient encore un partiel de job survit — tant mieux, on n'a rien à y faire.
    """
    store = os.path.realpath(STORAGE_BASE)
    cur = os.path.realpath(start)
    while cur != store and _inside_store(cur):
        try:
            os.rmdir(cur)                 # lève si non vide : on s'arrête là
        except OSError:
            return
        cur = os.path.dirname(cur)


def _render_or_404(doc: Document, ext: str,
                   only_pages: set[int] | None = None) -> bytes:
    """Recalcule le document traduit depuis l'original + la traduction stockée.

    Le rendu est un SERVICE (`app.services.translation_runner`), pas
    l'application. Cette fonction faisait auparavant `import app` en paresseux
    pour contourner le cycle que creait `include_router` ; la fabrique
    `create_app()` ayant supprime le cycle, l'import est redevenu normal.
    """
    if (not doc.original_path or not os.path.isfile(doc.original_path)
            or not doc.translated_path or not os.path.isfile(doc.translated_path)):
        raise HTTPException(status_code=404,
                            detail="Le fichier n'est plus disponible. Réessayez ou rechargez la page.")
    try:
        return render_translation_bytes(
            doc.original_path, doc.translated_path, ext, doc.target_lang,
            only_pages=only_pages)
    except Exception:
        logger.exception("Rendu impossible (doc %s, %s)", getattr(doc, "id", "?"), ext)
        raise HTTPException(status_code=500,
                            detail="Le rendu du document a échoué. Réessayez dans quelques instants.")


def _may_read_clear(user: User, doc: Document) -> bool:
    """A-t-on le droit de voir CE document en clair (et de le télécharger) ?

    Deux titres différents, et il suffit d'en avoir un :
      • l'abonnement — un plan payant couvre tout ce qu'il produit ;
      • l'achat — le forfait Gratuit paie à la page, et ce qu'il a payé lui
        appartient au même titre.

    Le droit était lu sur le seul PLAN. C'était juste tant que le gratuit ne
    pouvait rien acheter ; ça devient faux dès qu'il le peut — il aurait payé
    des pages sans jamais pouvoir les récupérer.
    """
    return is_paid_plan(user.plan) or doc.paid


def _doc_response(doc: Document) -> dict:
    return {
        "id": doc.id,
        "paid": doc.paid,
        "original_name": doc.original_name,
        "source_lang": doc.source_lang,
        "target_lang": doc.target_lang,
        "size_bytes": doc.size_bytes,
        "status": doc.status,
        "page_count": doc.page_count,
        "pages_done": doc.pages_done,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }


# ── GET /documents ───────────────────────────────────────────────────────────

@router.get("")
async def list_documents(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Liste les documents de l'utilisateur connecté, du plus récent au plus ancien."""
    stmt = (
        select(Document)
        .where(Document.user_id == user.id)
        .order_by(Document.created_at.desc())
    )
    result = await db.execute(stmt)
    docs = result.scalars().all()
    return [_doc_response(d) for d in docs]


# ── GET /documents/{id} ─────────────────────────────────────────────────────

@router.get("/{doc_id}")
async def get_document(
    doc_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Métadonnées d'un document."""
    doc = await db.get(Document, doc_id)
    if doc is None or doc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document introuvable.")
    return _doc_response(doc)


# ── GET /documents/{id}/download ─────────────────────────────────────────────

@router.get("/{doc_id}/download")
async def download_document(
    doc_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Téléchargement du fichier traduit (ou original si pas encore traduit).

    RÉSERVÉ AUX PLANS PAYANTS quand le fichier est une TRADUCTION. Ce contrôle
    manquait : on avait verrouillé /api/translate/result mais pas cette route,
    et la bibliothèque contournait donc tout le dispositif — un compte d'essai
    téléchargeait sa traduction en clair depuis « Mes documents ». Verrouiller
    une porte et laisser l'autre ouverte ne verrouille rien.

    L'ORIGINAL, lui, reste téléchargeable par tous : c'est le fichier de
    l'utilisateur, il nous l'a confié, on ne va pas le lui rançonner.
    """
    doc = await db.get(Document, doc_id)
    if doc is None or doc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document introuvable.")

    # Liste d'AUTORISATION : un plan inconnu est traité comme non payant.
    if doc.translated_path and not _may_read_clear(user, doc):
        raise HTTPException(
            status_code=402,
            detail="Cette traduction n'est pas payée. Réglez ses pages pour "
                   "la télécharger, ou passez à Starter.",
        )

    ext = os.path.splitext(doc.original_name)[1].lstrip(".").lower()

    # Pas encore de traduction : on rend l'original (le fichier de l'utilisateur).
    if not doc.translated_path:
        if not doc.original_path or not os.path.isfile(doc.original_path):
            raise HTTPException(status_code=404, detail="Le fichier n'est plus disponible. Réessayez ou rechargez la page.")
        with open(doc.original_path, "rb") as f:
            data = f.read()
        return Response(content=data, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{doc.original_name}"'})

    # `translated_path` désigne la TRADUCTION stockée (JSON), pas un rendu.
    #
    # Le cache de rendu ne contient QUE des PDF (c'est un cache d'APERÇU, cf.
    # render_cache.py). Pour un PDF, ce rendu EST le fichier téléchargeable —
    # on le réutilise. Pour tout autre format (PPTX, DOCX), le cache tient une
    # CONVERSION PDF de l'aperçu, PAS le document natif : le servir ici rendait
    # un PDF déguisé en .pptx que PowerPoint refusait d'ouvrir. On ne touche
    # donc au cache que pour le PDF ; les autres formats se régénèrent toujours
    # dans leur format natif.
    #
    # Reconstruire 285 pages dans la requête prenait 280 s (téléchargement qui
    # expire) : on reconstruit dans un thread (la boucle d'événements ne se fige
    # pas) et, pour le PDF, on CONSERVE le résultat — la lenteur ne se paie
    # qu'une fois.
    def _build() -> bytes:
        if ext == "pdf":
            cached = render_cache.cache_valid(doc.translated_path)
            if cached:
                with open(cached, "rb") as f:
                    return f.read()
            data = _render_or_404(doc, ext)
            render_cache.store_render(data, doc.translated_path)
            return data
        # Format natif non-PDF : jamais le cache d'aperçu.
        return _render_or_404(doc, ext)

    data = await run_in_threadpool(_build)
    base = os.path.splitext(doc.original_name)[0]
    dl_name = f"{base}_TRADUIT.{ext}"
    media = "application/pdf" if ext == "pdf" else "application/octet-stream"
    return Response(content=data, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{dl_name}"'})


# ── GET /documents/{id}/original ─────────────────────────────────────────────

@router.get("/{doc_id}/original")
async def original_document(
    doc_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    # EN DERNIER, comme `page` sur /preview : un paramètre inséré avant `user`
    # décalerait les appels POSITIONNELS existants (dont les tests).
    as_: str | None = Query(None, alias="as"),
):
    """Le fichier SOURCE, tel qu'il a été déposé — ou converti en PDF.

    /download rend la TRADUCTION dès qu'elle existe : impossible d'y récupérer
    la source. L'aperçu depuis la bibliothèque n'avait donc rien à afficher dans
    son panneau de gauche et retombait sur le PDF de DÉMO — on montrait le
    journal d'exemple à côté du CV de l'utilisateur.

    `?as=pdf` — POUR REGARDER, PAS POUR REPARTIR AVEC.
    Le viewer affiche tout en PDF. Sans ce paramètre, le client téléchargeait le
    PPTX natif (13,4 Mo mesurés sur un deck de 26 slides) puis le RENVOYAIT au
    serveur en multipart sur /api/preview/pdf pour que celui-ci le convertisse —
    alors que le serveur détient déjà cette conversion en cache (2,5 Mo). Trois
    transferts et une conversion pour un fichier qu'il suffisait de servir : le
    panneau gauche restait blanc dès que ce détour échouait, sans rien dire.
    La conversion est mise en cache par CONTENU (cf. convert_to_pdf_bytes) : le
    coût n'est payé qu'une fois pour tous les utilisateurs du même fichier.

    SANS `as`, on rend toujours les octets d'origine : c'est ce dont dépend la
    relance d'une traduction, qui a besoin du fichier NATIF.

    Aucun contrôle de plan : c'est le fichier de l'utilisateur.
    """
    doc = await db.get(Document, doc_id)
    if doc is None or doc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document introuvable.")
    if not doc.original_path or not os.path.isfile(doc.original_path):
        raise HTTPException(status_code=404, detail="Le fichier n'est plus disponible. Réessayez ou rechargez la page.")
    # Response explicite plutôt que FileResponse : évite les Range/206 qui
    # peuvent faire échouer le fetch côté frontend (ERR_FAILED 206).
    with open(doc.original_path, "rb") as f:
        data = f.read()
    ext = os.path.splitext(doc.original_name)[1].lstrip(".").lower()

    if as_ == "pdf" and ext != "pdf":
        try:
            data = await run_in_threadpool(convert_to_pdf_bytes, data, ext)
        except Exception:
            raise HTTPException(
                status_code=500,
                detail="La conversion du document source pour l'aperçu a échoué.")
        ext = "pdf"

    media = "application/pdf" if ext == "pdf" else "application/octet-stream"
    return Response(content=data, media_type=media)


# ── GET /documents/{id}/preview ──────────────────────────────────────────────

@router.get("/{doc_id}/preview")
async def preview_document(
    doc_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    # EN DERNIER, volontairement. Inséré après `doc_id`, ce paramètre décalait
    # tous les appels POSITIONNELS existants : `preview_document(id, user, db)`
    # passait `user` pour `page`, et `db` restait un `Depends` non résolu. Un
    # nouvel argument optionnel se met à la fin — la suite de tests l'a montré
    # avant que ça n'atteigne quiconque.
    page: int | None = None,
):
    """Aperçu de la traduction — rastérisé et filigrané pour un plan d'essai.

    Sans cette route, « Aperçu » depuis la bibliothèque passait par /download et
    rendait le PDF en clair : la même fuite que sur /partial, par une autre
    porte. Un plan d'essai peut REGARDER sa traduction, jamais en repartir avec
    un document exploitable.
    """
    doc = await db.get(Document, doc_id)
    if doc is None or doc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document introuvable.")

    ext = os.path.splitext(doc.original_name)[1].lstrip(".").lower()

    # Pas encore de traduction : on montre l'original (le fichier de l'utilisateur).
    if not doc.translated_path:
        if not doc.original_path or not os.path.isfile(doc.original_path):
            raise HTTPException(status_code=404, detail="Le fichier n'est plus disponible. Réessayez ou rechargez la page.")
        with open(doc.original_path, "rb") as f:
            data = f.read()
        ext_orig = os.path.splitext(doc.original_name)[1].lstrip(".").lower()
        media_orig = "application/pdf" if ext_orig == "pdf" else "application/octet-stream"
        return Response(content=data, media_type=media_orig)

    # Un plan d'essai ne reçoit que du rastérisé, et seulement pour le PDF : un
    # format non-PDF ne peut pas être filigrané ici sans risque de le livrer en
    # clair, donc on le réserve aux plans payants.
    clear = _may_read_clear(user, doc)
    if not clear and ext != "pdf":
        raise HTTPException(
            status_code=402,
            detail="Aperçu de ce format réservé aux traductions payées.",
        )

    # Rendu + rastérisation sont du CPU pur, de l'ordre de la seconde par page.
    # Exécutés dans la coroutine, ils bloquaient la boucle d'événements : cliquer
    # « Aperçu » figeait TOUTE l'application jusqu'à la fin du rendu. On les
    # sort dans un thread, et on n'en fait qu'un aller-retour (les deux étapes
    # sont enchaînées côté thread plutôt qu'en deux bascules).

    # ── Rendre UNE page n'a de sens que pour le PDF ───────────────────────
    #
    # Le moteur v2 sait ne reconstruire qu'une page et recopier les autres de
    # l'original : demander `?page=N` y économise vraiment (280 s le document de
    # 285 pages, ~8 s la page seule).
    #
    # Un PPTX ou un DOCX, lui, se réinjecte D'UN BLOC puis passe en entier par
    # LibreOffice : `only_pages` n'y change rien, la page demandée coûte le
    # document entier. Or on jetait ce rendu au motif qu'une page avait été
    # demandée (`if clear and not fenetre`) — et le frontend envoie TOUJOURS
    # `?page=N`. Le cache n'était donc jamais écrit, `X-Render: full` jamais
    # émis : chaque changement de page repayait la réinjection complète plus une
    # conversion LibreOffice. On paie ce rendu une fois, on le garde, et le
    # client reçoit tout le document d'un coup.
    fenetre = {page} if (page and page > 0 and ext == "pdf") else None

    # ── Chemin RAPIDE : le rendu complet est en cache ─────────────────────
    #
    # Une fois servi, le client a TOUT le document traduit (converti en PDF) :
    # plus une seule requête pendant la navigation (l'en-tête `X-Render: full`
    # le lui dit). Réservé au CLAIR : la version d'essai est rastérisée à la
    # demande, et rastériser 285 pages par requête coûterait plus que ce qu'on
    # évite.
    if clear:
        cached = render_cache.cache_valid(doc.translated_path)
        if cached:
            data = await run_in_threadpool(lambda: open(cached, "rb").read())
            return Response(content=data, media_type="application/pdf",
                            headers={"Cache-Control": "no-store",
                                     "X-Render": "full"})

    def _build_complet() -> bytes:
        """Rendu INTÉGRAL, en PDF, CONSERVÉ. Un seul à la fois par document.

        Le verrou n'est pas une précaution de style : sans lui, deux requêtes
        d'aperçu simultanées construisaient le même rendu en parallèle — deux
        réinjections PPTX dans deux moteurs, deux conversions LibreOffice, pour
        un seul résultat. Le second entrant retrouve ici le cache déjà écrit.
        """
        with render_cache.build_lock(doc.translated_path):
            cached = render_cache.cache_valid(doc.translated_path)
            if cached:
                with open(cached, "rb") as f:
                    return f.read()
            data = _render_or_404(doc, ext, only_pages=None)
            if ext != "pdf":
                        data = convert_to_pdf_bytes(data, ext)
            render_cache.store_render(data, doc.translated_path)
            return data

    if fenetre is None:
        data = await run_in_threadpool(_build_complet)
        if not clear:
            data = await run_in_threadpool(rasterize_for_trial, data)
        return Response(content=data, media_type="application/pdf",
                        headers={"Cache-Control": "no-store",
                                 "X-Render": "full"})

    # ── PDF, page par page ────────────────────────────────────────────────
    # Les pages non rendues sont recopiées de l'original, donc la pagination
    # reste celle du document — le lecteur n'a rien à recalculer. En tâche de
    # fond, on construit UNE fois le rendu complet : quelques minutes plus tard,
    # la navigation est instantanée et ne redemande plus rien.
    if clear:
        render_cache.ensure_background_build(
            doc.original_path, doc.translated_path, ext, doc.target_lang)

    def _build_page() -> bytes:
        data = _render_or_404(doc, ext, only_pages=fenetre)
        if not clear:
            data = rasterize_for_trial(data)      # ext == "pdf" garanti ici
        return data

    data = await run_in_threadpool(_build_page)
    return Response(content=data, media_type="application/pdf",
                    headers={"Cache-Control": "no-store",
                             "X-Render": "page"})


# ── DELETE /documents/{id} ───────────────────────────────────────────────────

@router.delete("/{doc_id}")
async def delete_document(
    doc_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Supprime un document et ses fichiers."""
    doc = await db.get(Document, doc_id)
    if doc is None or doc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document introuvable.")

    # On rembourse ce qui a été FACTURÉ, pas la taille du fichier. Un dépôt à
    # quota plein (ou sur plan sans stockage) est facturé 0 : rembourser `size`
    # faisait descendre `storage_used` sous la réalité à chaque suppression.
    charge = doc.storage_charged or 0
    original_path = doc.original_path
    translated_path = doc.translated_path
    await db.delete(doc)

    # Libérer le quota
    user.storage_used = max(0, user.storage_used - charge)
    await db.commit()

    # Purge par comptage de références. Le commit ci-dessus est INDISPENSABLE
    # avant de compter : tant que la suppression n'est pas validée, la ligne
    # courante se compterait elle-même et rien ne serait jamais orphelin.
    purged = 0
    for path in (translated_path, original_path):
        if path and await _purge_if_orphan(db, path):
            purged += 1

    return {"message": "Document supprimé.", "storage_freed": charge,
            "files_purged": purged}
