"""Stress tests for the bridge relay.

These tests push the relay harder than the correctness tests:

- High-throughput: 1000 numbered lines, verify all arrive in order
- High-throughput with exit: same but agent exits, relay must flush everything
- Rapid start/stop cycles: 5 cycles, verify no state leakage
- Concurrent input: 10 simultaneous /input requests, verify all get responses
- Large messages: single 500KB JSON-RPC line through the relay
"""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from druids_server.paths import BRIDGE_DIR

from tests.integration.test_relay import (
    MockRelay,
    _ensure_stopped,
    _free_port,
    _has_end_turn,
    _jsonrpc,
    _send,
    _send_handshake_and_prompt,
    _wait_for_pushed,
)


STUB_AGENT = str(Path(__file__).parent / "stub_agent.py")
STUB_AGENT_FLOOD = str(Path(__file__).parent / "stub_agent_flood.py")
FLOOD_COUNT = 1000


# ---------------------------------------------------------------------------
# Fixtures (separate bridge instance from the correctness tests)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_relay():
    relay = MockRelay(pull_block_seconds=3.0)
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
def bridge_port():
    return _free_port()


@pytest.fixture(scope="module")
def bridge_process(bridge_port):
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
def _cleanup(bridge_process, mock_relay):
    try:
        _ensure_stopped(bridge_process)
    except Exception:
        pass
    mock_relay.reset()
    yield
    mock_relay.reset()


def _start_agent(bridge_url, mock_relay, agent_script=STUB_AGENT, env=None):
    _ensure_stopped(bridge_url)
    body = {
        "command": sys.executable,
        "args": [agent_script],
        "relay_url": mock_relay.url,
        "bridge_id": "stress-test-bridge",
        "bridge_token": "stress-test-token",
    }
    if env:
        body["env"] = env
    resp = httpx.post(f"{bridge_url}/start", json=body, timeout=5)
    assert resp.status_code == 200, f"/start failed: {resp.text}"
    assert resp.json()["status"] == "started"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_line_numbers(messages):
    """Extract 'line N' numbers from pushed messages.

    Handles both plain ("line 42") and padded ("line 42AAAA...") text.
    """
    import re

    numbers = []
    for msg in messages:
        try:
            parsed = json.loads(msg)
        except (json.JSONDecodeError, TypeError):
            continue
        params = parsed.get("params", {})
        update = params.get("update", {})
        content = update.get("content", {})
        text = content.get("text", "")
        m = re.match(r"^line (\d+)", text)
        if m:
            numbers.append(int(m.group(1)))
    return numbers


# ---------------------------------------------------------------------------
# Stress tests
# ---------------------------------------------------------------------------


def test_stress_high_throughput(bridge_process, mock_relay):
    """1000 numbered lines should all reach the relay in order."""
    _start_agent(
        bridge_process,
        mock_relay,
        agent_script=STUB_AGENT_FLOOD,
        env={"FLOOD_COUNT": str(FLOOD_COUNT)},
    )
    _send_handshake_and_prompt(mock_relay)

    pushed = _wait_for_pushed(mock_relay, _has_end_turn, timeout=30.0)
    assert _has_end_turn(pushed), f"Never got end_turn. Got {len(pushed)} messages."

    numbers = _extract_line_numbers(pushed)
    assert len(numbers) == FLOOD_COUNT, (
        f"Expected {FLOOD_COUNT} numbered lines, got {len(numbers)}. "
        f"First missing: {next(i for i in range(FLOOD_COUNT) if i not in set(numbers)) if len(numbers) < FLOOD_COUNT else 'N/A'}"
    )
    assert numbers == list(range(FLOOD_COUNT)), "Lines arrived out of order or with duplicates."


def test_stress_high_throughput_with_exit(bridge_process, mock_relay):
    """1000 lines followed by agent exit -- flush must deliver everything."""
    _start_agent(
        bridge_process,
        mock_relay,
        agent_script=STUB_AGENT_FLOOD,
        env={"FLOOD_COUNT": str(FLOOD_COUNT), "EXIT_AFTER_FLOOD": "1"},
    )
    _send_handshake_and_prompt(mock_relay)

    pushed = _wait_for_pushed(mock_relay, _has_end_turn, timeout=30.0)
    assert _has_end_turn(pushed), f"Final response lost on exit. Got {len(pushed)} messages."

    numbers = _extract_line_numbers(pushed)
    assert len(numbers) == FLOOD_COUNT, f"Expected {FLOOD_COUNT} lines after exit flush, got {len(numbers)}."
    assert numbers == list(range(FLOOD_COUNT)), "Lines out of order or duplicated after exit flush."


def test_stress_rapid_start_stop_cycles(bridge_process, mock_relay):
    """5 start/prompt/stop cycles with no state leakage between them."""
    for cycle in range(5):
        mock_relay.reset()
        _start_agent(bridge_process, mock_relay)
        _send_handshake_and_prompt(mock_relay)

        pushed = _wait_for_pushed(mock_relay, _has_end_turn, timeout=10.0)
        assert _has_end_turn(pushed), f"Cycle {cycle}: no end_turn. Got {len(pushed)} messages."

        # Verify no "line N" messages leaked from a previous flood test.
        numbers = _extract_line_numbers(pushed)
        assert len(numbers) == 0, f"Cycle {cycle}: found {len(numbers)} flood lines from a previous test."

        _ensure_stopped(bridge_process)


def test_stress_concurrent_input(bridge_process, mock_relay):
    """10 simultaneous initialize requests should all get responses."""
    _start_agent(bridge_process, mock_relay)

    # Send 10 initialize requests concurrently via threads.
    results = [None] * 10
    errors = [None] * 10

    def send_init(idx):
        try:
            _send(mock_relay, _jsonrpc("initialize", {}, 1000 + idx))
            results[idx] = True
        except Exception as e:
            errors[idx] = str(e)

    threads = [threading.Thread(target=send_init, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert all(r is True for r in results), f"Some sends failed: {errors}"

    # Wait for all 10 responses to reach the relay.
    def has_10_protocol_versions(messages):
        count = sum(1 for m in messages if '"protocolVersion"' in m)
        return count >= 10

    pushed = _wait_for_pushed(mock_relay, has_10_protocol_versions, timeout=10.0)
    response_count = sum(1 for m in pushed if '"protocolVersion"' in m)
    assert response_count >= 10, f"Expected 10 initialize responses, got {response_count}."


def test_stress_large_input(bridge_process, mock_relay):
    """A single 500KB JSON-RPC input line should reach the agent via stdin."""
    _start_agent(bridge_process, mock_relay)

    big_payload = "x" * (500 * 1024)
    large_request = _jsonrpc("initialize", {"padding": big_payload}, 9999)

    _send(mock_relay, large_request)

    def has_response(messages):
        return any('"protocolVersion"' in m for m in messages)

    pushed = _wait_for_pushed(mock_relay, has_response, timeout=10.0)
    assert has_response(pushed), "Agent did not respond to large initialize request."


def test_stress_large_output_single_line(bridge_process, mock_relay):
    """A single 2MB output line (simulating a screenshot) should survive the relay.

    Real agents return base64-encoded screenshots in tool_call_update
    notifications. A 1920x1080 PNG screenshot is typically 1-3MB of
    base64. This tests that the relay handles a single oversized line.
    """
    _start_agent(
        bridge_process,
        mock_relay,
        agent_script=STUB_AGENT_FLOOD,
        env={"FLOOD_COUNT": "1", "PAD_BYTES": str(2 * 1024 * 1024)},
    )
    _send_handshake_and_prompt(mock_relay)

    pushed = _wait_for_pushed(mock_relay, _has_end_turn, timeout=30.0)
    assert _has_end_turn(pushed), f"end_turn not received. Got {len(pushed)} messages."

    numbers = _extract_line_numbers(pushed)
    assert len(numbers) == 1, f"Expected 1 large line, got {len(numbers)}."

    # Verify the payload was not truncated: find the line 0 message and
    # check its text length.
    for msg in pushed:
        try:
            parsed = json.loads(msg)
        except (json.JSONDecodeError, TypeError):
            continue
        text = parsed.get("params", {}).get("update", {}).get("content", {}).get("text", "")
        if text.startswith("line 0"):
            assert len(text) >= 2 * 1024 * 1024, f"Large output truncated: expected >= 2MB, got {len(text)} bytes."
            break
    else:
        pytest.fail("Could not find the large line 0 message in pushed output.")


def test_stress_many_large_lines(bridge_process, mock_relay):
    """10 lines of 500KB each (~5MB total) should all reach the relay intact."""
    line_count = 10
    line_size = 500 * 1024

    _start_agent(
        bridge_process,
        mock_relay,
        agent_script=STUB_AGENT_FLOOD,
        env={"FLOOD_COUNT": str(line_count), "PAD_BYTES": str(line_size)},
    )
    _send_handshake_and_prompt(mock_relay)

    pushed = _wait_for_pushed(mock_relay, _has_end_turn, timeout=30.0)
    assert _has_end_turn(pushed), f"end_turn not received. Got {len(pushed)} messages."

    numbers = _extract_line_numbers(pushed)
    assert len(numbers) == line_count, f"Expected {line_count} large lines, got {len(numbers)}."
    assert numbers == list(range(line_count)), "Large lines arrived out of order."


def test_stress_large_output_then_exit(bridge_process, mock_relay):
    """5MB of output followed by agent exit -- flush must deliver everything.

    This is the hardest case: the agent produces a burst of large output
    and immediately exits. The relay must detect exit via stdout_done,
    then flush all remaining multi-megabyte data.
    """
    line_count = 5
    line_size = 1024 * 1024  # 1MB each = 5MB total

    _start_agent(
        bridge_process,
        mock_relay,
        agent_script=STUB_AGENT_FLOOD,
        env={
            "FLOOD_COUNT": str(line_count),
            "PAD_BYTES": str(line_size),
            "EXIT_AFTER_FLOOD": "1",
        },
    )
    _send_handshake_and_prompt(mock_relay)

    pushed = _wait_for_pushed(mock_relay, _has_end_turn, timeout=60.0)
    assert _has_end_turn(pushed), f"Final response lost after 5MB output + exit. Got {len(pushed)} messages."

    numbers = _extract_line_numbers(pushed)
    assert len(numbers) == line_count, f"Expected {line_count} 1MB lines after exit, got {len(numbers)}."
    assert numbers == list(range(line_count)), "Lines out of order after large exit flush."
