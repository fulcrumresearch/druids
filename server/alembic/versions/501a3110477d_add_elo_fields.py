"""add elo fields to execution and programrating table

Revision ID: 501a3110477d
Revises: c4f9e2b31a7d
Create Date: 2026-02-21

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "501a3110477d"
down_revision: str | tuple[str, ...] | None = "d7a1f3e52b9c"
branch_labels: str | list[str] | None = None
depends_on: str | list[str] | None = None


def upgrade() -> None:
    # ExecutionRecord: add outcome and program_spec
    op.add_column("execution", sa.Column("outcome", sa.String(), nullable=True))
    op.add_column("execution", sa.Column("program_spec", sa.Text(), nullable=True))

    # ProgramRating table
    op.create_table(
        "programrating",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("key_type", sa.String(), nullable=False),
        sa.Column("rating", sa.Float(), nullable=False, server_default="1500.0"),
        sa.Column("num_comparisons", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_programrating_key"), "programrating", ["key"], unique=False)
    op.create_index(op.f("ix_programrating_key_type"), "programrating", ["key_type"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_programrating_key_type"), table_name="programrating")
    op.drop_index(op.f("ix_programrating_key"), table_name="programrating")
    op.drop_table("programrating")
    op.drop_column("execution", "program_spec")
    op.drop_column("execution", "outcome")
