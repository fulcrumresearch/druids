"""add spec table and drop programrating

Revision ID: a2b3c4d5e6f7
Revises: 501a3110477d
Create Date: 2026-02-22

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a2b3c4d5e6f7"
down_revision: str | tuple[str, ...] | None = "501a3110477d"
branch_labels: str | list[str] | None = None
depends_on: str | list[str] | None = None


def upgrade() -> None:
    op.create_table(
        "spec",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("hash", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False, server_default=""),
        sa.Column("yaml", sa.Text(), nullable=False),
        sa.Column("rating", sa.Float(), nullable=False, server_default="1500.0"),
        sa.Column("num_comparisons", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_spec_hash"), "spec", ["hash"], unique=True)

    op.drop_index(op.f("ix_programrating_key_type"), table_name="programrating")
    op.drop_index(op.f("ix_programrating_key"), table_name="programrating")
    op.drop_table("programrating")


def downgrade() -> None:
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

    op.drop_index(op.f("ix_spec_hash"), table_name="spec")
    op.drop_table("spec")
