"""Agent disconnect escalation.

A registered agent whose websocket drops is gone for good — nothing
reconnects it. The runtime must fail the owning scope so ``wait()`` raises
instead of blocking forever. Expected disconnects (scope teardown, viewers
that never registered) must not fail anything.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ramure import LocalImage, Runtime
from ramure.process import ProcessScope, _current_process, agent, wait
from ramure.types import ExecutionFailed
from tests.helpers import FakeAgentClient, disable_agent_launch, wait_for_server


async def _setup(tmp_path, monkeypatch):
    runtime = Runtime()
    await runtime.start()
    scope = ProcessScope(parent=None, runtime=runtime, image=LocalImage(tmp_path))
    token = _current_process.set(scope)
    disable_agent_launch(runtime, monkeypatch)
    await asyncio.to_thread(wait_for_server, runtime.server_url)
    return runtime, scope, token


async def _teardown(runtime, scope, token):
    await scope.cleanup()
    await runtime.close()
    _current_process.reset(token)


async def _connected_client(runtime, agent_id):
    client = FakeAgentClient(runtime.server_url, runtime.execution_id or "", agent_id)
    await asyncio.to_thread(client.connect)
    return client


def test_registered_agent_disconnect_fails_wait(tmp_path: Path, monkeypatch) -> None:
    """Dropping a registered agent's connection makes wait() raise."""

    async def run() -> None:
        runtime, scope, token = await _setup(tmp_path, monkeypatch)
        try:
            await agent("worker")
            client = await _connected_client(runtime, "worker")
            await asyncio.to_thread(client.register)

            waiter = asyncio.ensure_future(wait())
            await asyncio.sleep(0)  # waiter is now blocked on the outcome

            await asyncio.to_thread(client.close)

            with pytest.raises(ExecutionFailed, match="worker"):
                await asyncio.wait_for(waiter, timeout=5)
        finally:
            await _teardown(runtime, scope, token)

    asyncio.run(run())


def test_cleanup_disconnect_is_not_a_failure(tmp_path: Path, monkeypatch) -> None:
    """Teardown kills agents; the resulting disconnect must not fail the scope."""

    async def run() -> None:
        runtime, scope, token = await _setup(tmp_path, monkeypatch)
        try:
            await agent("worker")
            ag = runtime.agents["worker"]
            client = await _connected_client(runtime, "worker")
            await asyncio.to_thread(client.register)

            await scope.cleanup()
            assert ag.shutting_down is True

            await asyncio.to_thread(client.close)
            await asyncio.sleep(0.3)  # let the server process the close

            assert not scope._outcome.done()
        finally:
            await runtime.close()
            _current_process.reset(token)

    asyncio.run(run())


def test_unregistered_viewer_disconnect_is_ignored(tmp_path: Path, monkeypatch) -> None:
    """A sync-only client (log viewer) coming and going must not fail the scope."""

    async def run() -> None:
        runtime, scope, token = await _setup(tmp_path, monkeypatch)
        try:
            await agent("worker")
            viewer = await _connected_client(runtime, "worker")
            await asyncio.to_thread(viewer.sync)
            await asyncio.to_thread(viewer.close)
            await asyncio.sleep(0.3)

            assert not scope._outcome.done()
        finally:
            await _teardown(runtime, scope, token)

    asyncio.run(run())
