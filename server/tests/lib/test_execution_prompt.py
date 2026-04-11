"""Tests for prompt templating, session creation, and env injection in Execution."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from druids_server.lib.acp import ACPConfig
from druids_server.lib.agents import create_agent
from druids_server.lib.agents.base import Agent
from druids_server.lib.agents.claude import ClaudeAgent
from druids_server.lib.agents.config import AgentConfig
from druids_server.lib.execution import Execution


def _make_machine(instance_id="instance_1"):
    m = MagicMock()
    m.instance_id = instance_id
    m.stop = AsyncMock()
    m.exec = AsyncMock(return_value=MagicMock(stdout="", stderr="", exit_code=0, ok=True))
    m.expose_http_service = AsyncMock()
    m.ensure_bridge = AsyncMock(return_value=(f"{instance_id}:7462", "bridge-token"))
    m.git_pull = AsyncMock()
    m.write_cli_config = AsyncMock()
    return m


def _make_execution(**kwargs) -> Execution:
    defaults = {
        "id": uuid4(),
        "slug": "test-slug",
        "user_id": "user-1",
    }
    defaults.update(kwargs)
    return Execution(**defaults)


def _make_config(name: str = "test-agent", system_prompt: str | None = None) -> AgentConfig:
    return AgentConfig(name=name, system_prompt=system_prompt)


def _mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.start = AsyncMock()
    conn.new_session = AsyncMock(return_value="sess-1")
    conn.session_id = "sess-1"
    conn.on = MagicMock()
    conn.prompt = AsyncMock()
    conn.prompt_nowait = AsyncMock()
    conn.set_model = AsyncMock()
    conn.close = AsyncMock()
    return conn


class TestCreateSessionSystemPrompt:
    @pytest.mark.asyncio
    async def test_passes_system_prompt_to_new_session(self):
        """_create_acp_session passes config.system_prompt to conn.new_session()."""
        config = _make_config(system_prompt="Be thorough.")
        mock = _mock_conn()

        await Agent._create_acp_session(config, ACPConfig(), "test-slug", mock)

        mock.new_session.assert_called_once()
        call_kwargs = mock.new_session.call_args[1]
        assert call_kwargs["system_prompt"] == "Be thorough."

    @pytest.mark.asyncio
    async def test_passes_none_when_no_system_prompt(self):
        """_create_acp_session passes None when config has no system_prompt."""
        config = _make_config(system_prompt=None)
        mock = _mock_conn()

        await Agent._create_acp_session(config, ACPConfig(), "test-slug", mock)

        call_kwargs = mock.new_session.call_args[1]
        assert call_kwargs["system_prompt"] is None

    @pytest.mark.asyncio
    async def test_codex_agent_passes_system_prompt(self):
        """_create_acp_session passes system_prompt for codex-acp agents too."""
        config = AgentConfig(
            name="codex-agent",
            system_prompt="Write clean code.",
            agent_type="codex",
        )
        mock = _mock_conn()

        await Agent._create_acp_session(config, ACPConfig(), "test-slug", mock)

        call_kwargs = mock.new_session.call_args[1]
        assert call_kwargs["system_prompt"] == "Write clean code."


class TestCreateSessionSetModel:
    @pytest.mark.asyncio
    async def test_calls_set_model_for_claude_agent(self):
        """ClaudeAgent._create_acp_session calls set_model when config.model is set."""
        config = AgentConfig(name="claude", model="claude-opus-4-6")
        mock = _mock_conn()

        await ClaudeAgent._create_acp_session(config, ACPConfig(), "test-slug", mock)

        mock.set_model.assert_called_once_with("claude-opus-4-6")

    @pytest.mark.asyncio
    async def test_set_model_uses_custom_model(self):
        """ClaudeAgent._create_acp_session passes custom model value when overridden."""
        config = AgentConfig(name="claude", model="claude-sonnet-4-5-20250929")
        mock = _mock_conn()

        await ClaudeAgent._create_acp_session(config, ACPConfig(), "test-slug", mock)

        mock.set_model.assert_called_once_with("claude-sonnet-4-5-20250929")

    @pytest.mark.asyncio
    async def test_skips_set_model_when_none(self):
        """ClaudeAgent._create_acp_session does not call set_model when config.model is None."""
        config = _make_config()
        assert config.model is None

        mock = _mock_conn()

        await ClaudeAgent._create_acp_session(config, ACPConfig(), "test-slug", mock)

        mock.set_model.assert_not_called()


class TestTemplateVars:
    def test_root_agent_gets_branch_name(self):
        config = create_agent(
            "root",
            system_prompt="$execution_slug $agent_name $branch_name",
            slug="test-slug",
            user_id="user-1",
        )

        assert "test-slug" in config.system_prompt
        assert "root" in config.system_prompt
        assert "druids/test-slug" in config.system_prompt

    def test_non_root_agent_gets_branch_name(self):
        config = create_agent(
            "worker",
            system_prompt="$branch_name $agent_name",
            slug="test-slug",
            user_id="user-1",
        )

        assert "druids/test-slug" in config.system_prompt
        assert "worker" in config.system_prompt

    def test_working_directory_included(self):
        config = create_agent(
            "worker",
            working_directory="/home/agent/repo",
            system_prompt="dir=$working_directory",
            slug="test-slug",
            user_id="user-1",
        )

        assert config.system_prompt == "dir=/home/agent/repo"


class TestRunAgentTemplating:
    def test_system_prompt_templated(self):
        """create_agent() templates system_prompt."""
        config = create_agent(
            "root",
            system_prompt="slug=$execution_slug agent=$agent_name dir=$working_directory",
            slug="test-slug",
            user_id="user-1",
        )

        assert config.system_prompt == "slug=test-slug agent=root dir=/home/agent"

    def test_user_prompt_templated(self):
        """create_agent() templates user_prompt with runtime values."""
        config = create_agent(
            "root",
            prompt="I am $agent_name in $execution_slug",
            slug="test-slug",
            user_id="user-1",
        )

        assert config.prompt == "I am root in test-slug"


class TestBuildACPEnvInjection:
    @patch("druids_server.lib.agents.base.mint_token", return_value="fwd-token")
    @patch("druids_server.lib.agents.claude.settings")
    def test_injects_api_key(self, mock_settings, _mock_mint):
        """build_acp sets ANTHROPIC_API_KEY for direct API access."""
        mock_settings.anthropic_api_key.get_secret_value.return_value = "sk-real"
        config = AgentConfig(name="claude")
        acp = ClaudeAgent.build_acp(config, slug="test-slug", user_id="user-1")

        assert acp.env["ANTHROPIC_API_KEY"] == "sk-real"

    @patch("druids_server.lib.agents.base.mint_token", return_value="t")
    @patch("druids_server.lib.agents.base.settings")
    def test_root_agent_branch_name_templated(self, mock_settings, _mock_mint):
        """Root agent's system_prompt gets $branch_name filled in."""
        config = create_agent(
            "root",
            system_prompt="branch=$branch_name",
            slug="test-slug",
            user_id="user-1",
        )

        assert config.system_prompt == "branch=druids/test-slug"

    def test_safe_substitute_leaves_unknown_vars(self):
        """Unrecognized $variables are left unchanged by safe_substitute."""
        config = create_agent(
            "root",
            system_prompt="$unknown_var stays",
            slug="test-slug",
            user_id="user-1",
        )

        assert config.system_prompt == "$unknown_var stays"


class TestInitPromptDelivery:
    @pytest.mark.asyncio
    async def test_user_prompt_sent_after_provisioning(self):
        """Init prompt is sent as a fire-and-forget task after agent creation."""
        execution = _make_execution()
        machine = _make_machine()
        mock = _mock_conn()

        async def fake_create(agent_config, mach, **kwargs):
            return Agent(
                config=agent_config,
                machine=mach,
                bridge_id="b:7462",
                bridge_token="tok",
                session_id="sess-1",
                connection=mock,
            )

        with (
            patch.object(execution, "_provision_machine", return_value=machine),
            patch.object(ClaudeAgent, "create", side_effect=fake_create),
            patch("druids_server.lib.execution.execution_trace"),
        ):
            await execution.agent(name="root", prompt="Do the task.")

        # prompt() creates a background task; give it a tick to run
        await asyncio.sleep(0)
        mock.prompt.assert_called_once_with("Do the task.")

    @pytest.mark.asyncio
    async def test_no_user_prompt_means_no_prompt_call(self):
        """When user_prompt is None, no prompt task is created."""
        execution = _make_execution()
        machine = _make_machine()
        mock = _mock_conn()

        async def fake_create(agent_config, mach, **kwargs):
            return Agent(
                config=agent_config,
                machine=mach,
                bridge_id="b:7462",
                bridge_token="tok",
                session_id="sess-1",
                connection=mock,
            )

        with (
            patch.object(execution, "_provision_machine", return_value=machine),
            patch.object(ClaudeAgent, "create", side_effect=fake_create),
            patch("druids_server.lib.execution.execution_trace"),
        ):
            await execution.agent(name="root")

        await asyncio.sleep(0)
        mock.prompt.assert_not_called()


class TestCreateSessionConnectionLeak:
    @pytest.mark.asyncio
    async def test_closes_connection_when_new_session_fails(self):
        """conn.close() is called when new_session raises after start succeeds."""
        config = _make_config()

        mock = _mock_conn()
        mock.new_session = AsyncMock(side_effect=ConnectionError("session failed"))

        with pytest.raises(ConnectionError, match="session failed"):
            await Agent._create_acp_session(config, ACPConfig(), "test-slug", mock)

    @pytest.mark.asyncio
    async def test_closes_connection_when_set_model_fails(self):
        """conn.close() is called when set_model raises after session creation."""
        config = AgentConfig(name="claude", model="claude-opus-4-6")

        mock = _mock_conn()
        mock.set_model = AsyncMock(side_effect=ConnectionError("model failed"))

        with pytest.raises(ConnectionError, match="model failed"):
            await ClaudeAgent._create_acp_session(config, ACPConfig(), "test-slug", mock)


class TestCreateSessionDruidsMCP:
    @pytest.mark.asyncio
    async def test_druids_mcp_server_included(self):
        """_create_acp_session includes the druids MCP server in mcp_servers."""
        config = _make_config()
        acp = ACPConfig(env={"DRUIDS_ACCESS_TOKEN": "test-token"})

        mock = _mock_conn()
        await Agent._create_acp_session(config, acp, "test-slug", mock)

        mock.new_session.assert_called_once()
        call_kwargs = mock.new_session.call_args[1]
        mcp_servers = call_kwargs["mcp_servers"]
        assert mcp_servers is not None
        assert len(mcp_servers) >= 1

        druids_server = next(s for s in mcp_servers if s["name"] == "druids")
        assert "/amcp/" in druids_server["url"]
        assert "Authorization" in druids_server["headers"]
        assert "Bearer test-token" in druids_server["headers"]["Authorization"]

    @pytest.mark.asyncio
    async def test_druids_mcp_plus_program_mcp(self):
        """_create_acp_session includes both druids and program-provided MCP servers."""
        config = _make_config()
        acp = ACPConfig(
            env={"DRUIDS_ACCESS_TOKEN": "test-token"},
            mcp_servers={"slack": {"url": "https://slack.mcp/sse", "headers": {"Authorization": "Bearer xoxb"}}},
        )

        mock = _mock_conn()
        await Agent._create_acp_session(config, acp, "test-slug", mock)

        call_kwargs = mock.new_session.call_args[1]
        mcp_servers = call_kwargs["mcp_servers"]
        names = [s["name"] for s in mcp_servers]
        assert "druids" in names
        assert "slack" in names


class TestPreambleRemoved:
    def test_system_prompt_not_prepended(self):
        """create_agent() does not prepend DRUIDS_TOOLS_PREAMBLE to system_prompt."""
        config = create_agent(
            "root",
            system_prompt="Be thorough.",
            slug="test-slug",
            user_id="user-1",
        )

        assert config.system_prompt == "Be thorough."
        assert "druids tool" not in config.system_prompt
        assert "druids tools" not in config.system_prompt

    def test_no_system_prompt_stays_none(self):
        """create_agent() does not set system_prompt when none is provided."""
        config = create_agent(
            "root",
            slug="test-slug",
            user_id="user-1",
        )

        assert config.system_prompt is None


class TestForkAgentPreservesMCPServers:
    @pytest.mark.asyncio
    async def test_fork_preserves_source_mcp_servers(self):
        """fork_agent passes source agent's mcp_servers to the new agent config."""
        execution = _make_execution()
        machine = _make_machine()
        mock = _mock_conn()

        source_config = AgentConfig(
            name="source",
            mcp_servers={"custom": {"url": "https://custom.mcp/sse"}},
        )
        source = Agent(
            config=source_config,
            machine=machine,
            bridge_id="inst_1:7462",
            bridge_token="tok",
            session_id="sess-1",
            connection=mock,
        )
        execution.agents["source"] = source

        child_machine = _make_machine("child_1")

        with (
            patch("druids_server.lib.execution.Agent") as MockAgentCls,
            patch("druids_server.lib.execution.agent_class", return_value=MockAgentCls),
            patch.object(execution, "_bind_trace"),
            patch.object(source.machine, "create_child", AsyncMock(return_value=child_machine)),
            patch("druids_server.lib.execution.execution_trace"),
        ):
            mock_agent_instance = MagicMock()
            mock_agent_instance._resume_session_id = None
            mock_agent_instance.config = MagicMock()
            MockAgentCls.create = AsyncMock(return_value=mock_agent_instance)

            await execution.fork_agent(
                source=source,
                name="forked",
            )

            MockAgentCls.create.assert_called_once()
            # config is arg[0], machine is arg[1] (both positional)
            call_args = MockAgentCls.create.call_args
            assert call_args[0][1] is child_machine  # machine arg

