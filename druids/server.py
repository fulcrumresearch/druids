"""WebSocket server for agent communication."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import websockets

from druids.types import ToolCallError, to_jsonable

if TYPE_CHECKING:
    from druids.runtime import AgentRecord, Runtime


class Server:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self._server: websockets.WebSocketServer | None = None
        self._port: int | None = None

    @property
    def port(self) -> int:
        if self._port is None:
            raise RuntimeError("Server is not running")
        return self._port

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._server = await websockets.serve(self._handle, host, port)
        self._port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            self._port = None

    async def _handle(self, ws: Any) -> None:
        path = ws.request.path
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "agents" or parts[2] != "ws":
            await ws.close(4000, "invalid path")
            return

        agent_id = parts[1]
        try:
            rec = self.runtime._get_record(agent_id)
        except ToolCallError:
            await ws.close(4001, f"unknown agent: {agent_id}")
            return

        rec._notify = lambda entry: ws.send(entry.to_json())
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send(json.dumps({"error": "invalid JSON"}))
                    continue
                await self._dispatch(ws, agent_id, rec, msg)
        finally:
            rec._notify = None
            rec.log.append("disconnected", "agent")

    async def _dispatch(
        self, ws: Any, agent_id: str, rec: AgentRecord, msg: dict[str, Any]
    ) -> None:
        msg_type = msg.get("type")

        if msg_type == "sync":
            entries = rec.log.entries_after(msg.get("after", 0))
            await rec.push_entries(entries)

        elif msg_type == "event":
            event_type = msg.get("event_type", "")
            data = msg.get("data", {})
            await self._handle_event(agent_id, rec, event_type, data)

        else:
            await ws.send(json.dumps({"error": f"unknown message type: {msg_type}"}))

    async def _handle_event(
        self, agent_id: str, rec: AgentRecord, event_type: str, data: dict[str, Any]
    ) -> None:
        if event_type == "register":
            entry = rec.log.append("register", "agent", data)
            await rec.push(entry)
            try:
                tools = self.runtime._register_agent(
                    agent_id, data.get("execution_id", "")
                )
                result_entry = rec.log.append(
                    "registered", "server", {"tools": to_jsonable(tools)}
                )
                await rec.push(result_entry)
            except ToolCallError as exc:
                err_entry = rec.log.append(
                    "error", "server", {"error": str(exc)}
                )
                await rec.push(err_entry)

        elif event_type == "tool_call":
            entry = rec.log.append("tool_call", "agent", data)
            await rec.push(entry)

            call_id = data.get("call_id", "")
            tool_name = data.get("tool", "")
            params = data.get("params", {})

            try:
                result = await self.runtime._handle_tool_call_request(
                    agent_id, tool_name, params
                )
                result_entry = rec.log.append(
                    "tool_result",
                    "server",
                    {"call_id": call_id, "result": to_jsonable(result)},
                )
                await rec.push(result_entry)
            except Exception as exc:
                err_entry = rec.log.append(
                    "tool_result",
                    "server",
                    {"call_id": call_id, "error": str(exc)},
                )
                await rec.push(err_entry)

        else:
            entry = rec.log.append(event_type, "agent", data)
            await rec.push(entry)
