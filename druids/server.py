from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from druids.types import ToolCallError, to_jsonable

if TYPE_CHECKING:
    from druids.context import Context


@dataclass
class SSEEvent:
    event: str
    data: dict[str, Any]


class AgentChannel:
    """Per-agent async event channel with backlog for one SSE subscriber."""

    def __init__(self) -> None:
        self._backlog: deque[SSEEvent] = deque()
        self._subscriber: asyncio.Queue[SSEEvent] | None = None
        self.registered = asyncio.Event()

    def publish(self, event: SSEEvent) -> None:
        if self._subscriber is None:
            self._backlog.append(event)
            return
        self._subscriber.put_nowait(event)

    def subscribe(self) -> asyncio.Queue[SSEEvent]:
        subscriber: asyncio.Queue[SSEEvent] = asyncio.Queue()
        self._subscriber = subscriber
        while self._backlog:
            subscriber.put_nowait(self._backlog.popleft())
        return subscriber

    def unsubscribe(self, subscriber: asyncio.Queue[SSEEvent]) -> None:
        if self._subscriber is subscriber:
            self._subscriber = None


class OrchestratorServer:
    """Async in-process HTTP server for agent communication."""

    def __init__(self, ctx: Context, bind_host: str = "127.0.0.1", bind_port: int = 0):
        self.ctx = ctx
        self._host = bind_host
        self._port = bind_port
        self._server = None
        self._socket: socket.socket | None = None
        self._task: asyncio.Task[None] | None = None
        self._actual_port: int | None = None
        self._stop_event = asyncio.Event()

    @property
    def port(self) -> int:
        return self._actual_port or self._port

    async def start(self) -> None:
        import uvicorn

        app = _make_app(self.ctx, self._stop_event)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self._host, self._port))
        sock.listen(2048)
        sock.setblocking(False)

        self._socket = sock
        self._actual_port = sock.getsockname()[1]

        config = uvicorn.Config(
            app,
            host=self._host,
            port=self._port,
            log_level="warning",
            access_log=False,
            lifespan="off",
        )
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None
        self._task = asyncio.create_task(self._server.serve(sockets=[sock]))

        deadline = asyncio.get_running_loop().time() + 10
        while True:
            if self._server.started:
                return
            if self._task.done():
                await self._task
                raise RuntimeError("Server exited before startup completed")
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Timed out starting orchestrator server")
            await asyncio.sleep(0.01)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.TimeoutError:
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._task
            self._task = None
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        self._server = None



def _make_app(ctx: Context, stop_event: asyncio.Event) -> Starlette:
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
            result = await ctx._handle_tool_call_request(agent_id, tool, params)
        except ToolCallError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status_code)
        except Exception as exc:  # pragma: no cover
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse({"result": to_jsonable(result)})

    async def events(req: Request) -> Response:
        agent_id = req.path_params["agent_id"]
        try:
            subscription = ctx._subscribe_events(agent_id)
        except ToolCallError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status_code)

        async def stream():
            try:
                while not stop_event.is_set():
                    try:
                        event = await asyncio.wait_for(subscription.get(), timeout=10)
                    except asyncio.TimeoutError:
                        if await req.is_disconnected():
                            return
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

        return StreamingResponse(
            content=stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return Starlette(
        routes=[
            Route("/health", health),
            Route("/agents/register", register, methods=["POST"]),
            Route("/agents/{agent_id}/tool_call", tool_call, methods=["POST"]),
            Route("/agents/{agent_id}/events", events),
        ]
    )
