"""Execution runtime -- runs user programs in a sandbox.

This script is deployed to a lightweight sandbox VM and launched by the
server when an execution starts. It mirrors the Execution ctx API that
programs expect, translating method calls into HTTP requests back to the
server.

The runtime:
1. Reads config (program source, server URL, auth token, args)
2. exec()s the program source to get the `program` function
3. Calls `await program(ctx, **args)` which registers agents and handlers
4. Starts a Starlette server on localhost:9100 to receive relayed tool calls
   and serve tool/event listings
5. Waits until killed (server stops the sandbox on execution completion)

Communication:
- Outgoing (runtime -> server): httpx POST to /api/executions/{slug}/...
- Incoming (server -> runtime): server runs sandbox.exec("curl localhost:9100/...")
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("runtime")

RUNTIME_PORT = 9100


# ---------------------------------------------------------------------------
# RuntimeAgent -- mirrors Agent public API
# ---------------------------------------------------------------------------


@dataclass
class RuntimeAgent:
    """Client-side proxy for an agent. Method calls become HTTP requests to the server."""

    name: str
    _ctx: RuntimeContext
    _handlers: dict[str, Callable] = field(default_factory=dict)
    _ready: asyncio.Task | None = field(default=None, repr=False)

    async def _await_ready(self) -> None:
        """Wait for the server-side provisioning to complete."""
        if self._ready:
            await self._ready

    def on(self, tool_name: str) -> Callable:
        """Register a tool handler for this agent.

        If the handler function has a `caller` parameter, the runtime
        automatically injects the RuntimeAgent that invoked the tool.
        """

        def decorator(fn: Callable) -> Callable:
            self._handlers[tool_name] = fn
            return fn

        return decorator

    async def send(self, message: str) -> None:
        """Send a message to this agent. Blocks until the agent is provisioned."""
        await self._await_ready()
        await self._ctx._post(f"/agents/{self.name}/message", {"text": message})

    async def exec(self, command: str, *, user: str = "agent", timeout: int | None = None) -> Any:
        """Run a command on this agent's VM. Blocks until provisioned."""
        await self._await_ready()
        resp = await self._ctx._remote_exec(self.name, command)
        return _ExecResult(exit_code=resp["exit_code"], stdout=resp["stdout"], stderr=resp["stderr"])

    async def expose(self, name: str, port: int) -> str:
        """Expose a port on this agent's VM as a public HTTPS URL."""
        await self._await_ready()
        resp = await self._ctx._post(f"/agents/{self.name}/expose", {"service_name": name, "port": port})
        return resp["url"]

    async def fork(
        self,
        name: str,
        *,
        prompt: str | None = None,
        system_prompt: str | None = None,
        model: str | None = None,
        git: str | None = None,
        context: bool = False,
    ) -> RuntimeAgent:
        """Fork this agent's VM (COW) and create a new agent on the clone."""
        await self._await_ready()
        resp = await self._ctx._post(
            f"/agents/{self.name}/fork",
            {
                "name": name,
                "prompt": prompt,
                "system_prompt": system_prompt,
                "model": model,
                "git": git,
                "context": context,
            },
        )
        agent = RuntimeAgent(name=resp["name"], _ctx=self._ctx)
        self._ctx._agents[resp["name"]] = agent
        return agent

    async def snapshot_machine(self, name: str | None = None) -> str:
        """Snapshot this agent's VM and register it as a new devbox.

        Returns the devbox name. The snapshot can be used as a devbox for
        future executions.
        """
        await self._await_ready()
        payload: dict = {}
        if name:
            payload["devbox_name"] = name
        resp = await self._ctx._post(f"/agents/{self.name}/snapshot", payload)
        return resp["devbox_name"]


@dataclass(frozen=True)
class _ExecResult:
    """Minimal exec result matching the interface programs expect."""

    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


# ---------------------------------------------------------------------------
# RuntimeContext -- mirrors Execution ctx API
# ---------------------------------------------------------------------------


@dataclass
class RuntimeContext:
    """Context object passed to programs as `ctx`. Translates API calls to HTTP."""

    slug: str
    repo_full_name: str | None = None
    spec: str | None = None

    _base_url: str = ""
    _token: str = ""
    _agents: dict[str, RuntimeAgent] = field(default_factory=dict)
    _client_handlers: dict[str, Callable] = field(default_factory=dict)
    _connections: set[str] = field(default_factory=set)
    _topology: set[tuple[str, str]] = field(default_factory=set)
    _server_started: bool = field(default=False, repr=False)
    _server_task: asyncio.Task | None = field(default=None, repr=False)

    @property
    def agents(self) -> dict[str, RuntimeAgent]:
        """Agent name -> RuntimeAgent mapping."""
        return dict(self._agents)

    @property
    def connections(self) -> set[str]:
        """Names of agents with active ACP connections, refreshed on each client event."""
        return set(self._connections)

    def connect(self, a: RuntimeAgent, b: RuntimeAgent, *, direction: str = "both") -> None:
        """Declare that agents can communicate.

        Args:
            a: First agent.
            b: Second agent.
            direction: "both" for bidirectional (default), "forward" for a->b only.
        """
        self._topology.add((a.name, b.name))
        if direction == "both":
            self._topology.add((b.name, a.name))

    def is_connected(self, sender: RuntimeAgent | str, receiver: RuntimeAgent | str) -> bool:
        """Check if sender is allowed to message receiver.

        Agents are isolated by default. Only pairs declared via
        `connect()` can communicate.
        """
        sender_name = sender.name if isinstance(sender, RuntimeAgent) else sender
        receiver_name = receiver.name if isinstance(receiver, RuntimeAgent) else receiver
        return (sender_name, receiver_name) in self._topology

    async def _request(self, method: str, path: str, data: dict | None = None) -> dict:
        """Make an HTTP request to an execution API endpoint on the server."""
        url = f"{self._base_url}/api/executions/{self.slug}{path}"
        async with httpx.AsyncClient(timeout=600) as client:
            resp = await client.request(
                method, url, json=data or {}, headers={"Authorization": f"Bearer {self._token}"}
            )
            resp.raise_for_status()
            return resp.json()

    async def _post(self, path: str, data: dict | None = None) -> dict:
        """POST to an execution API endpoint on the server."""
        return await self._request("POST", path, data)

    async def _remote_exec(self, agent_name: str, command: str) -> dict:
        """Run a command on an agent's VM via the driver remote-exec endpoint."""
        url = f"{self._base_url}/api/remote-exec"
        async with httpx.AsyncClient(timeout=600) as client:
            resp = await client.post(
                url,
                json={"execution_slug": self.slug, "agent_name": agent_name, "command": command},
                headers={"Authorization": f"Bearer {self._token}"},
            )
            resp.raise_for_status()
            return resp.json()

    async def agent(
        self,
        name: str,
        *,
        prompt: str | None = None,
        system_prompt: str | None = None,
        model: str = "claude",
        git: str | None = None,
        working_directory: str | None = None,
        share_machine_with: RuntimeAgent | None = None,
        mcp_servers: dict[str, Any] | None = None,
    ) -> RuntimeAgent:
        """Create an agent. Returns immediately; provisioning runs in the background.

        The returned agent's exec/send/expose methods block until provisioning
        completes. Internally, we POST to /agents in a background task; the
        server provisions inline and returns when the agent is ready.
        """
        share_name = share_machine_with.name if share_machine_with else None
        agent = RuntimeAgent(name=name, _ctx=self)
        self._agents[name] = agent

        async def _create_and_wait():
            if share_machine_with:
                await share_machine_with._await_ready()
            payload: dict[str, Any] = {"name": name}
            if prompt is not None:
                payload["prompt"] = prompt
            if system_prompt is not None:
                payload["system_prompt"] = system_prompt
            if model != "claude":
                payload["model"] = model
            if git is not None:
                payload["git"] = git
            if working_directory is not None:
                payload["working_directory"] = working_directory
            if share_name is not None:
                payload["share_machine_with"] = share_name
            if mcp_servers is not None:
                payload["mcp_servers"] = mcp_servers
            await self._post("/agents", payload)

        agent._ready = asyncio.create_task(_create_and_wait())
        self._ensure_server()
        return agent

    def _ensure_server(self) -> None:
        """Start the HTTP server in the background if not already running.

        Called on first agent creation so that tool call relays from the server
        can reach us before wait() is called. Without this, agents that connect
        fast make tool calls that fail because localhost:9100 isn't listening.
        """
        if self._server_started:
            return
        self._server_started = True
        app = _build_app(self)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=RUNTIME_PORT, log_level="warning"))
        self._server_task = asyncio.get_event_loop().create_task(server.serve())
        logger.info("Runtime HTTP server started eagerly on 127.0.0.1:%d", RUNTIME_PORT)

    def on_client_event(self, event_name: str) -> Callable:
        """Register a handler for client events."""

        def decorator(fn: Callable) -> Callable:
            self._client_handlers[event_name] = fn
            return fn

        return decorator

    async def emit(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Emit an event to connected clients."""
        await self._post("/emit", {"event": event, "data": data})

    async def done(self, result: Any = None) -> None:
        """Signal successful completion."""
        await self._request("PATCH", "", {"status": "completed", "result": result})

    async def fail(self, reason: str) -> None:
        """Signal failure."""
        await self._request("PATCH", "", {"status": "failed", "reason": reason})

    async def wait(self) -> None:
        """Signal readiness and block until stopped.

        The HTTP server is started eagerly on first agent creation (see
        _ensure_server) so tool call relays work during the program's setup
        phases. This method pushes edge topology, registers client event
        names with the server, and then blocks on the already-running server
        task.
        """
        self._ensure_server()
        if self._topology:
            edges = [{"from": a, "to": b} for a, b in self._topology]
            await self._post("/edges", {"edges": edges})
        await self._post(
            "/ready",
            {
                "client_events": list(self._client_handlers.keys()),
                "agent_order": list(self._agents.keys()),
            },
        )
        logger.info("Runtime signaled ready, blocking on server task")
        if self._server_task:
            await self._server_task

    async def _handle_message(self, sender_name: str, args: dict[str, Any]) -> str:
        """Handle the built-in message tool with topology enforcement.

        Checks whether the sender is allowed to message the receiver
        according to the declared topology. If allowed, forwards the
        message to the server for delivery.
        """
        receiver = args.get("receiver", "")
        message = args.get("message", "")
        if receiver not in self._agents:
            return f"Agent '{receiver}' not found. Available: {', '.join(self._agents.keys())}"
        if not self.is_connected(sender_name, receiver):
            reachable = [n for n in self._agents if n != sender_name and self.is_connected(sender_name, n)]
            return f"Agent '{receiver}' not found. Available: {', '.join(reachable)}"
        await self._post("/send", {"sender": sender_name, "receiver": receiver, "text": message})
        return f"Message sent to {receiver}."

    async def _handle_tool_call(self, agent_name: str, tool_name: str, args: dict[str, Any]) -> Any:
        """Invoke a registered tool handler."""
        agent = self._agents.get(agent_name)
        if not agent:
            raise ValueError(f"Unknown agent: {agent_name}")

        # Built-in tools routed through runtime for topology enforcement
        if tool_name == "message":
            return await self._handle_message(agent_name, args)
        if tool_name == "list_agents":
            reachable = [n for n in self._agents if n != agent_name and self.is_connected(agent_name, n)]
            return ", ".join(reachable) if reachable else "No reachable agents."

        handler = agent._handlers.get(tool_name)
        if not handler:
            raise ValueError(f"No handler for tool '{tool_name}' on agent '{agent_name}'")
        if "caller" in inspect.signature(handler).parameters:
            args = {**args, "caller": agent}
        result = handler(**args)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    async def _refresh_connections(self) -> None:
        """Fetch current connection state from the server."""
        try:
            resp = await self._request("GET", "")
            self._connections = set(resp.get("connections", []))
        except Exception:
            logger.debug("Failed to refresh connections", exc_info=True)

    async def _handle_client_event(self, event_name: str, data: dict[str, Any]) -> Any:
        """Invoke a registered client event handler."""
        handler = self._client_handlers.get(event_name)
        if not handler:
            raise ValueError(f"No handler for client event '{event_name}'")
        await self._refresh_connections()
        result = handler(**data)
        if asyncio.iscoroutine(result):
            result = await result
        return result


# ---------------------------------------------------------------------------
# Tool schema extraction
# ---------------------------------------------------------------------------


_TYPE_TO_JSON: dict[str, str] = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
}


def _annotation_to_json_type(annotation: Any) -> str:
    """Convert a Python type annotation to a JSON Schema type string.

    Handles both resolved types (str, int) and string annotations ("str", "int")
    produced by `from __future__ import annotations`.
    """
    if annotation is inspect.Parameter.empty:
        return "string"
    name = annotation.__name__ if isinstance(annotation, type) else str(annotation)
    return _TYPE_TO_JSON.get(name, "string")


def _extract_tool_schema(tool_name: str, handler: Callable) -> dict:
    """Extract an MCP-compatible tool schema from a handler function.

    Inspects the function signature for parameter names, type annotations,
    and defaults. Uses the docstring as the tool description.
    """
    sig = inspect.signature(handler)
    description = inspect.getdoc(handler) or ""
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue
        json_type = _annotation_to_json_type(param.annotation)
        properties[param_name] = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return {
        "name": tool_name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def _extract_agent_tool_schemas(agent: RuntimeAgent) -> list[dict]:
    """Extract schemas for all tools registered on an agent."""
    return [_extract_tool_schema(name, handler) for name, handler in agent._handlers.items()]


# ---------------------------------------------------------------------------
# HTTP server (receives tool calls and serves listings to the druids server)
# ---------------------------------------------------------------------------


def _build_app(ctx: RuntimeContext) -> Starlette:
    """Build the Starlette app that receives relayed tool calls."""

    async def call_tool(request: Request) -> JSONResponse:
        data = await request.json()
        try:
            result = await ctx._handle_tool_call(data["agent_name"], data["tool_name"], data.get("args", {}))
            return JSONResponse({"result": result})
        except Exception as e:
            logger.exception("Tool call error")
            return JSONResponse({"error": str(e)}, status_code=500)

    async def handle_event(request: Request) -> JSONResponse:
        data = await request.json()
        try:
            result = await ctx._handle_client_event(data["event"], data.get("data", {}))
            return JSONResponse({"result": result})
        except Exception as e:
            logger.exception("Client event error")
            return JSONResponse({"error": str(e)}, status_code=500)

    async def list_tools(request: Request) -> JSONResponse:
        """Return tool names and schemas for an agent."""
        agent_name = request.query_params.get("agent")
        agent = ctx._agents.get(agent_name or "")
        if not agent:
            return JSONResponse({"tools": [], "schemas": []})
        return JSONResponse(
            {
                "tools": list(agent._handlers.keys()),
                "schemas": _extract_agent_tool_schemas(agent),
            }
        )

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return Starlette(
        routes=[
            Route("/call", call_tool, methods=["POST"]),
            Route("/event", handle_event, methods=["POST"]),
            Route("/tools", list_tools, methods=["GET"]),
            Route("/health", health, methods=["GET"]),
        ]
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    config_path = sys.argv[1]
    with open(config_path) as f:
        config = json.load(f)

    ctx = RuntimeContext(
        slug=config["slug"],
        repo_full_name=config.get("repo_full_name"),
        spec=config.get("spec"),
        _base_url=config["base_url"],
        _token=config["token"],
    )

    # Execute program source to get the `program` function
    namespace = {}
    exec(config["program_source"], namespace)  # noqa: S102
    program_fn = namespace.get("program")
    if not callable(program_fn):
        logger.error("Program source does not define a callable 'program'")
        await ctx._request(
            "PATCH", "", {"status": "failed", "reason": "Program source does not define a callable 'program'"}
        )
        return

    # Run the program (registers agents and handlers)
    args = config.get("args", {})
    try:
        await program_fn(ctx, **args)
    except Exception as e:
        logger.exception("Program function raised an exception")
        await ctx._request("PATCH", "", {"status": "failed", "reason": f"Program error: {e}"})
        return

    # Signal readiness then keep the HTTP server alive until the execution stops.
    # _ensure_server() may have already started the server eagerly (when ctx.agent()
    # was called during program setup), in which case we just wait on that task
    # instead of creating a second server on the same port.
    # Push edge topology so the graph view can render connections
    if ctx._topology:
        edges = [{"from": a, "to": b} for a, b in ctx._topology]
        await ctx._post("/edges", {"edges": edges})

    await ctx._post(
        "/ready",
        {
            "client_events": list(ctx._client_handlers.keys()),
            "agent_order": list(ctx._agents.keys()),
        },
    )
    if ctx._server_task is not None:
        logger.info("Runtime HTTP server already running, waiting on existing task")
        await ctx._server_task
    else:
        app = _build_app(ctx)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=RUNTIME_PORT, log_level="warning"))
        logger.info("Runtime HTTP server listening on 127.0.0.1:%d", RUNTIME_PORT)
        await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
