"""Paiement à la page — montant minimum de l'opérateur (100 F en XAF).

Ce qui est MESURÉ :
  • `billable_pages` atteint le minimum, et pas une page de plus (pour chaque
    taille de 1 à 20 pages) ; hors XAF, rien n'est arrondi ;
  • la ROUTE `/quote` annonce les pages facturées et le montant arrondi ;
  • la ROUTE `/collect` envoie à Campay un montant ≥ minimum, enregistre les
    pages facturées, et facture un document à SA taille (pas à celle envoyée) ;
  • `_apply_credit` débloque le document ET reverse le surplus au compte ;
    un achat d'avance crédite toutes les pages payées.

Exécution :  backend/venv/Scripts/python.exe backend/tests/test_paiement_minimum.py
PostgreSQL local requis (comme test_revue_ultra).
"""
from __future__ import annotations
import asyncio
import sys
import uuid

import racine  # noqa: F401  -- met backend/ sur le chemin

from sqlalchemy import select                            # noqa: E402
from sqlalchemy.ext.asyncio import (                     # noqa: E402
    create_async_engine, async_sessionmaker, AsyncSession,
)
from sqlalchemy.pool import NullPool                     # noqa: E402
from fastapi.testclient import TestClient                # noqa: E402

from app.core.database import DATABASE_URL               # noqa: E402
from app.core.pricing import (CURRENCY, MIN_PAYMENT_AMOUNT,  # noqa: E402
                              ZONE_AFRICA, ZONE_EMERGING, ZONE_GLOBAL,
                              billable_pages, page_price)
from app.models import User, Payment, Document           # noqa: E402
from app import config as cfg                            # noqa: E402
from app import create_app                               # noqa: E402
from app.core.security import create_access_token        # noqa: E402
from app.api import payments as route_payments           # noqa: E402
from app.api.payments import _apply_credit               # noqa: E402

_eng = create_async_engine(DATABASE_URL, poolclass=NullPool)
async_session = async_sessionmaker(_eng, class_=AsyncSession,
                                   expire_on_commit=False)


async def _utilisateur():
    u = User(email=f"minpay-{uuid.uuid4().hex[:10]}@example.com",
             plan="free", email_verified=True, page_credits=0)
    async with async_session() as db:
        db.add(u)
        await db.commit()
    return u


async def _supprimer(user_id):
    async with async_session() as db:
        for p in (await db.execute(select(Payment).where(
                Payment.user_id == user_id))).scalars():
            await db.delete(p)
        obj = await db.get(User, user_id)
        if obj:
            await db.delete(obj)
        await db.commit()


async def _document(user_id, page_count):
    d = Document(user_id=user_id, original_name="d.pdf", source_lang="auto",
                 target_lang="en", original_path="x.pdf", size_bytes=10,
                 status="done", paid=False, page_count=page_count)
    async with async_session() as db:
        db.add(d)
        await db.commit()
    return d


def check_calcul(ok):
    minimum = MIN_PAYMENT_AMOUNT[CURRENCY[ZONE_AFRICA]]
    unit = page_price(ZONE_AFRICA)
    juste = True
    for n in range(1, 21):
        b = billable_pages(ZONE_AFRICA, n)
        # Atteint le minimum, ne facture jamais moins que demandé, et une page
        # de moins serait soit sous le minimum, soit sous la demande.
        if not (b >= n and b * unit >= minimum
                and (b == n or (b - 1) * unit < minimum)):
            juste = False
    ok("CALCUL  zone A : minimum atteint, sans page superflue (1..20)", juste)
    ok("CALCUL  zone A : 1 page -> 2 pages facturees",
       billable_pages(ZONE_AFRICA, 1) == 2, str(billable_pages(ZONE_AFRICA, 1)))
    ok("CALCUL  hors XAF : aucun arrondi",
       billable_pages(ZONE_EMERGING, 1) == 1 and billable_pages(ZONE_GLOBAL, 1) == 1)


async def check_routes(client, ok):
    u = await _utilisateur()
    envoye = []

    async def faux_collect(**kw):
        envoye.append(kw)
        return {"reference": uuid.uuid4().hex, "ussd_code": None,
                "operator": "MTN"}

    vrai = route_payments.campay.collect
    route_payments.campay.collect = faux_collect
    ent = {"X-API-Key": cfg.FRONTEND_API_KEY, "X-Country": "CM",
           "Authorization": "Bearer " + create_access_token(u.id, u.email)}
    try:
        r = client.post("/api/payments/quote", json={"pages": 1}, headers=ent)
        q = r.json()
        ok("ROUTE   /quote 1 page : 2 facturees, 150 F, minimum publie",
           r.status_code == 200 and q.get("billed_pages") == 2
           and q.get("amount") == 150 and q.get("min_amount") == 100, str(q))

        r = client.post("/api/payments/collect",
                        json={"pages": 1, "phone": "237670000000"}, headers=ent)
        ok("ROUTE   /collect 1 page : Campay recoit 150 F",
           r.status_code == 200 and envoye and envoye[-1]["amount"] == 150,
           f"{r.status_code} {envoye[-1:] }")
        async with async_session() as db:
            p = await db.get(Payment, r.json().get("payment_id"))
        ok("ROUTE   /collect enregistre les pages facturees (2)",
           p is not None and p.pages == 2 and p.amount == 150)

        d = await _document(u.id, 3)
        r = client.post("/api/payments/collect",
                        json={"pages": 1, "phone": "237670000000",
                              "document_id": d.id}, headers=ent)
        ok("ROUTE   document de 3 pages annonce a 1 : facture 3 pages (225 F)",
           r.status_code == 200 and envoye[-1]["amount"] == 225,
           f"{r.status_code} {envoye[-1:]}")
    finally:
        route_payments.campay.collect = vrai
        await _supprimer(u.id)


async def check_credit(ok):
    u = await _utilisateur()
    try:
        d = await _document(u.id, 1)
        async with async_session() as db:
            pd = Payment(user_id=u.id, document_id=d.id, pages=2, amount=150,
                         currency="XAF", zone="A", status="SUCCESSFUL",
                         provider_ref=uuid.uuid4().hex)
            pa = Payment(user_id=u.id, pages=2, amount=150, currency="XAF",
                         zone="A", status="SUCCESSFUL",
                         provider_ref=uuid.uuid4().hex)
            db.add_all([pd, pa])
            await db.commit()
        async with async_session() as db:
            await _apply_credit(db, await db.get(Payment, pd.id))
        async with async_session() as db:
            doc = await db.get(Document, d.id)
            solde = (await db.get(User, u.id)).page_credits
        ok("CREDIT  document 1 page paye 2 : debloque + 1 page au compte",
           doc.paid and solde == 1, f"paid={doc.paid} solde={solde}")
        async with async_session() as db:
            await _apply_credit(db, await db.get(Payment, pa.id))
        async with async_session() as db:
            solde = (await db.get(User, u.id)).page_credits
        ok("CREDIT  achat d'avance de 2 pages : +2", solde == 3, f"solde={solde}")
        async with async_session() as db:
            await db.delete(await db.get(Document, d.id))
            await db.commit()
    finally:
        await _supprimer(u.id)


async def run():
    checks = []

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    check_calcul(ok)
    await check_credit(ok)
    with TestClient(create_app()) as client:
        await check_routes(client, ok)

    print()
    for nom, cond, detail in checks:
        print(f"  {'OK  ' if cond else 'ECHEC'}  {nom}"
              + (f"   [{detail}]" if not cond and detail else ""))
    reussis = sum(1 for _, c, _ in checks if c)
    print(f"\n  {reussis}/{len(checks)}\n")
    return reussis == len(checks)


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(run()) else 1)
