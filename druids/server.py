from __future__ import annotations

import asyncio
import json
import queue
import threading
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from druids.types import ToolCallError, to_jsonable

if TYPE_CHECKING:
    from druids.context import Context


@dataclass
class SSEEvent:
    event: str
    data: dict[str, Any]


class AgentChannel:
    """Per-agent SSE event queue with backlog for pre-connection events."""

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


def _make_app(ctx: Context) -> Starlette:
    """Build the Starlette app with all routes."""

    async def health(req: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def register(req: Request) -> JSONResponse:
        payload = await req.json()
        agent_id = str(payload.get("agent_id", ""))
        execution_id = str(payload.get("execution_id", ""))
        try:
            tools = ctx._register_agent(agent_id, execution_id)
        except ToolCallError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status_code)
        return JSONResponse({"tools": to_jsonable(tools)})

    async def tool_call(req: Request) -> JSONResponse:
        agent_id = req.path_params["agent_id"]
        payload = await req.json()
        tool = str(payload.get("tool", ""))
        params = payload.get("params", {}) or {}
        try:
            result = ctx._handle_tool_call_request(agent_id, tool, params)
        except ToolCallError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status_code)
        except Exception as exc:  # broad catch at API boundary
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse({"result": to_jsonable(result)})

    async def events(req: Request) -> Response:
        agent_id = req.path_params["agent_id"]
        try:
            subscription = ctx._subscribe_events(agent_id)
        except ToolCallError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status_code)

        ctx._log_event("agent_connected", agent=agent_id)

        async def stream():
            try:
                loop = asyncio.get_event_loop()
                while not ctx._server_stop_event.is_set():
                    try:
                        event = await asyncio.wait_for(
                            loop.run_in_executor(None, subscription.get, True, 10),
                            timeout=15,
                        )
                    except (asyncio.TimeoutError, queue.Empty):
                        yield ": keepalive\n\n"
                        continue
                    payload = json.dumps(to_jsonable(event.data))
                    yield f"event: {event.event}\ndata: {payload}\n\n"
                    if event.event == "shutdown":
                        return
            except asyncio.CancelledError:
                return
            finally:
                ctx._unsubscribe_events(agent_id, subscription)
                ctx._log_event("agent_disconnected", agent=agent_id)

        return StreamingResponse(
            content=stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return Starlette(routes=[
        Route("/health", health),
        Route("/agents/register", register, methods=["POST"]),
        Route("/agents/{agent_id}/tool_call", tool_call, methods=["POST"]),
        Route("/agents/{agent_id}/events", events),
    ])


class OrchestratorServer:
    """In-process HTTP server for agent communication."""

    def __init__(self, ctx: Context, bind_host: str, bind_port: int):
        self.ctx = ctx
        self._host = bind_host
        self._port = bind_port
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._actual_port: int | None = None

    @property
    def port(self) -> int:
        return self._actual_port or self._port

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="druids-server", daemon=True)
        self._thread.start()
        self._started.wait(timeout=10)

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        import uvicorn

        app = _make_app(self.ctx)
        config = uvicorn.Config(
            app,
            host=self._host,
            port=self._port,
            log_level="warning",
        )
        self._server = uvicorn.Server(config)

        # Capture the actual port after bind (when port=0)
        original_startup = self._server.startup

        async def _startup_and_signal(**kwargs: Any):
            await original_startup(**kwargs)
            for server in self._server.servers:
                for sock in server.sockets:
                    self._actual_port = sock.getsockname()[1]
                    break
            self._started.set()

        self._server.startup = _startup_and_signal
        self._server.run()
