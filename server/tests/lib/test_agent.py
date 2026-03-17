"""Tests for agent configuration and factory."""

from unittest.mock import MagicMock, patch

import pytest
from druids_server.lib.acp import ACPConfig
from druids_server.lib.agents import create_agent
from druids_server.lib.agents.base import Agent
from druids_server.lib.agents.claude import ClaudeAgent
from druids_server.lib.agents.codex import CodexAgent
from druids_server.lib.agents.config import AgentConfig


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


class TestAgentConfig:
    def test_defaults(self):
        """AgentConfig has sensible defaults."""
        config = AgentConfig(name="test")

        assert config.name == "test"
        assert config.agent_type == "claude"
        assert config.model is None
        assert config.prompt is None
        assert config.system_prompt is None
        assert config.working_directory == "/home/agent"
        assert config.git is None

    def test_working_directory_custom(self):
        """working_directory can be overridden."""
        config = AgentConfig(name="test", working_directory="/workspace")
        assert config.working_directory == "/workspace"


class TestAgent:
    def test_name_delegates_to_config(self):
        """Agent.name is a property delegating to config.name."""
        config = AgentConfig(name="worker")
        agent = Agent(
            config=config,
            machine=MagicMock(),
            bridge_id="test:7462",
            bridge_token="tok",
            session_id="sess-1",
            connection=MagicMock(),
        )
        assert agent.name == "worker"


class TestCreateClaudeAgent:
    def test_creates_claude_config(self):
        """create_agent with default agent_type creates a claude config."""
        config = create_agent("claude", slug="test-slug", user_id="user-1")

        assert config.agent_type == "claude"
        assert config.name == "claude"

    def test_default_model(self):
        """Default model is None (agent uses its own default)."""
        config = create_agent("claude", slug="test-slug", user_id="user-1")

        assert config.model is None

    def test_custom_model(self):
        """Model can be overridden."""
        config = create_agent("claude", model="claude-sonnet-4-5-20250929", slug="test-slug", user_id="user-1")

        assert config.model == "claude-sonnet-4-5-20250929"

    def test_working_directory_default(self):
        """working_directory defaults to /home/agent."""
        config = create_agent("claude", slug="test-slug", user_id="user-1")
        assert config.working_directory == "/home/agent"

    def test_working_directory_custom(self):
        """working_directory can be overridden."""
        config = create_agent("claude", working_directory="/home/agent/myrepo", slug="test-slug", user_id="user-1")
        assert config.working_directory == "/home/agent/myrepo"


class TestCreateCodexAgent:
    def test_creates_codex_config(self):
        """create_agent with agent_type='codex' creates a codex config."""
        config = create_agent("codex", agent_type="codex", slug="test-slug", user_id="user-1")

        assert config.agent_type == "codex"
        assert config.name == "codex"

    def test_working_directory_custom(self):
        """working_directory can be overridden."""
        config = create_agent(
            "codex",
            agent_type="codex",
            working_directory="/home/agent/myrepo",
            slug="test-slug",
            user_id="user-1",
        )
        assert config.working_directory == "/home/agent/myrepo"


class TestBuildClaudeACP:
    @patch("druids_server.lib.agents.base.mint_token", return_value="t")
    @patch("druids_server.lib.agents.claude.settings")
    def test_claude_acp_command(self, mock_settings, _mock_mint):
        """build_acp for claude produces claude-code-acp command."""
        mock_settings.base_url = "http://localhost"
        mock_settings.anthropic_api_key.get_secret_value.return_value = "sk-test"
        config = AgentConfig(name="claude")
        acp = ClaudeAgent.build_acp(config, slug="test-slug", user_id="user-1")

        assert acp.command == "claude-code-acp"

    @patch("druids_server.lib.agents.base.mint_token", return_value="t")
    @patch("druids_server.lib.agents.claude.settings")
    def test_claude_permission_bypass(self, mock_settings, _mock_mint):
        """Claude ACP config gets --dangerously-skip-permissions."""
        mock_settings.base_url = "http://localhost"
        mock_settings.anthropic_api_key.get_secret_value.return_value = "sk-test"
        config = AgentConfig(name="claude")
        acp = ClaudeAgent.build_acp(config, slug="test-slug", user_id="user-1")
        payload = acp.to_bridge_start(config.working_directory)

        assert "--dangerously-skip-permissions" in payload["args"]

    @patch("druids_server.lib.agents.base.mint_token", return_value="t")
    @patch("druids_server.lib.agents.claude.settings")
    def test_working_directory_in_bridge_payload(self, mock_settings, _mock_mint):
        """working_directory is passed through to_bridge_start()."""
        mock_settings.base_url = "http://localhost"
        mock_settings.anthropic_api_key.get_secret_value.return_value = "sk-test"
        config = AgentConfig(name="claude", working_directory="/home/agent/myrepo")
        acp = ClaudeAgent.build_acp(config, slug="test-slug", user_id="user-1")
        payload = acp.to_bridge_start(config.working_directory)

        assert payload["working_directory"] == "/home/agent/myrepo"


class TestBuildCodexACP:
    @patch("druids_server.lib.agents.base.mint_token", return_value="t")
    @patch("druids_server.lib.agents.codex.settings")
    def test_codex_acp_command(self, mock_settings, _mock_mint):
        """build_acp for codex produces codex-acp command."""
        mock_key = MagicMock()
        mock_key.get_secret_value.return_value = "sk-test"
        mock_settings.openai_api_key = mock_key
        config = AgentConfig(name="codex", agent_type="codex")
        acp = CodexAgent.build_acp(config, slug="test-slug", user_id="user-1")

        assert acp.command == "codex-acp"
        assert acp.env["OPENAI_API_KEY"] == "sk-test"

    @patch("druids_server.lib.agents.base.mint_token", return_value="t")
    @patch("druids_server.lib.agents.codex.settings")
    def test_raises_without_openai_key(self, mock_settings, _mock_mint):
        """build_acp raises ValueError when OPENAI_API_KEY is not configured."""
        mock_settings.openai_api_key = None
        config = AgentConfig(name="codex", agent_type="codex")

        with pytest.raises(ValueError, match="OPENAI_API_KEY not configured"):
            CodexAgent.build_acp(config, slug="test-slug", user_id="user-1")

    @patch("druids_server.lib.agents.base.mint_token", return_value="t")
    @patch("druids_server.lib.agents.codex.settings")
    def test_codex_permission_bypass(self, mock_settings, _mock_mint):
        """Codex ACP config gets codex permission bypass flags."""
        mock_key = MagicMock()
        mock_key.get_secret_value.return_value = "sk-test"
        mock_settings.openai_api_key = mock_key
        config = AgentConfig(name="codex", agent_type="codex")
        acp = CodexAgent.build_acp(config, slug="test-slug", user_id="user-1")
        payload = acp.to_bridge_start(config.working_directory)

        assert "-c" in payload["args"]
        assert 'approval_policy="never"' in payload["args"]

    @patch("druids_server.lib.agents.base.mint_token", return_value="t")
    @patch("druids_server.lib.agents.codex.settings")
    def test_working_directory_in_bridge_payload(self, mock_settings, _mock_mint):
        """working_directory is passed through to_bridge_start()."""
        mock_key = MagicMock()
        mock_key.get_secret_value.return_value = "sk-test"
        mock_settings.openai_api_key = mock_key
        config = AgentConfig(name="codex", agent_type="codex", working_directory="/home/agent/myrepo")
        acp = CodexAgent.build_acp(config, slug="test-slug", user_id="user-1")
        payload = acp.to_bridge_start(config.working_directory)

        assert payload["working_directory"] == "/home/agent/myrepo"
