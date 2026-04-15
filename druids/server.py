"""Orchestrator HTTP + WebSocket server."""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from druids.types import ToolCallError, to_jsonable

if TYPE_CHECKING:
    from druids.runtime import Runtime


STARTUP_TIMEOUT = 10
SHUTDOWN_TIMEOUT = 10


class OrchestratorServer:
    """Async in-process HTTP + WebSocket server for agent communication."""

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

    @property
    def port(self) -> int:
        return self._actual_port or self._port

    async def start(self) -> None:
        app = _OrchestratorAPI(self.runtime).app()
        sock = self._open_socket()

        self._socket = sock
        self._actual_port = sock.getsockname()[1]
        self._server = uvicorn.Server(self._build_config(app))
        self._server.install_signal_handlers = lambda: None
        self._task = asyncio.create_task(self._server.serve(sockets=[sock]))

        await self._wait_until_started()

    async def stop(self) -> None:
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

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/health", self.health),
                WebSocketRoute("/agents/{agent_id}/ws", self.agent_ws),
            ],
        )

    async def health(self, request: Request) -> JSONResponse:
        del request
        return JSONResponse({"status": "ok"})

    async def agent_ws(self, ws: WebSocket) -> None:
        agent_id = ws.path_params["agent_id"]
        await ws.accept()

        log = self.runtime._get_event_log(agent_id)
        log.set_ws(ws)

        try:
            await self._ws_loop(ws, agent_id, log)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            log.clear_ws()
            # Log disconnection
            log.append("disconnected", "agent")

    async def _ws_loop(
        self,
        ws: WebSocket,
        agent_id: str,
        log: Any,
    ) -> None:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"error": "invalid JSON"})
                continue

            msg_type = msg.get("type")

            if msg_type == "sync":
                after = msg.get("after", 0)
                entries = log.entries_after(after)
                await log.push_entries(entries)

            elif msg_type == "event":
                event_type = msg.get("event_type", "")
                data = msg.get("data", {})
                await self._handle_agent_event(ws, agent_id, log, event_type, data)

            else:
                await ws.send_json({"error": f"unknown message type: {msg_type}"})

    async def _handle_agent_event(
        self,
        ws: WebSocket,
        agent_id: str,
        log: Any,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        if event_type == "register":
            # Agent is registering
            entry = log.append("register", "agent", data)
            await log.push(entry)

            try:
                tools = self.runtime._register_agent(
                    agent_id, data.get("execution_id", "")
                )
                result_entry = log.append(
                    "registered", "server", {"tools": to_jsonable(tools)}
                )
                await log.push(result_entry)
            except ToolCallError as exc:
                err_entry = log.append(
                    "error", "server", {"error": str(exc)}
                )
                await log.push(err_entry)

        elif event_type == "tool_call":
            # Agent is calling a druids tool
            entry = log.append("tool_call", "agent", data)
            await log.push(entry)

            call_id = data.get("call_id", "")
            tool_name = data.get("tool", "")
            params = data.get("params", {})

            try:
                result = await self.runtime._handle_tool_call_request(
                    agent_id, tool_name, params
                )
                result_entry = log.append(
                    "tool_result",
                    "server",
                    {"call_id": call_id, "result": to_jsonable(result)},
                )
                await log.push(result_entry)
            except ToolCallError as exc:
                err_entry = log.append(
                    "tool_result",
                    "server",
                    {"call_id": call_id, "error": str(exc)},
                )
                await log.push(err_entry)
            except Exception as exc:
                err_entry = log.append(
                    "tool_result",
                    "server",
                    {"call_id": call_id, "error": str(exc)},
                )
                await log.push(err_entry)

        else:
            # Informational agent event (pi activity, etc.) — just log it
            entry = log.append(event_type, "agent", data)
            await log.push(entry)
