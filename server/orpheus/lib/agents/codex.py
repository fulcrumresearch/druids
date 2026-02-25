"""CodexAgent - Agent backed by codex-acp."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from orpheus.config import settings
from orpheus.lib.agents.base import ACPConfig, Agent
from orpheus.lib.agents.skills import upload_skills


if TYPE_CHECKING:
    from orpheus.lib.machine import Machine


logger = logging.getLogger(__name__)


@dataclass
class CodexAgent(Agent):
    """Agent backed by codex-acp."""

    def __post_init__(self):
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY not configured")
        self.config = ACPConfig(
            command="codex-acp",
            command_args=["-c", 'approval_policy="never"', "-c", 'sandbox_mode="danger-full-access"'],
            env={"OPENAI_API_KEY": settings.openai_api_key.get_secret_value()},
        )

    async def _write_config(self, machine: Machine) -> None:
        """Write developer_instructions and upload skills for codex-acp.

        codex-acp ignores `_meta.systemPrompt.append` in the ACP session/new
        request. The only way to deliver a system prompt is to write it to
        `~/.codex/config.toml` as `developer_instructions` before the bridge
        starts.
        """
        if self.system_prompt:
            escaped = self.system_prompt.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
            config_content = f'developer_instructions = """\n{escaped}\n"""'
            cmd = (
                f"sudo -u agent bash -c 'mkdir -p /home/agent/.codex' && "
                f"cat > /home/agent/.codex/config.toml << 'CODEX_CFG_EOF'\n{config_content}\nCODEX_CFG_EOF\n"
                f"chown agent:agent /home/agent/.codex/config.toml"
            )
            result = await machine.exec(cmd, check=False, user="root")
            if not result.ok:
                logger.warning(f"Failed to write Codex config: {result.stderr.strip()}")

        await upload_skills(machine, "/home/agent/.agents/skills")
