"""Tests for Agent."""

from unittest.mock import MagicMock, patch

import pytest
from orpheus.lib.agents.base import ACPConfig, Agent
from orpheus.lib.agents.claude import ClaudeAgent
from orpheus.lib.agents.codex import CodexAgent


class TestACPConfig:
    def test_to_bridge_start(self):
        """Correct payload shape."""
        config = ACPConfig(
            command="claude-code-acp",
            command_args=["--verbose"],
            env={"ANTHROPIC_API_KEY": "test-key"},
        )

        payload = config.to_bridge_start(working_directory="/workspace")

        assert payload == {
            "command": "claude-code-acp",
            "args": ["--verbose"],
            "env": {"ANTHROPIC_API_KEY": "test-key"},
            "working_directory": "/workspace",
        }

    def test_defaults(self):
        """Default values are sensible."""
        config = ACPConfig()

        payload = config.to_bridge_start(working_directory="/home/agent")

        assert payload["command"] == "claude-code-acp"
        assert payload["args"] == []
        assert payload["env"] == {}
        assert payload["working_directory"] == "/home/agent"


class TestAgent:
    def test_agent_is_program(self):
        """Agent extends Program."""
        agent = Agent(name="test")

        assert hasattr(agent, "name")
        assert hasattr(agent, "constructors")
        assert hasattr(agent, "exec")

    def test_agent_has_config(self):
        """Agent has config."""
        config = ACPConfig(command="test-cmd")
        agent = Agent(name="test", config=config)

        assert agent.config.command == "test-cmd"

    def test_system_prompt_defaults_to_none(self):
        """system_prompt defaults to None."""
        agent = Agent(name="test")
        assert agent.system_prompt is None

    def test_system_prompt_accepts_value(self):
        """system_prompt can be set."""
        agent = Agent(name="test", system_prompt="You are a helpful agent.")
        assert agent.system_prompt == "You are a helpful agent."

    def test_working_directory_default(self):
        """working_directory defaults to /home/agent."""
        agent = Agent(name="test")
        assert agent.working_directory == "/home/agent"

    def test_working_directory_custom(self):
        """working_directory can be overridden."""
        agent = Agent(name="test", working_directory="/workspace")
        assert agent.working_directory == "/workspace"


class TestClaudeAgent:
    def test_creates_claude_config(self):
        """ClaudeAgent creates an ACPConfig with claude-code-acp command."""
        agent = ClaudeAgent(name="claude")

        assert agent.config.command == "claude-code-acp"
        assert agent.config.env == {}

    def test_default_model(self):
        """Default model is claude-opus-4-6."""
        agent = ClaudeAgent(name="claude")

        assert agent.model == "claude-opus-4-6"

    def test_custom_model(self):
        """Model can be overridden."""
        agent = ClaudeAgent(name="claude", model="claude-sonnet-4-5-20250929")

        assert agent.model == "claude-sonnet-4-5-20250929"

    def test_working_directory_in_bridge_payload(self):
        """working_directory is passed through to_bridge_start()."""
        agent = ClaudeAgent(name="claude", working_directory="/home/agent/myrepo")
        payload = agent.config.to_bridge_start(agent.working_directory)

        assert agent.working_directory == "/home/agent/myrepo"
        assert payload["working_directory"] == "/home/agent/myrepo"

    def test_to_bridge_start_has_permission_bypass(self):
        """ClaudeAgent's config gets --dangerously-skip-permissions."""
        agent = ClaudeAgent(name="claude")
        payload = agent.config.to_bridge_start(agent.working_directory)

        assert "--dangerously-skip-permissions" in payload["args"]

    def test_is_agent(self):
        """ClaudeAgent is an agent."""
        agent = ClaudeAgent(name="claude")
        assert agent.is_agent is True


class TestCodexAgent:
    @patch("orpheus.lib.agents.codex.settings")
    def test_creates_codex_config(self, mock_settings_obj):
        """CodexAgent creates an ACPConfig with codex-acp command."""
        mock_key = MagicMock()
        mock_key.get_secret_value.return_value = "sk-test"
        mock_settings_obj.openai_api_key = mock_key

        agent = CodexAgent(name="codex")

        assert agent.config.command == "codex-acp"
        assert agent.config.env["OPENAI_API_KEY"] == "sk-test"

    @patch("orpheus.lib.agents.codex.settings")
    def test_raises_without_openai_key(self, mock_settings_obj):
        """CodexAgent raises ValueError when OPENAI_API_KEY is not configured."""
        mock_settings_obj.openai_api_key = None

        with pytest.raises(ValueError, match="OPENAI_API_KEY not configured"):
            CodexAgent(name="codex")

    @patch("orpheus.lib.agents.codex.settings")
    def test_working_directory_in_bridge_payload(self, mock_settings_obj):
        """working_directory is passed through to_bridge_start()."""
        mock_key = MagicMock()
        mock_key.get_secret_value.return_value = "sk-test"
        mock_settings_obj.openai_api_key = mock_key

        agent = CodexAgent(name="codex", working_directory="/home/agent/myrepo")
        payload = agent.config.to_bridge_start(agent.working_directory)

        assert agent.working_directory == "/home/agent/myrepo"
        assert payload["working_directory"] == "/home/agent/myrepo"

    @patch("orpheus.lib.agents.codex.settings")
    def test_to_bridge_start_has_permission_bypass(self, mock_settings_obj):
        """CodexAgent's config gets codex permission bypass flags."""
        mock_key = MagicMock()
        mock_key.get_secret_value.return_value = "sk-test"
        mock_settings_obj.openai_api_key = mock_key

        agent = CodexAgent(name="codex")
        payload = agent.config.to_bridge_start(agent.working_directory)

        assert "-c" in payload["args"]
        assert 'approval_policy="never"' in payload["args"]

    @patch("orpheus.lib.agents.codex.settings")
    def test_is_agent(self, mock_settings_obj):
        """CodexAgent is an agent."""
        mock_key = MagicMock()
        mock_key.get_secret_value.return_value = "sk-test"
        mock_settings_obj.openai_api_key = mock_key

        agent = CodexAgent(name="codex")
        assert agent.is_agent is True
