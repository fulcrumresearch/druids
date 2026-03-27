"""ProgramAgent -- wrapper returned by ctx.agent() for in-process programs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable
from uuid import UUID

from druids_server.db.session import get_session

if TYPE_CHECKING:
    from druids_server.lib.agents.base import Agent
    from druids_server.lib.execution import Execution


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecResult:
    """Result of running a command on an agent's VM."""

    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class ProgramAgent:
    """Wrapper returned by ctx.agent() that programs interact with.

    Matches the RuntimeAgent API so programs work identically whether
    run in-process (server) or in a sandbox (runtime).
    """

    def __init__(self, agent: Agent, execution: Execution) -> None:
        self._agent = agent
        self._execution = execution

    @property
    def name(self) -> str:
        return self._agent.name

    def on(self, tool_name: str) -> Callable:
        """Register a tool handler for this agent."""
        return self._agent.on(tool_name)

    async def send(self, message: str) -> None:
        """Send a message to this agent."""
        await self._agent.prompt(message)

    async def exec(self, command: str, *, user: str = "agent", timeout: int | None = None) -> ExecResult:
        """Run a command on this agent's VM."""
        result = await self._agent.machine.sandbox.exec(command, user=user, timeout=timeout)
        return ExecResult(exit_code=result.exit_code, stdout=result.stdout, stderr=result.stderr)

    async def snapshot_machine(self, name: str | None = None) -> str:
        """Snapshot this agent's VM and register it as a new devbox."""
        from druids_server.db.models.devbox import Devbox, get_devbox_by_name

        ex = self._execution
        snapshot_id = await self._agent.machine.snapshot()
        devbox_name = name or f"{ex.slug}-{self.name}"
        uid = UUID(ex.user_id) if isinstance(ex.user_id, str) else ex.user_id

        async with get_session() as db:
            existing = await get_devbox_by_name(db, uid, devbox_name)
            if existing:
                existing.snapshot_id = snapshot_id
                existing.updated_at = datetime.now(timezone.utc)
                db.add(existing)
            else:
                devbox = Devbox(
                    user_id=uid,
                    name=devbox_name,
                    repo_full_name=ex.repo_full_name or "",
                    snapshot_id=snapshot_id,
                    setup_completed_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                db.add(devbox)

        logger.info("Snapshot agent '%s' -> devbox '%s' snapshot=%s", self.name, devbox_name, snapshot_id)
        return devbox_name
