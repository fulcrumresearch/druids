from __future__ import annotations

import json
import re
import socket
import time
from typing import TYPE_CHECKING, Any

import websockets.sync.client

if TYPE_CHECKING:
    from ramure.runtime import Runtime


class FakeAgentClient:
    """Test client that speaks WebSocket to the orchestrator."""

    def __init__(self, base_url: str, execution_id: str, agent_id: str):
        self.base_url = base_url.rstrip("/")
        self.execution_id = execution_id
        self.agent_id = agent_id
        self._ws: websockets.sync.client.ClientConnection | None = None
        self._last_seq: int = 0
        self._call_counter: int = 0
        self._received_entries: list[dict[str, Any]] = []
        self._event_queue: list[dict[str, Any]] = []

    def connect(self) -> None:
        ws_url = self.base_url
        if ws_url.startswith("http://"):
            ws_url = ws_url.replace("http://", "ws://")
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

    def _drain_until(self, predicate, timeout: float = 5.0) -> dict[str, Any]:
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for matching entry")
            entry = self._recv(timeout=remaining)
            if predicate(entry):
                return entry

    def sync(self) -> None:
        self._send({"type": "sync", "after": self._last_seq})

    def register(self) -> list[dict[str, Any]]:
        self.sync()
        self._send({
            "type": "event",
            "event_type": "register",
            "data": {"execution_id": self.execution_id},
        })
        entry = self._drain_until(lambda e: e.get("type") == "registered")
        return entry["data"]["tools"]

    def tool_call(self, tool: str, params: dict[str, Any] | None = None) -> Any:
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
        if "error" in data:
            raise RuntimeError(data["error"])
        return data.get("result")

    def tool_call_error(self, tool: str, params: dict[str, Any] | None = None) -> tuple[str, str]:
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
        for i, entry in enumerate(self._event_queue):
            if entry.get("type") == expected_type:
                self._event_queue.pop(i)
                return entry.get("data", {})

        entry = self._drain_until(
            lambda e: e.get("type") == expected_type,
            timeout=timeout,
        )
        if entry in self._event_queue:
            self._event_queue.remove(entry)
        return entry.get("data", {})


def disable_agent_launch(runtime: Runtime, monkeypatch) -> None:
    async def fake_launch(agent):
        return False

    monkeypatch.setattr(runtime, "launch_agent", fake_launch)


def wait_for_server(base_url: str, timeout: float = 5) -> None:
    """Wait until the server port is accepting TCP connections."""
    m = re.search(r":(\d+)", base_url)
    if not m:
        return
    port = int(m.group(1))
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1)
            s.close()
            return
        except OSError as exc:
            last_error = exc
            time.sleep(0.05)
    raise RuntimeError(f"Server did not become ready: {last_error}")
