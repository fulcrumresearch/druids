"""ClaudeAgent - Agent backed by claude-code-acp."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from orpheus.lib.agents.base import ACPConfig, Agent
from orpheus.lib.agents.skills import upload_skills


if TYPE_CHECKING:
    from orpheus.lib.machine import Machine


logger = logging.getLogger(__name__)


@dataclass
class ClaudeAgent(Agent):
    """Agent backed by claude-code-acp.

    Model selection: the model is set via the ACP unstable_setSessionModel RPC
    after session creation (see Execution._connect_agent). Writing to
    ~/.claude/settings.json does not work because the claude subprocess that
    claude-code-acp spawns overwrites that file on startup. The ANTHROPIC_MODEL
    env var is also ignored since claude-code-acp dropped env-based model
    selection in favor of the set_model RPC.

    See: https://github.com/xenodism/agent-shell/issues/127
    """

    model: str = "claude-opus-4-6"

    def __post_init__(self):
        self.config = ACPConfig(
            command="claude-code-acp",
            command_args=["--dangerously-skip-permissions"],
            env={},
        )

    async def _write_config(self, machine: Machine) -> None:
        """Upload skills to the Claude Code skills directory."""
        await upload_skills(machine, "/home/agent/.claude/skills")
