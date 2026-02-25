"""Spec model for content-addressed YAML program specs with ELO ratings."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel


def compute_spec_hash(yaml_str: str) -> str:
    """SHA-256 hash of a YAML spec, truncated to 16 hex chars."""
    return hashlib.sha256(yaml_str.encode()).hexdigest()[:16]


class Spec(SQLModel, table=True):
    """Content-addressed YAML program spec with ELO rating."""

    __tablename__ = "spec"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    hash: str = Field(sa_column=sa.Column(sa.String, unique=True, index=True, nullable=False))
    label: str = Field(default="")
    yaml: str = Field(sa_column=sa.Column(sa.Text, nullable=False))
    rating: float = Field(default=1500.0)
    num_comparisons: int = Field(default=0)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False),
    )


async def upsert_spec(db: AsyncSession, hash: str, label: str, yaml: str) -> Spec:
    """Insert a spec if it does not exist, otherwise return the existing one unchanged."""
    result = await db.execute(select(Spec).where(Spec.hash == hash))
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing

    spec = Spec(hash=hash, label=label, yaml=yaml)
    db.add(spec)
    await db.flush()
    await db.refresh(spec)
    return spec


async def get_spec_by_hash(db: AsyncSession, hash: str) -> Spec | None:
    """Get a spec by its content hash."""
    result = await db.execute(select(Spec).where(Spec.hash == hash))
    return result.scalar_one_or_none()


async def get_all_specs(db: AsyncSession) -> list[Spec]:
    """Get all specs sorted by rating descending (for leaderboard)."""
    result = await db.execute(select(Spec).order_by(Spec.rating.desc()))
    return list(result.scalars().all())
