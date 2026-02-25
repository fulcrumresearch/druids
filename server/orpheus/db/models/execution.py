"""Execution record model."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel

from orpheus.utils.slugs import generate_execution_slug


if TYPE_CHECKING:
    from orpheus.db.models.task import Task


class ExecutionRecord(SQLModel, table=True):
    """A single execution attempt for a task."""

    __tablename__ = "execution"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    slug: str = Field(index=True)  # "gentle-nocturne-claude", unique per user
    task_id: UUID = Field(foreign_key="task.id", index=True)
    program_name: str
    root_instance_id: str | None = Field(default=None)
    status: str = Field(default="running", index=True)
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=sa.Column(sa.DateTime(timezone=True)),
    )
    stopped_at: datetime | None = Field(default=None, sa_column=sa.Column(sa.DateTime(timezone=True)))

    # PR workflow fields
    branch_name: str | None = Field(default=None)
    pr_number: int | None = Field(default=None)
    pr_url: str | None = Field(default=None)
    completed_at: datetime | None = Field(default=None, sa_column=sa.Column(sa.DateTime(timezone=True)))

    # Submit tool fields
    submit_summary: str | None = Field(default=None)

    # ELO comparison fields
    outcome: str | None = Field(default=None)  # "merged", "rejected", or NULL
    program_spec: str | None = Field(default=None, sa_column=sa.Column(sa.Text))  # raw YAML spec

    # Cumulative API token usage (incremented by proxy on each request)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    cache_read_input_tokens: int = Field(default=0)
    cache_creation_input_tokens: int = Field(default=0)


async def create_execution(db: AsyncSession, task: Task, program_name: str) -> ExecutionRecord:
    """Create a new execution record with auto-generated slug."""
    slug = generate_execution_slug(task.slug, program_name)
    branch_name = f"orpheus/{slug}"

    record = ExecutionRecord(
        slug=slug,
        task_id=task.id,
        program_name=program_name,
        branch_name=branch_name,
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
    """Get execution by slug (scoped to user via task)."""
    from orpheus.db.models.task import Task

    result = await db.execute(
        select(ExecutionRecord)
        .join(Task, ExecutionRecord.task_id == Task.id)
        .where(Task.user_id == user_id, ExecutionRecord.slug == slug)
    )
    return result.scalar_one_or_none()


async def get_execution_by_branch_name(db: AsyncSession, branch_name: str) -> ExecutionRecord | None:
    """Get execution by branch name (for webhook matching)."""
    result = await db.execute(select(ExecutionRecord).where(ExecutionRecord.branch_name == branch_name))
    return result.scalar_one_or_none()


async def get_execution_by_pr(db: AsyncSession, repo_full_name: str, pr_number: int) -> ExecutionRecord | None:
    """Find the most recent execution for a repository and PR number."""
    from orpheus.db.models.task import Task

    result = await db.execute(
        select(ExecutionRecord)
        .join(Task, ExecutionRecord.task_id == Task.id)
        .where(
            ExecutionRecord.pr_number == pr_number,
            Task.metadata_["repo_full_name"].as_string() == repo_full_name,
        )
        .order_by(ExecutionRecord.started_at.desc())
    )
    return result.scalars().first()


async def get_active_review_execution(db: AsyncSession, repo_full_name: str, pr_number: int) -> ExecutionRecord | None:
    """Find a running review execution for a given repo and PR number.

    Used to prevent duplicate review runs when multiple webhook deliveries fire.
    Matches by repo + PR number regardless of program name.
    """
    from orpheus.db.models.task import Task

    result = await db.execute(
        select(ExecutionRecord)
        .join(Task, ExecutionRecord.task_id == Task.id)
        .where(
            ExecutionRecord.status.in_(["running", "starting"]),
            Task.metadata_["repo_full_name"].as_string() == repo_full_name,
            Task.metadata_["pr_number"].as_string() == str(pr_number),
        )
    )
    return result.scalars().first()


async def get_user_execution_count(db: AsyncSession, user_id: UUID) -> int:
    """Count total executions for a user."""
    from orpheus.db.models.task import Task

    result = await db.execute(
        select(sa.func.count())
        .select_from(ExecutionRecord)
        .join(Task, ExecutionRecord.task_id == Task.id)
        .where(Task.user_id == user_id)
    )
    return result.scalar_one()


async def get_task_executions(db: AsyncSession, task_id: UUID) -> list[ExecutionRecord]:
    """Get all executions for a task."""
    query = (
        select(ExecutionRecord).where(ExecutionRecord.task_id == task_id).order_by(ExecutionRecord.started_at.desc())
    )
    result = await db.execute(query)
    return list(result.scalars().all())


async def increment_usage(
    db: AsyncSession,
    slug: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> int:
    """Atomically increment token usage counters for an execution.

    Returns the new cumulative output_tokens total (for budget enforcement).
    """
    result = await db.execute(
        sa.update(ExecutionRecord)
        .where(ExecutionRecord.slug == slug)
        .values(
            input_tokens=ExecutionRecord.input_tokens + input_tokens,
            output_tokens=ExecutionRecord.output_tokens + output_tokens,
            cache_read_input_tokens=ExecutionRecord.cache_read_input_tokens + cache_read_input_tokens,
            cache_creation_input_tokens=ExecutionRecord.cache_creation_input_tokens + cache_creation_input_tokens,
        )
        .returning(ExecutionRecord.output_tokens)
    )
    row = result.one_or_none()
    return row[0] if row else 0


async def update_execution_outcome(db: AsyncSession, execution_id: UUID, outcome: str) -> None:
    """Set the outcome field on an execution."""
    await db.execute(sa.update(ExecutionRecord).where(ExecutionRecord.id == execution_id).values(outcome=outcome))


async def update_execution(
    db: AsyncSession,
    execution_id: UUID,
    status: str | None = None,
    root_instance_id: str | None = None,
    pr_number: int | None = None,
    pr_url: str | None = None,
    summary: str | None = None,
) -> ExecutionRecord | None:
    record = await get_execution(db, execution_id)
    if not record:
        return None
    if status is not None:
        record.status = status
    if root_instance_id is not None:
        record.root_instance_id = root_instance_id
    if pr_number is not None:
        record.pr_number = pr_number
    if pr_url is not None:
        record.pr_url = pr_url
    if summary is not None:
        record.submit_summary = summary
    if status in ("stopped", "completed", "failed"):
        record.stopped_at = datetime.now(timezone.utc)
    if status == "completed":
        record.completed_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(record)
    return record
