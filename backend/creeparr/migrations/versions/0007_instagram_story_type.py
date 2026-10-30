"""instagram stories stored as instagram_story

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-30

"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Scans wrote "stories".rstrip("s"); the UI and the renewal rules use instagram_story.
    op.execute(
        "UPDATE posts SET post_type = 'instagram_story' WHERE post_type = 'instagram_storie'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE posts SET post_type = 'instagram_storie' WHERE post_type = 'instagram_story'"
    )
