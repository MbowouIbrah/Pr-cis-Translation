"""Administration des comptes — RÉSERVÉ à l'admin (`require_admin`).

Pourquoi cet écran existe : le forfait Gratuit reste à une page par mois. Pour
faire essayer Précis à un testeur sans toucher à cette règle, l'admin promeut
son compte à la main (Starter/Pro) le temps du test. C'est le levier « testeurs »
choisi par le propriétaire plutôt qu'un palier gratuit généreux.

Ce module NE fait que lire et changer le `plan`. Il ne crée ni ne supprime de
compte : un compte se crée par l'inscription normale, et le supprimer touche à
ses documents (cascade) — hors de portée de ce petit outil.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_admin
from app.models import (ASSIGNABLE_PLANS, User, get_plan_priority,
                        get_plan_storage)

router = APIRouter(prefix="/api/admin/users", tags=["Administration"])


def _serialize(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "plan": u.plan,
        "priority": get_plan_priority(u.plan),
        "page_credits": u.page_credits,
        "storage_used": u.storage_used,
        "email_verified": u.email_verified,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


@router.get("")
async def list_users(
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Liste des comptes, du plus récent au plus ancien. `q` filtre sur l'e-mail
    ou le nom (sous-chaîne insensible à la casse)."""
    limit = max(1, min(limit, 200))
    conds = []
    if q:
        like = f"%{q}%"
        conds.append(or_(User.email.ilike(like), User.name.ilike(like)))

    stmt = select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
    if conds:
        stmt = stmt.where(*conds)
    rows = (await db.execute(stmt)).scalars().all()

    count_stmt = select(func.count()).select_from(User)
    if conds:
        count_stmt = count_stmt.where(*conds)
    total = await db.scalar(count_stmt)

    return {
        "total": total or 0,
        "assignable_plans": list(ASSIGNABLE_PLANS),
        "users": [_serialize(u) for u in rows],
    }


class PlanBody(BaseModel):
    plan: str = Field(..., min_length=1, max_length=20)


@router.post("/{user_id}/plan")
async def change_plan(
    user_id: str,
    body: PlanBody,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Change le plan d'un compte. Le plan doit appartenir à la liste blanche —
    une valeur libre donnerait des droits fantômes, absents de la grille.

    L'admin ne peut PAS changer son PROPRE plan : se rétrograder soi-même, c'est
    perdre l'accès à cet écran et à tous les autres outils d'administration d'un
    seul clic, sans personne pour le rendre.
    """
    if body.plan not in ASSIGNABLE_PLANS:
        raise HTTPException(status_code=400,
                            detail=f"Plan inconnu : {body.plan}.")
    if user_id == admin.id:
        raise HTTPException(status_code=400,
                            detail="Un admin ne peut pas changer son propre plan.")

    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Compte introuvable.")

    target.plan = body.plan
    # La limite de stockage suit le plan. Les réponses la dérivent déjà de
    # `get_plan_storage`, mais on garde la colonne cohérente pour tout code qui
    # la lirait directement.
    target.storage_limit = get_plan_storage(body.plan)
    await db.commit()
    await db.refresh(target)
    return {"ok": True, "user": _serialize(target)}
