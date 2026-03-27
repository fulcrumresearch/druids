"""add program table and execution.program_id

Revision ID: f8a54c1a865d
Revises: 27476bfd1225
Create Date: 2026-03-16 16:20:34.083137

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f8a54c1a865d"
down_revision: str | list[str] | None = "27476bfd1225"
branch_labels: str | list[str] | None = None
depends_on: str | list[str] | None = None


def upgrade() -> None:
    """Add program table and link executions to programs."""
    op.create_table(
        "program",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_program_source_hash"), "program", ["source_hash"])
    op.create_index(op.f("ix_program_user_id"), "program", ["user_id"])

    with op.batch_alter_table("execution") as batch_op:
        batch_op.add_column(sa.Column("program_id", sa.Uuid(), nullable=True))
        batch_op.create_index(op.f("ix_execution_program_id"), ["program_id"])
        batch_op.create_foreign_key("fk_execution_program_id", "program", ["program_id"], ["id"])


def downgrade() -> None:
    """Remove program table and execution.program_id."""
    with op.batch_alter_table("execution") as batch_op:
        batch_op.drop_constraint("fk_execution_program_id", type_="foreignkey")
        batch_op.drop_index(op.f("ix_execution_program_id"))
        batch_op.drop_column("program_id")
    op.drop_index(op.f("ix_program_user_id"), table_name="program")
    op.drop_index(op.f("ix_program_source_hash"), table_name="program")
    op.drop_table("program")
