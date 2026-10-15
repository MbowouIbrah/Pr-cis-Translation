"""Abonnements Starter / Pro — achat, renouvellement, échéance.

Campay ne prélève jamais de lui-même : un abonnement est un paiement qui
ouvre (ou prolonge) une ÉCHÉANCE, et le compte revient au Gratuit une fois
celle-ci passée.

Ce qui est MESURÉ :
  • le prix vient de `pricing.py` (mensuel, annuel = 12 × le mensuel affiché),
    jamais du navigateur ; Entreprise / Gratuit ne s'achètent pas ;
  • un achat qui ferait PERDRE des droits est refusé sans rien écrire ni
    appeler (Pro qui achète Starter, Pro attribué sans échéance) ;
  • le paiement réussi pose le plan et l'échéance UNE seule fois, sans toucher
    au solde de pages ; un renouvellement anticipé s'AJOUTE à ce qui reste ;
    deux renouvellements crédités en même temps comptent tous les deux ;
  • l'échéance passée ramène le compte au Gratuit au premier appel authentifié,
    et la liste d'administration ne montre pas de plan échu ;
  • un changement de plan reporte le reste payé de l'ancien, au prorata des prix ;
  • un crédit qui rétrograderait le compte n'est pas appliqué mais marqué dû ;
  • un second achat de forfait pendant qu'un premier attend est refusé ;
  • le quota de pages court de date à date depuis la souscription ;
  • le moyen choisi est enregistré sur le paiement.

Exécution :  backend/venv/Scripts/python.exe backend/tests/test_abonnements.py
PostgreSQL local requis, migration 0009.
"""
from __future__ import annotations
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import racine  # noqa: F401  -- met backend/ sur le chemin

from sqlalchemy import select                            # noqa: E402
from sqlalchemy.ext.asyncio import (                     # noqa: E402
    create_async_engine, async_sessionmaker, AsyncSession,
)
from sqlalchemy.pool import NullPool                     # noqa: E402
from fastapi.testclient import TestClient                # noqa: E402

from app.core.database import DATABASE_URL               # noqa: E402
from app.models import (User, Payment, UsageRecord,     # noqa: E402
                        KIND_PLAN, get_plan_storage)
from app import config as cfg                            # noqa: E402
from app import create_app                               # noqa: E402
from app.core.security import create_access_token        # noqa: E402
from app.core.payment_gateway import campay              # noqa: E402
from app.core.pricing import plan_price, ZONE_AFRICA     # noqa: E402
from app.api.payments import _apply_credit               # noqa: E402
from app.services.usage import (add_months as _add_months,  # noqa: E402
                                quota_period, pages_used_this_month)

_eng = create_async_engine(DATABASE_URL, poolclass=NullPool)
async_session = async_sessionmaker(_eng, class_=AsyncSession,
                                   expire_on_commit=False)

PHONE = "670000000"   # MTN


async def _utilisateur(plan="free", expires=None, credits=0):
    u = User(email=f"abo-{uuid.uuid4().hex[:10]}@example.com", plan=plan,
             plan_expires_at=expires, email_verified=True, page_credits=credits)
    async with async_session() as db:
        db.add(u)
        await db.commit()
    return u


async def _compte(user_id):
    async with async_session() as db:
        return await db.get(User, user_id)


async def _paiements(user_id):
    async with async_session() as db:
        return (await db.execute(select(Payment).where(
            Payment.user_id == user_id).order_by(Payment.created_at))).scalars().all()


async def _supprimer(ids):
    async with async_session() as db:
        for uid in ids:
            for p in (await db.execute(select(Payment).where(
                    Payment.user_id == uid))).scalars():
                await db.delete(p)
            for r in (await db.execute(select(UsageRecord).where(
                    UsageRecord.user_id == uid))).scalars():
                await db.delete(r)
            obj = await db.get(User, uid)
            if obj:
                await db.delete(obj)
        await db.commit()


def _proche(a, b, tol=timedelta(minutes=2)):
    return a is not None and b is not None and abs(a - b) <= tol


async def _credit_paiement(user_id, plan, months, status="SUCCESSFUL"):
    """Paiement de plan déjà validé chez le prestataire, à créditer."""
    async with async_session() as db:
        p = Payment(user_id=user_id, pages=0, amount=1, currency="XAF", zone="A",
                    status=status, provider="campay", plan=plan, months=months,
                    provider_ref="ref-" + uuid.uuid4().hex)
        db.add(p)
        await db.commit()
    return p


async def _crediter(payment):
    async with async_session() as db:
        p = await db.get(Payment, payment.id)
        await _apply_credit(db, p)


def check_dates(ok):
    d = datetime(2027, 1, 31, 10, tzinfo=timezone.utc)
    ok("DATES   31 janv. + 1 mois = 28 févr. ; + 12 mois = 31 janv. suivant",
       _add_months(d, 1).date().isoformat() == "2027-02-28"
       and _add_months(d, 12).date().isoformat() == "2028-01-31",
       f"{_add_months(d, 1)} {_add_months(d, 12)}")

    u = User(plan="starter", plan_started_at=d, plan_expires_at=_add_months(d, 12))
    p = quota_period(u, datetime(2027, 3, 15, tzinfo=timezone.utc))
    ok("QUOTA   souscrit le 31/01 : période du 28/02 au 31/03 (pas de dérive)",
       p == (datetime(2027, 2, 28, 10, tzinfo=timezone.utc),
             datetime(2027, 3, 31, 10, tzinfo=timezone.utc)), f"{p}")
    p = quota_period(u, datetime(2027, 1, 31, 10, tzinfo=timezone.utc))
    ok("QUOTA   le jour même de la souscription : première période",
       p == (d, _add_months(d, 1)), f"{p}")
    p = quota_period(User(plan="pro"), datetime(2027, 3, 15, 8, tzinfo=timezone.utc))
    ok("QUOTA   plan sans souscription : mois civil",
       p == (datetime(2027, 3, 1, tzinfo=timezone.utc),
             datetime(2027, 4, 1, tzinfo=timezone.utc)), f"{p}")


async def check_routes(client, ok, a_nettoyer):
    appels = []

    async def faux_collect(**kw):
        appels.append(kw)
        return {"reference": "ref-" + uuid.uuid4().hex, "ussd_code": "*126#",
                "operator": "MTN"}

    async def faux_statut(ref):
        return {"status": "SUCCESSFUL"}

    def ent(u, pays="CM"):
        return {"X-API-Key": cfg.FRONTEND_API_KEY, "X-Country": pays,
                "Authorization": "Bearer " + create_access_token(u.id, u.email)}

    vrais = (campay.collect, campay.transaction_status, campay.username, campay.password)
    campay.collect, campay.transaction_status = faux_collect, faux_statut
    campay.username, campay.password = "u", "p"
    saved_env = os.environ.pop("PAYMENT_METHODS", None)
    try:
        u = await _utilisateur(credits=3)
        a_nettoyer.append(u.id)

        r = client.post("/api/payments/plan/quote",
                        json={"plan": "starter"}, headers=ent(u))
        q = r.json()
        ok("DEVIS   Starter mensuel : prix de pricing.py, 1 mois",
           r.status_code == 200 and q.get("amount") == plan_price("starter", ZONE_AFRICA)
           and q.get("months") == 1 and q.get("currency") == "XAF", f"{r.status_code} {q}")
        r = client.post("/api/payments/plan/quote",
                        json={"plan": "pro", "annual": True}, headers=ent(u))
        q = r.json()
        ok("DEVIS   Pro annuel : 12 x le mensuel annuel affiché",
           r.status_code == 200 and q.get("months") == 12
           and q.get("amount") == 12 * plan_price("pro", ZONE_AFRICA, annual=True), f"{q}")

        for plan in ("enterprise", "free", "admin", "inconnu"):
            r = client.post("/api/payments/plan",
                            json={"plan": plan, "phone": PHONE}, headers=ent(u))
            ok(f"REFUS   forfait '{plan}' : 422, rien écrit ni appelé",
               r.status_code == 422 and not appels and not await _paiements(u.id),
               f"{r.status_code}")

        r = client.post("/api/payments/plan", json={"plan": "starter"}, headers=ent(u))
        ok("REFUS   mobile money sans numéro : 422, rien appelé",
           r.status_code == 422 and not appels, f"{r.status_code}")

        # ── Achat Starter mensuel ─────────────────────────────────────────
        r = client.post("/api/payments/plan",
                        json={"plan": "starter", "method": "mtn_momo", "phone": PHONE},
                        headers=ent(u))
        ps = await _paiements(u.id)
        ok("ACHAT   Starter : Campay appelé au prix serveur, paiement tracé",
           r.status_code == 200 and len(appels) == 1
           and appels[0].get("amount") == plan_price("starter", ZONE_AFRICA)
           and len(ps) == 1 and ps[0].plan == "starter" and ps[0].months == 1
           and ps[0].pages == 0 and ps[0].method == "mtn_momo",
           f"{r.status_code} {r.text[:120]} {appels}")
        ok("ACHAT   compte inchangé tant que le paiement est en attente",
           (await _compte(u.id)).plan == "free")

        avant = datetime.now(timezone.utc)
        r = client.get(f"/api/payments/{ps[0].id}", headers=ent(u))
        c = await _compte(u.id)
        echeance = c.plan_expires_at
        ok("CREDIT  réussite : plan Starter, échéance à +1 mois, solde de pages intact",
           r.json().get("status") == "SUCCESSFUL" and r.json().get("plan") == "starter"
           and c.plan == "starter" and _proche(echeance, _add_months(avant, 1))
           and _proche(c.plan_started_at, avant) and c.page_credits == 3,
           f"{r.json()} {c.plan} {echeance} credits={c.page_credits}")

        await _crediter(ps[0])
        r = client.get(f"/api/payments/{ps[0].id}", headers=ent(u))
        ok("CREDIT  un seul crédit par paiement (webhook + interrogation en double)",
           (await _compte(u.id)).plan_expires_at == echeance)

        r = client.get("/api/auth/me", headers=ent(u))
        ok("PROFIL  /me expose l'échéance de l'abonnement",
           r.status_code == 200 and r.json().get("plan_expires_at") is not None,
           f"{r.status_code} {r.text[:120]}")

        # ── Renouvellement anticipé : la durée s'ajoute ────────────────────
        p2 = await _credit_paiement(u.id, "starter", 1)
        await _crediter(p2)
        c = await _compte(u.id)
        ok("RENOUV. anticipé : +1 mois À PARTIR de l'échéance en cours, même période",
           c.plan == "starter" and _proche(c.plan_expires_at, _add_months(echeance, 1))
           and _proche(c.plan_started_at, avant),
           f"{c.plan_expires_at} attendu {_add_months(echeance, 1)}")

        # Deux renouvellements crédités en même temps : les deux comptent.
        base = c.plan_expires_at
        p3, p4 = (await _credit_paiement(u.id, "starter", 1),
                  await _credit_paiement(u.id, "starter", 1))
        await asyncio.gather(_crediter(p3), _crediter(p4))
        c = await _compte(u.id)
        ok("RENOUV. concurrents : deux paiements = deux mois (verrou de ligne)",
           _proche(c.plan_expires_at, _add_months(base, 2)),
           f"{c.plan_expires_at} attendu {_add_months(base, 2)}")

        # ── Montée en gamme : Pro annuel commence maintenant ───────────────
        reste = c.plan_expires_at
        n = len(appels)
        r = client.post("/api/payments/plan",
                        json={"plan": "pro", "annual": True, "phone": PHONE},
                        headers=ent(u))
        pid = r.json().get("payment_id")
        avant = datetime.now(timezone.utc)
        client.get(f"/api/payments/{pid}", headers=ent(u))
        c = await _compte(u.id)
        report = (reste - avant) * (plan_price("starter", ZONE_AFRICA)
                                    / plan_price("pro", ZONE_AFRICA))
        attendu = _add_months(avant + report, 12)
        ok("MONTEE  Starter -> Pro annuel : +12 mois + reste Starter au prorata",
           r.status_code == 200 and len(appels) == n + 1
           and appels[-1].get("amount") == 12 * plan_price("pro", ZONE_AFRICA, annual=True)
           and c.plan == "pro" and report > timedelta(days=7)
           and _proche(c.plan_expires_at, attendu) and _proche(c.plan_started_at, avant),
           f"{r.status_code} {c.plan} {c.plan_expires_at} attendu {attendu}")

        # ── Achats qui feraient perdre des droits ──────────────────────────
        n, nb = len(appels), len(await _paiements(u.id))
        r = client.post("/api/payments/plan",
                        json={"plan": "starter", "phone": PHONE}, headers=ent(u))
        ok("REFUS   Pro actif qui achète Starter : 409, rien écrit ni appelé",
           r.status_code == 409 and len(appels) == n
           and len(await _paiements(u.id)) == nb, f"{r.status_code}")

        v = await _utilisateur(plan="pro")          # attribué, sans échéance
        e = await _utilisateur(plan="enterprise")
        a_nettoyer += [v.id, e.id]
        r1 = client.post("/api/payments/plan",
                         json={"plan": "pro", "phone": PHONE}, headers=ent(v))
        r2 = client.post("/api/payments/plan",
                         json={"plan": "pro", "phone": PHONE}, headers=ent(e))
        ok("REFUS   Pro sans échéance / Entreprise : 409, rien appelé",
           r1.status_code == 409 and r2.status_code == 409 and len(appels) == n,
           f"{r1.status_code} {r2.status_code}")

        # Passé admin entre l'invite et la validation : pas rétrogradé.
        w = await _utilisateur(plan="admin")
        a_nettoyer.append(w.id)
        pw = await _credit_paiement(w.id, "starter", 1)
        await _crediter(pw)
        r = client.get(f"/api/payments/{pw.id}", headers=ent(w))
        ok("CREDIT  compte admin au moment du crédit : plan intact, paiement dû",
           (await _compte(w.id)).plan == "admin" and r.json().get("refund_due") is True,
           f"{r.text[:160]}")

        # Pro acheté entre l'invite Starter et sa validation : pas rétrogradé.
        fin = datetime.now(timezone.utc) + timedelta(days=20)
        s = await _utilisateur(plan="pro", expires=fin)
        a_nettoyer.append(s.id)
        ps_ = await _credit_paiement(s.id, "starter", 1)
        await _crediter(ps_)
        cs = await _compte(s.id)
        async with async_session() as db:
            du = (await db.get(Payment, ps_.id)).refund_due
        ok("CREDIT  Starter validé sur un compte Pro : Pro intact, paiement dû",
           cs.plan == "pro" and _proche(cs.plan_expires_at, fin) and du,
           f"{cs.plan} {cs.plan_expires_at} du={du}")

        # Second achat pendant qu'une invite attend : refusé.
        k = await _utilisateur()
        a_nettoyer.append(k.id)
        n = len(appels)
        r1 = client.post("/api/payments/plan",
                         json={"plan": "starter", "phone": PHONE}, headers=ent(k))
        r2 = client.post("/api/payments/plan",
                         json={"plan": "pro", "phone": PHONE}, headers=ent(k))
        ok("REFUS   second achat de forfait pendant une invite en attente : 409",
           r1.status_code == 200 and r2.status_code == 409 and len(appels) == n + 1
           and len(await _paiements(k.id)) == 1, f"{r1.status_code} {r2.status_code}")

        # Paiement échoué : rien.
        x = await _utilisateur()
        a_nettoyer.append(x.id)
        await _crediter(await _credit_paiement(x.id, "pro", 1, status="FAILED"))
        ok("CREDIT  paiement échoué : compte toujours Gratuit",
           (await _compte(x.id)).plan == "free")

        # ── Échéance ───────────────────────────────────────────────────────
        y = await _utilisateur(plan="pro",
                               expires=datetime.now(timezone.utc) - timedelta(minutes=1))
        async with async_session() as db:
            (await db.get(User, y.id)).plan_started_at = datetime.now(timezone.utc)
            await db.commit()
        z = await _utilisateur(plan="pro",
                               expires=datetime.now(timezone.utc) + timedelta(days=3))
        a_nettoyer += [y.id, z.id]
        r = client.get("/api/auth/me", headers=ent(y))
        cy = await _compte(y.id)
        ok("ECHEANCE passée : retour au Gratuit au premier appel, sans échéance",
           r.status_code == 200 and r.json().get("plan") == "free"
           and cy.plan == "free" and cy.plan_expires_at is None
           and cy.plan_started_at is None and cy.storage_limit == get_plan_storage("free"),
           f"{r.json().get('plan')} {cy.plan} {cy.plan_expires_at}")
        client.get("/api/auth/me", headers=ent(z))
        ok("ECHEANCE future : plan conservé",
           (await _compte(z.id)).plan == "pro")

        # Liste d'administration : le compte échu n'est jamais passé par
        # require_auth, il ne doit pas y apparaître avec son ancien plan.
        y2 = await _utilisateur(plan="starter",
                                expires=datetime.now(timezone.utc) - timedelta(hours=1))
        adm = await _utilisateur(plan="admin")
        a_nettoyer += [y2.id, adm.id]
        r = client.get("/api/admin/users", params={"q": y2.email}, headers=ent(adm))
        vus = [x.get("plan") for x in r.json().get("users", [])]
        ok("ECHEANCE liste admin : un abonnement échu s'affiche Gratuit",
           r.status_code == 200 and vus == ["free"], f"{r.status_code} {vus}")

        # Quota : seules les pages de la période en cours comptent.
        q = await _utilisateur(plan="starter",
                               expires=datetime.now(timezone.utc) + timedelta(days=20))
        a_nettoyer.append(q.id)
        maintenant = datetime.now(timezone.utc)
        async with async_session() as db:
            cq = await db.get(User, q.id)
            # Souscrit il y a une heure : la ligne d'il y a deux heures est du
            # même mois civil (sauf dans les deux premières heures du mois)
            # mais d'avant la souscription.
            cq.plan_started_at = maintenant - timedelta(hours=1)
            db.add_all([
                UsageRecord(user_id=q.id, kind=KIND_PLAN, pages=5,
                            created_at=maintenant - timedelta(hours=2)),
                UsageRecord(user_id=q.id, kind=KIND_PLAN, pages=3,
                            created_at=maintenant - timedelta(minutes=10)),
            ])
            await db.commit()
            used = await pages_used_this_month(db, cq)
        ok("QUOTA   pages d'avant la souscription non comptées",
           used == 3, f"{used}")

        # Le moyen est désormais tracé aussi sur l'achat de pages.
        r = client.post("/api/payments/collect",
                        json={"pages": 2, "method": "orange_money", "phone": "690000000"},
                        headers=ent(x))
        ps = await _paiements(x.id)
        ok("PAGES   le moyen choisi est enregistré sur le paiement",
           r.status_code == 200 and ps[-1].method == "orange_money" and ps[-1].plan is None,
           f"{r.status_code} {[p.method for p in ps]}")
    finally:
        campay.collect, campay.transaction_status, campay.username, campay.password = vrais
        if saved_env is not None:
            os.environ["PAYMENT_METHODS"] = saved_env


async def run():
    checks, a_nettoyer = [], []

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    check_dates(ok)
    try:
        with TestClient(create_app()) as client:
            await check_routes(client, ok, a_nettoyer)
    finally:
        await _supprimer(a_nettoyer)

    print()
    for nom, cond, detail in checks:
        print(f"  {'OK  ' if cond else 'ECHEC'}  {nom}"
              + (f"   [{detail}]" if not cond and detail else ""))
    reussis = sum(1 for _, c, _ in checks if c)
    print(f"\n  {reussis}/{len(checks)}\n")
    return reussis == len(checks)


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(run()) else 1)
