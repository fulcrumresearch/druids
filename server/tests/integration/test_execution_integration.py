"""Integration tests for the Execution lifecycle with real relay connections.

Tests use an in-process stub agent via the relay hub (relay_stub fixture).
No Morph VMs, no bridge subprocesses, no database. The Execution object is
constructed directly with agents wired to relay bridge IDs.
"""

import asyncio
from unittest.mock import MagicMock
from uuid import uuid4

from druids_server.lib.acp import ACPConfig
from druids_server.lib.agents.base import Agent
from druids_server.lib.agents.config import AgentConfig
from druids_server.lib.connection import AgentConnection
from druids_server.lib.execution import Execution


def _make_config(name: str = "stub") -> AgentConfig:
    return AgentConfig(name=name)


async def _connect_agent(ex: Execution, config: AgentConfig, bridge_id: str, bridge_token: str) -> Agent:
    """Connect an agent through the relay hub, returning a fully-wired Agent."""
    conn = AgentConnection(bridge_id, bridge_token)
    await conn.start()

    acp = ACPConfig()
    session_id = await Agent._create_acp_session(
        config,
        acp,
        ex.slug,
        conn,
    )

    agent = Agent(
        config=config,
        machine=MagicMock(),
        bridge_id=bridge_id,
        bridge_token=bridge_token,
        session_id=session_id,
        connection=conn,
    )
    ex._bind_trace(config.name, conn)
    ex.agents[config.name] = agent
    return agent


async def test_create_acp_session(relay_stub):
    """_create_acp_session connects to an agent through the relay hub."""
    bridge_id, bridge_token = relay_stub
    ex = Execution(id=uuid4(), slug="test-execution", user_id="test-user")
    config = _make_config()

    try:
        agent = await _connect_agent(ex, config, bridge_id, bridge_token)
        assert "stub" in ex.agents
        assert agent.session_id == "stub-session-1"
    finally:
        await ex.stop()


async def test_events_through_connection(relay_stub):
    """Session update events flow through the connection after a prompt."""
    bridge_id, bridge_token = relay_stub
    ex = Execution(id=uuid4(), slug="test-execution", user_id="test-user")
    config = _make_config()

    try:
        agent = await _connect_agent(ex, config, bridge_id, bridge_token)
        result = await asyncio.wait_for(agent.connection.prompt("Hello"), timeout=5)
        assert result["stopReason"] == "end_turn"
    finally:
        await ex.stop()


async def test_done_sets_status():
    """done() transitions status to completed."""
    ex = Execution(id=uuid4(), slug="test-execution", user_id="test-user")
    assert ex.status == "created"

    await ex.done(result="all good")
    assert ex.status == "completed"
    assert ex._result == "all good"
    assert ex._done.is_set()


async def test_fail_sets_status():
    """fail() transitions status to failed."""
    ex = Execution(id=uuid4(), slug="test-execution", user_id="test-user")

    ex.fail("something broke")
    assert ex.status == "failed"
    assert ex._failure_reason == "something broke"
    assert ex._done.is_set()


async def test_stop_cleans_up(relay_stub):
    """stop() disconnects agents and clears the registry."""
    bridge_id, bridge_token = relay_stub
    ex = Execution(id=uuid4(), slug="test-execution", user_id="test-user")
    config = _make_config()

    await _connect_agent(ex, config, bridge_id, bridge_token)
    assert len(ex.agents) == 1

    await ex.stop()

    assert len(ex.agents) == 0
    assert ex.status == "stopped"
