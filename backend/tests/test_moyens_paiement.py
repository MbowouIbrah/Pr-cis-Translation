"""Moyens de paiement — registre moyen -> prestataire piloté par la configuration.

Ce qui est MESURÉ :
  • `provider_for` : un moyen n'est disponible que s'il a un prestataire
    CONNU, CONFIGURÉ, et qui encaisse la devise de la zone ; chaque refus a
    sa raison (no_provider, unsupported, not_configured, currency, unknown) ;
  • `method_of_phone` : l'opérateur se déduit du numéro, préfixe inconnu = None ;
  • `PAYMENT_METHODS` change le routage sans toucher au code ;
  • la ROUTE `/methods` liste les trois moyens, la carte grisée par défaut ;
  • la ROUTE `/collect` refuse un moyen indisponible SANS écrire de paiement
    ni appeler le prestataire ; exige un numéro pour le mobile money ; suit
    l'opérateur du NUMÉRO (un numéro Orange sous « MTN » n'ouvre pas Orange
    coupé) ; un ancien client sans `method` passe encore ;
    enregistre le prestataire et sa référence pour un moyen disponible ;
  • la ROUTE `/{id}` interroge le prestataire DU paiement, et aucun autre.

Exécution :  backend/venv/Scripts/python.exe backend/tests/test_moyens_paiement.py
PostgreSQL local requis (comme test_paiement_minimum).
"""
from __future__ import annotations
import asyncio
import os
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
from app.models import User, Payment                     # noqa: E402
from app import config as cfg                            # noqa: E402
from app import create_app                               # noqa: E402
from app.core.security import create_access_token        # noqa: E402
from app.core import payment_methods as pm               # noqa: E402
from app.core.payment_gateway import campay              # noqa: E402

_eng = create_async_engine(DATABASE_URL, poolclass=NullPool)
async_session = async_sessionmaker(_eng, class_=AsyncSession,
                                   expire_on_commit=False)


class _Env:
    """Pose PAYMENT_METHODS et les identifiants Campay le temps d'un bloc."""

    def __init__(self, routing=None, configured=True):
        self.routing, self.configured = routing, configured

    def __enter__(self):
        self.saved = (os.environ.get("PAYMENT_METHODS"), campay.username,
                      campay.password)
        if self.routing is None:
            os.environ.pop("PAYMENT_METHODS", None)
        else:
            os.environ["PAYMENT_METHODS"] = self.routing
        campay.username = "u" if self.configured else ""
        campay.password = "p" if self.configured else ""

    def __exit__(self, *exc):
        env, campay.username, campay.password = self.saved
        if env is None:
            os.environ.pop("PAYMENT_METHODS", None)
        else:
            os.environ["PAYMENT_METHODS"] = env


def check_registre(ok):
    def raison(m, cur="XAF"):
        return pm.provider_for(m, cur)[1]

    with _Env():
        ok("REGISTRE defaut : MTN et Orange via Campay, carte sans prestataire",
           raison(pm.MTN_MOMO) is None and raison(pm.ORANGE_MONEY) is None
           and raison(pm.CARD) == "no_provider",
           str([raison(m) for m in pm.METHODS]))
        ok("REGISTRE devise non encaissee (EUR) : refus 'currency'",
           raison(pm.MTN_MOMO, "EUR") == "currency")
        ok("REGISTRE moyen inconnu : refus 'unknown'", raison("paypal") == "unknown")
    with _Env("mtn_momo=campay, orange_money= ,card=campay"):
        ok("REGISTRE PAYMENT_METHODS reroute (Orange coupe) ; carte -> Campay refusee",
           raison(pm.ORANGE_MONEY) == "no_provider" and raison(pm.CARD) == "unsupported"
           and raison(pm.MTN_MOMO) is None,
           str([raison(m) for m in pm.METHODS]))
    with _Env("mtn_momo=zitopay"):
        ok("REGISTRE prestataire inconnu : refus 'no_provider'",
           raison(pm.MTN_MOMO) == "no_provider")
    with _Env(configured=False):
        ok("REGISTRE identifiants absents : refus 'not_configured'",
           raison(pm.MTN_MOMO) == "not_configured")
    vus = [pm.method_of_phone(n) for n in
           ("670000000", "237 655 00 00 00", "690000000", "+237 685000000", "620000000", "12")]
    ok("REGISTRE operateur deduit du numero (MTN, Orange, inconnu)",
       vus == ["mtn_momo", "orange_money", "orange_money", "orange_money", None, None], str(vus))


async def _utilisateur():
    u = User(email=f"moyens-{uuid.uuid4().hex[:10]}@example.com",
             plan="free", email_verified=True, page_credits=0)
    async with async_session() as db:
        db.add(u)
        await db.commit()
    return u


async def _paiements(user_id):
    async with async_session() as db:
        return (await db.execute(select(Payment).where(
            Payment.user_id == user_id))).scalars().all()


async def _supprimer(user_id):
    async with async_session() as db:
        for p in (await db.execute(select(Payment).where(
                Payment.user_id == user_id))).scalars():
            await db.delete(p)
        obj = await db.get(User, user_id)
        if obj:
            await db.delete(obj)
        await db.commit()


async def check_routes(client, ok):
    u = await _utilisateur()
    appels, statuts = [], []

    async def faux_collect(**kw):
        appels.append(kw)
        return {"reference": "ref-" + uuid.uuid4().hex, "ussd_code": "*126#",
                "operator": "ORANGE"}

    async def faux_statut(ref):
        statuts.append(ref)
        return {"status": "SUCCESSFUL"}

    vrais = (campay.collect, campay.transaction_status)
    campay.collect, campay.transaction_status = faux_collect, faux_statut
    ent = {"X-API-Key": cfg.FRONTEND_API_KEY, "X-Country": "CM",
           "Authorization": "Bearer " + create_access_token(u.id, u.email)}
    try:
        with _Env():
            r = client.get("/api/payments/methods", headers=ent)
            m = {x["id"]: x["available"] for x in r.json().get("methods", [])}
            ok("ROUTE   /methods : 3 moyens, carte grisee, raison non exposee",
               r.status_code == 200 and m == {"mtn_momo": True,
                                              "orange_money": True, "card": False}
               and not any("reason" in x for x in r.json()["methods"]),
               f"{r.status_code} {r.json()}")

            r = client.post("/api/payments/collect",
                            json={"pages": 2, "method": "card"}, headers=ent)
            ok("ROUTE   /collect carte indisponible : 409, rien ecrit, rien appele",
               r.status_code == 409 and not appels and not await _paiements(u.id),
               f"{r.status_code}")

            r = client.post("/api/payments/collect",
                            json={"pages": 2, "method": "mtn_momo"}, headers=ent)
            ok("ROUTE   /collect mobile money sans numero : 422, rien appele",
               r.status_code == 422 and not appels and not await _paiements(u.id),
               f"{r.status_code}")

            r = client.post("/api/payments/collect",
                            json={"pages": 2, "method": "orange_money",
                                  "phone": "690000000"}, headers={**ent, "X-Country": "FR"})
            ok("ROUTE   /collect zone hors FCFA : 501, rien appele",
               r.status_code == 501 and not appels, f"{r.status_code}")

            r = client.post("/api/payments/collect",
                            json={"pages": 2, "method": "orange_money",
                                  "phone": "690000000"}, headers=ent)
            ps = await _paiements(u.id)
            ok("ROUTE   /collect Orange : Campay appele, prestataire et reference notes",
               r.status_code == 200 and len(appels) == 1 and len(ps) == 1
               and ps[0].provider == "campay"
               and ps[0].provider_ref and ps[0].provider_ref.startswith("ref-")
               and r.json().get("ussd_code") == "*126#",
               f"{r.status_code} {[(p.provider, p.provider_ref) for p in ps]}")

            r = client.post("/api/payments/collect",
                            json={"pages": 2, "phone": "670000000"}, headers=ent)
            ok("ROUTE   /collect ancien client sans 'method' : MTN par defaut",
               r.status_code == 200 and len(appels) == 2, f"{r.status_code} {r.text[:80]}")

        with _Env("mtn_momo=campay,orange_money=,card="):
            r = client.post("/api/payments/collect",
                            json={"pages": 2, "method": "mtn_momo",
                                  "phone": "690000000"}, headers=ent)
            ok("ROUTE   /collect numero Orange sous MTN, Orange coupe : 409, rien appele",
               r.status_code == 409 and len(appels) == 2
               and len(await _paiements(u.id)) == 2, f"{r.status_code}")

        # Un paiement d'un prestataire que le registre ne connaît pas (retiré
        # depuis) : on n'interroge PAS Campay à sa place.
        async with async_session() as db:
            orphelin = Payment(user_id=u.id, pages=2, amount=150, currency="XAF",
                               zone="A", status="PENDING", provider="ancien",
                               provider_ref="ref-x-" + uuid.uuid4().hex)
            db.add(orphelin)
            await db.commit()
        r = client.get(f"/api/payments/{orphelin.id}", headers=ent)
        ok("ROUTE   /{id} prestataire inconnu : reste PENDING, Campay non interroge",
           r.status_code == 200 and r.json().get("status") == "PENDING"
           and not statuts, f"{r.status_code} {statuts}")

        r = client.get(f"/api/payments/{ps[0].id}", headers=ent)
        async with async_session() as db:
            solde = (await db.get(User, u.id)).page_credits
        ok("ROUTE   /{id} Campay : statut interroge, paiement credite",
           r.json().get("status") == "SUCCESSFUL" and statuts == [ps[0].provider_ref]
           and solde == 2, f"{r.json()} {statuts} solde={solde}")
    finally:
        campay.collect, campay.transaction_status = vrais
        await _supprimer(u.id)


async def run():
    checks = []

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    check_registre(ok)
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
