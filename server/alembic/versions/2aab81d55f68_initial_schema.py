"""initial schema - add submit_summary to execution

Revision ID: 2aab81d55f68
Revises:
Create Date: 2026-02-07 22:35:35.396221

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "2aab81d55f68"
down_revision: str | list[str] | None = None
branch_labels: str | list[str] | None = None
depends_on: str | list[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("execution", sa.Column("submit_summary", sa.VARCHAR(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("execution", "submit_summary")
