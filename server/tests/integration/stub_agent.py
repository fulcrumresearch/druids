#!/usr/bin/env python3
"""Stub ACP agent for integration tests.

Reads NDJSON from stdin, writes NDJSON to stdout. Implements the minimum
ACP protocol surface to exercise the real bridge and AgentConnection.

No dependencies beyond stdlib.
"""

import json
import sys


SESSION_ID = "stub-session-1"
PROMPT_COUNT = 0


def respond(request_id, result):
    """Send a JSON-RPC 2.0 response."""
    msg = {"jsonrpc": "2.0", "id": request_id, "result": result}
    sys.stdout.write(json.dumps(msg, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def notify(method, params):
    """Send a JSON-RPC 2.0 notification (no id)."""
    msg = {"jsonrpc": "2.0", "method": method, "params": params}
    sys.stdout.write(json.dumps(msg, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def handle_initialize(request_id, params):
    respond(request_id, {"protocolVersion": 1, "capabilities": {}})


def handle_session_new(request_id, params):
    respond(request_id, {"sessionId": SESSION_ID})


def handle_session_prompt(request_id, params):
    global PROMPT_COUNT
    PROMPT_COUNT += 1
    session_id = params.get("sessionId", SESSION_ID)

    # agent_message_chunk notification
    notify(
        "session/update",
        {
            "sessionId": session_id,
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": f"Response to prompt {PROMPT_COUNT}"},
            },
        },
    )

    # tool_call notification
    tool_call_id = f"tool-{PROMPT_COUNT}"
    notify(
        "session/update",
        {
            "sessionId": session_id,
            "update": {
                "sessionUpdate": "tool_call",
                "title": "echo",
                "toolCallId": tool_call_id,
                "rawInput": {"message": "hello"},
            },
        },
    )

    # tool_call_update (completed) notification
    notify(
        "session/update",
        {
            "sessionId": session_id,
            "update": {
                "sessionUpdate": "tool_call_update",
                "toolCallId": tool_call_id,
                "status": "completed",
                "rawOutput": {"result": "hello"},
            },
        },
    )

    # Response with end_turn
    respond(request_id, {"stopReason": "end_turn"})


HANDLERS = {
    "initialize": handle_initialize,
    "session/new": handle_session_new,
    "session/prompt": handle_session_prompt,
}


def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method")
        request_id = msg.get("id")
        params = msg.get("params", {})

        # No method means it's a response to something we sent (shouldn't happen)
        if method is None:
            continue

        # No id means it's a notification -- silently consume
        if request_id is None:
            continue

        handler = HANDLERS.get(method)
        if handler:
            handler(request_id, params)
        else:
            # Unknown method -- return null result
            respond(request_id, None)


if __name__ == "__main__":
    main()
