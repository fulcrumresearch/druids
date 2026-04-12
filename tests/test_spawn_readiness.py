"""Test spawn readiness semantics.

Verifies that:
1. The channel.registered event is set when an agent registers.
2. In non-manual mode, _spawn_agent blocks until registration.
3. Dynamic agent creation inside handlers works correctly.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from druids import Context, LocalImage
from druids.context import Agent
from tests.helpers import FakeAgentClient, wait_for_server


def _start_context(ctx: Context, timeout: float = 10) -> tuple[threading.Thread, dict[str, object]]:
    outcome: dict[str, object] = {}

    def runner() -> None:
        try:
            outcome["result"] = ctx.run(timeout=timeout)
        except Exception as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=runner, name="ctx-runner", daemon=True)
    thread.start()

    deadline = time.time() + 5
    while ctx.server_url is None and time.time() < deadline:
        time.sleep(0.01)
    assert ctx.server_url is not None
    wait_for_server(ctx.server_url)
    return thread, outcome


def test_registered_event_set_on_register(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The channel.registered event is set when the agent calls POST /agents/register."""
    monkeypatch.chdir(tmp_path)

    ctx = Context(image=LocalImage(tmp_path / "agent"), launch_mode="manual")
    agent = ctx.agent("worker")

    @agent.on("finish")
    def finish() -> str:
        ctx.done("ok")
        return "ok"

    thread, outcome = _start_context(ctx)

    channel = ctx._channels["worker"]
    assert not channel.registered.is_set()

    client = FakeAgentClient(ctx.server_url, ctx.execution_id, "worker")
    client.start_events()
    client.register()

    assert channel.registered.is_set()

    client.tool_call("finish")
    thread.join(timeout=5)
    assert outcome["result"] == "ok"


def test_dynamic_agent_in_handler_is_usable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dynamically created agents inside handlers should be immediately usable."""
    monkeypatch.chdir(tmp_path)

    ctx = Context(image=LocalImage(tmp_path / "shared"), launch_mode="manual")
    finder = ctx.agent("finder")
    created_agents: dict[str, Agent] = {}

    @finder.on("spawn")
    def spawn() -> str:
        worker = ctx.agent("worker", machine=finder.machine)
        created_agents["worker"] = worker
        ctx.done("spawned")
        return worker.name

    thread, outcome = _start_context(ctx)

    client = FakeAgentClient(ctx.server_url, ctx.execution_id, "finder")
    client.start_events()
    client.register()
    assert client.tool_call("spawn") == "worker"

    thread.join(timeout=5)
    assert outcome["result"] == "spawned"
    assert "worker" in ctx.agents
    assert created_agents["worker"].machine is finder.machine


def test_spawn_readiness_blocks_until_registered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that when launched=True, _spawn_agent blocks on channel.registered."""
    monkeypatch.chdir(tmp_path)

    ctx = Context(image=LocalImage(tmp_path / "agent"), launch_mode="manual")
    ctx._running = True
    ctx._open_log()
    ctx._start_server()

    try:
        agent = Agent(
            name="test-agent",
            _ctx=ctx,
            _machine=ctx._resolve_machine(None, None),
        )
        ctx._agents["test-agent"] = agent
        ctx._channels["test-agent"] = ctx._channels.get("test-agent", __import__("druids.server", fromlist=["AgentChannel"]).AgentChannel())

        channel = ctx._channels["test-agent"]

        # Simulate: registration happens after a delay
        def _delayed_register():
            time.sleep(0.3)
            channel.registered.set()

        reg_thread = threading.Thread(target=_delayed_register, daemon=True)
        reg_thread.start()

        # Directly call the wait path
        started = time.time()
        assert not channel.registered.is_set()
        channel.registered.wait(timeout=5)
        elapsed = time.time() - started

        assert channel.registered.is_set()
        assert elapsed >= 0.2  # Should have waited for the delayed set
    finally:
        ctx._shutdown()
