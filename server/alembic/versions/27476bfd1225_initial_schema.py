"""initial schema

Revision ID: 27476bfd1225
Revises:
Create Date: 2026-03-09 13:37:53.846145

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "27476bfd1225"
down_revision: str | list[str] | None = None
branch_labels: str | list[str] | None = None
depends_on: str | list[str] | None = None


def upgrade() -> None:
    """Create all tables."""
    op.create_table(
        "user",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("github_id", sa.Integer(), nullable=False),
        sa.Column("github_login", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_github_id"), "user", ["github_id"], unique=True)

    op.create_table(
        "execution",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("spec", sa.String(), nullable=False),
        sa.Column("repo_full_name", sa.String(), nullable=True),
        sa.Column("metadata_", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("branch_name", sa.String(), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("pr_url", sa.String(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("agents_", sa.JSON(), nullable=True),
        sa.Column("edges_", sa.JSON(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_input_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_creation_input_tokens", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_execution_slug"), "execution", ["slug"])
    op.create_index(op.f("ix_execution_user_id"), "execution", ["user_id"])
    op.create_index(op.f("ix_execution_status"), "execution", ["status"])

    op.create_table(
        "devbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("repo_full_name", sa.String(), nullable=False),
        sa.Column("instance_id", sa.String(), nullable=True),
        sa.Column("snapshot_id", sa.String(), nullable=True),
        sa.Column("setup_slug", sa.String(), nullable=True),
        sa.Column("setup_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_devbox_user_id"), "devbox", ["user_id"])
    op.create_index(op.f("ix_devbox_name"), "devbox", ["name"])
    op.create_index(op.f("ix_devbox_repo_full_name"), "devbox", ["repo_full_name"])

    op.create_table(
        "secret",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("devbox_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("encrypted_value", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["devbox_id"], ["devbox.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_secret_devbox_id"), "secret", ["devbox_id"])
    op.create_index(op.f("ix_secret_name"), "secret", ["name"])


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table("secret")
    op.drop_table("devbox")
    op.drop_table("execution")
    op.drop_table("user")
