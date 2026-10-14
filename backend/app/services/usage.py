"""Consommation du mois : ce que les quotas comptent, et ce qu'il en reste.

UNE SEULE SOURCE pour la route de traduction (qui refuse) et pour le profil
(qui affiche le reste). Deux calculs séparés finiraient par diverger, et
l'interface annoncerait une traduction que la route refuse.

Tout se lit dans `usage_records`, jamais dans `documents` : un document se
supprime, une consommation non (cf. `app.models.usage`).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (KIND_CREDITS, KIND_FREE_OFFER, KIND_LEGACY, UsageRecord,
                        User, get_plan_monthly_pages, get_plan_page_limit)


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _du_mois(user_id: str):
    return (UsageRecord.user_id == user_id,
            UsageRecord.created_at >= month_start(),
            UsageRecord.refunded.is_(False))


async def free_offer_used(db: AsyncSession, user_id: str) -> bool:
    """La traduction offerte du mois a-t-elle déjà servi ?

    `legacy` compte aussi : avant le registre, tout document du mois
    consommait l'offre."""
    r = await db.execute(
        select(func.count()).select_from(UsageRecord).where(
            *_du_mois(user_id),
            UsageRecord.kind.in_((KIND_FREE_OFFER, KIND_LEGACY)),
        )
    )
    return (r.scalar() or 0) >= 1


async def pages_used_this_month(db: AsyncSession, user_id: str) -> int:
    """Pages consommées ce mois au titre du quota d'un plan.

    Les pages ACHETÉES à l'unité ne sont pas du quota : elles sont payées."""
    r = await db.execute(
        select(func.coalesce(func.sum(UsageRecord.pages), 0)).where(
            *_du_mois(user_id), UsageRecord.kind != KIND_CREDITS,
        )
    )
    return int(r.scalar() or 0)


def record(db: AsyncSession, user_id: str, document_id: str | None,
           kind: str, pages: int) -> UsageRecord:
    """Ajoute une consommation à la session — commitée avec le Document, dans
    la MÊME transaction : l'une sans l'autre serait soit un quota qui ne
    compte pas, soit une consommation fantôme."""
    rec = UsageRecord(user_id=user_id, document_id=document_id,
                      kind=kind, pages=max(1, int(pages)))
    db.add(rec)
    return rec


async def refund_for_document(db: AsyncSession, document_id: str) -> None:
    """Neutralise la consommation d'une traduction ÉCHOUÉE.

    Idempotent : un échec reporté deux fois ne rembourse qu'une fois. Les
    pages achetées reviennent au solde ; l'offre du mois redevient libre."""
    rows = (await db.execute(
        select(UsageRecord).where(UsageRecord.document_id == document_id,
                                  UsageRecord.refunded.is_(False))
        .with_for_update()
    )).scalars().all()
    for rec in rows:
        rec.refunded = True
        if rec.kind == KIND_CREDITS:
            user = await db.get(User, rec.user_id, with_for_update=True)
            if user is not None:
                user.page_credits += rec.pages


async def usage_summary(db: AsyncSession, user: User) -> dict:
    """Ce qu'il reste à l'utilisateur ce mois-ci, tel que la route l'appliquera."""
    plan = user.plan
    if get_plan_page_limit(plan) == 1:
        offert = 0 if await free_offer_used(db, user.id) else 1
        return {
            "kind": "free",
            "free_translations_remaining": offert,
            "page_credits": user.page_credits,
            "resets_at": _mois_suivant().isoformat(),
        }
    quota = get_plan_monthly_pages(plan)
    utilise = await pages_used_this_month(db, user.id)
    return {
        "kind": "plan",
        "monthly_pages": quota,
        "pages_used": utilise,
        "pages_remaining": None if quota is None else max(0, quota - utilise),
        "page_credits": user.page_credits,
        "resets_at": _mois_suivant().isoformat(),
    }


def _mois_suivant() -> datetime:
    debut = month_start()
    if debut.month == 12:
        return debut.replace(year=debut.year + 1, month=1)
    return debut.replace(month=debut.month + 1)
