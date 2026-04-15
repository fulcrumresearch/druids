"""Tests for agent-owned KV state."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from druids import LocalImage, Runtime
from tests.helpers import FakeAgentClient, disable_agent_launch, wait_for_server


async def _make_client(ctx: Runtime, agent_id: str) -> FakeAgentClient:
    client = FakeAgentClient(ctx.server_url, ctx.execution_id or "", agent_id)
    await asyncio.to_thread(client.connect)
    return client


def test_set_and_get_state_via_tool_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Agent can set_state then get_state through tool calls."""

    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        ctx = Runtime(image=LocalImage(tmp_path / "worker"))
        await ctx.start()
        try:
            disable_agent_launch(ctx, monkeypatch)
            worker = await ctx.agent("worker")

            @worker.on("finish")
            async def finish() -> str:
                ctx.exit("ok")
                return "ok"

            assert ctx.server_url is not None
            await asyncio.to_thread(wait_for_server, ctx.server_url)

            client = await _make_client(ctx, "worker")
            try:
                await asyncio.to_thread(client.register)

                result = await asyncio.to_thread(
                    client.tool_call, "set_state", {"key": "status", "value": "running"}
                )
                assert "status" in result

                result = await asyncio.to_thread(
                    client.tool_call, "get_state", {"key": "status"}
                )
                assert result == "running"

                assert worker.state["status"] == "running"

                assert await asyncio.to_thread(client.tool_call, "finish") == "ok"
                assert await ctx.wait(timeout=5) == "ok"
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await ctx.close()

    asyncio.run(run())


def test_get_state_returns_null_for_missing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_state returns None (JSON null) for a key that hasn't been set."""

    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        ctx = Runtime(image=LocalImage(tmp_path / "worker"))
        await ctx.start()
        try:
            disable_agent_launch(ctx, monkeypatch)
            worker = await ctx.agent("worker")

            @worker.on("finish")
            async def finish() -> str:
                ctx.exit("ok")
                return "ok"

            assert ctx.server_url is not None
            await asyncio.to_thread(wait_for_server, ctx.server_url)

            client = await _make_client(ctx, "worker")
            try:
                await asyncio.to_thread(client.register)

                result = await asyncio.to_thread(
                    client.tool_call, "get_state", {"key": "nonexistent"}
                )
                assert result is None

                assert await asyncio.to_thread(client.tool_call, "finish") == "ok"
                assert await ctx.wait(timeout=5) == "ok"
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await ctx.close()

    asyncio.run(run())


def test_state_overwrite_replaces_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setting the same key twice replaces the value."""

    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        ctx = Runtime(image=LocalImage(tmp_path / "worker"))
        await ctx.start()
        try:
            disable_agent_launch(ctx, monkeypatch)
            worker = await ctx.agent("worker")

            @worker.on("finish")
            async def finish() -> str:
                ctx.exit("ok")
                return "ok"

            assert ctx.server_url is not None
            await asyncio.to_thread(wait_for_server, ctx.server_url)

            client = await _make_client(ctx, "worker")
            try:
                await asyncio.to_thread(client.register)

                await asyncio.to_thread(
                    client.tool_call, "set_state", {"key": "step", "value": "1"}
                )
                await asyncio.to_thread(
                    client.tool_call, "set_state", {"key": "step", "value": "2"}
                )
                result = await asyncio.to_thread(
                    client.tool_call, "get_state", {"key": "step"}
                )
                assert result == "2"
                assert worker.state["step"] == "2"

                assert await asyncio.to_thread(client.tool_call, "finish") == "ok"
                assert await ctx.wait(timeout=5) == "ok"
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await ctx.close()

    asyncio.run(run())


def test_state_is_per_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each agent has its own independent state."""

    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        ctx = Runtime()
        await ctx.start()
        try:
            disable_agent_launch(ctx, monkeypatch)
            alice = await ctx.agent("alice", image=LocalImage(tmp_path / "alice"))
            bob = await ctx.agent("bob", image=LocalImage(tmp_path / "bob"))

            @alice.on("finish")
            async def finish() -> str:
                ctx.exit("ok")
                return "ok"

            assert ctx.server_url is not None
            await asyncio.to_thread(wait_for_server, ctx.server_url)

            alice_client = await _make_client(ctx, "alice")
            bob_client = await _make_client(ctx, "bob")
            try:
                await asyncio.to_thread(alice_client.register)
                await asyncio.to_thread(bob_client.register)

                await asyncio.to_thread(
                    alice_client.tool_call, "set_state", {"key": "role", "value": "builder"}
                )
                await asyncio.to_thread(
                    bob_client.tool_call, "set_state", {"key": "role", "value": "reviewer"}
                )

                assert await asyncio.to_thread(
                    alice_client.tool_call, "get_state", {"key": "role"}
                ) == "builder"
                assert await asyncio.to_thread(
                    bob_client.tool_call, "get_state", {"key": "role"}
                ) == "reviewer"

                assert alice.state["role"] == "builder"
                assert bob.state["role"] == "reviewer"

                assert await asyncio.to_thread(alice_client.tool_call, "finish") == "ok"
                assert await ctx.wait(timeout=5) == "ok"
            finally:
                await asyncio.to_thread(alice_client.close)
                await asyncio.to_thread(bob_client.close)
        finally:
            await ctx.close()

    asyncio.run(run())


def test_handler_can_read_and_write_agent_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Orchestrator-side tool handlers can access agent.state directly."""

    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        ctx = Runtime(image=LocalImage(tmp_path / "worker"))
        await ctx.start()
        try:
            disable_agent_launch(ctx, monkeypatch)
            worker = await ctx.agent("worker")

            @worker.on("increment")
            async def increment() -> str:
                count = int(worker.state.get("count", "0"))
                worker.state["count"] = str(count + 1)
                return worker.state["count"]

            @worker.on("finish")
            async def finish() -> str:
                ctx.exit(worker.state.get("count", "0"))
                return "ok"

            assert ctx.server_url is not None
            await asyncio.to_thread(wait_for_server, ctx.server_url)

            client = await _make_client(ctx, "worker")
            try:
                await asyncio.to_thread(client.register)

                assert await asyncio.to_thread(client.tool_call, "increment") == "1"
                assert await asyncio.to_thread(client.tool_call, "increment") == "2"
                assert await asyncio.to_thread(client.tool_call, "increment") == "3"

                assert await asyncio.to_thread(
                    client.tool_call, "get_state", {"key": "count"}
                ) == "3"

                assert await asyncio.to_thread(client.tool_call, "finish") == "ok"
                assert await ctx.wait(timeout=5) == "3"
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await ctx.close()

    asyncio.run(run())


def test_set_state_and_get_state_in_registered_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """set_state and get_state appear in the tool list on registration."""

    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        ctx = Runtime(image=LocalImage(tmp_path / "worker"))
        await ctx.start()
        try:
            disable_agent_launch(ctx, monkeypatch)
            worker = await ctx.agent("worker")

            @worker.on("finish")
            async def finish() -> str:
                ctx.exit("ok")
                return "ok"

            assert ctx.server_url is not None
            await asyncio.to_thread(wait_for_server, ctx.server_url)

            client = await _make_client(ctx, "worker")
            try:
                tools = await asyncio.to_thread(client.register)

                tool_names = [t["name"] for t in tools]
                assert "set_state" in tool_names
                assert "get_state" in tool_names

                set_state_tool = next(t for t in tools if t["name"] == "set_state")
                assert "key" in set_state_tool["parameters"]["properties"]
                assert "value" in set_state_tool["parameters"]["properties"]
                assert set(set_state_tool["parameters"]["required"]) == {"key", "value"}

                get_state_tool = next(t for t in tools if t["name"] == "get_state")
                assert "key" in get_state_tool["parameters"]["properties"]
                assert get_state_tool["parameters"]["required"] == ["key"]

                assert await asyncio.to_thread(client.tool_call, "finish") == "ok"
                assert await ctx.wait(timeout=5) == "ok"
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await ctx.close()

    asyncio.run(run())
