"""Test async spawn readiness semantics."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from ramure import LocalImage, Runtime
from ramure.machines.base import Image, Machine
from ramure.process import ProcessScope, _current_process, agent
from ramure.types import ExecResult
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
                from ramure.process import done
                done("ok")
                return "ok"

            ag = runtime.agents["worker"]
            assert not ag.registered.is_set()

            await asyncio.to_thread(wait_for_server, runtime.server_url)
            client = await _make_client(runtime, "worker")
            try:
                await asyncio.to_thread(client.register)
                assert ag.registered.is_set()
                assert await asyncio.to_thread(client.tool_call, "finish") == "ok"
                from ramure.process import wait
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
                from ramure.process import done
                done("spawned")
                return worker.name

            await asyncio.to_thread(wait_for_server, runtime.server_url)
            client = await _make_client(runtime, "finder")
            try:
                await asyncio.to_thread(client.register)
                assert await asyncio.to_thread(client.tool_call, "spawn") == "worker"
                from ramure.process import wait
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
                target = runtime.agents[ag.name]

                async def delayed_register() -> None:
                    await asyncio.sleep(0.3)
                    target.registered.set()

                asyncio.create_task(delayed_register())
                return True

            monkeypatch.setattr(runtime, "launch_agent", fake_launch)

            started = time.perf_counter()
            worker = await agent("test-agent")
            elapsed = time.perf_counter() - started

            assert worker.name == "test-agent"
            assert runtime.agents["test-agent"].registered.is_set()
            assert elapsed >= 0.25
        finally:
            await _teardown(runtime, scope, token)

    asyncio.run(run())


def test_usage_event_is_mirrored_to_runtime_log(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup(tmp_path, monkeypatch)
        try:
            disable_agent_launch(runtime, monkeypatch)
            worker = await agent("worker")

            await asyncio.to_thread(wait_for_server, runtime.server_url)
            client = await _make_client(runtime, "worker")
            try:
                await asyncio.to_thread(client.register)
                await asyncio.to_thread(
                    client._send,
                    {
                        "type": "event",
                        "event_type": "usage",
                        "data": {
                            "model": "test-model",
                            "total_tokens": 123,
                            "cost": {"total": 0.0042},
                        },
                    },
                )
                assert await asyncio.to_thread(client.next_event, "usage")

                assert runtime.log is not None
                usage_entries = [
                    e for e in runtime.log.after(0) if e.type == "usage"
                ]
                assert usage_entries[-1].data == {
                    "agent": worker.name,
                    "model": "test-model",
                    "total_tokens": 123,
                    "cost": {"total": 0.0042},
                }
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await _teardown(runtime, scope, token)

    asyncio.run(run())


def test_spawned_machine_stops_if_agent_launch_fails(tmp_path: Path, monkeypatch) -> None:
    class StoppableMachine(Machine):
        stopped = False

        async def exec(
            self,
            command: str,
            *,
            user: str = "agent",
            timeout: int | None = None,
        ) -> ExecResult:
            return ExecResult(0, "", "", command=command)

        async def write_file(self, path: str, content: bytes | str) -> None:
            return None

        async def read_file(self, path: str) -> bytes:
            return b""

        async def stop(self) -> None:
            self.stopped = True

    class OneMachineImage(Image):
        id = "one-machine"

        def __init__(self, machine: StoppableMachine) -> None:
            self.machine = machine

        async def spawn(self) -> Machine:
            return self.machine

    async def run() -> None:
        runtime, scope, token = await _setup(tmp_path, monkeypatch)
        machine = StoppableMachine()
        try:
            async def fail_launch(ag):
                raise RuntimeError("launch failed")

            monkeypatch.setattr(runtime, "launch_agent", fail_launch)

            with pytest.raises(RuntimeError, match="launch failed"):
                await agent("bad", image=OneMachineImage(machine))

            assert machine.stopped
            assert "bad" not in runtime.agents
        finally:
            await _teardown(runtime, scope, token)

    asyncio.run(run())
