"""
Authentification — JWT, bcrypt, middleware FastAPI.

Utilisation :
    from backend.auth import require_auth, create_access_token, hash_password
"""
from __future__ import annotations
import uuid
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import User, RefreshToken

# ── Configuration ────────────────────────────────────────────────────────────

# Lus depuis `app.config` — et non par un `os.getenv` local. Le garde-fou de
# production y contrôle le secret ; s'il inspectait une lecture différente de
# celle qui signe ici, son feu vert ne prouverait rien.
from app.config import (JWT_EXPIRY_MINUTES,       # noqa: E402
                        JWT_SECRET,
                        REFRESH_TOKEN_EXPIRY_DAYS)

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRY_MINUTES = JWT_EXPIRY_MINUTES

security = HTTPBearer(auto_error=False)


# ── Password ─────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ── Tokens ───────────────────────────────────────────────────────────────────

def create_access_token(user_id: str, email: str) -> str:
    """JWT d'accès (courte durée, porté dans Authorization: Bearer)."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRY_MINUTES),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_access_token(token: str) -> dict:
    """Décode un JWT. Lève jwt.PyJWTError si invalide/expiré."""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


async def create_refresh_token(db: AsyncSession, user_id: str) -> str:
    """Refresh token stocké en DB (longue durée, rotation)."""
    raw = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRY_DAYS)

    rt = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires)
    db.add(rt)
    await db.commit()
    return raw


async def rotate_refresh_token(db: AsyncSession, raw_token: str) -> tuple[str, User] | None:
    """Valide un refresh token, le supprime, en émet un nouveau. Retourne (token, user)."""
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    result = await db.execute(stmt)
    rt = result.scalar_one_or_none()

    if rt is None or rt.expires_at < datetime.now(timezone.utc):
        return None

    user = await db.get(User, rt.user_id)
    if user is None:
        return None

    # Rotation : supprimer l'ancien, créer un nouveau.
    await db.delete(rt)
    await db.commit()

    new_raw = await create_refresh_token(db, user.id)
    return new_raw, user


async def revoke_user_tokens(db: AsyncSession, user_id: str) -> None:
    """Révoque tous les refresh tokens d'un utilisateur (logout)."""
    stmt = select(RefreshToken).where(RefreshToken.user_id == user_id)
    result = await db.execute(stmt)
    for rt in result.scalars().all():
        await db.delete(rt)
    await db.commit()


# ── Middleware ────────────────────────────────────────────────────────────────

async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dépendance FastAPI : extrait et valide le JWT, retourne l'utilisateur."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentification requise.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = verify_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expiré.")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Token invalide.")

    user = await db.get(User, payload["sub"])
    if user is None:
        raise HTTPException(status_code=401, detail="Utilisateur introuvable.")

    await _expire_plan_if_due(db, user)
    return user


async def _expire_plan_if_due(db: AsyncSession, user: User) -> None:
    """Abonnement échu : retour au forfait Gratuit, au premier passage.

    Campay ne prélève jamais de lui-même : sans renouvellement payé, la fin
    arrive. Elle s'applique ici, là où TOUTE route authentifiée passe, plutôt
    que dans une tâche planifiée qui laisserait des heures de droits gratuits.

    Écriture conditionnelle : un renouvellement crédité au même instant a déjà
    repoussé l'échéance, la condition ne la voit plus échue et ne touche à rien.
    """
    now = datetime.now(timezone.utc)
    if user.plan_expires_at is None or user.plan_expires_at > now:
        return
    await db.execute(
        update(User)
        .where(User.id == user.id, User.plan_expires_at <= now)
        .values(plan="free", plan_expires_at=None, storage_limit=0)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    await db.refresh(user)


async def optional_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Comme require_auth, mais ne bloque pas si pas de token (pages publiques)."""
    if credentials is None:
        return None
    try:
        payload = verify_access_token(credentials.credentials)
        user = await db.get(User, payload["sub"])
    except Exception:
        return None
    if user is not None:
        await _expire_plan_if_due(db, user)
    return user


async def require_admin(user: User = Depends(require_auth)) -> User:
    """Réserve une route aux comptes `admin`. 403 sinon.

    S'appuie sur `require_auth` : un visiteur non authentifié reçoit d'abord 401,
    un utilisateur authentifié mais non-admin reçoit 403. Le plan est la seule
    marque d'admin (`user.plan == 'admin'`), comme partout ailleurs.
    """
    if user.plan != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Réservé à l'administration.")
    return user
