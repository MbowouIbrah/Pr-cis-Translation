"""
Routes de paiement — achat de pages à l'unité (forfait Gratuit).

Règle du produit, telle que décidée : le forfait Gratuit a droit à UNE
traduction offerte par mois, qu'il peut lancer et regarder en aperçu masqué.
Tout le reste se paie À LA PAGE, et se paie AVANT. Ce qui est payé devient
visible en clair et téléchargeable ; rien d'autre ne l'est.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import User, Document, Payment
from app.core.security import require_auth
from app.core.pricing import zone_for_country, CURRENCY, page_price
from app.core.payment_gateway import (campay, CampayError, SUPPORTED_CURRENCY,
                           normalize_phone)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payments", tags=["Paiements"])

# Garde-fou : un achat porte sur un document réel, pas sur une commande d'un
# million de pages tapée dans la console du navigateur.
MAX_PAGES_PER_PAYMENT = 500


def _zone_of(request: Request, override: str | None = None) -> str:
    return zone_for_country(
        override
        or request.headers.get("CF-IPCountry")
        or request.headers.get("X-Country")
    )


class QuoteBody(BaseModel):
    pages: int = Field(..., ge=1, le=MAX_PAGES_PER_PAYMENT)


@router.post("/quote")
async def quote(body: QuoteBody, request: Request, user: User = Depends(require_auth)):
    """Ce que coûtera la traduction, AVANT de la lancer.

    Le prix est annoncé ici et refacturé à l'identique à l'encaissement : le
    montant que l'utilisateur voit est celui qui sera débité, jamais un autre.
    """
    zone = _zone_of(request)
    unit = page_price(zone)
    return {
        "pages": body.pages,
        "unit_price": unit,
        "amount": unit * body.pages,
        "currency": CURRENCY[zone],
        "zone": zone,
    }


class CollectBody(BaseModel):
    pages: int = Field(..., ge=1, le=MAX_PAGES_PER_PAYMENT)
    # Numéro au format international sans « + » (ex. 237670000000).
    phone: str = Field(..., min_length=9, max_length=15)
    # Paiement destiné à débloquer un document DÉJÀ traduit (la traduction
    # offerte, typiquement). Absent = achat de pages d'avance.
    document_id: str | None = None


@router.post("/collect")
async def collect(body: CollectBody, request: Request,
                  user: User = Depends(require_auth),
                  db: AsyncSession = Depends(get_db)):
    """Déclenche l'invite de paiement mobile money sur le téléphone du client.

    Rend la main IMMÉDIATEMENT : la saisie du code USSD prend souvent plus
    d'une minute, attendre le résultat ici bloquerait la requête et l'écran.
    Le client interroge ensuite `GET /api/payments/{id}`.
    """
    zone = _zone_of(request)
    if CURRENCY[zone] != SUPPORTED_CURRENCY:
        # Campay n'encaisse qu'en francs CFA. Le dire franchement vaut mieux
        # que de convertir en douce un prix affiché en euros.
        raise HTTPException(
            status_code=501,
            detail="Le paiement mobile money n'est disponible que dans la zone FCFA.",
        )

    doc = None
    if body.document_id:
        doc = await db.get(Document, body.document_id)
        if doc is None or doc.user_id != user.id:
            raise HTTPException(status_code=404, detail="Document introuvable.")
        if doc.paid:
            raise HTTPException(status_code=409, detail="Ce document est déjà payé.")

    amount = page_price(zone) * body.pages
    # Normalisé ICI, une fois : ce qui part chez Campay et ce qu'on enregistre
    # doivent être le même numéro. Le faire côté navigateur laisserait les deux
    # diverger dès qu'un autre client appellerait l'API.
    phone = normalize_phone(body.phone)

    # La trace existe AVANT l'appel. Si le réseau coupe juste après que Campay
    # a débité le client, on a de quoi réconcilier ; l'inverse perd l'argent
    # du client sans laisser la moindre trace chez nous.
    payment = Payment(
        user_id=user.id, document_id=body.document_id, pages=body.pages,
        amount=amount, currency=CURRENCY[zone], zone=zone,
        phone=phone, status="PENDING",
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    try:
        res = await campay.collect(
            amount=amount, phone=phone,
            description=f"Précis — {body.pages} page(s)",
            external_reference=payment.id,
        )
    except CampayError as e:
        # On journalise le détail, on n'en montre rien : la réponse d'un
        # fournisseur de paiement peut contenir des éléments de compte.
        logger.warning("Campay collect échoué (paiement %s) : %s", payment.id, e)
        payment.status = "FAILED"
        await db.commit()
        raise HTTPException(status_code=502, detail=e.user_message)

    payment.provider_ref = res.get("reference")
    await db.commit()

    return {
        "payment_id": payment.id,
        "status": "PENDING",
        "amount": amount,
        "currency": payment.currency,
        # À afficher : sur certains téléphones l'invite n'arrive pas et il faut
        # composer ce code à la main.
        "ussd_code": res.get("ussd_code"),
        "operator": res.get("operator"),
    }


async def _apply_credit(db: AsyncSession, payment: Payment) -> None:
    """Porte les pages achetées au compte — UNE SEULE FOIS.

    Le webhook de Campay et l'interrogation d'état par le client arrivent tous
    les deux, souvent simultanément, parfois plusieurs fois. Sans le drapeau
    `credited`, le même paiement créditerait deux ou trois fois : le client
    recevrait des pages qu'il n'a pas achetées, et le total ne se raccorderait
    à rien.
    """
    if payment.credited or payment.status != "SUCCESSFUL":
        return

    if payment.document_id:
        # Paiement ciblé : il débloque CE document, et rien d'autre.
        doc = await db.get(Document, payment.document_id)
        if doc is not None:
            doc.paid = True
    else:
        user = await db.get(User, payment.user_id)
        if user is not None:
            user.page_credits += payment.pages

    payment.credited = True
    await db.commit()


@router.get("/{payment_id}")
async def payment_status(payment_id: str, user: User = Depends(require_auth),
                         db: AsyncSession = Depends(get_db)):
    """État d'un paiement. C'est ce que le client interroge pendant l'attente."""
    payment = await db.get(Payment, payment_id)
    if payment is None or payment.user_id != user.id:
        raise HTTPException(status_code=404, detail="Paiement introuvable.")

    # Tant que c'est en attente, on redemande au fournisseur : le webhook peut
    # ne jamais arriver (pare-feu, environnement de développement sans URL
    # publique). Ne dépendre QUE du webhook, c'est laisser des clients payés
    # sans rien recevoir.
    if payment.status == "PENDING" and payment.provider_ref:
        try:
            res = await campay.transaction_status(payment.provider_ref)
            new_status = res.get("status", "PENDING")
            if new_status != payment.status:
                payment.status = new_status
                await db.commit()
                await _apply_credit(db, payment)
        except CampayError as e:
            logger.warning("Statut Campay indisponible (%s) : %s", payment_id, e)

    return {
        "payment_id": payment.id,
        "status": payment.status,
        "pages": payment.pages,
        "amount": payment.amount,
        "currency": payment.currency,
        "document_id": payment.document_id,
    }


@router.api_route(
    "/campay/webhook", methods=["GET", "POST"],
    # `operation_id` explicite : sans lui, les deux methodes derivent le MEME
    # identifiant du nom de la fonction, l'OpenAPI devient ambigu et tout
    # generateur de client s'en plaint (avertissement au demarrage).
    operation_id="campay_webhook",
    summary="Notification de paiement Campay",
    include_in_schema=False,   # endpoint appele par Campay, pas par le client
)
async def campay_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Notification de Campay.

    GET **et** POST : la méthode se choisit dans les réglages de l'application
    Campay. N'en accepter qu'une, c'est laisser un réglage du tableau de bord
    couper silencieusement les notifications.

    DEUX barrières, et la seconde suffirait :
      1. la `signature` (JWT HS256 signé avec la webhook key de l'application)
         prouve que l'appel vient de Campay ;
      2. l'état retenu n'est jamais celui annoncé dans le corps, mais celui que
         l'API de Campay confirme quand NOUS l'interrogeons, sur une référence
         que nous avons nous-mêmes émise.

    On garde les deux : la signature évite d'appeler Campay pour chaque
    sollicitation d'un inconnu, la re-vérification protège même si la clé fuit.
    """
    if request.method == "POST":
        try:
            body = dict(await request.json())
        except Exception:
            raise HTTPException(status_code=400, detail="Corps illisible.")
    else:
        body = dict(request.query_params)

    # Barrière 1 : origine prouvée.
    if not campay.verify_webhook_signature(body.get("signature", "")):
        # 403 et pas 400 : ce n'est pas une requête malformée, c'est une
        # requête non authentifiée. Le journal garde la trace de la tentative.
        logger.warning("Webhook Campay sans signature valide (ref=%s)",
                       body.get("reference"))
        raise HTTPException(status_code=403, detail="Signature invalide.")

    reference = body.get("reference") or body.get("external_reference")
    if not reference:
        raise HTTPException(status_code=400, detail="Référence absente.")

    stmt = select(Payment).where(
        (Payment.provider_ref == reference) | (Payment.id == reference)
    )
    payment = (await db.execute(stmt)).scalar_one_or_none()
    if payment is None:
        # 200 volontaire : un 404 inviterait Campay à réessayer indéfiniment
        # une référence qui ne nous concerne pas.
        logger.info("Webhook Campay pour une référence inconnue : %s", reference)
        return {"ok": True}

    if payment.status != "PENDING" or not payment.provider_ref:
        return {"ok": True}

    try:
        res = await campay.transaction_status(payment.provider_ref)
    except CampayError as e:
        logger.warning("Webhook : statut Campay indisponible (%s) : %s", reference, e)
        raise HTTPException(status_code=503, detail="Réessayez.")

    payment.status = res.get("status", "PENDING")
    await db.commit()
    await _apply_credit(db, payment)
    return {"ok": True}
