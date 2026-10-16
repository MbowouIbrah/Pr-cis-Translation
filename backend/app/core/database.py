"""
Engine SQLAlchemy async + session factory.

Utilisation :
    from backend.database import async_session
    async with async_session() as db:
        user = await db.get(User, user_id)
"""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# L'URL vient de `app.config`, seul endroit qui lit l'environnement. Ce module
# appelait `load_dotenv` sur `app/core/.env` — un chemin qui n'existe pas : le
# `.env` n'était vu que par l'effet de bord de l'import de `config`, et à défaut
# l'application se connectait SILENCIEUSEMENT à la base de démonstration.
from app.config import DATABASE_URL

from sqlalchemy import event
from sqlalchemy.exc import DisconnectionError

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=10, max_overflow=20)


@event.listens_for(engine.sync_engine, "checkout")
def _refuser_connexion_en_transaction(_dbapi_conn, record, _proxy):
    """Une connexion ne sort JAMAIS du pool avec une transaction ouverte.

    Une requête annulée pendant `BEGIN` ou `COMMIT` (client qui se déconnecte :
    `CancelledError` n'est pas une `Exception`) laisse asyncpg EN transaction
    alors que l'adaptateur SQLAlchemy se croit au repos : le rollback de
    restitution ne fait rien. Chaque session suivante tombe dans un savepoint
    de cette transaction, ne valide plus rien pour de bon, et voit des lignes
    que les autres connexions ne voient pas (mesuré : une carte qui apparaît
    puis disparaît selon la connexion tirée, des écritures perdues pendant des
    heures). La connexion est donc jetée — sa fermeture annule la transaction
    côté serveur — et le pool en fournit une autre."""
    # `record.driver_connection` = la connexion asyncpg elle-même ;
    # l'adaptateur (`_dbapi_conn`) n'expose pas cet état.
    en_tx = getattr(record.driver_connection, "is_in_transaction", None)
    if callable(en_tx) and en_tx():
        raise DisconnectionError("connexion restituée avec une transaction ouverte")

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    """Dépendance FastAPI : injecte une session async."""
    async with async_session() as session:
        yield session
