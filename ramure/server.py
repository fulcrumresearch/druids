"""WebSocket server for agent communication.

Wire protocol (see spec-replication.md):

- Client -> server: ``{type:"event", event_type, data}`` or
  ``{type:"sync", after:lastSeq}``.
- Server -> client: a ``LogEntry`` encoded as JSON.

The server is the sole writer of the log. Every client event is appended
via ``log.emit`` (which assigns ``seq``, persists, and schedules delivery
to all subscribers) and then reacted to. Reactions may append further
entries via the same ``log.emit`` primitive.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import websockets

from ramure.types import ToolCallError, to_jsonable

if TYPE_CHECKING:
    from ramure.log import Log, LogEntry
    from ramure.runtime import Runtime

Send = Callable[["LogEntry"], Awaitable[None]]


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
            ag = self.runtime.get_agent(agent_id)
        except ToolCallError:
            await ws.close(4001, f"unknown agent: {agent_id}")
            return

        log = ag.log

        async def send(entry: LogEntry) -> None:
            await ws.send(entry.to_json())

        unsubscribe = log.subscribe(send)
        # Only the connection that registers as the agent represents the
        # agent's liveness. Other clients on this endpoint (log viewers,
        # sync-only probes) may come and go freely.
        registered_here = False
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    # Malformed frame: ignore. All communication happens via
                    # log entries; there is no separate error channel.
                    continue
                if (
                    msg.get("type") == "event"
                    and msg.get("event_type") == "register"
                ):
                    registered_here = True
                await self._dispatch(agent_id, log, send, msg)
        finally:
            unsubscribe()
            log.emit("disconnected", {}, origin="agent")
            if registered_here:
                self.runtime.agent_disconnected(agent_id)

    async def _dispatch(
        self, agent_id: str, log: Log, send: Send, msg: dict[str, Any]
    ) -> None:
        msg_type = msg.get("type")

        if msg_type == "sync":
            after = int(msg.get("after", 0) or 0)
            for entry in log.after(after):
                try:
                    await send(entry)
                except Exception:
                    # Send failed: the connection will be torn down by
                    # the websocket layer; nothing else to do here.
                    return
            return

        if msg_type == "event":
            event_type = str(msg.get("event_type", ""))
            data = msg.get("data", {}) or {}
            entry = log.emit(event_type, data, origin="agent")
            if entry is not None:
                await self._react(agent_id, log, entry)

    # -- reactions: the only place derived entries are produced --

    async def _react(self, agent_id: str, log: Log, entry: LogEntry) -> None:
        if entry.type == "usage":
            if self.runtime.log is not None:
                self.runtime.log.emit("usage", {"agent": agent_id, **entry.data})
            return

        if entry.type == "register":
            try:
                tools = self.runtime.register_agent(
                    agent_id, entry.data.get("execution_id", "")
                )
                log.emit("registered", {"tools": to_jsonable(tools)})
            except ToolCallError as exc:
                log.emit("error", {"error": str(exc)})
            return

        if entry.type == "tool_call":
            call_id = entry.data.get("call_id", "")
            tool_name = entry.data.get("tool", "")
            params = entry.data.get("params", {}) or {}
            try:
                result = await self.runtime.handle_tool_call(
                    agent_id, tool_name, params
                )
                log.emit(
                    "tool_result",
                    {"call_id": call_id, "result": to_jsonable(result)},
                )
            except Exception as exc:
                log.emit(
                    "tool_result",
                    {"call_id": call_id, "error": str(exc)},
                )
            return

        # Other event types are logged but produce no derived entries.
