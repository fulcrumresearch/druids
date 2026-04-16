"""WebSocket server for agent communication."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import websockets

from druids.types import ToolCallError, to_jsonable

if TYPE_CHECKING:
    from druids.log import Log
    from druids.runtime import Runtime


class Server:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.server_instance: websockets.WebSocketServer | None = None
        self._port: int | None = None

    @property
    def port(self) -> int:
        if self._port is None:
            raise RuntimeError("Server is not running")
        return self._port

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.server_instance = await websockets.serve(self._handle, host, port)
        self._port = self.server_instance.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server_instance is not None:
            self.server_instance.close()
            await self.server_instance.wait_closed()
            self.server_instance = None
            self._port = None

    async def _handle(self, ws: Any) -> None:
        path = ws.request.path
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "agents" or parts[2] != "ws":
            await ws.close(4000, "invalid path")
            return

        agent_id = parts[1]
        try:
            rec = self.runtime.get_record(agent_id)
        except ToolCallError:
            await ws.close(4001, f"unknown agent: {agent_id}")
            return

        log = rec.log
        log.on_push = lambda entry: ws.send(entry.to_json())
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send(json.dumps({"error": "invalid JSON"}))
                    continue
                await self._dispatch(ws, agent_id, log, msg)
        finally:
            log.on_push = None
            log.emit("disconnected", origin="agent")

    async def _dispatch(
        self, ws: Any, agent_id: str, log: Log, msg: dict[str, Any]
    ) -> None:
        msg_type = msg.get("type")

        if msg_type == "sync":
            entries = log.after(msg.get("after", 0))
            await log.push_all(entries)

        elif msg_type == "event":
            event_type = msg.get("event_type", "")
            data = msg.get("data", {})
            await self._handle_event(agent_id, log, event_type, data)

        else:
            await ws.send(json.dumps({"error": f"unknown message type: {msg_type}"}))

    async def _handle_event(
        self, agent_id: str, log: Log, event_type: str, data: dict[str, Any]
    ) -> None:
        if event_type == "register":
            entry = log.emit("register", data, origin="agent")
            if entry is not None:
                await log.push(entry)
            try:
                tools = self.runtime.register_agent(
                    agent_id, data.get("execution_id", "")
                )
                result_entry = log.emit(
                    "registered", {"tools": to_jsonable(tools)}
                )
                if result_entry is not None:
                    await log.push(result_entry)
            except ToolCallError as exc:
                err_entry = log.emit("error", {"error": str(exc)})
                if err_entry is not None:
                    await log.push(err_entry)

        elif event_type == "tool_call":
            entry = log.emit("tool_call", data, origin="agent")
            if entry is not None:
                await log.push(entry)

            call_id = data.get("call_id", "")
            tool_name = data.get("tool", "")
            params = data.get("params", {})

            try:
                result = await self.runtime.handle_tool_call(
                    agent_id, tool_name, params
                )
                result_entry = log.emit(
                    "tool_result",
                    {"call_id": call_id, "result": to_jsonable(result)},
                )
                if result_entry is not None:
                    await log.push(result_entry)
            except Exception as exc:
                err_entry = log.emit(
                    "tool_result",
                    {"call_id": call_id, "error": str(exc)},
                )
                if err_entry is not None:
                    await log.push(err_entry)

        else:
            entry = log.emit(event_type, data, origin="agent")
            if entry is not None:
                await log.push(entry)
