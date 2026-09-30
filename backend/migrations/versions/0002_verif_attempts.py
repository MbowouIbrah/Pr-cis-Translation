"""verification_codes.attempts — compteur d'essais (anti-brute-force)

Revision ID: 0002_verif_attempts
Revises: 0001_init
Create Date: 2026-07-16
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "0002_verif_attempts"
down_revision: Union[str, None] = "0001_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "verification_codes",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("verification_codes", "attempts")
