"""Codex-backed agent."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from druids_server.config import settings
from druids_server.lib.acp import ACPConfig
from druids_server.lib.agents.base import Agent
from druids_server.lib.agents.config import AgentConfig


if TYPE_CHECKING:
    from druids_server.lib.machine import Machine


logger = logging.getLogger(__name__)


@dataclass
class CodexAgent(Agent):
    """Codex-backed agent."""

    @classmethod
    def auth_method(cls) -> str | None:
        """Codex uses OpenAI API key authentication."""
        return "openai-api-key"

    @classmethod
    def build_acp(
        cls,
        config: AgentConfig,
        *,
        slug: str,
        user_id: str,
        secrets: dict[str, str] | None = None,
    ) -> ACPConfig:
        """Build ACP config for codex-acp."""
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY not configured")
        env, mcp = cls._build_base_env(config, slug=slug, user_id=user_id, secrets=secrets)
        return ACPConfig(
            command="codex-acp",
            command_args=["-c", 'approval_policy="never"', "-c", 'sandbox_mode="danger-full-access"'],
            env={**env, "OPENAI_API_KEY": settings.openai_api_key.get_secret_value()},
            mcp_servers=mcp,
        )

    @classmethod
    async def _prepare_machine(
        cls,
        config: AgentConfig,
        machine: Machine,
        is_shared: bool,
    ) -> None:
        """Write CLI config and codex developer_instructions to the machine."""
        await super()._prepare_machine(config, machine, is_shared)
        await cls._write_codex_config(config, machine)

    @classmethod
    async def _write_codex_config(cls, config: AgentConfig, machine: Machine) -> None:
        """Write codex config files to the machine before bridge start."""
        if not config.system_prompt:
            return
        escaped = config.system_prompt.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        config_content = f'developer_instructions = """\n{escaped}\n"""'
        cmd = (
            f"sudo -u agent bash -c 'mkdir -p /home/agent/.codex' && "
            f"cat > /home/agent/.codex/config.toml << 'CODEX_CFG_EOF'\n{config_content}\nCODEX_CFG_EOF\n"
            f"chown agent:agent /home/agent/.codex/config.toml"
        )
        result = await machine.exec(cmd, check=False, user="root")
        if not result.ok:
            logger.warning("Failed to write Codex config: %s", result.stderr.strip())
