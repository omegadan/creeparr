"""indexes for dedupe and the hourly download window

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-22

"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Every completed download looks up twins by sha256, and every claim counts the
    # starts of the last hour; both scanned the whole table.
    op.create_index("ix_media_items_sha256", "media_items", ["sha256"])
    op.create_index("ix_download_jobs_started_at", "download_jobs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_download_jobs_started_at", table_name="download_jobs")
    op.drop_index("ix_media_items_sha256", table_name="media_items")
