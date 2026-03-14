"""
ACP Bridge - message-aware subprocess bridge for ACP agents.

Runs on Morph VMs. Starts an ACP-compatible agent as a subprocess
and relays stdin/stdout over HTTP long-poll to the server.

The bridge understands JSON-RPC messages. It queues session/prompt
requests and feeds them to the agent one at a time, handles cancel
directly, detects hung agents, and synthesizes error responses when
the agent is unresponsive or dead.

Usage:
    python bridge.py --port 8001
"""

import argparse
import asyncio
import json
import logging
import subprocess
import time
from dataclasses import dataclass, field

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("bridge")

app = FastAPI()

# Set by CLI args.
_auth_token: str | None = None
_liveness_timeout: float = 300.0


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
    relay_url: str | None = None
    bridge_id: str | None = None
    bridge_token: str | None = None


# ---------------------------------------------------------------------------
# Prompt queue
# ---------------------------------------------------------------------------


def _error_response(request_id: int | str, message: str) -> str:
    """Build a JSON-RPC error response line (NDJSON)."""
    resp = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32000, "message": message},
    }
    return json.dumps(resp, separators=(",", ":")) + "\n"


@dataclass
class PromptQueue:
    """Manages queued session/prompt requests for sequential processing."""

    _items: asyncio.Queue[dict] = field(default_factory=asyncio.Queue)
    processing_id: int | str | None = None
    cancel_requested: bool = False
    last_activity: float = field(default_factory=time.monotonic)

    def add(self, msg: dict) -> None:
        """Queue a session/prompt request."""
        self._items.put_nowait(msg)
        logger.info("Prompt queued: id=%s, depth=%d", msg.get("id"), self.depth)

    async def get(self) -> dict:
        """Block until a prompt is available, pop and start processing it."""
        item = await self._items.get()
        self.processing_id = item.get("id")
        self.cancel_requested = False
        self.last_activity = time.monotonic()
        logger.info("Prompt started: id=%s, remaining=%d", self.processing_id, self.depth)
        return item

    def finish(self) -> None:
        """Mark the current prompt as finished."""
        logger.info("Prompt finished: id=%s", self.processing_id)
        self.processing_id = None
        self.cancel_requested = False

    def drain_errors(self, reason: str) -> list[str]:
        """Drain all in-flight and queued prompts, returning error response lines.

        Called when the agent dies or is declared unresponsive.
        """
        errors = []
        if self.processing_id is not None:
            errors.append(_error_response(self.processing_id, reason))
            self.processing_id = None
        while True:
            try:
                msg = self._items.get_nowait()
                request_id = msg.get("id")
                if request_id is not None:
                    errors.append(_error_response(request_id, reason))
            except asyncio.QueueEmpty:
                break
        self.cancel_requested = False
        if errors:
            logger.info("Drained %d prompt(s) with error: %s", len(errors), reason)
        return errors

    @property
    def depth(self) -> int:
        """Number of prompts waiting (not including the in-flight one)."""
        return self._items.qsize()


# ---------------------------------------------------------------------------
# Agent process
# ---------------------------------------------------------------------------


@dataclass
class AgentProcess:
    proc: asyncio.subprocess.Process
    stdin_queue: asyncio.Queue  # raw messages from relay
    prompt_queue: PromptQueue = field(default_factory=PromptQueue)
    prompt_done: asyncio.Event = field(default_factory=asyncio.Event)

    # Tasks
    read_task: asyncio.Task | None = None
    router_task: asyncio.Task | None = None
    turn_task: asyncio.Task | None = None
    liveness_task: asyncio.Task | None = None
    stderr_task: asyncio.Task | None = None
    exit_task: asyncio.Task | None = None

    # Stats
    stdout_lines: int = 0
    stdin_lines: int = 0
    last_stdout_time: float = field(default_factory=time.monotonic)
    last_stdin_time: float = field(default_factory=time.monotonic)


# Single agent per bridge instance
agent: AgentProcess | None = None

# Output buffer and notification events
output_buffer: list[str] = []
output_event: asyncio.Event = asyncio.Event()
stdout_done: asyncio.Event = asyncio.Event()

relay_task: asyncio.Task | None = None

RELAY_PULL_TIMEOUT_SECONDS = 20.0
RELAY_MAX_INPUT_BATCH = 256
RELAY_PUSH_BATCH_SIZE = 256


# ---------------------------------------------------------------------------
# Core bridge: message-aware stdin/stdout relay
# ---------------------------------------------------------------------------


def _push_output(line: str) -> None:
    """Append a line to the output buffer and wake the relay pusher."""
    output_buffer.append(line)
    output_event.set()


async def read_stdout(proc: asyncio.subprocess.Process):
    """Read lines from agent stdout, track prompt completion, pass to output buffer.

    Every line is forwarded to the output buffer (and from there to the server
    via the relay). Additionally, if the line is a JSON-RPC response whose id
    matches the in-flight prompt, the prompt_done event is set so the turn
    processor knows to move on.
    """
    while True:
        line = await proc.stdout.readline()
        if not line:
            break

        decoded = line.decode()
        if agent:
            agent.stdout_lines += 1
            agent.last_stdout_time = time.monotonic()
            agent.prompt_queue.last_activity = time.monotonic()

        # Check if this is the response to the in-flight prompt
        if agent and agent.prompt_queue.processing_id is not None:
            try:
                msg = json.loads(decoded)
                # A response has 'id' and no 'method'
                if "id" in msg and "method" not in msg:
                    if msg["id"] == agent.prompt_queue.processing_id:
                        agent.prompt_done.set()
            except (json.JSONDecodeError, KeyError):
                pass

        _push_output(decoded)

    logger.info("read_stdout: pipe closed after %d lines", agent.stdout_lines if agent else -1)

    # Agent died. Flush any in-flight/queued prompts as errors.
    if agent:
        errors = agent.prompt_queue.drain_errors("agent process exited")
        for err in errors:
            _push_output(err)
        agent.prompt_done.set()  # unblock turn processor

    stdout_done.set()
    output_event.set()  # wake push_stdout so it sees stdout_done


async def read_stderr(proc: asyncio.subprocess.Process):
    """Drain stderr so the pipe buffer never fills (which would block the process)."""
    while True:
        line = await proc.stderr.readline()
        if not line:
            break
        logger.warning("agent stderr: %s", line.decode().rstrip())
    logger.info("read_stderr: pipe closed")


async def _write_to_stdin(proc: asyncio.subprocess.Process, data: str) -> bool:
    """Write a message to the agent's stdin. Returns False on pipe error."""
    if agent:
        agent.stdin_lines += 1
        agent.last_stdin_time = time.monotonic()
    try:
        proc.stdin.write(data.encode() if isinstance(data, str) else data)
        await proc.stdin.drain()
        return True
    except (BrokenPipeError, ConnectionResetError, OSError) as exc:
        logger.warning("write_stdin: pipe error: %s", exc)
        return False


async def route_incoming(proc: asyncio.subprocess.Process, queue: asyncio.Queue):
    """Read messages from the relay and route them.

    session/prompt requests are queued for sequential processing by the
    turn processor. session/cancel notifications are handled directly.
    Everything else (initialize, session/new, session/set_model, etc.)
    passes through to the agent immediately.
    """
    while True:
        raw = await queue.get()

        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            # Not valid JSON -- pass through unchanged
            await _write_to_stdin(proc, raw)
            continue

        method = parsed.get("method")
        has_id = "id" in parsed

        if method == "session/prompt" and has_id:
            # Queue for sequential processing
            if agent:
                agent.prompt_queue.add(parsed)
        elif method == "session/cancel":
            # Handle cancel: set flag and forward to agent
            if agent:
                agent.prompt_queue.cancel_requested = True
                logger.info("Cancel requested for prompt id=%s", agent.prompt_queue.processing_id)
            await _write_to_stdin(proc, raw)
        else:
            # Pass through (initialize, session/new, session/set_model, etc.)
            await _write_to_stdin(proc, raw)


async def process_turns(proc: asyncio.subprocess.Process):
    """Process queued prompts one at a time.

    Pops a prompt from the queue, forwards it to the agent via stdin,
    waits for the agent to respond (signaled by read_stdout), then
    moves on to the next prompt.
    """
    while True:
        if not agent:
            return

        try:
            item = await agent.prompt_queue.get()
        except asyncio.CancelledError:
            return

        agent.prompt_done.clear()

        # Forward the prompt to the agent
        raw = json.dumps(item, separators=(",", ":")) + "\n"
        ok = await _write_to_stdin(proc, raw)
        if not ok:
            # Agent stdin is broken -- drain everything with errors
            errors = agent.prompt_queue.drain_errors("agent stdin pipe broken")
            for err in errors:
                _push_output(err)
            return

        # Wait for the agent to respond (read_stdout sets prompt_done
        # when it sees a response matching the in-flight prompt id)
        try:
            await agent.prompt_done.wait()
        except asyncio.CancelledError:
            return

        agent.prompt_queue.finish()


async def monitor_liveness():
    """Detect hung agents and synthesize error responses.

    Checks every 5 seconds whether a prompt is in-flight and the agent
    has gone silent (no stdout) for longer than the liveness timeout.
    """
    while True:
        await asyncio.sleep(5)

        if not agent or agent.prompt_queue.processing_id is None:
            continue

        elapsed = time.monotonic() - agent.prompt_queue.last_activity
        if elapsed < _liveness_timeout:
            continue

        logger.warning(
            "Agent unresponsive: prompt id=%s, no stdout for %.0fs (timeout=%.0fs)",
            agent.prompt_queue.processing_id, elapsed, _liveness_timeout,
        )

        # Synthesize errors for all in-flight and queued prompts
        errors = agent.prompt_queue.drain_errors(
            f"agent unresponsive (no output for {int(elapsed)}s)"
        )
        for err in errors:
            _push_output(err)

        agent.prompt_done.set()  # unblock turn processor

        # Kill the agent process
        try:
            agent.proc.terminate()
        except ProcessLookupError:
            pass


async def watch_exit(proc: asyncio.subprocess.Process):
    """Wait for the agent process to exit and log the result."""
    returncode = await proc.wait()
    logger.info("Agent process exited with code %d", returncode)


# ---------------------------------------------------------------------------
# Reverse relay (transport layer -- unchanged)
# ---------------------------------------------------------------------------


async def run_reverse_relay(relay_url: str, bridge_id: str, bridge_token: str):
    """Push stdout to server and pull stdin from server over long-poll HTTP.

    Push and pull run as concurrent tasks so that stdout delivery is not
    blocked by the pull long-poll. When the agent exits, remaining buffered
    output is flushed before returning.
    """
    headers = {"Authorization": f"Bearer {bridge_token}"}
    push_url = f"{relay_url.rstrip('/')}/api/bridge/{bridge_id}/push"
    pull_url = f"{relay_url.rstrip('/')}/api/bridge/{bridge_id}/pull"
    sent_cursor = 0

    async def push_stdout(client: httpx.AsyncClient):
        nonlocal sent_cursor
        while True:
            try:
                # Push everything available.
                while sent_cursor < len(output_buffer):
                    chunk = output_buffer[sent_cursor : sent_cursor + RELAY_PUSH_BATCH_SIZE]
                    resp = await client.post(push_url, json={"messages": chunk}, headers=headers, timeout=30)
                    resp.raise_for_status()
                    sent_cursor += len(chunk)

                # Buffer is drained. If stdout is closed, we are done.
                if stdout_done.is_set():
                    return

                # Wait for new output. Clear the event first, then re-check
                # for data that arrived between the len() check and clear().
                output_event.clear()
                if sent_cursor < len(output_buffer):
                    continue
                try:
                    await asyncio.wait_for(output_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("Relay push error: %s", exc)
                await asyncio.sleep(1)

    async def pull_stdin(client: httpx.AsyncClient):
        while True:
            try:
                resp = await client.post(
                    pull_url,
                    json={"max_items": RELAY_MAX_INPUT_BATCH, "timeout_seconds": RELAY_PULL_TIMEOUT_SECONDS},
                    headers=headers,
                    timeout=RELAY_PULL_TIMEOUT_SECONDS + 10,
                )
                resp.raise_for_status()
                for message in resp.json().get("messages", []):
                    if agent:
                        await agent.stdin_queue.put(message)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("Relay pull error: %s", exc)
                await asyncio.sleep(1)

    async with httpx.AsyncClient() as client:
        push_task = asyncio.create_task(push_stdout(client))
        pull_task = asyncio.create_task(pull_stdin(client))

        try:
            await push_task
        except asyncio.CancelledError:
            push_task.cancel()
            await asyncio.gather(push_task, return_exceptions=True)
        finally:
            pull_task.cancel()
            await asyncio.gather(pull_task, return_exceptions=True)
            logger.info("Relay: stopped, pushed %d lines total", sent_cursor)


# ---------------------------------------------------------------------------
# FastAPI endpoints
# ---------------------------------------------------------------------------


@app.post("/start")
async def start_agent(req: StartRequest):
    """Start the ACP agent subprocess."""
    global agent, output_buffer, output_event, stdout_done, relay_task

    if agent is not None:
        return {"error": "Agent already running", "status": "error"}

    # Reset output state
    output_buffer = []
    output_event = asyncio.Event()
    stdout_done = asyncio.Event()

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

    # Start background tasks
    agent.read_task = asyncio.create_task(read_stdout(proc))
    agent.router_task = asyncio.create_task(route_incoming(proc, stdin_queue))
    agent.turn_task = asyncio.create_task(process_turns(proc))
    agent.liveness_task = asyncio.create_task(monitor_liveness())
    agent.stderr_task = asyncio.create_task(read_stderr(proc))
    agent.exit_task = asyncio.create_task(watch_exit(proc))
    if req.relay_url and req.bridge_id and req.bridge_token:
        relay_task = asyncio.create_task(run_reverse_relay(req.relay_url, req.bridge_id, req.bridge_token))

    return {"status": "started", "pid": proc.pid}


async def _stop_task(task: asyncio.Task, timeout: float = 0) -> None:
    """Cancel a task. If timeout > 0, wait that long for it to finish first."""
    if timeout > 0:
        try:
            await asyncio.wait_for(task, timeout=timeout)
            return
        except (asyncio.CancelledError, Exception):
            pass
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


@app.post("/stop")
async def stop_agent():
    """Stop the ACP agent subprocess.

    Order: terminate the process, wait for read_stdout to drain the pipe
    and synthesize error responses for pending prompts, let the relay push
    whatever remains, then cancel the queue processing tasks.
    """
    global agent, relay_task

    if agent is None:
        return {"error": "No agent running", "status": "error"}

    # 1. Terminate the process. This closes the stdout pipe.
    try:
        agent.proc.terminate()
    except ProcessLookupError:
        pass

    # 2. Wait for read_stdout to drain the pipe. After this, output_buffer
    #    is complete (including synthesized error responses) and stdout_done is set.
    if agent.read_task:
        await _stop_task(agent.read_task, timeout=5.0)

    # 3. Let the relay drain remaining output.
    if relay_task:
        await _stop_task(relay_task, timeout=10.0)
        relay_task = None

    # 4. Cancel the queue processing tasks.
    for task in [agent.router_task, agent.turn_task, agent.liveness_task]:
        if task:
            await _stop_task(task)

    await agent.proc.wait()
    agent = None

    return {"status": "stopped"}


@app.get("/status")
async def get_status():
    """Get agent status including prompt queue state."""
    global agent

    if agent is None:
        return {"status": "not_running"}

    if agent.proc.returncode is not None:
        return {"status": "exited", "returncode": agent.proc.returncode}

    now = time.monotonic()
    pq = agent.prompt_queue
    relaying = relay_task is not None and not relay_task.done()

    result = {
        "status": "running",
        "pid": agent.proc.pid,
        "relaying": relaying,
        "stdout_lines": agent.stdout_lines,
        "stdin_lines": agent.stdin_lines,
        "seconds_since_stdout": round(now - agent.last_stdout_time, 1),
        "seconds_since_stdin": round(now - agent.last_stdin_time, 1),
        "queue_depth": pq.depth,
        "prompt_in_flight": pq.processing_id is not None,
    }

    if pq.processing_id is not None:
        result["seconds_since_prompt_activity"] = round(now - pq.last_activity, 1)

    return result


def main():
    global _auth_token, _liveness_timeout

    parser = argparse.ArgumentParser(description="ACP Bridge")
    parser.add_argument("--port", type=int, default=8001, help="Port to listen on")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--auth-token", default=None, help="Bearer token required for all requests (except /status)")
    parser.add_argument(
        "--liveness-timeout", type=float, default=300.0,
        help="Seconds of agent silence before declaring it unresponsive (default: 300)",
    )
    args = parser.parse_args()

    _auth_token = args.auth_token
    _liveness_timeout = args.liveness_timeout
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
