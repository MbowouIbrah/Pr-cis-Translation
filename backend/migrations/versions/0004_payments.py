"""Paiement à la page — users.page_credits, documents.paid, table payments

Le droit de télécharger et de voir en clair se lisait sur le PLAN : un compte
`free` ne pouvait rien récupérer, y compris ce qu'il venait d'acheter. Le droit
descend au DOCUMENT (`documents.paid`), et les pages achetées d'avance vivent
sur le compte (`users.page_credits`).

Backfill : `documents.paid = TRUE` pour les documents appartenant à un compte
NON-`free`. Ces comptes avaient déjà le droit de télécharger via leur plan ;
laisser leurs documents à `paid = FALSE` reviendrait à le leur retirer d'un
coup. Les documents des comptes `free` restent à FALSE — c'est l'état exact de
ce qu'ils pouvaient faire jusqu'ici (aperçu masqué, pas de téléchargement).

Revision ID: 0004_payments
Revises: 0003_storage_charged
Create Date: 2026-07-19
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0004_payments"
down_revision: Union[str, None] = "0003_storage_charged"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("page_credits", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "documents",
        sa.Column("paid", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # Les plans payants gardent leurs droits acquis. On énumère l'EXCEPTION
    # (`free`), miroir de `is_paid_plan` : ajouter un plan payant demain ne doit
    # pas obliger à repasser ici.
    op.execute(
        "UPDATE documents SET paid = TRUE WHERE user_id IN "
        "(SELECT id FROM users WHERE plan <> 'free')"
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("document_id", sa.String(32),
                  sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("zone", sa.String(2), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False, server_default="campay"),
        # UNIQUE : c'est ce qui empêche deux lignes de représenter le même
        # encaissement si une notification arrive en double.
        sa.Column("provider_ref", sa.String(128), nullable=True, unique=True, index=True),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="PENDING", index=True),
        sa.Column("credited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("payments")
    op.drop_column("documents", "paid")
    op.drop_column("users", "page_credits")
