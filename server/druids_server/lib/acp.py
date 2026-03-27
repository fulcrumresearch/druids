"""ACP process configuration for the bridge."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ACPConfig(BaseModel):
    """Configuration for an ACP process.

    Built transiently during agent provisioning and passed to
    machine.ensure_bridge(). Not stored on the agent after creation.
    """

    model_config = ConfigDict(populate_by_name=True)

    command: str = "claude-code-acp"
    command_args: list[str] = []
    env: dict[str, str] = {}
    mcp_servers: dict[str, Any] = {}

    def to_bridge_start(self, working_directory: str) -> dict:
        """Convert to bridge /start request payload."""
        return {
            "command": self.command,
            "args": list(self.command_args),
            "env": dict(self.env),
            "working_directory": working_directory,
        }
