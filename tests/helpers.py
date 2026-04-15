from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from typing import TYPE_CHECKING, Any

import websockets
import websockets.sync.client

if TYPE_CHECKING:
    from druids.runtime import Runtime


class FakeAgentClient:
    """Test client that speaks WebSocket to the orchestrator, matching the spec."""

    def __init__(self, base_url: str, execution_id: str, agent_id: str):
        self.base_url = base_url.rstrip("/")
        self.execution_id = execution_id
        self.agent_id = agent_id
        self._ws: websockets.sync.client.ClientConnection | None = None
        self._last_seq: int = 0
        self._pending_calls: dict[str, Any] = {}  # call_id -> result holder
        self._call_counter: int = 0
        self._received_entries: list[dict[str, Any]] = []  # all received log entries
        self._event_queue: list[dict[str, Any]] = []  # entries not yet consumed

    def connect(self) -> None:
        """Open WebSocket connection."""
        ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        self._ws = websockets.sync.client.connect(
            f"{ws_url}/agents/{self.agent_id}/ws",
            open_timeout=10,
        )

    def close(self) -> None:
        if self._ws:
            self._ws.close()
            self._ws = None

    def _send(self, msg: dict[str, Any]) -> None:
        assert self._ws is not None
        self._ws.send(json.dumps(msg))

    def _recv(self, timeout: float = 5.0) -> dict[str, Any]:
        assert self._ws is not None
        raw = self._ws.recv(timeout=timeout)
        entry = json.loads(raw)
        self._last_seq = max(self._last_seq, entry.get("seq", 0))
        self._received_entries.append(entry)
        self._event_queue.append(entry)
        return entry

    def _drain_until(
        self,
        predicate,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Read entries until predicate(entry) is True, return that entry."""
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for matching entry")
            entry = self._recv(timeout=remaining)
            if predicate(entry):
                return entry

    def sync(self) -> None:
        """Send sync message with current last_seq."""
        self._send({"type": "sync", "after": self._last_seq})

    def register(self) -> list[dict[str, Any]]:
        """Register with the server. Returns tool definitions."""
        self.sync()
        self._send({
            "type": "event",
            "event_type": "register",
            "data": {"execution_id": self.execution_id},
        })
        # Drain until we get the 'registered' entry
        entry = self._drain_until(lambda e: e.get("type") == "registered")
        return entry["data"]["tools"]

    def tool_call(self, tool: str, params: dict[str, Any] | None = None) -> Any:
        """Call a druids tool and wait for the result."""
        self._call_counter += 1
        call_id = f"test-tc-{self._call_counter}"
        self._send({
            "type": "event",
            "event_type": "tool_call",
            "data": {"call_id": call_id, "tool": tool, "params": params or {}},
        })
        # Drain until we get the tool_result for this call_id
        entry = self._drain_until(
            lambda e: e.get("type") == "tool_result" and e.get("data", {}).get("call_id") == call_id
        )
        data = entry.get("data", {})
        if "error" in data:
            raise RuntimeError(data["error"])
        return data.get("result")

    def tool_call_error(self, tool: str, params: dict[str, Any] | None = None) -> tuple[str, str]:
        """Call a tool expecting an error. Returns (error_type, error_message)."""
        self._call_counter += 1
        call_id = f"test-tc-{self._call_counter}"
        self._send({
            "type": "event",
            "event_type": "tool_call",
            "data": {"call_id": call_id, "tool": tool, "params": params or {}},
        })
        entry = self._drain_until(
            lambda e: e.get("type") == "tool_result" and e.get("data", {}).get("call_id") == call_id
        )
        data = entry.get("data", {})
        return "error", data.get("error", "")

    def next_event(self, expected_type: str, timeout: float = 5.0) -> dict[str, Any]:
        """Wait for the next log entry of the given type. Returns its data."""
        # Check already-queued entries first
        for i, entry in enumerate(self._event_queue):
            if entry.get("type") == expected_type:
                self._event_queue.pop(i)
                return entry.get("data", {})

        # Otherwise drain from websocket
        entry = self._drain_until(
            lambda e: e.get("type") == expected_type,
            timeout=timeout,
        )
        # Remove it from event_queue (it was added by _recv → _drain_until)
        if entry in self._event_queue:
            self._event_queue.remove(entry)
        return entry.get("data", {})


def disable_agent_launch(runtime: Runtime, monkeypatch) -> None:
    async def fake_launch(agent):
        return False

    monkeypatch.setattr(runtime, "_launch_agent", fake_launch)


def wait_for_server(base_url: str, timeout: float = 5) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last_error = exc
            time.sleep(0.05)
    raise RuntimeError(f"Server did not become ready: {last_error}")
