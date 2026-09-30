"""documents.storage_charged — ce qui a été débité du quota à la création

La suppression remboursait `size_bytes` inconditionnellement alors que la
création ne facture parfois rien (quota plein, plan sans stockage) :
`User.storage_used` descendait sous la réalité. On mémorise le débit exact.

Backfill : `size_bytes` pour les lignes existantes — c'est ce qui a été
réellement débité dans le cas courant (plan avec stockage, quota non plein).
Pour un plan `free` (0 octet débité), rembourser trop est sans effet :
`storage_used` y est déjà 0 et la soustraction est bornée à 0.

Revision ID: 0003_storage_charged
Revises: 0002_verif_attempts
Create Date: 2026-07-17
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "0003_storage_charged"
down_revision: Union[str, None] = "0002_verif_attempts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("storage_charged", sa.BigInteger(), nullable=False,
                  server_default="0"),
    )
    op.execute("UPDATE documents SET storage_charged = size_bytes")


def downgrade() -> None:
    op.drop_column("documents", "storage_charged")
