"""Tests for the process model: @agent_process, done/fail/wait, spawn, scopes."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ramure import LocalImage, Runtime
from ramure.process import (
    ProcessHandle,
    ProcessScope,
    _current_process,
    agent,
    agent_process,
    connect,
    done,
    emit,
    expose,
    fail,
    spawn,
    wait,
)
from ramure.stream import Stream
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
                from ramure.types import ExecutionFailed
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
        from ramure.process import _spawn_handle
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
            from ramure import machine as create_machine
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

                # Agent-scope events live on the agent's own log.
                agent_types = [e.type for e in worker.events.snapshot()]
                assert "tool_call" in agent_types
                assert "tool_result" in agent_types

                # Runtime-scope events (agent lifecycle, connections)
                # live on the runtime log.
                runtime_types = [e.type for e in runtime.log.stream.snapshot()]
                assert "execution_started" in runtime_types
                assert "agent_created" in runtime_types
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


def test_spawn_failed_event_empty_message_uses_class_name() -> None:
    """A bare exception with no message must still produce a meaningful
    `data` on the `failed` event (the class name), not an empty string.

    This is the regression case for asyncio.TimeoutError, which carries
    no args: ``str(TimeoutError())`` is ``""``.
    """
    async def run() -> None:
        @agent_process(image=LocalImage(), timeout=0.05)
        async def hangs():
            # Never completes; the `timeout=` on the decorator trips first.
            await asyncio.sleep(10)

        @agent_process(image=LocalImage())
        async def outer():
            handle = spawn(hangs)
            async for event in handle.events:
                if event.type == "failed":
                    return event.data
            return None

        data = await outer()
        assert data == "TimeoutError", data

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
# expose / endpoints / attach
# ---------------------------------------------------------------------------

def test_expose_function_registers_endpoint() -> None:
    async def run() -> None:
        @agent_process(image=LocalImage())
        async def inner():
            @expose
            async def add(a: int, b: int) -> int:
                return a + b

            emit("ready", None)
            return await wait()

        @agent_process(image=LocalImage())
        async def outer():
            handle = spawn(inner)
            # Wait for the endpoint to be registered.
            async for event in handle.events:
                if event.type == "ready":
                    break
            assert await handle.call("add", a=2, b=3) == 5
            handle.cancel()
            return "ok"

        assert await outer() == "ok"

    asyncio.run(run())


def test_expose_rejects_non_async_function() -> None:
    async def run() -> None:
        @agent_process(image=LocalImage())
        async def proc():
            with pytest.raises(TypeError):
                expose(lambda: None)          # not async
            done(None)
            return await wait()

        await proc()

    asyncio.run(run())


def test_call_awaits_scope_ready() -> None:
    """handle.call() before the spawned task has started should not race."""
    async def run() -> None:
        started = asyncio.Event()

        @agent_process(image=LocalImage())
        async def inner():
            @expose
            async def ping() -> str:
                return "pong"

            started.set()
            return await wait()

        @agent_process(image=LocalImage())
        async def outer():
            handle = spawn(inner)
            # Call immediately, before the inner task has run.
            assert not started.is_set()
            result = await handle.call("ping")
            assert result == "pong"
            handle.cancel()
            return "ok"

        assert await outer() == "ok"

    asyncio.run(run())


def test_call_unknown_endpoint_raises() -> None:
    async def run() -> None:
        @agent_process(image=LocalImage())
        async def inner():
            return await wait()

        @agent_process(image=LocalImage())
        async def outer():
            handle = spawn(inner)
            with pytest.raises(ValueError, match="No endpoint"):
                await handle.call("nope")
            handle.cancel()
            return "ok"

        assert await outer() == "ok"

    asyncio.run(run())


def test_endpoint_runs_in_child_scope() -> None:
    """emit() inside an endpoint goes to the child's stream, not the caller's."""
    async def run() -> None:
        @agent_process(image=LocalImage())
        async def inner():
            @expose
            async def tick() -> str:
                emit("tick", {"n": 1})
                return "ok"

            emit("ready", None)
            return await wait()

        @agent_process(image=LocalImage())
        async def outer():
            handle = spawn(inner)
            async for event in handle.events:
                if event.type == "ready":
                    break
            await handle.call("tick")
            # The "tick" event should appear on the child's stream.
            saw_tick = False
            async for event in handle.events:
                if event.type == "tick":
                    saw_tick = True
                    break
            assert saw_tick
            handle.cancel()
            return "ok"

        assert await outer() == "ok"

    asyncio.run(run())


def test_attach_registers_endpoints_as_tools(tmp_path: Path, monkeypatch) -> None:
    """handle.attach(agent) should install endpoints as invokable tools."""
    async def run() -> None:
        runtime, scope, token = await _setup_runtime(tmp_path, monkeypatch)
        try:
            @agent_process(image=LocalImage())
            async def pool():
                @expose
                async def add(a: int, b: int) -> int:
                    return a + b

                @expose
                async def echo(msg: str) -> str:
                    return msg

                emit("ready", None)
                return await wait()

            handle = spawn(pool)
            # Wait until endpoints are registered.
            async for event in handle.events:
                if event.type == "ready":
                    break

            dispatcher = await agent("dispatcher")
            await handle.attach(dispatcher)
            assert "add" in dispatcher.handlers
            assert "echo" in dispatcher.handlers

            # Tool handlers route into the child scope and return correctly.
            assert await dispatcher.handlers["add"](a=2, b=3) == 5
            assert await dispatcher.handlers["echo"](msg="hi") == "hi"

            handle.cancel()
        finally:
            await _teardown_runtime(runtime, scope, token)

    asyncio.run(run())


def test_attach_respects_only_and_prefix(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup_runtime(tmp_path, monkeypatch)
        try:
            @agent_process(image=LocalImage())
            async def pool():
                @expose
                async def submit_task(task: str) -> str:
                    return task

                @expose
                async def cancel_task(task_id: str) -> str:
                    return task_id

                emit("ready", None)
                return await wait()

            handle = spawn(pool)
            async for event in handle.events:
                if event.type == "ready":
                    break

            dispatcher = await agent("dispatcher")
            await handle.attach(dispatcher, only=["submit_task"], prefix="pool_")
            assert "pool_submit_task" in dispatcher.handlers
            assert "pool_cancel_task" not in dispatcher.handlers
            assert "submit_task" not in dispatcher.handlers

            handle.cancel()
        finally:
            await _teardown_runtime(runtime, scope, token)

    asyncio.run(run())


def test_attach_unknown_endpoint_raises(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup_runtime(tmp_path, monkeypatch)
        try:
            @agent_process(image=LocalImage())
            async def pool():
                emit("ready", None)
                return await wait()

            handle = spawn(pool)
            async for event in handle.events:
                if event.type == "ready":
                    break

            dispatcher = await agent("dispatcher")
            with pytest.raises(ValueError, match="No endpoint"):
                await handle.attach(dispatcher, only=["ghost"])

            handle.cancel()
        finally:
            await _teardown_runtime(runtime, scope, token)

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
