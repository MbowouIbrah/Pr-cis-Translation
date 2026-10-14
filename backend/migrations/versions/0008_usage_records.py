"""Registre de consommation — table usage_records

Les quotas (traduction offerte du forfait Gratuit, pages du mois des plans
payants) se comptaient sur `documents`, que l'utilisateur peut supprimer :
supprimer sa traduction rendait le quota. Le registre survit à la suppression.

Reprise : chaque document existant devient une ligne `legacy`, pour que la
consommation du mois en cours ne soit pas remise à zéro par la migration.

Revision ID: 0008_usage_records
Revises: 0007_support_tickets
Create Date: 2026-10-14
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0008_usage_records"
down_revision: Union[str, None] = "0007_support_tickets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "usage_records",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("document_id", sa.String(32),
                  sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("refunded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now(), index=True),
    )
    # `id` réutilise celui du document : unique, et de la bonne longueur.
    op.execute(
        "INSERT INTO usage_records (id, user_id, document_id, kind, pages, refunded, created_at) "
        "SELECT id, user_id, id, 'legacy', COALESCE(page_count, 1), "
        "       (status = 'error'), created_at FROM documents"
    )


def downgrade() -> None:
    op.drop_table("usage_records")
