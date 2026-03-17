"""
ACP Bridge - HTTP-based subprocess bridge for ACP agents.

Runs on Morph VMs. Starts an ACP-compatible agent as a subprocess
and relays stdin/stdout over HTTP long-poll to the server.

Usage:
    python bridge.py --port 8001
"""

import argparse
import asyncio
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
    relay_url: str | None = None
    bridge_id: str | None = None
    bridge_token: str | None = None


@dataclass
class AgentProcess:
    proc: asyncio.subprocess.Process
    stdin_queue: asyncio.Queue
    read_task: asyncio.Task | None = None
    write_task: asyncio.Task | None = None
    stderr_task: asyncio.Task | None = None
    exit_task: asyncio.Task | None = None
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
# Core bridge: stdin/stdout relay
# ---------------------------------------------------------------------------


async def read_stdout(proc: asyncio.subprocess.Process):
    """Read lines from process stdout and append to output buffer."""
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        if agent:
            agent.stdout_lines += 1
            agent.last_stdout_time = time.monotonic()
        output_buffer.append(line.decode())
        output_event.set()
    logger.info("read_stdout: pipe closed after %d lines", agent.stdout_lines if agent else -1)
    stdout_done.set()
    output_event.set()  # wake push_stdout so it sees stdout_done immediately


async def read_stderr(proc: asyncio.subprocess.Process):
    """Drain stderr so the pipe buffer never fills (which would block the process)."""
    while True:
        line = await proc.stderr.readline()
        if not line:
            break
        logger.warning("agent stderr: %s", line.decode().rstrip())
    logger.info("read_stderr: pipe closed")


async def write_stdin(proc: asyncio.subprocess.Process, queue: asyncio.Queue):
    """Read messages from queue and write to process stdin."""
    while True:
        msg = await queue.get()
        if agent:
            agent.stdin_lines += 1
            agent.last_stdin_time = time.monotonic()
        try:
            proc.stdin.write(msg.encode() if isinstance(msg, str) else msg)
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            logger.warning("write_stdin: pipe error after %d msgs: %s", agent.stdin_lines if agent else -1, exc)
            return


async def watch_exit(proc: asyncio.subprocess.Process):
    """Wait for the agent process to exit and log the result."""
    returncode = await proc.wait()
    logger.info("Agent process exited with code %d", returncode)


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
            # Wait for push_stdout to drain everything and return. It checks
            # stdout_done.is_set() after each drain cycle: once the pipe is
            # closed AND the buffer is empty, it exits on its own. This means
            # push_stdout owns the full lifecycle of pushing -- no separate
            # flush path, no cancellation race, and its existing retry logic
            # covers transient errors during the final drain.
            await push_task
        except asyncio.CancelledError:
            push_task.cancel()
            await asyncio.gather(push_task, return_exceptions=True)
        finally:
            pull_task.cancel()
            await asyncio.gather(pull_task, return_exceptions=True)
            logger.info("Relay: stopped, pushed %d lines total", sent_cursor)


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

    # Start background tasks for reading/writing
    agent.read_task = asyncio.create_task(read_stdout(proc))
    agent.write_task = asyncio.create_task(write_stdin(proc, stdin_queue))
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

    Order matters: terminate the process, wait for read_stdout to drain
    the pipe, let the relay push whatever remains, then cancel write_stdin.
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
    #    is complete and stdout_done is set.
    if agent.read_task:
        await _stop_task(agent.read_task, timeout=5.0)

    # 3. Let the relay drain remaining output. stdout_done is already set,
    #    so push_stdout will finish after pushing whatever is left.
    if relay_task:
        await _stop_task(relay_task, timeout=10.0)
        relay_task = None

    # 4. Cancel write_stdin -- it may be blocked on the dead queue.
    if agent.write_task:
        await _stop_task(agent.write_task)

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

    now = time.monotonic()
    relaying = relay_task is not None and not relay_task.done()
    return {
        "status": "running",
        "pid": agent.proc.pid,
        "relaying": relaying,
        "stdout_lines": agent.stdout_lines,
        "stdin_lines": agent.stdin_lines,
        "seconds_since_stdout": round(now - agent.last_stdout_time, 1),
        "seconds_since_stdin": round(now - agent.last_stdin_time, 1),
    }


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
