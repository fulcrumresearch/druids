"""add stripe fields to user

Revision ID: 81e72be1adba
Revises: 2aab81d55f68
Create Date: 2026-02-17 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "81e72be1adba"
down_revision = "2aab81d55f68"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user", sa.Column("stripe_customer_id", sa.String(), nullable=True))
    op.add_column("user", sa.Column("subscription_status", sa.String(), nullable=True))
    op.create_index(op.f("ix_user_stripe_customer_id"), "user", ["stripe_customer_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_stripe_customer_id"), table_name="user")
    op.drop_column("user", "subscription_status")
    op.drop_column("user", "stripe_customer_id")
