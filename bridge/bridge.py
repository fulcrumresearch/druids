"""
ACP Bridge - HTTP-based subprocess bridge for ACP agents.

Runs on Morph VMs. Starts an ACP-compatible agent as a subprocess
and exposes it via HTTP: POST /input for stdin, GET /output for
stdout via SSE (Server-Sent Events).

Optional monitor: when monitor_prompt is provided in /start, a
lightweight loop observes the agent's activity and can nudge
the agent by injecting messages into its ACP session.

Usage:
    python bridge.py --port 8001
"""

import argparse
import asyncio
import json
import logging
import subprocess
import time
from dataclasses import dataclass

import anthropic
import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("bridge")

app = FastAPI()

# Set by --auth-token CLI arg. When set, all endpoints except /status
# require Authorization: Bearer <token>.
_auth_token: str | None = None


@app.middleware("http")
async def check_auth(request: Request, call_next):
    if _auth_token and request.url.path != "/status":
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {_auth_token}":
            raise HTTPException(status_code=401, detail="Unauthorized")
    return await call_next(request)


class StartRequest(BaseModel):
    command: str  # e.g., "claude-code-acp"
    args: list[str] = []
    env: dict[str, str] = {}
    working_directory: str | None = None
    monitor_prompt: str | None = None
    relay_url: str
    bridge_id: str
    bridge_token: str


class InputRequest(BaseModel):
    data: str


@dataclass
class AgentProcess:
    proc: asyncio.subprocess.Process
    stdin_queue: asyncio.Queue


# Single agent per bridge instance
agent: AgentProcess | None = None

# Output buffer and notification event
output_buffer: list[str] = []
output_event: asyncio.Event = asyncio.Event()

# Monitor state
monitor_task: asyncio.Task | None = None
relay_task: asyncio.Task | None = None

RELAY_PULL_TIMEOUT_SECONDS = 20.0
RELAY_MAX_INPUT_BATCH = 256
RELAY_PUSH_BATCH_SIZE = 256


# ---------------------------------------------------------------------------
# ACP event parsing -- extract human-readable activity from JSON-RPC stdout
# ---------------------------------------------------------------------------


def parse_acp_event(line: str) -> dict | None:
    """Parse a JSON-RPC line from ACP stdout into a readable event.

    Returns a dict with type/summary, or None if the line is not interesting.
    """
    try:
        msg = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None

    # Notifications have method + params
    method = msg.get("method")
    params = msg.get("params", {})

    if method == "session/update":
        update = params.get("update", {})
        session_update = update.get("sessionUpdate")

        if session_update == "agent_message_chunk":
            content = update.get("content", {})
            if content.get("type") == "text":
                text = content.get("text", "")
                if text:
                    return {"type": "text", "text": text}

        elif session_update == "tool_call":
            title = update.get("title", "unknown")
            raw_input = update.get("rawInput", {})
            input_str = str(raw_input) if raw_input else ""
            return {"type": "tool_call", "tool": title, "input": input_str}

        elif session_update == "tool_call_update":
            status = update.get("status")
            if status == "completed":
                title = update.get("title", "unknown")
                raw_output = update.get("rawOutput", "")
                output_str = str(raw_output) if raw_output else ""
                return {"type": "tool_result", "tool": title, "output": output_str}

    return None


# ---------------------------------------------------------------------------
# Monitor -- lightweight observer that watches agent activity
# ---------------------------------------------------------------------------

MONITOR_CHECK_INTERVAL = 45  # seconds between monitor checks
MONITOR_MODEL = "claude-haiku-4-5"

# The nudge is a JSON-RPC prompt request injected into the agent's ACP stdin.
# It uses session ID "monitor" which the ACP adapter treats as a new prompt
# in the existing session.


def build_nudge_jsonrpc(session_id: str, message: str) -> str:
    """Build a JSON-RPC prompt request to nudge the agent."""
    request = {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000),
        "method": "session/prompt",
        "params": {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": f"[Monitor] {message}"}],
        },
    }
    return json.dumps(request) + "\n"


async def run_monitor(prompt: str, api_key: str, base_url: str | None = None):
    """Background loop: observe agent activity, call the monitor model, maybe nudge."""
    client = (
        anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url)
        if base_url
        else anthropic.AsyncAnthropic(api_key=api_key)
    )
    activity_log: list[dict] = []
    last_check_index = 0
    session_id = None

    # Wait for agent to start producing output
    await asyncio.sleep(30)

    tools = [
        {
            "name": "nudge",
            "description": (
                "Send a message to the agent to correct its behavior. "
                "The agent will see this as a [Monitor] prefixed message. "
                "Use this when the agent is going off track: doing introspection "
                "instead of demoing, giving up too early, running tests before demoing, etc."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The corrective message to send to the agent.",
                    }
                },
                "required": ["message"],
            },
        },
    ]

    conversation = [
        {"role": "user", "content": prompt},
        {
            "role": "assistant",
            "content": "I'll monitor the agent's activity and intervene if needed. Waiting for initial activity...",
        },
    ]

    while True:
        await asyncio.sleep(MONITOR_CHECK_INTERVAL)

        if agent is None or agent.proc.returncode is not None:
            logger.info("Monitor: agent process ended, stopping monitor")
            break

        # Collect new events from stdout
        new_lines = output_buffer[last_check_index:]
        new_events = []
        for line in new_lines:
            event = parse_acp_event(line)
            if event:
                new_events.append(event)
                activity_log.append(event)
        last_check_index = len(output_buffer)

        logger.info(f"Monitor: buffer={len(output_buffer)} new_lines={len(new_lines)} parsed={len(new_events)}")

        # Extract session ID from the output buffer if we don't have it yet
        if not session_id:
            for line in output_buffer:
                try:
                    msg = json.loads(line)
                    result = msg.get("result", {})
                    if isinstance(result, dict) and "sessionId" in result:
                        session_id = result["sessionId"]
                        break
                except (json.JSONDecodeError, ValueError):
                    continue

        if not new_events:
            continue

        # Build activity summary for the monitor. No truncation -- the monitor
        # sees exactly what the agent sees. The only limit is the check interval.
        summary_lines = []
        for ev in new_events:
            if ev["type"] == "text":
                summary_lines.append(f"Agent said: {ev['text']}")
            elif ev["type"] == "tool_call":
                summary_lines.append(f"Agent called tool: {ev['tool']} | input: {ev['input']}")
            elif ev["type"] == "tool_result":
                summary_lines.append(f"Tool result ({ev['tool']}): {ev['output']}")
        activity_summary = "\n".join(summary_lines)

        conversation.append(
            {
                "role": "user",
                "content": f"New agent activity since last check:\n\n{activity_summary}\n\nEvaluate. If the agent is going off track, use the nudge tool. Otherwise just acknowledge briefly.",
            }
        )

        # Call the monitor model
        try:
            response = await client.messages.create(
                model=MONITOR_MODEL,
                max_tokens=1024,
                system="You are a review monitor running inside the bridge. You observe an AI agent's activity and intervene only when necessary. Be concise. Only nudge if you see a clear problem.",
                messages=conversation,
                tools=tools,
            )
        except Exception as e:
            logger.error(f"Monitor: API call failed: {e}")
            # Remove the last user message so we don't accumulate broken state
            conversation.pop()
            continue

        # Process response
        assistant_text = ""
        nudge_messages = []
        for block in response.content:
            if block.type == "text":
                assistant_text += block.text
            elif block.type == "tool_use" and block.name == "nudge":
                nudge_messages.append(block.input.get("message", ""))

        logger.info(f"Monitor: {assistant_text[:200]}")

        # Build assistant message for conversation history
        conversation.append({"role": "assistant", "content": response.content})

        # Execute nudges
        for nudge_msg in nudge_messages:
            if agent and session_id:
                logger.info(f"Monitor: nudging agent: {nudge_msg[:100]}")
                nudge_data = build_nudge_jsonrpc(session_id, nudge_msg)
                await agent.stdin_queue.put(nudge_data)

        # If there were tool uses, send tool results back
        if nudge_messages:
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": "Nudge sent to agent.",
                        }
                    )
            conversation.append({"role": "user", "content": tool_results})

            # Get follow-up response after tool use
            try:
                followup = await client.messages.create(
                    model=MONITOR_MODEL,
                    max_tokens=256,
                    system="You are a review monitor. Acknowledge the nudge briefly and continue watching.",
                    messages=conversation,
                    tools=tools,
                )
                conversation.append({"role": "assistant", "content": followup.content})
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Core bridge: stdin/stdout relay
# ---------------------------------------------------------------------------


async def read_stdout(proc: asyncio.subprocess.Process):
    """Read lines from process stdout and append to output buffer."""
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        output_buffer.append(line.decode())
        output_event.set()


async def write_stdin(proc: asyncio.subprocess.Process, queue: asyncio.Queue):
    """Read messages from queue and write to process stdin."""
    while True:
        msg = await queue.get()
        proc.stdin.write(msg.encode() if isinstance(msg, str) else msg)
        await proc.stdin.drain()


async def run_reverse_relay(relay_url: str, bridge_id: str, bridge_token: str):
    """Push stdout to server and pull stdin from server over long-poll HTTP."""
    headers = {"Authorization": f"Bearer {bridge_token}"}
    push_url = f"{relay_url.rstrip('/')}/api/bridge/{bridge_id}/push"
    pull_url = f"{relay_url.rstrip('/')}/api/bridge/{bridge_id}/pull"
    sent_cursor = 0

    async with httpx.AsyncClient() as client:
        while True:
            if agent is None or agent.proc.returncode is not None:
                logger.info("Relay: agent exited, stopping relay loop")
                return

            try:
                # Push any newly produced stdout lines.
                if sent_cursor < len(output_buffer):
                    end = len(output_buffer)
                    batch = output_buffer[sent_cursor:end]
                    offset = 0
                    while offset < len(batch):
                        chunk = batch[offset : offset + RELAY_PUSH_BATCH_SIZE]
                        resp = await client.post(push_url, json={"messages": chunk}, headers=headers, timeout=30)
                        resp.raise_for_status()
                        offset += len(chunk)
                    sent_cursor = end

                # Pull stdin messages with long-polling.
                resp = await client.post(
                    pull_url,
                    json={"max_items": RELAY_MAX_INPUT_BATCH, "timeout_seconds": RELAY_PULL_TIMEOUT_SECONDS},
                    headers=headers,
                    timeout=RELAY_PULL_TIMEOUT_SECONDS + 10,
                )
                resp.raise_for_status()
                payload = resp.json()
                messages = payload.get("messages", [])
                for message in messages:
                    await agent.stdin_queue.put(message)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("Relay loop error: %s", exc)
                await asyncio.sleep(1)


@app.post("/start")
async def start_agent(req: StartRequest):
    """Start the ACP agent subprocess."""
    global agent, output_buffer, output_event, monitor_task, relay_task

    if agent is not None:
        return {"error": "Agent already running", "status": "error"}

    # Reset output state
    output_buffer = []
    output_event = asyncio.Event()

    # Merge environment - Set IS_SANDBOX=1 to allow claude-code-acp to run as root
    env = {**dict(subprocess.os.environ), "IS_SANDBOX": "1", **req.env}

    # Start the subprocess
    # ACP uses newline-delimited JSON; tool results with large file contents
    # can produce lines well over the default 64KB asyncio buffer limit.
    proc = await asyncio.create_subprocess_exec(
        req.command,
        *req.args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=req.working_directory,
        limit=10 * 1024 * 1024,  # 10MB line buffer
    )

    stdin_queue = asyncio.Queue()
    agent = AgentProcess(proc=proc, stdin_queue=stdin_queue)

    # Start background tasks for reading/writing
    asyncio.create_task(read_stdout(proc))
    asyncio.create_task(write_stdin(proc, stdin_queue))
    relay_task = asyncio.create_task(run_reverse_relay(req.relay_url, req.bridge_id, req.bridge_token))

    # Start monitor if prompt provided
    if req.monitor_prompt:
        api_key = env.get("ANTHROPIC_API_KEY")
        base_url = env.get("ANTHROPIC_BASE_URL")
        if not api_key:
            logger.warning("monitor_prompt provided but no ANTHROPIC_API_KEY in env")
        else:
            logger.info("Starting monitor with Haiku")
            monitor_task = asyncio.create_task(run_monitor(req.monitor_prompt, api_key, base_url))

    return {"status": "started", "pid": proc.pid, "monitor": req.monitor_prompt is not None}


@app.post("/stop")
async def stop_agent():
    """Stop the ACP agent subprocess."""
    global agent, monitor_task, relay_task

    if agent is None:
        return {"error": "No agent running", "status": "error"}

    # Stop monitor first
    if monitor_task:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        monitor_task = None

    if relay_task:
        relay_task.cancel()
        try:
            await relay_task
        except asyncio.CancelledError:
            pass
        relay_task = None

    agent.proc.terminate()
    await agent.proc.wait()
    agent = None

    return {"status": "stopped"}


@app.get("/status")
async def get_status():
    """Get agent status."""
    global agent

    if agent is None:
        return {"status": "not_running"}

    if agent.proc.returncode is not None:
        return {"status": "exited", "returncode": agent.proc.returncode}

    monitoring = monitor_task is not None and not monitor_task.done()
    relaying = relay_task is not None and not relay_task.done()
    return {"status": "running", "pid": agent.proc.pid, "monitoring": monitoring, "relaying": relaying}


@app.post("/input")
async def post_input(req: InputRequest):
    """Write data to agent subprocess stdin. Fire-and-forget."""
    if agent is None:
        return {"error": "No agent running", "status": "error"}
    await agent.stdin_queue.put(req.data)
    return {"status": "ok"}


# Keepalive interval for SSE streams. Sends an SSE comment (`: keepalive`)
# so the reader can detect dead connections via read timeout.
KEEPALIVE_INTERVAL = 30


@app.get("/output")
async def get_output(request: Request):
    """SSE stream of agent subprocess stdout lines.

    Supports reconnection via Last-Event-ID header. Event IDs are
    1-indexed positions in the output buffer.
    """
    last_event_id = request.headers.get("last-event-id", "0")
    try:
        cursor = int(last_event_id)
    except ValueError:
        cursor = 0

    async def event_stream():
        nonlocal cursor
        while True:
            if cursor < len(output_buffer):
                end = len(output_buffer)
                for i in range(cursor, end):
                    yield f"id: {i + 1}\ndata: {output_buffer[i]}\n\n"
                cursor = end
            else:
                output_event.clear()
                # Re-check after clearing to close the race window: read_stdout
                # may have appended and set the event between our first check
                # and the clear.
                if cursor < len(output_buffer):
                    continue
                try:
                    await asyncio.wait_for(output_event.wait(), timeout=KEEPALIVE_INTERVAL)
                except asyncio.TimeoutError:
                    # No new data within the keepalive window. Send an SSE
                    # comment to keep the connection alive and let the reader
                    # know the stream is still healthy.
                    yield ": keepalive\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def main():
    global _auth_token

    parser = argparse.ArgumentParser(description="ACP Bridge")
    parser.add_argument("--port", type=int, default=8001, help="Port to listen on")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--auth-token", default=None, help="Bearer token required for all requests (except /status)")
    args = parser.parse_args()

    _auth_token = args.auth_token
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
