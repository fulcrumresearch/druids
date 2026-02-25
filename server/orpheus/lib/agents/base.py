"""Agent - an ACP agent that extends Program."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from orpheus.lib.program import Program


if TYPE_CHECKING:
    from orpheus.lib.machine import Machine


logger = logging.getLogger(__name__)


# Instance source configuration
# - "devbox": Create fresh VM from the devbox snapshot (default)
# - "fork": Fork from another Machine (copy-on-write)
InstanceSource = Literal["devbox", "fork"]


@dataclass
class ACPConfig:
    """Configuration for an ACP agent.

    Holds command, arguments, environment, and MCP servers for the ACP process.
    The working directory is owned by Agent, not ACPConfig, because it is an
    agent-level concept (repo checkout location) that both git operations and the
    bridge start payload need. A single source of truth avoids silent divergence.
    """

    command: str = "claude-code-acp"
    command_args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    mcp_servers: dict[str, Any] = field(default_factory=dict)

    def to_bridge_start(self, working_directory: str, monitor_prompt: str | None = None) -> dict:
        """Convert to bridge /start request payload.

        Args:
            working_directory: Directory to start the ACP process in.
                               Supplied by Agent.working_directory.
            monitor_prompt: Optional prompt for the bridge-local monitor.
        """
        payload = {
            "command": self.command,
            "args": list(self.command_args),
            "env": self.env,
            "working_directory": working_directory,
        }
        if monitor_prompt:
            payload["monitor_prompt"] = monitor_prompt
        return payload


@dataclass
class Agent(Program):
    """A Program that runs an ACP process on a Machine."""

    config: ACPConfig = field(default_factory=ACPConfig)
    model: str | None = None  # Set via ACP set_model RPC after session creation
    user_prompt: str | None = None  # User message sent on first connect
    system_prompt: str | None = None  # System prompt for the agent backend
    monitor_prompt: str | None = None  # Prompt for bridge-local monitor
    working_directory: str = "/home/agent"

    @property
    def is_agent(self) -> bool:
        return True

    # Instance source: "devbox" (provision from snapshot) or "fork" (COW from parent)
    instance_source: InstanceSource = "devbox"

    # Runtime: Machine that owns the VM (set by Execution._provision_machine)
    machine: Machine | None = field(default=None, repr=False)

    # Set by Execution.spawn when instance_source="fork"
    _fork_source: Machine | None = field(default=None, repr=False)

    # Git checkout (set by Execution before exec)
    repo_full_name: str | None = None
    git_branch: str | None = None

    async def exec(self, machine: Machine) -> list[Program]:
        """Start the ACP process on the given machine."""
        self.machine = machine
        logger.info("Agent.exec name=%s instance=%s", self.name, machine.instance_id)
        await self._write_config(machine)
        logger.info("Agent.exec name=%s starting bridge", self.name)
        await machine.ensure_bridge(self.config, self.monitor_prompt, self.working_directory)
        logger.info("Agent.exec name=%s bridge ready, bridge_id=%s", self.name, machine.bridge_id)
        return []

    async def _write_config(self, machine: Machine) -> None:
        """Override in subclasses to write backend-specific config."""
        pass
