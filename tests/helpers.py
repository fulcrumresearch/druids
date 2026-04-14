from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from druids.context import Context


class FakeAgentClient:
    def __init__(self, base_url: str, execution_id: str, agent_id: str):
        self.base_url = base_url.rstrip("/")
        self.execution_id = execution_id
        self.agent_id = agent_id
        self.events: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
        self._thread: threading.Thread | None = None

    def start_events(self) -> None:
        def reader() -> None:
            request = urllib.request.Request(f"{self.base_url}/agents/{self.agent_id}/events")
            with urllib.request.urlopen(request, timeout=30) as response:
                current_event = "message"
                current_data = "{}"
                while True:
                    raw = response.readline()
                    if not raw:
                        return
                    line = raw.decode("utf-8").rstrip("\n")
                    if not line:
                        self.events.put((current_event, json.loads(current_data or "{}")))
                        current_event = "message"
                        current_data = "{}"
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        current_event = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        current_data = line.split(":", 1)[1].strip()

        self._thread = threading.Thread(target=reader, name=f"events-{self.agent_id}", daemon=True)
        self._thread.start()

    def register(self) -> list[dict[str, Any]]:
        payload = json.dumps({"agent_id": self.agent_id, "execution_id": self.execution_id}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/agents/register",
            method="POST",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))["tools"]

    def tool_call(self, tool: str, params: dict[str, Any] | None = None) -> Any:
        payload = json.dumps({"tool": tool, "params": params or {}}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/agents/{self.agent_id}/tool_call",
            method="POST",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))["result"]

    def tool_call_error(self, tool: str, params: dict[str, Any] | None = None) -> tuple[int, str]:
        payload = json.dumps({"tool": tool, "params": params or {}}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/agents/{self.agent_id}/tool_call",
            method="POST",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = json.loads(response.read().decode("utf-8"))
                return response.status, body.get("error", "")
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            return exc.code, body.get("error", "")

    def next_event(self, expected: str | None = None, timeout: float = 5) -> dict[str, Any]:
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(f"Timed out waiting for event {expected!r}")
            event, data = self.events.get(timeout=remaining)
            if expected is None or event == expected:
                return data


def disable_agent_launch(ctx: Context, monkeypatch) -> None:
    async def fake_launch(agent):
        return False

    monkeypatch.setattr(ctx, "_launch_agent", fake_launch)


def wait_for_server(base_url: str, timeout: float = 5) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # pragma: no cover - retry loop
            last_error = exc
            time.sleep(0.05)
    raise RuntimeError(f"Server did not become ready: {last_error}")
