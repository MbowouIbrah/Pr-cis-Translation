"""Aperçu d'un document rouvert depuis la bibliothèque PENDANT sa traduction.

Le défaut : « Aperçu » reste actif sur une carte en cours, mais
`translated_path` n'est consigné qu'en fin de job. `/preview` renvoyait alors
l'ORIGINAL à la place de la traduction : l'utilisateur voyait original/original,
figé. On attend désormais :
  • job vivant avec des pages prêtes → le PDF PARTIEL (`X-Render: partial`),
    rastérisé pour un plan d'essai ;
  • rien de prêt → 202 `X-Render: pending`, sans corps ;
  • plus de job et pas de traduction → 404 ;
  • job FINI mais base pas à jour (report échoué) → son rendu final
    (`X-Render: full`) s'il réussit, 404 s'il échoue — jamais un partiel
    redemandé sans fin ;
  • une traduction interrompue par un redémarrage est REMBOURSÉE ;
  • dans AUCUN cas l'original déguisé en traduction.

Exécution :  cd backend && venv/Scripts/python.exe tests/test_apercu_en_cours.py
Requiert la base PostgreSQL (comme le reste du backend).
"""
from __future__ import annotations
import asyncio
import os
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
from app.models import User, Document, UsageRecord, KIND_CREDITS  # noqa: E402
from app.core.security import create_access_token        # noqa: E402
from app import config as cfg                            # noqa: E402
from app import create_app                               # noqa: E402
from app.services.jobs import jobs                       # noqa: E402
from app.api.documents import STORAGE_BASE               # noqa: E402

# Même raison que test_verrous_plan : le TestClient a sa propre boucle.
_eng = create_async_engine(DATABASE_URL, poolclass=NullPool)
async_session = async_sessionmaker(_eng, class_=AsyncSession,
                                   expire_on_commit=False)

ORIGINAL = "TEXTE-ORIGINAL-QWERTY"
TRADUIT = "TEXTE-TRADUIT-AZERTY"


def _pdf(mot: str) -> bytes:
    doc = fitz.open()
    doc.new_page(width=400, height=300).insert_text((40, 100), mot, fontsize=13)
    data = doc.tobytes()
    doc.close()
    return data


def _texte(data: bytes) -> str:
    d = fitz.open(stream=data, filetype="pdf")
    t = "".join(p.get_text() for p in d)
    d.close()
    return t


async def run():
    checks = []

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    racine_dir = os.path.join(STORAGE_BASE, f"_test_apercu_{uuid.uuid4().hex[:8]}")
    os.makedirs(racine_dir, exist_ok=True)
    orig = os.path.join(racine_dir, "original.pdf")
    partiel = os.path.join(racine_dir, "partial.pdf")
    with open(orig, "wb") as f:
        f.write(_pdf(ORIGINAL))

    payant = User(email=f"apercu-{uuid.uuid4().hex[:10]}@example.com",
                  plan="starter", email_verified=True)
    libre = User(email=f"apercu-{uuid.uuid4().hex[:10]}@example.com",
                 plan="free", email_verified=True)
    async with async_session() as db:
        db.add_all([payant, libre])
        await db.commit()
        docs = {}
        for cle, u, statut in (("vol", payant, "translating"),
                               ("vol_libre", libre, "translating"),
                               ("mort", payant, "error")):
            d = Document(user_id=u.id, original_name="doc.pdf",
                         source_lang="auto", target_lang="en",
                         original_path=orig, translated_path=None,
                         size_bytes=1, status=statut)
            db.add(d)
            await db.commit()
            docs[cle] = d.id
        # Traduction payée en pages, en vol au moment d'un « arrêt » : le
        # démarrage doit la marquer en erreur ET rembourser ses 2 pages.
        coupe = Document(user_id=payant.id, original_name="doc.pdf",
                         source_lang="auto", target_lang="en",
                         original_path=orig, translated_path=None,
                         size_bytes=1, status="translating")
        db.add(coupe)
        await db.flush()
        db.add(UsageRecord(user_id=payant.id, document_id=coupe.id,
                           kind=KIND_CREDITS, pages=2))
        await db.commit()
        docs["coupe"] = coupe.id

    def entetes(u):
        return {"X-API-Key": cfg.FRONTEND_API_KEY,
                "Authorization": f"Bearer {create_access_token(u.id, u.email)}"}

    job_id = jobs.create()
    job_libre = jobs.create()
    try:
        with TestClient(create_app()) as client:
            # Le lifespan vient de passer les `translating` en erreur (job
            # perdu par construction) : on remet les nôtres en vol, comme un
            # job lancé APRÈS le démarrage.
            async with async_session() as db:
                solde = (await db.get(User, payant.id)).page_credits
                statut_coupe = (await db.get(Document, docs["coupe"])).status
                for cle in ("vol", "vol_libre"):
                    (await db.get(Document, docs[cle])).status = "translating"
                await db.commit()
            ok("REDEMARRAGE : traduction interrompue en erreur et remboursee",
               statut_coupe == "error" and solde == 2,
               f"statut={statut_coupe} solde={solde}")

            jobs.set(job_id, document_id=docs["vol"], user_id=payant.id,
                     partial_path=partiel, pages=None)
            jobs.set(job_libre, document_id=docs["vol_libre"], user_id=libre.id,
                     partial_path=partiel, pages=None)
            url = f"/api/documents/{docs['vol']}/preview?page=1"

            # 1. Job lancé, aucune page encore prête.
            r = client.get(url, headers=entetes(payant))
            ok("EN COURS sans page prête : 202 pending",
               r.status_code == 202 and r.headers.get("X-Render") == "pending",
               f"status={r.status_code} render={r.headers.get('X-Render')}")
            ok("EN COURS sans page prête : jamais l'original",
               ORIGINAL not in (r.content.decode("latin-1", "ignore")
                                if r.content[:4] != b"%PDF" else _texte(r.content)))

            # 2. Le partiel existe : c'est LUI qu'on voit, pas l'original.
            with open(partiel, "wb") as f:
                f.write(_pdf(TRADUIT))
            r = client.get(url, headers=entetes(payant))
            corps = _texte(r.content) if r.status_code == 200 else ""
            ok("EN COURS : le PDF partiel est servi (X-Render: partial)",
               r.status_code == 200 and r.headers.get("X-Render") == "partial"
               and TRADUIT in corps,
               f"status={r.status_code} render={r.headers.get('X-Render')}")
            ok("EN COURS : l'original n'est PAS servi comme traduction",
               ORIGINAL not in corps)

            # 3. Plan d'essai : le partiel est rastérisé (aucun mot extractible).
            r = client.get(f"/api/documents/{docs['vol_libre']}/preview?page=1",
                           headers=entetes(libre))
            ok("EN COURS free : partiel servi mais rastérisé",
               r.status_code == 200 and r.headers.get("X-Render") == "partial"
               and TRADUIT not in _texte(r.content),
               f"status={r.status_code}")

            # 4. Job FINI, base pas à jour (le report a échoué) : le rendu
            # final du job, pas le partiel redemandé indéfiniment.
            final = os.path.join(racine_dir, "render.pdf")
            with open(final, "wb") as f:
                f.write(_pdf(TRADUIT + "-FINAL"))
            jobs.set(job_id, state="done", result_path=final)
            r = client.get(url, headers=entetes(payant))
            ok("FINI non consigne : rendu final servi (X-Render: full)",
               r.status_code == 200 and r.headers.get("X-Render") == "full"
               and TRADUIT + "-FINAL" in _texte(r.content),
               f"status={r.status_code} render={r.headers.get('X-Render')}")
            jobs.set(job_id, state="error", result_path=None)
            r = client.get(url, headers=entetes(payant))
            ok("ECHEC non consigne : 404, plus de partiel",
               r.status_code == 404, f"status={r.status_code}")

            # 5. Plus de job, pas de traduction : 404, pas l'original.
            r = client.get(f"/api/documents/{docs['mort']}/preview?page=1",
                           headers=entetes(payant))
            ok("SANS JOB ni traduction : 404", r.status_code == 404,
               f"status={r.status_code}")
    finally:
        jobs.forget(job_id)
        jobs.forget(job_libre)
        async with async_session() as db:
            for u in (payant, libre):
                obj = await db.get(User, u.id)
                if obj:
                    await db.delete(obj)
            await db.commit()
        shutil.rmtree(racine_dir, ignore_errors=True)

    print()
    for nom, cond, detail in checks:
        print(f"  {'OK  ' if cond else 'ECHEC'}  {nom}"
              + (f"   [{detail}]" if detail and not cond else ""))
    passe = sum(1 for _, c, _ in checks if c)
    print(f"\n  {passe}/{len(checks)}\n")
    return 0 if passe == len(checks) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
