"""Tests for inline tool dispatch and Machine.write_cli_config."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from druids_server.lib.agents.base import Agent
from druids_server.lib.agents.config import AgentConfig
from druids_server.lib.execution import Execution


def _make_execution(**kwargs) -> Execution:
    defaults = {
        "id": uuid4(),
        "slug": "test-slug",
        "user_id": "user-1",
    }
    defaults.update(kwargs)
    return Execution(**defaults)


def _make_agent(name: str = "builder") -> Agent:
    config = AgentConfig(name=name)
    machine = MagicMock()
    machine.instance_id = "inst_1"
    conn = MagicMock()
    conn.prompt = AsyncMock()
    conn.close = AsyncMock()
    return Agent(
        config=config,
        machine=machine,
        bridge_id="inst_1:7462",
        bridge_token="tok",
        session_id="sess-1",
        connection=conn,
    )


class TestDispatchTool:
    @pytest.mark.asyncio
    async def test_dispatches_to_handler(self):
        """call_tool dispatches to a registered handler."""
        ex = _make_execution()
        agent = _make_agent()
        ex.agents["builder"] = agent

        @agent.on("submit")
        def on_submit(summary: str = ""):
            return f"submitted: {summary}"

        result = await ex.call_tool("builder", "submit", {"summary": "done"})
        assert result == "submitted: done"

    @pytest.mark.asyncio
    async def test_async_handler(self):
        """call_tool awaits async handlers."""
        ex = _make_execution()
        agent = _make_agent()
        ex.agents["builder"] = agent

        @agent.on("check")
        async def on_check():
            return "checked"

        result = await ex.call_tool("builder", "check", {})
        assert result == "checked"

    @pytest.mark.asyncio
    async def test_missing_handler_returns_error(self):
        """call_tool returns error for unregistered tools."""
        ex = _make_execution()
        agent = _make_agent()
        ex.agents["builder"] = agent

        result = await ex.call_tool("builder", "nonexistent", {})
        assert "Error:" in result

    @pytest.mark.asyncio
    async def test_missing_agent_returns_error(self):
        """call_tool returns error for unknown agents."""
        ex = _make_execution()
        result = await ex.call_tool("nobody", "tool", {})
        assert "Error:" in result

    @pytest.mark.asyncio
    async def test_handler_exception_returns_error(self):
        """call_tool catches handler exceptions and returns error string."""
        ex = _make_execution()
        agent = _make_agent()
        ex.agents["builder"] = agent

        @agent.on("fail")
        def on_fail():
            raise ValueError("boom")

        result = await ex.call_tool("builder", "fail", {})
        assert "Error:" in result
        assert "boom" in result

    @pytest.mark.asyncio
    async def test_caller_injection(self):
        """Handlers with a `caller` parameter receive the Agent."""
        ex = _make_execution()
        agent = _make_agent()
        ex.agents["builder"] = agent

        received_caller = None

        @agent.on("inspect")
        def on_inspect(caller=None):
            nonlocal received_caller
            received_caller = caller
            return "ok"

        await ex.call_tool("builder", "inspect", {})
        assert received_caller is agent


class TestMessageTool:
    @pytest.mark.asyncio
    async def test_message_enforces_topology(self):
        """message tool blocks delivery when agents are not connected."""
        ex = _make_execution()
        a = _make_agent("alice")
        b = _make_agent("bob")
        ex.agents["alice"] = a
        ex.agents["bob"] = b

        result = await ex.call_tool("alice", "message", {"receiver": "bob", "message": "hi"})
        assert "not found" in result.lower() or "No reachable" in result

    @pytest.mark.asyncio
    async def test_message_sends_when_connected(self):
        """message tool delivers when topology allows it."""
        ex = _make_execution()
        a = _make_agent("alice")
        b = _make_agent("bob")
        ex.agents["alice"] = a
        ex.agents["bob"] = b
        ex.connect(a, b)

        result = await ex.call_tool("alice", "message", {"receiver": "bob", "message": "hi"})
        assert "sent" in result.lower()
        # prompt() creates a background task; give it a tick to run
        await asyncio.sleep(0)
        b.connection.prompt.assert_called_once()


class TestListAgentsTool:
    @pytest.mark.asyncio
    async def test_lists_reachable_agents(self):
        """list_agents returns only topology-connected agents."""
        ex = _make_execution()
        a = _make_agent("alice")
        b = _make_agent("bob")
        c = _make_agent("carol")
        ex.agents["alice"] = a
        ex.agents["bob"] = b
        ex.agents["carol"] = c
        ex.connect(a, b)

        result = await ex.call_tool("alice", "list_agents", {})
        assert "bob" in result
        assert "carol" not in result


class TestTopology:
    def test_connect_bidirectional(self):
        ex = _make_execution()
        a = _make_agent("a")
        b = _make_agent("b")
        ex.connect(a, b)
        assert ex.is_connected("a", "b")
        assert ex.is_connected("b", "a")

    def test_connect_forward_only(self):
        ex = _make_execution()
        a = _make_agent("a")
        b = _make_agent("b")
        ex.connect(a, b, direction="forward")
        assert ex.is_connected("a", "b")
        assert not ex.is_connected("b", "a")


class TestClientEvents:
    @pytest.mark.asyncio
    async def test_dispatch_client_event(self):
        """handle_client_event dispatches to registered handler."""
        ex = _make_execution()

        @ex.on_client_event("click")
        def on_click(x: int = 0, y: int = 0):
            return {"clicked": True, "x": x, "y": y}

        result = await ex.handle_client_event("click", {"x": 10, "y": 20})
        assert result == {"clicked": True, "x": 10, "y": 20}

    @pytest.mark.asyncio
    async def test_unregistered_event_returns_error(self):
        ex = _make_execution()
        result = await ex.handle_client_event("unknown", {})
        assert "error" in result

    def test_client_event_names_tracked(self):
        ex = _make_execution()

        @ex.on_client_event("click")
        def on_click():
            pass

        assert "click" in ex.list_client_events()


class TestWriteCliConfig:
    @pytest.mark.asyncio
    async def test_uses_write_file(self):
        """Machine.write_cli_config uses sandbox.write_file."""
        from druids_server.lib.machine import Machine

        sandbox = MagicMock()
        sandbox.exec = AsyncMock(return_value=MagicMock(ok=True))
        sandbox.write_file = AsyncMock()
        machine = Machine(sandbox=sandbox)

        await machine.write_cli_config("http://localhost:8000")

        sandbox.write_file.assert_called_once()
        path, content = sandbox.write_file.call_args[0]
        assert path == "/home/agent/.druids/config.json"
        parsed = json.loads(content)
        assert parsed == {"base_url": "http://localhost:8000"}
