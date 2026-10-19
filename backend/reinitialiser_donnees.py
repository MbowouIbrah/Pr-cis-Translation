r"""Remise a zero : efface TOUTES les donnees, puis recree le seul compte admin.

Vide toutes les tables (comptes, documents, paiements, consommations, jetons,
codes, tickets, journal d'erreurs) et les fichiers traduits et apercus
(TRANSLATIONS_DIR). Le schema (alembic_version) est conserve. Irreversible.

    backend\venv\Scripts\python.exe backend\reinitialiser_donnees.py --confirmer EFFACER-TOUT
    docker compose exec backend python reinitialiser_donnees.py --confirmer EFFACER-TOUT

Sans la phrase exacte, le script affiche ce qu'il effacerait et ne touche a
rien.
"""
import argparse
import asyncio
import os
import shutil

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from sqlalchemy import text
from app.config import TRANSLATIONS_DIR
from app.core.database import async_session as _asm, engine
from seed_admin import ensure_admins

PHRASE = "EFFACER-TOUT"
KEEP = {"alembic_version"}


async def _tables(db) -> list[str]:
    rows = await db.execute(text(
        "select tablename from pg_tables where schemaname = 'public' order by 1"))
    return [t for (t,) in rows.all() if t not in KEEP]


async def main(confirm: str | None) -> None:
    try:
        async with _asm() as db:
            tables = await _tables(db)
            for t in tables:
                n = (await db.execute(text(f'select count(*) from "{t}"'))).scalar()
                print(f"    {t:<22} {n} ligne(s)")
            fichiers = os.listdir(TRANSLATIONS_DIR) if os.path.isdir(TRANSLATIONS_DIR) else []
            print(f"    {TRANSLATIONS_DIR} : {len(fichiers)} element(s)")
            if confirm != PHRASE:
                print(f"\nRien efface. Relancer avec --confirmer {PHRASE}")
                return
            await db.execute(text(
                "truncate " + ", ".join(f'"{t}"' for t in tables)
                + " restart identity cascade"))
            await db.commit()
        for nom in fichiers:
            chemin = os.path.join(TRANSLATIONS_DIR, nom)
            if os.path.isdir(chemin):
                shutil.rmtree(chemin)
            else:
                os.remove(chemin)
        print("\nOK  Tables et fichiers vides.")
        await ensure_admins()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--confirmer", metavar="PHRASE")
    asyncio.run(main(p.parse_args().confirmer))
