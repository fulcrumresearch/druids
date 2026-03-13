"""Runtime and agent-facing execution endpoints.

These endpoints are called from inside the sandbox by the execution runtime
(druids_runtime) or by agents via the druids CLI. They manage agent
lifecycle, port exposure, tool registration, and event emission.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from druids_server.api.deps import Caller, UserExecutions, require_driver
from druids_server.api.routes.executions import _get_runtime_agent, _get_runtime_execution
from druids_server.db.models.devbox import Devbox, get_devbox_by_name
from druids_server.db.session import get_session
from druids_server.lib.agents.config import AgentConfig
from druids_server.utils import execution_trace


logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateAgentRequest(AgentConfig):
    """API request to create an agent. Extends `AgentConfig` with provisioning fields."""

    share_machine_with: str | None = None  # agent name, resolved server-side


class ExposePortRequest(BaseModel):
    service_name: str = "default"
    port: int


class RuntimeReadyRequest(BaseModel):
    client_events: list[str] = Field(default_factory=list)


class SendMessageRequest(BaseModel):
    sender: str
    receiver: str
    text: str


class EmitEventRequest(BaseModel):
    event: str
    data: dict[str, Any] | None = None


class SetEdgesRequest(BaseModel):
    edges: list[dict[str, str]]


class CallToolRequest(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)


class SnapshotAgentRequest(BaseModel):
    devbox_name: str | None = None  # optional name for the devbox (default: agent name)


# ---------------------------------------------------------------------------
# Agent lifecycle endpoints (called by the execution runtime)
# ---------------------------------------------------------------------------


@router.post("/executions/{slug}/agents", tags=["executions"], dependencies=[Depends(require_driver)])
async def create_agent(
    slug: str,
    request: CreateAgentRequest,
    caller: Caller,
    executions: UserExecutions,
):
    """Create an agent within a running execution.

    Provisions the VM, starts the bridge, and connects inline. Returns
    once the agent is fully ready.
    """
    ex = _get_runtime_execution(executions, slug)
    agent = await ex.provision_agent(
        name=request.name,
        agent_type=request.agent_type,
        model=request.model,
        prompt=request.prompt,
        system_prompt=request.system_prompt,
        git=request.git,
        working_directory=request.working_directory,
        share_machine_with=request.share_machine_with,
        mcp_servers=request.mcp_servers,
    )
    return {"name": agent.name}


@router.post("/executions/{slug}/agents/{name}/expose", tags=["executions"], dependencies=[Depends(require_driver)])
async def expose_agent_port(
    slug: str,
    name: str,
    request: ExposePortRequest,
    caller: Caller,
    executions: UserExecutions,
):
    """Expose a port on an agent's VM as a public HTTPS URL."""
    ex = _get_runtime_execution(executions, slug)
    _get_runtime_agent(ex, name)
    result = await ex._handle_expose(name, {"port": request.port, "service_name": request.service_name})
    if result.startswith("Error:"):
        raise HTTPException(400, result)
    return {"url": result}


@router.post("/executions/{slug}/ready", tags=["executions"], dependencies=[Depends(require_driver)])
async def runtime_ready(
    slug: str,
    request: RuntimeReadyRequest,
    caller: Caller,
    executions: UserExecutions,
):
    """Signal that the execution runtime is ready. Registers client event names."""
    ex = _get_runtime_execution(executions, slug)
    for event_name in request.client_events:
        ex._client_event_names.add(event_name)
    return {"status": "ready"}


@router.post("/executions/{slug}/edges", tags=["executions"], dependencies=[Depends(require_driver)])
async def set_edges(
    slug: str,
    request: SetEdgesRequest,
    caller: Caller,
    executions: UserExecutions,
):
    """Set the edge topology for this execution."""
    ex = _get_runtime_execution(executions, slug)
    ex.edges = request.edges
    execution_trace.topology(ex.user_id, ex.slug, list(ex.agents.keys()), request.edges)
    return {"status": "ok", "count": len(request.edges)}


@router.post("/executions/{slug}/emit", tags=["executions"], dependencies=[Depends(require_driver)])
async def emit_event(
    slug: str,
    request: EmitEventRequest,
    caller: Caller,
    executions: UserExecutions,
):
    """Emit a client event to the execution trace."""
    ex = _get_runtime_execution(executions, slug)
    ex.emit(request.event, request.data)
    return {"status": "emitted"}


@router.post("/executions/{slug}/send", tags=["executions"], dependencies=[Depends(require_driver)])
async def send_message(
    slug: str,
    request: SendMessageRequest,
    caller: Caller,
    executions: UserExecutions,
):
    """Deliver an agent-to-agent message. Called by the runtime after topology check."""
    ex = _get_runtime_execution(executions, slug)
    await ex.send(request.sender, request.receiver, request.text)
    return {"status": "sent"}


# ---------------------------------------------------------------------------
# Agent-scoped tool endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/executions/{slug}/agents/{agent_name}/tools",
    tags=["executions"],
    operation_id="list_agent_tools",
)
async def list_agent_tools(
    slug: str,
    agent_name: str,
    caller: Caller,
    executions: UserExecutions,
):
    """List tools registered for an agent."""
    if caller.agent_name and (caller.execution_slug != slug or caller.agent_name != agent_name):
        raise HTTPException(403, "Agents can only access their own tools")
    ex = _get_runtime_execution(executions, slug)
    _get_runtime_agent(ex, agent_name)
    return {"tools": await ex.list_tools(agent_name)}


@router.post(
    "/executions/{slug}/agents/{agent_name}/tools/{tool_name}",
    tags=["executions"],
    operation_id="call_agent_tool",
)
async def call_agent_tool(
    slug: str,
    agent_name: str,
    tool_name: str,
    request: CallToolRequest,
    caller: Caller,
    executions: UserExecutions,
):
    """Call a tool registered for an agent."""
    if caller.agent_name and (caller.execution_slug != slug or caller.agent_name != agent_name):
        raise HTTPException(403, "Agents can only access their own tools")
    ex = _get_runtime_execution(executions, slug)
    _get_runtime_agent(ex, agent_name)
    try:
        result = await ex.call_tool(agent_name, tool_name, request.args)
    except Exception:
        logger.exception("Tool call '%s' failed for agent '%s'", tool_name, agent_name)
        raise HTTPException(500, "Tool call failed")

    return {"result": result}


@router.get(
    "/executions/{slug}/agents/{agent_name}/trace",
    tags=["executions", "mcp-driver"],
    operation_id="get_agent_trace",
)
async def get_agent_trace(
    slug: str,
    agent_name: str,
    caller: Caller,
    executions: UserExecutions,
    n: int = 50,
):
    """Get the coalesced event trace for an agent."""
    ex = _get_runtime_execution(executions, slug)
    if not ex.has_agent(agent_name) and agent_name not in ex._archived_traces:
        raise HTTPException(404, f"Agent '{agent_name}' not found")

    return {
        "execution_slug": slug,
        "agent": agent_name,
        "trace": ex.get_agent_trace(agent_name, n=n),
    }


# ---------------------------------------------------------------------------
# Agent snapshot
# ---------------------------------------------------------------------------


@router.post(
    "/executions/{slug}/agents/{agent_name}/snapshot",
    tags=["executions"],
    dependencies=[Depends(require_driver)],
)
async def snapshot_agent(
    slug: str,
    agent_name: str,
    request: SnapshotAgentRequest,
    caller: Caller,
    executions: UserExecutions,
):
    """Snapshot an agent's VM and register it as a new devbox.

    Returns the new devbox name and snapshot ID.
    """
    ex = _get_runtime_execution(executions, slug)
    agent = _get_runtime_agent(ex, agent_name)

    snapshot_id = await agent.machine.snapshot()
    devbox_name = request.devbox_name or f"{slug}-{agent_name}"

    async with get_session() as db:
        existing = await get_devbox_by_name(db, caller.user.id, devbox_name)
        if existing:
            existing.snapshot_id = snapshot_id
            existing.updated_at = datetime.now(timezone.utc)
            db.add(existing)
        else:
            devbox = Devbox(
                user_id=caller.user.id,
                name=devbox_name,
                repo_full_name=ex.repo_full_name or "",
                snapshot_id=snapshot_id,
                setup_completed_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            db.add(devbox)

    logger.info("Snapshot agent '%s' (exec %s) -> devbox '%s' snapshot=%s", agent_name, slug, devbox_name, snapshot_id)
    return {"devbox_name": devbox_name, "snapshot_id": snapshot_id}
