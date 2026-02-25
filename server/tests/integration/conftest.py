"""Fixtures for integration tests.

Provides a real bridge subprocess and stub agent. No Morph VMs, no API keys.

- bridge_process (module): starts bridge.py, waits for /status, yields URL
- bridge_with_stub (function): POSTs /start with stub_agent.py, yields URL, POSTs /stop
"""

import socket
import subprocess
import sys
import time

import httpx
import pytest
from orpheus.paths import BRIDGE_DIR, STUB_AGENT_PATH


def _free_port() -> int:
    """Find an available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def bridge_port():
    return _free_port()


@pytest.fixture(scope="module")
def bridge_process(bridge_port):
    """Start the bridge as a subprocess and wait until it is ready."""
    proc = subprocess.Popen(
        [sys.executable, str(BRIDGE_DIR / "bridge.py"), "--port", str(bridge_port), "--host", "127.0.0.1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    url = f"http://127.0.0.1:{bridge_port}"

    # Poll /status until the bridge is accepting connections
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{url}/status", timeout=1)
            if resp.status_code == 200:
                break
        except (httpx.ConnectError, httpx.ReadError):
            pass
        time.sleep(0.1)
    else:
        proc.kill()
        stdout = proc.stdout.read().decode() if proc.stdout else ""
        stderr = proc.stderr.read().decode() if proc.stderr else ""
        raise RuntimeError(f"Bridge failed to start within 10s.\nstdout: {stdout}\nstderr: {stderr}")

    yield url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def bridge_with_stub(bridge_process):
    """Start the stub agent on the bridge. Stops it on teardown."""
    resp = httpx.post(
        f"{bridge_process}/start",
        json={"command": sys.executable, "args": [str(STUB_AGENT_PATH)]},
        timeout=5,
    )
    assert resp.status_code == 200, f"Bridge /start failed: {resp.text}"
    assert resp.json()["status"] == "started"

    yield bridge_process

    httpx.post(f"{bridge_process}/stop", timeout=5)


@pytest.fixture(autouse=True)
def _stop_bridge_agent(bridge_process):
    """Safety net: stop any running agent after each test."""
    yield
    try:
        httpx.post(f"{bridge_process}/stop", timeout=2)
    except Exception:
        pass
