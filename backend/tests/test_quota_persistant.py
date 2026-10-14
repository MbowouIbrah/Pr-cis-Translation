"""Quota persistant — supprimer un document ne rend PAS ce qu'il a consommé.

La faille (constatée en production, 14/10/2026) :
  « sur un compte gratuit j'ai une traduction par mois […] il me suffit de
    supprimer la traduction déjà effectuée pour pouvoir effectuer une autre
    traduction »

Les quotas se comptaient sur la table `documents`. Cette suite passe par les
VRAIES routes (dépôt, suppression, profil) : un test qui recalculerait la
requête à côté de la route mesurerait sa copie, pas le code qui refuse.

Couvre les trois titres de consommation : l'offre du mois (Gratuit), le solde
de pages achetées, le quota mensuel des plans payants — et le remboursement
d'une traduction ÉCHOUÉE, seule chose qui rend une consommation.

Exécution :  backend/venv/Scripts/python.exe backend/tests/test_quota_persistant.py
Requiert PostgreSQL et la migration 0008.
"""
from __future__ import annotations
import asyncio
import shutil
import sys
import uuid

import racine  # noqa: F401  -- met backend/ sur le chemin

import fitz                                              # noqa: E402
from fastapi.testclient import TestClient                # noqa: E402
from sqlalchemy.ext.asyncio import (                     # noqa: E402
    create_async_engine, async_sessionmaker, AsyncSession,
)
from sqlalchemy.pool import NullPool                     # noqa: E402

from app.core.database import DATABASE_URL               # noqa: E402
from app.models import User, UsageRecord, KIND_PLAN      # noqa: E402
from app.core.security import create_access_token        # noqa: E402
from app import config as cfg                            # noqa: E402
from app import create_app                               # noqa: E402
from app.api import translate as route_translate         # noqa: E402
from app.services.documents import build_job_paths       # noqa: E402
from app.core.files import get_file_hash                 # noqa: E402
from app.services.jobs import jobs                       # noqa: E402

_eng = create_async_engine(DATABASE_URL, poolclass=NullPool)
async_session = async_sessionmaker(_eng, class_=AsyncSession,
                                   expire_on_commit=False)


def _pdf(n_pages: int = 1) -> bytes:
    # Contenu UNIQUE : deux documents identiques partageraient le magasin.
    doc = fitz.open()
    for i in range(n_pages):
        doc.new_page(width=400, height=300).insert_text(
            (40, 100), f"quota {uuid.uuid4().hex} page {i}", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


async def run():
    checks = []

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    # Aucun travail réel : on mesure la DÉCISION de la route, pas le moteur.
    soumis = []
    vrai_submit = route_translate.scheduler.submit
    route_translate.scheduler.submit = lambda job_id, *a, **k: soumis.append(job_id)

    comptes = {
        "libre": User(email=f"q-libre-{uuid.uuid4().hex[:8]}@example.com",
                      plan="free", email_verified=True),
        "echec": User(email=f"q-echec-{uuid.uuid4().hex[:8]}@example.com",
                      plan="free", email_verified=True),
        "credits": User(email=f"q-cred-{uuid.uuid4().hex[:8]}@example.com",
                        plan="free", email_verified=True, page_credits=2),
        "starter": User(email=f"q-start-{uuid.uuid4().hex[:8]}@example.com",
                        plan="starter", email_verified=True),
    }
    async with async_session() as db:
        db.add_all(comptes.values())
        await db.commit()

    deposes: list[bytes] = []

    def entetes(u):
        return {"X-API-Key": cfg.FRONTEND_API_KEY,
                "Authorization": f"Bearer {create_access_token(u.id, u.email)}"}

    def deposer(client, u, n_pages=1):
        data = _pdf(n_pages)
        deposes.append(data)
        return client.post("/api/translate", headers=entetes(u),
                           files={"file": ("doc.pdf", data, "application/pdf")},
                           data={"target_lang": "en"})

    def reste(client, u):
        return client.get("/api/auth/me/usage", headers=entetes(u)).json()

    def doc_du_job(job_id):
        return (jobs.get(job_id) or {}).get("document_id")

    try:
        with TestClient(create_app()) as client:
            # ── Gratuit : supprimer ne rend pas l'offre ─────────────────────
            u = comptes["libre"]
            ok("GRATUIT  le profil annonce 1 traduction offerte",
               reste(client, u).get("free_translations_remaining") == 1,
               str(reste(client, u)))
            r1 = deposer(client, u)
            ok("GRATUIT  la traduction offerte passe", r1.status_code == 200,
               f"{r1.status_code} {r1.text[:120]}")
            ok("GRATUIT  le profil annonce 0 ensuite",
               reste(client, u).get("free_translations_remaining") == 0)
            r2 = deposer(client, u)
            ok("GRATUIT  la deuxième est refusée (402)", r2.status_code == 402)

            doc_id = doc_du_job(r1.json().get("job_id"))
            rd = client.delete(f"/api/documents/{doc_id}", headers=entetes(u))
            ok("GRATUIT  la suppression du document réussit",
               rd.status_code == 200, f"{rd.status_code} {rd.text[:120]}")
            r3 = deposer(client, u)
            ok("GRATUIT  APRÈS suppression : toujours refusée (la faille)",
               r3.status_code == 402, f"status={r3.status_code}")
            ok("GRATUIT  APRÈS suppression : le profil annonce toujours 0",
               reste(client, u).get("free_translations_remaining") == 0)

            # ── Échec de traduction : l'offre est rendue ────────────────────
            u = comptes["echec"]
            r = deposer(client, u)
            job_id = r.json().get("job_id")
            await asyncio.to_thread(jobs.sync_status, job_id, "error")
            ok("ÉCHEC  une traduction échouée rend l'offre du mois",
               reste(client, u).get("free_translations_remaining") == 1)
            await asyncio.to_thread(jobs.sync_status, job_id, "error")
            r = deposer(client, u)
            ok("ÉCHEC  on peut donc relancer", r.status_code == 200,
               f"{r.status_code} {r.text[:120]}")
            r = deposer(client, u)
            ok("ÉCHEC  rejoué deux fois : rendue UNE seule fois",
               r.status_code == 402, f"status={r.status_code}")

            # ── Pages achetées : débitées, remboursées en cas d'échec ───────
            u = comptes["credits"]
            deposer(client, u)                          # consomme l'offre
            r = deposer(client, u, n_pages=2)
            ok("CRÉDITS  2 pages achetées couvrent un document de 2 pages",
               r.status_code == 200, f"{r.status_code} {r.text[:120]}")
            ok("CRÉDITS  le solde tombe à 0",
               reste(client, u).get("page_credits") == 0)
            await asyncio.to_thread(jobs.sync_status, r.json()["job_id"], "error")
            await asyncio.to_thread(jobs.sync_status, r.json()["job_id"], "error")
            ok("CRÉDITS  échec (rejoué) : les 2 pages reviennent, une fois",
               reste(client, u).get("page_credits") == 2,
               str(reste(client, u)))

            # ── Plan payant : quota du mois, la suppression ne rend rien ───
            u = comptes["starter"]
            async with async_session() as db:
                db.add(UsageRecord(user_id=u.id, kind=KIND_PLAN, pages=99))
                await db.commit()
            r = deposer(client, u)
            ok("PLAN  pile le quota passe (99+1=100)", r.status_code == 200,
               f"{r.status_code} {r.text[:120]}")
            doc_id = doc_du_job(r.json().get("job_id"))
            client.delete(f"/api/documents/{doc_id}", headers=entetes(u))
            s = reste(client, u)
            ok("PLAN  après suppression : 100 pages toujours consommées",
               s.get("pages_used") == 100 and s.get("pages_remaining") == 0, str(s))
            r = deposer(client, u)
            ok("PLAN  après suppression : la suivante est refusée",
               r.status_code == 402, f"status={r.status_code}")
    finally:
        route_translate.scheduler.submit = vrai_submit
        for jid in soumis:
            jobs.forget(jid)
        async with async_session() as db:
            for u in comptes.values():
                obj = await db.get(User, u.id)
                if obj:
                    await db.delete(obj)
            await db.commit()
        for data in deposes:
            job_dir, _ = build_job_paths(get_file_hash(data), "en")
            shutil.rmtree(job_dir, ignore_errors=True)

    print()
    for nom, cond, detail in checks:
        print(f"  {'OK  ' if cond else 'ÉCHEC'}  {nom}"
              + (f"   [{detail}]" if not cond and detail else ""))
    reussis = sum(1 for _, c, _ in checks if c)
    print(f"\n  {reussis}/{len(checks)}\n")
    return reussis == len(checks)


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(run()) else 1)
