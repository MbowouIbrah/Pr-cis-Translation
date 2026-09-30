"""Jetons de session et codes de verification d'e-mail."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    String, Boolean, Integer, DateTime, ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, new_uuid, utcnow

if TYPE_CHECKING:
    from .user import User

class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id:         Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    user_id:    Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship("User", back_populates="refresh_tokens")


# ── VerificationCode ─────────────────────────────────────────────────────────

class VerificationCode(Base):
    __tablename__ = "verification_codes"

    id:         Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    user_id:    Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    code:       Mapped[str] = mapped_column(String(6), nullable=False)
    token:      Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used:       Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempts:   Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship("User", back_populates="verification_codes")
