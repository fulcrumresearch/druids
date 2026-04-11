"""Per-agent MCP endpoint.

One MCP server backed by the SDK's StreamableHTTPSessionManager, mounted
at /amcp/. Agent identity comes from the Authorization JWT (already
contains execution_slug and agent_name) via a contextvar.
"""

from __future__ import annotations

import contextvars
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

import mcp.types as mcp_types
from mcp.server.lowlevel.server import Server as MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from druids_server.api.deps import get_executions_registry
from druids_server.utils.forwarding_tokens import validate_token


logger = logging.getLogger(__name__)

# Agent identity for the current request, set by middleware.
_caller: contextvars.ContextVar[dict | None] = contextvars.ContextVar("_caller", default=None)


def _get_execution():
    """Look up the execution and agent name from the current request context."""
    caller = _caller.get()
    if not caller:
        return None, None
    execs = get_executions_registry().get(caller["sub"], {})
    return execs.get(caller.get("execution_slug")), caller.get("agent_name")


# MCP server (singleton)

mcp_server = MCPServer("druids")


@mcp_server.list_tools()
async def handle_list_tools() -> list[mcp_types.Tool]:
    """Return tools for the calling agent."""
    ex, agent_name = _get_execution()
    if not ex or not agent_name:
        return []
    return [
        mcp_types.Tool(
            name=s["name"],
            description=s.get("description", ""),
            inputSchema=s.get("inputSchema", {"type": "object", "properties": {}}),
        )
        for s in await ex.list_tool_schemas(agent_name)
    ]


@mcp_server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any] | None = None) -> list[mcp_types.TextContent]:
    """Call a tool on behalf of the calling agent."""
    ex, agent_name = _get_execution()
    if not ex or not agent_name:
        return [mcp_types.TextContent(type="text", text="Error: agent not found")]
    try:
        result = await ex.call_tool(agent_name, name, arguments or {})
        text = json.dumps(result) if isinstance(result, (dict, list)) else str(result)
        return [mcp_types.TextContent(type="text", text=text)]
    except Exception as e:
        logger.exception("MCP tools/call '%s' failed for agent '%s'", name, agent_name)
        return [mcp_types.TextContent(type="text", text=f"Error: {e}")]


# Lifespan and ASGI app

_session_manager: StreamableHTTPSessionManager | None = None


@asynccontextmanager
async def agent_mcp_lifespan():
    """Run the MCP session manager. Use during app lifespan."""
    global _session_manager
    _session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        stateless=True,
        json_response=True,
    )
    async with _session_manager.run():
        yield
    _session_manager = None


def create_agent_mcp_app() -> ASGIApp:
    """ASGI app that extracts agent identity from JWT and forwards to the MCP session manager."""

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        if _session_manager is None:
            await Response("MCP not ready", status_code=503)(scope, receive, send)
            return

        # Extract agent identity from Authorization header
        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"").decode()
        if auth.startswith("Bearer "):
            try:
                _caller.set(validate_token(auth[7:]))
            except Exception:
                logger.warning("MCP auth failed: invalid or expired token")

        scope = dict(scope, path="/")
        await _session_manager.handle_request(scope, receive, send)

    return app
