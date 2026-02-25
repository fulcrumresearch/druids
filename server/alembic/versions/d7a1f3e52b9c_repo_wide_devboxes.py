"""repo-wide devboxes: rename user_id to setup_by_user_id, unique repo_full_name

Revision ID: d7a1f3e52b9c
Revises: c4f9e2b31a7d
Create Date: 2026-02-19

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d7a1f3e52b9c"
down_revision: str | tuple[str, ...] | None = "c4f9e2b31a7d"
branch_labels: str | list[str] | None = None
depends_on: str | list[str] | None = None


def upgrade() -> None:
    # 1. Deduplicate: for each repo_full_name, keep the row with a non-null
    #    snapshot_id and the most recent updated_at. Delete the rest.
    op.execute(
        sa.text("""
            DELETE FROM devbox
            WHERE id NOT IN (
                SELECT DISTINCT ON (repo_full_name) id
                FROM devbox
                ORDER BY repo_full_name,
                         (snapshot_id IS NOT NULL) DESC,
                         updated_at DESC NULLS LAST,
                         created_at DESC
            )
        """)
    )

    # 2. Rename column user_id -> setup_by_user_id.
    op.alter_column("devbox", "user_id", new_column_name="setup_by_user_id")

    # 3. Add unique constraint on repo_full_name.
    op.create_unique_constraint("uq_devbox_repo_full_name", "devbox", ["repo_full_name"])


def downgrade() -> None:
    op.drop_constraint("uq_devbox_repo_full_name", "devbox", type_="unique")
    op.alter_column("devbox", "setup_by_user_id", new_column_name="user_id")
