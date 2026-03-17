#!/usr/bin/env python3
"""Stub ACP agent that exits after handling one prompt.

Used by relay tests to verify that the relay flushes output on agent exit.
Handles initialize and session/new normally, then on session/prompt produces
a notification and response and exits.

No dependencies beyond stdlib.
"""

import json
import sys
import time


SESSION_ID = "stub-session-1"


def respond(request_id, result):
    msg = {"jsonrpc": "2.0", "id": request_id, "result": result}
    sys.stdout.write(json.dumps(msg, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def notify(method, params):
    msg = {"jsonrpc": "2.0", "method": method, "params": params}
    sys.stdout.write(json.dumps(msg, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
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

        if request_id is None:
            continue

        if method == "initialize":
            respond(request_id, {"protocolVersion": 1, "capabilities": {}})
        elif method == "session/new":
            respond(request_id, {"sessionId": SESSION_ID})
        elif method == "session/prompt":
            session_id = params.get("sessionId", SESSION_ID)
            notify(
                "session/update",
                {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "final message before exit"},
                    },
                },
            )
            respond(request_id, {"stopReason": "end_turn"})
            sys.stdout.flush()
            time.sleep(0.1)
            sys.exit(0)
        else:
            respond(request_id, None)


if __name__ == "__main__":
    main()
