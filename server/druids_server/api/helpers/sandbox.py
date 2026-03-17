"""Sandbox resolution helpers for API routes."""

from __future__ import annotations

import logging

from druids_server.db.models.devbox import get_devbox
from druids_server.db.models.user import User
from druids_server.db.session import get_session
from druids_server.lib.execution import Execution
from druids_server.lib.machine import Machine
from druids_server.lib.sandbox.base import Sandbox


logger = logging.getLogger(__name__)

DEVBOX_TTL_SECONDS = 3600


class InstanceNotFound(Exception):
    """Raised when a target VM instance cannot be resolved."""


async def resolve_sandbox(
    user: User,
    executions: dict[str, Execution],
    repo: str | None = None,
    execution_slug: str | None = None,
    agent_name: str | None = None,
) -> Sandbox:
    """Resolve a Sandbox from either devbox or execution context.

    Raises `InstanceNotFound` if the target cannot be resolved.
    Raises `ValueError` if neither targeting option is provided.
    """
    if execution_slug and agent_name:
        ex = executions.get(execution_slug)
        if not ex:
            raise InstanceNotFound(f"Execution {execution_slug} not found")
        agent = ex.agents.get(agent_name)
        if not agent:
            raise InstanceNotFound(f"Agent {agent_name} not found")
        if not agent.machine or not agent.machine.sandbox:
            raise InstanceNotFound(f"Instance for {agent_name} not found")
        return agent.machine.sandbox

    if repo:
        async with get_session() as db:
            devbox = await get_devbox(db, user.id, repo)
            if not devbox:
                raise InstanceNotFound(f"No devbox for {repo}. Run 'druids devbox create' first.")

            if devbox.instance_id:
                try:
                    return await Sandbox.get(devbox.instance_id)
                except Exception:
                    logger.info("resolve_sandbox: instance %s not found, starting fresh", devbox.instance_id)

            sandbox = await Sandbox.create(
                snapshot_id=devbox.snapshot_id,
                metadata={"druids:devbox": "true", "druids:repo": repo},
                ttl_seconds=DEVBOX_TTL_SECONDS,
            )
            machine = Machine(sandbox=sandbox, snapshot_id=devbox.snapshot_id or "")
            await machine.init()
            devbox.instance_id = machine.instance_id
            db.add(devbox)
            return sandbox

    raise ValueError("Either repo or execution_slug+agent_name is required")
