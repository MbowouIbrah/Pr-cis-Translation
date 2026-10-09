"""Routes de traduction : depot, suivi SSE, partiel, resultat, apercu.

Ces routes ne traduisent pas elles-memes. Elles valident la demande, verifient
les droits, ouvrent un job et confient le travail a un THREAD worker
(`app.services.translation_runner`). Tout ce qui bloque -- reseau, LibreOffice,
rendu -- doit rester hors de la boucle asyncio, sinon c'est toute
l'application, flux SSE compris, qui se fige.
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
from datetime import datetime, timezone

from fastapi import (APIRouter, Depends, File, Form, Header, HTTPException,
                     Request, UploadFile)
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.documents import _may_read_clear
from app.config import ALLOWED_EXTENSIONS, MAX_FILE_SIZE, logger, resolve_quality
from app.core.database import get_db
from app.core.files import (get_file_hash, pages_token, parse_page_range,
                            sanitize_filename)
from app.core.security import require_auth, verify_access_token
from app.models import (Document, User, get_plan_page_limit,
                        get_plan_priority, get_plan_storage,
                        get_plan_monthly_pages, is_paid_plan)
from app.rate_limit import verify_api_key
from app.services.documents import (build_job_paths, cap_pages_for_plan,
                                    count_pages, pages_avec_texte)
from app.services.jobs import jobs
from app.services.office import convert_to_pdf_bytes
from app.services.scheduler import scheduler
from app.services.translation_runner import (run_pdf_v2_job,
                                             run_pptx_progressive_job,
                                             run_translation_job)
from app.services.trial_preview import rasterize_for_trial

router = APIRouter(tags=["Traduction"])


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.options("/api/translate")
async def translate_options():
    return JSONResponse(content={}, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-API-Key",
    })

@router.options("/api/preview/pdf")
async def preview_pdf_options():
    return JSONResponse(content={}, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-API-Key",
    })

@router.post("/api/preview/pdf")
async def preview_pdf_endpoint(
    request: Request,
    file: UploadFile = File(...),
    x_api_key: str = Header(None),
    # CONNEXION OBLIGATOIRE : la clé d'API est publique (bundle du frontend),
    # elle ne « protège » rien. Sans JWT, cet endpoint était une ferme de
    # conversion LibreOffice ouverte à n'importe qui sur Internet.
    current_user: "User" = Depends(require_auth),
):
    """Convertit un document (DOCX/PPTX/TXT) en PDF pour l'aperçu côté client.
    Les PDF sont renvoyés tels quels. La conversion ne change pas le fichier
    téléchargeable, elle ne sert qu'à un rendu exact dans le viewer."""
    verify_api_key(x_api_key)

    filename = file.filename or ""
    ext = filename.split(".")[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Type non supporté pour l'aperçu : .{ext}")

    try:
        file_bytes = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Impossible de lire le fichier. Vérifiez qu'il n'est pas corrompu et réessayez.")

    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="Fichier trop volumineux pour l'aperçu.")

    try:
        pdf_bytes = await asyncio.to_thread(convert_to_pdf_bytes, file_bytes, ext)
    except Exception as e:
        logger.error(f"Conversion aperçu PDF échouée : {e}")
        raise HTTPException(status_code=500, detail="La conversion du PDF a échoué. Vérifiez que le fichier est un PDF valide et non corrompu.")

    return Response(content=pdf_bytes, media_type="application/pdf")


async def _save_document_for_user(user: "User | None", db: "AsyncSession",
                                  job_id: str, filename: str, target_lang: str,
                                  original_path: str, size: int,
                                  paid: bool = False, page_count: int = 1):
    """Enregistre un Document pour l'utilisateur connecté.

    Le Document est AUSSI le registre d'usage mensuel (compteur freemium) : on
    l'insère donc pour TOUT utilisateur authentifié, plan `free` compris — sinon
    la limite « 1 page/mois » ne pourrait jamais s'appuyer sur rien (bug : un
    plan `free` a 0 Mo de stockage, l'ancien code refusait alors la création et
    le compteur restait éternellement à 0). Le stockage n'est facturé que si le
    plan en offre ET que le quota le permet ; sinon `charge = 0` (l'usage est
    tout de même journalisé)."""
    if user is None:
        return
    try:
        plan_storage = get_plan_storage(user.plan)
        charge = size if plan_storage > 0 else 0
        # L'admin n'a AUCUNE limite : son usage est compté, jamais plafonné.
        if (user.plan != "admin" and charge
                and user.storage_used + charge > plan_storage):
            charge = 0                      # quota plein : on journalise sans facturer
        doc = Document(user_id=user.id, original_name=filename, source_lang="auto",
                       target_lang=target_lang, original_path=original_path,
                       size_bytes=size, storage_charged=charge,
                       status="translating", paid=paid,
                       # Le quota mensuel SOMME cette colonne. Laissée à NULL
                       # (son ancien état), elle rendait tout quota de pages
                       # incomptable — donc invendable.
                       page_count=page_count)
        db.add(doc)
        user.storage_used += charge
        await db.commit()
        # Lien job → Document : permet à `jobs.done`/`jobs.error` de reporter
        # `status`/`translated_path` à la fin du traitement (thread worker).
        jobs.set(job_id, document_id=doc.id)
    except Exception:
        await db.rollback()


@router.post("/api/translate")
async def translate_endpoint(
    request: Request,
    file: UploadFile = File(...),
    target_lang: str = Form("en"),
    format_options: str = Form("{}"),
    quality: str = Form("fast"),
    precise: str = Form(""),
    pages: str = Form(""),
    debug: str = Form(""),
    x_api_key: str = Header(None),
    # CONNEXION OBLIGATOIRE (décision produit) : un visiteur ne lance aucune
    # traduction. `optional_auth` laissait passer l'anonyme avec 1 page —
    # or la clé d'API est publique (elle est dans le bundle du frontend), donc
    # ce quota ne coûtait qu'un onglet de navigation privée à contourner.
    current_user: "User" = Depends(require_auth),
    db: "AsyncSession" = Depends(get_db),
):
    """Démarre un job de traduction et retourne immédiatement un job_id.
    Le client peut ensuite écouter /api/translate/events/{job_id} (SSE)
    pour suivre la progression, puis télécharger via /api/translate/result/{job_id}."""
    verify_api_key(x_api_key)

    # Le flag 'precise' du frontend force le mode raisonnement (admin)
    if precise == "1":
        quality = "precise"

    filename = file.filename or ""
    ext = filename.split(".")[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Type non supporté : .{ext}")

    try:
        file_bytes = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Impossible de lire le fichier. Vérifiez qu'il n'est pas corrompu et réessayez.")

    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"Fichier trop volumineux ({len(file_bytes)/(1024*1024):.1f} Mo, max 100 Mo).")

    try:
        format_opts = json.loads(format_options) if format_options else {}
    except json.JSONDecodeError:
        format_opts = {}

    # Mode STRUCTURE (debug) : aucune traduction. Reproduit exactement
    # test_extract_inject.py — extraction → translated = texte d'origine →
    # injection avec bordures de debug (contours de paragraphes). Sert à vérifier
    # le moteur dans l'interface, à l'identique des tests.
    debug_mode = str(debug).strip().lower() in ("1", "true", "yes", "on")

    # Mode de traduction (rapide vs précis) → modèle + budget de tokens.
    quality = quality if quality in ("fast", "precise") else "fast"
    model, max_tokens = resolve_quality(quality)
    # Suffixe de cache : les deux modes produisent des résultats différents, ils
    # ne doivent JAMAIS partager le même translated.json ni le même PDF de sortie.
    qsuffix = "" if quality == "fast" else "_precise"
    if debug_mode:
        qsuffix = "_debug"   # n'écrase jamais une vraie traduction en cache

    # Sélection de pages (PDF/PPTX) : None = tout le document. Le jeton entre
    # dans toutes les clés de cache pour qu'une plage donnée ne réutilise jamais
    # le résultat d'une autre plage (ni du document entier).
    pages_set = parse_page_range(pages) if ext in ("pdf", "pptx") else None

    # ── Pages RÉELLEMENT traduisibles ────────────────────────────────────────
    #
    # Un PDF scanné est une suite d'images : aucun caractère n'y est
    # extractible, le moteur n'en tire rien et rend le document INCHANGÉ. La
    # facturation, elle, comptait les pages du conteneur. MESURÉ sur un scan
    # synthétique de trois pages : 3 pages débitées, 0 élément de texte extrait.
    # L'utilisateur payait, recevait son document tel quel, et son crédit avait
    # disparu — sans un mot, ni pour lui, ni dans le journal.
    #
    # Ce contrôle vient AVANT tout débit. C'est sa seule place utile : après, le
    # crédit est parti et le rembourser demanderait un chemin de compensation
    # qui n'existe pas.
    #
    # Les pages écartées ne sont pas perdues pour autant : `pages` ne découpe
    # pas le document, les pages hors sélection sont COPIÉES telles quelles
    # (cf. `translate_pdf_progressive`). L'utilisateur récupère donc son
    # document entier, ses pages scannées intactes, et ne paye que ce qui a pu
    # être traduit.
    lisibles = pages_avec_texte(file_bytes, ext)
    pages_ignorees = 0
    if lisibles is not None:
        visees = pages_set or set(range(1, count_pages(file_bytes, ext) + 1))
        traduisibles = visees & lisibles

        if not traduisibles:
            # Rien à lire dans tout ce qui était visé : on refuse, et on ne
            # débite rien. `reason` distingue ce cas d'un refus de quota — le
            # client ne doit surtout pas proposer d'acheter des pages pour un
            # document qu'aucun achat ne rendra traduisible.
            #
            # `JSONResponse` et non `HTTPException(detail={...})` : FastAPI
            # emboîterait alors l'objet sous `detail`, or le client lit
            # `err.detail` comme un TEXTE à afficher. Il montrerait
            # « [object Object] » à l'utilisateur, et `err.reason` — qu'il lit
            # au premier niveau, comme pour `need_credits` — serait introuvable.
            return JSONResponse(
                status_code=422,
                content={
                    "detail": (
                        "Ce document ne contient aucun texte lisible : ses pages "
                        "sont des images (document scanné ou photographié). "
                        "Aucune page n'a été décomptée. Pour le traduire, "
                        "convertissez-le d'abord en PDF texte avec un outil de "
                        "reconnaissance de caractères (OCR)."
                    ),
                    "reason": "scanned_document",
                    "pages_scanned": len(visees),
                },
            )

        if traduisibles != visees:
            # Document MIXTE. On ne touche à `pages_set` que dans ce cas : le
            # laisser à None quand tout est traduisible préserve les clés de
            # cache existantes (`pages_token`), qu'une sélection explicite
            # ferait diverger sans raison.
            pages_ignorees = len(visees) - len(traduisibles)
            pages_set = traduisibles
            logger.info(
                "Document mixte : %d page(s) sans texte extractible écartée(s) "
                "de la traduction et de la facturation.", pages_ignorees)

    # ── Limite de pages selon le plan ─────────────────────────────────────
    # On est dans un endpoint ASYNC : la session `db` et l'utilisateur
    # (`optional_auth`) sont déjà résolus par FastAPI. On interroge donc la base
    # par `await` direct — l'ancien code planifiait la coroutine sur la boucle
    # qui l'exécutait puis attendait le résultat en la bloquant (deadlock →
    # timeout 3 s → repli `free`), ce qui rétrogradait tout compte payant.
    plan = current_user.plan
    limit = get_plan_page_limit(plan)
    page_limit = limit if limit is not None else 999_999

    # Un plan limité en pages n'a droit qu'aux formats où la limite est
    # APPLICABLE (PDF, PPTX : l'extraction sait sélectionner ses pages). Le
    # DOCX se traduit d'un bloc : l'autoriser ici, c'était offrir un document
    # entier — la « limite » ne limitait rien.
    if page_limit == 1 and ext not in ("pdf", "pptx"):
        raise HTTPException(
            status_code=402,
            detail="Forfait Gratuit : essai sur PDF ou PPTX uniquement. "
                   "Passez à Starter pour traduire ce format.",
        )

    # Un plan payant couvre tout ce qu'il produit ; pour le forfait Gratuit,
    # seul l'achat rend le document lisible en clair (cf. `_may_read_clear`).
    doc_is_paid = is_paid_plan(plan)

    # ── Forfait Gratuit : une traduction offerte par mois, puis à la page ─────
    #
    # Deux titres pour traduire, dans cet ordre :
    #   1. la traduction OFFERTE du mois — une page, une fois ;
    #   2. les pages ACHETÉES d'avance, débitées ici.
    #
    # Le débit a lieu AVANT de lancer quoi que ce soit : c'est la règle du
    # produit (« paiement avant même de traduire »), et c'est aussi la seule
    # façon d'éviter qu'un travail coûteux parte pour un solde déjà vide.
    if page_limit == 1:
        # Verrou pessimiste sur la ligne User : le compteur est un
        # lire-puis-écrire (COUNT ici, INSERT du Document plus bas). Sans lock,
        # deux requêtes simultanées du même compte lisaient toutes deux 0 et
        # passaient toutes deux. Le lock tient jusqu'au commit de
        # `_save_document_for_user` : la seconde requête attend, recompte, 402.
        # Il protège désormais AUSSI le solde de pages : sans lui, deux
        # traductions lancées ensemble débiteraient toutes deux le même solde.
        locked = (await db.execute(
            select(User).where(User.id == current_user.id).with_for_update()
        )).scalar_one()
        month_start = datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0)
        r = await db.execute(
            select(func.count()).select_from(Document).where(
                Document.user_id == current_user.id,
                Document.created_at >= month_start,
            )
        )
        free_used = (r.scalar() or 0) >= 1

        if not free_used:
            # La traduction offerte : une page, celle que le plan autorise.
            pages_set = cap_pages_for_plan(pages_set, ext, page_limit)
        else:
            # On facture ce qui sera RÉELLEMENT traduit : la sélection si elle
            # existe, tout le document sinon.
            needed = len(pages_set) if pages_set else count_pages(file_bytes, ext)
            if locked.page_credits < needed:
                # Réponse STRUCTURÉE (pas un simple message) : le client a besoin
                # du NOMBRE de pages à acheter pour ouvrir directement le paiement
                # à la page, sans que l'utilisateur ait à deviner ni à retaper.
                # `reason` distingue ce cas (rattrapable par un achat) d'un 402 de
                # plan (mensuel), qu'un achat de pages ne débloque pas.
                return JSONResponse(
                    status_code=402,
                    content={
                        "detail": (f"Vous avez {locked.page_credits} page(s) disponible(s) "
                                   f"mais ce document en nécessite {needed}. "
                                   "Achetez des pages supplémentaires pour continuer."),
                        "reason": "need_credits",
                        "pages_needed": needed,
                        "pages_available": locked.page_credits,
                    },
                )
            locked.page_credits -= needed
            # Pas de `cap_pages_for_plan` ici : ces pages sont payées, les
            # plafonner à une seule reviendrait à encaisser sans livrer.
            #
            # Et le document qui va naître est PAYÉ : sans ce drapeau, on
            # débiterait le solde pour produire une traduction que son
            # acheteur ne pourrait ni voir en clair ni télécharger.
            doc_is_paid = True
    else:
        pages_set = cap_pages_for_plan(pages_set, ext, page_limit)

        # ── Plans payants : quota de pages du MOIS ────────────────────────────
        #
        # Distinct du plafond par document (`page_limit`), qui ne limite qu'une
        # traduction à la fois. Sans ce compteur, « 100 pages par mois » sur la
        # carte de tarifs voulait dire « autant de documents de 100 pages que
        # vous voulez » : le quota vendu n'existait tout simplement pas.
        monthly = get_plan_monthly_pages(plan)
        if monthly is not None:
            # Même verrou que pour le forfait Gratuit, et pour la même raison :
            # compter puis insérer est un lire-puis-écrire.
            await db.execute(
                select(User).where(User.id == current_user.id).with_for_update()
            )
            month_start = datetime.now(timezone.utc).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0)
            r = await db.execute(
                select(func.coalesce(func.sum(Document.page_count), 0)).where(
                    Document.user_id == current_user.id,
                    Document.created_at >= month_start,
                )
            )
            deja = int(r.scalar() or 0)
            demande = len(pages_set) if pages_set else count_pages(file_bytes, ext)
            if deja + demande > monthly:
                raise HTTPException(
                    status_code=402,
                    detail=(f"Vous avez déjà traduit {deja} page(s) ce mois-ci "
                            f"(limite : {monthly}). Ce document en demande {demande}. "
                            "Le compteur repart le 1er du mois prochain."),
                )

    # Ce qui sera RÉELLEMENT traduit — enregistré sur le Document, sinon le
    # quota du mois prochain n'aurait rien à compter (`page_count` restait NULL).
    pages_facturees = len(pages_set) if pages_set else count_pages(file_bytes, ext)

    ptok = pages_token(pages_set)
    psuffix = f"_{ptok}" if ptok else ""

    file_hash = get_file_hash(file_bytes)
    safe_name = sanitize_filename(filename)
    job_dir, lang_dir = build_job_paths(file_hash, target_lang)
    os.makedirs(lang_dir, exist_ok=True)

    original_path = os.path.join(job_dir, f"original.{ext}")
    extraction_path = os.path.join(job_dir, f"extraction{psuffix}.json")
    # LA traduction persistante — la sortie DeepSeek, seule chose coûteuse à
    # reconstituer. Pour le PDF, c'est le JSON du moteur v2 (texte + décisions
    # de page) ; pour DOCX/PPTX, le JSON de traduction. Le PDF/DOCX rendu, lui,
    # n'est PAS conservé : il se recalcule à la demande depuis ces deux-là.
    if ext == "pdf":
        translation_path = os.path.join(lang_dir, f"pages{qsuffix}{psuffix}.json")
    else:
        translation_path = os.path.join(lang_dir, f"translated{qsuffix}{psuffix}.json")

    output_filename = f"{safe_name}_TRADUIT{qsuffix}{psuffix}.{ext}"
    if debug_mode:
        output_filename = f"{safe_name}_STRUCTURE{psuffix}.{ext}"
    if format_opts.get("mode") and format_opts["mode"] != "preserve":
        output_filename = f"{safe_name}_TRADUIT{qsuffix}{psuffix}_{format_opts['mode']}.{ext}"
    layout_opts = format_opts.get("layout")
    if layout_opts:
        import hashlib as _hl
        layout_sig = _hl.sha1(json.dumps(layout_opts, sort_keys=True).encode()).hexdigest()[:10]
        base, dot, fext = output_filename.rpartition(".")
        output_filename = f"{base}_L{layout_sig}{dot}{fext}"

    job_id = jobs.create()
    # Rendu TRANSITOIRE, propre au job (nettoyé par le GC) : il sert le flux
    # progressif et le téléchargement immédiat, puis disparaît. La source de
    # vérité reste `translation_path`.
    output_path = os.path.join(lang_dir, f"render{qsuffix}{psuffix}_{job_id[:8]}.{ext}")
    # `user_id` : proprietaire, dont /partial et /result se servent.
    # `pages` : pages RÉELLEMENT traduites, les seules à protéger dans
    # l'aperçu d'essai (les autres sont des copies de l'original).
    jobs.set(job_id, user_id=current_user.id,
             pages=set(pages_set) if pages_set else None)
    if ext == "pdf":
        # PDF → moteur v2 PROGRESSIF : page traduite = page affichable.
        # `partial` grandit page à page ; `translation_path` (pages.json) = la
        # traduction persistante, qui sert aussi de cache de reprise.
        partial_path = os.path.join(lang_dir, f"partial{qsuffix}{psuffix}_{job_id[:8]}.pdf")
        target = run_pdf_v2_job
        job_args = (job_id, file_bytes, original_path, output_path,
                    output_filename, partial_path, translation_path, target_lang,
                    pages_set, debug_mode)
    elif ext == "pptx":
        # PPTX → moteur progressif slide par slide (comme le PDF v2).
        # Si compte admin : 5 slides traduites en parallèle.
        partial_path = os.path.join(lang_dir, f"partial{qsuffix}{psuffix}_{job_id[:8]}.pdf")
        is_admin = (current_user.plan == "admin")
        target = run_pptx_progressive_job
        job_args = (job_id, file_bytes, original_path, output_path,
                    output_filename, partial_path, translation_path, target_lang,
                    pages_set, debug_mode, is_admin)
    else:
        target = run_translation_job
        job_args = (job_id, file_bytes, filename, ext, target_lang, format_opts,
                    job_dir, lang_dir, original_path, extraction_path,
                    translation_path, output_path, output_filename,
                    model, max_tokens, pages_set, debug_mode)
    # ── Document en base AVANT de démarrer le worker ─────────────────────
    # L'ordre est un garde-fou, pas un détail : la ligne Document est la seule
    # référence qui protège les octets partagés du magasin contre la purge d'un
    # autre compte (`_purge_if_orphan` compte les références en base). Insérer
    # après `thread.start()` ouvrait deux fenêtres : (1) A supprime son document
    # pendant que le job de B démarre → les fichiers communs sont purgés sous
    # ses pieds ; (2) un cache-hit terminait le job avant que `document_id` ne
    # soit lié → le Document restait « translating » pour toujours.
    await _save_document_for_user(current_user, db, job_id, filename,
                                  target_lang, original_path, len(file_bytes),
                                  paid=doc_is_paid, page_count=pages_facturees)

    # File de PRIORITÉ, plus de thread lancé à la volée : l'ordonnanceur place le
    # job selon le niveau du plan (admin > pro > starter > gratuit) et le fait
    # démarrer dès qu'un worker se libère. Soumis APRÈS l'insertion du Document,
    # pour la même raison que l'ancien `thread.start()` venait après.
    scheduler.submit(job_id, get_plan_priority(current_user.plan), target, job_args)

    logger.info(f"Job {job_id} queued for '{filename}' -> {target_lang} "
                f"(priorité {get_plan_priority(current_user.plan)})")
    # `pages_ignorees` : pages sans texte extractible, écartées de la traduction
    # ET de la facturation. Renvoyé pour que l'interface puisse le DIRE — sans
    # cela, l'utilisateur d'un document mixte verrait des pages revenir
    # inchangées sans comprendre pourquoi, et croirait à une traduction ratée
    # là où il s'agit d'un scan qu'on n'a pas facturé.
    reponse = {"job_id": job_id}
    if pages_ignorees:
        reponse["pages_ignorees"] = pages_ignorees
    return JSONResponse(reponse)


@router.get("/api/translate/events/{job_id}")
async def translation_events(job_id: str, token: str = ""):
    """SSE endpoint : émet les events de progression jusqu'à done/error.

    EventSource (navigateur) ne supporte pas les headers custom : le JWT passe
    donc en QUERY (`?token=`). S'appuyer sur le seul job_id laissait le flux
    sans AUCUNE authentification — pas d'octets du document, mais le nom du
    fichier et la progression d'autrui, et surtout la file d'événements est à
    consommateur UNIQUE : un tiers branché sur le flux VOLE les événements du
    client légitime. On répond 404 (pas 403) pour ne pas révéler l'existence
    du job. Le middleware ne journalise que le path, jamais la query : le
    token ne fuit pas dans les logs.
    """
    try:
        payload = verify_access_token(token)
        user_id = payload["sub"]
    except Exception:
        raise HTTPException(status_code=404, detail="Job introuvable.")

    job = jobs.get(job_id)
    if not job or job.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Job introuvable.")

    async def event_stream():
        q: queue.Queue = job["q"]
        while True:
            try:
                # Lecture non-bloquante avec délai pour laisser respirer l'event loop
                try:
                    msg = q.get(timeout=0.2)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    await asyncio.sleep(0.1)
                    continue

                data = json.dumps(msg, ensure_ascii=False)
                yield f"data: {data}\n\n"

                if msg.get("type") in ("done", "error"):
                    break
            except asyncio.CancelledError:
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _job_of(job_id: str, user: "User"):
    """Le job `job_id`, s'il appartient bien à `user`.

    Le contrôle de propriété est INDISPENSABLE : la clé d'API voyage dans le
    bundle du frontend, elle est donc publique. Sans ce garde, un `job_id`
    deviné suffisait à lire le document d'un autre compte. On répond 404 (et
    non 403) pour ne pas révéler l'existence du job.
    """
    job = jobs.get(job_id)
    if not job or job.get("user_id") != user.id:
        raise HTTPException(status_code=404, detail="Job introuvable.")
    return job


async def _droit_au_clair(job: dict, user: "User", db: AsyncSession) -> bool:
    """Ce compte peut-il lire CE job en clair ? Même règle que les routes de
    `documents` (`_may_read_clear`) : l'abonnement OU l'achat de ce document.
    Lire le seul plan refusait le clair à un compte gratuit qui venait de
    payer précisément cette traduction."""
    if is_paid_plan(user.plan):
        return True
    doc_id = job.get("document_id")
    doc = await db.get(Document, doc_id) if doc_id else None
    return doc is not None and doc.user_id == user.id and _may_read_clear(user, doc)


@router.get("/api/translate/partial/{job_id}")
async def translation_partial(job_id: str, x_api_key: str = Header(None),
                              current_user: "User" = Depends(require_auth),
                              db: AsyncSession = Depends(get_db)):
    """PDF PARTIEL d'un job v2 en cours : contient les pages 1..k déjà
    traduites (réécrit atomiquement après chaque page). Le client le recharge
    à chaque événement `page done` pour afficher la traduction au fil de l'eau."""
    verify_api_key(x_api_key)
    job = _job_of(job_id, current_user)
    path = job.get("partial_path")
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=202, detail="Aucune page prête pour l'instant.")
    # Lecture déportée : un `read()` ici figerait la boucle d'événements, donc
    # le flux SSE qui annonce les pages suivantes de cette même traduction.
    data = await asyncio.to_thread(lambda: open(path, "rb").read())

    # PLAN D'ESSAI : jamais le clair. On envoyait le PDF traduit tel quel et on
    # comptait sur le navigateur pour l'assombrir — mesuré : un compte `free`
    # récupérait le FICHIER (782 mots extractibles) en trois clics dans l'onglet
    # Réseau. Rastériser retire la couche texte : il ne reste que des pixels
    # filigranés, inexploitables sans OCR. Le projecteur au survol, lui, marche
    # toujours (il lui faut des pixels nets, il en a).
    # Liste d'AUTORISATION, pas de refus : un plan inconnu (valeur corrompue,
    # plan retiré du barème) est traité comme non payant. `== FREE_PLAN`
    # donnait l'inverse : tout ce qui n'était pas littéralement "free" passait.
    if not await _droit_au_clair(job, current_user, db):
        data = rasterize_for_trial(data, job.get("pages"))

    return Response(content=data, media_type="application/pdf",
                    headers={"Cache-Control": "no-store"})


@router.get("/api/translate/result/{job_id}")
async def translation_result(job_id: str, x_api_key: str = Header(None),
                             current_user: "User" = Depends(require_auth),
                             db: AsyncSession = Depends(get_db)):
    """Retourne le fichier traduit une fois le job terminé.

    TÉLÉCHARGEMENT RÉSERVÉ AUX PLANS PAYANTS (décision produit). Le verrou
    n'existait que dans le frontend (bouton qui renvoyait vers la grille
    tarifaire) : un appel direct rendait le PDF complet à n'importe qui. Un
    verrou qui n'est pas appliqué par le serveur n'est pas un verrou.
    """
    verify_api_key(x_api_key)
    job = _job_of(job_id, current_user)
    if not await _droit_au_clair(job, current_user, db):
        raise HTTPException(
            status_code=402,
            detail="Forfait Gratuit : téléchargement indisponible. "
                   "Passez à Starter pour télécharger vos traductions.",
        )
    if job["state"] == "error":
        raise HTTPException(status_code=500, detail="La traduction a échoué. Réessayez ou contactez le support si le problème persiste.")
    if job["state"] != "done":
        raise HTTPException(status_code=202, detail="Job en cours.")
    if not job["result_path"] or not os.path.exists(job["result_path"]):
        raise HTTPException(status_code=500, detail="Fichier résultat introuvable.")

    return FileResponse(
        job["result_path"],
        media_type="application/octet-stream",
        filename=job["result_filename"],
    )
