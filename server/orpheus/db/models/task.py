"""Task model."""

import secrets
from datetime import datetime, timezone
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel

from orpheus.utils.slugs import generate_task_slug


class Task(SQLModel, table=True):
    """A task submitted by a user."""

    __table_args__ = (sa.UniqueConstraint("user_id", "slug", name="uq_task_user_slug"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    slug: str = Field(index=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    spec: str
    snapshot_id: str | None = Field(default=None)
    is_active: bool = Field(default=True, index=True)
    metadata_: dict = Field(default_factory=dict, sa_column=sa.Column(sa.JSON))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=sa.Column(sa.DateTime(timezone=True)),
    )
    updated_at: datetime | None = Field(default=None, sa_column=sa.Column(sa.DateTime(timezone=True)))


async def get_task(db: AsyncSession, task_id: UUID) -> Task | None:
    """Get task by ID."""
    result = await db.execute(select(Task).where(Task.id == task_id))
    return result.scalar_one_or_none()


async def get_task_by_slug(db: AsyncSession, user_id: UUID, slug: str) -> Task | None:
    """Get task by slug (scoped to user)."""
    result = await db.execute(select(Task).where(Task.user_id == user_id, Task.slug == slug))
    return result.scalar_one_or_none()


async def create_task(
    db: AsyncSession,
    user_id: UUID,
    spec: str,
    snapshot_id: str | None = None,
    metadata: dict | None = None,
) -> Task:
    """Create a new task with auto-generated slug."""
    # Generate unique slug for this user
    for _ in range(10):  # Max retries for collision
        slug = generate_task_slug()
        existing = await get_task_by_slug(db, user_id, slug)
        if not existing:
            break
    else:
        # Fallback: append random suffix
        slug = f"{generate_task_slug()}-{secrets.token_hex(2)}"

    task = Task(
        slug=slug,
        user_id=user_id,
        spec=spec,
        snapshot_id=snapshot_id,
        metadata_=metadata or {},
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    return task


async def get_user_tasks(db: AsyncSession, user_id: UUID, active_only: bool = False) -> list[Task]:
    """Get all tasks for a user."""
    query = select(Task).where(Task.user_id == user_id)
    if active_only:
        query = query.where(Task.is_active == True)  # noqa: E712
    query = query.order_by(Task.created_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


async def update_task_status(db: AsyncSession, task_id: UUID, is_active: bool) -> Task | None:
    """Update task active status."""
    task = await get_task(db, task_id)
    if task:
        task.is_active = is_active
        task.updated_at = datetime.now(timezone.utc)
        await db.flush()
        await db.refresh(task)
    return task
