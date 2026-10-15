"""Journal central des erreurs — écriture, regroupement, garde admin, cycle
« consigner puis vider », et alerte au seuil.

Ce qui est MESURÉ, jamais supposé :
  • `log_error` écrit une ligne, d'où qu'on l'appelle ;
  • deux occurrences d'un même défaut partagent leur `fingerprint` (regroupement)
    et deux défauts distincts non ;
  • `require_admin` bloque un non-admin (403) et laisse passer l'admin ;
  • on NE supprime PAS un log `new` (409) — il faut d'abord marquer traité ;
  • l'alerte part au franchissement du seuil, puis se tait (throttle).

Exécution :  backend/venv/Scripts/python.exe backend/tests/test_error_logs.py
Requiert PostgreSQL et la migration 0005.
"""
from __future__ import annotations
import asyncio
import sys
import uuid

import racine  # noqa: F401  -- met backend/ sur le chemin

from fastapi import HTTPException                         # noqa: E402
from sqlalchemy import delete, func, select              # noqa: E402
from sqlalchemy.ext.asyncio import (                      # noqa: E402
    AsyncSession, async_sessionmaker, create_async_engine,
)
from sqlalchemy.pool import NullPool                      # noqa: E402

from app.core.database import DATABASE_URL                # noqa: E402
from app.core.security import require_admin               # noqa: E402
from app.models import ErrorLog, STATUS_HANDLED, STATUS_NEW, User  # noqa: E402
from app.services import error_log as svc                 # noqa: E402
from app.api.logs import (                                 # noqa: E402
    TargetBody, delete_logs, list_groups, mark_handled,
)

_eng = create_async_engine(DATABASE_URL, poolclass=NullPool)
async_session = async_sessionmaker(_eng, class_=AsyncSession, expire_on_commit=False)

TOKEN = f"ERRLOG-TEST-{uuid.uuid4().hex[:8]}"

_checks: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    _checks.append((bool(cond), label))
    print(f"  {'OK ' if cond else 'XX '} {label}")


async def main() -> None:
    # ── Comptes de test ───────────────────────────────────────────────────────
    admin = User(email=f"admin-{TOKEN}@t.io", plan="admin")
    normal = User(email=f"user-{TOKEN}@t.io", plan="free")
    async with async_session() as db:
        db.add_all([admin, normal])
        await db.commit()
        admin_id, normal_id = admin.id, normal.id

    # ── 1. log_error écrit une ligne ──────────────────────────────────────────
    await svc.log_error("frontend", f"{TOKEN} boom", level="error",
                        stack="Trace\n at x", context={"url": "/x"},
                        location="Hero", trigger_alert=False)
    async with async_session() as db:
        row = (await db.execute(
            select(ErrorLog).where(ErrorLog.message.like(f"%{TOKEN} boom%"))
        )).scalars().first()
    check(row is not None and row.source == "frontend" and row.stack is not None,
          "log_error écrit une ligne (source + pile)")

    # ── 2. fingerprint : stable et discriminant ───────────────────────────────
    fp_a1 = svc.fingerprint("frontend", "error", "hello", "L")
    fp_a2 = svc.fingerprint("frontend", "error", "hello", "L")
    fp_b = svc.fingerprint("frontend", "error", "world", "L")
    check(fp_a1 == fp_a2, "fingerprint stable pour un même défaut")
    check(fp_a1 != fp_b, "fingerprint distingue deux défauts")

    # ── 3. Regroupement : 3 occurrences d'une même empreinte ──────────────────
    grp_fp = svc.fingerprint("backend", "error", f"{TOKEN} grp", "L")
    async with async_session() as db:
        for _ in range(3):
            db.add(ErrorLog(source="backend", level="error",
                            message=f"{TOKEN} grp", fingerprint=grp_fp,
                            status=STATUS_NEW))
        await db.commit()
        res = await list_groups(source="backend", q=TOKEN, _admin=admin, db=db)
    grp = next((g for g in res["groups"] if g["fingerprint"] == grp_fp), None)
    check(grp is not None and grp["count"] == 3 and grp["status"] == STATUS_NEW,
          "la liste regroupe 3 occurrences en une entrée (count=3, new)")

    # ── 4. Garde admin ────────────────────────────────────────────────────────
    blocked = False
    try:
        await require_admin(user=normal)
    except HTTPException as e:
        blocked = (e.status_code == 403)
    check(blocked, "require_admin refuse un non-admin (403)")
    check(await require_admin(user=admin) is admin, "require_admin laisse passer l'admin")

    # ── 5. Cycle : suppression REFUSÉE tant que `new`, permise une fois traité ─
    refused = False
    async with async_session() as db:
        try:
            await delete_logs(TargetBody(fingerprints=[grp_fp]), _admin=admin, db=db)
        except HTTPException as e:
            refused = (e.status_code == 409)
    check(refused, "suppression d'un log `new` refusée (409)")

    async with async_session() as db:
        r = await mark_handled(TargetBody(fingerprints=[grp_fp]), _admin=admin, db=db)
    check(r["handled"] == 3, "marquage `traité` sur les 3 occurrences")

    async with async_session() as db:
        r = await delete_logs(TargetBody(fingerprints=[grp_fp]), _admin=admin, db=db)
    check(r["deleted"] == 3, "suppression permise une fois `traité`")

    # ── 6. Alerte au seuil, puis throttle ─────────────────────────────────────
    svc._last_alert_at = 0.0
    async with async_session() as db:
        current_new = await db.scalar(
            select(func.count()).select_from(ErrorLog).where(ErrorLog.status == STATUS_NEW)
        )
    orig_threshold = svc.ERROR_LOG_ALERT_THRESHOLD
    svc.ERROR_LOG_ALERT_THRESHOLD = (current_new or 0) + 3   # nos 3 franchiront

    sent: list[str] = []
    import app.core.email as email_mod
    orig_send = email_mod.send_plain

    async def fake_send(to, subject, body):   # noqa: ANN001
        sent.append(subject)

    email_mod.send_plain = fake_send
    try:
        async with async_session() as db:
            for i in range(3):
                db.add(ErrorLog(source="backend", level="error",
                                message=f"{TOKEN} alert{i}",
                                fingerprint=svc.fingerprint("backend", "error", f"a{i}", "L"),
                                status=STATUS_NEW))
            await db.commit()
            first = await svc.maybe_alert_admins(db)
            second = await svc.maybe_alert_admins(db)
        check(first is True and len(sent) == 1, "alerte envoyée au franchissement du seuil")
        check(second is False and len(sent) == 1, "alerte throttlée ensuite (pas de spam)")
    finally:
        svc.ERROR_LOG_ALERT_THRESHOLD = orig_threshold
        email_mod.send_plain = orig_send

    # ── Nettoyage ─────────────────────────────────────────────────────────────
    async with async_session() as db:
        await db.execute(delete(ErrorLog).where(ErrorLog.message.like(f"%{TOKEN}%")))
        for uid in (admin_id, normal_id):
            u = await db.get(User, uid)
            if u:
                await db.delete(u)
        await db.commit()
    await _eng.dispose()

    # ── Verdict ───────────────────────────────────────────────────────────────
    passed = sum(1 for ok, _ in _checks if ok)
    total = len(_checks)
    print(f"\n{passed}/{total} contrôles")
    if passed != total:
        print("ÉCHECS :", [lbl for ok, lbl in _checks if not ok])
        sys.exit(1)
    print("OK — journal des erreurs conforme")


async def _purger() -> None:
    """Filet de sécurité : un test interrompu ne laisse ni admin (destinataire
    des alertes) ni erreurs de test en base."""
    async with async_session() as db:
        await db.execute(delete(ErrorLog).where(ErrorLog.message.like(f"%{TOKEN}%")))
        await db.execute(delete(User).where(User.email.like(f"%{TOKEN}%")))
        await db.commit()
    await _eng.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        asyncio.run(_purger())
