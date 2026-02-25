"""Tests for prompt templating and delivery in Execution."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from orpheus.lib.agents.base import ACPConfig, Agent
from orpheus.lib.agents.claude import ClaudeAgent
from orpheus.lib.execution import Execution
from orpheus.lib.program import Program


def _make_machine(instance_id="morph_123", bridge_id="bridge-1", bridge_token="token-1"):
    m = MagicMock()
    m.instance_id = instance_id
    m.bridge_id = bridge_id
    m.bridge_token = bridge_token
    m.stop = AsyncMock()
    m.exec = AsyncMock(return_value=MagicMock(stdout="", stderr="", exit_code=0, ok=True))
    m.ssh_key = AsyncMock()
    m.expose_http_service = AsyncMock()
    m.ensure_bridge = AsyncMock()
    m.git_pull = AsyncMock()
    return m


def _make_execution(**kwargs) -> Execution:
    defaults = {
        "id": uuid4(),
        "slug": "test-slug",
        "root": Program(name="root"),
        "user_id": "user-1",
    }
    defaults.update(kwargs)
    return Execution(**defaults)


def _make_agent(name: str = "test-agent", system_prompt: str | None = None, user_prompt: str | None = None) -> Agent:
    agent = Agent(
        name=name,
        config=ACPConfig(command="claude-code-acp", env={"ANTHROPIC_API_KEY": "sk-test"}),
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    agent.machine = _make_machine()
    return agent


def _mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.start = AsyncMock()
    conn.new_session = AsyncMock(return_value="sess-1")
    conn.session_id = "sess-1"
    conn.on = MagicMock()
    conn.prompt = AsyncMock()
    conn.set_model = AsyncMock()
    return conn


class TestConnectAgentSystemPrompt:
    @pytest.mark.asyncio
    async def test_passes_system_prompt_to_new_session(self):
        """_connect_agent passes agent.system_prompt to conn.new_session()."""
        execution = _make_execution()
        agent = _make_agent(system_prompt="Be thorough.")

        mock = _mock_conn()
        with patch("orpheus.lib.execution.AgentConnection", return_value=mock):
            await execution._connect_agent(agent)

        mock.new_session.assert_called_once()
        call_kwargs = mock.new_session.call_args[1]
        assert call_kwargs["system_prompt"] == "Be thorough."

    @pytest.mark.asyncio
    async def test_passes_none_when_no_system_prompt(self):
        """_connect_agent passes None when agent has no system_prompt."""
        execution = _make_execution()
        agent = _make_agent(system_prompt=None)

        mock = _mock_conn()
        with patch("orpheus.lib.execution.AgentConnection", return_value=mock):
            await execution._connect_agent(agent)

        call_kwargs = mock.new_session.call_args[1]
        assert call_kwargs["system_prompt"] is None

    @pytest.mark.asyncio
    async def test_codex_agent_passes_system_prompt(self):
        """_connect_agent passes system_prompt for codex-acp agents too."""
        execution = _make_execution()
        agent = Agent(
            name="codex-agent",
            config=ACPConfig(command="codex-acp", env={"OPENAI_API_KEY": "sk-test"}),
            system_prompt="Write clean code.",
        )
        agent.machine = _make_machine()

        mock = _mock_conn()
        with patch("orpheus.lib.execution.AgentConnection", return_value=mock):
            await execution._connect_agent(agent)

        mock.start.assert_called_once_with(auth_method="openai-api-key")
        call_kwargs = mock.new_session.call_args[1]
        assert call_kwargs["system_prompt"] == "Write clean code."


class TestConnectAgentSetModel:
    @pytest.mark.asyncio
    async def test_calls_set_model_for_claude_agent(self):
        """_connect_agent calls set_model for ClaudeAgent with agent.model."""
        execution = _make_execution()
        agent = ClaudeAgent(name="claude")
        agent.machine = _make_machine()

        mock = _mock_conn()
        with patch("orpheus.lib.execution.AgentConnection", return_value=mock):
            await execution._connect_agent(agent)

        mock.set_model.assert_called_once_with("claude-opus-4-6")

    @pytest.mark.asyncio
    async def test_set_model_uses_custom_model(self):
        """_connect_agent passes custom model value when overridden."""
        execution = _make_execution()
        agent = ClaudeAgent(name="claude", model="claude-sonnet-4-5-20250929")
        agent.machine = _make_machine()

        mock = _mock_conn()
        with patch("orpheus.lib.execution.AgentConnection", return_value=mock):
            await execution._connect_agent(agent)

        mock.set_model.assert_called_once_with("claude-sonnet-4-5-20250929")

    @pytest.mark.asyncio
    async def test_skips_set_model_for_base_agent(self):
        """_connect_agent does not call set_model for agents without a model field."""
        execution = _make_execution()
        agent = _make_agent()

        mock = _mock_conn()
        with patch("orpheus.lib.execution.AgentConnection", return_value=mock):
            await execution._connect_agent(agent)

        mock.set_model.assert_not_called()


class TestTemplateVars:
    def test_root_agent_gets_branch_name(self):
        root_agent = _make_agent(name="root")
        execution = _make_execution(root=root_agent)
        tvars = execution._template_vars(root_agent)

        assert tvars["execution_slug"] == "test-slug"
        assert tvars["agent_name"] == "root"
        assert tvars["branch_name"] == "orpheus/test-slug"

    def test_non_root_agent_gets_branch_name(self):
        execution = _make_execution()
        agent = _make_agent(name="worker")
        tvars = execution._template_vars(agent)

        assert tvars["branch_name"] == "orpheus/test-slug"
        assert tvars["agent_name"] == "worker"

    def test_working_directory_included(self):
        execution = _make_execution()
        agent = _make_agent(name="worker")
        agent.working_directory = "/home/agent/repo"
        tvars = execution._template_vars(agent)

        assert tvars["working_directory"] == "/home/agent/repo"


class TestRunProgramTemplating:
    @pytest.mark.asyncio
    async def test_system_prompt_templated_before_exec(self):
        """run_program templates system_prompt before calling exec()."""
        agent = _make_agent(
            name="root",
            system_prompt="slug=$execution_slug agent=$agent_name dir=$working_directory",
        )
        agent.working_directory = "/home/agent"
        execution = _make_execution(root=agent)

        captured_prompt = {}

        async def fake_exec(machine):
            captured_prompt["system_prompt"] = agent.system_prompt
            return []

        agent.exec = fake_exec

        mock = _mock_conn()
        with (
            patch("orpheus.lib.execution.AgentConnection", return_value=mock),
            patch.object(execution, "_provision_machine", return_value=_make_machine()),
        ):
            await execution.run_program(agent)

        assert captured_prompt["system_prompt"] == "slug=test-slug agent=root dir=/home/agent"

    @pytest.mark.asyncio
    async def test_user_prompt_templated(self):
        """run_program templates user_prompt with runtime values."""
        agent = _make_agent(name="root", user_prompt="I am $agent_name in $execution_slug")
        agent.working_directory = "/home/agent"
        execution = _make_execution(root=agent)
        agent.exec = AsyncMock(return_value=[])

        mock = _mock_conn()
        with (
            patch("orpheus.lib.execution.AgentConnection", return_value=mock),
            patch.object(execution, "_provision_machine", return_value=_make_machine()),
        ):
            await execution.run_program(agent)

        mock.prompt.assert_called_once_with("I am root in test-slug")


class TestClaudeEnvInjection:
    @pytest.mark.asyncio
    @patch("orpheus.lib.forwarding_tokens.mint_token", return_value="fwd-token")
    async def test_injects_forwarding_env(self, mock_mint):
        agent = ClaudeAgent(name="claude")
        agent.exec = AsyncMock(return_value=[])

        execution = _make_execution(root=agent)
        execution._connect_agent = AsyncMock()

        with patch.object(execution, "_provision_machine", return_value=_make_machine()):
            await execution.run_program(agent)

        assert agent.config.env["ANTHROPIC_API_KEY"] == "fwd-token"
        assert "/proxy/anthropic" in agent.config.env["ANTHROPIC_BASE_URL"]

    @pytest.mark.asyncio
    async def test_root_agent_branch_name_templated(self):
        """Root agent's system_prompt gets $branch_name filled in."""
        agent = _make_agent(name="root", system_prompt="branch=$branch_name")
        agent.working_directory = "/home/agent"
        execution = _make_execution(root=agent)
        agent.exec = AsyncMock(return_value=[])

        mock = _mock_conn()
        with (
            patch("orpheus.lib.execution.AgentConnection", return_value=mock),
            patch.object(execution, "_provision_machine", return_value=_make_machine()),
        ):
            await execution.run_program(agent)

        call_kwargs = mock.new_session.call_args[1]
        assert call_kwargs["system_prompt"] == "branch=orpheus/test-slug"

    @pytest.mark.asyncio
    async def test_safe_substitute_leaves_unknown_vars(self):
        """Unrecognized $variables are left unchanged by safe_substitute."""
        agent = _make_agent(name="root", system_prompt="$unknown_var stays")
        agent.working_directory = "/home/agent"
        execution = _make_execution(root=agent)
        agent.exec = AsyncMock(return_value=[])

        mock = _mock_conn()
        with (
            patch("orpheus.lib.execution.AgentConnection", return_value=mock),
            patch.object(execution, "_provision_machine", return_value=_make_machine()),
        ):
            await execution.run_program(agent)

        call_kwargs = mock.new_session.call_args[1]
        assert call_kwargs["system_prompt"] == "$unknown_var stays"


class TestMcpConfigHeaders:
    @pytest.mark.asyncio
    async def test_mcp_config_includes_identity_headers(self):
        """MCP config headers include X-Execution-Slug and X-Agent-Name."""
        execution = _make_execution(
            slug="test-slug",
            mcp_url="https://mcp.example.com",
            mcp_auth_token="token-123",
        )
        agent = _make_agent(name="my-agent")

        mock = _mock_conn()
        with patch("orpheus.lib.execution.AgentConnection", return_value=mock):
            await execution._connect_agent(agent)

        servers = mock.new_session.call_args.kwargs["mcp_servers"]
        assert len(servers) == 1
        headers = servers[0]["headers"]
        assert headers["X-Execution-Slug"] == "test-slug"
        assert headers["X-Agent-Name"] == "my-agent"
        assert headers["Authorization"] == "Bearer token-123"


class TestInitPromptDelivery:
    @pytest.mark.asyncio
    async def test_user_prompt_sent_without_context_prefix(self):
        """Init prompt is sent directly without [Execution Context] prefix."""
        agent = _make_agent(name="root", user_prompt="Do the task.")
        agent.working_directory = "/home/agent"
        execution = _make_execution(root=agent)
        agent.exec = AsyncMock(return_value=[])

        mock = _mock_conn()
        with (
            patch("orpheus.lib.execution.AgentConnection", return_value=mock),
            patch.object(execution, "_provision_machine", return_value=_make_machine()),
        ):
            await execution.run_program(agent)

        mock.prompt.assert_called_once_with("Do the task.")

    @pytest.mark.asyncio
    async def test_no_user_prompt_means_no_prompt_call(self):
        """When user_prompt is None, conn.prompt is not called."""
        agent = _make_agent(name="root", user_prompt=None)
        agent.working_directory = "/home/agent"
        execution = _make_execution(root=agent)
        agent.exec = AsyncMock(return_value=[])

        mock = _mock_conn()
        with (
            patch("orpheus.lib.execution.AgentConnection", return_value=mock),
            patch.object(execution, "_provision_machine", return_value=_make_machine()),
        ):
            await execution.run_program(agent)

        mock.prompt.assert_not_called()
