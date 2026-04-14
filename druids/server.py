from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from druids.types import ToolCallError, to_jsonable

if TYPE_CHECKING:
    from druids.runtime import Runtime


STARTUP_TIMEOUT = 10
SHUTDOWN_TIMEOUT = 10
EVENT_POLL_TIMEOUT = 10


@dataclass
class SSEEvent:
    event: str
    data: dict[str, Any]


class AgentChannel:
    """Per-agent async event channel with backlog for one SSE subscriber."""

    def __init__(self) -> None:
        self._backlog: deque[SSEEvent] = deque()
        self._subscriber: asyncio.Queue[SSEEvent] | None = None

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

    def __init__(
        self,
        runtime: Runtime,
        bind_host: str = "127.0.0.1",
        bind_port: int = 0,
    ) -> None:
        self.runtime = runtime
        self._host = bind_host
        self._port = bind_port
        self._server: uvicorn.Server | None = None
        self._socket: socket.socket | None = None
        self._task: asyncio.Task[None] | None = None
        self._actual_port: int | None = None
        self._stop_event = asyncio.Event()

    @property
    def port(self) -> int:
        return self._actual_port or self._port

    async def start(self) -> None:
        app = _OrchestratorAPI(self.runtime, self._stop_event).app()
        sock = self._open_socket()

        self._socket = sock
        self._actual_port = sock.getsockname()[1]
        self._server = uvicorn.Server(self._build_config(app))
        self._server.install_signal_handlers = lambda: None
        self._task = asyncio.create_task(self._server.serve(sockets=[sock]))

        await self._wait_until_started()

    async def stop(self) -> None:
        self._stop_event.set()

        if self._server is not None:
            self._server.should_exit = True

        task = self._task
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=SHUTDOWN_TIMEOUT)
            except asyncio.TimeoutError:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            self._task = None

        if self._socket is not None:
            self._socket.close()
            self._socket = None

        self._server = None

    def _open_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self._host, self._port))
        sock.listen(2048)
        sock.setblocking(False)
        return sock

    def _build_config(self, app: Starlette) -> uvicorn.Config:
        return uvicorn.Config(
            app,
            host=self._host,
            port=self._port,
            log_level="warning",
            access_log=False,
            lifespan="off",
        )

    async def _wait_until_started(self) -> None:
        server = self._server
        task = self._task
        if server is None or task is None:
            raise RuntimeError("Server startup was not initialized")

        deadline = asyncio.get_running_loop().time() + STARTUP_TIMEOUT
        while not server.started:
            if task.done():
                await task
                raise RuntimeError("Server exited before startup completed")
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Timed out starting orchestrator server")
            await asyncio.sleep(0.01)


@dataclass
class _OrchestratorAPI:
    runtime: Runtime
    stop_event: asyncio.Event

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/health", self.health),
                Route("/agents/register", self.register, methods=["POST"]),
                Route("/agents/{agent_id}/tool_call", self.tool_call, methods=["POST"]),
                Route("/agents/{agent_id}/events", self.events),
            ],
            exception_handlers={
                ToolCallError: self.handle_tool_call_error,
                Exception: self.handle_unexpected_error,
            },
        )

    async def health(self, request: Request) -> JSONResponse:
        del request
        return JSONResponse({"status": "ok"})

    async def register(self, request: Request) -> JSONResponse:
        payload = await request.json()
        agent_id = str(payload.get("agent_id", ""))
        execution_id = str(payload.get("execution_id", ""))
        tools = self.runtime._register_agent(agent_id, execution_id)
        return JSONResponse({"tools": to_jsonable(tools)})

    async def tool_call(self, request: Request) -> JSONResponse:
        agent_id = request.path_params["agent_id"]
        payload = await request.json()
        tool_name = str(payload.get("tool", ""))
        params = payload.get("params", {}) or {}
        result = await self.runtime._handle_tool_call_request(agent_id, tool_name, params)
        return JSONResponse({"result": to_jsonable(result)})

    async def events(self, request: Request) -> Response:
        agent_id = request.path_params["agent_id"]
        subscription = self.runtime._subscribe_events(agent_id)
        return StreamingResponse(
            content=self._stream_events(request, agent_id, subscription),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def handle_tool_call_error(
        self, request: Request, exc: Exception
    ) -> JSONResponse:
        del request
        if not isinstance(exc, ToolCallError):
            raise TypeError(f"Expected ToolCallError, got {type(exc).__name__}")
        return self._json_error(str(exc), status_code=exc.status_code)

    async def handle_unexpected_error(
        self, request: Request, exc: Exception
    ) -> JSONResponse:
        del request
        return self._json_error(str(exc), status_code=500)

    async def _stream_events(
        self,
        request: Request,
        agent_id: str,
        subscription: asyncio.Queue[SSEEvent],
    ):
        try:
            while not self.stop_event.is_set():
                poll_result, event = await self._poll_event(request, subscription)
                if poll_result == "disconnect":
                    return
                if poll_result == "keepalive":
                    yield ": keepalive\n\n"
                    continue

                assert event is not None
                yield self._encode_event(event)
                if event.event == "shutdown":
                    return
        except asyncio.CancelledError:
            return
        finally:
            self.runtime._unsubscribe_events(agent_id, subscription)

    async def _poll_event(
        self,
        request: Request,
        subscription: asyncio.Queue[SSEEvent],
    ) -> tuple[Literal["event", "keepalive", "disconnect"], SSEEvent | None]:
        try:
            return "event", await asyncio.wait_for(
                subscription.get(), timeout=EVENT_POLL_TIMEOUT
            )
        except asyncio.TimeoutError:
            if await request.is_disconnected():
                return "disconnect", None
            return "keepalive", None

    def _encode_event(self, event: SSEEvent) -> str:
        payload = json.dumps(to_jsonable(event.data))
        return f"event: {event.event}\ndata: {payload}\n\n"

    def _json_error(self, message: str, *, status_code: int) -> JSONResponse:
        return JSONResponse({"error": message}, status_code=status_code)
