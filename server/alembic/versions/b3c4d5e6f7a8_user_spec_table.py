"""add user_spec junction table

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-02-23

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7a8"
down_revision: str | tuple[str, ...] | None = "a2b3c4d5e6f7"
branch_labels: str | list[str] | None = None
depends_on: str | list[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_spec",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("spec_id", sa.Uuid(), sa.ForeignKey("spec.id"), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "spec_id", name="uq_user_spec_user_spec"),
    )
    op.create_index(op.f("ix_user_spec_user_id"), "user_spec", ["user_id"])
    op.create_index(op.f("ix_user_spec_spec_id"), "user_spec", ["spec_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_user_spec_spec_id"), table_name="user_spec")
    op.drop_index(op.f("ix_user_spec_user_id"), table_name="user_spec")
    op.drop_table("user_spec")
