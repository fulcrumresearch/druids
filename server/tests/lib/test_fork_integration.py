"""Integration-level tests for fork_agent that verify the full flow.

These tests mock the sandbox layer but exercise the real Execution,
Machine, and Agent classes to verify the fork path end-to-end.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from druids_server.lib.acp import ACPConfig
from druids_server.lib.agents.base import Agent
from druids_server.lib.agents.claude import ClaudeAgent
from druids_server.lib.agents.config import AgentConfig
from druids_server.lib.execution import Execution
from druids_server.lib.machine import Machine


def _make_execution(**kwargs) -> Execution:
    defaults = {
        "id": uuid4(),
        "slug": "test-slug",
        "user_id": "user-1",
        "repo_full_name": "org/repo",
        "git_branch": "main",
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
    conn.prompt_nowait = AsyncMock()
    conn.set_model = AsyncMock()
    conn.close = AsyncMock()
    return conn


def _mock_sandbox(workdir="/home/agent/repo", supports_cow=True):
    sandbox = MagicMock()
    sandbox.workdir = workdir
    sandbox.supports_cow = supports_cow
    sandbox.exec = AsyncMock(return_value=MagicMock(stdout="", stderr="", exit_code=0, ok=True))
    sandbox.write_file = AsyncMock()
    sandbox.read_file = AsyncMock(return_value="")
    sandbox.stop = AsyncMock()
    sandbox.snapshot = AsyncMock(return_value="snapshot-abc123")
    return sandbox


class TestForkIntegration:
    """Verify fork_agent works with real Execution and Agent wiring."""

    @pytest.mark.asyncio
    async def test_fork_sends_prompt_to_forked_agent(self):
        """When fork_agent is called with a prompt, the prompt is delivered to the forked agent."""
        execution = _make_execution()
        conn = _mock_conn()

        # Create a source agent with a real AgentConfig
        source_config = AgentConfig(
            name="builder",
            system_prompt="You are a builder.",
            model="claude-sonnet-4-5-20250929",
            git="write",
            working_directory="/home/agent/repo",
        )
        source_sandbox = _mock_sandbox()
        source_machine = Machine(
            sandbox=source_sandbox,
            snapshot_id="base-snap",
            repo_full_name="org/repo",
            git_branch="main",
            git_permissions="write",
        )
        source = Agent(
            config=source_config,
            machine=source_machine,
            bridge_id="src:7462",
            bridge_token="src-tok",
            session_id="sess-original",
            connection=_mock_conn(),
        )
        execution.agents["builder"] = source

        # The forked agent will be created by Agent.create, which we mock
        fork_conn = _mock_conn()

        async def fake_create(agent_config, mach, **kwargs):
            a = Agent(
                config=agent_config,
                machine=mach,
                bridge_id="fork:7462",
                bridge_token="fork-tok",
                session_id="",
                connection=fork_conn,
                _acp_config=ACPConfig(env={"DRUIDS_ACCESS_TOKEN": "test-tok"}),
                _slug="test-slug",
            )
            # Pre-set session_id so _ensure_session is skipped
            a.session_id = "fork-sess"
            return a

        child_sandbox = _mock_sandbox()
        child_machine = Machine(
            sandbox=child_sandbox,
            snapshot_id="snapshot-abc123",
        )

        with (
            patch("druids_server.lib.execution.Sandbox") as mock_sandbox_cls,
            patch.object(ClaudeAgent, "create", side_effect=fake_create),
            patch("druids_server.lib.execution.execution_trace"),
            patch("druids_server.lib.execution.Machine", return_value=child_machine),
        ):
            mock_sandbox_cls.create = AsyncMock(return_value=child_sandbox)
            forked = await execution.fork_agent(
                "builder",
                "builder-alt",
                prompt="Try the auth module differently.",
            )

        # Verify the prompt was sent
        await asyncio.sleep(0)  # Allow fire-and-forget tasks to run
        fork_conn.prompt.assert_called_once_with("Try the auth module differently.")

        # Verify snapshot was taken from source
        source_sandbox.snapshot.assert_called_once()

    @pytest.mark.asyncio
    async def test_fork_with_context_passes_resume_to_create(self):
        """With context=True, Agent.create receives the source session ID for --resume."""
        execution = _make_execution()

        source = Agent(
            config=AgentConfig(name="builder", working_directory="/home/agent/repo"),
            machine=Machine(sandbox=_mock_sandbox(), snapshot_id="base"),
            bridge_id="src:7462",
            bridge_token="src-tok",
            session_id="sess-to-copy",
            connection=_mock_conn(),
        )
        execution.agents["builder"] = source

        create_kwargs = {}

        async def fake_create(agent_config, mach, **kwargs):
            create_kwargs.update(kwargs)
            return Agent(
                config=agent_config,
                machine=mach,
                bridge_id="fork:7462",
                bridge_token="fork-tok",
                session_id="",
                connection=_mock_conn(),
            )

        child_sandbox = _mock_sandbox()

        with (
            patch("druids_server.lib.execution.Sandbox") as mock_sandbox_cls,
            patch.object(ClaudeAgent, "create", side_effect=fake_create),
            patch("druids_server.lib.execution.execution_trace"),
            patch("druids_server.lib.execution.Machine", return_value=Machine(sandbox=child_sandbox)),
        ):
            mock_sandbox_cls.create = AsyncMock(return_value=child_sandbox)
            await execution.fork_agent(
                "builder",
                "fork-ctx",
                context=True,
                prompt="Now try approach B.",
            )

        assert create_kwargs["resume_session_id"] == "sess-to-copy"

    @pytest.mark.asyncio
    async def test_fork_without_context_no_resume(self):
        """With context=False, Agent.create does not receive resume_session_id."""
        execution = _make_execution()

        source = Agent(
            config=AgentConfig(name="builder", working_directory="/home/agent/repo"),
            machine=Machine(sandbox=_mock_sandbox(), snapshot_id="base"),
            bridge_id="src:7462",
            bridge_token="src-tok",
            session_id="sess-original",
            connection=_mock_conn(),
        )
        execution.agents["builder"] = source

        create_kwargs = {}

        async def fake_create(agent_config, mach, **kwargs):
            create_kwargs.update(kwargs)
            return Agent(
                config=agent_config,
                machine=mach,
                bridge_id="fork:7462",
                bridge_token="fork-tok",
                session_id="",
                connection=_mock_conn(),
            )

        child_sandbox = _mock_sandbox()

        with (
            patch("druids_server.lib.execution.Sandbox") as mock_sandbox_cls,
            patch.object(ClaudeAgent, "create", side_effect=fake_create),
            patch("druids_server.lib.execution.execution_trace"),
            patch("druids_server.lib.execution.Machine", return_value=Machine(sandbox=child_sandbox)),
        ):
            mock_sandbox_cls.create = AsyncMock(return_value=child_sandbox)
            await execution.fork_agent(
                "builder",
                "fork-fresh",
                context=False,
            )

        assert create_kwargs.get("resume_session_id") is None

    @pytest.mark.asyncio
    async def test_forked_agents_are_independent(self):
        """After forking, the source and forked agent have separate connections."""
        execution = _make_execution()

        source_conn = _mock_conn()
        source = Agent(
            config=AgentConfig(name="builder", working_directory="/home/agent/repo"),
            machine=Machine(sandbox=_mock_sandbox(), snapshot_id="base"),
            bridge_id="src:7462",
            bridge_token="src-tok",
            session_id="sess-orig",
            connection=source_conn,
        )
        execution.agents["builder"] = source

        fork_conn = _mock_conn()

        async def fake_create(agent_config, mach, **kwargs):
            return Agent(
                config=agent_config,
                machine=mach,
                bridge_id="fork:7462",
                bridge_token="fork-tok",
                session_id="",
                connection=fork_conn,
            )

        child_sandbox = _mock_sandbox()

        with (
            patch("druids_server.lib.execution.Sandbox") as mock_sandbox_cls,
            patch.object(ClaudeAgent, "create", side_effect=fake_create),
            patch("druids_server.lib.execution.execution_trace"),
            patch("druids_server.lib.execution.Machine", return_value=Machine(sandbox=child_sandbox)),
        ):
            mock_sandbox_cls.create = AsyncMock(return_value=child_sandbox)
            forked = await execution.fork_agent("builder", "fork")

        # They should have different connections
        assert source.connection is not forked.connection
        assert source.bridge_id != forked.bridge_id

        # Sending to one doesn't affect the other
        await asyncio.sleep(0)
        source_conn.prompt.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_appended_to_acp_command_args(self):
        """When resume_session_id is passed, --resume is appended to ACP command args."""
        config = AgentConfig(name="fork-agent")
        machine = Machine(sandbox=_mock_sandbox())

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
                slug="test",
                user_id="user-1",
                resume_session_id="sess-42",
            )

        # The ACP config should contain --resume sess-42
        args = agent._acp_config.command_args
        assert "--resume" in args
        resume_idx = args.index("--resume")
        assert args[resume_idx + 1] == "sess-42"

        # The original --dangerously-skip-permissions should still be present
        assert "--dangerously-skip-permissions" in args
