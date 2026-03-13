"""Tests for the fork agent API endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from .conftest import SLUG


class TestForkAgentEndpoint:
    def test_fork_agent_success(self, client, mock_execution, mock_agent):
        """POST /executions/{slug}/agents/{name}/fork creates a forked agent."""
        mock_execution.agents["builder"] = mock_agent

        forked_agent = MagicMock()
        forked_agent.name = "builder-alt"
        mock_execution.fork_agent = AsyncMock(return_value=forked_agent)

        response = client.post(
            f"/executions/{SLUG}/agents/builder/fork",
            json={"name": "builder-alt", "prompt": "Try differently.", "context": False},
        )

        assert response.status_code == 200, f"Response: {response.json()}"
        assert response.json() == {"name": "builder-alt"}
        mock_execution.fork_agent.assert_called_once_with(
            "builder",
            "builder-alt",
            prompt="Try differently.",
            system_prompt=None,
            model=None,
            git=None,
            context=False,
        )

    def test_fork_agent_with_context(self, client, mock_execution, mock_agent):
        """POST with context=True passes it through to fork_agent."""
        mock_execution.agents["builder"] = mock_agent

        forked_agent = MagicMock()
        forked_agent.name = "fork-ctx"
        mock_execution.fork_agent = AsyncMock(return_value=forked_agent)

        response = client.post(
            f"/executions/{SLUG}/agents/builder/fork",
            json={"name": "fork-ctx", "context": True},
        )

        assert response.status_code == 200
        call_kwargs = mock_execution.fork_agent.call_args
        assert call_kwargs[1]["context"] is True

    def test_fork_agent_source_not_found(self, client, mock_execution):
        """POST returns 404 when the source agent does not exist."""
        mock_execution.agents = {}

        response = client.post(
            f"/executions/{SLUG}/agents/nonexistent/fork",
            json={"name": "fork-name"},
        )

        assert response.status_code == 404

    def test_fork_agent_execution_not_found(self, client):
        """POST returns 404 for an unknown execution slug."""
        response = client.post(
            "/executions/unknown-slug/agents/builder/fork",
            json={"name": "fork-name"},
        )

        assert response.status_code == 404
