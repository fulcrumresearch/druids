"""End-to-end relay tests using the real server bridge routes and relay hub.

Unlike test_relay.py which uses a mock relay server, these tests stand up
the actual BridgeRelayHub and bridge route handlers from the server code.
The server side uses AgentConnection (the real ACP client) to talk to the
stub agent through the bridge over the relay.

Data flow tested:

    AgentConnection.prompt()
      -> BridgeRelayWriter -> bridge_relay_hub.queue_input()
        -> bridge pulls via /api/bridge/{id}/pull
          -> bridge writes to agent stdin
            -> agent processes, writes to stdout
          -> bridge reads stdout
        -> bridge pushes via /api/bridge/{id}/push
      -> bridge_relay_hub.push_output()
    -> BridgeRelayReader -> AgentConnection receives response
"""

import asyncio
import subprocess
import sys
import threading
import time
from pathlib import Path

import druids_server.lib.connection as connection_module
import httpx
import pytest
import uvicorn
from druids_server.lib.connection import AgentConnection, BridgeRelayHub
from druids_server.paths import BRIDGE_DIR
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from tests.integration.test_relay import _ensure_stopped, _free_port


STUB_AGENT = str(Path(__file__).parent / "stub_agent.py")
STUB_AGENT_EXIT = str(Path(__file__).parent / "stub_agent_exit.py")

BRIDGE_ID = "e2e-test-bridge"
BRIDGE_TOKEN = "e2e-test-token"


# ---------------------------------------------------------------------------
# Real relay server using actual server code
# ---------------------------------------------------------------------------


def _build_relay_app(hub: BridgeRelayHub) -> FastAPI:
    """Build a FastAPI app with the real bridge relay endpoints.

    This replicates what the server does in druids_server/api/routes/bridge.py
    but uses a provided hub instance instead of the global singleton.
    """
    app = FastAPI()

    class PushRequest(BaseModel):
        messages: list[str] = Field(default_factory=list)

    class PullRequest(BaseModel):
        max_items: int = 128
        timeout_seconds: float = 20.0

    def _extract_bearer(authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "Missing bearer token")
        return authorization[7:]

    @app.post("/api/bridge/{bridge_id}/push")
    async def push_output(
        bridge_id: str,
        request: PushRequest,
        authorization: str | None = Header(default=None),
    ):
        token = _extract_bearer(authorization)
        if not hub.is_valid_token(bridge_id, token):
            raise HTTPException(401, "Invalid bridge credentials")
        await hub.mark_connected(bridge_id)
        await hub.push_output(bridge_id, request.messages)
        return {"status": "ok", "count": len(request.messages)}

    @app.post("/api/bridge/{bridge_id}/pull")
    async def pull_input(
        bridge_id: str,
        request: PullRequest,
        authorization: str | None = Header(default=None),
    ):
        token = _extract_bearer(authorization)
        if not hub.is_valid_token(bridge_id, token):
            raise HTTPException(401, "Invalid bridge credentials")
        await hub.mark_connected(bridge_id)
        max_items = max(1, min(request.max_items, 1024))
        timeout_seconds = max(0.0, min(request.timeout_seconds, 55.0))
        messages = await hub.pull_input(bridge_id, max_items=max_items, timeout_seconds=timeout_seconds)
        return {"messages": messages}

    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def relay_hub():
    """A fresh BridgeRelayHub replacing the global singleton.

    BridgeRelayWriter and BridgeRelayReader reference the module-level bridge_relay_hub.
    We replace it so the HTTP routes and the AgentConnection share the same
    queues.
    """
    hub = BridgeRelayHub()
    original = connection_module.bridge_relay_hub
    connection_module.bridge_relay_hub = hub
    yield hub
    connection_module.bridge_relay_hub = original


@pytest.fixture(scope="module")
def relay_server(relay_hub):
    """Start a server with the real bridge relay routes in a background thread."""
    app = _build_relay_app(relay_hub)
    port = _free_port()
    started = threading.Event()
    loop_ref: list[asyncio.AbstractEventLoop] = []

    def run():
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        original_startup = server.startup

        async def startup_hook(*a, **kw):
            await original_startup(*a, **kw)
            loop_ref.append(asyncio.get_event_loop())
            started.set()

        server.startup = startup_hook
        server.run()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert started.wait(timeout=10), "Relay server failed to start"
    url = f"http://127.0.0.1:{port}"
    yield url, loop_ref[0]


@pytest.fixture(scope="module")
def bridge_port():
    return _free_port()


@pytest.fixture(scope="module")
def bridge_process(bridge_port):
    """Start a bridge subprocess."""
    proc = subprocess.Popen(
        [sys.executable, str(BRIDGE_DIR / "bridge.py"), "--port", str(bridge_port), "--host", "127.0.0.1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{bridge_port}"
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
def _cleanup(bridge_process, relay_hub, relay_server):
    """Stop any running agent and unregister the bridge session."""
    _, loop = relay_server
    try:
        _ensure_stopped(bridge_process)
    except Exception:
        pass
    try:
        _run_in_loop(loop, relay_hub.unregister(BRIDGE_ID))
    except Exception:
        pass
    yield


def _run_in_loop(loop: asyncio.AbstractEventLoop, coro):
    """Run a coroutine in the relay server's event loop (from the test thread)."""
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=30)


def _start_bridge_agent(bridge_url: str, relay_url: str, agent_script: str = STUB_AGENT):
    """Start a stub agent on the bridge, pointing at the relay server."""
    _ensure_stopped(bridge_url)
    resp = httpx.post(
        f"{bridge_url}/start",
        json={
            "command": sys.executable,
            "args": [agent_script],
            "relay_url": relay_url,
            "bridge_id": BRIDGE_ID,
            "bridge_token": BRIDGE_TOKEN,
        },
        timeout=5,
    )
    assert resp.status_code == 200, f"/start failed: {resp.text}"
    assert resp.json()["status"] == "started"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def _e2e_handshake() -> AgentConnection:
    """Create an AgentConnection and do the full ACP handshake via the relay.

    AgentConnection.start() registers the bridge_id on the (patched) global
    hub, waits for the bridge to connect via push/pull, and sends initialize.
    """
    conn = AgentConnection(bridge_id=BRIDGE_ID, bridge_token=BRIDGE_TOKEN)
    await conn.start()
    return conn


def test_e2e_initialize_and_prompt(relay_server, bridge_process):
    """Full ACP round-trip through the real relay: initialize, new session, prompt."""
    relay_url, loop = relay_server
    _start_bridge_agent(bridge_process, relay_url)

    conn = _run_in_loop(loop, _e2e_handshake())
    try:
        session_id = _run_in_loop(loop, conn.new_session())
        assert session_id, "session/new did not return a session ID"

        result = _run_in_loop(loop, conn.prompt("hello"))
        assert result.get("stopReason") == "end_turn"
    finally:
        _run_in_loop(loop, conn.close())


def test_e2e_exit_agent_delivers_all_output(relay_server, bridge_process):
    """Output from an agent that exits should reach the server via the relay."""
    relay_url, loop = relay_server
    _start_bridge_agent(bridge_process, relay_url, agent_script=STUB_AGENT_EXIT)

    conn = _run_in_loop(loop, _e2e_handshake())
    try:
        session_id = _run_in_loop(loop, conn.new_session())
        assert session_id

        # The exit agent handles one prompt then exits. The relay must flush
        # all output before the connection reads EOF.
        result = _run_in_loop(loop, conn.prompt("hello"))
        assert result.get("stopReason") == "end_turn"
    finally:
        _run_in_loop(loop, conn.close())
