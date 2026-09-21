"""creator scan interval

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-20

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("creators") as batch_op:
        batch_op.add_column(sa.Column("scan_interval_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("creators") as batch_op:
        batch_op.drop_column("scan_interval_minutes")
