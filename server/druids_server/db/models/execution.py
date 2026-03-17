"""Execution record model."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel

from druids_server.utils.slugs import generate_task_slug


class ExecutionRecord(SQLModel, table=True):
    """A single execution."""

    __tablename__ = "execution"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    slug: str = Field(index=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    spec: str = Field(default="")
    repo_full_name: str | None = Field(default=None)
    metadata_: dict = Field(default_factory=dict, sa_column=sa.Column(sa.JSON))
    status: str = Field(default="starting", index=True)
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=sa.Column(sa.DateTime(timezone=True)),
    )
    stopped_at: datetime | None = Field(default=None, sa_column=sa.Column(sa.DateTime(timezone=True)))
    completed_at: datetime | None = Field(default=None, sa_column=sa.Column(sa.DateTime(timezone=True)))

    # PR workflow fields
    branch_name: str | None = Field(default=None)
    pr_number: int | None = Field(default=None)
    pr_url: str | None = Field(default=None)

    # Error message when execution fails
    error: str | None = Field(default=None, sa_column=sa.Column(sa.Text()))

    # Graph topology (persisted so the UI can render after completion)
    agents_: list = Field(default_factory=list, sa_column=sa.Column(sa.JSON, default=list))
    edges_: list = Field(default_factory=list, sa_column=sa.Column(sa.JSON, default=list))

    # Program that was executed (nullable for backward compat with old records)
    program_id: UUID | None = Field(default=None, foreign_key="program.id", index=True)

    # Cumulative API token usage (incremented by proxy on each request)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    cache_read_input_tokens: int = Field(default=0)
    cache_creation_input_tokens: int = Field(default=0)


async def create_execution(
    db: AsyncSession,
    user_id: UUID,
    spec: str,
    repo_full_name: str | None = None,
    metadata: dict | None = None,
    program_id: UUID | None = None,
) -> ExecutionRecord:
    """Create a new execution record with auto-generated slug."""
    for _ in range(10):
        slug = generate_task_slug()
        existing = await get_execution_by_slug(db, user_id, slug)
        if not existing:
            break
    else:
        slug = f"{generate_task_slug()}-{secrets.token_hex(2)}"

    branch_name = f"druids/{slug}"

    record = ExecutionRecord(
        slug=slug,
        user_id=user_id,
        spec=spec,
        repo_full_name=repo_full_name,
        metadata_=metadata or {},
        branch_name=branch_name,
        program_id=program_id,
    )
    db.add(record)
    await db.flush()
    await db.refresh(record)
    return record


async def get_execution(db: AsyncSession, execution_id: UUID) -> ExecutionRecord | None:
    """Get execution by ID."""
    result = await db.execute(select(ExecutionRecord).where(ExecutionRecord.id == execution_id))
    return result.scalar_one_or_none()


async def get_execution_by_slug(db: AsyncSession, user_id: UUID, slug: str) -> ExecutionRecord | None:
    """Get execution by slug (scoped to user)."""
    result = await db.execute(
        select(ExecutionRecord).where(ExecutionRecord.user_id == user_id, ExecutionRecord.slug == slug)
    )
    return result.scalar_one_or_none()


async def get_user_executions(db: AsyncSession, user_id: UUID, active_only: bool = False) -> list[ExecutionRecord]:
    """Get all executions for a user."""
    query = select(ExecutionRecord).where(ExecutionRecord.user_id == user_id)
    if active_only:
        query = query.where(ExecutionRecord.status.in_(["running", "starting"]))
    query = query.order_by(ExecutionRecord.started_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


async def increment_usage(
    db: AsyncSession,
    execution_id: UUID,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> int:
    """Atomically increment token usage counters for an execution.

    Returns the new cumulative output_tokens total (for budget enforcement).
    """
    await db.execute(
        sa.update(ExecutionRecord)
        .where(ExecutionRecord.id == execution_id)
        .values(
            input_tokens=ExecutionRecord.input_tokens + input_tokens,
            output_tokens=ExecutionRecord.output_tokens + output_tokens,
            cache_read_input_tokens=ExecutionRecord.cache_read_input_tokens + cache_read_input_tokens,
            cache_creation_input_tokens=ExecutionRecord.cache_creation_input_tokens + cache_creation_input_tokens,
        )
    )
    # Fetch the updated value (works on both Postgres and SQLite)
    result = await db.execute(select(ExecutionRecord.output_tokens).where(ExecutionRecord.id == execution_id))
    row = result.one_or_none()
    return row[0] if row else 0


async def update_execution(
    db: AsyncSession,
    execution_id: UUID,
    status: str | None = None,
    pr_number: int | None = None,
    pr_url: str | None = None,
    error: str | None = None,
    agents: list | None = None,
    edges: list | None = None,
) -> ExecutionRecord | None:
    """Update mutable fields on an execution record."""
    record = await get_execution(db, execution_id)
    if not record:
        return None
    if status is not None:
        record.status = status
    if pr_number is not None:
        record.pr_number = pr_number
    if pr_url is not None:
        record.pr_url = pr_url
    if error is not None:
        record.error = error
    if agents is not None:
        record.agents_ = agents
    if edges is not None:
        record.edges_ = edges
    if status in ("stopped", "completed", "failed"):
        record.stopped_at = datetime.now(timezone.utc)
    if status == "completed":
        record.completed_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(record)
    return record
