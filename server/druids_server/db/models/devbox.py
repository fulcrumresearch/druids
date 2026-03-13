"""Devbox model."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel


class Devbox(SQLModel, table=True):
    """A named environment snapshot for a user.

    Devboxes are decoupled from git. A devbox may optionally be associated with
    a repo (for convenience during setup), but the association is not required.
    Git credentials are provisioned per-agent at execution time, not stored here.
    """

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    name: str = Field(default="", index=True)
    repo_full_name: str = Field(default="", index=True)
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
    result = await db.execute(
        select(Devbox)
        .where(Devbox.user_id == user_id, Devbox.repo_full_name == repo_full_name)
        .order_by(sa.func.coalesce(Devbox.updated_at, Devbox.created_at).desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_devbox_by_name(db: AsyncSession, user_id: UUID, name: str) -> Devbox | None:
    """Get devbox by user_id and name."""
    result = await db.execute(select(Devbox).where(Devbox.user_id == user_id, Devbox.name == name))
    return result.scalar_one_or_none()


async def get_devbox_by_repo(db: AsyncSession, repo_full_name: str) -> Devbox | None:
    """Get the best devbox for a repo, across all users.

    Only returns devboxes with a completed snapshot (snapshot_id is not null).
    When multiple users have set up the same repo, picks the most recently updated one.
    """
    result = await db.execute(
        select(Devbox)
        .where(Devbox.repo_full_name == repo_full_name, Devbox.snapshot_id.isnot(None))
        .order_by(sa.func.coalesce(Devbox.updated_at, Devbox.created_at).desc(), Devbox.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_user_devboxes(db: AsyncSession, user_id: UUID) -> list[Devbox]:
    """Get all devboxes for a user."""
    result = await db.execute(
        select(Devbox).where(Devbox.user_id == user_id).order_by(sa.func.coalesce(Devbox.updated_at, Devbox.created_at).desc())
    )
    return list(result.scalars().all())


async def resolve_devbox(
    db: AsyncSession,
    user_id: UUID,
    *,
    name: str | None = None,
    repo_full_name: str | None = None,
) -> Devbox | None:
    """Resolve a devbox by name or repo. Name takes priority.

    When resolving by repo, tries the user's own devbox first, then falls back
    to any devbox with a completed snapshot for the same repo.
    """
    if name:
        return await get_devbox_by_name(db, user_id, name)
    if repo_full_name:
        own = await get_devbox(db, user_id, repo_full_name)
        if own:
            return own
        return await get_devbox_by_repo(db, repo_full_name)
    return None


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
