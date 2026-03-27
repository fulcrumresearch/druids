"""Agent config -- validated configuration for creating an agent."""

from __future__ import annotations

from string import Template
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from druids_server.lib.agents.types import AgentType
from druids_server.utils.templates import resolve_secret_refs


class AgentConfig(BaseModel):
    """Validated agent configuration. Describes what agent to create."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    agent_type: AgentType = "claude"
    model: str | None = None
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = None
    reasoning_summary: Literal["auto", "concise", "detailed", "none"] | None = None
    prompt: str | None = None
    system_prompt: str | None = None
    git: Literal["read", "post", "write"] | None = None
    working_directory: str = "/home/agent"
    mcp_servers: dict[str, Any] | None = None
    web_search: Literal["disabled", "cached", "live"] | None = None


def _is_openai_model(model: str) -> bool:
    """True if the model string belongs to the OpenAI / Codex backend."""
    return model == "codex" or model.startswith(("gpt", "o1", "o3", "o4", "codex-"))


def _resolve_model(model: str | None, agent_type: AgentType) -> tuple[str | None, AgentType]:
    """Normalize the model string and infer agent_type if needed.

    Bare backend names ("claude", "codex") are treated as "use the default"
    and resolved to None so set_model is skipped. OpenAI model IDs infer
    agent_type="codex" when the caller didn't set it explicitly.
    """
    if not model or model == "claude":
        return None, agent_type
    if _is_openai_model(model):
        resolved = None if model == "codex" else model
        return resolved, "codex"
    return model, agent_type


def create_agent(
    name: str,
    *,
    agent_type: AgentType = "claude",
    model: str | None = None,
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = None,
    reasoning_summary: Literal["auto", "concise", "detailed", "none"] | None = None,
    prompt: str | None = None,
    system_prompt: str | None = None,
    working_directory: str | None = None,
    git: str | None = None,
    mcp_servers: dict[str, Any] | None = None,
    web_search: Literal["disabled", "cached", "live"] | None = None,
    slug: str,
    user_id: str,
    secrets: dict[str, str] | None = None,
    spec: str | None = None,
) -> AgentConfig:
    """Create an agent config with templates resolved and secrets injected."""
    if git and git not in ("read", "post", "write"):
        raise ValueError(f"git must be 'read', 'post', or 'write', got '{git}'")

    resolved_model, resolved_type = _resolve_model(model, agent_type)
    resolved_dir = working_directory or "/home/agent"

    template_vars = {
        "execution_slug": slug,
        "agent_name": name,
        "working_directory": resolved_dir,
        "branch_name": f"druids/{slug}",
    }
    if spec:
        template_vars["spec"] = spec

    resolved_system = Template(system_prompt).safe_substitute(template_vars) if system_prompt else None
    resolved_user = Template(prompt).safe_substitute(template_vars) if prompt else None

    resolved_mcp = mcp_servers
    if secrets and resolved_mcp:
        resolved_mcp = resolve_secret_refs(resolved_mcp, secrets)

    return AgentConfig(
        name=name,
        agent_type=resolved_type,
        model=resolved_model,
        reasoning_effort=reasoning_effort,
        reasoning_summary=reasoning_summary,
        prompt=resolved_user,
        system_prompt=resolved_system,
        working_directory=resolved_dir,
        git=git,
        mcp_servers=resolved_mcp,
        web_search=web_search,
    )
