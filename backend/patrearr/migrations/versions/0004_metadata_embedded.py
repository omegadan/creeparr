"""metadata embedded flag

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-20

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("media_items") as batch_op:
        batch_op.add_column(
            sa.Column("metadata_embedded", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("media_items") as batch_op:
        batch_op.drop_column("metadata_embedded")
