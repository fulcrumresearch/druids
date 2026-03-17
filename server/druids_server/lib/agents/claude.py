"""Claude-backed agent."""

from __future__ import annotations

from dataclasses import dataclass

from druids_server.config import settings
from druids_server.lib.acp import ACPConfig
from druids_server.lib.agents.base import Agent
from druids_server.lib.agents.config import AgentConfig


@dataclass
class ClaudeAgent(Agent):
    """Claude-backed agent."""

    @classmethod
    def build_acp(
        cls,
        config: AgentConfig,
        *,
        slug: str,
        user_id: str,
        secrets: dict[str, str] | None = None,
    ) -> ACPConfig:
        """Build ACP config for claude-code-acp."""
        env, mcp = cls._build_base_env(config, slug=slug, user_id=user_id, secrets=secrets)
        env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key.get_secret_value()
        return ACPConfig(
            command="claude-code-acp",
            command_args=["--dangerously-skip-permissions"],
            env=env,
            mcp_servers=mcp,
        )
