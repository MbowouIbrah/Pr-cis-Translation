"""Administration des comptes — promotion d'un testeur, et les garde-fous.

Ce qui est MESURÉ :
  • `list_users` renvoie les comptes et la liste blanche des plans attribuables ;
  • `change_plan` promeut un compte (free -> starter) : plan ET limite de stockage
    suivent, et la priorité vendue monte avec ;
  • un plan hors liste blanche est REFUSÉ (400) — pas de droit fantôme ;
  • un admin NE PEUT PAS changer son propre plan (400) — anti-verrouillage ;
  • un compte inexistant -> 404.

Exécution :  backend/venv/Scripts/python.exe backend/tests/test_admin_users.py
Requiert PostgreSQL.
"""
from __future__ import annotations
import asyncio
import sys
import uuid

import racine  # noqa: F401  -- met backend/ sur le chemin

from fastapi import HTTPException                            # noqa: E402
from sqlalchemy import delete                                # noqa: E402
from sqlalchemy.ext.asyncio import (                         # noqa: E402
    AsyncSession, async_sessionmaker, create_async_engine,
)
from sqlalchemy.pool import NullPool                         # noqa: E402

from app.core.database import DATABASE_URL                   # noqa: E402
from app.models import User, get_plan_priority, get_plan_storage  # noqa: E402
from app.api.admin_users import PlanBody, change_plan, list_users  # noqa: E402

_eng = create_async_engine(DATABASE_URL, poolclass=NullPool)
async_session = async_sessionmaker(_eng, class_=AsyncSession, expire_on_commit=False)

TOKEN = f"ADMINUSR-{uuid.uuid4().hex[:8]}"

_checks: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    _checks.append((bool(cond), label))
    print(f"  {'OK ' if cond else 'XX '} {label}")


async def main() -> None:
    admin = User(email=f"admin-{TOKEN}@t.io", plan="admin")
    tester = User(email=f"tester-{TOKEN}@t.io", plan="free")
    async with async_session() as db:
        db.add_all([admin, tester])
        await db.commit()
        admin_id, tester_id = admin.id, tester.id

    # ── 1. Liste : le compte est là, avec la liste blanche des plans ──────────
    async with async_session() as db:
        res = await list_users(q=TOKEN, _admin=admin, db=db)
    emails = {u["email"] for u in res["users"]}
    check(f"tester-{TOKEN}@t.io" in emails, "list_users renvoie le compte cherché")
    check("starter" in res["assignable_plans"] and "free" in res["assignable_plans"],
          "list_users expose la liste blanche des plans")

    # ── 2. Promotion free -> starter : plan, stockage et priorité suivent ──────
    async with async_session() as db:
        r = await change_plan(tester_id, PlanBody(plan="starter"), admin=admin, db=db)
    check(r["ok"] and r["user"]["plan"] == "starter", "change_plan promeut free -> starter")
    check(r["user"]["priority"] == get_plan_priority("starter") > get_plan_priority("free"),
          "la priorité vendue monte avec le plan")
    async with async_session() as db:
        u = await db.get(User, tester_id)
        check(u is not None and u.plan == "starter", "le plan est persisté")
        check(u is not None and u.storage_limit == get_plan_storage("starter"),
              "la limite de stockage suit le plan")

    # ── 3. Plan hors liste blanche -> 400 ──────────────────────────────────────
    bad = False
    async with async_session() as db:
        try:
            await change_plan(tester_id, PlanBody(plan="platinum"), admin=admin, db=db)
        except HTTPException as e:
            bad = (e.status_code == 400)
    check(bad, "plan inconnu refusé (400) — pas de droit fantôme")

    # ── 4. Un admin ne change pas son PROPRE plan -> 400 ───────────────────────
    self_blocked = False
    async with async_session() as db:
        try:
            await change_plan(admin_id, PlanBody(plan="free"), admin=admin, db=db)
        except HTTPException as e:
            self_blocked = (e.status_code == 400)
    check(self_blocked, "un admin ne peut pas se rétrograder lui-même (400)")
    async with async_session() as db:
        a = await db.get(User, admin_id)
        check(a is not None and a.plan == "admin", "le plan de l'admin est intact")

    # ── 5. Compte inexistant -> 404 ────────────────────────────────────────────
    missing = False
    async with async_session() as db:
        try:
            await change_plan("does-not-exist-" + TOKEN, PlanBody(plan="pro"),
                              admin=admin, db=db)
        except HTTPException as e:
            missing = (e.status_code == 404)
    check(missing, "compte introuvable -> 404")

    # ── Nettoyage ─────────────────────────────────────────────────────────────
    async with async_session() as db:
        await db.execute(delete(User).where(User.email.like(f"%{TOKEN}%")))
        await db.commit()
    await _eng.dispose()

    passed = sum(1 for ok, _ in _checks if ok)
    total = len(_checks)
    print(f"\n{passed}/{total} contrôles")
    if passed != total:
        print("ÉCHECS :", [lbl for ok, lbl in _checks if not ok])
        sys.exit(1)
    print("OK — administration des comptes conforme")


async def _purger() -> None:
    """Filet de sécurité : un test interrompu ne laisse pas d'admin en base
    (l'alerte du journal d'erreurs part à TOUS les admins)."""
    async with async_session() as db:
        await db.execute(delete(User).where(User.email.like(f"%{TOKEN}%")))
        await db.commit()
    await _eng.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        asyncio.run(_purger())
