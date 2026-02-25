"""add setup_slug to devbox

Revision ID: c4f9e2b31a7d
Revises: b3e7a1c94d52
Create Date: 2026-02-17

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c4f9e2b31a7d"
down_revision: str | tuple[str, ...] | None = ("b3e7a1c94d52", "81e72be1adba")
branch_labels: str | list[str] | None = None
depends_on: str | list[str] | None = None


def upgrade() -> None:
    op.add_column("devbox", sa.Column("setup_slug", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("devbox", "setup_slug")
