"""Program model."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel


class Program(SQLModel, table=True):
    """A saved program source.

    Programs are deduplicated per user by content hash so that re-running the
    same source code reuses the existing record.
    """

    __tablename__ = "program"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    source: str = Field(sa_column=sa.Column(sa.Text(), nullable=False))
    source_hash: str = Field(index=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=sa.Column(sa.DateTime(timezone=True)),
    )


def hash_source(source: str) -> str:
    """Compute a SHA-256 hash of program source code."""
    return hashlib.sha256(source.encode()).hexdigest()


async def get_or_create_program(
    db: AsyncSession,
    user_id: UUID,
    source: str,
) -> Program:
    """Return an existing program with the same source, or create a new one."""
    content_hash = hash_source(source)
    result = await db.execute(select(Program).where(Program.user_id == user_id, Program.source_hash == content_hash))
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    program = Program(user_id=user_id, source=source, source_hash=content_hash)
    db.add(program)
    await db.flush()
    await db.refresh(program)
    return program


async def get_program(db: AsyncSession, program_id: UUID) -> Program | None:
    """Get a program by ID."""
    result = await db.execute(select(Program).where(Program.id == program_id))
    return result.scalar_one_or_none()


async def get_user_programs(db: AsyncSession, user_id: UUID) -> list[Program]:
    """Get all programs for a user, most recent first."""
    result = await db.execute(select(Program).where(Program.user_id == user_id).order_by(Program.created_at.desc()))
    return list(result.scalars().all())
