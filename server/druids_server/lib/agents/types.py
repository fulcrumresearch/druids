"""Agent type definitions and class lookup."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal


if TYPE_CHECKING:
    from druids_server.lib.agents.base import Agent

AgentType = Literal["claude", "codex"]


def agent_class(agent_type: AgentType) -> type[Agent]:
    """Return the Agent subclass for the given agent type."""
    from druids_server.lib.agents.claude import ClaudeAgent
    from druids_server.lib.agents.codex import CodexAgent

    _AGENT_CLASSES: dict[str, type[Agent]] = {
        "claude": ClaudeAgent,
        "codex": CodexAgent,
    }
    cls = _AGENT_CLASSES.get(agent_type)
    if cls is None:
        raise ValueError(f"Unknown agent type: {agent_type!r}")
    return cls
