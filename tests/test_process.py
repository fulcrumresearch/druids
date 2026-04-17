"""Tests for the process model: @agent_process, done/fail/wait, spawn, scopes."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from druids import LocalImage, Runtime
from druids.process import (
    ProcessHandle,
    ProcessScope,
    _current_process,
    agent,
    agent_process,
    connect,
    done,
    emit,
    fail,
    spawn,
    wait,
)
from druids.stream import Stream
from tests.helpers import FakeAgentClient, disable_agent_launch, wait_for_server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _setup_runtime(tmp_path: Path, monkeypatch):
    """Create a runtime + root scope for testing, with agent launch disabled."""
    runtime = Runtime()
    await runtime.start()
    scope = ProcessScope(parent=None, runtime=runtime, image=LocalImage(tmp_path))
    token = _current_process.set(scope)
    disable_agent_launch(runtime, monkeypatch)
    return runtime, scope, token


async def _teardown_runtime(runtime, scope, token):
    await scope.cleanup()
    await runtime.close()
    _current_process.reset(token)


async def _make_client(runtime: Runtime, agent_id: str) -> FakeAgentClient:
    client = FakeAgentClient(runtime.server_url, runtime.execution_id or "", agent_id)
    await asyncio.to_thread(client.connect)
    return client


# ---------------------------------------------------------------------------
# done / fail / wait
# ---------------------------------------------------------------------------

def test_done_and_wait(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup_runtime(tmp_path, monkeypatch)
        try:
            worker = await agent("worker")

            @worker.on("finish")
            async def finish(result: str = "") -> str:
                done(result)
                return "ok"

            await asyncio.to_thread(wait_for_server, runtime.server_url)
            client = await _make_client(runtime, "worker")
            try:
                await asyncio.to_thread(client.register)
                assert await asyncio.to_thread(client.tool_call, "finish", {"result": "hello"}) == "ok"
                assert await wait() == "hello"
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await _teardown_runtime(runtime, scope, token)

    asyncio.run(run())


def test_fail_and_wait(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup_runtime(tmp_path, monkeypatch)
        try:
            worker = await agent("worker")

            @worker.on("abort")
            async def abort(reason: str = "") -> str:
                fail(reason or "aborted")
                return "aborting"

            await asyncio.to_thread(wait_for_server, runtime.server_url)
            client = await _make_client(runtime, "worker")
            try:
                await asyncio.to_thread(client.register)
                await asyncio.to_thread(client.tool_call, "abort", {"reason": "bad input"})
                from druids.types import ExecutionFailed
                with pytest.raises(ExecutionFailed, match="bad input"):
                    await wait()
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await _teardown_runtime(runtime, scope, token)

    asyncio.run(run())


def test_done_called_twice_first_wins(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup_runtime(tmp_path, monkeypatch)
        try:
            worker = await agent("worker")

            @worker.on("finish")
            async def finish(result: str = "") -> str:
                done(result)
                return "ok"

            await asyncio.to_thread(wait_for_server, runtime.server_url)
            client = await _make_client(runtime, "worker")
            try:
                await asyncio.to_thread(client.register)
                await asyncio.to_thread(client.tool_call, "finish", {"result": "first"})
                await asyncio.to_thread(client.tool_call, "finish", {"result": "second"})
                assert await wait() == "first"
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await _teardown_runtime(runtime, scope, token)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# @agent_process decorator
# ---------------------------------------------------------------------------

def test_agent_process_root_creates_and_cleans_up_runtime() -> None:
    """Root @agent_process creates a runtime and tears it down."""
    async def run() -> None:
        @agent_process(image=LocalImage())
        async def my_process():
            scope = _current_process.get()
            assert scope is not None
            assert scope.runtime is not None
            assert scope.runtime.server_url is not None
            return "result"

        result = await my_process()
        assert result == "result"
        # After return, runtime is closed
        assert _current_process.get() is None

    asyncio.run(run())


def test_agent_process_nested_inherits_runtime() -> None:
    """Nested @agent_process reuses parent's runtime."""
    async def run() -> None:
        @agent_process
        async def inner():
            return "inner_result"

        @agent_process(image=LocalImage())
        async def outer():
            scope = _current_process.get()
            runtime = scope.runtime
            result = await inner()
            # Inner ran in same runtime
            assert result == "inner_result"
            return "outer_result"

        assert await outer() == "outer_result"

    asyncio.run(run())


def test_agent_process_emits_done_event() -> None:
    async def run() -> None:
        events = Stream()
        handle = ProcessHandle(events=events)

        @agent_process(image=LocalImage())
        async def my_process():
            return "value"

        # Simulate spawn by setting handle
        token = _current_process.set(None)
        spawn_token = None
        from druids.process import _spawn_handle
        spawn_token = _spawn_handle.set(handle)
        try:
            await my_process()
        finally:
            _spawn_handle.reset(spawn_token)
            _current_process.reset(token)

        collected = []
        async for event in events:
            collected.append(event)
        assert any(e.type == "done" and e.data == "value" for e in collected)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Scope cleanup
# ---------------------------------------------------------------------------

def test_scope_tracks_agents(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup_runtime(tmp_path, monkeypatch)
        try:
            a = await agent("alice")
            b = await agent("bob")
            assert len(scope.agents) == 2
            assert a in scope.agents
            assert b in scope.agents
            assert "alice" in runtime.agents
            assert "bob" in runtime.agents
        finally:
            await _teardown_runtime(runtime, scope, token)
        # After cleanup, records are removed
        assert "alice" not in runtime.agents
        assert "bob" not in runtime.agents

    asyncio.run(run())


def test_scope_tracks_machines(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup_runtime(tmp_path, monkeypatch)
        try:
            # agent() with no explicit machine spawns one
            a = await agent("worker")
            assert len(scope.machines) == 1

            # agent() with explicit machine does NOT add to scope.machines
            from druids import machine as create_machine
            m = await create_machine()
            machines_before = len(scope.machines)
            b = await agent("worker2", machine=m)
            # Machine was explicitly created, so one more for create_machine,
            # but agent("worker2", machine=m) should NOT add another
            assert len(scope.machines) == machines_before  # +0 from agent call
        finally:
            await _teardown_runtime(runtime, scope, token)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Agent events
# ---------------------------------------------------------------------------

def test_agent_events_stream(tmp_path: Path, monkeypatch) -> None:
    """Agent event stream receives log entries."""
    async def run() -> None:
        runtime, scope, token = await _setup_runtime(tmp_path, monkeypatch)
        try:
            worker = await agent("worker")

            @worker.on("ping")
            async def ping() -> str:
                return "pong"

            await asyncio.to_thread(wait_for_server, runtime.server_url)
            client = await _make_client(runtime, "worker")
            try:
                await asyncio.to_thread(client.register)
                await asyncio.to_thread(client.tool_call, "ping")

                # Check that events appeared in the agent's event stream
                types = [e.type for e in worker.events.snapshot()]
                assert "agent_created" in types
                assert "tool_call" in types
                assert "tool_result" in types
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await _teardown_runtime(runtime, scope, token)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# spawn
# ---------------------------------------------------------------------------

def test_spawn_returns_handle_with_events() -> None:
    async def run() -> None:
        @agent_process(image=LocalImage())
        async def inner():
            emit("hello", {"msg": "world"})
            return 42

        @agent_process(image=LocalImage())
        async def outer():
            handle = spawn(inner)
            collected = []
            async for event in handle.events:
                collected.append(event)
                if event.type == "done":
                    break
            assert any(e.type == "hello" for e in collected)
            assert any(e.type == "done" and e.data == 42 for e in collected)
            return "ok"

        assert await outer() == "ok"

    asyncio.run(run())


def test_spawn_cancel() -> None:
    async def run() -> None:
        @agent_process(image=LocalImage())
        async def slow():
            await asyncio.sleep(100)
            return "never"

        @agent_process(image=LocalImage())
        async def outer():
            handle = spawn(slow)
            await asyncio.sleep(0.05)
            handle.cancel()
            # Events should include "cancelled"
            collected = []
            async for event in handle.events:
                collected.append(event)
            types = [e.type for e in collected]
            assert "cancelled" in types
            return "ok"

        assert await outer() == "ok"

    asyncio.run(run())


def test_spawn_failed_event() -> None:
    async def run() -> None:
        @agent_process(image=LocalImage())
        async def failing():
            raise ValueError("boom")

        @agent_process(image=LocalImage())
        async def outer():
            handle = spawn(failing)
            collected = []
            async for event in handle.events:
                collected.append(event)
                if event.type == "failed":
                    break
            assert any(e.type == "failed" and "boom" in str(e.data) for e in collected)
            return "ok"

        assert await outer() == "ok"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------

def test_emit_shows_on_process_events() -> None:
    async def run() -> None:
        @agent_process(image=LocalImage())
        async def emitter():
            emit("step", {"n": 1})
            emit("step", {"n": 2})
            return "done"

        @agent_process(image=LocalImage())
        async def outer():
            handle = spawn(emitter)
            steps = []
            async for event in handle.events:
                if event.type == "step":
                    steps.append(event.data["n"])
                if event.type == "done":
                    break
            assert steps == [1, 2]
            return "ok"

        assert await outer() == "ok"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Connections still work
# ---------------------------------------------------------------------------

def test_connect_and_message(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup_runtime(tmp_path, monkeypatch)
        try:
            sender = await agent("sender")
            receiver = await agent("receiver")
            connect(sender, receiver)

            await asyncio.to_thread(wait_for_server, runtime.server_url)
            sender_client = await _make_client(runtime, "sender")
            receiver_client = await _make_client(runtime, "receiver")
            try:
                await asyncio.to_thread(sender_client.register)
                await asyncio.to_thread(receiver_client.register)

                result = await asyncio.to_thread(
                    sender_client.tool_call,
                    "message",
                    {"receiver": "receiver", "message": "hello"},
                )
                assert "sent" in result.lower()
            finally:
                await asyncio.to_thread(sender_client.close)
                await asyncio.to_thread(receiver_client.close)
        finally:
            await _teardown_runtime(runtime, scope, token)

    asyncio.run(run())
