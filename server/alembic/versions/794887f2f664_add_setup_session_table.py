"""add setup_session table

Revision ID: 794887f2f664
Revises: j4k5l6m7n8o9
Create Date: 2026-03-14 19:45:25.063539

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '794887f2f664'
down_revision: str | list[str] | None = 'j4k5l6m7n8o9'
branch_labels: str | list[str] | None = None
depends_on: str | list[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "setup_session",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("devbox_id", sa.UUID(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("instance_id", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("failed_step", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["devbox_id"], ["devbox.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_setup_session_user_id"), "setup_session", ["user_id"], unique=False)
    op.create_index(op.f("ix_setup_session_devbox_id"), "setup_session", ["devbox_id"], unique=False)
    op.create_index(op.f("ix_setup_session_state"), "setup_session", ["state"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_setup_session_state"), table_name="setup_session")
    op.drop_index(op.f("ix_setup_session_devbox_id"), table_name="setup_session")
    op.drop_index(op.f("ix_setup_session_user_id"), table_name="setup_session")
    op.drop_table("setup_session")
