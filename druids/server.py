from __future__ import annotations

import json
import queue
import re
import threading
from collections import deque
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from druids.types import ToolCallError, to_jsonable

if TYPE_CHECKING:
    from druids.context import Context


@dataclass
class SSEEvent:
    event: str
    data: dict[str, Any]


class AgentChannel:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._backlog: deque[SSEEvent] = deque()
        self._subscribers: set[queue.Queue[SSEEvent]] = set()
        self.registered = threading.Event()

    def publish(self, event: SSEEvent) -> None:
        with self._lock:
            if not self._subscribers:
                self._backlog.append(event)
                return
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            subscriber.put(event)

    def subscribe(self) -> queue.Queue[SSEEvent]:
        subscriber: queue.Queue[SSEEvent] = queue.Queue()
        with self._lock:
            self._subscribers.add(subscriber)
            while self._backlog:
                subscriber.put(self._backlog.popleft())
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[SSEEvent]) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)


class _DruidsHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler], ctx: Context):
        super().__init__(server_address, handler_class)
        self.ctx = ctx


class OrchestratorServer:
    """In-process HTTP server for agent registration, tool calls, and SSE."""

    def __init__(self, ctx: Context, bind_host: str, bind_port: int):
        self.ctx = ctx
        self.httpd = _DruidsHTTPServer((bind_host, bind_port), _RequestHandler, ctx)
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="druids-server", daemon=True)

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(timeout=5)


class _RequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._write_json(HTTPStatus.OK, {"status": "ok"})
            return

        match = re.fullmatch(r"/agents/([^/]+)/events", parsed.path)
        if match:
            self._handle_events(match.group(1))
            return

        self._write_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/agents/register":
            self._handle_register()
            return

        match = re.fullmatch(r"/agents/([^/]+)/tool_call", parsed.path)
        if match:
            self._handle_tool_call(match.group(1))
            return

        self._write_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    @property
    def ctx(self) -> Context:
        return self.server.ctx  # type: ignore[attr-defined]

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _write_json(self, status: int | HTTPStatus, body: dict[str, Any]) -> None:
        payload = json.dumps(to_jsonable(body)).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def _handle_register(self) -> None:
        payload = self._read_json()
        agent_id = str(payload.get("agent_id", ""))
        execution_id = str(payload.get("execution_id", ""))

        try:
            tools = self.ctx._register_agent(agent_id, execution_id)
        except ToolCallError as exc:
            self._write_json(exc.status_code, {"error": str(exc)})
            return

        self._write_json(HTTPStatus.OK, {"tools": tools})

    def _handle_tool_call(self, agent_id: str) -> None:
        payload = self._read_json()
        tool = str(payload.get("tool", ""))
        params = payload.get("params", {}) or {}

        try:
            result = self.ctx._handle_tool_call_request(agent_id, tool, params)
        except ToolCallError as exc:
            self._write_json(exc.status_code, {"error": str(exc)})
            return
        except Exception as exc:  # pragma: no cover - safety net
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return

        self._write_json(HTTPStatus.OK, {"result": result})

    def _handle_events(self, agent_id: str) -> None:
        try:
            subscription = self.ctx._subscribe_events(agent_id)
        except ToolCallError as exc:
            self._write_json(exc.status_code, {"error": str(exc)})
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.flush()

        self.ctx._log_event("agent_connected", agent=agent_id)
        try:
            while not self.ctx._server_stop_event.is_set():
                try:
                    event = subscription.get(timeout=10)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                payload = json.dumps(to_jsonable(event.data))
                chunk = f"event: {event.event}\ndata: {payload}\n\n".encode("utf-8")
                self.wfile.write(chunk)
                self.wfile.flush()
                if event.event == "shutdown":
                    return
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            self.ctx._unsubscribe_events(agent_id, subscription)
            self.ctx._log_event("agent_disconnected", agent=agent_id)
