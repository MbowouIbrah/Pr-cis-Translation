"""Registre de consommation : ce qui a été traduit, et au titre de quoi."""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, new_uuid, utcnow

# Au titre de quoi une traduction a été lancée.
KIND_FREE_OFFER = "free_offer"   # la traduction offerte du mois (forfait Gratuit)
KIND_CREDITS = "credits"         # pages achetées d'avance, débitées du solde
KIND_PLAN = "plan"               # quota mensuel d'un plan payant
KIND_LEGACY = "legacy"           # reprise des documents antérieurs au registre


class UsageRecord(Base):
    """Une traduction LANCÉE, consignée une fois pour toutes.

    Les quotas se comptaient sur la table `documents` — or l'utilisateur peut
    supprimer un document. Supprimer la traduction du mois rendait donc la
    traduction offerte à nouveau disponible, et chaque suppression rendait ses
    pages au quota d'un plan payant : la limite ne limitait rien.

    Cette table ne dépend d'aucune action de l'utilisateur. Supprimer le
    document met `document_id` à NULL ; la ligne, elle, reste et continue de
    compter. Seul un ÉCHEC de traduction la neutralise (`refunded`) : on ne
    facture pas ce qu'on n'a pas livré.
    """
    __tablename__ = "usage_records"

    id:          Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    user_id:     Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    document_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    kind:        Mapped[str] = mapped_column(String(16), nullable=False)
    pages:       Mapped[int] = mapped_column(Integer, nullable=False)
    refunded:    Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                  index=True)
