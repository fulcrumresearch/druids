"""Test async spawn readiness semantics."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from druids import Runtime, LocalImage
from tests.helpers import FakeAgentClient, disable_agent_launch, wait_for_server


async def _make_client(ctx: Runtime, agent_id: str) -> FakeAgentClient:
    client = FakeAgentClient(ctx.server_url, ctx.execution_id or "", agent_id)
    await asyncio.to_thread(client.connect)
    return client


def test_registered_event_set_on_register(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        ctx = Runtime(image=LocalImage(tmp_path / "agent"))
        await ctx.start()
        try:
            disable_agent_launch(ctx, monkeypatch)
            agent = await ctx.agent("worker")

            @agent.on("finish")
            async def finish() -> str:
                ctx.exit("ok")
                return "ok"

            rec = ctx._records["worker"]
            assert not rec.registered.is_set()

            assert ctx.server_url is not None
            await asyncio.to_thread(wait_for_server, ctx.server_url)

            client = await _make_client(ctx, "worker")
            try:
                await asyncio.to_thread(client.register)

                assert rec.registered.is_set()
                assert await asyncio.to_thread(client.tool_call, "finish") == "ok"
                assert await ctx.wait(timeout=5) == "ok"
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await ctx.close()

    asyncio.run(run())


def test_dynamic_agent_in_handler_is_usable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        ctx = Runtime(image=LocalImage(tmp_path / "shared"))
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

            client = await _make_client(ctx, "finder")
            try:
                await asyncio.to_thread(client.register)
                assert await asyncio.to_thread(client.tool_call, "spawn") == "worker"

                assert await ctx.wait(timeout=5) == "spawned"
                assert created_agents["worker"].machine is finder.machine
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await ctx.close()

    asyncio.run(run())


def test_spawn_readiness_blocks_until_registered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        ctx = Runtime(image=LocalImage(tmp_path / "agent"))
        await ctx.start()
        try:
            async def fake_launch(agent):
                rec = ctx._records[agent.name]

                async def delayed_register() -> None:
                    await asyncio.sleep(0.3)
                    rec.registered.set()

                asyncio.create_task(delayed_register())
                return True

            monkeypatch.setattr(ctx, "_launch_agent", fake_launch)

            started = time.perf_counter()
            agent = await ctx.agent("test-agent")
            elapsed = time.perf_counter() - started

            assert agent.name == "test-agent"
            assert ctx._records["test-agent"].registered.is_set()
            assert elapsed >= 0.25
        finally:
            await ctx.close()

    asyncio.run(run())
