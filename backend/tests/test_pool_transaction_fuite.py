"""Une connexion restituée au pool EN transaction ne doit contaminer personne.

Incident du 16/10 : une requête annulée pendant `BEGIN`/`COMMIT` a laissé une
connexion asyncpg dans une transaction que SQLAlchemy ignorait. Pendant des
heures, chaque session qui la tirait travaillait dans un savepoint de cette
transaction : écritures jamais validées, et une ligne visible une requête sur
N (la carte PPTX qui apparaissait puis disparaissait de la bibliothèque).

On reproduit l'état exact — transaction ouverte au niveau du pilote, adaptateur
au repos — puis on vérifie que la session suivante n'en hérite pas.
"""
from __future__ import annotations
import asyncio
import sys

import racine  # noqa: F401  -- met backend/ sur le chemin

from sqlalchemy import text                       # noqa: E402

from app.core.database import async_session, engine  # noqa: E402

echecs: list[str] = []


def check(nom: str, ok: bool) -> None:
    print(("OK   " if ok else "ECHEC") + " " + nom)
    if not ok:
        echecs.append(nom)


async def main() -> None:
    # 1. Une connexion fuit : BEGIN envoyé au pilote, l'adaptateur n'en sait rien
    #    (c'est ce que laisse une annulation au milieu de `transaction.start()`).
    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        brute = raw.driver_connection
        await conn.commit()                      # adaptateur au repos
        fuite = brute.transaction()             # jamais rattachée à l'adaptateur
        await fuite.start()
        await brute.execute("SELECT set_config('diag.fuite', 'oui', true)")
        check("la fuite est bien reproduite", brute.is_in_transaction())
    # La connexion est rendue au pool, transaction serveur toujours ouverte.

    # 2. La session suivante tire la connexion du pool (il n'y en a qu'une).
    async with async_session() as db:
        valeur = (await db.execute(
            text("SELECT current_setting('diag.fuite', true)"))).scalar()
        check("la session suivante ne voit pas la transaction fuyante",
              (valeur or "") != "oui")
        brute2 = (await (await db.connection()).get_raw_connection()).driver_connection
        await db.commit()
        check("après commit, la connexion n'est plus en transaction",
              not brute2.is_in_transaction())

    await engine.dispose()


asyncio.run(main())
print(f"\n{'ÉCHEC' if echecs else 'OK'} — {len(echecs)} échec(s)")
sys.exit(1 if echecs else 0)
