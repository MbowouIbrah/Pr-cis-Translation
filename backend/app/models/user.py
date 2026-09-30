"""Compte utilisateur."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    String, Boolean, BigInteger, Integer, DateTime,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, new_uuid, utcnow

# Importe pour le TYPAGE seulement : SQLAlchemy resout les cibles de
# relation par leur NOM dans son registre, pas par cet import. Le faire
# a l'execution creerait un cycle entre les modeles.
if TYPE_CHECKING:
    from .document import Document
    from .auth_tokens import RefreshToken, VerificationCode

class User(Base):
    __tablename__ = "users"

    id:            Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    email:         Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name:          Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    google_id:     Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    avatar_url:    Mapped[str | None] = mapped_column(String(512), nullable=True)
    plan:          Mapped[str] = mapped_column(String(20), default="free", nullable=False)
    storage_used:  Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    storage_limit: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # Pages achetées à l'unité et pas encore consommées (forfait Gratuit).
    # C'est un SOLDE, pas un historique : il est débité au lancement d'une
    # traduction, et les paiements qui l'ont alimenté vivent dans `payments`.
    page_credits:  Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at:    Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow,
    )

    documents: Mapped[list[Document]] = relationship(
        "Document", back_populates="user", cascade="all, delete-orphan",
    )
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan",
    )
    verification_codes: Mapped[list[VerificationCode]] = relationship(
        "VerificationCode", back_populates="user", cascade="all, delete-orphan",
    )
