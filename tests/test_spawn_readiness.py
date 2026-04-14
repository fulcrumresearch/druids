"""Test async spawn readiness semantics."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from druids import Context, LocalImage
from tests.helpers import FakeAgentClient, disable_agent_launch, wait_for_server


def test_registered_event_set_on_register(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        ctx = Context(image=LocalImage(tmp_path / "agent"))
        await ctx.start()
        try:
            disable_agent_launch(ctx, monkeypatch)
            agent = await ctx.agent("worker")

            @agent.on("finish")
            async def finish() -> str:
                ctx.exit("ok")
                return "ok"

            assert not agent._channel.registered.is_set()

            assert ctx.server_url is not None
            await asyncio.to_thread(wait_for_server, ctx.server_url)

            client = FakeAgentClient(ctx.server_url, ctx.execution_id or "", "worker")
            client.start_events()
            await asyncio.to_thread(client.register)

            assert agent._channel.registered.is_set()
            assert await asyncio.to_thread(client.tool_call, "finish") == "ok"
            assert await ctx.wait(timeout=5) == "ok"
        finally:
            await ctx.close()

    asyncio.run(run())


def test_dynamic_agent_in_handler_is_usable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        ctx = Context(image=LocalImage(tmp_path / "shared"))
        await ctx.start()
        try:
            disable_agent_launch(ctx, monkeypatch)
            finder = await ctx.agent("finder")
            created_agents: dict[str, object] = {}

            @finder.on("spawn")
            async def spawn() -> str:
                worker = await ctx.agent("worker", machine=finder.machine)
                created_agents["worker"] = worker
                ctx.exit("spawned")
                return worker.name

            assert ctx.server_url is not None
            await asyncio.to_thread(wait_for_server, ctx.server_url)

            client = FakeAgentClient(ctx.server_url, ctx.execution_id or "", "finder")
            client.start_events()
            await asyncio.to_thread(client.register)
            assert await asyncio.to_thread(client.tool_call, "spawn") == "worker"

            assert await ctx.wait(timeout=5) == "spawned"
            assert created_agents["worker"].machine is finder.machine
        finally:
            await ctx.close()

    asyncio.run(run())


def test_spawn_readiness_blocks_until_registered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        ctx = Context(image=LocalImage(tmp_path / "agent"))
        await ctx.start()
        try:
            async def fake_launch(agent):
                async def delayed_register() -> None:
                    await asyncio.sleep(0.3)
                    agent._channel.registered.set()

                asyncio.create_task(delayed_register())
                return True

            monkeypatch.setattr(ctx, "_launch_agent", fake_launch)

            started = time.perf_counter()
            agent = await ctx.agent("test-agent")
            elapsed = time.perf_counter() - started

            assert agent.name == "test-agent"
            assert agent._channel.registered.is_set()
            assert elapsed >= 0.25
        finally:
            await ctx.close()

    asyncio.run(run())
