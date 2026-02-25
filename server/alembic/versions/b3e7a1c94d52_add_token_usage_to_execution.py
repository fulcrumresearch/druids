"""add token usage columns to execution

Revision ID: b3e7a1c94d52
Revises: 2aab81d55f68
Create Date: 2026-02-17

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b3e7a1c94d52"
down_revision: str | list[str] | None = "2aab81d55f68"
branch_labels: str | list[str] | None = None
depends_on: str | list[str] | None = None


def upgrade() -> None:
    op.add_column("execution", sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("execution", sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("execution", sa.Column("cache_read_input_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(
        "execution", sa.Column("cache_creation_input_tokens", sa.Integer(), nullable=False, server_default="0")
    )


def downgrade() -> None:
    op.drop_column("execution", "cache_creation_input_tokens")
    op.drop_column("execution", "cache_read_input_tokens")
    op.drop_column("execution", "output_tokens")
    op.drop_column("execution", "input_tokens")
