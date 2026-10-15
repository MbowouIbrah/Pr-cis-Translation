"""Abonnements payés — users.plan_expires_at ; payments.plan, months, method

Un plan payant s'achète comme une page : un encaissement Campay, qui ne
prélève jamais de lui-même. L'abonnement a donc une FIN (`plan_expires_at`) ;
passée, le compte revient au forfait Gratuit. `NULL` = plan sans échéance
(gratuit, ou attribué par l'administration).

`payments.plan` / `months` : ce qu'un paiement achète quand ce n'est pas des
pages. `payments.method` : le moyen choisi par le client (MTN, Orange, carte),
jusqu'ici perdu une fois le prestataire déduit.

`users.plan_started_at` : début de l'abonnement en cours ; le quota de pages
court de date à date depuis ce jour, pas du 1er du mois. `payments.refund_due` :
paiement encaissé mais non appliqué (le compte a changé entre l'invite et la
validation) — à rembourser, jamais à oublier.

Revision ID: 0009_abonnements
Revises: 0008_usage_records
Create Date: 2026-10-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0009_abonnements"
down_revision: Union[str, None] = "0008_usage_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("plan_expires_at", sa.DateTime(timezone=True),
                                     nullable=True))
    op.add_column("users", sa.Column("plan_started_at", sa.DateTime(timezone=True),
                                     nullable=True))
    op.add_column("payments", sa.Column("refund_due", sa.Boolean(), nullable=False,
                                        server_default=sa.false()))
    op.add_column("payments", sa.Column("plan", sa.String(20), nullable=True))
    op.add_column("payments", sa.Column("months", sa.Integer(), nullable=True))
    op.add_column("payments", sa.Column("method", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("payments", "method")
    op.drop_column("payments", "months")
    op.drop_column("payments", "plan")
    op.drop_column("payments", "refund_due")
    op.drop_column("users", "plan_started_at")
    op.drop_column("users", "plan_expires_at")
