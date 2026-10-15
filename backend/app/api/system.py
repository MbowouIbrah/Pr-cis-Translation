"""Routes de service : sante, quota de stockage, grille tarifaire.

Elles ne traduisent rien et ne touchent a aucun moteur ; elles renseignent le
client sur l'etat du service et sur ce qu'il a le droit de faire.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.pricing import (CURRENCY, CURRENCY_DECIMALS, PLAN_RANK,
                              SELF_SERVE_PLANS, page_price,
                              plan_price, zone_for_country)
from app.core.security import require_auth
from app.models import (PLAN_LABELS, User, get_plan_monthly_pages,
                        get_plan_priority, get_plan_storage)
from app.rate_limit import rate_limit_decorator
from app import versions

router = APIRouter(tags=["Service"])


@router.get("/health")
@rate_limit_decorator("10/minute")
async def health_check(request: Request):
    """État du service et versions de ses composants.

    Les versions y figurent parce que c'est la PREMIÈRE chose qu'on interroge
    en exploitation : « quelle version tourne sur cette machine ? ». La deviner
    depuis un tag git suppose que le déploiement corresponde au dépôt.
    """
    return {
        "status": "healthy",
        "service": "Précis Translator API",
        "versions": versions.toutes(),
    }


@router.get("/api/user/storage")
async def user_storage(
    user: User = Depends(require_auth),
):
    return {
        "used": user.storage_used,
        # Dérivé du plan, jamais de la colonne — même raison que dans
        # `_user_response` : c'est `get_plan_storage` qui arbitre à l'écriture.
        "limit": get_plan_storage(user.plan),
        "plan": user.plan,
    }

@router.get("/api/pricing")
async def pricing(request: Request, country: str | None = None):
    """Grille tarifaire pour la zone de l'appelant.

    Le frontend n'embarque AUCUN prix : il affiche ce que cette route renvoie.
    Dupliquer la grille dans `PricingCards.tsx`, c'était garantir qu'un jour la
    carte annoncerait un montant que l'écran de paiement ne pratiquerait plus.

    La localisation vient de l'en-tête posé par le proxy (`CF-IPCountry` chez
    Cloudflare). Sans proxy géo, il n'y a pas de pays : on retombe alors sur le
    tarif PLEIN. Se tromper en faveur du client sur une remise de pouvoir
    d'achat serait une perte sèche et silencieuse ; se tromper en sa défaveur
    est visible et se corrige.

    `country` en paramètre ne sert qu'à la mise au point et aux tests — il est
    fourni par le client, donc n'importe qui peut réclamer la zone la moins
    chère. Ce n'est PAS un contrôle : au moment d'encaisser, la zone devra être
    reconfirmée côté serveur à partir du moyen de paiement réellement utilisé.
    """
    detected = (
        country
        or request.headers.get("CF-IPCountry")
        or request.headers.get("X-Country")
    )
    zone = zone_for_country(detected)
    currency = CURRENCY[zone]

    return {
        "zone": zone,
        "currency": currency,
        "decimals": CURRENCY_DECIMALS[currency],
        "page_price": page_price(zone),
        "plans": [
            {
                "key": key,
                "label": PLAN_LABELS.get(key, key),
                "monthly": plan_price(key, zone, annual=False),
                "annual": plan_price(key, zone, annual=True),
                "monthly_pages": get_plan_monthly_pages(key),
                "storage": get_plan_storage(key),
                # Niveau de vitesse VENDU (la carte l'affiche). Son application
                # réelle attend le chantier « file de priorité ».
                "priority": get_plan_priority(key),
                # Grille d'achat : rang (None = hors grille) et vente en ligne.
                "rank": PLAN_RANK.get(key),
                "for_sale": key in SELF_SERVE_PLANS,
            }
            for key in ("free", "starter", "pro", "enterprise")
        ],
    }
