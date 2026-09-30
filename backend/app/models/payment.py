"""Tentative d'encaissement."""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import (
    String, Boolean, Integer, DateTime, ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, new_uuid, utcnow

class Payment(Base):
    """Une tentative d'encaissement. Y compris celles qui échouent.

    On enregistre AVANT d'appeler le fournisseur, jamais après : un paiement
    dont la trace n'existe qu'en cas de succès est un paiement qu'on ne saura
    pas réconcilier le jour où le réseau coupe entre l'encaissement et la
    réponse. Le client a été débité ; nous, nous n'en saurions rien.
    """
    __tablename__ = "payments"

    id:       Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    user_id:  Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    # Paiement d'un document précis (débloquer un téléchargement) ou achat de
    # pages d'avance (`None`).
    document_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True,
    )
    pages:    Mapped[int] = mapped_column(Integer, nullable=False)
    # Montant en unités MINEURES, tel qu'envoyé au fournisseur. On garde aussi
    # la devise et la zone : un litige six mois plus tard se tranche sur ce qui
    # a été facturé ce jour-là, pas sur la grille en vigueur aujourd'hui.
    amount:   Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    zone:     Mapped[str] = mapped_column(String(2), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), default="campay", nullable=False)
    # Référence rendue par le fournisseur — la clé de réconciliation.
    provider_ref: Mapped[str | None] = mapped_column(String(128), nullable=True,
                                                     unique=True, index=True)
    # PENDING · SUCCESSFUL · FAILED — vocabulaire de Campay, gardé tel quel
    # pour qu'un état lu dans nos logs se retrouve dans leur tableau de bord.
    status:   Mapped[str] = mapped_column(String(20), default="PENDING",
                                          nullable=False, index=True)
    # Les crédits ont-ils DÉJÀ été portés au compte ? Le webhook et la
    # consultation d'état arrivent tous les deux, souvent en double : sans ce
    # drapeau, un même paiement crédite deux fois.
    credited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    phone:    Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow,
    )
