"""MCP routes for driver communication.

These endpoints are exposed via MCP so the driver (CLI, external agents) can call them as tools.
All endpoints require a driver token (no agents).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from druids_server.api.deps import Caller, UserExecutions, require_driver
from druids_server.api.helpers.sandbox import InstanceNotFound, resolve_sandbox
from druids_server.api.routes.executions import _get_runtime_execution


router = APIRouter(dependencies=[Depends(require_driver)])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class SendMessageRequest(BaseModel):
    execution_slug: str
    sender: str
    receiver: str  # name of receiver program
    message: str


class RemoteExecRequest(BaseModel):
    repo: str | None = None
    execution_slug: str | None = None
    agent_name: str | None = None
    command: str


class StopAgentRequest(BaseModel):
    execution_slug: str
    agent_name: str


class GetSSHRequest(BaseModel):
    execution_slug: str
    agent_name: str


class SendClientEventRequest(BaseModel):
    execution_slug: str
    event: str
    data: dict[str, object] = {}


# ---------------------------------------------------------------------------
# MCP endpoints (driver-callable)
# ---------------------------------------------------------------------------


@router.post("/messages/send", tags=["mcp-driver"], operation_id="send_message")
async def send_message(request: SendMessageRequest, caller: Caller, executions: UserExecutions):
    """Send message between programs. Use sender="driver" for external callers."""
    ex = _get_runtime_execution(executions, request.execution_slug)

    if request.sender != "driver" and not ex.has_agent(request.sender):
        raise HTTPException(404, f"Sender {request.sender} not found")

    if not ex.has_agent(request.receiver):
        raise HTTPException(404, f"Receiver {request.receiver} not found")

    await ex.send(request.sender, request.receiver, request.message)
    return {"status": "sent", "recipient": request.receiver}


@router.post("/agents/stop", tags=["mcp-driver"], operation_id="stop_agent")
async def stop_agent(request: StopAgentRequest, caller: Caller, executions: UserExecutions):
    """Stop an agent by name."""
    ex = _get_runtime_execution(executions, request.execution_slug)

    if not ex.has_agent(request.agent_name):
        raise HTTPException(404, f"Agent {request.agent_name} not found")

    await ex.shutdown_agent(request.agent_name)
    return {"status": "stopped", "agent_name": request.agent_name}


@router.post("/agents/ssh", tags=["mcp-driver"], operation_id="get_agent_ssh")
async def get_agent_ssh(request: GetSSHRequest, caller: Caller, executions: UserExecutions):
    """Get SSH credentials for an agent's VM."""
    ex = _get_runtime_execution(executions, request.execution_slug)

    agent = ex.agents.get(request.agent_name)
    if not agent:
        raise HTTPException(404, f"Agent {request.agent_name} not found")

    try:
        creds = await agent.machine.ssh_credentials()
    except Exception as e:
        logger.exception("Failed to retrieve SSH credentials for %s", request.agent_name)
        raise HTTPException(502, "Failed to retrieve SSH credentials") from e

    if not creds:
        raise HTTPException(501, "SSH access is not available for this sandbox backend")

    return {
        "host": creds.host,
        "port": creds.port,
        "username": creds.username,
        "private_key": creds.private_key,
        "password": creds.password,
    }


@router.post("/events/send", tags=["mcp-driver"], operation_id="send_client_event")
async def send_client_event(request: SendClientEventRequest, caller: Caller, executions: UserExecutions):
    """Send an event to a running program's client event handler.

    Programs register handlers via ``ctx.on_client_event("name")``. This
    endpoint dispatches the event and returns the handler's result.
    """
    ex = _get_runtime_execution(executions, request.execution_slug)

    available = ex.list_client_events()
    if request.event not in available:
        raise HTTPException(
            404,
            f"No handler for '{request.event}'. Available: {', '.join(available) or '(none)'}",
        )

    try:
        result = await asyncio.wait_for(
            ex.handle_client_event(request.event, request.data),
            timeout=30,
        )
    except TimeoutError:
        raise HTTPException(504, "Event handler did not respond within 30 seconds")
    except Exception:  # broad catch at API boundary
        logger.exception("Client event handler failed for '%s'", request.event)
        raise HTTPException(500, "Event handler failed")

    return {"result": result}


# ---------------------------------------------------------------------------
# Driver-only tools
# ---------------------------------------------------------------------------


@router.post("/remote-exec", tags=["mcp-driver"], operation_id="remote_exec")
async def remote_exec(request: RemoteExecRequest, caller: Caller, executions: UserExecutions):
    """Run a command on a devbox or agent VM. Returns stdout, stderr, and exit code."""
    try:
        sandbox = await resolve_sandbox(
            caller.user,
            executions,
            repo=request.repo,
            execution_slug=request.execution_slug,
            agent_name=request.agent_name,
        )
    except InstanceNotFound as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    try:
        result = await sandbox.exec(request.command)
    except Exception:
        logger.exception("Failed to execute command on sandbox %s", sandbox.instance_id)
        raise HTTPException(502, "Instance unavailable. It may have been stopped.")

    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
    }
