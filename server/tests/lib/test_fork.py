"""Tests for Execution.fork_agent() -- COW agent branching."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from druids_server.lib.agents.base import Agent
from druids_server.lib.agents.claude import ClaudeAgent
from druids_server.lib.agents.config import AgentConfig
from druids_server.lib.execution import Execution


def _make_machine(instance_id="instance_1"):
    m = MagicMock()
    m.instance_id = instance_id
    m.sandbox = MagicMock()
    m.sandbox.workdir = "/home/agent"
    m.stop = AsyncMock()
    m.exec = AsyncMock(return_value=MagicMock(stdout="", stderr="", exit_code=0, ok=True))
    m.ensure_bridge = AsyncMock(return_value=(f"{instance_id}:7462", "bridge-token"))
    m.write_cli_config = AsyncMock()
    m.snapshot = AsyncMock(return_value="snapshot-abc")
    m.init = AsyncMock()
    return m


def _make_execution(**kwargs) -> Execution:
    defaults = {
        "id": uuid4(),
        "slug": "test-slug",
        "user_id": "user-1",
    }
    defaults.update(kwargs)
    return Execution(**defaults)


def _mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.start = AsyncMock()
    conn.new_session = AsyncMock(return_value="sess-1")
    conn.session_id = "sess-1"
    conn.on = MagicMock()
    conn.prompt = AsyncMock()
    conn.set_model = AsyncMock()
    conn.close = AsyncMock()
    return conn


def _make_agent(name: str, machine=None, session_id: str = "sess-1") -> Agent:
    """Create a minimal Agent for testing."""
    conn = _mock_conn()
    return Agent(
        config=AgentConfig(
            name=name,
            system_prompt="Be helpful.",
            model="claude-sonnet-4-5-20250929",
            git="write",
            working_directory="/home/agent/repo",
        ),
        machine=machine or _make_machine(),
        bridge_id="b:7462",
        bridge_token="tok",
        session_id=session_id,
        connection=conn,
    )


class TestForkAgentBasic:
    @pytest.mark.asyncio
    async def test_fork_creates_new_agent(self):
        """fork_agent snapshots the source, provisions a child, and registers the new agent."""
        execution = _make_execution()
        source_machine = _make_machine("src-instance")
        source = _make_agent("builder", machine=source_machine)
        execution.agents["builder"] = source

        child_machine = _make_machine("child-instance")

        async def fake_create(agent_config, mach, **kwargs):
            return Agent(
                config=agent_config,
                machine=mach,
                bridge_id="child:7462",
                bridge_token="child-tok",
                session_id="",
                connection=_mock_conn(),
            )

        with (
            patch("druids_server.lib.execution.Sandbox") as mock_sandbox_cls,
            patch.object(ClaudeAgent, "create", side_effect=fake_create),
            patch("druids_server.lib.execution.execution_trace"),
        ):
            mock_sandbox_cls.create = AsyncMock(return_value=child_machine.sandbox)
            # Patch Machine constructor to return our mock
            with patch("druids_server.lib.execution.Machine", return_value=child_machine):
                forked = await execution.fork_agent(
                    "builder",
                    "builder-alt",
                    prompt="Try a different approach.",
                )

        assert forked.name == "builder-alt"
        assert "builder-alt" in execution.agents
        assert execution.agents["builder-alt"] is forked
        source_machine.snapshot.assert_called_once()

    @pytest.mark.asyncio
    async def test_fork_raises_for_missing_source(self):
        """fork_agent raises ValueError when the source agent does not exist."""
        execution = _make_execution()

        with pytest.raises(ValueError, match="not found"):
            await execution.fork_agent("nonexistent", "fork-name")

    @pytest.mark.asyncio
    async def test_fork_inherits_config_from_source(self):
        """fork_agent inherits system_prompt, model, git from source when not overridden."""
        execution = _make_execution()
        source = _make_agent("builder")
        execution.agents["builder"] = source

        captured_config = {}

        async def fake_create(agent_config, mach, **kwargs):
            captured_config.update({
                "system_prompt": agent_config.system_prompt,
                "model": agent_config.model,
                "git": agent_config.git,
                "working_directory": agent_config.working_directory,
            })
            return Agent(
                config=agent_config,
                machine=mach,
                bridge_id="child:7462",
                bridge_token="tok",
                session_id="",
                connection=_mock_conn(),
            )

        child_machine = _make_machine("child")

        with (
            patch("druids_server.lib.execution.Sandbox") as mock_sandbox_cls,
            patch.object(ClaudeAgent, "create", side_effect=fake_create),
            patch("druids_server.lib.execution.execution_trace"),
            patch("druids_server.lib.execution.Machine", return_value=child_machine),
        ):
            mock_sandbox_cls.create = AsyncMock(return_value=child_machine.sandbox)
            await execution.fork_agent("builder", "builder-fork")

        assert captured_config["system_prompt"] == "Be helpful."
        assert captured_config["git"] == "write"
        assert captured_config["working_directory"] == "/home/agent/repo"

    @pytest.mark.asyncio
    async def test_fork_overrides_config_when_specified(self):
        """fork_agent uses overrides for system_prompt, model, git when provided."""
        execution = _make_execution()
        source = _make_agent("builder")
        execution.agents["builder"] = source

        captured_config = {}

        async def fake_create(agent_config, mach, **kwargs):
            captured_config.update({
                "system_prompt": agent_config.system_prompt,
                "model": agent_config.model,
                "git": agent_config.git,
            })
            return Agent(
                config=agent_config,
                machine=mach,
                bridge_id="child:7462",
                bridge_token="tok",
                session_id="",
                connection=_mock_conn(),
            )

        child_machine = _make_machine("child")

        with (
            patch("druids_server.lib.execution.Sandbox") as mock_sandbox_cls,
            patch.object(ClaudeAgent, "create", side_effect=fake_create),
            patch("druids_server.lib.execution.execution_trace"),
            patch("druids_server.lib.execution.Machine", return_value=child_machine),
        ):
            mock_sandbox_cls.create = AsyncMock(return_value=child_machine.sandbox)
            await execution.fork_agent(
                "builder",
                "builder-fork",
                system_prompt="New system prompt.",
                model="claude-opus-4-6",
                git="read",
            )

        assert captured_config["system_prompt"] == "New system prompt."
        assert captured_config["model"] == "claude-opus-4-6"
        assert captured_config["git"] == "read"


class TestForkAgentContext:
    @pytest.mark.asyncio
    async def test_context_false_does_not_pass_resume(self):
        """With context=False (default), create() is not passed resume_session_id."""
        execution = _make_execution()
        source = _make_agent("builder")
        execution.agents["builder"] = source

        create_kwargs = {}

        async def fake_create(agent_config, mach, **kwargs):
            create_kwargs.update(kwargs)
            return Agent(
                config=agent_config,
                machine=mach,
                bridge_id="child:7462",
                bridge_token="tok",
                session_id="",
                connection=_mock_conn(),
            )

        child_machine = _make_machine("child")

        with (
            patch("druids_server.lib.execution.Sandbox") as mock_sandbox_cls,
            patch.object(ClaudeAgent, "create", side_effect=fake_create),
            patch("druids_server.lib.execution.execution_trace"),
            patch("druids_server.lib.execution.Machine", return_value=child_machine),
        ):
            mock_sandbox_cls.create = AsyncMock(return_value=child_machine.sandbox)
            await execution.fork_agent("builder", "fork", context=False)

        assert create_kwargs.get("resume_session_id") is None

    @pytest.mark.asyncio
    async def test_context_true_passes_resume_session_id(self):
        """With context=True, create() receives the source agent's session_id."""
        execution = _make_execution()
        source = _make_agent("builder", session_id="original-sess-42")
        execution.agents["builder"] = source

        create_kwargs = {}

        async def fake_create(agent_config, mach, **kwargs):
            create_kwargs.update(kwargs)
            return Agent(
                config=agent_config,
                machine=mach,
                bridge_id="child:7462",
                bridge_token="tok",
                session_id="",
                connection=_mock_conn(),
            )

        child_machine = _make_machine("child")

        with (
            patch("druids_server.lib.execution.Sandbox") as mock_sandbox_cls,
            patch.object(ClaudeAgent, "create", side_effect=fake_create),
            patch("druids_server.lib.execution.execution_trace"),
            patch("druids_server.lib.execution.Machine", return_value=child_machine),
        ):
            mock_sandbox_cls.create = AsyncMock(return_value=child_machine.sandbox)
            await execution.fork_agent("builder", "fork", context=True)

        assert create_kwargs["resume_session_id"] == "original-sess-42"


class TestAgentCreateResume:
    @pytest.mark.asyncio
    async def test_resume_session_id_appends_resume_args(self):
        """Agent.create() adds --resume <id> to ACP command_args when resume_session_id is set."""
        config = AgentConfig(name="fork-agent")
        machine = _make_machine()

        with (
            patch.object(ClaudeAgent, "_prepare_machine", new_callable=AsyncMock),
            patch.object(ClaudeAgent, "_open_connection", new_callable=AsyncMock, return_value=_mock_conn()),
            patch("druids_server.lib.agents.claude.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key.get_secret_value.return_value = "sk-test"
            agent = await ClaudeAgent.create(
                config,
                machine,
                is_shared=False,
                slug="test-slug",
                user_id="user-1",
                resume_session_id="sess-to-resume",
            )

        # The ACP config stored on the agent should have --resume in command_args
        assert "--resume" in agent._acp_config.command_args
        idx = agent._acp_config.command_args.index("--resume")
        assert agent._acp_config.command_args[idx + 1] == "sess-to-resume"

    @pytest.mark.asyncio
    async def test_no_resume_session_id_leaves_args_unchanged(self):
        """Agent.create() does not modify command_args when resume_session_id is None."""
        config = AgentConfig(name="normal-agent")
        machine = _make_machine()

        with (
            patch.object(ClaudeAgent, "_prepare_machine", new_callable=AsyncMock),
            patch.object(ClaudeAgent, "_open_connection", new_callable=AsyncMock, return_value=_mock_conn()),
            patch("druids_server.lib.agents.claude.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key.get_secret_value.return_value = "sk-test"
            agent = await ClaudeAgent.create(
                config,
                machine,
                is_shared=False,
                slug="test-slug",
                user_id="user-1",
            )

        assert "--resume" not in agent._acp_config.command_args


class TestForkAgentCleanup:
    @pytest.mark.asyncio
    async def test_stops_machine_on_failure(self):
        """fork_agent stops the child machine if agent creation fails."""
        execution = _make_execution()
        source = _make_agent("builder")
        execution.agents["builder"] = source

        child_machine = _make_machine("child")

        with (
            patch("druids_server.lib.execution.Sandbox") as mock_sandbox_cls,
            patch.object(ClaudeAgent, "create", side_effect=RuntimeError("create failed")),
            patch("druids_server.lib.execution.execution_trace"),
            patch("druids_server.lib.execution.Machine", return_value=child_machine),
        ):
            mock_sandbox_cls.create = AsyncMock(return_value=child_machine.sandbox)

            with pytest.raises(RuntimeError, match="create failed"):
                await execution.fork_agent("builder", "fork")

        child_machine.stop.assert_called_once()
        assert "fork" not in execution.agents
