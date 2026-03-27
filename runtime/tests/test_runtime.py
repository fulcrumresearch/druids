from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from druids_runtime import RuntimeAgent, RuntimeContext


def _make_ctx() -> RuntimeContext:
    return RuntimeContext(slug="test", _base_url="http://localhost", _token="tok")


def _make_agent(ctx: RuntimeContext, name: str = "agent") -> RuntimeAgent:
    agent = RuntimeAgent(name=name, _ctx=ctx)
    ctx._agents[name] = agent
    return agent


class TestToolHandlerCallerInjection:
    """Tests for automatic `caller` injection in tool handlers."""

    @pytest.mark.asyncio
    async def test_handler_without_caller(self) -> None:
        ctx = _make_ctx()
        agent = _make_agent(ctx, "builder")

        @agent.on("submit")
        async def on_submit(summary: str = "") -> str:
            return f"got: {summary}"

        result = await ctx._handle_tool_call("builder", "submit", {"summary": "done"})
        assert result == "got: done"

    @pytest.mark.asyncio
    async def test_handler_with_caller(self) -> None:
        ctx = _make_ctx()
        agent = _make_agent(ctx, "builder")

        @agent.on("submit")
        async def on_submit(caller: RuntimeAgent, summary: str = "") -> str:
            return f"{caller.name}: {summary}"

        result = await ctx._handle_tool_call("builder", "submit", {"summary": "done"})
        assert result == "builder: done"

    @pytest.mark.asyncio
    async def test_shared_tool_distinguishes_caller(self) -> None:
        ctx = _make_ctx()
        builder = _make_agent(ctx, "builder")
        reviewer = _make_agent(ctx, "reviewer")

        async def on_submit(caller: RuntimeAgent, summary: str = "") -> str:
            return f"{caller.name}: {summary}"

        builder.on("submit")(on_submit)
        reviewer.on("submit")(on_submit)

        r1 = await ctx._handle_tool_call("builder", "submit", {"summary": "x"})
        r2 = await ctx._handle_tool_call("reviewer", "submit", {"summary": "x"})
        assert r1 == "builder: x"
        assert r2 == "reviewer: x"

    @pytest.mark.asyncio
    async def test_sync_handler_with_caller(self) -> None:
        ctx = _make_ctx()
        agent = _make_agent(ctx, "worker")

        @agent.on("ping")
        def on_ping(caller: RuntimeAgent) -> str:
            return f"pong from {caller.name}"

        result = await ctx._handle_tool_call("worker", "ping", {})
        assert result == "pong from worker"

    @pytest.mark.asyncio
    async def test_unknown_agent_raises(self) -> None:
        ctx = _make_ctx()
        with pytest.raises(ValueError, match="Unknown agent"):
            await ctx._handle_tool_call("ghost", "submit", {})

    @pytest.mark.asyncio
    async def test_unknown_tool_raises(self) -> None:
        ctx = _make_ctx()
        _make_agent(ctx, "builder")
        with pytest.raises(ValueError, match="No handler for tool"):
            await ctx._handle_tool_call("builder", "nope", {})


class TestTopology:
    """Tests for `ctx.connect()` and `ctx.is_connected()`."""

    def test_connect_bidirectional(self) -> None:
        ctx = _make_ctx()
        a = _make_agent(ctx, "a")
        b = _make_agent(ctx, "b")
        ctx.connect(a, b)
        assert ctx.is_connected(a, b)
        assert ctx.is_connected(b, a)

    def test_connect_forward_only(self) -> None:
        ctx = _make_ctx()
        a = _make_agent(ctx, "a")
        b = _make_agent(ctx, "b")
        ctx.connect(a, b, direction="forward")
        assert ctx.is_connected(a, b)
        assert not ctx.is_connected(b, a)

    def test_no_topology_blocks_all(self) -> None:
        """When no connect() calls are made, agents are isolated."""
        ctx = _make_ctx()
        a = _make_agent(ctx, "a")
        b = _make_agent(ctx, "b")
        assert not ctx.is_connected(a, b)
        assert not ctx.is_connected(b, a)

    def test_not_connected(self) -> None:
        ctx = _make_ctx()
        a = _make_agent(ctx, "a")
        b = _make_agent(ctx, "b")
        c = _make_agent(ctx, "c")
        ctx.connect(a, b)
        assert not ctx.is_connected(a, c)

    def test_is_connected_accepts_strings(self) -> None:
        ctx = _make_ctx()
        a = _make_agent(ctx, "a")
        b = _make_agent(ctx, "b")
        ctx.connect(a, b)
        assert ctx.is_connected("a", "b")
        assert ctx.is_connected("b", "a")


class TestMessageRouting:
    """Tests for the built-in message tool routed through the runtime."""

    @pytest.mark.asyncio
    async def test_message_allowed_by_topology(self) -> None:
        ctx = _make_ctx()
        a = _make_agent(ctx, "a")
        b = _make_agent(ctx, "b")
        ctx.connect(a, b)

        with patch.object(ctx, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = {"status": "sent"}
            result = await ctx._handle_tool_call("a", "message", {"receiver": "b", "message": "hello"})

        assert result == "Message sent to b."
        mock_post.assert_called_once_with("/send", {"sender": "a", "receiver": "b", "text": "hello"})

    @pytest.mark.asyncio
    async def test_message_blocked_by_topology(self) -> None:
        ctx = _make_ctx()
        a = _make_agent(ctx, "a")
        b = _make_agent(ctx, "b")
        _make_agent(ctx, "c")
        ctx.connect(a, b)

        result = await ctx._handle_tool_call("c", "message", {"receiver": "a", "message": "hello"})
        assert "not found" in result
        assert "a" not in result.split("Available:")[1] or "a" not in result

    @pytest.mark.asyncio
    async def test_message_unknown_receiver(self) -> None:
        ctx = _make_ctx()
        _make_agent(ctx, "a")

        result = await ctx._handle_tool_call("a", "message", {"receiver": "ghost", "message": "hi"})
        assert "not found" in result
        assert "ghost" in result

    @pytest.mark.asyncio
    async def test_message_no_topology_blocks_all(self) -> None:
        ctx = _make_ctx()
        _make_agent(ctx, "a")
        _make_agent(ctx, "b")

        result = await ctx._handle_tool_call("a", "message", {"receiver": "b", "message": "hi"})
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_message_forward_only_blocks_reverse(self) -> None:
        ctx = _make_ctx()
        a = _make_agent(ctx, "a")
        b = _make_agent(ctx, "b")
        ctx.connect(a, b, direction="forward")

        result = await ctx._handle_tool_call("b", "message", {"receiver": "a", "message": "hi"})
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_message_blocked_lists_reachable_agents(self) -> None:
        ctx = _make_ctx()
        a = _make_agent(ctx, "a")
        b = _make_agent(ctx, "b")
        _make_agent(ctx, "c")
        ctx.connect(a, b)

        result = await ctx._handle_tool_call("a", "message", {"receiver": "c", "message": "hi"})
        assert "not found" in result
        assert "b" in result


class TestListAgents:
    """Tests for the built-in list_agents tool filtered by topology."""

    @pytest.mark.asyncio
    async def test_list_agents_shows_connected(self) -> None:
        ctx = _make_ctx()
        a = _make_agent(ctx, "a")
        b = _make_agent(ctx, "b")
        _make_agent(ctx, "c")
        ctx.connect(a, b)

        result = await ctx._handle_tool_call("a", "list_agents", {})
        assert "b" in result
        assert "c" not in result

    @pytest.mark.asyncio
    async def test_list_agents_excludes_self(self) -> None:
        ctx = _make_ctx()
        a = _make_agent(ctx, "a")
        b = _make_agent(ctx, "b")
        ctx.connect(a, b)

        result = await ctx._handle_tool_call("a", "list_agents", {})
        assert "a" not in result

    @pytest.mark.asyncio
    async def test_list_agents_no_topology_returns_none(self) -> None:
        ctx = _make_ctx()
        _make_agent(ctx, "a")
        _make_agent(ctx, "b")

        result = await ctx._handle_tool_call("a", "list_agents", {})
        assert result == "No reachable agents."

    @pytest.mark.asyncio
    async def test_list_agents_forward_only(self) -> None:
        ctx = _make_ctx()
        a = _make_agent(ctx, "a")
        b = _make_agent(ctx, "b")
        ctx.connect(a, b, direction="forward")

        assert "b" in await ctx._handle_tool_call("a", "list_agents", {})
        assert await ctx._handle_tool_call("b", "list_agents", {}) == "No reachable agents."


class TestRemoteExec:
    @pytest.mark.asyncio
    async def test_runtime_agent_exec_forwards_user_and_timeout(self) -> None:
        ctx = _make_ctx()
        agent = _make_agent(ctx, "worker")
        agent._ready = asyncio.create_task(asyncio.sleep(0))

        with patch.object(ctx, "_remote_exec", new_callable=AsyncMock) as mock_remote_exec:
            mock_remote_exec.return_value = {"stdout": "", "stderr": "", "exit_code": 0}
            result = await agent.exec("iptables -L", user="root", timeout=45)

        assert result.ok
        mock_remote_exec.assert_awaited_once_with("worker", "iptables -L", user="root", timeout=45)

    @pytest.mark.asyncio
    async def test_remote_exec_payload_includes_optional_fields(self) -> None:
        ctx = _make_ctx()

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            response = MagicMock()
            response.json.return_value = {"stdout": "ok", "stderr": "", "exit_code": 0}
            mock_post.return_value = response

            await ctx._remote_exec("worker", "whoami", user="root", timeout=30)

        body = mock_post.call_args.kwargs["json"]
        assert body["execution_slug"] == "test"
        assert body["agent_name"] == "worker"
        assert body["command"] == "whoami"
        assert body["user"] == "root"
        assert body["timeout"] == 30

    @pytest.mark.asyncio
    async def test_remote_exec_extends_http_timeout_for_long_commands(self) -> None:
        ctx = _make_ctx()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            response = MagicMock()
            response.json.return_value = {"stdout": "ok", "stderr": "", "exit_code": 0}
            mock_client.post.return_value = response
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            await ctx._remote_exec("worker", "sleep 1", timeout=900)

        mock_client_cls.assert_called_once_with(timeout=930)
