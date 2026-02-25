"""Admin endpoints."""

import logging

from fastapi import APIRouter
from sqlalchemy import func, select

from orpheus.api.deps import AdminUser
from orpheus.db.models.devbox import Devbox
from orpheus.db.models.execution import ExecutionRecord
from orpheus.db.models.task import Task
from orpheus.db.models.user import User
from orpheus.db.session import get_session


router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)


@router.get("/usage")
async def get_usage(user: AdminUser):
    """Get platform-wide usage statistics."""
    async with get_session() as db:
        total_users = (await db.execute(select(func.count()).select_from(User))).scalar_one()
        subscribed_users = (
            await db.execute(select(func.count()).select_from(User).where(User.subscription_status == "active"))
        ).scalar_one()

        repos_configured = (
            await db.execute(
                select(func.count(func.distinct(Devbox.repo_full_name))).where(Devbox.snapshot_id.isnot(None))
            )
        ).scalar_one()

        total_tasks = (await db.execute(select(func.count()).select_from(Task))).scalar_one()

        execution_rows = (
            await db.execute(select(ExecutionRecord.status, func.count()).group_by(ExecutionRecord.status))
        ).all()
        executions_by_status = {row[0]: row[1] for row in execution_rows}
        total_executions = sum(executions_by_status.values())

        token_sums = (
            await db.execute(
                select(
                    func.coalesce(func.sum(ExecutionRecord.input_tokens), 0),
                    func.coalesce(func.sum(ExecutionRecord.output_tokens), 0),
                    func.coalesce(func.sum(ExecutionRecord.cache_read_input_tokens), 0),
                    func.coalesce(func.sum(ExecutionRecord.cache_creation_input_tokens), 0),
                )
            )
        ).one()

        recent_executions = (
            await db.execute(
                select(
                    ExecutionRecord.slug,
                    ExecutionRecord.status,
                    ExecutionRecord.program_name,
                    ExecutionRecord.started_at,
                    ExecutionRecord.pr_url,
                    ExecutionRecord.input_tokens,
                    ExecutionRecord.output_tokens,
                    Task.slug.label("task_slug"),
                    Task.metadata_["repo_full_name"].as_string().label("repo_full_name"),
                    User.github_login.label("user_login"),
                )
                .join(Task, ExecutionRecord.task_id == Task.id)
                .join(User, Task.user_id == User.id)
                .order_by(ExecutionRecord.started_at.desc())
                .limit(50)
            )
        ).all()

    return {
        "users": {
            "total": total_users,
            "subscribed": subscribed_users,
        },
        "repos_configured": repos_configured,
        "tasks": {
            "total": total_tasks,
        },
        "executions": {
            "total": total_executions,
            "by_status": executions_by_status,
        },
        "tokens": {
            "input": token_sums[0],
            "output": token_sums[1],
            "cache_read": token_sums[2],
            "cache_creation": token_sums[3],
        },
        "recent_executions": [
            {
                "slug": row.slug,
                "status": row.status,
                "program_name": row.program_name,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "pr_url": row.pr_url,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "task_slug": row.task_slug,
                "repo_full_name": row.repo_full_name,
                "user_login": row.user_login,
            }
            for row in recent_executions
        ],
    }
