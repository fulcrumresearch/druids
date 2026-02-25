"""Devbox model."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel


class Devbox(SQLModel, table=True):
    """A per-repo devbox snapshot for a user."""

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    repo_full_name: str = Field(index=True)
    instance_id: str | None = Field(default=None)
    snapshot_id: str | None = Field(default=None)
    setup_slug: str | None = Field(default=None)
    setup_completed_at: datetime | None = Field(default=None, sa_column=sa.Column(sa.DateTime(timezone=True)))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=sa.Column(sa.DateTime(timezone=True)),
    )
    updated_at: datetime | None = Field(default=None, sa_column=sa.Column(sa.DateTime(timezone=True)))


async def get_devbox(db: AsyncSession, user_id: UUID, repo_full_name: str) -> Devbox | None:
    """Get devbox by user_id and repo_full_name."""
    result = await db.execute(select(Devbox).where(Devbox.user_id == user_id, Devbox.repo_full_name == repo_full_name))
    return result.scalar_one_or_none()


async def get_devbox_by_repo(db: AsyncSession, repo_full_name: str) -> Devbox | None:
    """Get the most recently updated devbox for a repo, across all users.

    Only returns devboxes with a completed snapshot (snapshot_id is not null).
    When multiple users have set up the same repo, picks the most recently updated one.
    """
    result = await db.execute(
        select(Devbox)
        .where(Devbox.repo_full_name == repo_full_name, Devbox.snapshot_id.isnot(None))
        .order_by(Devbox.updated_at.desc().nulls_last(), Devbox.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_user_devboxes(db: AsyncSession, user_id: UUID) -> list[Devbox]:
    """Get all devboxes for a user."""
    result = await db.execute(
        select(Devbox).where(Devbox.user_id == user_id).order_by(Devbox.updated_at.desc().nulls_last())
    )
    return list(result.scalars().all())


async def get_or_create_devbox(db: AsyncSession, user_id: UUID, repo_full_name: str) -> Devbox:
    """Get existing devbox or create new one."""
    devbox = await get_devbox(db, user_id, repo_full_name)
    if devbox:
        return devbox
    devbox = Devbox(user_id=user_id, repo_full_name=repo_full_name)
    db.add(devbox)
    await db.flush()
    await db.refresh(devbox)
    return devbox
