r"""Amorcage des comptes administrateurs : les cree, ou les promeut s'ils existent.

Idempotent : lance a chaque demarrage du conteneur (docker-entrypoint.sh),
apres les migrations. Une base neuve en production a donc son admin des le
premier demarrage, sans console ni requete SQL a la main.

Les adresses viennent de ADMIN_EMAIL, separees par des virgules (defaut :
mbowouibrah@gmail.com). Retirer une adresse de la liste ne retire PAS le plan
admin du compte : cela se fait depuis la vue admin. Les comptes n'ont pas de
mot de passe : connexion par code envoye par e-mail, ou par Google.

A la main :
    backend\venv\Scripts\python.exe backend\seed_admin.py
    docker compose exec backend python seed_admin.py

Sans accents ni emoji dans les sorties : la console Windows est en cp1252, et
un caractere hors de cette page fait planter le script APRES que la base a ete
modifiee -- une erreur qui ment alors sur ce qui s'est reellement passe.
"""
import os
import asyncio

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from sqlalchemy import func, select
from app.models import User
from app.core.database import async_session as _asm, engine

DEFAULT_ADMIN_EMAIL = "mbowouibrah@gmail.com"
ADMIN_NAME = "Ibrahim Mbowou"


def admin_emails() -> list[str]:
    """Adresses de ADMIN_EMAIL, normalisees et sans doublon, dans l'ordre."""
    brut = os.getenv("ADMIN_EMAIL") or DEFAULT_ADMIN_EMAIL
    emails: list[str] = []
    for e in brut.split(","):
        e = e.strip().lower()
        if e and e not in emails:
            emails.append(e)
    return emails or [DEFAULT_ADMIN_EMAIL]


async def ensure_admin(email: str) -> User:
    email = email.strip().lower()
    async with _asm() as db:
        user = (await db.execute(
            select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
        created = user is None
        if created:
            user = User(email=email,
                        name=ADMIN_NAME if email == DEFAULT_ADMIN_EMAIL else None)
            db.add(user)
        user.plan = "admin"
        user.email_verified = True
        user.storage_limit = 0  # 0 = illimite
        # Plan attribue, pas achete : sans echeance.
        user.plan_expires_at = None
        user.plan_started_at = None
        await db.commit()
        await db.refresh(user)
    print(f"OK  Compte admin {'cree' if created else 'a jour'} : {user.email}")
    return user


async def ensure_admins() -> list[User]:
    return [await ensure_admin(e) for e in admin_emails()]


async def main() -> None:
    try:
        await ensure_admins()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
