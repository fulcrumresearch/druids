"""Connection and reverse bridge relay transport."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from acp.connection import Connection
from acp.helpers import text_block
from acp.schema import (
    AllowedOutcome,
    HttpHeader,
    HttpMcpServer,
    InitializeRequest,
    NewSessionRequest,
    PromptRequest,
    RequestPermissionResponse,
)


logger = logging.getLogger(__name__)

BRIDGE_CONNECT_TIMEOUT_SECONDS = 120.0


def _log_task_exception(task: asyncio.Task) -> None:
    """Log exceptions from fire-and-forget tasks so they are not silently lost."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("Background prompt task failed: %s", exc, exc_info=exc)


@dataclass
class BridgeRelaySession:
    """Shared queues and state for one reverse bridge connection."""

    bridge_id: str
    token: str
    incoming: asyncio.Queue[bytes] = field(default_factory=asyncio.Queue)
    outgoing: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    connected: asyncio.Event = field(default_factory=asyncio.Event)


class BridgeRelayHub:
    """In-memory reverse relay registry and queues."""

    def __init__(self) -> None:
        self._sessions: dict[str, BridgeRelaySession] = {}
        self._lock = asyncio.Lock()

    async def register(self, bridge_id: str, token: str) -> None:
        async with self._lock:
            old = self._sessions.get(bridge_id)
            if old:
                await old.incoming.put(None)
            self._sessions[bridge_id] = BridgeRelaySession(bridge_id=bridge_id, token=token)

    async def unregister(self, bridge_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(bridge_id, None)
        if session:
            await session.incoming.put(None)

    def is_valid_token(self, bridge_id: str, token: str) -> bool:
        session = self._sessions.get(bridge_id)
        return bool(session and session.token == token)

    def _get_session(self, bridge_id: str) -> BridgeRelaySession:
        """Look up a relay session by bridge_id, raising if not found."""
        session = self._sessions.get(bridge_id)
        if not session:
            raise ConnectionError(f"Unknown bridge_id: {bridge_id}")
        return session

    async def mark_connected(self, bridge_id: str) -> None:
        session = self._sessions.get(bridge_id)
        if session:
            session.connected.set()

    async def wait_connected(self, bridge_id: str, timeout_seconds: float) -> None:
        session = self._get_session(bridge_id)
        await asyncio.wait_for(session.connected.wait(), timeout=timeout_seconds)

    async def queue_input(self, bridge_id: str, data: str) -> None:
        session = self._get_session(bridge_id)
        await session.outgoing.put(data)

    async def pull_input(self, bridge_id: str, max_items: int, timeout_seconds: float) -> list[str]:
        session = self._get_session(bridge_id)
        items: list[str] = []
        try:
            first = await asyncio.wait_for(session.outgoing.get(), timeout=timeout_seconds)
            items.append(first)
        except asyncio.TimeoutError:
            return []

        while len(items) < max_items:
            try:
                items.append(session.outgoing.get_nowait())
            except asyncio.QueueEmpty:
                break
        return items

    async def push_output(self, bridge_id: str, messages: list[str]) -> None:
        session = self._get_session(bridge_id)
        for msg in messages:
            text = msg if msg.endswith("\n") else f"{msg}\n"
            await session.incoming.put(text.encode())

    async def read_output(self, bridge_id: str) -> bytes:
        session = self._get_session(bridge_id)
        data = await session.incoming.get()
        if data is None:
            return b""
        return data


bridge_relay_hub = BridgeRelayHub()


class BridgeRelayWriter:
    """Sends ACP JSON-RPC lines to the bridge via relay."""

    def __init__(self, bridge_id: str):
        self._bridge_id = bridge_id
        self._pending = b""

    def write(self, data: bytes) -> None:
        self._pending += data

    async def drain(self) -> None:
        if not self._pending:
            return
        data = self._pending.decode()
        self._pending = b""
        await bridge_relay_hub.queue_input(self._bridge_id, data)


class BridgeRelayReader:
    """Reads ACP JSON-RPC lines from bridge stdout relay."""

    def __init__(self, bridge_id: str):
        self._bridge_id = bridge_id

    def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def readline(self) -> bytes:
        try:
            return await bridge_relay_hub.read_output(self._bridge_id)
        except ConnectionError:
            return b""


@dataclass
class AgentConnection:
    """Wraps ACP SDK Connection over reverse bridge relay queues."""

    bridge_id: str
    bridge_token: str
    session_id: str = field(default="", init=False)
    connection: Connection = field(init=False)
    _reader: BridgeRelayReader = field(init=False)
    _writer: BridgeRelayWriter = field(init=False)
    _handlers: dict[str, list[Callable[..., Awaitable[Any]]]] = field(default_factory=dict, init=False)

    def __post_init__(self):
        if not self.bridge_token:
            raise ValueError("bridge_token is required")
        self._reader = BridgeRelayReader(self.bridge_id)
        self._writer = BridgeRelayWriter(self.bridge_id)
        self.connection = Connection(self._dispatch_method, self._writer, self._reader)
        self._reader.start()
        self.on("session/request_permission", self._handle_permission_request)

    async def _handle_permission_request(self, params: dict) -> dict:
        """Auto-approve all tool permission requests."""
        options = params.get("options", [])
        tool_call = params.get("toolCall", {})
        tool_name = tool_call.get("name", "unknown")

        allow_option_id = None
        for opt in options:
            if opt.get("kind") == "allow" or "allow" in opt.get("name", "").lower():
                allow_option_id = opt.get("optionId")
                break

        if not allow_option_id and options:
            allow_option_id = options[0].get("optionId")

        if allow_option_id:
            logger.info("Auto-approving tool: %s", tool_name)
            response = RequestPermissionResponse(outcome=AllowedOutcome(optionId=allow_option_id, outcome="selected"))
            return response.model_dump(by_alias=True)

        logger.warning("No options to approve for tool: %s", tool_name)
        return {"_meta": None, "outcome": {"outcome": "cancelled"}}

    async def _dispatch_method(self, method: str, params: Any | None, is_notification: bool) -> Any | None:
        """Dispatch incoming methods to registered handlers."""
        logger.debug("Dispatch: %s (notification=%s)", method, is_notification)
        handlers = self._handlers.get(method, [])
        result = None
        for handler in handlers:
            result = await handler(params)
        return result

    async def start(self, auth_method: str | None = None) -> None:
        """Start the connection and initialize ACP."""
        await bridge_relay_hub.register(self.bridge_id, self.bridge_token)
        try:
            try:
                await bridge_relay_hub.wait_connected(self.bridge_id, BRIDGE_CONNECT_TIMEOUT_SECONDS)
            except asyncio.TimeoutError as e:
                raise ConnectionError(f"Bridge {self.bridge_id} did not connect within timeout") from e

            req = InitializeRequest(protocolVersion=1)
            await self.send_request("initialize", req.model_dump(by_alias=True))

            if auth_method:
                await self.send_request("authenticate", {"methodId": auth_method})
        except Exception:
            await bridge_relay_hub.unregister(self.bridge_id)
            raise

    async def new_session(
        self,
        cwd: str = "/home/agent",
        mcp_servers: list[dict] | None = None,
        system_prompt: str | None = None,
    ) -> str:
        """Create a new session. Returns session_id."""
        acp_mcp_servers = []
        if mcp_servers:
            for server in mcp_servers:
                headers = []
                if server.get("headers"):
                    for key, value in server["headers"].items():
                        headers.append(HttpHeader(name=key, value=value))
                acp_mcp_servers.append(
                    HttpMcpServer(
                        name=server["name"],
                        url=server["url"],
                        type="http",
                        headers=headers,
                    )
                )

        meta = None
        if system_prompt:
            meta = {"systemPrompt": {"append": system_prompt}}

        logger.info("Creating session with %d MCP servers: %s", len(acp_mcp_servers), [s.name for s in acp_mcp_servers])
        req = NewSessionRequest(cwd=cwd, mcpServers=acp_mcp_servers, field_meta=meta)
        req_dict = req.model_dump(by_alias=True)
        logger.info("session/new request: %s", list(req_dict.keys()))
        result = await self.send_request("session/new", req_dict)
        logger.info("session/new result: %s", list(result.keys()) if isinstance(result, dict) else result)
        if isinstance(result, dict) and "sessionId" in result:
            self.session_id = result["sessionId"]
        return self.session_id

    async def close(self) -> None:
        """Close the connection."""
        await self._reader.stop()
        await self.connection.close()
        await bridge_relay_hub.unregister(self.bridge_id)

    async def set_model(self, model_id: str) -> None:
        """Set the model for the current session."""
        if not self.session_id:
            raise RuntimeError("Cannot set model before session is created")
        await self.send_request(
            "session/set_model",
            {"sessionId": self.session_id, "modelId": model_id},
        )
        logger.info("Set model to %s for session %s", model_id, self.session_id)

    async def prompt(self, text: str) -> Any:
        """Send a prompt to the agent and wait for the response."""
        req = PromptRequest(
            sessionId=self.session_id,
            prompt=[text_block(text)],
        )
        return await self.send_request("session/prompt", req.model_dump(by_alias=True))

    async def prompt_nowait(self, text: str) -> None:
        """Send a prompt to the agent without waiting for the response."""
        task = asyncio.create_task(self.prompt(text))
        task.add_done_callback(_log_task_exception)

    async def cancel(self) -> None:
        """Cancel the agent's current operation."""
        if self.session_id:
            try:
                await self.send_notification("session/cancel", {"sessionId": self.session_id})
            except Exception:
                pass

    def on(self, method: str, handler: Callable) -> None:
        """Register a handler for a method (notification or request)."""
        if method not in self._handlers:
            self._handlers[method] = []
        self._handlers[method].append(handler)

    async def send_request(self, method: str, params: dict[str, Any]) -> Any:
        """Send a request and wait for response."""
        try:
            return await self.connection.send_request(method, params)
        except ConnectionError:
            raise
        except Exception as e:
            raise ConnectionError(f"Request {method} failed: {e}") from e

    async def send_notification(self, method: str, params: dict[str, Any]) -> None:
        """Send a notification (no response expected)."""
        await self.connection.send_notification(method, params)
