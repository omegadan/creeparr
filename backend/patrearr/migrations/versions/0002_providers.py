"""providers

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-20

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("creators") as batch_op:
        batch_op.add_column(
            sa.Column("provider", sa.String(length=32), nullable=False, server_default="patreon")
        )
        batch_op.alter_column("campaign_id", type_=sa.String(length=64))
        batch_op.create_unique_constraint("uq_creator_provider_id", ["provider", "campaign_id"])
    with op.batch_alter_table("posts") as batch_op:
        batch_op.alter_column("post_id", type_=sa.String(length=64))
        batch_op.create_unique_constraint("uq_post_creator_id", ["creator_id", "post_id"])


def downgrade() -> None:
    with op.batch_alter_table("posts") as batch_op:
        batch_op.drop_constraint("uq_post_creator_id", type_="unique")
    with op.batch_alter_table("creators") as batch_op:
        batch_op.drop_constraint("uq_creator_provider_id", type_="unique")
        batch_op.drop_column("provider")
