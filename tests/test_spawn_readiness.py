"""Test async spawn readiness semantics."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from druids import LocalImage, Runtime
from druids.process import ProcessScope, _current_process, agent
from tests.helpers import FakeAgentClient, disable_agent_launch, wait_for_server


async def _setup(tmp_path, monkeypatch):
    runtime = Runtime()
    await runtime.start()
    scope = ProcessScope(parent=None, runtime=runtime, image=LocalImage(tmp_path))
    token = _current_process.set(scope)
    return runtime, scope, token


async def _teardown(runtime, scope, token):
    await scope.cleanup()
    await runtime.close()
    _current_process.reset(token)


async def _make_client(runtime, agent_id):
    client = FakeAgentClient(runtime.server_url, runtime.execution_id or "", agent_id)
    await asyncio.to_thread(client.connect)
    return client


def test_registered_event_set_on_register(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup(tmp_path, monkeypatch)
        try:
            disable_agent_launch(runtime, monkeypatch)
            worker = await agent("worker")

            @worker.on("finish")
            async def finish() -> str:
                from druids.process import done
                done("ok")
                return "ok"

            rec = runtime.records["worker"]
            assert not rec.registered.is_set()

            await asyncio.to_thread(wait_for_server, runtime.server_url)
            client = await _make_client(runtime, "worker")
            try:
                await asyncio.to_thread(client.register)
                assert rec.registered.is_set()
                assert await asyncio.to_thread(client.tool_call, "finish") == "ok"
                from druids.process import wait
                assert await wait() == "ok"
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await _teardown(runtime, scope, token)

    asyncio.run(run())


def test_dynamic_agent_in_handler_is_usable(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup(tmp_path, monkeypatch)
        try:
            disable_agent_launch(runtime, monkeypatch)
            finder = await agent("finder")
            created_agents: dict[str, object] = {}

            @finder.on("spawn")
            async def spawn_agent() -> str:
                worker = await agent("worker", machine=finder.machine)
                created_agents["worker"] = worker
                from druids.process import done
                done("spawned")
                return worker.name

            await asyncio.to_thread(wait_for_server, runtime.server_url)
            client = await _make_client(runtime, "finder")
            try:
                await asyncio.to_thread(client.register)
                assert await asyncio.to_thread(client.tool_call, "spawn") == "worker"
                from druids.process import wait
                assert await wait() == "spawned"
                assert created_agents["worker"].machine is finder.machine
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await _teardown(runtime, scope, token)

    asyncio.run(run())


def test_spawn_readiness_blocks_until_registered(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup(tmp_path, monkeypatch)
        try:
            async def fake_launch(ag):
                rec = runtime.records[ag.name]

                async def delayed_register() -> None:
                    await asyncio.sleep(0.3)
                    rec.registered.set()

                asyncio.create_task(delayed_register())
                return True

            monkeypatch.setattr(runtime, "launch_agent", fake_launch)

            started = time.perf_counter()
            worker = await agent("test-agent")
            elapsed = time.perf_counter() - started

            assert worker.name == "test-agent"
            assert runtime.records["test-agent"].registered.is_set()
            assert elapsed >= 0.25
        finally:
            await _teardown(runtime, scope, token)

    asyncio.run(run())
