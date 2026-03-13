"""Integration tests for AgentConnection through the relay hub.

Each test gets a stub agent communicating via the relay hub
(via the relay_stub fixture). The tests exercise the full
JSON-RPC pipeline without bridge subprocesses or mocks.
"""

import asyncio

from druids_server.lib.connection import AgentConnection


async def test_initialize_and_new_session(relay_stub):
    """Full ACP handshake: initialize + session/new returns a session ID."""
    bridge_id, bridge_token = relay_stub
    conn = AgentConnection(bridge_id, bridge_token)
    try:
        await conn.start()
        session_id = await conn.new_session()
        assert session_id == "stub-session-1"
        assert conn.session_id == "stub-session-1"
    finally:
        await conn.close()


async def test_prompt_and_response(relay_stub):
    """Send a prompt, verify the stub returns end_turn."""
    bridge_id, bridge_token = relay_stub
    conn = AgentConnection(bridge_id, bridge_token)
    try:
        await conn.start()
        await conn.new_session()
        result = await asyncio.wait_for(conn.prompt("Hello, agent!"), timeout=5)
        assert result["stopReason"] == "end_turn"
    finally:
        await conn.close()


async def test_session_update_notifications(relay_stub):
    """Verify agent_message_chunk notifications arrive via the handler."""
    events = []

    async def on_update(params):
        events.append(params)

    bridge_id, bridge_token = relay_stub
    conn = AgentConnection(bridge_id, bridge_token)
    try:
        conn.on("session/update", on_update)
        await conn.start()
        await conn.new_session()
        await asyncio.wait_for(conn.prompt("test"), timeout=5)
        await asyncio.sleep(0.2)

        update_types = [e["update"]["sessionUpdate"] for e in events]
        assert "agent_message_chunk" in update_types

        chunk = next(e for e in events if e["update"]["sessionUpdate"] == "agent_message_chunk")
        assert chunk["update"]["content"]["type"] == "text"
        assert "Response to prompt" in chunk["update"]["content"]["text"]
    finally:
        await conn.close()


async def test_tool_call_notifications(relay_stub):
    """Verify tool_call and tool_call_update notifications are dispatched."""
    events = []

    async def on_update(params):
        events.append(params)

    bridge_id, bridge_token = relay_stub
    conn = AgentConnection(bridge_id, bridge_token)
    try:
        conn.on("session/update", on_update)
        await conn.start()
        await conn.new_session()
        await asyncio.wait_for(conn.prompt("test"), timeout=5)
        await asyncio.sleep(0.2)

        update_types = [e["update"]["sessionUpdate"] for e in events]
        assert "tool_call" in update_types
        assert "tool_call_update" in update_types

        tool_call = next(e for e in events if e["update"]["sessionUpdate"] == "tool_call")
        assert tool_call["update"]["title"] == "echo"
        assert tool_call["update"]["toolCallId"] == "tool-1"
        assert tool_call["update"]["rawInput"] == {"message": "hello"}

        tool_update = next(e for e in events if e["update"]["sessionUpdate"] == "tool_call_update")
        assert tool_update["update"]["status"] == "completed"
        assert tool_update["update"]["rawOutput"] == {"result": "hello"}
    finally:
        await conn.close()


async def test_multiple_prompts(relay_stub):
    """Two prompts on the same session both succeed."""
    bridge_id, bridge_token = relay_stub
    conn = AgentConnection(bridge_id, bridge_token)
    try:
        await conn.start()
        await conn.new_session()

        r1 = await asyncio.wait_for(conn.prompt("First"), timeout=5)
        assert r1["stopReason"] == "end_turn"

        r2 = await asyncio.wait_for(conn.prompt("Second"), timeout=5)
        assert r2["stopReason"] == "end_turn"
    finally:
        await conn.close()
