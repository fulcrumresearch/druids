"""Fixtures for integration tests.

Provides an in-process stub agent communicating through the relay hub.
No bridge subprocess, no Morph VMs, no API keys.

- relay_stub (function): runs a stub ACP agent in-process via the relay hub, yields (bridge_id, bridge_token)
"""

import asyncio
import json
from uuid import uuid4

import pytest
from druids_server.lib.connection import bridge_relay_hub


async def _stub_agent_loop(bridge_id: str) -> None:
    """Run stub ACP agent logic in-process via the relay hub.

    Reads JSON-RPC requests from the relay outgoing queue (server -> bridge)
    and writes responses to the incoming queue (bridge -> server). Implements
    the same protocol surface as stub_agent.py.
    """
    prompt_count = 0
    while True:
        try:
            items = await bridge_relay_hub.pull_input(bridge_id, max_items=10, timeout_seconds=0.5)
        except (ConnectionError, asyncio.CancelledError):
            return

        for item in items:
            for line in item.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                msg = json.loads(line)
                method = msg.get("method")
                request_id = msg.get("id")
                params = msg.get("params", {})

                if request_id is None:
                    continue

                responses: list[dict] = []
                if method == "initialize":
                    responses.append(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": {"protocolVersion": 1, "capabilities": {}},
                        }
                    )
                elif method == "session/new":
                    responses.append(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": {"sessionId": "stub-session-1"},
                        }
                    )
                elif method == "session/prompt":
                    prompt_count += 1
                    session_id = params.get("sessionId", "stub-session-1")
                    tool_call_id = f"tool-{prompt_count}"
                    responses.extend(
                        [
                            {
                                "jsonrpc": "2.0",
                                "method": "session/update",
                                "params": {
                                    "sessionId": session_id,
                                    "update": {
                                        "sessionUpdate": "agent_message_chunk",
                                        "content": {
                                            "type": "text",
                                            "text": f"Response to prompt {prompt_count}",
                                        },
                                    },
                                },
                            },
                            {
                                "jsonrpc": "2.0",
                                "method": "session/update",
                                "params": {
                                    "sessionId": session_id,
                                    "update": {
                                        "sessionUpdate": "tool_call",
                                        "title": "echo",
                                        "toolCallId": tool_call_id,
                                        "rawInput": {"message": "hello"},
                                    },
                                },
                            },
                            {
                                "jsonrpc": "2.0",
                                "method": "session/update",
                                "params": {
                                    "sessionId": session_id,
                                    "update": {
                                        "sessionUpdate": "tool_call_update",
                                        "toolCallId": tool_call_id,
                                        "status": "completed",
                                        "rawOutput": {"result": "hello"},
                                    },
                                },
                            },
                            {
                                "jsonrpc": "2.0",
                                "id": request_id,
                                "result": {"stopReason": "end_turn"},
                            },
                        ]
                    )
                else:
                    responses.append({"jsonrpc": "2.0", "id": request_id, "result": None})

                await bridge_relay_hub.push_output(bridge_id, [json.dumps(r, separators=(",", ":")) for r in responses])


@pytest.fixture
async def relay_stub():
    """Start an in-process stub agent communicating through the relay hub.

    Yields (bridge_id, bridge_token). The stub processes ACP requests
    (initialize, session/new, session/prompt) and sends back the same
    responses as stub_agent.py.
    """
    bridge_id = f"test-bridge-{uuid4().hex[:8]}"
    bridge_token = "test-token"

    async def _run():
        # Wait for AgentConnection.start() to register this bridge_id in the hub.
        for _ in range(500):
            if bridge_id in bridge_relay_hub._sessions:
                break
            await asyncio.sleep(0.01)
        else:
            return
        await bridge_relay_hub.mark_connected(bridge_id)
        await _stub_agent_loop(bridge_id)

    task = asyncio.create_task(_run())
    yield bridge_id, bridge_token
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await bridge_relay_hub.unregister(bridge_id)
