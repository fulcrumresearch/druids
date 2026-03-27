#!/usr/bin/env python3
"""Stub ACP agent that floods output lines on prompt.

Used by stress tests to verify that the relay delivers every line in
order with no gaps or duplicates. Produces FLOOD_COUNT agent_message_chunk
notifications, each containing "line {i}" optionally padded to PAD_BYTES,
followed by an end_turn response.

Environment variables:
  FLOOD_COUNT       Number of notifications to produce (default 1000).
  PAD_BYTES         Pad each notification's text to this many bytes with
                    base64-like filler. Simulates large payloads like
                    screenshots. Default 0 (no padding).
  EXIT_AFTER_FLOOD  If "1", exit after the first prompt.
"""

import json
import os
import sys
import time


SESSION_ID = "stub-session-1"
FLOOD_COUNT = int(os.environ.get("FLOOD_COUNT", "1000"))
PAD_BYTES = int(os.environ.get("PAD_BYTES", "0"))
EXIT_AFTER_FLOOD = os.environ.get("EXIT_AFTER_FLOOD", "0") == "1"


def respond(request_id, result):
    msg = {"jsonrpc": "2.0", "id": request_id, "result": result}
    sys.stdout.write(json.dumps(msg, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def notify(method, params):
    msg = {"jsonrpc": "2.0", "method": method, "params": params}
    sys.stdout.write(json.dumps(msg, separators=(",", ":")) + "\n")


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

            # Flood numbered notifications without flushing each one.
            for i in range(FLOOD_COUNT):
                text = f"line {i}"
                if PAD_BYTES > 0:
                    # Pad with repeating 'A' to simulate base64 image data.
                    padding_needed = max(0, PAD_BYTES - len(text))
                    text = text + "A" * padding_needed
                notify(
                    "session/update",
                    {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": text},
                        },
                    },
                )

            # Flush the entire batch at once.
            sys.stdout.flush()

            respond(request_id, {"stopReason": "end_turn"})

            if EXIT_AFTER_FLOOD:
                time.sleep(0.05)
                sys.exit(0)
        else:
            respond(request_id, None)


if __name__ == "__main__":
    main()
