"""Launch helper -- creates and starts executions."""

import asyncio
import logging

from orpheus.api.deps import get_executions_registry
from orpheus.api.github import get_installation_token
from orpheus.config import settings
from orpheus.lib.execution import Execution
from orpheus.lib.machine import Machine
from orpheus.db.models.execution import create_execution
from orpheus.db.models.task import Task, create_task
from orpheus.db.models.user import User
from orpheus.db.session import get_session


logger = logging.getLogger(__name__)


async def launch_execution(
    root,
    *,
    user: User,
    devbox_machine: Machine,
    repo_full_name: str,
    task: Task | None = None,
    spec: str = "",
    task_metadata: dict | None = None,
    git_branch: str | None = None,
    pr_number: int | None = None,
    pr_url: str | None = None,
    task_spec: str | None = None,
    program_spec: str | None = None,
) -> Execution:
    """Create a task (if needed) and execution, then start it in the background.

    Pass an existing `task` to add another execution to it (multi-program case).
    Otherwise a new task is created from `spec` and `task_metadata`.
    """
    # Validate GitHub access before persisting records so GitHub API failures
    # don't leave orphaned executions marked active in the database.
    # Machine fetches its own token during start().
    await get_installation_token(repo_full_name)

    async with get_session() as db:
        if task is None:
            task = await create_task(
                db,
                user_id=user.id,
                spec=spec,
                snapshot_id=devbox_machine.snapshot_id,
                metadata=task_metadata or {},
            )
        record = await create_execution(db, task, root.name)
        if pr_number is not None:
            record.pr_number = pr_number
        if pr_url is not None:
            record.pr_url = pr_url
        if program_spec is not None:
            record.program_spec = program_spec

    user_id_str = str(user.id)
    mcp_url = f"{settings.base_url}/mcp/exec/"

    registry = get_executions_registry()
    if user_id_str not in registry:
        registry[user_id_str] = {}

    resolved_task_spec = task_spec or (task.spec if task else spec) or None
    ex = Execution(
        id=record.id,
        slug=record.slug,
        root=root,
        user_id=user_id_str,
        devbox_machine=devbox_machine,
        task_id=task.id,
        repo_full_name=repo_full_name,
        git_branch=git_branch,
        task_spec=resolved_task_spec,
        mcp_url=mcp_url,
        mcp_auth_token=user.access_token,
    )
    registry[user_id_str][ex.slug] = ex

    logger.info(
        "launch_execution slug=%s task=%s root=%s snapshot=%s repo=%s",
        ex.slug,
        task.id,
        root.name,
        devbox_machine.snapshot_id,
        repo_full_name,
    )
    asyncio.create_task(ex.start_and_wait())
    return ex
