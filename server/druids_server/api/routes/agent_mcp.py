"""Per-agent MCP endpoint.

Exposes druids tools (built-in + program-defined) as a streamable HTTP MCP
server scoped to a single execution + agent. The bridge connects to this
endpoint and surfaces the tools natively in the agent's tool palette.

Protocol: MCP streamable HTTP (JSON-RPC over HTTP POST).
Auth: agent JWT (same token used for other API calls).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from druids_server.api.deps import Caller, UserExecutions


logger = logging.getLogger(__name__)

router = APIRouter()

# MCP protocol version
MCP_PROTOCOL_VERSION = "2025-03-26"


def _jsonrpc_response(id: Any, result: Any) -> dict:
    """Build a JSON-RPC 2.0 success response."""
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _jsonrpc_error(id: Any, code: int, message: str) -> dict:
    """Build a JSON-RPC 2.0 error response."""
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


@router.post(
    "/executions/{slug}/agents/{agent_name}/mcp",
    tags=["executions"],
    include_in_schema=False,
)
async def agent_mcp_endpoint(
    slug: str,
    agent_name: str,
    request: Request,
    caller: Caller,
    executions: UserExecutions,
):
    """Streamable HTTP MCP endpoint for agent tools.

    Handles JSON-RPC requests: initialize, notifications/initialized,
    tools/list, and tools/call.
    """
    if caller.agent_name and (caller.execution_slug != slug or caller.agent_name != agent_name):
        raise HTTPException(403, "Agents can only access their own tools")

    ex = executions.get(slug)
    if not ex:
        raise HTTPException(404, f"Execution '{slug}' not found")
    if not ex.has_agent(agent_name):
        raise HTTPException(404, f"Agent '{agent_name}' not found")

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_jsonrpc_error(None, -32700, "Parse error: invalid JSON"))

    if not isinstance(body, dict):
        return JSONResponse(_jsonrpc_error(None, -32600, "Invalid request: expected JSON object"))

    method = body.get("method")
    params = body.get("params", {})
    request_id = body.get("id")

    # Notifications (no id) get an empty 202 response
    if request_id is None:
        return JSONResponse(status_code=202, content={})

    if method == "initialize":
        result = {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "druids", "version": "1.0.0"},
        }
        return JSONResponse(_jsonrpc_response(request_id, result))

    if method == "tools/list":
        schemas = await ex.list_tool_schemas(agent_name)
        tools = []
        for schema in schemas:
            tools.append(
                {
                    "name": schema["name"],
                    "description": schema.get("description", ""),
                    "inputSchema": schema.get("inputSchema", {"type": "object", "properties": {}}),
                }
            )
        return JSONResponse(_jsonrpc_response(request_id, {"tools": tools}))

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        try:
            result = await ex.call_tool(agent_name, tool_name, arguments)
            text = json.dumps(result) if isinstance(result, (dict, list)) else str(result)
            return JSONResponse(
                _jsonrpc_response(
                    request_id,
                    {
                        "content": [{"type": "text", "text": text}],
                    },
                )
            )
        except Exception as e:
            logger.exception("MCP tools/call '%s' failed for agent '%s'", tool_name, agent_name)
            return JSONResponse(
                _jsonrpc_response(
                    request_id,
                    {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True,
                    },
                )
            )

    return JSONResponse(_jsonrpc_error(request_id, -32601, f"Method not found: {method}"))
