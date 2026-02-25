"""Integration tests for the Execution lifecycle with real connections.

StubAgent overrides Agent.exec() to start the stub agent on the local
bridge instead of provisioning a Morph VM. Everything else -- AgentConnection,
handlers, event queue -- runs unpatched.
"""

import asyncio
import sys
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from orpheus.lib.agents.base import Agent
from orpheus.lib.execution import Execution
from orpheus.lib.machine import Machine
from orpheus.paths import STUB_AGENT_PATH


@dataclass
class StubAgent(Agent):
    """Agent that uses the local bridge with stub_agent.py instead of Morph."""

    _bridge_url: str = ""

    async def exec(self, machine: Machine | MagicMock | None = None):
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._bridge_url}/start",
                json={"command": sys.executable, "args": [str(STUB_AGENT_PATH)]},
                timeout=5,
            )
            resp.raise_for_status()
        # Set a mock machine that provides instance_id and bridge_url
        mock_machine = MagicMock()
        mock_machine.instance_id = "stub-instance"
        mock_machine.bridge_url = self._bridge_url
        mock_machine.stop = AsyncMock()
        self.machine = mock_machine
        return []


def _make_execution(bridge_url: str, user_prompt: str | None = None) -> Execution:
    agent = StubAgent(
        name="stub",
        _bridge_url=bridge_url,
        user_prompt=user_prompt,
    )
    return Execution(
        id=uuid4(),
        slug="test-execution",
        root=agent,
        user_id="test-user",
    )


@pytest.fixture(autouse=True)
def _bypass_provisioning():
    """Bypass _provision_machine for integration tests (StubAgent sets its own machine)."""
    mock_machine = MagicMock()
    mock_machine.instance_id = "stub-instance"
    mock_machine.bridge_url = "http://stub"
    mock_machine.stop = AsyncMock()
    with patch.object(Execution, "_provision_machine", return_value=mock_machine):
        yield


async def test_start_connects_and_runs(bridge_process):
    """Execution.start() connects to agent and transitions to running."""
    ex = _make_execution(bridge_process)
    try:
        await ex.start()
        assert ex.status == "running"
        assert "stub" in ex.connections
        assert ex.connections["stub"].session_id == "stub-session-1"
    finally:
        await ex.stop()


async def test_events_received(bridge_process):
    """Start with user_prompt, drain events, verify session_update events arrive."""
    ex = _make_execution(bridge_process, user_prompt="Do something")
    try:
        await ex.start()

        # The user_prompt is fire-and-forget. Collect events until we have
        # at least 3 (agent_message_chunk + tool_call + tool_call_update).
        collected = []

        async def drain():
            async for event in ex.events():
                collected.append(event)
                if len(collected) >= 3:
                    return

        await asyncio.wait_for(drain(), timeout=10)

        assert len(collected) >= 3
        for agent_name, event in collected:
            assert agent_name == "stub"
            assert event["type"] == "session_update"
    finally:
        await ex.stop()


async def test_submit_and_resume_cycle(bridge_process):
    """submit() marks done; resume() clears it."""
    ex = _make_execution(bridge_process)
    try:
        await ex.start()

        await ex.submit(pr_url="https://github.com/test/pr/1", summary="Done")
        assert ex.done
        assert ex.status == "submitted"

        ex.resume()
        assert not ex.done
        assert ex.status == "running"
    finally:
        await ex.stop()


async def test_stop_cleans_up(bridge_process):
    """stop() disconnects agents and clears programs."""
    ex = _make_execution(bridge_process)
    await ex.start()
    assert len(ex.connections) == 1
    assert len(ex.programs) == 1

    await ex.stop()

    assert len(ex.connections) == 0
    assert len(ex.programs) == 0
    assert ex.status == "stopped"
