"""UserSpec junction table linking users to their registered specs."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel

from orpheus.db.models.spec import Spec


class UserSpec(SQLModel, table=True):
    """Junction table: a user has registered a spec for automatic execution."""

    __tablename__ = "user_spec"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(sa_column=sa.Column(sa.Uuid(), sa.ForeignKey("user.id"), nullable=False, index=True))
    spec_id: UUID = Field(sa_column=sa.Column(sa.Uuid(), sa.ForeignKey("spec.id"), nullable=False, index=True))
    registered_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False),
    )

    __table_args__ = (sa.UniqueConstraint("user_id", "spec_id", name="uq_user_spec_user_spec"),)


async def register_user_spec(db: AsyncSession, user_id: UUID, spec_id: UUID) -> UserSpec:
    """Register a spec for a user. Raises ValueError if already registered."""
    result = await db.execute(select(UserSpec).where(UserSpec.user_id == user_id, UserSpec.spec_id == spec_id))
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing

    user_spec = UserSpec(user_id=user_id, spec_id=spec_id)
    db.add(user_spec)
    await db.flush()
    await db.refresh(user_spec)
    return user_spec


async def unregister_user_spec(db: AsyncSession, user_id: UUID, spec_id: UUID) -> bool:
    """Unregister a spec for a user. Returns True if found and deleted."""
    result = await db.execute(select(UserSpec).where(UserSpec.user_id == user_id, UserSpec.spec_id == spec_id))
    row = result.scalar_one_or_none()
    if row is None:
        return False
    await db.delete(row)
    await db.flush()
    return True


async def get_user_specs(db: AsyncSession, user_id: UUID) -> list[Spec]:
    """Get all specs registered by a user, joined through the user_spec table."""
    result = await db.execute(
        select(Spec).join(UserSpec, UserSpec.spec_id == Spec.id).where(UserSpec.user_id == user_id)
    )
    return list(result.scalars().all())
