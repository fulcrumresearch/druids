"""Tests for the bridge reverse relay.

These tests expose bugs in the relay implementation in bridge.py:

1. Serial push/pull blocks stdout delivery for up to RELAY_PULL_TIMEOUT_SECONDS.
2. Agent's final output is dropped when the relay detects process exit.

Each test uses a mock relay server (lightweight FastAPI app) running in a
background thread. The bridge pushes output to and pulls input from this mock,
letting us observe what gets delivered and when.
"""

import asyncio
import json
import queue as stdlib_queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from druids_server.paths import BRIDGE_DIR
from fastapi import FastAPI, Request


STUB_AGENT = str(Path(__file__).parent / "stub_agent.py")
STUB_AGENT_EXIT = str(Path(__file__).parent / "stub_agent_exit.py")

# How long the mock relay's pull handler blocks. Shorter than the bridge's
# RELAY_PULL_TIMEOUT_SECONDS (20) so tests run faster, but long enough to
# expose the serial push/pull latency bug.
MOCK_PULL_BLOCK_SECONDS = 3.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _jsonrpc(method: str, params: dict, request_id: int) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}) + "\n"


def _send(mock_relay, data: str):
    mock_relay.queue_pull_message(data)


def _send_handshake_and_prompt(mock_relay):
    """Send ACP initialize, session/new, and session/prompt via the relay pull queue."""
    _send(mock_relay, _jsonrpc("initialize", {}, 1))
    _send(mock_relay, _jsonrpc("session/new", {}, 2))
    _send(
        mock_relay,
        _jsonrpc(
            "session/prompt",
            {
                "sessionId": "stub-session-1",
                "prompt": [{"type": "text", "text": "hello"}],
            },
            3,
        ),
    )


def _wait_for_pushed(mock, predicate, timeout=10.0, poll=0.1):
    """Poll mock.get_pushed() until predicate(messages) is True or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pushed = mock.get_pushed()
        if predicate(pushed):
            return pushed
        time.sleep(poll)
    return mock.get_pushed()


def _has_end_turn(messages):
    return any('"end_turn"' in m for m in messages)


def _has_protocol_version(messages):
    return any('"protocolVersion"' in m for m in messages)


# ---------------------------------------------------------------------------
# Mock relay server
# ---------------------------------------------------------------------------


class MockRelay:
    """Lightweight relay that records pushed output and serves pull input.

    Runs as a FastAPI app in a background thread. The bridge pushes output
    to POST /api/bridge/{bridge_id}/push and pulls input from
    POST /api/bridge/{bridge_id}/pull.
    """

    def __init__(self, pull_block_seconds: float = MOCK_PULL_BLOCK_SECONDS):
        self._pushed: list[str] = []
        self._pushed_lock = threading.Lock()
        self._pull_queue: stdlib_queue.Queue = stdlib_queue.Queue()
        self.pull_block_seconds = pull_block_seconds
        self.pull_entered = threading.Event()
        self.push_count = 0
        self._generation = 0
        self.url: str = ""
        self.app = FastAPI()
        self._setup_routes()

    def _setup_routes(self):
        relay = self

        @relay.app.post("/api/bridge/{bridge_id}/push")
        async def push(bridge_id: str, request: Request):
            body = await request.json()
            messages = body.get("messages", [])
            with relay._pushed_lock:
                relay._pushed.extend(messages)
                relay.push_count += 1
            return {"status": "ok", "count": len(messages)}

        @relay.app.post("/api/bridge/{bridge_id}/pull")
        async def pull(bridge_id: str, request: Request):
            gen = relay._generation
            body = await request.json()
            timeout = min(body.get("timeout_seconds", 20.0), relay.pull_block_seconds)
            relay.pull_entered.set()
            deadline = time.monotonic() + timeout
            messages: list[str] = []
            while time.monotonic() < deadline:
                if relay._generation != gen:
                    return {"messages": []}
                try:
                    messages.append(relay._pull_queue.get_nowait())
                    # Got one, drain the rest without waiting.
                    while True:
                        try:
                            messages.append(relay._pull_queue.get_nowait())
                        except stdlib_queue.Empty:
                            break
                    return {"messages": messages}
                except stdlib_queue.Empty:
                    await asyncio.sleep(0.1)
            return {"messages": messages}

    def get_pushed(self) -> list[str]:
        with self._pushed_lock:
            return list(self._pushed)

    def queue_pull_message(self, msg: str):
        """Queue a message to be returned by the next pull request."""
        self._pull_queue.put(msg)

    def reset(self):
        self._generation += 1
        with self._pushed_lock:
            self._pushed.clear()
            self.push_count = 0
        self.pull_entered.clear()
        while not self._pull_queue.empty():
            try:
                self._pull_queue.get_nowait()
            except stdlib_queue.Empty:
                break


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_relay():
    """Start a mock relay server in a background thread."""
    relay = MockRelay()
    port = _free_port()
    started = threading.Event()

    def run():
        config = uvicorn.Config(relay.app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        original_startup = server.startup

        async def startup_hook(*a, **kw):
            await original_startup(*a, **kw)
            started.set()

        server.startup = startup_hook
        server.run()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert started.wait(timeout=10), "Mock relay server failed to start"
    relay.url = f"http://127.0.0.1:{port}"
    yield relay


@pytest.fixture(scope="module")
def relay_bridge_port():
    return _free_port()


@pytest.fixture(scope="module")
def relay_bridge_process(relay_bridge_port):
    """Start a bridge subprocess for relay tests."""
    proc = subprocess.Popen(
        [sys.executable, str(BRIDGE_DIR / "bridge.py"), "--port", str(relay_bridge_port), "--host", "127.0.0.1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{relay_bridge_port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/status", timeout=1).status_code == 200:
                break
        except (httpx.ConnectError, httpx.ReadError):
            pass
        time.sleep(0.1)
    else:
        proc.kill()
        stdout = proc.stdout.read().decode() if proc.stdout else ""
        stderr = proc.stderr.read().decode() if proc.stderr else ""
        raise RuntimeError(f"Bridge failed to start.\nstdout: {stdout}\nstderr: {stderr}")
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(autouse=True)
def _cleanup(relay_bridge_process, mock_relay):
    """Ensure clean bridge state before and after each test."""
    # Pre-test: stop any leftover agent from a previous test.
    try:
        _ensure_stopped(relay_bridge_process)
    except Exception:
        pass
    mock_relay.reset()

    yield
    mock_relay.reset()


def _ensure_stopped(bridge_url: str):
    """Ensure no agent is running on the bridge."""
    status = httpx.get(f"{bridge_url}/status", timeout=5).json()
    if status.get("status") == "not_running":
        return
    # /stop blocks until the bridge is fully shut down.
    httpx.post(f"{bridge_url}/stop", timeout=15)
    status = httpx.get(f"{bridge_url}/status", timeout=5).json()
    if status.get("status") != "not_running":
        raise RuntimeError(f"Bridge did not stop after /stop returned: {status}")


def _start_agent(bridge_url: str, mock_relay: MockRelay, agent_script: str = STUB_AGENT):
    """Start an agent on the bridge with relay pointing to the mock."""
    _ensure_stopped(bridge_url)
    resp = httpx.post(
        f"{bridge_url}/start",
        json={
            "command": sys.executable,
            "args": [agent_script],
            "relay_url": mock_relay.url,
            "bridge_id": "test-bridge",
            "bridge_token": "test-token",
        },
        timeout=5,
    )
    assert resp.status_code == 200, f"/start failed: {resp.text}"
    assert resp.json()["status"] == "started"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_relay_connects_and_pushes(relay_bridge_process, mock_relay):
    """Sanity check: output from the agent eventually reaches the mock relay."""
    _start_agent(relay_bridge_process, mock_relay)
    _send_handshake_and_prompt(mock_relay)

    pushed = _wait_for_pushed(mock_relay, _has_end_turn, timeout=15.0)
    assert _has_end_turn(pushed), f"Prompt response never reached mock relay. Got {len(pushed)} messages."


def test_relay_delivers_output_promptly(relay_bridge_process, mock_relay):
    """Output should reach the relay promptly after the agent produces it.

    With concurrent push/pull, output is pushed independently and arrives
    shortly after the agent writes to stdout. A serial relay would add
    at least RELAY_PULL_TIMEOUT_SECONDS (20s) of latency per round-trip.
    """
    _start_agent(relay_bridge_process, mock_relay)
    _send_handshake_and_prompt(mock_relay)

    # Input travels through the pull queue and output through push.
    # With concurrent push/pull, the full round-trip should complete
    # well within the pull timeout (20s). We allow up to 10s to
    # account for the mock relay's pull block time (~3s).
    pushed = _wait_for_pushed(mock_relay, _has_end_turn, timeout=10.0)
    assert _has_end_turn(pushed), (
        f"Output not delivered within 10s. Got {len(pushed)} messages. The relay may be serialising push and pull."
    )


def test_relay_flushes_on_agent_exit(relay_bridge_process, mock_relay):
    """All output should reach the relay even when the agent exits.

    The exit agent produces output and exits after one prompt. The relay
    must flush remaining buffered output before it stops.
    """
    _start_agent(relay_bridge_process, mock_relay, agent_script=STUB_AGENT_EXIT)
    _send_handshake_and_prompt(mock_relay)

    # Wait for relay to detect exit and (hopefully) flush.
    pushed = _wait_for_pushed(mock_relay, _has_end_turn, timeout=10.0)
    assert _has_end_turn(pushed), (
        f"Final prompt response lost on agent exit. Got {len(pushed)} messages: {pushed[:3]}..."
    )


def test_relay_survives_stop_start_cycle(relay_bridge_process, mock_relay):
    """The relay must work correctly after a stop/start cycle.

    Start an exit agent (which causes stdout_done and read_stdout to run to
    completion), stop it, then start a new long-lived agent. If the bridge
    does not properly isolate per-agent state, the old read_stdout can set
    the new stdout_done event, causing the new relay to exit prematurely.
    """
    # Cycle 1: exit agent
    _start_agent(relay_bridge_process, mock_relay, agent_script=STUB_AGENT_EXIT)
    _send_handshake_and_prompt(mock_relay)
    _wait_for_pushed(mock_relay, _has_end_turn, timeout=10.0)

    # Stop and reset
    _ensure_stopped(relay_bridge_process)
    mock_relay.reset()

    # Cycle 2: long-lived agent
    _start_agent(relay_bridge_process, mock_relay, agent_script=STUB_AGENT)
    _send_handshake_and_prompt(mock_relay)

    pushed = _wait_for_pushed(mock_relay, _has_end_turn, timeout=10.0)
    assert _has_end_turn(pushed), f"Relay died after stop/start cycle. Got {len(pushed)} messages from second agent."


def test_relay_forwards_stdin_via_pull(relay_bridge_process, mock_relay):
    """Messages queued in the relay pull should reach the agent's stdin.

    Queue an ACP initialize request in the mock's pull response. The bridge
    pulls it, writes it to the agent's stdin, and the agent responds. The
    response reaches the mock via push.
    """
    _start_agent(relay_bridge_process, mock_relay)

    init_request = _jsonrpc("initialize", {}, 100)
    mock_relay.queue_pull_message(init_request)

    pushed = _wait_for_pushed(mock_relay, _has_protocol_version, timeout=10.0)
    assert _has_protocol_version(pushed), (
        f"Initialize response not received via relay round-trip. Got {len(pushed)} messages."
    )
